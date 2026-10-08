"""Chat threads (memory phase A): the server-side record of a conversation, what the model is told
of it, when the summary folds, and that a thread is its owner's alone."""
from __future__ import annotations

import asyncio
import json
import uuid

import pytest

from src.http_client import caller_organization_id
from src.services import chat_threads as ct
from src.services.principal import Principal, caller_principal


def _principal(role="user", org=None):
    return Principal(user_id=uuid.uuid4(), email="fm@example.com", organization_id=org or uuid.uuid4(), role=role,
                     can_ingest=True, building_ids=None)


class _Result:
    def __init__(self, rows=None, rowcount=0):
        self._rows = rows or []
        self.rowcount = rowcount

    def mappings(self):
        return self

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return self._rows


class _Conn:
    """A connection that answers from a script and records every statement."""

    def __init__(self, store):
        self.store = store

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        self.store["sql"].append((sql, params or {}))
        for needle, rows in self.store["answers"]:
            if needle in sql:
                return _Result(rows, rowcount=len(rows))
        return _Result([], rowcount=1)


class _Engine:
    def __init__(self, store):
        self.store = store

    def begin(self):
        return _Conn(self.store)

    def connect(self):
        return _Conn(self.store)


@pytest.fixture
def db(monkeypatch):
    store = {"sql": [], "answers": []}
    import src.database as database

    monkeypatch.setattr(database, "_get_engine", lambda: _Engine(store))
    monkeypatch.setattr(ct, "_ready", True)
    return store


def test_the_context_is_the_summary_then_the_last_turns_verbatim():
    out = ct.format_context("Earlier: the user asked about B-301's boilers.",
                            [{"question": "How many are overdue?", "answer": "Three, all at B-301."}])
    assert out.startswith("## Recent conversation")
    assert "**Earlier in this conversation (summary):** Earlier: the user asked about B-301's boilers." in out
    assert "**User:** How many are overdue?" in out and "**Assistant:** Three, all at B-301." in out
    assert ct.format_context(None, []) == ""
    long = "x" * (ct.CONTEXT_CHARS + 50)
    assert ct.format_context(None, [{"question": long, "answer": ""}]).endswith("…")


def test_a_question_opens_the_thread_under_the_caller_and_the_acting_company(db):
    p = _principal(role="superadmin")
    acting = str(uuid.uuid4())
    caller_principal.set(p)
    caller_organization_id.set(acting)
    asyncio.run(ct.record_question("sess-1", "  Which  boilers are overdue at B-301?  "))
    insert_thread = next(s for s in db["sql"] if "INSERT INTO plenum_cafm.chat_threads" in s[0])
    assert insert_thread[1]["org"] == acting and insert_thread[1]["uid"] == str(p.user_id)
    assert insert_thread[1]["title"] == "Which boilers are overdue at B-301?"
    # A follow-up keeps the first question as the title.
    assert "title = COALESCE(plenum_cafm.chat_threads.title, EXCLUDED.title)" in insert_thread[0]
    insert_turn = next(s for s in db["sql"] if "INSERT INTO plenum_cafm.chat_turns" in s[0])
    assert "COALESCE(max(turn_no), 0) + 1" in insert_turn[0]


def test_nobody_signed_in_records_nothing_and_a_store_that_is_down_never_raises(db, monkeypatch):
    caller_principal.set(None)
    asyncio.run(ct.record_question("sess-1", "hello"))
    assert db["sql"] == []
    monkeypatch.setattr(ct, "_ready", False)
    assert asyncio.run(ct.conversation_context("sess-1")) is None   # caller falls back
    assert asyncio.run(ct.list_threads()) == []


def test_the_answer_fills_the_latest_open_turn_and_keeps_only_tool_names(db, monkeypatch):
    scheduled = []
    monkeypatch.setattr(ct, "schedule_fold", lambda sid: scheduled.append(sid))
    db["answers"] = [("UPDATE plenum_cafm.chat_turns", [("Which boilers are overdue?",)])]
    asyncio.run(ct.record_answer("sess-1", "Three.", tools=[{"tool": "get_work_orders", "input": {"x": 1}},
                                                            {"tool": "get_work_orders"}, {"name": "udr_read_records"}]))
    upd = next(s for s in db["sql"] if "UPDATE plenum_cafm.chat_turns" in s[0])
    # only the NEWEST turn, and only while it has no answer
    assert "ORDER BY turn_no DESC LIMIT 1) AND answer IS NULL" in upd[0]
    assert upd[1]["tools"] == '["get_work_orders", "udr_read_records"]'
    assert scheduled == ["sess-1"]


