"""
BE1 — UDR agent tools: direct database access for user lookup, schema discovery,
and generic table queries.
Two-gate SQL injection protection: regex + parameterised queries only.
"""
import itertools
import json
import re
import time
from dataclasses import dataclass
from typing import Any

import httpx
import structlog
from langchain_core.tools import tool
from sqlalchemy import text

from .. import database
from ..config import settings
from ..http_client import PageEngineOwnsThisTurn, catalogue_closed, request as _request
from ..services.principal import caller_principal, restrict_records, table_building_clause, table_columns
from . import name_match
from . import ontology_qa as _oq

log = structlog.get_logger(__name__)

_SAFE_IDENT = re.compile(r"^[a-z_][a-z0-9_]{0,63}$")
_MAX_ROWS = 100
# Same predicate as ops-intelligence's shared.vendor_identity.live_vendor_sql (the source of
# truth), on the table's own name: query_table reads `FROM plenum_cafm.vendors` unaliased.
_LIVE_VENDORS = "COALESCE(lower(to_jsonb(vendors)->>'status'), 'active') <> 'deleted'"
#: A vendors read filtered on one of these is an exact lookup, so it keeps retired rows.
_VENDOR_KEY_COLUMNS = frozenset({"id", "vendor_code"})

# Schema cache: avoid re-querying information_schema on every session.
# Refreshes every 5 minutes so DDL changes are picked up without a restart.
_schema_cache: dict | None = None
_schema_cache_at: float = 0.0
_SCHEMA_TTL = 300  # seconds
_TIMEOUT = 60.0
_SERVICE = "udr"


@tool
async def get_schema() -> dict:
    """Return the live database schema for the plenum_cafm schema.

    Queries information_schema to return every table and its columns (with data
    types). Always call this FIRST before any query_table or compliance call so
    you know the exact table names and column names that exist right now.

    Returns a dict:
      {
        "tables": {
          "<table_name>": ["col1 (type)", "col2 (type)", ...],
          ...
        },
        "relationships": [
          {"from": "<table>.<column>", "to": "<table>.<column>"},   # declared foreign keys
          ...
        ]
      }

    JOIN ON `relationships`, NEVER ON TABLE-NAME SIMILARITY. A table named like a parent is
    not necessarily the parent: `meter_readings.meter_id` references `energy_meters.id`, not
    `meters.meter_id` — `meters` is a separate register and joining through it returns zero
    rows for a meter that has thousands of readings. If the column you want to join on is not
    in `relationships`, say so rather than guessing a key.

    Result is cached for 5 minutes so repeated calls within a session are free.
    """
    global _schema_cache, _schema_cache_at

    _owners = catalogue_closed()
    if _owners:  # the same door the HTTP tools go through; this one reads the database directly
        return _err(PageEngineOwnsThisTurn(_owners), "get_schema")

    now = time.monotonic()
    if _schema_cache is not None and (now - _schema_cache_at) < _SCHEMA_TTL:
        log.debug("udr.get_schema.cache_hit")
        return _schema_cache

    async with database.AsyncSessionLocal() as session:
        try:
            rows = (await session.execute(
                text("""
                    SELECT
                        c.table_name,
                        c.column_name,
                        c.data_type
                    FROM information_schema.columns c
                    JOIN information_schema.tables t
                        ON t.table_name  = c.table_name
                       AND t.table_schema = c.table_schema
                    WHERE c.table_schema = 'plenum_cafm'
                      AND t.table_type   = 'BASE TABLE'
                    ORDER BY c.table_name, c.ordinal_position
                """),
            )).mappings().all()
        except Exception as exc:
            log.error("udr.get_schema.error", error=str(exc))
            return {"error": str(exc)}

    tables: dict[str, list[str]] = {}
    for row in rows:
        tbl = row["table_name"]
        col = f"{row['column_name']} ({row['data_type']})"
        tables.setdefault(tbl, []).append(col)

    # The declared foreign keys — how the tables actually relate, as opposed to how their
    # names suggest they relate. Without this the model joined meter_readings to `meters`
    # because the name fit, and reported zero readings for a building holding fifty; the
    # real parent is energy_meters and the database has said so all along.
    relationships: list[dict[str, str]] = []
    async with database.AsyncSessionLocal() as session:
        try:
            fks = (await session.execute(
                text("""
                    SELECT kcu.table_name  AS from_table,
                           kcu.column_name AS from_column,
                           ccu.table_name  AS to_table,
                           ccu.column_name AS to_column
                    FROM information_schema.table_constraints tc
                    JOIN information_schema.key_column_usage kcu
                      ON kcu.constraint_name = tc.constraint_name
                     AND kcu.table_schema    = tc.table_schema
                    JOIN information_schema.constraint_column_usage ccu
                      ON ccu.constraint_name = tc.constraint_name
                     AND ccu.table_schema    = tc.table_schema
                    WHERE tc.constraint_type = 'FOREIGN KEY'
                      AND tc.table_schema    = 'plenum_cafm'
                    ORDER BY kcu.table_name, kcu.column_name
                """),
            )).mappings().all()
            relationships = [
                {"from": f"{r['from_table']}.{r['from_column']}",
                 "to": f"{r['to_table']}.{r['to_column']}"}
                for r in fks
            ]
        except Exception as exc:  # noqa: BLE001 — the column list is still worth having
            log.warning("udr.get_schema.relationships_failed", error=str(exc)[:200])

    result = {"tables": tables, "relationships": relationships}
    _schema_cache = result
    _schema_cache_at = now
    log.info("udr.get_schema.refreshed", table_count=len(tables))
    return result


@tool
async def lookup_user(user_id: str) -> dict:
    """Look up a CAFM platform user by their UUID.

    Returns the user's name, email, roles list, department, and phone number.
    Use this whenever you need to resolve who a user is or what permissions they hold.

    Args:
        user_id: UUID string of the user to look up.
    """
    async with database.AsyncSessionLocal() as session:
        try:
            result = await session.execute(
                text("""
                    SELECT u.id, u.full_name, u.email, u.department, u.phone,
                           array_agg(r.name) FILTER (WHERE r.name IS NOT NULL) AS roles
                    FROM plenum_cafm.users u
                    LEFT JOIN plenum_cafm.user_roles ur ON ur.user_id = u.id
                    LEFT JOIN plenum_cafm.roles r ON r.id = ur.role_id
                    WHERE u.id = :user_id
                    GROUP BY u.id, u.full_name, u.email, u.department, u.phone
                """),
                {"user_id": user_id},
            )
            row = result.mappings().one_or_none()
        except Exception as exc:
            log.error("udr.lookup_user.error", user_id=user_id, error=str(exc))
            return {"error": str(exc)}

    if row is None:
        return {"error": f"User {user_id!r} not found"}

    return {
        "user_id": str(row["id"]),
        "name": row["full_name"],
        "email": row["email"],
        "department": row["department"],
        "phone": row["phone"],
        "roles": list(row["roles"] or []),
    }


