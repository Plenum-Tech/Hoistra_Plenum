"""Who is asking, and what the answer is allowed to contain.

Every route under ``/api/v1/plenum`` and every route of the Table Editor took a database
session and nothing else. There was no caller: ``get_current_user`` returned
``TokenPayload(sub="anonymous")`` and the real JWT check sat commented out inside a string
literal. The service is pointed at the production database, so anyone who could reach it
could read or write any company's assets, work orders, users, roles and spare parts, and
the Table Editor could add or drop columns on top of that.

Identity is not decided here. It is resolved the way svc-work-order-management and
svc-deepagents resolve it — by asking operations-intelligence ``GET /api/auth/me`` with the
caller's token. That service owns accounts, roles and building allocations. This module does
not decode the token, does not hold the signing secret, and cannot disagree with the source
of truth about who may see what.

Two things then happen, and the second is the one that matters:

``current_principal``  401s a request that carries no usable token.

``guard`` binds the resolved caller to the SQLAlchemy session, so the company filter is
applied by the session rather than by the route. There are 135 CRUD routes here; a rule that
each route has to remember is a rule that is one forgotten route away from being no rule at
all. Instead every ORM SELECT gains ``organization_id = <the caller's company>`` through
``with_loader_criteria``, and every INSERT, UPDATE and DELETE is checked at flush. A route
that names no company is therefore scoped anyway, and a route that takes ``organization_id``
from the query string — as all of them do — can no longer be asked for somebody else's.

What this deliberately does not do is invent a building boundary the connector's models do
not have. Where a model maps ``building_id`` the allocation is applied too; where it does
not, company scope is the boundary and the building-scoped services remain the place to read
building-restricted data.
"""
from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass
from typing import Any, Iterable
from uuid import UUID

import httpx
from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import and_ as sa_and, event, false as sa_false
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import with_loader_criteria

from cafm_connector.core.logging import get_logger
from cafm_connector.models.plenum_cafm import PlenumBase

log = get_logger(__name__)

#: The service that owns identity. Same container on the deployed app, so loopback.
OPS_BASE_URL = os.environ.get(
    "OPERATIONS_INTELLIGENCE_BASE_URL", "http://127.0.0.1:8009"
).rstrip("/")
#: How long a resolved principal is trusted before /me is asked again.
CACHE_TTL_S = float(os.environ.get("PRINCIPAL_CACHE_TTL_SECONDS", "60"))

_cache: dict[str, tuple[float, "Principal"]] = {}

ADMIN_ROLES = ("admin", "superadmin", "owner")
SUPERADMIN_ROLES = ("superadmin", "owner")


@dataclass(frozen=True)
class Principal:
    user_id: UUID
    email: str
    organization_id: UUID | None
    role: str
    #: None = every building in the company. () = allocated to nothing.
    building_ids: tuple[UUID, ...] | None

    @property
    def is_admin(self) -> bool:
        return (self.role or "").lower() in ADMIN_ROLES

    @property
    def is_superadmin(self) -> bool:
        return (self.role or "").lower() in SUPERADMIN_ROLES

    def allows_building(self, building_id: Any) -> bool:
        if self.building_ids is None:
            return True
        if building_id is None:
            return False
        try:
            return UUID(str(building_id)) in self.building_ids
        except (ValueError, TypeError):
            return False


def _detail(error: str, reason: str, **extra: Any) -> dict:
    """Both vocabularies at once — operations-intelligence's {error, reason} and this
    service's {code, message} — so one client handles every service."""
    return {"ok": False, "error": error, "reason": reason, "code": reason,
            "message": error, **extra}


def _unauthorized(error: str, reason: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                         detail=_detail(error, reason),
                         headers={"WWW-Authenticate": "Bearer"})


def _forbidden(error: str, reason: str, **extra: Any) -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                         detail=_detail(error, reason, **extra))


def _from_me(payload: dict[str, Any]) -> Principal:
    user = payload.get("user") or {}
    ids = user.get("building_ids")
    building_ids = None if ids is None else tuple(UUID(str(b)) for b in ids)
    # A selected building narrows what this caller sees, exactly as it does upstream, and
    # only if it is one they may see. It can never widen.
    sel = user.get("selected_building_id")
    selected = UUID(str(sel)) if sel else None
    if selected is not None and (building_ids is None or selected in building_ids):
        building_ids = (selected,)
    return Principal(
        user_id=UUID(str(user["id"])),
        email=str(user.get("email") or ""),
        organization_id=UUID(str(user["organization_id"])) if user.get("organization_id") else None,
        role=str(user.get("platform_role") or user.get("role") or "user"),
        building_ids=building_ids,
    )


async def resolve(authorization: str | None) -> Principal:
    scheme, _, token = (authorization or "").partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token:
        raise _unauthorized("Send an Authorization: Bearer <token> header.", "missing_token")
    key = hashlib.sha256(token.encode("utf-8")).hexdigest()
    hit = _cache.get(key)
    if hit and hit[0] > time.monotonic():
        return hit[1]
    try:
        async with httpx.AsyncClient(base_url=OPS_BASE_URL, timeout=8.0) as client:
            resp = await client.get("/api/auth/me", headers={"Authorization": authorization})
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=_detail(
            "The identity service is unreachable.", "identity_unavailable",
            detail=str(exc)[:120])) from exc
    if resp.status_code == 401:
        detail = {}
        try:
            detail = (resp.json() or {}).get("detail") or {}
        except ValueError:
            pass
        raise _unauthorized(detail.get("error") or "Sign in again.",
                            detail.get("reason") or "invalid_token")
    if resp.status_code >= 400:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=_detail(
            "The identity service refused the check.", "identity_error"))
    principal = _from_me(resp.json())
    _cache[key] = (time.monotonic() + CACHE_TTL_S, principal)
    return principal


