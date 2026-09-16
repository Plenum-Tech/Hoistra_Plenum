"""Feature C — anomaly detection: the thirteen rules on the energy page's card.

Three live here (weekend spike, baseline drift, asset spike); the other ten are in
detectors.py and are bound to their inputs in scan_meter_anomalies."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from statistics import median
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Text as SAText, cast as sa_cast, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.logging import get_logger
from ...models.energy import EnergyAnomaly, EnergyMeter, MeterReading
from ...shared.approvals import enqueue_approval, write_audit
from . import detectors as D

log = get_logger(__name__)

# An anomaly in one of these has been dealt with; a fresh detection of the same conditions
# is a NEW event and should be raised again. Every other status means the event is still
# live and re-detecting it must not create a second row.
_SETTLED_STATUSES = ("resolved", "closed", "dismissed")

WEEKEND_SPIKE_PCT = 130.0
BASELINE_DRIFT_PCT = 110.0
ASSET_SPIKE_PCT = 120.0
PERSIST_HOURS = 48


def occupancy_profile(
    readings: list[tuple[datetime, float]],
) -> dict[tuple[bool, int], float]:
    """Typical consumption per (weekend?, hour-of-day), as a median over the window.

    A building's load is bimodal: an overnight base and a working-hours peak, with weekends
    at base all day. Comparing any window against a single flat figure — a median or a mean
    over the whole period — compares two different things whenever the window's mix of hours
    differs from the period's. That is the root of both defects this replaces: a 48-hour
    weekend reads as a collapse against a weekday-inclusive average, and a 48-hour weekday
    pair reads as a spike against a median that sits at the overnight base.

    Keyed on (is_weekend, hour) rather than (weekday, half-hour) so each bucket keeps enough
    samples to have a stable median over 30 days — roughly 44 for a weekday hour, 16 for a
    weekend one.
    """
    buckets: dict[tuple[bool, int], list[float]] = {}
    for t, k in readings:
        at = _aware(t)
        buckets.setdefault((is_weekend(at), at.hour), []).append(k)
    return {key: median(vals) for key, vals in buckets.items() if vals}


def expected_for(
    profile: dict[tuple[bool, int], float],
    stamps: list[datetime],
    *,
    fallback: float,
) -> float:
    """What the profile says this set of half-hours should have consumed, in total.

    ``fallback`` covers an hour the profile never saw (a short window, or a bank holiday
    pattern absent from the baseline period), so a missing bucket never silently scores a
    period as zero-expected.
    """
    return sum(profile.get((is_weekend(_aware(t)), _aware(t).hour), fallback) for t in stamps)


def _aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def is_weekend(dt: datetime) -> bool:
    return _aware(dt).weekday() >= 5


#: The annualised figure at or above which an anomaly is raised as high severity, per
#: currency. One number cannot serve four: £500 and AED 500 are not the same amount of
#: money, and comparing a dirham figure against a sterling threshold made every AE anomaly
#: read three times more urgent than the identical UK one. Roughly equivalent sums, not
#: exchange rates — the point is that "worth escalating" means the same thing everywhere.
HIGH_SEVERITY_AT: dict[str, float] = {"GBP": 500.0, "USD": 650.0, "AED": 2400.0, "SGD": 850.0}


def severity_for(amount: float | None, currency: str | None) -> str:
    """high / medium, against the threshold for the currency the figure is in.

    An unpriced anomaly is not automatically minor: the figure is unknown, not small. It
    takes the default rather than being sorted to the bottom of the queue by a zero it
    never had.
    """
    if amount is None:
        return "medium"
    return "high" if amount >= HIGH_SEVERITY_AT.get(
        str(currency or D.DEFAULT_CURRENCY).upper(), HIGH_SEVERITY_AT["GBP"]) else "medium"


def money_phrase(amount: float | None, currency: str | None, impact: dict[str, Any] | None) -> str:
    """How a figure reads in a queue summary — money where there is money, the rule's own
    measure where there is not. Never "£0.00" for something nobody has priced."""
    if amount is not None:
        return f"est {str(currency or D.DEFAULT_CURRENCY).upper()} {amount}/yr excess"
    if impact and impact.get("value") is not None:
        return f"{impact['value']} {impact.get('unit') or ''}".strip() + " (not priceable yet)"
    return "impact not quantified"


async def currency_for_building(session: AsyncSession, building_id: UUID | None) -> str:
    """The currency this building's tariff is quoted in, from the country it sits in."""
    if not building_id:
        return D.DEFAULT_CURRENCY
    try:
        async with session.begin_nested():
            # Off the site, not the building: a building inherits its address from the site
            # it stands on, and plenum_cafm.buildings has no country column here at all.
            country = (await session.execute(text("""
                SELECT s.country_code
                  FROM plenum_cafm.buildings b
                  LEFT JOIN plenum_cafm.sites s
                    ON s.site_id = b.site_id OR s.id = b.site_id
                 WHERE b.building_id = CAST(:b AS uuid)
                 LIMIT 1
            """), {"b": str(building_id)})).scalar()
    except Exception as exc:  # noqa: BLE001 — a missing column must not stop a scan
        log.warning("energy.anomaly.currency_lookup_failed", error=str(exc)[:200])
        return D.DEFAULT_CURRENCY
    return D.currency_for(country)


def _dec(value: Any) -> Decimal | None:
    """A number for the database, or NULL where the rule had no number to give."""
    return None if value is None else Decimal(str(value))


