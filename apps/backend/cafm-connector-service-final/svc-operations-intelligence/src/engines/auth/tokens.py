"""Access tokens, refresh sessions, and the reason there are two kinds.

An access token is a signed statement that is believed on sight. That makes it fast and
makes it impossible to withdraw: once issued, it is valid until it expires, and no
password change, lockout or "sign out everywhere" can reach it. So it is short.

A refresh token is a row. It can be revoked the instant it needs to be, which is what
makes "reset my password" mean anything — otherwise whoever had the old password keeps a
working session for as long as their token lasts, and the reset only inconveniences the
owner. It is stored as a SHA-256 digest: a leaked backup of ``auth_sessions`` is then a
list of used-up hashes rather than a set of live credentials.

Refresh tokens rotate. Each use issues a new one and revokes the old, so a token that is
presented twice is either a bug or a stolen copy — and the second presentation is
detectable, which a non-rotating token never is.
"""
from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import jwt
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import settings
from ...core.logging import get_logger
from .secrets_store import jwt_secret

log = get_logger(__name__)

ACCESS = "access"


@dataclass(frozen=True)
class TokenPair:
    access_token: str
    refresh_token: str
    token_type: str
    expires_in: int
    session_id: UUID


@dataclass(frozen=True)
class Principal:
    """Who a valid access token says the caller is."""
    user_id: UUID
    email: str
    organization_id: UUID | None
    session_id: UUID | None
    issued_at: datetime
    password_changed_at: int


class InvalidToken(Exception):
    """The token is absent, malformed, expired, or no longer honoured."""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason
        self.message = message


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _epoch(value: datetime | None) -> int:
    if value is None:
        return 0
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return int(value.timestamp())


def hash_refresh_token(token: str) -> str:
    """SHA-256, not bcrypt.

    A refresh token is 32 bytes from the system CSPRNG — there is no dictionary to run
    against it and nothing to slow down. bcrypt here would add 250ms to every refresh and
    buy nothing, which is the mirror image of using SHA-256 on a password.
    """
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def issue_access_token(
    *,
    user_id: UUID,
    email: str,
    organization_id: UUID | None,
    session_id: UUID | None,
    password_changed_at: datetime | None,
) -> tuple[str, int]:
    """A signed access token and its lifetime in seconds."""
    ttl = max(1, int(settings.auth_access_token_ttl_minutes))
    now = _now()
    claims: dict[str, Any] = {
        "sub": str(user_id),
        "email": email,
        "org": str(organization_id) if organization_id else None,
        "sid": str(session_id) if session_id else None,
        # When the account's password was last set. Checked against the stored value on
        # every request, so a password change invalidates tokens issued before it without
        # needing to find and delete them.
        "pwd": _epoch(password_changed_at),
        "typ": ACCESS,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=ttl)).timestamp()),
        "jti": secrets.token_urlsafe(12),
    }
    token = jwt.encode(claims, jwt_secret(), algorithm=settings.auth_jwt_algorithm)
    return token, ttl * 60


def decode_access_token(token: str) -> dict[str, Any]:
    """Decode and check the signature, or raise :class:`InvalidToken`.

    ``algorithms`` is pinned to the one configured. Accepting whatever the token's own
    header asks for is the classic JWT defeat: a token that says ``alg: none`` verifies
    against nothing, and one that says ``HS256`` against an RS256 deployment gets checked
    with the public key as an HMAC secret.
    """
    if not token:
        raise InvalidToken("missing", "No access token supplied.")
    try:
        return jwt.decode(
            token,
            jwt_secret(),
            algorithms=[settings.auth_jwt_algorithm],
            options={"require": ["exp", "iat", "sub"]},
        )
    except jwt.ExpiredSignatureError:
        raise InvalidToken("expired", "Your session has expired. Sign in again.") from None
    except jwt.InvalidTokenError as exc:
        log.info("auth.token.rejected", error=type(exc).__name__)
        raise InvalidToken("invalid", "That access token is not valid.") from None


async def principal_from_token(session: AsyncSession, token: str) -> Principal:
    """The caller behind a token, after checking the account still agrees.

    A valid signature is not enough. Between issue and use the account may have been
    disabled, deleted, or had its password changed by someone recovering it from an
    attacker — and in every one of those cases the token is still perfectly well signed.
    """
    claims = decode_access_token(token)
    if claims.get("typ") != ACCESS:
        # A refresh token is not an access token. Without this check the longer-lived
        # credential would be accepted everywhere the short one is.
        raise InvalidToken("wrong_type", "That is not an access token.")

    try:
        user_id = UUID(str(claims.get("sub")))
    except (TypeError, ValueError):
        raise InvalidToken("invalid", "That access token is not valid.") from None

    row = (
        await session.execute(
            text(
                """SELECT id, email, organization_id, status, password_changed_at
                   FROM plenum_cafm.users WHERE id = :i"""
            ),
            {"i": str(user_id)},
        )
    ).mappings().first()
    if row is None:
        raise InvalidToken("no_account", "That account no longer exists.")
    if str(row["status"]).lower() not in {"active", "pending_verification"}:
        raise InvalidToken("disabled", "That account is not active.")

    if int(claims.get("pwd") or 0) != _epoch(row["password_changed_at"]):
        # Issued before the current password was set. This is the whole point of the
        # claim: a reset ends every session that existed before it.
        raise InvalidToken("password_changed",
                           "Your password was changed. Sign in again.")

    sid = claims.get("sid")
    return Principal(
        user_id=row["id"],
        email=row["email"],
        organization_id=row["organization_id"],
        session_id=UUID(sid) if sid else None,
        issued_at=datetime.fromtimestamp(int(claims["iat"]), tz=timezone.utc),
        password_changed_at=int(claims.get("pwd") or 0),
    )


