"""The connector service had no caller, and every route answered for every company.

``get_current_user`` returned ``TokenPayload(sub="anonymous")`` with the real check
commented out inside a string literal, so 135 CRUD routes, 14 connector-management routes
holding source credentials, and 8 Table Editor routes that can add or drop columns were all
open to anyone who could reach the port — against the production database.

These pin the boundary where it is now enforced: the shared session dependency (so a new
route cannot forget it), the admin gate on the Table Editor, and the company filter that
the session applies to reads and to writes.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
os.environ.setdefault("DB_URL", "postgresql+asyncpg://u:p@localhost/db")
os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("SECRETS_AES_KEY", "00" * 32)

from cafm_connector.api import security  # noqa: E402
from cafm_connector.api.routes.plenum_cafm import plenum_router  # noqa: E402
from cafm_connector.api.routes.plenum_cafm.table_customizer import (  # noqa: E402
    table_editor_inner,
)

ORG = UUID("11111111-1111-1111-1111-111111111111")


def _principal(role: str = "user", buildings=None) -> security.Principal:
    return security.Principal(user_id=uuid4(), email="dev@example.com",
                              organization_id=ORG, role=role, building_ids=buildings)


@pytest.fixture
def app() -> FastAPI:
    a = FastAPI()
    a.include_router(plenum_router, prefix="/api/v1/plenum")
    a.mount("/table-editor", table_editor_inner)
    return a


def test_every_plenum_route_refuses_a_caller_without_a_token(app):
    # Not a sample: the whole router, so a route added later cannot quietly be the open one.
    client = TestClient(app)
    paths = sorted({
        r.path for r in app.routes
        if r.path.startswith("/api/v1/plenum") and "{" not in r.path and "GET" in (r.methods or ())
    })
    assert len(paths) >= 20, f"expected the full CRUD surface, found {len(paths)}"
    for path in paths:
        resp = client.get(path)
        assert resp.status_code == 401, f"{path} answered {resp.status_code} with no token"
        assert resp.json()["detail"]["reason"] == "missing_token"


def test_the_table_editor_refuses_a_caller_without_a_token(app):
    client = TestClient(app)
    assert client.get("/table-editor/tables").status_code == 401


def test_the_table_editor_refuses_a_signed_in_user_who_is_not_an_administrator(app):
    from cafm_connector.api.routes.plenum_cafm.deps import get_plenum_db

    async def _me():
        return _principal(role="user")

    async def _never():
        raise AssertionError("the route reached the database before its access check")
        yield  # pragma: no cover

    # The Table Editor is a mounted sub-application, which keeps its own override registry.
    table_editor_inner.dependency_overrides[security.current_principal] = _me
    table_editor_inner.dependency_overrides[get_plenum_db] = _never
    try:
        resp = TestClient(app).get("/table-editor/tables")
        assert resp.status_code == 403
        assert resp.json()["detail"]["reason"] == "admin_required"
    finally:
        table_editor_inner.dependency_overrides.clear()


def test_the_credential_holding_connector_routes_are_no_longer_anonymous():
    """connectors.py stores the credentials for a customer's source systems. Its routes
    depend on ``get_current_user``, which used to hand every one of them the same anonymous
    caller without asking for a token."""
    import asyncio
    import inspect

    from cafm_connector.api import dependencies

    sig = inspect.signature(dependencies.get_current_user)
    assert sig.parameters["principal"].default.dependency is security.current_principal

    p = _principal(role="admin")
    token = asyncio.run(dependencies.get_current_user(p))
    assert token.sub == str(p.user_id) != "anonymous"
    assert token.organization_id == ORG


def test_the_session_dependency_is_what_requires_the_caller():
    """The 401 comes from asking for the session, not from each route remembering to ask.

    Every route in this package depends on ``get_plenum_db``; if the caller were resolved
    route by route, the boundary would be one forgotten route away from absent.
    """
    import inspect

    from cafm_connector.api.routes.plenum_cafm import deps

    params = inspect.signature(deps.get_plenum_db).parameters
    assert "principal" in params
    assert params["principal"].default.dependency is security.current_principal
    # The unscoped escape hatch exists for migrations and workers, and is named for it.
    assert hasattr(deps, "unscoped_session")


class _Recorder:
    """A stand-in session that records the criteria the guard attaches."""

    def __init__(self):
        self.sync_session = self

    def dispatch_noop(self, *a, **k):
        pass


def test_a_superadmin_is_not_narrowed_and_everyone_else_is():
    import sqlalchemy

    # A superadmin works across companies by design, so guard() returns the session
    # untouched; for anyone else it registers the listeners that narrow reads and writes.
    engine_session = _FakeSession()
    security.guard(engine_session, _principal(role="superadmin"))
    assert engine_session.listeners == []

    engine_session = _FakeSession()
    security.guard(engine_session, _principal(role="user"))
    assert sorted(engine_session.listeners) == ["before_flush", "do_orm_execute"]
    assert sqlalchemy  # the import is what makes the event API available


class _FakeSession:
    def __init__(self):
        self.listeners: list[str] = []
        self.sync_session = self


@pytest.fixture(autouse=True)
def _capture_event_registration(monkeypatch):
    from sqlalchemy import event

    real = event.listens_for

    def fake(target, identifier, *a, **k):
        if isinstance(target, _FakeSession):
            target.listeners.append(identifier)

            def deco(fn):
                return fn

            return deco
        return real(target, identifier, *a, **k)

    monkeypatch.setattr(event, "listens_for", fake)
    yield


def test_an_empty_allocation_matches_no_row_rather_than_every_row():
    """() means "allocated to nothing" and must not read as "no filter"."""
    p = _principal(role="user", buildings=())
    assert p.allows_building(uuid4()) is False
    assert _principal(role="user", buildings=None).allows_building(uuid4()) is True


def test_a_caller_with_no_company_cannot_be_conjured_from_the_query_string():
    """Tenancy comes from /me. The routes all take organization_id as a query parameter;
    the session filter is what makes that parameter unable to widen anything."""
    payload = {"user": {"id": str(uuid4()), "email": "a@b.c",
                        "organization_id": str(ORG), "platform_role": "user",
                        "building_ids": [str(ORG)]}}
    p = security._from_me(payload)
    assert p.organization_id == ORG
    assert p.building_ids == (ORG,)
    assert p.is_admin is False


def _compiled(stmt) -> str:
    from sqlalchemy.dialects import postgresql

    return " ".join(
        str(stmt.compile(dialect=postgresql.dialect(),
                         compile_kwargs={"literal_binds": True})).split()
    )


def test_the_pagination_total_is_narrowed_as_well_as_the_rows():
    """Every list route counts with select(count()).select_from(select(Model).subquery()).

    A first attempt narrowed only statements whose top-level entity was the model, so the
    rows came back correctly scoped while the total counted the whole table — a restricted
    caller saw "6 of 54". The criteria must reach inside the subquery.
    """
    from sqlalchemy import func, select

    from cafm_connector.models.plenum_cafm import Asset

    options = security._options_for(_principal(role="user"))
    rows = select(Asset)
    total = select(func.count()).select_from(select(Asset).subquery())
    for option in options:
        rows = rows.options(option)
        total = total.options(option)
    assert str(ORG) in _compiled(rows)
    assert str(ORG) in _compiled(total), "the count ignored the company filter"


def test_two_callers_never_share_a_compiled_filter():
    """The criteria are plain expressions, not a lambda against the declarative base.

    SQLAlchemy keys a lambda criterion's cache on the lambda's code object, so a per-request
    value is either refused as uncacheable or baked in from whichever caller compiled it
    first — which would serve one company's rows to another.
    """
    from sqlalchemy import select

    from cafm_connector.models.plenum_cafm import Asset

    other = UUID("22222222-2222-2222-2222-222222222222")
    mine = security.Principal(uuid4(), "a@b", ORG, "user", None)
    theirs = security.Principal(uuid4(), "c@d", other, "user", None)

    def sql_for(p):
        stmt = select(Asset)
        for option in security._options_for(p):
            stmt = stmt.options(option)
        return _compiled(stmt)

    assert str(ORG) in sql_for(mine) and str(other) not in sql_for(mine)
    assert str(other) in sql_for(theirs) and str(ORG) not in sql_for(theirs)


def test_building_allocation_reaches_the_tables_that_have_a_building():
    """assets, work_orders and locations all carry a populated building_id in the database.

    None of them mapped it, so the connector could not narrow to an allocation at all.
    """
    from cafm_connector.models.plenum_cafm import Asset, Location, WorkOrder

    for model in (Asset, WorkOrder, Location):
        assert hasattr(model, "building_id"), f"{model.__name__} cannot be building-scoped"

    from sqlalchemy import select

    one = UUID("33333333-3333-3333-3333-333333333333")
    stmt = select(Asset)
    for option in security._options_for(_principal(role="user", buildings=(one,))):
        stmt = stmt.options(option)
    sql = _compiled(stmt)
    assert str(one) in sql and str(ORG) in sql


def test_a_model_added_later_is_scoped_without_being_listed():
    """The scoped set is read from the mapper registry, not from a hand-kept list."""
    scoped = {c.__name__ for c in security._scoped_models()}
    assert {"Asset", "WorkOrder", "Vendor", "User", "SparePart"} <= scoped
    assert len(scoped) >= 30
