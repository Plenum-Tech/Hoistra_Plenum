"""What happens when the two auth secrets are not set.

This monorepo already ships `jwt_secret: str = "change-me-in-production"` in
cafm-connector-service. Anyone who has read the repository can mint a token for any
account in any deployment still running on it, and the forged tokens are indistinguishable
from real ones in every log. These tests pin the behaviour that makes that impossible
here: production refuses to start, development is loud.
"""
from __future__ import annotations

import pytest

from src.engines.auth import secrets_store as S


@pytest.fixture(autouse=True)
def _clear_ephemeral():
    S._EPHEMERAL.clear()
    yield
    S._EPHEMERAL.clear()


def _env(monkeypatch, environment, jwt="", pepper=""):
    monkeypatch.setattr(S.settings, "environment", environment)
    monkeypatch.setattr(S.settings, "auth_jwt_secret", jwt)
    monkeypatch.setattr(S.settings, "auth_otp_pepper", pepper)


# ── production will not invent a secret ───────────────────────────────────────


@pytest.mark.parametrize("environment", ["production", "PRODUCTION", "prod", "staging"])
def test_production_refuses_to_run_without_a_signing_key(monkeypatch, environment):
    _env(monkeypatch, environment)
    with pytest.raises(S.AuthNotConfigured) as exc:
        S.jwt_secret()
    assert "AUTH_JWT_SECRET" in str(exc.value)


def test_production_refuses_the_placeholder_that_is_already_in_this_repo(monkeypatch):
    _env(monkeypatch, "production", jwt="change-me-in-production")
    with pytest.raises(S.AuthNotConfigured) as exc:
        S.jwt_secret()
    assert "placeholder" in str(exc.value)


def test_production_refuses_a_short_key(monkeypatch):
    """A short HMAC key is brute-forceable offline against any token ever issued — and
    unlike a stolen key, nothing about it looks wrong from the inside."""
    _env(monkeypatch, "production", jwt="hunter2")
    with pytest.raises(S.AuthNotConfigured) as exc:
        S.jwt_secret()
    assert "characters" in str(exc.value)


def test_production_accepts_a_real_secret(monkeypatch):
    real = "9f2a" * 12
    _env(monkeypatch, "production", jwt=real, pepper=real[::-1])
    assert S.jwt_secret() == real


# ── development generates, and says so ───────────────────────────────────────


def test_development_generates_a_key_per_process(monkeypatch):
    _env(monkeypatch, "development")
    first = S.jwt_secret()
    assert len(first) >= 32
    assert S.jwt_secret() == first, "stable within the process"


def test_a_generated_key_is_not_a_shared_constant(monkeypatch):
    """The failure mode being avoided: every deployment that forgot to set it agreeing on
    the same key."""
    _env(monkeypatch, "development")
    a = S.jwt_secret()
    S._EPHEMERAL.clear()
    assert S.jwt_secret() != a


# ── the two secrets stay separate ────────────────────────────────────────────


def test_the_pepper_is_not_derived_from_the_signing_key(monkeypatch):
    """If the pepper fell back to the signing key, rotating the signing key to end every
    session would also invalidate every one-time code in flight — password resets would
    stop working mid-rollout and nothing would say why."""
    real = "9f2a" * 12
    _env(monkeypatch, "development", jwt=real)
    assert S.otp_pepper() != S.jwt_secret()


def test_configured_reports_state_without_revealing_values(monkeypatch):
    real = "9f2a" * 12
    _env(monkeypatch, "development", jwt=real, pepper="")
    state = S.configured()
    assert state == {"jwt_secret": True, "otp_pepper": False}
    assert real not in str(state)


def test_a_placeholder_does_not_count_as_configured(monkeypatch):
    _env(monkeypatch, "development", jwt="changeme", pepper="secret")
    assert S.configured() == {"jwt_secret": False, "otp_pepper": False}
