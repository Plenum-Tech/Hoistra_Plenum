"""The live reading feed produces values a panel can be trusted to grade.

Three things have to hold or the instrumented-asset card lies:

* a value said to be in band is inside its limits, and one said to be out is outside — the
  panel grades the number, not the intention behind it;
* the same half hour always produces the same reading, so a backfill agrees with what the
  live loop would have written and a re-run corrects rather than duplicates;
* the reading moves between half hours, because a card that says "live" beside a number that
  never changes is the one detail that gives a demo away.

The out-of-band count follows the condition grade rather than a coin toss. That matters for
more than tidiness: the failure model already weighs the grade and the band count as separate
signals, so drawing them independently would let a card read "as new, 3 of 8 out of band" and
invite exactly the question the demo cannot answer.
"""
from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2] / "db" / "tools" / "refresh_asset_readings.py"
_spec = importlib.util.spec_from_file_location("refresh_asset_readings", _SRC)
feed = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(feed)

SLOT = datetime(2026, 9, 22, 14, 30)
EIGHT = feed.SENSORS["chiller"]


class TestWhatRunsOutOfBand:
    @pytest.mark.parametrize("grade,expected", [(1, 0), (2, 0), (3, 1), (4, 2), (5, 3)])
    def test_the_count_follows_the_condition_grade(self, grade, expected):
        assert len(feed.out_of_band_metrics("HP-CH-01", grade, EIGHT)) == expected

    def test_which_metrics_does_not_change_between_refreshes(self):
        first = feed.out_of_band_metrics("HP-CRC-01", 5, EIGHT)
        assert first == feed.out_of_band_metrics("HP-CRC-01", 5, EIGHT)
        assert first <= set(EIGHT), "a metric can only be out of band if the asset has it"

    def test_two_assets_of_the_same_grade_do_not_fail_identically(self):
        a = feed.out_of_band_metrics("HP-CH-01", 4, EIGHT)
        b = feed.out_of_band_metrics("AC-GEN-01", 4, EIGHT)
        assert a != b, "a portfolio where every grade-4 asset fails the same way reads as generated"

    def test_an_ungraded_asset_is_not_assumed_broken(self):
        assert feed.out_of_band_metrics("HP-CH-01", None, EIGHT) == set()

    def test_more_grades_than_sensors_cannot_ask_for_more_metrics_than_exist(self):
        three = feed.SENSORS["fcu"]
        assert len(feed.out_of_band_metrics("AC-FCU-01", 5, three)) == 3


class TestTheValueItself:
    def test_in_band_readings_are_inside_the_limits(self):
        for metric in EIGHT:
            v = feed.reading_for(asset_code="HP-CH-02", metric=metric, lo=3.0, hi=5.5,
                                 slot=SLOT, outside=False, grade=2)
            assert 3.0 <= v <= 5.5, f"{metric} graded in band at {v}"

    def test_out_of_band_readings_are_outside_the_limits(self):
        for metric in EIGHT:
            v = feed.reading_for(asset_code="HP-CRC-01", metric=metric, lo=3.0, hi=5.5,
                                 slot=SLOT, outside=True, grade=5)
            assert v < 3.0 or v > 5.5, f"{metric} said to be out of band sat at {v}"

    def test_a_metric_that_fails_low_fails_low(self):
        """A fuel level and an oil pressure go wrong by running out, not by overflowing.
        Pushing those above their upper limit would read as a sensor fault, not a fault."""
        for metric in ("fuel_level", "oil_pressure", "battery_voltage", "load_percentage"):
            v = feed.reading_for(asset_code="AC-GEN-01", metric=metric, lo=60.0, hi=100.0,
                                 slot=SLOT, outside=True, grade=4)
            assert v < 60.0, f"{metric} failed high at {v}"

    def test_a_metric_that_fails_high_fails_high(self):
        v = feed.reading_for(asset_code="AC-GEN-01", metric="coolant_temp", lo=70.0, hi=95.0,
                             slot=SLOT, outside=True, grade=4)
        assert v > 95.0

    def test_the_same_half_hour_always_reads_the_same(self):
        args = dict(asset_code="HP-CH-01", metric="temperature", lo=5.0, hi=95.0,
                    outside=False, grade=3)
        assert (feed.reading_for(slot=SLOT, **args) == feed.reading_for(slot=SLOT, **args))

    def test_the_reading_moves_between_half_hours(self):
        args = dict(asset_code="HP-CH-01", metric="load_percentage", lo=30.0, hi=100.0,
                    outside=False, grade=3)
        seen = {feed.reading_for(slot=SLOT + timedelta(minutes=30 * i), **args) for i in range(12)}
        assert len(seen) >= 10, f"only {len(seen)} distinct values over six hours"

    def test_a_reading_never_sits_exactly_on_a_limit(self):
        """The grader treats a value equal to a limit as in band. A generator that parks
        readings on the boundary makes that choice look like a bug."""
        for i in range(48):
            v = feed.reading_for(asset_code="HP-AHU-01", metric="vibration", lo=0.0, hi=7.1,
                                 slot=SLOT + timedelta(minutes=30 * i), outside=False, grade=3)
            assert v not in (0.0, 7.1)


