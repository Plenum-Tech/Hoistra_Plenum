"""Who is asking — resolved from operations-intelligence, never decided here.

This service had no notion of a caller. Every route took a session and answered for the
whole database: any bearer of the URL could list every work order, asset and location in
every company, and a user allocated to one building saw them all. The UDR agent calls these
routes fifteen times per conversation, carrying the caller's token — which this service then
ignored.

The token is verified the same way svc-deepagents verifies it: by asking
operations-intelligence ``GET /api/auth/me`` with it. That service owns accounts, roles and
building allocations, and answers with the caller's ``building_ids`` — None for an admin
(the whole company), a list for a user, an empty list for a user allocated to nothing. This
module does not decode the token, does not hold the signing secret, and cannot disagree with
the source of truth about who may see what.

Resolved principals are cached for a short while per token so a burst of tool calls in one
conversation costs one round trip, not fifteen.
"""
from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import httpx
from fastapi import Header, HTTPException, status

#: The service that owns identity. Same container on the deployed app, so loopback.
OPS_BASE_URL = os.environ.get("OPERATIONS_INTELLIGENCE_BASE_URL", "http://127.0.0.1:8009").rstrip("/")
#: How long a resolved principal is trusted before /me is asked again.
CACHE_TTL_S = float(os.environ.get("PRINCIPAL_CACHE_TTL_SECONDS", "60"))

_cache: dict[str, tuple[float, "Principal"]] = {}


@dataclass(frozen=True)
class Principal:
    user_id: UUID
    email: str
    organization_id: UUID | None
    role: str
    #: None = every building in the company. () = allocated to nothing.
    building_ids: tuple[UUID, ...] | None
    buildings: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    selected_building_id: UUID | None = None

    @property
    def restricted(self) -> bool:
        return self.building_ids is not None

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
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                         detail={"ok": False, "error": error, "reason": reason})


def _from_me(payload: dict[str, Any]) -> Principal:
    user = payload.get("user") or {}
    ids = user.get("building_ids")
    building_ids = None if ids is None else tuple(UUID(str(b)) for b in ids)
    # A selected building narrows what this caller sees, exactly as Scope does upstream —
    # only if it is one they may see. /me only ever returns a selection that passed that
    # check, but the rule is cheap to restate and expensive to get wrong.
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
        buildings=tuple(user.get("buildings") or ()),
        selected_building_id=selected,
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
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail={
            "ok": False, "error": "The identity service is unreachable.", "reason": "identity_unavailable",
            "detail": str(exc)[:120]}) from exc
    if resp.status_code == 401:
        detail = {}
        try:
            detail = (resp.json() or {}).get("detail") or {}
        except ValueError:
            pass
        raise _unauthorized(detail.get("error") or "Sign in again.", detail.get("reason") or "invalid_token")
    if resp.status_code >= 400:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail={
            "ok": False, "error": "The identity service refused the check.", "reason": "identity_error"})
    principal = _from_me(resp.json())
    _cache[key] = (time.monotonic() + CACHE_TTL_S, principal)
    return principal


async def current_principal(authorization: str | None = Header(default=None)) -> Principal:
    """FastAPI dependency: the signed-in caller, or 401."""
    return await resolve(authorization)


def building_clause(principal: Principal, column: str, *, param: str = "scope_buildings") -> tuple[str, dict]:
    """The predicate that narrows a building-keyed query to this caller.

    ("", {}) for an admin. " AND FALSE" for a user allocated to nothing — a predicate that
    matches no row, never the absence of one. The column is chosen by the route, never by
    the request.
    """
    if principal.building_ids is None:
        return "", {}
    if not principal.building_ids:
        return " AND FALSE", {}
    return f" AND {column} = ANY(CAST(:{param} AS uuid[]))", {param: [str(b) for b in principal.building_ids]}


def scope_select(q, principal: Principal, column):
    """Narrow a SQLAlchemy select on a building column to this caller.

    Unchanged for an admin; a predicate that matches no row for a user allocated to
    nothing; otherwise ``column IN (their buildings)``. The column is the route's choice.
    """
    if principal.building_ids is None:
        return q
    if not principal.building_ids:
        from sqlalchemy import false

        return q.where(false())
    return q.where(column.in_(list(principal.building_ids)))


def assert_building(principal: Principal, building_id, *, action: str = "read") -> None:
    """403 unless this caller may act on this building — same detail shape and reason as
    operations-intelligence, so one client handles both."""
    if principal.building_ids is None:
        return
    if principal.allows_building(building_id):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={
            "code": "building_not_allocated",
            "reason": "building_not_allocated",
            "message": f"You are not allocated to that building, so you cannot {action} its data.",
            "building_id": str(building_id) if building_id else None,
        },
    )
