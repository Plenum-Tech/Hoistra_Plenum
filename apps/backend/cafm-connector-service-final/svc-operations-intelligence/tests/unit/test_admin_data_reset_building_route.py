"""The data-reset routes with one building chosen: the building must be the company's, the
confirmation is the building's name, and the engine is handed that building — nothing else.

The engine and the database are stubbed: what is pinned here is the route's own checks.
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
from src.engines.auth.tokens import Principal

ORG = UUID("11111111-1111-5111-8111-111111111111")
HARBOUR = "c343c566-0000-4000-8000-000000000001"
STRANGER = str(uuid4())
COMPANY = "Northbridge Estates Ltd"


class _Session:
    async def rollback(self):
        pass

    def begin(self):
        session = self

        class _Tx:
            async def __aenter__(self):
                return session

            async def __aexit__(self, *a):
                return False
        return _Tx()


@pytest.fixture
def calls(monkeypatch):
    seen: dict = {"runs": [], "audits": []}

    async def _s():
        yield _Session()

    async def _principal():
        return Principal(
            user_id=uuid4(), email="admin@example.com", organization_id=ORG, session_id=None,
            issued_at=datetime.now(timezone.utc), password_changed_at=0, role="admin",
            can_ingest=False, building_ids=None)

    async def _org_name(session, org):
        return COMPANY

    async def _buildings(session, org):
        return {HARBOUR: {"id": HARBOUR, "name": "Harbour Point", "building_code": "B-101"}}

    async def _run(session, org, *, areas, apply, building_id=None):
        seen["runs"].append({"areas": areas, "apply": apply, "building_id": building_id})
        return {"organization_id": org, "applied": apply, "areas": [], "row_total": 7,
                "building_id": building_id, "buildings": 1, "links_cleared": [], "blocked": [],
                "skipped": {}, "kept": [], "company_wide": [], "available_areas": {}}

    async def _audit(session, **kw):
        seen["audits"].append(kw)

    monkeypatch.setattr(admin_routes.settings, "org_data_reset_enabled", True)
    monkeypatch.setattr(admin_routes, "_org_name", _org_name)
    monkeypatch.setattr(admin_routes, "_company_buildings", _buildings)
    monkeypatch.setattr(admin_routes.org_data_reset, "run_reset", _run)
    monkeypatch.setattr(admin_routes, "write_audit", _audit)
    app.dependency_overrides[get_session] = _s
    app.dependency_overrides[auth_routes.current_principal] = _principal
    try:
        yield seen
    finally:
        app.dependency_overrides.clear()


def test_the_preview_for_one_building_is_confirmed_with_its_name(calls):
    r = TestClient(app).get(f"/api/admin/data-reset?areas=assets&building_id={HARBOUR}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["confirm_with"] == "Harbour Point"
    assert body["building"] == {"id": HARBOUR, "name": "Harbour Point", "building_code": "B-101"}
    assert body["organization_name"] == COMPANY
    assert calls["runs"] == [{"areas": ["assets"], "apply": False, "building_id": HARBOUR}]


def test_the_company_preview_is_still_confirmed_with_the_company_name(calls):
    r = TestClient(app).get("/api/admin/data-reset?areas=assets")
    assert r.status_code == 200, r.text
    assert r.json()["confirm_with"] == COMPANY and r.json()["building"] is None
    assert calls["runs"][0]["building_id"] is None


def test_another_companys_building_is_refused_before_the_engine(calls):
    c = TestClient(app)
    r = c.get(f"/api/admin/data-reset?areas=assets&building_id={STRANGER}")
    assert r.status_code == 404 and r.json()["detail"]["reason"] == "building_not_found"
    r = c.post("/api/admin/data-reset", json={"areas": ["assets"], "building_id": STRANGER,
                                               "confirm": "Harbour Point"})
    assert r.status_code == 404 and r.json()["detail"]["reason"] == "building_not_found"
    assert calls["runs"] == []


def test_a_building_reset_wants_the_building_name_not_the_company_name(calls):
    r = TestClient(app).post("/api/admin/data-reset", json={
        "areas": ["assets"], "building_id": HARBOUR, "confirm": COMPANY})
    assert r.status_code == 400
    assert r.json()["detail"] == {"ok": False, "reason": "confirm_mismatch",
                                  "error": "Type the building name exactly to confirm the reset.",
                                  "confirm_with": "Harbour Point"}
    assert calls["runs"] == []


def test_a_building_reset_deletes_that_building_and_audits_which_one(calls):
    r = TestClient(app).post("/api/admin/data-reset", json={
        "areas": ["assets"], "building_id": HARBOUR, "confirm": "Harbour Point"})
    assert r.status_code == 200, r.text
    assert calls["runs"] == [{"areas": ["assets"], "apply": True, "building_id": HARBOUR}]
    (audit,) = calls["audits"]
    assert audit["input_payload"]["building_id"] == HARBOUR
    assert audit["detail"]["building"] == {"id": HARBOUR, "name": "Harbour Point", "building_code": "B-101"}


def test_a_building_with_no_name_is_confirmed_by_its_code(calls, monkeypatch):
    async def _nameless(session, org):
        return {HARBOUR: {"id": HARBOUR, "name": None, "building_code": "B-101"}}

    monkeypatch.setattr(admin_routes, "_company_buildings", _nameless)
    c = TestClient(app)
    r = c.get(f"/api/admin/data-reset?areas=assets&building_id={HARBOUR}")
    assert r.status_code == 200 and r.json()["confirm_with"] == "B-101"
    r = c.post("/api/admin/data-reset", json={"areas": ["assets"], "building_id": HARBOUR, "confirm": COMPANY})
    assert r.status_code == 400 and r.json()["detail"]["confirm_with"] == "B-101"
    r = c.post("/api/admin/data-reset", json={"areas": ["assets"], "building_id": HARBOUR, "confirm": "B-101"})
    assert r.status_code == 200, r.text


def test_the_engines_refusal_of_a_building_is_a_404(calls, monkeypatch):
    async def _refuse(session, org, *, areas, apply, building_id=None):
        raise admin_routes.org_data_reset.ResetForeignBuilding("not this company's")

    monkeypatch.setattr(admin_routes.org_data_reset, "run_reset", _refuse)
    r = TestClient(app).get(f"/api/admin/data-reset?areas=assets&building_id={HARBOUR}")
    assert r.status_code == 404 and r.json()["detail"]["reason"] == "building_not_found"