class TestSlots:
    @pytest.mark.parametrize("minute,expected", [(0, 0), (14, 0), (29, 0), (30, 30), (59, 30)])
    def test_a_moment_snaps_to_its_half_hour(self, minute, expected):
        got = feed.snap(datetime(2026, 9, 22, 14, minute, 17, 456))
        assert (got.minute, got.second, got.microsecond) == (expected, 0, 0)

    def test_a_day_of_slots_is_forty_eight_of_them(self):
        start = datetime(2026, 9, 22, 0, 0)
        assert len(feed.slots_between(start, start + timedelta(days=1))) == 49  # inclusive

    def test_slots_run_oldest_first(self):
        start = datetime(2026, 9, 22, 0, 0)
        got = feed.slots_between(start, start + timedelta(hours=6))
        assert got == sorted(got)


class TestTheClassOfPlant:
    @pytest.mark.parametrize("name,code,cls", [
        ("Chiller 1 · Roof Plant", "HP-CH-01", "chiller"),
        ("CRAC Cabinet · Comms Room 2", "HP-CRC-01", "crac"),
        ("ACU Cabinet · Comms Room 2", "XX-01", "crac"),
        ("Boiler 1 · LTHW", "HP-BLR-01", "boiler"),
        ("AHU 1 · Levels 1-4", "HP-AHU-01", "ahu"),
        ("Pump · Heating Circuit", "AC-PMP-01", "pump"),
        ("Generator · Life Safety", "AC-GEN-01", "generator"),
        ("FCU · Residents Lounge", "AC-FCU-01", "fcu"),
    ])
    def test_plant_is_recognised_from_the_name_on_record(self, name, code, cls):
        assert feed.class_of(name, code) == cls

    def test_a_lift_carries_no_sensors_and_is_not_instrumented(self):
        """Not everything on a register is instrumented. A lift, a fire panel and a door
        controller have no feed here, and inventing one for them would put readings on the
        page that no building management system produces."""
        for name, code in [("Lift Car 1 · Tower", "HP-LFT-01"),
                           ("Fire Alarm Panel · Main", "HP-FIR-01"),
                           ("Door Access Controller · Lobby", "HP-DOR-01"),
                           ("Lighting Control · Car Park", "HP-LTG-01")]:
            assert feed.class_of(name, code) is None

    def test_every_class_with_sensors_has_a_band_for_each_of_them(self):
        """A metric with no band grades as unknown and drops out of the failure denominator,
        so a sensor list that outruns the bands quietly shrinks the card."""
        import importlib.util as iu
        src = _SRC.parent / "seed_northbridge_assets.py"
        spec = iu.spec_from_file_location("seed_northbridge_assets", src)
        seeder = iu.module_from_spec(spec)
        spec.loader.exec_module(seeder)
        banded = {b[0] for b in seeder.BANDS}
        for cls, metrics in feed.SENSORS.items():
            missing = set(metrics) - banded
            assert not missing, f"{cls} carries {missing} with no band on record"
