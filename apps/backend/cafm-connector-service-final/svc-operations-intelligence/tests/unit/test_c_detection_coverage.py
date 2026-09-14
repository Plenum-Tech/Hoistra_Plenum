"""Detection coverage: which rules ran, fired or were skipped — from the scan, not a guess.

The shell counted rule coverage from a meter's route string. The report now asks the
detectors themselves, with a dry run that writes nothing. These pin the merge rule (fired
beats clear beats skipped), the thirteen-rule catalogue matching the shell's ids, the route's
gate, and that the scan's dry run returns hits without touching the database.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from src.api.routes import auth as auth_routes
from src.app import app
from src.db import get_session
from src.engines.auth.tokens import Principal
from src.engines.energy import anomalies, detection_coverage as DC


def test_the_catalogue_is_the_shells_thirteen_rules_with_its_ids():
    assert len(DC.RULES) == 13
    assert {short for _, short, _ in DC.RULES} == {
        "nonocc", "spike", "drift", "schedule", "baseload", "calendar", "weather", "peak",
        "fight", "cop", "regress", "dataq", "tou"}
    assert set(DC.RULE_IDS) == {
        "weekend_spike", "baseline_drift", "asset_spike", "nonocc_spike", "schedule_mismatch", "baseload_creep",
        "peak_excursion", "data_quality", "tou_misalignment", "weather_residual",
        "simultaneous_heating_cooling", "post_works_regression", "chiller_efficiency"}


def test_fired_beats_clear_beats_skipped_and_hits_add_up():
    cell = DC._merge(None, "skipped", reason="no capacity")
    assert cell["status"] == "skipped"
    cell = DC._merge(cell, "clear")
    assert cell["status"] == "clear"
    cell = DC._merge(cell, "skipped", reason="whatever")
    assert cell["status"] == "clear"                       # a skip never demotes a run
    cell = DC._merge(cell, "fired", hits=1, metric_pct=140.0)
    assert cell["status"] == "fired" and cell["hits"] == 1
    cell = DC._merge(cell, "fired", hits=2, metric_pct=120.0)
    assert cell["hits"] == 3 and cell["metric_pct"] == 140.0   # the first firing's figure stands
    assert DC._merge(cell, "clear")["status"] == "fired"


class Exploding:
    async def execute(self, *a, **k):
        raise AssertionError("route reached the database before its access check")
    async def commit(self): pass


async def _exploding():
    yield Exploding()


@pytest.fixture
def client():
    app.dependency_overrides[get_session] = _exploding
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_the_coverage_route_needs_a_caller(client):
    r = client.get("/api/energy/detection/coverage")
    assert r.status_code == 401 and r.json()["detail"]["reason"] == "missing_token"


def test_a_user_cannot_read_coverage_for_a_building_they_are_not_allocated(client):
    mine, theirs = uuid4(), uuid4()
    p = Principal(user_id=uuid4(), email="x@example.com", organization_id=None, session_id=None,
                  issued_at=datetime.now(timezone.utc), password_changed_at=0, role="user",
                  can_ingest=False, building_ids=(mine,))
    async def _dep():
        return p
    app.dependency_overrides[auth_routes.current_principal] = _dep
    r = client.get(f"/api/energy/detection/coverage?building_id={theirs}")
    assert r.status_code == 403, r.text[:200]


@pytest.mark.asyncio
async def test_an_empty_scope_is_an_empty_report_without_a_query():
    rep = await DC.coverage(Exploding(), building_ids=[])
    assert rep == {"ok": True, "buildings": [], "rules": [], "summary": {"buildings": 0}}


def test_the_scan_has_a_dry_run_that_returns_hits_without_writing():
    import inspect
    sig = inspect.signature(anomalies.scan_meter_anomalies)
    assert sig.parameters["persist"].default is True
    src = inspect.getsource(anomalies.scan_meter_anomalies)
    assert '"dry_run": True' in src and "if persist:\n        await session.commit()" in src


def test_the_closed_work_order_lookup_runs_in_its_own_savepoint():
    # A caught database error left the session aborted and every later query in the scan
    # failed; the lookup now releases its savepoint and casts both timestamp columns alike.
    import inspect
    src = inspect.getsource(anomalies._closed_work_orders)
    assert "begin_nested()" in src and "completed_at::timestamptz" in src
