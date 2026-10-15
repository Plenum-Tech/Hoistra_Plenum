"""Chat memories (phase B): what is kept, who it belongs to, how it is recalled and forgotten."""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone

import pytest

from src.http_client import caller_organization_id
from src.services import chat_memories as cm
from src.services.principal import Principal, caller_principal


def _principal(role="user", org=None, uid=None):
    return Principal(user_id=uid or uuid.uuid4(), email="fm@example.com", organization_id=org or uuid.uuid4(), role=role,
                     can_ingest=True, building_ids=None)


class _Result:
    def __init__(self, rows=None, rowcount=0):
        self._rows, self.rowcount = rows or [], rowcount

    def mappings(self):
        return self

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


class _Conn:
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
    monkeypatch.setattr(cm, "_ready", True)

    async def no_embed(texts):
        return None

    monkeypatch.setattr(cm, "embed", no_embed)
    return store


def test_the_extraction_is_kept_to_what_the_rules_allow():
    items = cm.clean([
        {"kind": "fact", "text": "At Bishopsgate Tower, AHU-3 is the unit tenants call 'the big unit'.", "subject": "AHU-3"},
        {"kind": "preference", "text": "The user prefers costs in AED."},
        {"kind": "fact", "text": "There were 794 work orders in September."},          # computed figures slip through the
        {"kind": "fact", "text": "The site manager's passport number is N1234567."},   # model sometimes; the guard catches ids
        {"kind": "fact", "text": "Call him on 050 123 4567 for access."},
        {"kind": "opinion", "text": "Apex seem slow."},                                 # not a kind we keep
        {"kind": "correction", "text": "short"},
    ])
    assert [i["kind"] for i in items] == ["fact", "preference", "fact"]
    assert items[2]["text"].startswith("There were 794")   # figures are the model's job to leave out; the prompt says so
    assert all(not cm.looks_personal(i["text"]) for i in items)


def test_a_preference_is_the_persons_and_a_fact_is_the_companys(db):
    p = _principal(role="superadmin")
    acting = str(uuid.uuid4())
    n = asyncio.run(cm.store([{"kind": "preference", "text": "The user prefers costs in AED.", "subject": None},
                              {"kind": "fact", "text": "At Bishopsgate Tower the plant room is on level 3.", "subject": "Bishopsgate Tower"}],
                             principal=p, org=acting, source_thread="s1", source_turn=2))
    assert n == 2
    inserts = [x for x in db["sql"] if "INSERT INTO plenum_cafm.chat_memories" in x[0]]
    assert inserts[0][1]["uid"] == str(p.user_id) and inserts[0][1]["org"] == acting       # mine
    assert inserts[1][1]["uid"] is None and inserts[1][1]["by"] == str(p.user_id)           # the company's, signed


def test_a_near_duplicate_refreshes_the_existing_memory_instead_of_inserting(db):
    existing = [{"id": str(uuid.uuid4()), "kind": "fact", "text": "At Bishopsgate Tower the plant room is on level 3.",
                 "embedding": None, "user_id": None}]
    db["answers"] = [("FROM plenum_cafm.chat_memories WHERE", existing)]
    p = _principal()
    caller_principal.set(p)
    caller_organization_id.set(None)
    n = asyncio.run(cm.store([{"kind": "fact", "text": "at bishopsgate tower the plant room is on level 3.", "subject": None}],
                             principal=p, org=str(p.organization_id)))
    assert n == 0
    assert any("use_count = use_count + 1" in x[0] and x[1].get("id") == existing[0]["id"] for x in db["sql"])


def test_recall_ranks_by_the_question_and_tells_the_model_to_reread_figures(db):
    p = _principal()
    caller_principal.set(p)
    caller_organization_id.set(None)
    now = datetime(2026, 10, 1, tzinfo=timezone.utc)
    mems = [{"id": "m1", "kind": "fact", "text": "At Bishopsgate Tower the boiler plant room is on level 3, not the basement.",
             "embedding": None, "user_id": None, "created_by_email": "ops@example.com", "created_at": now},
            {"id": "m2", "kind": "preference", "text": "The user prefers costs in AED.", "embedding": None,
             "user_id": str(p.user_id), "created_by_email": "fm@example.com", "created_at": now},
            {"id": "m3", "kind": "fact", "text": "Talvern Lifts invoices arrive on the fifth.", "embedding": None,
             "user_id": None, "created_by_email": None, "created_at": now}]
    db["answers"] = [("FROM plenum_cafm.chat_memories WHERE", mems)]
    hits = asyncio.run(cm.recall("Where is the boiler plant room at Bishopsgate Tower?"))
    assert [h["id"] for h in hits] == ["m1"]
    block = cm.format_recall(hits)
    assert block.startswith("## What I remember") and "re-read live data" in block
    assert "(fact, company-wide; from ops@example.com, 01 Oct 2026)" in block
    assert any("last_used_at = now()" in x[0] and x[1]["ids"] == ["m1"] for x in db["sql"])
    # The scope: the company's shared rows plus this person's own.
    sel = next(x for x in db["sql"] if "FROM plenum_cafm.chat_memories WHERE" in x[0])
    assert "(user_id IS NULL OR user_id = CAST(:uid AS uuid))" in sel[0] and sel[1]["org"] == str(p.organization_id)


