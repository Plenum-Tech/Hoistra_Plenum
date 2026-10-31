"""Monitoring on a scratch Postgres (tracelab_scratch): the window is read from the chat's tables through the read-only
main engine (chat answers of a company, never an upload, a golden run, an answer with no company, a view-as answer or an
answer outside the window), the verdicts, predictions, batches and job checkpoints live in the lab database, and no
statement names both (Golden Rule G0). Skips without TRACE_LAB_TEST_DB_URL.

The sign-in service's account table is not part of the chat's trace tables: a database without it still gives a window."""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import DBAPIError, IntegrityError

import trace_lab_m4_fixtures as fx
from tl_scratch import lab_scratch, main_scratch, requires_scratch  # noqa: F401
from trace_lab import db, jobs, judges, monitor
from trace_lab_m2_fixtures import NOW, add_thread, add_turn, truncate_chat, turn_id

pytestmark = requires_scratch

UTC = timezone.utc
ORG_A = fx.ORG_A
ORG_B = fx.ORG_B
HOME = str(uuid.uuid4())                  # the platform team's own company
ACTOR = fx.OWNER_ID
WINDOW = (NOW - timedelta(days=1), NOW + timedelta(hours=1))
WRITE = re.compile(r"^\s*(INSERT|UPDATE|DELETE|TRUNCATE|CREATE|ALTER|DROP|GRANT|COPY)\b", re.I)
EX3 = [{"turn_id": "turn-a", "role": "clear_fail", "critique": "Names AHU-09.", "verdict": "Fail"},
       {"turn_id": "turn-b", "role": "clear_pass", "critique": "Every code returned.", "verdict": "Pass"},
       {"turn_id": "turn-c", "role": "borderline", "critique": "Implied code.", "verdict": "Fail"}]
TRAIN3 = [("turn-a", "train", True), ("turn-b", "train", False), ("turn-c", "train", True)]
EVIDENCE = json.dumps({"text": "QUESTION\nq", "chars": 10, "sha": "x", "truncated": False})


@pytest.fixture
async def chat(main_scratch, lab_scratch):  # noqa: F811
    """The writable main engine, seeded; the lab engine is db.lab_engine(). Code under test reads the chat through
    db.main_engine() (read-only) only."""
    yield main_scratch
    await truncate_chat(main_scratch)


async def _users(engine, *rows):
    """The platform team's accounts: (user_id, own company) pairs in plenum_cafm.users, as the sign-in service keeps them."""
    async with engine.begin() as conn:
        await conn.execute(text("CREATE TABLE IF NOT EXISTS plenum_cafm.users "
                                "(id uuid PRIMARY KEY, organization_id uuid NOT NULL, platform_role varchar(20) NOT NULL)"))
        for uid, org in rows:
            await conn.execute(text("INSERT INTO plenum_cafm.users (id, organization_id, platform_role) "
                                    "VALUES (CAST(:u AS uuid), CAST(:o AS uuid), 'superadmin')"), {"u": uid, "o": org})


async def _put_turns(engine, specs: list[dict]) -> None:
    """Chat answers with one span each, all through ONE connection (the scratch engine opens a fresh connection for every
    call of the shared helpers, and a flaky connect would then fail a test for no reason of its own)."""
    async with engine.begin() as conn:
        for s in specs:
            await conn.execute(text(
                "INSERT INTO plenum_cafm.agent_trace_turns (turn_id, session_id, organization_id, user_id, email, question, "
                "answer, route, started_at, ok, feedback_rating) VALUES (:t, :s, CAST(:o AS uuid), CAST(:u AS uuid), "
                "'asker@example.com', :q, :a, 'single:wo_engine', :at, true, :fb)"),
                {"t": turn_id(s["n"]), "s": s.get("session") or f"sess-{s['n']}", "o": s["org"],
                 "u": s.get("user") or str(uuid.uuid4()), "q": s.get("question", "How many jobs were raised in August?"),
                 "a": s.get("answer", "In August 2026."), "at": NOW - timedelta(minutes=s.get("minutes_ago", 30)),
                 "fb": s.get("feedback")})
            await conn.execute(text(
                "INSERT INTO plenum_cafm.agent_trace_spans (id, turn_id, organization_id, seq, kind, name, agent, started_at, "
                "output, ok) VALUES (CAST(:id AS uuid), :t, CAST(:o AS uuid), 1, 'tool', 'answer_from_records', 'wo_engine', "
                ":at, CAST(:out AS jsonb), true)"),
                {"id": str(uuid.uuid4()), "t": turn_id(s["n"]), "o": s["org"], "at": NOW,
                 "out": json.dumps({"count": 758, "period": "2026-08"})})


async def _asked_by(engine, tid, user_id):
    async with engine.begin() as conn:
        await conn.execute(text("UPDATE plenum_cafm.agent_trace_turns SET user_id = CAST(:u AS uuid) WHERE turn_id = :t"),
                           {"u": user_id, "t": tid})


# ---- the window (chat tables, main engine) ---------------------------------------------------------------

async def test_the_window_is_only_the_chat_answers_of_a_company(chat):
    await add_turn(chat, 1, ORG_A, minutes_ago=50)                                           # a chat answer
    await add_turn(chat, 2, ORG_A, minutes_ago=49, question="Validate and ingest this document.")   # an upload
    await add_turn(chat, 3, ORG_A, minutes_ago=48, session="golden-run-1-case-a-1")           # a golden run
    await add_turn(chat, 4, None, minutes_ago=47)                                            # no company (R27)
    await add_turn(chat, 5, ORG_B, minutes_ago=46)                                           # another company
    await add_turn(chat, 6, ORG_A, minutes_ago=3 * 24 * 60)                                  # before the window
    await add_turn(chat, 7, ORG_A, minutes_ago=45, session="report-weekly-1")                # a report
    await add_turn(chat, 8, ORG_A, minutes_ago=44, question="Re-run with corrections (spelling): how many?")
    await add_turn(chat, 9, ORG_A, minutes_ago=-120)                                         # after the window end
    rows = await monitor.window_turns(*WINDOW, None)
    assert [r["turn_id"] for r in rows] == [turn_id(1), turn_id(5)]
    rows = await monitor.window_turns(*WINDOW, ORG_A)
    assert [r["turn_id"] for r in rows] == [turn_id(1)]
    row = rows[0]
    assert set(row) == {"turn_id", "session_id", "organization_id", "user_id", "started_at", "ok", "feedback_rating"}
    assert row["organization_id"] == ORG_A and isinstance(row["user_id"], str) and row["ok"] is True


async def test_the_window_includes_its_start_and_excludes_its_end(chat):
    await _put_turns(chat, [{"n": 1, "org": ORG_A, "minutes_ago": 11}, {"n": 2, "org": ORG_A, "minutes_ago": 10},
                            {"n": 3, "org": ORG_A, "minutes_ago": 7}, {"n": 4, "org": ORG_A, "minutes_ago": 5},
                            {"n": 5, "org": ORG_A, "minutes_ago": 4}])
    start, end = NOW - timedelta(minutes=10), NOW - timedelta(minutes=5)
    rows = await monitor.window_turns(start, end, ORG_A)
    assert [r["turn_id"] for r in rows] == [turn_id(2), turn_id(3)]        # a week is [Monday, next Monday)


async def test_a_superadmin_acting_for_another_company_is_left_out(chat):
    visiting, at_home, ordinary = (str(uuid.uuid4()) for _ in range(3))
    await _users(chat, (visiting, HOME), (at_home, ORG_A))
    for n in (1, 2, 3):
        await add_turn(chat, n, ORG_A, minutes_ago=50 - n)
    await _asked_by(chat, turn_id(1), visiting)                 # a superadmin of another company, viewing as ORG_A
    await _asked_by(chat, turn_id(2), at_home)                  # a superadmin asking inside their own company
    await _asked_by(chat, turn_id(3), ordinary)                 # an ordinary account
    rows = await monitor.window_turns(*WINDOW, ORG_A)
    assert [r["turn_id"] for r in rows] == [turn_id(2), turn_id(3)]


async def test_a_database_without_the_accounts_table_still_gives_a_window(chat):
    await add_turn(chat, 1, ORG_A, minutes_ago=30)
    assert [r["turn_id"] for r in await monitor.window_turns(*WINDOW, ORG_A)] == [turn_id(1)]


async def test_a_window_is_read_in_pages_ordered_by_start_time_then_turn_id(chat, monkeypatch):
    monkeypatch.setattr(monitor, "WINDOW_PAGE", 3)
    for n in range(1, 9):                                       # eight answers, all at one instant: the id breaks ties
        await add_turn(chat, n, ORG_A, minutes_ago=30)
    await add_turn(chat, 9, ORG_A, minutes_ago=60)              # earlier: first
    await add_turn(chat, 10, ORG_A, minutes_ago=10)             # later: last
    calls = []
    real = monitor._window_page

    async def spy(start, end, org, after, limit):
        calls.append(after)
        return await real(start, end, org, after, limit)

    monkeypatch.setattr(monitor, "_window_page", spy)
    rows = await monitor.window_turns(*WINDOW, ORG_A)
    assert [r["turn_id"] for r in rows] == [turn_id(9)] + [turn_id(n) for n in range(1, 9)] + [turn_id(10)]
    assert len(calls) == 4 and calls[0] is None and calls[1][1] == turn_id(2)        # 3 + 3 + 3 + 1


async def test_the_window_is_read_through_the_read_only_engine_and_writes_nothing(chat):
    await add_turn(chat, 1, ORG_A, minutes_ago=30)
    seen: list[str] = []
    sync_engine = db.main_engine().sync_engine

    def record(conn, cursor, statement, *args):
        seen.append(statement)

    event.listen(sync_engine, "before_cursor_execute", record)
    try:
        await monitor.window_turns(*WINDOW, None)
    finally:
        event.remove(sync_engine, "before_cursor_execute", record)
    assert any("SET TRANSACTION READ ONLY" in s for s in seen)
    assert not [s for s in seen if WRITE.match(s)], seen
    assert not [s for s in seen if "tracelab" in s]             # the chat's database never names the lab's tables
    with pytest.raises(DBAPIError, match="read-only"):          # and the engine itself refuses a write
        async with db.main_engine().begin() as conn:
            await conn.execute(text("UPDATE plenum_cafm.agent_trace_turns SET ok = false"))


# ---- verdicts, predictions, batches and checkpoints (lab engine) ------------------------------------------

