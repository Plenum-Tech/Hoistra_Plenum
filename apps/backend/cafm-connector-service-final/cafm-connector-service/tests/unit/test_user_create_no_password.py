"""POST /users must not be able to set a credential.

It used to. `UserCreate.password_hash: str` was taken from the request body and stored
verbatim, which meant two things:

  the caller chose the password digest for an account belonging to somebody else, and
  no policy could apply — `password_hash="x"` stored "x", and every rule this platform
  has about length, reuse and obvious choices was bypassed by a client that simply did
  not run it.

The capability is removed rather than validated. These tests pin that it stays removed:
a schema is one merge away from growing a field back, and this particular field grows
back looking helpful.
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from cafm_connector.api.routes.plenum_cafm.org_users import UNUSABLE_PASSWORD
from cafm_connector.api.schemas.plenum_cafm import UserCreate, UserResponse, UserUpdate

VALID = {"organization_id": uuid4(), "full_name": "Bala Perera", "email": "bala@example.com"}


# ── no credential goes in ────────────────────────────────────────────────────


def test_create_has_no_password_field_of_any_name():
    fields = set(UserCreate.model_fields)
    assert not {f for f in fields if "password" in f or "hash" in f}, fields


def test_update_has_no_password_field_of_any_name():
    fields = set(UserUpdate.model_fields)
    assert not {f for f in fields if "password" in f or "hash" in f}, fields


def test_sending_a_password_hash_is_refused_not_ignored():
    """Ignoring it would hand a caller still using the old contract a cheerful 201 for an
    account whose password is not what they believe. They would find out at the point
    somebody cannot sign in."""
    with pytest.raises(ValidationError) as exc:
        UserCreate(**VALID, password_hash="$2b$12$anything")
    assert exc.value.errors()[0]["type"] == "extra_forbidden"
    assert exc.value.errors()[0]["loc"] == ("password_hash",)


@pytest.mark.parametrize("field", ["password", "passwordHash", "pw", "hash"])
def test_no_near_miss_spelling_gets_through_either(field):
    with pytest.raises(ValidationError):
        UserCreate(**VALID, **{field: "hunter2hunter2"})


def test_an_update_cannot_set_a_credential_by_any_route():
    """A generic PUT that can write a credential is an account takeover reachable by
    anyone allowed to correct a phone number."""
    with pytest.raises(ValidationError):
        UserUpdate(password_hash="$2b$12$anything")


# ── nor does one come out ────────────────────────────────────────────────────


def test_the_response_never_carries_a_hash():
    assert "password_hash" not in UserResponse.model_fields


# ── roles are bounded here ───────────────────────────────────────────────────


def test_an_invite_defaults_to_facilities_manager():
    assert UserCreate(**VALID).role == "user"


def test_an_invite_may_name_admin_or_user():
    assert UserCreate(**VALID, role="admin").role == "admin"


def test_an_invite_cannot_appoint_a_superadmin():
    """A platform superadmin is appointed through the auth service, which enforces who may
    do that and writes an audit row. A field on a create form does neither."""
    with pytest.raises(ValidationError):
        UserCreate(**VALID, role="superadmin")


def test_the_role_cannot_be_changed_through_the_generic_update():
    assert "role" not in UserUpdate.model_fields


# ── the sentinel ─────────────────────────────────────────────────────────────


def test_the_invited_placeholder_cannot_be_a_working_password():
    """It is not a hash of anything. bcrypt cannot parse it, so every verification against
    it fails — there is no password, guessable or otherwise, that opens an invited
    account before its owner sets one."""
    import bcrypt

    assert UNUSABLE_PASSWORD.startswith("!")
    with pytest.raises(ValueError):
        bcrypt.checkpw(b"anything", UNUSABLE_PASSWORD.encode())
