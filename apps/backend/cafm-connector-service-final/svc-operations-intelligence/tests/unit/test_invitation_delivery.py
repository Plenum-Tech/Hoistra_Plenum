"""An invitation is only "sent" when the email actually left.

send_platform_email() answers ok=True for a real send AND for a dry-run AND for "no
transport configured" — the last two mean nobody received anything. create() used to test
ok, so with EMAIL_DRY_RUN=false and a misconfigured transport the UI said "Invitation sent"
while the invitee got nothing and the one usable link was discarded. It now tests
status == "sent", hands the link back otherwise, and refuses to email a relative link when
PUBLIC_APP_URL is blank (a mail client cannot resolve one).
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

from src.engines.auth import invitations


class FakeResult:
    def __init__(self, row=None, scalar=None):
        self._row, self._scalar = row, scalar
    def mappings(self): return self
    def first(self): return self._row
    def scalar(self): return self._scalar


class FakeSession:
    """Answers create()'s five queries in order: no existing user, new user id, supersede
    old invitations, new invitation id, company name."""
    def __init__(self):
        self.calls = 0
        self.committed = False
    async def execute(self, *a, **k):
        self.calls += 1
        return {
            1: FakeResult(row=None),
            2: FakeResult(scalar=uuid4()),
            3: FakeResult(),
            4: FakeResult(scalar=uuid4()),
            5: FakeResult(scalar="Acme Facilities"),
        }.get(self.calls, FakeResult())
    async def commit(self): self.committed = True


def _run(monkeypatch, *, public_app_url, dry_run, delivery):
    """delivery: what the fake send_platform_email returns; None = it must not be called."""
    sent_with = {}

    async def fake_send(session, **kw):
        sent_with.update(kw)
        assert delivery is not None, "send_platform_email must not be called here"
        return dict(delivery)

    monkeypatch.setattr(invitations, "send_platform_email", fake_send)
    monkeypatch.setattr(invitations, "settings",
                        SimpleNamespace(public_app_url=public_app_url, email_dry_run=dry_run))
    s = FakeSession()
    out = asyncio.run(invitations.create(
        s, organization_id=uuid4(), email="D.Reyes@Example.com", full_name="D Reyes",
        role="user", can_ingest=False, building_ids=[], invited_by=None,
    ))
    assert s.committed
    return out, sent_with


def test_a_real_send_is_delivered_and_keeps_the_link_out_of_the_receipt(monkeypatch):
    out, sent_with = _run(monkeypatch, public_app_url="https://app.example.com", dry_run=False,
                          delivery={"ok": True, "status": "sent", "provider": "graph"})
    assert out["delivered"] is True
    assert "accept_url" not in out
    assert sent_with["to_address"] == "d.reyes@example.com"
    assert "https://app.example.com/accept-invitation?token=" in sent_with["body"]
    assert "redacted" in sent_with["log_body"] and "token=" not in sent_with["log_body"]


def test_dry_run_hands_the_link_back(monkeypatch):
    out, _ = _run(monkeypatch, public_app_url="https://app.example.com", dry_run=True,
                  delivery={"ok": True, "status": "dry_run"})
    assert out["delivered"] is False
    assert out["accept_url"].startswith("https://app.example.com/accept-invitation?token=")


def test_no_transport_is_NOT_a_delivery_even_though_the_sender_says_ok(monkeypatch):
    # The old bug: EMAIL_DRY_RUN=false, Graph/SMTP not configured -> sender returns
    # ok=True status=dry_run -> create() reported it as sent and dropped the link.
    out, _ = _run(monkeypatch, public_app_url="https://app.example.com", dry_run=False,
                  delivery={"ok": True, "status": "dry_run"})
    assert out["delivered"] is False
    assert "accept_url" in out, "the only copy of the link must come back to the inviter"


def test_a_failed_send_hands_the_link_back(monkeypatch):
    out, _ = _run(monkeypatch, public_app_url="https://app.example.com", dry_run=False,
                  delivery={"ok": False, "status": "failed", "error": "ErrorAccessDenied"})
    assert out["delivered"] is False
    assert "accept_url" in out


def test_blank_public_app_url_refuses_to_email_a_relative_link(monkeypatch):
    out, sent_with = _run(monkeypatch, public_app_url="", dry_run=False, delivery=None)
    assert sent_with == {}, "nothing was emailed"
    assert out["delivered"] is False
    assert out["email_sent"]["status"] == "not_sent"
    assert "PUBLIC_APP_URL" in out["note"]
    assert out["accept_url"].startswith("/accept-invitation?token=")


def test_blank_public_app_url_in_dry_run_still_dry_runs(monkeypatch):
    # Nothing leaves in dry-run, so the relative form is harmless and the send is logged.
    out, sent_with = _run(monkeypatch, public_app_url="", dry_run=True,
                          delivery={"ok": True, "status": "dry_run"})
    assert sent_with, "dry-run still records the email"
    assert out["delivered"] is False
    assert "accept_url" in out
