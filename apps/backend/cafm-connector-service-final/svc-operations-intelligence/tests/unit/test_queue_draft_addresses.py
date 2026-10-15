"""Where a Decision-queue draft may be sent, and whose queue item a send may touch.

7 Oct 2026: a click on any Decision-queue card opens its page with the email that resolves it
already drafted in the dock, sent by one press of Approve & send — for real. The drafts the
engines did not write themselves are addressed to a vendor, and the address can only come
from the vendor's own contact record, the way the Assets drafts are addressed: the primary
contact, or the only one, and otherwise no address at all — never a guess.

The same click is the first screen to send with a queue_item_id, so the send route's
lookup of that item has to stay inside the caller's company: it loaded any item by id and
wrote the recipient and the "sent" marks onto it, whoever's it was.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from src.api.routes import approvals as approvals_routes
from src.api.routes import auth as auth_routes
from src.app import app
from src.db import get_session
from src.engines.auth.tokens import Principal
from src.shared import approvals

ORG = UUID("11111111-1111-1111-1111-111111111111")
OTHER = UUID("99999999-9999-9999-9999-999999999999")
B1 = UUID("22222222-2222-2222-2222-222222222222")


class _Nested:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def first(self):
        return self._rows[0] if self._rows else None


class _Session:
    """Answers the vendor read with the rows it was given and records what it was asked."""

    def __init__(self, rows=None, raises=None):
        self.rows, self.raises, self.calls = rows or [], raises, []

    def begin_nested(self):
        return _Nested()

    async def execute(self, stmt, params=None):
        self.calls.append((str(stmt), params or {}))
        if self.raises:
            raise self.raises
        return _Result(self.rows)


@pytest.fixture
def contacts(monkeypatch):
    """The vendor_contacts rule (primary, else the only one, else candidates) has its own
    tests; here it answers per vendor id and records who it was asked about."""
    from src.engines.energy import asset_intelligence as ai

    asked: list = []
    book = {
        "v-1": {"email": "ops@pennardfire.co.uk", "candidates": []},
        "v-2": {"email": None, "candidates": ["a@ostley.co.uk", "b@ostley.co.uk"]},
    }

    async def fake(session, vendor_id):
        asked.append(vendor_id)
        return book.get(vendor_id, {"email": None, "candidates": []})

    monkeypatch.setattr(ai, "vendor_contacts", fake)
    return asked


# ── the read ────────────────────────────────────────────────────────────────────────────


async def test_a_vendor_in_the_company_answers_with_its_contact_on_record(contacts):
    s = _Session(rows=[{"id": "v-1", "vendor_name": "Pennard Fire Services", "organization_id": str(ORG)}])
    out = await approvals.vendor_contact(s, vendor_id="v-1", organization_id=ORG, building_ids=None)
    assert out == {"ok": True, "vendor": {"id": "v-1", "name": "Pennard Fire Services"},
                   "email": "ops@pennardfire.co.uk", "candidates": [], "written": False}
    sql, params = s.calls[0]
    assert "plenum_cafm.vendors" in sql and params["vid"] == "v-1"
    assert contacts == ["v-1"]


async def test_several_contacts_and_none_primary_leave_the_address_to_the_reader(contacts):
    s = _Session(rows=[{"id": "v-2", "vendor_name": "Ostley Power Services", "organization_id": str(ORG)}])
    out = await approvals.vendor_contact(s, vendor_id="v-2", organization_id=ORG, building_ids=None)
    assert out["email"] is None
    assert out["candidates"] == ["a@ostley.co.uk", "b@ostley.co.uk"]


async def test_another_companys_vendor_answers_as_if_it_did_not_exist(contacts):
    s = _Session(rows=[{"id": "v-1", "vendor_name": "Pennard Fire Services", "organization_id": str(OTHER)}])
    out = await approvals.vendor_contact(s, vendor_id="v-1", organization_id=ORG, building_ids=None)
    assert out == {"ok": False, "reason": "not_found"}
    assert contacts == [], "another company's contact book was read"


async def test_a_vendor_with_no_company_on_record_is_not_handed_to_a_company(contacts):
    """An address is the one thing this read gives out; a vendor row that names no company
    cannot be shown to belong to the caller's."""
    s = _Session(rows=[{"id": "v-1", "vendor_name": "Pennard Fire Services", "organization_id": None}])
    out = await approvals.vendor_contact(s, vendor_id="v-1", organization_id=ORG, building_ids=None)
    assert out == {"ok": False, "reason": "not_found"}
    assert contacts == []


async def test_an_unknown_vendor_is_not_found(contacts):
    out = await approvals.vendor_contact(_Session(rows=[]), vendor_id="nope", organization_id=ORG,
                                         building_ids=None)
    assert out == {"ok": False, "reason": "not_found"}
    assert contacts == []


async def test_no_vendor_id_asks_nothing(contacts):
    s = _Session(rows=[{"id": "v-1", "vendor_name": "x", "organization_id": str(ORG)}])
    out = await approvals.vendor_contact(s, vendor_id="  ", organization_id=ORG, building_ids=None)
    assert out == {"ok": False, "reason": "not_found"}
    assert s.calls == []


