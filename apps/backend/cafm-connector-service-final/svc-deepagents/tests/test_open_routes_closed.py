"""The routes that answered anybody now ask who is calling.

The activity log holds every input and output of a conversation, and it — with the workspace
and status of any session, the resume of any interrupted run and the ingest batch list — was
served to any caller with no token at all. A restriction on what a signed-in person may see
means nothing beside a route that needs no sign-in, so each now resolves the caller, and a
conversation is read only by its owner (or their company's admin, or a superadmin).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute

from src.agents import page_access as PA
from src.api.routes import ingest_batch as ingest_batch_routes
from src.api.routes import workflow as W
from src.services import chat_threads as ct
from src.services import principal as P

ORG, OTHER_ORG = str(uuid4()), str(uuid4())
CUT = datetime(2026, 11, 12, 9, 30, tzinfo=timezone.utc)


def caller(role="user", org=ORG, pages=None, narrowed_at=None):
    return P.Principal(user_id=uuid4(), email="x@example.com", organization_id=org, role=role,
                       can_ingest=False, building_ids=None, pages=pages, access_narrowed_at=narrowed_at)


def _carries(dependant, target) -> bool:
    return dependant.call is target or any(_carries(d, target) for d in dependant.dependencies)


def route(router, method, path):
    for r in router.routes:
        if isinstance(r, APIRoute) and r.path == path and method in r.methods:
            return r
    raise AssertionError(f"no route {method} {path}")


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/workflow/activity"), ("POST", "/api/workflow/activity"),
    ("GET", "/api/workflow/activity/{session_id}"), ("GET", "/api/workflow/activity/{session_id}/turns"),
    ("GET", "/api/workflow/workspace/{session_id}"), ("GET", "/api/workflow/status/{session_id}"),
    ("POST", "/api/workflow/resume/{session_id}"),
])
def test_the_route_needs_a_signed_in_caller(method, path):
    assert _carries(route(W.router, method, path).dependant, P.current_principal), (method, path)


def test_the_ingest_batch_routes_need_a_signed_in_caller():
    routes = [r for r in ingest_batch_routes.router.routes if isinstance(r, APIRoute)]
    assert len(routes) >= 3
    for r in routes:
        assert _carries(r.dependant, P.current_principal), r.path


# ── whose conversation is it ──────────────────────────────────────────────────

@pytest.fixture
def owned_by(monkeypatch):
    def use(owner):
        async def _owner(_session_id):
            return owner
        monkeypatch.setattr(ct, "session_owner", _owner)
    return use


async def guard(principal, *, unknown_ok=False):
    await W._session_guard("s1", principal, unknown_ok=unknown_ok)


async def refused(principal, **kw) -> bool:
    try:
        await guard(principal, **kw)
    except HTTPException as exc:
        assert exc.status_code == 404
        return True
    return False


async def test_a_person_reads_their_own_conversation(owned_by):
    me = caller()
    owned_by({"user_id": str(me.user_id), "organization_id": ORG})
    assert not await refused(me)


async def test_a_colleagues_conversation_is_not_theirs_to_read(owned_by):
    owned_by({"user_id": str(uuid4()), "organization_id": ORG})
    assert await refused(caller())
    assert not await refused(caller(role="admin"))            # their company's admin may
    assert not await refused(caller(role="superadmin", org=None))


async def test_another_companys_conversation_is_nobody_elses(owned_by):
    owned_by({"user_id": str(uuid4()), "organization_id": OTHER_ORG})
    assert await refused(caller())
    assert await refused(caller(role="admin"))
    assert not await refused(caller(role="superadmin", org=None))


async def test_a_session_with_no_record(owned_by):
    owned_by(None)
    # Its activity is nobody's to read: nothing shows whose it is.
    assert await refused(caller()) and await refused(caller(role="admin"))
    # Its workspace and status are polled before the first question is recorded.
    assert not await refused(caller(), unknown_ok=True)
    assert not await refused(caller(role="superadmin", org=None))


async def test_the_list_of_every_session_is_the_platforms(monkeypatch):
    async def _recent(limit=50):
        return [{"session_id": "s1"}]
    monkeypatch.setattr(W.activity_log, "recent_sessions", _recent)
    for who in (caller(), caller(role="admin")):
        with pytest.raises(HTTPException) as e:
            await W.activity_sessions(limit=5, principal=who)
        assert e.value.status_code == 403
    out = await W.activity_sessions(limit=5, principal=caller(role="superadmin", org=None))
    assert out["sessions"] == [{"session_id": "s1"}]


def test_activity_from_before_the_narrowing_is_not_shown():
    rows = [{"id": 1, "created_at": (CUT - timedelta(hours=1)).isoformat()},
            {"id": 2, "created_at": CUT + timedelta(hours=1)}, {"id": 3, "created_at": None}]
    held = caller(pages=frozenset({"compliance"}), narrowed_at=CUT)
    assert [r["id"] for r in W._since_cut(held, rows)] == [2]
    assert W._since_cut(caller(), rows) is rows
    assert W._since_cut(caller(pages=frozenset({"compliance"})), rows) is rows


# ── resuming a run is done as the caller ──────────────────────────────────────

async def test_resume_runs_as_the_caller_and_under_their_pages(owned_by, monkeypatch):
    me = caller(pages=frozenset({"compliance"}))
    owned_by({"user_id": str(me.user_id), "organization_id": ORG})
    seen = {}

    class Orchestrator:
        async def resume(self, session_id, decision):
            seen["pages"] = PA.current()
            seen["caller"] = P.caller_principal.get()
            from src.http_client import caller_authorization
            seen["authorization"] = caller_authorization.get()
            return {"session_id": session_id, "answer": "ok", "tool_calls": [], "success": True}

    monkeypatch.setattr(W, "_to_response", lambda result: result)
    token = PA.active_pages.set(PA.FROM_CALLER)
    try:
        request = SimpleNamespace(headers={"authorization": "Bearer abc"})
        out = await W.resume_workflow("s1", W.ResumeRequest(decision={"approved": True}), request,
                                      principal=me, orchestrator=Orchestrator())
    finally:
        PA.active_pages.reset(token)
    assert out["answer"] == "ok"
    assert seen["caller"] is me and seen["pages"] == frozenset({"compliance"})
    assert seen["authorization"] == "Bearer abc"


async def test_resume_of_somebody_elses_run_is_refused(owned_by):
    owned_by({"user_id": str(uuid4()), "organization_id": OTHER_ORG})
    with pytest.raises(HTTPException) as e:
        await W.resume_workflow("s1", W.ResumeRequest(decision={}), SimpleNamespace(headers={}),
                                principal=caller(), orchestrator=SimpleNamespace())
    assert e.value.status_code == 404