def test_a_repeated_answer_write_never_fills_an_older_question(db, monkeypatch):
    """Some paths record one answer twice. The second write finds the newest turn answered and does
    nothing - it used to fill an OLDER unanswered turn (a stopped one) with this answer (5 Oct 2026)."""
    scheduled = []
    monkeypatch.setattr(ct, "schedule_fold", lambda sid: scheduled.append(sid))
    asyncio.run(ct.record_answer("sess-1", "Three.", tools=[]))      # UPDATE matches no row
    assert not any("UPDATE plenum_cafm.chat_threads" in s_[0] for s_ in db["sql"])
    assert scheduled == []


def test_the_answer_keeps_its_card_payloads_beside_the_names(db, monkeypatch):
    """A reopened chat renders the dashboard it rendered live (5 Oct 2026): the two presentation
    payloads are kept after the names; names stay strings, so readers of names are unchanged."""
    monkeypatch.setattr(ct, "schedule_fold", lambda sid: None)
    resp = {"narrative": "Five vendors are blocked.", "kpis": [{"value": 5}]}
    pipe = {"engine": "compliance", "steps": []}
    asyncio.run(ct.record_answer("sess-1", "Five.", tools=[
        {"tool": "list_vendor_accreditations", "input": {"risk_filter": "blocked"}, "output": {"rows": [{"secret": "x"}]}},
        {"tool": "compliance_pipeline", "input": {}, "output": {"steps": ["old"]}},
        {"tool": "compliance_pipeline", "input": {}, "output": pipe},
        {"tool": "compliance_response", "input": {}, "output": resp}]))
    upd = next(s for s in db["sql"] if "UPDATE plenum_cafm.chat_turns" in s[0])
    stored = json.loads(upd[1]["tools"])
    assert stored[:3] == ["list_vendor_accreditations", "compliance_pipeline", "compliance_response"]
    # the FIRST of each, the one the chat draws (complianceLive.extractComplianceAnswer uses find)
    assert stored[3:] == [{"tool": "compliance_pipeline", "output": {"steps": ["old"]}}, {"tool": "compliance_response", "output": resp}]
    # only the presentation payloads: no other tool's input or output is kept
    assert "secret" not in upd[1]["tools"] and "risk_filter" not in upd[1]["tools"]


def test_an_oversized_card_payload_is_not_kept(db, monkeypatch):
    monkeypatch.setattr(ct, "schedule_fold", lambda sid: None)
    big = {"narrative": "x" * (ct.CARD_MAX_BYTES + 10)}
    asyncio.run(ct.record_answer("sess-1", "Five.", tools=[{"tool": "compliance_response", "output": big}]))
    upd = next(s for s in db["sql"] if "UPDATE plenum_cafm.chat_turns" in s[0])
    assert json.loads(upd[1]["tools"]) == ["compliance_response"]


def test_the_summary_folds_only_once_enough_turns_left_the_window(db, monkeypatch):
    turns = [{"turn_no": i, "question": f"q{i}", "answer": f"a{i}"} for i in range(1, 1 + ct.CONTEXT_TURNS + 3)]
    db["answers"] = [("SELECT summary, summary_through, turn_count", [{"summary": "", "summary_through": 0, "turn_count": len(turns)}]),
                     ("SELECT turn_no, question, answer FROM", turns)]
    calls = []

    async def fake_summarise(summary, older):
        calls.append((summary, [t["turn_no"] for t in older]))
        return "Folded."

    monkeypatch.setattr(ct, "_summarise", fake_summarise)
    # 3 older turns < FOLD_AFTER: wait.
    assert asyncio.run(ct.fold_summary("sess-1")) is False and calls == []
    # Forced, or once FOLD_AFTER have accumulated: only the turns beyond the verbatim window fold.
    assert asyncio.run(ct.fold_summary("sess-1", force=True)) is True
    assert calls == [("", [1, 2, 3])]
    upd = next(s for s in db["sql"] if "SET summary = :s, summary_through = :t" in s[0])
    assert upd[1] == {"s": "Folded.", "t": 3, "tid": "sess-1"}


