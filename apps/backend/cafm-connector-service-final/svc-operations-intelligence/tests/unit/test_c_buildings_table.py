"""Building table rows — pure shaping, no DB."""
from __future__ import annotations

from uuid import uuid4

from src.engines.energy.buildings import (
    apply_rolling_benchmarks,
    parse_use_mix,
    country_code_for,
    metering_for,
    shape_building_row,
    tm46_type_for,
)


def _site(**over):
    base = {
        "key": "SITE-001",
        "alt_id": None,
        "name": "Bishopsgate Tower",
        "code": "B-001",
        "country": "United Kingdom",
        "city": "London",
        "region": "Greater London",
        "postcode": "EC2N",
        "status": "active",
        "site_type": "Commercial",
        "floors": "34",
        "gfa_sqm": "38,276.5",
    }
    base.update(over)
    return base


def test_country_aliases():
    assert country_code_for("United Kingdom") == "UK"
    assert country_code_for("uae") == "AE"
    assert country_code_for("United Arab Emirates") == "AE"
    assert country_code_for("Singapore") == "SG"
    assert country_code_for("US") == "US"
    assert country_code_for("") is None


def test_tm46_type_mapping():
    assert tm46_type_for("Commercial") == "office"
    assert tm46_type_for("Mixed use tower") == "office"
    assert tm46_type_for("Hospital") == "hospital"
    assert tm46_type_for("Car park") is None


def test_uk_row_reads_against_tm46_by_site_type():
    row = shape_building_row(_site(), profile=None, snapshot=None, meters=[])
    assert row["site_id"] == "SITE-001" and row["site_uuid"] is None
    assert row["country_code"] == "UK"
    assert row["benchmark_standard"] == "CIBSE TM46"
    assert row["benchmark_standing"] == "guidance"
    assert row["building_type"] == "office"
    assert row["benchmark_source"] == "tm46_combined_by_use"
    assert row["benchmark_kwh_per_m2"] and row["benchmark_kwh_per_m2"] > 0
    assert row["floors"] == 34 and row["gfa_sqm"] == 38276.5
    assert row["eui_kwh_per_m2"] is None
    assert row["metering_granularity"] == "none"
    assert set(row["completeness_missing"]) == {"energy_profile", "meters", "eui"}
    assert row["record_completeness_pct"] == 67


def test_snapshot_benchmark_wins_and_deviation_kept():
    snap = {"eui_kwh_per_m2": 214.0, "benchmark_kwh_per_m2": 215.0, "deviation_pct": -0.47, "period_start": "2026-01-01", "period_end": "2026-06-30", "meter_type": "electricity"}
    prof = {"building_type": "office", "gia_m2": 38000.0, "tm46_electricity_benchmark": 95.0}
    row = shape_building_row(_site(gfa_sqm=None), profile=prof, snapshot=snap, meters=[{"mpan": "1200", "is_sub_meter": False, "raw_metadata": {}}])
    assert row["benchmark_source"] == "eui_snapshot"
    assert row["benchmark_kwh_per_m2"] == 215.0
    assert row["deviation_pct"] == -0.5
    assert row["gfa_sqm"] == 38000.0 and row["gfa_source"] == "energy_profile"
    assert row["metering_route"] == "HH data collector · LoA"
    assert row["metering_granularity"] == "building-level"
    assert row["metering_inferred"] is True
    assert row["completeness_missing"] == []


def test_us_row_has_standard_but_no_tm46_number():
    row = shape_building_row(_site(country="United States", region="New York"), profile=None, snapshot=None, meters=[])
    assert row["benchmark_standard"].startswith("Energy Star")
    assert row["benchmark_standing"] == "enacted"
    assert row["benchmark_kwh_per_m2"] is None and row["benchmark_source"] is None


def test_metering_route_and_granularity():
    m = metering_for([
        {"dcc_device_id": "DCC-1", "is_sub_meter": False, "raw_metadata": {}},
        {"mpan": "1", "is_sub_meter": True, "raw_metadata": {"simulate": True}},
    ])
    assert m["metering_granularity"] == "sub-metered"
    assert m["metering_inferred"] is False
    assert m["meters_simulated"] is True
    assert "SMETS2" in m["metering_route"] and "HH data collector" in m["metering_route"]
    explicit = metering_for([{"raw_metadata": {"route": "Green Button CMD · aggregator"}}])
    assert explicit["metering_route"] == "Green Button CMD · aggregator"


