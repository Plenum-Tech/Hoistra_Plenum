"""Benchmark validation: every market's rule, applied from what is actually on record.

The Energy page said "no EUI reading on record" over a building with thirteen months of
half-hourly readings, because nothing ran the rules. These pin the pure parts of the run —
which rule each market gets, how an EUI is annualised from partial data and labelled, that a
single-fuel EUI reads against that fuel's TM46 figure, how the excess is priced in the
market's currency — and that the routes are gated and the scheduler keeps its interval.
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
from src.engines.energy import benchmarks as BM
from src.engines.reports import scheduler as S


# ── which rule ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("cc,rule", [
    ("UK", "tm46"), ("GB", "tm46"), ("US", "ll97"), ("SG", "bca"), ("AE", "portfolio_rolling"),
    ("FR", "portfolio_rolling"), (None, "portfolio_rolling"),
])
def test_each_market_gets_its_own_rule_never_the_rolling_median_by_accident(cc, rule):
    assert BM._rule_for(cc) == rule


# ── the EUI ──────────────────────────────────────────────────────────────────

def test_an_eui_is_annualised_from_the_months_that_have_data():
    d = BM.derive_eui(total_kwh=60_000, months=6, gfa_m2=1_000)
    assert d["eui_kwh_per_m2"] == 120.0 and d["annual_kwh"] == 120_000 and d["provisional"] is False


def test_fewer_than_three_months_is_provisional_not_hidden():
    d = BM.derive_eui(total_kwh=10_000, months=2, gfa_m2=500)
    assert d["eui_kwh_per_m2"] == 120.0 and d["provisional"] is True


def test_no_area_or_no_use_means_no_eui():
    assert BM.derive_eui(total_kwh=10_000, months=6, gfa_m2=None) is None
    assert BM.derive_eui(total_kwh=0, months=6, gfa_m2=500) is None


@pytest.mark.parametrize("fuels,expected", [
    ({"electricity": 100.0}, "electricity"),
    ({"gas": 100.0}, "gas"),
    ({"electricity": 100.0, "gas": 50.0}, "combined"),
    ({"electricity": 100.0, "gas": 0.0}, "electricity"),   # a fuel with nothing read is not a fuel
    ({}, "combined"),
])
def test_the_fuel_measured_is_what_the_benchmark_is_chosen_for(fuels, expected):
    assert BM.fuel_for(fuels) == expected


# ── the benchmark ────────────────────────────────────────────────────────────

def test_uk_reads_tm46_for_the_fuel_measured():
    elec = BM.benchmark_for(cc="UK", use="office", recorded=None, peers=[], fuel="electricity")
    comb = BM.benchmark_for(cc="UK", use="office", recorded=None, peers=[], fuel="combined")
    assert elec["basis"] == "tm46_electricity_by_use" and comb["basis"] == "tm46_combined_by_use"
    assert elec["value"] < comb["value"]          # electricity alone is a fraction of the whole


def test_uk_without_a_mappable_use_falls_back_to_the_recorded_figure_then_says_why():
    assert BM.benchmark_for(cc="UK", use=None, recorded=210.0, peers=[]) == {
        "value": 210.0, "basis": "recorded", "building_type": None}
    missing = BM.benchmark_for(cc="UK", use=None, recorded=None, peers=[])
    assert missing["value"] is None and missing["reason"] == "use_type"


def test_singapore_reads_its_own_figure_or_the_bca_office_reference():
    assert BM.benchmark_for(cc="SG", use="office", recorded=180.0, peers=[])["basis"] == "recorded"
    b = BM.benchmark_for(cc="SG", use="office", recorded=None, peers=[])
    assert b == {"value": BM.BCA_OFFICE_REFERENCE_KWH_M2, "basis": "bca_office_reference"}


def test_the_us_rule_is_not_a_kwh_figure():
    b = BM.benchmark_for(cc="US", use="office", recorded=None, peers=[])
    assert b["value"] is None and b["basis"] == "ll97_energy_star" and b["reason"] == "us_rule_not_kwh_m2"


def test_the_uae_rule_is_the_median_of_the_others_or_nothing_yet():
    b = BM.benchmark_for(cc="AE", use="office", recorded=None, peers=[200.0, 240.0, 260.0])
    assert b == {"value": 240.0, "basis": "portfolio_rolling", "comparables": 3}
    one = BM.benchmark_for(cc="AE", use="office", recorded=None, peers=[200.0])
    assert one["value"] is None and one["reason"] == "comparable_buildings" and one["comparables"] == 1
    # A recorded figure stands in until there are comparables, and is labelled as recorded.
    assert BM.benchmark_for(cc="AE", use="office", recorded=228.0, peers=[200.0])["basis"] == "recorded"


# ── the money ────────────────────────────────────────────────────────────────

def test_excess_is_priced_in_the_markets_currency_at_its_tariff():
    m = BM.price_excess(eui=250.0, bench=200.0, gfa_m2=1_000, cc="AE")
    assert m["deviation_pct"] == 25.0 and m["excess_kwh"] == 50_000
    assert m["currency"] == "AED" and m["priced"] is True and m["cost"] == round(50_000 * m["tariff"])
    uk = BM.price_excess(eui=250.0, bench=200.0, gfa_m2=1_000, cc="UK")
    assert uk["currency"] == "GBP" and uk["cost"] != m["cost"]


def test_a_building_under_its_benchmark_costs_nothing_and_says_so():
    m = BM.price_excess(eui=150.0, bench=200.0, gfa_m2=1_000, cc="UK")
    assert m["deviation_pct"] == -25.0 and m["excess_kwh"] == 0 and m["cost"] == 0


def test_nothing_to_compare_is_not_priced():
    m = BM.price_excess(eui=None, bench=200.0, gfa_m2=1_000, cc="SG")
    assert m["priced"] is False and m["cost"] is None and m["currency"] == "SGD"


# ── the routes and the clock ─────────────────────────────────────────────────

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


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/energy/benchmarks/validation"), ("POST", "/api/energy/benchmarks/validate"),
])
def test_the_validation_routes_need_a_caller(client, method, path):
    r = client.request(method, path)
    assert r.status_code == 401 and r.json()["detail"]["reason"] == "missing_token"


def test_a_user_cannot_validate_a_building_they_are_not_allocated(client):
    mine, theirs = uuid4(), uuid4()
    p = Principal(user_id=uuid4(), email="x@example.com", organization_id=None, session_id=None,
                  issued_at=datetime.now(timezone.utc), password_changed_at=0, role="user",
                  can_ingest=False, building_ids=(mine,))
    async def _dep():
        return p
    app.dependency_overrides[auth_routes.current_principal] = _dep
    r = client.get(f"/api/energy/benchmarks/validation?building_id={theirs}")
    assert r.status_code == 403, r.text[:200]


@pytest.mark.asyncio
async def test_the_scheduler_runs_the_validation_once_per_interval(monkeypatch):
    calls = []

    async def fake_validate(session, **kw):
        calls.append(kw)
        return {"summary": {"buildings": 0, "validated": 0, "derived_from_readings": 0}, "snapshots_written": 0}

    class Q:
        def scalars(self): return self
        def all(self): return []

    class Sess:
        async def execute(self, *a, **k): return Q()
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

    from src.engines.energy import benchmarks as bench_engine
    monkeypatch.setattr(bench_engine, "validate", fake_validate)
    monkeypatch.setattr(S, "AsyncSessionLocal", lambda: Sess())
    S._benchmarks_last_run = None
    assert await S.validate_benchmarks_if_due() is not None
    assert await S.validate_benchmarks_if_due() is None       # inside the interval: not again
    assert await S.validate_benchmarks_if_due(force=True) is not None
    assert len(calls) == 2 and all(c["persist"] is True and c["organization_id"] is None for c in calls)
    S._benchmarks_last_run = None


def test_validation_is_on_by_default_and_daily():
    from src.config import settings
    assert settings.benchmark_validation_enabled is True
    assert settings.benchmark_validation_every_hours == 24
