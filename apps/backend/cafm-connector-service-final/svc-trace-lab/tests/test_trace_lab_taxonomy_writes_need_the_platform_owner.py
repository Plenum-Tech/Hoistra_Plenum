"""D7 on the taxonomy routes: changing the taxonomy, setting notes aside, running proposals and editing requirements are
the platform owner's (a superadmin acting for no company); a company admin, a superadmin acting for a company and a plain
user all get 403 platform_only, before the store is asked and before readiness. Domain refusals keep the house envelope
{detail: {reason, error, ...}}. The store is faked: no database, no network, no model."""
from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import trace_lab_m4_fixtures as fx
from src.services.principal import Principal, current_principal
from trace_lab import annotations, db, proposals, requirements, routes_taxonomy, taxonomy

B = "/api/trace-lab"
MODE = "00000000-0000-4000-8000-000000000009"
RUN = "00000000-0000-4000-8000-000000000008"
NOTE = "00000000-0000-4000-8000-000000000007"
DRAFT = {"name": "counts_cancelled_jobs_as_open", "definition": "Present when the answer counts cancelled jobs as open.",
         "boundary": "", "level": "technical", "agent": "wo_engine"}
CHILD = dict(DRAFT, created_from=[NOTE])
IMPL = {"doc": "apps/backend/CLAUDE.md", "section": "", "line": 3, "quote": "every value traces to a DB row"}
WINDOW = {"since": "2026-10-10T00:00:00+00:00", "until": "2026-10-15T00:00:00+00:00"}

#: (method, path, json body or None, query params, the stubbed function, the status the owner gets)
OWNER_ROUTES = [
    ("POST", "/modes", dict(DRAFT, created_from=[NOTE]), None, (taxonomy, "create"), 201),
    ("PATCH", f"/modes/{MODE}", {"base_version": 2, "boundary": "Not on hold."}, None, (taxonomy, "edit"), 200),
    ("POST", "/modes/merge", {"source_ids": [MODE], "into": MODE, "base_versions": {MODE: 1}}, None, (taxonomy, "merge"), 200),
    ("POST", f"/modes/{MODE}/rename", {"name": "counts_closed_jobs", "base_version": 1}, None, (taxonomy, "rename"), 200),
    ("POST", f"/modes/{MODE}/split", {"base_version": 1, "children": [CHILD, dict(CHILD, name="counts_on_hold_jobs")]}, None,
     (taxonomy, "split"), 200),
    ("POST", f"/modes/{MODE}/retire", {"reason": "not seen again", "base_version": 1}, None, (taxonomy, "retire"), 200),
    ("POST", f"/modes/{MODE}/restore", {"base_version": 1}, None, (taxonomy, "restore"), 200),
    ("POST", f"/notes/{NOTE}/dispose", {"outcome": "no_mode_fits", "note": "Too vague."}, None,
     (annotations, "dispose_note"), 200),
    ("POST", f"/notes/{NOTE}/undispose", None, None, (annotations, "undispose_note"), 200),
    ("GET", "/proposals/estimate", None, {"kind": "grouping"}, (proposals, "estimate"), 200),
    ("POST", "/proposal-runs", {"kind": "cells", "usd_cap": 2.0, **WINDOW}, None, (proposals, "start_run"), 202),
    ("GET", "/proposal-runs", None, None, (proposals, "list_runs"), 200),
    ("GET", f"/proposal-runs/{RUN}", None, None, (proposals, "get_run"), 200),
    ("POST", f"/proposal-runs/{RUN}/resume", {"usd_cap": 3.0}, None, (proposals, "resume_run"), 202),
    ("POST", f"/proposal-runs/{RUN}/pause", None, None, (proposals, "pause_run"), 200),
    ("POST", "/requirements", {"area": "GEN", "statement": "Every figure traces to a record.", "level": "technical",
                               "implementations": [IMPL]}, None, (requirements, "create"), 201),
    ("POST", "/requirements/seed", None, None, (requirements, "seed"), 200),
    ("POST", "/requirements/GEN-1/revisions", {"statement": "Every figure traces to one record.", "note": "Tighter.",
                                               "implementations": [IMPL]}, None, (requirements, "revise"), 201),
    ("POST", "/requirements/GEN-1/withdraw", {"note": "No longer true."}, None, (requirements, "withdraw"), 200),
]
IDS = [f"{m} {p}" for m, p, *_ in OWNER_ROUTES]


def _principal(role="admin", org=fx.ORG_A):
    return Principal(user_id=uuid.uuid4(), email="who@example.com", organization_id=uuid.UUID(org) if org else None,
                     role=role, can_ingest=True, building_ids=None)


