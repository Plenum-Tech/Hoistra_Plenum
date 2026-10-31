"""Accepting an invitation sets a password and leaves the account pending a confirmation
code — the same OTP mechanism register() uses — rather than marking it verified outright.
The link proved the invitee could reach it once; the code proves they hold the mailbox
right now, the same bar a self-registered account has to clear.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from src.engines.auth import invitations


class FakeResult:
    def __init__(self, row=None, rows=None):
        self._row, self._rows = row, rows or []
    def mappings(self): return self
    def first(self): return self._row
    def all(self): return self._rows


class FakeSession:
    """Answers accept()'s queries by position: invitation row, user row, then anything
    else (UPDATEs/INSERTs) gets an empty result. Captures every statement + params."""
    def __init__(self, invitation_row, user_row):
        self.invitation_row, self.user_row = invitation_row, user_row
        self.calls: list[tuple[str, dict]] = []
        self.committed = False
    async def execute(self, stmt, params=None):
        sql = str(getattr(stmt, "text", stmt))
        self.calls.append((sql, params or {}))
        n = len(self.calls)
        if n == 1:
            return FakeResult(row=self.invitation_row)
        if n == 2:
            return FakeResult(row=self.user_row)
        return FakeResult()
    async def commit(self): self.committed = True

    def update_users_call(self):
        for sql, params in self.calls:
            if "UPDATE plenum_cafm.users" in sql:
                return sql, params
        return None, None


def open_invitation(role="admin"):
    return {
        "id": uuid4(), "organization_id": uuid4(), "email": "d.reyes@example.com",
        "role": role, "can_ingest": False, "building_ids": [],
        "expires_at": datetime.now(timezone.utc) + timedelta(days=1), "accepted_at": None,
    }


def run_accept(monkeypatch, *, sent=True):
    captured = {}

    async def fake_send_code(session, **kw):
        captured.update(kw)
        return {"sent": sent, "rate_limited": False, "delivery_status": "sent" if sent else None}

    monkeypatch.setattr(invitations.account_engine, "_send_code", fake_send_code)
    s = FakeSession(open_invitation(), {"id": uuid4()})
    out = asyncio.run(invitations.accept(
        s, token="t", password="Correct-Horse-Battery-2026!", full_name="Dana Reyes",
        request_ip="203.0.113.9",
    ))
    return s, out, captured


def test_the_account_is_left_pending_verification_not_active(monkeypatch):
    s, out, _ = run_accept(monkeypatch)
    sql, params = s.update_users_call()
    assert "status = 'pending_verification'" in sql
    assert "email_verified = FALSE" in sql
    assert "email_verified_at" not in sql, "no longer set here — verify_email() sets it once the code is entered"
    assert s.committed


def test_a_confirmation_code_is_issued_to_the_invited_address_before_the_commit(monkeypatch):
    s, out, captured = run_accept(monkeypatch)
    assert captured["email"] == "d.reyes@example.com"
    assert captured["purpose"] == "email_verification"
    assert captured["name"] == "Dana Reyes"
    assert captured["request_ip"] == "203.0.113.9"


def test_the_response_tells_the_caller_a_code_is_needed_next(monkeypatch):
    _, out, _ = run_accept(monkeypatch)
    assert out["verification_required"] is True
    assert out["email"] == "d.reyes@example.com"
    assert "otp" in out and "code_length" in out["otp"]


def test_a_rate_limited_code_send_still_completes_the_accept(monkeypatch):
    # otp.py enforces its own per-address limits; accept() must not fail the whole request
    # over a code that will simply be requested again (sign_in()'s own resend, or Resend
    # code on the verify screen) — the password is already set either way.
    s, out, _ = run_accept(monkeypatch, sent=False)
    assert out["ok"] is True
    assert s.committed
