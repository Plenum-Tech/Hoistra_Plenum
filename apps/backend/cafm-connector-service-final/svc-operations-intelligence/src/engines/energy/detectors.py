"""The ten detection rules the rule card names and the engine did not have.

anomalies.py detects three things: a weekend spike, a baseline drift and a single-asset
spike. The energy page lists thirteen rules. These are the other ten — each a pure function
over readings (and, where the rule needs it, one more input: an occupancy calendar, a tariff
band, degree days, BMS trends, a closed work order, a chiller's design figure) that returns
the same dict the three existing detectors return, so scan_meter_anomalies can run them
without learning anything new:

    {"anomaly_type", "metric_pct", "excess_kwh", "annualised_excess_kwh",
     "financial_gbp", "tariff_gbp_per_kwh", "detail"}

or None when the rule does not fire. Nothing here touches the database; the scan loads what
each rule needs and hands it in, and a rule whose input is absent is skipped, not faked.

Two conventions the rule card sets and these keep. A rule's threshold is the one printed on
the card (30% for non-occupancy, 45 minutes for schedule, 15% for kW/RT, 30 minutes for
simultaneous heating and cooling). And data quality is "scored as a data fault, never a
building fault": it carries no financial figure, because a gap in the feed costs nothing and
pricing it would put a fictional number on the PM's queue.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from statistics import median
from typing import Any, Sequence

Readings = Sequence[tuple[datetime, float]]

NONOCC_RATIO_PCT = 30.0          # unoccupied-hours mean as % of occupied-hours mean
NONOCC_NOVELTY_POINTS = 5.0      # …and at least this many points above its own baseline
SCHEDULE_TOLERANCE_MIN = 45
BASELOAD_WEEKS = 3               # consecutive week-on-week rises
BASELOAD_RISE_PCT = 3.0
DAYTIME_FLAT_PCT = 5.0
PEAK_MIN_INTERVALS = 48
DATAQ_MIN_PCT = 2.0              # share of intervals that are gaps / flat / estimated
FLATLINE_RUN = 12                # identical consecutive intervals (6 h at half-hourly)
GAP_MIN_HOURS = 2.0
TOU_DISPROPORTION_PCT = 125.0    # peak-band mean load vs the rest of the working day
TOU_DEFAULT_PREMIUM = 0.5        # peak rate premium as a fraction of the flat tariff
WEATHER_MIN_MONTHS = 12
CUSUM_SLACK_SIGMA = 0.5
CUSUM_LIMIT_SIGMA = 4.0
FIGHT_MIN_MINUTES = 30
FIGHT_CALL_PCT = 10.0            # a valve / call signal above this is "calling"
REGRESS_HORIZON_DAYS = 30
REGRESS_FIX_DROP = 0.90          # the fix must have brought load to ≤ 90% of pre-fix
REGRESS_BACK_TO = 0.95           # …and it is back once load is ≥ 95% of pre-fix
COP_EXCESS_PCT = 115.0           # kW/RT more than 15% above design
COP_MIN_LOAD_FRACTION = 0.40
COP_AMBIENT_BAND_C = 2.0


@dataclass(frozen=True)
class OccupancyHours:
    """When the building is meant to be occupied. Local clock hours, half-open [start, end)."""

    start_hour: int = 7
    end_hour: int = 19
    weekdays_only: bool = True

    def occupied(self, t: datetime) -> bool:
        if self.weekdays_only and t.weekday() >= 5:
            return False
        return self.start_hour <= t.hour < self.end_hour


DEFAULT_OCCUPANCY = OccupancyHours()


@dataclass(frozen=True)
class TariffBand:
    """A priced band of the day. Rates are per kWh in the meter's currency; None = unknown."""

    name: str
    start_hour: int
    end_hour: int
    weekdays_only: bool = True
    rate: float | None = None

    def covers(self, t: datetime) -> bool:
        if self.weekdays_only and t.weekday() >= 5:
            return False
        return self.start_hour <= t.hour < self.end_hour

    @property
    def hours(self) -> float:
        return float(self.end_hour - self.start_hour)


#: UK DUoS red band. The default when a meter carries no bands of its own.
DEFAULT_PEAK_BANDS: tuple[TariffBand, ...] = (TariffBand("peak", 16, 19, True, None),)


# ── shared helpers ───────────────────────────────────────────────────────────────────

def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _sorted(readings: Readings) -> list[tuple[datetime, float]]:
    return sorted(((_aware(t), float(k)) for t, k in readings), key=lambda x: x[0])


