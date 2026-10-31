"""0006_watch.sql on a throwaway Postgres (tracelab_scratch): the rules the Watch tables enforce for themselves.

0006 applies after 0005 with the lock timeout first. A monitoring verdict is one row per evaluator and answer and a
decided one never changes. One batch is active per company, and one for the platform (organization_id NULL). An eval
case has a kind and a baseline that agree, a runnable twin once it leaves proposed, a source that names what it came
from, and one current version. An eval case run is one row per case and golden run. Skips without
TRACE_LAB_TEST_DB_URL (tests/tl_scratch.py refuses any database but tracelab_scratch).

Trace Lab's database holds no chat tables, so a verdict or case names its turn id. The hatch that company offboarding
would use is the foundation's `tracelab.allow_purge`. The Regression cases set shape, the 20 run caps and the
monitor_flagged strategy belong to 0002 and 0003 and are tested with them."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

import trace_lab_m4_fixtures as fx
from tl_scratch import lab_scratch, requires_scratch  # noqa: F401
from trace_lab import migrate

pytestmark = requires_scratch

UTC = timezone.utc
ORG_A = fx.ORG_A
ORG_B = fx.ORG_B
WEEK = (datetime(2026, 10, 5, tzinfo=UTC), datetime(2026, 10, 12, tzinfo=UTC))
CODE_KEY = "code:no_answer@v1"
TURN = "turn-00000000000000a1"


async def _fails(engine, exc, match, make) -> None:
    """The statement make(conn) runs is refused with exc, and the message names the rule that refused it."""
    with pytest.raises(exc, match=match):
        async with engine.begin() as conn:
            await make(conn)


async def _job(conn, kind: str = "monitor_batch", org: str | None = ORG_A) -> str:
    jid = str(uuid.uuid4())
    await conn.execute(text(
        "INSERT INTO tracelab.jobs (id, kind, status, organization_id, scope_key, usd_cap) "
        "VALUES (CAST(:id AS uuid), :kind, 'done', CAST(:org AS uuid), :scope, 5)"),
        {"id": jid, "kind": kind, "org": org, "scope": f"{kind}:{jid}"})
    return jid


async def _batch(conn, org: str | None = None, status: str = "queued", **over) -> str:
    row = {"id": str(uuid.uuid4()), "org": org, "period": "2026-W41", "start": WEEK[0], "end": WEEK[1], "rate": "0.25",
           "channel": "chat", "status": status, "job": None, "usd": "0", **over}
    row["rate"], row["usd"] = Decimal(str(row["rate"])), Decimal(str(row["usd"]))
    await conn.execute(text(
        "INSERT INTO tracelab.monitor_batches (id, organization_id, period_label, window_start, window_end, channel, "
        "sample_rate, seed, risk_groups, judges, checks, status, job_id, usd) VALUES (CAST(:id AS uuid), "
        "CAST(:org AS uuid), :period, :start, :end, :channel, :rate, 7, CAST(:groups AS text[]), CAST('[]' AS jsonb), "
        "CAST('[]' AS jsonb), :status, CAST(:job AS uuid), :usd)"),
        {**row, "groups": ["failed"]})
    return row["id"]


async def _verdict(conn, batch_id: str, status: str, flag: int | None, *, key: str = CODE_KEY, turn: str = TURN,
                   org: str = ORG_A, judge_id: str | None = None, **over) -> str:
    row = {"id": str(uuid.uuid4()), "ek": key, "tid": turn, "org": org, "ts": WEEK[0] + timedelta(hours=9),
           "status": status, "fp": flag, "batch": batch_id, "judge": judge_id, "attempts": 1, **over}
    await conn.execute(text(
        "INSERT INTO tracelab.monitor_verdicts (id, evaluator_key, turn_id, organization_id, turn_started_at, status, "
        "failure_present, first_batch_id, judge_id, attempts) VALUES (CAST(:id AS uuid), :ek, :tid, CAST(:org AS uuid), "
        ":ts, :status, :fp, CAST(:batch AS uuid), CAST(:judge AS uuid), :attempts)"), row)
    return row["id"]


async def _code_judge(conn) -> str:
    """A frozen-or-draft code judge is enough for a verdict to name: a mode, a split header, one judge version."""
    mode = await fx.add_mode(conn, "no_answer_sql", status="confirmed", agent="udr",
                             definition="Present when the answer gives nothing to act on.")
    split = str(uuid.uuid4())
    await conn.execute(text(
        "INSERT INTO tracelab.judge_splits (id, mode_id, version, fractions, seed, min_per_class, min_labels, counts, "
        "labels_sha) VALUES (CAST(:id AS uuid), CAST(:m AS uuid), 1, CAST(:fr AS numeric(4,3)[]), 7, 10, 30, "
        "CAST('{}' AS jsonb), :sha)"),
        {"id": split, "m": mode, "fr": [0.2, 0.4, 0.4], "sha": "0" * 64})
    jid = str(uuid.uuid4())
    await conn.execute(text(
        "INSERT INTO tracelab.judges (id, mode_id, version, judge_key, kind, split_id, check_key, check_version, "
        "prompt_hash) VALUES (CAST(:id AS uuid), CAST(:m AS uuid), 0, 'no_answer-v0', 'code', CAST(:s AS uuid), "
        "'no_answer', 1, 'code00000000')"), {"id": jid, "m": mode, "s": split})
    return jid


async def _regression_set(conn, org: str = ORG_A) -> str:
    """The company Regression cases set as 0002 shapes it: kind regression, version 0, made by Trace Lab itself."""
    sid = str(uuid.uuid4())
    await conn.execute(text(
        "INSERT INTO tracelab.golden_sets (id, organization_id, name, kind, version, set_hash, created_by) "
        "VALUES (CAST(:id AS uuid), CAST(:org AS uuid), 'Regression cases', 'regression', 0, 'abcdef012345', 'system')"),
        {"id": sid, "org": org})
    return sid


async def _golden_case(conn, set_id: str, key: str = "e-0000000a", org: str = ORG_A, idx: int = 0) -> str:
    gid = str(uuid.uuid4())
    await conn.execute(text(
        "INSERT INTO tracelab.golden_cases (id, set_id, organization_id, case_key, case_version, idx, question, "
        "expected_answer, evaluation) VALUES (CAST(:id AS uuid), CAST(:s AS uuid), CAST(:org AS uuid), :key, "
        "'abcdef012345', :idx, 'How many work orders were raised at Bishopsgate Tower in July 2026?', "
        "'There were 786.', 'objective')"), {"id": gid, "s": set_id, "org": org, "key": key, "idx": idx})
    return gid


async def _golden_run(conn, set_id: str, org: str = ORG_A, *, k: int = 5, purpose: str = "baseline") -> str:
    rid = str(uuid.uuid4())
    await conn.execute(text(
        "INSERT INTO tracelab.golden_runs (id, organization_id, set_id, label, purpose, k, status, started_by) "
        "VALUES (CAST(:id AS uuid), CAST(:org AS uuid), CAST(:s AS uuid), :label, :p, :k, 'done', 'u1')"),
        {"id": rid, "org": org, "s": set_id, "label": "run-" + rid[:8], "p": purpose, "k": k})
    return rid


async def _case(conn, **over) -> str:
    row = {"id": str(uuid.uuid4()), "org": ORG_A, "key": "e-" + uuid.uuid4().hex[:8], "version": 1, "status": "proposed",
           "kind": "unclassified", "rate": None, "twin": None, "stype": "live_turn", "turn": "turn-00000000000000b1",
           "batch": None, "gsource": None, "supersedes": None, "mode": None, "run": None, **over}
    row["rate"] = None if row["rate"] is None else Decimal(str(row["rate"]))
    await conn.execute(text(
        "INSERT INTO tracelab.eval_cases (id, organization_id, case_key, version, status, kind, baseline_pass_rate, "
        "baseline_run_id, mode_id, source_type, source_turn_id, source_batch_id, source_golden_case_id, "
        "golden_case_id, input, expected, supersedes) VALUES (CAST(:id AS uuid), CAST(:org AS uuid), :key, :version, "
        ":status, :kind, :rate, CAST(:run AS uuid), CAST(:mode AS uuid), :stype, :turn, CAST(:batch AS uuid), "
        "CAST(:gsource AS uuid), CAST(:twin AS uuid), CAST(:input AS jsonb), CAST(:expected AS jsonb), "
        "CAST(:supersedes AS uuid))"),
        {**row, "input": json.dumps({"question": "q", "followups": []}),
         "expected": json.dumps({"assertions": [], "checks": [], "judges": [], "golden_expected": True})})
    return row["id"]


async def _case_run(conn, case_id: str, run_id: str, **over) -> str:
    row = {"id": str(uuid.uuid4()), "case": case_id, "run": run_id, "purpose": "release_check", "k": 5, **over}
    cols = ["id", "eval_case_id", "golden_run_id", "purpose", "k"]
    binds = ["CAST(:id AS uuid)", "CAST(:case AS uuid)", "CAST(:run AS uuid)", ":purpose", ":k"]
    for name in ("n", "passes", "degraded", "pending", "decision"):
        if name in row:
            cols.append(name)
            binds.append(f":{name}")
    await conn.execute(text(f"INSERT INTO tracelab.eval_case_runs ({', '.join(cols)}) VALUES ({', '.join(binds)})"), row)
    return row["id"]


async def _twin(conn, org: str = ORG_A, key: str = "e-0000000a") -> str:
    return await _golden_case(conn, await _regression_set(conn, org), key, org)


async def test_migrations_through_0006_apply_with_lock_timeout(lab_scratch):
    state = await migrate.verify(lab_scratch)
    assert state.status == "ready" and state.applied_version >= 6
    found = {f.version: f for f in migrate.files()}
    assert found[6].name == "0006_watch.sql"
    first = [ln for ln in found[6].sql.splitlines() if ln.strip() and not ln.strip().startswith("--")][0]
    assert first == "SET LOCAL lock_timeout = '3s';"
    async with lab_scratch.connect() as conn:
        ledger = {int(r["version"]): r for r in await migrate.read_ledger(conn)}
    assert ledger[6]["checksum"] == found[6].checksum and ledger[6]["applied_by"] == "pytest"


async def test_monitor_verdict_unique_and_decided_is_final(lab_scratch):
    async with lab_scratch.begin() as conn:
        bid = await _batch(conn)
        await _verdict(conn, bid, "decided", 1)
        await _verdict(conn, bid, "error", None, key="code:refusal@v1")
    await _fails(lab_scratch, IntegrityError, "ux_monitor_verdicts_evaluator_turn",
                 lambda c: _verdict(c, bid, "decided", 0))                      # same evaluator and answer
    async with lab_scratch.begin() as conn:                                    # an error row may be retried
        await conn.execute(text("UPDATE tracelab.monitor_verdicts SET status = 'decided', failure_present = 0, "
                                "attempts = attempts + 1 WHERE evaluator_key = 'code:refusal@v1'"))
    for key in ("code:no_answer@v1", "code:refusal@v1"):                       # a decided row never changes again
        for change in ("failure_present = 0", "attempts = 9", "updated_at = now()", "detail = '{}'::jsonb"):
            await _fails(lab_scratch, DBAPIError, "a decided verdict is never changed",
                         lambda c, k=key, ch=change: c.execute(text(
                             f"UPDATE tracelab.monitor_verdicts SET {ch} WHERE evaluator_key = '{k}'")))
    await _fails(lab_scratch, DBAPIError, "removed only with company data",
                 lambda c: c.execute(text("DELETE FROM tracelab.monitor_verdicts")))
    await _fails(lab_scratch, DBAPIError, "TRUNCATE refused",
                 lambda c: c.execute(text("TRUNCATE tracelab.monitor_verdicts")))
    async with lab_scratch.begin() as conn:                                    # only company offboarding deletes
        await conn.execute(text("SET LOCAL tracelab.allow_purge = 'on'"))
        assert (await conn.execute(text("DELETE FROM tracelab.monitor_verdicts"))).rowcount == 2
    async with lab_scratch.begin() as conn:
        await _verdict(conn, bid, "decided", 1)
        await conn.execute(text("SET LOCAL tracelab.allow_purge = 'on'"))
        await conn.execute(text("TRUNCATE tracelab.monitor_verdicts"))
    async with lab_scratch.connect() as conn:
        assert (await conn.execute(text("SELECT count(*) FROM tracelab.monitor_verdicts"))).scalar() == 0


async def test_an_unsettled_verdict_can_change_until_it_is_decided(lab_scratch):
    """Only a decided verdict is final: undecidable, not_applicable and error rows are retried in place."""
    async with lab_scratch.begin() as conn:
        bid = await _batch(conn)
        await _verdict(conn, bid, "undecidable", None, key="code:a@v1")
        await _verdict(conn, bid, "not_applicable", None, key="code:b@v1")
        await _verdict(conn, bid, "error", None, key="code:c@v1")
    async with lab_scratch.begin() as conn:
        await conn.execute(text("UPDATE tracelab.monitor_verdicts SET attempts = attempts + 1, detail = '{}'::jsonb"))
        await conn.execute(text("UPDATE tracelab.monitor_verdicts SET status = 'decided', failure_present = 1"))
    async with lab_scratch.connect() as conn:
        rows = (await conn.execute(text("SELECT status, failure_present, attempts FROM tracelab.monitor_verdicts"))).all()
    assert sorted(tuple(r) for r in rows) == [("decided", 1, 2)] * 3


async def test_monitor_verdict_value_rules(lab_scratch):
    async with lab_scratch.begin() as conn:
        bid = await _batch(conn)
        judge = await _code_judge(conn)
        await _verdict(conn, bid, "decided", 0, key=f"judge:{judge}", judge_id=judge, turn="turn-j")   # a judge verdict
    async with lab_scratch.begin() as conn:
        await _verdict(conn, bid, "undecidable", None, key="code:x_y1@v12", turn="turn-k")             # a code verdict
    nothing = "ck_monitor_verdicts_decided"
    for bad, match in (
            (dict(status="decided", flag=None), nothing),                       # decided needs a flag
            (dict(status="error", flag=1), nothing),                            # a flag only comes with a decision
            (dict(status="decided", flag=2), "failure_present_check"),
            (dict(status="maybe", flag=None), "monitor_verdicts_status_check"),
            (dict(status="decided", flag=1, key="judge:" + "a" * 36), "ck_monitor_verdicts_judge"),   # judge key, no judge
            (dict(status="decided", flag=1, judge_id=judge), "ck_monitor_verdicts_judge"),            # code key, with a judge
            (dict(status="decided", flag=1, key="judge:not-a-judge-id", judge_id=judge), "evaluator_key_check"),
            (dict(status="decided", flag=1, key="judge:" + "A" * 36, judge_id=judge), "evaluator_key_check"),
            (dict(status="decided", flag=1, key="code:Bad Name@v1"), "evaluator_key_check"),
            (dict(status="decided", flag=1, key="code:no_version"), "evaluator_key_check"),
            (dict(status="decided", flag=1, key=""), "evaluator_key_check"),
            (dict(status="decided", flag=1, turn=""), "turn_id_check"),
            (dict(status="decided", flag=1, turn="t" * 201), "turn_id_check"),
            (dict(status="decided", flag=1, attempts=0), "attempts_check")):
        fields = {k: v for k, v in bad.items() if k not in ("status", "flag")}
        await _fails(lab_scratch, IntegrityError, match,
                     lambda c, b=bad, f=fields: _verdict(c, bid, b["status"], b["flag"], **f))
    await _fails(lab_scratch, IntegrityError, "monitor_verdicts_first_batch_id_fkey",
                 lambda c: _verdict(c, str(uuid.uuid4()), "decided", 1, turn="turn-z"))              # a batch must exist
    await _fails(lab_scratch, IntegrityError, "monitor_verdicts_judge_id_fkey",
                 lambda c: _verdict(c, bid, "decided", 1, key="judge:" + "b" * 36, judge_id=str(uuid.uuid4()),
                                    turn="turn-y"))


async def test_one_active_batch_per_scope(lab_scratch):
    async with lab_scratch.begin() as conn:
        await _batch(conn, ORG_A)
        await _batch(conn, None)                                          # one company and the platform: fine
    for org, status in ((ORG_A, "running"), (ORG_A, "queued"), (None, "queued"), (None, "running")):
        await _fails(lab_scratch, IntegrityError, "ux_monitor_batches_active",
                     lambda c, o=org, s=status: _batch(c, o, s))
    async with lab_scratch.begin() as conn:
        await _batch(conn, ORG_A, "done")                                 # finished batches never block
        await _batch(conn, ORG_A, "capped")
        await _batch(conn, ORG_A, "failed")
        await _batch(conn, ORG_A, "interrupted")
        await _batch(conn, None, "done")
        await _batch(conn, ORG_B)                                         # another company has its own
    async with lab_scratch.begin() as conn:                               # finishing the active one frees the scope
        await conn.execute(text("UPDATE tracelab.monitor_batches SET status = 'done' WHERE status = 'queued'"))
        await _batch(conn, ORG_A)
        await _batch(conn, None, "running")


async def test_monitor_batch_value_rules(lab_scratch):
    async with lab_scratch.begin() as conn:
        job = await _job(conn)
        await _batch(conn, ORG_A, "done", rate=1, job=job)                # a rate of 1 is every answer
        await _batch(conn, ORG_A, "done", rate=0.0001)
    for over, match in ((dict(rate=0), "sample_rate_check"), (dict(rate=1.5), "sample_rate_check"),
                        (dict(rate=-0.1), "sample_rate_check"),
                        (dict(channel="voice"), "monitor_batches_channel_check"),
                        (dict(status="paused"), "monitor_batches_status_check"),
                        (dict(period="ab"), "period_label_check"), (dict(period="x" * 41), "period_label_check"),
                        (dict(usd=-1), "monitor_batches_usd_check"),
                        (dict(end=WEEK[0]), "ck_monitor_batches_window"),                      # empty window
                        (dict(end=WEEK[0] - timedelta(days=1)), "ck_monitor_batches_window"),  # backwards window
                        (dict(job=str(uuid.uuid4())), "monitor_batches_job_id_fkey")):         # the job must exist
        await _fails(lab_scratch, IntegrityError, match, lambda c, o=over: _batch(c, ORG_B, **o))


async def test_eval_case_kind_baseline_checks(lab_scratch):
    async with lab_scratch.begin() as conn:
        twin = await _twin(conn)
        await _case(conn, status="proposed")                              # a proposed case has no twin yet
        await _case(conn, status="active", kind="regression", twin=twin)
        await _case(conn, status="active", kind="capability", rate=0.6, twin=twin)
        await _case(conn, status="retired")                               # a retired case may lose its twin
    for bad, match in ((dict(status="draft"), "ck_eval_cases_twin"),                             # no twin
                       (dict(status="baselining"), "ck_eval_cases_twin"),
                       (dict(status="active", twin=twin), "ck_eval_cases_active_classified"),    # active but unclassified
                       (dict(status="active", kind="capability", twin=twin), "ck_eval_cases_capability_rate"),
                       (dict(status="active", kind="regression", rate=0.4, twin=twin), "ck_eval_cases_capability_rate"),
                       (dict(status="draft", kind="unclassified", rate=0.4, twin=twin), "ck_eval_cases_capability_rate"),
                       (dict(status="draft", kind="capability", rate=0.5, twin=twin), "baseline_pass_rate_check"),
                       (dict(status="draft", kind="capability", rate=1.0, twin=twin), "baseline_pass_rate_check"),
                       (dict(status="draft", kind="hybrid", twin=twin), "eval_cases_kind_check"),
                       (dict(status="done", twin=twin), "eval_cases_status_check")):
        await _fails(lab_scratch, IntegrityError, match, lambda c, b=bad: _case(c, **b))
    async with lab_scratch.begin() as conn:                               # every rate a 5-run baseline can give
        for rate in (0.0, 0.2, 0.4, 0.6, 0.8):
            await _case(conn, status="draft", kind="capability", rate=rate, twin=twin)


async def test_eval_case_source_names_what_it_came_from(lab_scratch):
    async with lab_scratch.begin() as conn:
        twin = await _twin(conn)
        batch = await _batch(conn, ORG_A, "done")
        await _case(conn, stype="live_turn", turn="turn-1")
        await _case(conn, stype="monitor", turn="turn-2", batch=batch)
        await _case(conn, stype="golden_case", turn=None, gsource=twin)
        await _case(conn, stype="golden_case", turn="turn-3", gsource=twin)       # a golden case may also cite a turn
    for bad, match in ((dict(stype="golden_case", turn=None), "ck_eval_cases_golden_source"),      # no golden case named
                       (dict(stype="live_turn", gsource=twin), "ck_eval_cases_golden_source"),     # one named by mistake
                       (dict(stype="live_turn", turn=None), "ck_eval_cases_turn_source"),
                       (dict(stype="monitor", turn="turn-4"), "ck_eval_cases_monitor_source"),     # no batch named
                       (dict(stype="monitor", turn=None, batch=batch), "ck_eval_cases_turn_source"),
                       (dict(stype="live_turn", turn=""), "source_turn_id_check"),
                       (dict(stype="chat"), "eval_cases_source_type_check"),
                       (dict(stype="monitor", turn="turn-5", batch=str(uuid.uuid4())), "source_batch_id_fkey"),
                       (dict(stype="live_turn", mode=str(uuid.uuid4())), "eval_cases_mode_id_fkey"),
                       (dict(stype="live_turn", run=str(uuid.uuid4())), "baseline_run_id_fkey"),
                       (dict(status="draft", twin=str(uuid.uuid4())), "golden_case_id_fkey")):
        await _fails(lab_scratch, IntegrityError, match, lambda c, b=bad: _case(c, **b))


async def test_eval_case_has_one_current_version_per_company_and_key(lab_scratch):
    async with lab_scratch.begin() as conn:
        twin = await _twin(conn)
        first = await _case(conn, key="e-00000001", status="active", kind="regression", twin=twin)
        await _case(conn, key="e-00000001", org=ORG_B, status="proposed")        # another company, same key
        await _case(conn, key="e-00000002", status="proposed")
    await _fails(lab_scratch, IntegrityError, "ux_eval_cases_current",
                 lambda c: _case(c, key="e-00000001", version=2, status="proposed"))       # two live versions
    await _fails(lab_scratch, IntegrityError, "ux_eval_cases_key_version",
                 lambda c: _case(c, key="e-00000001", version=1, status="superseded", kind="regression", twin=twin))
    for key in ("e-123", "E-00000001", "e-0000000g", "e-000000012", "case-1", ""):
        await _fails(lab_scratch, IntegrityError, "case_key_check", lambda c, k=key: _case(c, key=k))
    await _fails(lab_scratch, IntegrityError, "eval_cases_version_check", lambda c: _case(c, version=0))
    async with lab_scratch.begin() as conn:                                              # superseding frees the key
        await conn.execute(text("UPDATE tracelab.eval_cases SET status = 'superseded' WHERE id = CAST(:i AS uuid)"),
                           {"i": first})
        second = await _case(conn, key="e-00000001", version=2, status="active", kind="regression", twin=twin,
                             supersedes=first)
        await _case(conn, key="e-00000001", version=3, status="superseded", kind="regression", twin=twin)
    async with lab_scratch.connect() as conn:
        row = (await conn.execute(text("SELECT supersedes::text FROM tracelab.eval_cases WHERE id = CAST(:i AS uuid)"),
                                  {"i": second})).scalar()
    assert row == first
    await _fails(lab_scratch, IntegrityError, "eval_cases_supersedes_fkey",
                 lambda c: _case(c, key="e-00000009", supersedes=str(uuid.uuid4())))


async def test_eval_case_run_is_one_row_per_case_and_golden_run(lab_scratch):
    async with lab_scratch.begin() as conn:
        sid = await _regression_set(conn)
        twin = await _golden_case(conn, sid)
        case = await _case(conn, status="active", kind="regression", twin=twin)
        other = await _case(conn, status="active", kind="regression", twin=twin)
        run = await _golden_run(conn, sid)
        second = await _golden_run(conn, sid, purpose="release_check")
        await _case_run(conn, case, run)
        await _case_run(conn, other, run)                                         # two cases, one run
        await _case_run(conn, case, second, purpose="repeat", k=20, n=20, passes=20, decision="pass")
    async with lab_scratch.connect() as conn:                                     # the defaults of a run still going
        row = (await conn.execute(text(
            "SELECT n, passes, degraded, pending, reps::text, graders::text, decision, reason FROM "
            "tracelab.eval_case_runs WHERE eval_case_id = CAST(:c AS uuid) AND golden_run_id = CAST(:r AS uuid)"),
            {"c": case, "r": run})).one()
    assert tuple(row) == (0, 0, 0, 0, "[]", "{}", "incomplete", "running")
    await _fails(lab_scratch, IntegrityError, "ux_eval_case_runs_case_run", lambda c: _case_run(c, case, run))
    for over, match in ((dict(passes=6, n=5), "ck_eval_case_runs_counts"),
                        (dict(passes=1), "ck_eval_case_runs_counts"),                 # more passes than runs
                        (dict(k=0), "eval_case_runs_k_check"), (dict(k=21), "eval_case_runs_k_check"),
                        (dict(degraded=-1), "eval_case_runs_degraded_check"),
                        (dict(pending=-1), "eval_case_runs_pending_check"),
                        (dict(passes=-1), "eval_case_runs_passes_check"),
                        (dict(purpose="adhoc"), "eval_case_runs_purpose_check"),      # a golden run purpose, not a case one
                        (dict(decision="maybe"), "eval_case_runs_decision_check")):
        await _fails(lab_scratch, IntegrityError, match,
                     lambda c, o=over: _case_run(c, other, second, **o))
    await _fails(lab_scratch, IntegrityError, "eval_case_id_fkey", lambda c: _case_run(c, str(uuid.uuid4()), run))
    await _fails(lab_scratch, IntegrityError, "golden_run_id_fkey", lambda c: _case_run(c, case, str(uuid.uuid4())))
