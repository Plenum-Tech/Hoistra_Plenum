"""Delete readings left behind by a deleted meter — but only ones already held elsewhere.

1,061 readings in production sit on meter_id 9c50b7b5-…, which has no row in
energy_meters at all. They were written by a single CSV import at 13:21:20 on 31 August;
meter 1200023305687 was created 40 seconds later, at 13:22:00, and the same CSV was
re-imported against it. The first meter was then deleted and its readings stayed.

They are not lost data. Every one of the 1,061 is identical — same timestamp, same
consumption, same period — to a reading on 1200023305687, whose 1,407 readings cover a
wider window (2 August rather than 9 August). Re-pointing them at that meter would
double-count 33,460 kWh onto a meter that already holds every one of those readings.

So they are deleted, and the condition that makes that safe is written into the statement
rather than checked beforehand: a reading is removed only if its meter is absent from
energy_meters AND an identical reading exists on a meter that is present. A reading with no
twin cannot be deleted by this tool however it is invoked, so the worst case is that it
removes nothing.

    python db/tools/clear_stranded_meter_readings.py --to <dsn>           # dry run
    python db/tools/clear_stranded_meter_readings.py --to <dsn> --apply

Dry run by default.
"""
from __future__ import annotations

import argparse
import asyncio
import re
from typing import Any

import asyncpg

#: A reading whose meter no longer exists, and which is held identically on a meter that
#: does. Both halves matter: the first makes it unreachable, the second makes it redundant.
#: Used verbatim for counting and for deleting, so the dry run and the write cannot differ.
WHERE = """
    NOT EXISTS (SELECT 1 FROM plenum_cafm.energy_meters em WHERE em.id = a.meter_id)
    AND EXISTS (
        SELECT 1 FROM plenum_cafm.meter_readings b
          JOIN plenum_cafm.energy_meters em2 ON em2.id = b.meter_id
         WHERE b.reading_at       = a.reading_at
           AND b.consumption_kwh  = a.consumption_kwh
           AND b.period_minutes   = a.period_minutes)
"""


def _ssl_for(dsn: str) -> Any:
    host = re.search(r"@([^/:]+)", dsn)
    return "require" if host and not host.group(1).startswith(("localhost", "127.")) else False


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--to", required=True, help="Postgres DSN")
    ap.add_argument("--apply", action="store_true", help="Write. Without it, nothing changes.")
    args = ap.parse_args()

    conn = await asyncpg.connect(args.to, ssl=_ssl_for(args.to))
    try:
        total = await conn.fetchval("SELECT count(*) FROM plenum_cafm.meter_readings")
        unreachable = await conn.fetchval(
            """SELECT count(*) FROM plenum_cafm.meter_readings a
                WHERE NOT EXISTS (SELECT 1 FROM plenum_cafm.energy_meters em
                                   WHERE em.id = a.meter_id)"""
        )
        removable = await conn.fetchval(
            f"SELECT count(*) FROM plenum_cafm.meter_readings a WHERE {WHERE}"
        )
        print(f"meter_readings: {total} rows")
        print(f"   on a meter that no longer exists : {unreachable}")
        print(f"   of those, held identically elsewhere: {removable}")
        print(f"   would be LOST if deleted            : {unreachable - removable}")

        if unreachable - removable:
            print("\n   those are real readings with no twin. This tool cannot delete them —"
                  "\n   they need a meter, not a delete.")

        groups = await conn.fetch(
            f"""SELECT a.meter_id::text AS mid, count(*) AS n,
                       min(a.reading_at)::date AS lo, max(a.reading_at)::date AS hi,
                       round(sum(a.consumption_kwh), 1) AS kwh
                  FROM plenum_cafm.meter_readings a
                 WHERE {WHERE}
                 GROUP BY 1 ORDER BY 2 DESC"""
        )
        if groups:
            print("\n   by meter:")
            for g in groups:
                print(f"     {g['mid'][:8]}  {g['n']} readings  {g['lo']} -> {g['hi']}  "
                      f"{g['kwh']} kWh (duplicated)")

        print()
        if not removable:
            print("nothing to remove.")
            return
        if not args.apply:
            print(f"dry run — nothing written. --apply would delete {removable} row(s).")
            return

        async with conn.transaction():
            tag = await conn.execute(
                f"DELETE FROM plenum_cafm.meter_readings a WHERE {WHERE}"
            )
        after = await conn.fetchval("SELECT count(*) FROM plenum_cafm.meter_readings")
        left = await conn.fetchval(
            """SELECT count(*) FROM plenum_cafm.meter_readings a
                WHERE NOT EXISTS (SELECT 1 FROM plenum_cafm.energy_meters em
                                   WHERE em.id = a.meter_id)"""
        )
        print(f"{tag}. meter_readings {total} -> {after}; "
              f"still on a missing meter: {left}.")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
