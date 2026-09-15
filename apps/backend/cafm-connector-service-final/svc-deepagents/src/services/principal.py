"""Who is calling deep-agents, resolved by the service that owns identity.

deep-agents has no users table and no signing key, and should have neither: two services
that can each mint or verify a token are two places a bug can let someone in. So the caller
is resolved by forwarding their Authorization header to operations-intelligence
`GET /api/auth/me`, which reads role, ingestion right and building allocation from the
database on every call — the same answer every other route gets, from the same code.

The result is cached for a short while per token, because a single chat turn can consult
this several times and the answer does not change within a minute. It is NOT cached across
tokens or beyond that, because a company admin withdrawing someone's ingestion right expects
the next upload to be refused, not the one after the cache expires.
"""
from __future__ import annotations

import hashlib
import re
from contextvars import ContextVar
import time
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import httpx
import structlog
from fastapi import Header, HTTPException, status

from ..config import settings
from ..http_client import request as _request

log = structlog.get_logger(__name__)

_TTL_SECONDS = 60
_cache: dict[str, tuple[float, "Principal"]] = {}


@dataclass(frozen=True)
class Principal:
    user_id: UUID
    email: str
    organization_id: UUID | None
    role: str
    can_ingest: bool
    #: None = unrestricted (admin/superadmin). () = allocated to nothing.
    building_ids: tuple[UUID, ...] | None
    buildings: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    @property
    def is_admin(self) -> bool:
        return self.role in ("admin", "superadmin")

    def allows_building(self, building_id: UUID | str | None) -> bool:
        if building_id is None:
            return False
        if self.building_ids is None:
            return True
        try:
            return UUID(str(building_id)) in self.building_ids
        except (ValueError, TypeError):
            return False


def _unauthorized(error: str, reason: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"ok": False, "error": error, "reason": reason},
        headers={"WWW-Authenticate": "Bearer"},
    )


def _from_me(body: dict[str, Any]) -> Principal:
    u = body.get("user") or {}
    raw_ids = u.get("building_ids")
    ids: tuple[UUID, ...] | None
    if raw_ids is None:
        ids = None
    else:
        ids = tuple(UUID(str(b)) for b in raw_ids)
    return Principal(
        user_id=UUID(str(u["id"])),
        email=str(u.get("email") or ""),
        organization_id=UUID(str(u["organization_id"])) if u.get("organization_id") else None,
        role=str(u.get("role") or "user"),
        can_ingest=bool(u.get("can_ingest")),
        building_ids=ids,
        buildings=tuple(u.get("buildings") or ()),
    )


async def resolve(authorization: str | None) -> Principal:
    """The caller, or 401. Never a guess."""
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise _unauthorized("Send an Authorization: Bearer <token> header.", "missing_token")
    key = hashlib.sha256(token.strip().encode("utf-8")).hexdigest()
    hit = _cache.get(key)
    now = time.monotonic()
    if hit and hit[0] > now:
        return hit[1]
    try:
        resp = await _request(
            "GET", settings.operations_intelligence_base_url.rstrip("/"), "/api/auth/me",
            service="operations_intelligence", timeout=15.0,
            headers={"Authorization": f"Bearer {token.strip()}"}, max_attempts=1,
        )
        body = resp.json()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 401:
            try:
                detail = exc.response.json().get("detail") or {}
            except Exception:  # noqa: BLE001
                detail = {}
            raise _unauthorized(detail.get("error") or "Sign in again.",
                                detail.get("reason") or "invalid_token") from None
        log.error("principal.resolve.upstream_error", status=exc.response.status_code)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail={"ok": False, "error": "Identity service unavailable.",
                                    "reason": "identity_unavailable"}) from None
    except Exception as exc:  # noqa: BLE001
        log.error("principal.resolve.failed", error=str(exc)[:200])
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail={"ok": False, "error": "Identity service unavailable.",
                                    "reason": "identity_unavailable"}) from None
    principal = _from_me(body)
    _cache[key] = (now + _TTL_SECONDS, principal)
    return principal


async def current_principal(authorization: str | None = Header(default=None)) -> Principal:
    """FastAPI dependency: the signed-in caller."""
    return await resolve(authorization)


def forget(authorization: str | None) -> None:
    """Drop a cached principal — after an action that changes what the caller may do."""
    _, _, token = (authorization or "").partition(" ")
    if token.strip():
        _cache.pop(hashlib.sha256(token.strip().encode("utf-8")).hexdigest(), None)