async def _mode_and_split(conn, name: str = "wrong_period_sql") -> tuple[str, str]:
    mid = await fx.add_mode(conn, name, status="confirmed", agent="udr",
                            definition="Present when the answer quotes the wrong period.")
    sid = str(uuid.uuid4())
    await conn.execute(text(
        "INSERT INTO tracelab.judge_splits (id, mode_id, version, fractions, seed, min_per_class, min_labels, counts, "
        "labels_sha) VALUES (CAST(:id AS uuid), CAST(:m AS uuid), 1, CAST(:fr AS numeric(4,3)[]), 7, 10, 30, "
        "CAST('{}' AS jsonb), :sha)"), {"id": sid, "m": mid, "fr": [0.2, 0.4, 0.4], "sha": "0" * 64})
    for tid, split, present in TRAIN3:
        await conn.execute(text(
            "INSERT INTO tracelab.judge_split_members (split_id, turn_id, organization_id, conversation_key, split, "
            "label_present, label_source, evidence_snapshot) VALUES (CAST(:s AS uuid), :t, CAST(:o AS uuid), :ck, :sp, "
            ":p, 'human_structured_label', CAST(:ev AS jsonb))"),
            {"s": sid, "t": tid, "o": ORG_A, "ck": "turn:" + tid, "sp": split, "p": present, "ev": EVIDENCE})
    return mid, sid


async def _llm_judge(conn, mode_id: str, split_id: str) -> str:
    jid = str(uuid.uuid4())
    parts = {"fail_rule": "The answer quotes the wrong period.", "pass_rule": "Right period.", "examples": EX3}
    await conn.execute(text(
        "INSERT INTO tracelab.judges (id, mode_id, version, judge_key, kind, split_id, model, prompt_text, "
        "prompt_parts, prompt_hash) VALUES (CAST(:id AS uuid), CAST(:m AS uuid), 0, 'wrong_period_sql-v0', 'llm', "
        "CAST(:s AS uuid), 'claude-sonnet-5', 'prompt', CAST(:parts AS jsonb), 'hash00000000')"),
        {"id": jid, "m": mode_id, "s": split_id, "parts": json.dumps(parts)})
    return jid


async def _code_judge(conn, mode_id: str, split_id: str) -> str:
    jid = str(uuid.uuid4())
    await conn.execute(text(
        "INSERT INTO tracelab.judges (id, mode_id, version, judge_key, kind, split_id, check_key, check_version, "
        "prompt_hash) VALUES (CAST(:id AS uuid), CAST(:m AS uuid), 1, 'no_answer-v1', 'code', CAST(:s AS uuid), "
        "'no_answer', 1, 'code00000000')"), {"id": jid, "m": mode_id, "s": split_id})
    return jid


async def _lab_judge(lab, kind: str = "llm") -> tuple[str, str]:
    """(judge id, mode id) of one registered judge."""
    async with lab.begin() as conn:
        mid, sid = await _mode_and_split(conn)
        jid = await (_llm_judge if kind == "llm" else _code_judge)(conn, mid, sid)
    return jid, mid


async def _new_batch(org: str | None = None, *, finish_job: bool = False, **over) -> dict:
    """A queued batch and its job, as start_batch records them."""
    job = await jobs.create(kind="monitor_batch", scope_key=f"monitor_batch:{org or '*'}", organization_id=org,
                            params={}, usd_cap=Decimal("5"), usd_estimate=Decimal("1"), total=10, actor_id=ACTOR,
                            actor_email="owner@example.com")
    if finish_job:
        await jobs.finish(str(job["id"]), "done")
    row = {"id": str(uuid.uuid4()), "organization_id": org, "period_label": "2026-W41",
           "window_start": datetime(2026, 10, 5, tzinfo=UTC), "window_end": datetime(2026, 10, 12, tzinfo=UTC),
           "sample_rate": 0.25, "seed": 7, "risk_groups": ["failed"], "judges": [{"judge_id": "j", "mode_id": "m"}],
           "checks": monitor.live_checks(), "status": "queued", "job_id": str(job["id"]), "usd_cap": Decimal("5"),
           "started_by": ACTOR, "started_by_email": "owner@example.com", **over}
    await monitor._insert_batch(row)
    return row


def _verdict(row: dict, tid: str, status: str, flag: int | None, *, ek: str = "code:no_answer@v1", **kw) -> dict:
    return monitor.verdict_row(evaluator_key=ek, turn={"turn_id": tid, "organization_id": ORG_A,
                                                       "started_at": datetime(2026, 10, 6, 9, tzinfo=UTC)},
                               status=status, failure_present=flag, detail={"findings": []}, batch_id=row["id"], **kw)


async def _job(job_id: str) -> dict:
    return await jobs.get(job_id, organization_id=None, platform=True)


async def test_a_batch_round_trips_through_its_row(lab_scratch):
    row = await _new_batch(ORG_A)
    loaded = await monitor._load_batch(row["id"])
    assert loaded["organization_id"] == ORG_A or str(loaded["organization_id"]) == ORG_A
    assert loaded["status"] == "queued" and loaded["plan"] is None and loaded["results"] is None
    assert loaded["judges"] == [{"judge_id": "j", "mode_id": "m"}] and loaded["checks"] == monitor.live_checks()
    assert loaded["sample_rate"] == Decimal("0.2500") and loaded["risk_groups"] == ["failed"] and loaded["usd"] == 0
    await monitor._update_batch(row["id"], plan={"random": ["t1"], "risk_groups": {}, "to_judge": ["t1"]},
                                status="running")
    await monitor._update_batch(row["id"], results={"modes": [{"crossed": True}], "at": NOW}, usd=Decimal("0.5"),
                                status="done", finished=True)
    done = await monitor._load_batch(row["id"])
    assert done["status"] == "done" and done["finished_at"] is not None and done["usd"] == Decimal("0.500000")
    assert done["plan"]["to_judge"] == ["t1"] and done["results"]["modes"] == [{"crossed": True}]
    out = monitor.batch_public(done, with_plan=True)
    assert out["scope"] == "company" and out["crossed"] is True and out["plan"]["random"] == ["t1"]
    json.dumps(out)
    assert await monitor._load_batch("not-a-uuid") is None and await monitor._load_batch(str(uuid.uuid4())) is None
    with pytest.raises(ValueError):
        await monitor._update_batch(row["id"], organization_id=ORG_B)
    await monitor._update_batch(row["id"])                                         # nothing to change is not an error


async def test_one_batch_is_active_per_scope_and_start_batch_gives_a_losing_job_back(lab_scratch, monkeypatch):
    first = await _new_batch(None, finish_job=True)                                # queued, its job already ended
    preview = {"period_label": "2026-W41", "window": {"start": "2026-10-05T00:00:00+00:00", "end": "2026-10-12T00:00:00+00:00"},
               "n_window": 3, "judges": [], "checks": [], "usd_estimate": 0.0, "sample_rate": 0.25, "seed": 7,
               "risk_groups": ["failed"]}

    async def fake_preview(**kw):
        return dict(preview)

    started = []
    monkeypatch.setattr(monitor, "plan_preview", fake_preview)
    monkeypatch.setattr(monitor, "run_in_background", lambda batch_id: started.append(batch_id))
    with pytest.raises(monitor.WatchError) as e:
        await monitor.start_batch(organization_id=None, since=None, until=None, sample_rate=0.25, seed=7,
                                  risk_groups=None, mode_ids=None, usd_cap=5, actor_id=ACTOR, actor_email=None, now=NOW)
    assert e.value.status == 409 and e.value.reason == "batch_running" and started == []
    listed = await jobs.list_for(organization_id=None, kind="monitor_batch")
    assert sorted(j["status"] for j in listed) == ["done", "failed"]                # the new job freed its scope
    await monitor._update_batch(first["id"], status="done", finished=True)         # the first batch ends: a new one starts
    out = await monitor.start_batch(organization_id=None, since=None, until=None, sample_rate=0.25, seed=7,
                                    risk_groups=None, mode_ids=None, usd_cap=5, actor_id=ACTOR, actor_email=None,
                                    now=NOW)
    assert started == [out["batch"]["id"]] and out["job"]["status"] == "queued"
    saved = await monitor._load_batch(out["batch"]["id"])
    assert saved["status"] == "queued" and str(saved["job_id"]) == out["job"]["id"]
    job = await _job(out["job"]["id"])
    assert job["kind"] == "monitor_batch" and job["scope_key"] == "monitor_batch:*" and job["total"] == 3
    assert job["params"] == {"batch_id": out["batch"]["id"]} and Decimal(str(job["usd_cap"])) == Decimal("5.0000")


async def test_a_company_batch_and_the_platform_batch_are_active_side_by_side(lab_scratch):
    await _new_batch(None)
    await _new_batch(ORG_A)
    await _new_batch(ORG_B)
    with pytest.raises(jobs.JobConflict):                                           # the job scope says the same
        await _new_batch(ORG_A)


async def test_a_decided_verdict_never_changes_and_an_error_is_retried_in_place(lab_scratch):
    row = await _new_batch()
    job_id = row["job_id"]
    await monitor._save(job_id, verdicts=[_verdict(row, "turn-x", "error", None), _verdict(row, "turn-y", "decided", 0)],
                        done_delta=2)
    again = [_verdict(row, "turn-x", "decided", 1), _verdict(row, "turn-y", "decided", 1)]    # y is decided: untouched
    await monitor._save(job_id, verdicts=again, done_delta=2)
    got = await monitor._verdicts("code:no_answer@v1", ["turn-x", "turn-y", "turn-none"])
    assert set(got) == {"turn-x", "turn-y"}
    assert (got["turn-x"]["status"], got["turn-x"]["failure_present"]) == ("decided", 1)
    assert (got["turn-y"]["status"], got["turn-y"]["failure_present"]) == ("decided", 0)
    async with lab_scratch.connect() as conn:
        attempts = dict((await conn.execute(text(
            "SELECT turn_id, attempts FROM tracelab.monitor_verdicts"))).all())
    assert attempts == {"turn-x": 2, "turn-y": 1}
    assert (await _job(job_id))["done"] == 4


async def test_save_checkpoints_the_job_in_the_same_transaction_as_the_rows(lab_scratch):
    row = await _new_batch()
    job_id = row["job_id"]
    await jobs.claim(job_id)
    await jobs.reserve(job_id, Decimal("0.02"))
    await monitor._save(job_id, verdicts=[_verdict(row, "turn-x", "decided", 0)], usd=Decimal("0.015"),
                        released=Decimal("0.02"), done_delta=1, progress={"phase": "judging"})
    job = await _job(job_id)
    assert (job["done"], Decimal(str(job["usd_spent"])), Decimal(str(job["usd_reserved"]))) == (1, Decimal("0.015"), 0)
    assert job["progress"]["phase"] == "judging"
    bad = _verdict(row, "turn-z", "decided", 0, ek="judge:" + str(uuid.uuid4()), judge_id=str(uuid.uuid4()))   # no such judge
    with pytest.raises(IntegrityError):
        await monitor._save(job_id, verdicts=[bad], usd=Decimal("9"), done_delta=5)
    job = await _job(job_id)
    assert job["done"] == 1 and Decimal(str(job["usd_spent"])) == Decimal("0.015")          # nothing of the failed save stuck
    with pytest.raises(IntegrityError):                       # and a checkpoint the job refuses keeps the rows out as well
        await monitor._save(job_id, verdicts=[_verdict(row, "turn-w", "decided", 0)], usd=Decimal("-1"), done_delta=1)
    assert await monitor._verdicts("code:no_answer@v1", ["turn-w"]) == {}
    assert (await _job(job_id))["done"] == 1


