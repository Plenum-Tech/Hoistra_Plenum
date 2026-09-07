"""Unit tests — Feature C gap-closes (DCC sim, occupancy, condition)."""
from datetime import datetime, timedelta, timezone

from src.engines.energy.anomalies import detect_baseline_drift
from src.engines.energy.condition import deduce_condition_from_text
from src.engines.energy.dcc_client import _simulate_fill
from src.engines.energy.meters import find_half_hour_gaps


class TestCDccSim:
    def test_simulate_fill_covers_gap_window(self):
        start = datetime(2026, 7, 1, 10, 0, tzinfo=timezone.utc)
        end = start + timedelta(hours=2)  # 4 HH slots
        readings = _simulate_fill(start, end, meter_type="electricity", mpan="123", mprn=None)
        assert len(readings) == 4
        times = [
            datetime.fromisoformat(r["reading_at"].replace("Z", "+00:00"))
            for r in readings
        ]
        gaps = find_half_hour_gaps(times, window_start=start, window_end=end)
        assert gaps == []


class TestCOccupancyGuardLogic:
    def test_baseline_drift_still_detectable_without_occupancy_check(self):
        end = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
        readings = []
        for i in range(14 * 48):
            t = end - timedelta(minutes=30 * (14 * 48 - 1 - i))
            k = 1.5 if i >= 7 * 48 else 1.0
            readings.append((t, k))
        hit = detect_baseline_drift(readings, tariff=0.28)
        assert hit is not None


class TestCConditionVectorFallbackHeuristic:
    def test_failed_inspection_scores_critical(self):
        score, label, meta = deduce_condition_from_text(
            "Inspection: unit failed life safety check — unsafe"
        )
        assert score == 1
        assert meta["method"] == "heuristic"
