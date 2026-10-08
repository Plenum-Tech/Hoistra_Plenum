"""This service now knows who is asking, and answers for their buildings only.

Every route took a session and answered for the whole table: any bearer of the URL listed
every work order, asset and location in every company. The caller is now resolved from
operations-intelligence's /me and the reads are narrowed to their buildings. These run
without a database — the in-memory engine the integration tests use cannot build the
ARRAY-typed tables and errors before any test here would start — so they pin the predicate
itself, the 403, and what each route hands the session.
"""
from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from src.app import app
from src.db import get_session
from src.models.asset import Asset
from src.models.location import Location
from src.models.work_order import WorkOrder
from src.services import principal as P

MINE, ALSO_MINE, THEIRS = uuid4(), uuid4(), uuid4()


def make(building_ids, role="user"):
    return P.Principal(user_id=uuid4(), email="x@example.com", organization_id=None,
                       role=role, building_ids=building_ids)


def compiled(q) -> str:
    return str(q.compile(dialect=postgresql.dialect())).replace("\n", " ")


# ── the principal as /me describes it ─────────────────────────────────────────

def test_me_with_a_selected_building_narrows_to_that_one():
    p = P._from_me({"user": {"id": str(uuid4()), "email": "a@b.c", "platform_role": "user",
                             "building_ids": [str(MINE), str(ALSO_MINE)],
                             "selected_building_id": str(MINE)}})
    assert p.building_ids == (MINE,) and p.selected_building_id == MINE


def test_a_selection_outside_the_allocation_cannot_widen_it():
    p = P._from_me({"user": {"id": str(uuid4()), "building_ids": [str(MINE)],
                             "selected_building_id": str(THEIRS)}})
    assert p.building_ids == (MINE,)


def test_an_admin_is_unrestricted_until_they_select():
    assert P._from_me({"user": {"id": str(uuid4()), "platform_role": "admin"}}).building_ids is None
    p = P._from_me({"user": {"id": str(uuid4()), "platform_role": "admin",
                             "building_ids": None, "selected_building_id": str(MINE)}})
    assert p.building_ids == (MINE,)


def test_allocated_to_nothing_is_an_empty_tuple_not_none():
    assert P._from_me({"user": {"id": str(uuid4()), "building_ids": []}}).building_ids == ()


# ── the predicate ─────────────────────────────────────────────────────────────

def test_scope_select_bounds_an_admin_to_their_own_company():
    """`building_ids is None` means "every building in the company" — the company, not every
    company (services/principal.py's own docstring). This used to return the query untouched,
    so any admin listing work orders, assets or locations received every other tenant's rows.
    The rows carry no organization of their own, so the boundary runs through
    plenum_cafm.buildings, which does."""
    org = uuid4()
    p = P.Principal(user_id=uuid4(), email="x@example.com", organization_id=org,
                    role="admin", building_ids=None)
    sql = compiled(P.scope_select(select(WorkOrder), p, WorkOrder.building_id))
    assert "WHERE" in sql, "an admin query must carry a boundary"
    assert "plenum_cafm.buildings" in sql
    assert "organization_id" in sql


def test_scope_select_still_lets_a_superadmin_read_across_companies():
    """The one role meant to see every tenant keeps doing so."""
    q = select(WorkOrder)
    p = P.Principal(user_id=uuid4(), email="x@example.com", organization_id=uuid4(),
                    role="superadmin", building_ids=None)
    assert P.scope_select(q, p, WorkOrder.building_id) is q


def test_scope_select_fails_closed_for_a_principal_with_no_company():
    """Failing closed is the only safe default for a tenancy boundary. No active account is
    in this state, so nothing legitimate is refused by it."""
    assert "WHERE false" in compiled(P.scope_select(select(WorkOrder), make(None), WorkOrder.building_id))


def test_scope_select_narrows_a_user_to_their_buildings():
    q = P.scope_select(select(WorkOrder), make((MINE, ALSO_MINE)), WorkOrder.building_id)
    sql = compiled(q)
    assert "work_orders.building_id IN (" in sql
    params = q.compile(dialect=postgresql.dialect()).params
    assert {b for v in params.values() for b in (v if isinstance(v, list) else [v])} == {MINE, ALSO_MINE}


