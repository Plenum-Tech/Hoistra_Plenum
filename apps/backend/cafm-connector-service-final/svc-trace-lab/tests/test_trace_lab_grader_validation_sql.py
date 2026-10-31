"""Grader validation on a throwaway Postgres (tracelab_scratch): it reads the final grades of one grader version on
judgment cases only (never the runner's own verdicts), pairs them with human labels from the registered sources,
pools companies and reports each, and caches for a minute. Skips without TRACE_LAB_TEST_DB_URL."""
from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import text

from tl_scratch import lab_scratch, requires_scratch  # noqa: F401
from trace_lab import grader_validation as gvl

pytestmark = requires_scratch

A, B = str(uuid.uuid4()), str(uuid.uuid4())
V, OLD = "abcdef012345", "000000000000"


async def _world(conn, org, *, evaluation="judgment", version=1):
    sid, cid, rid = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    await conn.execute(text(
        "INSERT INTO tracelab.golden_sets (id, organization_id, name, version, status, source_filename, source_sha256, "
        "set_hash, case_count, created_by) VALUES (CAST(:s AS uuid), CAST(:o AS uuid), 'Set', :v, :st, 's.csv', :sha, "
        "'aaaaaaaaaaaa', 1, 'u1')"),
        {"s": sid, "o": org, "v": version, "st": "active" if version == 1 else "retired", "sha": "a" * 64})
    await conn.execute(text(
        "INSERT INTO tracelab.golden_cases (id, set_id, organization_id, case_key, case_version, idx, question, "
        "expected_answer, evaluation) VALUES (CAST(:c AS uuid), CAST(:s AS uuid), CAST(:o AS uuid), 'k1', "
        "'abcdef012345', 0, 'Q?', 'A', :e)"), {"c": cid, "s": sid, "o": org, "e": evaluation})
    await conn.execute(text(
        "INSERT INTO tracelab.golden_runs (id, organization_id, set_id, label, k, status, started_by) VALUES "
        "(CAST(:r AS uuid), CAST(:o AS uuid), CAST(:s AS uuid), :l, 20, 'done', 'u1')"),
        {"r": rid, "o": org, "s": sid, "l": "run-" + rid[:8]})
    return rid, cid


async def _graded(conn, org, run, case, rep, verdict, *, version=V, source="grader", key="k1", age=0):
    tid = f"turn-{uuid.uuid4().hex[:16]}"
    await conn.execute(text(
        "INSERT INTO tracelab.golden_results (run_id, organization_id, case_id, case_key, case_version, rep, session_id, "
        "turn_id, question, outcome, grade, created_at) VALUES (CAST(:r AS uuid), CAST(:o AS uuid), CAST(:c AS uuid), "
        ":k, 'abcdef012345', :rep, 'golden-x', :t, 'Q?', 'unsure', CAST(:g AS jsonb), now() - make_interval(secs => :age))"),
        {"r": run, "o": org, "c": case, "k": key, "rep": rep, "t": tid, "age": age,
         "g": json.dumps({"verdict": verdict, "grader_version": version, "source": source})})
    return tid


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(gvl, "_SOURCES", [])
    gvl.clear_cache()


async def test_validation_reads_one_version_on_judgment_cases_and_pools_companies(lab_scratch):
    labels: dict[str, str] = {}
    async with lab_scratch.begin() as conn:
        ra, ca = await _world(conn, A)
        rb, cb = await _world(conn, B)
        ro, co = await _world(conn, A, evaluation="objective", version=2)
        labels[await _graded(conn, A, ra, ca, 1, "pass", age=5)] = "pass"     # the oldest of three reps: left out
        labels[await _graded(conn, A, ra, ca, 2, "fail", age=4)] = "pass"
        labels[await _graded(conn, A, ra, ca, 3, "pass", age=1)] = "pass"
        labels[await _graded(conn, B, rb, cb, 1, "fail")] = "fail"
        labels[await _graded(conn, B, rb, cb, 2, "unsure")] = "fail"
        labels[await _graded(conn, A, ro, co, 1, "pass")] = "pass"            # objective: not this measure
        labels[await _graded(conn, A, ra, ca, 4, "pass", version=OLD, key="k1")] = "fail"   # another version
        labels[await _graded(conn, B, rb, cb, 3, "fail", source="runner")] = "fail"       # a chat error
        await _graded(conn, B, rb, cb, 4, "pass")                              # no human label yet

    async def source(ids):
        return {t: labels[t] for t in ids if t in labels}

    gvl.register_label_source(source)
    v = await gvl.validation(grader_version=V)
    assert (v["grader_version"], v["evaluation"], v["graded"], v["labelled"]) == (V, "judgment", 6, 5)
    p = v["pooled"]
    assert (p["n"], p["n_pass"], p["n_fail"], p["tp"], p["fn"], p["tn"], p["unsure"]) == (4, 2, 2, 1, 1, 1, 1)
    assert [c["organization_id"] for c in v["by_company"]] == sorted([A, B])
    assert v["passes"] is False and v["thresholds"] == gvl.thresholds()
    assert await gvl.passes(V) is False


async def test_enough_agreeing_labels_pass_and_the_result_is_cached(lab_scratch, monkeypatch):
    monkeypatch.setenv("TRACE_LAB_GRADER_MIN_PER_CLASS", "2")
    labels: dict[str, str] = {}
    async with lab_scratch.begin() as conn:
        for org in (A, B):
            run, case = await _world(conn, org)
            labels[await _graded(conn, org, run, case, 1, "pass")] = "pass"
            labels[await _graded(conn, org, run, case, 2, "fail")] = "fail"

    async def source(ids):
        return {t: labels[t] for t in ids if t in labels}

    gvl.register_label_source(source)
    v = await gvl.validation(grader_version=V)
    assert v["passes"] is True and (v["pooled"]["tpr"], v["pooled"]["tnr"]) == (1.0, 1.0)
    assert [(c["n_pass"], c["n_fail"]) for c in v["by_company"]] == [(1, 1), (1, 1)]
    async with lab_scratch.begin() as conn:
        await conn.execute(text("UPDATE tracelab.golden_results SET grade = jsonb_set(grade, '{verdict}', '\"fail\"')"))
    assert (await gvl.validation(grader_version=V))["passes"] is True        # cached for a minute
    gvl.clear_cache()
    assert (await gvl.validation(grader_version=V))["passes"] is False
