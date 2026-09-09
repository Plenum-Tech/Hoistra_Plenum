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

from . import keys

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
    # The role as the DATABASE holds it right now, not as the token claimed. A token
    # minted before a demotion still says "admin", and believing it would mean a
    # revoked privilege keeps working until the token expires.
    role: str


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
    role: str = "user",
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
        # Carried for a client that wants to render a menu without another round trip.
        # It is NOT what any authorisation decision reads — principal_from_token reads
        # the row, because a token outlives a demotion by up to its whole lifetime.
        "role": role,
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
        # `sub` is carried as the string it was issued as, not parsed into a UUID. On an
        # integer-keyed deployment UUID("18") raises, which turned every bearer call from
        # every existing member of staff into a 401 that said "invalid" and meant "this
        # service expects a different kind of database".
        sub = str(claims.get("sub") or "").strip()
        if not sub:
            raise InvalidToken("invalid", "That session is not valid. Sign in again.")
        user_id = keys.coerce(sub, (await keys.key_shape(session)).users)
        if user_id is None:
            raise InvalidToken("invalid", "That session is not valid. Sign in again.")
    except (TypeError, ValueError):
        raise InvalidToken("invalid", "That access token is not valid.") from None

    # The session is joined, not just the account. Without this an access token outlives
    # everything meant to end it: "sign out everywhere" reports success and the token
    # keeps working for the rest of its life; a demotion takes effect on privilege but
    # not on the session; a stolen laptop stays signed in for the full TTL after somebody
    # has pressed every button the product offers to stop it. The join costs one extra
    # indexed lookup on a query that was already being made.
    sid = claims.get("sid")
    row = (
        await session.execute(
            text(
                """SELECT u.id, u.email, u.organization_id, u.status,
                          u.password_changed_at, u.platform_role AS role,
                          s.revoked_at, s.expires_at, s.revoked_reason
                   FROM plenum_cafm.users u
                   LEFT JOIN plenum_cafm.auth_sessions s
                          ON s.id = CAST(:s AS UUID)
                   WHERE u.id = :i"""
            ),
            {"i": await keys.user_key(session, user_id), "s": sid},
        )
    ).mappings().first()
    if row is None:
        raise InvalidToken("no_account", "That account no longer exists.")

    if str(row["status"]).lower() not in {"active", "pending_verification"}:
        raise InvalidToken("disabled", "That account is not active.")

    # Checked BEFORE the session below, so the more specific reason wins. A
    # password reset both moves this instant and revokes every session, and
    # "your password was changed" tells the person what happened where "that
    # session was ended" leaves them guessing.
    if int(claims.get("pwd") or 0) != _epoch(row["password_changed_at"]):
        # Issued before the current password was set. This is the whole point of the
        # claim: a reset ends every session that existed before it.
        raise InvalidToken("password_changed",
                           "Your password was changed. Sign in again.")

    if sid is not None:
        if row["revoked_at"] is not None:
            reason = str(row["revoked_reason"] or "")
            raise InvalidToken(
                "session_revoked",
                "That session was ended. Sign in again."
                if reason != "role_reduced"
                else "Your access level changed. Sign in again.",
            )
        expires = row["expires_at"]
        if expires is not None:
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            if expires <= _now():
                raise InvalidToken("expired", "Your session has expired. Sign in again.")

    return Principal(
        user_id=row["id"],
        email=row["email"],
        organization_id=row["organization_id"],
        session_id=UUID(sid) if sid else None,
        issued_at=datetime.fromtimestamp(int(claims["iat"]), tz=timezone.utc),
        password_changed_at=int(claims.get("pwd") or 0),
        role=str(row["role"] or "user"),
    )


async def open_session(
    session: AsyncSession,
    *,
    user_id: UUID,
    email: str,
    organization_id: UUID | None,
    password_changed_at: datetime | None,
    role: str = "user",
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
                "u": await keys.user_key(session, user_id),
                "h": hash_refresh_token(refresh),
                "x": _now() + timedelta(days=days),
                "ua": (user_agent or "")[:400] or None, "ip": request_ip,
            },
        )
    ).scalar_one()

    access, expires_in = issue_access_token(
        user_id=user_id, email=email, organization_id=organization_id,
        session_id=session_id, password_changed_at=password_changed_at, role=role,
    )
    log.info("auth.session.opened", user_id=str(user_id), session_id=str(session_id))
    return TokenPair(
        access_token=access, refresh_token=refresh, token_type="Bearer",
        expires_in=expires_in, session_id=session_id,
    )


#: How long the digest a session held before its last rotation stays acceptable.
#:
#: Two tabs restored together both present the same stored refresh token, microseconds
#: apart. Without this the second arrival is indistinguishable from a stolen token being
#: replayed, and the response to a stolen token is to end every session — so restoring a
#: browser signed the person out. Long enough to cover a restore and one network retry,
#: short enough that a token copied off a device is worthless by the time it is used.
ROTATION_GRACE_SECONDS = 30