def _interval_minutes(pts: list[tuple[datetime, float]]) -> int:
    """The feed's interval, as the median gap between consecutive stamps. 30 if unknown."""
    if len(pts) < 2:
        return 30
    gaps = [(b[0] - a[0]).total_seconds() / 60 for a, b in zip(pts, pts[1:])]
    gaps = [g for g in gaps if g > 0]
    return int(round(median(gaps))) if gaps else 30


def _mean(vals: Sequence[float]) -> float | None:
    return sum(vals) / len(vals) if vals else None


def _finance(excess_kwh: float, annualised_frequency: float, tariff: float) -> dict[str, float]:
    annualised = max(0.0, excess_kwh) * annualised_frequency
    return {
        "excess_kwh": round(max(0.0, excess_kwh), 4),
        "annualised_excess_kwh": round(annualised, 4),
        "financial_gbp": round(annualised * tariff, 2),
        "tariff_gbp_per_kwh": tariff,
    }


def _window(pts: list[tuple[datetime, float]], days: int) -> list[tuple[datetime, float]]:
    end = pts[-1][0]
    start = end - timedelta(days=days)
    return [(t, k) for t, k in pts if t > start]


# ── nonocc · Non-occupancy spike ─────────────────────────────────────────────────────

def detect_nonocc_spike(
    readings: Readings,
    *,
    tariff: float,
    occupancy: OccupancyHours = DEFAULT_OCCUPANCY,
    window_days: int = 7,
) -> dict[str, Any] | None:
    """Consumption in unoccupied hours > 30% of the occupied-hours average.

    Over the last ``window_days``. The card's 30% is the rule; the novelty guard is ours: a
    building whose overnight load has always sat at 32% of daytime is not a spike, so when
    the readings reach back far enough to know the meter's own ratio, the current one must
    also be at least five points above it. With no history to compare, the card's rule
    stands alone.
    """
    pts = _sorted(readings)
    if len(pts) < 48:
        return None
    cur = _window(pts, window_days)
    prior = [(t, k) for t, k in pts if t <= cur[0][0]] if cur else []

    def ratio(window: list[tuple[datetime, float]]) -> tuple[float | None, float | None, float | None]:
        occ = [k for t, k in window if occupancy.occupied(t)]
        unocc = [k for t, k in window if not occupancy.occupied(t)]
        o, u = _mean(occ), _mean(unocc)
        if o is None or u is None or o <= 0:
            return None, o, u
        return 100.0 * u / o, o, u

    ratio_pct, occ_mean, unocc_mean = ratio(cur)
    if ratio_pct is None or ratio_pct <= NONOCC_RATIO_PCT:
        return None
    baseline_pct, _, _ = ratio(prior) if len(prior) >= 48 * 7 else (None, None, None)
    if baseline_pct is not None and ratio_pct < baseline_pct + NONOCC_NOVELTY_POINTS:
        return None

    allowed = occ_mean * NONOCC_RATIO_PCT / 100.0
    excess = sum(max(0.0, k - allowed) for t, k in cur if not occupancy.occupied(t))
    return {
        "anomaly_type": "nonocc_spike",
        "metric_pct": round(ratio_pct, 2),
        **_finance(excess, 365.0 / window_days, tariff),
        "detail": {
            "occupied_mean_kwh": round(occ_mean, 4),
            "unoccupied_mean_kwh": round(unocc_mean, 4),
            "baseline_ratio_pct": round(baseline_pct, 2) if baseline_pct is not None else None,
            "occupancy": f"{occupancy.start_hour:02d}:00–{occupancy.end_hour:02d}:00"
                         + (" weekdays" if occupancy.weekdays_only else ""),
            "window_days": window_days,
        },
    }


# ── schedule · Schedule mismatch ─────────────────────────────────────────────────────

def _day_switch_times(
    day: list[tuple[datetime, float]], interval_min: int
) -> tuple[datetime | None, datetime | None, float]:
    """When plant came on and went off: the first and last interval above the midpoint
    between the day's overnight base and its peak. Returns (start, stop, base)."""
    if len(day) < 12:
        return None, None, 0.0
    overnight = [k for t, k in day if t.hour < 5] or [k for _, k in day]
    base = min(overnight)
    vals = sorted(k for _, k in day)
    peak = vals[int(0.95 * (len(vals) - 1))]
    if peak <= base * 1.2:
        return None, None, base  # no daytime profile to time
    threshold = base + 0.5 * (peak - base)
    on = [t for t, k in day if k >= threshold]
    if not on:
        return None, None, base
    return on[0], on[-1] + timedelta(minutes=interval_min), base


