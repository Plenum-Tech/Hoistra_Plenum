"""What was said before a person's pages were narrowed is not shown to them again.

A stored answer may hold data from a page that has since been taken away, and earlier turns
are fed back into later ones. So a caller held to some pages reads — and the model is given —
only the turns, memories and documents that are theirs to see now. Nothing is deleted: a
caller given every page again sees everything again.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src import database
from src.api.routes import documents as documents_routes
from src.api.routes import traces as traces_routes
from src.agents import page_access as PA
from src.services import chat_memories as cm
from src.services import chat_threads as ct
from src.services import principal as P

CUT = datetime(2026, 11, 12, 9, 30, tzinfo=timezone.utc)
BEFORE, AFTER = CUT - timedelta(days=3), CUT + timedelta(hours=2)
ORG = str(uuid4())
COMPLIANCE = frozenset({"compliance", "buildings"})


def caller(pages=None, narrowed_at=None, role="user"):
    return P.Principal(user_id=uuid4(), email="x@example.com", organization_id=ORG, role=role,
                       can_ingest=False, building_ids=None, pages=pages,
                       access_role="general_user" if pages is not None else None,
                       access_narrowed_at=narrowed_at)


class _Row(dict):
    """A row read by name or, as a plain result row is, by position."""

    def __getitem__(self, key):
        return list(self.values())[key] if isinstance(key, int) else super().__getitem__(key)


class _Result:
    def __init__(self, rows):
        self._rows = [_Row(r) for r in rows]

    def mappings(self):
        return self

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return list(self._rows)


class _Conn:
    def __init__(self, engine):
        self.engine = engine

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        self.engine.calls.append((sql, dict(params or {})))
        for needle, rows in self.engine.script:
            if needle in sql:
                return _Result(rows)
        return _Result([])


class _Engine:
    def __init__(self, script):
        self.script, self.calls = script, []

    def connect(self):
        return _Conn(self)

    def sql(self, needle):
        return [(s, p) for s, p in self.calls if needle in s]


@pytest.fixture
def store(monkeypatch):
    def use(script):
        engine = _Engine(script)
        monkeypatch.setattr(database, "_get_engine", lambda: engine)
        monkeypatch.setattr(ct, "_ready", True)
        return engine
    return use


@pytest.fixture(autouse=True)
def _signed_out():
    token = P.caller_principal.set(None)
    pages = PA.active_pages.set(PA.FROM_CALLER)
    yield
    P.caller_principal.reset(token)
    PA.active_pages.reset(pages)


def as_(p):
    P.caller_principal.set(p)
    return p


def ws_row(created):
    return {"working_set": {"vendor": "Acme"}, "created_at": created}


WS = "SELECT working_set"


def head(created):
    return {"id": "s1", "title": "t", "building_id": None, "turn_count": 5, "created_at": created,
            "last_message_at": AFTER, "summary": "the earlier energy figures", "summary_through": 3,
            "working_set": {"vendor": "Acme"}}


TURN = {"turn_no": 4, "question": "q", "answer": "a", "tools": [], "citations": [], "route": "r",
        "created_at": AFTER, "answered_at": AFTER}


# ── reading a thread ──────────────────────────────────────────────────────────

async def test_turns_before_narrowing_are_not_read(store):
    as_(caller(COMPLIANCE, CUT))
    engine = store([("FROM " + ct.THREADS, [head(BEFORE)]), ("FROM " + ct.TURNS, [TURN])])
    out = await ct.get_thread("s1")
    (sql, params), = engine.sql("FROM " + ct.TURNS)
    assert "created_at > CAST(:cut AS timestamptz)" in sql and params["cut"] == CUT
    assert out["summary"] is None                     # it folds the turns that are now hidden
    assert out["turn_count"] == 1


async def test_a_thread_begun_after_the_narrowing_keeps_its_summary(store):
    as_(caller(COMPLIANCE, CUT))
    store([("FROM " + ct.THREADS, [head(AFTER)]), ("FROM " + ct.TURNS, [TURN])])
    out = await ct.get_thread("s1")
    assert out["summary"] == "the earlier energy figures"


async def test_threads_before_narrowing_are_not_listed(store):
    as_(caller(COMPLIANCE, CUT))
    engine = store([("FROM " + ct.THREADS, [head(BEFORE)])])
    await ct.list_threads(q="energy")
    (sql, params), = engine.sql("FROM " + ct.THREADS)
    assert "last_message_at > CAST(:cut AS timestamptz)" in sql and params["cut"] == CUT
    assert "summary ILIKE" not in sql                 # an old summary is not searchable either
    assert "CASE WHEN created_at > CAST(:cut AS timestamptz)" in sql


async def test_normal_user_history_is_unchanged(store):
    for who in (caller(), caller(role="admin"), caller(COMPLIANCE, None)):
        as_(who)
        engine = store([(WS, [ws_row(BEFORE)]), ("FROM " + ct.THREADS, [head(BEFORE)]),
                        ("FROM " + ct.TURNS, [TURN])])
        out = await ct.get_thread("s1")
        await ct.list_threads(q="energy")
        await ct.conversation_context("s1")
        await ct.last_turn("s1")
        assert await ct.working_set("s1") == {"vendor": "Acme"}
        assert not [s for s, p in engine.calls if ":cut" in s or "cut" in p or "created_at" in s.split("FROM")[0]
                    and ("SELECT working_set" in s or "SELECT summary, summary_through" in s)]
        assert out["summary"] == "the earlier energy figures" and out["turn_count"] == 5
        assert any("summary ILIKE" in s for s, _ in engine.calls)


# ── what is fed back into the next turn ───────────────────────────────────────

async def test_turns_before_narrowing_do_not_enter_context(store, monkeypatch):
    as_(caller(COMPLIANCE, CUT))
    seen = {}
    monkeypatch.setattr(ct, "format_context", lambda summary, turns: seen.update(summary=summary, turns=turns) or "ctx")
    engine = store([("FROM " + ct.THREADS, [head(BEFORE)]), ("FROM " + ct.TURNS, [TURN])])
    assert await ct.conversation_context("s1") == "ctx"
    (sql, params), = engine.sql("FROM " + ct.TURNS)
    assert "created_at > CAST(:cut AS timestamptz)" in sql and params["cut"] == CUT
    assert params["after"] == 0                       # not "after the summary": the summary is gone
    assert seen["summary"] is None


async def test_context_is_whole_for_a_thread_begun_after_the_narrowing(store, monkeypatch):
    as_(caller(COMPLIANCE, CUT))
    seen = {}
    monkeypatch.setattr(ct, "format_context", lambda summary, turns: seen.update(summary=summary) or "ctx")
    engine = store([("FROM " + ct.THREADS, [head(AFTER)]), ("FROM " + ct.TURNS, [TURN])])
    await ct.conversation_context("s1")
    assert seen["summary"] == "the earlier energy figures"
    assert engine.sql("FROM " + ct.TURNS)[0][1]["after"] == 3


async def test_last_turn_and_working_set_follow_the_cutoff(store):
    as_(caller(COMPLIANCE, CUT))
    engine = store([(WS, [ws_row(BEFORE)]), ("FROM " + ct.TURNS, [TURN])])
    await ct.last_turn("s1")
    assert "created_at > CAST(:cut AS timestamptz)" in engine.sql("FROM " + ct.TURNS)[0][0]
    assert await ct.working_set("s1") is None         # built from the turns that are now hidden
    store([(WS, [ws_row(AFTER)])])
    assert await ct.working_set("s1") == {"vendor": "Acme"}


# ── memories ──────────────────────────────────────────────────────────────────

def test_a_normal_users_memories_are_what_they_were():
    p = caller()
    where, params = cm._visible_sql(p, ORG)
    assert where == ("organization_id = CAST(:org AS uuid) AND deleted_at IS NULL"
                     " AND (user_id IS NULL OR user_id = CAST(:uid AS uuid)) AND status = 'active'")
    assert set(params) == {"uid", "org"}


def test_role_user_recalls_only_own_memories_after_narrowing():
    where, params = cm._visible_sql(caller(COMPLIANCE, CUT), ORG)
    assert "created_by = CAST(:uid AS uuid)" in where
    assert "created_at > CAST(:cut AS timestamptz)" in where and params["cut"] == CUT
    where, params = cm._visible_sql(caller(COMPLIANCE, None), ORG)
    assert "created_by = CAST(:uid AS uuid)" in where and "cut" not in params


def test_memories_list_follows_the_same_rule():
    where, _ = cm._visible_sql(caller(COMPLIANCE, CUT), ORG, include_pending=True)
    assert "created_by = CAST(:uid AS uuid)" in where and "created_at > CAST(:cut" in where
    where, _ = cm._visible_sql(caller(COMPLIANCE, CUT), None)
    assert "created_by = CAST(:uid AS uuid)" in where and "created_at > CAST(:cut" in where


# ── traces ────────────────────────────────────────────────────────────────────

def test_trace_reads_start_at_the_narrowing():
    p = caller(COMPLIANCE, CUT)
    assert traces_routes._floor(p, None) == CUT
    assert traces_routes._floor(p, BEFORE) == CUT
    assert traces_routes._floor(p, AFTER) == AFTER
    for who in (caller(), caller(COMPLIANCE, None)):
        assert traces_routes._floor(who, None) is None
        assert traces_routes._floor(who, BEFORE) == BEFORE


def test_a_trace_from_before_the_narrowing_is_not_opened():
    p = caller(COMPLIANCE, CUT)
    assert traces_routes._hidden(p, {"started_at": BEFORE.isoformat()})
    assert traces_routes._hidden(p, {"started_at": BEFORE})
    assert traces_routes._hidden(p, {"started_at": None})          # undated: cannot be shown recent
    assert not traces_routes._hidden(p, {"started_at": AFTER.isoformat()})
    assert not traces_routes._hidden(caller(), {"started_at": BEFORE.isoformat()})


# ── documents ─────────────────────────────────────────────────────────────────

def test_document_link_refused_for_unheld_type():
    held = caller(COMPLIANCE)
    assert documents_routes._page_allows(held, {"doc_type": "compliance_certificate"})
    assert documents_routes._page_allows(held, {"doc_type": "drawing"})
    assert not documents_routes._page_allows(held, {"doc_type": "contract"})
    assert not documents_routes._page_allows(held, {"doc_type": "invoice"})
    assert documents_routes._page_allows(caller(), {"doc_type": "contract"})
    assert not documents_routes._page_allows(held, None)           # no register row: not theirs
    assert documents_routes._page_allows(caller(), None)