@tool
async def query_table(table_name: str, filters: dict[str, Any] | None = None) -> list[dict]:
    """Query any table in the plenum_cafm schema with optional equality filters.

    Returns up to 100 records as a list of dicts. Use this to inspect live data
    in any CAFM table. All identifiers are validated before execution.

    A filter on a name or code column (name, *_name, title, label, *_code, *_ref, *_number)
    that matches nothing exactly is then matched however it is typed: any case, without the
    kind word ("JASMEET BUILDING" finds "JASMEET"), a code without its hyphen (B090765 finds
    B-090765), the words in another order, the start of a name ("Acme" finds "Acme Facilities
    Ltd"), and for a name one letter wrong — such rows carry `_name_match`; say so. A code is
    never matched by a part of it. A name of kind words alone ("the building") reads nothing.

    Always call get_schema() first so you use the correct table_name and
    column names — never guess them.

    On `vendors`, deleted vendors (status 'deleted') are left out of lists. A lookup by
    id or vendor_code still finds one (its name ends " (deleted)"), so the vendor a past
    work order names always resolves. To list them — only when the user asks about
    deleted vendors or history — filter on status.

    Args:
        table_name: Exact table name in plenum_cafm as returned by get_schema().
        filters: Optional dict of {column_name: value} equality filters.
                 Column names must match what get_schema() returned for this table.
    """
    if not _SAFE_IDENT.match(table_name):
        log.warning("udr.query_table.unsafe_table", table=table_name)
        return [{"error": f"Unsafe table name: {table_name!r}"}]

    params: dict[str, Any] = {}
    where_clause = ""

    if filters:
        conditions: list[str] = []
        for col, val in filters.items():
            if not _SAFE_IDENT.match(col):
                log.warning("udr.query_table.unsafe_column", column=col)
                return [{"error": f"Unsafe column name: {col!r}"}]
            param_key = f"p_{col}"
            conditions.append(f"{col} = :{param_key}")
            params[param_key] = val
        if conditions:
            where_clause = "WHERE " + " AND ".join(conditions)

    # A name of kind words alone ("the building") names no record (29 Oct 2026): nothing is read.
    kind = _kind_for(table_name)
    named = _named_filters(filters)
    if any(_NAME_COLUMN.search(c) and _names_nothing(v, kind) for c, v in named):
        return []

    # A deleted vendor keeps its row for its history (Hussain, 12 Oct 2026), so a plain read
    # of vendors would list, count and offer it. Left out unless the caller asks by status, or
    # looks one vendor up by id or code: that is resolving the vendor a closed work order or
    # invoice names, a history read, and the model cannot know beforehand it was deleted.
    keys = {str(c).lower() for c in (filters or {})}
    live_vendors = table_name == "vendors" and "status" not in keys and not (keys & _VENDOR_KEY_COLUMNS)
    if live_vendors:
        where_clause = (where_clause + " AND " if where_clause else "WHERE ") + _LIVE_VENDORS

    async with database.AsyncSessionLocal() as session:
        try:
            # The caller's buildings bound this read like every other. Decided from the
            # table's real columns (building_id, vendor, asset, site, meter), never from the
            # question; an admin gets no clause and a caller allocated to nothing gets none of
            # the rows.
            bsql, bparams = await table_building_clause(session, table_name)
            if bsql:
                where_clause = (where_clause + bsql) if where_clause else "WHERE " + bsql[len(" AND "):]
                params.update(bparams)
            sql = f"SELECT * FROM plenum_cafm.{table_name} {where_clause} LIMIT {_MAX_ROWS}"
            import time as _time

            from . import trace as _trace

            t0 = _time.perf_counter()
            result = await session.execute(text(sql), params)
            rows = [dict(r) for r in result.mappings().all()]
            _trace.on_sql(sql, params, rows, (_time.perf_counter() - t0) * 1000, label="query_table " + table_name)
            if not rows and named and bsql.strip() != "AND FALSE":
                # The equality above is how a name filter was always read, and it still goes first;
                # "JASMEET BUILDING", B090765 or "Acme" found nothing by it (29 Oct 2026).
                tail = (" AND " + _LIVE_VENDORS if live_vendors else "") + bsql
                rows = await _filters_however_typed(session, table_name, filters, named, kind, tail, bparams)
            return rows
        except Exception as exc:
            log.error("udr.query_table.error", table=table_name, error=str(exc))
            from . import trace as _trace

            _trace.on_sql(f"SELECT * FROM plenum_cafm.{table_name} ...", params, None, None, label="query_table " + table_name,
                          error=str(exc).splitlines()[0][:300])
            return [{"error": str(exc)}]


def _err(exc: Exception, op: str) -> dict:
    if isinstance(exc, httpx.HTTPStatusError):
        log.error(f"udr.{op}.http_error", status=exc.response.status_code, body=exc.response.text[:300])
        return {"error": exc.response.text[:300], "status_code": exc.response.status_code}
    log.error(f"udr.{op}.error", error=str(exc)[:300])
    return {"error": str(exc)[:300]}


# ── a name, however it is typed (29 Oct 2026) ─────────────────────────────────────────────────
# "HOW MANY FLOORS IN JASMEET BUILDING" found nothing: the building is named "JASMEET", and each
# lookup here compared the typed text its own way. find_location never read buildings and searched
# sites and locations for '%JASMEET BUILDING%'; find_asset did the same over assets, and
# get_asset_documents took the first of five such rows ("chiller 1" read Chiller 10); query_table
# compared a name filter with `=`; udr_search_records sent the phrase once to a substring search.
# They now share the one resolution ladder (agents/name_match.py, ontology_qa's SQL rungs): exact,
# compact (B090765 = B-090765), the phrase from the start of a word, every word in any order, one
# letter wrong — stopping at the first rung that finds a record and returning every record at that
# rung, never one picked. A phrase of kind words alone ("the building") names nothing, reads nothing.
#
# find_location, find_asset and get_asset_documents also read plenum_cafm with no company at all:
# acting for one company, "Bishopsgate Tower" answered from a site no company owns (14 Oct 2026).
# They now read inside the caller's scope (ontology_qa.ScopedReader: plenum_scoped's views, with the
# caller's company and buildings set for the transaction), and with no caller they read nothing.

