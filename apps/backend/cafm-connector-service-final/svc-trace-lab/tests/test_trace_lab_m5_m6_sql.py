"""0005_judges.sql on a throwaway Postgres (tracelab_scratch): the rules the judge tables enforce for themselves.

A judge version never changes its prompt and freezing is one-way, few-shot examples come from the train split, a split
only ever retires, its members are append-only, the held-out test runs once and only against a frozen judge, and a decided
prediction never changes. Skips without TRACE_LAB_TEST_DB_URL (tests/tl_scratch.py refuses any database but
tracelab_scratch). Later Phase 6 tasks append their own tests below these.

Trace Lab's database holds no chat tables, so a member or prediction just names its turn id. The hatch that company
offboarding would use is the foundation's `tracelab.allow_purge`."""
from __future__ import annotations

import json
import re
import types
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

import golden_seed
import trace_lab_m4_fixtures as fx
from tl_scratch import lab_scratch, main_scratch, requires_scratch  # noqa: F401
from trace_lab import evidence, judges, labels, taxonomy

pytestmark = requires_scratch

TRAIN3 = [("turn-a", "train", True), ("turn-b", "train", False), ("turn-c", "train", True),
          ("turn-d", "dev", True), ("turn-e", "test", False)]
EX3 = [{"turn_id": "turn-a", "role": "clear_fail", "critique": "Names AHU-09.", "verdict": "Fail"},
       {"turn_id": "turn-b", "role": "clear_pass", "critique": "Every code returned.", "verdict": "Pass"},
       {"turn_id": "turn-c", "role": "borderline", "critique": "Implied code.", "verdict": "Fail"}]
EVIDENCE = json.dumps({"text": "QUESTION\nq", "chars": 10, "sha": "x", "truncated": False})


async def _mode(conn, name: str = "invented_codes_sql") -> str:
    return await fx.add_mode(conn, name, status="confirmed", agent="udr",
                             definition="Present when the answer names a code no step returned.")


async def _split(conn, mode_id: str, members, *, version: int = 1, org: str = fx.ORG_A) -> str:
    sid = str(uuid.uuid4())
    await conn.execute(text(
        "INSERT INTO tracelab.judge_splits (id, mode_id, version, fractions, seed, min_per_class, min_labels, "
        "counts, labels_sha) VALUES (CAST(:id AS uuid), CAST(:m AS uuid), :v, CAST(:fr AS numeric(4,3)[]), 7, 10, 30, "
        "CAST(:c AS jsonb), :sha)"),
        {"id": sid, "m": mode_id, "v": version, "fr": [0.2, 0.4, 0.4], "c": json.dumps({}), "sha": "0" * 64})
    for turn_id, split, present in members:
        await _member(conn, sid, turn_id, split, present, org)
    return sid


async def _member(conn, split_id: str, turn_id: str, split: str, present: bool, org: str = fx.ORG_A, *,
                  source: str = "human_structured_label") -> None:
    await conn.execute(text(
        "INSERT INTO tracelab.judge_split_members (split_id, turn_id, organization_id, conversation_key, split, "
        "label_present, label_source, evidence_snapshot) VALUES (CAST(:s AS uuid), :t, CAST(:o AS uuid), :ck, :sp, :p, "
        ":src, CAST(:ev AS jsonb))"),
        {"s": split_id, "t": turn_id, "o": org, "ck": "turn:" + turn_id, "sp": split, "p": present, "src": source,
         "ev": EVIDENCE})


async def _judge(conn, mode_id: str, split_id: str, *, version: int = 0, examples=None) -> str:
    jid = str(uuid.uuid4())
    parts = {"fail_rule": "The answer names a code.", "pass_rule": "No invented code.",
             "examples": EX3 if examples is None else examples}
    await conn.execute(text(
        "INSERT INTO tracelab.judges (id, mode_id, version, judge_key, kind, split_id, model, prompt_text, "
        "prompt_parts, prompt_hash) VALUES (CAST(:id AS uuid), CAST(:m AS uuid), :v, :key, 'llm', CAST(:s AS uuid), "
        "'claude-sonnet-5', :p, CAST(:parts AS jsonb), :ph)"),
        {"id": jid, "m": mode_id, "v": version, "key": f"invented_codes_sql-v{version}", "s": split_id,
         "p": f"prompt {version}", "parts": json.dumps(parts), "ph": f"hash{version:08d}"})
    return jid


async def _code_judge(conn, mode_id: str, split_id: str, *, version: int = 0) -> str:
    jid = str(uuid.uuid4())
    await conn.execute(text(
        "INSERT INTO tracelab.judges (id, mode_id, version, judge_key, kind, split_id, check_key, check_version, "
        "prompt_hash) VALUES (CAST(:id AS uuid), CAST(:m AS uuid), :v, :key, 'code', CAST(:s AS uuid), "
        "'codes_come_from_steps', 1, :ph)"),
        {"id": jid, "m": mode_id, "v": version, "key": f"codes_come_from_steps-v{version}", "s": split_id,
         "ph": f"code{version:08d}"})
    return jid


async def _freeze(conn, judge_id: str) -> None:
    await conn.execute(text(
        "UPDATE tracelab.judges SET status = 'frozen', stop_reason = 'Stable on dev twice.', frozen_at = now(), "
        "frozen_by = CAST(:u AS uuid), frozen_by_email = 'owner@plenum.test' WHERE id = CAST(:j AS uuid)"),
        {"j": judge_id, "u": fx.OWNER_ID})


async def _run(conn, judge_id: str, split: str, status: str = "queued", job_id: str | None = None) -> str:
    rid = str(uuid.uuid4())
    await conn.execute(text("INSERT INTO tracelab.judge_runs (id, judge_id, split, status, items, job_id) "
                            "VALUES (CAST(:id AS uuid), CAST(:j AS uuid), :sp, :st, CAST('[]' AS jsonb), "
                            "CAST(:job AS uuid))"),
                       {"id": rid, "j": judge_id, "sp": split, "st": status, "job": job_id})
    return rid


async def _prediction(conn, turn_id: str, judge_id: str, *, prompt_hash: str = "hash00000000",
                      org: str = fx.ORG_A, status: str = "decided", failure_present: int | None = 1,
                      error: str | None = None) -> None:
    await conn.execute(text(
        "INSERT INTO tracelab.judge_predictions (prompt_hash, turn_id, judge_id, organization_id, status, "
        "failure_present, error) VALUES (:ph, :t, CAST(:j AS uuid), CAST(:o AS uuid), :st, :fp, :err)"),
        {"ph": prompt_hash, "t": turn_id, "j": judge_id, "o": org, "st": status, "fp": failure_present, "err": error})


async def _refused(engine, sql: str, params: dict | None = None, exc=DBAPIError, match: str | None = None) -> None:
    with pytest.raises(exc, match=match):
        async with engine.begin() as conn:
            await conn.execute(text(sql), params or {})


async def _base(engine) -> tuple[str, str, str]:
    async with engine.begin() as conn:
        mid = await _mode(conn)
        sid = await _split(conn, mid, TRAIN3)
        jid = await _judge(conn, mid, sid)
    return mid, sid, jid


async def _scalar(engine, sql: str, params: dict | None = None):
    async with engine.connect() as conn:
        return (await conn.execute(text(sql), params or {})).scalar_one()