async def test_a_user_restricted_to_buildings_only_reaches_vendors_working_on_them(contacts):
    s = _Session(rows=[{"id": "v-1", "vendor_name": "Pennard Fire Services", "organization_id": str(ORG)}])
    await approvals.vendor_contact(s, vendor_id="v-1", organization_id=ORG, building_ids=(B1,))
    sql, params = s.calls[0]
    assert "v.id::text IN (SELECT v.vendor_id::text FROM" in sql
    assert params["vc_building_ids"] == [str(B1)]


async def test_a_user_restricted_to_no_buildings_reaches_no_vendor(contacts):
    s = _Session(rows=[])
    await approvals.vendor_contact(s, vendor_id="v-1", organization_id=ORG, building_ids=())
    assert " AND FALSE" in s.calls[0][0]


async def test_a_caller_with_no_company_is_not_handed_any_vendors_address(contacts):
    """No company in scope (ORG_ID unset, scope enforcement off) is not "every company": only a
    superadmin reads across companies."""
    s = _Session(rows=[{"id": "v-1", "vendor_name": "Pennard Fire Services", "organization_id": str(OTHER)}])
    out = await approvals.vendor_contact(s, vendor_id="v-1", organization_id=None, building_ids=None)
    assert out == {"ok": False, "reason": "not_found"}
    assert s.calls == [] and contacts == []
    out = await approvals.vendor_contact(s, vendor_id="v-1", organization_id=None, building_ids=None,
                                         is_superadmin=True)
    assert out["email"] == "ops@pennardfire.co.uk"


async def test_a_vendor_table_that_cannot_be_read_says_so_rather_than_not_found(contacts):
    s = _Session(raises=RuntimeError('relation "plenum_cafm.vendors" does not exist'))
    out = await approvals.vendor_contact(s, vendor_id="v-1", organization_id=ORG, building_ids=None)
    assert out == {"ok": False, "reason": "unreadable"}


# ── the route ───────────────────────────────────────────────────────────────────────────


@pytest.fixture
def client(monkeypatch):
    seen: dict = {}

    async def _session():
        yield _Session()

    def principal(building_ids=None):
        async def _p():
            return Principal(user_id=UUID(int=2), email="pm@example.com", organization_id=ORG,
                             session_id=None, issued_at=datetime.now(timezone.utc),
                             password_changed_at=0, role="user", can_ingest=True,
                             building_ids=building_ids)
        return _p

    async def fake_contact(session, *, vendor_id, organization_id, building_ids, is_superadmin=False):
        seen.update(vendor_id=vendor_id, organization_id=organization_id, building_ids=building_ids)
        if vendor_id == "v-1":
            return {"ok": True, "vendor": {"id": "v-1", "name": "Pennard Fire Services"},
                    "email": "ops@pennardfire.co.uk", "candidates": [], "written": False}
        if vendor_id == "broken":
            return {"ok": False, "reason": "unreadable"}
        return {"ok": False, "reason": "not_found"}

    monkeypatch.setattr(approvals_routes.approvals_svc, "vendor_contact", fake_contact)
    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[auth_routes.current_principal] = principal()
    try:
        yield TestClient(app), seen, principal
    finally:
        app.dependency_overrides.clear()


def test_the_route_answers_the_vendors_address_in_the_callers_company(client):
    c, seen, _ = client
    r = c.get("/api/approvals/vendor-contact", params={"vendor_id": "v-1"})
    assert r.status_code == 200
    assert r.json()["email"] == "ops@pennardfire.co.uk"
    assert seen == {"vendor_id": "v-1", "organization_id": ORG, "building_ids": None}


def test_the_route_passes_a_restricted_users_buildings(client):
    c, seen, principal = client
    app.dependency_overrides[auth_routes.current_principal] = principal((B1,))
    c.get("/api/approvals/vendor-contact", params={"vendor_id": "v-1"})
    assert seen["building_ids"] == (B1,)


def test_a_vendor_the_caller_cannot_see_is_a_404(client):
    c, _, _ = client
    r = c.get("/api/approvals/vendor-contact", params={"vendor_id": "v-elsewhere"})
    assert r.status_code == 404
    assert r.json()["detail"]["reason"] == "not_found"


def test_a_read_that_failed_is_a_503_not_a_missing_vendor(client):
    c, _, _ = client
    r = c.get("/api/approvals/vendor-contact", params={"vendor_id": "broken"})
    assert r.status_code == 503
    assert r.json()["detail"]["reason"] == "unreadable"


def test_naming_another_company_is_refused(client):
    c, seen, _ = client
    r = c.get("/api/approvals/vendor-contact", params={"vendor_id": "v-1", "organization_id": str(OTHER)})
    assert r.status_code == 403
    assert seen == {}, "the read ran for a company the caller is not in"


# ── the send ────────────────────────────────────────────────────────────────────────────


