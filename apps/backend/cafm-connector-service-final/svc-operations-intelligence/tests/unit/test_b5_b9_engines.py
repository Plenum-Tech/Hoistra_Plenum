"""B5–B9: the EPC band and MEES, Energy Star and LL97, filings, chiller kW/RT — the pure
parts, and the routes' access boundary.

Each engine's arithmetic is pinned against a figure that can be checked by hand: an office at
the national median scores 50; a building using a quarter less scores about 75; LL97 emissions
multiply out from the published coefficients; LL84 for 2025 is due 1 May 2026; a Green Mark
award lasts three years.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from src.api.routes import auth as auth_routes
from src.app import app
from src.db import get_session
from src.engines.auth.tokens import Principal
from src.engines.compliance import epc_rating as E
from src.engines.compliance import filings as F
from src.engines.energy import us_ratings as U

# ── B5 · EPC band ───────────────────────────────────────────────────────────────────

class TestEpcBand:
    @pytest.mark.parametrize("raw,expect", [
        ("C", ("C", None)), ("c 63", ("C", 63)), ("Band D", ("D", None)), ("A+", ("A", None)),
        ("Asset rating: C 63", ("C", 63)), ("This building's energy rating is D", ("D", None)),
        ("Energy rating B (42)", ("B", 42)), ("Asset rating (A-G) and score | E | 118", ("E", 118)),
        ("63", ("C", 63)), ("Rating: F", ("F", None)), ("no rating here", (None, None)), ("", (None, None)),
    ])
    def test_parses_the_ways_a_certificate_writes_it(self, raw, expect):
        assert E.parse_energy_rating(raw) == expect

    def test_score_alone_implies_the_band(self):
        assert E.band_for_score(0) == "A" and E.band_for_score(63) == "C" and E.band_for_score(151) == "G"

    def test_fields_dict_is_read_under_any_of_the_pack_labels(self):
        assert E.rating_from_fields({"Asset rating (A-G) and score": "C 63"}) == ("C", 63)
        assert E.rating_from_fields({"energy_rating": "d", "energy_score": "88"}) == ("D", 88)
        assert E.rating_from_fields({"issue_date": "2024-01-01"}) == (None, None)

    def test_the_register_band_is_read_from_verification_evidence(self):
        meta = {"verification": {"evidence": {"current_energy_rating": "e"}}}
        assert E.rating_from_verification(meta) == "E"
        assert E.rating_from_verification({}) is None
        assert E.rating_from_verification(None) is None

    def test_only_energy_certificate_types_carry_a_band(self):
        assert E.is_epc_type("EPC") and E.is_epc_type("dec")
        assert not E.is_epc_type("FIRE_ALARM") and not E.is_epc_type(None)

    @pytest.mark.parametrize("band,status", [
        ("A", "compliant"), ("B", "compliant"), ("C", "at_risk_2030"), ("E", "at_risk_2030"),
        ("F", "below_minimum"), ("G", "below_minimum"), (None, "unknown"),
    ])
    def test_mees_position(self, band, status):
        assert E.mees_position(band)["status"] == status

    def test_f_is_below_e_and_e_is_not(self):
        assert E.below("F", "E") is True and E.below("E", "E") is False and E.below(None, "E") is None


# ── B6 · Energy Star estimate ───────────────────────────────────────────────────────

def _kwh_for_source_eui(source_eui: float, gfa_m2: float, factor: float = 2.80) -> float:
    return source_eui / factor / U.KWH_TO_KBTU * gfa_m2 * U.M2_TO_FT2


class TestEnergyStar:
    def test_a_building_at_the_national_median_scores_fifty(self):
        kwh = _kwh_for_source_eui(U.MEDIAN_SOURCE_EUI["office"], 8000)
        out = U.energy_star_estimate(kwh_by_fuel={"electricity": kwh}, gfa_m2=8000, property_type="office")
        assert out["ok"] and out["score"] == 50 and out["estimate"] is True

    def test_a_quarter_below_median_is_about_certification(self):
        # EPA's office table puts the 75th-percentile building roughly a quarter below median.
        kwh = _kwh_for_source_eui(U.MEDIAN_SOURCE_EUI["office"] * 0.75, 8000)
        out = U.energy_star_estimate(kwh_by_fuel={"electricity": kwh}, gfa_m2=8000, property_type="office")
        assert 72 <= out["score"] <= 78
        assert out["status"] == "certifiable" or out["points_short"] <= 3

    def test_more_energy_means_a_lower_score_and_a_reduction_to_certify(self):
        a = U.energy_star_estimate(kwh_by_fuel={"electricity": 1_000_000}, gfa_m2=8000, property_type="office")
        b = U.energy_star_estimate(kwh_by_fuel={"electricity": 1_500_000}, gfa_m2=8000, property_type="office")
        assert b["score"] < a["score"] and b["reduction_to_certify_pct"] > a["reduction_to_certify_pct"]

    def test_fewer_than_twelve_months_is_projected_and_annualised(self):
        full = U.energy_star_estimate(kwh_by_fuel={"electricity": 1_200_000}, gfa_m2=8000, property_type="office", months=12)
        half = U.energy_star_estimate(kwh_by_fuel={"electricity": 600_000}, gfa_m2=8000, property_type="office", months=6)
        assert half["score"] == full["score"] and half["basis"] == "projected" and full["basis"] == "actual"

    def test_gas_is_weighted_by_its_source_factor(self):
        e = U.energy_star_estimate(kwh_by_fuel={"electricity": 100_000}, gfa_m2=1000, property_type="office")
        g = U.energy_star_estimate(kwh_by_fuel={"natural_gas": 100_000}, gfa_m2=1000, property_type="office")
        assert e["source_eui_kbtu_ft2"] > g["source_eui_kbtu_ft2"]
        assert abs(e["source_eui_kbtu_ft2"] / g["source_eui_kbtu_ft2"] - 2.80 / 1.05) < 1e-2

    def test_property_type_mapping(self):
        assert U.property_type_for("Commercial") == "office" and U.property_type_for("Hospital") == "hospital"
        assert U.property_type_for("unknown thing") == "other"


# ── B7 · LL97 ───────────────────────────────────────────────────────────────────────

class TestLL97:
    def test_emissions_multiply_out_from_the_published_coefficients(self):
        out = U.ll97_position(kwh_by_fuel={"electricity": 1_000_000, "natural_gas": 100_000}, gfa_m2=10_000,
                              year=2025, primary_use="Commercial")
        expected = 1_000_000 * 0.000288962 + 100_000 * U.KWH_TO_KBTU * 0.00005311
        assert abs(out["emissions_tco2e"] - expected) < 0.01
        assert out["compliance_period"] == "2024-2029" and out["occupancy_groups"][0]["group"] == "B"

    def test_the_cap_is_the_group_limit_times_area(self):
        out = U.ll97_position(kwh_by_fuel={"electricity": 1}, gfa_m2=10_000, year=2025, primary_use="Retail")
        assert abs(out["cap_tco2e"] - 0.01181 * 10_000 * U.M2_TO_FT2) < 0.01

    def test_over_the_cap_is_penalised_at_268_per_tonne(self):
        out = U.ll97_position(kwh_by_fuel={"electricity": 5_000_000}, gfa_m2=1_000, year=2025, primary_use="Commercial")
        assert out["status"] == "over" and out["over_tco2e"] > 0
        assert abs(out["penalty_usd"] - out["over_tco2e"] * 268.0) < 0.5  # both rounded

    def test_2030_limits_are_tighter(self):
        a = U.ll97_position(kwh_by_fuel={"electricity": 1}, gfa_m2=10_000, year=2025, primary_use="Commercial")
        b = U.ll97_position(kwh_by_fuel={"electricity": 1}, gfa_m2=10_000, year=2031, primary_use="Commercial")
        assert b["cap_tco2e"] < a["cap_tco2e"]

    def test_mixed_use_blends_the_limits_by_area_share(self):
        mix = [{"use": "Commercial", "pct": 50}, {"use": "Residential", "pct": 50}]
        out = U.ll97_position(kwh_by_fuel={"electricity": 1}, gfa_m2=10_000, year=2025, use_mix=mix)
        blended = (0.00846 + 0.00675) / 2
        assert abs(out["limit_tco2e_per_ft2"] - blended) < 1e-9

    def test_years_outside_the_periods_are_refused(self):
        assert U.ll97_position(kwh_by_fuel={"electricity": 1}, gfa_m2=1, year=2023)["ok"] is False


# ── B8 · filings ────────────────────────────────────────────────────────────────────

class TestFilings:
    def test_due_dates(self):
        assert F.due_date_for("LL84", 2025) == date(2026, 5, 1)
        assert F.due_date_for("BCA_BENCHMARKING", 2025) == date(2026, 9, 30)
        assert F.due_date_for("GREEN_MARK", 2025) is None

    def test_ll84_position_through_the_year(self):
        # On 1 Mar 2026 the 2024 report (due May 2025) must be on file; 2025 is not due yet.
        assert F.position_for("LL84", [], today=date(2026, 3, 1))["period_year"] == 2024
        assert F.position_for("LL84", [], today=date(2026, 3, 1))["status"] == "overdue"
        filed_2025 = [{"scheme": "LL84", "period_year": 2025, "status": "filed", "valid_until": None, "certification_level": None}]
        assert F.position_for("LL84", filed_2025, today=date(2026, 6, 1))["status"] == "filed"
        filed_2024 = [{"scheme": "LL84", "period_year": 2024, "status": "filed", "valid_until": None, "certification_level": None}]
        assert F.position_for("LL84", filed_2024, today=date(2026, 6, 1))["status"] == "overdue"

    def test_green_mark_is_a_certification_with_a_level_and_a_life(self):
        award = [{"scheme": "GREEN_MARK", "period_year": 2024, "status": "certified", "certification_level": "Gold",
                  "valid_until": "2027-06-01"}]
        pos = F.position_for("GREEN_MARK", award, today=date(2026, 1, 1))
        assert pos["status"] == "certified" and pos["level"] == "Gold"
        assert F.position_for("GREEN_MARK", award, today=date(2028, 1, 1))["status"] == "lapsed"
        assert F.position_for("GREEN_MARK", [], today=date(2026, 1, 1))["status"] == "none"

    def test_unknown_scheme_and_bad_level_are_refused(self):
        import asyncio
        with pytest.raises(F.FilingError) as e:
            asyncio.run(F.record_filing(None, organization_id=None, building_id=uuid4(), scheme="LL9999", period_year=2025))
        assert e.value.reason == "unknown_scheme"
        with pytest.raises(F.FilingError) as e:
            asyncio.run(F.record_filing(None, organization_id=None, building_id=uuid4(), scheme="GREEN_MARK",
                                        period_year=2025, certification_level="Diamond"))
        assert e.value.reason == "bad_level"


# ── the routes hold the boundary ────────────────────────────────────────────────────

ORG = UUID("11111111-1111-5111-8111-111111111111")
MINE, THEIRS = uuid4(), uuid4()


def principal(role="user", buildings=None, can_ingest=False):
    return Principal(user_id=uuid4(), email="x@example.com", organization_id=ORG, session_id=None,
                     issued_at=datetime.now(timezone.utc), password_changed_at=0, role=role,
                     can_ingest=can_ingest, building_ids=buildings)


class Exploding:
    async def execute(self, *a, **k):
        raise AssertionError("route reached the database before its access check")
    def add(self, *a, **k):
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


def as_(p):
    async def _dep():
        return p
    app.dependency_overrides[auth_routes.current_principal] = _dep


@pytest.mark.parametrize("method,path,body", [
    ("GET", f"/api/compliance/mees?building_id={THEIRS}", None),
    ("GET", f"/api/compliance/filings?building_id={THEIRS}", None),
    ("GET", f"/api/energy/ratings?building_id={THEIRS}", None),
    ("GET", f"/api/energy/ratings/position?country_code=US&building_id={THEIRS}", None),
    ("POST", "/api/energy/ratings/compute", {"building_id": str(THEIRS), "scheme": "ll97"}),
    ("POST", "/api/compliance/filings", {"building_id": str(THEIRS), "scheme": "LL84", "period_year": 2025}),
])
def test_a_user_cannot_touch_another_building_through_the_new_routes(client, method, path, body):
    as_(principal(buildings=(MINE,), can_ingest=True))
    r = client.request(method, path, json=body)
    assert r.status_code == 403, (path, r.status_code, r.text[:200])
    assert r.json()["detail"]["reason"] == "building_not_allocated"


@pytest.mark.parametrize("path,body", [
    ("/api/energy/weather/degree-days", {"building_id": str(MINE), "months": []}),
    ("/api/energy/bms/trends", {"building_id": str(MINE), "samples": []}),
    (f"/api/energy/chillers/{uuid4()}/readings", {"readings": []}),
])
def test_the_new_ingest_routes_need_the_ingest_right(client, path, body):
    as_(principal(buildings=(MINE,), can_ingest=False))
    r = client.post(path, json=body)
    assert r.status_code == 403, (path, r.status_code, r.text[:200])
    assert r.json()["detail"]["reason"] == "cannot_ingest"


def test_a_bad_scheme_is_a_400_not_a_500(client):
    as_(principal(role="admin", can_ingest=True))
    r = client.post("/api/energy/ratings/compute", json={"building_id": str(MINE), "scheme": "leed"})
    assert r.status_code == 400 and r.json()["detail"]["reason"] == "bad_scheme"


def test_cert_to_dict_carries_the_band_and_the_mees_word():
    from src.engines.compliance.certificates import cert_to_dict
    c = SimpleNamespace(
        id=uuid4(), organization_id=ORG, org_id=None, certificate_ref=None, certificate_type_code="EPC",
        cert_type=None, certificate_number="1234-5678", cert_scope="Building", asset_id=None, site_id=None,
        site_ref=None, building_name="Riverside", building_reference=None, building_id=None, vendor_id=None,
        issue_date=None, expiry_date=None, next_due_date=None, inspection_frequency_months=None,
        inspector_name=None, inspector_accreditation_number=None, issuer=None, result=None,
        defects_found=None, remedial_actions=None, remedial_status=None, status="valid", days_to_expiry=None,
        insurance_risk_flag=False, authenticity_warning=None, country_code="UK", state=None, region=None,
        document_id=None, source_document_id=None, raw_metadata={}, energy_rating="F", energy_score=132,
        created_at=None, updated_at=None,
    )
    d = cert_to_dict(c)  # type: ignore[arg-type]
    assert d["energy_rating"] == "F" and d["energy_score"] == 132 and d["mees"] == "below_minimum"


# ── two failures the unit tests could not see, and now can ──────────────────────────

def test_a_bad_filing_scheme_is_a_400_not_a_500(client):
    # The compliance router raised HTTPException without importing it: every refusal on
    # these routes became a 500 with a NameError behind it.
    as_(principal(role="admin", can_ingest=True))
    r = client.post("/api/compliance/filings",
                    json={"building_id": str(MINE), "scheme": "LL9999", "period_year": 2025})
    assert r.status_code == 400, r.text[:200]
    assert r.json()["detail"]["reason"] == "unknown_scheme"


@pytest.mark.parametrize("module", ["compliance", "energy", "contract_performance", "approvals",
                                    "admin", "superadmin", "auth"])
def test_every_router_imports_what_it_raises(module):
    import ast
    from pathlib import Path
    src = (Path(__file__).resolve().parents[2] / "src" / "api" / "routes" / f"{module}.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    # names the module can legitimately raise: what it imports, and what it defines itself
    imported = {a.asname or a.name for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))
                for a in n.names}
    imported |= {n.name for n in ast.walk(tree)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
    raised = {n.exc.func.id for n in ast.walk(tree)
              if isinstance(n, ast.Raise) and isinstance(n.exc, ast.Call) and isinstance(n.exc.func, ast.Name)}
    import builtins
    missing = {name for name in raised if name not in imported and not hasattr(builtins, name)}
    assert not missing, (module, sorted(missing))


def test_a_date_parameter_is_bound_as_a_date_not_a_string():
    """asyncpg binds CAST(:p AS date) as a date and rejects a str ("no attribute 'toordinal'").

    The twelve-month consumption window passed ISO strings, so every Energy Star and LL97
    computation answered 500 on the deployed app while passing every unit test.
    """
    import asyncio
    from datetime import date as _date
    from src.engines.energy import us_ratings as U

    seen: dict[str, object] = {}

    class Result:
        def mappings(self): return self
        def all(self): return []

    class Session:
        async def execute(self, _stmt, params=None):
            seen.update(params or {})
            return Result()

    asyncio.run(U.consumption_for_building(Session(), building_id=uuid4(), months=12,
                                           end=_date(2026, 9, 11)))
    assert isinstance(seen["s"], _date) and isinstance(seen["e"], _date), seen
    assert not isinstance(seen["s"], str)


def test_no_sql_literal_carries_a_python_comment():
    """A `#` line inside a text() string is SQL, not a comment — and `:name` in it is a bind.

    A note added above the closing quotes of the consumption query ended up inside the SQL,
    and the words "CAST(:p AS date)" in it became a fourth bind parameter the caller never
    supplied: every rating computation answered 500. Postgres has no `#` comment syntax, so
    a `#` line inside a SQL literal is always this mistake.
    """
    import ast
    from pathlib import Path

    offenders = []
    for path in (Path(__file__).resolve().parents[2] / "src").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "text"):
                continue
            for arg in node.args:
                if not (isinstance(arg, ast.Constant) and isinstance(arg.value, str)):
                    continue
                for i, line in enumerate(arg.value.splitlines()):
                    if line.lstrip().startswith("#"):
                        offenders.append(f"{path.name}:{node.lineno}+{i}: {line.strip()[:60]}")
    assert not offenders, offenders


def test_the_consumption_window_binds_exactly_its_three_parameters():
    import inspect
    from sqlalchemy import text as sa_text
    from src.engines.energy import us_ratings as U

    src = inspect.getsource(U.consumption_for_building)
    sql = src[src.index('text("""') + 8: src.index('"""), {')]
    assert set(sa_text(sql)._bindparams) == {"b", "s", "e"}, sorted(sa_text(sql)._bindparams)