#: Words for the kind of record, never part of its name: "JASMEET BUILDING" is "JASMEET".
_PLACE_KIND = frozenset({"building", "buildings", "site", "sites", "property", "properties",
                         "premises", "location", "locations"})
_ASSET_KIND = frozenset({"asset", "assets", "equipment"})
#: Company suffixes are not here: "Ltd" is part of a vendor's name, and "Acme" still finds
#: "Acme Facilities Ltd" by the phrase rung.
_VENDOR_KIND = frozenset({"vendor", "vendors", "contractor", "contractors", "supplier", "suppliers"})
_KIND_BY_TABLE = {"buildings": _PLACE_KIND, "sites": _PLACE_KIND, "locations": _PLACE_KIND,
                  "assets": _ASSET_KIND, "vendors": _VENDOR_KIND}
#: Records one name lookup returns. When more match, the answer says so; it never cuts silently.
_MATCH_CAP = 25
#: query_table filters compared however they are typed once their equality found nothing: a name
#: on every rung, a code (vendor_code, building_code, wo_number) only exact and compact.
_NAME_COLUMN = re.compile(r"(?:^|_)(name|title|label)$")
_CODE_COLUMN = re.compile(r"(?:^|_)(code|ref|reference|number|no|num|barcode)$")
#: Rows read per probe when a udr_search_records term found nothing as typed.
_PROBE_ROWS = 500
_UNRESTRICTED = "current_setting('app.udr_unrestricted', true) = '1'"


def _kind_for(table: str) -> frozenset[str]:
    """The kind words of a table: its own set, else its last word, singular and plural."""
    if table in _KIND_BY_TABLE:
        return _KIND_BY_TABLE[table]
    last = table.rsplit("_", 1)[-1]
    one = _oq.singular(last)
    return frozenset({last, one, one + "s"})


def _names_nothing(text_: str, kind) -> bool:
    return not name_match.meaningful(text_, kind)


@dataclass
class _Source:
    """One table a name is looked for in, read inside the caller's company."""

    name: str
    from_sql: str            # what is read, aliased t
    bound: str               # " AND (...)" keeping it to the caller's company, or ""
    key: str
    exact_cols: list[str]    # compared by the exact and compact rungs: ids and codes, and the names
    name_cols: list[str]     # compared by every rung


#: (table, key columns — the first present, id/code columns, name columns), as find_location reads
#: them; a column the deployment does not have is left out rather than failing the read.
_PLACES = (
    ("buildings", ("building_id", "id"), ("building_id", "id", "building_code", "code"),
     ("name", "building_name", "building_code", "code")),
    ("sites", ("site_id", "id"), ("site_id", "id", "site_code", "building_code"),
     ("site_name", "name", "building_name", "site_code", "building_code", "city")),
    ("locations", ("id", "location_id"), ("id", "location_id", "location_code", "code"),
     ("name", "location_name", "address", "city")),
)
_ASSETS = ("assets", ("id",), ("id", "asset_code", "barcode", "serial_number", "external_asset_id", "qr_code"),
           ("asset_name", "asset_code", "id", "barcode", "serial_number", "external_asset_id", "qr_code"))


async def _company_bound(session, table: str, cols: frozenset[str]) -> tuple[str, str]:
    """Where ``table`` is read from, and what keeps it to the caller's company.

    The platform rule (CLAUDE.md, 14 Oct 2026): a row's own company column decides, else its
    building's, else its site's; NULL never means shared. plenum_scoped's views apply a table's own
    organization_id and the caller's building allocation. sites.organization_id is an INTEGER that is
    NULL on every row, so a site is the company's only through a company building standing on it
    (buildings.site_id names it by site_id or by id). A table with no route to a company is read
    only by a superadmin acting for no company, whom the views do not filter either."""
    bcols = cols if table == "buildings" else await table_columns(session, "buildings")
    buildings_known = {"building_id", "organization_id"} <= bcols
    on_buildings = 'SELECT b."site_id"::text FROM plenum_scoped."buildings" b WHERE b."site_id" IS NOT NULL'
    if table == "sites":
        keys = [k for k in ("site_id", "id") if k in cols]
        if buildings_known and "site_id" in bcols and keys:
            own = " OR ".join(f"t.{_oq.qi(k)}::text IN ({on_buildings})" for k in keys)
            return 'plenum_cafm."sites" t', f" AND ({_UNRESTRICTED} OR {own})"
        return 'plenum_cafm."sites" t', f" AND {_UNRESTRICTED}"
    src = f"plenum_scoped.{_oq.qi(table)} t"
    if "organization_id" in cols:
        return src, ""
    routes = []
    if buildings_known and "building_id" in cols:
        routes.append('t."building_id" IS NOT NULL AND t."building_id"::text IN '
                      '(SELECT b."building_id"::text FROM plenum_scoped."buildings" b)')
    if buildings_known and "site_id" in cols and "site_id" in bcols:
        site_keys = [on_buildings]
        if {"site_id", "id"} <= await table_columns(session, "sites"):
            site_keys += [
                f'SELECT s."site_id"::text FROM plenum_cafm."sites" s WHERE s."id"::text IN ({on_buildings})',
                f'SELECT s."id"::text FROM plenum_cafm."sites" s WHERE s."site_id"::text IN ({on_buildings})']
        routes.append(('t."building_id" IS NULL AND ' if "building_id" in cols else "")
                      + 't."site_id"::text IN (' + " UNION ".join(site_keys) + ")")
    if not routes:
        return src, f" AND {_UNRESTRICTED}"
    return src, f" AND ({_UNRESTRICTED} OR " + " OR ".join(f"({r})" for r in routes) + ")"


async def _source(session, spec) -> _Source | None:
    table, keys, ids, names = spec
    cols = await table_columns(session, table)
    key = next((c for c in keys if c in cols), None)
    name_cols = [c for c in names if c in cols]
    exact_cols = list(dict.fromkeys([*(c for c in ids if c in cols), *name_cols]))
    if not key or not exact_cols:
        return None
    from_sql, bound = await _company_bound(session, table, cols)
    return _Source(table, from_sql, bound, key, exact_cols, name_cols)


