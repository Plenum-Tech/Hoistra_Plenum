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
