"""svc-udr had no caller, and it reads and writes every table in the schema.

Eighteen routes: list every table, describe any schema, read, search, create, update and
delete rows in a table named by the request, run a caller-supplied SELECT, and an agent that
does all of it from a sentence. None asked for a token, and nginx routes ``/backend/udr/`` to
it from the public internet — verified live before this change, an anonymous
``GET /backend/udr/api/tables/`` returned the full table list.

These pin the boundary: the whole router surface refuses an anonymous caller (not a sample —
a route added later must not be the open one), the generic-table and agent routes need an
administrator, and the saved spaces and run history take their company from the token rather
than from the query string.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("DB_URL", "postgresql+asyncpg://u:p@localhost/d")
logging.disable(logging.CRITICAL)

from src.app import app  # noqa: E402
from src.services import principal as P  # noqa: E402

ORG = UUID("11111111-1111-1111-1111-111111111111")
OTHER = UUID("22222222-2222-2222-2222-222222222222")


def _principal(role: str = "user", org: UUID | None = ORG) -> P.Principal:
    return P.Principal(user_id=uuid4(), email="dev@example.com", organization_id=org,
                       role=role, building_ids=None)


@pytest.fixture
def client() -> TestClient:
    yield TestClient(app)
    app.dependency_overrides.clear()


class _Rows:
    """Enough of a result for a route to finish without a database."""

    def fetchall(self):
        return []

    def first(self):
        return None

    def scalar(self):
        return 0

    @property
    def rowcount(self):
        return 0


class _Session:
    async def execute(self, *a, **k):
        return _Rows()

    async def commit(self):
        pass


def _as(principal: P.Principal, *, with_session: bool = False) -> None:
    async def _me():
        return principal

    app.dependency_overrides[P.current_principal] = _me
    if with_session:
        from src.db import get_session

        async def _session():
            yield _Session()

        app.dependency_overrides[get_session] = _session


def _code(response) -> str:
    body = response.json()
    errors = body.get("errors") or []
    return errors[0].get("code", "") if errors else ""


# ── every route needs a caller ────────────────────────────────────────────────

def test_every_route_refuses_a_caller_without_a_token(client):
    paths = sorted({
        (m, r.path) for r in app.routes
        if getattr(r, "path", "").startswith("/api")
        for m in (r.methods or ()) if m in ("GET", "POST", "PATCH", "DELETE")
    })
    assert len(paths) >= 15, f"expected the whole surface, found {len(paths)}"
    for method, path in paths:
        concrete = (path.replace("{table}", "assets").replace("{record_id}", "1")
                        .replace("{run_id}", str(uuid4())).replace("{space_id}", str(uuid4())))
        resp = client.request(method, concrete, json={} if method in ("POST", "PATCH") else None)
        assert resp.status_code == 401, f"{method} {concrete} answered {resp.status_code}"
        assert _code(resp) == "missing_token"


def test_the_health_check_stays_open(client):
    assert client.get("/health").status_code == 200


# ── the routes whose table the request names ──────────────────────────────────

@pytest.mark.parametrize("method, path", [
    ("GET", "/api/tables/"),
    ("GET", "/api/tables/assets/schema"),
    ("GET", "/api/tables/assets/records"),
    ("POST", "/api/tables/assets/records"),
    ("POST", "/api/tables/query/select"),
    ("POST", "/api/agent/query"),
])
def test_reading_or_writing_an_arbitrary_table_needs_an_administrator(client, method, path):
    """No per-row company filter can be written for a query whose table arrives with the
    request — a predicate has to know which column holds the company. So the gate is the
    role, as it is for the connector service's Table Editor."""
    _as(_principal(role="user"))
    resp = client.request(method, path, json={"message": "hi", "sql": "SELECT 1"})
    assert resp.status_code == 403
    assert _code(resp) == "admin_required"


def test_an_administrator_is_past_the_gate(client):
    _as(_principal(role="admin"), with_session=True)
    resp = client.get("/api/spaces")
    assert resp.status_code == 200


# ── the routes with a real organization_id ────────────────────────────────────

def test_saved_spaces_and_runs_do_not_need_an_administrator(client):
    """These have a fixed shape and a company column, so they are scoped rather than gated:
    an ordinary user has saved spaces and run history of their own."""
    _as(_principal(role="user"), with_session=True)
    for path in ("/api/spaces", "/api/udr/scripts", "/api/udr/runs?session_id=x"):
        assert client.get(path).status_code == 200, path


def test_naming_another_company_is_refused_rather_than_answered(client):
    """The company used to come from the query string, so asking for another one returned
    it. It now comes from the token."""
    _as(_principal(role="user"), with_session=True)
    for path in (f"/api/spaces?organization_id={OTHER}",
                 f"/api/udr/scripts?organization_id={OTHER}"):
        resp = client.get(path)
        assert resp.status_code == 403, path
        assert _code(resp) == "wrong_organization"


def test_a_superadmin_may_name_another_company(client):
    _as(_principal(role="superadmin", org=ORG))
    assert P.organization_for(_principal(role="superadmin"), str(OTHER)) == OTHER


def test_your_own_company_is_the_default_and_needs_no_parameter():
    p = _principal(role="user")
    assert P.organization_for(p, None) == ORG
    assert P.organization_for(p, str(ORG)) == ORG


def test_a_plain_user_asking_for_another_company_is_forbidden():
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        P.organization_for(_principal(role="user"), str(OTHER))
    assert exc.value.status_code == 403
    assert exc.value.detail["reason"] == "wrong_organization"


# ── the predicate itself ──────────────────────────────────────────────────────

def test_a_superadmin_is_not_narrowed_and_everyone_else_is():
    from src.api.routes.runs import _visible as runs_visible
    from src.api.routes.spaces import _visible as spaces_visible

    for visible in (runs_visible, spaces_visible):
        clause, params = visible(_principal(role="superadmin"))
        assert clause == "" and params == {}
        clause, params = visible(_principal(role="user"))
        assert "organization_id = CAST(:scope_org AS UUID)" in clause
        assert params["scope_org"] == str(ORG)
        # Rows with no company predate the scoping and were visible to everybody; hiding
        # them would empty the panel for existing users rather than protect anything.
        assert "organization_id IS NULL" in clause


def test_the_identity_service_is_the_only_source_of_the_role():
    """This service must not be able to disagree with operations-intelligence about who may
    see what, so it decodes nothing itself."""
    import inspect

    src = inspect.getsource(P)
    assert "/api/auth/me" in src
    assert "jwt" not in src.lower() and "decode" not in src.lower()


def test_a_resolved_caller_is_built_from_the_me_payload():
    payload = {"user": {"id": str(uuid4()), "email": "a@b.c", "organization_id": str(ORG),
                        "platform_role": "admin", "building_ids": None}}
    p = P._from_me(payload)
    assert p.organization_id == ORG and p.is_admin and not p.is_superadmin
    assert p.building_ids is None