def detect_schedule_mismatch(
    readings: Readings,
    *,
    tariff: float,
    occupancy: OccupancyHours = DEFAULT_OCCUPANCY,
    window_days: int = 7,
) -> dict[str, Any] | None:
    """Plant start or stop more than 45 minutes outside the occupancy calendar.

    Timed from the load curve: the first and last interval of each working day above the
    midpoint between that day's overnight base and its peak. Judged on the median over the
    window's working days, so one early Monday does not fire it and one holiday does not
    hide it.
    """
    pts = _sorted(readings)
    if len(pts) < 48 * 3:
        return None
    interval = _interval_minutes(pts)
    cur = _window(pts, window_days)
    by_day: dict[date, list[tuple[datetime, float]]] = {}
    for t, k in cur:
        if occupancy.weekdays_only and t.weekday() >= 5:
            continue
        by_day.setdefault(t.date(), []).append((t, k))

    early: list[float] = []
    late: list[float] = []
    excess = 0.0
    evaluated = 0
    for d, day in sorted(by_day.items()):
        start, stop, base = _day_switch_times(day, interval)
        if start is None or stop is None:
            continue
        evaluated += 1
        sched_start = start.replace(hour=occupancy.start_hour, minute=0, second=0, microsecond=0)
        sched_end = start.replace(hour=occupancy.end_hour, minute=0, second=0, microsecond=0)
        early.append((sched_start - start).total_seconds() / 60)
        late.append((stop - sched_end).total_seconds() / 60)
        # what ran above base while the calendar said the building was shut
        excess += sum(max(0.0, k - base) for t, k in day
                      if (t < sched_start and t >= start) or (t >= sched_end and t < stop))
    if evaluated < 3:
        return None
    med_early, med_late = median(early), median(late)
    worst = max(med_early, med_late)
    if worst <= SCHEDULE_TOLERANCE_MIN:
        return None
    return {
        "anomaly_type": "schedule_mismatch",
        "metric_pct": round(100.0 * worst / SCHEDULE_TOLERANCE_MIN, 2),
        **_finance(excess, 365.0 / window_days, tariff),
        "detail": {
            "median_start_early_min": round(med_early, 1),
            "median_stop_late_min": round(med_late, 1),
            "days_evaluated": evaluated,
            "scheduled": f"{occupancy.start_hour:02d}:00–{occupancy.end_hour:02d}:00",
            "tolerance_min": SCHEDULE_TOLERANCE_MIN,
        },
    }


# ── baseload · Baseload creep ────────────────────────────────────────────────────────

def detect_baseload_creep(
    readings: Readings,
    *,
    tariff: float,
) -> dict[str, Any] | None:
    """Overnight minimum rising week on week while daytime is flat.

    Three consecutive weekly rises of at least 3% in the overnight floor (median of each
    day's 00:00–05:00 minimum), with the weekday daytime median moving less than 5% over the
    same span. Daytime flat is what separates creep — something left on — from the whole
    building simply getting busier, which baseline_drift already reports.
    """
    pts = _sorted(readings)
    if len(pts) < 48 * 7 * (BASELOAD_WEEKS + 1):
        return None
    weeks: dict[tuple[int, int], list[tuple[datetime, float]]] = {}
    for t, k in pts:
        iso = t.isocalendar()
        weeks.setdefault((iso[0], iso[1]), []).append((t, k))
    keys = sorted(weeks)
    # only complete weeks (≥ 6 days of data) count
    complete = [w for w in keys if len({t.date() for t, _ in weeks[w]}) >= 6]
    if len(complete) < BASELOAD_WEEKS + 1:
        return None
    complete = complete[-(BASELOAD_WEEKS + 1):]

    floors: list[float] = []
    daytime: list[float] = []
    for w in complete:
        by_day: dict[date, list[float]] = {}
        day_vals: list[float] = []
        for t, k in weeks[w]:
            if t.hour < 5:
                by_day.setdefault(t.date(), []).append(k)
            if t.weekday() < 5 and 9 <= t.hour < 17:
                day_vals.append(k)
        if not by_day or not day_vals:
            return None
        floors.append(median(min(v) for v in by_day.values()))
        daytime.append(median(day_vals))
    if floors[0] <= 0 or daytime[0] <= 0:
        return None
    rises = [100.0 * (b - a) / a for a, b in zip(floors, floors[1:])]
    if any(r < BASELOAD_RISE_PCT for r in rises):
        return None
    daytime_move = 100.0 * (daytime[-1] - daytime[0]) / daytime[0]
    if abs(daytime_move) > DAYTIME_FLAT_PCT:
        return None
    interval = _interval_minutes(pts)
    per_week = 7 * 24 * 60 / interval
    excess = (floors[-1] - floors[0]) * per_week
    return {
        "anomaly_type": "baseload_creep",
        "metric_pct": round(100.0 * floors[-1] / floors[0], 2),
        **_finance(excess, 52.0, tariff),
        "detail": {
            "weekly_overnight_floor_kwh": [round(f, 4) for f in floors],
            "weekly_rise_pct": [round(r, 2) for r in rises],
            "weekly_daytime_median_kwh": [round(d, 4) for d in daytime],
            "daytime_move_pct": round(daytime_move, 2),
            "weeks": [f"{y}-W{w:02d}" for y, w in complete],
        },
    }