async def test_a_judge_reply_is_cached_with_its_verdict_in_one_save(lab_scratch):
    jid, mid = await _lab_judge(lab_scratch)
    row = await _new_batch()
    reply = SimpleNamespace(status="decided", failure_present=1, critique="Quotes June.", usd=0.02, error=None)
    judge = {"id": jid, "prompt_hash": "hash00000000", "model": "claude-sonnet-5"}
    arg = monitor.prediction_args(judge=judge, turn_id="turn-p", organization_id=ORG_A, reply=reply,
                                  inp=SimpleNamespace(text="e", chars=100, truncated=False, sha="sha"))
    ek = f"judge:{jid}"
    await monitor._save(row["job_id"], verdicts=[_verdict(row, "turn-p", "decided", 1, ek=ek, judge_id=jid, mode_id=mid)],
                        prediction=arg, usd=Decimal("0.02"), done_delta=1)
    cached = await monitor._predictions("hash00000000", ["turn-p", "turn-q"])
    assert list(cached) == ["turn-p"] and cached["turn-p"]["failure_present"] == 1 and cached["turn-p"]["critique"] == "Quotes June."
    assert (await monitor._verdicts(ek, ["turn-p"]))["turn-p"]["failure_present"] == 1
    failed = SimpleNamespace(status="error", failure_present=None, critique="", usd=0.01, error="RateLimitError")
    bad = monitor.prediction_args(judge=judge, turn_id="turn-q", organization_id=ORG_A, reply=failed, inp={})
    await monitor._save(row["job_id"], verdicts=[_verdict(row, "turn-q", "error", None, ek=ek, judge_id=jid, mode_id=mid)],
                        prediction=bad, usd=Decimal("0.01"))
    assert list(await monitor._predictions("hash00000000", ["turn-p", "turn-q"])) == ["turn-p"]   # an error is not cached
    # A save that cannot write its verdict leaves its prediction unwritten too: one transaction, never half a result.
    again = monitor.prediction_args(judge=judge, turn_id="turn-r", organization_id=ORG_A, reply=reply, inp={})
    nobody = _verdict(row, "turn-r", "decided", 1, ek=f"judge:{uuid.uuid4()}", judge_id=str(uuid.uuid4()))   # no such judge
    with pytest.raises(IntegrityError):
        await monitor._save(row["job_id"], verdicts=[nobody], prediction=again, usd=Decimal("0.02"))
    assert list(await monitor._predictions("hash00000000", ["turn-r"])) == []


async def test_verdict_counts_group_by_check_status_and_flag(lab_scratch):
    row = await _new_batch()
    ek2 = "code:refusal@v1"
    await monitor._save(row["job_id"], verdicts=[
        _verdict(row, "t1", "decided", 1), _verdict(row, "t2", "decided", 0), _verdict(row, "t3", "decided", 0),
        _verdict(row, "t4", "undecidable", None), _verdict(row, "t1", "decided", 0, ek=ek2),
        _verdict(row, "t9", "decided", 1)])
    await monitor._save(row["job_id"], verdicts=[_verdict(row, "t1", "decided", 1, ek="code:refusal@v2")])   # not asked for
    counts = await monitor._verdict_counts(["code:no_answer@v1", ek2], ["t1", "t2", "t3", "t4"])
    got = {(c["evaluator_key"], c["status"], c["failure_present"]): int(c["n"]) for c in counts}
    assert got == {("code:no_answer@v1", "decided", 1): 1, ("code:no_answer@v1", "decided", 0): 2,
                   ("code:no_answer@v1", "undecidable", None): 1, (ek2, "decided", 0): 1}
    assert await monitor._verdict_counts([], ["t1"]) == [] and await monitor._verdict_counts([ek2], []) == []


async def test_review_batches_are_found_by_monitor_batch_and_failure_mode(lab_scratch):
    ids = {}
    async with lab_scratch.begin() as conn:
        for key, (batch, mode) in {"b1m1": ("mb-1", "m-1"), "b1m2": ("mb-1", "m-2"), "b2m1": ("mb-2", "m-1")}.items():
            ids[key] = str(uuid.uuid4())
            await conn.execute(text(
                "INSERT INTO tracelab.sample_batches (id, organization_id, plan_step, strategy, params, seed, window_since, "
                "window_until, requested, selected, pool_size, counts_for_prevalence, created_by) VALUES (CAST(:id AS uuid), "
                "CAST(:o AS uuid), 'extra', 'monitor_flagged', CAST(:p AS jsonb), 7, now() - interval '2 days', now(), 1, 1, "
                "1, false, CAST(:by AS uuid))"),
                {"id": ids[key], "o": ORG_A, "by": ACTOR, "p": json.dumps({"monitor_batch_id": batch, "mode_id": mode})})
        await conn.execute(text(                                                  # a drawn batch is never a monitoring one
            "INSERT INTO tracelab.sample_batches (id, organization_id, plan_step, strategy, params, seed, window_since, "
            "window_until, requested, selected, pool_size, counts_for_prevalence, created_by) VALUES (CAST(:id AS uuid), "
            "CAST(:o AS uuid), 'initial_uniform', 'uniform', CAST(:p AS jsonb), 7, now() - interval '2 days', now(), 1, 1, "
            "1, true, CAST(:by AS uuid))"),
            {"id": str(uuid.uuid4()), "o": ORG_A, "by": ACTOR, "p": json.dumps({"monitor_batch_id": "mb-1", "mode_id": "m-1"})})
    assert await monitor._review_batches_for("mb-1", "m-1") == [ids["b1m1"]]
    assert await monitor._review_batches_for("mb-1", "m-2") == [ids["b1m2"]]
    assert await monitor._review_batches_for("mb-2", "m-1") == [ids["b2m1"]]
    assert await monitor._review_batches_for("mb-3", "m-1") == []


async def test_a_code_check_is_validated_by_a_frozen_judge_in_use_with_a_finished_test_run(lab_scratch):
    jid, mid = await _lab_judge(lab_scratch, kind="code")
    assert await monitor._validated_check_keys() == set()
    async with lab_scratch.begin() as conn:
        await conn.execute(text(
            "UPDATE tracelab.judges SET status = 'frozen', stop_reason = 'Stable.', frozen_at = now(), "
            "frozen_by = CAST(:u AS uuid), frozen_by_email = 'owner@plenum.test', decision = 'use' "
            "WHERE id = CAST(:j AS uuid)"), {"j": jid, "u": ACTOR})
    assert await monitor._validated_check_keys() == set()                          # no test run yet
    async with lab_scratch.begin() as conn:
        await conn.execute(text("INSERT INTO tracelab.judge_runs (id, judge_id, split, status, items) VALUES "
                                "(CAST(:id AS uuid), CAST(:j AS uuid), 'test', 'done', CAST('[]' AS jsonb))"),
                           {"id": str(uuid.uuid4()), "j": jid})
    assert await monitor._validated_check_keys() == {"code:no_answer@v1"}


# ---- the threshold action on real tables -------------------------------------------------------------------

async def _flagged_batch(chat, lab, *, flagged: list[int], turns: dict[int, str | None]):
    """Answers seeded in the chat, one LLM judge, a monitoring batch whose plan names every answer and a flagged verdict
    for each of `flagged` (the others pass). Returns (batch row as loaded, snapshot, judge id)."""
    jid, mid = await _lab_judge(lab)
    await _put_turns(chat, [{"n": n, "org": org, "minutes_ago": 60 - n} for n, org in turns.items()])
    ek = f"judge:{jid}"
    snap = {"judge_id": jid, "judge_key": "wrong_period_sql-v0", "mode_id": mid, "mode_name": "wrong_period_sql",
            "kind": "llm", "evaluator_key": ek, "threshold": 0.15, "prompt_hash": "hash00000000"}
    row = await _new_batch(None, judges=[snap], window_start=NOW - timedelta(days=2), window_end=NOW + timedelta(hours=1),
                           period_label="2026-W42")
    ids = [turn_id(n) for n in turns]
    await monitor._update_batch(row["id"], plan={"random": ids, "risk_groups": {}, "to_judge": ids})
    verdicts = [monitor.verdict_row(evaluator_key=ek, turn={"turn_id": turn_id(n), "organization_id": org or ORG_A,
                                                            "started_at": NOW}, status="decided",
                                    failure_present=1 if n in flagged else 0, detail={}, batch_id=row["id"],
                                    judge_id=jid, mode_id=mid) for n, org in turns.items()]
    await monitor._save(row["job_id"], verdicts=verdicts)
    return await monitor._load_batch(row["id"]), snap, jid


async def test_turn_rows_name_the_company_and_session_of_each_chat_answer(chat):
    await add_turn(chat, 1, ORG_A, session="sess-1")
    await add_turn(chat, 2, ORG_B, session="sess-2")
    rows = await monitor._turn_rows([turn_id(1), turn_id(2), turn_id(3)])
    assert rows[turn_id(1)] == {"turn_id": turn_id(1), "organization_id": ORG_A, "session_id": "sess-1"}
    assert rows[turn_id(2)]["organization_id"] == ORG_B and turn_id(3) not in rows
    assert await monitor._turn_rows([]) == {}


async def test_flagged_answers_become_one_review_batch_per_company_once(chat, lab_scratch):
    batch, snap, jid = await _flagged_batch(chat, lab_scratch, flagged=[1, 2, 3, 5],
                                            turns={1: ORG_A, 2: ORG_A, 3: ORG_B, 4: ORG_B, 5: ORG_A})
    made = await monitor.open_review_batches(str(batch["id"]), snap["mode_id"], actor_id=ACTOR,
                                             actor_email="owner@example.com")
    assert len(made) == 2
    assert sorted(await monitor._review_batches_for(str(batch["id"]), snap["mode_id"])) == sorted(made)
    async with lab_scratch.connect() as conn:
        rows = (await conn.execute(text(
            "SELECT b.organization_id::text AS org, b.strategy, b.counts_for_prevalence, b.created_by::text AS by, "
            "b.params, b.plan_step, b.selected, (SELECT array_agg(i.turn_id ORDER BY i.position) FROM "
            "tracelab.sample_items i WHERE i.batch_id = b.id) AS turns, (SELECT min(i.reason) FROM "
            "tracelab.sample_items i WHERE i.batch_id = b.id) AS reason FROM tracelab.sample_batches b"))).mappings().all()
        snapped = {r[0] for r in (await conn.execute(text("SELECT turn_id FROM tracelab.evidence_turns"))).all()}
    by_org = {r["org"]: r for r in rows}
    assert set(by_org) == {ORG_A, ORG_B}
    assert by_org[ORG_A]["turns"] == [turn_id(1), turn_id(2), turn_id(5)] and by_org[ORG_B]["turns"] == [turn_id(3)]
    for r in rows:
        params = r["params"] if isinstance(r["params"], dict) else json.loads(r["params"])
        assert params == {"monitor_batch_id": str(batch["id"]), "mode_id": snap["mode_id"], "judge_id": jid}
        assert (r["strategy"], r["counts_for_prevalence"], r["by"], r["plan_step"]) == ("monitor_flagged", False, ACTOR, "extra")
        assert r["reason"] == "flagged by wrong_period_sql-v0 in 2026-W42: uniform sample"
    assert snapped == {turn_id(1), turn_id(2), turn_id(3), turn_id(5)}      # every flagged answer outlives the purge
    again = await monitor.open_review_batches(str(batch["id"]), snap["mode_id"], actor_id=ACTOR, actor_email=None)
    assert sorted(again) == sorted(made)
    async with lab_scratch.connect() as conn:
        assert (await conn.execute(text("SELECT count(*) FROM tracelab.sample_batches"))).scalar_one() == 2


