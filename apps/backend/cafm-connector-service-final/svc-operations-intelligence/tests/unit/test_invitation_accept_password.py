"""Accepting an invitation with a strong password must succeed.

passwords.validate() returns the normalised password and raises WeakPassword for a weak one.
The accept path read its return value as "the problem", so every strong password was
refused as weak_password — with the password itself echoed as the message. This pins the
two halves: a strong password gets past the check, a weak one is refused with a reason
that is not the secret.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from src.engines.auth import invitations


class Passed(Exception):
    """Raised by the fake session on the SECOND query: validation let the password through."""


class FakeResult:
    def __init__(self, row): self._row = row
    def mappings(self): return self
    def first(self): return self._row


class FakeSession:
    def __init__(self, row):
        self.row, self.calls = row, 0
    async def execute(self, *a, **k):
        self.calls += 1
        if self.calls == 1:
            return FakeResult(self.row)
        raise Passed()


def open_invitation():
    return {"id": uuid4(), "organization_id": uuid4(), "email": "d.reyes@example.com",
            "role": "user", "can_ingest": False, "building_ids": [],
            "expires_at": datetime.now(timezone.utc) + timedelta(days=1), "accepted_at": None}


def test_a_strong_password_is_not_refused():
    s = FakeSession(open_invitation())
    with pytest.raises(Passed):
        asyncio.run(invitations.accept(s, token="t", password="Riverside-Only-2026!"))


def test_a_weak_password_is_refused_without_echoing_it():
    s = FakeSession(open_invitation())
    with pytest.raises(invitations.InvitationError) as e:
        asyncio.run(invitations.accept(s, token="t", password="short"))
    assert e.value.reason == "weak_password"
    assert "short" not in e.value.message.split() or "character" in e.value.message.lower()
    assert e.value.message != "short"


def test_the_persons_own_email_is_not_a_password():
    s = FakeSession(open_invitation())
    with pytest.raises(invitations.InvitationError) as e:
        asyncio.run(invitations.accept(s, token="t", password="d.reyes@example.com"))
    assert e.value.reason == "weak_password"