# ── peak · Peak demand excursion ─────────────────────────────────────────────────────

def detect_peak_excursion(
    readings: Readings,
    *,
    tariff: float,
    capacity_kw: float | None = None,
    prior_year_max_kw: float | None = None,
    window_days: int = 7,
) -> dict[str, Any] | None:
    """kW peak above agreed capacity or the prior-year maximum.

    Interval kWh becomes average kW over the interval. The limit is the agreed capacity when
    the meter has one, else the prior-year maximum; with neither the rule has nothing to
    judge against and does not fire.
    """
    limit = capacity_kw or prior_year_max_kw
    if not limit or limit <= 0:
        return None
    pts = _sorted(readings)
    if len(pts) < PEAK_MIN_INTERVALS:
        return None
    interval = _interval_minutes(pts)
    hours = interval / 60.0
    cur = _window(pts, window_days)
    kw = [(t, k / hours) for t, k in cur]
    peak_t, peak_kw = max(kw, key=lambda x: x[1])
    if peak_kw <= limit:
        return None
    over = [(t, p) for t, p in kw if p > limit]
    excess = sum((p - limit) * hours for _, p in over)
    return {
        "anomaly_type": "peak_excursion",
        "metric_pct": round(100.0 * peak_kw / limit, 2),
        **_finance(excess, 12.0, tariff),
        "detail": {
            "peak_kw": round(peak_kw, 3),
            "peak_at": peak_t.isoformat(),
            "limit_kw": round(limit, 3),
            "limit_basis": "agreed_capacity" if capacity_kw else "prior_year_max",
            "intervals_over": len(over),
            "interval_minutes": interval,
        },
    }


# ── dataq · Data-quality anomaly ─────────────────────────────────────────────────────

def detect_data_quality(
    readings: Readings,
    *,
    tariff: float,
    flags: Sequence[tuple[datetime, str | None]] | None = None,
) -> dict[str, Any] | None:
    """Gaps, flatlines or estimated reads in the feed — a data fault, never a building fault.

    No financial figure, deliberately: nothing was consumed by a gap. The metric is the share
    of expected intervals that are missing, flat, estimated or negative.
    """
    pts = _sorted(readings)
    if len(pts) < 48:
        return None
    interval = _interval_minutes(pts)
    step = timedelta(minutes=interval)

    missing = 0
    gaps: list[dict[str, Any]] = []
    for a, b in zip(pts, pts[1:]):
        gap = b[0] - a[0]
        if gap >= max(step * 2, timedelta(hours=GAP_MIN_HOURS)):
            n = int(gap / step) - 1
            missing += n
            gaps.append({"from": a[0].isoformat(), "to": b[0].isoformat(),
                         "hours": round(gap.total_seconds() / 3600, 2)})

    flat = 0
    flat_runs = 0
    run = 1
    for a, b in zip(pts, pts[1:]):
        if b[1] == a[1] and a[1] > 0:
            run += 1
        else:
            if run >= FLATLINE_RUN:
                flat += run
                flat_runs += 1
            run = 1
    if run >= FLATLINE_RUN:
        flat += run
        flat_runs += 1

    negative = sum(1 for _, k in pts if k < 0)
    estimated = 0
    if flags:
        estimated = sum(1 for _, f in flags
                        if f and str(f).strip().lower() in ("estimated", "estimate", "e", "est"))

    expected = len(pts) + missing
    bad = missing + flat + negative + estimated
    if expected <= 0:
        return None
    pct = 100.0 * bad / expected
    if pct < DATAQ_MIN_PCT and negative == 0:
        return None
    return {
        "anomaly_type": "data_quality",
        "metric_pct": round(pct, 2),
        "excess_kwh": 0.0,
        "annualised_excess_kwh": 0.0,
        "financial_gbp": 0.0,
        "tariff_gbp_per_kwh": tariff,
        "detail": {
            "missing_intervals": missing,
            "gaps": gaps[:20],
            "longest_gap_hours": max((g["hours"] for g in gaps), default=0.0),
            "flatline_intervals": flat,
            "flatline_runs": flat_runs,
            "estimated_reads": estimated,
            "negative_reads": negative,
            "expected_intervals": expected,
            "interval_minutes": interval,
            "note": "scored as a data fault, never a building fault",
        },
    }


