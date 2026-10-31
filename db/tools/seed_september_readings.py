"""September, for meters whose feed stopped in August.

Production's three meters carry half-hourly reads to 31 August 13:00 and nothing since, so
every energy screen shows a fortnight-old picture and the scan has nothing new to find.
hoistra_test runs to 11 September. This fills the gap to now.

The readings are **simulated and say so**. Each row is written with ``source='simulator'``,
the same as the live demo feed, because a simulated reading and a metered one must never be
confusable — the findings the engine produces are only worth anything if you can tell which
data they came from. Two consequences worth stating plainly:

  * an anomaly raised on a September reading is an anomaly about invented data;
  * the EUI and cost figures for September are invented too, and the monthly report for
    September will be as well.

Only meters that already have a history are fed, and only meters attached to a building:
feeding a meter that is on no building produces readings that belong to nothing. Each
meter's base load is taken from its own recent real consumption rather than the simulator's
generic default, so September continues the shape of that meter's August instead of
starting a different building's profile.

Nothing existing is overwritten — ``backfill_range`` skips a slot a meter already has, which
is what keeps hoistra_test's real 1–11 September reads intact.

Dry run by default. ``--apply`` writes.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join("apps", "backend", "cafm-connector-service-final",
                                "svc-operations-intelligence"))

SCHEMA = "plenum_cafm"


async def run(dsn: str, expect_db: str, apply: bool, month: str) -> int:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from src.engines.energy import simulator

    year, mon = (int(x) for x in month.split("-"))
    start = datetime(year, mon, 1, tzinfo=timezone.utc)
    end = min(datetime.now(timezone.utc),
              (start + timedelta(days=32)).replace(day=1) - timedelta(minutes=30))

    engine = create_async_engine(dsn, pool_pre_ping=True)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        db_name = (await db.execute(text("select current_database()"))).scalar()
        if db_name != expect_db:
            print(f"refusing: connected to {db_name!r}, expected {expect_db!r}")
            await engine.dispose()
            return 2
        print(f"database: {db_name}")
        print(f"window:   {start:%Y-%m-%d %H:%M} .. {end:%Y-%m-%d %H:%M} UTC\n")

        # A meter worth feeding has a history to continue and a building to belong to.
        # The base load is the mean of each meter's own last fortnight of real reads, so
        # September continues that meter's shape. "Its own last fortnight" needs the meter's
        # own maximum, which a window function cannot supply inside FILTER — so the span is
        # settled per meter in a subquery first.
        candidates = (await db.execute(text(f"""
            WITH span AS (
                SELECT meter_id, max(reading_at) AS newest, count(*) AS readings
                  FROM {SCHEMA}.meter_readings GROUP BY meter_id
            )
            SELECT m.id::text AS id, m.meter_type, b.name AS building,
                   span.readings, span.newest,
                   round(avg(r.consumption_kwh)::numeric, 3) AS recent_mean
              FROM {SCHEMA}.energy_meters m
              JOIN {SCHEMA}.buildings b ON b.building_id = m.site_id
              JOIN span ON span.meter_id = m.id
              JOIN {SCHEMA}.meter_readings r
                ON r.meter_id = m.id
               AND r.reading_at > span.newest - interval '14 days'
             WHERE m.active
             GROUP BY 1, 2, 3, 4, 5
             ORDER BY 4 DESC
        """))).mappings().all()

        print(f"{'meter':<10} {'type':<12} {'building':<20} {'reads':>7}  newest")
        for m in candidates:
            print(f"  {m['id'][:8]:<8} {str(m['meter_type']):<12} {str(m['building'])[:18]:<20} "
                  f"{m['readings']:>7}  {m['newest']}  base={m['recent_mean']}")
        if not candidates:
            print("  none — no active meter has both a history and a building")
            await engine.dispose()
            return 0

        already = (await db.execute(text(f"""
            SELECT count(*) FROM {SCHEMA}.meter_readings
             WHERE reading_at >= :s AND reading_at <= :e
        """), {"s": start.replace(tzinfo=None), "e": end.replace(tzinfo=None)})).scalar()
        slots = int((end - start).total_seconds() // 1800) + 1
        print(f"\n  {slots} half-hours per meter · {already} readings already in the window")

        if not apply:
            print("\ndry run — nothing written. Re-run with --apply.")
            await engine.dispose()
            return 0

        print("\nflagging those meters for simulation…")
        for m in candidates:
            await db.execute(text(f"""
                UPDATE {SCHEMA}.energy_meters
                   -- Cast every parameter: jsonb_build_object takes "any", so the driver has
                   -- nothing to infer a bind's type from and refuses to prepare the
                   -- statement at all.
                   SET raw_metadata = coalesce(raw_metadata, '{{}}'::jsonb) || jsonb_build_object(
                           'simulate', true,
                           'sim_base_kwh', CAST(:base AS double precision),
                           'sim_note', CAST(:note AS text))
                 WHERE id = CAST(:id AS uuid)
            """), {"id": m["id"], "base": float(m["recent_mean"] or 40.0),
                   "note": f"simulated feed enabled {datetime.now(timezone.utc):%Y-%m-%d} to "
                           f"fill {month}; base taken from this meter's own recent reads"})
        await db.commit()

        out = await simulator.backfill_range(db, start=start, end=end)
        print(f"  inserted={out['inserted']} skipped_existing={out['skipped_existing']} "
              f"faults={out['faults_injected']}")

        print("\nafter:")
        for r in (await db.execute(text(f"""
            SELECT m.id::text AS id, m.meter_type, count(r.*) AS n, max(r.reading_at) AS newest,
                   count(*) FILTER (WHERE r.source = 'simulator') AS simulated
              FROM {SCHEMA}.energy_meters m
              JOIN {SCHEMA}.meter_readings r ON r.meter_id = m.id
             WHERE coalesce(m.raw_metadata->>'simulate','false') = 'true'
             GROUP BY 1, 2 ORDER BY 3 DESC
        """))).mappings().all():
            print(f"  {r['id'][:8]} {str(r['meter_type']):<12} {r['n']:>6} reads "
                  f"({r['simulated']} simulated)  newest {r['newest']}")
    await engine.dispose()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--database", default="plenum_agent")
    ap.add_argument("--month", default="2026-09", help="YYYY-MM to fill")
    ap.add_argument("--dsn", default=None)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    dsn = a.dsn
    if not dsn:
        dsn = next(l.split("=", 1)[1].strip() for l in
                   open(os.path.join("apps", "backend", ".env"), encoding="utf-8")
                   if l.startswith("DB_URL="))
    dsn = re.sub(r"/[^/?]+(\?|$)", f"/{a.database}\\1", dsn)
    return asyncio.run(run(dsn, a.database, a.apply, a.month))


if __name__ == "__main__":
    sys.exit(main())
