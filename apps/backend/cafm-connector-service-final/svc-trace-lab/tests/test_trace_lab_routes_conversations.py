"""Trace Lab conversation routes: GET only, a reviewer of the company first, then readiness, the house error
envelope, and their registration in trace_lab.app. Loaders are faked; no database, no network."""
from __future__ import annotations

import uuid

import fastapi.params
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from src.services.principal import Principal, current_principal
from trace_lab import auth, db
from trace_lab import conversations as cv
from trace_lab import routes_conversations as routes

ORG = "11111111-1111-4111-8111-111111111111"
PATHS = ["/api/trace-lab/conversations", "/api/trace-lab/conversations/updates?watermark=2026-10-15T12:00:00Z",
         "/api/trace-lab/conversations/facets", "/api/trace-lab/conversations/s%3Aabc", "/api/trace-lab/turns/turn-1",
         "/api/trace-lab/runs", "/api/trace-lab/model-calls", "/api/trace-lab/model-calls/" + str(uuid.uuid4())]


def principal(role="admin", org=ORG):
    return Principal(user_id=uuid.uuid4(), email=f"{role}@example.com", organization_id=uuid.UUID(org) if org else None,
                     role=role, can_ingest=True, building_ids=None)


def client(who, *, ready=True):
    async def fake_ready():
        if not ready:
            raise HTTPException(status_code=503, detail={"reason": "trace_lab_unavailable", "error": "Not ready."})

    app = FastAPI()
    app.include_router(routes.router, prefix="/api/trace-lab")
    app.dependency_overrides[db.require_ready] = fake_ready
    if who is not None:
        app.dependency_overrides[current_principal] = lambda: who
    return TestClient(app)


def fake_loaders(monkeypatch):
    seen = {}

    async def ok(name, *a, **kw):
        seen[name] = (a, kw)
        return {"ok": True, "name": name}

    for name in ("list_conversations", "conversation_updates", "facets", "list_runs", "list_model_calls"):
        monkeypatch.setattr(cv, name, lambda *a, _n=name, **kw: ok(_n, *a, **kw))

    async def conv(*a, **kw):
        seen["get_conversation"] = (a, kw)
        return {"key": a[2]}

    async def turn(*a, **kw):
        return {"trace": {"id": a[2]}, "conversation_key": "s:abc"}

    async def call(*a, **kw):
        return {"model_call": {"span_id": a[2]}, "turn_id": "turn-1", "conversation_key": "s:abc"}

    monkeypatch.setattr(cv, "get_conversation", conv)
    monkeypatch.setattr(cv, "get_turn", turn)
    monkeypatch.setattr(cv, "get_model_call", call)
    return seen


def test_the_phase_2_names_these_routes_build_on():
    # P2-T1/T2/T7 own these; if one is missing or shaped differently, stop and report (never redefine it here).
    assert isinstance(auth.caller, fastapi.params.Depends) and auth.caller.dependency is current_principal
    assert callable(auth.require_reviewer) and callable(db.require_ready) and callable(db.http_error)
    e = db.http_error(404, "x_not_found", "Not here.", extra_key=1)
    assert isinstance(e, HTTPException) and e.status_code == 404
    assert e.detail["reason"] == "x_not_found" and e.detail["error"] == "Not here." and e.detail["extra_key"] == 1


def test_every_route_is_a_get_behind_the_reviewer_check():
    paths = set()
    for r in routes.router.routes:
        assert r.methods == {"GET"}, r.path
        assert routes.reviewer in [d.call for d in r.dependant.dependencies], r.path
        paths.add(r.path)
    assert paths == {"/conversations", "/conversations/updates", "/conversations/facets", "/conversations/{key:path}",
                     "/turns/{turn_id}", "/runs", "/model-calls", "/model-calls/{span_id}"}


def test_the_router_is_registered_in_the_trace_lab_app():
    from trace_lab import app as app_module

    assert routes.router in app_module.ROUTERS
    c = TestClient(app_module.app)              # no "with": the lifespan (engines, schema check) never runs
    assert c.get("/api/trace-lab/no-such-route").status_code == 404
    for path in ("/api/trace-lab/conversations", "/api/trace-lab/model-calls/" + str(uuid.uuid4())):
        assert c.get(path).status_code != 404, path       # served (401 without a bearer)


def test_routes_need_a_signed_in_caller(monkeypatch):
    fake_loaders(monkeypatch)
    c = client(None)
    for p in PATHS:
        assert c.get(p).status_code == 401, p


def test_plain_users_get_403(monkeypatch):
    fake_loaders(monkeypatch)
    c = client(principal("user"))
    for p in PATHS:
        r = c.get(p)
        assert r.status_code == 403 and r.json()["detail"]["reason"] == "reviewers_only", p


