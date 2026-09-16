"""The hours a building actually keeps, against the hours its benchmark assumes.

A building at 198 kWh/m2/yr against a reference of 172 is 15% over. Part of that is waste and
part is that the reference assumes a shorter day than the building works, and the two take
different answers: one is a work order, the other is a re-benchmark. Nothing could separate them
before, because the assumption was not in the schema and the actual was never derived.

The assumption is stored (see migrations/building_assumed_hours.sql). The actual is computed
here from the load profile every time it is asked for, and never written: a stored operating
profile goes stale the moment the building changes its pattern, and a stale one is worse than
none because it reads as measurement.

How "in use" is decided. Consumption over a day is bimodal — a flat overnight floor and a
working-hours plateau. The floor is the 10th percentile of the hourly medians, the plateau the
90th, and an hour counts as in use when its median sits above the floor by more than
``IN_USE_FRACTION`` of the distance between them. A fraction rather than an absolute threshold,
because the same building in January and July has the same shape at different magnitudes.

What this deliberately does NOT do is call a wide gap waste. A building open longer than its
pack assumed may be correctly benchmarked against the wrong pack — which is a re-benchmark, not
a fault. The engine reports the gap and what it is worth; what to do about it is a judgement it
does not make.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.logging import get_logger

log = get_logger(__name__)

#: How far above the overnight floor an hour must sit, as a fraction of floor-to-plateau, to
#: count as occupied. 0.25 keeps a lightly-used early hour in and leaves the base load out.
IN_USE_FRACTION = 0.25

#: Fewest distinct days needed before a derived profile is reported at all. A fortnight of
#: half-hourly data is enough to see a weekly pattern; less is one atypical week presented as a
#: habit, and answering "the building opens at 07:00" off three days is a guess with a decimal
#: point on it.
MIN_DAYS = 14


def in_use_hours(hourly_median: dict[int, float]) -> list[int]:
    """Which hours of the day this building is in use, from its median hourly load.

    Returns the hours, not a span: a building with a lunchtime dip is still open through it, and
    a span would either swallow a genuine two-shift pattern or invent a closure that is not
    there. The caller turns hours into a span only when they are contiguous.
    """
    if not hourly_median:
        return []
    values = sorted(hourly_median.values())
    if len(values) < 4:
        return []
    floor = values[max(0, int(len(values) * 0.10) - 1)]
    plateau = values[min(len(values) - 1, int(len(values) * 0.90))]
    spread = plateau - floor
    if spread <= 0:
        # Flat all day: continuous process load, or a meter reading a constant. Either way
        # there is no occupancy signal to read, and inventing one from noise would be worse.
        return []
    cutoff = floor + spread * IN_USE_FRACTION
    return sorted(h for h, v in hourly_median.items() if v > cutoff)


def _span(hours: list[int]) -> tuple[int | None, int | None, bool]:
    """(open, close, contiguous). Close is exclusive-end, so 07:00-23:00 closes at 23."""
    if not hours:
        return None, None, True
    contiguous = hours == list(range(hours[0], hours[-1] + 1))
    return hours[0], hours[-1] + 1, contiguous


async def operating_hours(
    session: AsyncSession,
    *,
    building_id: UUID | str,
    days: int = 60,
) -> dict[str, Any]:
    """Assumed hours, derived hours, and the gap between them for one building.

    Every figure carries whether it is known. A missing assumption is reported as missing and
    NOT filled with a plausible default: the whole value of this comparison is that it says
    which pack the building was judged against, and a fabricated assumption behind a
    re-benchmark argument is worse than no argument.
    """
    profile = (
        await session.execute(
            text(
                """
                SELECT building_type, gia_m2,
                       assumed_open_hour, assumed_close_hour,
                       assumed_days_per_week, assumed_hours_source
                  FROM plenum_cafm.building_energy_profiles
                 WHERE building_id::text = :b
                 LIMIT 1
                """
            ),
            {"b": str(building_id)},
        )
    ).mappings().first()

    rows = (
        await session.execute(
            text(
                f"""
                SELECT EXTRACT(HOUR FROM r.reading_at)::int            AS hour,
                       EXTRACT(ISODOW FROM r.reading_at)::int          AS isodow,
                       percentile_cont(0.5) WITHIN GROUP (ORDER BY r.consumption_kwh) AS median_kwh
                  FROM plenum_cafm.meter_readings r
                  JOIN plenum_cafm.energy_meters m ON m.id = r.meter_id
                 WHERE m.building_id::text = :b
                   AND r.reading_at > now() - interval '{int(days)} days'
                   AND r.consumption_kwh IS NOT NULL
                 GROUP BY 1, 2
                """
            ),
            {"b": str(building_id)},
        )
    ).mappings().all()

    # Counted separately, and this is not fussiness. Taken inside the GROUP BY above it counts
    # distinct dates WITHIN one (hour, weekday) bucket — which is how often that weekday came
    # round, about 13 times in 90 days. Every building then reported 13 days of data and was
    # refused for having too little, while holding 43,000 readings over three months.
    days_seen = int(
        await session.scalar(
            text(
                f"""
                SELECT count(DISTINCT r.reading_at::date)
                  FROM plenum_cafm.meter_readings r
                  JOIN plenum_cafm.energy_meters m ON m.id = r.meter_id
                 WHERE m.building_id::text = :b
                   AND r.reading_at > now() - interval '{int(days)} days'
                   AND r.consumption_kwh IS NOT NULL
                """
            ),
            {"b": str(building_id)},
        )
        or 0
    )

    weekday = {r["hour"]: float(r["median_kwh"]) for r in rows if r["isodow"] <= 5}
    weekend = {r["hour"]: float(r["median_kwh"]) for r in rows if r["isodow"] >= 6}

    enough = days_seen >= MIN_DAYS
    wd_hours = in_use_hours(weekday) if enough else []
    we_hours = in_use_hours(weekend) if enough else []
    open_h, close_h, contiguous = _span(wd_hours)

    assumed_open = profile["assumed_open_hour"] if profile else None
    assumed_close = profile["assumed_close_hour"] if profile else None
    assumed_known = assumed_open is not None and assumed_close is not None

    assumed_days = (profile["assumed_days_per_week"] if profile else None) or 5

    gap_hours = None
    gap_weekly = None
    if assumed_known and open_h is not None and close_h is not None:
        gap_hours = (close_h - open_h) - (assumed_close - assumed_open)
        # Weekly, not daily, because the daily figure misses the weekend entirely. A building
        # running 07:00-19:00 against an assumed 09:00-21:00 has the same twelve hours and a
        # daily gap of zero — and if it also runs Saturdays, it works twelve hours a week more
        # than its pack assumes while the comparison reports no difference at all. That is
        # exactly the case in this estate: weekend operation observed against a five-day
        # assumption.
        derived_days = assumed_days + (2 if we_hours else 0)
        gap_weekly = ((close_h - open_h) * derived_days
                      - (assumed_close - assumed_open) * assumed_days)

    return {
        "building_id": str(building_id),
        "building_type": profile["building_type"] if profile else None,
        "assumed": {
            "known": assumed_known,
            "open_hour": assumed_open,
            "close_hour": assumed_close,
            "hours_per_day": (assumed_close - assumed_open) if assumed_known else None,
            "days_per_week": profile["assumed_days_per_week"] if profile else None,
            "source": profile["assumed_hours_source"] if profile else None,
        },
        "derived": {
            "known": enough and bool(wd_hours),
            "days_of_data": days_seen,
            "open_hour": open_h,
            "close_hour": close_h,
            "hours_per_day": (close_h - open_h) if open_h is not None else None,
            "weekday_hours_in_use": wd_hours,
            "weekend_hours_in_use": we_hours,
            # A non-contiguous set is a real pattern (two shifts, a cleaning window), not an
            # error — but a caller printing "07:00-23:00" over it would be describing a
            # continuity the readings do not show.
            "contiguous": contiguous,
            "weekend_operation": bool(we_hours),
        },
        "gap_hours_per_day": gap_hours,
        "gap_hours_per_week": gap_weekly,
        "note": _note(assumed_known, enough, wd_hours, gap_weekly, days_seen,
                      weekend=bool(we_hours), assumed_days=assumed_days),
    }


def _note(assumed_known: bool, enough: bool, wd_hours: list[int],
          gap: int | None, days_seen: int, *, weekend: bool = False,
          assumed_days: int = 5) -> str:
    """One sentence saying what may and may not be concluded from this."""
    if not enough:
        return (f"Only {days_seen} days of readings; at least {MIN_DAYS} are needed before an "
                f"operating pattern is a habit rather than one atypical week.")
    if not wd_hours:
        return ("The load is flat across the day, so there is no occupancy signal to read — a "
                "continuous process load, or a meter reporting a constant.")
    if not assumed_known:
        return ("The building's operating hours are derived, but the benchmark's assumed hours "
                "are not recorded, so the fit of the benchmark cannot be judged. Set "
                "assumed_open_hour / assumed_close_hour with their source.")
    weekend_note = (
        f" It also runs at weekends, which the assumption of {assumed_days} days a week does "
        f"not allow for." if weekend and assumed_days <= 5 else "")
    if gap is not None and gap > 0:
        return (f"The building is in use about {gap} hours a WEEK longer than its benchmark "
                f"assumes.{weekend_note} Part of the gap to reference is therefore benchmark "
                f"fit rather than waste — a re-benchmark question, not a work order.")
    if gap is not None and gap < 0:
        return (f"The building is in use about {abs(gap)} hours a week LESS than its benchmark "
                f"assumes, so the gap to reference is not explained by hours.")
    return ("The building keeps the hours its benchmark assumes; hours do not explain the gap."
            + weekend_note)
