"""Which rows a caller may see, for a table that is not known until the request arrives.

svc-udr authenticates every route and gates the table and agent routes on role. That stops an
anonymous read, and it stops an ordinary user. It does not stop an administrator of one company
reading another company's rows, because no route applied a row filter at all:

    GET /backend/udr/api/tables/buildings/records

ran ``SELECT * FROM plenum_cafm."buildings"`` with no predicate. An admin of a company that owns
no buildings still got every building in the database.

The reason it was left that way is written in principal.py: a company filter needs to know which
column holds the company, and the table is named by the request. That is true of a filter written
by hand. It is not true of one derived at request time — the table IS known by the time the query
is built, so its columns can be read from ``information_schema`` and the predicate composed from
what is actually there. That is the same runtime-probe approach the rest of this codebase uses
where the two databases disagree on shape.

What gets applied, per table:

    organization_id present  ->  AND "organization_id"::text = :scope_org
    building_id present      ->  AND "building_id"::text IN (:scope_b0, …)   [when allocated]
    neither present          ->  nothing

Casts on both sides because the two databases disagree on the type of these columns (uuid here,
character varying there); comparing as text is the one form that works against both and matches
how scoping is written elsewhere in this repo.

Three conventions carried over from the other services, so scoping means one thing everywhere:

  * ``building_ids is None``  -> every building in the company. No building predicate.
  * ``building_ids == ()``    -> allocated to nothing. ``AND FALSE`` — not "no filter".
  * a superadmin is unrestricted. That role administers the platform, and narrowing it here
    would break the cross-company views it exists to provide.

A caller with no company at all gets ``AND FALSE`` on any table that has an organization_id.
Returning everything to a principal whose company is unknown is the exact failure being fixed,
and an empty result is the safe reading of "we do not know who you belong to".

Tables with neither column pass through unfiltered. That is a deliberate choice and it is worth
stating what it costs: 99 of the 224 tables in plenum_cafm have no organization_id and no
building_id, and while most are reference data (countries, currencies, regulation_packs) or
per-user rows, some are audit logs. Those stay readable by any admin. Narrowing them needs a
join route per table, which is a separate piece of work — see UNSCOPED_WITH_FK_ROUTE below for
the ones that have an obvious route when someone wants it.
"""
from __future__ import annotations

import re
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..core.logging import get_logger
from .principal import Principal

log = get_logger(__name__)

SCHEMA = settings.db_schema

#: The columns that carry scope directly. Order matters only for readable SQL.
ORG_COLUMN = "organization_id"
BUILDING_COLUMN = "building_id"

#: Tables with no scope column that DO have a foreign key reaching one. Not used yet — recorded
#: here because the probe cannot infer them and the next person should not have to rediscover
#: them. ``asset_id`` reaches a building through assets; ``vendor_id`` reaches a company
#: through vendors.
UNSCOPED_WITH_FK_ROUTE: dict[str, tuple[str, str, str]] = {
    # table                  -> (local column, target table, target scope column)
    "asset_documents":        ("asset_id", "assets", BUILDING_COLUMN),
    "asset_events":           ("asset_id", "assets", BUILDING_COLUMN),
    "asset_warranties":       ("asset_id", "assets", BUILDING_COLUMN),
    "inspections":            ("asset_id", "assets", BUILDING_COLUMN),
    "maintenance_history":    ("asset_id", "assets", BUILDING_COLUMN),
    "wo_assets":              ("asset_id", "assets", BUILDING_COLUMN),
    "work_order_assets":      ("asset_id", "assets", BUILDING_COLUMN),
    "vendor_contacts":        ("vendor_id", "vendors", ORG_COLUMN),
    "work_order_businesses":  ("vendor_id", "vendors", ORG_COLUMN),
}

#: Never readable or writable through this service, by anyone, superadmin included.
#:
#: These are not "unscoped tables we have not got to yet" — scoping them is the wrong frame.
#: They hold live credentials and bearer-equivalent tokens, and the Universal Database Reader is
#: reachable at /backend/udr/ from the public internet and driven by an agent that composes its
#: own queries from a user's sentence. There is no question about a building or a work order
#: whose answer requires a one-time passcode, so the honest setting is off.
#:
#: Counted on the live database when this was written: auth_otp_codes 9 rows, auth_sessions 183,
#: approval_action_tokens 154 — every one of them readable by an administrator of any company
#: before this list existed.
#:
#: A denied table has no view in plenum_scoped either, so caller-supplied SELECT naming one
#: fails to resolve rather than falling back to the base table.
DENIED_TABLES: frozenset[str] = frozenset({
    "auth_otp_codes",          # one-time passcodes; production sign-in is OTP-only
    "auth_sessions",           # live session records
    "auth_role_changes",       # audit of privilege changes
    "approval_action_tokens",  # single-use tokens that authorise an approval by possession
})


