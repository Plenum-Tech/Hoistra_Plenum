"""A paged list must end its ORDER BY on a key no two rows share.

The Assets page reads every page of /api/work-orders/ and /api/assets with OFFSET paging and
joins them. Work orders were ordered by created_at alone and assets by asset_name alone, and
neither is unique: a workbook migration loads every row in one transaction, so every row
carries the same DEFAULT now(), and asset names repeat across buildings. Postgres returns
tied rows in whatever order the plan of that one page produces — a bounded heap for the
early pages, a quicksort for the later ones — so across pages some rows came back several
times and others never came back at all (28 Sep 2026: 1,985 tied work orders, one returned
four times, t201 / t401 / t601 never). The page count still summed to N, because each repeat
displaced a row that was skipped, so nothing downstream noticed.

The primary key as the last sort key makes the order total, and a total order is the only
kind OFFSET paging can walk without repeating or losing a row.
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql

from src.app import app
from src.db import get_session
from src.models.asset import Asset
from src.models.location import Location
from src.models.work_order import WorkOrder
from src.services import principal as P


class _Recording:
    """A session that records each statement and answers with no rows."""

    def __init__(self):
        self.statements = []

    class _R:
        def scalars(self): return self
        def all(self): return []
        def scalar_one(self): return 0

    async def execute(self, stmt, *a, **k):
        self.statements.append(stmt)
        return self._R()


@pytest.fixture
def recorded():
    session = _Recording()

    async def _s():
        yield session

    async def _p():
        return P.Principal(user_id=uuid4(), email="x@example.com", organization_id=uuid4(),
                           role="admin", building_ids=None)

    app.dependency_overrides[get_session] = _s
    app.dependency_overrides[P.current_principal] = _p
    yield TestClient(app, raise_server_exceptions=True), session
    app.dependency_overrides.clear()


def _order_by(stmt) -> list[str]:
    sql = str(stmt.compile(dialect=postgresql.dialect())).replace("\n", " ")
    assert " ORDER BY " in sql, sql
    keys = sql.split(" ORDER BY ", 1)[1].split(" LIMIT ", 1)[0]
    return [k.strip() for k in keys.split(",")]


def _pk(model) -> str:
    """The primary key as the compiled SQL names it, read from the model rather than typed
    out: every table here maps it to a Python attribute with a different name from the column."""
    (col,) = model.__table__.primary_key.columns
    return f"{model.__table__.fullname}.{col.name}"


def _paged(session) -> list:
    return [st for st in session.statements
            if " OFFSET " in str(st.compile(dialect=postgresql.dialect()))]


@pytest.mark.parametrize("page", [1, 2, 7])
def test_the_work_order_list_ends_its_order_on_the_primary_key(recorded, page):
    client, session = recorded
    assert client.get("/api/work-orders/", params={"page": page, "limit": 200}).status_code == 200
    (stmt,) = _paged(session)
    keys = _order_by(stmt)
    assert keys[0] == "plenum_cafm.work_orders.created_at DESC", "newest first is still the order"
    assert keys[-1].split()[0] == _pk(WorkOrder), keys


@pytest.mark.parametrize("page", [1, 2, 7])
def test_the_asset_list_ends_its_order_on_the_primary_key(recorded, page):
    client, session = recorded
    assert client.get("/api/assets", params={"page": page, "limit": 200}).status_code == 200
    (stmt,) = _paged(session)
    keys = _order_by(stmt)
    assert keys[0] == "plenum_cafm.assets.asset_name", "alphabetical is still the order people read"
    assert keys[-1].split()[0] == _pk(Asset), keys


@pytest.mark.parametrize("page", [1, 2, 7])
def test_the_location_list_ends_its_order_on_the_primary_key(recorded, page):
    """Same shape, same failure: paged by name, and a location name like "Plant Room" or
    "Roof" repeats in every building."""
    client, session = recorded
    assert client.get("/api/locations", params={"page": page, "limit": 500}).status_code == 200
    (stmt,) = _paged(session)
    keys = _order_by(stmt)
    assert keys[0] == "plenum_cafm.locations.name", "alphabetical is still the order people read"
    assert keys[-1].split()[0] == _pk(Location), keys


def test_the_tiebreaker_is_the_tables_real_key_not_a_look_alike():
    """wo_code is not unique on one database (948 distinct of 1,728 set) and asset_code is
    free text, so neither would make the order total. The key has to be the id column."""
    assert _pk(WorkOrder) == "plenum_cafm.work_orders.id"
    assert _pk(Asset) == "plenum_cafm.assets.id"
    assert _pk(Location) == "plenum_cafm.locations.id"
