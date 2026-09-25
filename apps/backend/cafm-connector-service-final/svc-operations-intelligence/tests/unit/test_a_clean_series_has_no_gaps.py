"""A series with no holes is reported as having none, and a migrated one is looked at.

Two faults, found on 22 Sep 2026 while making the migration run the same gap rule the upload
runs.

The first: the rule widens its window by an hour at each end, so that a gap straddling the
edge of an upload is still seen. Nothing clamped that widening to the readings the meter
actually has, so a series beginning at midnight was reported as missing the two half-hours
before midnight. Every clean upload arrived with exactly one gap against it and the count
meant nothing. A hole needs readings on both sides of it; before the first reading there is
no hole, only the beginning.

The second: gap detection lived inside the ingest function, so readings written any other way
were never examined. A meter that arrived by migration reported a clean series because nobody
had looked, which reads identically to a series that is genuinely clean.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.engines.energy import meters as M

METER = "11111111-2222-3333-4444-555555555555"
START = datetime(2025, 9, 22, 0, 0, tzinfo=timezone.utc)
HH = timedelta(minutes=30)


def series(n: int, *, skip: set[int] | None = None) -> list[datetime]:
    """n consecutive half-hours from START, minus any index in `skip`."""
    skip = skip or set()
    return [START + HH * i for i in range(n) if i not in skip]


class _Result:
    def __init__(self, rows, one=None):
        self._rows, self._one = rows, one

    def first(self):
        return self._one

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _Session:
    """Answers the three reads record_gaps_for_meter makes, in the order it makes them."""

    def __init__(self, readings: list[datetime], open_gaps: list | None = None):
        self.readings = readings
        self.open_gaps = open_gaps or []
        self.added: list = []
        self._call = 0

    async def execute(self, *a, **k):
        self._call += 1
        if self._call == 1:                       # bounds
            lo = min(self.readings) if self.readings else None
            hi = max(self.readings) if self.readings else None
            return _Result([], one=(lo, hi))
        if self._call == 2:                       # readings in window
            return _Result(self.readings)
        return _Result(self.open_gaps)            # gaps already open

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        return None


async def run_gaps(readings, *, open_gaps=None):
    s = _Session(readings, open_gaps)
    lo = min(readings) if readings else START
    hi = max(readings) if readings else START
    return await M.record_gaps_for_meter(
        s, meter_id=METER, organization_id=None, window_start=lo, window_end=hi
    ), s


class TestACleanSeries:
    @pytest.mark.asyncio
    async def test_a_full_day_reports_no_gaps(self):
        rows, s = await run_gaps(series(48))
        assert rows == []
        assert s.added == []

    @pytest.mark.asyncio
    async def test_the_start_of_a_series_is_not_a_gap(self):
        """The whole bug. The hour before the first reading is not missing data; it is the
        time before this meter had any."""
        rows, _ = await run_gaps(series(48))
        assert rows == [], "a series that begins at midnight is not missing 23:00 and 23:30"

    @pytest.mark.asyncio
    async def test_the_end_of_a_series_is_not_a_gap_either(self):
        rows, _ = await run_gaps(series(10))
        assert rows == []

    @pytest.mark.asyncio
    async def test_a_year_of_half_hours_is_clean(self):
        rows, _ = await run_gaps(series(17_520))
        assert rows == []


class TestARealHole:
    @pytest.mark.asyncio
    async def test_two_missing_half_hours_in_the_middle_are_flagged(self):
        rows, s = await run_gaps(series(48, skip={20, 21}))
        assert len(rows) == 1
        assert rows[0]["missing_periods"] == 2
        assert len(s.added) == 1

    @pytest.mark.asyncio
    async def test_one_missing_half_hour_is_not_enough_to_flag(self):
        """The rule wants two consecutive. A single dropped reading is noise, and flagging it
        would bury the real outages."""
        rows, _ = await run_gaps(series(48, skip={20}))
        assert rows == []

    @pytest.mark.asyncio
    async def test_two_separate_holes_are_two_findings(self):
        rows, _ = await run_gaps(series(48, skip={10, 11, 30, 31, 32}))
        assert len(rows) == 2
        assert sorted(r["missing_periods"] for r in rows) == [2, 3]

    @pytest.mark.asyncio
    async def test_a_hole_already_being_chased_is_not_flagged_again(self):
        """Re-flagging restarts its retry count, so it would never escalate."""
        first, _ = await run_gaps(series(48, skip={20, 21}))
        assert len(first) == 1

        class _Open:
            gap_start = START + HH * 20
            gap_end = START + HH * 22
            status = "open"

        again, s = await run_gaps(series(48, skip={20, 21}), open_gaps=[_Open()])
        assert again == []
        assert s.added == []


class TestAMeterWithNothing:
    @pytest.mark.asyncio
    async def test_a_meter_with_no_readings_is_not_an_error(self):
        rows, s = await run_gaps([])
        assert rows == []
        assert s.added == []


class TestTheRuleIsReachableFromElsewhere:
    def test_the_detector_exists_for_readings_written_another_way(self):
        """Gap detection used to live inside the ingest function. The migration writes
        meter_readings directly, so it needed a way to run the same rule afterwards rather
        than a second copy of it."""
        assert callable(getattr(M, "record_gaps_for_meter", None))
        assert callable(getattr(M, "detect_gaps_for_meters", None))