# ── tou · Time-of-use misalignment ───────────────────────────────────────────────────

def detect_tou_misalignment(
    readings: Readings,
    *,
    tariff: float,
    bands: Sequence[TariffBand] = DEFAULT_PEAK_BANDS,
    offpeak_rate: float | None = None,
    occupancy: OccupancyHours = DEFAULT_OCCUPANCY,
    window_days: int = 7,
) -> dict[str, Any] | None:
    """Shiftable load sitting in the peak price band.

    The band's mean interval load is compared with the same day's mean over the other
    occupied hours — not with the day's time share, because a working building naturally
    puts more of its energy into 16:00–19:00 than into 03:00–06:00 and that is occupancy,
    not misalignment. Load in the band above what the rest of the working day draws is the
    discretionary part: what could run at another hour. It is priced at the band premium
    when the rates are known and at half the flat tariff when they are not, and says which.
    """
    pts = _sorted(readings)
    if len(pts) < 48 * 3 or not bands:
        return None
    cur = _window(pts, window_days)
    by_day: dict[date, list[tuple[datetime, float]]] = {}
    for t, k in cur:
        by_day.setdefault(t.date(), []).append((t, k))

    def applies(b: TariffBand, t: datetime) -> bool:
        return not (b.weekdays_only and t.weekday() >= 5)

    band_vals: list[float] = []
    ref_vals: list[float] = []
    shiftable = 0.0
    days = 0
    for d, day in by_day.items():
        active = [b for b in bands if applies(b, day[0][0])]
        if not active:
            continue
        in_band = [k for t, k in day if any(b.covers(t) for b in active)]
        reference = [k for t, k in day
                     if not any(b.covers(t) for b in active) and occupancy.occupied(t)]
        if not reference:
            reference = [k for t, k in day if not any(b.covers(t) for b in active)]
        if not in_band or not reference:
            continue
        days += 1
        ref_mean = sum(reference) / len(reference)
        band_vals.extend(in_band)
        ref_vals.extend(reference)
        shiftable += sum(max(0.0, k - ref_mean) for k in in_band)
    if days == 0 or not ref_vals:
        return None
    band_mean = sum(band_vals) / len(band_vals)
    ref_mean = sum(ref_vals) / len(ref_vals)
    if ref_mean <= 0:
        return None
    pct = 100.0 * band_mean / ref_mean
    if pct <= TOU_DISPROPORTION_PCT:
        return None
    peak_rate = next((b.rate for b in bands if b.rate), None)
    if peak_rate is not None and offpeak_rate is not None and peak_rate > offpeak_rate:
        premium, basis = peak_rate - offpeak_rate, "band_rates"
    else:
        premium, basis = tariff * TOU_DEFAULT_PREMIUM, "default_premium_half_of_tariff"
    annualised = shiftable * (260.0 / days)
    return {
        "anomaly_type": "tou_misalignment",
        "metric_pct": round(pct, 2),
        "excess_kwh": round(shiftable, 4),
        "annualised_excess_kwh": round(annualised, 4),
        "financial_gbp": round(annualised * premium, 2),
        "tariff_gbp_per_kwh": tariff,
        "detail": {
            "peak_band_mean_kwh": round(band_mean, 4),
            "other_occupied_mean_kwh": round(ref_mean, 4),
            "bands": [f"{b.name} {b.start_hour:02d}:00–{b.end_hour:02d}:00" for b in bands],
            "premium_per_kwh": round(premium, 6),
            "premium_basis": basis,
            "days_evaluated": days,
        },
    }


# ── weather · Weather-normalised residual (CUSUM) ────────────────────────────────────

def _ols(y: list[float], xs: list[list[float]]) -> list[float] | None:
    """Ordinary least squares with an intercept, by normal equations. None if singular."""
    n = len(y)
    cols = [[1.0] * n] + xs
    p = len(cols)
    ata = [[sum(cols[i][r] * cols[j][r] for r in range(n)) for j in range(p)] for i in range(p)]
    aty = [sum(cols[i][r] * y[r] for r in range(n)) for i in range(p)]
    # Gauss-Jordan
    m = [row[:] + [aty[i]] for i, row in enumerate(ata)]
    for c in range(p):
        pivot = max(range(c, p), key=lambda r: abs(m[r][c]))
        if abs(m[pivot][c]) < 1e-12:
            return None
        m[c], m[pivot] = m[pivot], m[c]
        div = m[c][c]
        m[c] = [v / div for v in m[c]]
        for r in range(p):
            if r != c and m[r][c] != 0:
                f = m[r][c]
                m[r] = [a - f * b for a, b in zip(m[r], m[c])]
    return [m[i][p] for i in range(p)]


