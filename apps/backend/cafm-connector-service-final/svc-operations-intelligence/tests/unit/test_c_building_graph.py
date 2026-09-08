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


def _energy(**over):
    base = {"profiles": {}, "snapshots": {}, "meters": {}}
    base.update(over)
    return base


def test_a_buildings_own_energy_record_always_applies():
    from src.engines.energy.buildings import attribute_energy
    e = _energy(profiles={"B-01": {"gia_m2": 100.0}}, snapshots={"B-01": {"eui_kwh_per_m2": 214.0}})
    prof, snap, mtrs, how = attribute_energy("B-01", "S-01", 3, e["profiles"], e["snapshots"], e["meters"])
    assert how == "building" and snap["eui_kwh_per_m2"] == 214.0


def test_site_energy_applies_when_the_building_is_the_whole_site():
    from src.engines.energy.buildings import attribute_energy
    e = _energy(snapshots={"S-01": {"eui_kwh_per_m2": 214.0}})
    prof, snap, mtrs, how = attribute_energy("B-01", "S-01", 1, e["profiles"], e["snapshots"], e["meters"])
    assert how == "site_sole_building" and snap["eui_kwh_per_m2"] == 214.0


def test_site_energy_is_never_copied_onto_several_buildings():
    """The whole point: one site meter across three buildings would either invent a split or
    count the same kilowatt-hours three times. It does neither."""
    from src.engines.energy.buildings import attribute_energy
    e = _energy(snapshots={"S-02": {"eui_kwh_per_m2": 198.0}}, meters={"S-02": [{"mpan": "1"}]})
    for bid in ("B-02", "B-02b", "B-02c"):
        prof, snap, mtrs, how = attribute_energy(bid, "S-02", 3, e["profiles"], e["snapshots"], e["meters"])
        assert snap is None and mtrs == []
        assert how == "unattributed_site_shared"


def test_one_building_on_a_shared_site_may_still_have_its_own_reading():
    from src.engines.energy.buildings import attribute_energy
    e = _energy(snapshots={"S-02": {"eui_kwh_per_m2": 198.0}, "B-02b": {"eui_kwh_per_m2": 176.0}})
    _, snap, _, how = attribute_energy("B-02b", "S-02", 3, e["profiles"], e["snapshots"], e["meters"])
    assert how == "building" and snap["eui_kwh_per_m2"] == 176.0
    _, snap2, _, how2 = attribute_energy("B-02c", "S-02", 3, e["profiles"], e["snapshots"], e["meters"])
    assert snap2 is None and how2 == "unattributed_site_shared"


def test_no_energy_anywhere_reports_none():
    from src.engines.energy.buildings import attribute_energy
    assert attribute_energy("B-09", "S-09", 1, {}, {}, {})[3] == "none"


def test_square_feet_convert_to_metres_before_any_benchmark_sees_them():
    """412,000 ft² is 38,276 m². Every benchmark is kWh/m², so an unconverted square-foot
    figure would understate EUI by a factor of ten and read as far under benchmark."""
    from src.engines.energy.buildings import sqft_to_sqm
    assert sqft_to_sqm(412_000) == 38276.05
    assert sqft_to_sqm("148,000") == 13749.65
    assert sqft_to_sqm(None) is None
    assert sqft_to_sqm("not a number") is None


def test_the_canonical_row_resolves_country_and_standard_from_its_location():
    from src.engines.energy.buildings import building_to_row_input
    src = building_to_row_input({
        "building_id": "u-1", "site_id": "s-1", "name": "Bishopsgate Tower",
        "primary_use": "Commercial", "floors": "34", "gross_area_sqft": "412000",
        "eui_kwh_m2": "214", "hoist_score": "88",
        "loc_country_code": "GB", "loc_region": "Greater London",
        "pack_standard": "CIBSE TM46", "pack_standing": "guidance",
        "pack_standing_note": "guidance · EPC E law", "pack_benchmark_source": "TM46 category",
    })
    assert src["country_code"] == "GB" and src["region"] == "Greater London"
    assert src["benchmark_standard"] == "CIBSE TM46" and src["benchmark_standing"] == "guidance"
    assert src["use_type"] == "Commercial" and src["floors"] == "34"
    assert src["gfa_sqm"] == 38276.05
    assert src["eui_kwh_per_m2"] == "214"


def test_a_building_with_no_location_falls_back_without_inventing_a_pack():
    from src.engines.energy.buildings import building_to_row_input
    src = building_to_row_input({"building_id": "u-2", "name": "Unlocated"})
    assert src["country_code"] is None and src["benchmark_standard"] is None
