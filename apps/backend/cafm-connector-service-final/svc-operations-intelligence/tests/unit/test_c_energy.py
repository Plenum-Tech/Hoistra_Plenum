"""Unit tests — Feature C Energy Intelligence (pure logic)."""
from datetime import date, datetime, timedelta, timezone

from src.engines.energy.anomalies import (
    detect_asset_spike,
    detect_baseline_drift,
    detect_weekend_spike,
    financial_translation,
)
from src.engines.energy.condition import deduce_condition_from_text
from src.engines.energy.eui import compute_eui, tm46_benchmark
from src.engines.energy.meters import coerce_reading_at, find_half_hour_gaps, parse_readings_csv


class TestCGaps:
    def test_two_consecutive_missing_flagged(self):
        start = datetime(2026, 7, 1, 0, 0, tzinfo=timezone.utc)
        # Present every HH except 01:00 and 01:30
        times = [start + timedelta(minutes=30 * i) for i in range(8) if i not in (2, 3)]
        gaps = find_half_hour_gaps(times, window_start=start, window_end=start + timedelta(hours=4))
        assert len(gaps) == 1
        assert gaps[0][2] == 2

    def test_single_missing_not_flagged(self):
        start = datetime(2026, 7, 1, 0, 0, tzinfo=timezone.utc)
        times = [start + timedelta(minutes=30 * i) for i in range(8) if i != 2]
        gaps = find_half_hour_gaps(times, window_start=start, window_end=start + timedelta(hours=4))
        assert gaps == []


class TestCEui:
    def test_eui_deviation_and_financial(self):
        # 1000 kWh over 365 days on 100 m² → annualised EUI 10
        # benchmark 8 → deviation 25%, excess = 1000 - 8*100 = 200, £ at 0.30 = 60
        r = compute_eui(
            total_kwh=1000.0,
            gia_m2=100.0,
            period_start=date(2025, 1, 1),
            period_end=date(2025, 12, 31),
            annual_benchmark_kwh_m2=8.0,
            tariff_gbp_per_kwh=0.30,
        )
        assert r["ok"] is True
        assert r["eui_kwh_per_m2_annualised"] == 10.0
        assert r["deviation_pct"] == 25.0
        assert r["excess_kwh"] == 200.0
        assert r["financial_gbp"] == 60.0

    def test_tm46_office_electricity_present(self):
        b = tm46_benchmark("office", "electricity")
        assert b is not None and b > 0


class TestCCondition:
    def test_critical_keywords_score_1(self):
        score, label, meta = deduce_condition_from_text("Asset failed — unsafe, immediate action")
        assert score == 1
        assert "Critical" in label
        assert meta["signals"]

    def test_poor_score_2(self):
        score, _, _ = deduce_condition_from_text("Major defect — replacement recommended")
        assert score == 2

    def test_excellent_score_5(self):
        score, _, _ = deduce_condition_from_text("Excellent condition, no defects found")
        assert score == 5


class TestCAnomalies:
    def test_financial_translation(self):
        f = financial_translation(excess_kwh=10.0, annualised_frequency=52.0, tariff=0.25)
        assert f["financial_gbp"] == 130.0

    def test_weekend_spike_detected(self):
        # Build 4 weeks of weekend readings at 1 kWh, then recent weekend at 2 kWh (>130%)
        base = datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc)  # Saturday
        readings: list[tuple[datetime, float]] = []
        for w in range(5):
            sat = base + timedelta(weeks=w)
            sun = sat + timedelta(days=1)
            val = 2.0 if w == 4 else 1.0
            for h in range(0, 48):
                readings.append((sat + timedelta(minutes=30 * h), val))
                readings.append((sun + timedelta(minutes=30 * h), val))
        hit = detect_weekend_spike(readings, tariff=0.28)
        assert hit is not None
        assert hit["anomaly_type"] == "weekend_spike"
        assert hit["metric_pct"] > 130.0

    def test_baseline_drift_detected(self):
        end = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
        readings = []
        # 14 days of HH: first 7 at 1.0, last 7 at 1.5 (>110%)
        for i in range(14 * 48):
            t = end - timedelta(minutes=30 * (14 * 48 - 1 - i))
            k = 1.5 if i >= 7 * 48 else 1.0
            readings.append((t, k))
        hit = detect_baseline_drift(readings, tariff=0.28)
        assert hit is not None
        assert hit["anomaly_type"] == "baseline_drift"

    def test_asset_spike_detected(self):
        end = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
        readings = []
        # Persist window is 48h inclusive of the boundary sample — spike slightly longer
        spike_periods = 100
        total = 30 * 48
        for i in range(total):
            t = end - timedelta(minutes=30 * (total - 1 - i))
            k = 2.0 if i >= total - spike_periods else 1.0
            readings.append((t, k))
        hit = detect_asset_spike(readings, tariff=0.28)
        assert hit is not None
        assert hit["anomaly_type"] == "asset_spike"


class TestCCsvParse:
    def test_coerce_reading_at_strips_seconds_and_z(self):
        at = coerce_reading_at("2026-01-08T12:30:17Z")
        assert at is not None
        assert at.second == 0
        assert at.microsecond == 0
        assert at.tzinfo is not None
        assert at.minute == 30

    def test_coerce_reading_at_rejects_garbage(self):
        assert coerce_reading_at("not-a-date") is None
        assert coerce_reading_at(None) is None

    def test_parse_readings_csv_maps_mpan_and_kwh(self):
        text = (
            "timestamp,kwh,mpan\n"
            "2026-01-08T00:00:00Z,1.2,9130847265\n"
            "2026-01-08T00:30:00Z,1.4,9130847265\n"
        )
        rows = parse_readings_csv(text)
        assert len(rows) == 2
        assert rows[0]["mpan"] == "9130847265"
        assert rows[0]["consumption_kwh"] == "1.2"
        assert rows[1]["reading_at"] == "2026-01-08T00:30:00Z"

