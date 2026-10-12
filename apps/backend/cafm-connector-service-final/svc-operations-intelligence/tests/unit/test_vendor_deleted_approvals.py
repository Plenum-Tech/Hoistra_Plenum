"""A deleted vendor is never offered, emailed or approved about from the Decision queue.

Deleting a vendor with history RETIRES it (Hussain, 12 Oct 2026): the row stays with status
'deleted' so its invoices and closed work still resolve. Everything that offers or sends to a
vendor reads that status. These pin the queue's side: the address lookup the dock's drafts fill
their To line from, the rule that hides pending items whose subject is gone, and the two calls
that send mail — the approve decision and the dock's send. No database: the vendor's status is
answered by a fake session the way the shared check (vendor_identity.is_deleted_vendor) asks it.
"""
from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql

from src.api.routes import approvals as approvals_routes
from src.api.routes import auth as auth_routes
from src.app import app
from src.db import get_session
from src.engines.auth.tokens import Principal
from src.shared import approvals
from src.shared.vendor_identity import deleted_vendor_ids_sql, live_vendor_sql

ORG = UUID("11111111-1111-1111-1111-111111111111")
RETIRED = UUID("33333333-3333-3333-3333-333333333333")
LIVE = UUID("44444444-4444-4444-4444-444444444444")


class _Nested:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Status:
    def __init__(self, value):
        self._value = value

    def scalar(self):
        return self._value


class _Session:
    """Answers the shared status read from ``statuses`` (vendor id -> status) and records every
    statement. ``broken`` makes every read fail, as a lost connection would."""

    def __init__(self, statuses=None, item=None, broken=False):
        self.statuses, self.item, self.broken = statuses or {}, item, broken
        self.sql: list[tuple[str, dict]] = []
        self.added: list = []
        self.committed = False

    def begin_nested(self):
        return _Nested()

    async def execute(self, stmt, params=None):
        self.sql.append((str(stmt), dict(params or {})))
        if self.broken:
            raise RuntimeError("connection reset")
        return _Status(self.statuses.get((params or {}).get("vid")))

    async def get(self, *_a, **_k):
        return self.item

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        pass

    async def commit(self):
        self.committed = True


# ── the refusal every write that links new data to a vendor shares ──────────────────────


async def test_a_live_vendor_is_not_refused():
    s = _Session({str(LIVE): "active"})
    assert await approvals.retired_vendor_refusal(s, LIVE, what="nothing was recorded") is None


async def test_a_retired_vendor_is_refused_and_told_why():
    s = _Session({str(RETIRED): "deleted"})
    out = await approvals.retired_vendor_refusal(s, RETIRED, what="nothing was recorded")
    assert out == {"ok": False, "reason": "vendor_deleted",
                   "error": "This vendor has been deleted, so nothing was recorded."}


async def test_a_status_that_cannot_be_read_refuses_rather_than_guesses():
    # A write that would link new data to a vendor must not go ahead on a guess: the vendor may
    # be the one an admin just deleted.
    out = await approvals.retired_vendor_refusal(_Session(broken=True), RETIRED, what="nothing was recorded")
    assert out["ok"] is False and out["reason"] == "vendor_unreadable"
    assert out["error"] == ("This vendor's record could not be checked just now, so nothing was "
                            "recorded. Try again in a moment.")


async def test_no_vendor_named_is_nothing_to_refuse_and_asks_nothing():
    s = _Session()
    assert await approvals.retired_vendor_refusal(s, None, what="x") is None
    assert await approvals.retired_vendor_refusal(s, "  ", what="x") is None
    assert s.sql == []


def test_the_read_filter_keeps_rows_with_no_vendor_and_drops_retired_ones():
    from sqlalchemy import select

    from src.models.contract_performance import VendorMonthlyScorecard as Card

    sql = " ".join(str(select(Card.id).where(approvals.not_retired_vendor(Card.vendor_id))
                       .compile(dialect=postgresql.dialect())).split())
    assert "vendor_monthly_scorecards.vendor_id IS NULL OR" in sql
    assert ("CAST(plenum_cafm.vendor_monthly_scorecards.vendor_id AS VARCHAR) NOT IN ("
            + deleted_vendor_ids_sql() + ")") in sql