def financial_translation(
    *,
    excess_kwh: float | None,
    annualised_frequency: float,
    tariff: float,
    currency: str | None = None,
) -> dict[str, Any]:
    """excess kWh × annualised frequency × current tariff.

    Delegates to the shared implementation so the three rules that live in this file and the
    ten in detectors.py cannot drift apart on what "no figure" means or what currency a
    figure is in. ``excess_kwh=None`` yields no money at all rather than zero.
    """
    return D._finance(excess_kwh, annualised_frequency, tariff, currency)


def detect_weekend_spike(
    readings: list[tuple[datetime, float]],
    *,
    tariff: float,
) -> dict[str, Any] | None:
    """Weekend/non-occupancy spike >130% of 4-week weekend baseline.

    Each weekend in the window is compared against the others, and the worst is reported.

    This used to treat "the weekend" as the last 48 hours of data. Run on a Wednesday —
    which the daily 08:00 scan is, five days in seven — those 48 hours contain no weekend
    at all, the candidate set came back empty, and the detector returned None having
    examined nothing. Plant left running over Saturday was invisible by Tuesday.
    """
    if not readings:
        return None
    now = max(_aware(t) for t, _ in readings)
    four_weeks = now - timedelta(days=28)
    weekend_pts = [
        (_aware(t), k) for t, k in readings if is_weekend(t) and _aware(t) >= four_weeks
    ]
    if len(weekend_pts) < 8:
        return None

    # Bucket by the Saturday each reading belongs to, so a Sat/Sun pair is one weekend.
    buckets: dict[Any, list[float]] = {}
    for t, k in weekend_pts:
        saturday = (t - timedelta(days=t.weekday() - 5)).date()
        buckets.setdefault(saturday, []).append(k)
    if len(buckets) < 2:
        return None

    best: tuple[float, Any, list[float], float] | None = None
    for sat, vals in buckets.items():
        others = [v for s, vs in buckets.items() if s != sat for v in vs]
        if not others or len(vals) < 4:
            continue
        cand_avg = sum(vals) / len(vals)
        other_avg = sum(others) / len(others)
        if other_avg <= 0:
            continue
        p = 100.0 * cand_avg / other_avg
        if p > WEEKEND_SPIKE_PCT and (best is None or p > best[0]):
            best = (p, sat, vals, other_avg)
    if best is None:
        return None
    pct, _sat, recent_weekend, base_avg = best
    recent_avg = sum(recent_weekend) / len(recent_weekend)
    excess = (recent_avg - base_avg) * len(recent_weekend)
    # ~52 weekends/year
    fin = financial_translation(excess_kwh=excess, annualised_frequency=52 / max(1, len(recent_weekend) / 48), tariff=tariff)
    return {
        "anomaly_type": "weekend_spike",
        "metric_pct": round(pct, 2),
        **fin,
        "detail": {"recent_avg_kwh": recent_avg, "baseline_avg_kwh": base_avg},
    }


def detect_baseline_drift(
    readings: list[tuple[datetime, float]],
    *,
    tariff: float,
) -> dict[str, Any] | None:
    """7-day rolling avg >110% of prior period, persisting ≥48 hours."""
    if len(readings) < 48:
        return None
    sorted_r = sorted(((_aware(t), k) for t, k in readings), key=lambda x: x[0])
    end = sorted_r[-1][0]
    cur_start = end - timedelta(days=7)
    prior_start = cur_start - timedelta(days=7)
    cur = [k for t, k in sorted_r if cur_start <= t <= end]
    prior = [k for t, k in sorted_r if prior_start <= t < cur_start]
    if not cur or not prior:
        return None
    cur_avg = sum(cur) / len(cur)
    prior_avg = sum(prior) / len(prior)
    if prior_avg <= 0:
        return None
    pct = 100.0 * cur_avg / prior_avg
    if pct <= BASELINE_DRIFT_PCT:
        return None
    # Persist ≥48h: the last 48 hours must ALSO be above threshold — but measured against
    # what the prior week did in those same hours, not against its flat average.
    #
    # The flat comparison could not fire when the window ended on a weekend. Saturday and
    # Sunday in an unoccupied building run at roughly two thirds of a week that contains
    # five working days, so the persistence check discarded the drift however large it was.
    # That is two days in seven, and it includes every Monday-morning scan.
    prior_pts = [(t, k) for t, k in sorted_r if prior_start <= t < cur_start]
    last_48_pts = [(t, k) for t, k in sorted_r if t >= end - timedelta(hours=PERSIST_HOURS)]
    if not last_48_pts or not prior_pts:
        return None
    profile = occupancy_profile(prior_pts)
    expected_48 = expected_for(
        profile, [t for t, _ in last_48_pts], fallback=prior_avg
    )
    actual_48 = sum(k for _, k in last_48_pts)
    if expected_48 <= 0 or 100.0 * actual_48 / expected_48 <= BASELINE_DRIFT_PCT:
        return None
    excess = (cur_avg - prior_avg) * len(cur)
    fin = financial_translation(
        excess_kwh=excess,
        annualised_frequency=365 / 7,
        tariff=tariff,
    )
    return {
        "anomaly_type": "baseline_drift",
        "metric_pct": round(pct, 2),
        **fin,
        "detail": {
            "cur_7d_avg": cur_avg,
            "prior_7d_avg": prior_avg,
            "persisted_hours": PERSIST_HOURS,
            "persistence_pct": round(100.0 * actual_48 / expected_48, 2),
        },
    }


