"""GET / PATCH /api/energy/assets/{id}/vendor: scoped like /investigate, admins only to change.

No real database: get_session and current_principal are overridden and the engine calls are
replaced, so what is pinned is the route's own work — who may ask, who may change, and what
each refusal answers as.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from src.api.routes import auth as auth_routes
from src.api.routes import energy as energy_routes
from src.app import app
from src.db import get_session
from src.engines.auth.tokens import Principal

ORG = UUID("11111111-1111-1111-1111-111111111111")
B1 = UUID("22222222-2222-2222-2222-222222222222")
ADMIN = UUID(int=2)
DETAIL = {"ok": True, "asset": {"id": "a1", "asset_name": "Boiler 1", "asset_code": "B-301-BOILER-01",
                                "building_id": str(B1), "building": "Bishopsgate Tower"}, "readings": []}


def _client(monkeypatch, role, change_result=None):
    async def _session():
        yield AsyncMock()

    async def _principal():
        return Principal(user_id=ADMIN, email="a@example.com", organization_id=ORG, session_id=None,
                         issued_at=datetime.now(timezone.utc), password_changed_at=0, role=role,
                         can_ingest=True, building_ids=None)

    calls: dict = {}

    async def _ids(session, s, building_id):
        return [B1]

    async def _resolve(session, asset_id):
        return asset_id

    async def _detail(session, *, asset_id, building_ids):
        return DETAIL if asset_id == "a1" else {"ok": False, "reason": "not_found_or_not_in_scope"}

    async def _view(session, **kw):
        calls["view"] = kw
        return {"ok": True, "asset": {"id": "a1"}, "vendor": None, "choices": []}

    async def _change(session, **kw):
        calls["change"] = kw
        return change_result or {"ok": True, "changed": True, "vendor": {"id": "v2", "name": "Mitie"}}

    monkeypatch.setattr(energy_routes.position_svc, "building_ids_for", _ids)
    monkeypatch.setattr(energy_routes.anom_svc, "resolve_asset", _resolve)
    monkeypatch.setattr(energy_routes.ai_svc, "asset_detail", _detail)
    monkeypatch.setattr(energy_routes.asset_vendor, "vendor_view", _view)
    monkeypatch.setattr(energy_routes.asset_vendor, "change_vendor", _change)
    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[auth_routes.current_principal] = _principal
    return TestClient(app), calls


@pytest.fixture(autouse=True)
def _clear():
    yield
    app.dependency_overrides.clear()


def test_the_drawer_says_whether_this_reader_may_change_the_vendor(monkeypatch):
    c, calls = _client(monkeypatch, "admin")
    r = c.get("/api/energy/assets/a1/vendor")
    assert r.status_code == 200, r.text
    assert r.json()["can_change"] is True and calls["view"]["asset_id"] == "a1"
    c, _ = _client(monkeypatch, "user")
    assert c.get("/api/energy/assets/a1/vendor").json()["can_change"] is False


def test_an_admins_change_carries_who_made_it(monkeypatch):
    c, calls = _client(monkeypatch, "admin")
    r = c.patch("/api/energy/assets/a1/vendor", json={"vendor_id": "v2", "note": "new contract"})
    assert r.status_code == 200, r.text
    kw = calls["change"]
    assert (kw["asset_id"], kw["vendor_id"], kw["user_id"], kw["role"], kw["note"]) == \
        ("a1", "v2", ADMIN, "admin", "new contract")


def test_a_facilities_manager_cannot_change_it(monkeypatch):
    c, calls = _client(monkeypatch, "user")
    r = c.patch("/api/energy/assets/a1/vendor", json={"vendor_id": "v2"})
    assert r.status_code == 403
    assert "change" not in calls


def test_an_asset_outside_your_buildings_is_404(monkeypatch):
    c, _ = _client(monkeypatch, "admin")
    assert c.get("/api/energy/assets/elsewhere/vendor").status_code == 404
    assert c.patch("/api/energy/assets/elsewhere/vendor", json={"vendor_id": "v2"}).status_code == 404


@pytest.mark.parametrize("reason,code", [("vendor_not_found", 404), ("blocked", 409), ("inactive", 409),
                                         ("different_trade", 409),
                                         ("legacy_id", 422), ("not_recorded", 503), ("not_changed", 503)])
def test_each_refusal_answers_as_what_it_is(monkeypatch, reason, code):
    c, _ = _client(monkeypatch, "admin", change_result={"ok": False, "reason": reason, "error": "why"})
    r = c.patch("/api/energy/assets/a1/vendor", json={"vendor_id": "v2"})
    assert r.status_code == code
    assert r.json()["detail"] == {"ok": False, "reason": reason, "error": "why"}


def test_a_vendor_must_be_named(monkeypatch):
    c, _ = _client(monkeypatch, "admin")
    assert c.patch("/api/energy/assets/a1/vendor", json={}).status_code == 422
    assert c.patch("/api/energy/assets/a1/vendor", json={"vendor_id": "  "}).status_code == 422


def test_the_drawer_is_read_for_the_callers_company_and_only_an_admin_gets_the_choices(monkeypatch):
    c, calls = _client(monkeypatch, "admin")
    c.get("/api/energy/assets/a1/vendor")
    assert calls["view"]["organization_id"] == ORG and calls["view"]["include_choices"] is True
    c.patch("/api/energy/assets/a1/vendor", json={"vendor_id": "v2"})
    assert calls["change"]["organization_id"] == ORG
    c, calls = _client(monkeypatch, "user")
    c.get("/api/energy/assets/a1/vendor")
    assert calls["view"]["include_choices"] is False