def test_scope_select_with_nothing_allocated_matches_no_row():
    assert "WHERE false" in compiled(P.scope_select(select(Asset), make(()), Asset.building_id))


def test_building_clause_for_raw_sql_matches_operations_intelligence():
    assert P.building_clause(make(None), "building_id") == ("", {})
    assert P.building_clause(make(()), "building_id") == (" AND FALSE", {})
    sql, params = P.building_clause(make((MINE,)), "w.building_id")
    assert sql == " AND w.building_id = ANY(CAST(:scope_buildings AS uuid[]))"
    assert params == {"scope_buildings": [str(MINE)]}


def test_assert_building_refuses_with_the_same_reason_as_upstream():
    P.assert_building(make(None), THEIRS)                 # an admin: anything
    P.assert_building(make((MINE,)), MINE)                # theirs: fine
    with pytest.raises(HTTPException) as e:
        P.assert_building(make((MINE,)), THEIRS)
    assert e.value.status_code == 403
    assert e.value.detail["reason"] == "building_not_allocated"
    with pytest.raises(HTTPException):
        P.assert_building(make((MINE,)), None)            # a work order on no building is not theirs


# ── the routes hand the narrowed statement to the session ─────────────────────

class Recording:
    """A session that records each statement and answers with the rows it was given."""
    def __init__(self, rows=()):
        self.rows, self.statements = list(rows), []

    class _R:
        def __init__(self, rows): self._rows = rows
        def scalars(self): return self
        def all(self): return self._rows
        def scalar_one_or_none(self): return self._rows[0] if self._rows else None
        # The list routes count before paging, over the same scoped query as the rows.
        def scalar_one(self): return len(self._rows)

    async def execute(self, stmt, *a, **k):
        self.statements.append(stmt)
        return self._R(self.rows)
    async def commit(self): pass
    async def rollback(self): pass
    async def refresh(self, *a): pass
    async def flush(self): pass
    def add(self, *a): pass


@pytest.fixture
def client():
    yield TestClient(app, raise_server_exceptions=True)
    app.dependency_overrides.clear()


def wire(client_, principal, session):
    async def _s():
        yield session
    async def _p():
        return principal
    app.dependency_overrides[get_session] = _s
    app.dependency_overrides[P.current_principal] = _p


def test_every_read_route_needs_a_caller(client):
    async def _s():
        yield Recording()
    app.dependency_overrides[get_session] = _s
    for path in ("/api/work-orders/", "/api/work-orders/filter/active", "/api/assets",
                 "/api/locations", "/api/dashboard/stats", "/api/work-orders/WO-1"):
        r = client.get(path)
        assert r.status_code == 401, (path, r.status_code, r.text[:120])


@pytest.mark.parametrize("path,table", [
    ("/api/work-orders/", "work_orders"),
    ("/api/work-orders/filter/active", "work_orders"),
    ("/api/work-orders/filter/pending-approval", "work_orders"),
    ("/api/assets", "assets"),
    ("/api/locations", "locations"),
])
def test_a_users_list_is_narrowed_in_sql(client, path, table):
    s = Recording()
    wire(client, make((MINE,)), s)
    r = client.get(path)
    assert r.status_code == 200, (path, r.text[:200])
    assert r.json() == []
    # Every statement the route issued, not only the last: the pre-paging count must carry
    # the same predicate as the rows, or a restricted caller is told "0 of 54".
    scoped = [st for st in s.statements if table in compiled(st)]
    assert scoped, path
    assert all(f"{table}.building_id IN (" in compiled(st) for st in scoped), path


def test_the_dashboard_counts_only_the_callers_buildings(client):
    s = Recording()
    wire(client, make((MINE,)), s)
    assert client.get("/api/dashboard/stats").status_code == 200
    assert all("building_id IN (" in compiled(st) for st in s.statements)


def test_an_admin_list_is_not_narrowed(client):
    s = Recording()
    wire(client, make(None, role="admin"), s)
    assert client.get("/api/work-orders/").status_code == 200
    assert "building_id IN" not in compiled(s.statements[-1])