def detect_weather_residual(
    monthly_kwh: Sequence[tuple[date, float]],
    degree_days: Sequence[tuple[date, float, float]],
    *,
    tariff: float,
) -> dict[str, Any] | None:
    """CUSUM on degree-day regression residuals breaches the control limit.

    kWh per month is regressed on heating and cooling degree days for the same months. The
    residuals are what weather does not explain. A one-sided CUSUM (slack 0.5σ, limit 4σ,
    σ from the median absolute deviation so one bad month does not inflate it) accumulates
    positive residuals; the rule fires when the sum breaches the limit — consumption has
    been running above what the weather accounts for, for long enough to matter.
    """
    dd = {(d.year, d.month): (h, c) for d, h, c in degree_days}
    rows = sorted(((d, k) for d, k in monthly_kwh if (d.year, d.month) in dd), key=lambda x: x[0])
    if len(rows) < WEATHER_MIN_MONTHS:
        return None
    y = [k for _, k in rows]
    hdd = [dd[(d.year, d.month)][0] for d, _ in rows]
    cdd = [dd[(d.year, d.month)][1] for d, _ in rows]
    coef = _ols(y, [hdd, cdd])
    model = "hdd+cdd"
    if coef is None:
        coef = _ols(y, [hdd])
        model = "hdd"
        if coef is None:
            return None
    def predict(i: int) -> float:
        return coef[0] + coef[1] * hdd[i] + (coef[2] * cdd[i] if len(coef) > 2 else 0.0)
    resid = [y[i] - predict(i) for i in range(len(y))]
    med = median(resid)
    mad = median(abs(r - med) for r in resid)
    sigma = 1.4826 * mad if mad > 0 else (sum(r * r for r in resid) / len(resid)) ** 0.5
    if sigma <= 0:
        return None
    k, h = CUSUM_SLACK_SIGMA * sigma, CUSUM_LIMIT_SIGMA * sigma
    s = 0.0
    run_start = 0
    path: list[float] = []
    for i, r in enumerate(resid):
        s = max(0.0, s + (r - k))
        if s == 0.0:
            run_start = i + 1
        path.append(round(s, 3))
    if s <= h:
        return None
    run = list(range(run_start, len(resid)))
    excess = sum(resid[i] for i in run if resid[i] > 0)
    return {
        "anomaly_type": "weather_residual",
        "metric_pct": round(100.0 * s / h, 2),
        **_finance(excess, 12.0 / max(1, len(run)), tariff),
        "detail": {
            "model": model,
            "coefficients": {"intercept": round(coef[0], 3), "per_hdd": round(coef[1], 4),
                             "per_cdd": round(coef[2], 4) if len(coef) > 2 else None},
            "sigma_kwh": round(sigma, 3),
            "cusum": path[-12:],
            "control_limit": round(h, 3),
            "run_months": [rows[i][0].strftime("%Y-%m") for i in run],
            "months_fitted": len(rows),
        },
    }


# ── fight · Simultaneous heating and cooling ─────────────────────────────────────────

