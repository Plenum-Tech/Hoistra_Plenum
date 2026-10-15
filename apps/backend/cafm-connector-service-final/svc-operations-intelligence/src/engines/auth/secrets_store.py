"""Where the two auth secrets come from, and what happens when they are absent.

There are exactly two: the key that signs access tokens, and the pepper that keys the
hash of one-time codes. Neither ships with a default value.

A shipped default is not a convenience, it is a shared secret published to everyone who
has ever read the repository. `jwt_secret: str = "change-me-in-production"` is already in
this monorepo (cafm-connector-service/core/config.py) and would let anyone holding that
string mint a token for any account in any deployment that had not changed it — with
nothing in the logs to distinguish those tokens from real ones.

So: production refuses to start without them. Development generates one per process,
which means restarting the service signs everybody out. That is mildly irritating, which
is the point — it is noticed, and then it is set.
"""
from __future__ import annotations

import secrets

from ...config import settings
from ...core.logging import get_logger

log = get_logger(__name__)

#: Generated once per process when a secret is absent outside production.
_EPHEMERAL: dict[str, str] = {}

#: Values that are not secrets. They appear in example files and get copied forward, and
#: a placeholder that reaches production is worse than an empty one because it starts.
_PLACEHOLDERS = frozenset({
    "change-me", "change-me-in-production", "changeme", "secret", "test",
    "change-me-before-going-to-production", "your-secret-here", "todo",
})


class AuthNotConfigured(RuntimeError):
    """A required auth secret is missing in an environment that must not invent one."""


def _is_production() -> bool:
    return str(settings.environment or "").strip().lower() in {"production", "prod", "staging"}


def _resolve(name: str, configured: str, *, min_length: int = 32) -> str:
    value = (configured or "").strip()

    if value and value.lower() in _PLACEHOLDERS:
        # Named rather than silently accepted. A deployment running on "change-me" has no
        # authentication at all, and it looks exactly like one that does.
        if _is_production():
            raise AuthNotConfigured(
                f"{name} is set to the placeholder {value!r}. Set it to a real secret "
                f"(at least {min_length} random characters) before running in "
                f"{settings.environment}."
            )
        log.warning("auth.secret_is_placeholder", secret=name, environment=settings.environment)
        value = ""

    if value and len(value) < min_length:
        if _is_production():
            raise AuthNotConfigured(
                f"{name} is only {len(value)} characters. Use at least {min_length}: a "
                "short signing key is brute-forceable offline against any token you have "
                "ever issued."
            )
        log.warning("auth.secret_too_short", secret=name, length=len(value), want=min_length)

    if value:
        return value

    if _is_production():
        raise AuthNotConfigured(
            f"{name} is not set. This service will not run in {settings.environment} "
            "without it — a generated key would change on every restart and a default "
            "one would be shared with every other deployment that forgot to set it."
        )

    if name not in _EPHEMERAL:
        _EPHEMERAL[name] = secrets.token_urlsafe(48)
        log.warning(
            "auth.secret_generated_for_this_process",
            secret=name,
            environment=settings.environment,
            note="tokens and codes will not survive a restart; set it to keep them",
        )
    return _EPHEMERAL[name]


def jwt_secret() -> str:
    """The key access tokens are signed with."""
    return _resolve("AUTH_JWT_SECRET", settings.auth_jwt_secret)


def otp_pepper() -> str:
    """The key one-time codes are hashed under.

    Kept apart from the signing key so a copy of the codes table plus the signing key is
    still not enough to derive a live code, and so either can be rotated alone.
    """
    configured = settings.auth_otp_pepper
    if not (configured or "").strip():
        # Falling back to the signing key would quietly couple them: rotating the signing
        # key to end all sessions would also invalidate every code in flight, and someone
        # would spend an afternoon working out why password resets stopped mid-rollout.
        return _resolve("AUTH_OTP_PEPPER", "")
    return _resolve("AUTH_OTP_PEPPER", configured)


def configured() -> dict[str, bool]:
    """Whether each secret is really set — for a health endpoint, never for a response."""
    return {
        "jwt_secret": bool((settings.auth_jwt_secret or "").strip())
        and (settings.auth_jwt_secret or "").strip().lower() not in _PLACEHOLDERS,
        "otp_pepper": bool((settings.auth_otp_pepper or "").strip())
        and (settings.auth_otp_pepper or "").strip().lower() not in _PLACEHOLDERS,
    }