@pytest.fixture
def client(monkeypatch):
    state = {"ready": True}
    calls: list[tuple[str, tuple, dict]] = []

    async def ready():
        if not state["ready"]:
            raise HTTPException(status_code=503, detail={"reason": "trace_lab_unavailable", "error": "Not ready."})

    app = FastAPI()
    app.include_router(routes_taxonomy.router, prefix=B)
    app.dependency_overrides[db.require_ready] = ready

    def stub(module, name, result):
        async def fake(*a, **kw):
            calls.append((name, a, kw))
            if isinstance(result, Exception):
                raise result
            return result

        monkeypatch.setattr(module, name, fake)

    for _m, _p, _b, _q, (module, name), _s in OWNER_ROUTES:
        stub(module, name, [] if name == "list_runs" else {})

    def as_(p):
        app.dependency_overrides[current_principal] = lambda: p
        return TestClient(app)

    as_.calls, as_.state, as_.stub = calls, state, stub
    return as_


def _send(c, method, path, body, query):
    return c.request(method, B + path, json=body, params=query)


@pytest.mark.parametrize("route", OWNER_ROUTES, ids=IDS)
def test_only_the_platform_owner_may_call_an_owner_route(client, route):
    method, path, body, query, (_mod, name), ok = route
    for who, org in ((_principal("admin"), None), (_principal("user"), None), (_principal("admin", org=None), None),
                     (_principal("superadmin", org=None), fx.ORG_A)):
        q = dict(query or {}, **({"organization_id": org} if org else {}))
        r = _send(client(who), method, path, body, q)
        assert r.status_code == 403 and r.json()["detail"]["reason"] == "platform_only", (who.role, org)
    assert client.calls == []                                           # the store is never asked
    r = _send(client(_principal("superadmin", org=None)), method, path, body, query)
    assert r.status_code == ok and [c[0] for c in client.calls] == [name]


@pytest.mark.parametrize("route", OWNER_ROUTES, ids=IDS)
def test_a_stranger_learns_nothing_about_readiness_and_the_owner_meets_it(client, route):
    method, path, body, query, _stub, _ok = route
    client.state["ready"] = False
    assert _send(client(_principal("admin")), method, path, body, query).status_code == 403
    r = _send(client(_principal("superadmin", org=None)), method, path, body, query)
    assert r.status_code == 503 and r.json()["detail"]["reason"] == "trace_lab_unavailable" and client.calls == []


def test_the_owner_creates_a_mode_with_the_data_level_and_sends_only_what_the_store_needs(client):
    r = _send(client(_principal("superadmin", org=None)), "POST", "/modes", dict(DRAFT, level="data", created_from=[NOTE]), None)
    assert r.status_code == 201 and r.json() == {"ok": True}
    (_name, args, kw), = client.calls
    assert args[1]["level"] == "data" and args[1]["name"] == DRAFT["name"] and "created_from" not in args[1]
    assert kw == {"created_from": [NOTE]}


def test_an_edit_sends_only_the_fields_the_owner_named_and_the_version(client):
    _send(client(_principal("superadmin", org=None)), "PATCH", f"/modes/{MODE}",
          {"base_version": 4, "boundary": "Not on hold.", "status": "frozen", "rescope": True}, None)
    (_n, args, kw), = client.calls
    assert args[1] == MODE and args[2] == {"boundary": "Not on hold.", "status": "frozen"}
    assert kw == {"base_version": 4, "rescope": True}


def test_a_merge_a_split_and_a_retire_pass_their_bodies_through(client):
    c = client(_principal("superadmin", org=None))
    _send(c, "POST", "/modes/merge", {"source_ids": [MODE], "new": DRAFT, "base_versions": {MODE: 3}}, None)
    _send(c, "POST", f"/modes/{MODE}/split", {"base_version": 2, "children": [CHILD, dict(CHILD, name="counts_on_hold_jobs")],
                                              "assignments": {"turn-1": [0]}}, None)
    _send(c, "POST", f"/modes/{MODE}/retire", {"reason": "not seen again", "base_version": 2}, None)
    merge, split, retire = client.calls
    assert merge[2] == {"into": None, "new": {**DRAFT, "nearest_mode_id": None, "evaluator_type": None, "requirement_ids": []},
                        "base_versions": {MODE: 3}} and merge[1][1] == [MODE]
    assert [x["name"] for x in split[1][2]] == [DRAFT["name"], "counts_on_hold_jobs"] and split[1][3] == {"turn-1": [0]}
    assert split[2] == {"base_version": 2} and retire[2] == {"reason": "not seen again", "base_version": 2}


def test_a_run_is_started_with_its_cap_window_and_company_filter_and_resumed_with_an_optional_cap(client):
    c = client(_principal("superadmin", org=None))
    _send(c, "POST", "/proposal-runs", {"kind": "depth_scan", "mode_id": MODE, "usd_cap": 1.5, "filter_org": fx.ORG_B,
                                        "limit": 50, "include_candidates": True, **WINDOW}, None)
    _send(c, "POST", f"/proposal-runs/{RUN}/resume", None, None)
    start, resume = client.calls
    assert start[2]["kind"] == "depth_scan" and start[2]["mode_id"] == MODE and start[2]["usd_cap"] == 1.5
    assert start[2]["filter_org"] == fx.ORG_B and start[2]["limit"] == 50 and start[2]["include_candidates"] is True
    assert start[2]["since"].year == 2026 and resume[2] == {"usd_cap": None}