async def test_a_send_naming_another_companys_queue_item_is_refused_and_touches_nothing(monkeypatch):
    item = SimpleNamespace(id=uuid4(), organization_id=OTHER, email_draft={"subject": "Renewal", "to": "x@y.z"},
                           payload={}, source_feature="A", related_entity_type="vendor",
                           related_entity_id=uuid4(), updated_at=None)

    class S:
        added: list = []

        def add(self, row):
            self.added.append(row)

        async def get(self, *_a, **_k):
            return item

        async def commit(self):
            raise AssertionError("committed a send against another company's queue item")

    async def must_not_send(*_a, **_k):
        raise AssertionError("sent mail for another company's queue item")

    monkeypatch.setattr(approvals, "send_platform_email", must_not_send)
    monkeypatch.setattr(approvals, "settings", SimpleNamespace(email_delivery_mode="platform_send"))
    out = await approvals.send_approval_email_draft(
        S(), to_address="ops@pennardfire.co.uk", subject="Renewal", body="Please renew.",
        queue_item_id=item.id, organization_id=ORG,
    )
    assert out["ok"] is False
    assert item.email_draft == {"subject": "Renewal", "to": "x@y.z"}, "the other company's draft was rewritten"
    assert item.payload == {}


async def test_a_send_naming_a_queue_item_with_no_company_is_refused_to_a_company(monkeypatch):
    """A company's queue lists only items carrying its id, so an item naming no company was never
    on its screen — and what follows would write onto it."""
    item = SimpleNamespace(id=uuid4(), organization_id=None, email_draft={"subject": "Renewal"},
                           payload={}, source_feature="A", related_entity_type=None,
                           related_entity_id=None, updated_at=None)

    class S:
        def add(self, row):
            raise AssertionError("logged a send against an item with no company")

        async def get(self, *_a, **_k):
            return item

        async def commit(self):
            raise AssertionError("committed")

    async def must_not_send(*_a, **_k):
        raise AssertionError("sent")

    monkeypatch.setattr(approvals, "send_platform_email", must_not_send)
    monkeypatch.setattr(approvals, "settings", SimpleNamespace(email_delivery_mode="platform_send"))
    out = await approvals.send_approval_email_draft(
        S(), to_address="ops@pennardfire.co.uk", subject="Renewal", body="Please renew.",
        queue_item_id=item.id, organization_id=ORG,
    )
    assert out["ok"] is False
    assert item.payload == {}


async def test_the_sibling_sync_after_a_send_stays_inside_the_items_company(monkeypatch):
    """The send marks the other pending items about the same record as sent too. A vendor row is
    not always one company's, so that sweep must not reach another company's items."""
    item = SimpleNamespace(id=uuid4(), organization_id=ORG, email_draft={"subject": "Renewal"},
                           payload={}, source_feature="A", related_entity_type="vendor",
                           related_entity_id=uuid4(), updated_at=None)
    swept: list = []

    class _Scalars:
        def all(self):
            return []

    class _R:
        def scalars(self):
            return _Scalars()

    class S:
        def add(self, row):
            pass

        async def get(self, *_a, **_k):
            return item

        async def execute(self, stmt, *_a, **_k):
            swept.append(str(stmt.compile(compile_kwargs={"literal_binds": False})))
            return _R()

        async def commit(self):
            pass

    async def fake_send(session, **kw):
        return {"ok": True, "status": "sent", "email_id": "e1"}

    async def quiet(*_a, **_k):
        return None

    monkeypatch.setattr(approvals, "send_platform_email", fake_send)
    monkeypatch.setattr(approvals, "write_audit", quiet)
    monkeypatch.setattr(approvals, "apply_pm_action_track", quiet)
    monkeypatch.setattr(approvals, "settings", SimpleNamespace(email_delivery_mode="platform_send"))
    await approvals.send_approval_email_draft(
        S(), to_address="ops@pennardfire.co.uk", subject="Renewal", body="Please renew.",
        queue_item_id=item.id, organization_id=ORG,
    )
    assert swept, "the sibling sweep ran"
    assert "approvals_queue_items.organization_id = " in swept[0].replace("plenum_cafm.", "")


async def test_a_send_naming_the_callers_own_queue_item_is_recorded_on_it(monkeypatch):
    item = SimpleNamespace(id=uuid4(), organization_id=ORG, email_draft={"subject": "Renewal"},
                           payload={}, source_feature="A", related_entity_type=None,
                           related_entity_id=None, updated_at=None)

    class S:
        def add(self, row):
            pass

        async def get(self, *_a, **_k):
            return item

        async def commit(self):
            pass

    async def fake_send(session, **kw):
        return {"ok": True, "status": "sent", "email_id": "e1"}

    async def quiet(*_a, **_k):
        return None

    monkeypatch.setattr(approvals, "send_platform_email", fake_send)
    monkeypatch.setattr(approvals, "write_audit", quiet)
    monkeypatch.setattr(approvals, "apply_pm_action_track", quiet)
    monkeypatch.setattr(approvals, "settings", SimpleNamespace(email_delivery_mode="platform_send"))
    out = await approvals.send_approval_email_draft(
        S(), to_address="ops@pennardfire.co.uk", subject="Renewal", body="Please renew.",
        queue_item_id=item.id, organization_id=ORG,
    )
    assert out["status"] == "sent"
    assert item.payload["email_sent_to"] == "ops@pennardfire.co.uk"