async def test_a_company_whose_flagged_answers_are_already_in_a_batch_is_skipped(chat, lab_scratch):
    batch, snap, _ = await _flagged_batch(chat, lab_scratch, flagged=[1, 3], turns={1: ORG_A, 2: ORG_A, 3: ORG_B})
    async with lab_scratch.begin() as conn:                                  # ORG_B's answer 3 is in a review batch already
        await fx.add_batch_item(conn, turn_id(3), ORG_B)
    made = await monitor.open_review_batches(str(batch["id"]), snap["mode_id"], actor_id=ACTOR, actor_email=None)
    assert len(made) == 1
    async with lab_scratch.connect() as conn:
        orgs = [r[0] for r in (await conn.execute(text(
            "SELECT organization_id::text FROM tracelab.sample_batches WHERE id = CAST(:i AS uuid)"), {"i": made[0]})).all()]
    assert orgs == [ORG_A]


async def test_crossing_a_threshold_fills_the_review_batches_and_the_proposed_cases(chat, lab_scratch, monkeypatch):
    batch, snap, _ = await _flagged_batch(chat, lab_scratch, flagged=[1, 2], turns={1: ORG_A, 2: ORG_B, 3: ORG_A})
    proposed = []

    async def propose(b, mode_id, ids):
        proposed.append((mode_id, list(ids)))
        return ["case-1"]

    monkeypatch.setattr(monitor, "_propose_cases", propose)
    results = {"modes": [{"mode_id": snap["mode_id"], "crossed": True, "review_batch_ids": [], "proposed_case_ids": []}]}
    await monitor._act_on_crossings(batch, batch["plan"], results)
    assert len(results["modes"][0]["review_batch_ids"]) == 2 and results["modes"][0]["proposed_case_ids"] == ["case-1"]
    assert proposed == [(snap["mode_id"], [turn_id(1), turn_id(2)])]


# ---- a batch from start to finish on real tables (only the model call is replaced) ----------------------------

MARKER = "WRONG-PERIOD"


async def _judge_in_use(lab, *, threshold: float = 0.15) -> tuple[str, str]:
    """A frozen LLM judge with decision 'use' and a finished held-out test run that agreed with its labels."""
    jid, mid = await _lab_judge(lab)
    pairs = [{"turn_id": f"held-{i}", "label": 1 if i < 10 else 0, "failure_present": 1 if i < 10 else 0,
              "status": "decided"} for i in range(20)]
    async with lab.begin() as conn:
        await conn.execute(text(
            "UPDATE tracelab.judges SET status = 'frozen', stop_reason = 'Stable.', frozen_at = now(), "
            "frozen_by = CAST(:u AS uuid), frozen_by_email = 'owner@plenum.test', decision = 'use', "
            "monitor_threshold = :t WHERE id = CAST(:j AS uuid)"), {"j": jid, "u": ACTOR, "t": threshold})
        await conn.execute(text(
            "INSERT INTO tracelab.judge_runs (id, judge_id, split, status, items, results, n) VALUES "
            "(CAST(:id AS uuid), CAST(:j AS uuid), 'test', 'done', CAST('[]' AS jsonb), CAST(:r AS jsonb), 20)"),
            {"id": str(uuid.uuid4()), "j": jid, "r": json.dumps(pairs)})
    return jid, mid


async def _seed_week(chat, *, wrong: set[int]):
    """Twelve chat answers of ORG_A inside the window: those in `wrong` carry the marker the fake judge fails on, answer
    3 was rated down, and answers 1 and 2 are the same person asking the same question again in one conversation."""
    await add_thread(chat, "sess-ask", ORG_A)
    asker = str(uuid.uuid4())
    await _put_turns(chat, [
        {"n": n, "org": ORG_A, "minutes_ago": 60 - n, "session": "sess-ask" if n in (1, 2) else f"sess-{n}",
         "user": asker if n in (1, 2) else None, "answer": MARKER + " in July" if n in wrong else "In August 2026.",
         "feedback": "down" if n == 3 else None} for n in range(1, 13)])


@pytest.fixture
def judge_calls(monkeypatch):
    """judges.ASK replaced: fails exactly the answers that carry the marker. No model is ever called."""
    calls: list[str] = []

    async def ask(model, prompt_text, judge_text):
        calls.append(judge_text)
        fail = MARKER in judge_text
        return SimpleNamespace(failure_present=1 if fail else 0, critique="Quotes July." if fail else "Matches.",
                               status="decided", usd=0.01, error=None)

    real = monitor.stats.corrected_prevalence
    monkeypatch.setattr(judges, "ASK", ask)
    monkeypatch.setattr(monitor.stats, "corrected_prevalence",
                        lambda s, l, p, **kw: real(s, l, p, bootstrap_iterations=500))     # the same arithmetic, fast
    return calls


async def _start(monkeypatch, **over):
    started = []
    monkeypatch.setattr(monitor, "run_in_background", lambda batch_id: started.append(batch_id))
    kw = dict(organization_id=None, since=NOW - timedelta(days=2), until=NOW, sample_rate=0.5, seed=7,
              risk_groups=["thumbs_down", "re_ask"], mode_ids=None, usd_cap=1, actor_id=ACTOR,
              actor_email="owner@example.com")
    out = await monitor.start_batch(**{**kw, **over})
    assert started == [out["batch"]["id"]]
    return out


async def test_a_batch_runs_end_to_end_and_a_second_batch_over_the_same_answers_costs_nothing(
        chat, lab_scratch, monkeypatch, judge_calls):
    jid, mid = await _judge_in_use(lab_scratch)
    await _seed_week(chat, wrong=set(range(1, 9)))                          # most answers are wrong: the mode is crossed
    proposed = []

    async def propose(batch, mode_id, ids):
        proposed.append((mode_id, list(ids)))
        return ["case-1"]

    monkeypatch.setattr(monitor, "_propose_cases", propose)
    first = await _start(monkeypatch)
    assert first["batch"]["status"] == "queued" and first["batch"]["judges"][0]["judge_id"] == jid
    await monitor.work_batch(first["batch"]["id"])
    done = await monitor._load_batch(first["batch"]["id"])
    assert done["status"] == "done" and done["finished_at"] is not None
    plan, res = done["plan"], done["results"]
    assert plan["risk_groups"]["thumbs_down"] == [turn_id(3)] and plan["risk_groups"]["re_ask"] == [turn_id(1)]
    assert len(plan["random"]) == 6 and set(plan["to_judge"]) >= {turn_id(1), turn_id(3)} | set(plan["random"])
    assert len(judge_calls) == len(plan["to_judge"])
    mode = res["modes"][0]
    assert mode["n_sample"] == 6 and mode["crossed"] is True and mode["corrected"] == mode["raw"] > 0.15
    assert mode["flagged"] == sum(1 for t in plan["to_judge"] if int(t[-16:], 16) <= 8)
    assert res["n_window"] == 12 and res["n_random"] == 6 and res["code_checks"][0]["n"] == 12
    assert len(mode["review_batch_ids"]) == 1 and mode["proposed_case_ids"] == ["case-1"]
    assert proposed[0][0] == mid and set(proposed[0][1]) <= set(plan["random"]) and len(proposed[0][1]) <= 5
    job = await _job(str((await monitor._load_batch(first["batch"]["id"]))["job_id"]))
    assert job["status"] == "done" and job["done"] == 12 and Decimal(str(job["usd_spent"])) == Decimal("0.01") * len(judge_calls)
    assert Decimal(str(done["usd"])) == Decimal(str(job["usd_spent"])) and Decimal(str(job["usd_reserved"])) == 0
    async with lab_scratch.connect() as conn:
        kept = {r[0] for r in (await conn.execute(text("SELECT turn_id FROM tracelab.evidence_turns"))).all()}
        checks_rows = (await conn.execute(text(
            "SELECT count(*) FROM tracelab.monitor_verdicts WHERE evaluator_key LIKE 'code:%'"))).scalar_one()
        judge_rows = (await conn.execute(text(
            "SELECT count(*) FROM tracelab.monitor_verdicts WHERE evaluator_key = :ek"), {"ek": f"judge:{jid}"})).scalar_one()
    assert kept >= set(plan["to_judge"])                                    # every judged answer outlives the span purge
    assert checks_rows == 12 * len(monitor.live_checks()) and judge_rows == len(plan["to_judge"])

    calls_before = len(judge_calls)
    second = await _start(monkeypatch)
    await monitor.work_batch(second["batch"]["id"])
    again = await monitor._load_batch(second["batch"]["id"])
    assert again["status"] == "done" and len(judge_calls) == calls_before   # decided verdicts are reused: no new call
    job2 = await _job(str(again["job_id"]))
    assert Decimal(str(job2["usd_spent"])) == 0 and again["results"]["modes"][0]["n_sample"] == 6
    assert again["results"]["modes"][0]["review_batch_ids"] == []           # every flagged answer is in a batch already
    async with lab_scratch.connect() as conn:
        assert (await conn.execute(text("SELECT count(*) FROM tracelab.sample_batches"))).scalar_one() == 1


async def test_a_company_batch_reads_only_that_company_and_a_batch_with_no_judge_runs_the_checks(
        chat, lab_scratch, monkeypatch, judge_calls):
    await _seed_week(chat, wrong=set())
    await add_turn(chat, 50, ORG_B, minutes_ago=30)
    out = await _start(monkeypatch, organization_id=ORG_A, risk_groups=[])
    assert out["batch"]["judges"] == [] and out["batch"]["scope"] == "company"
    await monitor.work_batch(out["batch"]["id"])
    done = await monitor._load_batch(out["batch"]["id"])
    assert done["status"] == "done" and done["results"]["n_window"] == 12 and done["results"]["modes"] == []
    assert judge_calls == []
    async with lab_scratch.connect() as conn:
        orgs = {r[0] for r in (await conn.execute(text(
            "SELECT DISTINCT organization_id::text FROM tracelab.monitor_verdicts"))).all()}
    assert orgs == {ORG_A}