def test_naming_another_company_is_403_unless_superadmin(monkeypatch):
    seen = fake_loaders(monkeypatch)
    other = str(uuid.uuid4())
    assert client(principal("admin")).get(f"/api/trace-lab/conversations?organization_id={other}").status_code == 403
    r = client(principal("superadmin")).get(f"/api/trace-lab/conversations?organization_id={other}")
    assert r.status_code == 200 and seen["list_conversations"][0][1] == other


def test_a_platform_superadmin_must_name_a_company(monkeypatch):
    fake_loaders(monkeypatch)
    r = client(principal("superadmin", org=None)).get("/api/trace-lab/conversations")
    assert r.status_code == 400 and r.json()["detail"]["reason"] == "name_a_company"


def test_routes_answer_503_while_trace_lab_is_not_ready_but_only_to_reviewers(monkeypatch):
    fake_loaders(monkeypatch)
    c = client(principal("admin"), ready=False)
    for p in PATHS:
        assert c.get(p).status_code == 503, p
    assert client(principal("user"), ready=False).get(PATHS[0]).status_code == 403


def test_query_errors_are_400_with_a_reason(monkeypatch):
    c = client(principal("admin"))
    r = c.get("/api/trace-lab/conversations?reviewed=maybe")
    assert r.status_code == 400 and r.json()["detail"]["reason"] == "bad_reviewed"
    assert c.get("/api/trace-lab/conversations?since=2024-01-01T00:00:00Z").json()["detail"]["reason"] == "window_too_wide"
    r = c.get("/api/trace-lab/conversations?cursor=not-a-cursor")
    assert r.status_code == 400 and r.json()["detail"]["reason"] == "bad_cursor"
    assert c.get("/api/trace-lab/model-calls?channel=email").json()["detail"]["reason"] == "bad_channel"
    assert c.get("/api/trace-lab/conversations?run=yesterday").json()["detail"]["reason"] == "bad_run"
    assert c.get("/api/trace-lab/conversations?status=maybe").status_code == 422


def test_the_list_route_hands_the_parsed_filter_and_company_to_the_loader(monkeypatch):
    seen = fake_loaders(monkeypatch)
    who = principal("admin")
    r = client(who).get("/api/trace-lab/conversations?channel=chat,upload&q=boilers&limit=25&cursor=abc")
    assert r.status_code == 200 and r.json() == {"ok": True, "name": "list_conversations"}
    (p, org, f), kw = seen["list_conversations"]
    assert p is who and org == ORG and f.channels == ("chat", "upload") and f.q == "boilers"
    assert kw == {"cursor": "abc", "limit": 25}
    r = client(who).get("/api/trace-lab/model-calls?kind=router&agent=agent_router&limit=200")
    (_, _, mf), kw = seen["list_model_calls"]
    assert r.status_code == 200 and isinstance(mf, cv.ModelCallFilter) and mf.kind == "router" and kw["limit"] == 200
    r = client(who).get("/api/trace-lab/runs")
    assert r.status_code == 200 and set(seen["list_runs"][1]) == {"since", "until"}


def test_updates_and_facets_are_not_read_as_a_conversation_key(monkeypatch):
    seen = fake_loaders(monkeypatch)
    c = client(principal("admin"))
    assert c.get("/api/trace-lab/conversations/updates?watermark=2026-10-15T12:00:00Z").json()["name"] == "conversation_updates"
    assert c.get("/api/trace-lab/conversations/facets").json()["name"] == "facets"
    assert c.get("/api/trace-lab/conversations/updates").status_code == 422      # the watermark is required
    assert c.get("/api/trace-lab/conversations/s%3Aa%2Fb?turn=turn-2").json()["conversation"] == {"key": "s:a/b"}
    assert seen["get_conversation"][1] == {"turn_id": "turn-2", "include_deleted": False}
    assert c.get("/api/trace-lab/turns/turn-9").json() == {"ok": True, "trace": {"id": "turn-9"}, "conversation_key": "s:abc"}


@pytest.mark.parametrize("path,reason", [("/api/trace-lab/conversations/s%3Anope", "conversation_not_found"),
                                         ("/api/trace-lab/turns/turn-nope", "turn_not_found"),
                                         ("/api/trace-lab/model-calls/" + str(uuid.uuid4()), "model_call_not_found")])
def test_unknown_conversation_turn_and_call_are_404(monkeypatch, path, reason):
    async def none(*a, **kw):
        return None

    for name in ("get_conversation", "get_turn", "get_model_call"):
        monkeypatch.setattr(cv, name, none)
    r = client(principal("admin")).get(path)
    assert r.status_code == 404 and r.json()["detail"]["reason"] == reason