# ── the address a draft is sent to ──────────────────────────────────────────────────────


async def test_a_retired_vendor_has_no_address_to_send_to():
    s = _Session()
    await approvals.vendor_contact(s, vendor_id="V-01", organization_id=ORG, building_ids=None)
    sql, params = s.sql[0]
    assert live_vendor_sql("v") in sql, "the address lookup would offer a deleted vendor's contact"
    assert params["vid"] == "V-01"


# ── pending items about a retired vendor are not offered ────────────────────────────────


class _Recorder:
    def __init__(self):
        self.statements: list = []

    async def execute(self, stmt, *a, **k):
        self.statements.append(stmt)

        class _R:
            def scalars(self):
                return self

            def all(self):
                return []

        return _R()


async def _queue_sql() -> str:
    s = _Recorder()
    await approvals.list_queue(s)
    return " ".join(str(s.statements[0].compile(dialect=postgresql.dialect())).split())


async def test_an_item_about_a_retired_vendor_is_not_on_record():
    sql = await _queue_sql()
    assert ("CAST(plenum_cafm.vendors.id AS VARCHAR) = "
            "CAST(plenum_cafm.approvals_queue_items.related_entity_id AS VARCHAR) AND "
            + live_vendor_sql("vendors")) in sql


async def test_an_item_naming_a_retired_vendor_only_in_its_payload_is_hidden_too():
    # Invoice flags, staleness flags and legacy-id passport shares carry the vendor in the
    # payload alone (related_entity_id is null or names something else).
    sql = await _queue_sql()
    assert ("(plenum_cafm.approvals_queue_items.payload ->> %(payload_1)s) NOT IN ("
            + deleted_vendor_ids_sql() + ")") in sql
    s = _Recorder()
    await approvals.list_queue(s)
    params = s.statements[0].compile(dialect=postgresql.dialect()).params
    assert params["payload_1"] == "vendor_id"


async def test_decided_items_about_a_retired_vendor_stay_history():
    # The payload rule opens with "not pending", so a decided item is never hidden by it, and an
    # item with no vendor in its payload is not hidden either.
    sql = await _queue_sql()
    assert ("(plenum_cafm.approvals_queue_items.status != %(status_2)s OR "
            "(plenum_cafm.approvals_queue_items.payload ->> %(payload_1)s) IS NULL OR "
            "((plenum_cafm.approvals_queue_items.payload ->> %(payload_1)s) NOT IN (") in sql
    s = _Recorder()
    await approvals.list_queue(s)
    assert s.statements[0].compile(dialect=postgresql.dialect()).params["status_2"] == "pending"


# ── the approve decision ────────────────────────────────────────────────────────────────


def _item(**kw):
    base = dict(id=uuid4(), organization_id=ORG, source_feature="A", item_type="vendor_email",
                summary="Renew your gas safe registration", status="pending", payload={},
                related_entity_type="vendor", related_entity_id=RETIRED,
                email_draft={"to": "ops@vendor.example", "subject": "Renewal", "body": "Please renew."},
                pm_notes=None, decided_by=None, decided_at=None, updated_at=None)
    base.update(kw)
    return SimpleNamespace(**base)


async def test_approving_an_item_about_a_retired_vendor_is_refused_and_changes_nothing():
    item = _item()
    s = _Session({str(RETIRED): "deleted"}, item=item)
    out = await approvals.decide_queue_item(s, item.id, decision="approve")
    assert out == {"ok": False, "reason": "vendor_deleted",
                   "error": "This vendor has been deleted, so this can no longer be approved. "
                            "Dismiss it instead."}
    assert item.status == "pending" and item.decided_at is None
    assert s.added == [] and not s.committed, "an email handoff was logged for a deleted vendor"


