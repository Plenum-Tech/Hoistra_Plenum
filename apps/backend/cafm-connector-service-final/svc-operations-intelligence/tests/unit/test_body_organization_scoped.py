"""A company named in a request BODY is checked the same way as one in the query string.

The router-level scope dependency reads organization_id from the query string only. Forty
routes read it from the parsed body instead, or passed None to the engine when the query
string left it out — and the engines read None as "every company". Every such read now
goes through access.organization_for. These pin the rule live on a few routes, and on all
of them by walking the app's own route table: any route whose body model, query string or
form carries organization_id must call the check.

Alongside it, the two other rules the spec names for writes: a plain user cannot create a
building, and ingestion — on the direct routes as well as through deep-agents — needs the
per-user right.
"""
from __future__ import annotations

import inspect
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from pydantic import BaseModel

from src.api.routes import auth as auth_routes
from src.app import app
from src.db import get_session
from src.engines.auth import access
from src.engines.auth.tokens import Principal

ORG, OTHER = UUID("11111111-1111-5111-8111-111111111111"), uuid4()
PREFIXES = ("/api/energy", "/api/compliance", "/api/contract-performance", "/api/approvals")


def principal(role="admin", can_ingest=True, buildings=None):
    return Principal(user_id=uuid4(), email="x@example.com", organization_id=ORG,
                     session_id=None, issued_at=datetime.now(timezone.utc),
                     password_changed_at=0, role=role, can_ingest=can_ingest,
                     building_ids=buildings)


def scope(role="admin"):
    return access.scope_for(principal(role))


class Exploding:
    async def execute(self, *a, **k):
        raise AssertionError("route reached the database before its access check")
    def add(self, *a, **k):
        raise AssertionError("route reached the database before its access check")
    async def commit(self): pass


async def _exploding():
    yield Exploding()


@pytest.fixture
def client():
    app.dependency_overrides[get_session] = _exploding
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def as_(p):
    async def _dep():
        return p
    app.dependency_overrides[auth_routes.current_principal] = _dep


# ── the rule itself ─────────────────────────────────────────────────────────────────

def test_no_company_named_means_the_callers_own():
    assert access.organization_for(scope(), None) == ORG


def test_naming_your_own_company_is_fine():
    assert access.organization_for(scope(), ORG) == ORG


def test_an_admin_naming_another_company_is_refused():
    with pytest.raises(HTTPException) as e:
        access.organization_for(scope("admin"), OTHER)
    assert e.value.status_code == 403
    assert e.value.detail["reason"] == "wrong_organization"
    assert e.value.detail["your_organization_id"] == str(ORG)


def test_a_superadmin_naming_another_company_gets_it():
    assert access.organization_for(scope("superadmin"), OTHER) == OTHER


# ── live, at the edge ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", [
    "/api/compliance/scan", "/api/compliance/evidence-pack", "/api/compliance/certificates",
    "/api/compliance/resource-skills",
    "/api/contract-performance/contracts/extract", "/api/contract-performance/fm-staleness",
    "/api/energy/meters", "/api/approvals/send-email",
])
def test_a_body_naming_another_company_is_403_before_any_query(client, path):
    as_(principal("admin"))
    body = {"organization_id": str(OTHER)}
    if path.endswith("/send-email"):
        body.update(to="pm@example.com", subject="x", body="y")
    r = client.post(path, json=body)
    # 403 from the check, or 422 if the body failed validation first — never 200, never
    # a database call (Exploding would have surfaced as an AssertionError).
    assert r.status_code in (403, 422), (path, r.status_code, r.text[:200])
    if r.status_code == 403:
        assert r.json()["detail"]["reason"] == "wrong_organization"


def test_a_put_body_naming_another_company_is_403(client):
    as_(principal("admin"))
    r = client.put("/api/contract-performance/admin/weights", json={"organization_id": str(OTHER)})
    assert r.status_code == 403, r.text[:200]
    assert r.json()["detail"]["reason"] == "wrong_organization"


def test_at_least_one_of_those_routes_actually_reaches_the_check(client):
    as_(principal("admin"))
    r = client.post("/api/compliance/scan", json={"organization_id": str(OTHER)})
    assert r.status_code == 403, r.text[:200]


def test_a_plain_user_cannot_create_a_building(client):
    as_(principal("user", buildings=(uuid4(),)))
    r = client.post("/api/energy/buildings", json={"name": "New Tower", "country": "UK"})
    assert r.status_code == 403, r.text[:200]
    assert r.json()["detail"]["reason"] == "admin_required"


def test_an_admin_creating_a_building_for_another_company_is_refused(client):
    as_(principal("admin"))
    r = client.post("/api/energy/buildings",
                    json={"name": "New Tower", "country": "UK", "organization_id": str(OTHER)})
    assert r.status_code == 403, r.text[:200]
    assert r.json()["detail"]["reason"] == "wrong_organization"


@pytest.mark.parametrize("method,path", [
    ("POST", "/api/energy/readings/ingest"),
    ("POST", "/api/contract-performance/contracts/ingest"),
    ("POST", "/api/compliance/ingest-batch"),
    ("POST", "/api/compliance/documents/ingest"),
])
def test_a_read_only_user_cannot_ingest_on_the_direct_routes(client, method, path):
    as_(principal("user", can_ingest=False, buildings=(uuid4(),)))
    r = client.request(method, path, json={})
    # 403 from the ingest check, or 422 if the body failed validation first — never a
    # write. The next test proves the check is what fires when the body is valid.
    assert r.status_code in (403, 422), (path, r.status_code, r.text[:200])
    if r.status_code == 403:
        assert r.json()["detail"]["reason"] == "cannot_ingest"


def test_the_ingest_check_fires_on_a_valid_body(client):
    as_(principal("user", can_ingest=False, buildings=(uuid4(),)))
    r = client.post("/api/contract-performance/contracts/ingest", json={"organization_id": str(ORG)})
    assert r.status_code == 403, r.text[:200]
    assert r.json()["detail"]["reason"] == "cannot_ingest"


# ── all of them, from the route table ──────────────────────────────────────────────

def _routes():
    for r in app.routes:
        if isinstance(r, APIRoute) and r.path.startswith(PREFIXES):
            yield r


def _takes_company(route) -> list[str]:
    ways = []
    for p in route.dependant.body_params:
        t = p.type_
        if isinstance(t, type) and issubclass(t, BaseModel) and "organization_id" in t.model_fields:
            ways.append(f"body:{t.__name__}")
    for p in route.dependant.query_params + list(getattr(route.dependant, "form_params", [])):
        if p.name == "organization_id":
            ways.append("query/form")
    return ways


def test_every_route_that_takes_a_company_from_the_client_checks_it():
    missing = []
    for r in _routes():
        ways = _takes_company(r)
        if ways and "organization_for(" not in inspect.getsource(r.endpoint):
            missing.append((sorted(r.methods)[0], r.path, ways))
    assert not missing, missing


def test_every_ingestion_route_checks_the_ingest_right():
    missing = []
    for r in _routes():
        if r.methods & {"POST", "PUT"} and any(w in r.path for w in ("ingest", "upload", "import")):
            if "assert_can_ingest(" not in inspect.getsource(r.endpoint):
                missing.append(r.path)
    assert not missing, missing
