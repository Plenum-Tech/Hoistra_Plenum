"""Accounts and sessions — sign-up with email OTP, sign-in, password reset.

Why these live in svc-operations-intelligence rather than cafm-connector-service, which
owns plenum_cafm.users: every flow here has to send an email, and the only working email
transport on the platform (Microsoft Graph with SMTP fallback, dry-run when neither is
configured, every message logged to ops_email_log) is in this service. Duplicating that
transport is a worse outcome than reading a table that lives in the same schema — which
is done with SQL, not a second ORM model, so there is still exactly one declaration of
what a user is.

Rate limiting is per-address and lives on the OTP rows, not in this module: it must
survive a restart and apply across replicas, and an in-process counter does neither.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from ...engines.auth import keys

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import settings
from ...shared.email_graph import graph_configured
from ...core.logging import get_logger
from ...db import get_session
from ...engines.auth import accounts as acc
from ...engines.auth import otp as otp_engine
from ...engines.auth import roles as role_engine
from ...engines.auth import secrets_store
from ...engines.auth import tokens as token_engine
from ..schemas.auth import (
    AcceptedResponse,
    ChangePasswordRequest,
    ForgotPasswordRequest,
    RefreshRequest,
    RegisterRequest,
    ResendCodeRequest,
    ResetPasswordRequest,
    RoleChangeResponse,
    SessionResponse,
    SetRoleRequest,
    SignInRequest,
    SignOutRequest,
    SimpleResponse,
    VerifyEmailRequest,
)

log = get_logger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _client_ip(request: Request) -> str | None:
    """The caller's address, as far as it can be trusted.

    X-Forwarded-For is set by the gateway in front of this service and can also be set by
    the caller, so it is recorded as a hint on OTP rows and never used to decide anything.
    Only the left-most entry is kept; the rest are whatever the client felt like sending.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()[:64] or None
    return request.client.host if request.client else None


def _fail(exc: acc.AuthError) -> HTTPException:
    detail: dict[str, Any] = {"ok": False, "error": exc.message, "reason": exc.reason}
    detail.update(exc.extra)
    return HTTPException(status_code=exc.status, detail=detail)


# ── creating an account ──────────────────────────────────────────────────────────────


@router.post("/register", response_model=AcceptedResponse,
             status_code=status.HTTP_202_ACCEPTED)
async def register(
    body: RegisterRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Create an account and email a confirmation code.

    202, not 201: the account exists but cannot be used until the address is confirmed,
    and reporting "created" invites a client to sign the person straight in.

    The response is identical for an address that is already registered. That is not an
    oversight — "email already in use" turns this endpoint into a membership oracle for
    any list of addresses. The owner of the mailbox is told instead, which is the person
    who actually needs to know.
    """
    try:
        return await acc.register(
            session,
            email=body.email, password=body.password, full_name=body.full_name,
            organization_id=body.organization_id, phone=body.phone,
            request_ip=_client_ip(request),
        )
    except acc.AuthError as exc:
        raise _fail(exc) from None


@router.post("/verify-email", response_model=SessionResponse)
async def verify_email(
    body: VerifyEmailRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user_agent: str | None = Header(default=None, alias="user-agent"),
):
    """Confirm an address with its code, and sign the person in.

    Signing in here rather than making them type the password they set two minutes ago:
    they have just proved both the password (at registration) and the mailbox.
    """
    try:
        return await acc.verify_email(
            session, email=body.email, code=body.code,
            user_agent=user_agent, request_ip=_client_ip(request),
        )
    except acc.AuthError as exc:
        raise _fail(exc) from None


@router.post("/resend-code", response_model=AcceptedResponse,
             status_code=status.HTTP_202_ACCEPTED)
async def resend_code(
    body: ResendCodeRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Send another confirmation code.

    Issuing a new code invalidates the previous one — otherwise five presses of "resend"
    leave five live codes and the five-guess limit is really twenty-five.
    """
    try:
        return await acc.resend_code(
            session, email=body.email, purpose=otp_engine.EMAIL_VERIFICATION,
            request_ip=_client_ip(request),
        )
    except acc.AuthError as exc:
        raise _fail(exc) from None


# ── sessions ─────────────────────────────────────────────────────────────────────────


@router.post("/login", response_model=SessionResponse)
async def login(
    body: SignInRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user_agent: str | None = Header(default=None, alias="user-agent"),
):
    """Exchange an email and password for an access token and a refresh token.

    A wrong password and an address with no account return the same 401, after the same
    amount of work — see passwords.burn_time, without which the response time answers the
    question the error message refuses to.
    """
    try:
        return await acc.sign_in(
            session, email=body.email, password=body.password,
            user_agent=user_agent, request_ip=_client_ip(request),
        )
    except acc.AuthError as exc:
        raise _fail(exc) from None


@router.post("/refresh", response_model=SessionResponse)
async def refresh(
    body: RefreshRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user_agent: str | None = Header(default=None, alias="user-agent"),
):
    """Trade a refresh token for a new pair. The old one stops working immediately.

    Presenting a token that was already exchanged ends every session on the account: the
    only ways that happens are a bug or a stolen copy, and the second is worth the
    disruption of the first.
    """
    try:
        pair = await token_engine.rotate_session(
            session, refresh_token=body.refresh_token,
            user_agent=user_agent, request_ip=_client_ip(request),
        )
        principal = await token_engine.principal_from_token(session, pair.access_token)
        await session.commit()
    except token_engine.InvalidToken as exc:
        await session.commit()   # a replay revokes sessions; that must not roll back
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"ok": False, "error": exc.message, "reason": exc.reason},
        ) from None
    row = await acc.find_by_email(session, principal.email)
    return {
        "ok": True,
        "user": acc.public_user(row),
        "tokens": {
            "access_token": pair.access_token, "refresh_token": pair.refresh_token,
            "token_type": pair.token_type, "expires_in": pair.expires_in,
        },
    }


