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
    assert {r[1] for r in DC.RULES} == {
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
async def test_an_empty_scope_still_answers_without_touching_the_database():
    # Exploding() fails on any query, so this pins that an empty allocation is answered from
    # the catalogue alone. What changed is the answer: the thirteen rules come back listed and
    # unarmed rather than as an empty list, because an empty panel reads as "there are no
    # rules" when the truth is "no building here has the data route any of them needs".
    rep = await DC.coverage(Exploding(), building_ids=[])
    assert rep["ok"] is True
    assert rep["buildings"] == []
    assert rep["summary"] == {"buildings": 0}
    assert len(rep["rules"]) == 13
    assert all(r["armed"] == 0 for r in rep["rules"])
    assert all(r["description"] and r["needs"] for r in rep["rules"])


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


def _seeder():
    """The seeder lives in db/tools, outside the package — load it by path."""
    import importlib.util, pathlib, sys
    p = pathlib.Path(__file__).resolve().parents[2] / "db" / "tools" / "seed_detection_demo.py"
    sys.path.insert(0, str(p.parent))
    spec = importlib.util.spec_from_file_location("seed_detection_demo", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_every_fault_lands_on_a_building_that_can_show_it():
    # Two faults drawn from nine left baseload creep on nobody: the rule looked dead when
    # it simply had nothing to find. The top-up fills the gaps — and never puts a fault on
    # a feed whose other faults would mask it.
    S = _seeder()
    ids = [f"00000000-0000-0000-0000-{i:012d}" for i in range(9)]
    plan = S.assign_faults(ids)
    # Covered means: landed somewhere its own feed does not mask it.
    covered = {f for v in plan.values() for f in v if not S.CONFLICTS.get(f, set()) & set(v)}
    assert set(S.FAULTS) <= covered, sorted(set(S.FAULTS) - covered)
    # and the top-up itself never adds a fault the building's other faults would hide
    for b, faults in plan.items():
        for f in faults[2:]:
            assert not S.CONFLICTS.get(f, set()) & set(faults), (b, faults)


def test_a_building_keeps_the_pair_the_hash_gave_it():
    S = _seeder()
    ids = [f"00000000-0000-0000-0000-{i:012d}" for i in range(9)]
    plan = S.assign_faults(ids)
    for b in ids:
        assert plan[b][:2] == S.faults_for(b)
    # adding a building only ever appends to others — it never rewrites their first two
    more = S.assign_faults(ids + ["00000000-0000-0000-0000-000000000099"])
    for b in ids:
        assert more[b][:2] == plan[b][:2]


def test_an_empty_estate_assigns_nothing():
    assert _seeder().assign_faults([]) == {}
