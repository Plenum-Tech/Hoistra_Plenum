"""Assets across the whole portfolio, and a category that reads as a word.

Two gaps the frontend developer hit on the Assets page: every asset route wanted an asset_id
or a building_id already in hand, so there was no way to list a portfolio; and ``category_id``
is a real foreign key that only cafm-connector-service could resolve to a name — the service
that had no authentication at all.

``GET /assets`` now takes no required parameter, filters on things that exist, and reports its
pre-paging total. ``GET /asset-categories`` resolves the key. These run without a database —
the in-memory engine cannot build the ARRAY-typed tables — so they pin the query the route
builds, the filters, and the column-spelling probe that keeps both databases working.
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import String, cast, func, select
from sqlalchemy.dialects import postgresql

from src.models.asset import Asset
from src.services import asset_catalogue
from src.services import principal as P

MINE, THEIRS = uuid4(), uuid4()


def make(building_ids, role="user"):
    return P.Principal(user_id=uuid4(), email="x@example.com", organization_id=None,
                       role=role, building_ids=building_ids)


def compiled(q) -> str:
    return " ".join(str(q.compile(dialect=postgresql.dialect())).split())


# ── the portfolio list ────────────────────────────────────────────────────────

def test_listing_assets_needs_no_building_id():
    """The gap: every asset route required a building_id or an asset_id already in hand."""
    from src.api.routes.assets import list_assets

    params = list_assets.__annotations__
    assert "building_id" in params
    import inspect

    sig = inspect.signature(list_assets)
    assert sig.parameters["building_id"].default.default is None, "building_id must be optional"


def test_an_unrestricted_caller_sees_the_whole_portfolio_and_a_restricted_one_does_not():
    everyone = P.scope_select(select(Asset), make(None), Asset.building_id)
    restricted = P.scope_select(select(Asset), make((MINE,)), Asset.building_id)
    assert "building_id IN" not in compiled(everyone)
    assert "building_id IN" in compiled(restricted)
    # Allocated to nothing is a predicate matching no row, not the absence of one.
    assert "WHERE false" in compiled(
        P.scope_select(select(Asset), make(()), Asset.building_id)
    )


def test_the_total_is_counted_over_the_same_scoped_query_as_the_rows():
    """A count over the unscoped table beside scoped rows reads as "6 of 54"."""
    q = P.scope_select(select(Asset), make((MINE,)), Asset.building_id)
    total = select(func.count()).select_from(q.subquery())
    assert "building_id IN" in compiled(total)


def test_the_search_covers_the_code_and_serial_not_only_the_name():
    from sqlalchemy import or_

    like = "%pump%"
    q = select(Asset).where(or_(Asset.asset_name.ilike(like),
                                Asset.asset_code.ilike(like),
                                Asset.serial_number.ilike(like)))
    sql = compiled(q)
    assert "asset_name" in sql and "asset_code" in sql and "serial_number" in sql


def test_the_category_filter_compares_as_text_because_the_key_type_differs():
    """category_id is uuid in one database and integer in another; a bare comparison
    raises "operator does not exist: integer = uuid" on the second."""
    q = select(Asset).where(cast(Asset.category_id, String) == "7")
    assert "CAST(plenum_cafm.assets.category_id AS VARCHAR)" in compiled(q)


# ── the columns that were real but never mapped ───────────────────────────────

def test_the_asset_model_maps_the_columns_the_page_needs():
    for column in ("category_id", "location_id", "criticality", "health_score",
                   "installation_date", "asset_code", "status"):
        assert hasattr(Asset, column), f"{column} is a real column but is not mapped"


def test_the_response_carries_the_category_name_beside_its_id():
    from src.api.schemas.asset import AssetResponse

    fields = AssetResponse.model_fields
    assert "category_id" in fields and "category_name" in fields
    item = AssetResponse(asset_id=str(uuid4()), asset_name="Chiller 1",
                         category_id="7", category_name="Chillers", health_score=89)
    assert item.category_name == "Chillers" and item.health_score == 89.0


# ── the column-spelling probe ─────────────────────────────────────────────────

class _Result:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _Session:
    def __init__(self, columns):
        self.columns = columns
        self.queries = []

    async def execute(self, statement, params=None):
        self.queries.append((str(statement), params))
        return _Result([(c,) for c in self.columns])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "columns, name_col, parent_col",
    [
        ({"id", "organization_id", "name", "description", "parent_id"}, "name", "parent_id"),
        ({"id", "organization_id", "category_name", "parent_category_id"},
         "category_name", "parent_category_id"),
    ],
)
async def test_the_catalogue_reads_the_column_names_rather_than_assuming_them(
    columns, name_col, parent_col
):
    """One database spells it name/parent_id, another category_name/parent_category_id.
    Picking either at import time breaks the other."""
    asset_catalogue._shape = None
    shape = await asset_catalogue.catalogue_shape(_Session(columns))
    assert shape["name_col"] == name_col
    assert shape["parent_col"] == parent_col
    asset_catalogue._shape = None


@pytest.mark.asyncio
async def test_a_missing_categories_table_is_an_empty_list_not_a_failure():
    asset_catalogue._shape = None
    assert await asset_catalogue.list_categories(_Session(set()), organization_id=None) == []
    assert await asset_catalogue.names_for(_Session(set()), [uuid4()]) == {}
    asset_catalogue._shape = None


@pytest.mark.asyncio
async def test_no_categories_to_resolve_asks_the_database_nothing():
    asset_catalogue._shape = None
    session = _Session({"id", "name"})
    assert await asset_catalogue.names_for(session, [None, None]) == {}
    assert session.queries == []
    asset_catalogue._shape = None