async def _ladder(reader, sources: list[_Source], text_: str, kind, label: str):
    """The first rung at which ``text_`` names a record in any of ``sources``, and every row each
    source has at that rung — up to _MATCH_CAP + 1, so a cut can be said. (None, {}) when nothing."""
    for mode in _oq.LADDER:
        if mode == "fuzzy":
            found = await _closest(reader, sources, text_, kind, label)
            return ("fuzzy", found) if found else (None, {})
        for i, attempt in enumerate(_oq._rung_attempts(mode, text_, kind)):
            found: dict[str, list[dict]] = {}
            for src in sources:
                p = _oq.Params()
                cols = src.exact_cols if mode in ("exact", "compact") else src.name_cols
                cond = _oq._rung_cond(cols, mode, attempt, p)
                if not cond:
                    continue
                rows = await reader.try_fetch(
                    f"SELECT t.* FROM {src.from_sql} WHERE ({cond}){src.bound} LIMIT {_MATCH_CAP + 1}",
                    p, f"{label} {src.name} ({mode}{'' if i == 0 else ' ' + str(i + 1)})")
                if rows:
                    found[src.name] = rows
            if found:
                return mode, found
    return None, {}


async def _closest(reader, sources: list[_Source], text_: str, kind, label: str) -> dict[str, list[dict]]:
    """The close-spelling rung: candidates sharing a three-letter piece with a word of the name, then
    only those clearly closest to it — never closeness in a code or a number (name_match.similarity)."""
    attempts = _oq._rung_attempts("fuzzy", text_, kind)
    if not attempts:
        return {}
    core = " ".join(name_match.meaningful(text_, kind))
    scored: list[tuple[float, str, str]] = []
    for src in sources:
        p = _oq.Params()
        cond = _oq._rung_cond(src.name_cols, "fuzzy", attempts[0], p)
        if not cond:
            continue
        texts = ", ".join(f"t.{_oq.qi(c)}::text AS {_oq.qi(c)}" for c in src.name_cols)
        rows = await reader.try_fetch(
            f'SELECT t.{_oq.qi(src.key)}::text AS "match_key", {texts} FROM {src.from_sql} '
            f"WHERE ({cond}){src.bound} LIMIT {_oq.FUZZY_CANDIDATES}", p, f"{label} {src.name} (fuzzy)")
        for row in rows:
            score = max((name_match.similarity(core, row[c]) for c in src.name_cols if row.get(c)), default=0.0)
            if score >= name_match.FUZZY_MIN:
                scored.append((score, src.name, row["match_key"]))
    if not scored:
        return {}
    top = max(s for s, _, _ in scored)
    found: dict[str, list[dict]] = {}
    for src in sources:
        keys = list(dict.fromkeys(k for s, n, k in scored if n == src.name and s >= top - name_match.FUZZY_TIE))
        if not keys:
            continue
        p = _oq.Params()
        marks = ", ".join(p.add(k) for k in keys[:_MATCH_CAP + 1])
        rows = await reader.try_fetch(
            f"SELECT t.* FROM {src.from_sql} WHERE t.{_oq.qi(src.key)}::text IN ({marks}){src.bound} "
            f"LIMIT {_MATCH_CAP + 1}", p, f"{label} {src.name} (closest)")
        if rows:
            found[src.name] = rows
    return found


def _match_note(text_: str, mode: str | None, found: dict[str, list[dict]], sources: list[_Source]) -> str | None:
    """What the answer must say about how ``text_`` was matched; None for one plain match."""
    cols = {s.name: s.name_cols or s.exact_cols for s in sources}
    shown = {n: rows[:_MATCH_CAP] for n, rows in found.items() if rows}

    def label(table, row):
        return next((str(row[c]) for c in cols.get(table, ()) if row.get(c) not in (None, "")), "?")

    notes = []
    if mode == "fuzzy":
        labels = list(dict.fromkeys(label(n, r) for n, rows in shown.items() for r in rows))
        notes.append(f"'{text_}' is not on the register as written; the closest is "
                     + ", ".join(f"'{x}'" for x in labels[:5]) + " — say so.")
    cut = [n for n, rows in found.items() if len(rows) > _MATCH_CAP]
    if cut:
        notes.append(f"More than {_MATCH_CAP} {' and '.join(cut)} match '{text_}'; the first {_MATCH_CAP} "
                     "are shown — say so, and ask for a more exact name or a code.")
    total = sum(len(rows) for rows in shown.values())
    if total > 1:
        per = ", ".join(f"{n}: {len(rows)}" for n, rows in shown.items())
        notes.append(f"'{text_}' matches {'more than ' if cut else ''}{total} records ({per}). If they are "
                     "different, list them and ask which one was meant — never pick one.")
    return " ".join(notes) or None


def _scoped_reader():
    """The caller's scoped read, or why nothing may be read (no caller, allocated to nothing)."""
    try:
        return _oq.ScopedReader(caller_principal.get()), None
    except _oq.NotAllowed as exc:
        return None, f"Nothing can be read: {exc}."


async def _find_assets(reader, ident: str):
    src = await _source(reader.s, _ASSETS)
    sources = [src] if src else []
    mode, found = await _ladder(reader, sources, ident, _ASSET_KIND, "find_asset")
    return mode, found, sources


def _named_filters(filters) -> list[tuple[str, str]]:
    """query_table's filters on a name or a code column with a text value."""
    return [(c, v) for c, v in (filters or {}).items()
            if isinstance(v, str) and v.strip() and (_NAME_COLUMN.search(c) or _CODE_COLUMN.search(c))]


def _closest_rows(rows: list[dict], col: str, value: str, kind) -> list[dict]:
    core = " ".join(name_match.meaningful(value, kind))
    scored = [(name_match.similarity(core, r.get(col)), r) for r in rows if r.get(col) not in (None, "")]
    scored = [x for x in scored if x[0] >= name_match.FUZZY_MIN]
    if not scored:
        return []
    top = max(s for s, _ in scored)
    note = f"closest spelling to '{value}', which is not on the register as written — say so"
    return [{**r, "_name_match": note} for s, r in scored if s >= top - name_match.FUZZY_TIE][:_MAX_ROWS]


