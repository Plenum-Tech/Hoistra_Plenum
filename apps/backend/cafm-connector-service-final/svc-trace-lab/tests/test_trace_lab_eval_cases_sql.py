"""Eval cases on a scratch Postgres (tracelab_scratch): a case and its golden twin land in the lab database (the
company's Regression cases set), the chat is read through the read-only main engine for the head of one answer only,
the baseline classifies a case, the release check reads its runs, and no statement names both databases (Golden Rule
G0). The golden runner itself is not run: its rows are seeded the way runner._store_item writes them (one row per
message, the final row carrying the outcome) and START_GOLDEN_RUN is replaced. Skips without TRACE_LAB_TEST_DB_URL."""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import IntegrityError

import trace_lab_m4_fixtures as fx
from golden_seed import seed_result, seed_set
from tl_scratch import lab_scratch, main_scratch, requires_scratch  # noqa: F401
from trace_lab import db, eval_cases, golden, golden_store, jobs, runner
from trace_lab.monitor import WatchError
from trace_lab_m2_fixtures import add_span, add_thread, add_turn, truncate_chat, turn_id

pytestmark = requires_scratch

UTC = timezone.utc
ORG_A = fx.ORG_A
ORG_B = fx.ORG_B
ACTOR = fx.OWNER_ID
EMAIL = "reviewer@example.com"
QUESTION = "How many work orders were raised at Bishopsgate Tower in July 2026?"
CTX = eval_cases.RunContext(org=ORG_A, principal=SimpleNamespace(role="admin", user_id=ACTOR, email=EMAIL),
                            authorization="Bearer test")
WRITE = re.compile(r"^\s*(INSERT|UPDATE|DELETE|TRUNCATE|CREATE|ALTER|DROP|GRANT|COPY)\b", re.I)


@pytest.fixture
async def chat(main_scratch, lab_scratch):  # noqa: F811
    """The writable main engine, seeded; the lab engine is db.lab_engine(). Code under test reads the chat through
    db.main_engine() (read-only) only."""
    yield main_scratch
    await truncate_chat(main_scratch)


async def _scalar(sql: str, **params):
    async with db.lab_engine().connect() as conn:
        return (await conn.execute(text(sql), params)).scalar()


async def _rows(sql: str, **params):
    async with db.lab_engine().connect() as conn:
        return [dict(r) for r in (await conn.execute(text(sql), params)).mappings().all()]


async def _wrong(turn: str, org: str = ORG_A) -> None:
    """A reviewer marked the answer wrong: a live verdict annotation (the lab's own table)."""
    async with db.lab_engine().begin() as conn:
        await fx.add_note(conn, turn, org)


async def _no_judges(monkeypatch) -> None:
    async def none(mode_ids=None):
        return []

    monkeypatch.setattr(eval_cases, "judges_in_use", none)


async def _case_from_golden(org: str = ORG_A, *, key: str = "1", expected=None, set_case=None):
    """A draft case made from a golden case of the company's uploaded set. Returns the stored case."""
    async with db.lab_engine().begin() as conn:
        seeded = set_case or await seed_set(conn, org, cases=((key, ()),))
    return await eval_cases.create_case(
        org, source={"type": "golden_case", "golden_case_id": seeded["cases"][key]},
        expected=expected or {"checks": [{"check": "must_contain", "text": "786"}], "golden_expected": False},
        actor_id=ACTOR, actor_email=EMAIL)


# ---- the twin lands in the company's Regression cases set ----------------------------------------------------------

async def test_case_twin_lands_in_the_regression_set(chat, monkeypatch):
    await _no_judges(monkeypatch)
    await add_thread(chat, "s-obs", ORG_A)
    await add_turn(chat, 1, ORG_A, session="s-obs", question=QUESTION, minutes_ago=60)
    await add_turn(chat, 2, ORG_A, session="s-obs", question="And in August?", minutes_ago=50)
    await add_turn(chat, 3, ORG_A, session="s-3", question=QUESTION, minutes_ago=40)
    for n in (2, 3):
        await add_span(chat, turn_id(n), org=ORG_A)
        await _wrong(turn_id(n))
    first = await eval_cases.create_case(ORG_A, source={"type": "live_turn", "turn_id": turn_id(2)},
                                         expected_answer="There were 786 work orders in July 2026.",
                                         actor_id=ACTOR, actor_email=EMAIL)
    await eval_cases.create_case(ORG_A, source={"type": "live_turn", "turn_id": turn_id(3)},
                                 expected_answer="There were 786 work orders in July 2026.", actor_id=ACTOR,
                                 actor_email=EMAIL)
    sets = await _rows("SELECT * FROM tracelab.golden_sets WHERE organization_id = CAST(:o AS uuid)", o=ORG_A)
    assert len(sets) == 1
    (rs,) = sets
    assert (rs["kind"], rs["name"], rs["version"], rs["status"], rs["case_count"]) == ("regression", "Regression cases", 0, "active", 2)
    assert rs["created_by"] == ACTOR and rs["created_by_email"] == EMAIL
    twins = await _rows("SELECT * FROM tracelab.golden_cases WHERE set_id = CAST(:s AS uuid) ORDER BY idx", s=str(rs["id"]))
    assert [(t["idx"], t["row_no"]) for t in twins] == [(0, 1), (1, 2)]
    t1 = twins[0]
    assert t1["case_key"] == f"{first['case_key']}.v1" and t1["question"] == QUESTION and t1["organization_id"] is not None
    followups = json.loads(t1["followups"]) if isinstance(t1["followups"], str) else t1["followups"]
    assert followups == ["And in August?"]
    assert t1["expected_answer"].startswith("There were 786") and t1["evaluation"] == "objective" and t1["ask_as"] == "company"
    assert re.fullmatch(r"[0-9a-f]{12}", t1["case_version"]) and t1["verified_on"] == datetime.now(UTC).date()
    assert t1["page_context"] == "" and t1["stale"] is False                    # no page was recorded: ask with none
    assert rs["set_hash"] == golden.set_hash([(t["case_key"], t["case_version"]) for t in twins])
    assert str((await golden_store.regression_set(ORG_A))["id"]) == str(rs["id"])      # found by golden_store's own reader
    assert first["status"] == "draft" and first["golden_case_id"] == str(t1["id"]) and first["input"]["followups"] == ["And in August?"]
    kept = await _rows("SELECT turn_id, reason, organization_id FROM tracelab.evidence_turns ORDER BY turn_id")
    assert [(k["turn_id"], k["reason"]) for k in kept] == [(turn_id(2), "proposed"), (turn_id(3), "proposed")]
    assert await _scalar("SELECT count(*) FROM tracelab.evidence_spans") == 2          # their spans were copied too
    with pytest.raises(WatchError) as e:                                       # one live case per answer
        await eval_cases.create_case(ORG_A, source={"type": "live_turn", "turn_id": turn_id(2)}, expected_answer="x",
                                     actor_id=ACTOR, actor_email=EMAIL)
    assert e.value.reason == "case_exists"
    edited = await eval_cases.edit_case(ORG_A, first["id"], base=first["rev"], expected_answer="786 work orders.",
                                        actor_id=ACTOR, actor_email=EMAIL)
    assert edited["version"] == 2 and edited["status"] == "draft" and edited["supersedes"] == first["id"]
    assert await _scalar("SELECT status FROM tracelab.eval_cases WHERE id = CAST(:i AS uuid)", i=first["id"]) == "superseded"
    keys = [r["case_key"] for r in await _rows("SELECT case_key FROM tracelab.golden_cases ORDER BY idx")]
    assert keys[2] == f"{first['case_key']}.v2" and len(keys) == 3
    assert await _scalar("SELECT case_count FROM tracelab.golden_sets WHERE id = CAST(:s AS uuid)", s=str(rs["id"])) == 3
    with pytest.raises(WatchError) as e:                                       # the old version is gone for edits
        await eval_cases.edit_case(ORG_A, first["id"], base=first["rev"], actor_id=ACTOR, actor_email=EMAIL)
    assert e.value.reason == "case_retired"
    listed = await eval_cases.list_cases(ORG_A)                                # the list shows the current version only
    assert sorted(c["version"] for c in listed if c["case_key"] == first["case_key"]) == [2]
    assert [c["status"] for c in await eval_cases.list_cases(ORG_A, status="superseded")] == ["superseded"]
    with pytest.raises(WatchError) as e:                                       # and a stale base is refused
        await eval_cases.edit_case(ORG_A, edited["id"], base=edited["rev"] + 5, actor_id=ACTOR, actor_email=EMAIL)
    assert e.value.reason == "stale_base"