def detect_asset_spike(
    readings: list[tuple[datetime, float]],
    *,
    tariff: float,
) -> dict[str, Any] | None:
    """Sub-meter >120% of 30-day median, sustained for ≥48 hours.

    Two things this used to get wrong, both of which made it unable to fire on ordinary
    building plant.

    It required EVERY half-hour in the window to exceed the threshold. Anything with a
    day/night profile — which is most plant — sits below its own 30-day median overnight by
    construction, so a single 03:00 reading vetoed a spike that ran for three days. The
    window mean is what "sustained above" means; individual periods dipping is the normal
    shape of the load, not evidence against the spike.

    And it only ever looked at the 48 hours ending at the latest reading, so a spike that
    started and finished last week was invisible. A scan run on Monday could not see
    Thursday's fault. It now scans every 48-hour window and reports the worst.
    """
    if len(readings) < 48:
        return None
    sorted_r = sorted(((_aware(t), k) for t, k in readings), key=lambda x: x[0])
    end = sorted_r[-1][0]
    recent = [(t, k) for t, k in sorted_r if t >= end - timedelta(days=30)]
    if len(recent) < 10:
        return None

    # Compared against what this meter normally does in these hours, not against a flat
    # 30-day median. Plant load is bimodal — more than half of every day sits at the
    # overnight base — so the median sits at that base, and any 48-hour window containing
    # two working days cleared 120% of it by construction. That reported 189% on a
    # sub-meter with nothing wrong at all, which makes the detector unable to distinguish
    # a fault from a Tuesday.
    #
    # The profile is drawn from the same 30 days the spike sits in, so a long enough fault
    # raises its own baseline. A median tolerates a few days of it; a fault running for
    # most of a month is a level change, which is what baseline_drift is for.
    profile = occupancy_profile(recent)
    overall = median([k for _, k in recent])
    if overall <= 0:
        return None

    best: tuple[float, list[tuple[datetime, float]], float, float] | None = None
    for i, (t0, _) in enumerate(recent):
        block = [
            (t, k) for t, k in recent[i:] if t < t0 + timedelta(hours=PERSIST_HOURS)
        ]
        # A window only counts if it actually spans the persistence period; a trailing
        # partial window would let a few hours pass as two days.
        if len(block) < 4 or block[-1][0] - block[0][0] < timedelta(
            hours=PERSIST_HOURS - 1
        ):
            continue
        expected = expected_for(profile, [t for t, _ in block], fallback=overall)
        if expected <= 0:
            continue
        actual = sum(k for _, k in block)
        pct_block = 100.0 * actual / expected
        if pct_block > ASSET_SPIKE_PCT and (best is None or pct_block > best[0]):
            best = (pct_block, block, actual, expected)
    if best is None:
        return None
    pct, last_48, actual_kwh, expected_kwh = best
    excess = max(0.0, actual_kwh - expected_kwh)
    fin = financial_translation(
        excess_kwh=excess,
        annualised_frequency=365 * 24 / PERSIST_HOURS,
        tariff=tariff,
    )
    return {
        "anomaly_type": "asset_spike",
        "metric_pct": round(pct, 2),
        **fin,
        "detail": {
            "expected_kwh_48h": round(expected_kwh, 3),
            "actual_kwh_48h": round(actual_kwh, 3),
            "window_start": last_48[0][0].isoformat(),
            "window_end": last_48[-1][0].isoformat(),
        },
    }


async def _load_readings(
    session: AsyncSession,
    meter_id: UUID,
    *,
    days: int = 35,
) -> list[tuple[datetime, float]]:
    """Load the most recent `days` of readings that actually exist.

    Windowing from *now* made historical CSV imports (e.g. Dec–Jan loaded in
    August) look empty to the detectors, so Scan reported complete with nothing.
    """
    latest = (
        await session.execute(
            select(func.max(MeterReading.reading_at)).where(MeterReading.meter_id == meter_id)
        )
    ).scalar_one_or_none()
    if latest is None:
        return []
    start = _aware(latest) - timedelta(days=days)
    rows = list(
        (
            await session.execute(
                select(MeterReading.reading_at, MeterReading.consumption_kwh).where(
                    MeterReading.meter_id == meter_id,
                    MeterReading.reading_at >= start,
                )
            )
        ).all()
    )
    return [(r[0], float(r[1])) for r in rows]


def _occupancy_from(meta: dict[str, Any] | None) -> D.OccupancyHours:
    """The meter's occupancy calendar from raw_metadata.occupancy_hours, else the default."""
    oh = (meta or {}).get("occupancy_hours") or {}
    try:
        return D.OccupancyHours(
            start_hour=int(oh.get("start_hour", D.DEFAULT_OCCUPANCY.start_hour)),
            end_hour=int(oh.get("end_hour", D.DEFAULT_OCCUPANCY.end_hour)),
            weekdays_only=bool(oh.get("weekdays_only", True)),
        )
    except (TypeError, ValueError):
        return D.DEFAULT_OCCUPANCY


