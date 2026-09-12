"""Invitations: a company admin invites a user, a superadmin invites a company's admin.

One mechanism for both, because they are the same act with a different role attached. The
invitee gets an email carrying a single-use link; following it lets them set a password, and
that activates the account. Until then the user row exists in status ``invited`` so the
console can show "pending invite", and the allocation and ingestion right are recorded on
the invitation so they apply the moment the account is activated — not when someone
remembers to come back and set them.

The token is stored hashed. The email carries the only plaintext copy, and a table anyone
with read access can query must not be a second one — the same reasoning the OTP codes
already follow in this package.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import settings
from ...core.logging import get_logger
from ...shared.approvals import send_platform_email
from . import passwords as password_engine
from . import roles as role_engine

log = get_logger(__name__)

INVITE_TTL_DAYS = 7


class InvitationError(Exception):
    def __init__(self, reason: str, message: str, http_status: int = 400):
        super().__init__(message)
        self.reason, self.message, self.http_status = reason, message, http_status


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _accept_url(token: str) -> str:
    base = (getattr(settings, "public_app_url", None) or "").rstrip("/")
    return f"{base}/accept-invitation?token={token}" if base else f"/accept-invitation?token={token}"


def _invite_email(*, name: str, company: str, role: str, url: str, days: int,
                  inviter: str | None) -> tuple[str, str, str]:
    """Subject, body, and the redacted body for the email log."""
    who = (name or "").split(" ")[0] or "there"
    what = ("administer" if role_engine.at_least(role, role_engine.ADMIN)
            else "use")
    by = f" {inviter} has" if inviter else " You have been"
    subject = f"You're invited to {company} on Hoistra"
    body = (
        f"Hello {who},\n\n"
        f"{by.strip()} invited you to {what} {company} on Hoistra.\n\n"
        f"Accept the invitation and set your password here:\n{url}\n\n"
        f"The link is valid for {days} days and can be used once. If you were not "
        f"expecting this, you can ignore it.\n"
    )
    redacted = body.replace(url, "<invitation link redacted>")
    return subject, body, redacted


async def _company_name(session: AsyncSession, organization_id: UUID) -> str:
    name = await session.execute(
        text("SELECT name FROM plenum_cafm.organizations WHERE id = :o"), {"o": organization_id}
    )
    return str(name.scalar() or "your company")


async def create(
    session: AsyncSession,
    *,
    organization_id: UUID,
    email: str,
    full_name: str | None,
    role: str,
    can_ingest: bool,
    building_ids: list[UUID],
    invited_by: UUID | None,
    inviter_name: str | None = None,
    job_title: str | None = None,
) -> dict[str, Any]:
    """Create the pending account and the invitation, and send the email.

    Idempotent on (organisation, email): re-inviting someone whose invitation is still
    open issues a fresh link and expires the old one, rather than creating a second account.
    An email that already belongs to an ACTIVE account is refused — that person exists and
    should be edited, not re-invited.
    """
    addr = (email or "").strip().lower()
    if not addr or "@" not in addr:
        raise InvitationError("bad_email", "A valid email address is required.")
    if role not in role_engine.ROLES:
        raise InvitationError("bad_role", f"Unknown role {role!r}.")

    existing = (
        await session.execute(
            text("""SELECT id, organization_id, status FROM plenum_cafm.users
                     WHERE lower(email) = :e"""),
            {"e": addr},
        )
    ).mappings().first()

    if existing and str(existing["status"]).lower() == "active":
        raise InvitationError(
            "already_active",
            "That address already has an active account. Edit the user instead.", 409,
        )
    if existing and existing["organization_id"] and existing["organization_id"] != organization_id:
        raise InvitationError(
            "other_company",
            "That address belongs to a different company.", 409,
        )

    token = secrets.token_urlsafe(32)
    expires = _now() + timedelta(days=INVITE_TTL_DAYS)

    if existing:
        user_id = existing["id"]
        await session.execute(
            text("""UPDATE plenum_cafm.users
                       SET full_name = COALESCE(:n, full_name), platform_role = :r,
                           can_ingest = :ci, job_title = COALESCE(:jt, job_title),
                           invited_at = now(), invited_by = :by, status = 'invited',
                           updated_at = now()
                     WHERE id = :i"""),
            {"n": full_name, "r": role, "ci": bool(can_ingest), "jt": job_title,
             "by": invited_by, "i": user_id},
        )
    else:
        # A placeholder password hash that can never verify: the account cannot be used
        # until the invitation is accepted and a real password set.
        placeholder = password_engine.hash_password(secrets.token_urlsafe(48))
        user_id = (
            await session.execute(
                text("""INSERT INTO plenum_cafm.users
                            (organization_id, full_name, email, password_hash, status,
                             platform_role, can_ingest, job_title, email_verified,
                             invited_at, invited_by, created_at, updated_at)
                        VALUES (:o, :n, :e, :ph, 'invited', :r, :ci, :jt, FALSE,
                                now(), :by, now(), now())
                        RETURNING id"""),
                {"o": organization_id, "n": full_name, "e": addr, "ph": placeholder,
                 "r": role, "ci": bool(can_ingest), "jt": job_title, "by": invited_by},
            )
        ).scalar()

    # Any earlier open invitation for this address is superseded.
    await session.execute(
        text("""UPDATE plenum_cafm.invitations SET expires_at = now()
                 WHERE organization_id = :o AND lower(email) = :e AND accepted_at IS NULL
                   AND expires_at > now()"""),
        {"o": organization_id, "e": addr},
    )
    invitation_id = (
        await session.execute(
            text("""INSERT INTO plenum_cafm.invitations
                        (organization_id, email, full_name, role, can_ingest, building_ids,
                         token_hash, invited_by, expires_at)
                    VALUES (:o, :e, :n, :r, :ci, CAST(:b AS uuid[]), :h, :by, :x)
                    RETURNING id"""),
            {"o": organization_id, "e": addr, "n": full_name, "r": role,
             "ci": bool(can_ingest), "b": [str(b) for b in building_ids],
             "h": _hash(token), "by": invited_by, "x": expires},
        )
    ).scalar()

    company = await _company_name(session, organization_id)
    url = _accept_url(token)
    subject, body, redacted = _invite_email(
        name=full_name or "", company=company, role=role, url=url,
        days=INVITE_TTL_DAYS, inviter=inviter_name,
    )
    sent = await send_platform_email(
        session, to_address=addr, subject=subject, body=body,
        organization_id=organization_id, commit=False, log_body=redacted,
    )
    await session.commit()
    log.info("invitations.created", organization_id=str(organization_id), role=role,
             user_id=str(user_id), sent=bool(sent.get("sent") or sent.get("ok")))
    out: dict[str, Any] = {
        "ok": True, "invitation_id": str(invitation_id), "user_id": str(user_id),
        "email": addr, "role": role, "can_ingest": bool(can_ingest),
        "building_ids": [str(b) for b in building_ids],
        "expires_at": expires.isoformat(), "email_sent": sent,
    }
    if settings.email_dry_run or not (sent.get("sent") or sent.get("ok")):
        # Nobody received a link, so the caller gets it — otherwise the invitation is
        # unreachable and the account is stuck in `invited` for ever.
        out["accept_url"] = url
        out["note"] = "Email was not delivered; share this link with the invitee directly."
    return out


async def accept(
    session: AsyncSession, *, token: str, password: str, full_name: str | None = None,
) -> dict[str, Any]:
    """Follow the link: set the password, activate the account, apply the allocation."""
    row = (
        await session.execute(
            text("""SELECT id, organization_id, email, role, can_ingest, building_ids,
                           expires_at, accepted_at
                      FROM plenum_cafm.invitations WHERE token_hash = :h"""),
            {"h": _hash((token or "").strip())},
        )
    ).mappings().first()
    if row is None:
        raise InvitationError("invalid", "That invitation link is not valid.", 404)
    if row["accepted_at"] is not None:
        raise InvitationError("used", "That invitation has already been accepted.", 409)
    expires = row["expires_at"]
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires <= _now():
        raise InvitationError("expired", "That invitation has expired. Ask to be re-invited.", 410)

    # validate() returns the normalised password and RAISES for a weak one. It was read as
    # "return the problem", so every strong password was refused with the password itself
    # as the message — nobody could accept an invitation, and the refusal echoed the secret.
    try:
        password = password_engine.validate(
            password, email=row["email"], full_name=full_name or "",
        )
    except password_engine.WeakPassword as exc:
        raise InvitationError("weak_password", str(exc)) from None

    user = (
        await session.execute(
            text("SELECT id FROM plenum_cafm.users WHERE lower(email) = :e"),
            {"e": str(row["email"]).lower()},
        )
    ).mappings().first()
    if user is None:
        raise InvitationError("no_account", "The invited account no longer exists.", 404)
    user_id = user["id"]

    await session.execute(
        text("""UPDATE plenum_cafm.users
                   SET password_hash = :ph, status = 'active', email_verified = TRUE,
                       email_verified_at = now(), activated_at = now(),
                       password_changed_at = now(), platform_role = :r, can_ingest = :ci,
                       full_name = COALESCE(:n, full_name), updated_at = now()
                 WHERE id = :i"""),
        {"ph": password_engine.hash_password(password), "r": row["role"],
         "ci": bool(row["can_ingest"]), "n": full_name, "i": user_id},
    )
    for bid in (row["building_ids"] or []):
        await session.execute(
            text("""INSERT INTO plenum_cafm.user_buildings (user_id, building_id, granted_by)
                    VALUES (:u, :b, NULL) ON CONFLICT DO NOTHING"""),
            {"u": user_id, "b": bid},
        )
    await session.execute(
        text("""UPDATE plenum_cafm.invitations
                   SET accepted_at = now(), accepted_user_id = :u WHERE id = :i"""),
        {"u": user_id, "i": row["id"]},
    )
    # A company whose administrator has just activated is no longer onboarding.
    if role_engine.at_least(str(row["role"]), role_engine.ADMIN) and row["organization_id"]:
        await session.execute(
            text("""UPDATE plenum_cafm.organizations SET lifecycle = 'active', updated_at = now()
                     WHERE id = :o AND lifecycle <> 'active'"""),
            {"o": row["organization_id"]},
        )
    await session.commit()
    log.info("invitations.accepted", user_id=str(user_id), role=row["role"])
    return {"ok": True, "user_id": str(user_id), "email": row["email"], "role": row["role"],
            "organization_id": str(row["organization_id"]) if row["organization_id"] else None}


async def pending_for(session: AsyncSession, organization_id: UUID) -> int:
    """Open invitations for one company — the "pending invites" tile."""
    n = (await session.execute(
        text("""SELECT count(*) FROM plenum_cafm.invitations
                 WHERE organization_id = :o AND accepted_at IS NULL AND expires_at > now()"""),
        {"o": organization_id},
    )).scalar()
    return int(n or 0)
