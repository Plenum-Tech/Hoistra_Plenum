"""The domain routes are behind the boundary now.

Before this every endpoint on /api/energy, /api/compliance, /api/contract-performance and
/api/approvals was open, took the company from the client, and never checked a building.
These pin the three things that changed — a token is required, a building named in the
path must be one of the caller's, and lists are narrowed to their buildings — each of them
at the edge, before the database is reached.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from src.api.routes import auth as auth_routes
from src.app import app
from src.db import get_session
from src.engines.auth.tokens import Principal

ORG = UUID("11111111-1111-5111-8111-111111111111")
MINE, THEIRS = uuid4(), uuid4()


def principal(role="user", buildings=None, can_ingest=False):
    return Principal(user_id=uuid4(), email="x@example.com", organization_id=ORG,
                     session_id=None, issued_at=datetime.now(timezone.utc),
                     password_changed_at=0, role=role, can_ingest=can_ingest,
                     building_ids=buildings)


class Exploding:
    async def execute(self, *a, **k):
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


@pytest.mark.parametrize("path", [
    "/api/energy/buildings", f"/api/energy/buildings/{MINE}/graph",
    f"/api/energy/buildings/{MINE}/meter-summary", "/api/compliance/certificates",
    "/api/contract-performance/contracts", "/api/contract-performance/invoices",
    "/api/approvals", "/api/energy/meters",
])
def test_every_domain_route_needs_a_token(client, path):
    r = client.get(path)
    assert r.status_code == 401, (path, r.status_code)
    assert r.json()["detail"]["reason"] == "missing_token"


@pytest.mark.parametrize("path", [
    f"/api/energy/buildings/{THEIRS}/graph",
    f"/api/energy/buildings/{THEIRS}/meter-summary",
    f"/api/energy/buildings/{THEIRS}/cost-drivers",
    f"/api/contract-performance/invoices?building_id={THEIRS}",
])
def test_a_user_cannot_read_a_building_they_are_not_allocated(client, path):
    # The assertion fires before any query: the Exploding session proves it.
    as_(principal(buildings=(MINE,)))
    r = client.get(path)
    assert r.status_code == 403, (path, r.status_code, r.text[:200])
    assert r.json()["detail"]["reason"] == "building_not_allocated"


def test_a_user_allocated_to_nothing_is_refused_everywhere(client):
    as_(principal(buildings=()))
    r = client.get(f"/api/energy/buildings/{MINE}/graph")
    assert r.status_code == 403


@pytest.mark.parametrize("method,path", [
    ("PATCH", f"/api/energy/buildings/{THEIRS}"),
    ("DELETE", f"/api/energy/buildings/{THEIRS}"),
])
def test_a_user_cannot_change_a_building_they_are_not_allocated(client, method, path):
    as_(principal(buildings=(MINE,)))
    r = client.request(method, path, json={})
    # 403 from the check, or 422 if the body failed validation first — never 200, and never
    # a database call (Exploding would have raised as a 500).
    assert r.status_code in (403, 422), (path, r.status_code)


def test_naming_another_company_in_the_query_is_refused_on_every_domain_router(client):
    as_(principal(role="admin"))
    other = uuid4()
    for path in (f"/api/energy/buildings?organization_id={other}",
                 f"/api/compliance/certificates?organization_id={other}",
                 f"/api/contract-performance/contracts?organization_id={other}"):
        r = client.get(path)
        assert r.status_code == 403, path
        assert r.json()["detail"]["reason"] == "wrong_organization"


def test_a_superadmin_may_name_another_company(client):
    # It then reaches the database, which is the point. The Exploding session raises the
    # moment it is touched, and TestClient re-raises server exceptions — so the assertion
    # surfacing here (rather than a 403) is the evidence the gate let the call through.
    as_(principal(role="superadmin"))
    r = client.get(f"/api/energy/buildings?organization_id={uuid4()}")
    # The engine catches its own database errors and answers with an empty table rather
    # than raising, so the Exploding session never surfaces here. What matters is what the
    # gate did: 200 means it let the superadmin through; 403 would mean it did not.
    assert r.status_code == 200, r.text[:200]


def test_the_kill_switch_is_off_by_default():
    from src.config import settings
    assert settings.auth_enforce_scope is True