async def _filters_however_typed(session, table: str, filters: dict, named, kind, tail: str,
                                 tail_params: dict) -> list[dict]:
    """query_table's name and code filters on the ladder, after their equality found nothing.

    A name climbs every rung; a code only exact and compact — B090765 is B-090765, but ACME is not
    ACME01, and a vendor looked up by its code stays an exact lookup that keeps a retired vendor.
    Every other filter stays an equality, and the first read's vendors and building clauses
    (``tail``) are carried onto every rung."""
    import time as _time

    from . import trace as _trace

    named_cols = {c for c, _ in named}
    fixed = [f"{c} = :p_{c}" for c in filters if c not in named_cols]
    fixed_params = {f"p_{c}": v for c, v in filters.items() if c not in named_cols}
    names = [c for c, _ in named if _NAME_COLUMN.search(c)]
    for mode in (_oq.LADDER if names else ("exact", "compact")):
        if mode == "fuzzy" and len(names) != 1:
            break                         # a close spelling is judged on one name at a time
        plan = []
        for col, val in named:
            m = mode if col in names else ("exact" if mode == "exact" else "compact")
            attempts = _oq._rung_attempts(m, val, kind)
            if not attempts and m == "words" and len(names) > 1:
                m, attempts = "search", _oq._rung_attempts("search", val, kind)
            if not attempts:
                plan = []
                break
            plan.append([(col, m, a) for a in attempts])
        if not plan:
            continue                      # e.g. one word has no "words" rung: the phrase rung tried it
        for i, combo in enumerate(itertools.product(*plan)):
            p = _oq.Params()
            conds = fixed + ["(" + _oq._rung_cond([col], m, a, p) + ")" for col, m, a in combo]
            limit = _oq.FUZZY_CANDIDATES if mode == "fuzzy" else _MAX_ROWS
            sql = f"SELECT * FROM plenum_cafm.{table} WHERE " + " AND ".join(conds) + f"{tail} LIMIT {limit}"
            bound = {**fixed_params, **tail_params, **p.d}
            t0 = _time.perf_counter()
            result = await session.execute(text(sql), bound)
            rows = [dict(r) for r in result.mappings().all()]
            _trace.on_sql(sql, bound, rows, (_time.perf_counter() - t0) * 1000,
                          label=f"query_table {table} ({mode}{'' if i == 0 else ' ' + str(i + 1)})")
            if mode == "fuzzy":
                rows = _closest_rows(rows, names[0], dict(named)[names[0]], kind)
            if rows:
                return rows
    return []


@tool
async def find_asset(identifier: str) -> dict:
    """Resolve an asset from ANY identifier — id (PK, e.g. 'AST-AHU-601'), asset_code,
    asset_name, barcode, serial_number, external_asset_id, qr_code — however it is typed: any
    case, a code with or without its hyphens, the words in another order, or one letter wrong
    (then `note` says it is the closest spelling — say so).

    Use this FIRST whenever the user refers to an asset by a code/name; don't guess which
    column holds it. Returns every matching asset of the caller's company (up to 25; `note`
    says when more matched) — several when the name is shared: list them and ask which, never
    pick one. Each row includes `document_ids` — the ingested documents linked to that asset;
    pass the asset to get_asset_documents to read their content (compliance reports, manuals,
    certificates, etc.).
    """
    ident = (identifier or "").strip()
    if not ident:
        return {"identifier": identifier, "matches": [], "count": 0}
    if _names_nothing(ident, _ASSET_KIND):
        return {"identifier": ident, "matches": [], "count": 0,
                "note": f"'{ident}' names no particular asset; ask which one is meant."}
    reader, refused = _scoped_reader()
    if refused:
        return {"identifier": ident, "matches": [], "count": 0, "error": refused}
    try:
        async with reader as r:
            mode, found, sources = await _find_assets(r, ident)
    except Exception as exc:
        return _err(exc, "find_asset")
    rows = found.get("assets", [])[:_MATCH_CAP]
    out: dict[str, Any] = {"identifier": ident, "matches": rows, "count": len(rows), "matched_by": mode}
    note = _match_note(ident, mode, found, sources) if found else None
    if note:
        out["note"] = note
    return out