async def rotate_session(
    session: AsyncSession, *, refresh_token: str,
    user_agent: str | None = None, request_ip: str | None = None,
) -> TokenPair:
    """Exchange a refresh token for a new pair, keeping the session it belongs to.

    The session row outlives the token: rotation replaces ``refresh_token_hash`` in place,
    remembers the digest it replaced, and does not change the session id.

    That the id survives is the point. ``principal_from_token`` refuses any access token
    whose ``sid`` has been revoked, so ending the row on every rotation invalidated the
    access token each *other* tab was still holding — one tab refreshing signed the person
    out everywhere on its next bearer-checked call.
    """
    digest = hash_refresh_token(refresh_token)
    row = (
        await session.execute(
            text(
                """SELECT s.id, s.user_id, s.expires_at, s.revoked_at, s.rotated_at,
                          (s.refresh_token_hash = :h) AS is_current,
                          u.email, u.organization_id, u.status, u.password_changed_at,
                          u.platform_role AS role
                   FROM plenum_cafm.auth_sessions s
                   JOIN plenum_cafm.users u ON u.id = s.user_id
                   WHERE s.refresh_token_hash = :h
                      OR s.prev_refresh_token_hash = :h
                   FOR UPDATE OF s"""
            ),
            {"h": digest},
        )
    ).mappings().first()

    if row is None:
        raise InvalidToken("invalid", "That session is not valid. Sign in again.")

    if row["revoked_at"] is not None:
        # A token for a session that was deliberately ended — replay, or a stolen backup.
        # The safe reading is that the account is compromised, so every session goes.
        log.warning("auth.session.replay", user_id=str(row["user_id"]),
                    session_id=str(row["id"]), matched="revoked_session")
        await revoke_all_sessions(session, user_id=row["user_id"], reason="refresh_replay")
        raise InvalidToken("replayed",
                           "That session was already used. Every session has been ended "
                           "as a precaution — sign in again.")

    if not row["is_current"]:
        # The digest matched what this session held BEFORE its last rotation. Inside the
        # grace window that is the second tab of a browser restore, exchanging the same
        # stored token a moment later. Outside it, it is a token that has been sitting
        # somewhere since it was superseded, which is what theft looks like.
        rotated_at = row["rotated_at"]
        if rotated_at is not None and rotated_at.tzinfo is None:
            rotated_at = rotated_at.replace(tzinfo=timezone.utc)
        age = (_now() - rotated_at).total_seconds() if rotated_at else None
        if age is None or age > ROTATION_GRACE_SECONDS:
            log.warning("auth.session.replay", user_id=str(row["user_id"]),
                        session_id=str(row["id"]), matched="stale_previous_token",
                        age_seconds=age)
            await revoke_all_sessions(
                session, user_id=row["user_id"], reason="refresh_replay")
            raise InvalidToken(
                "replayed",
                "That session was already used. Every session has been ended "
                "as a precaution — sign in again.")
        log.info("auth.session.rotation_grace", user_id=str(row["user_id"]),
                 session_id=str(row["id"]), age_seconds=round(age, 3))

    expires_at = row["expires_at"]
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= _now():
        raise InvalidToken("expired", "Your session has expired. Sign in again.")

    if str(row["status"]).lower() != "active":
        raise InvalidToken("disabled", "That account is not active.")

    # Rotate in place. The digest being replaced is kept, so the tab that presented it a
    # moment ago is recognised rather than accused; the session id — and therefore every
    # access token already issued against it — survives untouched.
    refresh = secrets.token_urlsafe(32)
    await session.execute(
        text(
            """UPDATE plenum_cafm.auth_sessions
               SET prev_refresh_token_hash = refresh_token_hash,
                   refresh_token_hash      = :new,
                   rotated_at              = now(),
                   last_used_at            = now()
               WHERE id = :i"""
        ),
        {"new": hash_refresh_token(refresh), "i": row["id"]},
    )

    access, expires_in = issue_access_token(
        user_id=row["user_id"], email=row["email"],
        organization_id=row["organization_id"],
        session_id=row["id"], password_changed_at=row["password_changed_at"],
        role=str(row["role"] or "user"),
    )
    log.info("auth.session.rotated", user_id=str(row["user_id"]),
             session_id=str(row["id"]))
    return TokenPair(
        access_token=access, refresh_token=refresh, token_type="Bearer",
        expires_in=expires_in, session_id=row["id"],
    )


async def revoke_session(session: AsyncSession, *, refresh_token: str,
                         reason: str = "signed_out") -> bool:
    """End one session. True if there was a live one to end."""
    result = await session.execute(
        text(
            """UPDATE plenum_cafm.auth_sessions
               SET revoked_at = now(), revoked_reason = :r
               WHERE (refresh_token_hash = :h OR prev_refresh_token_hash = :h)
                 AND revoked_at IS NULL"""
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
        {"u": await keys.user_key(session, user_id), "r": reason},
    )
    n = int(result.rowcount or 0)
    if n:
        log.info("auth.sessions.revoked", user_id=str(user_id), count=n, reason=reason)
    return n