async def test_an_interrupted_batch_is_resumed_from_its_stored_state(chat, lab_scratch, monkeypatch, judge_calls):
    await _judge_in_use(lab_scratch)
    await _seed_week(chat, wrong={4, 5})
    out = await _start(monkeypatch, risk_groups=["thumbs_down"])
    batch_id = out["batch"]["id"]
    job_id = out["job"]["id"]
    await jobs.claim(job_id)                                                # a worker took it, then the service restarted
    await jobs.mark_interrupted(stale_after_s=-1)
    listed = await monitor._settle([await monitor._load_batch(batch_id)])
    assert listed[0]["status"] == "interrupted"
    started = []
    monkeypatch.setattr(monitor, "run_in_background", lambda bid: started.append(bid))
    resumed = await monitor.resume_batch(batch_id, org=None)
    assert resumed["batch"]["status"] == "queued" and started == [batch_id]
    assert (await _job(job_id))["status"] == "queued"
    await monitor.work_batch(batch_id)
    final = await monitor._load_batch(batch_id)
    assert final["status"] == "done" and (await _job(job_id))["status"] == "done"
    with pytest.raises(monitor.WatchError) as e:                            # a finished batch cannot be resumed
        await monitor.resume_batch(batch_id, org=None)
    assert e.value.reason == "not_interrupted"


async def test_the_plan_preview_counts_the_window_and_prices_the_calls_on_real_tables(chat, lab_scratch, monkeypatch):
    jid, mid = await _judge_in_use(lab_scratch)
    await _seed_week(chat, wrong=set())
    out = await monitor.plan_preview(organization_id=None, since=NOW - timedelta(days=2), until=NOW, sample_rate=0.5,
                                     risk_groups=["thumbs_down"])
    assert out["n_window"] == 12 and out["n_random"] == 6 and out["n_risk_only"] in (0, 1)
    assert out["judges"][0]["judge_id"] == jid and out["judges"][0]["mode_id"] == mid and out["unpriced"] == []
    per = monitor.call_usd("claude-sonnet-5", "prompt", monitor.EST_EVIDENCE_CHARS)
    assert out["usd_estimate"] == float(round(per * out["n_to_judge"], 4)) > 0
    capped = await monitor.plan_preview(organization_id=ORG_B, since=NOW - timedelta(days=2), until=NOW)
    assert capped["n_window"] == 0 and capped["usd_estimate"] == 0.0


# ---- reads: batches, one batch, chart, code-check trend, prevalence, automatic vs people (Phase 7 task 3) ---------

from golden_seed import seed_result, seed_run, seed_set  # noqa: E402
from trace_lab import checks, evidence, grader_validation  # noqa: E402

FIXED = datetime(2026, 10, 15, 12, tzinfo=UTC)          # a Thursday: 2026-W41 is Oct 5-12, W42 is Oct 12-19
W41 = datetime(2026, 10, 7, 10, tzinfo=UTC)
W42 = datetime(2026, 10, 14, 10, tzinfo=UTC)
LIVE_CHECK = next(c for c in checks.CHECKS.values() if c.scope == "live")


async def _finished_batch(lab, org, period, *, started, status="done", window=(None, None), results=None, **over):
    """A batch that ended (its job done), started at `started`: one queued batch per scope at a time, so each is
    finished before the next is made."""
    row = await _new_batch(org, finish_job=True, period_label=period, **{
        k: v for k, v in (("window_start", window[0]), ("window_end", window[1])) if v is not None}, **over)
    fields = {"status": status, "finished": True}
    if results is not None:
        fields["results"] = results
    await monitor._update_batch(row["id"], **fields)
    async with lab.begin() as conn:
        await conn.execute(text("UPDATE tracelab.monitor_batches SET started_at = :s WHERE id = CAST(:id AS uuid)"),
                           {"s": started, "id": row["id"]})
    return row


async def test_batches_are_listed_newest_first_for_one_scope_or_all_without_their_plans(lab_scratch):
    w40 = (datetime(2026, 9, 28, tzinfo=UTC), datetime(2026, 10, 5, tzinfo=UTC))
    w41 = (datetime(2026, 10, 5, tzinfo=UTC), datetime(2026, 10, 12, tzinfo=UTC))
    p1 = await _finished_batch(lab_scratch, None, "2026-W40", started=FIXED - timedelta(days=5), window=w40)
    a1 = await _finished_batch(lab_scratch, ORG_A, "2026-W40", started=FIXED - timedelta(days=4), window=w40)
    a2 = await _finished_batch(lab_scratch, ORG_A, "2026-W41", started=FIXED - timedelta(days=2), window=w41,
                               results={"modes": [{"crossed": True}]})
    b1 = await _finished_batch(lab_scratch, ORG_B, "2026-W41", started=FIXED - timedelta(days=1), window=w41,
                               status="failed")
    await monitor._update_batch(a2["id"], plan={"random": ["t1"], "risk_groups": {}, "to_judge": ["t1"]})
    ids = lambda rows: [str(r["id"]) for r in rows]                                  # noqa: E731
    everything = await monitor._batch_rows(None, 20, None)
    assert ids(everything) == [b1["id"], a2["id"], a1["id"], p1["id"]]
    assert ids(await monitor._batch_rows(ORG_A, 20, None)) == [a2["id"], a1["id"]]
    assert ids(await monitor._batch_rows(ORG_B, 20, None)) == [b1["id"]]
    assert ids(await monitor._batch_rows(None, 1, None)) == [b1["id"]]
    assert ids(await monitor._batch_rows(None, 20, FIXED - timedelta(days=2))) == [a1["id"], p1["id"]]   # older than a2
    assert len(await monitor._batch_rows(None, 0, None)) == 1                       # at least one, whatever is asked
    assert len(await monitor._batch_rows(None, 10 ** 6, None)) == 4                 # and a cap
    assert all("plan" not in r for r in everything)                                 # a plan names every sampled answer
    assert everything[1]["results"] == {"modes": [{"crossed": True}]}               # decoded
    assert ids(await monitor._scope_batches(None)) == [p1["id"]]                    # the Due marker's scope is exact
    assert ids(await monitor._scope_batches(ORG_A)) == [a2["id"], a1["id"]]
    assert all("plan" not in r for r in await monitor._scope_batches(ORG_A))

    company = await monitor.list_batches(org=ORG_A, now=FIXED)
    assert [b["id"] for b in company["batches"]] == [a2["id"], a1["id"]]
    assert company["due"] == {"period_label": "2026-W41", "is_due": False}          # A's W41 batch covers last week
    assert company["batches"][0]["crossed"] is True and company["batches"][0]["scope"] == "company"
    platform = await monitor.list_batches(org=None, now=FIXED)
    assert len(platform["batches"]) == 4 and {b["scope"] for b in platform["batches"]} == {"platform", "company"}
    assert platform["due"] == {"period_label": "2026-W41", "is_due": True}          # the platform's own batch is W40's
    other = await monitor.list_batches(org=ORG_B, now=FIXED)
    assert other["due"]["is_due"] is True                                           # B's only batch failed
    json.dumps([company, platform, other])


async def test_listing_batches_settles_one_whose_job_was_interrupted(lab_scratch):
    row = await _new_batch(ORG_A)
    await jobs.claim(row["job_id"])
    await jobs.mark_interrupted(stale_after_s=-1)
    out = await monitor.list_batches(org=ORG_A, now=FIXED)
    assert [b["status"] for b in out["batches"]] == ["interrupted"]
    assert (await monitor._load_batch(row["id"]))["status"] == "interrupted"        # and the row now says so


async def _seed_flags(chat, lab):
    """Judge verdicts on answers in the chat (1, 2), one the chat has purged but the lab kept (3), one named nowhere (5),
    another company's (4), and verdicts that are not flags (6: pass, 7: error)."""
    jid, mid = await _lab_judge(lab)
    await _put_turns(chat, [{"n": 1, "org": ORG_A, "question": "Q one: how many jobs in July?"},
                            {"n": 2, "org": ORG_A, "question": "Q two with Bearer abcdefghijklmnop in it"},
                            {"n": 3, "org": ORG_A, "question": "Q three, since purged"},
                            {"n": 4, "org": ORG_B, "question": "Q four of another company"}])
    await evidence.snapshot([turn_id(3)], organization_id=ORG_A, reason="monitored")
    async with chat.begin() as conn:
        await conn.execute(text("DELETE FROM plenum_cafm.agent_trace_spans WHERE turn_id = :t"), {"t": turn_id(3)})
        await conn.execute(text("DELETE FROM plenum_cafm.agent_trace_turns WHERE turn_id = :t"), {"t": turn_id(3)})
    ek = f"judge:{jid}"
    base = datetime(2026, 10, 6, 9, tzinfo=UTC)
    spec = [(1, ORG_A, "decided", 1, 1), (2, ORG_A, "decided", 1, 2), (3, ORG_A, "decided", 1, 3),
            (4, ORG_B, "decided", 1, 4), (5, ORG_A, "decided", 1, 5), (6, ORG_A, "decided", 0, 6),
            (7, ORG_A, "error", None, 7)]
    batch = await _new_batch(None, finish_job=True)
    rows = [monitor.verdict_row(evaluator_key=ek, turn={"turn_id": turn_id(n), "organization_id": org,
                                                        "started_at": base + timedelta(minutes=m)},
                                status=st, failure_present=fp, detail={"critique": f"Critique {n}."} if st == "decided" else {},
                                batch_id=batch["id"], judge_id=jid, mode_id=mid) for n, org, st, fp, m in spec]
    code = monitor.verdict_row(evaluator_key=LIVE_CHECK.evaluator_key, turn={"turn_id": turn_id(1), "organization_id": ORG_A,
                                                                              "started_at": base},
                               status="decided", failure_present=1, detail={"findings": ["No answer text.", "Empty."]},
                               batch_id=batch["id"])
    await monitor._save(batch["job_id"], verdicts=rows + [code])
    await monitor._update_batch(batch["id"], status="done", finished=True)         # free the platform scope for the test
    return ek, jid, mid, batch


