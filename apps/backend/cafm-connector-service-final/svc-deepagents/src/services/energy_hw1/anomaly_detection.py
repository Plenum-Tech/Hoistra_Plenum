"""Deterministic anomaly calculations for source-verified Homework Energy rows.

The production Energy engine uses SQLAlchemy models owned by another service. This
module keeps the Homework profile independent of that database while preserving its
approved thresholds and comparable-hour method.
"""
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from statistics import median


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _bucket(stamp: datetime) -> tuple[bool, int]:
    stamp = _aware(stamp)
    return stamp.weekday() >= 5, stamp.hour


def _profile(rows):
    buckets = defaultdict(list)
    for stamp, amount in rows:
        buckets[_bucket(stamp)].append(amount)
    return {key: median(values) for key, values in buckets.items() if values}


def _cost(excess_kwh: Decimal, tariff: Decimal | None, annualizer: Decimal):
    values = {
        "excess_kwh": str(max(Decimal(0), excess_kwh)),
        "annualized_excess_kwh": str(max(Decimal(0), excess_kwh) * annualizer),
        "tariff_gbp_per_kwh": str(tariff) if tariff is not None else None,
        "financial_gbp": (str(max(Decimal(0), excess_kwh) * tariff)
                          if tariff is not None else None),
        "annualized_financial_gbp": (str(max(Decimal(0), excess_kwh) * annualizer * tariff)
                                    if tariff is not None else None),
    }
    return values


