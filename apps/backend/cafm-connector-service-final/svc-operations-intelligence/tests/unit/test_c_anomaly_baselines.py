"""What the anomaly detectors compare against.

A building's load is bimodal — an overnight base, a working-hours peak, weekends at base
all day. Both detectors used to compare a 48-hour window against a single flat figure over
a longer period, which compares two different things whenever the window's mix of hours
differs from the period's. That produced one detector that could not fire and one that
could not stay silent:

* baseline drift discarded any drift whose window ended on a weekend, because two
  unoccupied days average far below a week containing five working ones;
* asset spike reported ~190% on a sub-meter with nothing wrong, because the median of a
  bimodal load sits at the overnight base and any two working days clear 120% of it.

Both now compare against an (is_weekend, hour) profile.
"""
from __future__ import annotations

import math
import random
from datetime import datetime, timedelta, timezone

from src.engines.energy.anomalies import (
    detect_asset_spike,
    detect_baseline_drift,
    expected_for,
    occupancy_profile,
)

STEP = timedelta(minutes=30)
THURSDAY = datetime(2024, 3, 28, 23, 30, tzinfo=timezone.utc)
SUNDAY = datetime(2024, 3, 31, 23, 30, tzinfo=timezone.utc)


def series(
    end: datetime,
    *,
    days: int = 90,
    base: float = 16.0,
    peak: float = 50.0,
    drift_mult: float = 1.0,
    drift_days: int = 7,
    spike_mult: float = 1.0,
    spike_from_days: int = 21,
    spike_len_days: int = 3,
    seed: int = 1,
) -> list[tuple[datetime, float]]:
    """Half-hourly office load: overnight base, working-hours peak, weekends at base."""
    rng = random.Random(seed)
    out: list[tuple[datetime, float]] = []
    t = end - timedelta(days=days) + STEP
    drift_from = end - timedelta(days=drift_days)
    spike_from = end - timedelta(days=spike_from_days)
    while t <= end:
        hour = t.hour + t.minute / 60
        v = peak if (7 <= hour < 19 and t.weekday() < 5) else base
        v *= (1 + 0.06 * math.sin(hour / 24 * 2 * math.pi)) * rng.uniform(0.94, 1.06)
        if t >= drift_from:
            v *= drift_mult
        if spike_from <= t < spike_from + timedelta(days=spike_len_days):
            v *= spike_mult
        out.append((t, v))
        t += STEP
    return out


class TestOccupancyProfile:
    def test_separates_working_hours_from_overnight(self):
        profile = occupancy_profile(series(THURSDAY))
        assert profile[(False, 11)] > 2 * profile[(False, 3)]

    def test_separates_weekday_from_weekend_in_the_same_hour(self):
        profile = occupancy_profile(series(THURSDAY))
        assert profile[(False, 11)] > 2 * profile[(True, 11)]

    def test_unseen_hour_falls_back_rather_than_scoring_zero(self):
        profile = {(False, 9): 40.0}
        stamps = [datetime(2024, 3, 27, 3, 0, tzinfo=timezone.utc)]  # not in profile
        assert expected_for(profile, stamps, fallback=12.5) == 12.5


class TestBaselineDrift:
    """The defect: a drift ending on a weekend was discarded however large."""

    def test_fires_when_the_window_ends_on_a_weekday(self):
        out = detect_baseline_drift(series(THURSDAY, drift_mult=1.18), tariff=0.28)
        assert out is not None
        assert out["metric_pct"] > 110

    def test_fires_on_the_same_drift_when_the_window_ends_on_a_weekend(self):
        out = detect_baseline_drift(series(SUNDAY, drift_mult=1.18), tariff=0.28)
        assert out is not None, "a weekend-ending window used to discard the drift"
        assert out["metric_pct"] > 110

    def test_the_two_agree_within_a_point(self):
        # Same drift, different end day: the answer should not depend on when it is run.
        thu = detect_baseline_drift(series(THURSDAY, drift_mult=1.18), tariff=0.28)
        sun = detect_baseline_drift(series(SUNDAY, drift_mult=1.18), tariff=0.28)
        assert abs(thu["metric_pct"] - sun["metric_pct"]) < 1.0

    def test_persistence_is_reported_against_the_same_hours(self):
        out = detect_baseline_drift(series(THURSDAY, drift_mult=1.18), tariff=0.28)
        assert out["detail"]["persistence_pct"] > 110

    def test_silent_on_a_flat_load(self):
        assert detect_baseline_drift(series(THURSDAY), tariff=0.28) is None

    def test_silent_on_a_drift_too_small_to_matter(self):
        assert detect_baseline_drift(series(THURSDAY, drift_mult=1.05), tariff=0.28) is None


class TestAssetSpike:
    """The defect: a normal day/night profile scored ~190% against its own median."""

    def test_silent_on_a_sub_meter_with_nothing_wrong(self):
        clean = series(THURSDAY, base=6.5, peak=19.0, seed=7)
        assert detect_asset_spike(clean, tariff=0.28) is None

    def test_silent_on_a_weekend_ending_window_too(self):
        clean = series(SUNDAY, base=6.5, peak=19.0, seed=7)
        assert detect_asset_spike(clean, tariff=0.28) is None

    def test_fires_on_a_sustained_spike(self):
        spiked = series(THURSDAY, base=6.5, peak=19.0, spike_mult=1.6, seed=7)
        out = detect_asset_spike(spiked, tariff=0.28)
        assert out is not None
        assert out["metric_pct"] > 120

    def test_metric_tracks_the_size_of_the_spike(self):
        # It has to mean something: 1.6x must score above 1.25x, and both above threshold.
        small = detect_asset_spike(
            series(THURSDAY, base=6.5, peak=19.0, spike_mult=1.25, seed=7), tariff=0.28
        )
        large = detect_asset_spike(
            series(THURSDAY, base=6.5, peak=19.0, spike_mult=1.6, seed=7), tariff=0.28
        )
        assert 120 < small["metric_pct"] < large["metric_pct"]

    def test_excess_is_consumption_above_the_profile(self):
        out = detect_asset_spike(
            series(THURSDAY, base=6.5, peak=19.0, spike_mult=1.6, seed=7), tariff=0.28
        )
        d = out["detail"]
        assert d["actual_kwh_48h"] > d["expected_kwh_48h"]
        assert out["excess_kwh"] > 0