async def test_a_retired_vendor_named_only_in_the_payload_is_refused_too():
    # A legacy vendor id ("V-01") cannot sit in the uuid related_entity_id; it rides in the payload.
    item = _item(item_type="passport_share", related_entity_id=None, payload={"vendor_id": "V-01"})
    s = _Session({"V-01": "deleted"}, item=item)
    out = await approvals.decide_queue_item(s, item.id, decision="approve")
    assert out["reason"] == "vendor_deleted"


async def test_a_booking_that_would_go_to_a_retired_contractor_is_refused():
    # Approving a booking request emails its top recommended contractor, for real.
    item = _item(item_type="booking_request", related_entity_type="compliance_certificate",
                 related_entity_id=uuid4(),
                 payload={"recommended_vendors": [{"vendor_id": str(RETIRED), "vendor_name": "Acme",
                                                   "email": "ops@acme.example"}]})
    s = _Session({str(RETIRED): "deleted"}, item=item)
    out = await approvals.decide_queue_item(s, item.id, decision="approve")
    assert out["reason"] == "vendor_deleted"
    assert out["error"].startswith("The contractor this booking would go to has been deleted")
    assert item.status == "pending"


def _booking(*recommended):
    return _item(item_type="booking_request", related_entity_type="compliance_certificate",
                 related_entity_id=uuid4(),
                 payload={"recommended_vendors": [
                     {"vendor_id": str(v), "vendor_name": n, "email": f"ops@{n.lower()}.example"}
                     for v, n in recommended]})


async def test_a_booking_whose_top_contractor_was_retired_goes_to_the_next_live_one(monkeypatch):
    # Review, 12 Oct 2026: booking.fulfill_booking_on_approve passes over a retired
    # recommendation and books the next live one; approve refused before it got there.
    booked = []

    async def fulfil(session, item):
        booked.append(item.id)
        return {"ok": True, "booking": {"booking_request_sent": True}}

    from src.engines.compliance import booking

    monkeypatch.setattr(booking, "fulfill_booking_on_approve", fulfil)
    monkeypatch.setattr(approvals, "settings", SimpleNamespace(email_delivery_mode="handoff"))
    item = _booking((RETIRED, "Acme"), (LIVE, "Gough"))
    s = _Session({str(RETIRED): "deleted", str(LIVE): "active"}, item=item)

    async def only_the_item(model, key):   # the certificate it is about is not needed here
        return item if key == item.id else None

    s.get = only_the_item
    out = await approvals.decide_queue_item(s, item.id, decision="approve")
    assert out.get("reason") != "vendor_deleted", out
    assert booked == [item.id]


async def test_a_booking_whose_every_contractor_was_retired_is_refused():
    item = _booking((RETIRED, "Acme"), (UUID(int=5), "Brook"))
    s = _Session({str(RETIRED): "deleted", str(UUID(int=5)): "deleted"}, item=item)
    out = await approvals.decide_queue_item(s, item.id, decision="approve")
    assert out["reason"] == "vendor_deleted" and item.status == "pending"


async def test_the_dock_send_for_a_booking_with_a_live_contractor_left_is_not_refused():
    item = _booking((RETIRED, "Acme"), (LIVE, "Gough"))
    s = _Session({str(RETIRED): "deleted", str(LIVE): "active"}, item=item)
    assert await approvals._retired_vendor_named_by(s, item, what="nothing was sent") is None


async def test_the_write_guard_reads_the_status_for_share():
    # A write racing the delete waits for it, then reads 'deleted' (review, 12 Oct 2026).
    s = _Session({str(LIVE): "active"})
    await approvals.retired_vendor_refusal(s, LIVE, what="x")
    assert s.sql and all(sql.rstrip().upper().endswith("FOR SHARE") for sql, _ in s.sql)


