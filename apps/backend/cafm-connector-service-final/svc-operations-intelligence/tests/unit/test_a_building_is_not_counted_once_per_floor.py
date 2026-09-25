"""A building with a meter per floor is still one building.

A sub-meter reads a share of the incoming supply: the electricity on Level 3 has already gone
through the main meter. Summed over every active meter, a building with twelve floor meters
reads nearly twice what it used, its gap to the reference becomes a large number, and the
cost card prices the excess of a building that does not exist. Before floor sub-meters were
ingested anywhere this could not go wrong; the day they were, it would have, silently.

So the EUI window and the contracted-tariff read count a building's main meters where it has
one on the fuel, and its sub-meters only where it has none. And the floor read that shows
where the energy goes groups those sub-meters by the floor their section sits on, prices them
at the main meter's rate when they carry none, and says what share of the supply they cover.
"""
from __future__ import annotations

from datetime import datetime, timezone

from src.engines.energy import benchmarks, floor_meters, meter_scope, us_ratings

B = "c343c566-8cc2-40d4-93d3-5cf1ec52d26f"
NOW = datetime(2026, 9, 24, tzinfo=timezone.utc)


def _row(meter_id, fuel, kwh, *, sub=False, floor=None, level=None, tariff=None,
         area=None, anomalies=0, supply=None, asset=None):
    return {
        "meter_id": meter_id, "building_id": B, "building": "Harbour Point",
        "building_code": "B-101", "fuel": fuel, "supply": supply or meter_id,
        "is_sub_meter": sub, "tariff": tariff, "description": None,
        "asset_id": f"asset-{asset}" if asset else None, "asset_name": asset,
        "asset_code": f"B-101-{asset}" if asset else None,
        "section_id": f"sec-{floor}" if floor else None, "section": floor,
        "section_type": "office" if floor else None, "area_m2": area,
        "floor": floor, "level": level, "kwh": kwh, "readings": 1440,
        "last_reading_at": NOW, "open_anomalies": anomalies,
    }


class TestWhichMetersABuildingIsCountedBy:
    def test_a_main_meter_always_counts(self):
        sql = meter_scope.counted_meters("em")
        assert sql.startswith("(NOT em.is_sub_meter OR NOT EXISTS (")

    def test_a_sub_meter_counts_only_where_no_main_meter_reads_its_fuel(self):
        sql = meter_scope.counted_meters("em")
        assert "p.building_id = em.building_id AND p.active AND NOT p.is_sub_meter" in sql
        assert "lower(coalesce(p.meter_type, 'electricity')) = lower(coalesce(em.meter_type, 'electricity'))" in sql

    def test_the_eui_window_and_the_tariff_read_both_apply_it(self):
        import inspect
        assert "counted_meters(" in inspect.getsource(us_ratings)
        assert "counted_meters(" in inspect.getsource(benchmarks.contracted_tariffs)

    def test_the_alias_is_the_callers(self):
        assert "NOT x.is_sub_meter" in meter_scope.counted_meters("x")


class TestTheFloorTheSectionNames:
    def test_basement_ground_and_levels(self):
        f = meter_scope.floor_level_from_name
        assert f("Basement") == -1 and f("Ground") == 0 and f("Level 3") == 3 and f("L7") == 7

    def test_a_name_that_is_not_a_floor_is_none(self):
        assert meter_scope.floor_level_from_name("Tenant floors") is None
        assert meter_scope.floor_level_from_name("") is None


class TestSubMetersByFloor:
    ROWS = [
        _row("E0", "electricity", 10_000.0, tariff=0.21),
        _row("G1", "gas", 4_000.0, tariff=0.062),
        _row("E-L01", "electricity", 1_500.0, sub=True, floor="Level 1", level=1, area=1000.0),
        _row("E-L02", "electricity", 2_500.0, sub=True, floor="Level 2", level=2, area=1000.0,
             anomalies=1),
        _row("G-B", "gas", 3_000.0, sub=True, floor="Basement", level=-1, tariff=0.062),
        _row("E-X", "electricity", 100.0, sub=True),        # a sub-meter on no section
        _row("A-CH1", "electricity", 1_200.0, sub=True, asset="CHILLER-01", anomalies=1),
    ]

    def test_an_asset_sub_meter_is_listed_by_its_asset_not_on_a_floor(self):
        b = floor_meters.group_by_floor(self.ROWS, days=30)[0]
        assert [m["asset_name"] for m in b["assets"]] == ["CHILLER-01"]
        assert b["assets"][0]["share_pct"] == 12.0 and b["assets"][0]["cost"] == 252.0
        assert all(m["asset_id"] is None for f in b["floors"] for m in f["meters"])
        assert "1 asset" in b["summary"]

    def test_one_entry_per_building_floors_basement_first(self):
        out = floor_meters.group_by_floor(self.ROWS, days=30)
        assert len(out) == 1
        assert [f["floor"] for f in out[0]["floors"]] == ["Basement", "Level 1", "Level 2"]

    def test_a_sub_meter_with_no_floor_is_unplaced_not_dropped(self):
        b = floor_meters.group_by_floor(self.ROWS, days=30)[0]
        assert [m["supply"] for m in b["unplaced"]] == ["E-X"]
        assert b["sub_meters"] == 5

    def test_share_is_of_the_main_meter_on_the_same_fuel(self):
        b = floor_meters.group_by_floor(self.ROWS, days=30)[0]
        by = {m["supply"]: m for f in b["floors"] for m in f["meters"]}
        assert by["E-L01"]["share_pct"] == 15.0
        assert by["E-L02"]["share_pct"] == 25.0
        assert by["G-B"]["share_pct"] == 75.0

    def test_a_sub_meter_with_no_tariff_is_priced_at_the_main_meters_rate(self):
        b = floor_meters.group_by_floor(self.ROWS, days=30)[0]
        by = {m["supply"]: m for f in b["floors"] for m in f["meters"]}
        assert by["E-L01"]["rate_used"] == 0.21 and by["E-L01"]["cost"] == 315.0

    def test_coverage_says_what_the_floors_account_for(self):
        b = floor_meters.group_by_floor(self.ROWS, days=30)[0]
        assert b["coverage_pct_by_fuel"] == {"electricity": 40.0, "gas": 75.0}
        assert "40% of the electricity supply" in b["summary"]

    def test_a_floor_is_annualised_per_square_metre(self):
        b = floor_meters.group_by_floor(self.ROWS, days=30)[0]
        l2 = next(f for f in b["floors"] if f["floor"] == "Level 2")
        assert l2["kwh_per_m2_year"] == round(2500 / 1000 * 365 / 30, 1)
        assert l2["open_anomalies"] == 1

    def test_no_main_meter_means_no_share_not_a_zero(self):
        rows = [_row("E-L01", "electricity", 500.0, sub=True, floor="Level 1", level=1)]
        b = floor_meters.group_by_floor(rows, days=30)[0]
        assert b["floors"][0]["meters"][0]["share_pct"] is None
        assert b["coverage_pct_by_fuel"] == {}

    def test_a_building_with_no_sub_meters_says_so(self):
        b = floor_meters.group_by_floor(self.ROWS[:2], days=30)[0]
        assert b["floors"] == [] and "No sub-meters on record" in b["summary"]
