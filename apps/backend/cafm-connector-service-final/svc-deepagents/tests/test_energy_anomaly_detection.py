from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from src.services.energy_hw1.anomaly_detection import detect


def hourly_profile(as_of: date, *, changed_after: date | None = None):
    start = datetime.combine(as_of - timedelta(days=34), datetime.min.time(), tzinfo=timezone.utc)
    end = datetime.combine(as_of + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
    rows = []
    stamp = start
    while stamp < end:
        amount = Decimal("12") if stamp.date() >= as_of - timedelta(days=6) else Decimal("10")
        rows.append((stamp, amount))
        stamp += timedelta(hours=1)
    if changed_after:
        # The adapter consumes occupancy evidence separately; this test helper's parameter
        # makes the requested change boundary visible in test names/call sites.
        assert changed_after <= as_of
    return rows


def test_detector_finds_persistent_rise_against_matched_hour_median():
    as_of = date(2026, 10, 25)
    result = detect(hourly_profile(as_of), as_of=as_of, tariff=Decimal("0.25"))

    assert result["available"] is True
    finding = next(row for row in result["findings"] if row["type"] == "baseline_drift")
    assert Decimal(finding["metric_pct"]) > Decimal("110")
    assert Decimal(finding["excess_kwh"]) > 0
    assert Decimal(finding["financial_gbp"]) > 0
    assert finding["window_start"] < finding["window_end"]


def test_detector_does_not_claim_clean_when_hourly_window_is_sparse():
    as_of = date(2026, 10, 25)
    result = detect(hourly_profile(as_of)[:100], as_of=as_of, tariff=Decimal("0.25"))

    assert result["available"] is False
    assert result["findings"] == []
    assert result["reason"]


def test_detector_only_returns_submeter_spike_for_submeter():
    as_of = date(2026, 10, 25)
    rows = hourly_profile(as_of)
    rows = [(stamp, amount * 2 if stamp >= datetime(2026, 10, 23, tzinfo=timezone.utc) else amount)
            for stamp, amount in rows]

    building_result = detect(rows, as_of=as_of, tariff=Decimal("0.25"), is_sub_meter=False)
    asset_result = detect(rows, as_of=as_of, tariff=Decimal("0.25"), is_sub_meter=True)

    assert not any(row["type"] == "asset_spike" for row in building_result["findings"])
    assert any(row["type"] == "asset_spike" for row in asset_result["findings"])


def test_detector_finds_weekend_spike_against_other_complete_weekends():
    as_of = date(2026, 10, 25)
    rows = hourly_profile(as_of)
    rows = [(stamp, Decimal("16") if stamp.date() >= as_of - timedelta(days=1) else amount)
            for stamp, amount in rows]

    result = detect(rows, as_of=as_of, tariff=Decimal("0.25"))

    finding = next(row for row in result["findings"] if row["type"] == "weekend_spike")
    assert Decimal(finding["metric_pct"]) > Decimal("130")
    assert Decimal(finding["financial_gbp"]) > 0