async def test_migration_0005_applies_at_head(lab_scratch):
    async with lab_scratch.connect() as conn:
        versions = [r[0] for r in (await conn.execute(text(
            "SELECT version FROM tracelab.schema_migrations ORDER BY version"))).all()]
        tables = {r[0] for r in (await conn.execute(text(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'tracelab'"))).all()}
    assert 5 in versions
    assert {"judge_splits", "judge_split_members", "judges", "judge_predictions", "judge_runs"} <= tables


async def test_judges_trigger_refuses_prompt_change_unfreeze_delete(lab_scratch):
    _, _, jid = await _base(lab_scratch)
    p = {"j": jid}
    await _refused(lab_scratch, "UPDATE tracelab.judges SET prompt_text = 'other' WHERE id = CAST(:j AS uuid)", p,
                   match="the prompt of invented_codes_sql-v0 is immutable")
    await _refused(lab_scratch, "UPDATE tracelab.judges SET prompt_parts = '{}' WHERE id = CAST(:j AS uuid)", p,
                   match="the prompt of invented_codes_sql-v0 is immutable")
    await _refused(lab_scratch, "UPDATE tracelab.judges SET model = 'other' WHERE id = CAST(:j AS uuid)", p,
                   match="invented_codes_sql-v0 is immutable; register a new version")
    await _refused(lab_scratch, "UPDATE tracelab.judges SET prompt_hash = 'other' WHERE id = CAST(:j AS uuid)", p,
                   match="invented_codes_sql-v0 is immutable; register a new version")
    await _refused(lab_scratch, "UPDATE tracelab.judges SET version = 9 WHERE id = CAST(:j AS uuid)", p,
                   match="is immutable; register a new version")
    await _refused(lab_scratch, "DELETE FROM tracelab.judges WHERE id = CAST(:j AS uuid)", p,
                   match="a judge version is never deleted")
    async with lab_scratch.begin() as conn:
        await _freeze(conn, jid)
    await _refused(lab_scratch, "UPDATE tracelab.judges SET status = 'draft' WHERE id = CAST(:j AS uuid)", p,
                   match="freezing is one-way")
    await _refused(lab_scratch, "UPDATE tracelab.judges SET stop_reason = 'changed my mind' WHERE id = CAST(:j AS uuid)",
                   p, match="the freeze record of invented_codes_sql-v0 never changes")
    await _refused(lab_scratch, "UPDATE tracelab.judges SET frozen_at = now() - interval '1 day' "
                                "WHERE id = CAST(:j AS uuid)", p, match="the freeze record")
    await _refused(lab_scratch, "UPDATE tracelab.judges SET frozen_by_email = 'someone@else.test' "
                                "WHERE id = CAST(:j AS uuid)", p, match="the freeze record")
    await _refused(lab_scratch, "UPDATE tracelab.judges SET frozen_by = gen_random_uuid() "
                                "WHERE id = CAST(:j AS uuid)", p, match="the freeze record")
    async with lab_scratch.begin() as conn:      # the decision and the threshold stay writable
        await conn.execute(text("UPDATE tracelab.judges SET decision = 'use', monitor_threshold = 0.15, "
                                "decision_note = 'Good enough.', rev = rev + 1 WHERE id = CAST(:j AS uuid)"), p)
    assert await _scalar(lab_scratch, "SELECT decision FROM tracelab.judges WHERE id = CAST(:j AS uuid)", p) == "use"


async def test_the_judge_row_shape_is_checked_by_kind(lab_scratch):
    async with lab_scratch.begin() as conn:
        mid = await _mode(conn)
        sid = await _split(conn, mid, TRAIN3)
        await _code_judge(conn, mid, sid)       # a code judge: a check key and version, no prompt, no examples
    base = ("INSERT INTO tracelab.judges (id, mode_id, version, judge_key, kind, split_id, prompt_parts, {cols}, "
            "prompt_hash) VALUES (gen_random_uuid(), CAST(:m AS uuid), :v, 'k', :kind, CAST(:s AS uuid), "
            "CAST(:parts AS jsonb), {vals}, 'h')")
    p = {"m": mid, "s": sid, "parts": json.dumps({"examples": EX3})}     # three good examples: only the CHECK can refuse
    for v, kind, cols, vals in (
            (1, "llm", "prompt_text", "'p'"),                                        # no model
            (2, "llm", "model", "'m'"),                                              # no prompt text
            (3, "llm", "model, prompt_text, check_key", "'m', 'p', 'codes_come_from_steps'"),
            (4, "code", "check_key", "'codes_come_from_steps'"),                     # no check version
            (5, "code", "check_key, check_version, prompt_text", "'codes_come_from_steps', 1, 'p'"),
            (6, "other", "model, prompt_text", "'m', 'p'")):
        with pytest.raises(IntegrityError):
            async with lab_scratch.begin() as conn:
                await conn.execute(text(base.format(cols=cols, vals=vals)), {**p, "v": v, "kind": kind})
    for sql in ("UPDATE tracelab.judges SET status = 'frozen'",                      # frozen with no stop reason
                "UPDATE tracelab.judges SET monitor_threshold = 1",
                "UPDATE tracelab.judges SET monitor_threshold = 0",
                "UPDATE tracelab.judges SET decision = 'maybe'"):
        await _refused(lab_scratch, sql, exc=IntegrityError)
    async with lab_scratch.begin() as conn:      # and a second version of the same mode is free to register
        await _code_judge(conn, mid, sid, version=1)
    await _refused(lab_scratch, "INSERT INTO tracelab.judges (id, mode_id, version, judge_key, kind, split_id, "
                                "check_key, check_version, prompt_hash) VALUES (gen_random_uuid(), "
                                "CAST(:m AS uuid), 1, 'dup', 'code', CAST(:s AS uuid), 'x', 1, 'h')", p,
                   exc=IntegrityError)           # (mode_id, version) is unique


async def test_few_shot_examples_must_be_train_members(lab_scratch):
    async with lab_scratch.begin() as conn:
        mid = await _mode(conn)
        sid = await _split(conn, mid, TRAIN3 + [("turn-f", "train", False)])
    dev_example = [dict(EX3[0], turn_id="turn-d")] + EX3[1:]
    wrong_label = EX3[:2] + [dict(EX3[2], verdict="Pass")]
    elsewhere = EX3[:2] + [dict(EX3[2], turn_id="turn-nowhere")]
    four = EX3 + [dict(EX3[1], turn_id="turn-f", role="clear_pass")]
    for bad, why in ((dev_example, "not a train member"), (wrong_label, "not a train member"),
                     (elsewhere, "not a train member"), (EX3[:2], "exactly three examples, not 2"),
                     (four, "exactly three examples, not 4"), ([], "exactly three examples, not 0"),
                     (EX3 + [EX3[0]], "example turn-a is used twice"), ([EX3[0]] * 3, "example turn-a is used twice"),
                     ({"turn_id": "turn-a"}, "examples of an LLM judge are a list"),
                     ("turn-a", "examples of an LLM judge are a list")):
        with pytest.raises(DBAPIError, match=why):
            async with lab_scratch.begin() as conn:
                await _judge(conn, mid, sid, examples=bad)
    async with lab_scratch.begin() as conn:
        await _judge(conn, mid, sid)             # the right three go in
    async with lab_scratch.begin() as conn:      # examples of another split's members do not count
        await conn.execute(text("UPDATE tracelab.judge_splits SET status = 'retired', retired_at = now() "
                                "WHERE id = CAST(:s AS uuid)"), {"s": sid})
        other = await _split(conn, mid, [("turn-x", "train", True), ("turn-y", "train", False),
                                         ("turn-z", "train", True)], version=2)
    with pytest.raises(DBAPIError, match="not a train member"):
        async with lab_scratch.begin() as conn:
            await _judge(conn, mid, other, version=1)


async def test_split_members_append_only_and_split_retire_only(lab_scratch):
    _, sid, _ = await _base(lab_scratch)
    p = {"s": sid}
    await _refused(lab_scratch, "UPDATE tracelab.judge_split_members SET label_present = NOT label_present "
                                "WHERE split_id = CAST(:s AS uuid)", p, match="append-only")
    await _refused(lab_scratch, "DELETE FROM tracelab.judge_split_members WHERE split_id = CAST(:s AS uuid)", p,
                   match="append-only")
    await _refused(lab_scratch, "TRUNCATE tracelab.judge_split_members", match="append-only")
    await _refused(lab_scratch, "UPDATE tracelab.judge_splits SET counts = '{}' WHERE id = CAST(:s AS uuid)", p,
                   match="a split is immutable")
    await _refused(lab_scratch, "UPDATE tracelab.judge_splits SET status = 'retired', seed = 8 "   # retire changes nothing else
                                "WHERE id = CAST(:s AS uuid)", p, match="a split is immutable")
    await _refused(lab_scratch, "DELETE FROM tracelab.judge_splits WHERE id = CAST(:s AS uuid)", p,
                   match="a split is never deleted")
    async with lab_scratch.begin() as conn:
        await conn.execute(text("UPDATE tracelab.judge_splits SET status = 'retired', retired_at = now() "
                                "WHERE id = CAST(:s AS uuid)"), p)
    await _refused(lab_scratch, "UPDATE tracelab.judge_splits SET status = 'active', retired_at = NULL "
                                "WHERE id = CAST(:s AS uuid)", p, match="only active to retired")
    assert await _scalar(lab_scratch, "SELECT count(*) FROM tracelab.judge_split_members") == len(TRAIN3)


async def test_a_mode_has_one_active_split_and_a_new_version_follows_a_retired_one(lab_scratch):
    async with lab_scratch.begin() as conn:
        mid = await _mode(conn)
        first = await _split(conn, mid, TRAIN3)
    with pytest.raises(IntegrityError):          # a second active split for the mode
        async with lab_scratch.begin() as conn:
            await _split(conn, mid, TRAIN3, version=2)
    async with lab_scratch.begin() as conn:
        await conn.execute(text("UPDATE tracelab.judge_splits SET status = 'retired', retired_at = now() "
                                "WHERE id = CAST(:s AS uuid)"), {"s": first})
        await _split(conn, mid, TRAIN3, version=2)
    with pytest.raises(IntegrityError):          # versions are unique per mode
        async with lab_scratch.begin() as conn:
            await conn.execute(text("UPDATE tracelab.judge_splits SET status = 'retired', retired_at = now() "
                                    "WHERE mode_id = CAST(:m AS uuid) AND version = 2"), {"m": mid})
            await _split(conn, mid, TRAIN3, version=2)


async def test_members_and_predictions_name_a_company_and_a_known_source(lab_scratch):
    _, sid, jid = await _base(lab_scratch)
    for kwargs in ({"org": None},                                  # a member names its company
                   {"source": "guess"},                            # and a label source the labels table knows
                   {"split": "holdout"}):
        with pytest.raises(IntegrityError):
            async with lab_scratch.begin() as conn:
                await _member(conn, sid, "turn-q", **{"split": "train", **kwargs, "present": True})
    for turn_id, key in (("turn-q", "x"), ("", "turn:q"), ("turn-a", "turn:a")):   # key too short, empty id, twice
        with pytest.raises(IntegrityError):
            async with lab_scratch.begin() as conn:
                await conn.execute(text(
                    "INSERT INTO tracelab.judge_split_members (split_id, turn_id, organization_id, conversation_key, "
                    "split, label_present, label_source, evidence_snapshot) VALUES (CAST(:s AS uuid), :t, "
                    "gen_random_uuid(), :ck, 'dev', true, 'human_structured_label', '{}')"),
                    {"s": sid, "t": turn_id, "ck": key})
    for source in ("human_structured_label", "human_open_code", "accepted_suggestion", "rejected_suggestion",
                   "accepted_label_proposal"):
        async with lab_scratch.begin() as conn:
            await _member(conn, sid, f"turn-{source}", "train", True, source=source)
    with pytest.raises(IntegrityError):                            # a prediction names its company too
        async with lab_scratch.begin() as conn:
            await _prediction(conn, "turn-d", jid, org=None)
    with pytest.raises(IntegrityError):                            # and has a turn id
        async with lab_scratch.begin() as conn:
            await _prediction(conn, "", jid)


async def test_test_run_needs_frozen_judge_and_dev_stops_at_freeze(lab_scratch):
    _, _, jid = await _base(lab_scratch)
    with pytest.raises(DBAPIError, match="the held-out test needs a frozen judge"):
        async with lab_scratch.begin() as conn:
            await _run(conn, jid, "test")
    async with lab_scratch.begin() as conn:
        await _run(conn, jid, "dev")
        await _run(conn, jid, "dev")             # dev may run as often as the judge is a draft
        await _freeze(conn, jid)
    with pytest.raises(DBAPIError, match="dev runs stop when the judge is frozen"):
        async with lab_scratch.begin() as conn:
            await _run(conn, jid, "dev")
    assert await _scalar(lab_scratch, "SELECT count(*) FROM tracelab.judge_runs") == 2


async def test_one_test_run_per_judge(lab_scratch):
    _, _, jid = await _base(lab_scratch)
    async with lab_scratch.begin() as conn:
        await _freeze(conn, jid)
        await _run(conn, jid, "test")
    with pytest.raises(IntegrityError):
        async with lab_scratch.begin() as conn:
            await _run(conn, jid, "test")


async def test_a_new_judge_version_has_its_own_dev_runs_and_its_own_test(lab_scratch):
    """Freezing v0 stops v0's dev runs and spends v0's one test, and says nothing about v1."""
    mid, sid, v0 = await _base(lab_scratch)
    async with lab_scratch.begin() as conn:
        await _run(conn, v0, "dev")
        await _freeze(conn, v0)
        await _run(conn, v0, "test")
        v1 = await _judge(conn, mid, sid, version=1)
        await _run(conn, v1, "dev")
        await _freeze(conn, v1)
        await _run(conn, v1, "test")
    assert await _scalar(lab_scratch, "SELECT count(*) FROM tracelab.judge_runs WHERE split = 'test'") == 2


async def test_finished_test_run_is_never_changed(lab_scratch):
    _, _, jid = await _base(lab_scratch)
    async with lab_scratch.begin() as conn:
        await _freeze(conn, jid)
        rid = await _run(conn, jid, "test", status="running")
        await conn.execute(text("UPDATE tracelab.judge_runs SET n = 1, usd = 0.5 WHERE id = CAST(:r AS uuid)"),
                           {"r": rid})       # an unfinished test run is still being written
        await conn.execute(text("UPDATE tracelab.judge_runs SET status = 'done', results = CAST('[]' AS jsonb) "
                                "WHERE id = CAST(:r AS uuid)"), {"r": rid})
    p = {"r": rid}
    await _refused(lab_scratch, "UPDATE tracelab.judge_runs SET tpr = 0.5 WHERE id = CAST(:r AS uuid)", p,
                   match="a finished held-out test is never changed")
    await _refused(lab_scratch, "UPDATE tracelab.judge_runs SET status = 'running' WHERE id = CAST(:r AS uuid)", p,
                   match="a finished held-out test is never changed")
    await _refused(lab_scratch, "DELETE FROM tracelab.judge_runs WHERE id = CAST(:r AS uuid)", p, match="runs are kept")
    await _refused(lab_scratch, "TRUNCATE tracelab.judge_runs", match="runs are kept")


async def test_a_run_never_changes_its_judge_split_or_items(lab_scratch):
    mid, sid, jid = await _base(lab_scratch)
    async with lab_scratch.begin() as conn:
        other = await _judge(conn, mid, sid, version=1)
        rid = await _run(conn, jid, "dev", status="running")
    p = {"r": rid, "o": other}
    for sql in ("UPDATE tracelab.judge_runs SET judge_id = CAST(:o AS uuid) WHERE id = CAST(:r AS uuid)",
                "UPDATE tracelab.judge_runs SET split = 'test' WHERE id = CAST(:r AS uuid)",
                "UPDATE tracelab.judge_runs SET items = CAST('[{\"turn_id\": \"turn-d\"}]' AS jsonb) "
                "WHERE id = CAST(:r AS uuid)"):
        await _refused(lab_scratch, sql, p, match="never change")
    async with lab_scratch.begin() as conn:      # the numbers of an unfinished dev run are written as it goes
        await conn.execute(text("UPDATE tracelab.judge_runs SET status = 'done', n = 4, tp = 1, fn = 1, tn = 1, fp = 1, "
                                "tpr = 0.5, tnr = 0.5 WHERE id = CAST(:r AS uuid)"), p)
        await conn.execute(text("UPDATE tracelab.judge_runs SET label_flips = 1 WHERE id = CAST(:r AS uuid)"), p)
    await _refused(lab_scratch, "DELETE FROM tracelab.judge_runs WHERE id = CAST(:r AS uuid)", p, match="runs are kept")


async def test_a_run_may_name_its_job_and_the_job_must_exist(lab_scratch):
    _, _, jid = await _base(lab_scratch)
    job_id = str(uuid.uuid4())
    async with lab_scratch.begin() as conn:
        await conn.execute(text(
            "INSERT INTO tracelab.jobs (id, kind, status, organization_id, scope_key, params, usd_cap, created_by) "
            "VALUES (CAST(:id AS uuid), 'judge_run', 'running', NULL, :sk, '{}'::jsonb, 5, CAST(:by AS uuid))"),
            {"id": job_id, "sk": f"judge_run:{jid}:dev", "by": fx.OWNER_ID})
        await _run(conn, jid, "dev", job_id=job_id)
    with pytest.raises(IntegrityError):
        async with lab_scratch.begin() as conn:
            await _run(conn, jid, "dev", job_id=str(uuid.uuid4()))
    for bad in ("queued ", "paused", "skipped"):
        with pytest.raises(IntegrityError):
            async with lab_scratch.begin() as conn:
                await _run(conn, jid, "dev", status=bad)


async def test_decided_prediction_immutable(lab_scratch):
    _, _, jid = await _base(lab_scratch)
    async with lab_scratch.begin() as conn:
        await _prediction(conn, "turn-d", jid, status="error", failure_present=None, error="timeout")
        await conn.execute(text("UPDATE tracelab.judge_predictions SET status = 'decided', failure_present = 1, "
                                "error = NULL, attempts = 2 WHERE turn_id = 'turn-d'"))
    await _refused(lab_scratch, "UPDATE tracelab.judge_predictions SET failure_present = 0 WHERE turn_id = 'turn-d'",
                   match="a decided prediction is never changed")
    await _refused(lab_scratch, "DELETE FROM tracelab.judge_predictions WHERE turn_id = 'turn-d'",
                   match="a decided prediction is never changed")
    await _refused(lab_scratch, "TRUNCATE tracelab.judge_predictions", match="TRUNCATE refused")
    async with lab_scratch.begin() as conn:      # company offboarding is the only way out
        await conn.execute(text("SET LOCAL tracelab.allow_purge = 'on'"))
        await conn.execute(text("DELETE FROM tracelab.judge_predictions WHERE turn_id = 'turn-d'"))
    assert await _scalar(lab_scratch, "SELECT count(*) FROM tracelab.judge_predictions") == 0


async def test_a_failed_prediction_is_retried_in_place_and_no_row_is_ever_deleted(lab_scratch):
    _, _, jid = await _base(lab_scratch)
    async with lab_scratch.begin() as conn:
        await _prediction(conn, "turn-d", jid, status="error", failure_present=None, error="timeout")
        await _prediction(conn, "turn-e", jid, status="undecidable", failure_present=None, error="no verdict")
        await conn.execute(text("UPDATE tracelab.judge_predictions SET attempts = attempts + 1, error = 'overloaded' "
                                "WHERE turn_id = 'turn-d'"))
    assert await _scalar(lab_scratch, "SELECT attempts FROM tracelab.judge_predictions WHERE turn_id = 'turn-d'") == 2
    for turn_id in ("turn-d", "turn-e"):         # the cache only grows: not even a failure row is deleted
        await _refused(lab_scratch, "DELETE FROM tracelab.judge_predictions WHERE turn_id = :t", {"t": turn_id},
                       match="never changed")
    assert await _scalar(lab_scratch, "SELECT count(*) FROM tracelab.judge_predictions") == 2


async def test_a_prediction_is_decided_exactly_when_it_has_a_verdict_and_is_cached_once(lab_scratch):
    _, _, jid = await _base(lab_scratch)
    for kwargs in ({"status": "decided", "failure_present": None},      # decided with no verdict
                   {"status": "error", "failure_present": 1, "error": "x"},
                   {"status": "undecidable", "failure_present": 0},
                   {"status": "decided", "failure_present": 2},
                   {"status": "maybe", "failure_present": None}):
        with pytest.raises(IntegrityError):
            async with lab_scratch.begin() as conn:
                await _prediction(conn, "turn-d", jid, **kwargs)
    async with lab_scratch.begin() as conn:
        await _prediction(conn, "turn-d", jid, failure_present=0)
        await _prediction(conn, "turn-d", jid, prompt_hash="hash11111111")   # another prompt, the same turn
        await _prediction(conn, "turn-e", jid)                               # the same prompt, another turn
    with pytest.raises(IntegrityError):                                      # one decision per prompt hash and turn
        async with lab_scratch.begin() as conn:
            await _prediction(conn, "turn-d", jid)
    with pytest.raises(IntegrityError):                                      # a prediction belongs to a real judge
        async with lab_scratch.begin() as conn:
            await _prediction(conn, "turn-f", str(uuid.uuid4()))


# What refuses a TRUNCATE of each judge table, and the foreign keys to drop (inside the transaction that is then
# refused and rolled back) so that the table's own guard is what answers rather than Postgres's reference check.
TRUNCATE_GUARDS = {
    "judge_splits": ("a split is never deleted", [("judge_split_members", "judge_split_members_split_id_fkey"),
                                                  ("judges", "judges_split_id_fkey")]),
    "judge_split_members": ("append-only", []),
    "judges": ("a judge version is never deleted", [("judge_predictions", "judge_predictions_judge_id_fkey"),
                                                    ("judge_runs", "judge_runs_judge_id_fkey"),
                                                    ("monitor_verdicts", "monitor_verdicts_judge_id_fkey")]),
    "judge_predictions": ("TRUNCATE refused", []),
    "judge_runs": ("runs are kept", []),
}


@pytest.mark.parametrize("table", sorted(TRUNCATE_GUARDS))
async def test_truncate_is_refused_on_every_judge_table(lab_scratch, table):
    _, _, jid = await _base(lab_scratch)
    async with lab_scratch.begin() as conn:
        await _run(conn, jid, "dev")
        await _prediction(conn, "turn-d", jid)
    why, foreign_keys = TRUNCATE_GUARDS[table]
    with pytest.raises(DBAPIError, match=why):
        async with lab_scratch.begin() as conn:
            for child, constraint in foreign_keys:
                await conn.execute(text(f"ALTER TABLE tracelab.{child} DROP CONSTRAINT {constraint}"))
            await conn.execute(text(f"TRUNCATE tracelab.{table}"))
    for t in ("judge_splits", "judge_split_members", "judges", "judge_predictions", "judge_runs"):
        assert await _scalar(lab_scratch, f"SELECT count(*) FROM tracelab.{t}") >= 1, t


async def test_the_hatch_frees_rows_but_never_a_judge_or_its_identity(lab_scratch):
    """Under tracelab.allow_purge a company offboarding may scrub a prompt, and may remove runs, splits' members and
    predictions. A judge row is still never deleted, and its model, hash and version never change."""
    mid, sid, jid = await _base(lab_scratch)
    async with lab_scratch.begin() as conn:
        await _run(conn, jid, "dev")
        await _prediction(conn, "turn-d", jid)
    async with lab_scratch.begin() as conn:
        await conn.execute(text("SET LOCAL tracelab.allow_purge = 'on'"))
        await conn.execute(text("UPDATE tracelab.judges SET prompt_text = '[removed]', "
                                "prompt_parts = prompt_parts - 'examples' WHERE id = CAST(:j AS uuid)"), {"j": jid})
        await conn.execute(text("DELETE FROM tracelab.judge_runs"))
        await conn.execute(text("DELETE FROM tracelab.judge_predictions"))
        await conn.execute(text("DELETE FROM tracelab.judge_split_members WHERE turn_id = 'turn-e'"))
    p = {"j": jid}
    for sql, why in (("UPDATE tracelab.judges SET model = 'other' WHERE id = CAST(:j AS uuid)", "is immutable"),
                     ("UPDATE tracelab.judges SET prompt_hash = 'other' WHERE id = CAST(:j AS uuid)", "is immutable"),
                     ("DELETE FROM tracelab.judges WHERE id = CAST(:j AS uuid)", "never deleted")):
        with pytest.raises(DBAPIError, match=why):
            async with lab_scratch.begin() as conn:
                await conn.execute(text("SET LOCAL tracelab.allow_purge = 'on'"))
                await conn.execute(text(sql), p)
    scrubbed = await _scalar(lab_scratch, "SELECT prompt_text FROM tracelab.judges WHERE id = CAST(:j AS uuid)", p)
    assert scrubbed == "[removed]"
    assert await _scalar(lab_scratch, "SELECT count(*) FROM tracelab.judge_split_members") == len(TRAIN3) - 1
    # off by default and not left on: the next transaction has no hatch
    await _refused(lab_scratch, "UPDATE tracelab.judges SET prompt_text = 'back' WHERE id = CAST(:j AS uuid)", p,
                   match="the prompt of")


# ── the judge store end to end (Phase 6 task 3): real SQL on both scratch schemas, a stubbed model ───────────────────
# Chat turns and spans sit in the (read-only) main schema, labels, modes, splits, judges and runs in the lab schema,
# exactly as in production where they are two databases. Only the judge model is replaced (judges.ASK).

LATER = datetime.now(timezone.utc) + timedelta(hours=1)        # a label must be newer than its mode (the database's now())


def _tid(i: int) -> str:
    return f"turn-e2e{i:012d}"


async def _thread(conn, session_id: str, org: str = fx.ORG_A) -> None:
    await conn.execute(text("INSERT INTO plenum_cafm.chat_threads (id, organization_id, user_id, title) "
                            "VALUES (:id, CAST(:o AS uuid), CAST(:u AS uuid), 'A conversation')"),
                       {"id": session_id, "o": org, "u": str(uuid.uuid4())})


async def _e2e(lab, main, monkeypatch, *, n_fail: int = 30, n_pass: int = 30, org: str = fx.ORG_A,
               mode_status: str = "confirmed", level: str = "technical"):
    """60 traced answers in one company, labelled 30 Fail / 30 Pass one at a time by a person, one mode, and a fake
    judge model that reads the site number from the evidence (the answers in ns.misses are deliberate misses)."""
    turns = [_tid(i) for i in range(n_fail + n_pass)]
    async with main.begin() as conn:
        for i, t in enumerate(turns):
            await fx.add_turn(conn, t, org, session_id=f"sess-{i}", answer=f"Site {i} has open jobs.",
                              question=f"How many open jobs at site number {i} today?")
            await fx.add_span(conn, t, org, kind="tool", name="query_table", seq=1, input={"table": "work_orders"},
                              output={"count": 1})
    async with lab.begin() as conn:
        mid = await fx.add_mode(conn, "invented_codes_e2e", status=mode_status, agent="udr", level=level,
                                definition="Present when the answer names a code no step returned.")
        for i, t in enumerate(turns):
            await fx.add_label(conn, t, mid, present=i < n_fail, org=org, created_at=LATER + timedelta(seconds=i))
    ns = types.SimpleNamespace(mode_id=mid, org=org, turns=turns, calls=[], errors=set(), principal=fx.owner(),
                               judges=judges, main=main, lab=lab, usd=0.01, on_call=None, misses={3})

    async def fake_ask(model, prompt_text, judge_text):
        i = int(re.search(r"site number (\d+)", judge_text).group(1))
        ns.calls.append(i)
        if ns.on_call:
            await ns.on_call(i)
        if i in ns.errors:
            return judges.JudgeReply(None, "", "error", None, "upstream timeout")
        fail = (i < n_fail) != (i in ns.misses)
        return judges.JudgeReply(1 if fail else 0, f"Read answer {i}.", "decided", ns.usd, None)

    monkeypatch.setattr(judges, "judge_model", lambda: "claude-sonnet-5")
    monkeypatch.setattr(judges, "ASK", fake_ask)
    return ns


async def _drop_label(ns, i: int) -> None:
    """The person clears their label on answer i (a tombstone through the real label path)."""
    cell = next(c for c in await labels.human_origin_cells(ns.mode_id) if c["turn_id"] == _tid(i))
    await labels.set_label(fx.owner(), None, _tid(i), ns.mode_id, present=None, base=cell["label_id"])


async def test_split_is_stored_with_members_and_snapshots(lab_scratch, main_scratch, monkeypatch):
    ns = await _e2e(lab_scratch, main_scratch, monkeypatch)
    j = ns.judges
    ready = await j.readiness(ns.principal, ns.mode_id)
    assert (ready["labels"]["groups_fail"], ready["labels"]["groups_pass"], ready["next_step"]) == (30, 30, "create_split")
    assert ready["minimum"] == {"per_class": 30, "eval_per_class": 10} and ready["split"] is None
    split = await j.create_split(ns.principal, ns.mode_id)
    assert split["version"] == 1 and split["counts"]["dev"] == {"fail": 12, "pass": 12} and split["badge"] is None
    assert len(split["members"]) == 60 and all(m["excerpt"].startswith("QUESTION\n") for m in split["members"])
    assert all(m["conversation_key"].startswith("turn:") for m in split["members"])     # no chat thread: per turn
    async with lab_scratch.connect() as conn:                     # every judged answer is kept as evidence (D5)
        kept = [tuple(r) for r in (await conn.execute(text(
            "SELECT count(*), count(DISTINCT reason), min(reason) FROM tracelab.evidence_turns"))).all()]
        spans = (await conn.execute(text("SELECT count(*) FROM tracelab.evidence_spans"))).scalar_one()
    assert kept == [(60, 1, "judged")] and spans == 60            # each answer's one tool step came with it
    again = await j.create_split(ns.principal, ns.mode_id)
    assert again["version"] == 2
    async with lab_scratch.connect() as conn:
        states = [tuple(r) for r in (await conn.execute(text(
            "SELECT version, status FROM tracelab.judge_splits ORDER BY version"))).all()]
    assert states == [(1, "retired"), (2, "active")]
    await _drop_label(ns, 0)                                      # 29 Fail left
    with pytest.raises(j.JudgeError) as e:
        await j.create_split(ns.principal, ns.mode_id)
    assert e.value.reason == "labels_too_few"
    with pytest.raises(j.JudgeError) as e:
        await j.create_split(ns.principal, ns.mode_id, override_min_labels=True, override_reason=" ")
    assert e.value.reason == "override_reason_required"
    thin = await j.create_split(ns.principal, ns.mode_id, override_min_labels=True,
                                override_reason="Pilot volume; approved by Hussain on 15 Oct.")
    assert thin["badge"] == "Built below the label minimum (30 Pass / 29 Fail)"
    assert (await j.get_active_split(ns.mode_id))["id"] == thin["id"]
    async with lab_scratch.connect() as conn:
        assert (await conn.execute(text("SELECT count(*) FROM tracelab.evidence_turns"))).scalar_one() == 60


async def test_readiness_says_what_is_missing_and_only_the_platform_owner_asks(lab_scratch, main_scratch, monkeypatch):
    ns = await _e2e(lab_scratch, main_scratch, monkeypatch, n_fail=5, n_pass=5)
    j = ns.judges
    ready = await j.readiness(ns.principal, ns.mode_id)
    assert (ready["labels"]["groups_fail"], ready["labels"]["groups_pass"], ready["next_step"]) == (5, 5, "label_more")
    assert ready["mode"]["name"] == "invented_codes_e2e" and ready["judges"] == [] and ready["labels"]["excluded"] == 0
    with pytest.raises(j.JudgeError) as e:
        await j.create_split(ns.principal, ns.mode_id)
    assert e.value.reason == "labels_too_few"
    with pytest.raises(j.JudgeError) as e:                       # 5 of each class cannot fill a dev and a test of 10
        await j.create_split(ns.principal, ns.mode_id, override_min_labels=True, override_reason="Pilot.")
    assert (e.value.status, e.value.reason) == (409, "split_too_thin")
    for call in (j.readiness(fx.admin(), ns.mode_id), j.create_split(fx.admin(), ns.mode_id)):
        with pytest.raises(j.JudgeError) as e:
            await call
        assert (e.value.status, e.value.reason) == (403, "platform_only")
    for bad in (str(uuid.uuid4()), "not-an-id"):
        with pytest.raises(j.JudgeError) as e:
            await j.readiness(ns.principal, bad)
        assert (e.value.status, e.value.reason) == (404, "mode_not_found")
        with pytest.raises(j.JudgeError) as e:
            await j.get_active_split(bad)
        assert e.value.status == 404
    with pytest.raises(j.JudgeError) as e:
        await j.get_active_split(ns.mode_id)
    assert (e.value.status, e.value.reason) == (404, "no_active_split")
    async with lab_scratch.connect() as conn:                     # a refused split keeps nothing
        assert (await conn.execute(text("SELECT count(*) FROM tracelab.evidence_turns"))).scalar_one() == 0
        assert (await conn.execute(text("SELECT count(*) FROM tracelab.judge_splits"))).scalar_one() == 0


@pytest.mark.parametrize("status, level, reason", [("candidate", "technical", "mode_not_confirmed"),
                                                   ("confirmed", "data", "data_fault_mode")])
async def test_only_a_confirmed_judge_eligible_mode_gets_a_split(lab_scratch, main_scratch, monkeypatch, status, level,
                                                                 reason):
    ns = await _e2e(lab_scratch, main_scratch, monkeypatch, mode_status=status, level=level)
    with pytest.raises(judges.JudgeError) as e:
        await judges.create_split(ns.principal, ns.mode_id)
    assert (e.value.status, e.value.reason) == (409, reason)


async def test_the_loader_reads_each_company_from_the_chat_and_then_from_its_snapshot(lab_scratch, main_scratch,
                                                                                      monkeypatch):
    a, b = fx.ORG_A, fx.ORG_B
    async with main_scratch.begin() as conn:
        await _thread(conn, "sess-seen", a)
        for t, org, sid, at in (("turn-aaa-1", a, "sess-seen", LATER - timedelta(hours=3)),
                                ("turn-aaa-2", a, "sess-seen", LATER - timedelta(hours=2)),
                                ("turn-aaa-3", a, "sess-seen", LATER - timedelta(hours=1)),
                                ("turn-bbb-1", b, "sess-b", LATER - timedelta(hours=1))):
            await fx.add_turn(conn, t, org, session_id=sid, started_at=at,
                              question=f"Question of {t}: how many open jobs are there?", answer=f"Answer of {t}.")
        await fx.add_span(conn, "turn-aaa-3", a, kind="tool", name="query_table", seq=1, input={"q": 1}, output={"n": 4})
        await fx.add_span(conn, "turn-aaa-3", a, kind="db", name="sql", seq=2, input={"sql": "SELECT 1", "label": "jobs"},
                          output={"rows": [{"n": 4}], "row_count": 1})
        await fx.add_span(conn, "turn-bbb-1", b, kind="tool", name="b_tool", seq=1, input={}, output={"secret": "b"})
    await evidence.snapshot(["turn-aaa-3"], organization_id=a, reason="judged")
    async with main_scratch.begin() as conn:                     # the chat purged this answer and its steps
        await conn.execute(text("DELETE FROM plenum_cafm.agent_trace_spans WHERE turn_id = 'turn-aaa-3'"))
        await conn.execute(text("DELETE FROM plenum_cafm.agent_trace_turns WHERE turn_id = 'turn-aaa-3'"))
    got = await judges.load_judge_turns(["turn-aaa-3", "turn-bbb-1", "turn-nowhere", "turn-aaa-3", ""])
    assert sorted(got) == ["turn-aaa-3", "turn-bbb-1"]
    purged, other = got["turn-aaa-3"], got["turn-bbb-1"]
    assert (purged.organization_id, other.organization_id) == (a, b)
    assert [t["name"] for t in purged.tools] == ["query_table"] and [d["label"] for d in purged.db] == ["jobs"]
    assert purged.db[0]["rows"] == [{"n": 4}] and purged.spans_purged is False       # read from the snapshot's spans
    assert purged.question.startswith("Question of turn-aaa-3") and purged.channel == "chat"
    assert [p["turn_id"] for p in purged.prior] == ["turn-aaa-1", "turn-aaa-2"]       # an observed conversation
    assert purged.session_kind == "observed" and other.session_kind == "inferred" and other.prior == ()
    assert other.tools[0]["output"] == {"secret": "b"} and purged.started_at is not None
    facts = await judges.turn_facts(["turn-aaa-3", "turn-bbb-1", "turn-nowhere"])
    assert sorted(facts) == ["turn-aaa-3", "turn-bbb-1"]
    assert facts["turn-aaa-3"]["conversation_key"] == "session:sess-seen"             # the chat's thread still exists
    assert facts["turn-bbb-1"]["conversation_key"] == "turn:turn-bbb-1" and facts["turn-bbb-1"]["organization_id"] == b
    assert facts["turn-aaa-3"]["question"].startswith("Question of turn-aaa-3")


async def test_conversations_group_by_session_and_a_golden_case_belongs_to_its_company(lab_scratch, main_scratch,
                                                                                       monkeypatch):
    a, b = fx.ORG_A, fx.ORG_B
    async with main_scratch.begin() as conn:
        await _thread(conn, "sess-one", a)
        for t, sid in (("turn-s-1", "sess-one"), ("turn-s-2", "sess-one"), ("turn-s-3", "sess-solo")):
            await fx.add_turn(conn, t, a, session_id=sid, question=f"Distinct question for {t} about work orders?")
    golden = {}
    async with lab_scratch.begin() as conn:
        mid = await fx.add_mode(conn, "invented_codes_keys", status="confirmed", agent="udr")
        for t in ("turn-s-1", "turn-s-2", "turn-s-3"):
            await fx.add_label(conn, t, mid, org=a, created_at=LATER)
        for org in (a, b):                                        # both companies' sets have a case "1"
            s = await golden_seed.seed_set(conn, org)
            run = await golden_seed.seed_run(conn, org, s["set_id"])
            _, tid = await golden_seed.seed_result(conn, org, run, s["cases"]["1"], "1", turn_id=f"turn-g{org[:4]}")
            golden[org] = tid
            await conn.execute(text(
                "INSERT INTO tracelab.evidence_turns (turn_id, organization_id, turn, reason) VALUES (:t, CAST(:o AS uuid), "
                "CAST(:j AS jsonb), 'golden')"),
                {"t": tid, "o": org, "j": json.dumps({"turn_id": tid, "session_id": f"golden-{org[:8]}-1-r1",
                                                      "organization_id": org, "question": f"Golden question {org[:4]}?",
                                                      "answer": "A golden answer.", "started_at": LATER.isoformat()})})
            await fx.add_label(conn, tid, mid, org=org, created_at=LATER)
        await fx.add_label(conn, "turn-vanished", mid, org=a, created_at=LATER)      # a label on an answer nobody holds
    records, unavailable = await judges.label_records(mid)
    keys = {r["turn_id"]: r["conversation_key"] for r in records}
    assert keys["turn-s-1"] == keys["turn-s-2"] == "session:sess-one" and keys["turn-s-3"] == "turn:turn-s-3"
    assert keys[golden[a]] == f"golden:{a}/1" and keys[golden[b]] == f"golden:{b}/1"     # never merged across companies
    assert unavailable == [{"turn_id": "turn-vanished", "group_key": None, "reason": "turn_unavailable"}]
    assert {r["organization_id"] for r in records} == {a, b}
    facts = await judges.turn_facts([golden[a], golden[b]])
    assert facts[golden[a]]["golden_case_key"] == "1" and facts[golden[b]]["golden_case_key"] == "1"
    kept_eval, _, excluded = judges.group_records(records)
    assert len(kept_eval) == 4 and [x["reason"] for x in excluded] == ["same_conversation"]
    ready = await judges.readiness(fx.owner(), mid)
    assert ready["labels"]["excluded"] == 2                      # one repeated conversation and one unavailable answer


async def _register_v0(ns, *, kind: str = "llm", fail_rule: str = "The answer names a code that no step returned.",
                       change_note: str = "", principal=None):
    j = ns.judges
    split = await j.active_split(ns.mode_id) or await j.create_split(ns.principal, ns.mode_id)
    who = principal or ns.principal
    if kind == "code":
        return await j.register(who, mode_id=ns.mode_id, kind="code", check_key="invented_identifiers",
                                change_note=change_note or "A code check for the same mode.")
    train = [m for m in split["members"] if m["split"] == "train"]
    f = next(m for m in train if m["label_present"])
    p = next(m for m in train if not m["label_present"])
    b = next(m for m in train if m["turn_id"] not in (f["turn_id"], p["turn_id"]))
    examples = [{"turn_id": f["turn_id"], "role": "clear_fail", "critique": "Names a code that no step returned.",
                 "verdict": "Fail"},
                {"turn_id": p["turn_id"], "role": "clear_pass", "critique": "Every code came from a step.", "verdict": "Pass"},
                {"turn_id": b["turn_id"], "role": "borderline", "critique": "Close call; the code is only implied.",
                 "verdict": "Fail" if b["label_present"] else "Pass"}]
    return await j.register(who, mode_id=ns.mode_id, kind="llm", change_note=change_note,
                            parts={"fail_rule": fail_rule, "pass_rule": "Every code in the answer was returned by a step."},
                            examples=examples)


async def test_register_list_and_get_a_judge(lab_scratch, main_scratch, monkeypatch):
    ns = await _e2e(lab_scratch, main_scratch, monkeypatch)
    j = ns.judges
    judge, warnings = await _register_v0(ns)
    assert (judge["version"], judge["judge_key"], judge["status"], warnings) == (0, "invented_codes_e2e-v0", "draft", [])
    with pytest.raises(j.JudgeError) as e:
        await _register_v0(ns)
    assert e.value.reason == "same_prompt" and e.value.extra["judge_id"] == judge["id"]
    code, _ = await _register_v0(ns, kind="code")
    assert (code["version"], code["kind"], code["check_key"]) == (1, "code", "invented_identifiers")
    listed = await j.list_judges(ns.principal, None, full=False)
    assert [x["version"] for x in listed["judges"]] == [0, 1]
    assert not ({"prompt_hash", "model", "change_note", "rev"} & set(listed["judges"][0]))
    assert listed["modes"] == [{"id": ns.mode_id, "name": "invented_codes_e2e", "status": "confirmed"}]
    full = await j.list_judges(ns.principal, None, full=True)
    assert full["judges"][0]["model"] == "claude-sonnet-5" and len(full["judges"][0]["prompt_hash"]) == 12
    assert [x["version"] for x in (await j.list_judges(ns.principal, None, mode_id=ns.mode_id, full=True))["judges"]] == [0, 1]
    assert (await j.list_judges(ns.principal, None, mode_id=str(uuid.uuid4()), full=True))["judges"] == []
    detail = await j.get_judge(judge["id"])
    assert detail["judge"]["prompt_text"].startswith("You are evaluating one answer from Hoist")
    assert len(detail["judge"]["prompt_parts"]["examples"]) == 3
    assert all(ex["excerpt"].startswith("QUESTION\n") for ex in detail["judge"]["prompt_parts"]["examples"])
    assert len(detail["preview_turn_ids"]) == 5 and detail["runs"] == []
    assert [r["version"] for r in detail["iterations"]] == [0, 1]
    assert detail["test_companies"] == [{"organization_id": ns.org, "fail": 12, "pass": 12}]
    assert (await j.get_judge(code["id"]))["judge"]["check_version"] == 1
    for bad in (str(uuid.uuid4()), "not-an-id"):
        with pytest.raises(j.JudgeError) as e:
            await j.get_judge(bad)
        assert (e.value.status, e.value.reason) == (404, "judge_not_found")
    assert await j.fetch_judge(judge["id"]) is not None and await j.fetch_judge(str(uuid.uuid4())) is None
    assert await j.fetch_run(str(uuid.uuid4())) is None


async def test_a_company_admin_reads_the_judges_list_without_prompts_and_cannot_register(lab_scratch, main_scratch,
                                                                                         monkeypatch):
    ns = await _e2e(lab_scratch, main_scratch, monkeypatch)
    j = ns.judges
    await _register_v0(ns)
    brief = await j.list_judges(fx.admin(), fx.ORG_A, full=False)
    assert [x["judge_key"] for x in brief["judges"]] == ["invented_codes_e2e-v0"]
    assert not ({"prompt_hash", "model", "change_note", "rev", "stop_reason", "split_id"} & set(brief["judges"][0]))
    assert brief["modes"][0]["name"] == "invented_codes_e2e"
    with pytest.raises(j.JudgeError) as e:                       # defence in depth: the route also refuses
        await _register_v0(ns, kind="code", principal=fx.admin())
    assert (e.value.status, e.value.reason) == (403, "platform_only")


async def test_register_refuses_what_cannot_be_a_judge(lab_scratch, main_scratch, monkeypatch):
    ns = await _e2e(lab_scratch, main_scratch, monkeypatch)
    j = ns.judges
    async with lab_scratch.begin() as conn:
        bare = await fx.add_mode(conn, "mode_with_no_split", status="confirmed", agent="udr")
        cand = await fx.add_mode(conn, "mode_still_a_candidate", status="candidate", agent="udr")
    for mid, kind_args, reason in (
            (bare, {"kind": "code", "check_key": "invented_identifiers"}, "no_active_split"),
            (cand, {"kind": "code", "check_key": "invented_identifiers"}, "mode_not_confirmed"),
            (str(uuid.uuid4()), {"kind": "code", "check_key": "invented_identifiers"}, "mode_not_found")):
        with pytest.raises(j.JudgeError) as e:
            await j.register(ns.principal, mode_id=mid, **kind_args)
        assert e.value.reason == reason, (mid, reason)
    split = await j.create_split(ns.principal, ns.mode_id)
    train = [m for m in split["members"] if m["split"] == "train"]
    fail = next(m for m in train if m["label_present"])
    dev = next(m for m in split["members"] if m["split"] == "dev")
    base = {"mode_id": ns.mode_id, "kind": "llm"}
    parts = {"fail_rule": "A rule.", "pass_rule": "Another rule."}
    for kwargs, reason in (({"kind": "robot"}, "bad_kind"),
                           ({"kind": "code", "check_key": "no_such_check"}, "unknown_check"),
                           ({"kind": "code", "check_key": "expected_values"}, "unknown_check"),     # a case check, not a live one
                           ({"parts": None, "examples": []}, "parts_required"),
                           ({"parts": {"fail_rule": " ", "pass_rule": "x"}, "examples": []}, "parts_required"),
                           ({"parts": parts, "examples": []}, "examples_required"),
                           ({"parts": parts, "examples": [
                               {"turn_id": dev["turn_id"], "role": "clear_fail", "critique": "c", "verdict": "Fail"},
                               {"turn_id": fail["turn_id"], "role": "clear_pass", "critique": "c", "verdict": "Pass"},
                               {"turn_id": train[1]["turn_id"], "role": "borderline", "critique": "c", "verdict": "Fail"}]},
                            "example_not_in_train")):
        with pytest.raises(j.JudgeError) as e:
            await j.register(ns.principal, **{**base, **kwargs})
        assert e.value.reason == reason, (kwargs, e.value.reason)
    monkeypatch.setattr(j, "judge_model", lambda: "a-model-with-no-price")
    with pytest.raises(j.JudgeError) as e:
        await _register_v0(ns)
    assert e.value.reason == "model_unpriced"
    async with lab_scratch.connect() as conn:                    # every refusal left the judges table empty
        assert (await conn.execute(text("SELECT count(*) FROM tracelab.judges"))).scalar_one() == 0


async def test_later_versions_need_a_change_note_and_warn_past_two_revisions_and_below_the_minimum(
        lab_scratch, main_scratch, monkeypatch):
    ns = await _e2e(lab_scratch, main_scratch, monkeypatch)
    j = ns.judges
    await _drop_label(ns, 0)
    await j.create_split(ns.principal, ns.mode_id, override_min_labels=True, override_reason="Pilot volume; approved.")
    v0, warnings = await _register_v0(ns)
    assert warnings == [{"code": "built_below_minimum", "message": "Built below the label minimum (30 Pass / 29 Fail)"}]
    assert v0["min_labels_override"] == "Built below the label minimum (30 Pass / 29 Fail)"
    with pytest.raises(j.JudgeError) as e:
        await _register_v0(ns, fail_rule="A second rule.")
    assert (e.value.status, e.value.reason) == (422, "change_note_required")
    codes = []
    for n in range(1, 4):
        v, warnings = await _register_v0(ns, fail_rule=f"Revision {n} of the rule.", change_note=f"Tightened the rule ({n}).")
        assert v["version"] == n
        codes.append([w["code"] for w in warnings])
    assert codes == [["built_below_minimum"], ["built_below_minimum"], ["beyond_revision_limit", "built_below_minimum"]]
    rows = await j.iteration_log(ns.mode_id)
    assert [(r["version"], r["change_note"]) for r in rows] == [
        (0, ""), (1, "Tightened the rule (1)."), (2, "Tightened the rule (2)."), (3, "Tightened the rule (3).")]
    assert all(r["dev_runs"] == 0 and r["test"] is None and r["frozen"] is False for r in rows)
    assert await j.iteration_log(str(uuid.uuid4())) == []


def _split_numbers(split: dict, which: str, *, present: bool | None = None) -> list[int]:
    return sorted(int(m["turn_id"][-12:]) for m in split["members"]
                  if m["split"] == which and (present is None or m["label_present"] == present))


async def _dev_done(ns, judge_id: str) -> dict:
    j = ns.judges
    out = await j.start_run(ns.principal, judge_id, split="dev", schedule=False)
    await j.work_judge_run(out["run"]["id"])
    return await j.get_run(out["run"]["id"])


async def _jobs(lab, kind: str = "judge_run") -> list[dict]:
    async with lab.connect() as conn:
        return [dict(r) for r in (await conn.execute(text(
            "SELECT id::text AS id, status, usd_spent, usd_cap, usd_reserved, done, total, error FROM tracelab.jobs "
            "WHERE kind = :k ORDER BY created_at"), {"k": kind})).mappings().all()]


async def test_dev_run_caches_and_an_incomplete_run_resumes(lab_scratch, main_scratch, monkeypatch):
    ns = await _e2e(lab_scratch, main_scratch, monkeypatch)
    j = ns.judges
    judge, _ = await _register_v0(ns)
    est = await j.estimate(judge["id"], "dev")
    assert (est["items"], est["cached"], est["to_call"]) == (24, 0, 24) and est["usd_estimate"] > 0
    assert est["model"] == "claude-sonnet-5"
    dev_ids = _split_numbers(await j.active_split(ns.mode_id), "dev")
    ns.errors = set(dev_ids[:4])
    first = await j.start_run(ns.principal, judge["id"], split="dev", schedule=False)
    assert first["run"]["status"] == "queued" and first["job"]["status"] == "queued"
    await j.work_judge_run(first["run"]["id"])
    run = await j.get_run(first["run"]["id"])
    assert (run["status"], run["n_error"], len(ns.calls)) == ("incomplete", 4, 24)
    assert run["tpr"] is None and run["disagreements"] == [] and run["job"]["status"] == "done"
    ns.errors = set()
    again = await j.start_run(ns.principal, judge["id"], split="dev", schedule=False)
    assert again["run"]["id"] == first["run"]["id"]                       # resumed, not a new run
    await j.work_judge_run(again["run"]["id"])
    run = await j.get_run(first["run"]["id"])
    assert run["status"] == "done" and len(ns.calls) == 28                # only the 4 errors were asked again
    assert run["tpr"] is not None and run["job"]["status"] == "done" and run["items"] == 24
    assert run["usd"] == pytest.approx(0.24) and run["tpr_interval"][0] <= run["tpr"] <= run["tpr_interval"][1]
    assert (run["tp"] + run["fn"] + run["tn"] + run["fp"], run["n"], run["n_error"]) == (24, 24, 0)
    async with lab_scratch.connect() as conn:                              # an error row is retried in place
        rows = [tuple(r) for r in (await conn.execute(text(
            "SELECT attempts, status FROM tracelab.judge_predictions WHERE turn_id = ANY(CAST(:t AS text[])) "
            "ORDER BY turn_id"), {"t": [_tid(i) for i in dev_ids[:4]]})).all()]
        assert (await conn.execute(text("SELECT count(*) FROM tracelab.judge_predictions"))).scalar_one() == 24
    assert rows == [(2, "decided")] * 4
    jobs_ = await _jobs(lab_scratch)
    assert [(x["status"], x["done"]) for x in jobs_] == [("done", 24), ("done", 4)]
    assert sum(float(x["usd_spent"]) for x in jobs_) == pytest.approx(0.24) and all(float(x["usd_reserved"]) == 0 for x in jobs_)
    fresh = await _dev_done(ns, judge["id"])
    assert fresh["id"] != run["id"] and fresh["status"] == "done" and len(ns.calls) == 28    # the cache answered all 24
    assert fresh["usd"] == 0 and fresh["tpr"] == run["tpr"]
    preview = await j.preview(ns.principal, judge["id"], (await j.get_judge(judge["id"]))["preview_turn_ids"][0])
    assert preview["usd"] == 0 and preview["verdict"] in ("Pass", "Fail") and len(ns.calls) == 28
    test_turn = next(m["turn_id"] for m in (await j.active_split(ns.mode_id))["members"] if m["split"] == "test")
    with pytest.raises(j.JudgeError) as e:
        await j.preview(ns.principal, judge["id"], test_turn)
    assert e.value.reason == "test_split_locked"
    est2 = await j.estimate(judge["id"], "dev")
    assert (est2["cached"], est2["to_call"], est2["usd_estimate"]) == (24, 0, 0)


async def test_a_dev_run_reads_each_disagreement_with_its_answer(lab_scratch, main_scratch, monkeypatch):
    ns = await _e2e(lab_scratch, main_scratch, monkeypatch)
    j = ns.judges
    split = await j.create_split(ns.principal, ns.mode_id)
    miss_fail = _split_numbers(split, "dev", present=True)[0]            # a Fail the judge calls Pass
    false_alarm = _split_numbers(split, "dev", present=False)[0]         # a Pass the judge calls Fail
    ns.misses = {miss_fail, false_alarm}
    judge, _ = await _register_v0(ns)
    run = await _dev_done(ns, judge["id"])
    assert run["status"] == "done" and (run["tp"], run["fn"], run["tn"], run["fp"]) == (11, 1, 11, 1)
    assert run["tpr"] == pytest.approx(11 / 12, abs=1e-4) and run["agreement"] == pytest.approx(22 / 24, abs=1e-4)
    assert run["untriaged"] == 2 and run["disagreement_count"] == 2
    by_kind = {d["kind"]: d for d in run["disagreements"]}
    assert by_kind["missed_failure"]["turn_id"] == _tid(miss_fail) and by_kind["false_alarm"]["turn_id"] == _tid(false_alarm)
    assert all(d["excerpt"].startswith("QUESTION\n") and d["triage"] is None for d in run["disagreements"])
    assert run["label_flips"] == 0 and run["job"]["usd_cap"] == pytest.approx(5.0)
    listed = await j.get_judge(judge["id"])
    assert listed["judge"]["display_status"] == "dev" and listed["runs"][0]["id"] == run["id"]
    assert listed["judge"]["dev"]["n"] == 24 and listed["judge"]["dev"]["tpr"] == run["tpr"]
    assert (await j.readiness(ns.principal, ns.mode_id))["next_step"] == "triage"


async def test_a_run_over_its_cap_is_refused_and_one_that_reaches_it_stops_and_resumes_with_more(
        lab_scratch, main_scratch, monkeypatch):
    ns = await _e2e(lab_scratch, main_scratch, monkeypatch)
    j = ns.judges
    judge, _ = await _register_v0(ns)
    est = (await j.estimate(judge["id"], "dev"))["usd_estimate"]
    for cap in (0, -1, float("nan"), float("inf")):
        with pytest.raises(j.JudgeError) as e:
            await j.start_run(ns.principal, judge["id"], split="dev", usd_cap=cap, schedule=False)
        assert (e.value.status, e.value.reason) == (422, "bad_cap")
    with pytest.raises(j.JudgeError) as e:
        await j.start_run(ns.principal, judge["id"], split="dev", usd_cap=est / 2, schedule=False)
    assert (e.value.status, e.value.reason) == (409, "over_cap") and e.value.extra["usd_estimate"] == est
    assert await _jobs(lab_scratch) == []                                  # a refusal created nothing
    async with lab_scratch.connect() as conn:
        assert (await conn.execute(text("SELECT count(*) FROM tracelab.judge_runs"))).scalar_one() == 0
    ns.usd = 0.05                                                          # the real calls cost more than estimated
    out = await j.start_run(ns.principal, judge["id"], split="dev", usd_cap=est, schedule=False)
    await j.work_judge_run(out["run"]["id"])
    run = await j.get_run(out["run"]["id"])
    assert run["status"] == "capped" and run["job"]["status"] == "capped" and run["finished_at"] is not None
    assert 0 < len(ns.calls) < 24 and run["tpr"] is None and run["usd"] <= est + 0.05
    spent_first, calls_first = run["usd"], len(ns.calls)
    more = await j.start_run(ns.principal, judge["id"], split="dev", usd_cap=5.0, schedule=False)
    assert more["run"]["id"] == out["run"]["id"] and more["job"]["id"] != out["job"]["id"]      # same run, a new job
    await j.work_judge_run(more["run"]["id"])
    run = await j.get_run(out["run"]["id"])
    assert run["status"] == "done" and len(ns.calls) == 24 and run["usd_cap"] == 5.0
    assert run["usd"] == pytest.approx(0.05 * 24) and run["usd"] > spent_first and calls_first < 24
    jobs_ = await _jobs(lab_scratch)
    assert [x["status"] for x in jobs_] == ["capped", "done"]
    assert sum(float(x["usd_spent"]) for x in jobs_) == pytest.approx(run["usd"])     # the Platform cost reads these


async def test_a_stop_request_pauses_a_run_and_the_next_start_continues_the_same_job(lab_scratch, main_scratch,
                                                                                     monkeypatch):
    ns = await _e2e(lab_scratch, main_scratch, monkeypatch)
    j = ns.judges
    judge, _ = await _register_v0(ns)
    out = await j.start_run(ns.principal, judge["id"], split="dev", schedule=False)
    from trace_lab import jobs

    assert await jobs.request_stop(out["job"]["id"])
    await j.work_judge_run(out["run"]["id"])
    run = await j.get_run(out["run"]["id"])
    assert (run["status"], run["job"]["status"], ns.calls) == ("interrupted", "paused", [])
    again = await j.start_run(ns.principal, judge["id"], split="dev", schedule=False)
    assert (again["run"]["id"], again["job"]["id"]) == (out["run"]["id"], out["job"]["id"])
    await j.work_judge_run(out["run"]["id"])
    run = await j.get_run(out["run"]["id"])
    assert run["status"] == "done" and run["job"]["status"] == "done" and len(ns.calls) == 24


async def test_a_run_whose_job_the_sweep_took_away_is_interrupted_and_pays_for_nothing_more(lab_scratch, main_scratch,
                                                                                            monkeypatch):
    ns = await _e2e(lab_scratch, main_scratch, monkeypatch)
    j = ns.judges
    judge, _ = await _register_v0(ns)
    out = await j.start_run(ns.principal, judge["id"], split="dev", schedule=False)

    async def sweep_takes_the_job(i: int) -> None:
        if len(ns.calls) == 3:
            async with lab_scratch.begin() as conn:
                await conn.execute(text("UPDATE tracelab.jobs SET status = 'interrupted' WHERE id = CAST(:j AS uuid)"),
                                   {"j": out["job"]["id"]})

    ns.on_call = sweep_takes_the_job
    await j.work_judge_run(out["run"]["id"])
    run = await j.get_run(out["run"]["id"])
    assert run["status"] == "interrupted" and run["job"]["status"] == "interrupted"   # not failed, not capped
    assert 3 <= len(ns.calls) < 24
    ns.on_call = None
    spent = float((await _jobs(lab_scratch))[0]["usd_spent"])
    assert spent > 0                                                       # the calls in flight were still paid for
    again = await j.start_run(ns.principal, judge["id"], split="dev", schedule=False)
    assert (again["run"]["id"], again["job"]["id"]) == (out["run"]["id"], out["job"]["id"])
    assert again["run"]["job"]["usd_cap"] == pytest.approx(5.0)            # what this job spent plus what the run's cap left
    await j.work_judge_run(out["run"]["id"])
    assert (await j.get_run(out["run"]["id"]))["status"] == "done" and len(ns.calls) == 24


async def test_a_crash_in_the_worker_marks_the_run_and_its_job_failed_and_a_new_run_can_start(lab_scratch, main_scratch,
                                                                                              monkeypatch):
    ns = await _e2e(lab_scratch, main_scratch, monkeypatch)
    j = ns.judges
    judge, _ = await _register_v0(ns)
    out = await j.start_run(ns.principal, judge["id"], split="dev", schedule=False)

    async def broken(*a, **k):
        raise RuntimeError("the lab database dropped the connection")

    real = j.write_prediction
    monkeypatch.setattr(j, "write_prediction", broken)
    await j.work_judge_run(out["run"]["id"])                              # never raises
    run = await j.get_run(out["run"]["id"])
    assert run["status"] == "failed" and "dropped the connection" in run["error"]
    assert run["job"]["status"] == "failed" and run["finished_at"] is not None
    monkeypatch.setattr(j, "write_prediction", real)
    again = await j.start_run(ns.principal, judge["id"], split="dev", schedule=False)
    assert again["run"]["id"] != out["run"]["id"]                         # a failed dev run is not resumed
    await j.work_judge_run(again["run"]["id"])
    assert (await j.get_run(again["run"]["id"]))["status"] == "done"


async def test_runs_are_refused_while_one_is_going_and_for_a_judge_that_is_not_there(lab_scratch, main_scratch,
                                                                                     monkeypatch):
    ns = await _e2e(lab_scratch, main_scratch, monkeypatch)
    j = ns.judges
    judge, _ = await _register_v0(ns)
    started = await j.start_run(ns.principal, judge["id"], split="dev", schedule=False)
    with pytest.raises(j.JudgeError) as e:
        await j.start_run(ns.principal, judge["id"], split="dev", schedule=False)
    assert (e.value.status, e.value.reason) == (409, "run_in_progress")
    with pytest.raises(j.JudgeError) as e:                                 # the held-out test waits for the freeze
        await j.start_run(ns.principal, judge["id"], split="test", schedule=False)
    assert e.value.reason == "not_frozen"
    for split in ("train", "", None):
        with pytest.raises(j.JudgeError) as e:
            await j.start_run(ns.principal, judge["id"], split=split, schedule=False)
        assert (e.value.status, e.value.reason) == (422, "bad_split")
        with pytest.raises(j.JudgeError) as e:
            await j.estimate(judge["id"], split)
        assert e.value.reason == "bad_split"
    for bad in (str(uuid.uuid4()), "not-an-id"):
        with pytest.raises(j.JudgeError) as e:
            await j.start_run(ns.principal, bad, split="dev", schedule=False)
        assert (e.value.status, e.value.reason) == (404, "judge_not_found")
        with pytest.raises(j.JudgeError) as e:
            await j.get_run(bad)
        assert (e.value.status, e.value.reason) == (404, "run_not_found")
    with pytest.raises(j.JudgeError) as e:
        await j.start_run(fx.admin(), judge["id"], split="dev", schedule=False)
    assert (e.value.status, e.value.reason) == (403, "platform_only")
    assert [x["status"] for x in await _jobs(lab_scratch)] == ["queued"] and started["job"]["status"] == "queued"


async def test_a_member_whose_label_is_gone_is_left_out_of_a_run(lab_scratch, main_scratch, monkeypatch):
    ns = await _e2e(lab_scratch, main_scratch, monkeypatch)
    j = ns.judges
    judge, _ = await _register_v0(ns)
    split = await j.active_split(ns.mode_id)
    gone = _split_numbers(split, "dev")[0]
    await _drop_label(ns, gone)                                            # the person clears that label after the split
    est = await j.estimate(judge["id"], "dev")
    assert est["items"] == 23
    out = await j.start_run(ns.principal, judge["id"], split="dev", schedule=False)
    await j.work_judge_run(out["run"]["id"])
    run = await j.get_run(out["run"]["id"])
    assert run["status"] == "done" and run["n"] == 23 and gone not in ns.calls


async def test_a_code_judge_runs_for_nothing_and_reads_answers_the_chat_has_purged(lab_scratch, main_scratch,
                                                                                   monkeypatch):
    ns = await _e2e(lab_scratch, main_scratch, monkeypatch)
    j = ns.judges
    split = await j.create_split(ns.principal, ns.mode_id)
    async with main_scratch.begin() as conn:                              # the chat purged every answer in the split
        await conn.execute(text("DELETE FROM plenum_cafm.agent_trace_spans"))
        await conn.execute(text("DELETE FROM plenum_cafm.agent_trace_turns"))
    code, _ = await j.register(ns.principal, mode_id=ns.mode_id, kind="code", check_key="no_answer")
    est = await j.estimate(code["id"], "dev")
    assert (est["items"], est["usd_estimate"], est["model"]) == (24, 0, None)
    out = await j.start_run(ns.principal, code["id"], split="dev", schedule=False)
    await j.work_judge_run(out["run"]["id"])
    run = await j.get_run(out["run"]["id"])
    assert run["status"] == "done" and run["usd"] == 0 and ns.calls == []     # no model call, no cost
    assert (run["tp"], run["fn"], run["tn"], run["fp"]) == (12, 0, 0, 12)     # every answer passes no_answer
    assert run["disagreements"] and all(d["kind"] == "missed_failure" for d in run["disagreements"])
    assert (await _jobs(lab_scratch))[0]["status"] == "done" and float((await _jobs(lab_scratch))[0]["usd_spent"]) == 0
    assert len(split["members"]) == 60


async def test_a_preview_judges_one_answer_once_and_keeps_what_it_read(lab_scratch, main_scratch, monkeypatch):
    ns = await _e2e(lab_scratch, main_scratch, monkeypatch)
    j = ns.judges
    judge, _ = await _register_v0(ns)
    dev_turn = _tid(_split_numbers(await j.active_split(ns.mode_id), "dev")[0])
    first = await j.preview(ns.principal, judge["id"], dev_turn)
    assert first["status"] == "decided" and first["usd"] == pytest.approx(0.01) and len(ns.calls) == 1
    assert first["critique"].startswith("Read answer") and first["verdict"] in ("Pass", "Fail")
    again = await j.preview(ns.principal, judge["id"], dev_turn)
    assert (again["usd"], again["verdict"], len(ns.calls)) == (0, first["verdict"], 1)      # the cache answered
    # an answer that is in no split is read from the chat and kept as evidence before its prediction is stored
    async with main_scratch.begin() as conn:
        await fx.add_turn(conn, "turn-loose-1", fx.ORG_A, session_id="sess-x", answer="Site 77 has open jobs.",
                          question="How many open jobs at site number 77 today?")
    loose = await j.preview(ns.principal, judge["id"], "turn-loose-1")
    assert loose["status"] == "decided" and ns.calls[-1] == 77
    async with lab_scratch.connect() as conn:
        kept = (await conn.execute(text("SELECT organization_id::text FROM tracelab.evidence_turns "
                                        "WHERE turn_id = 'turn-loose-1'"))).scalar_one()
        org = (await conn.execute(text("SELECT organization_id::text FROM tracelab.judge_predictions "
                                       "WHERE turn_id = 'turn-loose-1'"))).scalar_one()
    assert kept == org == fx.ORG_A
    with pytest.raises(j.JudgeError) as e:
        await j.preview(ns.principal, judge["id"], "turn-not-anywhere")
    assert (e.value.status, e.value.reason) == (404, "turn_not_found")
    ns.errors = {78}                                                       # a failed read is shown and retried, never a pass
    async with main_scratch.begin() as conn:
        await fx.add_turn(conn, "turn-loose-2", fx.ORG_A, session_id="sess-y", answer="Site 78 has open jobs.",
                          question="How many open jobs at site number 78 today?")
    bad = await j.preview(ns.principal, judge["id"], "turn-loose-2")
    assert (bad["status"], bad["verdict"], bad["failure_present"], bad["critique"]) == ("error", None, None, "upstream timeout")
    ns.errors = set()
    good = await j.preview(ns.principal, judge["id"], "turn-loose-2")
    assert good["status"] == "decided"
    async with lab_scratch.connect() as conn:
        row = (await conn.execute(text("SELECT status, attempts FROM tracelab.judge_predictions "
                                       "WHERE turn_id = 'turn-loose-2'"))).one()
    assert tuple(row) == ("decided", 2)


async def test_restart_marks_running_judge_runs_interrupted(lab_scratch, main_scratch, monkeypatch):
    ns = await _e2e(lab_scratch, main_scratch, monkeypatch)
    j = ns.judges
    judge, _ = await _register_v0(ns)
    out = await j.start_run(ns.principal, judge["id"], split="dev", schedule=False)
    async with lab_scratch.begin() as conn:
        await conn.execute(text("UPDATE tracelab.judge_runs SET status = 'running' WHERE id = CAST(:r AS uuid)"),
                           {"r": out["run"]["id"]})
        await conn.execute(text("UPDATE tracelab.jobs SET status = 'interrupted' WHERE id = CAST(:j AS uuid)"),
                           {"j": out["job"]["id"]})
    assert await j.recover_after_restart() == 1
    assert await j.recover_after_restart() == 0                            # nothing left to recover
    assert (await j.get_run(out["run"]["id"]))["status"] == "interrupted"
    again = await j.start_run(ns.principal, judge["id"], split="dev", schedule=False)
    assert again["run"]["id"] == out["run"]["id"] and again["job"]["id"] == out["job"]["id"]
    await j.work_judge_run(again["run"]["id"])
    assert (await j.get_run(out["run"]["id"]))["status"] == "done"


async def test_start_run_recovers_a_run_the_boot_sweep_left_marked_running(lab_scratch, main_scratch, monkeypatch):
    """The job sweep can run without the judge recovery (the schema turned ready later). A run still marked running
    whose job was swept must not block a new start for ever."""
    ns = await _e2e(lab_scratch, main_scratch, monkeypatch)
    j = ns.judges
    judge, _ = await _register_v0(ns)
    out = await j.start_run(ns.principal, judge["id"], split="dev", schedule=False)
    async with lab_scratch.begin() as conn:
        await conn.execute(text("UPDATE tracelab.judge_runs SET status = 'running' WHERE id = CAST(:r AS uuid)"),
                           {"r": out["run"]["id"]})
        await conn.execute(text("UPDATE tracelab.jobs SET status = 'interrupted' WHERE id = CAST(:j AS uuid)"),
                           {"j": out["job"]["id"]})
    again = await j.start_run(ns.principal, judge["id"], split="dev", schedule=False)
    assert again["run"]["id"] == out["run"]["id"]


async def _worker_died(lab, out, *, quiet_for_s: int) -> None:
    """The worker claimed the run and went quiet: run and job both read running, the job's last heartbeat is
    `quiet_for_s` seconds old (a quick container restart leaves exactly this, and nothing has swept it yet)."""
    async with lab.begin() as conn:
        await conn.execute(text("UPDATE tracelab.judge_runs SET status = 'running' WHERE id = CAST(:r AS uuid)"),
                           {"r": out["run"]["id"]})
        await conn.execute(text("UPDATE tracelab.jobs SET status = 'running', worker = 'worker-that-died', "
                                "heartbeat_at = now() - make_interval(secs => :quiet) WHERE id = CAST(:j AS uuid)"),
                           {"j": out["job"]["id"], "quiet": float(quiet_for_s)})


async def test_start_run_interrupts_and_resumes_a_run_whose_worker_died_inside_the_sweep_window(
        lab_scratch, main_scratch, monkeypatch):
    """A worker that dies within STALE_AFTER_S of its last heartbeat leaves its run and its job 'running'; the boot
    sweep only takes jobs older than that and nothing sweeps later, so start_run sweeps for itself (as
    runner.regrade does) instead of answering 409 run_in_progress until the next boot."""
    ns = await _e2e(lab_scratch, main_scratch, monkeypatch)
    j = ns.judges
    judge, _ = await _register_v0(ns)
    out = await j.start_run(ns.principal, judge["id"], split="dev", schedule=False)
    await _worker_died(lab_scratch, out, quiet_for_s=600)
    again = await j.start_run(ns.principal, judge["id"], split="dev", schedule=False)
    assert (again["run"]["id"], again["job"]["id"]) == (out["run"]["id"], out["job"]["id"])      # the same run and job
    await j.work_judge_run(again["run"]["id"])
    run = await j.get_run(out["run"]["id"])
    assert run["status"] == "done" and run["job"]["status"] == "done" and run["n"] == 24


async def test_start_run_leaves_a_run_whose_worker_is_still_beating_alone(lab_scratch, main_scratch, monkeypatch):
    """The sweep only takes a job that has gone quiet: a run with a fresh heartbeat is going, so a second start is
    refused and neither the run nor its job is touched."""
    ns = await _e2e(lab_scratch, main_scratch, monkeypatch)
    j = ns.judges
    judge, _ = await _register_v0(ns)
    out = await j.start_run(ns.principal, judge["id"], split="dev", schedule=False)
    await _worker_died(lab_scratch, out, quiet_for_s=5)
    with pytest.raises(j.JudgeError) as e:
        await j.start_run(ns.principal, judge["id"], split="dev", schedule=False)
    assert (e.value.status, e.value.reason) == (409, "run_in_progress")
    assert (await _scalar(lab_scratch, "SELECT status FROM tracelab.judge_runs WHERE id = CAST(:r AS uuid)",
                          {"r": out["run"]["id"]})) == "running"
    assert (await _scalar(lab_scratch, "SELECT status FROM tracelab.jobs WHERE id = CAST(:j AS uuid)",
                          {"j": out["job"]["id"]})) == "running"


async def test_a_held_out_test_whose_worker_died_inside_the_sweep_window_resumes_and_keeps_its_one_attempt(
        lab_scratch, main_scratch, monkeypatch):
    ns = await _e2e(lab_scratch, main_scratch, monkeypatch)
    j = ns.judges
    ns.misses = set()
    judge, _ = await _register_v0(ns)
    await _dev_done(ns, judge["id"])
    await _freeze_judge(ns, judge["id"])
    out = await j.start_run(ns.principal, judge["id"], split="test", schedule=False)
    await _worker_died(lab_scratch, out, quiet_for_s=600)
    again = await j.start_run(ns.principal, judge["id"], split="test", schedule=False)      # not run_in_progress
    assert (again["run"]["id"], again["job"]["id"]) == (out["run"]["id"], out["job"]["id"])
    await j.work_judge_run(again["run"]["id"])
    assert (await j.get_run(out["run"]["id"]))["status"] == "done"
    with pytest.raises(j.JudgeError) as e:                                                  # the one test was run
        await j.start_run(ns.principal, judge["id"], split="test", schedule=False)
    assert e.value.reason == "test_already_run"


async def test_a_sweep_that_fails_never_stops_a_start(lab_scratch, main_scratch, monkeypatch):
    """The sweep is a courtesy before the checks: when it cannot run, start_run goes on and the checks decide."""
    ns = await _e2e(lab_scratch, main_scratch, monkeypatch)
    j = ns.judges
    judge, _ = await _register_v0(ns)

    async def down(*a, **k):
        raise ConnectionError("the lab database is not answering")

    monkeypatch.setattr(j.jobs, "mark_interrupted", down)
    out = await j.start_run(ns.principal, judge["id"], split="dev", schedule=False)
    assert out["run"]["status"] == "queued"


async def test_a_scheduled_run_works_in_the_background_and_a_scheduling_failure_never_reaches_the_caller(
        lab_scratch, main_scratch, monkeypatch):
    import asyncio

    ns = await _e2e(lab_scratch, main_scratch, monkeypatch)
    j = ns.judges
    judge, _ = await _register_v0(ns)
    out = await j.start_run(ns.principal, judge["id"], split="dev")        # schedule=True is the default
    assert out["run"]["status"] == "queued"
    for _ in range(300):
        await asyncio.sleep(0.1)
        if (await j.get_run(out["run"]["id"]))["status"] == "done":
            break
    assert (await j.get_run(out["run"]["id"]))["status"] == "done"
    if j._RUNNING:                                                         # the worker may still be closing its last connection
        await asyncio.wait(list(j._RUNNING), timeout=10)
    assert not j._RUNNING and (await j.get_run(out["run"]["id"]))["job"]["status"] == "done"


async def test_a_scheduling_failure_leaves_the_run_interrupted_and_the_next_start_resumes_it(
        lab_scratch, main_scratch, monkeypatch):
    ns = await _e2e(lab_scratch, main_scratch, monkeypatch)
    j = ns.judges
    judge, _ = await _register_v0(ns)

    def cannot_schedule(run_id):
        raise RuntimeError("no event loop to put the worker on")

    with monkeypatch.context() as m:
        m.setattr(j, "run_in_background", cannot_schedule)
        out = await j.start_run(ns.principal, judge["id"], split="dev")    # never raises to the caller
    assert out["run"]["status"] == "queued"                                # what the request itself created
    run = await j.get_run(out["run"]["id"])
    assert (run["status"], run["job"]["status"]) == ("interrupted", "paused") and ns.calls == []
    again = await j.start_run(ns.principal, judge["id"], split="dev", schedule=False)
    assert (again["run"]["id"], again["job"]["id"]) == (out["run"]["id"], out["job"]["id"])
    await j.work_judge_run(again["run"]["id"])
    assert (await j.get_run(out["run"]["id"]))["status"] == "done" and len(ns.calls) == 24


async def test_a_run_needs_labelled_answers_a_sane_cap_and_a_priced_model(lab_scratch, main_scratch, monkeypatch):
    ns = await _e2e(lab_scratch, main_scratch, monkeypatch)
    j = ns.judges
    judge, _ = await _register_v0(ns)
    with pytest.raises(j.JudgeError) as e:
        await j.start_run(ns.principal, judge["id"], split="dev", usd_cap="a lot", schedule=False)
    assert (e.value.status, e.value.reason) == (422, "bad_cap")
    with monkeypatch.context() as m:                                        # every label of the split is gone
        async def none(mode_id, *, org=None):
            return []

        m.setattr(labels, "human_origin_cells", none)
        assert (await j.estimate(judge["id"], "dev"))["items"] == 0
        with pytest.raises(j.JudgeError) as e:
            await j.start_run(ns.principal, judge["id"], split="dev", schedule=False)
        assert (e.value.status, e.value.reason) == (409, "no_items")
    with monkeypatch.context() as m:                                        # a model that lost its price cannot be capped
        m.setattr(j, "model_priced", lambda model: False)
        with pytest.raises(j.JudgeError) as e:
            await j.start_run(ns.principal, judge["id"], split="dev", schedule=False)
        assert (e.value.status, e.value.reason) == (409, "model_unpriced")
        dev_turn = _tid(_split_numbers(await j.active_split(ns.mode_id), "dev")[0])
        with pytest.raises(j.JudgeError) as e:
            await j.preview(ns.principal, judge["id"], dev_turn)
        assert e.value.reason == "model_unpriced" and ns.calls == []
    assert await _jobs(lab_scratch) == []                                   # no refusal opened a job or a run
    async with lab_scratch.connect() as conn:
        assert (await conn.execute(text("SELECT count(*) FROM tracelab.judge_runs"))).scalar_one() == 0
        assert (await conn.execute(text("SELECT count(*) FROM tracelab.judge_predictions"))).scalar_one() == 0


async def test_a_resumed_run_keeps_its_own_cap_and_a_stopped_job_is_raised_to_the_new_one(lab_scratch, main_scratch,
                                                                                          monkeypatch):
    from trace_lab import jobs

    ns = await _e2e(lab_scratch, main_scratch, monkeypatch)
    j = ns.judges
    judge, _ = await _register_v0(ns)
    out = await j.start_run(ns.principal, judge["id"], split="dev", usd_cap=3.0, schedule=False)
    assert out["run"]["usd_cap"] == 3.0 and out["run"]["job"]["usd_cap"] == pytest.approx(3.0)
    assert await jobs.request_stop(out["job"]["id"])
    await j.work_judge_run(out["run"]["id"])                               # stopped before any call: paused, spent nothing
    again = await j.start_run(ns.principal, judge["id"], split="dev", usd_cap=2.0, schedule=False)
    assert again["job"]["id"] == out["job"]["id"]                           # the same job, back in the queue
    assert again["run"]["usd_cap"] == 2.0 and again["run"]["job"]["usd_cap"] == pytest.approx(2.0)
    await j.work_judge_run(again["run"]["id"])                              # the stop request was cleared by the resume
    assert (await j.get_run(out["run"]["id"]))["status"] == "done" and len(ns.calls) == 24


async def test_a_capped_run_that_asks_for_less_than_it_spent_is_refused_and_left_as_it_was(lab_scratch, main_scratch,
                                                                                           monkeypatch):
    ns = await _e2e(lab_scratch, main_scratch, monkeypatch)
    j = ns.judges
    judge, _ = await _register_v0(ns)
    est = (await j.estimate(judge["id"], "dev"))["usd_estimate"]
    ns.usd = 0.05
    out = await j.start_run(ns.principal, judge["id"], split="dev", usd_cap=est, schedule=False)
    await j.work_judge_run(out["run"]["id"])
    capped = await j.get_run(out["run"]["id"])
    assert capped["status"] == "capped"
    jobs_before = await _jobs(lab_scratch)
    with pytest.raises(j.JudgeError) as e:                                  # nothing is left under half of what was spent
        await j.start_run(ns.principal, judge["id"], split="dev", usd_cap=capped["usd"] / 2, schedule=False)
    assert (e.value.status, e.value.reason) == (409, "over_cap")
    assert await _jobs(lab_scratch) == jobs_before
    again = await j.get_run(out["run"]["id"])
    assert (again["status"], again["usd_cap"], again["job"]["status"]) == ("capped", capped["usd_cap"], "capped")


async def test_a_run_that_is_already_finished_or_unknown_does_nothing_when_worked_again(lab_scratch, main_scratch,
                                                                                        monkeypatch):
    ns = await _e2e(lab_scratch, main_scratch, monkeypatch)
    j = ns.judges
    judge, _ = await _register_v0(ns)
    run = await _dev_done(ns, judge["id"])
    calls = len(ns.calls)
    await j.work_judge_run(run["id"])                                       # its job is done: nothing to claim
    assert len(ns.calls) == calls and (await j.get_run(run["id"]))["status"] == "done"
    await j.work_judge_run(str(uuid.uuid4()))                               # never raises for a run that is not there
    assert len(await _jobs(lab_scratch)) == 1


async def test_a_code_judge_preview_is_free_and_an_answer_of_another_company_is_kept_under_its_own(
        lab_scratch, main_scratch, monkeypatch):
    ns = await _e2e(lab_scratch, main_scratch, monkeypatch)
    j = ns.judges
    code, _ = await _register_v0(ns, kind="code")
    split = await j.active_split(ns.mode_id)
    dev_turn = _tid(_split_numbers(split, "dev")[0])
    first = await j.preview(ns.principal, code["id"], dev_turn)
    assert (first["status"], first["usd"], first["verdict"], first["truncated"]) == ("decided", 0.0, "Pass", False)
    assert ns.calls == []
    assert (await j.preview(ns.principal, code["id"], dev_turn))["usd"] == 0
    async with main_scratch.begin() as conn:
        await fx.add_turn(conn, "turn-other-company", fx.ORG_B, session_id="sess-b", answer="Site 5 has open jobs.",
                          question="How many open jobs at site number 5 today?")
        await fx.add_span(conn, "turn-other-company", fx.ORG_B, kind="tool", name="query_table", seq=1, output={"count": 1})
    other = await j.preview(ns.principal, code["id"], "turn-other-company")
    assert other["status"] == "decided"
    async with lab_scratch.connect() as conn:
        kept = (await conn.execute(text("SELECT organization_id::text FROM tracelab.evidence_turns "
                                        "WHERE turn_id = 'turn-other-company'"))).scalar_one()
        pred = (await conn.execute(text("SELECT organization_id::text, attempts, usd FROM tracelab.judge_predictions "
                                        "WHERE turn_id = 'turn-other-company'"))).one()
        mine = (await conn.execute(text("SELECT attempts FROM tracelab.judge_predictions WHERE turn_id = :t"),
                                   {"t": dev_turn})).scalar_one()
    assert kept == pred[0] == fx.ORG_B and pred[1] == 1 and mine == 1       # a repeat read never wrote again
    for bad in ("", " ", "x" * 201, None):
        with pytest.raises(j.JudgeError) as e:
            await j.preview(ns.principal, code["id"], bad)
        assert (e.value.status, e.value.reason) == (404, "turn_not_found")
    with pytest.raises(j.JudgeError) as e:
        await j.preview(fx.admin(), code["id"], dev_turn)
    assert (e.value.status, e.value.reason) == (403, "platform_only")


# ── triage, freeze, the one held-out test, the decision and the threshold (Phase 6 task 3, part 4) ─────────────────

async def _flip_label(ns, i: int) -> None:
    """The person changes their mind on answer i (a new label through the real label path)."""
    cell = next(c for c in await labels.human_origin_cells(ns.mode_id) if c["turn_id"] == _tid(i))
    await labels.set_label(fx.owner(), None, _tid(i), ns.mode_id, present=not cell["present"], base=cell["label_id"])


async def _triage_everything(ns, run: dict, triage: str = "judge_wrong") -> dict:
    for d in run["disagreements"]:
        run = await ns.judges.triage(ns.principal, run["id"], d["turn_id"], triage=triage,
                                     note="Missed the implied code.", base=run["rev"])
    return run


async def _freeze_judge(ns, judge_id: str, reason: str = "Dev agreement is high; a third revision is not needed."):
    rev = (await ns.judges.get_judge(judge_id))["judge"]["rev"]
    return await ns.judges.freeze(ns.principal, judge_id, stop_reason=reason, base=rev)


async def _held_out_test(ns, judge_id: str) -> dict:
    out = await ns.judges.start_run(ns.principal, judge_id, split="test", schedule=False)
    await ns.judges.work_judge_run(out["run"]["id"])
    return await ns.judges.get_run(out["run"]["id"])


async def _mode_row(lab, mode_id: str) -> dict:
    async with lab.connect() as conn:
        return dict((await conn.execute(text("SELECT status, frozen_at FROM tracelab.modes WHERE id = CAST(:m AS uuid)"),
                                        {"m": mode_id})).mappings().one())


async def test_every_disagreement_is_triaged_before_the_freeze_and_triage_keeps_its_history(
        lab_scratch, main_scratch, monkeypatch):
    ns = await _e2e(lab_scratch, main_scratch, monkeypatch)
    j = ns.judges
    split = await j.create_split(ns.principal, ns.mode_id)
    ns.misses = {_split_numbers(split, "dev", present=True)[0], _split_numbers(split, "dev", present=False)[0]}
    judge, _ = await _register_v0(ns)
    long_enough = "Dev agreement is high; a third revision is not needed."
    with pytest.raises(j.JudgeError) as e:                                  # nothing to freeze before a dev run
        await j.freeze(ns.principal, judge["id"], stop_reason=long_enough, base=judge["rev"])
    assert (e.value.status, e.value.reason) == (409, "no_dev_run")
    run = await _dev_done(ns, judge["id"])
    assert run["untriaged"] == 2 and run["rev"] == 2
    with pytest.raises(j.JudgeError) as e:
        await j.freeze(ns.principal, judge["id"], stop_reason=long_enough, base=judge["rev"])
    assert (e.value.status, e.value.reason, e.value.extra["untriaged"]) == (409, "untriaged_disagreements", 2)
    first, second = (d["turn_id"] for d in run["disagreements"])
    for kwargs, status, reason in (
            ({"turn_id": "turn-not-here", "triage": "judge_wrong"}, 409, "not_a_disagreement"),
            ({"turn_id": first, "triage": "the_model_was_tired"}, 422, "bad_triage"),
            ({"turn_id": first, "triage": "judge_wrong", "base": run["rev"] + 3}, 409, "stale_base")):
        with pytest.raises(j.JudgeError) as e:
            await j.triage(ns.principal, run["id"], **{"note": None, "base": run["rev"], **kwargs})
        assert (e.value.status, e.value.reason) == (status, reason), kwargs
    for bad in (str(uuid.uuid4()), "not-an-id"):
        with pytest.raises(j.JudgeError) as e:
            await j.triage(ns.principal, bad, first, triage="judge_wrong", note=None, base=1)
        assert (e.value.status, e.value.reason) == (404, "run_not_found")
    with pytest.raises(j.JudgeError) as e:
        await j.triage(fx.admin(), run["id"], first, triage="judge_wrong", note=None, base=run["rev"])
    assert (e.value.status, e.value.reason) == (403, "platform_only")
    run = await j.triage(ns.principal, run["id"], first, triage="judge_wrong", note=" Missed the implied code. ",
                         base=run["rev"])
    assert run["rev"] == 3 and run["untriaged"] == 1
    run = await j.triage(ns.principal, run["id"], first, triage="label_wrong", note=None, base=run["rev"])
    row = next(d for d in run["disagreements"] if d["turn_id"] == first)
    assert (row["triage"], row["note"], row["by_email"], row["by"]) == ("label_wrong", None, "owner@plenum.test", fx.OWNER_ID)
    assert [(h["triage"], h["note"]) for h in row["history"]] == [("judge_wrong", "Missed the implied code.")]
    assert run["untriaged"] == 1                                            # re-triaging one answer settles no other
    run = await j.triage(ns.principal, run["id"], second, triage="definition_unclear", note=None, base=run["rev"])
    assert run["untriaged"] == 0 and run["rev"] == 5
    assert (await j.readiness(ns.principal, ns.mode_id))["next_step"] == "freeze_or_revise"
    assert (await j.get_judge(judge["id"]))["iterations"][0]["untriaged"] == 0


async def test_freezing_is_one_way_it_freezes_the_mode_and_stops_dev_runs(lab_scratch, main_scratch, monkeypatch):
    ns = await _e2e(lab_scratch, main_scratch, monkeypatch)
    j = ns.judges
    split = await j.create_split(ns.principal, ns.mode_id)
    ns.misses = {_split_numbers(split, "dev", present=True)[0]}
    judge, _ = await _register_v0(ns)
    await _triage_everything(ns, await _dev_done(ns, judge["id"]))
    rev = (await j.get_judge(judge["id"]))["judge"]["rev"]
    for kwargs, reason in (({"stop_reason": "short"}, "stop_reason_required"),
                           ({"stop_reason": "x" * 2001}, "stop_reason_required"),
                           ({"stop_reason": "A long enough stop reason.", "base": rev + 1}, "stale_base")):
        with pytest.raises(j.JudgeError) as e:
            await j.freeze(ns.principal, judge["id"], **{"base": rev, **kwargs})
        assert e.value.reason == reason, kwargs
    with pytest.raises(j.JudgeError) as e:
        await j.freeze(fx.admin(), judge["id"], stop_reason="A long enough stop reason.", base=rev)
    assert (e.value.status, e.value.reason) == (403, "platform_only")
    assert (await _mode_row(lab_scratch, ns.mode_id))["status"] == "confirmed"            # every refusal changed nothing
    frozen = await j.freeze(ns.principal, judge["id"], stop_reason=" Stable on dev; no third revision. ", base=rev)
    assert (frozen["status"], frozen["stop_reason"], frozen["frozen_by_email"], frozen["rev"]) == (
        "frozen", "Stable on dev; no third revision.", "owner@plenum.test", rev + 1)
    mode = await _mode_row(lab_scratch, ns.mode_id)
    assert mode["status"] == "frozen" and mode["frozen_at"] is not None
    async with lab_scratch.connect() as conn:
        events = [r["detail"] for r in (await conn.execute(text(
            "SELECT detail FROM tracelab.mode_events WHERE mode_id = CAST(:m AS uuid) AND action = 'edit'"),
            {"m": ns.mode_id})).mappings().all()]
        history = (await conn.execute(text("SELECT history FROM tracelab.judges WHERE id = CAST(:j AS uuid)"),
                                      {"j": judge["id"]})).scalar_one()
    assert events and events[-1]["by"] == "judge_freeze" and events[-1]["status"] == ["confirmed", "frozen"]
    assert [(h["field"], h["from"], h["to"], h["by"]) for h in history] == [
        ("status", "draft", "frozen", "owner@plenum.test")]
    with pytest.raises(j.JudgeError) as e:                                  # one way
        await j.freeze(ns.principal, judge["id"], stop_reason="Freezing it a second time.", base=frozen["rev"])
    assert (e.value.status, e.value.reason) == (409, "judge_frozen")
    with pytest.raises(j.JudgeError) as e:
        await j.start_run(ns.principal, judge["id"], split="dev", schedule=False)
    assert e.value.reason == "judge_frozen"
    before = len(await _jobs(lab_scratch))
    with monkeypatch.context() as m:                                        # a freeze that slips in after the checks
        m.setattr(j, "run_refusal", lambda *a, **k: None)
        with pytest.raises(j.JudgeError) as e:
            await j.start_run(ns.principal, judge["id"], split="dev", schedule=False)
    assert e.value.reason == "judge_frozen"                                 # the database's own guard answers
    jobs_ = await _jobs(lab_scratch)
    assert len(jobs_) == before + 1 and jobs_[-1]["status"] == "failed"     # ... and the job it opened is released
    assert (await j.readiness(ns.principal, ns.mode_id))["next_step"] == "run_test"


async def test_a_judge_of_a_retired_mode_cannot_freeze_it(lab_scratch, main_scratch, monkeypatch):
    ns = await _e2e(lab_scratch, main_scratch, monkeypatch)
    j = ns.judges
    ns.misses = set()
    judge, _ = await _register_v0(ns)
    await _dev_done(ns, judge["id"])
    await taxonomy.retire(ns.principal, ns.mode_id, reason="Replaced by a wider mode.", base_version=1)
    with pytest.raises(j.JudgeError) as e:
        await _freeze_judge(ns, judge["id"])
    assert (e.value.status, e.value.reason) == (409, "mode_not_confirmed")
    assert (await j.get_judge(judge["id"]))["judge"]["status"] == "draft"            # nothing was half done
    assert (await _mode_row(lab_scratch, ns.mode_id))["status"] == "retired"


async def test_the_held_out_test_runs_once_resumes_when_incomplete_and_is_never_recomputed(
        lab_scratch, main_scratch, monkeypatch):
    ns = await _e2e(lab_scratch, main_scratch, monkeypatch)
    j = ns.judges
    split = await j.create_split(ns.principal, ns.mode_id)
    test_miss = _split_numbers(split, "test", present=True)[0]
    ns.misses = {test_miss}
    judge, _ = await _register_v0(ns)
    with pytest.raises(j.JudgeError) as e:                                  # the test waits for the freeze
        await j.start_run(ns.principal, judge["id"], split="test", schedule=False)
    assert e.value.reason == "not_frozen"
    await _dev_done(ns, judge["id"])
    await _freeze_judge(ns, judge["id"])
    calls_before = len(ns.calls)
    test_ids = _split_numbers(split, "test")
    ns.errors = {test_ids[0]}
    out = await j.start_run(ns.principal, judge["id"], split="test", schedule=False)
    await j.work_judge_run(out["run"]["id"])
    partial = await j.get_run(out["run"]["id"])
    assert (partial["status"], partial["n_error"], partial["tpr"], partial["label_flips"]) == ("incomplete", 1, None, None)
    ns.errors = set()
    again = await j.start_run(ns.principal, judge["id"], split="test", schedule=False)
    assert again["run"]["id"] == out["run"]["id"]                           # resuming is not a second attempt
    await j.work_judge_run(again["run"]["id"])
    test_run = await j.get_run(out["run"]["id"])
    assert test_run["status"] == "done" and test_run["n"] == 24 and len(test_run["results"]) == 24
    assert len(ns.calls) - calls_before == 24 + 1                           # 24 items, the one error asked a second time
    assert (test_run["tp"], test_run["fn"], test_run["tn"], test_run["fp"]) == (12, 0, 11, 1)
    assert test_run["tpr"] == 1.0 and test_run["tnr"] == pytest.approx(11 / 12, abs=1e-4)
    assert [d["turn_id"] for d in test_run["disagreements"]] == [_tid(test_miss)]
    with pytest.raises(j.JudgeError) as e:
        await j.start_run(ns.principal, judge["id"], split="test", schedule=False)
    assert (e.value.status, e.value.reason) == (409, "test_already_run")
    jobs_before = len(await _jobs(lab_scratch))
    with monkeypatch.context() as m:                                        # a second test that slips past the checks
        m.setattr(j, "run_refusal", lambda *a, **k: None)
        with pytest.raises(j.JudgeError) as e:
            await j.start_run(ns.principal, judge["id"], split="test", schedule=False)
    assert e.value.reason == "test_already_run"                             # the one-test index answers
    jobs_ = await _jobs(lab_scratch)
    assert len(jobs_) == jobs_before + 1 and jobs_[-1]["status"] == "failed"
    labels_, preds = await j.test_pairs(judge["id"])
    assert len(labels_) == len(preds) == 24 and sum(1 for a, b in zip(labels_, preds) if (a, b) == (1, 0)) == 1
    assert await j.test_pairs(str(uuid.uuid4())) == ([], [])
    # the person changes their mind on one held-out label afterwards: a warning, never a new metric (J6)
    await _flip_label(ns, test_ids[1])
    detail = await j.get_judge(judge["id"])
    assert {"code": "labels_changed_since_test", "message": "1 test labels changed since the test run"} in detail["warnings"]
    assert (await j.get_run(out["run"]["id"]))["tpr"] == test_run["tpr"]
    assert (await j.test_pairs(judge["id"]))[0] == labels_                  # the pairs keep the labels as tested
    assert (await j.readiness(ns.principal, ns.mode_id))["next_step"] == "decide"


async def test_the_decision_and_the_threshold_belong_to_a_judge_that_has_been_tested(lab_scratch, main_scratch,
                                                                                     monkeypatch):
    ns = await _e2e(lab_scratch, main_scratch, monkeypatch)
    j = ns.judges
    ns.misses = set()
    judge, _ = await _register_v0(ns)
    jid = judge["id"]
    for call, reason in ((j.set_decision(ns.principal, jid, decision="use", note=None, base=judge["rev"]), "no_test_run"),
                         (j.set_threshold(ns.principal, jid, threshold=0.2, base=judge["rev"]), "not_in_use")):
        with pytest.raises(j.JudgeError) as e:
            await call
        assert (e.value.status, e.value.reason) == (409, reason)
    await _dev_done(ns, jid)
    await _freeze_judge(ns, jid)
    test_run = await _held_out_test(ns, jid)
    assert test_run["status"] == "done" and test_run["disagreements"] == [] and test_run["tpr"] == 1.0
    rev = (await j.get_judge(jid))["judge"]["rev"]
    with pytest.raises(j.JudgeError) as e:
        await j.set_decision(ns.principal, jid, decision="maybe", note=None, base=rev)
    assert (e.value.status, e.value.reason) == (422, "bad_decision")
    with pytest.raises(j.JudgeError) as e:
        await j.set_decision(ns.principal, jid, decision="use", note=None, base=rev + 1)
    assert (e.value.status, e.value.reason) == (409, "stale_base")
    for bad in (str(uuid.uuid4()), "not-an-id"):
        with pytest.raises(j.JudgeError) as e:
            await j.set_decision(ns.principal, bad, decision="use", note=None, base=1)
        assert (e.value.status, e.value.reason) == (404, "judge_not_found")
        with pytest.raises(j.JudgeError) as e:
            await j.set_threshold(ns.principal, bad, threshold=0.2, base=1)
        assert e.value.reason == "judge_not_found"
    with pytest.raises(j.JudgeError) as e:
        await j.set_decision(fx.admin(), jid, decision="use", note=None, base=rev)
    assert (e.value.status, e.value.reason) == (403, "platform_only")
    assert await j.in_use_judges() == {}
    no = await j.set_decision(ns.principal, jid, decision="do_not_use", note="Too many false alarms.", base=rev)
    assert (no["decision"], no["in_use"], no["decision_note"], no["monitor_threshold"]) == (
        "do_not_use", False, "Too many false alarms.", None)
    assert await j.in_use_judges() == {} and (await j.readiness(ns.principal, ns.mode_id))["next_step"] == "revise"
    with pytest.raises(j.JudgeError) as e:                                  # a judge that is not in use has no threshold
        await j.set_threshold(ns.principal, jid, threshold=0.2, base=no["rev"])
    assert e.value.reason == "not_in_use"
    decided = await j.set_decision(ns.principal, jid, decision="use", note="  Held-out rates are good. ", base=no["rev"])
    assert (decided["decision"], decided["in_use"], decided["decision_note"]) == ("use", True, "Held-out rates are good.")
    in_use = await j.in_use_judges()
    assert set(in_use) == {ns.mode_id} and in_use[ns.mode_id]["id"] == jid and in_use[ns.mode_id]["test_status"] == "done"
    assert (await j.readiness(ns.principal, ns.mode_id))["next_step"] == "in_use"
    for bad in (0, 1, 1.5, -0.1):
        with pytest.raises(j.JudgeError) as e:
            await j.set_threshold(ns.principal, jid, threshold=bad, base=decided["rev"])
        assert (e.value.status, e.value.reason) == (422, "bad_threshold"), bad
    with pytest.raises(j.JudgeError) as e:
        await j.set_threshold(ns.principal, jid, threshold=0.2, base=decided["rev"] + 1)
    assert (e.value.status, e.value.reason) == (409, "stale_base")
    tuned = await j.set_threshold(ns.principal, jid, threshold=0.15, base=decided["rev"])
    assert tuned["monitor_threshold"] == pytest.approx(0.15) and tuned["rev"] == decided["rev"] + 1
    cleared = await j.set_threshold(ns.principal, jid, threshold=None, base=tuned["rev"])
    assert cleared["monitor_threshold"] is None
    async with lab_scratch.connect() as conn:
        history = (await conn.execute(text("SELECT history FROM tracelab.judges WHERE id = CAST(:j AS uuid)"),
                                      {"j": jid})).scalar_one()
    assert [(h["field"], h["from"], h["to"]) for h in history] == [
        ("status", "draft", "frozen"), ("decision", None, "do_not_use"), ("decision", "do_not_use", "use"),
        ("monitor_threshold", None, 0.15), ("monitor_threshold", 0.15, None)]


async def test_a_new_version_in_use_inherits_the_threshold_of_the_version_it_replaces(lab_scratch, main_scratch,
                                                                                      monkeypatch):
    ns = await _e2e(lab_scratch, main_scratch, monkeypatch)
    j = ns.judges
    ns.misses = set()
    v0, _ = await _register_v0(ns)
    await _dev_done(ns, v0["id"])
    await _freeze_judge(ns, v0["id"])
    await _held_out_test(ns, v0["id"])
    rev = (await j.get_judge(v0["id"]))["judge"]["rev"]
    use0 = await j.set_decision(ns.principal, v0["id"], decision="use", note=None, base=rev)
    await j.set_threshold(ns.principal, v0["id"], threshold=0.15, base=use0["rev"])
    # version 1 is a code check, which costs nothing to run; it calls every answer a pass, so every failure is a miss.
    # Between the versions the person changes their mind on one dev answer: a Fail becomes a Pass.
    await _flip_label(ns, _split_numbers(await j.active_split(ns.mode_id), "dev", present=True)[0])
    v1, _ = await _register_v0(ns, kind="code", change_note="A code check for the same mode.")
    assert v1["version"] == 1 and v1["monitor_threshold"] is None
    run = await _dev_done(ns, v1["id"])
    assert run["label_flips"] == 1 and run["disagreement_count"] == 11 and run["usd"] == 0
    assert run["tn"] + run["fp"] == 11 and run["n"] == 24                    # one Fail fewer; the run read live labels
    await _triage_everything(ns, run, triage="label_wrong")
    await _freeze_judge(ns, v1["id"])
    test = await _held_out_test(ns, v1["id"])
    assert test["status"] == "done", test["error"]
    assert test["usd"] == 0
    assert set(await j.in_use_judges()) == {ns.mode_id} and (await j.in_use_judges())[ns.mode_id]["id"] == v0["id"]
    rev1 = (await j.get_judge(v1["id"]))["judge"]["rev"]
    use1 = await j.set_decision(ns.principal, v1["id"], decision="use", note=None, base=rev1)
    assert use1["monitor_threshold"] == pytest.approx(0.15)                 # carried over, not guessed
    assert (await j.in_use_judges())[ns.mode_id]["id"] == v1["id"]          # the highest version in use wins
    assert (await j.get_judge(v0["id"]))["judge"]["in_use"] is False
    with pytest.raises(j.JudgeError) as e:
        await j.set_threshold(ns.principal, v0["id"], threshold=0.3, base=(await j.get_judge(v0["id"]))["judge"]["rev"])
    assert e.value.reason == "not_in_use"
    detail = await j.get_judge(v1["id"])
    assert [r["version"] for r in detail["iterations"]] == [0, 1] and detail["iterations"][1]["frozen"] is True
    assert detail["iterations"][1]["test"]["tpr"] == test["tpr"]


async def test_a_lost_connection_interrupts_a_run_and_the_held_out_test_keeps_its_one_attempt(
        lab_scratch, main_scratch, monkeypatch):
    ns = await _e2e(lab_scratch, main_scratch, monkeypatch)
    j = ns.judges
    ns.misses = set()
    judge, _ = await _register_v0(ns)
    await _dev_done(ns, judge["id"])
    await _freeze_judge(ns, judge["id"])
    out = await j.start_run(ns.principal, judge["id"], split="test", schedule=False)

    async def blip(*a, **k):
        raise TimeoutError()                                  # asyncpg's connect timeout carries no message

    real = j.write_prediction
    with monkeypatch.context() as m:
        m.setattr(j, "write_prediction", blip)
        await j.work_judge_run(out["run"]["id"])
    run = await j.get_run(out["run"]["id"])
    assert (run["status"], run["job"]["status"]) == ("interrupted", "paused")
    assert "TimeoutError" in run["error"] and run["finished_at"] is None
    assert j.write_prediction is real
    again = await j.start_run(ns.principal, judge["id"], split="test", schedule=False)   # not test_already_run
    assert (again["run"]["id"], again["job"]["id"]) == (out["run"]["id"], out["job"]["id"])
    assert again["run"]["error"] is None
    await j.work_judge_run(out["run"]["id"])
    done = await j.get_run(out["run"]["id"])
    assert done["status"] == "done" and done["error"] is None and done["n"] == 24
    with pytest.raises(j.JudgeError) as e:                    # and now it is the one test that was run
        await j.start_run(ns.principal, judge["id"], split="test", schedule=False)
    assert e.value.reason == "test_already_run"


# ── the failure report (Phase 6 task 3, part 5) ─────────────────────────────────────────────────────────────────

async def test_the_failure_report_reads_the_modes_their_labels_and_their_evaluator(lab_scratch, main_scratch,
                                                                                   monkeypatch):
    ns = await _e2e(lab_scratch, main_scratch, monkeypatch)
    j = ns.judges
    async with lab_scratch.begin() as conn:                      # a candidate and a retired-looking mode stay out of it
        await fx.add_mode(conn, "mode_still_a_candidate", status="candidate", agent="udr")
    out = await j.failure_report(ns.principal)
    assert [e["name"] for e in out["modes"]] == ["invented_codes_e2e"] and out["generated_at"].endswith("+00:00")
    entry = out["modes"][0]
    assert entry["labeled_counts"] == {"fail": 30, "pass": 30} and entry["sample_fraction"] == 0.5
    assert entry["evaluator"] is None and entry["evaluator_reason"] == "No frozen judge with a finished held-out test yet."
    assert (entry["prevalence"], entry["prevalence_reason"]) == (None, "No frozen judge yet.")
    assert entry["definition"].startswith("Present when") and entry["requirement_ids"] == [] and entry["examples"] == []
    md = await j.failure_report(ns.principal, fmt="md")
    assert md.startswith("# Failure report") and "## invented_codes_e2e" in md and "mode_still_a_candidate" not in md
    assert "Evaluator: none (No frozen judge with a finished held-out test yet.)" in md
    with pytest.raises(j.JudgeError) as e:
        await j.failure_report(fx.admin())
    assert (e.value.status, e.value.reason) == (403, "platform_only")
    # a judge in use becomes the mode's evaluator, with its held-out rates and the labels' own counts
    ns.misses = set()
    judge, _ = await _register_v0(ns)
    await _dev_done(ns, judge["id"])
    await _freeze_judge(ns, judge["id"])
    await _held_out_test(ns, judge["id"])
    rev = (await j.get_judge(judge["id"]))["judge"]["rev"]
    await j.set_decision(ns.principal, judge["id"], decision="use", note=None, base=rev)
    entry = (await j.failure_report(ns.principal))["modes"][0]
    ev = entry["evaluator"]
    assert (ev["type"], ev["judge_key"], ev["model"], ev["decision"]) == (
        "llm", "invented_codes_e2e-v0", "claude-sonnet-5", "use")
    assert (ev["test_tpr"], ev["test_tnr"]) == (1.0, 1.0) and ev["test_class_counts"] == {"failure": 12, "nonfailure": 12}
    assert ev["min_labels_override"] is None and entry["evaluator_reason"] is None
    assert entry["prevalence"] is None and entry["prevalence_reason"] == (
        "Monitoring has not produced an estimate for this mode yet.")        # the seam is monitor.latest_prevalence now
    assert (await j.latest_prevalence(ns.mode_id))[0] is None
    md = await j.failure_report(ns.principal, fmt="md")
    assert "Evaluator: LLM judge invented_codes_e2e-v0 (claude-sonnet-5); held-out TPR 1.000" in md
    assert "decision: use" in md and "Prevalence: not available" in md


async def test_golden_answers_that_failed_are_candidates_named_with_their_company(lab_scratch, main_scratch, monkeypatch):
    a, b = fx.ORG_A, fx.ORG_B
    async with lab_scratch.begin() as conn:
        mid = await fx.add_mode(conn, "invented_codes_cases", status="confirmed", agent="udr")
        for org in (a, b):                                        # both companies' sets have a case "1"
            s = await golden_seed.seed_set(conn, org)
            run = await golden_seed.seed_run(conn, org, s["set_id"])
            _, tid = await golden_seed.seed_result(conn, org, run, s["cases"]["1"], "1", turn_id=f"turn-g{org[:4]}")
            await conn.execute(text(
                "INSERT INTO tracelab.evidence_turns (turn_id, organization_id, turn, reason) VALUES (:t, CAST(:o AS uuid), "
                "CAST(:j AS jsonb), 'golden')"),
                {"t": tid, "o": org, "j": json.dumps({"turn_id": tid, "session_id": f"golden-{org[:8]}-1-r1",
                                                      "organization_id": org, "question": f"Golden question {org[:4]}?",
                                                      "answer": "A golden answer.", "started_at": LATER.isoformat()})})
            await fx.add_label(conn, tid, mid, org=org, present=True, created_at=LATER)
        await fx.add_label(conn, "turn-g-vanished", mid, org=a, present=False, created_at=LATER)
    entry = next(e for e in (await judges.failure_report(fx.owner()))["modes"] if e["name"] == "invented_codes_cases")
    assert entry["labeled_counts"] == {"fail": 2, "pass": 1}
    assert entry["evaluation_case_candidates"] == sorted([f"{a}/1", f"{b}/1"])      # case "1" of each company, not one "1"