async def open_session(
    session: AsyncSession,
    *,
    user_id: UUID,
    email: str,
    organization_id: UUID | None,
    password_changed_at: datetime | None,
    user_agent: str | None = None,
    request_ip: str | None = None,
) -> TokenPair:
    """Start a signed-in session: one access token and one refresh token."""
    refresh = secrets.token_urlsafe(32)
    days = max(1, int(settings.auth_refresh_token_ttl_days))
    session_id = (
        await session.execute(
            text(
                """INSERT INTO plenum_cafm.auth_sessions
                       (user_id, refresh_token_hash, expires_at, user_agent, request_ip)
                   VALUES (:u, :h, :x, :ua, :ip)
                   RETURNING id"""
            ),
            {
                "u": str(user_id), "h": hash_refresh_token(refresh),
                "x": _now() + timedelta(days=days),
                "ua": (user_agent or "")[:400] or None, "ip": request_ip,
            },
        )
    ).scalar_one()

    access, expires_in = issue_access_token(
        user_id=user_id, email=email, organization_id=organization_id,
        session_id=session_id, password_changed_at=password_changed_at,
    )
    log.info("auth.session.opened", user_id=str(user_id), session_id=str(session_id))
    return TokenPair(
        access_token=access, refresh_token=refresh, token_type="Bearer",
        expires_in=expires_in, session_id=session_id,
    )


async def rotate_session(
    session: AsyncSession, *, refresh_token: str,
    user_agent: str | None = None, request_ip: str | None = None,
) -> TokenPair:
    """Exchange a refresh token for a new pair, revoking the one presented."""
    digest = hash_refresh_token(refresh_token)
    row = (
        await session.execute(
            text(
                """SELECT s.id, s.user_id, s.expires_at, s.revoked_at,
                          u.email, u.organization_id, u.status, u.password_changed_at
                   FROM plenum_cafm.auth_sessions s
                   JOIN plenum_cafm.users u ON u.id = s.user_id
                   WHERE s.refresh_token_hash = :h
                   FOR UPDATE OF s"""
            ),
            {"h": digest},
        )
    ).mappings().first()

    if row is None:
        raise InvalidToken("invalid", "That session is not valid. Sign in again.")

    if row["revoked_at"] is not None:
        # A revoked token being presented means the holder is using a copy that was
        # already exchanged — replay, or a stolen backup. The safe reading is that the
        # account is compromised, so every session it has goes.
        log.warning("auth.session.replay", user_id=str(row["user_id"]),
                    session_id=str(row["id"]))
        await revoke_all_sessions(session, user_id=row["user_id"], reason="refresh_replay")
        raise InvalidToken("replayed",
                           "That session was already used. Every session has been ended "
                           "as a precaution — sign in again.")

    expires_at = row["expires_at"]
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= _now():
        raise InvalidToken("expired", "Your session has expired. Sign in again.")

    if str(row["status"]).lower() != "active":
        raise InvalidToken("disabled", "That account is not active.")

    await session.execute(
        text(
            """UPDATE plenum_cafm.auth_sessions
               SET revoked_at = now(), revoked_reason = 'rotated', last_used_at = now()
               WHERE id = :i"""
        ),
        {"i": row["id"]},
    )
    return await open_session(
        session,
        user_id=row["user_id"], email=row["email"],
        organization_id=row["organization_id"],
        password_changed_at=row["password_changed_at"],
        user_agent=user_agent, request_ip=request_ip,
    )


async def revoke_session(session: AsyncSession, *, refresh_token: str,
                         reason: str = "signed_out") -> bool:
    """End one session. True if there was a live one to end."""
    result = await session.execute(
        text(
            """UPDATE plenum_cafm.auth_sessions
               SET revoked_at = now(), revoked_reason = :r
               WHERE refresh_token_hash = :h AND revoked_at IS NULL"""
        ),
        {"h": hash_refresh_token(refresh_token), "r": reason},
    )
    return bool(result.rowcount)


async def revoke_all_sessions(session: AsyncSession, *, user_id: UUID,
                              reason: str = "password_reset") -> int:
    """End every session this account has. Returns how many were live."""
    result = await session.execute(
        text(
            """UPDATE plenum_cafm.auth_sessions
               SET revoked_at = now(), revoked_reason = :r
               WHERE user_id = :u AND revoked_at IS NULL"""
        ),
        {"u": str(user_id), "r": reason},
    )
    n = int(result.rowcount or 0)
    if n:
        log.info("auth.sessions.revoked", user_id=str(user_id), count=n, reason=reason)
    return n