def _coerce_doc_ids(value: Any) -> list[str]:
    """Normalise an asset's document_ids (JSONB list / JSON string / None) to list[str]."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        items = list(value)
    else:
        s = str(value).strip()
        if not s or s in ("[]", "NULL"):
            return []
        try:
            parsed = json.loads(s)
            items = parsed if isinstance(parsed, list) else [parsed]
        except (ValueError, TypeError):
            items = [s]
    return [str(x).strip() for x in items if str(x).strip()]


@tool
async def get_asset_documents(identifier: str, query: str = "", max_chunks: int = 12) -> dict:
    """Read the ingested DOCUMENTS linked to an asset and return their content to summarise.

    Every asset row carries a `document_ids` array referencing files ingested into the document
    store (public.documents + public.document_chunks — compliance reports, manuals, certificates,
    inspection records, etc.). Use this whenever the user asks for an asset's details, compliance,
    history or "all information" that lives in attached documents rather than the structured
    columns of the assets table.

    Flow: resolve the asset -> its document_ids -> document metadata + the most relevant text
    chunks (filtered by `query`, falling back to the asset code, then to the opening chunks).
    Returns a bundle you should summarise for the user, citing the source file(s).

    Args:
        identifier: asset id / code / name / serial / barcode (same as find_asset).
        query: optional focus term to select the most relevant chunks (e.g. 'warranty',
            'fire', a sub-system). Defaults to the asset code.
        max_chunks: max chunks to return (capped at 40 to protect context).
    """
    ident = (identifier or "").strip()
    if not ident:
        return {"identifier": identifier, "asset": None, "documents": [], "chunks": []}
    max_chunks = max(1, min(int(max_chunks or 24), 40))

    if _names_nothing(ident, _ASSET_KIND):
        return {"identifier": ident, "asset": None, "documents": [], "chunks": [],
                "note": f"'{ident}' names no particular asset; ask which one is meant."}
    reader, refused = _scoped_reader()
    if refused:
        return {"identifier": ident, "asset": None, "documents": [], "chunks": [], "error": refused}
    # The asset is resolved as find_asset resolves it, in the caller's company; its documents are
    # then read by the ids on that asset row, in the same read-only transaction.
    async with reader as scoped:
        session = scoped.s
        try:
            mode, found, sources = await _find_assets(scoped, ident)
        except Exception as exc:
            return _err(exc, "get_asset_documents.find")
        assets = found.get("assets", [])

        if not assets:
            return {"identifier": ident, "asset": None, "documents": [], "chunks": [],
                    "note": "No asset matched that identifier."}
        if len(assets) > 1:
            # Whose documents to read is the user's call when several assets answer to the name,
            # never the first row's: the old read took the first of five partial matches, and
            # "chiller 1" read Chiller 10's documents (29 Oct 2026).
            return {"identifier": ident, "asset": None, "documents": [], "chunks": [],
                    "candidates": [{k: a.get(k) for k in ("id", "asset_code", "asset_name", "building_id",
                                                          "site_id") if k in a} for a in assets[:_MATCH_CAP]],
                    "note": (_match_note(ident, mode, found, sources) or "")
                    + " Call again with the chosen asset's id or asset_code."}

        asset = assets[0]
        match_note = _match_note(ident, mode, found, sources)
        doc_ids = _coerce_doc_ids(asset.get("document_ids"))
        # Return the FULL asset row (every structured field) so a "complete
        # information" answer can list them all — not just a 4-field brief.
        asset_brief = {
            k: v for k, v in asset.items() if k not in ("document_ids", "embedding")
        }
        asset_brief["linked_document_count"] = len(doc_ids)
        if not doc_ids:
            return {"identifier": ident, "asset": asset_brief, "documents": [], "chunks": [],
                    "note": "This asset has no linked documents (document_ids is empty).",
                    **({"match_note": match_note} if match_note else {})}

        # Document metadata (plenum_cafm ingestion store).
        documents: list[dict] = []
        try:
            res = await session.execute(
                text("SELECT id, original_filename AS file_name, blob_url AS download_url, "
                     "document_type, status, page_count AS num_pages "
                     "FROM plenum_cafm.ingestion_documents WHERE id::text = ANY(:ids)"),
                {"ids": doc_ids},
            )
            documents = [dict(r) for r in res.mappings().all()]
        except Exception as exc:
            log.warning("udr.get_asset_documents.docs_error", error=str(exc)[:200])

        focus = (query or asset.get("asset_code") or "").strip()

        async def fetch_chunks(term: str | None):
            params: dict[str, Any] = {"ids": doc_ids, "lim": max_chunks}
            where = "ingestion_id::text = ANY(:ids)"
            if term:
                where += " AND chunk_text ILIKE :term"
                params["term"] = f"%{term}%"
            sql = ("SELECT ingestion_id AS document_id, chunk_index, page_start, "
                   "heading AS section_label, chunk_text AS text_content "
                   f"FROM plenum_cafm.document_chunks WHERE {where} "
                   "ORDER BY ingestion_id, chunk_index LIMIT :lim")
            res2 = await session.execute(text(sql), params)
            return [dict(r) for r in res2.mappings().all()]

        try:
            rows = await fetch_chunks(focus) if focus else []
            if not rows:  # fall back to the document's opening chunks
                rows = await fetch_chunks(None)
        except Exception as exc:
            return _err(exc, "get_asset_documents.chunks")

        # Always expose a working download ref. "doc:<id>" is resolved by the UI
        # to GET /api/documents/<id>/download — which serves the stored original
        # when present, else the extracted text — so a link is ALWAYS available
        # even when blob_url is null (no more "Download URL is not available").
        for d in documents:
            d["download_url"] = f"doc:{d.get('id')}"
        file_by_id = {str(d.get("id")): d.get("file_name") for d in documents}
        chunks = [{
            "source_file": file_by_id.get(str(r.get("document_id"))) or r.get("document_id"),
            "download_url": f"doc:{r.get('document_id')}",
            "section": r.get("section_label"),
            "page": r.get("page_start"),
            "chunk_index": r.get("chunk_index"),
            "text": (str(r.get("text_content") or "")[:2000]),
        } for r in rows]

        return {
            "identifier": ident,
            "asset": asset_brief,
            **({"match_note": match_note} if match_note else {}),
            "documents": documents,
            "chunk_count": len(chunks),
            "filter_used": focus or "(opening chunks)",
            "chunks": chunks,
            "note": (
                "Summarise the answer from these document chunks. "
                "If the chunks contain a compliance / findings table (rows of checks with a "
                "Status and a Finding/Action — e.g. warranty, Legionella/ACoP L8, SFG20 PPM), "
                "REPRODUCE it as a markdown table with columns 'Compliance Check | Status | "
                "Finding / Action'; do not drop those rows. "
                "Cite EVERY source document as a markdown link [<source_file>](<download_url>) "
                "using each chunk's download_url (always present) so the user can open/download "
                "it. Never write a placeholder like (#) or 'Download URL is not available'. "
                "Older note (no longer applies — download_url is always set): only fall back to "
                "the bare source_file name when download_url is null. "
                "These are the documents linked to this asset via document_ids."
            ),
        }


@tool
async def find_location(identifier: str) -> dict:
    """Resolve a BUILDING, SITE or LOCATION from any identifier — a building's name or code
    (e.g. 'JASMEET', 'B-090765'), site_id (e.g. 'SITE-DXB-001'), site_code/site_name/city, or a
    location id/name/address/city — however it is typed: any case, with or without the word
    "building"/"site", a code with or without its hyphen, the words in another order, or one
    letter wrong (then `note` says it is the closest spelling — say so).

    Use this to resolve where an asset/work order is, or which building a question names.
    Searches the buildings, sites and locations of the caller's company and returns every
    match from each (up to 25 per table; `note` says when more matched) — several when a name
    is shared: list them and ask which, never pick one. Empty only if the place truly isn't
    in the data.
    """
    ident = (identifier or "").strip()
    out: dict[str, Any] = {"identifier": ident, "buildings": [], "sites": [], "locations": []}
    if not ident:
        return {**out, "count": 0}
    if _names_nothing(ident, _PLACE_KIND):
        return {**out, "count": 0,
                "note": f"'{ident}' names no particular place; ask which building, site or location is meant."}
    reader, refused = _scoped_reader()
    if refused:
        return {**out, "count": 0, "error": refused}
    try:
        async with reader as r:
            sources = [s for s in [await _source(r.s, spec) for spec in _PLACES] if s]
            mode, found = await _ladder(r, sources, ident, _PLACE_KIND, "find_location")
    except Exception as exc:
        return {**out, "count": 0, **_err(exc, "find_location")}
    for name, rows in found.items():
        out[name] = rows[:_MATCH_CAP]
    out["count"] = len(out["buildings"]) + len(out["sites"]) + len(out["locations"])
    out["matched_by"] = mode
    note = (_match_note(ident, mode, found, sources) if found
            else f"No building, site or location in your company matches '{ident}'.")
    if note:
        out["note"] = note
    return out


@tool
async def udr_agent_query(message: str) -> dict:
    """Run a natural-language UDR query via svc-udr agent endpoint.

    Use this for complex ad-hoc UDR requests where structured parameters are not
    known upfront; svc-udr will decide the sequence of list/describe/read/write.
    """
    try:
        resp = await _request(
            "POST",
            settings.udr_base_url,
            "/api/agent/query",
            service=_SERVICE,
            timeout=_TIMEOUT,
            json={"message": message},
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "agent_query")


@tool
async def udr_list_tables() -> dict:
    """List all UDR-exposed tables with row-count metadata."""
    try:
        resp = await _request(
            "GET",
            settings.udr_base_url,
            "/api/tables/",
            service=_SERVICE,
            timeout=_TIMEOUT,
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "list_tables")


@tool
async def udr_describe_table(table: str) -> dict:
    """Describe table schema details including keys and relationships."""
    try:
        resp = await _request(
            "GET",
            settings.udr_base_url,
            f"/api/tables/{table}/schema",
            service=_SERVICE,
            timeout=_TIMEOUT,
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "describe_table")



@tool
async def find_tables(question: str, k: int = 8, domain: str | None = None) -> dict:
    """UDR — Which tables answer this question. Call this BEFORE naming a table.

    Searches the table catalogue by meaning: every plenum_cafm table has a written purpose,
    the questions it is the right source for, its grain (what one row is), its keys, its links
    to other tables — declared foreign keys AND the links that exist only by column naming —
    and sample values. Pass the user's question as asked; paraphrasing it loses the words the
    embedding matches on.

    Returns the best `k` tables, best first, each with `purpose`, `answers`, `not_for` (the
    near-miss table for a question that sounds like this one), `links_out` / `links_in` (the
    joins available), `primary_key`, `columns` (names) and `score`. Then `table_card` for the
    one or two you will actually read, and `get_schema` only to confirm exact column names.

    `domain` narrows to one area: org, assets, work, compliance, energy, vendors, supply, docs,
    reference, platform.

    Measured 17 Sep 2026: without this, "which assets have never been scored" was answered by
    a LEFT JOIN against a table that does not exist. The catalogue would have said
    `assets.health_score`, and that `asset_condition_scores` is the near-miss.
    """
    try:
        params: dict[str, Any] = {"q": question, "k": k}
        if domain:
            params["domain"] = domain
        resp = await _request("GET", settings.udr_base_url, "/api/catalog/search",
                              service=_SERVICE, timeout=_TIMEOUT, params=params)
        return resp.json()
    except Exception as exc:
        return _err(exc, "find_tables")


@tool
async def table_card(table: str) -> dict:
    """UDR — One table's card from the catalogue: purpose, the questions it answers, what one
    row is, primary key, every link in and out (declared vs by-name), every column with type,
    nullability and sample values, three redacted sample rows, and which service owns it.

    Read this before writing SQL against a table you have not used in this session. The sample
    values tell you the status spellings and the id formats; the links tell you what you can
    join to without guessing. A table with `purpose_source: "heuristic"` had no model-written
    purpose — treat its description as structural, not semantic.
    """
    try:
        resp = await _request("GET", settings.udr_base_url, f"/api/catalog/{table}",
                              service=_SERVICE, timeout=_TIMEOUT)
        return resp.json()
    except Exception as exc:
        return _err(exc, "table_card")


async def _scoped(table: str, payload: dict, *, key: str = "rows") -> dict:
    """Narrow a svc-udr read to the caller's buildings before the model sees it."""
    rows = payload.get(key) if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return payload
    async with database.AsyncSessionLocal() as session:
        kept, refused = await restrict_records(session, table, [r for r in rows if isinstance(r, dict)])
    out = {**payload, key: kept, "count": len(kept)}
    if refused:
        out["note"] = refused
    elif len(kept) != len(rows):
        out["note"] = f"{len(rows) - len(kept)} row(s) outside your buildings were withheld."
    return out