def is_denied(table: str) -> bool:
    """Whether this table is closed to the Universal Database Reader entirely."""
    return table.strip().lower() in DENIED_TABLES


#: table name -> the scope columns it actually has. Filled on first use per table.
#: Module level, like the shape caches in the other services: the schema does not change
#: between requests, and re-reading information_schema per row read is a needless round trip.
_columns: dict[str, frozenset[str]] = {}


def reset_cache() -> None:
    """Forget the probed schema. For tests, and for a process that outlives a migration."""
    _columns.clear()


async def scope_columns(session: AsyncSession, table: str) -> frozenset[str]:
    """Which of the scope columns ``table`` actually has, read once and remembered."""
    cached = _columns.get(table)
    if cached is not None:
        return cached
    result = await session.execute(
        text(
            """
            SELECT column_name
              FROM information_schema.columns
             WHERE table_schema = :schema
               AND table_name   = :table
               AND column_name  = ANY(:wanted)
            """
        ),
        {"schema": SCHEMA, "table": table, "wanted": [ORG_COLUMN, BUILDING_COLUMN]},
    )
    found = frozenset(str(r[0]) for r in result)
    _columns[table] = found
    log.debug("scope.columns_probed", table=table, columns=sorted(found))
    return found


def predicate_for(
    columns: frozenset[str],
    principal: Principal | None,
) -> tuple[str, dict[str, Any]]:
    """``(sql_fragment, params)`` restricting a table with these columns to this caller.

    The fragment is a bare boolean expression with no WHERE and no AND — the caller decides
    how to attach it, because some queries already have a WHERE and some do not. An empty
    string means "no restriction applies", which is NOT the same as a fragment of ``FALSE``.

    ``principal`` of None means the query is not being made on anybody's behalf (a migration,
    a test). It is left unrestricted deliberately: every route that reaches this passes a real
    principal, and inventing a restriction for an internal caller would silently break them.
    """
    if principal is None or principal.is_superadmin:
        return "", {}

    parts: list[str] = []
    params: dict[str, Any] = {}

    if ORG_COLUMN in columns:
        if principal.organization_id is None:
            # Unknown company. Everything, or nothing — nothing is the safe half.
            return "FALSE", {}
        parts.append(f'"{ORG_COLUMN}"::text = :scope_org')
        params["scope_org"] = str(principal.organization_id)

    if BUILDING_COLUMN in columns and principal.building_ids is not None:
        if not principal.building_ids:
            # Allocated to no building. () is not None, and must not read as "no filter".
            return "FALSE", {}
        keys = [f"scope_b{i}" for i in range(len(principal.building_ids))]
        placeholders = ", ".join(f":{k}" for k in keys)
        parts.append(f'"{BUILDING_COLUMN}"::text IN ({placeholders})')
        params.update({k: str(b) for k, b in zip(keys, principal.building_ids)})

    return (" AND ".join(parts), params) if parts else ("", {})


async def predicate(
    session: AsyncSession,
    table: str,
    principal: Principal | None,
) -> tuple[str, dict[str, Any]]:
    """``predicate_for`` against the live schema for ``table``."""
    return predicate_for(await scope_columns(session, table), principal)


def combine(where_clause: str, fragment: str) -> str:
    """Attach a scope fragment to a WHERE clause that may or may not already exist."""
    if not fragment:
        return where_clause
    stripped = (where_clause or "").strip()
    if not stripped:
        return f"WHERE {fragment}"
    return f"{stripped} AND {fragment}"


def describe(principal: Principal | None, columns: frozenset[str]) -> dict[str, Any]:
    """What was applied, for the response and the log. A caller who gets fewer rows than they
    expected should be able to see why without reading the source."""
    if principal is None:
        return {"scoped": False, "reason": "no_principal"}
    if principal.is_superadmin:
        return {"scoped": False, "reason": "superadmin"}
    if not (columns & {ORG_COLUMN, BUILDING_COLUMN}):
        return {"scoped": False, "reason": "no_scope_column_on_table"}
    return {
        "scoped": True,
        "organization_id": str(principal.organization_id) if principal.organization_id else None,
        "building_ids": (None if principal.building_ids is None
                         else [str(b) for b in principal.building_ids]),
        "by": sorted(columns & {ORG_COLUMN, BUILDING_COLUMN}),
    }


