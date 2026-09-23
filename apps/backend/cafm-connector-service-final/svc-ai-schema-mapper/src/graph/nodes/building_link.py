"""
The building link — how a migrated row finds its plenum_cafm.buildings.building_id.

Every page in Hoistra is scoped by building: an asset or work order without a `building_id`
is invisible to a plain user and unattributed for an admin. A CMMS export never carries that
UUID; it carries a site reference ("S-01"), a site or building name, or a building code, and
until 21 Sep 2026 the writer dropped those on purpose ("FK columns that require UUID
resolution — cannot be filled at write time"). The result was ten assets with no building
that shadowed ten correctly linked ones, and every code-based join returning both.

This module is the resolution the writer lacked, kept pure so it can be tested without a
database:

  building_hint(row)                what in a source row names a building (or None)
  site_names_from_run(...)          the run's own Sites sheet: reference → name, so a site
                                    that is being migrated in the same file resolves even
                                    before its row lands
  BuildingResolver.resolve(hint)    hint → building_id, via buildings (name / code / site_id)
                                    and then via sites (site_id / site_code → name), cached,
                                    None when nothing or more than one thing matches
  asset_match_code(filtered)        the code an asset row would collide on
  build_asset_merge_update(...)     the UPDATE that merges a re-imported asset into the row
                                    that already exists, never clearing a building it has

The resolver takes an async `fetch(sql, params) -> list[tuple]` so the writer can hand it the
session (inside a savepoint) and a test can hand it a dict.
"""
from __future__ import annotations

import re
from typing import Awaitable, Callable, Iterable

Fetch = Callable[[str, dict], Awaitable[list]]

_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)

# Source columns that name a building, most specific first. `building_id` itself is only a
# hint when it is NOT already a UUID (a code or a name that landed under that header).
_HINT_KEYS = (
    "building_id", "building", "building_name", "building_code",
    "site_ref", "site_id", "site", "site_name", "site_code", "location_site",
)

# Source tables that are a list of sites, whatever the sheet was called.
_SITE_TABLES = frozenset({"sites", "site", "buildings", "building", "locations"})

# Columns a merge never overwrites: identity, tenancy, and when the row was first created.
_MERGE_KEEP = frozenset({"id", "organization_id", "created_at"})


def looks_like_uuid(value: object) -> bool:
    return bool(value) and bool(_UUID.match(str(value).strip()))


def building_hint(row: dict | None) -> str | None:
    """The first non-empty value in `row` that names a building; None when the row already
    carries a real building_id, or names nothing."""
    if not isinstance(row, dict):
        return None
    if looks_like_uuid(row.get("building_id")):
        return None
    for key in _HINT_KEYS:
        v = row.get(key)
        if v is None:
            continue
        s = str(v).strip()
        if s and not looks_like_uuid(s):
            return s
    return None


def site_names_from_run(cleaned_tables: dict | None, table_routing: dict | None) -> dict[str, str]:
    """reference (lower-cased) → site/building name, from every source table in this run that
    is routed to (or named like) a sites table. A reference that is itself a name maps to
    itself, so "Bishopsgate Tower" resolves whichever column carried it."""
    out: dict[str, str] = {}
    routing = table_routing or {}
    for source, rows in (cleaned_tables or {}).items():
        dest = str(routing.get(source, source) or "").lower()
        if dest not in _SITE_TABLES and str(source).lower() not in _SITE_TABLES:
            continue
        if not isinstance(rows, list):
            continue
        for r in rows:
            if not isinstance(r, dict):
                continue
            name = next((str(r[k]).strip() for k in ("site_name", "building_name", "name") if r.get(k) not in (None, "")), None)
            if not name:
                continue
            for k in ("site_id", "site_code", "building_code", "id", "code", "site_ref"):
                ref = r.get(k)
                if ref not in (None, "") and not looks_like_uuid(ref):
                    out.setdefault(str(ref).strip().lower(), name)
            out.setdefault(name.lower(), name)
    return out


