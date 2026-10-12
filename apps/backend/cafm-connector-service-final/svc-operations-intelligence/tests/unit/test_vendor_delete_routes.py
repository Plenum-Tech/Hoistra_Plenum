"""The two routes behind "Delete vendor": who may call them, which company they act in, and
what each refusal answers.

GET /api/admin/vendors/{id}/delete is the plan the dialog shows; POST carries out what the admin
confirmed. Admin only, company from the verified scope only - a superadmin viewing a company
deletes in that company and is recorded as themselves (Hussain, 12 Oct 2026). The engine is
replaced here; test_vendor_delete_run.py covers what it does.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from src.api.routes import admin as admin_routes
from src.api.routes import auth as auth_routes
from src.app import app
from src.db import get_session
from src.engines.admin import vendor_delete as vd
from src.engines.auth.tokens import Principal

ORG = UUID("11111111-1111-5111-8111-111111111111")
OTHER = UUID("22222222-2222-5222-8222-222222222222")
PATH = "/api/admin/vendors/V-01/delete"
BODY = {"confirm": "Apex Mechanical", "expect": "retire", "reason": "Merged into Apex Group"}
#: Internal names the screen must never show (no-internal-names rule).
_INTERNAL = ("plenum_cafm", "svc-", "work_orders", "approvals_queue_items", "SELECT", "UPDATE")


def principal(role: str, org=ORG) -> Principal:
    return Principal(
        user_id=uuid4(), email=f"{role}@example.com", organization_id=org, session_id=None,
        issued_at=datetime.now(timezone.utc), password_changed_at=0, role=role,
        can_ingest=False, building_ids=None if role != "user" else (uuid4(),),
    )


class Recorder:
    """Stands in for the engine: remembers how it was called, answers or raises."""

    def __init__(self, answer=None, raises=None):
        self.calls: list[dict] = []
        self.answer = answer or {"ok": True, "applied": False, "mode": "remove", "groups": {}}
        self.raises = raises

    async def __call__(self, session, **kw):
        self.calls.append(kw)
        if self.raises:
            raise self.raises
        return self.answer


@pytest.fixture
def engine(monkeypatch):
    rec = Recorder()
    monkeypatch.setattr(admin_routes.vendor_delete, "run", rec)

    async def _s():
        yield object()
    app.dependency_overrides[get_session] = _s
    try:
        yield rec
    finally:
        app.dependency_overrides.clear()


def as_(p: Principal) -> Principal:
    async def _dep():
        return p
    app.dependency_overrides[auth_routes.current_principal] = _dep
    return p


def test_the_plan_is_counted_for_the_admins_own_company_and_never_applied(engine):
    me = as_(principal("admin"))
    r = TestClient(app).get(PATH)
    assert r.status_code == 200, r.text
    assert r.json() == engine.answer
    assert engine.calls == [{"organization_id": ORG, "vendor_id": "V-01", "apply": False,
                             "user_id": me.user_id, "role": "admin"}]


def test_the_delete_carries_what_the_admin_confirmed(engine):
    me = as_(principal("admin"))
    r = TestClient(app).post(PATH, json=BODY)
    assert r.status_code == 200, r.text
    assert engine.calls == [{"organization_id": ORG, "vendor_id": "V-01", "apply": True,
                             "user_id": me.user_id, "role": "admin",
                             "confirm": "Apex Mechanical", "expect": "retire",
                             "reason": "Merged into Apex Group"}]


def test_a_superadmin_viewing_a_company_deletes_in_that_company_as_themselves(engine):
    me = as_(principal("superadmin"))
    r = TestClient(app).post(f"{PATH}?organization_id={OTHER}", json=BODY)
    assert r.status_code == 200, r.text
    call = engine.calls[0]
    assert call["organization_id"] == OTHER
    assert call["user_id"] == me.user_id and call["role"] == "superadmin"


def test_an_admin_naming_another_company_is_refused_before_the_engine(engine):
    as_(principal("admin"))
    for r in (TestClient(app).get(f"{PATH}?organization_id={OTHER}"),
              TestClient(app).post(f"{PATH}?organization_id={OTHER}", json=BODY)):
        assert r.status_code == 403
        assert r.json()["detail"]["reason"] == "wrong_organization"
    assert engine.calls == []


def test_a_plain_user_cannot_delete_a_vendor(engine):
    as_(principal("user"))
    assert TestClient(app).get(PATH).status_code == 403
    assert TestClient(app).post(PATH, json=BODY).status_code == 403
    assert engine.calls == []


def test_no_token_cannot_delete_a_vendor(engine):
    r = TestClient(app).post(PATH, json=BODY)
    assert r.status_code == 401 and r.json()["detail"]["reason"] == "missing_token"
    assert engine.calls == []


@pytest.mark.parametrize("body", [
    {},
    {"confirm": "", "expect": "remove"},
    {"confirm": "x" * 301, "expect": "remove"},
    {"confirm": "Apex Mechanical", "expect": "purge"},
    {"confirm": "Apex Mechanical"},
    {"confirm": "Apex Mechanical", "expect": "remove", "reason": "x" * 501},
])
def test_a_malformed_request_is_refused_before_the_engine(engine, body):
    as_(principal("admin"))
    assert TestClient(app).post(PATH, json=body).status_code == 422
    assert engine.calls == []


def test_an_already_deleted_vendor_is_not_an_error(engine):
    engine.answer = {"ok": True, "applied": False, "already": True, "mode": "retire"}
    as_(principal("admin"))
    r = TestClient(app).post(PATH, json=BODY)
    assert r.status_code == 200 and r.json()["already"] is True


@pytest.mark.parametrize("exc,status,reason,extra", [
    (vd.VendorDeleteBadId("x"), 400, "bad_vendor_id", {}),
    (vd.VendorNotFound("V-01"), 404, "vendor_not_found", {}),
    (vd.VendorConfirmMismatch("Apex Mechanical"), 400, "confirm_mismatch",
     {"confirm_with": "Apex Mechanical"}),
    (vd.VendorPlanChanged({"mode": "retire"}), 409, "plan_changed", {"plan": {"mode": "retire"}}),
    (vd.VendorBusy("locked"), 409, "vendor_busy", {"note": "Nothing was changed."}),
    (vd.VendorDeleteFailed("work_orders", "Open work orders",
                           'update on table "work_orders" violates foreign key'),
     409, "delete_refused", {"step": "work_orders", "note": "Nothing was changed."}),
    (vd.VendorNotRecorded("permission denied for table ops_audit_log"), 503, "not_recorded",
     {"note": "Nothing was changed."}),
])
def test_each_refusal_answers_with_its_own_status(engine, exc, status, reason, extra):
    engine.raises = exc
    as_(principal("admin"))
    r = TestClient(app).post(PATH, json=BODY)
    assert r.status_code == status, r.text
    detail = r.json()["detail"]
    assert detail["ok"] is False and detail["reason"] == reason
    assert detail["error"] and not any(w in detail["error"] for w in _INTERNAL), detail["error"]
    for k, v in extra.items():
        assert detail[k] == v, k


def test_a_refusal_while_planning_answers_the_same_way(engine):
    engine.raises = vd.VendorNotFound("V-01")
    as_(principal("admin"))
    r = TestClient(app).get(PATH)
    assert r.status_code == 404 and r.json()["detail"]["reason"] == "vendor_not_found"