def detect_simultaneous_heating_cooling(
    trends: Sequence[tuple[datetime, str, float, float]],
    *,
    tariff: float,
    zone_kw: float | None = None,
) -> dict[str, Any] | None:
    """Heating and cooling both calling in the same zone for more than 30 minutes.

    ``trends`` are BMS samples: (timestamp, zone, heating %, cooling %). A run is consecutive
    samples in one zone with both signals above 10%. Priced only when the caller can say what
    the zone's plant draws; otherwise the excess is reported in hours and left unpriced,
    rather than invented.
    """
    by_zone: dict[str, list[tuple[datetime, float, float]]] = {}
    for t, zone, h, c in trends:
        by_zone.setdefault(str(zone), []).append((_aware(t), float(h), float(c)))
    runs: list[dict[str, Any]] = []
    for zone, samples in by_zone.items():
        samples.sort(key=lambda x: x[0])
        if len(samples) < 2:
            continue
        gaps = [(b[0] - a[0]).total_seconds() / 60 for a, b in zip(samples, samples[1:])]
        positive = [g for g in gaps if g > 0]
        step = median(positive) if positive else 15.0
        # Two trend exports covering the same hours arrive interleaved, and read literally
        # their timestamps make a five-minute cadence look like a one-minute one — which
        # chops every run below the threshold. So the zone is put on a regular grid at its
        # own cadence and each slot takes the STRONGEST signal seen in it: if one export
        # says heating is calling at 14:20 and another says cooling is, both are calling.
        slot = max(1.0, step)
        grid: dict[int, tuple[float, float]] = {}
        for t, h, c in samples:
            key = int(round(t.timestamp() / (slot * 60)))
            prev = grid.get(key, (0.0, 0.0))
            grid[key] = (max(prev[0], h), max(prev[1], c))
        keys = sorted(grid)
        start_key: int | None = None
        last_key: int | None = None
        for key in keys + [keys[-1] + 2]:
            h, c = grid.get(key, (0.0, 0.0))
            both = h > FIGHT_CALL_PCT and c > FIGHT_CALL_PCT
            contiguous = last_key is not None and key - last_key <= 1
            if both and (start_key is None or not contiguous):
                if start_key is not None and last_key is not None:
                    minutes = (last_key - start_key + 1) * slot
                    if minutes >= FIGHT_MIN_MINUTES:
                        runs.append({"zone": zone,
                                     "from": datetime.fromtimestamp(start_key * slot * 60, timezone.utc).isoformat(),
                                     "to": datetime.fromtimestamp(last_key * slot * 60, timezone.utc).isoformat(),
                                     "minutes": round(minutes, 1)})
                start_key = last_key = key
            elif both:
                last_key = key
            else:
                if start_key is not None and last_key is not None:
                    minutes = (last_key - start_key + 1) * slot
                    if minutes >= FIGHT_MIN_MINUTES:
                        runs.append({"zone": zone,
                                     "from": datetime.fromtimestamp(start_key * slot * 60, timezone.utc).isoformat(),
                                     "to": datetime.fromtimestamp(last_key * slot * 60, timezone.utc).isoformat(),
                                     "minutes": round(minutes, 1)})
                start_key = last_key = None
    if not runs:
        return None
    longest = max(r["minutes"] for r in runs)
    hours = sum(r["minutes"] for r in runs) / 60.0
    excess = hours * zone_kw if zone_kw else 0.0
    return {
        "anomaly_type": "simultaneous_heating_cooling",
        "metric_pct": round(100.0 * longest / FIGHT_MIN_MINUTES, 2),
        **_finance(excess, 52.0, tariff),
        "detail": {
            "runs": sorted(runs, key=lambda r: -r["minutes"])[:20],
            "zones_affected": sorted({r["zone"] for r in runs}),
            "fighting_hours": round(hours, 2),
            "zone_kw": zone_kw,
            "priced": bool(zone_kw),
            "note": None if zone_kw else "unpriced: no zone_kw supplied",
        },
    }


# ── regress · Post-works regression ──────────────────────────────────────────────────

def detect_post_works_regression(
    readings: Readings,
    *,
    tariff: float,
    closed_work_orders: Sequence[tuple[datetime, str]],
    window_days: int = 7,
    horizon_days: int = REGRESS_HORIZON_DAYS,
) -> dict[str, Any] | None:
    """Consumption back to pre-fix level within 30 days of a closed work order.

    For each work order closed inside the horizon: the mean load in the week before it
    closed, the week after, and the latest week. The fix counts as having worked if the
    week after was at or below 90% of the week before; it has regressed if the latest week
    is back at 95% or more of the week before. The worst regression is reported.
    """
    pts = _sorted(readings)
    if len(pts) < 48 * 14 or not closed_work_orders:
        return None
    end = pts[-1][0]
    span = timedelta(days=window_days)
    now_vals = [k for t, k in pts if t > end - span]
    now_mean = _mean(now_vals)
    if now_mean is None:
        return None
    best: dict[str, Any] | None = None
    for closed_at, ref in closed_work_orders:
        closed = _aware(closed_at)
        if closed > end or closed < end - timedelta(days=horizon_days):
            continue
        pre = [k for t, k in pts if closed - span <= t < closed]
        post = [k for t, k in pts if closed <= t < closed + span]
        pre_mean, post_mean = _mean(pre), _mean(post)
        if not pre_mean or post_mean is None or len(pre) < 48 or len(post) < 48:
            continue
        if post_mean > REGRESS_FIX_DROP * pre_mean:
            continue  # the fix never showed in the readings; nothing to regress from
        if closed + span >= end - span:
            continue  # the "after" week is the latest week; too soon to tell
        ratio = now_mean / pre_mean
        if ratio < REGRESS_BACK_TO:
            continue
        interval = _interval_minutes(pts)
        per_week = window_days * 24 * 60 / interval
        cand = {
            "anomaly_type": "post_works_regression",
            "metric_pct": round(100.0 * ratio, 2),
            **_finance((now_mean - post_mean) * per_week, 365.0 / window_days, tariff),
            "detail": {
                "work_order": ref,
                "closed_at": closed.isoformat(),
                "pre_fix_mean_kwh": round(pre_mean, 4),
                "post_fix_mean_kwh": round(post_mean, 4),
                "current_mean_kwh": round(now_mean, 4),
                "fix_drop_pct": round(100.0 * (1 - post_mean / pre_mean), 2),
                "days_since_close": (end - closed).days,
            },
        }
        if best is None or cand["metric_pct"] > best["metric_pct"]:
            best = cand
    return best


