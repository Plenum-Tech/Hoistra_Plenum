"""A retired vendor's passport is gone, it is not booked, and it takes no new operative skills.

Hussain, 12 Oct 2026: deleting a vendor with history RETIRES it. Three places would still
have acted on it:

- the passport built, shared and served a live public link for it — "cleared for
  engagement" for a supplier the company has stopped using;
- approving a booking raised before the delete emailed the booking, with a calendar invite,
  to the retired vendor, because the recommendation was a snapshot taken at scan time;
- operative skills could be written against it, and its skills still listed.

No database: the fake routes by SQL text and leaves a retired vendor out only when the
statement carries the live-vendor predicate (or the deleted-ids subquery under NOT IN).
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID

import pytest

from src.engines.compliance import booking
from src.engines.compliance import passport
from src.engines.compliance import resource_skills
from src.shared import vendor_identity as vi

ORG = UUID("00000000-0000-0000-0000-000000000001")
OLD = UUID("aaaaaaaa-0000-0000-0000-000000000001")   # retired
NEW = UUID("bbbbbbbb-0000-0000-0000-000000000002")   # live

_LIVE = re.compile(re.escape(vi.live_vendor_sql("qq_alias")).replace("qq_alias", r"\w+"))
_DELETED_IDS = vi.deleted_vendor_ids_sql()


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


class DB:
    def __init__(self, vendors=(), skills=(), unreadable_status=False):
        self.vendors = {str(v["id"]): v for v in vendors}
        self.skills = list(skills)
        self.unreadable_status = unreadable_status
        self.statements: list[str] = []
        self.added: list = []

    @property
    def retired(self):
        return {k for k, v in self.vendors.items() if v.get("status") == vi.DELETED_STATUS}

    def begin_nested(self):
        return _Nested()

    async def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        p = dict(params or {})
        self.statements.append(sql)
        live_only = bool(_LIVE.search(sql)) or (_DELETED_IDS in sql and "NOT IN" in sql)
        if "FROM plenum_cafm.resource_skills" in sql:
            return _Result([k for k in self.skills
                            if not (live_only and str(k.vendor_id) in self.retired)])
        if "plenum_cafm.vendors" not in sql:
            return _Result([])
        if "to_jsonb(v)->>'status') FROM" in sql:                         # is_deleted_vendor
            if self.unreadable_status:
                raise RuntimeError("canceling statement due to statement timeout")
            return _Result(scalar=(self.vendors.get(str(p.get("vid"))) or {}).get("status"))
        if "AS organization_id, lower(" in sql:                          # vendor_link_refusal
            if self.unreadable_status:
                raise RuntimeError("canceling statement due to statement timeout")
            v = {k.lower(): v for k, v in self.vendors.items()}.get(str(p.get("vid")).lower())
            return _Result([{"organization_id": v.get("organization_id"),
                             "status": v.get("status")}] if v else [])
        rows = [v for k, v in self.vendors.items()
                if not (live_only and k in self.retired)
                and ("vid" not in p or p["vid"] in (k, v.get("vendor_code")))]
        return _Result(rows)

    async def get(self, model, key):
        return None

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        return None

    async def commit(self):
        return None


def vendor(vid, name, status="active"):
    return {"id": str(vid), "vendor_name": name, "vendor_code": None, "status": status,
            "block_state": "Clear", "block_reason": None, "blocked_accreditation_type": None,
            "block_date": None, "phone": None, "website": None, "city": None, "country": None,
            "notes": None, "organization_id": str(ORG)}


RETIRED = vendor(OLD, "Acme Lifts (deleted)", status="deleted")
LIVE = vendor(NEW, "Brook Gas")


# ── the passport ─────────────────────────────────────────────────────────────────────────


@pytest.fixture
def passport_parts(monkeypatch):
    shared = []

    async def certs(*a, **k):
        return []

    async def cov(*a, **k):
        return {"vendors": []}

    async def skills(*a, **k):
        return []

    async def enqueue(session, **kw):
        shared.append(kw)
        return SimpleNamespace(id=UUID(int=3))

    async def token(session, item_id, **kw):
        shared.append({"token_for": item_id})
        return {"token": "tok", "expires_at": "2026-10-19T00:00:00+00:00"}

    async def audit(*a, **k):
        return None

    monkeypatch.setattr(passport.cert_svc, "list_certificates", certs)
    monkeypatch.setattr(passport, "vendor_coverage", cov)
    monkeypatch.setattr(passport.skill_svc, "list_vendor_operative_skills", skills)
    monkeypatch.setattr(passport, "enqueue_approval", enqueue)
    monkeypatch.setattr(passport, "create_approval_token", token)
    monkeypatch.setattr(passport, "write_audit", audit)
    return shared


async def test_a_retired_vendor_has_no_passport(passport_parts):
    out = await passport.build_vendor_passport(DB([RETIRED]), str(OLD), organization_id=ORG)
    assert out == {"ok": False, "error": "vendor not found"}


async def test_a_live_vendor_still_has_one(passport_parts):
    out = await passport.build_vendor_passport(DB([LIVE]), str(NEW), organization_id=ORG)
    assert out["ok"] is True and out["vendor"]["id"] == str(NEW)


async def test_a_retired_vendors_passport_cannot_be_shared(passport_parts):
    out = await passport.share_vendor_passport(DB([RETIRED]), str(OLD), organization_id=ORG)
    assert out["ok"] is False
    assert passport_parts == [], "no share item, no public link"


async def test_an_old_share_link_says_the_passport_is_no_longer_available(
    monkeypatch, passport_parts
):
    async def peek(session, token):
        return {"ok": True, "expires_at": "2026-10-19T00:00:00+00:00",
                "item": {"item_type": "passport_share", "related_entity_id": str(OLD),
                         "payload": {"vendor_id": str(OLD)}}}

    monkeypatch.setattr(passport, "peek_approval_token", peek)
    out = await passport.passport_from_share_token(DB([RETIRED]), "tok")
    assert out["ok"] is False and out["reason"] == "passport_unavailable"
    assert out["error"] == "This vendor passport is no longer available."
    assert "share" not in out, "nothing of the old snapshot is served"


# ── a booking approved after the delete ──────────────────────────────────────────────────


@pytest.fixture
def mail(monkeypatch):
    sent = []

    async def send(session, **kw):
        sent.append(kw)
        return {"status": "sent"}

    async def site(*a, **k):
        return {"building_name": "Bishopsgate Tower", "address": "1 Bishopsgate"}

    async def nothing(*a, **k):
        return None

    monkeypatch.setattr(booking, "send_platform_email", send)
    monkeypatch.setattr(booking, "resolve_site_display", site)
    monkeypatch.setattr(booking, "write_audit", nothing)
    monkeypatch.setattr(booking, "apply_pm_action_track", nothing)
    return sent


def _item(recommended):
    return SimpleNamespace(id=UUID(int=21), organization_id=ORG, related_entity_id=None,
                           related_entity_type=None,
                           payload={"recommended_vendors": recommended,
                                    "recommended_accreditation": "GAS_SAFE"})


def _rec(vid, name, email):
    return {"vendor_id": str(vid), "vendor_name": name, "email": email, "accreditation": "GAS_SAFE"}


async def test_a_booking_skips_a_recommended_vendor_deleted_since(mail):
    item = _item([_rec(OLD, "Acme Lifts", "book@acme.example"),
                  _rec(NEW, "Brook Gas", "book@brook.example")])
    out = await booking.fulfill_booking_on_approve(DB([RETIRED, LIVE]), item)
    assert out["ok"] is True
    [msg] = mail
    assert msg["to_address"] == "book@brook.example"
    assert msg["body"].startswith("Dear Brook Gas,")


async def test_a_booking_whose_only_contractor_was_deleted_is_not_sent(mail):
    item = _item([_rec(OLD, "Acme Lifts", "book@acme.example")])
    out = await booking.fulfill_booking_on_approve(DB([RETIRED]), item)
    assert mail == []
    assert out["ok"] is False and out["reason"] == "vendor_deleted"
    assert out["booking"]["booking_request_sent"] is False
    assert "deleted" in item.payload["pm_action_status"].lower()


# ── operative skills ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "vendors, unreadable, vid, reason",
    [([RETIRED], False, str(OLD), "vendor_deleted"),
     ([RETIRED], True, str(OLD), "vendor_unreadable"),
     # Review, 12 Oct 2026: a vendor removed since, or another company's, read as "not
     # deleted" and took the skill.
     ([], False, str(NEW), "vendor_not_found"),
     ([{**LIVE, "organization_id": str(UUID(int=99))}], False, str(NEW), "vendor_not_found"),
     ([LIVE], False, "V-01", "invalid_vendor_id")],
    ids=["retired", "status-unreadable", "removed", "other-company", "not-a-uuid"])
async def test_no_skill_is_written_against_a_vendor_it_cannot_belong_to(
        vendors, unreadable, vid, reason):
    db = DB(vendors, unreadable_status=unreadable)
    out = await resource_skills.upsert_resource_skill(
        db, {"vendor_id": vid, "skill_type_code": "ACS_CARD", "organization_id": str(ORG)},
    )
    assert out["ok"] is False and out["reason"] == reason
    assert db.added == []


async def test_a_skill_for_a_live_vendor_of_the_company_is_written():
    db = DB([LIVE])
    out = await resource_skills.upsert_resource_skill(
        db, {"vendor_id": str(NEW), "skill_type_code": "ACS_CARD", "organization_id": str(ORG)},
    )
    assert out.get("reason") is None and len(db.added) == 1


def _skill(n, vid):
    return SimpleNamespace(id=UUID(int=n), vendor_id=vid, operative_name="J. Smith",
                           skill_type_code="ACS_CARD", certificate_number=f"ACS-{n}",
                           expiry_date=None, status="Current", days_to_expiry=200)


@pytest.mark.parametrize("org", [None, ORG], ids=["unrestricted", "company"])
async def test_a_retired_vendors_skills_are_not_listed(org):
    db = DB([RETIRED, LIVE], skills=[_skill(1, OLD), _skill(2, NEW)])
    rows = await resource_skills.list_vendor_operative_skills(db, organization_id=org)
    assert [r["vendor_id"] for r in rows] == [str(NEW)]


# ── the routes answer the refusals by what they are ──────────────────────────────────────


def _client(monkeypatch):
    from unittest.mock import AsyncMock

    from fastapi.testclient import TestClient

    from src.api.routes import auth as auth_routes
    from src.app import app
    from src.db import get_session
    from src.engines.auth.tokens import Principal

    async def _session():
        yield AsyncMock()

    async def _principal():
        return Principal(user_id=UUID(int=2), email="a@example.com", organization_id=ORG,
                         session_id=None, issued_at=datetime.now(timezone.utc),
                         password_changed_at=0, role="admin", can_ingest=True, building_ids=None)

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[auth_routes.current_principal] = _principal
    return TestClient(app), app


def test_the_skill_route_answers_a_retired_vendor_with_409(monkeypatch):
    from src.api.routes import compliance as c_routes

    async def refuse(session, data, **kw):
        return {"ok": False, "reason": "vendor_deleted", "error": "That vendor has been deleted."}

    monkeypatch.setattr(c_routes.skill_svc, "upsert_resource_skill", refuse)
    client, app = _client(monkeypatch)
    try:
        r = client.post("/api/compliance/resource-skills",
                        json={"vendor_id": str(OLD), "skill_type_code": "ACS_CARD"})
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == {"ok": False, "reason": "vendor_deleted",
                                  "error": "That vendor has been deleted."}


def test_an_old_passport_link_answers_410(monkeypatch):
    from src.api.routes import compliance as c_routes

    async def gone(session, token):
        return {"ok": False, "reason": "passport_unavailable",
                "error": "This vendor passport is no longer available."}

    monkeypatch.setattr(c_routes.passport_svc, "passport_from_share_token", gone)
    client, app = _client(monkeypatch)
    try:
        r = client.get("/api/compliance/passport/share/tok")
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 410, r.text
    assert r.json()["detail"]["error"] == "This vendor passport is no longer available."


@pytest.mark.parametrize("reason, code", [("vendor_not_found", 404), ("invalid_vendor_id", 400),
                                          ("vendor_unreadable", 503)])
@pytest.mark.parametrize("route", ["skill", "link"])
def test_the_routes_answer_each_vendor_refusal_by_what_it_is(monkeypatch, route, reason, code):
    from src.api.routes import compliance as c_routes

    async def refuse(*a, **kw):
        return {"ok": False, "reason": reason, "error": "No."}

    monkeypatch.setattr(c_routes.skill_svc, "upsert_resource_skill", refuse)
    monkeypatch.setattr(c_routes.cert_svc, "link_certificate_document", refuse)
    client, app = _client(monkeypatch)
    try:
        if route == "skill":
            r = client.post("/api/compliance/resource-skills",
                            json={"vendor_id": str(OLD), "skill_type_code": "ACS_CARD"})
        else:
            r = client.post(f"/api/compliance/certificates/{UUID(int=7)}/link",
                            json={"vendor_id": str(OLD)})
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == code, r.text
    assert r.json()["detail"]["reason"] == reason
