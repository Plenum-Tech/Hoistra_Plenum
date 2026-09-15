"""Asset categories, read through a service that knows who is asking.

``assets.category_id`` is a real foreign key, and until now the only thing that resolved it
to a name was cafm-connector-service's ``/asset-categories`` — which had no authentication at
all. So the choice for a page that wanted to show "Chiller" instead of a UUID was between an
unauthenticated service and nothing.

The table is not spelled the same everywhere, and not typed the same either: one database
has ``name`` and ``parent_id`` keyed by uuid, another has ``category_name`` and
``parent_category_id`` keyed by integer. Rather than pick one and break the other, the column
names are read from ``information_schema`` once per process and cached — the same way
``graph_shape`` decides what the graph tables actually look like — and keys are compared as
text so the type does not have to be known at import time.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.logging import get_logger

log = get_logger(__name__)

#: Column spellings seen in the wild, most specific first.
_NAME_COLS = ("category_name", "name", "title", "label")
_PARENT_COLS = ("parent_category_id", "parent_id")

_shape: dict[str, Any] | None = None


async def catalogue_shape(session: AsyncSession) -> dict[str, Any]:
    """Which columns ``plenum_cafm.asset_categories`` actually has, cached per process."""
    global _shape
    if _shape is not None:
        return _shape
    rows = (
        await session.execute(
            text(
                """SELECT column_name FROM information_schema.columns
                    WHERE table_schema = 'plenum_cafm' AND table_name = 'asset_categories'"""
            )
        )
    ).all()
    columns = {r[0] for r in rows}
    _shape = {
        "exists": bool(columns),
        "columns": columns,
        "name_col": next((c for c in _NAME_COLS if c in columns), None),
        "parent_col": next((c for c in _PARENT_COLS if c in columns), None),
        "has_description": "description" in columns,
    }
    return _shape


async def list_categories(
    session: AsyncSession, *, organization_id: UUID | None, limit: int = 500
) -> list[dict[str, Any]]:
    """Every asset category in the caller's company, with the count of assets using it.

    Categories are company-wide, not building-scoped: the table carries no building column,
    and a category is a classification rather than a thing that sits somewhere.
    """
    shape = await catalogue_shape(session)
    if not shape["exists"] or not shape["name_col"]:
        return []
    name_col = shape["name_col"]
    parent = f"c.{shape['parent_col']}::text" if shape["parent_col"] else "NULL"
    description = "c.description" if shape["has_description"] else "NULL"
    where = "WHERE c.organization_id = CAST(:org AS uuid)" if organization_id else ""
    rows = (
        await session.execute(
            text(
                f"""SELECT c.id::text AS category_id,
                           c.{name_col} AS name,
                           {description} AS description,
                           {parent} AS parent_category_id,
                           (SELECT count(*) FROM plenum_cafm.assets a
                             WHERE a.category_id = c.id) AS asset_count
                      FROM plenum_cafm.asset_categories c
                      {where}
                     ORDER BY c.{name_col}
                     LIMIT :limit"""
            ),
            {"org": str(organization_id) if organization_id else None, "limit": int(limit)},
        )
    ).mappings().all()
    return [dict(r) for r in rows]


async def names_for(session: AsyncSession, category_ids: list[Any]) -> dict[str, str]:
    """``{category_id: name}`` for the categories these assets actually use.

    One query for a page of assets, rather than one per row or a join that would have to
    know the column name at import time.
    """
    ids = [str(c) for c in category_ids if c]
    if not ids:
        return {}
    shape = await catalogue_shape(session)
    if not shape["exists"] or not shape["name_col"]:
        return {}
    rows = (
        await session.execute(
            text(
                f"""SELECT id::text AS id, {shape['name_col']} AS name
                      FROM plenum_cafm.asset_categories
                     WHERE id::text = ANY(CAST(:ids AS text[]))"""
            ),
            {"ids": ids},
        )
    ).all()
    return {r[0]: r[1] for r in rows}
