"""Registration, email verification, sign-in, and password reset.

One rule shapes most of what follows: **a stranger must not be able to learn who has an
account here.** A registration form that says "that email is already taken" and a reset
form that says "no account with that address" are, together, a free membership oracle —
point it at a list of addresses and it tells you which of your customers use this
platform, which is worth money to a competitor and worth more to whoever is writing the
phishing email. So:

  registering an address that already exists  → the same 202 as a new one, and an email
                                                to the owner saying someone tried
  asking to reset an address with no account   → the same 202, and no email
  signing in with a wrong password             → the same error as an unknown address,
                                                after the same amount of time

The last one needs the deliberate work in :func:`sign_in`: bcrypt takes ~250ms and a
missing row takes none, so without burning the same time the response latency answers the
question the error message refuses to.

The account table belongs to cafm-connector-service. It is read and written here with SQL
rather than a second ORM model, so there is one declaration of what a user is.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import settings
from ...core.logging import get_logger
from ...shared.approvals import send_platform_email
from . import otp as otp_engine
from . import roles as role_engine
from .otp import EMAIL_VERIFICATION, PASSWORD_RESET, normalise_email
from .passwords import WeakPassword, burn_time, hash_password, validate, verify_password
from . import tokens as token_engine

log = get_logger(__name__)

#: Said to anyone whose credentials did not work, whatever the actual reason.
GENERIC_SIGNIN_FAILURE = "That email address and password do not match an account."

#: Statuses that can still sign in or finish a reset. "invited" is an account an
#: operator created for someone else: it exists, it has no usable password, and the
#: person becomes able to sign in by setting one through the reset flow — which proves
#: they hold the mailbox at the same moment.
LIVE_STATUSES = frozenset({"active", "pending_verification", "invited"})

#: Said after every request for a reset code, whether or not an account exists.
GENERIC_RESET_ACCEPTED = (
    "If that address has an account, a reset code is on its way. It is valid for "
    "{ttl} minutes."
)


class AuthError(Exception):
    """A failure with a message that is safe to return to the caller."""

    def __init__(self, message: str, *, status: int = 400, reason: str = "invalid",
                 extra: dict[str, Any] | None = None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.reason = reason
        self.extra = extra or {}


@dataclass(frozen=True)
class Account:
    id: UUID
    email: str
    full_name: str
    organization_id: UUID | None
    status: str
    email_verified: bool
    password_changed_at: datetime | None
    failed_login_count: int
    locked_until: datetime | None
    role: str


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


_SELECT = """SELECT id, email, full_name, organization_id, status, email_verified,
                    password_hash, password_changed_at, failed_login_count, locked_until,
                    role, last_login_at
             FROM plenum_cafm.users"""


def _account(row: Any) -> Account:
    return Account(
        role=str(row["role"] or role_engine.DEFAULT_ROLE),
        id=row["id"], email=row["email"], full_name=row["full_name"],
        organization_id=row["organization_id"], status=str(row["status"] or "active"),
        email_verified=bool(row["email_verified"]),
        password_changed_at=_aware(row["password_changed_at"]),
        failed_login_count=int(row["failed_login_count"] or 0),
        locked_until=_aware(row["locked_until"]),
    )


async def find_by_email(session: AsyncSession, email: str, *, lock: bool = False):
    """The account for an address, matched case-insensitively, or None."""
    row = (
        await session.execute(
            text(f"{_SELECT} WHERE lower(email) = :e" + (" FOR UPDATE" if lock else "")),
            {"e": normalise_email(email)},
        )
    ).mappings().first()
    return row


# ── which organisation a new account joins ───────────────────────────────────────────


async def resolve_organization(session: AsyncSession, requested: str | None) -> UUID:
    """The organisation a registration belongs to, or an error saying it cannot be known.

    Never guessed. Putting a new account in the wrong tenant hands one customer's data to
    another, and nothing about it looks wrong afterwards — the account works, it is simply
    looking at the wrong portfolio.
    """
    candidate = (requested or "").strip() or (settings.auth_default_organization_id or "").strip()
    if candidate:
        try:
            org = UUID(candidate)
        except ValueError:
            raise AuthError("organization_id must be a UUID.", reason="organization_id") from None
        exists = (
            await session.execute(
                text("SELECT 1 FROM plenum_cafm.organizations WHERE id = :i"),
                {"i": str(org)},
            )
        ).first()
        if not exists:
            raise AuthError("No organisation with that id.", reason="organization_id")
        return org

    rows = (
        await session.execute(
            text("SELECT id FROM plenum_cafm.organizations ORDER BY created_at LIMIT 2")
        )
    ).scalars().all()
    if len(rows) == 1:
        return rows[0]
    if not rows:
        raise AuthError(
            "This deployment has no organisation to attach an account to. Create one "
            "first, or set AUTH_DEFAULT_ORGANIZATION_ID.",
            status=409, reason="no_organization",
        )
    raise AuthError(
        "This deployment has more than one organisation, so organization_id is required "
        "— an account created in the wrong one would look entirely normal.",
        status=400, reason="organization_id_required",
    )


# ── the emails ───────────────────────────────────────────────────────────────────────


def _code_email(code: str, minutes: int, *, purpose: str, name: str) -> tuple[str, str, str]:
    """Subject, body, and the redacted body that goes in the email log."""
    who = (name or "").split(" ")[0] or "there"
    if purpose == EMAIL_VERIFICATION:
        subject = "Confirm your Hoistra email address"
        lead = ("Someone — we hope you — created a Hoistra account with this address. "
                "Use this code to confirm it:")
    else:
        subject = "Reset your Hoistra password"
        lead = ("Someone asked to reset the Hoistra password for this address. "
                "Use this code to set a new one:")
    body = (
        f"Hello {who},\n\n{lead}\n\n"
        f"    {code}\n\n"
        f"The code is valid for {minutes} minutes and can be used once.\n\n"
        "If this was not you, ignore this message — nothing has changed, and whoever "
        "asked cannot proceed without the code.\n\n"
        "Hoistra"
    )
    # What the audit row keeps. The code is the one part that must not survive the ten
    # minutes it is alive for.
    redacted = body.replace(code, "[code redacted — not stored]")
    return subject, body, redacted


async def _send_code(
    session: AsyncSession, *, email: str, purpose: str, name: str,
    user_id: UUID | None, organization_id: UUID | None, request_ip: str | None,
) -> dict[str, Any]:
    """Issue a code and email it. Returns the rate-limit outcome, never the code."""
    issued = await otp_engine.issue(
        session, email=email, purpose=purpose, user_id=user_id, request_ip=request_ip,
    )
    if isinstance(issued, otp_engine.RateLimited):
        return {"sent": False, "rate_limited": True, "reason": issued.reason,
                "retry_after_seconds": issued.retry_after_seconds,
                "message": issued.message}

    subject, body, redacted = _code_email(
        issued.code, issued.ttl_minutes, purpose=purpose, name=name,
    )
    delivery = await send_platform_email(
        session, to_address=email, subject=subject, body=body,
        organization_id=organization_id, commit=False, log_body=redacted,
    )
    return {"sent": True, "rate_limited": False,
            "delivery_status": delivery.get("status"),
            "expires_at": issued.expires_at.isoformat(),
            "ttl_minutes": issued.ttl_minutes}


async def _warn_existing_account(session: AsyncSession, row: Any) -> None:
    """Tell the owner that someone tried to register their address.

    This is what replaces "that email is taken". The person who typed it learns nothing;
    the person who owns the mailbox learns something they would want to know.
    """
    await send_platform_email(
        session,
        to_address=row["email"],
        subject="Someone tried to create a Hoistra account with your address",
        body=(
            f"Hello,\n\nSomeone just tried to register a Hoistra account using "
            f"{row['email']}, which already has one.\n\n"
            "If that was you, sign in as usual — or use 'forgot password' if you cannot "
            "remember it. If it was not you, no action is needed: no account was created "
            "and nothing about yours has changed.\n\nHoistra"
        ),
        organization_id=row["organization_id"], commit=False,
    )


# ── register ─────────────────────────────────────────────────────────────────────────


async def register(
    session: AsyncSession, *, email: str, password: str, full_name: str,
    organization_id: str | None = None, phone: str | None = None,
    request_ip: str | None = None,
) -> dict[str, Any]:
    """Create an unverified account and send its confirmation code.

    Always reports the same thing. The two branches below — new address, existing address
    — take different actions and return identical payloads, because the difference between
    them is exactly what a stranger is trying to find out.
    """
    if not settings.auth_allow_self_registration:
        raise AuthError("Sign-up is closed on this deployment. Ask an administrator to "
                        "create your account.", status=403, reason="registration_closed")

    address = normalise_email(email)
    if not address or "@" not in address or address.startswith("@") or address.endswith("@"):
        raise AuthError("Enter a valid email address.", reason="email")
    if len(address) > 255:
        raise AuthError("That email address is too long.", reason="email")
    name = (full_name or "").strip()
    if len(name) < 2:
        raise AuthError("Enter your name.", reason="full_name")
    if len(name) > 255:
        raise AuthError("That name is too long.", reason="full_name")

    # Password rules are checked BEFORE the address is looked up, so a weak password is
    # rejected identically whether or not the address is already registered — otherwise
    # the order of the errors leaks the thing the rest of this function hides.
    try:
        password = validate(password, email=address, full_name=name)
    except WeakPassword as exc:
        raise AuthError(str(exc), reason="password") from None

    org = await resolve_organization(session, organization_id)
    ttl = int(settings.auth_otp_ttl_minutes)
    same_answer = {
        "ok": True,
        "status": "verification_sent",
        "email": address,
        "message": (
            f"Check {address} for a {int(settings.auth_otp_length)}-digit code and enter "
            f"it to finish setting up your account. The code lasts {ttl} minutes."
        ),
        "otp": otp_engine.describe_limits(),
    }

    existing = await find_by_email(session, address)
    if existing is not None:
        log.info("auth.register.address_in_use", email_domain=address.split("@")[-1])
        await _warn_existing_account(session, existing)
        await session.commit()
        return same_answer

    # Self-registration NEVER grants a role. The one exception is the bootstrap: a fresh
    # platform has no superadmin and only a superadmin can appoint one, so the first
    # account registered with the configured address becomes one — and only while there
    # is still no superadmin at all, so the setting grants nothing once one exists. The
    # address is confirmed by email like any other, so this does not hand the platform to
    # whoever types it first; they have to hold the mailbox.
    role = role_engine.DEFAULT_ROLE
    bootstrap = (settings.auth_bootstrap_superadmin_email or "").strip().lower()
    if bootstrap and bootstrap == address and not await role_engine.superadmin_exists(session):
        role = role_engine.SUPERADMIN
        log.warning("auth.bootstrap_superadmin", email_domain=address.split("@")[-1])

    # The id is generated here rather than left to the column. plenum_cafm.users is
    # declared in two places: this service's migration gives id a DEFAULT of
    # gen_random_uuid(), and cafm-connector-service's ORM declares it with a PYTHON-side
    # default — so SQLAlchemy's create_all emits the column with no DEFAULT at all.
    # Whichever ran first decides, and on every database where the ORM won, this INSERT
    # violated NOT NULL and registration was impossible. Supplying the value works on
    # both shapes and depends on neither.
    new_id = uuid4()
    try:
        user_id = (
            await session.execute(
                text(
                    """INSERT INTO plenum_cafm.users
                           (id, organization_id, full_name, email, password_hash, phone,
                            status, email_verified, password_changed_at, role)
                       VALUES (:i, :o, :n, :e, :h, :p, 'pending_verification', false,
                               now(), :r)
                       RETURNING id"""
                ),
                {"i": str(new_id), "o": str(org), "n": name, "e": address,
                 "h": hash_password(password), "p": (phone or "").strip() or None,
                 "r": role},
            )
        ).scalar_one()
    except IntegrityError as exc:
        await session.rollback()
        # This branch exists for ONE case: two registrations for the same address at
        # once, where the loser should report what the winner would have. It used to
        # catch every IntegrityError and report success for all of them — so a NOT NULL
        # violation on id returned 202 "check your email" for an account that was never
        # created, and nothing anywhere said otherwise.
        #
        # So the race is now proved rather than assumed: if the address really does have
        # an account, somebody else made it and the generic answer is correct. If it does
        # not, the insert failed for another reason and must not be reported as success.
        if await find_by_email(session, address) is not None:
            log.info("auth.register.race", email_domain=address.split("@")[-1])
            return same_answer
        log.error("auth.register.insert_failed", error=str(exc.orig)[:200]
                  if getattr(exc, "orig", None) else str(exc)[:200])
        raise AuthError(
            "The account could not be created. This is a fault on our side, not "
            "something you did — please tell an administrator.",
            status=500, reason="insert_failed",
        ) from None

    if role != role_engine.DEFAULT_ROLE:
        await role_engine.record_change(
            session, user_id=user_id, from_role=None, to_role=role,
            changed_by=None, reason="bootstrap: first account at the configured address",
            request_ip=request_ip,
        )
    await _send_code(
        session, email=address, purpose=EMAIL_VERIFICATION, name=name,
        user_id=user_id, organization_id=org, request_ip=request_ip,
    )
    await session.commit()
    log.info("auth.register.created", user_id=str(user_id), role=role)
    return same_answer


# ── confirm the address ──────────────────────────────────────────────────────────────


async def verify_email(
    session: AsyncSession, *, email: str, code: str,
    user_agent: str | None = None, request_ip: str | None = None,
) -> dict[str, Any]:
    """Confirm an address with its code and sign the person in."""
    address = normalise_email(email)
    result = await otp_engine.verify(
        session, email=address, purpose=EMAIL_VERIFICATION, code=code,
    )
    if not result.ok:
        # Committed even though the request failed: the attempt counter lives on that row,
        # and a rollback here would roll the count back too, making the limit unenforceable
        # by anyone willing to keep failing.
        await session.commit()
        raise AuthError(result.message, status=400, reason=result.reason,
                        extra={"attempts_remaining": result.attempts_remaining})

    row = await find_by_email(session, address, lock=True)
    if row is None:
        await session.commit()
        raise AuthError("That account no longer exists.", status=404, reason="no_account")

    await session.execute(
        text(
            """UPDATE plenum_cafm.users
               SET email_verified = true,
                   email_verified_at = COALESCE(email_verified_at, now()),
                   status = CASE WHEN status = 'pending_verification' THEN 'active'
                                 ELSE status END,
                   updated_at = now()
               WHERE id = :i"""
        ),
        {"i": str(row["id"])},
    )

    fresh = await find_by_email(session, address)
    pair = await token_engine.open_session(
        session, user_id=fresh["id"], email=fresh["email"],
        organization_id=fresh["organization_id"],
        password_changed_at=_aware(fresh["password_changed_at"]),
        role=str(fresh["role"] or role_engine.DEFAULT_ROLE),
        user_agent=user_agent, request_ip=request_ip,
    )
    await session.commit()
    log.info("auth.email_verified", user_id=str(row["id"]))
    return {
        "ok": True,
        "message": "Your email address is confirmed and you are signed in.",
        "user": public_user(fresh),
        "tokens": _token_payload(pair),
    }


async def resend_code(
    session: AsyncSession, *, email: str, purpose: str = EMAIL_VERIFICATION,
    request_ip: str | None = None,
) -> dict[str, Any]:
    """Send another code. Says the same thing whether or not there is an account."""
    address = normalise_email(email)
    ttl = int(settings.auth_otp_ttl_minutes)
    same_answer = {
        "ok": True,
        "status": "sent",
        # Echoed back because the caller just typed it — it says nothing about whether an
        # account exists, and a client that has to re-read its own form state to render
        # "we sent a code to X" is being made to work for a field the response declares.
        "email": address,
        "message": f"If that address needs a code, one is on its way. It lasts {ttl} minutes.",
        "otp": otp_engine.describe_limits(),
    }

    row = await find_by_email(session, address)
    if row is None:
        # Rate limiting still runs, so this path cannot be used as a free oracle by
        # timing how long the "no such user" branch takes versus the real one.
        await otp_engine.check_rate_limit(session, address, purpose)
        await session.commit()
        return same_answer

    if purpose == EMAIL_VERIFICATION and bool(row["email_verified"]):
        await session.commit()
        return same_answer

    outcome = await _send_code(
        session, email=address, purpose=purpose, name=row["full_name"],
        user_id=row["id"], organization_id=row["organization_id"], request_ip=request_ip,
    )
    await session.commit()
    if outcome.get("rate_limited"):
        raise AuthError(outcome["message"], status=429, reason=outcome["reason"],
                        extra={"retry_after_seconds": outcome["retry_after_seconds"]})
    return same_answer


# ── sign in ──────────────────────────────────────────────────────────────────────────


async def sign_in(
    session: AsyncSession, *, email: str, password: str,
    user_agent: str | None = None, request_ip: str | None = None,
) -> dict[str, Any]:
    """Exchange an email and password for a session."""
    address = normalise_email(email)
    row = await find_by_email(session, address, lock=True)

    if row is None:
        # Same cost as a real check. Without it, a missing account answers in ~1ms and a
        # wrong password in ~250ms, and the generic message above is decoration.
        burn_time()
        await session.commit()
        raise AuthError(GENERIC_SIGNIN_FAILURE, status=401, reason="invalid_credentials")

    locked_until = _aware(row["locked_until"])
    if locked_until and locked_until > _now():
        wait = int((locked_until - _now()).total_seconds())
        await session.commit()
        minutes = max(1, -(-wait // 60))   # rounded UP: 899s is 15 minutes, not 14
        raise AuthError(
            f"Too many failed attempts. Try again in {minutes} minute"
            f"{'s' if minutes != 1 else ''}, or reset your password.",
            status=429, reason="locked", extra={"retry_after_seconds": wait},
        )

    if not verify_password(password, row["password_hash"]):
        failures = int(row["failed_login_count"] or 0) + 1
        cap = int(settings.auth_login_max_failures)
        lock_for = int(settings.auth_lockout_minutes)
        await session.execute(
            text(
                # Every parameter is cast. Without the casts Postgres sees :f used both as
                # an INT column assignment and as the left side of a comparison and refuses
                # the statement outright with "inconsistent types deduced for parameter $1"
                # — which surfaced as a 500 on the wrong-password path, i.e. exactly the
                # path an attacker exercises and a normal test run never reaches.
                """UPDATE plenum_cafm.users
                   SET failed_login_count = CAST(:f AS INT),
                       locked_until = CASE WHEN CAST(:f AS INT) >= CAST(:cap AS INT)
                                           THEN CAST(:until AS TIMESTAMPTZ)
                                           ELSE locked_until END,
                       updated_at = now()
                   WHERE id = :i"""
            ),
            {"f": failures, "cap": cap,
             "until": _now() + timedelta(minutes=lock_for), "i": str(row["id"])},
        )
        await session.commit()
        log.info("auth.signin.failed", user_id=str(row["id"]), failures=failures,
                 locked=failures >= cap)
        raise AuthError(GENERIC_SIGNIN_FAILURE, status=401, reason="invalid_credentials")

    if str(row["status"]).lower() not in LIVE_STATUSES:
        await session.commit()
        # Distinct from a wrong password on purpose: the password was right, so this
        # reveals nothing the caller did not already know, and "your account is suspended"
        # is the only message that lets them do the right thing next.
        raise AuthError("That account has been disabled. Contact an administrator.",
                        status=403, reason="disabled")

    if not bool(row["email_verified"]):
        # The password was correct, so sending a fresh code here leaks nothing.
        await _send_code(
            session, email=address, purpose=EMAIL_VERIFICATION, name=row["full_name"],
            user_id=row["id"], organization_id=row["organization_id"],
            request_ip=request_ip,
        )
        await session.commit()
        raise AuthError(
            "Confirm your email address first. We have sent a new code to "
            f"{address}.",
            status=403, reason="email_not_verified", extra={"email": address},
        )

    await session.execute(
        text(
            """UPDATE plenum_cafm.users
               SET last_login_at = now(), failed_login_count = 0, locked_until = NULL,
                   updated_at = now()
               WHERE id = :i"""
        ),
        {"i": str(row["id"])},
    )
    pair = await token_engine.open_session(
        session, user_id=row["id"], email=row["email"],
        organization_id=row["organization_id"],
        password_changed_at=_aware(row["password_changed_at"]),
        role=str(row["role"] or role_engine.DEFAULT_ROLE),
        user_agent=user_agent, request_ip=request_ip,
    )
    await session.commit()
    log.info("auth.signin.ok", user_id=str(row["id"]))
    return {"ok": True, "user": public_user(row), "tokens": _token_payload(pair)}


# ── forgot / reset ───────────────────────────────────────────────────────────────────


async def forgot_password(
    session: AsyncSession, *, email: str, request_ip: str | None = None,
) -> dict[str, Any]:
    """Send a reset code, if there is anywhere to send it.

    Returns the same 202 either way. A reset form that says "no account with that
    address" is a membership check anyone can run against any list of addresses.
    """
    address = normalise_email(email)
    ttl = int(settings.auth_otp_ttl_minutes)
    same_answer = {
        "ok": True,
        "status": "accepted",
        "email": address,
        "message": GENERIC_RESET_ACCEPTED.format(ttl=ttl),
        "otp": otp_engine.describe_limits(),
    }

    row = await find_by_email(session, address)
    if row is None:
        await otp_engine.check_rate_limit(session, address, PASSWORD_RESET)
        await session.commit()
        log.info("auth.forgot.unknown_address", email_domain=address.split("@")[-1])
        return same_answer

    if str(row["status"]).lower() not in LIVE_STATUSES:
        await session.commit()
        return same_answer

    outcome = await _send_code(
        session, email=address, purpose=PASSWORD_RESET, name=row["full_name"],
        user_id=row["id"], organization_id=row["organization_id"], request_ip=request_ip,
    )
    await session.commit()
    if outcome.get("rate_limited"):
        # The one place the generic answer gives way: a caller being throttled has to be
        # told to wait, and the throttle applies to addresses with no account too, so it
        # still says nothing about who is registered.
        raise AuthError(outcome["message"], status=429, reason=outcome["reason"],
                        extra={"retry_after_seconds": outcome["retry_after_seconds"]})
    return same_answer


async def reset_password(
    session: AsyncSession, *, email: str, code: str, new_password: str,
    request_ip: str | None = None,
) -> dict[str, Any]:
    """Set a new password with a reset code, in one call.

    One call, not two, and deliberately: a "check this code is valid" step would either
    consume the code (leaving nothing to reset with) or leave it live after proving it is
    correct — handing anyone who guesses it a verified code they can spend at leisure.
    """
    address = normalise_email(email)

    row = await find_by_email(session, address)
    # The password is checked against the account's own name and address, so those are
    # needed before the code is spent. A weak new password must not consume the code —
    # otherwise a typo costs the person their only way in.
    try:
        new_password = validate(
            new_password,
            email=address,
            full_name=(row["full_name"] if row is not None else ""),
        )
    except WeakPassword as exc:
        raise AuthError(str(exc), reason="password") from None

    # Verified but NOT consumed. Two checks still stand between here and the write, and
    # a code spent on either of them is gone for good: the person is told to choose a
    # different password and has nothing left to choose it with.
    #
    # The obvious alternative — run those checks first — is worse. "Is this the account's
    # current password" answered before any code is presented is a password oracle on an
    # unauthenticated endpoint: submit a guess, read the error, learn whether it was
    # right. So the order stays, and the code is spent at the last possible moment
    # instead.
    result = await otp_engine.verify(
        session, email=address, purpose=PASSWORD_RESET, code=code, consume=False,
    )
    if not result.ok:
        await session.commit()          # keep the attempt count — see verify_email
        raise AuthError(result.message, status=400, reason=result.reason,
                        extra={"attempts_remaining": result.attempts_remaining})

    if row is None:
        # A verified code for an address with no account: only reachable if the account
        # was deleted between the code being sent and used.
        await session.commit()
        raise AuthError("That account no longer exists.", status=404, reason="no_account")

    if verify_password(new_password, row["password_hash"]):
        await session.commit()
        raise AuthError(
            "That is the password the account already has. If you are resetting it "
            "because someone else may know it, choose a different one. Your code is "
            "still valid.",
            reason="password_unchanged",
        )

    # Committed to the write now, so the code is spent.
    await otp_engine.consume(session, result.otp_id)

    await session.execute(
        text(
            """UPDATE plenum_cafm.users
               SET password_hash = :h, password_changed_at = now(),
                   failed_login_count = 0, locked_until = NULL,
                   email_verified = true,
                   email_verified_at = COALESCE(email_verified_at, now()),
                   -- 'invited' too: an operator created this account and the
                   -- person is now setting their own password, which is the
                   -- moment it becomes theirs. Leaving it invited would mean an
                   -- account that works and still reads as an outstanding
                   -- invitation on every list of them.
                   status = CASE WHEN status IN ('pending_verification', 'invited')
                                 THEN 'active' ELSE status END,
                   updated_at = now()
               WHERE id = :i"""
        ),
        {"h": hash_password(new_password), "i": str(row["id"])},
    )

    # Everything the old password could reach, ended. A reset that leaves the intruder's
    # session running has locked the owner out of their own account and nobody else.
    revoked = await token_engine.revoke_all_sessions(
        session, user_id=row["id"], reason="password_reset",
    )
    await otp_engine.invalidate_outstanding(
        session, address, PASSWORD_RESET, reason="reset_completed",
    )
    await otp_engine.invalidate_outstanding(
        session, address, EMAIL_VERIFICATION, reason="reset_completed",
    )

    await send_platform_email(
        session,
        to_address=row["email"],
        subject="Your Hoistra password was changed",
        body=(
            "Hello,\n\nThe password on your Hoistra account was just changed, and every "
            "signed-in session was ended.\n\n"
            "If this was you, there is nothing to do. If it was not, reset your password "
            "immediately and tell your administrator — whoever did this had access to "
            "this mailbox.\n\nHoistra"
        ),
        organization_id=row["organization_id"], commit=False,
    )
    await session.commit()
    log.info("auth.password_reset", user_id=str(row["id"]), sessions_revoked=revoked)
    return {
        "ok": True,
        "message": "Your password is set. Sign in with it — every other session has been "
                   "signed out.",
        "sessions_ended": revoked,
    }


async def change_password(
    session: AsyncSession, *, user_id: UUID, current_password: str, new_password: str,
    keep_this_session: str | None = None,
) -> dict[str, Any]:
    """Change a password from inside a signed-in session, using the current one."""
    row = (
        await session.execute(
            text(f"{_SELECT} WHERE id = :i FOR UPDATE"), {"i": str(user_id)},
        )
    ).mappings().first()
    if row is None:
        raise AuthError("That account no longer exists.", status=404, reason="no_account")

    if not verify_password(current_password, row["password_hash"]):
        # Not the generic sign-in message: the caller is already authenticated, so there
        # is nothing left to hide from them, and "your current password is wrong" is the
        # only message they can act on.
        raise AuthError("Your current password is not correct.", status=401,
                        reason="invalid_credentials")
    try:
        new_password = validate(new_password, email=row["email"],
                                full_name=row["full_name"])
    except WeakPassword as exc:
        raise AuthError(str(exc), reason="password") from None
    if verify_password(new_password, row["password_hash"]):
        raise AuthError("That is the password you already have.",
                        reason="password_unchanged")

    await session.execute(
        text(
            """UPDATE plenum_cafm.users
               SET password_hash = :h, password_changed_at = now(), updated_at = now()
               WHERE id = :i"""
        ),
        {"h": hash_password(new_password), "i": str(user_id)},
    )
    revoked = await token_engine.revoke_all_sessions(
        session, user_id=user_id, reason="password_changed",
    )
    await send_platform_email(
        session, to_address=row["email"],
        subject="Your Hoistra password was changed",
        body=("Hello,\n\nYour Hoistra password was changed from a signed-in session, and "
              "all other sessions were ended.\n\nIf this was not you, reset your password "
              "immediately.\n\nHoistra"),
        organization_id=row["organization_id"], commit=False,
    )
    await session.commit()
    log.info("auth.password_changed", user_id=str(user_id), sessions_revoked=revoked)
    return {
        "ok": True,
        # The old access token stops working the moment password_changed_at moves, so the
        # caller needs a new pair even though they never signed out.
        "message": "Your password is changed. Sign in again with the new one.",
        "sessions_ended": revoked,
    }


# ── shaping what leaves the service ──────────────────────────────────────────────────


def public_user(row: Any) -> dict[str, Any]:
    """The fields of an account that may leave this service.

    Built by naming what goes out rather than by removing what must not. A denylist is
    one added column away from returning password_hash to the browser.
    """
    return {
        "id": str(row["id"]),
        "email": row["email"],
        "full_name": row["full_name"],
        "organization_id": str(row["organization_id"]) if row["organization_id"] else None,
        "status": row["status"],
        "email_verified": bool(row["email_verified"]),
        "role": str(row["role"] or role_engine.DEFAULT_ROLE),
        "role_label": role_engine.LABELS.get(
            str(row["role"] or role_engine.DEFAULT_ROLE), ""
        ),
        # The PREVIOUS sign-in, not this one — read before last_login_at is stamped, so a
        # client can say "last seen Tuesday" on the screen it draws straight after. Null
        # on a first sign-in, which is the truth rather than a placeholder.
        #
        # It is filled here because PublicUser declares the field: a response model that
        # names a key its handler never sets returns null for ever, and a client binds to
        # it and shows nothing with no way to tell that from an account never used.
        "last_login_at": (
            row["last_login_at"].isoformat()
            if "last_login_at" in row.keys() and row["last_login_at"] else None
        ),
    }


def _token_payload(pair: token_engine.TokenPair) -> dict[str, Any]:
    return {
        "access_token": pair.access_token,
        "refresh_token": pair.refresh_token,
        "token_type": pair.token_type,
        "expires_in": pair.expires_in,
    }


# ── roles ────────────────────────────────────────────────────────────────────────────


async def set_role(
    session: AsyncSession, *, actor: Any, target_user_id: UUID, new_role: str,
    reason: str | None = None, request_ip: str | None = None,
) -> dict[str, Any]:
    """Change an account's platform role.

    ``actor`` is the signed-in caller. The rules live in ``roles.may_assign`` and are pure
    — every branch of "who may grant what" is testable without a database, which matters
    for the code that decides who gets the run of the platform.
    """
    wanted = role_engine.normalise(new_role)
    if wanted is None:
        raise AuthError(
            f"Unknown role. Choose one of: {', '.join(sorted(role_engine.ROLES))}.",
            reason="unknown_role",
        )

    row = (
        await session.execute(
            text(f"{_SELECT} WHERE id = :i FOR UPDATE"), {"i": str(target_user_id)},
        )
    ).mappings().first()
    if row is None:
        raise AuthError("No account with that id.", status=404, reason="no_account")

    current = str(row["role"] or role_engine.DEFAULT_ROLE)
    decision = role_engine.may_assign(
        actor_role=actor.role, actor_id=actor.user_id, actor_org=actor.organization_id,
        target_id=row["id"], target_role=current, target_org=row["organization_id"],
        new_role=wanted,
    )
    if not decision.allowed:
        log.info("auth.role_change_refused", reason=decision.reason,
                 actor=str(actor.user_id), target=str(row["id"]))
        raise AuthError(decision.message, status=403, reason=decision.reason)

    if current == wanted:
        return {"ok": True, "changed": False,
                "message": f"{row['email']} is already {wanted}.",
                "user": public_user(row)}

    # The last superadmin cannot be demoted. Otherwise a platform can arrive at a state
    # where nobody is able to appoint anyone — recoverable only by an UPDATE against the
    # production database, which is exactly the operation this whole feature exists so
    # that nobody has to perform.
    if current == role_engine.SUPERADMIN and wanted != role_engine.SUPERADMIN:
        remaining = (
            await session.execute(
                text("SELECT count(*) FROM plenum_cafm.users WHERE role = :r AND id <> :i"),
                {"r": role_engine.SUPERADMIN, "i": str(row["id"])},
            )
        ).scalar_one()
        if int(remaining or 0) == 0:
            raise AuthError(
                "That is the only superadmin on the platform. Appoint another one first "
                "— otherwise nobody is left who can appoint anybody.",
                status=409, reason="last_superadmin",
            )

    await session.execute(
        text("UPDATE plenum_cafm.users SET role = :r, updated_at = now() WHERE id = :i"),
        {"r": wanted, "i": str(row["id"])},
    )
    await role_engine.record_change(
        session, user_id=row["id"], from_role=current, to_role=wanted,
        changed_by=actor.user_id, reason=reason, request_ip=request_ip,
    )

    # Their existing tokens still CLAIM the old role. That claim is not what any decision
    # reads — principal_from_token reads the row — but ending the sessions makes a
    # reduction take effect visibly rather than whenever a token happens to expire, which
    # is what somebody revoking access in a hurry believes has happened.
    revoked = 0
    if role_engine.rank(wanted) < role_engine.rank(current):
        revoked = await token_engine.revoke_all_sessions(
            session, user_id=row["id"], reason="role_reduced",
        )

    await send_platform_email(
        session, to_address=row["email"],
        subject="Your Hoistra access level changed",
        body=(
            "Hello,\n\nYour access on Hoistra was changed from "
            f"{role_engine.LABELS.get(current, current)} to "
            f"{role_engine.LABELS.get(wanted, wanted)}.\n\n"
            "If you were not expecting this, tell your administrator.\n\nHoistra"
        ),
        organization_id=row["organization_id"], commit=False,
    )
    await session.commit()
    fresh = await find_by_email(session, row["email"])
    return {
        "ok": True, "changed": True,
        "message": f"{row['email']} is now {wanted}."
                   + (f" {revoked} session(s) ended." if revoked else ""),
        "user": public_user(fresh),
        "sessions_ended": revoked,
    }


async def list_accounts(
    session: AsyncSession, *, actor: Any, limit: int = 100, offset: int = 0,
) -> dict[str, Any]:
    """The accounts this caller may see: their own organisation, or all of them.

    An admin sees their organisation; a superadmin sees the platform. The scope is in the
    WHERE clause rather than applied to the results afterwards, so a paging mistake cannot
    leak a row from somewhere else.
    """
    if not role_engine.is_admin(actor.role):
        raise AuthError("Only an administrator can list accounts.", status=403,
                        reason="forbidden")

    scoped = not role_engine.at_least(actor.role, role_engine.SUPERADMIN)
    where = "WHERE organization_id = :o" if scoped else ""
    params: dict[str, Any] = {"lim": max(1, min(int(limit), 500)), "off": max(0, int(offset))}
    if scoped:
        params["o"] = str(actor.organization_id) if actor.organization_id else None

    rows = (
        await session.execute(
            text(
                f"""SELECT id, email, full_name, organization_id, status, email_verified,
                           role, last_login_at, created_at
                    FROM plenum_cafm.users {where}
                    ORDER BY created_at DESC LIMIT :lim OFFSET :off"""
            ),
            params,
        )
    ).mappings().all()
    total = (
        await session.execute(
            text(f"SELECT count(*) FROM plenum_cafm.users {where}"),
            {k: v for k, v in params.items() if k == "o"},
        )
    ).scalar_one()

    return {
        "ok": True,
        "scope": "organisation" if scoped else "platform",
        "total": int(total or 0),
        # public_user already reads last_login_at from the row, so it is not layered on
        # a second time here — two sources for one field is how they end up disagreeing.
        "users": [public_user(r) for r in rows],
    }