@router.post("/logout", response_model=SimpleResponse)
async def logout(
    body: SignOutRequest,
    session: AsyncSession = Depends(get_session),
    authorization: str | None = Header(default=None),
):
    """End this session, or every session on the account.

    Always reports success. A signed-out caller presenting an expired token has got what
    they asked for, and an error here would only ever be acted on by someone probing
    which tokens are live.
    """
    ended = 0
    if body.everywhere:
        try:
            principal = await _principal(session, authorization)
        except HTTPException:
            principal = None
        if principal is not None:
            ended = await token_engine.revoke_all_sessions(
                session, user_id=principal.user_id, reason="signed_out_everywhere",
            )
    elif body.refresh_token:
        ended = 1 if await token_engine.revoke_session(
            session, refresh_token=body.refresh_token,
        ) else 0
    await session.commit()
    return {"ok": True, "message": "Signed out.", "sessions_ended": ended}


# ── who am I ─────────────────────────────────────────────────────────────────────────


async def _principal(session: AsyncSession, authorization: str | None):
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"ok": False, "error": "Send an Authorization: Bearer <token> header.",
                    "reason": "missing_token"},
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        return await token_engine.principal_from_token(session, token.strip())
    except token_engine.InvalidToken as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"ok": False, "error": exc.message, "reason": exc.reason},
            headers={"WWW-Authenticate": "Bearer"},
        ) from None


async def current_principal(
    session: AsyncSession = Depends(get_session),
    authorization: str | None = Header(default=None),
) -> token_engine.Principal:
    """FastAPI dependency: the caller, or 401.

    Other routers can depend on this to require a signed-in caller. Nothing else in this
    service does yet — every existing endpoint is still open, and wiring them up is a
    separate change with its own blast radius.
    """
    return await _principal(session, authorization)


@router.get("/me")
async def me(
    session: AsyncSession = Depends(get_session),
    principal: token_engine.Principal = Depends(current_principal),
):
    """The signed-in account. Used by a client to check a stored token still works."""
    row = await acc.find_by_email(session, principal.email)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"ok": False, "error": "That account no longer exists.",
                    "reason": "no_account"},
        )
    return {"ok": True, "user": acc.public_user(row),
            "session_id": str(principal.session_id) if principal.session_id else None}


# ── forgotten and changed passwords ──────────────────────────────────────────────────


@router.post("/password/forgot", response_model=AcceptedResponse,
             status_code=status.HTTP_202_ACCEPTED)
