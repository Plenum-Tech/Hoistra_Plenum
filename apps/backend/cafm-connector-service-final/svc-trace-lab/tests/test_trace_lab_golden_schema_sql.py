"""0002_golden.sql on a throwaway Postgres (tracelab_scratch): it applies and verifies, one active scenario set per
company, the regression set's shape, one row per message, the outcome, purpose and repetition limits, no credential
in a run's params, and append-only cases and reveals. Skips without TRACE_LAB_TEST_DB_URL."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from tl_scratch import lab_scratch, requires_scratch  # noqa: F401
from trace_lab import migrate

pytestmark = requires_scratch

ORG = str(uuid.uuid4())
OTHER = str(uuid.uuid4())
SHA = "a" * 64


async def _set(conn, org=ORG, *, version=1, status="active", kind="scenario", filename="set.xlsx", sha=SHA, count=1):
    sid = str(uuid.uuid4())
    await conn.execute(text(
        "INSERT INTO tracelab.golden_sets (id, organization_id, name, kind, version, status, source_filename, "
        "source_sha256, set_hash, case_count, created_by) VALUES (CAST(:id AS uuid), CAST(:org AS uuid), 'Set', "
        ":kind, :v, :st, :fn, :sha, 'aaaaaaaaaaaa', :n, 'u1')"),
        {"id": sid, "org": org, "kind": kind, "v": version, "st": status, "fn": filename, "sha": sha, "n": count})
    return sid


async def _case(conn, set_id, org=ORG, key="1", idx=0):
    cid = str(uuid.uuid4())
    await conn.execute(text(
        "INSERT INTO tracelab.golden_cases (id, set_id, organization_id, case_key, case_version, idx, question, "
        "expected_answer, evaluation) VALUES (CAST(:id AS uuid), CAST(:s AS uuid), CAST(:org AS uuid), :k, "
        "'abcdef012345', :i, 'How many floors?', '22', 'objective')"),
        {"id": cid, "s": set_id, "org": org, "k": key, "i": idx})
    return cid


async def _run(conn, set_id, org=ORG, *, k=1, purpose="adhoc", params="{}"):
    rid = str(uuid.uuid4())
    await conn.execute(text(
        "INSERT INTO tracelab.golden_runs (id, organization_id, set_id, label, purpose, k, status, started_by, params) "
        "VALUES (CAST(:id AS uuid), CAST(:org AS uuid), CAST(:s AS uuid), :label, :p, :k, 'running', 'u1', "
        "CAST(:params AS jsonb))"),
        {"id": rid, "org": org, "s": set_id, "label": "run-" + rid[:8], "p": purpose, "k": k, "params": params})
    return rid


async def _result(conn, run_id, case_id, *, rep=1, msg_idx=0, outcome="pending"):
    await conn.execute(text(
        "INSERT INTO tracelab.golden_results (run_id, organization_id, case_id, case_key, case_version, rep, msg_idx, "
        "session_id, turn_id, question, outcome) VALUES (CAST(:r AS uuid), CAST(:org AS uuid), CAST(:c AS uuid), '1', "
        "'abcdef012345', :rep, :m, 'golden-x-1-r1', :t, 'How many floors?', :o)"),
        {"r": run_id, "org": ORG, "c": case_id, "rep": rep, "m": msg_idx, "t": f"turn-{uuid.uuid4().hex[:16]}",
         "o": outcome})


async def _refused(engine, sql, params=None, exc=(IntegrityError, DBAPIError)):
    with pytest.raises(exc):
        async with engine.begin() as conn:
            await conn.execute(text(sql), params or {})


async def test_0002_applies_and_verifies(lab_scratch):
    st = await migrate.verify(lab_scratch)
    assert st.status == "ready" and st.applied_version == len(migrate.files()) >= 2
    async with lab_scratch.connect() as conn:
        names = set((await conn.execute(text(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'tracelab'"))).scalars().all())
    assert {"golden_sets", "golden_cases", "golden_runs", "golden_results", "golden_reveals"} <= names


async def test_one_active_scenario_set_per_company(lab_scratch):
    async with lab_scratch.begin() as conn:
        await _set(conn, version=1, status="retired")
        await _set(conn, version=2)
        await _set(conn, OTHER, version=1)
    with pytest.raises(IntegrityError):
        async with lab_scratch.begin() as conn:
            await _set(conn, version=3)


async def test_the_regression_set_shape_and_one_per_company(lab_scratch):
    async with lab_scratch.begin() as conn:
        await _set(conn, version=1)
        await _set(conn, version=0, kind="regression", filename=None, sha=None, count=0)
    for bad in ({"version": 1}, {"version": 0, "kind": "scenario"}):
        with pytest.raises(IntegrityError):
            async with lab_scratch.begin() as conn:
                kw = {"kind": "regression", "filename": None, "sha": None, "count": 0, **bad}
                await _set(conn, str(uuid.uuid4()), **kw)
    with pytest.raises(IntegrityError):
        async with lab_scratch.begin() as conn:
            await _set(conn, version=0, kind="regression", filename=None, sha=None, count=0)
    with pytest.raises(IntegrityError):                      # an uploaded set needs its file and a case
        async with lab_scratch.begin() as conn:
            await _set(conn, OTHER, version=5, count=0)


async def test_runs_take_the_r8_purposes_and_1_to_20_reps(lab_scratch):
    async with lab_scratch.begin() as conn:
        s = await _set(conn)
        for purpose, k in (("adhoc", 1), ("baseline", 5), ("release_check", 10), ("repeat", 20)):
            await _run(conn, s, purpose=purpose, k=k)
    for purpose, k in (("check", 1), ("adhoc", 0), ("adhoc", 21)):
        with pytest.raises(IntegrityError):
            async with lab_scratch.begin() as conn:
                await _run(conn, s, purpose=purpose, k=k)


async def test_a_run_never_stores_a_credential(lab_scratch):
    async with lab_scratch.begin() as conn:
        s = await _set(conn)
        await _run(conn, s, params='{"model": "gpt-5.6-terra", "flags": {"hitl": true}}')
    with pytest.raises(IntegrityError):
        async with lab_scratch.begin() as conn:
            await _run(conn, s, params='{"authorization": "Bearer x"}')


async def test_one_row_per_message_and_the_outcome_list(lab_scratch):
    async with lab_scratch.begin() as conn:
        s = await _set(conn)
        c = await _case(conn, s)
        r = await _run(conn, s, k=2)
        await _result(conn, r, c)
        await _result(conn, r, c, msg_idx=1, outcome="pass")
        await _result(conn, r, c, rep=2, outcome="stale_reference")
    with pytest.raises(IntegrityError):
        async with lab_scratch.begin() as conn:
            await _result(conn, r, c, msg_idx=1)
    for outcome in ("needs_person", "infra_failed", "grading_pending", "context_unavailable", "unvalidated"):
        with pytest.raises(IntegrityError):
            async with lab_scratch.begin() as conn:
                await _result(conn, r, c, rep=3, outcome=outcome)


async def test_cases_and_reveals_are_append_only(lab_scratch):
    async with lab_scratch.begin() as conn:
        s = await _set(conn)
        await _case(conn, s)
        await conn.execute(text(
            "INSERT INTO tracelab.golden_reveals (organization_id, turn_id, reviewer_id) VALUES "
            "(CAST(:org AS uuid), 'turn-1', 'u1') ON CONFLICT DO NOTHING"), {"org": ORG})
        await conn.execute(text(
            "INSERT INTO tracelab.golden_reveals (organization_id, turn_id, reviewer_id) VALUES "
            "(CAST(:org AS uuid), 'turn-1', 'u1') ON CONFLICT DO NOTHING"), {"org": ORG})
        n = (await conn.execute(text("SELECT count(*) FROM tracelab.golden_reveals"))).scalar_one()
    assert n == 1
    await _refused(lab_scratch, "UPDATE tracelab.golden_cases SET question = 'changed'")
    await _refused(lab_scratch, "DELETE FROM tracelab.golden_cases")
    await _refused(lab_scratch, "UPDATE tracelab.golden_reveals SET reviewer_email = 'x'")
    await _refused(lab_scratch, "DELETE FROM tracelab.golden_reveals")
    async with lab_scratch.begin() as conn:                   # company offboarding may still purge
        await conn.execute(text("SET LOCAL tracelab.allow_purge = 'on'"))
        await conn.execute(text("DELETE FROM tracelab.golden_reveals"))
        await conn.execute(text("DELETE FROM tracelab.golden_cases"))
