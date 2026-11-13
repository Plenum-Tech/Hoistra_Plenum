"""Holes the 13 Nov review of the page gate found in this service, each pinned here.

* The work-order chat read the asset register for anybody who held the maintenance page, across
  every company, and returned any company's conversation to any signed-in caller.
* /api/maintenance/ask answered from decisions the caller's pages hide.
* The inspection panel carried energy anomalies to a caller without the energy page.
* A decision carried the certificate behind it to a caller without the compliance page.
* A work order with no code was taken for another module's approvals item and dropped.
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from src.agent.tools import asset_tools as at
from src.agent.tools.executor import ToolExecutor
from src.agent.session_store import SessionStore
from src.api.routes import chat as chat_route
from src.app import app
from src.db import get_session
from src.services import ask as ask_svc
from src.services import inspection_intelligence as ii
from src.services import maintenance as mx
from src.services import principal as P

ORG = uuid4()


def caller(pages=None, role="user", org=ORG, user_id=None):
    return P.Principal(user_id=user_id or uuid4(), email="x@example.com", organization_id=org,
                       role=role, building_ids=None, pages=pages)


class Exploding:
    async def execute(self, *a, **k):
        raise AssertionError("read the database")

    async def get(self, *a, **k):
        raise AssertionError("read the database")

    def add(self, *_a):
        raise AssertionError("wrote the database")

    async def commit(self):
        pass

    async def rollback(self):
        pass


class _Result:
    def scalars(self):
        return self

    def all(self):
        return []

    def scalar_one_or_none(self):
        return None


class Recording:
    """A session that answers every read with nothing and keeps the statements it was sent."""

    def __init__(self):
        self.statements = []
        self.added = []

    async def execute(self, stmt, *a, **k):
        self.statements.append(str(stmt.compile(compile_kwargs={"literal_binds": False})))
        return _Result()

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        pass

    async def rollback(self):
        pass


def refused(out) -> bool:
    return isinstance(out, dict) and out.get("reason") == "page_not_allowed"


# ── the work-order chat's asset lookups ─────────────────────────────────────────────

@pytest.mark.parametrize("tool,args", [
    ("search_assets", {"query": "chiller"}),
    ("get_asset_details", {"asset_id": "AHU-301"}),
    ("get_asset_intelligence", {"asset_id": "AHU-301"}),
])
async def test_the_chat_does_not_read_the_asset_register_without_the_assets_page(tool, args):
    out = await ToolExecutor(Exploding(), "s", principal=caller(pages=frozenset({"ops"}))).execute(tool, args)
    assert refused(out), out


async def test_the_chat_reads_assets_for_a_holder_of_the_assets_page():
    s = Recording()
    out = await ToolExecutor(s, "s", principal=caller(pages=frozenset({"ops", "assets"}))).execute(
        "search_assets", {"query": "chiller"})
    assert not refused(out) and s.statements


@pytest.mark.parametrize("tool,args", [
    ("search_assets", {"query": "chiller"}),
    ("get_asset_details", {"asset_id": "AHU-301"}),
    ("search_locations", {"query": "roof"}),
    ("find_ppm_schedules", {"asset_id": str(uuid4())}),
])
async def test_the_chat_lookups_are_held_to_the_callers_company(tool, args):
    s = Recording()
    await ToolExecutor(s, "s", principal=caller()).execute(tool, args)
    assert s.statements, "the lookup read nothing"
    for sql in s.statements:
        assert "plenum_cafm.buildings" in sql and "organization_id" in sql, sql


async def test_a_superadmin_chat_lookup_still_reads_across_companies():
    s = Recording()
    await ToolExecutor(s, "s", principal=caller(role="superadmin")).execute(
        "search_assets", {"query": "chiller"})
    assert s.statements and "plenum_cafm.buildings" not in s.statements[0]


# ── a conversation belongs to the person who had it ─────────────────────────────────

async def test_a_new_conversation_records_who_had_it():
    s = Recording()
    me = caller()
    await SessionStore(s).create(source="chat", owner=me)
    ctx = s.added[0].context
    assert ctx["owner"] == {"user_id": str(me.user_id), "organization_id": str(ORG)}


@pytest.mark.parametrize("who,owner,visible", [
    ("self", "self", True),
    ("other user", "other", False),
    ("company admin", "other", True),
    ("admin elsewhere", "other-org", False),
    ("superadmin", "other-org", True),
    ("user", None, False),          # a conversation from before owners were recorded
    ("superadmin", None, True),
])
def test_who_may_read_a_conversation(who, owner, visible):
    me = uuid4()
    p = {"self": caller(user_id=me), "other user": caller(), "company admin": caller(role="admin"),
         "admin elsewhere": caller(role="admin", org=uuid4()),
         "superadmin": caller(role="superadmin", org=uuid4()), "user": caller()}[who]
    rec = {"self": {"user_id": str(me), "organization_id": str(ORG)},
           "other": {"user_id": str(uuid4()), "organization_id": str(ORG)},
           "other-org": {"user_id": str(uuid4()), "organization_id": str(uuid4())},
           None: None}[owner]
    assert chat_route.may_read(p, rec) is visible


@pytest.fixture
def client():
    async def _session():
        yield Exploding()
    app.dependency_overrides[get_session] = _session
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        app.dependency_overrides.clear()


def as_(p):
    async def _dep():
        return p
    app.dependency_overrides[P.current_principal] = _dep


class FakeOrchestrator:
    seen: list = []

    def __init__(self, session, principal=None):
        self.principal = principal

    async def chat(self, message, session_id=None, source="chat"):
        FakeOrchestrator.seen.append((session_id, self.principal))
        return {"session_id": session_id or "new", "reply": "ok", "work_order": None}

    async def get_history(self, session_id):
        return [{"role": "user", "content": "secret"}]


def _owned_by(monkeypatch, owner):
    async def _owner(self, session_id):
        return True, owner
    monkeypatch.setattr(SessionStore, "owner", _owner)
    monkeypatch.setattr(chat_route, "WOOrchestrator", FakeOrchestrator)


def test_another_persons_conversation_history_is_not_found(client, monkeypatch):
    _owned_by(monkeypatch, {"user_id": str(uuid4()), "organization_id": str(ORG)})
    as_(caller(pages=None))
    assert client.get("/api/chat/abc/history").status_code == 404


def test_your_own_conversation_history_is_returned(client, monkeypatch):
    me = caller()
    _owned_by(monkeypatch, {"user_id": str(me.user_id), "organization_id": str(ORG)})
    as_(me)
    r = client.get("/api/chat/abc/history")
    assert r.status_code == 200 and r.json()["count"] == 1


def test_a_message_never_continues_another_persons_conversation(client, monkeypatch):
    _owned_by(monkeypatch, {"user_id": str(uuid4()), "organization_id": str(ORG)})
    me = caller()
    as_(me)
    FakeOrchestrator.seen = []
    r = client.post("/api/chat/", json={"message": "hi", "session_id": "abc"})
    assert r.status_code == 200
    assert FakeOrchestrator.seen == [(None, me)]


def test_a_message_continues_your_own_conversation_as_you(client, monkeypatch):
    me = caller()
    _owned_by(monkeypatch, {"user_id": str(me.user_id), "organization_id": str(ORG)})
    as_(me)
    FakeOrchestrator.seen = []
    client.post("/api/chat/", json={"message": "hi", "session_id": "abc"})
    assert FakeOrchestrator.seen == [("abc", me)]


# ── /api/maintenance/ask answers from what the caller's pages show ──────────────────

async def test_ask_reads_decisions_under_the_callers_pages(monkeypatch):
    seen = {}

    async def _decisions(session, **kw):
        seen.update(kw)
        return {"total": 0, "by_state": {}, "decisions": [], "count": 0}
    monkeypatch.setattr(mx, "decisions", _decisions)
    await ask_svc.ask(Exploding(), question="What decisions are owed?", building_ids=None,
                      pages=frozenset({"ops"}))
    assert seen.get("pages") == frozenset({"ops"})


async def test_ask_for_a_caller_with_every_page_is_the_call_it_always_was(monkeypatch):
    seen = {}

    async def _decisions(session, **kw):
        seen.update(kw)
        return {"total": 0, "by_state": {}, "decisions": [], "count": 0}
    monkeypatch.setattr(mx, "decisions", _decisions)
    await ask_svc.ask(Exploding(), question="What decisions are owed?", building_ids=None)
    assert "pages" not in seen


def test_the_ask_route_hands_over_the_callers_pages(client, monkeypatch):
    seen = {}

    async def _ask(session, **kw):
        seen.update(kw)
        return {"ok": True}

    async def _scope(*a, **k):
        return []
    from src.api.routes import maintenance as route
    monkeypatch.setattr(route.ask_svc, "ask", _ask)
    monkeypatch.setattr(route, "_scope", _scope)
    as_(caller(pages=frozenset({"ops"})))
    client.post("/api/maintenance/ask", json={"question": "what decisions are owed"})
    assert seen.get("pages") == frozenset({"ops"})


# ── energy anomalies on the inspection panel need the energy page ───────────────────

async def test_corroborated_anomalies_withheld_without_energy():
    out = await ii.corroborated_anomalies(Exploding(), building_ids=None,
                                          pages=frozenset({"ops"}))
    assert out["answerable"] is False and out.get("anomalies") is None
    assert out["reason"] == "not part of the pages you have access to"


def test_unconverted_recommendations_lose_the_energy_figures_without_energy():
    card = {"answerable": True, "count": 2, "now_flagged_by_energy": 1,
            "headline": "2 recommendations never converted to orders — 1 on assets now flagged "
                        "by energy, so the inspector saw it first",
            "reports": [{"id": "a", "now_flagged_by_energy": True},
                        {"id": "b", "now_flagged_by_energy": False}]}
    out = ii.held_unconverted(card, frozenset({"ops"}))
    assert "now_flagged_by_energy" not in out
    assert all("now_flagged_by_energy" not in r for r in out["reports"])
    assert out["headline"] == "2 recommendations never converted to orders"
    assert ii.held_unconverted(card, None) is card
    assert ii.held_unconverted(card, frozenset({"ops", "energy"})) is card


async def test_the_panel_does_not_read_anomalies_without_energy(monkeypatch):
    async def _card(*a, **k):
        return {"answerable": True, "count": 0, "reports": [], "headline": "0",
                "now_flagged_by_energy": 0}

    async def _boom(*a, **k):
        raise AssertionError("read the anomalies")

    async def _corpus(*a, **k):
        return {"reports": 0, "since": None}
    for name in ("unconverted_recommendations", "warranted_findings", "poorly_graded"):
        monkeypatch.setattr(ii, name, _card)
    monkeypatch.setattr(ii, "corpus", _corpus)
    monkeypatch.setattr(ii, "_corroborated_rows", _boom, raising=False)
    out = await ii.panel(Exploding(), building_ids=None, pages=frozenset({"ops"}))
    assert out["cards"]["corroborated_anomalies"]["answerable"] is False
    assert "now_flagged_by_energy" not in out["cards"]["unconverted_recommendations"]


def test_the_anomalies_card_refuses_a_caller_without_energy(client, monkeypatch):
    async def _scope(*a, **k):
        return []
    from src.api.routes import maintenance as route
    monkeypatch.setattr(route, "_scope", _scope)
    as_(caller(pages=frozenset({"ops"})))
    r = client.get("/api/maintenance/inspection-intelligence/corroborated-anomalies")
    assert r.status_code == 403
    assert r.json()["detail"]["reason"] == "page_not_allowed"
    assert r.json()["detail"]["page"] == "energy"


# ── the certificate behind a decision needs the compliance page ─────────────────────

def _statutory_rows():
    return [{"work_order": "WO-1", "asset_id": "A1", "vendor_id": None}]


LOOKUP = {"asset:A1": {"certificate": "GS-1", "certificate_type": "GAS", "expires": "2026-11-20",
                       "lapsed": False}}


def test_the_certificate_behind_a_decision_is_withheld_without_compliance():
    from datetime import date
    rows = _statutory_rows()
    assert mx.mark_statutory_rows(rows, LOOKUP, today=date(2026, 11, 13),
                                  pages=frozenset({"ops"})) == 1
    assert rows[0]["statutory"] is True and rows[0]["statutory_certificate"] is None


def test_the_certificate_behind_a_decision_shows_with_compliance_or_every_page():
    from datetime import date
    for pages in (None, frozenset({"ops", "compliance"})):
        rows = _statutory_rows()
        mx.mark_statutory_rows(rows, LOOKUP, today=date(2026, 11, 13), pages=pages)
        assert rows[0]["statutory_certificate"]["certificate"] == "GS-1"


# ── a work order is the maintenance page's row whatever its code ────────────────────

def test_a_work_order_with_no_code_is_still_a_work_order():
    rows = [{"work_order": None, "source": "Vendors", "state": "Blocked"},
            {"work_order": None, "queue_item": "q1", "source": "Vendors", "state": "To raise"}]
    kept = mx.held_decisions(rows, frozenset({"ops"}))
    assert [d["state"] for d in kept] == ["Blocked"]
