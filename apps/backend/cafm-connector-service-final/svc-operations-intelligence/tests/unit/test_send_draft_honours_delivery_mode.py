"""The dock's send button obeys EMAIL_DELIVERY_MODE like every other send.

PRD Q1 made handoff the default: the platform drafts, the PM sends from their own client.
decide_queue_item() honours that. send_approval_email_draft() — what the dock's
"Approve & send" posts to — went straight to send_platform_email whatever the mode said, so
a deployment that had chosen handoff still sent vendor mail from the shared mailbox the
moment someone pressed the button. config.py's validator frames a wrong mode as "an outage
nobody is told about"; a route that ignores the mode is the same outage in reverse.
"""
from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.shared import approvals


class FakeSession:
    def __init__(self):
        self.added: list = []
        self.committed = False

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        pass

    async def commit(self):
        self.committed = True

    async def get(self, *_a, **_k):
        return None


@pytest.fixture
def quiet_audit(monkeypatch):
    audits: list[dict] = []

    async def fake_audit(session, **kw):
        audits.append(kw)

    monkeypatch.setattr(approvals, "write_audit", fake_audit)
    return audits


def _settings(mode: str):
    return SimpleNamespace(email_delivery_mode=mode, default_pm_email="pm@example.com",
                           email_dry_run=False, smtp_host="", smtp_user="", smtp_password="")


async def test_handoff_mode_hands_back_a_mailto_and_sends_nothing(monkeypatch, quiet_audit):
    monkeypatch.setattr(approvals, "settings", _settings("handoff"))

    async def must_not_send(*_a, **_k):
        raise AssertionError("send_platform_email was called in handoff mode")

    monkeypatch.setattr(approvals, "send_platform_email", must_not_send)
    s = FakeSession()
    out = await approvals.send_approval_email_draft(
        s, to_address="ops@vendor.example", subject="Renewal", body="Please renew.",
        organization_id=uuid4(),
    )
    assert out["ok"] is True
    assert out["status"] == "handoff"
    assert out["handoff"]["mailto_uri"].startswith("mailto:ops@vendor.example?subject=Renewal")
    assert out["email_sent_to"] is None, "nothing was sent, so nothing was sent to anyone"
    # The handoff is on the record like a send would be: one ops_email_log row, one audit row.
    assert any(getattr(r, "status", None) == "handoff" for r in s.added)
    assert s.committed
    assert quiet_audit and quiet_audit[0]["action_type"] == "approvals_queue.email_handoff"


async def test_handoff_does_not_stamp_the_queue_item_as_sent(monkeypatch, quiet_audit):
    """queue_item_to_dict() falls back to email_draft.last_sent_at for email_sent_at, so a
    stamp written before the mode branch made the Approvals rail show a sent timestamp
    beside email_sent=false for mail that never left the platform."""
    monkeypatch.setattr(approvals, "settings", _settings("handoff"))

    async def must_not_send(*_a, **_k):
        raise AssertionError("send_platform_email was called in handoff mode")

    monkeypatch.setattr(approvals, "send_platform_email", must_not_send)
    item = SimpleNamespace(id=uuid4(), organization_id=uuid4(), email_draft={"subject": "Renewal"},
                           payload={}, source_feature="A", updated_at=None)

    class SessionWithItem(FakeSession):
        async def get(self, *_a, **_k):
            return item

    out = await approvals.send_approval_email_draft(
        SessionWithItem(), to_address="ops@vendor.example", subject="Renewal", body="Please renew.",
        queue_item_id=item.id,
    )
    assert out["status"] == "handoff"
    assert item.email_draft["to"] == "ops@vendor.example", "the chosen recipient is still kept"
    assert "last_sent_at" not in item.email_draft, "a handoff was stamped as a send"
    assert item.email_draft["last_handoff_at"]


async def test_platform_send_mode_still_sends(monkeypatch, quiet_audit):
    monkeypatch.setattr(approvals, "settings", _settings("platform_send"))
    sent_with: dict = {}

    async def fake_send(session, **kw):
        sent_with.update(kw)
        return {"ok": True, "status": "sent", "email_id": "e1"}

    monkeypatch.setattr(approvals, "send_platform_email", fake_send)
    out = await approvals.send_approval_email_draft(
        FakeSession(), to_address="ops@vendor.example", subject="Renewal", body="Please renew.",
        organization_id=uuid4(),
    )
    assert out["status"] == "sent"
    assert out["email_sent_to"] == "ops@vendor.example"
    assert sent_with["to_address"] == "ops@vendor.example"


async def test_a_refused_draft_is_refused_before_the_mode_matters(monkeypatch, quiet_audit):
    monkeypatch.setattr(approvals, "settings", _settings("handoff"))
    out = await approvals.send_approval_email_draft(
        FakeSession(), to_address="not-an-address", subject="x", body="y",
    )
    assert out == {"ok": False, "error": "Enter a valid PM email address"}