def _bands_from(meta: dict[str, Any] | None) -> tuple[list[D.TariffBand], float | None]:
    """Tariff bands from raw_metadata.tariff_bands [{name,start_hour,end_hour,weekdays_only,rate}]
    and raw_metadata.offpeak_rate; the UK red band when the meter has none."""
    raw = (meta or {}).get("tariff_bands")
    bands: list[D.TariffBand] = []
    for b in raw or []:
        try:
            bands.append(D.TariffBand(str(b.get("name") or "peak"), int(b["start_hour"]), int(b["end_hour"]),
                                      bool(b.get("weekdays_only", True)),
                                      float(b["rate"]) if b.get("rate") is not None else None))
        except (KeyError, TypeError, ValueError):
            continue
    off = (meta or {}).get("offpeak_rate")
    return (bands or list(D.DEFAULT_PEAK_BANDS)), (float(off) if off is not None else None)


async def _prior_year_max_kw(session: AsyncSession, meter_id: UUID, latest: datetime) -> float | None:
    """The highest interval demand in the year before the current window — the peak rule's
    fallback limit when no agreed capacity is on the meter."""
    row = (await session.execute(
        select(func.max(MeterReading.consumption_kwh), func.min(MeterReading.period_minutes)).where(
            MeterReading.meter_id == meter_id,
            MeterReading.reading_at < latest - timedelta(days=7),
            MeterReading.reading_at >= latest - timedelta(days=372),
        )
    )).first()
    if not row or row[0] is None:
        return None
    minutes = int(row[1] or 30)
    return float(row[0]) * 60.0 / max(1, minutes)


async def _quality_flags(session: AsyncSession, meter_id: UUID, since: datetime) -> list[tuple[datetime, str | None]]:
    rows = (await session.execute(
        select(MeterReading.reading_at, MeterReading.quality_flag).where(
            MeterReading.meter_id == meter_id, MeterReading.reading_at >= since))).all()
    return [(r[0], r[1]) for r in rows]


async def _closed_work_orders(session: AsyncSession, meter: EnergyMeter, since: datetime) -> list[tuple[datetime, str]]:
    """Work orders closed on the meter's asset (a sub-meter) or building in the horizon."""
    from sqlalchemy import text as _text
    where = []
    params: dict[str, Any] = {"since": since}
    if meter.asset_id:
        where.append("asset_id = CAST(:a AS uuid)"); params["a"] = str(meter.asset_id)
    if meter.building_id:
        where.append("building_id = CAST(:b AS uuid)"); params["b"] = str(meter.building_id)
    if not where:
        return []
    try:
        # In a savepoint: on the deployment where completed_at is a plain timestamp and
        # closed_at a timestamptz, the bare coalesce raised — and because the error was
        # caught here, the session was left aborted and every later query in the scan
        # (weather, BMS trends, the write itself) failed with "transaction is aborted".
        # The savepoint releases cleanly either way; the casts make both columns one type.
        async with session.begin_nested():
            rows = (await session.execute(_text(f"""
                SELECT coalesce(closed_at, completed_at::timestamptz) AS closed,
                       coalesce(wo_code, workorder_ref, id::text)
                  FROM plenum_cafm.work_orders
                 WHERE ({' OR '.join(where)}) AND coalesce(closed_at, completed_at::timestamptz) >= :since
                 ORDER BY 1 DESC LIMIT 20"""), params)).all()
    except Exception as exc:  # noqa: BLE001 — a missing column must not stop the scan
        log.warning("energy.anomaly.work_orders_unavailable", error=str(exc)[:160])
        return []
    return [(r[0], str(r[1])) for r in rows if r[0] is not None]


async def _monthly_and_degree_days(session: AsyncSession, meter: EnergyMeter):
    """Monthly kWh for the meter and the building's degree days, for the weather rule."""
    if not meter.building_id:
        return [], []
    from sqlalchemy import text as _text
    months = (await session.execute(_text("""
        SELECT date_trunc('month', reading_at)::date AS m, sum(consumption_kwh)
          FROM plenum_cafm.meter_readings WHERE meter_id = CAST(:m AS uuid)
         GROUP BY 1 ORDER BY 1"""), {"m": str(meter.id)})).all()
    try:
        dd = (await session.execute(_text("""
            SELECT month, hdd, cdd FROM plenum_cafm.weather_degree_days
             WHERE building_id = CAST(:b AS uuid) ORDER BY month"""), {"b": str(meter.building_id)})).all()
    except Exception:  # noqa: BLE001 — table absent on an old database
        dd = []
    # a partial current month would read as a drop; only complete months are compared
    if months:
        months = months[:-1]
    return ([(r[0], float(r[1])) for r in months], [(r[0], float(r[1]), float(r[2])) for r in dd])


async def _bms_trends(session: AsyncSession, meter: EnergyMeter, since: datetime):
    if not meter.building_id:
        return []
    from sqlalchemy import text as _text
    try:
        rows = (await session.execute(_text("""
            SELECT recorded_at, zone, coalesce(heating_pct, 0), coalesce(cooling_pct, 0)
              FROM plenum_cafm.bms_trends
             WHERE building_id = CAST(:b AS uuid) AND recorded_at >= :since
             ORDER BY zone, recorded_at"""), {"b": str(meter.building_id), "since": since})).all()
    except Exception:  # noqa: BLE001
        return []
    return [(r[0], r[1], float(r[2]), float(r[3])) for r in rows]


