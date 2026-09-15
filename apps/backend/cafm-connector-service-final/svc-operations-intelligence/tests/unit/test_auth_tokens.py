"""Access tokens: what they must refuse, and the one claim that makes a reset mean something."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import jwt
import pytest

from src.config import settings
from src.engines.auth import otp as O
from src.engines.auth import secrets_store
from src.engines.auth import tokens as T

USER = uuid4()
ORG = uuid4()
SESSION = uuid4()
PWD_AT = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


def _token(**over):
    kwargs = dict(user_id=USER, email="a@b.com", organization_id=ORG,
                  session_id=SESSION, password_changed_at=PWD_AT)
    kwargs.update(over)
    return T.issue_access_token(**kwargs)[0]


# ── the claims ───────────────────────────────────────────────────────────────


def test_a_token_carries_who_and_when_and_nothing_else():
    claims = T.decode_access_token(_token())
    assert claims["sub"] == str(USER)
    assert claims["typ"] == T.ACCESS
    assert claims["pwd"] == int(PWD_AT.timestamp())
    # Whatever else is in there, it is not a credential.
    assert "password" not in str(claims).lower()


def test_two_tokens_for_the_same_account_are_distinct():
    """A jti per token, so one can be told from another in a log."""
    assert T.decode_access_token(_token())["jti"] != T.decode_access_token(_token())["jti"]


def test_expiry_is_the_configured_ttl():
    token, expires_in = T.issue_access_token(
        user_id=USER, email="a@b.com", organization_id=ORG,
        session_id=SESSION, password_changed_at=PWD_AT,
    )
    assert expires_in == settings.auth_access_token_ttl_minutes * 60


# ── what a token must refuse ─────────────────────────────────────────────────


def test_a_token_signed_with_another_key_is_refused():
    forged = jwt.encode({"sub": str(USER), "typ": T.ACCESS,
                         "iat": 1, "exp": 9999999999},
                        "some-other-key-entirely", algorithm="HS256")
    with pytest.raises(T.InvalidToken):
        T.decode_access_token(forged)


def test_an_unsigned_token_is_refused():
    """The classic JWT defeat: alg=none verifies against nothing. It only works if the
    verifier trusts the token's own header to choose the algorithm."""
    unsigned = jwt.encode({"sub": str(USER), "typ": T.ACCESS, "iat": 1,
                           "exp": 9999999999}, key="", algorithm="none")
    with pytest.raises(T.InvalidToken):
        T.decode_access_token(unsigned)


def test_an_expired_token_says_so_rather_than_being_merely_invalid():
    past = {
        "sub": str(USER), "typ": T.ACCESS,
        "iat": int((datetime.now(timezone.utc) - timedelta(hours=2)).timestamp()),
        "exp": int((datetime.now(timezone.utc) - timedelta(hours=1)).timestamp()),
    }
    token = jwt.encode(past, secrets_store.jwt_secret(),
                       algorithm=settings.auth_jwt_algorithm)
    with pytest.raises(T.InvalidToken) as exc:
        T.decode_access_token(token)
    assert exc.value.reason == "expired"


def test_a_token_with_no_expiry_is_refused():
    """Otherwise a token minted without exp is valid for ever."""
    forever = jwt.encode({"sub": str(USER), "typ": T.ACCESS, "iat": 1},
                        secrets_store.jwt_secret(),
                        algorithm=settings.auth_jwt_algorithm)
    with pytest.raises(T.InvalidToken):
        T.decode_access_token(forever)


def test_rubbish_is_refused_without_raising_something_else():
    for junk in ("", "not.a.token", "a.b.c", "Bearer x"):
        with pytest.raises(T.InvalidToken):
            T.decode_access_token(junk)


# ── refresh tokens ───────────────────────────────────────────────────────────


def test_a_refresh_token_is_stored_as_a_digest():
    digest = T.hash_refresh_token("some-refresh-token")
    assert len(digest) == 64 and digest != "some-refresh-token"
    assert T.hash_refresh_token("some-refresh-token") == digest, "deterministic"
    assert T.hash_refresh_token("some-refresh-tokeN") != digest


# ── one-time codes ───────────────────────────────────────────────────────────


def test_a_code_is_the_configured_length_and_zero_padded():
    for _ in range(50):
        code = O.generate_code()
        assert len(code) == settings.auth_otp_length and code.isdigit()


def test_codes_are_not_all_the_same():
    assert len({O.generate_code() for _ in range(200)}) > 100


def test_the_full_range_including_leading_zeros_is_reachable():
    """A code formatted with str(int) instead of zero-padding would silently lose every
    value below 100000 — a tenth of the space, and always the same tenth."""
    codes = [O.generate_code() for _ in range(3000)]
    assert any(c.startswith("0") for c in codes)


def test_the_digest_depends_on_the_salt_and_the_pepper():
    a = O._digest("123456", "salt-one")
    b = O._digest("123456", "salt-two")
    assert a != b, "the same code under two salts must not share a digest"
    assert a == O._digest("123456", "salt-one"), "deterministic"
    assert len(a) == 64


def test_the_stored_digest_is_not_the_code():
    assert "123456" not in O._digest("123456", "some-salt")


def test_email_matching_is_case_and_space_insensitive():
    assert O.normalise_email("  Bala@Example.COM ") == "bala@example.com"


def test_purposes_are_a_closed_set():
    assert O.PURPOSES == {"email_verification", "password_reset"}