async def test_a_stale_edit_leaves_no_twin_behind(chat, monkeypatch):
    await _no_judges(monkeypatch)
    case = await _case_from_golden()
    before = await _scalar("SELECT count(*) FROM tracelab.golden_cases WHERE case_key LIKE 'e-%'")
    with pytest.raises(WatchError) as e:
        await eval_cases.edit_case(ORG_A, case["id"], base=case["rev"] + 1, input_override={"question": "Another?"},
                                   actor_id=ACTOR, actor_email=EMAIL)
    assert (e.value.status, e.value.reason) == (409, "stale_base")
    assert await _scalar("SELECT count(*) FROM tracelab.golden_cases WHERE case_key LIKE 'e-%'") == before
    assert await _scalar("SELECT case_count FROM tracelab.golden_sets WHERE kind = 'regression'") == before
    assert await _scalar("SELECT status FROM tracelab.eval_cases WHERE id = CAST(:i AS uuid)", i=case["id"]) == "draft"


async def test_no_set_is_made_without_an_actor_and_another_companys_answer_is_never_a_case(chat, monkeypatch):
    await _no_judges(monkeypatch)
    await add_turn(chat, 1, ORG_A, question=QUESTION)
    await add_turn(chat, 2, ORG_B, question=QUESTION)
    await _wrong(turn_id(1))
    await _wrong(turn_id(2), ORG_B)
    with pytest.raises(WatchError) as e:
        await eval_cases.create_case(ORG_A, source={"type": "live_turn", "turn_id": turn_id(2)}, expected_answer="x",
                                     actor_id=ACTOR, actor_email=EMAIL)
    assert (e.value.status, e.value.reason) == (404, "turn_not_in_scope")
    assert await _scalar("SELECT count(*) FROM tracelab.evidence_turns") == 0           # nothing of company B was copied
    with pytest.raises(WatchError) as e:
        await eval_cases.create_case(ORG_A, source={"type": "live_turn", "turn_id": turn_id(1)}, expected_answer="x",
                                     actor_id=ACTOR, actor_email=None)
    assert (e.value.status, e.value.reason) == (422, "actor_required")
    assert await _scalar("SELECT count(*) FROM tracelab.golden_sets") == 0
    assert await _scalar("SELECT count(*) FROM tracelab.eval_cases") == 0


# ---- the head of one answer (the only read of the chat) -------------------------------------------------------------