def detect(readings, *, as_of, tariff=None, is_sub_meter=False):
    """Return source-window findings and evidence state; never infer missing hours as 0."""
    scan_start = datetime.combine(as_of - timedelta(days=34), datetime.min.time(), tzinfo=timezone.utc)
    scan_end = datetime.combine(as_of + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
    normalized = sorted(
        ((_aware(stamp), Decimal(str(amount))) for stamp, amount in readings
         if scan_start <= _aware(stamp) < scan_end), key=lambda row: row[0]
    )
    # Duplicate timestamps make a median/energy comparison ambiguous. The caller reports
    # incomplete evidence rather than silently choosing one of the source values.
    if not normalized or len({stamp for stamp, _ in normalized}) != len(normalized):
        return {"available": False, "reason": "hourly readings are missing or duplicated", "findings": []}
    if any(amount < 0 for _, amount in normalized):
        return {"available": False, "reason": "negative consumption requires source review", "findings": []}

    # The latest 48 hours are compared only with the same weekend/hour buckets from the
    # preceding 33 days. Require a complete target window and at least two reference values
    # for every target bucket to avoid treating a single observation as a stable median.
    target_start = scan_end - timedelta(hours=48)
    target = [(stamp, amount) for stamp, amount in normalized if target_start <= stamp < scan_end]
    baseline = [(stamp, amount) for stamp, amount in normalized if stamp < target_start]
    expected_hours = [target_start + timedelta(hours=i) for i in range(48)]
    if [stamp for stamp, _ in target] != expected_hours:
        return {"available": False, "reason": "the latest 48-hour reading window is incomplete", "findings": []}
    reference = defaultdict(list)
    for stamp, amount in baseline:
        reference[_bucket(stamp)].append(amount)
    if any(len(reference[_bucket(stamp)]) < 2 for stamp in expected_hours):
        return {"available": False, "reason": "matched weekend/hour baseline is incomplete", "findings": []}

    expected = [Decimal(str(median(reference[_bucket(stamp)]))) for stamp in expected_hours]
    expected_total = sum(expected, Decimal(0))
    actual_total = sum((amount for _, amount in target), Decimal(0))
    if expected_total <= 0:
        return {"available": False, "reason": "matched baseline has no positive consumption", "findings": []}
    matched_pct = actual_total * Decimal(100) / expected_total
    profile = _profile(baseline)
    findings = []

    # The existing Energy contract treats a weekend load above 130% of comparable
    # weekend hours as a spike. Compare complete Saturday/Sunday periods and select the
    # strongest supported weekend; partial weekends are not a clean bill of health.
    by_stamp = {stamp: amount for stamp, amount in normalized}
    saturday_dates = sorted({stamp.date() - timedelta(days=stamp.weekday() - 5)
                             for stamp, _ in normalized if stamp.weekday() >= 5})
    weekend_blocks = {}
    for saturday in saturday_dates:
        start = datetime.combine(saturday, datetime.min.time(), tzinfo=timezone.utc)
        stamps = [start + timedelta(hours=i) for i in range(48)]
        if all(stamp in by_stamp for stamp in stamps):
            weekend_blocks[saturday] = [(stamp, by_stamp[stamp]) for stamp in stamps]
    weekend_hit = None
    for saturday, block in weekend_blocks.items():
        other_blocks = [rows for day, rows in weekend_blocks.items() if day != saturday]
        if not other_blocks:
            continue
        expected_values = []
        for stamp, _ in block:
            # Match each observation by weekend/hour. Saturdays and Sundays both map to
            # the same established weekend bucket; the median limits a single fault week.
            peers = [amount for rows in other_blocks for peer_stamp, amount in rows
                     if _bucket(peer_stamp) == _bucket(stamp)]
            if len(peers) < 2:
                expected_values = []
                break
            expected_values.append(Decimal(str(median(peers))))
        if len(expected_values) != 48:
            continue
        expected_sum = sum(expected_values, Decimal(0))
        actual_sum = sum((amount for _, amount in block), Decimal(0))
        ratio = actual_sum * Decimal(100) / expected_sum if expected_sum > 0 else Decimal(0)
        if ratio > 130 and (weekend_hit is None or ratio > weekend_hit[0]):
            weekend_hit = ratio, saturday, block, actual_sum - expected_sum
    if weekend_hit:
        ratio, saturday, block, excess = weekend_hit
        findings.append({"type": "weekend_spike", "metric_pct": str(ratio.quantize(Decimal("0.01"))),
            "window_start": block[0][0].isoformat(), "window_end": block[-1][0].isoformat(),
            "weekend_start": saturday.isoformat(),
            **_cost(excess, tariff, Decimal(52))})

    # Established baseline-drift threshold: the current 7-day level must exceed 110% of
    # the preceding 7-day level and its final 48 hours must also exceed the matched profile.
    current_start = scan_end - timedelta(days=7)
    previous_start = current_start - timedelta(days=7)
    current = [(s, v) for s, v in normalized if current_start <= s < scan_end]
    previous = [(s, v) for s, v in normalized if previous_start <= s < current_start]
    if len(current) == 168 and len(previous) == 168 and all(_bucket(s) in profile for s, _ in target):
        current_total = sum((v for _, v in current), Decimal(0))
        previous_total = sum((v for _, v in previous), Decimal(0))
        # A logged occupancy change in the scan window suppresses this finding in the caller.
        if previous_total > 0 and current_total * 100 > previous_total * 110 and matched_pct > 110:
            excess = max(Decimal(0), current_total - previous_total)
            findings.append({"type": "baseline_drift", "metric_pct": str(
                (current_total * 100 / previous_total).quantize(Decimal("0.01"))),
                "window_start": current_start.isoformat(), "window_end": (scan_end - timedelta(hours=1)).isoformat(),
                **_cost(excess, tariff, Decimal(365) / Decimal(7))})

    # Established sub-meter threshold: any complete 48-hour comparable window above 120%.
    if is_sub_meter:
        best = None
        for index in range(len(normalized) - 47):
            block = normalized[index:index + 48]
            if block[-1][0] - block[0][0] != timedelta(hours=47):
                continue
            expected_block = [Decimal(str(median(reference[_bucket(stamp)])))
                              for stamp, _ in block if reference.get(_bucket(stamp))]
            if len(expected_block) != 48:
                continue
            expected_sum = sum(expected_block, Decimal(0))
            actual_sum = sum((amount for _, amount in block), Decimal(0))
            if expected_sum > 0 and actual_sum * 100 > expected_sum * 120:
                ratio = actual_sum * 100 / expected_sum
                if best is None or ratio > best[0]:
                    best = ratio, block[0][0], block[-1][0], actual_sum - expected_sum
        if best:
            ratio, start, end, excess = best
            findings.append({"type": "asset_spike", "metric_pct": str(ratio.quantize(Decimal("0.01"))),
                "window_start": start.isoformat(), "window_end": end.isoformat(),
                **_cost(excess, tariff, Decimal(365) / Decimal(2))})

    return {"available": True, "reason": None, "findings": findings,
            "window_start": scan_start.isoformat(), "window_end": (scan_end - timedelta(hours=1)).isoformat(),
            "hourly_reading_count": len(normalized), "matched_48h_pct": str(matched_pct.quantize(Decimal("0.01")))}
