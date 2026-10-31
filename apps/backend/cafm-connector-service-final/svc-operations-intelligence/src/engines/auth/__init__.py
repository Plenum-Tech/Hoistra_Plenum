"""Email-as-identity accounts: sign-up, OTP confirmation, sign-in, password reset.

    secrets_store   where the signing key and the code pepper come from
    passwords       bcrypt hashing, and what makes a password acceptable
    otp             one-time codes: issue, rate-limit, verify
    tokens          short access tokens, revocable refresh sessions
    accounts        the flows, and the enumeration/timing rules that shape them

plenum_cafm.users belongs to cafm-connector-service. It is read and written here with
SQL rather than a second ORM model, so there is one declaration of what a user is.
"""
from .accounts import AuthError
from .otp import EMAIL_VERIFICATION, PASSWORD_RESET
from .tokens import InvalidToken, Principal

__all__ = [
    "AuthError",
    "EMAIL_VERIFICATION",
    "PASSWORD_RESET",
    "InvalidToken",
    "Principal",
]