async def test_flagged_answers_carry_the_question_from_the_chat_or_the_snapshot_and_never_cross_companies(chat, lab_scratch):
    ek, jid, mid, batch = await _seed_flags(chat, lab_scratch)
    ids = [turn_id(n) for n in range(1, 8)]
    got = await monitor._flagged_rows([ek], ids, org=ORG_A, offset=0, limit=50)
    assert [r["turn_id"] for r in got] == [turn_id(1), turn_id(2), turn_id(3), turn_id(5)]       # flags of company A, oldest first
    assert [r["question"] for r in got] == ["Q one: how many jobs in July?", "Q two with Bearer abcdefghijklmnop in it",
                                            "Q three, since purged", None]                          # chat, chat, snapshot, nowhere
    assert [r["critique"] for r in got] == ["Critique 1.", "Critique 2.", "Critique 3.", "Critique 5."]
    assert all(r["organization_id"] == ORG_A and r["evaluator_key"] == ek for r in got)
    everyone = await monitor._flagged_rows([ek], ids, org=None, offset=0, limit=50)
    assert [r["turn_id"] for r in everyone] == [turn_id(n) for n in (1, 2, 3, 4, 5)]
    page = await monitor._flagged_rows([ek], ids, org=None, offset=1, limit=2)
    assert [r["turn_id"] for r in page] == [turn_id(2), turn_id(3)]
    assert len(await monitor._flagged_rows([ek], ids, org=None, offset=0, limit=0)) == 1       # a page is at least one row
    assert [r["turn_id"] for r in await monitor._flagged_rows([ek], [turn_id(1)], org=None, offset=0, limit=50)] == [turn_id(1)]
    code = await monitor._flagged_rows([LIVE_CHECK.evaluator_key], ids, org=ORG_A, offset=0, limit=50)
    assert [(r["turn_id"], r["critique"]) for r in code] == [(turn_id(1), "No answer text.; Empty.")]   # a code check has findings
    assert await monitor._flagged_rows([], ids, org=None, offset=0, limit=5) == []
    assert await monitor._flagged_rows([ek], [], org=None, offset=0, limit=5) == []


async def test_reading_flags_names_one_database_per_statement_and_writes_nothing(chat, lab_scratch):
    ek, jid, mid, batch = await _seed_flags(chat, lab_scratch)
    seen: dict[str, list[str]] = {"main": [], "lab": []}
    engines = {"main": db.main_engine().sync_engine, "lab": db.lab_engine().sync_engine}
    handlers = {}
    for name, eng in engines.items():
        def record(conn, cursor, statement, *args, _n=name):
            seen[_n].append(statement)
        handlers[name] = record
        event.listen(eng, "before_cursor_execute", record)
    try:
        await monitor._flagged_rows([ek], [turn_id(n) for n in range(1, 8)], org=None, offset=0, limit=50)
        await monitor.list_batches(org=None, now=FIXED)
        await monitor.code_check_trend(org=None, periods=2, now=FIXED)
        await monitor.automation_trend(org=None, weeks=2, now=FIXED)
        await monitor.latest_prevalence(mid)
    finally:
        for name, eng in engines.items():
            event.remove(eng, "before_cursor_execute", handlers[name])
    both = [s for s in seen["main"] + seen["lab"] if "plenum_cafm" in s and "tracelab" in s]
    assert not both, both                                                       # no statement spans the two databases
    assert not [s for s in seen["main"] if "tracelab" in s]                     # the chat is never asked about Trace Lab
    assert not [s for s in seen["lab"] if "plenum_cafm" in s]
    assert any("plenum_cafm" in s for s in seen["main"]) and any("tracelab" in s for s in seen["lab"])
    assert not [s for s in seen["main"] if WRITE.match(s)], seen["main"]       # the chat is read, never written
    assert not [s for s in seen["lab"] if WRITE.match(s) and "SET LOCAL" not in s]   # and a read of Trace Lab writes nothing


async def test_batch_detail_names_the_flagged_answers_of_a_real_batch_redacted_and_scoped(chat, lab_scratch):
    ek, jid, mid, batch = await _seed_flags(chat, lab_scratch)
    snap = {"judge_id": jid, "judge_key": "wrong_period_sql-v0", "mode_id": mid, "mode_name": "wrong_period_sql",
            "kind": "llm", "evaluator_key": ek, "threshold": 0.15, "prompt_hash": "hash00000000"}
    ids = [turn_id(n) for n in range(1, 8)]
    row = await _finished_batch(lab_scratch, None, "2026-W41", started=FIXED, judges=[snap])
    await monitor._update_batch(row["id"], plan={"random": ids[:2], "risk_groups": {"thumbs_down": [ids[2]], "failed": [ids[4]]},
                                                 "to_judge": ids})
    out = await monitor.batch_detail(row["id"], org=None)
    assert [(f["turn_id"], f["group"], f["mode_name"]) for f in out["flagged"]] == [
        (turn_id(1), "uniform sample", "wrong_period_sql"), (turn_id(2), "uniform sample", "wrong_period_sql"),
        (turn_id(3), "rated down by the asker", "wrong_period_sql"), (turn_id(4), "risk group", "wrong_period_sql"),
        (turn_id(5), "the answer failed", "wrong_period_sql")]
    second = out["flagged"][1]
    assert "abcdefghijklmnop" not in second["question"] and "Bearer [redacted]" in second["question"]
    assert out["flagged"][4]["question"] == "" and out["batch"]["scope"] == "platform" and "plan" not in out["batch"]
    only = await monitor.batch_detail(row["id"], org=None, mode_id=mid, offset=3, limit=1)
    assert [f["turn_id"] for f in only["flagged"]] == [turn_id(4)]
    nothing = await monitor.batch_detail(row["id"], org=None, mode_id=str(uuid.uuid4()))
    assert nothing["flagged"] == []                                              # a mode the batch did not run
    for org in (ORG_A, ORG_B):                                                   # a platform batch is not a company's to read
        with pytest.raises(monitor.WatchError) as e:
            await monitor.batch_detail(row["id"], org=org)
        assert e.value.status == 404
    company = await _finished_batch(lab_scratch, ORG_A, "2026-W41", started=FIXED, judges=[snap])
    await monitor._update_batch(company["id"], plan={"random": ids, "risk_groups": {}, "to_judge": ids})
    mine = await monitor.batch_detail(company["id"], org=ORG_A)
    assert [f["turn_id"] for f in mine["flagged"]] == [turn_id(1), turn_id(2), turn_id(3), turn_id(5)]   # B's flag stays out
    with pytest.raises(monitor.WatchError) as e:
        await monitor.batch_detail(company["id"], org=ORG_B)
    assert e.value.status == 404
    with pytest.raises(monitor.WatchError):
        await monitor.batch_detail("not-a-uuid", org=None)


def _estimate(mid, corrected, **kw):
    return {"mode_id": mid, "raw": corrected, "corrected": corrected, "ci_low": 0.01, "ci_high": 0.2, "confidence": 0.95,
            "n_sample": 60, "threshold": 0.15, "crossed": False, "validity_warning": "", "reason": None, **kw}


async def test_the_chart_and_the_prevalence_read_the_latest_finished_batch_of_each_period_for_one_scope(lab_scratch):
    async with lab_scratch.begin() as conn:
        mid = await fx.add_mode(conn, "wrong_period_chart", status="confirmed", agent="udr",
                                definition="Present when the answer quotes the wrong period.")
    w40 = (datetime(2026, 9, 28, tzinfo=UTC), datetime(2026, 10, 5, tzinfo=UTC))
    w41 = (datetime(2026, 10, 5, tzinfo=UTC), datetime(2026, 10, 12, tzinfo=UTC))
    w42 = (datetime(2026, 10, 12, tzinfo=UTC), datetime(2026, 10, 19, tzinfo=UTC))
    await _finished_batch(lab_scratch, None, "2026-W40", started=FIXED - timedelta(days=9), window=w40,
                          results={"modes": [_estimate(mid, 0.30)]})
    capped = await _finished_batch(lab_scratch, None, "2026-W40", started=FIXED - timedelta(days=8), window=w40,
                                   status="capped", results={"modes": [_estimate(mid, 0.12)]})     # re-run: it wins
    failed = await _finished_batch(lab_scratch, None, "2026-W41", started=FIXED - timedelta(days=2), window=w41,
                                   status="failed", results={"modes": [_estimate(mid, 0.99)]})
    await _new_batch(None, period_label="2026-W42", window_start=w42[0], window_end=w42[1])         # still queued
    company = await _finished_batch(lab_scratch, ORG_A, "2026-W40", started=FIXED - timedelta(days=8), window=w40,
                                    results={"modes": [_estimate(mid, 0.50)]})
    periods = await monitor._period_batches(None)
    assert [(p["period_label"], str(p["id"])) for p in periods] == [("2026-W40", capped["id"])]
    assert [str(p["id"]) for p in await monitor._period_batches(ORG_A)] == [company["id"]]
    assert await monitor._period_batches(ORG_B) == []
    assert await monitor._mode(mid) == {"id": mid, "name": "wrong_period_chart"}
    assert await monitor._mode(str(uuid.uuid4())) is None and await monitor._mode("not-a-uuid") is None
    out = await monitor.chart(org=None, mode_id=mid)
    assert [(p["period_label"], p["corrected"], p["batch_id"]) for p in out["points"]] == [("2026-W40", 0.12, capped["id"])]
    assert out["mode"]["name"] == "wrong_period_chart" and out["threshold"] == 0.15 and out["y_max"] == 0.25
    mine = await monitor.chart(org=ORG_A, mode_id=mid)
    assert [p["corrected"] for p in mine["points"]] == [0.5]
    assert (await monitor.chart(org=ORG_B, mode_id=mid))["points"] == []
    json.dumps([out, mine])
    found, reason = await monitor.latest_prevalence(mid)
    assert reason == "" and found["period"] == "2026-W40" and found["corrected"] == 0.12      # the platform's, not the company's
    assert await judges.latest_prevalence(mid) == (found, "")                                  # the failure report's seam
    assert failed["id"] and (await monitor.latest_prevalence(str(uuid.uuid4())))[1] == (
        "Monitoring has not produced an estimate for this mode yet.")


async def _code_judge_in_use(lab, check, *, tested_at, mode_name="code_check_mode"):
    """A frozen code judge for `check`, decision use, with a finished held-out test run at `tested_at`."""
    async with lab.begin() as conn:
        mid, sid = await _mode_and_split(conn, mode_name)
        jid = str(uuid.uuid4())
        await conn.execute(text(
            "INSERT INTO tracelab.judges (id, mode_id, version, judge_key, kind, split_id, check_key, check_version, "
            "prompt_hash, status, stop_reason, frozen_at, frozen_by, frozen_by_email, decision) VALUES (CAST(:id AS uuid), "
            "CAST(:m AS uuid), 1, :jk, 'code', CAST(:s AS uuid), :ck, :cv, 'code00000000', 'frozen', 'Stable.', :at, "
            "CAST(:u AS uuid), 'owner@plenum.test', 'use')"),
            {"id": jid, "m": mid, "jk": f"{check.key}-v1", "s": sid, "ck": check.key, "cv": check.version, "at": tested_at,
             "u": ACTOR})
        await conn.execute(text(
            "INSERT INTO tracelab.judge_runs (id, judge_id, split, status, items, finished_at) VALUES "
            "(CAST(:id AS uuid), CAST(:j AS uuid), 'test', 'done', CAST('[]' AS jsonb), :at)"),
            {"id": str(uuid.uuid4()), "j": jid, "at": tested_at})
    return jid, mid


