"""Request and response bodies for the auth routes.

Two things these deliberately do NOT do.

They do not enforce the password policy. Pydantic would reject a weak password with a 422
naming the field before the handler runs, which sounds fine until you notice it happens at
a different point in the request than every other rejection — and the difference in *when*
a request fails is itself information. The policy lives in ``engines.auth.passwords`` and
runs at a fixed point in each flow.

And no response model carries ``password_hash``. The shapes below name what goes out
rather than filtering what must not: a denylist is one added column away from returning a
password digest to a browser.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, EmailStr, Field


class RegisterRequest(BaseModel):
    """Email is the account identifier — there is no separate username."""
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)
    full_name: str = Field(min_length=1, max_length=255)
    phone: str | None = Field(default=None, max_length=50)
    # Required only where the deployment has more than one organisation. Left out, the
    # single organisation is used and a guess is refused rather than made.
    organization_id: str | None = None


class VerifyEmailRequest(BaseModel):
    email: EmailStr
    code: str = Field(min_length=4, max_length=12)


class ResendCodeRequest(BaseModel):
    email: EmailStr


class SignInRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=10, max_length=500)


class SignOutRequest(BaseModel):
    refresh_token: str | None = Field(default=None, max_length=500)
    # Ends every session, not just this one — for "I think someone else is signed in".
    everywhere: bool = False


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    """One call: the code and the new password together.

    Splitting this into "check the code" then "set the password" would either spend the
    code on the check, leaving nothing to reset with, or leave a code that has been
    confirmed correct sitting live — which is a strictly better prize for a guesser than
    an unconfirmed one.
    """
    email: EmailStr
    code: str = Field(min_length=4, max_length=12)
    new_password: str = Field(min_length=1, max_length=200)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=1, max_length=200)


class SetRoleRequest(BaseModel):
    """Which role, and why.

    The reason is optional but recorded. Six months later "who made this person an
    admin" has an answer either way; "and why" only has one if somebody typed it.
    """
    role: str = Field(min_length=1, max_length=20)
    reason: str | None = Field(default=None, max_length=500)


class PublicUser(BaseModel):
    id: str
    email: str
    full_name: str
    organization_id: str | None = None
    status: str
    email_verified: bool
    role: str = "user"
    role_label: str | None = None
    last_login_at: str | None = None


class TokenBundle(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "Bearer"
    expires_in: int


class SessionResponse(BaseModel):
    ok: bool = True
    user: PublicUser
    tokens: TokenBundle
    message: str | None = None


class AcceptedResponse(BaseModel):
    """What every flow that sends an email returns.

    The same shape and the same wording whether or not an account exists, because the
    difference between those two answers is what a stranger is trying to learn.
    """
    ok: bool = True
    status: str
    message: str
    email: str | None = None
    otp: dict[str, Any] | None = None


class SimpleResponse(BaseModel):
    ok: bool = True
    message: str
    sessions_ended: int | None = None


class RoleChangeResponse(BaseModel):
    ok: bool = True
    changed: bool
    message: str
    user: PublicUser
    sessions_ended: int | None = None
