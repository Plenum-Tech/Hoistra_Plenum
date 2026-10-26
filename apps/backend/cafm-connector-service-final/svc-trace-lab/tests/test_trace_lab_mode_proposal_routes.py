"""The failure-mode proposal routes (the senior's decision 3): a company admin (or a superadmin acting for a company)
proposes and reads only their own company's proposals; the platform team (a superadmin acting for no company) reads
them all and alone accepts or rejects; an admin can never confirm. Who may ask is auth.py's one rule. The store is
faked: no database, no network, no model."""
from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from src.services.principal import Principal, current_principal
from trace_lab import app as app_module
from trace_lab import db, routes_mode_proposals, taxonomy
from trace_lab.review_errors import Conflict, Forbidden, Invalid, NotFound, Stale
from trace_lab.settings import Settings

ORG = str(uuid.uuid4())
OTHER = str(uuid.uuid4())
B = "/api/trace-lab"
MODE = str(uuid.uuid4())
PROPOSE = {"name": "counts_cancelled_jobs_as_open", "definition": "Present when the answer counts cancelled jobs as open.",
           "level": "technical", "agent": "wo_engine", "created_from": [str(uuid.uuid4())]}


def _principal(role="admin", org=ORG):
    return Principal(user_id=uuid.uuid4(), email="who@example.com", organization_id=uuid.UUID(org) if org else None,
                     role=role, can_ingest=True, building_ids=None)


@pytest.fixture
def client(monkeypatch):
    calls = []
    state = {"ready": True}

    async def ready():
        if not state["ready"]:
            raise HTTPException(status_code=503, detail={"reason": "trace_lab_unavailable", "error": "Not ready."})

    app = FastAPI()
    app.include_router(routes_mode_proposals.router, prefix=B)
    app.dependency_overrides[db.require_ready] = ready

    async def fake_create(principal, draft, *, created_from, org=None):
        calls.append(("create", org, draft, created_from))
        return {"mode": {"id": MODE, "status": "proposed"}, "labels_written": 0}

    async def fake_list(principal, org, *, status="proposed"):
        calls.append(("list", org, status))
        return [{"id": MODE, "status": "proposed"}]

    async def fake_accept(principal, mode_id, *, base_version=None, edits=None):
        calls.append(("accept", mode_id, base_version, edits))
        return {"mode": {"id": mode_id, "status": "candidate"}, "labels_written": 2}

    async def fake_reject(principal, mode_id, reason, *, base_version=None):
        calls.append(("reject", mode_id, reason, base_version))
        return {"mode": {"id": mode_id, "status": "rejected"}}

    monkeypatch.setattr(taxonomy, "create", fake_create)
    monkeypatch.setattr(taxonomy, "list_proposals", fake_list)
    monkeypatch.setattr(taxonomy, "accept_proposal", fake_accept)
    monkeypatch.setattr(taxonomy, "reject_proposal", fake_reject)

    def as_(p):
        app.dependency_overrides[current_principal] = lambda: p
        return TestClient(app)

    as_.calls, as_.state = calls, state
    return as_


# ── reading ────────────────────────────────────────────────────────────────────────────

def test_an_admin_lists_only_their_own_companys_proposals(client):
    r = client(_principal()).get(f"{B}/mode-proposals")
    assert r.status_code == 200 and r.json() == {"ok": True, "proposals": [{"id": MODE, "status": "proposed"}]}
    assert client.calls == [("list", ORG, "proposed")]


def test_an_admin_naming_another_company_is_refused(client):
    r = client(_principal()).get(f"{B}/mode-proposals", params={"organization_id": OTHER})
    assert r.status_code == 403 and r.json()["detail"]["reason"] == "bad_company" and not client.calls


def test_the_platform_reads_every_company_and_a_superadmin_acting_reads_one(client):
    c = client(_principal("superadmin", org=None))
    assert c.get(f"{B}/mode-proposals", params={"status": "all"}).status_code == 200
    assert c.get(f"{B}/mode-proposals", params={"organization_id": OTHER}).status_code == 200
    assert client.calls == [("list", None, "all"), ("list", OTHER, "proposed")]


