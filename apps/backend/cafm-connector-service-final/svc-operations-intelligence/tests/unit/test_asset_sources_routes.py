"""The three read-only routes behind an investigation: scoped like /investigate, bounded, and
answering with the same dataset the walk's line is derived from.

No real database: get_session and current_principal are overridden, and the engine calls the
routes make are replaced, so what is pinned is the route's own work — who may ask, for how
long a window, and what it hands the engine.
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
DETAIL = {"ok": True, "asset": {"id": "a1", "asset_name": "Boiler 1", "asset_code": "B-301-BOILER-01",
                                "building_id": str(B1), "building": "Bishopsgate Tower"},
          "readings": [{"reading_type": "pressure", "value": 4.0, "unit": "bar", "band_lo": 3.0,
                        "band_hi": 5.5, "state": "in_band", "note": None}]}


@pytest.fixture
def client(monkeypatch):
    async def _session():
        yield AsyncMock()

    async def _principal():
        return Principal(user_id=UUID(int=2), email="a@example.com", organization_id=ORG,
                         session_id=None, issued_at=datetime.now(timezone.utc),
                         password_changed_at=0, role="admin", can_ingest=True, building_ids=None)

    calls: dict = {}

    async def _ids(session, s, building_id):
        return [B1]

    async def _resolve(session, asset_id):
        return asset_id

    async def _detail(session, *, asset_id, building_ids):
        calls["detail"] = {"asset_id": asset_id, "building_ids": building_ids}
        return DETAIL if asset_id == "a1" else {"ok": False, "reason": "not_found_or_not_in_scope"}

    async def _bms(session, **kw):
        calls["bms"] = kw
        return {"status": "found", "kind": "asset_readings", "points": 96}

    async def _bill(session, **kw):
        calls["bill"] = kw
        return {"status": "found", "total": {"comparable": False}}

    async def _dd(session, **kw):
        calls["dd"] = kw
        return {"status": "found", "source": "open-meteo", "months": []}

    monkeypatch.setattr(energy_routes.position_svc, "building_ids_for", _ids)
    monkeypatch.setattr(energy_routes.anom_svc, "resolve_asset", _resolve)
    monkeypatch.setattr(energy_routes.ai_svc, "asset_detail", _detail)
    monkeypatch.setattr(energy_routes.asset_sources, "bms_trend", _bms)
    monkeypatch.setattr(energy_routes.asset_sources, "utility_bill", _bill)
    monkeypatch.setattr(energy_routes.asset_sources, "degree_days", _dd)
    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[auth_routes.current_principal] = _principal
    try:
        yield TestClient(app), calls
    finally:
        app.dependency_overrides.clear()


def test_bms_trend_hands_the_engine_the_bands_the_asset_read_resolved(client):
    c, calls = client
    r = c.get("/api/energy/assets/a1/bms-trend", params={"weeks": 4})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["written"] is False and body["points"] == 96
    assert body["asset"]["asset_code"] == "B-301-BOILER-01"
    assert calls["bms"]["bands"] == {"pressure": {"lo": 3.0, "hi": 5.5, "unit": "bar", "note": None}}
    assert calls["detail"]["building_ids"] == [B1]


def test_the_utility_bill_is_read_for_the_assets_building(client):
    c, calls = client
    r = c.get("/api/energy/assets/a1/utility-bill")
    assert r.status_code == 200, r.text
    assert calls["bill"]["building_id"] == str(B1)
    assert r.json()["written"] is False


@pytest.mark.parametrize("path", ["bms-trend", "utility-bill"])
def test_an_asset_outside_your_buildings_is_404(client, path):
    c, _ = client
    assert c.get(f"/api/energy/assets/elsewhere/{path}").status_code == 404


@pytest.mark.parametrize("weeks", [0, 53])
def test_the_window_is_bounded(client, weeks):
    c, _ = client
    assert c.get("/api/energy/assets/a1/bms-trend", params={"weeks": weeks}).status_code == 422


def test_degree_days_are_read_monthly_from_the_first_of_the_month(client):
    c, calls = client
    r = c.get("/api/energy/weather/degree-days", params={"building_id": str(B1), "months": 3})
    assert r.status_code == 200, r.text
    kw = calls["dd"]
    assert kw["monthly"] is True and kw["start"].day == 1 and kw["building_id"] == str(B1)
    assert (kw["today"].year * 12 + kw["today"].month) - (kw["start"].year * 12 + kw["start"].month) == 2
    assert r.json()["written"] is False


def test_degree_days_for_a_building_outside_your_buildings_is_404(client):
    c, calls = client
    r = c.get("/api/energy/weather/degree-days",
              params={"building_id": "33333333-3333-3333-3333-333333333333"})
    assert r.status_code == 404
    assert "dd" not in calls


@pytest.mark.parametrize("months", [0, 37])
def test_the_degree_day_window_is_bounded(client, months):
    c, _ = client
    r = c.get("/api/energy/weather/degree-days", params={"building_id": str(B1), "months": months})
    assert r.status_code == 422


def test_degree_days_over_weeks_cover_the_same_window_as_the_walk(client):
    # The dock fetches this beside the walk's weather line; a months window starting on the 1st
    # would put figures in the summary that differ from the line right above it.
    c, calls = client
    r = c.get("/api/energy/weather/degree-days", params={"building_id": str(B1), "weeks": 8})
    assert r.status_code == 200, r.text
    kw = calls["dd"]
    assert (kw["today"] - kw["start"]).days == 56


def test_degree_days_weeks_are_bounded(client):
    c, _ = client
    assert c.get("/api/energy/weather/degree-days",
                 params={"building_id": str(B1), "weeks": 53}).status_code == 422
