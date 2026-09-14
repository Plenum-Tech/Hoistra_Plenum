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
from ...shared.email_template import wrap_html
from . import accounts as account_engine
from . import otp as otp_engine
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
                  inviter: str | None) -> tuple[str, str, str, str]:
    """Subject, plain-text body, HTML body, and the redacted body for the email log."""
    who = (name or "").split(" ")[0] or "there"
    what = ("administer" if role_engine.at_least(role, role_engine.ADMIN)
            else "use")
    by = f" {inviter} has" if inviter else " You have been"
    lead = f"{by.strip()} invited you to {what} {company} on Hoistra."
    # The account is not fully set up on this link alone: setting a password here starts
    # it, and a short confirmation code — sent right after — finishes it. Said up front so
    # the code that follows a minute later reads as the next step, not a second, unrelated
    # email arriving out of nowhere.
    next_step = ("After you set a password, we'll send a short code to this address to "
                "confirm it — enter it and you're signed in.")
    validity = (f"The link is valid for {days} days and can be used once. If you were not "
               "expecting this, you can ignore it.")
    subject = f"You're invited to {company} on Hoistra"
    body = (
        f"Hello {who},\n\n{lead}\n\n"
        f"Accept the invitation and set your password here:\n{url}\n\n"
        f"{next_step}\n\n{validity}\n"
    )
    html = wrap_html(
        heading=subject,
        lines=[f"Hello {who},", lead, next_step],
        cta_label="Accept invitation", cta_url=url,
        footnote=validity,
    )
    redacted = body.replace(url, "<invitation link redacted>")
    return subject, body, html, redacted


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
    subject, body, html, redacted = _invite_email(
        name=full_name or "", company=company, role=role, url=url,
        days=INVITE_TTL_DAYS, inviter=inviter_name,
    )
    # The reader of this email is a mail client, not the app: a relative link in it is
    # dead on arrival. With no PUBLIC_APP_URL the message is not sent at all — the caller
    # gets the link (and the reason) instead of the invitee getting one that cannot work.
    # In dry-run nothing leaves anyway, so the relative form is harmless there.
    no_base = not (getattr(settings, "public_app_url", None) or "").strip()
    if no_base and not settings.email_dry_run:
        sent: dict[str, Any] = {
            "ok": False, "status": "not_sent",
            "error": "PUBLIC_APP_URL is not set — refusing to email a link that would be relative.",
        }
        log.warning("invitations.email_skipped_no_public_app_url", user_id=str(user_id))
    else:
        sent = await send_platform_email(
            session, to_address=addr, subject=subject, body=body, html_body=html,
            organization_id=organization_id, commit=False, log_body=redacted,
        )
    await session.commit()
    # send_platform_email() returns ok=True for a dry-run AND for "no transport configured"
    # — both of which mean nobody received anything. Only status == "sent" is a delivery;
    # anything else must hand the link back or the invitation is unreachable and the
    # account sits in `invited` for ever, while the UI reports it as sent.
    delivered = sent.get("status") == "sent"
    log.info("invitations.created", organization_id=str(organization_id), role=role,
             user_id=str(user_id), delivered=delivered, delivery_status=sent.get("status"))
    out: dict[str, Any] = {
        "ok": True, "invitation_id": str(invitation_id), "user_id": str(user_id),
        "email": addr, "role": role, "can_ingest": bool(can_ingest),
        "building_ids": [str(b) for b in building_ids],
        "expires_at": expires.isoformat(), "email_sent": sent, "delivered": delivered,
    }
    if not delivered:
        out["accept_url"] = url
        out["note"] = (
            sent.get("error") if sent.get("status") == "not_sent"
            else "Email was not delivered; share this link with the invitee directly."
        )
    return out


async def accept(
    session: AsyncSession, *, token: str, password: str, full_name: str | None = None,
    request_ip: str | None = None,
) -> dict[str, Any]:
    """Follow the link: set the password, apply the allocation, and leave the account
    ``pending_verification`` with a confirmation code already on its way — the same OTP
    mechanism register() uses, not a second one. The invited person types the code next
    (POST /verify-email) and that call signs them in; nobody re-enters the password they
    just set. An account that never comes back to verify is not stuck, either: sign_in()
    already resends a fresh code and asks for it the moment such an account tries to log in.
    """
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
                   SET password_hash = :ph, status = 'pending_verification', email_verified = FALSE,
                       activated_at = now(),
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
    # Issued and sent BEFORE the commit below, so the code and the account it belongs to
    # land together — a crash between the two would otherwise leave an account that can
    # never be verified against a code nobody has.
    code_outcome = await account_engine._send_code(
        session, email=str(row["email"]), purpose=otp_engine.EMAIL_VERIFICATION,
        name=full_name or "", user_id=user_id, organization_id=row["organization_id"],
        request_ip=request_ip,
    )
    await session.commit()
    log.info("invitations.accepted", user_id=str(user_id), role=row["role"],
             code_sent=bool(code_outcome.get("sent")))
    return {"ok": True, "user_id": str(user_id), "email": row["email"], "role": row["role"],
            "verification_required": True, "otp": otp_engine.describe_limits(),
            "organization_id": str(row["organization_id"]) if row["organization_id"] else None}


async def pending_for(session: AsyncSession, organization_id: UUID) -> int:
    """Open invitations for one company — the "pending invites" tile."""
    n = (await session.execute(
        text("""SELECT count(*) FROM plenum_cafm.invitations
                 WHERE organization_id = :o AND accepted_at IS NULL AND expires_at > now()"""),
        {"o": organization_id},
    )).scalar()
    return int(n or 0)
