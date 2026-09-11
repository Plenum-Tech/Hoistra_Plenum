"""The building is the access boundary. This is the one place that says so.

Thirty routes in this service take organization_id from the client and none derives it from
the signed-in caller; no route filters by building at all. So tenancy is a convention the
client is trusted to follow, and a user allocated to one building can read every building
by asking. The spec calls this out as the important backend requirement, and it is.

Everything here resolves to one object, ``Scope``, built from the Principal that
principal_from_token already reads from the database on every request:

    organization_id   the company. A superadmin may name another; nobody else may.
    building_ids      None for an admin (the whole company), a tuple for a user — and an
                      EMPTY tuple is a real answer meaning "allocated to nothing".
    can_ingest        whether new data may be added at all.

Routes depend on ``scope`` and hand it to the engines, which call ``building_filter`` to
get the SQL fragment that narrows a building-keyed query and ``assert_building`` before
writing anything against a building. A route that does neither is a route the boundary does
not reach, and the two helpers are shaped so that using them is shorter than not.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Query, status

from . import roles as role_engine
from . import tokens as token_engine


@dataclass(frozen=True)
class Scope:
    """What the caller may see and do, resolved once per request."""

    user_id: UUID
    role: str
    organization_id: UUID | None
    #: None = unrestricted within the organisation. () = restricted to nothing.
    building_ids: tuple[UUID, ...] | None
    can_ingest: bool

    @property
    def restricted(self) -> bool:
        return self.building_ids is not None

    @property
    def is_admin(self) -> bool:
        return role_engine.at_least(self.role, role_engine.ADMIN)

    @property
    def is_superadmin(self) -> bool:
        return role_engine.at_least(self.role, role_engine.SUPERADMIN)

    def allows_building(self, building_id: UUID | str | None) -> bool:
        """Whether this caller may read or write against one building."""
        if building_id is None:
            return False
        if not self.restricted:
            return True
        try:
            return UUID(str(building_id)) in (self.building_ids or ())
        except (ValueError, TypeError):
            return False


def _forbidden(error: str, reason: str, **extra: Any) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={"ok": False, "error": error, "reason": reason, **extra},
    )


def scope_for(
    principal: token_engine.Principal, requested_org: UUID | None = None
) -> Scope:
    """Build the scope, honouring an explicit organisation only from a superadmin.

    Anyone else asking for a company other than their own gets 403 rather than silently
    their own — a client that sends the wrong id has a bug worth surfacing, and silently
    substituting would hide it until it mattered.
    """
    org = principal.organization_id
    if requested_org is not None and requested_org != org:
        if not role_engine.at_least(principal.role, role_engine.SUPERADMIN):
            raise _forbidden(
                "You can only work within your own company.", "wrong_organization",
                your_organization_id=str(org) if org else None,
            )
        org = requested_org
    return Scope(
        user_id=principal.user_id,
        role=principal.role,
        organization_id=org,
        building_ids=principal.building_ids,
        can_ingest=principal.can_ingest,
    )


def building_filter(
    scope: Scope, column: str = "building_id", *, prefix: str = "scope"
) -> tuple[str, dict[str, Any]]:
    """A SQL fragment and its parameters that narrow a building-keyed query to the scope.

    Returns ("", {}) for an unrestricted caller, so it can be appended unconditionally:

        sql, params = building_filter(scope, "d.building_id")
        text(f"SELECT … FROM plenum_cafm.documents d WHERE d.building_id IS NOT NULL {sql}")

    ``column`` is an identifier the CALLER chose, never one from a request — the same rule
    as everywhere else in this repo. It is validated anyway, because a helper used in thirty
    places will one day be handed a variable.
    """
    if not scope.restricted:
        return "", {}
    if not _SAFE_COLUMN.match(column):
        raise ValueError(f"unsafe column reference: {column!r}")
    ids = [str(b) for b in (scope.building_ids or ())]
    if not ids:
        # Allocated to nothing. A predicate that matches no row, rather than no predicate,
        # which would match every row — the exact failure this module exists to remove.
        return " AND FALSE", {}
    key = f"{prefix}_building_ids"
    return f" AND {column} = ANY(CAST(:{key} AS uuid[]))", {key: ids}


def assert_building(scope: Scope, building_id: UUID | str | None, *, action: str = "access") -> UUID:
    """403 unless this caller may act on this building. Returns the id as a UUID."""
    if building_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"ok": False, "error": "A building is required.", "reason": "no_building"},
        )
    try:
        bid = UUID(str(building_id))
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"ok": False, "error": "building_id is not a UUID.", "reason": "bad_building"},
        ) from None
    if not scope.allows_building(bid):
        raise _forbidden(
            f"You are not allocated to that building, so you cannot {action} its data.",
            "building_not_allocated", building_id=str(bid),
        )
    return bid


def assert_can_ingest(scope: Scope) -> None:
    """403 unless the caller may add data. Read-only users are told so, not stonewalled."""
    if not scope.can_ingest:
        raise _forbidden(
            "Your account can view its buildings but cannot ingest data. "
            "Ask your company administrator to grant ingestion.",
            "cannot_ingest",
        )


import re  # noqa: E402 — placed after the definitions it guards for readability

_SAFE_COLUMN = re.compile(r"^[a-z_][a-z0-9_]*(\.[a-z_][a-z0-9_]*)?$")


# ── FastAPI dependencies ─────────────────────────────────────────────────────────────
# Imported lazily inside the dependency so this module does not import the auth router
# (which imports this) at module load.

async def scope(
    organization_id: UUID | None = Query(
        None,
        description="Superadmin only: act as this company. Everyone else is scoped to "
                    "their own and gets 403 for any other value.",
    ),
    principal: token_engine.Principal = Depends(lambda: None),  # replaced below
) -> Scope:  # pragma: no cover — replaced by _bind() at import of the routes package
    raise RuntimeError("access.scope must be bound with access.bind(current_principal)")


def bind(current_principal_dep, get_session_dep=None):
    """Produce the real ``scope`` dependency given the router's current_principal.

    Done this way because current_principal lives in api/routes/auth.py, which imports this
    module; a direct import here would be circular. The routes package calls bind() once and
    exports the result.

    The enforcement switch is read ONCE, here, and decides which dependency is returned.
    Settings are fixed for the life of the process, and choosing at bind time keeps the
    signed-in path a plain ``Depends(current_principal)`` — so FastAPI's dependency
    overrides still reach it in tests, and a route cannot be accidentally half-enforced.
    """
    from ...config import settings  # local: config imports nothing from here

    if not settings.auth_enforce_scope:
        import structlog
        structlog.get_logger(__name__).warning(
            "access.scope_enforcement_disabled",
            note="every domain route is open and unscoped; AUTH_ENFORCE_SCOPE=false",
        )

        async def _unscoped(
            organization_id: UUID | None = Query(None),
        ) -> Scope:
            # The boundary is switched off. Said on every request, because a switch meant
            # for one bad afternoon becomes permanent when it is silent.
            structlog.get_logger(__name__).warning("access.scope_enforcement_disabled")
            return Scope(user_id=UUID(int=0), role=role_engine.SUPERADMIN,
                         organization_id=organization_id, building_ids=None, can_ingest=True)

        return _unscoped, _unscoped

    async def _scope(
        organization_id: UUID | None = Query(
            None,
            description="Superadmin only: act as this company. Everyone else is scoped to "
                        "their own and gets 403 for any other value.",
        ),
        principal: token_engine.Principal = Depends(current_principal_dep),
    ) -> Scope:
        return scope_for(principal, organization_id)

    async def _ingest_scope(s: Scope = Depends(_scope)) -> Scope:
        assert_can_ingest(s)
        return s

    return _scope, _ingest_scope