# ── The scoped-views route, for SQL this service did not write ───────────────────────────
#
# A predicate can be composed for a query whose table is known. It cannot be injected into a
# caller-supplied SELECT with joins, CTEs and subqueries. So for that route the restriction
# lives in the objects the SQL names: migrations/udr_scoped_views.sql builds one self-filtering
# view per table in `plenum_scoped`, and the caller's SQL is pointed at that schema instead.

#: The schema of self-filtering views. Must match udr_scoped_views.sql.
SCOPED_SCHEMA = "plenum_scoped"

#: Transaction-local settings the views read. Must match udr_scoped_views.sql.
SETTING_ORG = "app.udr_org"
SETTING_BUILDINGS = "app.udr_buildings"
SETTING_UNRESTRICTED = "app.udr_unrestricted"

#: `plenum_cafm.t` and `"plenum_cafm".t`, in any case, with or without space before the dot.
_QUALIFIED = re.compile(r'(?:"' + SCHEMA + r'"|\b' + SCHEMA + r'\b)\s*\.', re.IGNORECASE)


def redirect_to_scoped_schema(sql: str) -> str:
    """Point a caller's SELECT at the filtered views instead of the base tables.

    The agent is told to write ``plenum_cafm.buildings``, and a search_path alone would not
    catch that — a schema-qualified name ignores it. So qualified references are rewritten and
    the search_path covers whatever is left unqualified.

    The rewrite is textual, which has one honest limitation: ``plenum_cafm`` appearing inside a
    string literal is rewritten too. The consequence is a changed literal in a query nobody
    writes, which is why this is preferred over parsing SQL to find it.
    """
    return _QUALIFIED.sub(f"{SCOPED_SCHEMA}.", sql)


def allocated_to_nothing(principal: Principal | None) -> bool:
    """True when this caller holds no building at all and is not unrestricted.

    Handled in Python rather than in the view. The view distinguishes "no building filter" from
    "this list of buildings", and an empty list has no honest spelling in a setting that is a
    string: NULL and '' both have to mean "no filter" (a pooled connection cannot be relied on
    to tell them apart across a SET LOCAL), which would turn "allocated to nothing" into
    "allocated to everything" — the failure this whole change exists to stop. So the empty case
    never reaches SQL: it answers with no rows here.
    """
    if principal is None or principal.is_superadmin:
        return False
    return principal.building_ids is not None and not principal.building_ids


def session_settings(principal: Principal | None) -> list[tuple[str, str]]:
    """The ``(name, value)`` settings to apply for this caller before their SQL runs.

    Every one is set on every request, including the ones that mean "unrestricted". Setting
    them unconditionally is the point: these are transaction-local, and a value left over from
    a previous caller on a pooled connection would scope one user's query to another's company.

    Values are always strings — never None. ``''`` means "not restricted by this dimension";
    the empty-building-list case is refused by ``allocated_to_nothing`` before it gets here.
    """
    if principal is None or principal.is_superadmin:
        return [(SETTING_UNRESTRICTED, "1"), (SETTING_ORG, ""), (SETTING_BUILDINGS, "")]
    return [
        (SETTING_UNRESTRICTED, "0"),
        (SETTING_ORG, str(principal.organization_id) if principal.organization_id else ""),
        (SETTING_BUILDINGS,
         "" if principal.building_ids is None
         else ",".join(str(b) for b in principal.building_ids)),
    ]


__all__ = [
    "DENIED_TABLES",
    "SCOPED_SCHEMA",
    "SETTING_BUILDINGS",
    "SETTING_ORG",
    "SETTING_UNRESTRICTED",
    "allocated_to_nothing",
    "redirect_to_scoped_schema",
    "session_settings",
    "BUILDING_COLUMN",
    "ORG_COLUMN",
    "UNSCOPED_WITH_FK_ROUTE",
    "combine",
    "describe",
    "is_denied",
    "predicate",
    "predicate_for",
    "reset_cache",
    "scope_columns",
]