def test_rolling_benchmark_for_market_without_standard():
    def uk(eui):
        return shape_building_row(_site(key=str(uuid4()), site_type="Office"), profile=None,
                                  snapshot={"eui_kwh_per_m2": eui, "benchmark_kwh_per_m2": None, "deviation_pct": None}, meters=[])
    dubai = shape_building_row(_site(key="D-1", country="UAE", region="Dubai", site_type="Offices"), profile=None,
                               snapshot={"eui_kwh_per_m2": 250.0, "benchmark_kwh_per_m2": None, "deviation_pct": None}, meters=[])
    assert dubai["benchmark_standard"] == "Rolling portfolio benchmark"
    assert dubai["benchmark_standing"] == "none"
    assert dubai["benchmark_kwh_per_m2"] is None
    rows = apply_rolling_benchmarks([uk(200.0), uk(220.0), uk(180.0), dubai])
    assert dubai["benchmark_kwh_per_m2"] == 200.0
    assert dubai["benchmark_source"] == "portfolio_rolling"
    assert dubai["benchmark_comparables"] == 3
    assert dubai["deviation_pct"] == 25.0
    # Not enough comparables → no benchmark, honestly.
    lone = shape_building_row(_site(key="D-2", country="UAE", site_type="Hospital"), profile=None, snapshot=None, meters=[])
    apply_rolling_benchmarks([lone] + rows)
    assert lone["benchmark_kwh_per_m2"] is None


def test_recorded_site_columns_fill_in_and_are_labelled():
    site = _site(
        building_name="Bishopsgate Tower (recorded)", building_code="BT-1", country_code="UK",
        use_type="Commercial",
        use_mix='[{"use":"Commercial","pct":92},{"use":"Retail","pct":8}]',
        metering_route="HH data collector · LoA", metering_granularity="sub-metered",
        benchmark_standard="CIBSE TM46", benchmark_standing="guidance", benchmark_standing_note="guidance · EPC E law",
        eui_kwh_per_m2="214", benchmark_kwh_per_m2="215", hoist_score="88",
    )
    row = shape_building_row(site, profile=None, snapshot=None, meters=[])
    assert row["name"] == "Bishopsgate Tower (recorded)" and row["code"] == "BT-1"
    assert row["use_mix"] == [{"use": "Commercial", "pct": 92.0}, {"use": "Retail", "pct": 8.0}]
    assert row["eui_kwh_per_m2"] == 214.0 and row["eui_source"] == "sites_recorded"
    assert row["benchmark_kwh_per_m2"] == 215.0 and row["benchmark_source"] == "sites_recorded"
    assert row["deviation_pct"] == -0.5
    assert row["metering_route"] == "HH data collector · LoA" and row["metering_granularity"] == "sub-metered"
    assert row["metering_inferred"] is False and row["metering_source"] == "sites_recorded"
    assert row["hoist_score"] == 88
    assert row["completeness_missing"] == ["energy_profile"]


def test_computed_figures_beat_recorded_ones():
    site = _site(eui_kwh_per_m2="999", benchmark_kwh_per_m2="999", metering_route="stale", metering_granularity="building-level")
    snap = {"eui_kwh_per_m2": 214.0, "benchmark_kwh_per_m2": 215.0, "deviation_pct": -0.47}
    row = shape_building_row(site, profile=None, snapshot=snap, meters=[{"dcc_device_id": "D", "is_sub_meter": True, "raw_metadata": {}}, {"mpan": "1", "is_sub_meter": True, "raw_metadata": {}}])
    assert row["eui_kwh_per_m2"] == 214.0 and row["eui_source"] == "eui_snapshot"
    assert row["benchmark_source"] == "eui_snapshot"
    assert row["metering_source"] == "energy_meters" and row["metering_granularity"] == "sub-metered"


def test_parse_use_mix_shapes():
    assert parse_use_mix(None) == []
    assert parse_use_mix("not json") == []
    assert parse_use_mix([["Retail", 100]]) == [{"use": "Retail", "pct": 100.0}]
    assert parse_use_mix({"Office": 60, "Retail": 40}) == [{"use": "Office", "pct": 60.0}, {"use": "Retail", "pct": 40.0}]


def test_a_whole_building_eui_reads_against_the_combined_benchmark():
    """A recorded EUI names no fuel and covers all of them. Scoring it against TM46's
    electricity-only figure reported a building sitting AT its benchmark as 125% over it."""
    site = _site(eui_kwh_per_m2="214", building_id="u-1", primary_use="Commercial")
    row = shape_building_row(site, profile=None, snapshot=None, meters=[])
    assert row["benchmark_source"] == "tm46_combined_by_use"
    assert row["benchmark_kwh_per_m2"] == 212.0
    assert row["eui_source"] == "buildings_recorded"
    assert abs(row["deviation_pct"]) < 2


def test_a_snapshot_reads_against_the_fuel_it_measured():
    snap = {"eui_kwh_per_m2": 95.0, "benchmark_kwh_per_m2": None, "deviation_pct": None,
            "meter_type": "electricity"}
    row = shape_building_row(_site(), profile=None, snapshot=snap, meters=[])
    assert row["benchmark_source"] == "tm46_electricity_by_use"
    assert row["benchmark_kwh_per_m2"] == 95.0