#: The signed-in caller for the duration of one request, for code that runs SQL directly.
#:
#: The HTTP client already carries the caller's token to operations-intelligence, so every
#: read that goes over HTTP comes back scoped to their buildings. The reads that do NOT go
#: over HTTP — the compliance agent, the UDR agent's generic table read, two fetches in the
#: orchestrator — had no way to know who was asking, and answered for every building. This
#: is set beside caller_authorization at the top of each workflow route, and building_clause
#: below turns it into the same predicate operations-intelligence applies.
caller_principal: ContextVar["Principal | None"] = ContextVar("caller_principal", default=None)

#: Tables a building question can be narrowed on, and the column that names the building.
#: energy_meters and energy_anomalies key the building as building_id (called site_id
#: before Sep 2026; the fallback below still covers a database that has not migrated).
BUILDING_COLUMN: dict[str, str] = {
    "compliance_certificates": "building_id",
    "work_orders": "building_id",
    "assets": "building_id",
    "buildings": "building_id",
    "energy_meters": "building_id",
    "energy_anomalies": "building_id",
    "documents": "building_id",
    "meter_readings": None,           # keyed on meter_id — filtered through energy_meters
}


def building_clause(column: str, *, param: str = "scope_buildings") -> tuple[str, dict]:
    """A SQL fragment and its parameters narrowing a building-keyed query to the caller.

    ("", {}) for an admin or when no caller is set — an unscoped read stays unscoped rather
    than breaking a background job. " AND FALSE" for a user allocated to nothing: a predicate
    that matches no row, never the absence of one, which would match every row — the exact
    failure this exists to remove. The column is chosen by the caller, never by the model.
    """
    p = caller_principal.get()
    if p is None or p.building_ids is None:
        return "", {}
    if not p.building_ids:
        return " AND FALSE", {}
    return f" AND {column} = ANY(CAST(:{param} AS uuid[]))", {param: [str(b) for b in p.building_ids]}


def restrict_rows(rows: list[dict], column: str) -> list[dict]:
    """The same boundary applied after the fact, for a read that came back unfiltered."""
    p = caller_principal.get()
    if p is None or p.building_ids is None:
        return rows
    allowed = {str(b) for b in p.building_ids}
    return [r for r in rows if str(r.get(column) or "") in allowed]


#: The vendors that have any footprint on a set of buildings: a certificate filed for one,
#: a work order raised on one, an invoice verified against one. Vendor tables carry no
#: building of their own, so this is what "the vendors for my buildings" means everywhere a
#: restricted caller asks about vendors. vendor_id is uuid on all three tables.
VENDORS_ON_BUILDINGS_SQL = (
    "(SELECT c.vendor_id FROM plenum_cafm.compliance_certificates c"
    " WHERE c.vendor_id IS NOT NULL AND c.building_id = ANY(CAST(:{p} AS uuid[]))"
    " UNION SELECT w.vendor_id FROM plenum_cafm.work_orders w"
    " WHERE w.vendor_id IS NOT NULL AND w.building_id = ANY(CAST(:{p} AS uuid[]))"
    " UNION SELECT i.vendor_id FROM plenum_cafm.invoices i"
    " WHERE i.vendor_id IS NOT NULL AND i.building_id = ANY(CAST(:{p} AS uuid[])))"
)
#: A building's site key, on a portfolio where sites are keyed by text and buildings link
#: to them by site_id — plus the building id itself, for tables that key the site as the
#: building's uuid (the energy tables here).
SITES_ON_BUILDINGS_SQL = (
    "(SELECT b.site_id FROM plenum_cafm.buildings b"
    " WHERE b.site_id IS NOT NULL AND b.building_id = ANY(CAST(:{p} AS uuid[]))"
    " UNION SELECT u::text FROM unnest(CAST(:{p} AS uuid[])) AS u)"
)
ASSETS_ON_BUILDINGS_SQL = (
    "(SELECT a.id::text FROM plenum_cafm.assets a WHERE a.building_id = ANY(CAST(:{p} AS uuid[])))"
)
METERS_ON_BUILDINGS_SQL = (
    "(SELECT m.id FROM plenum_cafm.energy_meters m WHERE m.building_id = ANY(CAST(:{p} AS uuid[])))"
)

_columns_cache: dict[str, frozenset[str]] = {}
#: The same identifier rule table_customizer applies — a name that fails it never reaches SQL.
_SAFE_IDENT = re.compile(r"^[a-z_][a-z0-9_]{0,63}$")


async def table_columns(session, table_name: str) -> frozenset[str]:
    """The columns plenum_cafm.<table> actually has, from the catalogue, cached per process."""
    if not _SAFE_IDENT.match(table_name or ""):
        return frozenset()
    hit = _columns_cache.get(table_name)
    if hit is not None:
        return hit
    from sqlalchemy import text as _text
    rows = await session.execute(
        _text("SELECT column_name FROM information_schema.columns"
              " WHERE table_schema = 'plenum_cafm' AND table_name = :t"),
        {"t": table_name},
    )
    cols = frozenset(str(r[0]) for r in rows)
    if cols:
        _columns_cache[table_name] = cols
    return cols


