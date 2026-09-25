"""A year of readings is scanned as a year, not as its final month.

Every rule reads a 35-day window, because that is what the rules are about: a weekend
against the other three in the month, this week against last. That is right for "what is
wrong now" and wrong for a first ingest: load twelve months and a single scan reports on
the last of them, which reads as "the year was quiet" when nobody has looked at it.

The sweep asks the same rules the same question at weekly intervals across the data. No
threshold moves. The only difference is where each window ends - and a finding is dated to
the window it was found in, so November's firing sits in November.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from src.engines.energy import anomalies

METER = UUID("11111111-2222-3333-4444-555555555555")
LATEST = datetime(2026, 9, 21, 23, 30, tzinfo=timezone.utc)
EARLIEST = LATEST - timedelta(days=365)


class _Bounds:
    def __init__(self, lo, hi):
        self._v = (lo, hi)

    def first(self):
        return self._v


class _Session:
    """Answers only the reading-bounds query the sweep makes."""

    def __init__(self, lo=EARLIEST, hi=LATEST):
        self.lo, self.hi = lo, hi

    async def execute(self, *a, **k):
        return _Bounds(self.lo, self.hi)


@pytest.fixture()
def swept(monkeypatch):
    """Record the window end each scan was asked for, instead of running the rules."""
    seen: list[datetime] = []

    async def fake_scan(session, *, meter_id, organization_id=None, persist=True, as_of=None):
        seen.append(as_of)
        return {"ok": True, "anomalies": [{"anomaly_type": "weekend_spike", "as_of": as_of}]}

    monkeypatch.setattr(anomalies, "scan_meter_anomalies", fake_scan)
    return seen


class TestWhatItSweeps:
    @pytest.mark.asyncio
    async def test_a_year_of_readings_is_asked_about_every_week(self, swept):
        out = await anomalies.backfill_meter_anomalies(_Session(), meter_id=METER)
        assert out["ok"] is True
        # 365 days, the first 35 spent filling the rules' own comparison window, stepped
        # weekly - roughly forty-eight windows, and every one of them dated.
        assert 45 <= out["windows"] <= 50, out["windows"]
        assert out["created"] == out["windows"]
        assert all(w is not None for w in swept), "every window must be dated, or it is just today again"

    @pytest.mark.asyncio
    async def test_the_windows_walk_forward_and_stop_at_the_last_reading(self, swept):
        await anomalies.backfill_meter_anomalies(_Session(), meter_id=METER)
        assert swept == sorted(swept), "a sweep runs oldest first, so the register reads as history"
        assert swept[-1] == LATEST, "the newest window ends at the newest reading, not after it"
        assert swept[0] >= EARLIEST + timedelta(days=34), (
            "the first window needs its 35 days of comparison behind it - an empty "
            "comparison is not a quiet week"
        )

    @pytest.mark.asyncio
    async def test_history_days_narrows_the_sweep(self, swept):
        out = await anomalies.backfill_meter_anomalies(_Session(), meter_id=METER, history_days=90)
        assert 7 <= out["windows"] <= 10, out["windows"]
        assert swept[0] >= LATEST - timedelta(days=90)

    @pytest.mark.asyncio
    async def test_a_meter_with_no_readings_is_not_an_error(self, swept):
        out = await anomalies.backfill_meter_anomalies(_Session(lo=None, hi=None), meter_id=METER)
        assert out["ok"] is True and out["windows"] == 0
        assert "no readings" in out["reason"]
        assert swept == []

    @pytest.mark.asyncio
    async def test_a_short_series_still_gets_one_window(self, swept):
        """Ten days of readings cannot fill the comparison window, and still deserve a look:
        the rules decide there is nothing to say, rather than never being asked."""
        out = await anomalies.backfill_meter_anomalies(
            _Session(lo=LATEST - timedelta(days=10), hi=LATEST), meter_id=METER
        )
        assert out["windows"] == 1
        assert swept == [LATEST]

    @pytest.mark.asyncio
    async def test_one_bad_window_does_not_end_the_sweep(self, monkeypatch):
        calls = {"n": 0}

        async def flaky(session, *, meter_id, organization_id=None, persist=True, as_of=None):
            calls["n"] += 1
            if calls["n"] == 3:
                raise RuntimeError("one window blew up")
            return {"ok": True, "anomalies": []}

        monkeypatch.setattr(anomalies, "scan_meter_anomalies", flaky)
        out = await anomalies.backfill_meter_anomalies(
            _Session(lo=LATEST - timedelta(days=90), hi=LATEST), meter_id=METER
        )
        assert out["ok"] is True
        assert calls["n"] == out["windows"] > 3, "the sweep carried on past the failure"