def test_what_do_you_remember_and_forget_are_answered_without_a_model(db):
    p = _principal()
    caller_principal.set(p)
    caller_organization_id.set(None)
    assert asyncio.run(cm.shortcut("How many boilers at B-301?", "s")) is None
    db["answers"] = [("FROM plenum_cafm.chat_memories WHERE", [])]
    out = asyncio.run(cm.shortcut("What do you remember about us?", "s"))
    assert out["success"] and out["route_intent"] == "memory" and out["answer"].startswith("Nothing yet")
    mems = [{"id": str(uuid.uuid4()), "kind": "preference", "text": "The user prefers costs in AED.", "embedding": None,
             "user_id": str(p.user_id), "created_by_email": "fm@example.com"},
            {"id": str(uuid.uuid4()), "kind": "fact", "text": "Talvern Lifts invoices arrive on the fifth.", "embedding": None,
             "user_id": None, "created_by_email": "ops@example.com"}]
    db["answers"] = [("FROM plenum_cafm.chat_memories WHERE", mems)]
    out = asyncio.run(cm.shortcut("what do you remember about me", "s"))
    assert "**Your preferences**" in out["answer"] and "costs in AED" in out["answer"]
    assert "shared with colleagues" in out["answer"] and "_(from ops@example.com)_" in out["answer"]
    out = asyncio.run(cm.shortcut("forget that I prefer AED", "s"))
    assert out["answer"].startswith('Forgotten: "The user prefers costs in AED."')
    upd = next(x for x in db["sql"] if "SET deleted_at = now()" in x[0])
    assert upd[1]["id"] == mems[0]["id"]
    # Not an admin: a colleague's company fact is not theirs to remove, and the SQL says so.
    assert "(user_id = CAST(:uid AS uuid) OR created_by = CAST(:uid AS uuid))" in upd[0]
    out = asyncio.run(cm.shortcut("forget the moon landing", "s"))
    assert out["answer"].startswith("I couldn't find a memory matching")


def test_nothing_signed_in_or_store_down_never_raises(db, monkeypatch):
    caller_principal.set(None)
    assert asyncio.run(cm.recall("anything")) == []
    assert asyncio.run(cm.extract_and_store("s", "q", "a")) == 0
    monkeypatch.setattr(cm, "_ready", False)
    assert asyncio.run(cm.shortcut("what do you remember", "s")) is None
    assert cm.format_recall([]) == ""


def test_the_routes_need_a_signed_in_caller_and_list_without_vectors(monkeypatch, db):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from src.api.routes import memories as routes
    from src.services.principal import current_principal

    app = FastAPI()
    app.include_router(routes.router)
    c = TestClient(app)
    assert c.get("/api/memories").status_code in (401, 403)
    p = _principal(role="admin")
    app.dependency_overrides[current_principal] = lambda: p
    db["answers"] = [("FROM plenum_cafm.chat_memories WHERE", [
        {"id": "m1", "kind": "fact", "text": "x", "embedding": json.dumps([0.1] * 3), "user_id": None,
         "created_by_email": None, "created_at": datetime(2026, 10, 1, tzinfo=timezone.utc), "last_used_at": None,
         "use_count": 0, "source_thread": "s"}])]
    r = c.get("/api/memories")
    assert r.status_code == 200 and r.json()["can_manage"] and "embedding" not in r.json()["memories"][0]
    assert r.json()["memories"][0]["created_at"].startswith("2026-10-01")



def test_a_colleagues_teaching_waits_for_an_admin_and_a_preference_does_not(db):
    user = _principal(role="user")
    caller_principal.set(user)
    caller_organization_id.set(str(user.organization_id))
    asyncio.run(cm.store([{"kind": "correction", "text": "Repurchase means parts below reorder level, not energy recommendations.", "subject": None},
                          {"kind": "preference", "text": "The user prefers costs in AED.", "subject": None}],
                         principal=user, org=str(user.organization_id)))
    inserts = [x[1] for x in db["sql"] if "INSERT INTO plenum_cafm.chat_memories" in x[0]]
    assert [i["status"] for i in inserts] == ["pending", "active"]
    assert cm.needs_approval(_principal(role="admin"), "correction") is False
    # what the chat recalls is the active set; the page sees pending ones too
    db["sql"].clear()
    asyncio.run(cm.visible())
    assert "AND status = 'active'" in db["sql"][-1][0]
    asyncio.run(cm.visible(include_pending=True))
    assert "status = 'pending' AND (created_by = CAST(:uid AS uuid) OR :admin)" in db["sql"][-1][0]


def test_only_an_admin_reviews_and_a_rejection_hides_the_teaching(db):
    user = _principal(role="user")
    caller_principal.set(user)
    caller_organization_id.set(str(user.organization_id))
    assert asyncio.run(cm.review("m-1", "approve")) is False
    admin = _principal(role="admin", org=user.organization_id)
    caller_principal.set(admin)
    assert asyncio.run(cm.review("m-1", "approve")) is True
    sql, params = db["sql"][-1]
    assert "SET status = 'active'" in sql and "status = 'pending'" in sql and params["email"] == admin.email and params["id"] == "m-1"
    assert asyncio.run(cm.review("m-2", "reject")) is True
    sql, _ = db["sql"][-1]
    assert "SET status = 'rejected', deleted_at = now()" in sql
    assert asyncio.run(cm.review("m-3", "maybe")) is False
