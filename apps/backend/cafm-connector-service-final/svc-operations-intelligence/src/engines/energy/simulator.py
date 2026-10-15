"""Half-hourly demo feed — one new reading per meter, every 30 minutes.

Feature C only shows what it can do against data that keeps arriving: a dashboard built on
a frozen extract shows the same anomaly for ever, and a detector that has already fired
cannot be seen firing. This appends one half-hour to every meter marked for simulation, so
the register moves on its own.

The values are not noise. Each meter follows an occupancy profile, and a fault is injected
on a rotating schedule so the three detectors get something to find at different times:

  weekday profile        overnight base, ramp from 07:00, decay from 18:00
  weekend profile        out-of-hours trickle
  rotating fault         a different one every few hours, so the feed exercises the
                         weekend-spike, baseline-drift and asset-spike paths in turn
                         rather than producing one permanent anomaly

Only meters whose raw_metadata carries {"simulate": true} are fed. Nothing else is touched,
so a real DCC-backed meter cannot be quietly overwritten by demo data — the two must never
be confusable, because the whole point of the engine is that its findings are real.
"""

from __future__ import annotations

import math
import random
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.logging import get_logger
from ...models.energy import EnergyMeter, MeterReading

log = get_logger(__name__)

HH = timedelta(minutes=30)

# Fault schedule, keyed off the hour of day so a run at any time is reproducible from the
# clock alone rather than from stored state. None means "behave normally", which most of
# the day is — a feed that is always faulty teaches a detector nothing.
_FAULT_BY_HOUR = {
    2: "baseline_drift",
    3: "baseline_drift",
    9: "asset_spike",
    10: "asset_spike",
    11: "asset_spike",
    16: "weekend_spike",   # applied only when it is actually a weekend
    17: "weekend_spike",
}


def _shape(ts: datetime) -> float:
    """Occupancy multiplier for a half-hour."""
    hour = ts.hour + ts.minute / 60.0
    if ts.weekday() >= 5:
        return 0.30 + 0.06 * math.sin(hour / 24 * 2 * math.pi)
    if hour < 6:
        return 0.28
    if hour < 8:
        return 0.28 + (hour - 6) / 2 * 0.72
    if hour < 18:
        return 1.0 + 0.08 * math.sin((hour - 8) / 10 * math.pi)
    if hour < 22:
        return 1.0 - (hour - 18) / 4 * 0.65
    return 0.32


def _fault_multiplier(ts: datetime, *, is_sub_meter: bool) -> tuple[float, str | None]:
    fault = _FAULT_BY_HOUR.get(ts.hour)
    if fault == "weekend_spike":
        if ts.weekday() < 5:
            return 1.0, None
        return 1.9, "weekend_spike"
    if fault == "baseline_drift":
        return 1.18, "baseline_drift"
    if fault == "asset_spike":
        # A sub-meter fault. Applying it to the site meter would be a different anomaly
        # wearing the same label.
        if not is_sub_meter:
            return 1.0, None
        return 1.45, "asset_spike"
    return 1.0, None


