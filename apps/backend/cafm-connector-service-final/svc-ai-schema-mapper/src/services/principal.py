"""Who is asking - resolved from operations-intelligence, never decided here.

This service has had no notion of a caller: every /api/migration route answers anyone who has
the URL. Measured 30 Sep 2026 against the deployed app, anonymously: a migration's status came
back 200, and DELETE /api/migration/{id} - cancel - took no token either. Cancel is the first
route to be narrowed, because it is the one the interface now offers as a button.

The token is checked the way svc-work-order-management and svc-deepagents check it: by asking
operations-intelligence ``GET /api/auth/me`` with it. That service owns accounts and roles; this
module does not decode tokens and holds no signing secret. Resolved callers are cached for a
minute per token.
"""
from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass
from uuid import UUID

import httpx
from fastapi import Header, HTTPException, status

OPS_BASE_URL = os.environ.get("OPERATIONS_INTELLIGENCE_BASE_URL", "http://127.0.0.1:8009").rstrip("/")
CACHE_TTL_S = float(os.environ.get("PRINCIPAL_CACHE_TTL_SECONDS", "60"))

_cache: dict[str, tuple[float, "Principal"]] = {}


@dataclass(frozen=True)
class Principal:
    user_id: UUID
    email: str
    organization_id: UUID | None
    role: str
    can_ingest: bool

    @property
    def is_superadmin(self) -> bool:
        return self.role == "superadmin"

    @property
    def is_admin(self) -> bool:
        return self.role in ("admin", "superadmin")


def _detail(error: str, reason: str) -> dict:
    return {"ok": False, "error": error, "reason": reason}


def from_me(payload: dict) -> Principal:
    user = payload.get("user") or {}
    return Principal(
        user_id=UUID(str(user["id"])),
        email=str(user.get("email") or ""),
        organization_id=UUID(str(user["organization_id"])) if user.get("organization_id") else None,
        role=str(user.get("platform_role") or user.get("role") or "user"),
        can_ingest=bool(user.get("can_ingest")),
    )


async def resolve(authorization: str | None) -> Principal:
    scheme, _, token = (authorization or "").partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                            detail=_detail("Sign in to do this.", "missing_token"))
    key = hashlib.sha256(token.encode("utf-8")).hexdigest()
    hit = _cache.get(key)
    if hit and hit[0] > time.monotonic():
        return hit[1]
    try:
        async with httpx.AsyncClient(base_url=OPS_BASE_URL, timeout=8.0) as client:
            resp = await client.get("/api/auth/me", headers={"Authorization": "Bearer " + token})
    except httpx.HTTPError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail=_detail("The identity service is unreachable.", "identity_unavailable")) from exc
    if resp.status_code == 401:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=_detail("Sign in again.", "invalid_token"))
    if resp.status_code >= 400:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail=_detail("The identity service refused the check.", "identity_error"))
    principal = from_me(resp.json())
    _cache[key] = (time.monotonic() + CACHE_TTL_S, principal)
    return principal


async def current_principal(authorization: str | None = Header(default=None)) -> Principal:
    """FastAPI dependency: the signed-in caller, or 401."""
    return await resolve(authorization)


def may_cancel(principal: Principal, job_organization_id) -> tuple[bool, str]:
    """Whether this caller may stop this company's migration, and why not when they may not.

    A superadmin may stop any run. Anyone else must belong to the company the run is for, and be
    someone who may run migrations there: an admin, or a user with the ingestion right. A run
    with no company recorded (older jobs) can only be stopped by a superadmin - there is no
    company to check it against, and "nobody owns it" must not read as "anybody may".
    """
    if principal.is_superadmin:
        return True, ""
    if job_organization_id is None or principal.organization_id is None:
        return False, "This migration is not recorded against your company."
    if str(job_organization_id) != str(principal.organization_id):
        return False, "This migration belongs to another company."
    if not (principal.is_admin or principal.can_ingest):
        return False, "Only an admin or someone who may run migrations can cancel one."
    return True, ""