async def current_principal(authorization: str | None = Header(default=None)) -> Principal:
    """FastAPI dependency: the signed-in caller, or 401."""
    return await resolve(authorization)


async def require_admin(principal: Principal = Depends(current_principal)) -> Principal:
    """FastAPI dependency: the caller, if they administer the company — or 403.

    The Table Editor reads and writes arbitrary rows in arbitrary tables and can add or drop
    columns. No per-row company filter can be expressed over a query that names its table at
    runtime, so the gate is the role instead.
    """
    if not principal.is_admin:
        raise _forbidden(
            "Only an administrator can use the table editor.", "admin_required",
            your_role=principal.role,
        )
    return principal


# ── binding the caller to the session ────────────────────────────────────────────────

def _scoped_models() -> list[type]:
    """Every mapped class that has a column worth narrowing, read from the registry.

    Read rather than listed, so a model added later is scoped the day it is written instead
    of the day somebody remembers to add it to a list here.
    """
    out = []
    for mapper in PlenumBase.registry.mappers:
        cls = mapper.class_
        if hasattr(cls, "organization_id") or hasattr(cls, "building_id"):
            out.append(cls)
    return out


def _options_for(principal: Principal) -> list[Any]:
    """One loader criterion per mapped class, as plain SQL expressions.

    Deliberately not a lambda against the declarative base: SQLAlchemy compiles a lambda
    criterion once and keys the cache on the lambda's code, so a per-request value like the
    caller's company is either rejected as uncacheable or — worse — baked in from whichever
    caller compiled it first. Plain expressions bind their values per execution.
    """
    org = principal.organization_id
    allowed = None if principal.building_ids is None else list(principal.building_ids)
    options = []
    for cls in _scoped_models():
        clauses = []
        org_col = getattr(cls, "organization_id", None)
        if org_col is not None:
            clauses.append(org_col == org)
        bld_col = getattr(cls, "building_id", None)
        if bld_col is not None and allowed is not None:
            # An empty allocation is a real answer meaning "allocated to nothing", so it
            # becomes a predicate matching no row — never the absence of a predicate.
            clauses.append(bld_col.in_(allowed) if allowed else sa_false())
        if clauses:
            options.append(
                with_loader_criteria(cls, sa_and(*clauses), include_aliases=True)
            )
    return options


def guard(session: AsyncSession, principal: Principal) -> AsyncSession:
    """Make this session answer only for this caller's company and buildings.

    Reads: every ORM SELECT gains the criteria, which reach the entity wherever it appears —
    ``session.get()``, eager loads, aliases, and the
    ``select(count()).select_from(select(Model).subquery())`` shape these routes use for
    their pagination totals, so a restricted caller's count matches their rows.

    Writes: checked at flush, because an INSERT carrying somebody else's ``organization_id``
    is the same breach as a SELECT of their rows. An insert that names no company is stamped
    with the caller's rather than rejected: every one of these routes builds its object from
    a request body that was free to omit the field, and a row with a null company belongs to
    everyone.

    A superadmin is not narrowed — that role exists to work across companies, and
    operations-intelligence is where it is granted.
    """
    if principal.is_superadmin:
        return session
    org = principal.organization_id
    options = _options_for(principal)
    sync_session = session.sync_session

    @event.listens_for(sync_session, "do_orm_execute")
    def _narrow_reads(state) -> None:  # pragma: no cover - exercised against the database
        if not state.is_select or state.is_column_load or state.is_relationship_load:
            return
        for option in options:
            state.statement = state.statement.options(option)

    @event.listens_for(sync_session, "before_flush")
    def _check_writes(sess, _flush_context, _instances) -> None:  # pragma: no cover
        for obj in sess.new:
            if hasattr(obj, "organization_id"):
                current = getattr(obj, "organization_id", None)
                if current is None:
                    setattr(obj, "organization_id", org)
                elif str(current) != str(org):
                    raise _forbidden(
                        "You can only create records in your own company.",
                        "wrong_organization",
                        your_organization_id=str(org) if org else None,
                    )
            _check_building(obj, principal, "create")
        changed = [o for o in sess.dirty if sess.is_modified(o, include_collections=False)]
        for obj in changed + list(sess.deleted):
            if hasattr(obj, "organization_id"):
                current = getattr(obj, "organization_id", None)
                if current is not None and str(current) != str(org):
                    raise _forbidden(
                        "That record belongs to another company.", "wrong_organization",
                        your_organization_id=str(org) if org else None,
                    )
            _check_building(obj, principal, "change")

    return session


def _check_building(obj: Any, principal: Principal, action: str) -> None:
    if principal.building_ids is None or not hasattr(obj, "building_id"):
        return
    building_id = getattr(obj, "building_id", None)
    if building_id is None or principal.allows_building(building_id):
        return
    raise _forbidden(
        f"You are not allocated to that building, so you cannot {action} its records.",
        "building_not_allocated", building_id=str(building_id),
    )