def test_the_context_reads_past_the_summary_boundary_only(db):
    db["answers"] = [("SELECT summary, summary_through FROM", [{"summary": "So far: boilers.", "summary_through": 4}]),
                     ("SELECT question, answer, turn_no FROM", [{"question": "q6", "answer": "a6", "turn_no": 6},
                                                               {"question": "q5", "answer": "a5", "turn_no": 5}])]
    out = asyncio.run(ct.conversation_context("sess-1"))
    sel = next(s for s in db["sql"] if "SELECT question, answer, turn_no" in s[0])
    assert sel[1]["after"] == 4 and sel[1]["n"] == ct.CONTEXT_TURNS
    assert out.index("So far: boilers.") < out.index("**User:** q5") < out.index("**User:** q6")


def test_a_thread_is_listed_and_opened_for_its_owner_in_that_company_only(db):
    p = _principal()
    caller_principal.set(p)
    caller_organization_id.set(None)
    asyncio.run(ct.list_threads(q="boiler"))
    sql, params = db["sql"][-1]
    assert "user_id = CAST(:uid AS uuid) AND organization_id = CAST(:org AS uuid)" in sql
    assert params["org"] == str(p.organization_id) and params["q"] == "%boiler%"
    assert "deleted_at IS NULL" in sql
    db["answers"] = [("FROM plenum_cafm.chat_threads WHERE id = :tid", [])]
    assert asyncio.run(ct.get_thread("someone-elses")) is None


def test_the_routes_need_a_signed_in_caller_and_refuse_another_company(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from src.api.routes import threads as routes
    from src.services.principal import current_principal

    app = FastAPI()
    app.include_router(routes.router)
    c = TestClient(app)
    assert c.get("/api/threads").status_code in (401, 403)

    p = _principal()
    app.dependency_overrides[current_principal] = lambda: p

    async def fake_list(**kw):
        return [{"id": "t1", "title": "Boilers"}]

    monkeypatch.setattr(ct, "list_threads", fake_list)
    monkeypatch.setattr(ct, "_ready", True)
    r = c.get("/api/threads")
    assert r.status_code == 200 and r.json()["threads"][0]["id"] == "t1"
    assert c.get("/api/threads", params={"organization_id": str(uuid.uuid4())}).status_code == 403

    async def fake_get(tid):
        return None

    monkeypatch.setattr(ct, "get_thread", fake_get)
    assert c.get("/api/threads/nope").status_code == 404


def test_the_list_pages_on_from_the_last_thread_it_returned(db):
    # The Sessions page reads the whole history a page at a time (7 Oct 2026): `before` is the
    # last row's last_message_at, and the next page starts AT it: threads sharing that time with
    # the last row were skipped by a strict "<" (8 Oct 2026). The client drops the repeats.
    p = _principal()
    caller_principal.set(p)
    caller_organization_id.set(None)
    asyncio.run(ct.list_threads(limit=200, before="2026-10-01T09:30:00+00:00"))
    sql, params = db["sql"][-1]
    assert "last_message_at <= CAST(:before AS timestamptz)" in sql
    # Bound as a datetime: asyncpg types the parameter as timestamptz from the CAST and refuses
    # a string ("expected a datetime.date or datetime.datetime instance"), so a string cursor
    # was a 500 on every second page and the history stopped at the newest 200 (8 Oct 2026).
    from datetime import datetime, timezone
    assert params["before"] == datetime(2026, 10, 1, 9, 30, tzinfo=timezone.utc)
    assert isinstance(params["before"], datetime) and params["lim"] == 200
    asyncio.run(ct.list_threads(limit=200, before="2026-10-01T09:30:00Z"))
    assert db["sql"][-1][1]["before"] == datetime(2026, 10, 1, 9, 30, tzinfo=timezone.utc)
    asyncio.run(ct.list_threads(limit=200))
    sql, params = db["sql"][-1]
    assert ":before" not in sql and "before" not in params


def test_a_cursor_that_is_not_a_time_is_ignored(db):
    caller_principal.set(_principal())
    caller_organization_id.set(None)
    asyncio.run(ct.list_threads(before="not-a-time'; drop table x;--"))
    sql, params = db["sql"][-1]
    assert ":before" not in sql and "before" not in params
