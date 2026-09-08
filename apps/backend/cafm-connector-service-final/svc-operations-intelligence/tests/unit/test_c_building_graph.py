"""Building-graph rollups — pure shaping, no DB."""
from __future__ import annotations

from src.engines.energy.building_rollup import (
    dominant_use,
    merge_figure,
    use_mix_from_area,
)
from src.engines.energy.buildings import apply_graph_rollup, building_to_row_input


def test_use_mix_is_area_weighted_and_ordered():
    mix = use_mix_from_area({"Retail": 33.0, "Commercial": 380.0})
    assert mix == [{"use": "Commercial", "pct": 92.0}, {"use": "Retail", "pct": 8.0}]


def test_use_mix_ignores_types_with_no_measured_area():
    mix = use_mix_from_area({"Commercial": 100.0, "Plant": 0.0, "Void": None})
    assert mix == [{"use": "Commercial", "pct": 100.0}]


def test_use_mix_of_nothing_is_empty_not_zero():
    assert use_mix_from_area({}) == []
    assert use_mix_from_area({"Commercial": 0.0}) == []


def test_dominant_use_calls_a_split_building_mixed():
    single = [{"use": "Commercial", "pct": 100.0}]
    lopsided = [{"use": "Commercial", "pct": 92.0}, {"use": "Retail", "pct": 8.0}]
    split = [{"use": "Residential", "pct": 46.0}, {"use": "Commercial", "pct": 34.0},
             {"use": "Retail", "pct": 20.0}]
    assert dominant_use(single) == "Commercial"
    assert dominant_use(lopsided) == "Commercial"
    assert dominant_use(split) == "Mixed"
    assert dominant_use([]) is None


def test_merge_figure_prefers_the_counted_value():
    assert merge_figure(34, 30, counted_source="floors_table", recorded_source="rec") == (34, "floors_table")
    assert merge_figure(None, 30, counted_source="floors_table", recorded_source="rec") == (30, "rec")
    assert merge_figure(0, 30, counted_source="floors_table", recorded_source="rec") == (30, "rec")
    assert merge_figure(None, None, counted_source="a", recorded_source="b") == (None, None)


def test_building_row_input_maps_the_buildings_table_shape():
    src = building_to_row_input({
        "building_id": "B-001", "site_id": "S-01", "name": "Bishopsgate Tower",
        "building_code": "BT", "country": "United Kingdom", "state": "Greater London",
        "use_type": "Commercial", "floors_recorded": "30", "gfa_sqm_recorded": "1000",
        "hoist_score": "88",
    })
    assert src["key"] == "B-001" and src["alt_id"] == "S-01"
    assert src["name"] == "Bishopsgate Tower" and src["region"] == "Greater London"
    assert src["floors"] == "30" and src["gfa_sqm"] == "1000"


def test_graph_counts_beat_recorded_figures_and_name_their_source():
    row = {"floors": 30, "gfa_sqm": 1000.0, "use_mix": [{"use": "Office", "pct": 100.0}],
           "gfa_source": "buildings_recorded"}
    out = apply_graph_rollup(row, {
        "floors": 34, "spaces": 98, "gfa_sqm": 38276.0,
        "use_mix": [{"use": "Commercial", "pct": 92.0}, {"use": "Retail", "pct": 8.0}],
        "dominant_use": "Commercial", "counts": {"assets": 232, "work_orders": 121},
    })
    assert out["floors"] == 34 and out["floors_source"] == "floors_table"
    assert out["gfa_sqm"] == 38276.0 and out["gfa_source"] == "spaces_sum"
    assert out["use_mix"][0]["use"] == "Commercial" and out["use_mix_source"] == "spaces_by_type"
    assert out["use_type"] == "Commercial" and out["spaces"] == 98
    assert out["graph_counts"]["assets"] == 232


def test_recorded_figures_survive_when_the_graph_is_empty():
    row = {"floors": 30, "gfa_sqm": 1000.0, "use_mix": [{"use": "Office", "pct": 100.0}]}
    out = apply_graph_rollup(row, None)
    assert out["floors"] == 30 and out["floors_source"] == "buildings_recorded"
    assert out["gfa_sqm"] == 1000.0 and out["use_mix_source"] == "buildings_recorded"
    assert out["graph_counts"] == {}


def test_a_building_with_no_figures_at_all_claims_nothing():
    out = apply_graph_rollup({"floors": None, "gfa_sqm": None, "use_mix": []}, {})
    assert out["floors"] is None and out["floors_source"] is None
    assert out["gfa_source"] is None and out["use_mix_source"] is None
