"""One-time codes: issuing them, and the four separate ways one can fail to verify.

The code is never stored. What is stored is ``HMAC-SHA256(pepper, salt || code)`` — a
per-row salt so two accounts that draw 418 902 on the same day do not share a digest, and
a server-side pepper so a stolen copy of the table cannot be ground against six digits
offline. Six digits is a million possibilities: an afternoon on a laptop if you have the
hashes and the key, and nothing at all if you have the hashes alone.

Three limits, because a one-time code has three distinct attack surfaces:

  guessing   ``max_attempts`` per code, counted on the row, not per request
  flooding   a cooldown between sends, so nobody's inbox becomes a weapon
  churning   a ceiling per hour, because every resend is a fresh draw at the code space

And one rule that is easy to miss: issuing a new code kills the previous one. Otherwise
five "resend" clicks leave five live codes and the guessing budget is five times what it
says on the tin.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import settings
from ...core.logging import get_logger
from .secrets_store import otp_pepper

log = get_logger(__name__)

#: The two things a code can be for. Anything else is a programming error, not input.
EMAIL_VERIFICATION = "email_verification"
PASSWORD_RESET = "password_reset"
PURPOSES = frozenset({EMAIL_VERIFICATION, PASSWORD_RESET})


def normalise_email(email: str) -> str:
    """Lowercased and trimmed. Every lookup in this package goes through here.

    The local part of an address is case-sensitive by RFC 5321 and case-insensitive at
    every mail provider anyone actually uses. Treating Bala@x.com and bala@x.com as two
    accounts would mean two people each certain they own one identity.
    """
    return (email or "").strip().lower()


@dataclass(frozen=True)
class IssuedCode:
    """A freshly issued code. ``code`` is in memory only and is never persisted."""
    code: str
    otp_id: UUID
    expires_at: datetime
    ttl_minutes: int


@dataclass(frozen=True)
class RateLimited:
    """Refused before a code was drawn."""
    reason: str          # cooldown | hourly_cap
    retry_after_seconds: int
    message: str


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    reason: str          # verified | no_code | expired | too_many_attempts | mismatch
    message: str
    otp_id: UUID | None = None
    user_id: UUID | None = None
    attempts_remaining: int | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _digest(code: str, salt: str) -> str:
    return hmac.new(
        otp_pepper().encode("utf-8"),
        (salt + code).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def generate_code() -> str:
    """A zero-padded decimal code drawn from the system CSPRNG.

    ``secrets``, not ``random``: Mersenne Twister output is reconstructible from a handful
    of prior draws, and the prior draws here are emailed to whoever asks for them.
    """
    length = max(4, min(int(settings.auth_otp_length), 10))
    return f"{secrets.randbelow(10 ** length):0{length}d}"


async def _recent_sends(session: AsyncSession, email: str, purpose: str) -> list[datetime]:
    rows = (
        await session.execute(
            text(
                """SELECT created_at FROM plenum_cafm.auth_otp_codes
                   WHERE email = :e AND purpose = :p AND created_at > :since
                   ORDER BY created_at DESC"""
            ),
            {"e": email, "p": purpose, "since": _now() - timedelta(hours=1)},
        )
    ).scalars().all()
    return [r if r.tzinfo else r.replace(tzinfo=timezone.utc) for r in rows]


async def check_rate_limit(
    session: AsyncSession, email: str, purpose: str
) -> RateLimited | None:
    """Whether this address may be sent another code right now."""
    sends = await _recent_sends(session, email, purpose)
    if not sends:
        return None

    cooldown = int(settings.auth_otp_resend_cooldown_seconds)
    elapsed = (_now() - sends[0]).total_seconds()
    if elapsed < cooldown:
        wait = int(cooldown - elapsed) + 1
        return RateLimited(
            reason="cooldown",
            retry_after_seconds=wait,
            message=f"A code was just sent. Ask for another in {wait} seconds.",
        )

    cap = int(settings.auth_otp_max_per_hour)
    if len(sends) >= cap:
        oldest = sends[-1]
        wait = max(1, int((oldest + timedelta(hours=1) - _now()).total_seconds()))
        return RateLimited(
            reason="hourly_cap",
            retry_after_seconds=wait,
            # Deliberately does not say how many were sent or to whom — this endpoint is
            # reachable without an account, and the count is itself information.
            message="Too many codes requested for this address. Try again later.",
        )
    return None


async def invalidate_outstanding(
    session: AsyncSession, email: str, purpose: str, *, reason: str = "superseded"
) -> int:
    """Kill every live code for this address and purpose. Returns how many."""
    result = await session.execute(
        text(
            """UPDATE plenum_cafm.auth_otp_codes
               SET invalidated_at = now()
               WHERE email = :e AND purpose = :p
                 AND consumed_at IS NULL AND invalidated_at IS NULL"""
        ),
        {"e": email, "p": purpose},
    )
    n = int(result.rowcount or 0)
    if n:
        log.info("auth.otp.invalidated", purpose=purpose, count=n, reason=reason)
    return n


async def issue(
    session: AsyncSession,
    *,
    email: str,
    purpose: str,
    user_id: UUID | None = None,
    request_ip: str | None = None,
    enforce_rate_limit: bool = True,
) -> IssuedCode | RateLimited:
    """Draw a code, store its digest, and hand the code back to be emailed.

    The returned code exists only in this process's memory and in the message that goes
    out. Nothing writes it to the database, and nothing logs it.
    """
    if purpose not in PURPOSES:
        raise ValueError(f"unknown OTP purpose {purpose!r}")
    email = normalise_email(email)

    if enforce_rate_limit:
        limited = await check_rate_limit(session, email, purpose)
        if limited is not None:
            log.info("auth.otp.rate_limited", purpose=purpose, reason=limited.reason)
            return limited

    # Before the new one exists, so a crash between the two leaves no code rather than
    # two live ones.
    await invalidate_outstanding(session, email, purpose)

    code = generate_code()
    salt = secrets.token_hex(16)
    ttl = max(1, int(settings.auth_otp_ttl_minutes))
    expires_at = _now() + timedelta(minutes=ttl)

    otp_id = (
        await session.execute(
            text(
                """INSERT INTO plenum_cafm.auth_otp_codes
                       (user_id, email, purpose, code_hash, salt, expires_at,
                        max_attempts, request_ip)
                   VALUES (:u, :e, :p, :h, :s, :x, :m, :ip)
                   RETURNING id"""
            ),
            {
                "u": str(user_id) if user_id else None,
                "e": email, "p": purpose,
                "h": _digest(code, salt), "s": salt, "x": expires_at,
                "m": max(1, int(settings.auth_otp_max_attempts)),
                "ip": request_ip,
            },
        )
    ).scalar_one()

    # Everything about the code except the code.
    log.info("auth.otp.issued", purpose=purpose, otp_id=str(otp_id), ttl_minutes=ttl)
    return IssuedCode(code=code, otp_id=otp_id, expires_at=expires_at, ttl_minutes=ttl)


async def verify(
    session: AsyncSession,
    *,
    email: str,
    purpose: str,
    code: str,
    consume: bool = True,
) -> VerifyResult:
    """Check a submitted code against the newest live one for this address.

    A failed attempt is counted on the row and committed by the caller even when the
    request as a whole fails — otherwise the attempt limit is bypassed by never letting
    the transaction succeed.
    """
    email = normalise_email(email)
    submitted = (code or "").strip().replace(" ", "").replace("-", "")

    row = (
        await session.execute(
            text(
                """SELECT id, user_id, code_hash, salt, expires_at, attempts, max_attempts
                   FROM plenum_cafm.auth_otp_codes
                   WHERE email = :e AND purpose = :p
                     AND consumed_at IS NULL AND invalidated_at IS NULL
                   ORDER BY created_at DESC
                   LIMIT 1
                   FOR UPDATE"""
            ),
            {"e": email, "p": purpose},
        )
    ).mappings().first()

    if row is None:
        return VerifyResult(
            ok=False, reason="no_code",
            message="That code is not valid. Ask for a new one.",
        )

    expires_at = row["expires_at"]
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    if expires_at <= _now():
        await session.execute(
            text("UPDATE plenum_cafm.auth_otp_codes SET invalidated_at = now() WHERE id = :i"),
            {"i": row["id"]},
        )
        return VerifyResult(
            ok=False, reason="expired", otp_id=row["id"],
            message="That code has expired. Ask for a new one.",
        )

    if int(row["attempts"]) >= int(row["max_attempts"]):
        await session.execute(
            text("UPDATE plenum_cafm.auth_otp_codes SET invalidated_at = now() WHERE id = :i"),
            {"i": row["id"]},
        )
        return VerifyResult(
            ok=False, reason="too_many_attempts", otp_id=row["id"],
            message="Too many incorrect attempts. Ask for a new code.",
        )

    # compare_digest, not ==: string comparison returns as soon as two characters differ,
    # and the time it took says how many leading digits were right.
    if not hmac.compare_digest(_digest(submitted, row["salt"]), row["code_hash"]):
        attempts = int(row["attempts"]) + 1
        await session.execute(
            text("UPDATE plenum_cafm.auth_otp_codes SET attempts = :a WHERE id = :i"),
            {"a": attempts, "i": row["id"]},
        )
        remaining = max(0, int(row["max_attempts"]) - attempts)
        log.info("auth.otp.mismatch", purpose=purpose, otp_id=str(row["id"]),
                 attempts=attempts, remaining=remaining)
        return VerifyResult(
            ok=False, reason="mismatch", otp_id=row["id"],
            attempts_remaining=remaining,
            # The remaining count is shown on purpose: it tells the person who mistyped
            # that they have room to try again, and tells an attacker only what the
            # published limit already says.
            message=("That code is not correct. "
                     + (f"{remaining} attempt{'s' if remaining != 1 else ''} left."
                        if remaining else "Ask for a new code.")),
        )

    if consume:
        await session.execute(
            text("UPDATE plenum_cafm.auth_otp_codes SET consumed_at = now() WHERE id = :i"),
            {"i": row["id"]},
        )
    log.info("auth.otp.verified", purpose=purpose, otp_id=str(row["id"]))
    return VerifyResult(
        ok=True, reason="verified", otp_id=row["id"],
        user_id=row["user_id"], message="Verified.",
    )


async def consume(session: AsyncSession, otp_id) -> None:
    """Spend a code that :func:`verify` accepted without consuming.

    Verification and consumption are separable so a caller with further checks to run —
    the reset flow still has "is this the password you already have" ahead of it — can
    accept the code, fail the request for an unrelated reason, and leave the person their
    code. Spending it there means being told to choose a different password with nothing
    left to choose it with.
    """
    if otp_id is None:
        return
    await session.execute(
        text("UPDATE plenum_cafm.auth_otp_codes SET consumed_at = now() "
             "WHERE id = :i AND consumed_at IS NULL"),
        {"i": otp_id},
    )


async def purge_expired(session: AsyncSession, *, older_than_days: int = 7) -> int:
    """Delete spent and expired rows. A consumed code is not evidence of anything."""
    result = await session.execute(
        text(
            """DELETE FROM plenum_cafm.auth_otp_codes
               WHERE created_at < :cut
                 AND (consumed_at IS NOT NULL OR invalidated_at IS NOT NULL
                      OR expires_at < now())"""
        ),
        {"cut": _now() - timedelta(days=max(1, older_than_days))},
    )
    return int(result.rowcount or 0)


def describe_limits() -> dict[str, Any]:
    """The published rules, for a client that wants to show them."""
    return {
        "code_length": int(settings.auth_otp_length),
        "ttl_minutes": int(settings.auth_otp_ttl_minutes),
        "max_attempts": int(settings.auth_otp_max_attempts),
        "resend_cooldown_seconds": int(settings.auth_otp_resend_cooldown_seconds),
        "max_per_hour": int(settings.auth_otp_max_per_hour),
    }