async def simulate_half_hour(
    session: AsyncSession,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Append the half-hour ending now to every meter flagged for simulation."""
    now = (now or datetime.now(timezone.utc)).replace(second=0, microsecond=0)
    slot = now.replace(minute=0 if now.minute < 30 else 30)

    meters = (
        await session.execute(
            select(EnergyMeter).where(
                text("coalesce(raw_metadata->>'simulate','false') = 'true'")
            )
        )
    ).scalars().all()
    if not meters:
        return {"ok": True, "meters": 0, "inserted": 0, "note": "no meters flagged simulate"}

    inserted, faults = 0, {}
    for m in meters:
        base = float((m.raw_metadata or {}).get("sim_base_kwh") or 40.0)
        mult, fault = _fault_multiplier(slot, is_sub_meter=bool(m.is_sub_meter))
        # Seeded per meter and slot so a re-run for the same half-hour produces the same
        # number — a demo feed that changes its own history is worse than no feed.
        rng = random.Random(f"{m.id}|{slot.isoformat()}")
        kwh = base * _shape(slot) * mult * rng.uniform(0.97, 1.03)

        existing = (
            await session.execute(
                select(MeterReading).where(
                    MeterReading.meter_id == m.id,
                    MeterReading.reading_at == slot,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            existing.consumption_kwh = Decimal(f"{kwh:.4f}")
        else:
            session.add(
                MeterReading(
                    meter_id=m.id,
                    asset_id=m.asset_id,
                    organization_id=m.organization_id,
                    reading_at=slot,
                    consumption_kwh=Decimal(f"{kwh:.4f}"),
                    source="simulator",
                    quality_flag="ok",
                )
            )
            inserted += 1
        if fault:
            faults[fault] = faults.get(fault, 0) + 1

    await session.commit()
    log.info(
        "energy.simulator.tick",
        slot=slot.isoformat(),
        meters=len(meters),
        inserted=inserted,
        faults=faults,
    )
    return {
        "ok": True,
        "slot": slot.isoformat(),
        "meters": len(meters),
        "inserted": inserted,
        "faults_injected": faults,
    }


async def backfill_range(
    session: AsyncSession,
    *,
    start: datetime,
    end: datetime,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Fill every half-hour between ``start`` and ``end`` that a flagged meter is missing.

    ``simulate_half_hour`` appends the current half-hour and, if that slot already exists,
    replaces its value. That is right for a live demo feed catching up after a restart and
    wrong for filling a month: hoistra_test holds real DCC reads for the first eleven days
    of September, and a range-fill that updated existing slots would quietly replace eleven
    days of real data with invented numbers. So this skips any slot a meter already has,
    unless a caller explicitly asks otherwise.

    Every row it writes carries ``source='simulator'``, the same as the live feed — a
    simulated reading and a metered one must never be confusable, whichever path wrote it.
    """
    start = start.replace(second=0, microsecond=0)
    start = start.replace(minute=0 if start.minute < 30 else 30)
    meters = (
        await session.execute(
            select(EnergyMeter).where(
                text("coalesce(raw_metadata->>'simulate','false') = 'true'")
            )
        )
    ).scalars().all()
    if not meters:
        return {"ok": True, "meters": 0, "inserted": 0, "note": "no meters flagged simulate"}

    # One query per meter for what it already has, rather than one per slot: a month at
    # half-hourly is ~1,440 slots, and asking the database that many times per meter turns a
    # few seconds into several minutes.
    have: dict[Any, set[datetime]] = {}
    for m in meters:
        rows = (await session.execute(
            select(MeterReading.reading_at).where(
                MeterReading.meter_id == m.id,
                MeterReading.reading_at >= start,
                MeterReading.reading_at <= end,
            )
        )).all()
        # Compared as instants, not as wall-clock strings: reading_at is timestamptz, and a
        # naive value is read as the CLIENT's local time. Stripping the zone put a UTC slot
        # into the database four hours early — so the existence check looked at one instant
        # and the insert wrote another, and the rows that came back carried the load shape
        # of a completely different hour of the day.
        have[m.id] = {r[0].astimezone(timezone.utc) if r[0].tzinfo else r[0].replace(tzinfo=timezone.utc)
                      for r in rows}

    inserted, skipped, faults = 0, 0, {}
    for m in meters:
        base = float((m.raw_metadata or {}).get("sim_base_kwh") or 40.0)
        slot = start
        while slot <= end:
            if slot in have[m.id] and not overwrite:
                skipped += 1
                slot += HH
                continue
            mult, fault = _fault_multiplier(slot, is_sub_meter=bool(m.is_sub_meter))
            rng = random.Random(f"{m.id}|{slot.isoformat()}")
            kwh = base * _shape(slot) * mult * rng.uniform(0.97, 1.03)
            session.add(
                MeterReading(
                    meter_id=m.id,
                    asset_id=m.asset_id,
                    organization_id=m.organization_id,
                    reading_at=slot,
                    consumption_kwh=Decimal(f"{kwh:.4f}"),
                    source="simulator",
                    quality_flag="ok",
                )
            )
            inserted += 1
            if fault:
                faults[fault] = faults.get(fault, 0) + 1
            slot += HH
        await session.flush()
    await session.commit()
    log.info("energy.simulator.backfill", start=start.isoformat(), end=end.isoformat(),
             meters=len(meters), inserted=inserted, skipped=skipped, faults=faults)
    return {
        "ok": True,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "meters": len(meters),
        "inserted": inserted,
        "skipped_existing": skipped,
        "faults_injected": faults,
    }