async def scan_meter_anomalies(
    session: AsyncSession,
    *,
    meter_id: UUID,
    organization_id: UUID | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    """Run every rule this meter's data can arm.

    ``persist=False`` is the same run with nothing written: the hits come back in
    ``anomalies`` (marked ``dry_run``) and nothing is opened, re-stamped or queued — what the
    detection-coverage report uses to say which rules fire where, without side effects.
    """
    meter = await session.get(EnergyMeter, meter_id)
    if not meter:
        return {"ok": False, "error": "meter_not_found"}
    tariff = float(meter.tariff_gbp_per_kwh or 0.28)
    currency = await currency_for_building(session, meter.building_id)
    readings = await _load_readings(session, meter_id)
    detectors = [detect_weekend_spike, detect_baseline_drift]
    if meter.is_sub_meter:
        detectors.append(detect_asset_spike)

    # The ten rules from the card. Each is bound to its inputs here so the loop below can
    # call every detector the same way; a rule whose input this meter does not have is
    # listed under "skipped" rather than run on nothing.
    meta = meter.raw_metadata or {}
    skipped: dict[str, str] = {}
    if not meter.is_sub_meter:
        # Thirteen rules exist; a rule that cannot run on this meter says so rather than
        # disappearing, so "rules_run" plus "skipped" always accounts for all of them.
        skipped["asset_spike"] = "this meter is not a sub-meter, so no single asset is isolated"

    if readings:
        latest = _aware(max(t for t, _ in readings))
        occupancy = _occupancy_from(meta)
        bands, offpeak = _bands_from(meta)
        capacity = meta.get("capacity_kw") or meta.get("agreed_capacity_kva")
        prior_max = None if capacity else await _prior_year_max_kw(session, meter_id, latest)
        flags = await _quality_flags(session, meter_id, latest - timedelta(days=35))
        work_orders = await _closed_work_orders(session, meter, latest - timedelta(days=D.REGRESS_HORIZON_DAYS + 7))
        monthly, degree_days = await _monthly_and_degree_days(session, meter)
        trends = await _bms_trends(session, meter, latest - timedelta(days=7))

        detectors += [
            lambda r, *, tariff: D.detect_nonocc_spike(r, tariff=tariff, occupancy=occupancy),
            lambda r, *, tariff: D.detect_schedule_mismatch(r, tariff=tariff, occupancy=occupancy),
            lambda r, *, tariff: D.detect_baseload_creep(r, tariff=tariff),
            lambda r, *, tariff: D.detect_data_quality(r, tariff=tariff, flags=flags),
            lambda r, *, tariff: D.detect_tou_misalignment(r, tariff=tariff, bands=bands,
                                                            offpeak_rate=offpeak, occupancy=occupancy),
        ]
        if capacity or prior_max:
            detectors.append(lambda r, *, tariff: D.detect_peak_excursion(
                r, tariff=tariff, capacity_kw=float(capacity) if capacity else None, prior_year_max_kw=prior_max))
        else:
            skipped["peak_excursion"] = "no capacity_kw on the meter and no prior-year readings"
        if work_orders:
            detectors.append(lambda r, *, tariff: D.detect_post_works_regression(
                r, tariff=tariff, closed_work_orders=work_orders))
        else:
            skipped["post_works_regression"] = "no work order closed on this asset or building in the last 37 days"
        if degree_days and len(monthly) >= D.WEATHER_MIN_MONTHS:
            detectors.append(lambda r, *, tariff: D.detect_weather_residual(monthly, degree_days, tariff=tariff))
        else:
            skipped["weather_residual"] = ("no degree days for the building" if not degree_days
                                           else f"{len(monthly)} complete months; needs {D.WEATHER_MIN_MONTHS}")
        if trends:
            zone_kw = meta.get("zone_kw")
            detectors.append(lambda r, *, tariff: D.detect_simultaneous_heating_cooling(
                trends, tariff=tariff, zone_kw=float(zone_kw) if zone_kw else None))
        else:
            skipped["simultaneous_heating_cooling"] = "no BMS trends for the building in the last 7 days"
        skipped["chiller_efficiency"] = "runs per chiller asset: POST /api/energy/chillers/scan"

    # PRD: baseline drift only when no logged occupancy change in the window
    from .occupancy import has_occupancy_change

    end_now = datetime.now(timezone.utc)
    occupancy_changed = await has_occupancy_change(
        session,
        building_id=meter.building_id,
        window_start=end_now - timedelta(days=14),
        window_end=end_now,
    )

    created: list[dict[str, Any]] = []
    for det in detectors:
        if det is detect_baseline_drift and occupancy_changed:
            log.info(
                "energy.anomaly.baseline_suppressed_occupancy",
                meter_id=str(meter_id),
                building_id=str(meter.building_id) if meter.building_id else None,
            )
            continue
        hit = det(readings, tariff=tariff)
        if not hit:
            continue

        # A rule is a pure function over readings and does not know where the building is;
        # the currency is a property of the building, so it is stamped here rather than
        # threaded through thirteen signatures. Without it every figure was labelled GBP,
        # including the ones a Dubai meter priced in dirhams.
        hit["currency"] = hit.get("currency") or currency
        # And every row leads with a named measure. Most rules mean annualised cost by that;
        # one that could not be priced says what it did measure instead of showing a blank.
        hit.setdefault("impact", D.money_impact(hit))

        if not persist:
            created.append({"anomaly_type": hit["anomaly_type"], "metric_pct": hit["metric_pct"],
                            "financial_gbp": hit.get("financial_gbp"), "currency": hit["currency"],
                            "impact": hit.get("impact"), "dry_run": True})
            continue

        # A spike stays in the readings, so every later scan detects it again. Without this
        # guard each run wrote another anomaly AND another approval for the same event: one
        # weekend appeared five times, and the August report priced it five times over.
        # Re-detecting a live event is not news — note that we saw it again and move on.
        existing = (
            await session.execute(
                select(EnergyAnomaly)
                .where(
                    EnergyAnomaly.meter_id == meter.id,
                    EnergyAnomaly.anomaly_type == hit["anomaly_type"],
                    EnergyAnomaly.metric_pct == Decimal(str(hit["metric_pct"])),
                    EnergyAnomaly.status.notin_(_SETTLED_STATUSES),
                )
                .order_by(EnergyAnomaly.detected_at.desc())
                .limit(1)
            )
        ).scalars().first()
        if existing is not None:
            existing.window_end = datetime.now(timezone.utc)
            log.info(
                "energy.anomaly.already_open",
                meter_id=str(meter_id),
                anomaly_type=hit["anomaly_type"],
                anomaly_id=str(existing.id),
            )
            continue

        row = EnergyAnomaly(
            id=uuid4(),
            organization_id=organization_id or meter.organization_id,
            building_id=meter.building_id,
            meter_id=meter.id,
            asset_id=meter.asset_id,
            anomaly_type=hit["anomaly_type"],
            window_end=datetime.now(timezone.utc),
            metric_pct=Decimal(str(hit["metric_pct"])),
            # NULL where the rule could not price the firing. The column is nullable and
            # NULL is the honest answer; 0 would say "this costs nothing", which is a
            # different claim and the one the data-quality rule alone gets to make.
            excess_kwh=_dec(hit.get("excess_kwh")),
            annualised_excess_kwh=_dec(hit.get("annualised_excess_kwh")),
            financial_gbp=_dec(hit.get("financial_gbp")),
            currency=hit.get("currency") or currency,
            tariff_used=Decimal(str(tariff)),
            detail_json={**(hit.get("detail") or {}), "impact": hit.get("impact")},
            status="open",
        )
        session.add(row)
        await session.flush()
        item = await enqueue_approval(
            session,
            source_feature="C",
            item_type=f"energy_anomaly_{hit['anomaly_type']}",
            summary=(
                f"Energy anomaly {hit['anomaly_type']} on meter {meter_id}: "
                f"{hit['metric_pct']}% · "
                f"{money_phrase(hit.get('financial_gbp'), hit.get('currency'), hit.get('impact'))}. "
                f"Actions: Acknowledge / Monitor / Mark expected. No WO created."
            ),
            severity=severity_for(hit.get("financial_gbp"), hit.get("currency")),
            payload={
                "anomaly_id": str(row.id),
                "anomaly_type": hit["anomaly_type"],
                "financial_gbp": hit.get("financial_gbp"),
                "currency": hit.get("currency"),
                "impact": hit.get("impact"),
                "actions": ["acknowledge", "monitor", "mark_expected"],
                "creates_work_order": False,
                "status_for_future_wo_engine": "open",
            },
            organization_id=organization_id or meter.organization_id,
            related_entity_type="energy_anomaly",
            related_entity_id=row.id,
        )
        row.queue_item_id = item.id
        created.append(
            {
                "id": str(row.id),
                "anomaly_type": hit["anomaly_type"],
                "metric_pct": hit["metric_pct"],
                "financial_gbp": hit.get("financial_gbp"),
                "currency": hit.get("currency"),
                "impact": hit.get("impact"),
                "queue_item_id": str(item.id),
            }
        )

    await write_audit(
        session,
        actor="system",
        action_type="energy.anomaly.scan",
        source_feature="C",
        organization_id=organization_id or meter.organization_id,
        detail={"meter_id": str(meter_id), "created": len(created)},
    )
    if persist:
        await session.commit()
    return {"ok": True, "meter_id": str(meter_id), "anomalies": created,
            "rules_run": len(detectors), "skipped": skipped if readings else {"all": "no readings"}}


async def act_on_anomaly(
    session: AsyncSession,
    anomaly_id: UUID,
    *,
    action: str,
    reason: str | None = None,
) -> dict[str, Any]:
    """Acknowledge / Monitor / Mark expected (with reason)."""
    action_l = action.lower().replace(" ", "_")
    mapping = {
        "acknowledge": ("acknowledged", "acknowledge"),
        "acknowledged": ("acknowledged", "acknowledge"),
        "monitor": ("monitoring", "monitor"),
        "monitoring": ("monitoring", "monitor"),
        "mark_expected": ("expected", "mark_expected"),
        "expected": ("expected", "mark_expected"),
    }
    if action_l not in mapping:
        return {"ok": False, "error": "action must be acknowledge | monitor | mark_expected"}
    if action_l in {"mark_expected", "expected"} and not (reason or "").strip():
        return {"ok": False, "error": "reason_required_for_mark_expected"}

    row = await session.get(EnergyAnomaly, anomaly_id)
    if not row:
        return {"ok": False, "error": "not_found"}
    status, pm_action = mapping[action_l]
    row.status = status
    row.pm_action = pm_action
    row.pm_reason = reason
    row.acted_at = datetime.now(timezone.utc)
    row.updated_at = datetime.now(timezone.utc)
    await write_audit(
        session,
        actor="pm",
        action_type=f"energy.anomaly.{pm_action}",
        source_feature="C",
        organization_id=row.organization_id,
        input_payload={"anomaly_id": str(anomaly_id), "reason": reason},
    )
    await session.commit()
    return {
        "ok": True,
        "anomaly_id": str(anomaly_id),
        "status": row.status,
        "pm_action": row.pm_action,
        "note": "Stored for future Work Order Engine consumption; no WO created in this phase.",
    }


async def scan_all_active_meters(
    session: AsyncSession,
    *,
    organization_id: UUID | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    """Daily cron — scan active meters for weekend/baseline/asset anomalies."""
    q = select(EnergyMeter).where(EnergyMeter.active.is_(True)).limit(limit)
    if organization_id:
        q = q.where(EnergyMeter.organization_id == organization_id)
    meters = list((await session.execute(q)).scalars().all())
    results = []
    for m in meters:
        try:
            # scan_meter_anomalies commits; use nested pattern carefully
            r = await scan_meter_anomalies(
                session, meter_id=m.id, organization_id=organization_id or m.organization_id
            )
            results.append({"meter_id": str(m.id), "ok": r.get("ok"), "count": len(r.get("anomalies") or [])})
        except Exception as exc:  # noqa: BLE001
            log.warning("energy.anomaly.scan_meter_failed", meter_id=str(m.id), error=str(exc)[:200])
            results.append({"meter_id": str(m.id), "ok": False, "error": str(exc)[:200]})
    return {"ok": True, "meters_scanned": len(meters), "results": results}


async def list_anomalies(
    session: AsyncSession,
    *,
    status: str | None = "open",
    organization_id: UUID | None = None,
    limit: int = 100,
    building_id: UUID | str | None = None,
    asset_id: UUID | str | None = None,
    meter_id: UUID | str | None = None,
    anomaly_type: str | None = None,
    min_cost: float | None = None,
    min_days_active: int | None = None,
    order_by: str = "detected_at",
) -> list[dict[str, Any]]:
    """Anomalies, narrowed by any of the dimensions the activity log shows.

    The list used to filter on status alone, so "baseline drift on Town Hall over £5k, active
    more than ten days" could only be answered by pulling every row and discarding most of them
    in the caller — which silently caps at `limit` and reports a subset as if it were the
    answer. Each of these is a column on the row, and belongs in the WHERE clause.

    ``min_days_active`` counts from `window_start` — how long the deviation has persisted, not
    how long anyone has known about it. An anomaly acknowledged on day one is still active on
    day twelve.

    ``order_by`` accepts `detected_at` (newest first) or `financial` (dearest first). Money is
    usually the right order: a 300% spike on a 2 kW circuit sorts above a 20% drift on a chiller
    by percentage, and below it by anything that matters.
    """
    q = select(EnergyAnomaly)
    if status:
        q = q.where(EnergyAnomaly.status == status)
    if organization_id:
        q = q.where(EnergyAnomaly.organization_id == organization_id)
    # Compared as text on both sides: these keys are uuid in one deployment's schema and
    # character varying in the other, and an uncast comparison raises rather than returning
    # nothing — the same mismatch that took /api/assets down.
    if building_id:
        q = q.where(sa_cast(EnergyAnomaly.building_id, SAText) == str(building_id))
    if asset_id:
        q = q.where(sa_cast(EnergyAnomaly.asset_id, SAText) == str(asset_id))
    if meter_id:
        q = q.where(sa_cast(EnergyAnomaly.meter_id, SAText) == str(meter_id))
    if anomaly_type:
        # Matched case-insensitively and on the underscored form, so "Baseline drift" as the
        # UI prints it finds `baseline_drift` as the column holds it.
        wanted = anomaly_type.strip().lower().replace(" ", "_")
        q = q.where(func.lower(EnergyAnomaly.anomaly_type) == wanted)
    if min_cost is not None:
        q = q.where(EnergyAnomaly.financial_gbp >= min_cost)
    if min_days_active is not None:
        # COALESCE, because window_start is NULL on every row in this deployment — only
        # window_end and detected_at are written. Filtering on window_start alone matched
        # nothing at any threshold, including one day, while 154 anomalies sat in the table:
        # an empty list that reads as "none are that old" rather than as "that column is not
        # populated here". detected_at is when the scan first saw it, which is the honest
        # answer to "how long has this been active" when the window's start was never stored.
        cutoff = datetime.now(timezone.utc) - timedelta(days=int(min_days_active))
        q = q.where(func.coalesce(EnergyAnomaly.window_start,
                                  EnergyAnomaly.detected_at) <= cutoff)
    order = (EnergyAnomaly.financial_gbp.desc().nullslast()
             if str(order_by).lower().startswith("financ")
             else EnergyAnomaly.detected_at.desc())
    q = q.order_by(order).limit(limit)
    rows = list((await session.execute(q)).scalars().all())

    # Which of these were detected on a simulated feed. An anomaly reads as a finding about
    # a building; one raised on invented readings is a finding about the simulator, and the
    # money figure beside it is invented too. Asked once for the whole page rather than per
    # row, and never allowed to fail the list — a missing marker is worse than no list only
    # if the list is the thing that breaks.
    simulated: set[str] = set()
    meter_ids = [r.meter_id for r in rows if r.meter_id]
    if meter_ids:
        try:
            async with session.begin_nested():
                found = (await session.execute(text("""
                    SELECT DISTINCT a.id::text
                      FROM plenum_cafm.energy_anomalies a
                      JOIN plenum_cafm.meter_readings r ON r.meter_id = a.meter_id
                     WHERE a.id = ANY(CAST(:ids AS uuid[]))
                       AND r.source = 'simulator'
                       AND r.reading_at <= a.detected_at
                       AND r.reading_at > a.detected_at - interval '30 days'
                       -- What the detector could actually see. A reading stamped inside the
                       -- window but WRITTEN afterwards was not available when the anomaly
                       -- was raised, and marking the anomaly simulated because of it would
                       -- discredit a finding that came off real data.
                       AND r.created_at <= a.detected_at
                """), {"ids": [str(r.id) for r in rows]})).all()
            simulated = {x[0] for x in found}
        except Exception as exc:  # noqa: BLE001
            log.warning("energy.anomaly.simulated_lookup_failed", error=str(exc)[:200])

    return [
        {
            "id": str(r.id),
            "anomaly_type": r.anomaly_type,
            # True means the window this was detected in contains simulated readings.
            "simulated": str(r.id) in simulated,
            "status": r.status,
            "metric_pct": float(r.metric_pct) if r.metric_pct is not None else None,
            # financial_gbp keeps its name for callers that already read it; the column has
            # always held the site's own currency, and `currency` is what says which.
            "financial_gbp": float(r.financial_gbp) if r.financial_gbp is not None else None,
            "financial_amount": float(r.financial_gbp) if r.financial_gbp is not None else None,
            "currency": r.currency or D.DEFAULT_CURRENCY,
            "impact": (r.detail_json or {}).get("impact"),
            "annualised_excess_kwh": float(r.annualised_excess_kwh) if r.annualised_excess_kwh is not None else None,
            "meter_id": str(r.meter_id) if r.meter_id else None,
            "asset_id": str(r.asset_id) if r.asset_id else None,
            "building_id": str(r.building_id) if r.building_id else None,
            # Deprecated alias: the column was called site_id until Sep 2026 and the
            # shell still joins on it. Remove once the frontend reads building_id.
            "site_id": str(r.building_id) if r.building_id else None,
            "detected_at": r.detected_at.isoformat() if r.detected_at else None,
            "pm_action": r.pm_action,
            "pm_reason": r.pm_reason,
        }
        for r in rows
    ]


#: What an anomaly summary may be grouped by. An allow-list, not an f-string of whatever
#: arrived: this becomes a column name in SQL.
SUMMARY_GROUPS: dict[str, str] = {
    "building": "b.name",
    "type": "a.anomaly_type",
    "status": "a.status",
    "asset": "a.asset_id::text",
}


async def summarise_anomalies(
    session: AsyncSession,
    *,
    group_by: str = "building",
    organization_id: UUID | None = None,
    building_ids: list[str] | None = None,
    status: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Counts and totals per building, type, status or asset — in one query.

    "Which building has the most anomalies", "which type dominates", "what is waiting on an
    action" are questions about the WHOLE table. Answering them by listing rows and counting
    them in the caller silently truncates at the page size and reports a subset as the answer.

    Three figures per group, and the difference between them matters:

      count    how many findings
      worst    the largest single finding — the honest headline, same basis as the rollup
      naive_sum   every finding added together. Detectors overlap, so this DOUBLE COUNTS.
                  It is returned because leaving it out invites the caller to compute it,
                  and named so it cannot be mistaken for a total anyone should quote.

    Grouped by currency as well as by the requested dimension, because the estate spans four
    of them. The two dearest buildings here are in AED and the next in GBP; adding those
    produces a number in no currency at all.
    """
    column = SUMMARY_GROUPS.get(str(group_by).lower())
    if column is None:
        raise ValueError(
            f"group_by must be one of {sorted(SUMMARY_GROUPS)}; got {group_by!r}")

    where = ["1=1"]
    params: dict[str, Any] = {"lim": limit}
    if organization_id:
        where.append("a.organization_id = CAST(:org AS uuid)")
        params["org"] = str(organization_id)
    if status:
        where.append("a.status = :st")
        params["st"] = status
    if building_ids is not None:
        if not building_ids:
            where.append("FALSE")
        else:
            where.append("a.building_id::text = ANY(:blds)")
            params["blds"] = [str(b) for b in building_ids]

    sql = f"""
        SELECT COALESCE({column}::text, '(unattributed)') AS label,
               COALESCE(a.currency, 'GBP')                AS currency,
               count(*)                                   AS count,
               max(a.financial_gbp)                       AS worst,
               sum(a.financial_gbp)                       AS naive_sum,
               count(*) FILTER (WHERE a.financial_gbp IS NULL) AS unpriced
          FROM plenum_cafm.energy_anomalies a
          LEFT JOIN plenum_cafm.buildings b ON b.building_id = a.building_id
         WHERE {' AND '.join(where)}
         GROUP BY 1, 2
         ORDER BY max(a.financial_gbp) DESC NULLS LAST
         LIMIT :lim
    """
    rows = (await session.execute(text(sql), params)).mappings().all()
    return {
        "group_by": str(group_by).lower(),
        "groups": [
            {
                "label": r["label"],
                "currency": r["currency"],
                "count": int(r["count"]),
                "worst": float(r["worst"]) if r["worst"] is not None else None,
                "naive_sum": float(r["naive_sum"]) if r["naive_sum"] is not None else None,
                "unpriced": int(r["unpriced"]),
            }
            for r in rows
        ],
        "note": (
            "worst is the largest single finding and is the figure to quote. naive_sum adds "
            "overlapping detectors and double counts; it is not a total. Currencies are not "
            "combined."
        ),
    }