class BuildingResolver:
    """Resolves building hints for one organisation, one run. Every distinct hint costs at
    most two reads; the answer (including "no match") is cached."""

    def __init__(self, fetch: Fetch, organization_id: str, schema: str = "plenum_cafm",
                 site_names: dict[str, str] | None = None) -> None:
        self._fetch = fetch
        self._org = str(organization_id or "")
        self._schema = schema
        self._names = dict(site_names or {})
        self._cache: dict[str, str | None] = {}
        self.reads = 0
        self.ambiguous: list[str] = []

    async def resolve(self, hint: str | None) -> str | None:
        if not hint:
            return None
        key = str(hint).strip().lower()
        if not key:
            return None
        if key in self._cache:
            return self._cache[key]
        found = await self._resolve(key)
        self._cache[key] = found
        return found

    async def _buildings_matching(self, names: Iterable[str]) -> list[str]:
        wanted = sorted({str(n).strip().lower() for n in names if n and str(n).strip()})
        if not wanted:
            return []
        self.reads += 1
        rows = await self._fetch(
            f"SELECT building_id::text FROM {self._schema}.buildings "
            f"WHERE organization_id::text = :org AND ("
            f"lower(name) = ANY(:names) OR lower(coalesce(building_code, '')) = ANY(:names) "
            f"OR lower(coalesce(site_id::text, '')) = ANY(:names)) LIMIT 3",
            {"org": self._org, "names": wanted},
        )
        return sorted({str(r[0]) for r in rows if r and r[0]})

    async def _resolve(self, key: str) -> str | None:
        # 1. The hint, or the name the run's own Sites sheet gives it, is a building.
        candidates = {key}
        if key in self._names:
            candidates.add(self._names[key])
        ids = await self._buildings_matching(candidates)
        if len(ids) == 1:
            return ids[0]
        if len(ids) > 1:
            self.ambiguous.append(key)
            return None
        # 2. The hint is a site the database already knows; its name is the building's.
        self.reads += 1
        rows = await self._fetch(
            f"SELECT coalesce(site_name, ''), coalesce(building_name, ''), coalesce(name, '') "
            f"FROM {self._schema}.sites WHERE organization_id::text = :org AND ("
            f"lower(coalesce(site_id::text, '')) = :k OR lower(coalesce(site_code, '')) = :k "
            f"OR lower(coalesce(site_name, '')) = :k OR lower(coalesce(name, '')) = :k) LIMIT 5",
            {"org": self._org, "k": key},
        )
        names = {str(c).strip() for r in rows for c in (r or ()) if c and str(c).strip()}
        if not names:
            return None
        ids = await self._buildings_matching(names)
        if len(ids) == 1:
            return ids[0]
        if len(ids) > 1:
            self.ambiguous.append(key)
        return None


def asset_match_code(filtered: dict | None) -> str | None:
    """The code an asset row is identified by across imports: `asset_code`, else a non-UUID
    `id` (the June 2026 import stored the code there)."""
    if not isinstance(filtered, dict):
        return None
    code = str(filtered.get("asset_code") or "").strip()
    if code:
        return code
    ident = str(filtered.get("id") or "").strip()
    if ident and not looks_like_uuid(ident):
        return ident
    return None


ASSET_LOOKUP_SQL = (
    "SELECT id::text FROM {schema}.assets WHERE organization_id::text = :org "
    "AND (asset_code = :code OR id::text = :code) LIMIT 1"
)

# A source row names its contractor — "Meridian Mechanical Ltd" — and every table that
# points at a vendor does so by uuid (work_orders.assigned_vendor, resources.vendor_id,
# spare_parts.supplier_id). Nothing resolved the one into the other, so a migrated work
# order kept the NAME in its text column and left the FK null. The vendor scorecard joins
# on that FK, found nothing, and reported "0 work orders" against every SLA clause while
# 16 jobs sat in the table. Matched case-insensitively on the trimmed name.
VENDOR_LOOKUP_SQL = (
    "SELECT id::text FROM {schema}.vendors WHERE organization_id::text = :org "
    "AND lower(btrim(vendor_name)) = lower(btrim(:name)) ORDER BY created_at LIMIT 1"
)

ASSET_BUILDING_SQL = (
    "SELECT building_id::text FROM {schema}.assets WHERE organization_id::text = :org "
    "AND (id::text = :ref OR asset_code = :ref) AND building_id IS NOT NULL LIMIT 1"
)


def build_asset_merge_update(schema: str, filtered: dict, existing_id: str) -> tuple[str, dict]:
    """UPDATE that folds a re-imported asset row into the existing row `existing_id`.

    Every provided column is written except identity/tenancy/created_at; `asset_code` is set
    (the June rows had none); `building_id` only fills a gap — a building the row already has
    is never replaced by what a later import guessed."""
    params: dict = {"_existing_id": str(existing_id)}
    sets: list[str] = []
    for col, val in (filtered or {}).items():
        if col in _MERGE_KEEP or val is None:
            continue
        params[col] = val
        if col == "building_id":
            sets.append("building_id = COALESCE(building_id, :building_id)")
        else:
            sets.append(f"{col} = :{col}")
    if "updated_at" not in params:
        sets.append("updated_at = now()")
    sql = f"UPDATE {schema}.assets SET {', '.join(sets)} WHERE id::text = :_existing_id"
    return sql, params