_MAX_READ_LIMIT = 500


@tool
async def udr_read_records(
    table: str,
    limit: int = 50,
    offset: int = 0,
    order_by: str | None = None,
    order_dir: str = "asc",
) -> dict:
    """Read records from a table with pagination and optional sorting (max 500 rows per call).

    For larger datasets use offset paging or write_file Mode 3 offload.
    """
    params: dict[str, Any] = {
        "limit": min(max(limit, 1), _MAX_READ_LIMIT),
        "offset": max(offset, 0),
        "order_dir": order_dir,
    }
    if order_by:
        params["order_by"] = order_by
    try:
        resp = await _request(
            "GET",
            settings.udr_base_url,
            f"/api/tables/{table}/records",
            service=_SERVICE,
            timeout=_TIMEOUT,
            params=params,
        )
        return await _scoped(table, resp.json())
    except Exception as exc:
        return _err(exc, "read_records")


@tool
async def udr_get_record(table: str, record_id: str, id_column: str = "id") -> dict:
    """Fetch one record by primary key."""
    try:
        resp = await _request(
            "GET",
            settings.udr_base_url,
            f"/api/tables/{table}/records/{record_id}",
            service=_SERVICE,
            timeout=_TIMEOUT,
            params={"id_column": id_column},
        )
        record = resp.json()
        scoped = await _scoped(table, {"rows": [record] if isinstance(record, dict) else []})
        if not scoped["rows"]:
            return {"error": scoped.get("note")
                    or f"Record {record_id!r} in {table} is not in your buildings."}
        return scoped["rows"][0]
    except Exception as exc:
        return _err(exc, "get_record")


