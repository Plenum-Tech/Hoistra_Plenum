"""The three platform roles, and who is allowed to hand them out.

    superadmin  the whole platform, across every organisation
    admin       everything inside their own organisation
    user        a facilities manager — the default

Ordered, so "at least admin" is one comparison rather than a set of special cases that
someone eventually gets wrong by forgetting to add superadmin to one of them.

Three rules govern changes, and each exists because of a specific way this goes wrong:

  **Self-registration never grants anything.** A new account is always ``user``. An
  endpoint that takes the role from the request body is an endpoint where anyone can
  register as an administrator.

  **Nobody changes their own role.** Not even a superadmin — which sounds paternalistic
  until you notice it is the only thing standing between one compromised admin session
  and a self-promotion, and that a superadmin demoting themselves by accident can leave a
  platform with no superadmin and no way to appoint one.

  **An admin cannot make or unmake a superadmin.** Otherwise "admin" and "superadmin" are
  the same role with two names, since the first can always award itself the second by way
  of a colleague.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from . import keys

from ...core.logging import get_logger

log = get_logger(__name__)

SUPERADMIN = "superadmin"
ADMIN = "admin"
USER = "user"

#: Rank, not just membership. Comparing ranks means a new role slots in by editing this
#: one dict, instead of by finding every `in {"admin", "superadmin"}` in the codebase.
RANK: dict[str, int] = {USER: 10, ADMIN: 20, SUPERADMIN: 30}

ROLES = frozenset(RANK)

#: What each one is, in the words a person would use. Returned by the API so a client
#: does not have to hard-code its own descriptions and drift from these.
LABELS: dict[str, str] = {
    SUPERADMIN: "Superadmin — the whole platform, across every organisation",
    ADMIN: "Admin — everything inside their own organisation",
    USER: "Facilities manager — the default for a new account",
}

#: What self-registration creates, always.
DEFAULT_ROLE = USER


def rank(role: str | None) -> int:
    """The rank of a role. An unknown role ranks below everything — fails closed.

    A row whose role column somehow holds 'Admin' or 'fm' grants nothing rather than
    everything, and the account behaves like an ordinary user until someone notices.
    """
    return RANK.get(str(role or "").strip().lower(), 0)


def at_least(role: str | None, minimum: str) -> bool:
    return rank(role) >= rank(minimum)


def is_admin(role: str | None) -> bool:
    """Admin or above — the ordinary "may administer" test."""
    return at_least(role, ADMIN)


def normalise(role: str | None) -> str | None:
    value = str(role or "").strip().lower()
    return value if value in ROLES else None


@dataclass(frozen=True)
class RoleDecision:
    allowed: bool
    reason: str
    message: str


def may_assign(
    *,
    actor_role: str,
    actor_id: UUID,
    actor_org: UUID | None,
    target_id: UUID,
    target_role: str,
    target_org: UUID | None,
    new_role: str,
) -> RoleDecision:
    """Whether ``actor`` may set ``target``'s role to ``new_role``.

    Pure, so every branch is testable without a database — which matters here, because
    these are the rules that decide who can grant themselves the run of the platform.
    """
    wanted = normalise(new_role)
    if wanted is None:
        return RoleDecision(False, "unknown_role",
                            f"Unknown role. Choose one of: {', '.join(sorted(ROLES))}.")

    if actor_id == target_id:
        return RoleDecision(
            False, "self",
            "You cannot change your own role. Ask another administrator — this is what "
            "stops one taken-over session from promoting itself.",
        )

    if not is_admin(actor_role):
        return RoleDecision(False, "forbidden", "Only an administrator can change a role.")

    if at_least(actor_role, SUPERADMIN):
        return RoleDecision(True, "superadmin", "")

    # From here the actor is an admin, not a superadmin.
    if wanted == SUPERADMIN:
        return RoleDecision(
            False, "cannot_grant_superadmin",
            "An admin cannot appoint a superadmin. Otherwise the two roles are the same "
            "role with two names, because any admin could award themselves the other "
            "through a colleague.",
        )
    if target_role == SUPERADMIN:
        return RoleDecision(
            False, "cannot_demote_superadmin",
            "An admin cannot change a superadmin's role.",
        )
    if actor_org is None or target_org is None or actor_org != target_org:
        return RoleDecision(
            False, "other_organisation",
            "That account belongs to another organisation.",
        )
    return RoleDecision(True, "admin_in_own_org", "")


async def superadmin_exists(session: AsyncSession) -> bool:
    """Whether the platform has appointed anyone yet — the bootstrap question."""
    found = (
        await session.execute(
            text("SELECT 1 FROM plenum_cafm.users WHERE role = :r LIMIT 1"),
            {"r": SUPERADMIN},
        )
    ).first()
    return found is not None


async def record_change(
    session: AsyncSession,
    *,
    user_id: UUID,
    from_role: str | None,
    to_role: str,
    changed_by: UUID | None,
    reason: str | None = None,
    request_ip: str | None = None,
) -> None:
    """Append the change to the audit table. Never updates, never deletes."""
    await session.execute(
        text(
            """INSERT INTO plenum_cafm.auth_role_changes
                   (user_id, changed_by, from_role, to_role, reason, request_ip)
               VALUES (:u, :b, :f, :t, :r, :ip)"""
        ),
        {"u": await keys.user_key(session, user_id),
         "b": await keys.user_key(session, changed_by) if changed_by else None,
         "f": from_role, "t": to_role, "r": reason, "ip": request_ip},
    )
    log.info("auth.role_changed", user_id=str(user_id), **{"from": from_role},
             to=to_role, by=str(changed_by) if changed_by else "platform")


def describe() -> list[dict[str, Any]]:
    """The roles, ranked, for a client building a picker."""
    return [
        {"role": r, "rank": RANK[r], "label": LABELS[r], "default": r == DEFAULT_ROLE}
        for r in sorted(ROLES, key=lambda x: -RANK[x])
    ]