async def _verdict_at(lab, batch, ek, tid, org, started, status="decided", flag=0, *, updated=None, judge_id=None,
                      detail=None):
    """A monitoring verdict with explicit times (a decided one is never updated, so its times are set on insert)."""
    async with lab.begin() as conn:
        await conn.execute(text(
            "INSERT INTO tracelab.monitor_verdicts (id, evaluator_key, turn_id, organization_id, turn_started_at, judge_id, "
            "status, failure_present, detail, first_batch_id, updated_at) VALUES (gen_random_uuid(), :ek, :t, "
            "CAST(:o AS uuid), :ts, CAST(:j AS uuid), :st, :fp, CAST(:d AS jsonb), CAST(:b AS uuid), :up)"),
            {"ek": ek, "t": tid, "o": org, "ts": started, "j": judge_id, "st": status, "d": json.dumps(detail or {}),
             "fp": flag if status == "decided" else None, "b": batch["id"], "up": updated or started})


async def test_the_code_check_trend_counts_each_week_of_one_scope_and_marks_a_validated_check(lab_scratch):
    ek = LIVE_CHECK.evaluator_key
    batch = await _new_batch(None, finish_job=True)
    for n, (org, when, st, flag) in enumerate([
            (ORG_A, W41, "decided", 1), (ORG_A, W41 + timedelta(hours=1), "decided", 0), (ORG_A, W41, "decided", 0),
            (ORG_A, W41, "undecidable", 0), (ORG_A, W42, "decided", 0), (ORG_B, W41, "decided", 1),
            (ORG_A, datetime(2026, 9, 1, tzinfo=UTC), "decided", 1)]):                  # the last is far before the span
        await _verdict_at(lab_scratch, batch, ek, f"t{n}", org, when, st, flag)
    jid, _ = await _lab_judge(lab_scratch)                                              # a judge verdict is not a code check
    await _verdict_at(lab_scratch, batch, f"judge:{jid}", "tj", ORG_A, W41, flag=1, judge_id=jid)
    rows = await monitor._check_week_rows(ORG_A, datetime(2026, 10, 5, tzinfo=UTC))
    assert {(r["evaluator_key"], r["week_start"], r["n"], r["decided"], r["flagged"]) for r in rows} == {
        (ek, datetime(2026, 10, 5), 4, 3, 1), (ek, datetime(2026, 10, 12), 1, 1, 0)}      # Monday of each week, A only
    out = await monitor.code_check_trend(org=ORG_A, periods=2, now=FIXED)
    first = next(c for c in out["checks"] if c["evaluator_key"] == ek)
    assert first["validated"] is False
    assert first["weeks"] == [{"period_label": "2026-W41", "n": 4, "flagged": 1, "rate": 0.3333},
                              {"period_label": "2026-W42", "n": 1, "flagged": 0, "rate": 0.0}]
    both = await monitor.code_check_trend(org=None, periods=2, now=FIXED)
    assert next(c for c in both["checks"] if c["evaluator_key"] == ek)["weeks"][0]["n"] == 5   # every company
    assert (await monitor.code_check_trend(org=ORG_B, periods=2, now=FIXED))["checks"][0]["weeks"][1]["n"] == 0
    await _code_judge_in_use(lab_scratch, LIVE_CHECK, tested_at=W41 - timedelta(days=3))
    assert next(c for c in (await monitor.code_check_trend(org=ORG_A, periods=2, now=FIXED))["checks"]
                if c["evaluator_key"] == ek)["validated"] is True
    json.dumps(out)


R1 = "bbbbbbbb-0000-4000-8000-0000000000a1"
R2 = "bbbbbbbb-0000-4000-8000-0000000000a2"
TESTED = datetime(2026, 10, 8, 10, tzinfo=UTC)         # the held-out test of the code judge finished here (inside W41)


async def _note_at(conn, tid, org, at, *, reviewer=R1, verdict="right", tombstone=False):
    """A reviewer verdict written at an explicit time (an annotation is never edited, so its time is set on insert)."""
    aid = str(uuid.uuid4())
    wrong = verdict == "wrong" and not tombstone
    await conn.execute(text(
        "INSERT INTO tracelab.annotations (id, lineage_id, version, kind, organization_id, conversation_key, turn_id, "
        "reviewer_id, tombstone, verdict, note, field, quote, quote_occurrence, quote_start, span_name, span_kind, blind, "
        "created_at) VALUES (CAST(:id AS uuid), CAST(:id AS uuid), 1, 'verdict', CAST(:o AS uuid), :ck, :t, "
        "CAST(:r AS uuid), :tomb, :v, :note, :field, :quote, :occ, :qs, :sn, :sk, true, :at)"),
        {"id": aid, "o": org, "ck": "t:" + tid, "t": tid, "r": reviewer, "tomb": tombstone,
         "v": None if tombstone else verdict, "note": None if tombstone else ("Wrong period." if wrong else "no failure observed"),
         "field": "answer" if wrong else None, "quote": "July" if wrong else None, "occ": 1 if wrong else None,
         "qs": 0 if wrong else None, "sn": "turn" if wrong else None, "sk": "answer" if wrong else None, "at": at})


async def _eval_case(conn, org, key, run_id, reps):
    cid = str(uuid.uuid4())
    await conn.execute(text(
        "INSERT INTO tracelab.eval_cases (id, organization_id, case_key, status, source_type, source_turn_id, input, expected) "
        "VALUES (CAST(:id AS uuid), CAST(:o AS uuid), :k, 'proposed', 'live_turn', 'L-src', CAST('{}' AS jsonb), "
        "CAST('{}' AS jsonb))"), {"id": cid, "o": org, "k": key})
    await conn.execute(text(
        "INSERT INTO tracelab.eval_case_runs (id, eval_case_id, golden_run_id, purpose, k, reps) VALUES "
        "(gen_random_uuid(), CAST(:c AS uuid), CAST(:r AS uuid), 'baseline', 5, CAST(:reps AS jsonb))"),
        {"c": cid, "r": run_id, "reps": json.dumps(reps)})


async def _seed_automation(lab):
    """Two weeks of checking in one scratch database, every time explicit (W41 is Oct 5-12, W42 Oct 12-19):
    a code check validated mid-W41, an LLM judge's verdict, reviewer verdicts (one in a uniform batch, one in a
    withdrawn one, one in a diversity batch), labels (single, bulk, deleted), golden answers of several kinds, and
    eval-case reps. Company A holds most of it, company B a little."""
    batch = await _new_batch(None, finish_job=True)
    jid, _ = await _lab_judge(lab)                                             # a draft LLM judge: a verdict names it
    code_jid, code_mode = await _code_judge_in_use(lab, LIVE_CHECK, tested_at=TESTED, mode_name="auto_code_mode")
    ek = LIVE_CHECK.evaluator_key
    async with lab.begin() as conn:
        await conn.execute(text("UPDATE tracelab.modes SET confirmed_at = :at WHERE id = CAST(:m AS uuid)"),
                           {"at": datetime(2026, 10, 2, tzinfo=UTC), "m": code_mode})
        second = await fx.add_mode(conn, "auto_second_mode", status="confirmed", agent="udr")
        await conn.execute(text("UPDATE tracelab.modes SET confirmed_at = :at WHERE id = CAST(:m AS uuid)"),
                           {"at": datetime(2026, 10, 13, tzinfo=UTC), "m": second})
    for tid, org, when, status in [("L-code-ok", ORG_A, TESTED + timedelta(days=1), "decided"),
                                   ("L-code-early", ORG_A, TESTED - timedelta(days=2), "decided"),      # before it was validated
                                   ("L-code-undecided", ORG_A, TESTED + timedelta(days=1), "undecidable"),
                                   ("L-code-w42", ORG_A, W42, "decided"), ("B-code", ORG_B, W42, "decided")]:
        await _verdict_at(lab, batch, ek, tid, org, when, status, 0)
    await _verdict_at(lab, batch, f"judge:{jid}", "L-judge", ORG_A, W41, flag=1, judge_id=jid)
    async with lab.begin() as conn:
        ga = await seed_set(conn, ORG_A, cases=(("1", ("and the next month?",)), ("2", ())))
        gb = await seed_set(conn, ORG_B)
        run_a, run_b = await seed_run(conn, ORG_A, ga["set_id"], k=20), await seed_run(conn, ORG_B, gb["set_id"])
        c1, c2 = ga["cases"]["1"], ga["cases"]["2"]
        decisive = {"source": "grader", "evaluation": "objective", "value_check": {"status": "all_found"}}
        model_only = {"source": "grader", "evaluation": "objective", "value_check": {"status": "none"}}
        chat_error = {"source": "runner", "evaluation": "objective", "value_check": {"status": "none"}}
        judgment = {"source": "grader", "evaluation": "judgment", "reading": "needs_person", "validated_outcome": "pass"}
        await seed_result(conn, ORG_A, run_a, c1, "1", rep=1, msg_idx=0, outcome="pending", turn_id="G0")      # not the final row
        await seed_result(conn, ORG_A, run_a, c1, "1", rep=1, msg_idx=1, outcome="pass", grade=decisive,
                          graded_at=W41, turn_id="G1")
        await seed_result(conn, ORG_A, run_a, c2, "2", rep=1, outcome="fail", grade=model_only, graded_at=W41, turn_id="G2")
        await seed_result(conn, ORG_A, run_a, c2, "2", rep=2, outcome="fail", grade=chat_error, graded_at=W41, turn_id="G3")
        await seed_result(conn, ORG_A, run_a, c2, "2", rep=3, outcome="unsure", grade={**judgment, "grader_version": "gv-ok"},
                          graded_at=W41, turn_id="G4")
        await seed_result(conn, ORG_A, run_a, c2, "2", rep=4, outcome="unsure", grade={**judgment, "grader_version": "gv-old"},
                          graded_at=W41, turn_id="G5")
        await seed_result(conn, ORG_A, run_a, c2, "2", rep=5, outcome="degraded", grade=model_only, graded_at=W41, turn_id="G6")
        await seed_result(conn, ORG_A, run_a, c2, "2", rep=6, outcome="pass", grade=decisive, graded_at=W42, turn_id="G-ann")
        await seed_result(conn, ORG_A, run_a, c2, "2", rep=7, outcome="pending", turn_id="G-lab")              # only named by a label
        await seed_result(conn, ORG_A, run_a, c2, "2", rep=8, outcome="pass", grade=decisive,
                          graded_at=datetime(2026, 9, 20, tzinfo=UTC), turn_id="G-old")                       # before the span
        await _eval_case(conn, ORG_A, "e-0000000a", run_a, [
            {"rep": 1, "turn_id": "E1", "status": "passed", "graded_at": W41.isoformat()},
            {"rep": 2, "turn_id": "E2", "status": "ungraded"},
            {"rep": 3, "turn_id": "E3", "status": "failed", "graded_at": W42.isoformat()},
            {"rep": 4, "turn_id": "E4", "status": "passed", "graded_at": "not a time"},                      # unreadable: left out
            {"rep": 5, "status": "passed", "graded_at": W41.isoformat()}])                                  # no answer named
        await _eval_case(conn, ORG_A, "e-0000000b", run_a, {})                                              # not a list of reps
        await _eval_case(conn, ORG_B, "e-0000000c", run_b, [{"rep": 1, "turn_id": "EB", "status": "passed",
                                                             "graded_at": W41.isoformat()}])
        await _note_at(conn, "L-judge", ORG_A, W41, reviewer=R1, verdict="right")
        await _note_at(conn, "L-person", ORG_A, W41, reviewer=R1, verdict="wrong")
        await _note_at(conn, "L-person", ORG_A, W41 + timedelta(hours=2), reviewer=R2, verdict="right")
        await _note_at(conn, "L-diverse", ORG_A, W41, reviewer=R1)
        await _note_at(conn, "L-withdrawn", ORG_A, W41, reviewer=R1)
        await _note_at(conn, "G-ann", ORG_A, W41, reviewer=R1)                                              # a golden answer
        await _note_at(conn, "L-gone", ORG_A, W41, reviewer=R1, tombstone=True)
        await _note_at(conn, "B-person", ORG_B, W42, reviewer=R1)
        uniform = await fx.add_batch_item(conn, "L-judge", ORG_A, strategy="uniform")
        await fx.add_batch_item(conn, "L-person", ORG_A, batch_id=uniform, position=2)
        await fx.add_batch_item(conn, "L-diverse", ORG_A, strategy="diversity", plan_step="cluster_representatives")
        gone = await fx.add_batch_item(conn, "L-withdrawn", ORG_A, strategy="uniform")
        await conn.execute(text("UPDATE tracelab.sample_batches SET withdrawn_at = now() WHERE id = CAST(:b AS uuid)"),
                           {"b": gone})
        run = await fx.add_run(conn, "cells", mode_id=code_mode, org=ORG_A)
        proposal = await fx.add_proposal(conn, run, turn_id="L-bulk", mode_id=code_mode, org=ORG_A)
        await fx.add_label(conn, "L-label", code_mode, org=ORG_A, created_at=W41)
        await fx.add_label(conn, "G-lab", code_mode, org=ORG_A, created_at=W41)
        await fx.add_label(conn, "L-bulk", code_mode, org=ORG_A, created_at=W41, source="accepted_label_proposal",
                           approval="bulk", proposal_id=proposal)
        await fx.add_label(conn, "L-undone", code_mode, org=ORG_A, created_at=W41, present=None)
    return {"code_mode": code_mode, "second_mode": second}