async def test_an_item_about_a_retired_vendor_can_still_be_dismissed(monkeypatch):
    item = _item()
    s = _Session({str(RETIRED): "deleted"}, item=item)
    out = await approvals.decide_queue_item(s, item.id, decision="dismiss")
    assert out["ok"] is True and item.status == "dismissed" and s.committed


async def test_an_item_about_a_live_vendor_is_approved_as_before(monkeypatch):
    monkeypatch.setattr(approvals, "settings", SimpleNamespace(email_delivery_mode="handoff"))
    item = _item(related_entity_id=LIVE)
    s = _Session({str(LIVE): "active"}, item=item)
    out = await approvals.decide_queue_item(s, item.id, decision="approve")
    assert out["ok"] is True and item.status == "handoff_ready"


# ── the dock's send ─────────────────────────────────────────────────────────────────────


async def test_a_send_for_an_item_about_a_retired_vendor_sends_nothing(monkeypatch):
    async def must_not_send(*_a, **_k):
        raise AssertionError("emailed a deleted vendor")

    monkeypatch.setattr(approvals, "send_platform_email", must_not_send)
    monkeypatch.setattr(approvals, "settings", SimpleNamespace(email_delivery_mode="platform_send"))
    item = _item()
    draft_before = dict(item.email_draft)
    s = _Session({str(RETIRED): "deleted"}, item=item)
    out = await approvals.send_approval_email_draft(
        s, to_address="ops@vendor.example", subject="Renewal", body="Please renew.",
        queue_item_id=item.id, organization_id=ORG)
    assert out == {"ok": False, "reason": "vendor_deleted",
                   "error": "This vendor has been deleted, so nothing was sent."}
    assert item.email_draft == draft_before, "the recipient was written onto the item anyway"
    assert s.added == [] and not s.committed


async def test_a_handoff_for_an_item_about_a_retired_vendor_is_refused_too(monkeypatch):
    monkeypatch.setattr(approvals, "settings", SimpleNamespace(email_delivery_mode="handoff"))
    item = _item()
    s = _Session({str(RETIRED): "deleted"}, item=item)
    out = await approvals.send_approval_email_draft(
        s, to_address="ops@vendor.example", subject="Renewal", body="Please renew.",
        queue_item_id=item.id, organization_id=ORG)
    assert out["reason"] == "vendor_deleted"
    assert s.added == [], "an email handoff was logged for a deleted vendor"


# ── the routes ──────────────────────────────────────────────────────────────────────────


@pytest.fixture
def client(monkeypatch):
    async def _session():
        yield _Session()

    async def _principal():
        return Principal(user_id=UUID(int=2), email="pm@example.com", organization_id=ORG,
                         session_id=None, issued_at=__import__("datetime").datetime.now(
                             __import__("datetime").timezone.utc),
                         password_changed_at=0, role="user", can_ingest=True, building_ids=None)

    refusal = {"ok": False, "reason": "vendor_deleted",
               "error": "This vendor has been deleted, so nothing was sent."}

    async def refuse(*_a, **_k):
        return dict(refusal)

    monkeypatch.setattr(approvals_routes.approvals_svc, "send_approval_email_draft", refuse)
    monkeypatch.setattr(approvals_routes.approvals_svc, "decide_queue_item", refuse)
    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[auth_routes.current_principal] = _principal
    try:
        yield TestClient(app), refusal
    finally:
        app.dependency_overrides.clear()


def test_the_send_route_answers_a_deleted_vendor_with_a_conflict(client):
    c, refusal = client
    r = c.post("/api/approvals/send-email", json={"to": "ops@vendor.example", "subject": "Renewal",
                                                  "body": "Please renew.", "queue_item_id": str(uuid4())})
    assert r.status_code == 409
    assert r.json()["detail"] == refusal


def test_the_decide_route_answers_a_deleted_vendor_with_a_conflict(client):
    c, refusal = client
    r = c.post(f"/api/approvals/{uuid4()}/decide", json={"decision": "approve"})
    assert r.status_code == 409
    assert r.json()["detail"] == refusal