async def table_building_clause(session, table_name: str, *, alias: str = "",
                                param: str = "scope_buildings") -> tuple[str, dict]:
    """The caller's building boundary expressed against whatever this table keys on.

    Decided from the table's real columns, never from the request: a building_id column is
    used directly; a vendor table is narrowed to the vendors with a footprint on the caller's
    buildings; sites, assets and meters through their own links; energy tables through the
    site_id that names the building here. A table with none of those carries nothing that
    belongs to a building — reference data — and is read whole. Admins and background jobs
    (no caller) get no clause at all.
    """
    p = caller_principal.get()
    if p is None or p.building_ids is None:
        return "", {}
    if not p.building_ids:
        return " AND FALSE", {}
    params = {param: [str(b) for b in p.building_ids]}
    pre = f"{alias}." if alias else ""
    cols = await table_columns(session, table_name)
    if not cols:
        # Not a table the catalogue knows: nothing can be narrowed, so nothing is read.
        return " AND FALSE", {}
    if "building_id" in cols:
        return f" AND {pre}building_id = ANY(CAST(:{param} AS uuid[]))", params
    if table_name == "vendors":
        return f" AND {pre}id::text IN (SELECT vendor_id::text FROM {VENDORS_ON_BUILDINGS_SQL.format(p=param)} v)", params
    if table_name == "sites":
        return f" AND {pre}id::text IN {SITES_ON_BUILDINGS_SQL.format(p=param)}", params
    if table_name.startswith("energy_") and "site_id" in cols:
        return f" AND {pre}site_id = ANY(CAST(:{param} AS uuid[]))", params
    if "meter_id" in cols:
        return f" AND {pre}meter_id IN {METERS_ON_BUILDINGS_SQL.format(p=param)}", params
    if "vendor_id" in cols:
        return f" AND {pre}vendor_id::text IN (SELECT vendor_id::text FROM {VENDORS_ON_BUILDINGS_SQL.format(p=param)} v)", params
    if "asset_id" in cols:
        return f" AND {pre}asset_id::text IN {ASSETS_ON_BUILDINGS_SQL.format(p=param)}", params
    if "site_id" in cols:
        return f" AND {pre}site_id::text IN {SITES_ON_BUILDINGS_SQL.format(p=param)}", params
    return "", {}


def certificate_clause(alias: str = "c", *, param: str = "scope_buildings") -> tuple[str, dict]:
    """The compliance rule, the same one operations-intelligence applies on its own list:
    a restricted caller sees certificates filed for their buildings, plus vendor
    accreditations — which name no property — and never another building's certificate."""
    p = caller_principal.get()
    if p is None or p.building_ids is None:
        return "", {}
    vendor_only = f"({alias}.building_id IS NULL AND lower({alias}.cert_scope) = 'vendor')"
    if not p.building_ids:
        return f" AND {vendor_only}", {}
    return (f" AND ({alias}.building_id = ANY(CAST(:{param} AS uuid[])) OR {vendor_only})",
            {param: [str(b) for b in p.building_ids]})


async def restrict_records(session, table_name: str, rows: list[dict]) -> tuple[list[dict], str | None]:
    """The same boundary applied to rows another service returned unfiltered.

    Rows are re-checked against the database with the table's own clause rather than
    trusted by their fields, so a table keyed on vendor or asset is narrowed correctly too.
    Returns the rows that pass and, when the whole read had to be refused, why.
    """
    p = caller_principal.get()
    if p is None or p.building_ids is None or not rows:
        return rows, None
    clause, params = await table_building_clause(session, table_name)
    if not clause:
        return rows, None
    if clause.strip() == "AND FALSE":
        return [], "Your account is not allocated to any building, so there is nothing to show."
    cols = await table_columns(session, table_name)
    if "id" not in cols or not all("id" in r for r in rows):
        if all("building_id" in r for r in rows):
            return restrict_rows(rows, "building_id"), None
        return [], f"{table_name} rows cannot be checked against your buildings; ask by building instead."
    from sqlalchemy import text as _text
    ids = [str(r["id"]) for r in rows]
    kept = await session.execute(
        _text(f"SELECT id::text FROM plenum_cafm.{table_name} WHERE id::text = ANY(:ids){clause}"),
        {"ids": ids, **params},
    )
    allowed = {str(k[0]) for k in kept}
    return [r for r in rows if str(r["id"]) in allowed], None
