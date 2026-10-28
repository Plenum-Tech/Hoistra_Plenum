"""The renewal-email route stays inside the caller's company, and queues one draft per certificate.

28 Oct 2026. POST /api/compliance/certificates/renewal-email used to:

* load the certificate by id or number with no company check, so a signed-in user of one
  company could queue a renewal email in another company's approvals queue, addressed to that
  company's vendor;
* queue another approvals item (and another audit row) on every click, so the Decision queue
  filled with copies and the same email could be sent twice;
* fall back to the default PM address when the vendor had no contact, without saying so.

Now another company's certificate is 404 exactly like an unknown id and nothing is written; a
pending renewal draft for the same certificate is returned (``reused: true``) with no new
rows; ``to_is_fallback`` says when the draft goes to the default PM address.

No database: the fake records every statement and every row added, and answers the
approvals lookup from a list the test controls.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace
from uuid import UUID

import pytest
from sqlalchemy.dialects import postgresql

from src.config import settings
from src.engines.compliance import certificates as certs_mod
from src.engines.compliance import contractors
from src.models import ApprovalsQueueItem, OpsAuditLog

ORG_A = UUID("00000000-0000-0000-0000-00000000000a")
ORG_B = UUID("00000000-0000-0000-0000-00000000000b")
CERT_ID = UUID(int=7)
VENDOR = UUID("cccccccc-0000-0000-0000-000000000003")
BUILDING = UUID("dddddddd-0000-0000-0000-000000000004")
OTHER_BUILDING = UUID("eeeeeeee-0000-0000-0000-000000000005")


class _Result:
    def __init__(self, rows=()):
        self._rows = list(rows)

    def mappings(self):
        return self

    def scalars(self):
        return self

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return list(self._rows)

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


class _Nested:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class FakeDB:
    """One certificate (by id and by number), an optional vendor contact, and the pending
    approvals items a lookup would find. Every statement is recorded in Postgres SQL."""

    def __init__(self, cert=None, contact=None, pending=()):
        self.cert = cert
        self.contact = contact
        self.pending = list(pending)
        self.statements: list[tuple[str, dict]] = []
        self.added: list = []
        self.committed = 0

    def begin_nested(self):
        return _Nested()

    async def execute(self, stmt, params=None):
        try:
            compiled = stmt.compile(dialect=postgresql.dialect())
            sql = " ".join(str(compiled).split())
            p = {**dict(compiled.params or {}), **dict(params or {})}
        except Exception:  # noqa: BLE001 — a text() the dialect cannot compile alone
            sql, p = " ".join(str(stmt).split()), dict(params or {})
        self.statements.append((sql, p))
        if "plenum_cafm.vendors" in sql:
            return _Result([self.contact] if self.contact else [])
        if "approvals_queue_items" in sql:
            return _Result(self.pending)
        if "compliance_certificates" in sql:
            # Answers whatever the statement asks for: the engine must not rely on the SQL
            # filter alone to keep another company's certificate out.
            return _Result([self.cert] if self.cert is not None else [])
        return _Result([])

    async def get(self, model, key):
        if self.cert is not None and key == self.cert.id:
            return self.cert
        return None

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        return None

    async def commit(self):
        self.committed += 1

    def sql(self, needle: str) -> list[str]:
        return [s for s, _ in self.statements if needle in s]


def _cert(**kw):
    base = dict(id=CERT_ID, organization_id=ORG_A, org_id=ORG_A, raw_metadata={},
                vendor_id=VENDOR, cert_scope="Vendor", site_id=None, building_id=None,
                certificate_type_code="GAS_SAFE", cert_type="GAS_SAFE",
                certificate_number="GS-1", country_code="UK", days_to_expiry=20,
                status="Expiring Soon", expiry_date=date(2026, 11, 17))
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.fixture(autouse=True)
def _pack(monkeypatch):
    async def pack(*a, **k):
        return SimpleNamespace(certificate_type_name="Gas Safe", verification_url=None,
                               required_contractor_accreditation="Gas Safe registered")

    async def live_vendor(*a, **k):
        return None

    async def site(session, site_id):
        return {"building_name": "Ashgrove House"}

    monkeypatch.setattr(certs_mod, "get_pack_type", pack)
    monkeypatch.setattr(certs_mod, "retired_vendor_refusal", live_vendor)
    monkeypatch.setattr(contractors, "resolve_site_display", site)


def _queued(db):
    return [r for r in db.added if isinstance(r, ApprovalsQueueItem)]


def _audited(db):
    return [r for r in db.added if isinstance(r, OpsAuditLog)]


# ── the happy path is unchanged, plus two new fields ─────────────────────────────────────


async def test_a_first_click_queues_one_item_and_answers_every_existing_field():
    db = FakeDB(_cert(), contact={"vendor_name": "Brook Gas", "email": "office@brook.example"})
    out = await certs_mod.draft_renewal_email(db, certificate_id=CERT_ID, organization_id=ORG_A)

    assert out["ok"] is True
    [item] = _queued(db)
    assert len(_audited(db)) == 1 and db.committed == 1
    assert out["queue_item_id"] == str(item.id) and out["certificate_id"] == str(CERT_ID)
    draft = out["email_draft"]
    assert draft["to"] == "office@brook.example"
    assert draft["subject"] == "[ACTION] Accreditation renewal — Gas Safe — Brook Gas"
    assert {"to", "subject", "body", "status", "days_to_expiry", "mailto_uri"} <= set(draft)
    [chat] = out["email_drafts"]
    assert chat["queue_item_id"] == str(item.id) and chat["certificate_id"] == str(CERT_ID)
    assert chat["summary"] == "Renewal email — Gas Safe (GS-1)" and chat["escalation"] is False
    assert out["message"] == (
        "Renewal email drafted and queued — review and Send from the Approvals card.")
    assert out["reused"] is False and out["to_is_fallback"] is False
    assert item.status == "pending" and item.item_type == "vendor_email"
    assert item.related_entity_type == "compliance_certificate"
    assert item.related_entity_id == CERT_ID and item.organization_id == ORG_A
    assert item.payload == {"certificate_id": str(CERT_ID), "renewal": True,
                            "no_work_order": True, "to_is_fallback": False}


async def test_a_caller_with_no_scope_given_still_drafts_as_before():
    # The engine's old signature (no company) keeps working for in-process callers.
    db = FakeDB(_cert(), contact={"vendor_name": "Brook Gas", "email": "office@brook.example"})
    out = await certs_mod.draft_renewal_email(db, certificate_id=CERT_ID)
    assert out["ok"] is True and len(_queued(db)) == 1


# ── the company boundary ─────────────────────────────────────────────────────────────────


async def test_another_companys_certificate_by_id_is_not_found_and_nothing_is_written():
    db = FakeDB(_cert(organization_id=ORG_B, org_id=ORG_B),
                contact={"vendor_name": "Brook Gas", "email": "office@brook.example"})
    out = await certs_mod.draft_renewal_email(db, certificate_id=CERT_ID, organization_id=ORG_A)

    unknown = await certs_mod.draft_renewal_email(
        FakeDB(None), certificate_id=UUID(int=99), organization_id=ORG_A)
    assert out == unknown, "the same answer as an unknown id"
    assert out["ok"] is False and out["reason"] == "not_found"
    assert db.added == [] and db.committed == 0
    assert db.sql("approvals_queue_items") == [] and db.sql("plenum_cafm.vendors") == []


async def test_a_certificate_with_no_company_is_not_shared():
    db = FakeDB(_cert(organization_id=None, org_id=None))
    out = await certs_mod.draft_renewal_email(db, certificate_id=CERT_ID, organization_id=ORG_A)
    assert out["reason"] == "not_found" and db.added == [] and db.committed == 0


async def test_the_register_rule_org_id_alone_is_the_callers():
    # list_certificates shows a row whose org_id (not organization_id) is the caller's; the
    # renewal route accepts what the register shows.
    db = FakeDB(_cert(organization_id=None, org_id=ORG_A))
    out = await certs_mod.draft_renewal_email(db, certificate_id=CERT_ID, organization_id=ORG_A)
    assert out["ok"] is True and len(_queued(db)) == 1


async def test_a_number_lookup_searches_only_the_callers_register():
    db = FakeDB(_cert(organization_id=ORG_B, org_id=ORG_B))
    out = await certs_mod.draft_renewal_email(
        db, certificate_number="GS-1", organization_id=ORG_A)

    assert out["reason"] == "not_found" and db.added == [] and db.committed == 0
    [lookup] = db.sql("FROM plenum_cafm.compliance_certificates")
    assert "compliance_certificates.organization_id = " in lookup
    assert "compliance_certificates.org_id = " in lookup


async def test_a_restricted_caller_cannot_draft_for_a_building_they_are_not_allocated():
    cert = _cert(cert_scope="Building", vendor_id=None, building_id=OTHER_BUILDING)
    db = FakeDB(cert)
    out = await certs_mod.draft_renewal_email(
        db, certificate_id=CERT_ID, organization_id=ORG_A, building_ids=(BUILDING,))
    assert out["reason"] == "not_found" and db.added == [] and db.committed == 0

    allowed = FakeDB(_cert(cert_scope="Building", vendor_id=None, building_id=BUILDING))
    ok = await certs_mod.draft_renewal_email(
        allowed, certificate_id=CERT_ID, organization_id=ORG_A, building_ids=(BUILDING,))
    assert ok["ok"] is True

    # A vendor accreditation names no building and is on a restricted caller's register.
    vendor = FakeDB(_cert(), contact={"vendor_name": "Brook Gas", "email": "o@brook.example"})
    v = await certs_mod.draft_renewal_email(
        vendor, certificate_id=CERT_ID, organization_id=ORG_A, building_ids=(BUILDING,))
    assert v["ok"] is True


# ── one pending draft per certificate ────────────────────────────────────────────────────


async def test_a_second_click_returns_the_pending_item_and_writes_nothing():
    queued_draft = {"to": "office@brook.example", "subject": "[ACTION] queued subject",
                    "body": "queued body", "status": "Expiring Soon", "days_to_expiry": 21,
                    "mailto_uri": "mailto:office@brook.example"}
    existing = SimpleNamespace(
        id=UUID(int=55), summary="Renewal email — Gas Safe (GS-1)", email_draft=queued_draft,
        payload={"certificate_id": str(CERT_ID), "renewal": True, "no_work_order": True,
                 "to_is_fallback": False})
    db = FakeDB(_cert(), contact={"vendor_name": "Brook Gas", "email": "office@brook.example"},
                pending=[existing])
    out = await certs_mod.draft_renewal_email(db, certificate_id=CERT_ID, organization_id=ORG_A)

    assert out["ok"] is True and out["reused"] is True
    assert out["queue_item_id"] == str(UUID(int=55))
    assert out["email_draft"] == queued_draft, "the draft that is queued, not a new one"
    [chat] = out["email_drafts"]
    assert chat["queue_item_id"] == str(UUID(int=55)) and chat["subject"] == "[ACTION] queued subject"
    assert chat["certificate_id"] == str(CERT_ID) and chat["summary"] == existing.summary
    assert out["to_is_fallback"] is False
    assert db.added == [], "no second item and no second audit row"
    assert db.committed == 0


async def test_the_reuse_lookup_is_this_routes_pending_item_for_this_certificate():
    db = FakeDB(_cert(), contact={"vendor_name": "Brook Gas", "email": "office@brook.example"})
    await certs_mod.draft_renewal_email(db, certificate_id=CERT_ID, organization_id=ORG_A)

    order = [s for s, _ in db.statements]
    lock = next(i for i, s in enumerate(order) if "pg_advisory_xact_lock" in s)
    look = next(i for i, s in enumerate(order) if "approvals_queue_items" in s)
    assert lock < look, "a double click waits for the first insert, then finds it"
    [(sql, params)] = [(s, p) for s, p in db.statements if "approvals_queue_items" in s]
    assert "approvals_queue_items.status = %(status_1)s" in sql and params["status_1"] == "pending"
    assert params["item_type_1"] == "vendor_email"
    assert params["related_entity_type_1"] == "compliance_certificate"
    assert params["related_entity_id_1"] == CERT_ID
    assert "approvals_queue_items.payload ->> %(payload_1)s) = %(param_1)s" in sql
    assert params["payload_1"] == "renewal" and params["param_1"] == "true"
    [(_, lock_params)] = [(s, p) for s, p in db.statements if "pg_advisory_xact_lock" in s]
    assert lock_params["k"] == f"renewal-email:{CERT_ID}:vendor_email"


async def test_an_item_already_sent_is_never_handed_back():
    # 28 Oct 2026 (review): a sent renewal stays 'pending' with payload.email_sent true. The
    # lookup leaves it out, so the next press drafts afresh instead of reopening old wording.
    db = FakeDB(_cert(), contact={"vendor_name": "Brook Gas", "email": "office@brook.example"})
    await certs_mod.draft_renewal_email(db, certificate_id=CERT_ID, organization_id=ORG_A)
    [(sql, params)] = [(s, p) for s, p in db.statements if "approvals_queue_items" in s]
    assert "->> %(payload_2)s), %(coalesce_1)s) != %(coalesce_2)s" in sql
    assert params["payload_2"] == "email_sent" and params["coalesce_1"] == "false" and params["coalesce_2"] == "true"


async def test_a_reused_item_from_before_the_flag_infers_it_from_the_address():
    existing = SimpleNamespace(
        id=UUID(int=56), summary="Renewal email — Gas Safe (GS-1)",
        email_draft={"to": settings.default_pm_email, "subject": "s", "body": "b"},
        payload={"certificate_id": str(CERT_ID), "renewal": True})
    db = FakeDB(_cert(), contact=None, pending=[existing])
    out = await certs_mod.draft_renewal_email(db, certificate_id=CERT_ID, organization_id=ORG_A)
    assert out["reused"] is True and out["to_is_fallback"] is True and db.added == []


# ── the fallback address is said out loud ────────────────────────────────────────────────


async def test_no_vendor_contact_falls_back_to_the_pm_and_says_so():
    db = FakeDB(_cert(), contact={"vendor_name": "Brook Gas", "email": None})
    out = await certs_mod.draft_renewal_email(db, certificate_id=CERT_ID, organization_id=ORG_A)
    assert out["email_draft"]["to"] == settings.default_pm_email
    assert out["to_is_fallback"] is True
    [item] = _queued(db)
    assert item.payload["to_is_fallback"] is True


async def test_a_building_booking_goes_to_the_pm_by_design_not_as_a_fallback():
    db = FakeDB(_cert(cert_scope="Building", vendor_id=None, building_id=BUILDING))
    out = await certs_mod.draft_renewal_email(db, certificate_id=CERT_ID, organization_id=ORG_A)
    assert out["ok"] is True and out["to_is_fallback"] is False
    assert _queued(db)[0].item_type == "booking_request"


# ── the route ────────────────────────────────────────────────────────────────────────────


def _post(monkeypatch, db, body, *, role="admin", org=ORG_A, building_ids=None):
    from fastapi.testclient import TestClient

    from src.api.routes import auth as auth_routes
    from src.app import app
    from src.db import get_session
    from src.engines.auth.tokens import Principal

    async def _session():
        yield db

    async def _principal():
        return Principal(user_id=UUID(int=2), email="a@example.com", organization_id=org,
                         session_id=None, issued_at=datetime.now(timezone.utc),
                         password_changed_at=0, role=role, can_ingest=True,
                         building_ids=building_ids)

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[auth_routes.current_principal] = _principal
    try:
        return TestClient(app).post("/api/compliance/certificates/renewal-email", json=body)
    finally:
        app.dependency_overrides.clear()


def test_the_route_answers_another_companys_certificate_404_like_an_unknown_id(monkeypatch):
    foreign = FakeDB(_cert(organization_id=ORG_B, org_id=ORG_B),
                     contact={"vendor_name": "Brook Gas", "email": "office@brook.example"})
    r = _post(monkeypatch, foreign, {"certificate_id": str(CERT_ID)})
    unknown = _post(monkeypatch, FakeDB(None), {"certificate_id": str(UUID(int=99))})

    assert r.status_code == 404, r.text
    assert unknown.status_code == 404 and r.json() == unknown.json()
    assert r.json()["detail"]["reason"] == "not_found"
    assert foreign.added == [] and foreign.committed == 0
    by_number = _post(monkeypatch, foreign, {"certificate_number": "GS-1"})
    assert by_number.status_code == 404 and foreign.added == []


def test_the_route_drafts_for_the_callers_own_certificate(monkeypatch):
    db = FakeDB(_cert(), contact={"vendor_name": "Brook Gas", "email": "office@brook.example"})
    r = _post(monkeypatch, db, {"certificate_id": str(CERT_ID)})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["reused"] is False and body["to_is_fallback"] is False
    assert body["queue_item_id"] == str(_queued(db)[0].id)


def test_the_route_passes_the_callers_scope_to_the_engine(monkeypatch):
    from src.api.routes import compliance as c_routes

    seen = {}

    async def engine(session, **kw):
        seen.update(kw)
        return {"ok": True}

    monkeypatch.setattr(c_routes.cert_svc, "draft_renewal_email", engine)
    r = _post(monkeypatch, FakeDB(None), {"certificate_id": str(CERT_ID)},
              role="user", building_ids=(BUILDING,))
    assert r.status_code == 200, r.text
    assert seen["organization_id"] == ORG_A and seen["building_ids"] == (BUILDING,)