@pytest.mark.parametrize("method,path,body,query", [
    ("POST", "/modes", dict(DRAFT, name="Bad Name", created_from=[NOTE]), None),
    ("POST", "/modes", dict(DRAFT, level="nope", created_from=[NOTE]), None),
    ("POST", "/modes", dict(DRAFT, definition="short", created_from=[NOTE]), None),
    ("POST", "/modes", dict(DRAFT, created_from=[]), None),
    ("PATCH", f"/modes/{MODE}", {"boundary": "No version."}, None),
    ("PATCH", f"/modes/{MODE}", {"base_version": 1, "status": "retired"}, None),
    ("POST", f"/modes/{MODE}/retire", {"reason": "", "base_version": 1}, None),
    ("POST", f"/modes/{MODE}/split", {"base_version": 1, "children": [CHILD]}, None),
    ("POST", "/modes/merge", {"source_ids": [], "base_versions": {}}, None),
    ("POST", f"/notes/{NOTE}/dispose", {"outcome": "not_a_thing", "note": "x"}, None),
    ("POST", f"/notes/{NOTE}/dispose", {"outcome": "no_mode_fits", "note": ""}, None),
    ("POST", "/proposal-runs", {"kind": "cells", "usd_cap": 0, **WINDOW}, None),
    ("POST", "/proposal-runs", {"kind": "everything", "usd_cap": 1, **WINDOW}, None),
    ("POST", "/proposal-runs", {"kind": "cells", "usd_cap": 1, "limit": 501, **WINDOW}, None),
    ("GET", "/proposals/estimate", None, {"kind": "nope"}),
    ("POST", "/requirements", {"area": "XX", "statement": "Every figure traces to a record.", "level": "technical"}, None),
    ("POST", "/requirements", {"area": "GEN", "statement": "short", "level": "technical"}, None),
    ("POST", "/requirements/GEN-1/withdraw", {"note": "no"}, None),
])
def test_a_malformed_owner_request_is_a_422_before_the_store_is_asked(client, method, path, body, query):
    r = _send(client(_principal("superadmin", org=None)), method, path, body, query)
    assert r.status_code == 422 and client.calls == []


@pytest.mark.parametrize("make,status,reason", [
    (lambda: taxonomy.conflict("mode_frozen"), 409, "mode_frozen"),
    (lambda: taxonomy.conflict("name_taken"), 409, "name_taken"),
    (lambda: taxonomy.invalid("contract_incomplete", missing=["boundary", "positives"]), 422, "contract_incomplete"),
    (lambda: taxonomy.not_found("mode_not_found"), 404, "mode_not_found"),
    (lambda: taxonomy.forbidden("platform_only"), 403, "platform_only"),
])
def test_domain_refusals_use_the_house_envelope(client, make, status, reason):
    client.stub(taxonomy, "edit", make())
    r = client(_principal("superadmin", org=None)).patch(f"{B}/modes/{MODE}", json={"base_version": 2, "boundary": "Not on hold."})
    d = r.json()["detail"]
    assert r.status_code == status and d["reason"] == reason and d["error"] and d["error"] != "That could not be done."
    if reason == "contract_incomplete":
        assert d["missing"] == ["boundary", "positives"]


def test_a_stale_edit_is_a_409_carrying_the_current_mode(client):
    from trace_lab.review_errors import Stale

    client.stub(taxonomy, "edit", Stale({"id": MODE, "version": 3}))
    r = client(_principal("superadmin", org=None)).patch(f"{B}/modes/{MODE}", json={"base_version": 2, "boundary": "Not on hold."})
    d = r.json()["detail"]
    assert r.status_code == 409 and d["reason"] == "stale" and d["current"] == {"id": MODE, "version": 3}


def test_a_name_that_a_race_let_through_to_the_unique_index_is_still_a_409(client):
    from sqlalchemy.exc import IntegrityError

    client.stub(taxonomy, "create", IntegrityError("INSERT", {}, Exception("violates unique constraint ux_tl_modes_live_name")))
    r = client(_principal("superadmin", org=None)).post(f"{B}/modes", json=dict(DRAFT, created_from=[NOTE]))
    assert r.status_code == 409 and r.json()["detail"]["reason"] == "name_taken"


def test_an_integrity_error_that_is_not_ours_is_not_swallowed(client):
    from sqlalchemy.exc import IntegrityError

    client.stub(taxonomy, "create", IntegrityError("INSERT", {}, Exception("violates something else")))
    with pytest.raises(IntegrityError):
        client(_principal("superadmin", org=None)).post(f"{B}/modes", json=dict(DRAFT, created_from=[NOTE]))