@tool
async def udr_search_records(
    table: str,
    search_term: str,
    search_columns: list[str],
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """Search records using case-insensitive partial matches across columns.

    A term that finds nothing as typed is then matched however it is typed — without the kind
    word ("JASMEET BUILDING" finds "JASMEET"), a code without its hyphen (B090765), the words in
    another order, then one letter wrong — and `match_note` says how; say so. A term of kind
    words alone ("the building") searches nothing.
    """
    kind = _kind_for(table)
    if _names_nothing(search_term or "", kind):
        return {"table": table, "search_term": search_term, "rows": [], "count": 0, "limit": limit,
                "offset": offset, "match_note": f"'{search_term}' names no particular record; ask which one is meant."}

    async def search(term: str, lim: int, off: int = 0) -> dict:
        resp = await _request(
            "POST",
            settings.udr_base_url,
            f"/api/tables/{table}/records/search",
            service=_SERVICE,
            timeout=_TIMEOUT,
            json={
                "search_term": term,
                "search_columns": search_columns,
                "limit": lim,
                "offset": off,
            },
        )
        return resp.json()

    try:
        payload = await search(search_term, limit, offset)
        rows = payload.get("rows") if isinstance(payload, dict) else None
        if offset == 0 and isinstance(rows, list) and not rows:
            payload = await _search_however_typed(search, payload, search_term, search_columns, limit, kind)
        return await _scoped(table, payload)
    except Exception as exc:
        return _err(exc, "search_records")


def _row_rung(term: str, row: dict, columns, kind) -> int | None:
    rungs = [name_match.rung(term, row.get(c), kind) for c in columns if row.get(c) not in (None, "")]
    rungs = [x for x in rungs if x is not None]
    return min(rungs) if rungs else None


def _searched(payload: dict, rows: list[dict], limit: int, note: str, capped: bool) -> dict:
    shown = rows[:max(1, limit)]
    if len(rows) > len(shown):
        note += f" More than {len(shown)} matched; the first {len(shown)} are shown — say so."
    if capped:
        note += " A probe read its most rows, so a match may be missing — ask for a more exact name or a code."
    if len(shown) > 1:
        note += " If they are different records, list them and ask which one was meant — never pick one."
    return {**payload, "rows": shown, "count": len(shown), "match_note": note}


async def _search_however_typed(search, payload: dict, term: str, columns, limit: int, kind) -> dict:
    """udr_search_records' term on the ladder, after svc-udr's substring search found nothing as typed.

    svc-udr compares ILIKE '%term%' in the caller's company, so each probe here is a substring that
    every record the term can name contains — its core words ("JASMEET BUILDING" -> "jasmeet"), then
    its longest run of letters or digits (B090765 -> "090765", "tower hussain" -> "hussain") — and
    the rows it returns are kept only where they match the term on a rung (name_match.rung, best
    first). Then one letter wrong: candidates sharing a three-letter piece, the clearly closest kept.
    """
    core_words = name_match.meaningful(term, kind)
    core = " ".join(core_words)
    typed = name_match.clean(term)
    probes: list[str] = []
    if core and core != typed:
        probes.append(core)
    runs = sorted({run for w in core_words for run in re.findall(r"[a-z]+|[0-9]+", w)}, key=lambda x: (-len(x), x))
    if runs and runs[0] not in probes and runs[0] != typed:
        probes.append(runs[0])
    for probe in probes:
        got = await search(probe, _PROBE_ROWS)
        got_rows = (got.get("rows") or []) if isinstance(got, dict) else []
        ranked = [(rg, row) for row in got_rows
                  if (rg := _row_rung(term, row, columns, kind)) is not None and rg <= 3]
        if ranked:
            ranked.sort(key=lambda x: x[0])           # stable: svc-udr's order within a rung
            return _searched(payload, [row for _, row in ranked], limit,
                             f"'{term}' found nothing as typed; these match it (searched as '{probe}').",
                             len(got_rows) >= _PROBE_ROWS)
    candidates: dict[str, dict] = {}
    capped = False
    for frag in name_match.fragments(core)[:6]:
        got = await search(frag, _PROBE_ROWS)
        got_rows = (got.get("rows") or []) if isinstance(got, dict) else []
        capped = capped or len(got_rows) >= _PROBE_ROWS
        for row in got_rows:
            candidates.setdefault(json.dumps(row, sort_keys=True, default=str), row)
    scored = []
    for row in candidates.values():
        score = max((name_match.similarity(core, row.get(c)) for c in columns if row.get(c) not in (None, "")),
                    default=0.0)
        if score >= name_match.FUZZY_MIN:
            scored.append((score, row))
    if not scored:
        return payload
    top = max(s for s, _ in scored)
    rows = [row for s, row in sorted(scored, key=lambda x: -x[0]) if s >= top - name_match.FUZZY_TIE]
    labels = [str(next((row[c] for c in columns if row.get(c) not in (None, "")), "?")) for row in rows[:5]]
    return _searched(payload, rows, limit,
                     f"'{term}' is not on the register as written; the closest is "
                     + ", ".join(f"'{x}'" for x in labels) + " — say so.", capped)


@tool
async def udr_create_record(table: str, data: dict[str, Any]) -> dict:
    """Create a record in a UDR table."""
    try:
        resp = await _request(
            "POST",
            settings.udr_base_url,
            f"/api/tables/{table}/records",
            service=_SERVICE,
            timeout=_TIMEOUT,
            max_attempts=1,
            json={"data": data},
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "create_record")


@tool
async def udr_update_record(
    table: str,
    record_id: str,
    data: dict[str, Any],
    id_column: str = "id",
) -> dict:
    """Update fields on a record in a UDR table."""
    try:
        resp = await _request(
            "PATCH",
            settings.udr_base_url,
            f"/api/tables/{table}/records/{record_id}",
            service=_SERVICE,
            timeout=_TIMEOUT,
            max_attempts=1,
            json={"data": data, "id_column": id_column},
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "update_record")


@tool
async def udr_delete_record(table: str, record_id: str, id_column: str = "id") -> dict:
    """Delete a record from a UDR table by primary key."""
    try:
        resp = await _request(
            "DELETE",
            settings.udr_base_url,
            f"/api/tables/{table}/records/{record_id}",
            service=_SERVICE,
            timeout=_TIMEOUT,
            max_attempts=1,
            params={"id_column": id_column},
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "delete_record")


@tool
async def udr_execute_select(sql: str, params: dict[str, Any] | None = None) -> dict:
    """Execute a custom SELECT statement through UDR with safety checks."""
    try:
        resp = await _request(
            "POST",
            settings.udr_base_url,
            "/api/tables/query/select",
            service=_SERVICE,
            timeout=_TIMEOUT,
            json={"sql": sql, "params": params or {}},
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "execute_select")
