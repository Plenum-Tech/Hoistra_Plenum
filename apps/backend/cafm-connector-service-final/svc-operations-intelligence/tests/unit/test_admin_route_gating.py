"""The new consoles are gated before they touch the database.

Every /api/admin and /api/superadmin route is behind a role dependency. These pin the gate
itself — no token is 401, the wrong role is 403, the wrong company is 403 — with the database
never reached, because a gate that only holds when the query behind it happens to fail is
not a gate.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from src.api.routes import admin as admin_routes
from src.api.routes import auth as auth_routes
from src.app import app
from src.db import get_session
from src.engines.auth.tokens import Principal

ORG = UUID("11111111-1111-5111-8111-111111111111")
OTHER = UUID("22222222-2222-5222-8222-222222222222")


def principal(role: str, org=ORG, buildings=None, can_ingest=False) -> Principal:
    return Principal(
        user_id=uuid4(), email=f"{role}@example.com", organization_id=org, session_id=None,
        issued_at=datetime.now(timezone.utc), password_changed_at=0, role=role,
        can_ingest=can_ingest, building_ids=buildings,
    )


class ExplodingSession:
    """If any route reaches the database before its gate, this makes that visible."""

    async def execute(self, *a, **k):
        raise AssertionError("route reached the database before its access gate")

    async def commit(self):
        pass


async def _exploding_session():
    yield ExplodingSession()


@pytest.fixture
def client():
    app.dependency_overrides[get_session] = _exploding_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def as_(p: Principal):
    async def _dep():
        return p
    app.dependency_overrides[auth_routes.current_principal] = _dep


ADMIN_ROUTES = [
    ("GET", "/api/admin/users"), ("GET", "/api/admin/buildings"), ("GET", "/api/admin/usage"),
    ("GET", "/api/admin/ingestion-audit"),
]
SUPER_ROUTES = [
    ("GET", "/api/superadmin/companies"), ("GET", "/api/superadmin/credits"),
    ("GET", f"/api/superadmin/companies/{uuid4()}"),
]


@pytest.mark.parametrize("method,path", ADMIN_ROUTES + SUPER_ROUTES)
def test_no_token_is_401(client, method, path):
    r = client.request(method, path)
    assert r.status_code == 401
    assert r.json()["detail"]["reason"] == "missing_token"


@pytest.mark.parametrize("method,path", ADMIN_ROUTES)
def test_a_plain_user_cannot_reach_the_admin_console(client, method, path):
    as_(principal("user", buildings=(uuid4(),)))
    r = client.request(method, path)
    assert r.status_code == 403
    assert r.json()["detail"]["required_role"] == "admin"


@pytest.mark.parametrize("method,path", SUPER_ROUTES)
def test_a_company_admin_cannot_reach_the_super_admin_console(client, method, path):
    as_(principal("admin"))
    r = client.request(method, path)
    assert r.status_code == 403
    assert r.json()["detail"]["required_role"] == "superadmin"


def test_an_admin_asking_for_another_company_is_refused(client):
    as_(principal("admin"))
    r = client.get(f"/api/admin/users?organization_id={OTHER}")
    assert r.status_code == 403
    assert r.json()["detail"]["reason"] == "wrong_organization"


def test_an_admin_with_no_company_cannot_use_the_console(client):
    as_(principal("admin", org=None))
    r = client.get("/api/admin/users")
    assert r.status_code == 400
    assert r.json()["detail"]["reason"] == "no_organization"


def test_the_invite_form_validates_before_any_query(client):
    as_(principal("admin"))
    r = client.post("/api/admin/users/invite", json={"full_name": "x", "email": "not-an-email"})
    assert r.status_code == 422  # pydantic, before the route body runs


def test_accepting_an_invitation_needs_no_token():
    # Public by design: the link IS the credential. The DB is reached here (to look up the
    # token), so a session that cannot answer must produce a clean error, not a 401.
    class NoRow:
        async def execute(self, *a, **k):
            class R:
                def mappings(self): return self
                def first(self): return None
            return R()
        async def commit(self): pass

    async def _s():
        yield NoRow()
    app.dependency_overrides[get_session] = _s
    try:
        r = TestClient(app).post("/api/auth/invitations/accept",
                                 json={"token": "x" * 20, "password": "Correct-horse-9"})
        assert r.status_code == 404
        assert r.json()["detail"]["reason"] == "invalid"
    finally:
        app.dependency_overrides.clear()


def test_foreign_buildings_are_rejected_before_any_write():
    owned = {str(uuid4()): {"id": "a"}, str(uuid4()): {"id": "b"}}
    stranger = uuid4()
    with pytest.raises(HTTPException) as e:
        admin_routes._check_buildings([UUID(next(iter(owned))), stranger], owned)
    assert e.value.status_code == 400
    assert e.value.detail["reason"] == "foreign_buildings"
    assert e.value.detail["building_ids"] == [str(stranger)]


def test_the_new_routes_are_all_registered():
    paths = {r.path for r in app.routes}
    for p in ("/api/superadmin/companies", "/api/superadmin/companies/{organization_id}",
              "/api/superadmin/companies/{organization_id}/invite-admin", "/api/superadmin/credits",
              "/api/admin/users", "/api/admin/users/invite", "/api/admin/users/{user_id}",
              "/api/admin/buildings", "/api/admin/usage", "/api/admin/ingestion-audit",
              "/api/auth/invitations/accept"):
        assert p in paths, p
