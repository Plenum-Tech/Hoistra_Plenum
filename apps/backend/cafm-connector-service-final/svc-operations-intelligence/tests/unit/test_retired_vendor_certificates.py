"""Certificates never link new data to a retired vendor, and say so when the vendor is gone.

Hussain, 12 Oct 2026: a vendor with history is RETIRED on delete — status 'deleted', name
marked " (deleted)", vendor-scope certificates archived. What stays visible is history: a
certificate still names the vendor it was issued to, and the API says that vendor was
deleted (``vendor_deleted: True``) while keeping ``vendor_on_record`` True, so the Hoist
vendors screen never mistakes the certificate for an unregistered supplier and offers to
create it again.

What must not happen is anything NEW: a re-upload re-linking to the retired row, a PM's
link pick landing on it, a renewal email drafted to it, or its old block counted as a
blocked vendor today.

No database: the fake routes by SQL text and answers the way Postgres would — it leaves a
retired vendor out only when the statement carries the live-vendor predicate.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from types import SimpleNamespace
from uuid import UUID

import pytest

from src.engines.compliance import certificates as certs_mod
from src.engines.compliance import country_pack
from src.engines.compliance import resource_skills
from src.shared import vendor_identity as vi

ORG = UUID("00000000-0000-0000-0000-000000000001")
OLD = UUID("aaaaaaaa-0000-0000-0000-000000000001")   # retired
NEW = UUID("bbbbbbbb-0000-0000-0000-000000000002")   # live

_LIVE = re.compile(re.escape(vi.live_vendor_sql("qq_alias")).replace("qq_alias", r"\w+"))


def filters_retired(sql: str) -> bool:
    return bool(_LIVE.search(sql)) or vi.deleted_vendor_ids_sql() in sql


class _Result:
    def __init__(self, rows=(), scalar=None):
        self._rows = list(rows)
        self._scalar = scalar

    def mappings(self):
        return self

    def scalars(self):
        return self

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return list(self._rows)

    def scalar(self):
        return self._scalar

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


class _Nested:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class CertDB:
    """Vendors and certificates; every statement recorded."""

    def __init__(self, vendors=(), certs=(), cert=None, unreadable_status=False):
        self.vendors = {str(v["id"]): v for v in vendors}
        self.certs = list(certs)
        self.cert = cert
        self.unreadable_status = unreadable_status
        self.statements: list[tuple[str, dict]] = []
        self.added: list = []
        self.committed = 0

    def begin_nested(self):
        return _Nested()

    async def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        p = dict(params or {})
        self.statements.append((sql, p))
        if "plenum_cafm.vendors" in sql and sql.upper().startswith("SELECT"):
            if "to_jsonb(v)->>'status') FROM" in sql:        # is_deleted_vendor
                if self.unreadable_status:
                    raise RuntimeError("canceling statement due to statement timeout")
                v = self._by_id(p.get("vid"), sql)
                return _Result(scalar=(v or {}).get("status"))
            if "AS organization_id, lower(" in sql:           # vendor_link_refusal
                if self.unreadable_status:
                    raise RuntimeError("canceling statement due to statement timeout")
                v = self._by_id(p.get("vid"), sql)
                return _Result([{"organization_id": v.get("organization_id"),
                                 "status": v.get("status")}] if v else [])
            rows = [v for v in self.vendors.values()
                    if not (filters_retired(sql) and v.get("status") == vi.DELETED_STATUS)]
            if "COUNT(*)" in sql.upper():
                return _Result(scalar=sum(1 for v in rows
                                          if str(v.get("block_state") or "").lower() == "blocked"))
            if "ids" in p:
                rows = [v for v in rows if str(v["id"]) in {str(i) for i in p["ids"]}]
            return _Result([{**v, "id": str(v["id"])} for v in rows])
        if "FROM plenum_cafm.compliance_certificates" in sql:
            return _Result(self.certs)
        return _Result([])

    def _by_id(self, vid, sql):
        # Postgres compares text exactly; the guard lower()s both sides, so only then does a
        # differently spelled uuid find its row.
        if "lower(v.id::text) = lower(:vid)" in sql:
            return {k.lower(): v for k, v in self.vendors.items()}.get(str(vid).lower())
        return self.vendors.get(str(vid))

    async def get(self, model, key):
        return self.cert

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        return None

    async def commit(self):
        self.committed += 1

    async def refresh(self, obj):
        return None

    def wrote(self, needle: str) -> bool:
        return any(needle in s for s, _ in self.statements)


def vendor(vid, name, status="active", block_state="Clear"):
    return {"id": vid, "vendor_name": name, "status": status, "block_state": block_state,
            "blocked_accreditation_type": None, "block_reason": None, "block_date": None,
            "organization_id": str(ORG)}


RETIRED = vendor(OLD, "Acme Lifts (deleted)", status="deleted", block_state="Blocked")
LIVE = vendor(NEW, "Brook Gas")


# ── (a) the certificates API says when the vendor was deleted ─────────────────────────────


def _row(vid):
    return {"id": "c-1", "vendor_id": str(vid), "certificate_type_code": "GAS_SAFE",
            "expiry_date": "2027-06-30", "raw_metadata": {}, "cert_scope": "Vendor"}


async def test_a_certificate_of_a_retired_vendor_says_the_vendor_was_deleted():
    db = CertDB([RETIRED, LIVE])
    [old, new] = await certs_mod._enrich_certificate_rows(db, [_row(OLD), _row(NEW)])
    assert old["vendor_deleted"] is True
    # Still on record: Hoist vendors must not offer to create it again.
    assert old["vendor_on_record"] is True and old["vendor_name"] == "Acme Lifts (deleted)"
    assert "vendor_deleted" not in new and new["vendor_on_record"] is True


# ── (d) a PM's link pick never lands on a retired vendor ──────────────────────────────────


def _cert(**kw):
    base = dict(id=UUID(int=7), organization_id=ORG, org_id=ORG, raw_metadata={},
                document_id=UUID(int=9), vendor_id=None, cert_scope="Vendor", site_id=None,
                site_ref=None, asset_id=None, certificate_type_code="GAS_SAFE",
                cert_type="GAS_SAFE", certificate_number="GS-1", country_code="UK",
                days_to_expiry=-3, status="Lapsed", expiry_date=date(2026, 10, 9))
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.mark.parametrize(
    "unreadable, vid, reason",
    [(False, str(OLD), "vendor_deleted"),
     (True, str(OLD), "vendor_unreadable"),
     # Review, 12 Oct 2026: an upper-case uuid is the same retired vendor, not a missing one.
     (False, str(OLD).upper(), "vendor_deleted")],
    ids=["retired", "status-unreadable", "retired-upper-case"])
async def test_linking_a_certificate_to_a_retired_vendor_is_refused(unreadable, vid, reason):
    cert = _cert()
    db = CertDB([RETIRED], cert=cert, unreadable_status=unreadable)
    out = await certs_mod.link_certificate_document(db, cert.id, vendor_id=vid)
    assert out["ok"] is False and out["reason"] == reason
    assert cert.vendor_id is None, "the certificate keeps the vendor it had"
    assert not db.wrote("INSERT INTO plenum_cafm.files") and db.committed == 0


async def test_linking_to_a_live_vendor_still_works():
    cert = _cert()
    db = CertDB([LIVE], cert=cert)
    out = await certs_mod.link_certificate_document(db, cert.id, vendor_id=str(NEW))
    assert out["ok"] is True and str(cert.vendor_id) == str(NEW)


@pytest.mark.parametrize(
    "vendors, vid, reason",
    [([], str(NEW), "vendor_not_found"),                                  # removed since
     ([{**LIVE, "organization_id": str(UUID(int=99))}], str(NEW), "vendor_not_found"),
     ([LIVE], "V-01", "invalid_vendor_id")],                            # column is uuid
    ids=["removed", "other-company", "not-a-uuid"])
async def test_linking_to_a_vendor_that_is_not_the_companys_is_refused(vendors, vid, reason):
    # Review, 12 Oct 2026: a vendor with no row read as "not deleted" and was linked.
    cert = _cert()
    db = CertDB(vendors, cert=cert)
    out = await certs_mod.link_certificate_document(db, cert.id, vendor_id=vid)
    assert out["ok"] is False and out["reason"] == reason
    assert cert.vendor_id is None and db.committed == 0


async def test_a_linked_vendor_id_is_stored_as_the_canonical_uuid():
    cert = _cert()
    db = CertDB([LIVE], cert=cert)
    out = await certs_mod.link_certificate_document(db, cert.id, vendor_id=str(NEW).upper())
    assert out["ok"] is True and cert.vendor_id == NEW


# ── (f)/(i) an upsert never sets vendor_id to a retired vendor ───────────────────────────


def test_a_retired_prior_vendor_is_not_carried_forward():
    assert certs_mod._keep_vendor_id(None, OLD, prior_deleted=True) is None
    assert certs_mod._keep_vendor_id(None, OLD) == OLD   # the 22 Sep rule still holds


@pytest.mark.parametrize(
    "incoming, prior, unreadable, expected",
    [
        (OLD, None, False, None),     # the payload names the retired vendor
        (None, OLD, False, None),     # the certificate was linked to it before
        (NEW, OLD, False, NEW),       # an explicit live vendor still wins
        (None, NEW, False, NEW),      # a live prior is carried forward
        (NEW, None, True, None),      # status unreadable: a link is refused, not guessed
    ],
    ids=["incoming-retired", "prior-retired", "incoming-live", "prior-live", "unreadable"],
)
async def test_the_upsert_links_only_a_live_vendor(incoming, prior, unreadable, expected):
    db = CertDB([RETIRED, LIVE], unreadable_status=unreadable)
    assert await certs_mod._linkable_vendor_id(db, incoming, prior) == expected


async def test_a_retired_vendors_old_block_is_not_carried_onto_its_certificates():
    # Review, 12 Oct 2026: retiring keeps block_state 'Blocked'; a building certificate it
    # issued was badged Blocked and the chat counted it as a blocked vendor.
    db = CertDB([RETIRED])
    row = {**_row(OLD), "cert_scope": "Building", "days_to_expiry": 200}
    [out] = await certs_mod._enrich_certificate_rows(db, [row])
    assert out["vendor_deleted"] is True
    assert out["vendor_block_state"] is None and out["block_reason"] is None
    assert out["risk_badge"] != "Blocked"
    assert not certs_mod._matches_risk_filter(out, "blocked")


# ── superseding: a different vendor's certificate is never superseded by name ────────────


def _sup_cert(n, vid, name, expiry):
    return SimpleNamespace(id=UUID(int=n), vendor_id=vid, raw_metadata={"vendor_name": name},
                           expiry_date=expiry, certificate_type_code="GAS_SAFE",
                           cert_scope="Vendor", organization_id=ORG, org_id=ORG,
                           cert_type="GAS_SAFE", status="Valid", days_to_expiry=200,
                           updated_at=None)


@pytest.mark.parametrize(
    "cand_vid, expected",
    [(OLD, 0),      # both ids set and different: another vendor's, whatever the name says
     (None, 1),     # the candidate has no id: the name is all there is to go on
     (NEW, 1)],     # the same vendor
    ids=["different-ids", "missing-id", "same-id"])
async def test_only_the_same_vendors_certificate_is_superseded(cand_vid, expected):
    new = _sup_cert(1, NEW, "Acme Lifts", date(2027, 6, 30))
    old = _sup_cert(2, cand_vid, "Acme Lifts", date(2026, 6, 30))

    class _SupDB(CertDB):
        async def execute(self, stmt, params=None):
            sql = str(stmt)
            if "compliance_certificates" in sql and sql.lstrip().upper().startswith("SELECT"):
                return _Result([old])
            return await super().execute(stmt, params)

    db = _SupDB([])
    assert await certs_mod._supersede_prior_vendor_certs(db, new, org_id=ORG) == expected


# ── (g) a retired vendor is not a blocked vendor today ───────────────────────────────────


def _space(monkeypatch):
    async def no_pack(*a, **k):
        return []

    async def no_flags(session, ids):
        return {}

    monkeypatch.setattr(country_pack, "list_pack_types", no_pack)
    monkeypatch.setattr(resource_skills, "lapsed_operative_flags_for_vendors", no_flags)


def _vendor_cert(n, vid, expiry):
    return SimpleNamespace(id=UUID(int=n), vendor_id=vid, cert_scope="Vendor",
                           raw_metadata={}, expiry_date=expiry, days_to_expiry=None, status=None,
                           insurance_risk_flag=False, certificate_number="GS-1",
                           certificate_type_code="GAS_SAFE", cert_type="GAS_SAFE",
                           building_id=None)


async def test_a_retired_vendor_is_not_counted_as_blocked(monkeypatch):
    _space(monkeypatch)
    lapsed = date(2026, 1, 1)
    current = date(2027, 12, 31)
    db = CertDB([RETIRED, LIVE],
                certs=[_vendor_cert(1, OLD, lapsed), _vendor_cert(2, NEW, current)])
    out = await certs_mod.saved_space_summary(db, organization_id=ORG)
    assert out["risk_dashboard"]["vendors_blocked"] == 0
    # Its certificate is history, not a live accreditation.
    assert out["vendor_certificates"]["total"] == 1
    assert out["vendor_certificates"]["lapsed"] == 0


async def test_the_register_wide_blocked_count_leaves_retired_vendors_out(monkeypatch):
    _space(monkeypatch)
    db = CertDB([RETIRED, LIVE], certs=[])
    out = await certs_mod.saved_space_summary(db, organization_id=ORG)
    assert out["risk_dashboard"]["vendors_blocked"] == 0


# ── (h) no renewal email to a retired vendor ─────────────────────────────────────────────


async def test_no_renewal_email_is_drafted_for_a_retired_vendor(monkeypatch):
    queued = []

    async def pack(*a, **k):
        return SimpleNamespace(certificate_type_name="Gas Safe", verification_url=None,
                               required_contractor_accreditation=None)

    async def enqueue(session, **kw):
        queued.append(kw)
        return SimpleNamespace(id=UUID(int=5))

    monkeypatch.setattr(certs_mod, "get_pack_type", pack)
    monkeypatch.setattr(certs_mod, "enqueue_approval", enqueue)
    cert = _cert(vendor_id=OLD)
    db = CertDB([RETIRED], cert=cert)
    out = await certs_mod.draft_renewal_email(db, certificate_id=cert.id)
    assert out["ok"] is False and out["reason"] == "vendor_deleted"
    assert "deleted" in out["error"].lower()
    assert queued == [] and db.committed == 0


async def test_no_renewal_email_is_drafted_when_the_vendor_cannot_be_checked(monkeypatch):
    # Review, 12 Oct 2026: a draft queued to a vendor is new data for it; a status that cannot
    # be read refuses rather than guesses "live".
    queued = []

    async def pack(*a, **k):
        return SimpleNamespace(certificate_type_name="Gas Safe", verification_url=None,
                               required_contractor_accreditation=None)

    async def enqueue(session, **kw):
        queued.append(kw)
        return SimpleNamespace(id=UUID(int=5))

    monkeypatch.setattr(certs_mod, "get_pack_type", pack)
    monkeypatch.setattr(certs_mod, "enqueue_approval", enqueue)
    cert = _cert(vendor_id=NEW)
    db = CertDB([LIVE], cert=cert, unreadable_status=True)
    out = await certs_mod.draft_renewal_email(db, certificate_id=cert.id)
    assert out["ok"] is False and out["reason"] == "vendor_unreadable"
    assert queued == [] and db.committed == 0


# ── the link route answers a refusal as a conflict ───────────────────────────────────────


def test_the_link_route_answers_a_retired_vendor_with_409(monkeypatch):
    from unittest.mock import AsyncMock

    from fastapi.testclient import TestClient

    from src.api.routes import auth as auth_routes
    from src.api.routes import compliance as c_routes
    from src.app import app
    from src.db import get_session
    from src.engines.auth.tokens import Principal

    async def _session():
        yield AsyncMock()

    async def _principal():
        return Principal(user_id=UUID(int=2), email="a@example.com", organization_id=ORG,
                         session_id=None, issued_at=datetime.now(timezone.utc),
                         password_changed_at=0, role="admin", can_ingest=True, building_ids=None)

    async def refuse(session, certificate_id, **kw):
        return {"ok": False, "reason": "vendor_deleted",
                "error": "That vendor has been deleted. Pick another vendor."}

    monkeypatch.setattr(c_routes.cert_svc, "link_certificate_document", refuse)
    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[auth_routes.current_principal] = _principal
    try:
        r = TestClient(app).post(f"/api/compliance/certificates/{UUID(int=7)}/link",
                                 json={"vendor_id": str(OLD)})
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["reason"] == "vendor_deleted"
    assert r.json()["detail"]["ok"] is False