def test_a_plain_user_and_an_admin_with_no_company_cannot_read(client):
    for p in (_principal("user"), _principal("admin", org=None)):
        r = client(p).get(f"{B}/mode-proposals")
        assert r.status_code == 403 and r.json()["detail"]["reason"] == "reviewers_only"
    assert not client.calls


def test_an_unknown_status_is_a_422_before_the_store_is_asked(client):
    r = client(_principal()).get(f"{B}/mode-proposals", params={"status": "bogus"})
    assert r.status_code == 422 and not client.calls


# ── proposing ──────────────────────────────────────────────────────────────────────────

def test_an_admin_proposes_for_their_own_company(client):
    r = client(_principal()).post(f"{B}/mode-proposals", json=PROPOSE)
    assert r.status_code == 201 and r.json() == {"ok": True, "mode": {"id": MODE, "status": "proposed"}, "labels_written": 0}
    (kind, org, draft, cited), = client.calls
    assert (kind, org, cited) == ("create", ORG, PROPOSE["created_from"])
    assert draft["name"] == PROPOSE["name"] and draft["boundary"] == "" and draft["level"] == "technical"


def test_the_platform_only_fields_are_not_part_of_a_proposal(client):
    body = dict(PROPOSE, evaluator_type="judge", requirement_ids=["GEN-1"], status="confirmed")
    assert client(_principal()).post(f"{B}/mode-proposals", json=body).status_code == 201
    draft = client.calls[0][2]
    assert "evaluator_type" not in draft and "requirement_ids" not in draft and "status" not in draft


def test_a_superadmin_acting_for_a_company_proposes_for_it_but_the_platform_view_cannot(client):
    acting = client(_principal("superadmin", org=None)).post(f"{B}/mode-proposals", json=PROPOSE, params={"organization_id": OTHER})
    assert acting.status_code == 201 and client.calls[0][1] == OTHER
    r = client(_principal("superadmin", org=None)).post(f"{B}/mode-proposals", json=PROPOSE)
    assert r.status_code == 400 and r.json()["detail"]["reason"] == "name_a_company" and len(client.calls) == 1


def test_a_plain_user_cannot_propose(client):
    r = client(_principal("user")).post(f"{B}/mode-proposals", json=PROPOSE)
    assert r.status_code == 403 and r.json()["detail"]["reason"] == "reviewers_only" and not client.calls


@pytest.mark.parametrize("patch", [{"name": "Bad Name"}, {"name": "ab"}, {"definition": "short"}, {"level": "nope"},
                                   {"agent": "nope"}, {"created_from": []}, {"created_from": ["x"] * 51}])
def test_a_malformed_proposal_is_a_422(client, patch):
    r = client(_principal()).post(f"{B}/mode-proposals", json=dict(PROPOSE, **patch))
    assert r.status_code == 422 and not client.calls


# ── deciding ───────────────────────────────────────────────────────────────────────────

def test_the_platform_accepts_a_proposal(client):
    r = client(_principal("superadmin", org=None)).post(
        f"{B}/mode-proposals/{MODE}/accept", json={"base_version": 3, "edits": {"name": "counts_cancelled_work_orders"}})
    assert r.status_code == 200 and r.json() == {"ok": True, "mode": {"id": MODE, "status": "candidate"}, "labels_written": 2}
    assert client.calls == [("accept", MODE, 3, {"name": "counts_cancelled_work_orders"})]
    assert client(_principal("superadmin", org=None)).post(f"{B}/mode-proposals/{MODE}/accept").status_code == 200
    assert client.calls[1] == ("accept", MODE, None, None)


def test_the_platform_rejects_a_proposal_with_a_reason(client):
    r = client(_principal("superadmin", org=None)).post(f"{B}/mode-proposals/{MODE}/reject",
                                                        json={"reason": "Too vague.", "base_version": 2})
    assert r.status_code == 200 and r.json() == {"ok": True, "mode": {"id": MODE, "status": "rejected"}}
    assert client.calls == [("reject", MODE, "Too vague.", 2)]
    for body in ({}, {"reason": ""}, {"reason": "x" * 501}):
        assert client(_principal("superadmin", org=None)).post(f"{B}/mode-proposals/{MODE}/reject", json=body).status_code == 422
    assert len(client.calls) == 1