async def test_the_head_of_an_answer_is_read_from_the_chat_for_its_company_and_never_written(chat):
    wrapped = "Page: Assets\n\n---\n\n**Current user message:**\nHow many assets are due a service?"
    await add_turn(chat, 1, ORG_A, session="s-1", question=QUESTION)
    await add_turn(chat, 2, ORG_A, session="s-2", question="Validate and ingest this document.")
    await add_turn(chat, 3, ORG_B, session="s-3", question=QUESTION)
    await add_turn(chat, 4, None, session="s-4", question=QUESTION)
    await add_turn(chat, 5, ORG_A, session="golden-run-1-case-a-1", question=QUESTION)
    await add_turn(chat, 6, ORG_A, session="s-6", question=wrapped)
    seen = []
    engine = db.main_engine().sync_engine

    @event.listens_for(engine, "before_cursor_execute")
    def spy(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    try:
        one = await eval_cases._turn_for_case(ORG_A, turn_id(1))
        assert (one["turn_id"], one["organization_id"], one["session_id"], one["channel"]) == (turn_id(1), ORG_A, "s-1", "chat")
        assert one["question"] == QUESTION
        assert (await eval_cases._turn_for_case(ORG_A, turn_id(2)))["channel"] == "upload"
        assert (await eval_cases._turn_for_case(ORG_A, turn_id(5)))["channel"] == "golden"
        assert (await eval_cases._turn_for_case(ORG_A, turn_id(6)))["question"] == "How many assets are due a service?"
        assert await eval_cases._turn_for_case(ORG_A, turn_id(3)) is None            # another company's answer
        assert (await eval_cases._turn_for_case(None, turn_id(3)))["organization_id"] == ORG_B   # a platform batch
        assert await eval_cases._turn_for_case(None, turn_id(4)) is None             # an answer with no company
        assert await eval_cases._turn_for_case(ORG_A, "turn-missing") is None
        assert await eval_cases._turn_for_case(ORG_A, "t" * 300) is None
    finally:
        event.remove(engine, "before_cursor_execute", spy)
    assert seen and not [s for s in seen if WRITE.match(s)]                          # a read-only transaction, no write
    assert not [s for s in seen if "tracelab." in s]                                  # and no statement names the lab


# ---- a confirmed failure ------------------------------------------------------------------------------------------

async def test_a_failure_is_confirmed_by_a_live_single_label_or_a_wrong_verdict_of_the_same_company(chat):
    async with db.lab_engine().begin() as conn:
        m1 = await fx.add_mode(conn, "wrong_period", status="confirmed")
        m2 = await fx.add_mode(conn, "invented_code", status="confirmed")
        await fx.add_label(conn, "t-label", m1, present=True)
        await fx.add_label(conn, "t-absent", m1, present=False)
        run = await fx.add_run(conn, "cells", mode_id=m1, org=ORG_A)
        bulk = await fx.add_proposal(conn, run, turn_id="t-bulk", mode_id=m1)
        await fx.add_label(conn, "t-bulk", m1, present=True, source="accepted_suggestion", approval="bulk",
                           proposal_id=bulk)
        await fx.add_label(conn, "t-tomb", m1, present=None)
        await fx.add_label(conn, "t-other-org", m1, present=True, org=ORG_B)
        await fx.add_note(conn, "t-wrong", ORG_A, verdict="wrong")
        await fx.add_note(conn, "t-right", ORG_A, verdict="right")
        await fx.add_note(conn, "t-wrong-b", ORG_B, verdict="wrong")
        old = await fx.add_label(conn, "t-moved", m1, present=True)
    async with db.lab_engine().begin() as conn:
        await conn.execute(text("UPDATE tracelab.labels SET superseded_by = gen_random_uuid() WHERE id = CAST(:i AS uuid)"), {"i": old})
    confirmed = eval_cases._failure_confirmed
    assert await confirmed(ORG_A, "t-label", m1) is True
    assert await confirmed(ORG_A, "t-label", None) is True
    assert await confirmed(ORG_A, "t-label", m2) is False                              # a label for another mode
    assert await confirmed(ORG_A, "t-absent", m1) is False
    assert await confirmed(ORG_A, "t-bulk", m1) is False                               # approved in bulk: no person decided it
    assert await confirmed(ORG_A, "t-tomb", m1) is False
    assert await confirmed(ORG_A, "t-moved", m1) is False                              # only the live label counts
    assert await confirmed(ORG_A, "t-other-org", m1) is False
    assert await confirmed(ORG_A, "t-wrong", m2) is True                               # a wrong verdict covers any mode
    assert await confirmed(ORG_A, "t-right", m1) is False
    assert await confirmed(ORG_A, "t-wrong-b", m1) is False and await confirmed(ORG_B, "t-wrong-b", None) is True


# ---- a monitoring flag becomes a proposed case, a person confirms it ------------------------------------------------

async def _monitor_batch(org: str | None = ORG_A) -> str:
    bid = str(uuid.uuid4())
    async with db.lab_engine().begin() as conn:
        await conn.execute(text(
            "INSERT INTO tracelab.monitor_batches (id, organization_id, period_label, window_start, window_end, channel, "
            "sample_rate, seed, risk_groups, judges, checks, status, started_by, started_by_email) VALUES "
            "(CAST(:id AS uuid), CAST(:org AS uuid), '2026-W41', :s, :e, 'chat', 0.25, 7, CAST(:g AS text[]), "
            "CAST('[]' AS jsonb), CAST('[]' AS jsonb), 'done', CAST(:by AS uuid), :email)"),
            {"id": bid, "org": org, "s": datetime(2026, 10, 5, tzinfo=UTC), "e": datetime(2026, 10, 12, tzinfo=UTC),
             "g": ["failed"], "by": ACTOR, "email": EMAIL})
    return bid


async def test_a_monitoring_flag_is_proposed_without_a_twin_and_confirmed_with_one(chat, monkeypatch):
    async with db.lab_engine().begin() as conn:
        mode = await fx.add_mode(conn, "wrong_period", status="confirmed")
    for n in (1, 2, 3):
        await add_turn(chat, n, ORG_A, session=f"s-{n}", question=QUESTION)
    await add_turn(chat, 4, ORG_A, session="s-4", question="Raise a work order for the boiler")
    batch = {"id": await _monitor_batch(), "started_by": ACTOR, "started_by_email": EMAIL}
    ids = await eval_cases.propose_from_monitor(batch=batch, mode_id=mode, turn_ids=[turn_id(n) for n in (1, 2, 3, 4)])
    assert len(ids) == 3                                                              # the write request cannot replay
    rows = await _rows("SELECT * FROM tracelab.eval_cases ORDER BY source_turn_id")
    assert [r["status"] for r in rows] == ["proposed"] * 3 and {r["golden_case_id"] for r in rows} == {None}
    assert {r["source_type"] for r in rows} == {"monitor"} and {str(r["source_batch_id"]) for r in rows} == {batch["id"]}
    assert str(rows[0]["created_by"]) == ACTOR and rows[0]["created_by_email"] == EMAIL
    assert await _scalar("SELECT count(*) FROM tracelab.golden_sets") == 0            # confirming is where the twin is made
    assert await _scalar("SELECT count(*) FROM tracelab.evidence_turns") == 3
    again = await eval_cases.propose_from_monitor(batch=batch, mode_id=mode, turn_ids=[turn_id(1), turn_id(2)])
    assert again == []                                                                # already proposed: skipped
    case = rows[0]
    kw = dict(base=case["rev"], expected_answer="There were 786 work orders in July 2026.", actor_id=ACTOR, actor_email=EMAIL)
    with pytest.raises(WatchError) as e:
        await eval_cases.confirm_case(ORG_A, str(case["id"]), **kw)                   # nobody has marked it wrong yet
    assert e.value.reason == "failure_not_confirmed"
    await _wrong(case["source_turn_id"])
    with pytest.raises(WatchError) as e:
        await eval_cases.confirm_case(ORG_A, str(case["id"]), **{**kw, "base": case["rev"] + 1})
    assert e.value.reason == "stale_base"
    assert await _scalar("SELECT count(*) FROM tracelab.golden_cases") == 0           # the refused confirm left no twin
    assert await _scalar("SELECT count(*) FROM tracelab.golden_sets") == 0
    out = await eval_cases.confirm_case(ORG_A, str(case["id"]), **kw)
    assert out["status"] == "draft" and out["golden_case_id"] and out["rev"] == case["rev"] + 1
    assert await _scalar("SELECT case_key FROM tracelab.golden_cases") == f"{case['case_key']}.v1"
    assert await _scalar("SELECT case_count FROM tracelab.golden_sets WHERE kind = 'regression'") == 1
    with pytest.raises(WatchError) as e:
        await eval_cases.confirm_case(ORG_A, str(case["id"]), **{**kw, "base": out["rev"]})
    assert e.value.reason == "not_proposed"
    # the same list, by filter: the superseded and retired ones stay out unless asked for
    retired = await eval_cases.retire_case(ORG_A, str(rows[1]["id"]), base=rows[1]["rev"], reason="  not a real failure  ")
    assert retired["status"] == "retired" and retired["retired_reason"] == "not a real failure"
    with pytest.raises(WatchError) as e:
        await eval_cases.retire_case(ORG_A, str(rows[1]["id"]), base=retired["rev"], reason="again")
    assert e.value.reason == "stale_base"
    with pytest.raises(WatchError) as e:
        await eval_cases.retire_case(ORG_A, str(uuid.uuid4()), base=1, reason="x")
    assert e.value.status == 404
    with pytest.raises(WatchError) as e:
        await eval_cases.retire_case(ORG_A, "not-a-uuid", base=1, reason="x")
    assert e.value.status == 404
    assert {c["status"] for c in await eval_cases.list_cases(ORG_A)} == {"draft", "proposed", "retired"}
    assert [c["status"] for c in await eval_cases.list_cases(ORG_A, status="retired")] == ["retired"]
    assert len(await eval_cases.list_cases(ORG_A, mode_id=mode)) == 3 and await eval_cases.list_cases(ORG_A, kind="regression") == []
    assert await eval_cases.list_cases(ORG_B) == []
    got = await eval_cases.get_case(ORG_A, str(case["id"]))
    assert got["case"]["id"] == str(case["id"]) and got["runs"] == []
    assert got["twin"] == {"case_key": f"{case['case_key']}.v1", "expected_answer": "There were 786 work orders in July 2026.",
                           "evaluation": "objective"}
    with pytest.raises(WatchError) as e:
        await eval_cases.get_case(ORG_B, str(case["id"]))                              # another company's case: not found
    assert e.value.status == 404


# ---- baseline, release check, report: through the golden runner's rows -----------------------------------------------

async def _golden_run(set_id: str, *, purpose: str, k: int, started_at: datetime | None = None, params=None,
                      with_job: bool = True) -> tuple[str, str | None]:
    """A golden run row (and its job) as runner.start_run leaves them. Returns (run id, job id)."""
    rid = str(uuid.uuid4())
    job_id = None
    async with db.lab_engine().begin() as conn:
        if with_job:
            job = await jobs.create(kind="golden_run", scope_key=f"golden_run:{ORG_A}:{rid}", organization_id=ORG_A,
                                    params={"run_id": rid}, usd_cap=5, actor_id=ACTOR, actor_email=EMAIL)
            job_id = job["id"]
        await conn.execute(text(
            "INSERT INTO tracelab.golden_runs (id, organization_id, set_id, label, purpose, k, status, job_id, started_by, "
            "started_at, params) VALUES (CAST(:id AS uuid), CAST(:org AS uuid), CAST(:s AS uuid), :label, :p, :k, 'running', "
            "CAST(:job AS uuid), 'u1', coalesce(CAST(:at AS timestamptz), now()), CAST(:params AS jsonb))"),
            {"id": rid, "org": ORG_A, "s": set_id, "label": "run-" + rid[:8], "p": purpose, "k": k, "job": job_id,
             "at": started_at, "params": json.dumps(params or {})})
    return rid, job_id


async def _answer(run_id: str, twin: dict, rep: int, answer: str, *, outcome: str = "pass", msg_idx: int = 0,
                  graded: bool = True) -> str:
    """One message row of one (case, rep) as runner._store_item writes it (the final row carries the outcome and
    graded_at, an earlier one stays pending), and the evidence of its answer. Returns the turn id."""
    tid = f"turn-gold-{run_id[:6]}-{twin['case_key'].replace('.', '')}-{rep}-{msg_idx}"
    async with db.lab_engine().begin() as conn:
        await seed_result(conn, ORG_A, run_id, str(twin["id"]), twin["case_key"], rep=rep, msg_idx=msg_idx,
                          outcome=outcome, turn_id=tid, graded_at=datetime.now(UTC) if graded else None)
        await conn.execute(text(
            "INSERT INTO tracelab.evidence_turns (turn_id, organization_id, turn, reason) VALUES "
            "(:t, CAST(:o AS uuid), CAST(:j AS jsonb), 'golden') ON CONFLICT DO NOTHING"),
            {"t": tid, "o": ORG_A, "j": json.dumps({
                "turn_id": tid, "organization_id": ORG_A, "session_id": f"golden-{run_id[:8]}-r{rep}", "question": QUESTION,
                "answer": answer, "started_at": datetime.now(UTC).isoformat(), "ok": True, "route": "single:wo_engine",
                "tool_calls": 2})})
    return tid


async def _error_row(run_id: str, twin: dict, rep: int) -> None:
    """The final row of a rep the service could not answer three times: outcome error, no turn, never graded."""
    async with db.lab_engine().begin() as conn:
        await conn.execute(text(
            "INSERT INTO tracelab.golden_results (run_id, organization_id, case_id, case_key, case_version, rep, msg_idx, "
            "session_id, turn_id, question, answer, outcome, error) VALUES (CAST(:r AS uuid), CAST(:o AS uuid), "
            "CAST(:c AS uuid), :k, '000000000000', :rep, 0, :sid, NULL, 'q', NULL, 'error', 'the service failed')"),
            {"r": run_id, "o": ORG_A, "c": str(twin["id"]), "k": twin["case_key"], "rep": rep,
             "sid": f"golden-{run_id[:8]}-r{rep}"})


async def _twin_of(case: dict) -> dict:
    return (await _rows("SELECT id, case_key FROM tracelab.golden_cases WHERE id = CAST(:i AS uuid)", i=case["golden_case_id"]))[0]


def _fake_runner(monkeypatch, *, params=None):
    """START_GOLDEN_RUN: the run row and job runner.start_run would leave, nothing asked."""
    made = []

    async def start(*, ctx, set_id, case_keys, k, purpose, usd_cap, note):
        rid, job_id = await _golden_run(set_id, purpose=purpose, k=k, params=params,
                                        started_at=datetime.now(UTC) + timedelta(minutes=len(made)))
        made.append({"id": rid, "job_id": job_id, "case_keys": list(case_keys), "k": k, "purpose": purpose})
        return {"id": rid, "label": "run-" + rid[:8], "status": "running"}

    monkeypatch.setattr(eval_cases, "START_GOLDEN_RUN", start)
    return made


async def test_baseline_classifies_cases_then_a_release_check_decides_and_reports(chat, monkeypatch):
    await _no_judges(monkeypatch)
    made = _fake_runner(monkeypatch, params={"git_sha": "f5-1a2b3c4", "chat_model": "gpt-5.6-terra",
                                             "grader_model": "claude-sonnet-5", "flags": {"ORCHESTRATOR_LLM_ROUTING": "on"}})
    async with db.lab_engine().begin() as conn:
        seeded = await seed_set(conn, ORG_A, cases=(("1", ()), ("2", ())))
    c1 = await _case_from_golden(set_case=seeded, key="1")
    c2 = await _case_from_golden(set_case=seeded, key="2")
    t1, t2 = await _twin_of(c1), await _twin_of(c2)

    out = await eval_cases.start_baseline(CTX, c1["id"])                              # 5 runs on the twin
    run1 = made[0]
    assert (run1["k"], run1["purpose"], run1["case_keys"]) == (5, "baseline", [t1["case_key"]])
    got = await eval_cases.get_case(ORG_A, c1["id"])
    assert got["case"]["status"] == "baselining" and got["case"]["baseline_run_id"] == out["golden_run"]["id"]
    with pytest.raises(WatchError) as e:                                              # its run is still open
        await eval_cases.start_baseline(CTX, c1["id"])
    assert e.value.reason == "baseline_running"
    await jobs.finish(run1["job_id"], "done")
    for rep in range(1, 6):
        await _answer(run1["id"], t1, rep, "There were 786 work orders in July 2026.")
    read = await eval_cases.eval_run(ORG_A, run1["id"])                               # reading classifies (free)
    (cr,) = read["case_runs"]
    assert [r["status"] for r in cr["reps"]] == ["passed"] * 5 and cr["classification"] == {"kind": "regression", "baseline_pass_rate": None}
    row = (await _rows("SELECT status, kind, baseline_pass_rate FROM tracelab.eval_cases WHERE id = CAST(:i AS uuid)", i=c1["id"]))[0]
    assert (row["status"], row["kind"], row["baseline_pass_rate"]) == ("active", "regression", None)
    stored = (await _rows("SELECT * FROM tracelab.eval_case_runs WHERE eval_case_id = CAST(:i AS uuid)", i=c1["id"]))[0]
    assert (stored["n"], stored["passes"], stored["decision"], stored["purpose"]) == (5, 5, "pass", "baseline")
    assert float(stored["pass_hat_k"]) == 1.0 and stored["reason"].startswith("baseline: a regression case")
    assert read["run"]["purpose"] == "baseline"

    await eval_cases.start_baseline(CTX, c2["id"])                                    # 3 of 5: a capability case
    run2 = made[1]
    await jobs.finish(run2["job_id"], "done")
    for rep, ok in zip(range(1, 6), (True, False, True, False, True)):
        await _answer(run2["id"], t2, rep, "There were 786 work orders." if ok else "There were 758 work orders.")
    await eval_cases.eval_run(ORG_A, run2["id"])
    row = (await _rows("SELECT status, kind, baseline_pass_rate FROM tracelab.eval_cases WHERE id = CAST(:i AS uuid)", i=c2["id"]))[0]
    assert (row["status"], row["kind"], float(row["baseline_pass_rate"])) == ("active", "capability", 0.6)
    with pytest.raises(WatchError) as e:
        await eval_cases.start_baseline(CTX, c2["id"])
    assert e.value.reason == "already_classified"

    # release check 1: everything passes
    rel = await eval_cases.start_release_check(CTX, k=2)
    assert rel["cases"] == {"regression": 1, "capability": 1}
    r1 = made[2]
    assert r1["purpose"] == "release_check" and sorted(r1["case_keys"]) == sorted([t1["case_key"], t2["case_key"]])
    await jobs.finish(r1["job_id"], "done")
    for rep in (1, 2):
        await _answer(r1["id"], t1, rep, "There were 786 work orders.")
        await _answer(r1["id"], t2, rep, "There were 786 work orders." if rep == 1 else "There were 758 work orders.")
    report1 = await eval_cases.release_report(ORG_A, r1["id"])
    assert report1["decision"] == "pass" and report1["reason"] == "all 1 regression cases passed 2 of 2 runs"
    assert report1["changes_since_last"] is None and report1["runs_without_verdict"] == []
    assert [c["case_key"] for c in report1["capability"]] == [c2["case_key"]]
    cap = report1["capability"][0]
    assert (cap["passes"], cap["n"], cap["baseline_pass_rate"], cap["decision"], cap["pass_hat_5"]) == (1, 2, 0.6, "pass", None)
    assert report1["fingerprint"]["image"] == "f5-1a2b3c4" and report1["fingerprint"]["openai_model"] == "gpt-5.6-terra"
    assert report1["fingerprint"]["flags"] == {"ORCHESTRATOR_LLM_ROUTING": "on"}

    # release check 2: the regression case fails one run
    await eval_cases.start_release_check(CTX, k=2, include_capability=False)
    r2 = made[3]
    assert r2["case_keys"] == [t1["case_key"]]
    await jobs.finish(r2["job_id"], "done")
    await _answer(r2["id"], t1, 1, "There were 786 work orders.")
    await _answer(r2["id"], t1, 2, "I could not find that.")
    report2 = await eval_cases.release_report(ORG_A, r2["id"])
    assert report2["decision"] == "block" and report2["reason"] == "1 of 1 regression cases failed at least one run"
    assert report2["changes_since_last"] == [{"case_key": c1["case_key"], "before": "pass", "after": "block"}]
    assert report2["regression"][0]["passes"] == 1 and len(report2["regression"][0]["turn_ids"]) == 2
    md = eval_cases.render_release_md(report2, company="Northbridge Estates Ltd")
    assert "**Decision: BLOCK**" in md and f"{c1['case_key']}: pass → block" in md
    assert "Build: f5-1a2b3c4" in md and "Chat model: gpt-5.6-terra" in md
    for internal in ("svc-", "plenum_", "deepagents", "judge:", "code:", "@v1", "golden:"):
        assert internal not in md, internal

    listed = await eval_cases.list_release_checks(ORG_A)
    assert [(c["golden_run_id"], c["decision"]) for c in listed] == [(r2["id"], "block"), (r1["id"], "pass")]
    assert await eval_cases.list_release_checks(ORG_B) == []
    with pytest.raises(WatchError) as e:
        await eval_cases.release_report(ORG_B, r1["id"])                               # another company's run
    assert e.value.status == 404
    with pytest.raises(WatchError) as e:
        await eval_cases.release_report(ORG_A, run1["id"])                             # a baseline is not a release check
    assert e.value.status == 404
    await eval_cases.eval_run(ORG_A, r1["id"])                                         # reading an older run again...
    latest = (await eval_cases.get_case(ORG_A, c1["id"]))["case"]["latest_run"]
    assert latest["golden_run_id"] == r2["id"] and latest["decision"] == "block"      # ...never makes it the latest one

    # a third check is read against the check right before it, not against the first one
    await eval_cases.start_release_check(CTX, k=2, include_capability=False)
    r3 = made[4]
    await jobs.finish(r3["job_id"], "done")
    await _answer(r3["id"], t1, 1, "There were 786 work orders.")
    await _answer(r3["id"], t1, 2, "I could not find that.")
    report3 = await eval_cases.release_report(ORG_A, r3["id"])
    assert report3["decision"] == "block" and report3["changes_since_last"] == []

    # only the current, classified versions of active cases run: not a retired one, nor one an edit replaced
    c2_now = (await eval_cases.get_case(ORG_A, c2["id"]))["case"]
    retired = await eval_cases.retire_case(ORG_A, c2["id"], base=c2_now["rev"], reason="no longer wanted")
    assert retired["status"] == "retired"
    assert [c["case_key"] for c in await eval_cases._active_cases(ORG_A)] == [c1["case_key"]]
    c1_now = (await eval_cases.get_case(ORG_A, c1["id"]))["case"]
    await eval_cases.edit_case(ORG_A, c1["id"], base=c1_now["rev"], input_override={"question": "How many in July?"},
                               actor_id=ACTOR, actor_email=EMAIL)
    assert await eval_cases._active_cases(ORG_A) == []                              # v1 is superseded, v2 has no baseline yet
    with pytest.raises(WatchError) as e:
        await eval_cases.start_release_check(CTX, k=2)
    assert e.value.reason == "no_active_cases"


async def test_a_baseline_and_a_release_read_again_after_the_judge_changed_keep_the_judges_that_decided_them(chat, monkeypatch):
    """The graders stored with a case run are the judge versions that made its verdicts. Reading a baseline or a
    release check again after the judge was replaced or retired must not rewrite them: judge_changed_since_baseline
    would read False exactly when it should read True, and the report would say no judge made a verdict."""
    made = _fake_runner(monkeypatch)
    async with db.lab_engine().begin() as conn:
        mode = await fx.add_mode(conn, "wrong_period", status="confirmed")
    live = []

    def judge(version: int) -> dict:                                  # a code judge: free, no model, no prediction cache
        return {"judge_id": f"judge-{version}", "mode_id": mode, "mode_name": "wrong_period", "judge_key": f"wrong_period-v{version}",
                "kind": "code", "model": None, "prompt_text": None, "prompt_hash": f"judge-v{version}-hash", "check_key": "no_answer",
                "check_version": 1}

    async def in_use(mode_ids=None):
        return [j for j in live if not mode_ids or j["mode_id"] in {str(m) for m in mode_ids}]

    def recorded(version: int) -> dict:
        return {"mode_id": mode, "judge_id": f"judge-{version}", "judge_key": f"wrong_period-v{version}",
                "prompt_hash": f"judge-v{version}-hash"}

    async def stored_graders(run_id: str) -> dict:
        raw = await _scalar("SELECT graders FROM tracelab.eval_case_runs WHERE golden_run_id = CAST(:r AS uuid)", r=run_id)
        return raw if isinstance(raw, dict) else json.loads(raw)

    monkeypatch.setattr(eval_cases, "judges_in_use", in_use)
    live[:] = [judge(1)]
    case = await _case_from_golden(expected={"judges": [{"mode_id": mode}], "golden_expected": False})
    twin = await _twin_of(case)
    await eval_cases.start_baseline(CTX, case["id"])
    base = made[0]
    await jobs.finish(base["job_id"], "done")
    for rep in range(1, 6):
        await _answer(base["id"], twin, rep, "There were 786 work orders in July 2026.")
    read = await eval_cases.eval_run(ORG_A, base["id"])                                # graded by version 1: a regression case
    assert [r["status"] for r in read["case_runs"][0]["reps"]] == ["passed"] * 5
    assert read["case_runs"][0]["classification"]["kind"] == "regression"
    assert (await stored_graders(base["id"]))["judges"] == [recorded(1)]

    live[:] = [judge(2)]                                                               # the judge is replaced by version 2
    await eval_cases.start_release_check(CTX, k=2)
    rel = made[1]
    await jobs.finish(rel["job_id"], "done")
    for rep in (1, 2):
        await _answer(rel["id"], twin, rep, "There were 786 work orders.")
    report = await eval_cases.release_report(ORG_A, rel["id"])
    assert report["regression"][0]["judge_changed_since_baseline"] is True            # version 2 now, version 1 at the baseline
    assert [(j["judge_key"], j["prompt_hash"]) for j in report["graders"]["judges"]] == [("wrong_period-v2", "judge-v2-hash")]
    assert (await stored_graders(rel["id"]))["judges"] == [recorded(2)]

    for now in ([judge(3)], [], [judge(1)]):                                           # replaced again, retired, put back
        live[:] = now
        await eval_cases.eval_run(ORG_A, base["id"])                                   # the baseline is read again...
        again = await eval_cases.release_report(ORG_A, rel["id"])                      # ...and so is the release check
        assert (await stored_graders(base["id"]))["judges"] == [recorded(1)]
        assert (await stored_graders(rel["id"]))["judges"] == [recorded(2)]
        assert again["regression"][0]["judge_changed_since_baseline"] is True
        assert again["graders"] == report["graders"]


async def test_repeat_n_summarises_the_failure_rate_of_one_case(chat, monkeypatch):
    await _no_judges(monkeypatch)
    made = _fake_runner(monkeypatch)
    case = await _case_from_golden()
    twin = await _twin_of(case)
    out = await eval_cases.start_repeat(CTX, case["id"], n=4)
    run = made[0]
    assert (run["k"], run["purpose"]) == (4, "repeat") and out["case"]["id"] == case["id"]
    for rep, answer in zip(range(1, 5), ("There were 786.", "There were 758.", "There were 758.", "There were 786.")):
        await _answer(run["id"], twin, rep, answer)
    read = await eval_cases.eval_run(ORG_A, run["id"])
    (cr,) = read["case_runs"]
    assert cr["purpose"] == "repeat" and [r["status"] for r in cr["reps"]] == ["passed", "failed", "failed", "passed"]
    assert cr["summary"]["n"] == 4 and cr["summary"]["failure_rate"] == 0.5 and cr["summary"]["excluded"] == 0
    assert cr["summary"]["mode_counts"] == {"check:must_contain": 2}
    assert cr["decision"] == "incomplete"                                              # an unclassified case decides nothing
    stored = (await _rows("SELECT summary FROM tracelab.eval_case_runs WHERE eval_case_id = CAST(:i AS uuid)", i=case["id"]))[0]
    summary = stored["summary"] if isinstance(stored["summary"], dict) else json.loads(stored["summary"])
    assert summary["failure_rate"] == 0.5 and summary["guidance"] == eval_cases.REPLAY_GUIDANCE
    # a rep the runner has not stored yet reads as missing, and a failed one stays failed when read again
    again = await eval_cases.eval_run(ORG_A, run["id"])
    assert [r["status"] for r in again["case_runs"][0]["reps"]] == ["passed", "failed", "failed", "passed"]


async def test_a_rep_the_runner_never_stored_is_missing_and_one_that_errored_is_degraded(chat, monkeypatch):
    await _no_judges(monkeypatch)
    made = _fake_runner(monkeypatch)
    case = await _case_from_golden(expected={"checks": [{"check": "must_contain", "text": "786"}], "golden_expected": True})
    twin = await _twin_of(case)
    await eval_cases.start_repeat(CTX, case["id"], n=3)
    run = made[0]
    await _answer(run["id"], twin, 1, "There were 786.", outcome="unsure")           # the golden grade needs a person
    await _error_row(run["id"], twin, 2)                                               # rep 2: the service failed 3 times
    read = await eval_cases.eval_run(ORG_A, run["id"])
    reps = read["case_runs"][0]["reps"]
    assert [r["status"] for r in reps] == ["pending", "degraded", "missing"]
    assert reps[1]["degraded_reason"] == "the service failed three times in a row"
    stored = (await _rows("SELECT n, passes, degraded, pending, decision FROM tracelab.eval_case_runs WHERE eval_case_id = CAST(:i AS uuid)",
                          i=case["id"]))[0]
    assert (stored["n"], stored["passes"], stored["degraded"], stored["pending"]) == (0, 0, 1, 2)   # pending counts the missing too


async def test_a_run_of_another_company_is_never_read_or_graded(chat, monkeypatch):
    await _no_judges(monkeypatch)
    made = _fake_runner(monkeypatch)
    case = await _case_from_golden()
    await eval_cases.start_repeat(CTX, case["id"], n=2)
    with pytest.raises(WatchError) as e:
        await eval_cases.eval_run(ORG_B, made[0]["id"])
    assert e.value.status == 404
    with pytest.raises(WatchError) as e:
        await eval_cases.start_grading(ORG_B, made[0]["id"], usd_cap=None, actor_id=ACTOR, actor_email=EMAIL)
    assert e.value.status == 404
    for unknown in (str(uuid.uuid4()), "not-a-uuid"):
        with pytest.raises(WatchError) as e:
            await eval_cases.eval_run(ORG_A, unknown)
        assert e.value.status == 404


async def test_grade_now_is_a_golden_grade_job_the_golden_runners_worker_runs_with_the_eval_handler(chat, monkeypatch):
    await _no_judges(monkeypatch)
    made = _fake_runner(monkeypatch)
    started = []

    async def not_yet(job_id):                                                        # the test runs the worker itself
        started.append(job_id)
        return True

    monkeypatch.setattr(runner, "start_grade_job", not_yet)
    case = await _case_from_golden()
    twin = await _twin_of(case)
    await eval_cases.start_repeat(CTX, case["id"], n=2)
    run = made[0]
    await jobs.finish(run["job_id"], "done")
    for rep in (1, 2):
        await _answer(run["id"], twin, rep, "There were 786 work orders.")
    out = await eval_cases.start_grading(ORG_A, run["id"], usd_cap=0.5, actor_id=ACTOR, actor_email=EMAIL)
    job_id = out["job"]["id"]
    assert started == [job_id]
    job = (await _rows("SELECT * FROM tracelab.jobs WHERE id = CAST(:i AS uuid)", i=job_id))[0]
    params = job["params"] if isinstance(job["params"], dict) else json.loads(job["params"])
    assert job["kind"] == "golden_grade" and job["scope_key"] == f"golden_grade:eval:{run['id']}" and job["status"] == "queued"
    assert float(job["usd_cap"]) == 0.5 and str(job["organization_id"]) == ORG_A
    assert params == {"handler": "eval_cases", "golden_run_id": run["id"], "organization_id": ORG_A}
    with pytest.raises(WatchError) as e:                                              # one grading job at a time per run
        await eval_cases.start_grading(ORG_A, run["id"], usd_cap=None, actor_id=ACTOR, actor_email=EMAIL)
    assert (e.value.status, e.value.reason) == (409, "grading_running")
    assert await jobs.claim(job_id) is not None
    assert await runner._grade_worker(job_id) == "done"                              # the worker finds the handler by name
    job = (await _rows("SELECT status, error FROM tracelab.jobs WHERE id = CAST(:i AS uuid)", i=job_id))[0]
    assert (job["status"], job["error"]) == ("done", None)
    stored = (await _rows("SELECT n, passes FROM tracelab.eval_case_runs WHERE eval_case_id = CAST(:i AS uuid)", i=case["id"]))[0]
    assert (stored["n"], stored["passes"]) == (2, 2)
    # a finished job frees its scope: grading can be asked for again
    again = await eval_cases.start_grading(ORG_A, run["id"], usd_cap=None, actor_id=ACTOR, actor_email=EMAIL)
    assert again["job"]["id"] != job_id


async def test_nothing_in_eval_cases_writes_the_chats_database(chat, monkeypatch):
    """A whole flow (a case from a turn, a twin, a baseline start, a read) through the read-only main engine."""
    await _no_judges(monkeypatch)
    _fake_runner(monkeypatch)
    await add_turn(chat, 1, ORG_A, session="s-1", question=QUESTION)
    await _wrong(turn_id(1))
    seen = []
    engine = db.main_engine().sync_engine

    @event.listens_for(engine, "before_cursor_execute")
    def spy(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    try:
        case = await eval_cases.create_case(ORG_A, source={"type": "live_turn", "turn_id": turn_id(1)},
                                            expected_answer="There were 786.", actor_id=ACTOR, actor_email=EMAIL)
        await eval_cases.start_baseline(CTX, case["id"])
        await eval_cases.list_cases(ORG_A)
    finally:
        event.remove(engine, "before_cursor_execute", spy)
    assert seen and not [s for s in seen if WRITE.match(s)]
    assert not [s for s in seen if "tracelab." in s]                                    # no statement names both databases


async def test_a_golden_run_with_no_job_row_still_reads(chat, monkeypatch):
    """golden_runs.job_id may be empty (the seed helpers of other tests make such runs): the run still reads, from its own status."""
    await _no_judges(monkeypatch)
    case = await _case_from_golden()
    twin = await _twin_of(case)
    run_id, _ = await _golden_run(twin_set := str(await _scalar("SELECT set_id FROM tracelab.golden_cases WHERE id = CAST(:i AS uuid)",
                                                                  i=str(twin["id"]))), purpose="repeat", k=2, with_job=False)
    async with db.lab_engine().begin() as conn:
        await conn.execute(text("INSERT INTO tracelab.eval_case_runs (id, eval_case_id, golden_run_id, purpose, k) "
                                "VALUES (gen_random_uuid(), CAST(:c AS uuid), CAST(:r AS uuid), 'repeat', 2)"),
                           {"c": case["id"], "r": run_id})
    assert twin_set
    await _answer(run_id, twin, 1, "There were 786.")
    read = await eval_cases.eval_run(ORG_A, run_id)
    assert read["run"]["status"] == "running" and read["case_runs"][0]["reps"][0]["status"] == "passed"


async def test_the_golden_runner_reads_the_body_and_the_twin_this_module_makes(chat, monkeypatch):
    """runner.estimate runs the runner's own request checks (the set, the case keys, the screening, the price) without
    asking the chat anything: the twin of a case, named in the body _run_body builds, is a case it will run."""
    await _no_judges(monkeypatch)
    case = await _case_from_golden()
    twin = await _twin_of(case)
    set_id = str(await _scalar("SELECT set_id FROM tracelab.golden_cases WHERE id = CAST(:i AS uuid)", i=str(twin["id"])))
    body = eval_cases._run_body(set_id=set_id, case_keys=[twin["case_key"]], k=5, purpose="baseline", usd_cap=None, note="n")
    est = await runner.estimate(org=ORG_A, principal=SimpleNamespace(role="admin"), body=body)
    assert (est["set_id"], est["case_keys"], est["purpose"], est["skipped"], est["k"]) == (set_id, [twin["case_key"]], "baseline", [], 5)
    assert est["set"]["id"] == set_id and est["est_total_usd"] > 0
    other = eval_cases._run_body(set_id=set_id, case_keys=["e-ffffffff.v9"], k=5, purpose="baseline", usd_cap=None, note="n")
    with pytest.raises(runner.RunRefused) as e:
        await runner.estimate(org=ORG_A, principal=SimpleNamespace(role="admin"), body=other)
    assert e.value.reason == "unknown_cases"


async def test_the_final_row_of_a_case_with_follow_ups_is_the_one_that_counts(chat, monkeypatch):
    await _no_judges(monkeypatch)
    made = _fake_runner(monkeypatch)
    async with db.lab_engine().begin() as conn:
        seeded = await seed_set(conn, ORG_A, cases=(("1", ("And in August?",)),))
    case = await _case_from_golden(set_case=seeded, key="1")
    assert case["input"]["followups"] == ["And in August?"]
    twin = await _twin_of(case)
    await eval_cases.start_repeat(CTX, case["id"], n=2)
    run = made[0]
    await _answer(run["id"], twin, 1, "There were 758 in July.", outcome="pending", msg_idx=0, graded=False)
    final = await _answer(run["id"], twin, 1, "There were 786 in August.", msg_idx=1)
    await _answer(run["id"], twin, 2, "There were 786 in July.", outcome="pending", msg_idx=0, graded=False)
    await _answer(run["id"], twin, 2, "I could not find that.", outcome="fail", msg_idx=1)
    read = await eval_cases.eval_run(ORG_A, run["id"])
    reps = read["case_runs"][0]["reps"]
    assert [r["status"] for r in reps] == ["passed", "failed"]            # each rep is read from its last message only
    assert reps[0]["turn_id"] == final


async def test_a_case_that_changes_underneath_a_write_is_refused_and_leaves_nothing_behind(chat, monkeypatch):
    """The checks of edit, confirm and retire read the case first; the guarded UPDATEs are what stops two writers."""
    await _no_judges(monkeypatch)
    case = await _case_from_golden()
    stored = (await _rows("SELECT * FROM tracelab.eval_cases WHERE id = CAST(:i AS uuid)", i=case["id"]))[0]
    twins = "SELECT count(*) FROM tracelab.golden_cases WHERE case_key LIKE 'e-%'"
    before = await _scalar(twins)
    async with db.lab_engine().begin() as conn:                                # another writer got in after this one read
        await conn.execute(text("UPDATE tracelab.eval_cases SET rev = rev + 1 WHERE id = CAST(:i AS uuid)"), {"i": case["id"]})
    new = eval_cases.next_version(stored)
    twin = eval_cases._twin_from(new, expected_answer="x", evaluation="objective", values=[], source="s")
    with pytest.raises(WatchError) as e:
        await eval_cases._insert_case(new, twin, actor_id=ACTOR, actor_email=EMAIL, supersede=(case["id"], case["rev"]))
    assert e.value.reason == "stale_base"
    with pytest.raises(WatchError) as e:                                       # the twin is made first, then refused, then gone
        await eval_cases._with_twin(
            twin, actor_id=ACTOR, actor_email=EMAIL, params={"id": case["id"], "base": case["rev"]},
            update_sql="UPDATE tracelab.eval_cases SET golden_case_id = CAST(:g AS uuid) WHERE id = CAST(:id AS uuid) AND rev = :base")
    assert e.value.reason == "stale_base"
    with pytest.raises(WatchError) as e:
        await eval_cases.retire_case(ORG_A, case["id"], base=case["rev"], reason="x")
    assert e.value.reason == "stale_base"
    with pytest.raises(WatchError) as e:
        await eval_cases._mark_draft(case["id"], case["rev"])
    assert e.value.reason == "stale_base"
    assert await _scalar(twins) == before and await _scalar("SELECT case_count FROM tracelab.golden_sets WHERE kind = 'regression'") == before
    row = (await _rows("SELECT status, rev FROM tracelab.eval_cases WHERE case_key = :k", k=case["case_key"]))
    assert [(r["status"], r["rev"]) for r in row] == [("draft", case["rev"] + 1)]     # one row, untouched but for the other writer


async def test_only_a_clash_of_case_keys_is_case_exists(chat, monkeypatch):
    await _no_judges(monkeypatch)
    case = await _case_from_golden()
    same_key = eval_cases._new_case(ORG_A, case["case_key"], status="proposed", source_type="live_turn", inp={"question": "q?"},
                                    expected={"checks": [], "judges": [], "golden_expected": True}, source_turn_id="t-1")
    with pytest.raises(WatchError) as e:
        await eval_cases._insert_case(same_key, None, actor_id=ACTOR, actor_email=EMAIL)
    assert (e.value.status, e.value.reason) == (409, "case_exists")
    no_such_mode = eval_cases._new_case(ORG_A, "e-0000beef", status="proposed", source_type="live_turn", inp={"question": "q?"},
                                        expected={"checks": [], "judges": [], "golden_expected": True},
                                        mode_id=str(uuid.uuid4()), source_turn_id="t-2")
    with pytest.raises(IntegrityError):                                        # any other refusal is a bug to see, not a 409
        await eval_cases._insert_case(no_such_mode, None, actor_id=ACTOR, actor_email=EMAIL)


async def test_a_twin_flags_a_request_to_change_something_and_a_retired_case_frees_its_answer(chat, monkeypatch):
    await _no_judges(monkeypatch)
    async with db.lab_engine().begin() as conn:
        seeded = await seed_set(conn, ORG_A, cases=(("1", ()),))
    out = await eval_cases.create_case(ORG_A, source={"type": "golden_case", "golden_case_id": seeded["cases"]["1"]},
                                       input_override={"question": "Raise a work order for the boiler in Plant Room 2"},
                                       expected={"checks": [{"check": "must_contain", "text": "raised"}], "golden_expected": False},
                                       actor_id=ACTOR, actor_email=EMAIL)
    assert (await _twin_of(out))["case_key"] == f"{out['case_key']}.v1"
    flags = await _scalar("SELECT flags FROM tracelab.golden_cases WHERE id = CAST(:i AS uuid)", i=out["golden_case_id"])
    assert list(flags) == ["write_intent"]                                     # the runner will refuse the change, not ask it
    await add_turn(chat, 1, ORG_A, session="s-1", question=QUESTION)
    await _wrong(turn_id(1))
    body = dict(source={"type": "live_turn", "turn_id": turn_id(1)}, expected_answer="There were 786.", actor_id=ACTOR,
                actor_email=EMAIL)
    first = await eval_cases.create_case(ORG_A, **body)
    with pytest.raises(WatchError) as e:
        await eval_cases.create_case(ORG_A, **body)
    assert e.value.reason == "case_exists"
    await eval_cases.retire_case(ORG_A, first["id"], base=first["rev"], reason="not a real failure")
    second = await eval_cases.create_case(ORG_A, **body)                      # a retired case no longer holds the answer
    assert second["id"] != first["id"] and second["case_key"] != first["case_key"] and second["source_turn_id"] == turn_id(1)


async def test_a_classification_only_touches_the_case_that_is_baselining_on_that_run(chat, monkeypatch):
    await _no_judges(monkeypatch)
    case = await _case_from_golden()
    twin = await _twin_of(case)
    set_id = str(await _scalar("SELECT set_id FROM tracelab.golden_cases WHERE id = CAST(:i AS uuid)", i=str(twin["id"])))
    state = "SELECT status, kind, baseline_pass_rate FROM tracelab.eval_cases WHERE id = CAST(:i AS uuid)"
    capability = {"kind": "capability", "baseline_pass_rate": 0.2}
    await eval_cases._classify(case["id"], capability, str(uuid.uuid4()))             # a draft case is not baselining
    assert (await _rows(state, i=case["id"]))[0] == {"status": "draft", "kind": "unclassified", "baseline_pass_rate": None}
    run_id, _ = await _golden_run(set_id, purpose="baseline", k=5)
    await eval_cases._begin_baseline(case["id"], run_id)
    await eval_cases._classify(case["id"], capability, str(uuid.uuid4()))             # baselining, but on another run
    assert (await _rows(state, i=case["id"]))[0]["status"] == "baselining"
    await eval_cases._classify(case["id"], capability, run_id)
    row = (await _rows(state, i=case["id"]))[0]
    assert (row["status"], row["kind"], float(row["baseline_pass_rate"])) == ("active", "capability", 0.2)
    await eval_cases._classify(case["id"], {"kind": "regression", "baseline_pass_rate": None}, run_id)   # once only
    assert (await _rows(state, i=case["id"]))[0]["kind"] == "capability"


async def test_the_baseline_graders_are_those_of_the_baseline_run_only(chat, monkeypatch):
    await _no_judges(monkeypatch)
    case = await _case_from_golden()
    twin = await _twin_of(case)
    set_id = str(await _scalar("SELECT set_id FROM tracelab.golden_cases WHERE id = CAST(:i AS uuid)", i=str(twin["id"])))
    base_run, _ = await _golden_run(set_id, purpose="baseline", k=5)
    await eval_cases._begin_baseline(case["id"], base_run)
    later_run, _ = await _golden_run(set_id, purpose="release_check", k=2)
    await eval_cases._add_case_runs([{"case_id": case["id"], "run_id": later_run, "purpose": "release_check", "k": 2}])
    mode = str(uuid.uuid4())
    async with db.lab_engine().begin() as conn:
        for run_id, prompt_hash in ((base_run, "baseline0000"), (later_run, "release00000")):
            await conn.execute(text("UPDATE tracelab.eval_case_runs SET graders = CAST(:g AS jsonb) "
                                    "WHERE golden_run_id = CAST(:r AS uuid)"),
                               {"g": json.dumps({"judges": [{"mode_id": mode, "prompt_hash": prompt_hash}]}), "r": run_id})
    assert await eval_cases._baseline_graders([case["id"]]) == {case["id"]: {"judges": [{"mode_id": mode, "prompt_hash": "baseline0000"}]}}
    assert await eval_cases._baseline_graders([]) == {}
    assert await eval_cases._baseline_graders([str(uuid.uuid4())]) == {}
    # a baseline run that holds no row of this case says nothing about it, whatever other runs of the case hold
    other_run, _ = await _golden_run(set_id, purpose="baseline", k=5)
    async with db.lab_engine().begin() as conn:
        await conn.execute(text("UPDATE tracelab.eval_cases SET baseline_run_id = CAST(:r AS uuid) WHERE id = CAST(:i AS uuid)"),
                           {"r": other_run, "i": case["id"]})
    assert await eval_cases._baseline_graders([case["id"]]) == {}


async def test_a_confirm_that_loses_a_race_leaves_no_twin(chat, monkeypatch):
    async with db.lab_engine().begin() as conn:
        mode = await fx.add_mode(conn, "wrong_period", status="confirmed")
    await add_turn(chat, 1, ORG_A, session="s-1", question=QUESTION)
    await _wrong(turn_id(1))
    batch = {"id": await _monitor_batch(), "started_by": ACTOR, "started_by_email": EMAIL}
    (case_id,) = await eval_cases.propose_from_monitor(batch=batch, mode_id=mode, turn_ids=[turn_id(1)])
    seen = await eval_cases._case(ORG_A, case_id)                                     # what the confirming request read
    async with db.lab_engine().begin() as conn:                                       # another writer changed it meanwhile
        await conn.execute(text("UPDATE tracelab.eval_cases SET rev = rev + 1 WHERE id = CAST(:i AS uuid)"), {"i": case_id})

    async def stale(org, cid):
        return dict(seen)

    monkeypatch.setattr(eval_cases, "_case", stale)
    with pytest.raises(WatchError) as e:
        await eval_cases.confirm_case(ORG_A, case_id, base=seen["rev"], expected_answer="There were 786.", actor_id=ACTOR,
                                      actor_email=EMAIL)
    assert e.value.reason == "stale_base"
    assert await _scalar("SELECT count(*) FROM tracelab.golden_cases") == 0              # the twin made for it was rolled back
    assert await _scalar("SELECT status FROM tracelab.eval_cases WHERE id = CAST(:i AS uuid)", i=case_id) == "proposed"


async def test_the_monitor_proposes_cases_through_this_module_from_a_real_batch_row(chat, monkeypatch):
    """monitor._propose_cases hands propose_from_monitor the batch row as the lab database returns it (ids as UUID
    objects): the proposed case names its batch and its starter."""
    from trace_lab import monitor

    async with db.lab_engine().begin() as conn:
        mode = await fx.add_mode(conn, "wrong_period", status="confirmed")
    await add_turn(chat, 1, ORG_A, session="s-1", question=QUESTION)
    batch = await monitor._load_batch(await _monitor_batch())
    ids = await monitor._propose_cases(batch, mode, [turn_id(1)])
    assert len(ids) == 1
    row = (await _rows("SELECT * FROM tracelab.eval_cases WHERE id = CAST(:i AS uuid)", i=ids[0]))[0]
    assert row["status"] == "proposed" and row["source_type"] == "monitor" and str(row["source_batch_id"]) == str(batch["id"])
    assert str(row["created_by"]) == ACTOR and row["created_by_email"] == EMAIL and str(row["mode_id"]) == mode
    assert str(row["organization_id"]) == ORG_A and row["source_turn_id"] == turn_id(1)