# ── cop · Chiller efficiency ─────────────────────────────────────────────────────────

def detect_chiller_efficiency(
    samples: Sequence[tuple[datetime, float, float, float | None]],
    *,
    tariff: float,
    design_kw_per_rt: float,
    design_capacity_rt: float | None = None,
    design_ambient_c: float | None = None,
    ambient_band_c: float = COP_AMBIENT_BAND_C,
) -> dict[str, Any] | None:
    """kW per RT more than 15% above design at matched ambient.

    ``samples`` are (timestamp, electrical kW, cooling load RT, ambient °C or None). Matched
    ambient means within ±2 °C of the design ambient when one is given; part-load samples
    below 40% of design capacity are dropped when the capacity is known, because every
    chiller is inefficient at low load and that is not a fault. The figure is load-weighted:
    total kW over total RT across the kept samples.
    """
    if design_kw_per_rt <= 0:
        return None
    kept: list[tuple[datetime, float, float, float | None]] = []
    for t, kw, rt, amb in samples:
        if rt is None or rt <= 0 or kw is None or kw < 0:
            continue
        if design_capacity_rt and rt < COP_MIN_LOAD_FRACTION * design_capacity_rt:
            continue
        if design_ambient_c is not None and amb is not None and abs(amb - design_ambient_c) > ambient_band_c:
            continue
        kept.append((_aware(t), float(kw), float(rt), amb))
    if len(kept) < 4:
        return None
    kept.sort(key=lambda x: x[0])
    total_kw = sum(kw for _, kw, _, _ in kept)
    total_rt = sum(rt for _, _, rt, _ in kept)
    actual = total_kw / total_rt
    pct = 100.0 * actual / design_kw_per_rt
    if pct <= COP_EXCESS_PCT:
        return None
    gaps = [(b[0] - a[0]).total_seconds() / 3600 for a, b in zip(kept, kept[1:])]
    step_h = median(g for g in gaps if g > 0) if any(g > 0 for g in gaps) else 0.25
    observed_h = len(kept) * step_h
    excess = sum((kw - design_kw_per_rt * rt) * step_h for _, kw, rt, _ in kept if kw > design_kw_per_rt * rt)
    ambs = [a for *_, a in kept if a is not None]
    return {
        "anomaly_type": "chiller_efficiency",
        "metric_pct": round(pct, 2),
        **_finance(excess, 8760.0 / observed_h if observed_h > 0 else 0.0, tariff),
        "detail": {
            "actual_kw_per_rt": round(actual, 4),
            "design_kw_per_rt": design_kw_per_rt,
            "samples_used": len(kept),
            "samples_total": len(samples),
            "observed_hours": round(observed_h, 2),
            "ambient_c_mean": round(sum(ambs) / len(ambs), 2) if ambs else None,
            "ambient_matched": design_ambient_c is not None,
            "min_load_fraction": COP_MIN_LOAD_FRACTION if design_capacity_rt else None,
            "window": [kept[0][0].isoformat(), kept[-1][0].isoformat()],
        },
    }


#: The rule card's ids, mapped to what this module calls the anomaly.
RULE_IDS: dict[str, str] = {
    "nonocc": "nonocc_spike",
    "schedule": "schedule_mismatch",
    "baseload": "baseload_creep",
    "weather": "weather_residual",
    "peak": "peak_excursion",
    "fight": "simultaneous_heating_cooling",
    "cop": "chiller_efficiency",
    "regress": "post_works_regression",
    "dataq": "data_quality",
    "tou": "tou_misalignment",
    # the three that already existed
    "calendar": "weekend_spike",
    "drift": "baseline_drift",
    "spike": "asset_spike",
}