@pytest.mark.parametrize("who", [_principal("admin"), _principal("user"), _principal("admin", org=OTHER),
                                 _principal("admin", org=None)])
def test_an_admin_cannot_confirm_or_reject_a_proposal(client, who):
    for path, body in ((f"/mode-proposals/{MODE}/accept", None), (f"/mode-proposals/{MODE}/reject", {"reason": "No."})):
        r = client(who).post(B + path, json=body)
        assert r.status_code == 403 and r.json()["detail"]["reason"] == "platform_only", (path, r.text)
    assert not client.calls


def test_a_superadmin_acting_for_a_company_is_not_the_platform(client):
    for path, body in ((f"/mode-proposals/{MODE}/accept", None), (f"/mode-proposals/{MODE}/reject", {"reason": "No."})):
        r = client(_principal("superadmin", org=None)).post(B + path, json=body, params={"organization_id": OTHER})
        assert r.status_code == 403 and r.json()["detail"]["reason"] == "platform_only"
    assert not client.calls


def test_a_stranger_learns_nothing_about_readiness_and_an_unready_service_answers_503_to_the_platform(client):
    client.state["ready"] = False
    r = client(_principal()).post(f"{B}/mode-proposals/{MODE}/accept")
    assert r.status_code == 403 and r.json()["detail"]["reason"] == "platform_only"        # auth is checked first
    r = client(_principal("superadmin", org=None)).post(f"{B}/mode-proposals/{MODE}/accept")
    assert r.status_code == 503 and r.json()["detail"]["reason"] == "trace_lab_unavailable"
    assert client(_principal()).get(f"{B}/mode-proposals").status_code == 503
    assert not client.calls


@pytest.mark.parametrize("exc,status,reason", [
    (Stale({"id": MODE}), 409, "stale"), (NotFound("mode_not_found"), 404, "mode_not_found"),
    (Conflict("not_a_proposal"), 409, "not_a_proposal"), (Invalid("reason_required"), 422, "reason_required"),
    (Forbidden("platform_only"), 403, "platform_only")])
def test_store_refusals_keep_the_house_envelope(client, monkeypatch, exc, status, reason):
    async def refuse(*a, **k):
        raise exc

    monkeypatch.setattr(taxonomy, "accept_proposal", refuse)
    r = client(_principal("superadmin", org=None)).post(f"{B}/mode-proposals/{MODE}/accept")
    assert r.status_code == status
    detail = r.json()["detail"]
    assert detail["reason"] == reason and isinstance(detail["error"], str) and detail["error"]
    if isinstance(exc, Stale):
        assert detail["current"] == {"id": MODE}


# ── the service ────────────────────────────────────────────────────────────────────────

PATHS = [("GET", "/mode-proposals"), ("POST", "/mode-proposals"), ("POST", "/mode-proposals/{mode_id}/accept"),
         ("POST", "/mode-proposals/{mode_id}/reject")]


def test_the_router_is_in_the_service_and_holds_exactly_its_four_routes():
    assert routes_mode_proposals.router in app_module.ROUTERS
    own = {(m, r.path) for r in routes_mode_proposals.router.routes for m in r.methods}
    assert own == set(PATHS)


def test_no_path_repeats_a_path_another_router_serves_and_every_route_refuses_a_stranger():
    others = [(m, r.path) for router in app_module.ROUTERS if router is not routes_mode_proposals.router
              for r in router.routes for m in r.methods]
    assert not set(others) & set(PATHS) and len(others) == len(set(others))
    c = TestClient(app_module.create_app(Settings()))
    for method, path in PATHS:
        filled = path.format(mode_id="m-1")
        assert c.request(method, B + filled).status_code in (401, 403, 422), (method, path)
        assert c.request(method, filled).status_code == 404                     # served only under the prefix
