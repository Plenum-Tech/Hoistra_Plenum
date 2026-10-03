"""Who is asking. This service had no idea, and it reads and writes every table.

svc-udr is the Universal Database Reader: eighteen routes that list every table in
``plenum_cafm``, describe any schema, read, search, create, update and delete rows in a table
named at request time, run a caller-supplied SELECT, and drive all of it from a
natural-language agent. None of them asked for a caller, and nginx routes ``/backend/udr/``
to it from the public internet. Verified before this change: an anonymous
``GET /backend/udr/api/tables/`` returned the full table list.

Identity is not decided here. It is resolved the way cafm-connector-service,
svc-work-order-management and svc-deepagents resolve it — by asking operations-intelligence
``GET /api/auth/me`` with the caller's token. This service holds no signing secret and cannot
disagree with the source of truth about who may see what.

Two gates, because the routes are not alike:

``current_principal`` is attached to every router, so no route can be reached without a token
and none can be added later that forgets to ask.

``require_admin`` is attached to the table and agent routers. Their table is chosen by the
request, so no per-row company filter can be written for them — a predicate needs to know
which column holds the company, and that is only knowable once the table is known. The
honest boundary for "read or write any row of any table, or ask an agent to" is the role.
The saved-spaces and run-history routes are different: they have a fixed shape and a real
``organization_id``, so they are scoped to the caller's company instead of gated by role.

Deep-agents calls these routes on a user's behalf and already forwards that user's bearer
(``http_client.caller_authorization`` is set at the top of each workflow route and attached
to every downstream call), so its UDR tools keep working and now answer as the person who
asked rather than as nobody.
"""
from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import httpx
from fastapi import Depends, Header, HTTPException, status

from ..core.logging import get_logger

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


def _detail(error: str, reason: str, **extra: Any) -> dict:
    """Both vocabularies at once — operations-intelligence's {error, reason} and this
    service's {code, message} — so one frontend handler covers every service."""
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

    For the routes whose table is named by the request. A company filter cannot be written
    for a query whose table is not known until it arrives, so the gate is the role instead
    of a predicate.
    """
    if not principal.is_admin:
        raise _forbidden(
            "Only an administrator can read and write arbitrary tables through the "
            "Universal Database Reader.", "admin_required", your_role=principal.role,
        )
    return principal


def organization_for(principal: Principal, requested: Any = None) -> UUID | None:
    """The company a request acts in, given the one the client named — if any.

    The caller's own by default. A different one is 403 unless the caller is a superadmin,
    who gets the company they asked for. Without this, an omitted organization_id reached
    the queries as None, which they read as "the rows belonging to nobody" — and a named one
    was simply believed.
    """
    if requested in (None, ""):
        return principal.organization_id
    try:
        wanted = UUID(str(requested))
    except (ValueError, TypeError):
        raise _forbidden("That is not a company id.", "bad_organization") from None
    if wanted == principal.organization_id or principal.is_superadmin:
        return wanted
    raise _forbidden(
        "You can only work within your own company.", "wrong_organization",
        your_organization_id=str(principal.organization_id) if principal.organization_id else None,
    )
