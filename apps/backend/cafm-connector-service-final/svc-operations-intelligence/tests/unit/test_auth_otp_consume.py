"""Verifying a code and spending it are separate steps, and the reset flow needs them to be.

The bug this pins: POST /password/reset verified the code (consuming it), then checked
whether the new password was the one the account already had, and refused. The code was
gone. The person was told to choose a different password with nothing left to choose it
with, and the next attempt came back "that code is not valid" — which reads as the reset
link being broken.

Moving the check earlier is not the fix. "Is this the account's current password"
answered before any code is presented is a password oracle on an unauthenticated
endpoint: submit a guess, read the error, learn whether it was right. So the order
stands, and the code is spent at the last possible moment instead.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from src.engines.auth import otp as OTP


class _Row(dict):
    pass


class _Result:
    def __init__(self, rows=None, rowcount=0):
        self._rows = rows or []
        self.rowcount = rowcount

    def mappings(self):
        return self

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return self._rows

    def scalars(self):
        return self

    def scalar_one(self):
        return self._rows[0] if self._rows else None


class _Session:
    """Records every statement, and answers the one SELECT verify() makes."""

    def __init__(self, row):
        self.row = row
        self.statements: list[tuple[str, dict]] = []

    async def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        self.statements.append((sql, params or {}))
        if sql.startswith("SELECT id, user_id, code_hash"):
            return _Result([self.row] if self.row else [])
        return _Result(rowcount=1)

    def wrote(self, fragment: str) -> bool:
        return any(fragment in s for s, _ in self.statements)


@pytest.fixture(autouse=True)
def _pepper(monkeypatch):
    monkeypatch.setattr(OTP, "otp_pepper", lambda: "unit-test-pepper-value-000000000000")


def _live_row(code="123456", salt="abcdef", attempts=0):
    return _Row(
        id=uuid4(), user_id=uuid4(),
        code_hash=OTP._digest(code, salt), salt=salt,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        attempts=attempts, max_attempts=5,
    )


def _verify(session, code, consume):
    return asyncio.run(
        OTP.verify(session, email="a@b.com", purpose=OTP.PASSWORD_RESET,
                   code=code, consume=consume)
    )


# ── the separation ───────────────────────────────────────────────────────────


def test_verify_with_consume_false_accepts_the_code_without_spending_it():
    s = _Session(_live_row())
    result = _verify(s, "123456", consume=False)
    assert result.ok is True and result.reason == "verified"
    assert not s.wrote("SET consumed_at"), "the code must still be live"


def test_verify_with_consume_true_spends_it():
    s = _Session(_live_row())
    assert _verify(s, "123456", consume=True).ok is True
    assert s.wrote("SET consumed_at = now()")


def test_consume_spends_the_row_verify_left_alone():
    s = _Session(_live_row())
    result = _verify(s, "123456", consume=False)
    asyncio.run(OTP.consume(s, result.otp_id))
    assert s.wrote("SET consumed_at = now()")


def test_consume_only_touches_a_code_that_is_still_live():
    """Guarded in SQL, so a double call cannot move consumed_at a second time and
    rewrite when the code was actually used."""
    s = _Session(_live_row())
    asyncio.run(OTP.consume(s, uuid4()))
    sql = [q for q, _ in s.statements if "consumed_at = now()" in q][0]
    assert "consumed_at IS NULL" in sql


def test_consume_of_nothing_is_a_no_op():
    """verify() returns otp_id None on every failure path; consume must not then issue a
    statement with a null id and update a row it was never given."""
    s = _Session(_live_row())
    asyncio.run(OTP.consume(s, None))
    assert s.statements == []


# ── a rejected code is still not spent ───────────────────────────────────────


def test_a_wrong_code_is_never_consumed_either_way():
    for consume in (True, False):
        s = _Session(_live_row())
        result = _verify(s, "999999", consume=consume)
        assert result.ok is False and result.reason == "mismatch"
        # "SET consumed_at", not "consumed_at" — the SELECT that finds the live code
        # carries "WHERE consumed_at IS NULL", and matching that made this pass for the
        # wrong reason on the first run.
        assert not s.wrote("SET consumed_at")
        assert s.wrote("SET attempts ="), "but the attempt IS counted"


def test_the_attempt_counter_moves_on_a_miss_regardless_of_consume():
    s = _Session(_live_row(attempts=2))
    result = _verify(s, "999999", consume=False)
    assert result.attempts_remaining == 2
    params = [p for q, p in s.statements if "SET attempts =" in q][0]
    assert params["a"] == 3


def test_a_correct_code_does_not_burn_an_attempt():
    s = _Session(_live_row())
    _verify(s, "123456", consume=False)
    assert not s.wrote("SET attempts =")


# ── the digest is what makes any of this safe ────────────────────────────────


def test_two_codes_with_the_same_value_do_not_share_a_digest():
    """Per-row salt. Without it, everyone who draws 418 902 today has an identical hash,
    and one cracked code reads across every account that happened to draw it."""
    assert OTP._digest("418902", "salt-one") != OTP._digest("418902", "salt-two")


def test_the_digest_depends_on_the_pepper(monkeypatch):
    """A stolen copy of the table is not a set of codes: without the server's key the
    million candidates cannot be ground against the stored hashes."""
    with_a = OTP._digest("418902", "same-salt")
    monkeypatch.setattr(OTP, "otp_pepper", lambda: "a-completely-different-pepper-000")
    assert OTP._digest("418902", "same-salt") != with_a


def test_spaces_and_dashes_in_a_typed_code_are_forgiven():
    """People paste "123 456" out of an email. That is the same code."""
    s = _Session(_live_row())
    assert _verify(s, " 123-456 ", consume=False).ok is True
