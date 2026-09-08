"""A partial graph count must never replace a survey figure."""
from __future__ import annotations

from src.engines.energy.buildings import apply_graph_rollup


def test_three_spaces_on_a_tower_do_not_become_its_floor_area():
    """The bug this pins: 3 spaces recorded on a 24-storey building summed to 3,345 m2
    and replaced the surveyed 40,000 m2 — a twelvefold understatement that looked like an
    ordinary number, and would divide any computed EUI by the wrong denominator."""
    row = {"gfa_sqm": 40000.0, "gfa_source": "sites", "floors": 24}
    out = apply_graph_rollup(row, {"gfa_sqm": 3344.51, "floors": 24, "spaces": 3})
    assert out["gfa_sqm"] == 40000.0, "the survey stands"
    assert out["gfa_source"] == "sites"
    assert out["gfa_counted_sqm"] == 3344.51, "the count still travels, beside it"
    assert any("counted of" in p for p in out["partial_counts"])


def test_a_complete_count_still_beats_the_survey():
    """The counted figure is the better one when it is actually complete — the spaces on
    record ARE the building."""
    row = {"gfa_sqm": 3000.0, "gfa_source": "sites"}
    out = apply_graph_rollup(row, {"gfa_sqm": 3200.0, "spaces": 40})
    assert out["gfa_sqm"] == 3200.0
    assert out["gfa_source"] == "spaces_sum"
    assert out["partial_counts"] == []


def test_a_building_with_no_survey_takes_whatever_was_counted():
    out = apply_graph_rollup({}, {"gfa_sqm": 900.0, "floors": 3, "spaces": 6})
    assert out["gfa_sqm"] == 900.0 and out["gfa_source"] == "spaces_sum"
    assert out["floors"] == 3 and out["floors_source"] == "floors_table"
    assert out["partial_counts"] == []


def test_fewer_floor_rows_than_the_survey_is_an_incomplete_graph_not_a_correction():
    row = {"floors": 24}
    out = apply_graph_rollup(row, {"floors": 3, "spaces": 3})
    assert out["floors"] == 24
    assert out["floors_counted"] == 3
    assert "floors: 3 of 24 on record" in out["partial_counts"]


def test_a_building_with_no_graph_rows_keeps_its_survey_and_claims_nothing():
    out = apply_graph_rollup({"gfa_sqm": 5000.0, "floors": 10}, None)
    assert out["gfa_sqm"] == 5000.0 and out["floors"] == 10
    assert out["graph_counts"] == {}