def test_a_work_order_on_another_building_is_refused_not_hidden(client):
    wo = WorkOrder(work_order_id="WO-1", building_id=THEIRS, status="pending_approval")
    wire(client, make((MINE,)), Recording([wo]))
    r = client.get("/api/work-orders/WO-1")
    assert r.status_code == 403, r.text[:200]
    assert "building_not_allocated" in r.text


def test_a_work_order_on_their_own_building_is_returned(client):
    wo = WorkOrder(work_order_id="WO-2", building_id=MINE, status="pending_approval")
    wire(client, make((MINE,)), Recording([wo]))
    r = client.get("/api/work-orders/WO-2")
    assert r.status_code == 200, r.text[:200]
    assert r.json()["building_id"] == str(MINE)


def test_creating_a_work_order_on_another_building_is_refused(client):
    wire(client, make((MINE,)), Recording())
    r = client.post("/api/work-orders/", json={
        "source": "manual", "asset": "AHU-1", "location": "Roof", "issue_description": "Noise",
        "requester_name": "A", "requester_email": "a@b.co", "building_id": str(THEIRS)})
    assert r.status_code == 403, r.text[:200]


def test_an_asset_on_another_building_is_refused(client):
    a = Asset(asset_id=uuid4(), asset_name="Chiller", building_id=THEIRS)
    wire(client, make((MINE,)), Recording([a]))
    r = client.get(f"/api/assets/{a.asset_id}")
    assert r.status_code == 403, r.text[:200]


# ── a superadmin viewing one company gets that company's work orders ───────────
# /api/assets took organization_id and /api/work-orders/ did not, so a superadmin viewing
# company X got X's assets beside every company's work orders. The Assets page matches an
# order to an asset by name when the order carries no asset_id — and none raised through
# this service does — so company Y's forty open orders on "AHU-1" were counted against X's
# AHU-1 (28 Sep 2026). The two reads have to describe one portfolio.

def _org_params(stmt) -> set:
    """Every bound value in the statement that is a company id."""
    params = stmt.compile(dialect=postgresql.dialect()).params
    return {v for v in params.values() if isinstance(v, UUID)} & {ORG_A, ORG_B}


ORG_A, ORG_B = uuid4(), uuid4()


def _su(org=ORG_A):
    return P.Principal(user_id=uuid4(), email="s@example.com", organization_id=org,
                       role="superadmin", building_ids=None)


def _admin(org=ORG_A):
    return P.Principal(user_id=uuid4(), email="a@example.com", organization_id=org,
                       role="admin", building_ids=None)


def test_a_superadmin_naming_a_company_gets_only_its_work_orders(client):
    s = Recording()
    wire(client, _su(ORG_A), s)
    r = client.get("/api/work-orders/", params={"organization_id": str(ORG_B)})
    assert r.status_code == 200, r.text[:200]
    sql = compiled(s.statements[-1])
    assert "plenum_cafm.buildings" in sql, "the read must be drawn through the company's buildings"
    assert _org_params(s.statements[-1]) == {ORG_B}, "the company asked for, not their own"


def test_naming_another_company_cannot_widen_anyone_elses_work_orders(client):
    """The parameter only narrows: an admin naming another company still gets their own."""
    s = Recording()
    wire(client, _admin(ORG_A), s)
    r = client.get("/api/work-orders/", params={"organization_id": str(ORG_B)})
    assert r.status_code == 200, r.text[:200]
    assert _org_params(s.statements[-1]) == {ORG_A}


@pytest.mark.parametrize("who,asked", [
    (_su, ORG_B), (_su, None), (_su, "not-a-company"), (_admin, ORG_B), (_admin, None),
])
def test_work_orders_and_assets_answer_for_the_same_company(client, who, asked):
    """Whatever the caller and whatever they ask for, the two reads the Assets page joins are
    bounded by the same company."""
    principal = who()
    params = {} if asked is None else {"organization_id": str(asked)}
    wos, assets = Recording(), Recording()
    wire(client, principal, wos)
    assert client.get("/api/work-orders/", params=params).status_code == 200
    wire(client, principal, assets)
    assert client.get("/api/assets", params=params).status_code == 200
    assert _org_params(wos.statements[-1]) == _org_params(assets.statements[-1]), (who, asked)