async def forgot_password(
    body: ForgotPasswordRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Send a reset code to an address that has an account.

    202 and the same wording either way. "No account with that address" here is a
    membership check anyone can run against any list of addresses, at no cost.
    """
    try:
        return await acc.forgot_password(
            session, email=body.email, request_ip=_client_ip(request),
        )
    except acc.AuthError as exc:
        raise _fail(exc) from None


@router.post("/password/reset", response_model=SimpleResponse)
async def reset_password(
    body: ResetPasswordRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Set a new password using the emailed code, and end every existing session.

    One call rather than "verify the code" then "set the password": a separate check
    step either spends the code, leaving nothing to reset with, or leaves a
    known-correct code live for whoever guessed it.
    """
    try:
        return await acc.reset_password(
            session, email=body.email, code=body.code,
            new_password=body.new_password, request_ip=_client_ip(request),
        )
    except acc.AuthError as exc:
        raise _fail(exc) from None


@router.post("/password/change", response_model=SimpleResponse)
async def change_password(
    body: ChangePasswordRequest,
    session: AsyncSession = Depends(get_session),
    principal: token_engine.Principal = Depends(current_principal),
):
    """Change a password from inside a session, using the current one.

    The current password is required even though the caller is authenticated: without it
    an unattended browser is a permanent account takeover.
    """
    try:
        return await acc.change_password(
            session, user_id=principal.user_id,
            current_password=body.current_password, new_password=body.new_password,
        )
    except acc.AuthError as exc:
        raise _fail(exc) from None


# ── operational ──────────────────────────────────────────────────────────────────────


@router.get("/config")
async def auth_config(response: Response):
    """The published rules, and whether this deployment is configured to enforce them.

    ``secrets_configured`` reports whether the two required secrets are really set. It
    reports booleans and never the values: a deployment running on a per-process
    generated key works perfectly and signs everyone out on every restart, and this is
    the only place that difference is visible without reading the logs.

    ``email_delivery`` is there for the same reason. Every flow that issues a code answers
    202 and says one is on its way; a deployment with EMAIL_DRY_RUN set, or with neither
    Graph nor SMTP configured, writes the message to ops_email_log and drops it, and answers
    202 all the same. It cannot be reported per request — the 202 is deliberately identical
    for an address with an account and one without, and whether the mail was dropped is only
    knowable where an account exists, so saying it there would leak the membership the
    generic wording exists to protect. Said about the deployment, before anyone types an
    address, it leaks nothing.
    """
    secrets_state = secrets_store.configured()
    if not all(secrets_state.values()):
        response.headers["X-Auth-Config-Warning"] = "auth secrets not set"
    graph = graph_configured()
    smtp = bool(settings.smtp_host and settings.smtp_user and settings.smtp_password)
    transport = "graph" if graph else ("smtp" if smtp else "none")
    live = bool(not settings.email_dry_run and (graph or smtp))
    if not live:
        response.headers["X-Auth-Config-Warning"] = (
            response.headers.get("X-Auth-Config-Warning", "") + "; codes are not delivered"
        ).lstrip("; ")
    return {
        "ok": True,
        "self_registration": bool(settings.auth_allow_self_registration),
        "password": {"min_length": int(settings.auth_password_min_length)},
        "otp": otp_engine.describe_limits(),
        "secrets_configured": secrets_state,
        # live: a code requested now would actually be sent. dry_run: the flag is set, so it
        # would not be, whatever is configured. transport: what would carry it.
        "email_delivery": {"live": live, "dry_run": bool(settings.email_dry_run),
                           "transport": transport},
    }


# ── role gating, for this router and any other ───────────────────────────────────────


def require_role(minimum: str):
    """A dependency that admits callers at or above ``minimum``.

    Ranked rather than enumerated: ``require_role(ADMIN)`` admits superadmins without
    anyone having to remember to list them. A set-membership check is one forgotten
    entry away from an endpoint a superadmin cannot reach, which is the kind of bug that
    gets fixed by widening the set until the check means nothing.
    """

    async def _dep(
        principal: token_engine.Principal = Depends(current_principal),
    ) -> token_engine.Principal:
        if not role_engine.at_least(principal.role, minimum):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "ok": False,
                    "error": f"This needs {minimum} access. Yours is {principal.role}.",
                    "reason": "forbidden",
                    "required_role": minimum,
                    "your_role": principal.role,
                },
            )
        return principal

    return _dep


require_admin = require_role(role_engine.ADMIN)
require_superadmin = require_role(role_engine.SUPERADMIN)


@router.get("/roles")
async def list_roles():
    """The three roles, ranked, with what each one means.

    Returned rather than hard-coded in the client, so a picker cannot drift from what the
    server actually enforces.
    """
    return {"ok": True, "roles": role_engine.describe(),
            "default": role_engine.DEFAULT_ROLE}


@router.get("/users")
async def list_users(
    limit: int = 100,
    offset: int = 0,
    session: AsyncSession = Depends(get_session),
    principal: token_engine.Principal = Depends(require_admin),
):
    """The accounts this caller may see — their organisation, or the whole platform."""
    try:
        return await acc.list_accounts(session, actor=principal, limit=limit, offset=offset)
    except acc.AuthError as exc:
        raise _fail(exc) from None


@router.post("/users/{user_id}/role", response_model=RoleChangeResponse)
async def set_user_role(
    user_id: str,
    body: SetRoleRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    principal: token_engine.Principal = Depends(require_admin),
):
    """Change an account's platform role.

    A superadmin may set any role. An admin may move accounts between admin and user
    within their own organisation, and may neither appoint nor demote a superadmin —
    otherwise the two roles are one role with two names, since any admin could award
    themselves the other through a colleague. Nobody may change their own.
    """
    # The engine reads the key type; this route has to as well. Parsing the path segment
    # as a UUID here meant that on an integer-keyed deployment every role change was
    # refused with "user_id must be a UUID" — a 400 that is both unarguable and wrong,
    # from the one endpoint an administrator needs in order to appoint anybody.
    shape = await keys.key_shape(session)
    if not keys.is_valid(user_id, shape.users):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"ok": False,
                    "error": f"user_id must be {keys.describe(shape.users)}.",
                    "reason": "user_id"},
        )
    target = keys.coerce(user_id, shape.users)
    try:
        return await acc.set_role(
            session, actor=principal, target_user_id=target, new_role=body.role,
            reason=body.reason, request_ip=_client_ip(request),
        )
    except acc.AuthError as exc:
        raise _fail(exc) from None