@pytest.fixture
def trusted_graders(monkeypatch):
    """grader_validation.passes: only grader version gv-ok is trusted. Records what it was asked."""
    asked: list[str] = []

    async def passes(version, *, evaluation="judgment"):
        asked.append(version)
        return version == "gv-ok"

    monkeypatch.setattr(grader_validation, "passes", passes)
    return asked


async def test_automation_rows_read_every_source_of_one_company_between_two_times(lab_scratch, trusted_graders):
    await _seed_automation(lab_scratch)
    start, end = datetime(2026, 10, 5, tzinfo=UTC), datetime(2026, 10, 19, tzinfo=UTC)
    rows = await monitor._automation_rows(ORG_A, start, end)
    assert set(rows) == {"auto", "human", "golden_acc", "live_acc", "modes", "automated"}
    auto = {(r["turn_id"], r["source"]): r["validated"] for r in rows["auto"]}
    assert auto == {("L-judge", "judge_frozen"): True, ("L-code-ok", "code_validated"): True,
                    ("L-code-early", "code_validated"): False,                       # made before its test finished
                    ("L-code-w42", "code_validated"): True,
                    ("G1", "golden_auto"): True, ("G2", "golden_auto"): False,       # the model alone graded G2
                    ("G3", "golden_auto"): True, ("G4", "golden_auto"): True,        # a chat error; a trusted grader
                    ("G-ann", "golden_auto"): True, ("E1", "golden_auto"): True, ("E3", "golden_auto"): True}
    assert all(r["is_golden"] is (r["source"] == "golden_auto") for r in rows["auto"])
    assert sorted(set(trusted_graders)) == ["gv-ok", "gv-old"]                       # asked once per version still waiting
    assert sorted((r["correct"] for r in rows["golden_acc"])) == [False, False, True, True, True]   # G1, G2, G3, G4, G-ann
    human = {(r["turn_id"], r["source"], r["is_golden"]) for r in rows["human"]}
    assert human == {("L-judge", "open_coding", False), ("L-person", "open_coding", False),
                     ("L-diverse", "open_coding", False), ("L-withdrawn", "open_coding", False),
                     ("G-ann", "golden_person", True), ("L-label", "labels", False), ("G-lab", "labels", True)}
    assert sorted(r["right"] for r in rows["live_acc"]) == [False, True, True]       # L-judge, L-person x2: a fair sample only
    assert len(rows["modes"]) == 2 and len(rows["automated"]) == 1
    assert [str(r["mode_id"]) for r in rows["automated"]] and rows["automated"][0]["since"] == TESTED
    assert all(r["at"] is not None for r in rows["auto"] + rows["human"])
    whole = await monitor._automation_rows(None, start, end)                         # the platform owner sees every company
    assert {"EB", "B-code"} <= {r["turn_id"] for r in whole["auto"]} and "B-person" in {r["turn_id"] for r in whole["human"]}
    assert not {"EB", "B-code", "B-person"} & {r["turn_id"] for r in rows["auto"] + rows["human"]}
    narrow = await monitor._automation_rows(ORG_A, datetime(2026, 10, 12, tzinfo=UTC), end)     # [start, end) only
    assert {r["turn_id"] for r in narrow["auto"]} == {"L-code-w42", "G-ann", "E3"} and narrow["human"] == []


async def test_the_automatic_and_people_trend_of_a_company_and_of_the_platform(lab_scratch, trusted_graders):
    await _seed_automation(lab_scratch)
    out = await monitor.automation_trend(org=ORG_A, weeks=2, now=FIXED)
    w41, w42 = out["weeks"]
    assert (w41["week"], w42["week"]) == ("2026-W41", "2026-W42")
    assert (w41["checked_any"], w41["auto_only"], w41["people_only"], w41["both"]) == (12, 5, 6, 1)
    assert w41["auto_share"] == 0.4167 and w41["people_share"] == 0.5833
    assert (w41["answers_live"], w41["answers_golden"]) == (6, 6)
    assert w41["auto_by_source"] == {"golden_auto": 4, "code_validated": 1, "judge_frozen": 1}
    assert w41["people_by_source"] == {"open_coding": 4, "labels": 2, "golden_person": 1}
    assert (w41["modes_confirmed"], w41["modes_automated"]) == (1, 1)
    assert (w41["golden_accuracy"]["decided"], w41["golden_accuracy"]["correct"], w41["golden_accuracy"]["rate"]) == (4, 2, 0.5)
    assert (w41["live_accuracy"]["reviewed"], w41["live_accuracy"]["right"], w41["live_accuracy"]["rate"]) == (3, 2, 0.6667)
    assert (w42["checked_any"], w42["auto_share"], w42["people_share"]) == (3, 1.0, 0.0)
    assert (w42["answers_live"], w42["answers_golden"], w42["modes_confirmed"], w42["modes_automated"]) == (1, 2, 2, 1)
    assert w42["golden_accuracy"]["decided"] == 1 and w42["live_accuracy"]["rate"] is None
    assert set(out["definitions"]) >= {"auto_share", "people_share", "golden_accuracy", "live_accuracy"}
    json.dumps(out)
    mine = (await monitor.automation_trend(org=ORG_B, weeks=2, now=FIXED))["weeks"]
    assert (mine[0]["checked_any"], mine[0]["auto_share"], mine[1]["checked_any"], mine[1]["people_only"]) == (1, 1.0, 2, 1)
    platform = (await monitor.automation_trend(org=None, weeks=2, now=FIXED))["weeks"]
    assert (platform[0]["checked_any"], platform[1]["checked_any"]) == (13, 5)       # A's weeks plus B's
    empty = (await monitor.automation_trend(org=str(uuid.uuid4()), weeks=2, now=FIXED))["weeks"]
    assert [(w["checked_any"], w["auto_share"]) for w in empty] == [(0, None), (0, None)]


async def test_the_automation_read_is_a_read_only_transaction_on_the_lab_database(lab_scratch, trusted_graders):
    await _seed_automation(lab_scratch)
    seen: list[str] = []
    eng = db.lab_engine().sync_engine

    def record(conn, cursor, statement, *args):
        seen.append(statement)

    event.listen(eng, "before_cursor_execute", record)
    try:
        await monitor._automation_rows(None, datetime(2026, 10, 5, tzinfo=UTC), datetime(2026, 10, 19, tzinfo=UTC))
    finally:
        event.remove(eng, "before_cursor_execute", record)
    assert seen[0].startswith("SET TRANSACTION READ ONLY")
    assert not [s for s in seen if WRITE.match(s)] and not [s for s in seen if "plenum_cafm" in s]


async def test_a_batch_that_ran_is_read_back_through_every_read(chat, lab_scratch, monkeypatch, judge_calls):
    jid, mid = await _judge_in_use(lab_scratch)
    await _seed_week(chat, wrong=set(range(1, 9)))
    monkeypatch.setattr(monitor, "_propose_cases", lambda *a, **k: _async_value(["case-1"]))
    out = await _start(monkeypatch)
    await monitor.work_batch(out["batch"]["id"])
    now = datetime.now(UTC)
    listed = await monitor.list_batches(org=None, now=now)
    assert [b["id"] for b in listed["batches"]] == [out["batch"]["id"]] and listed["batches"][0]["status"] == "done"
    assert listed["batches"][0]["crossed"] is True and listed["batches"][0]["results"]["modes"][0]["mode_id"] == mid
    detail = await monitor.batch_detail(out["batch"]["id"], org=None)
    assert len(detail["flagged"]) == detail["batch"]["results"]["modes"][0]["flagged"] > 0
    assert all(f["mode_name"] == "wrong_period_sql" and f["critique"] == "Quotes July." and f["question"] for f in detail["flagged"])
    assert {f["group"] for f in detail["flagged"]} <= {"uniform sample", "rated down by the asker", "asked again soon after"}
    chart = await monitor.chart(org=None, mode_id=mid)
    assert len(chart["points"]) == 1 and chart["points"][0]["crossed"] is True and chart["threshold"] == 0.15
    found, reason = await monitor.latest_prevalence(mid)
    assert reason == "" and found["corrected"] == chart["points"][0]["corrected"] > 0.15
    assert found["batch_id"] == out["batch"]["id"] and found["n_sample"] == 6
    report = await judges.failure_report(fx.owner())
    assert report["modes"][0]["prevalence"] is None or report["modes"][0]["prevalence"]["batch_id"] == out["batch"]["id"]
    trend = await monitor.code_check_trend(org=None, periods=1, now=now)
    assert {c["evaluator_key"] for c in trend["checks"]} == {c["evaluator_key"] for c in monitor.live_checks()}
    assert sum(c["weeks"][0]["n"] for c in trend["checks"]) >= 12


async def _async_value(value):
    return value
