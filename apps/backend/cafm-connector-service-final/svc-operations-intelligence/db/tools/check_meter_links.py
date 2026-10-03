"""Are the meters linked, and can every reading reach a building? Read only.

Counting rows proves nothing here. Almost every link in the energy chain is a plain uuid
column with no foreign key behind it — energy_meters.building_id, energy_meters.section_id,
energy_anomalies.building_id — so a wrong value inserts cleanly, joins to nothing, and the
rows vanish from the page with no error raised anywhere. It reads as a rendering bug rather
than an ingest one.

The only declared foreign key in the chain is meter_readings.meter_id, which is why a reading
with no meter fails loudly while a meter with no building fails silently.

So this checks the joins rather than the counts, and says plainly which of the two states a
quiet page is in: nothing ingested, or ingested and attached to nothing.

    python db/tools/check_meter_links.py --db hoistra_test
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys

import asyncpg

sys.stdout.reconfigure(encoding="utf-8")

ROOT_ENV = r"C:\CMMS\Hoistra\.env"

#: Each is a count that should be zero. A non-zero answer names the breakage.
INTEGRITY = [
    ("meters with no building at all",
     "SELECT count(*) FROM plenum_cafm.energy_meters WHERE building_id IS NULL",
     "the anomaly scan still sweeps these, and writes findings attributed to nothing"),
    ("meters whose building_id matches no building",
     """SELECT count(*) FROM plenum_cafm.energy_meters m
         WHERE m.building_id IS NOT NULL AND NOT EXISTS (
           SELECT 1 FROM plenum_cafm.buildings b WHERE b.building_id = m.building_id)""",
     "a uuid that points nowhere; no constraint would have caught it"),
    ("meters whose section_id matches no section",
     """SELECT count(*) FROM plenum_cafm.energy_meters m
         WHERE m.section_id IS NOT NULL AND NOT EXISTS (
           SELECT 1 FROM plenum_cafm.building_sections s WHERE s.section_id = m.section_id)""",
     "the sub-meter reads a section this building has not got"),
    ("readings whose meter_id matches no meter",
     """SELECT count(*) FROM plenum_cafm.meter_readings r
         WHERE NOT EXISTS (
           SELECT 1 FROM plenum_cafm.energy_meters m WHERE m.id = r.meter_id)""",
     "should be impossible: this one link IS a foreign key"),
    ("readings that cannot reach a building through their meter",
     """SELECT count(*) FROM plenum_cafm.meter_readings r
          JOIN plenum_cafm.energy_meters m ON m.id = r.meter_id
         WHERE m.building_id IS NULL""",
     "stored, and counted towards no building, no EUI and no anomaly"),
    ("anomalies with no building",
     """SELECT count(*) FROM plenum_cafm.energy_anomalies WHERE building_id IS NULL""",
     "invisible on a building-scoped page, however real the finding"),
    ("sub-meters that name no section",
     """SELECT count(*) FROM plenum_cafm.energy_meters
         WHERE is_sub_meter AND section_id IS NULL""",
     "not fatal, but a sub-meter that sits nowhere cannot be read against its own reference"),
]


def dsn_for() -> str:
    raw = os.environ.get("PLENUM_DB_DSN")
    if not raw and os.path.exists(ROOT_ENV):
        for line in open(ROOT_ENV, encoding="utf-8"):
            if line.startswith("PLENUM_DB_DSN="):
                raw = line.split("=", 1)[1].strip().strip('"')
    if not raw:
        raise SystemExit("PLENUM_DB_DSN not set")
    return re.sub(r"^postgresql\+asyncpg://", "postgresql://", raw)


async def run(db: str) -> None:
    c = await asyncpg.connect(dsn_for(), database=db, timeout=60)
    print(f"\n################ {db} ################\n")

    meters = await c.fetch("""
        SELECT coalesce(m.mpan, m.mprn, '(no supply number)') AS supply,
               m.meter_type, m.is_sub_meter, m.active,
               coalesce(b.building_code, '** NOT LINKED **') AS building,
               sec.name AS section, f.name AS floor, a.asset_code AS asset,
               (SELECT count(*) FROM plenum_cafm.meter_readings r WHERE r.meter_id = m.id) AS readings,
               (SELECT max(r.reading_at)::date FROM plenum_cafm.meter_readings r
                 WHERE r.meter_id = m.id) AS newest
          FROM plenum_cafm.energy_meters m
          LEFT JOIN plenum_cafm.buildings b ON b.building_id = m.building_id
          LEFT JOIN plenum_cafm.building_sections sec ON sec.section_id = m.section_id
          LEFT JOIN plenum_cafm.floors f ON f.floor_id = sec.floor_id
          LEFT JOIN plenum_cafm.assets a ON a.id = m.asset_id
         ORDER BY b.building_code NULLS FIRST, m.meter_type""")

    print(f"meters on record: {len(meters)}")
    if not meters:
        print("  Nothing to link. An empty Energy page here means nothing was ingested,")
        print("  which is a different problem from data that landed and attached to nothing.")
    for m in meters:
        where = m["section"] or ("whole building" if not m["is_sub_meter"] else "** NOT PLACED **")
        floor = f" · {m['floor']}" if m["floor"] else ""
        kind = "sub-meter" if m["is_sub_meter"] else "main"
        asset = f" · asset {m['asset']}" if m["asset"] else ""
        state = "" if m["active"] else "   [INACTIVE - the scan skips it]"
        print(f"  {m['supply']:18} {m['meter_type']:12} {kind:10} "
              f"{m['building']:16} {where}{floor}{asset}")
        print(f"  {'':18} readings {m['readings']:<8} newest {m['newest']}{state}")

    print("\nintegrity — every one of these should be zero:")
    bad = 0
    for label, sql, why in INTEGRITY:
        n = await c.fetchval(sql)
        if n:
            bad += 1
            print(f"  {label:54} {n:>6}   <-- {why}")
        else:
            print(f"  {label:54} {n:>6}   ok")

    print("\nreadings per building, reached through the meter:")
    for r in await c.fetch("""
        SELECT b.building_code, b.name, count(r.id) AS readings,
               min(r.reading_at)::date AS lo, max(r.reading_at)::date AS hi
          FROM plenum_cafm.buildings b
          LEFT JOIN plenum_cafm.energy_meters m ON m.building_id = b.building_id
          LEFT JOIN plenum_cafm.meter_readings r ON r.meter_id = m.id
         GROUP BY 1, 2 ORDER BY 1"""):
        span = f"{r['lo']} .. {r['hi']}" if r["readings"] else "-"
        print(f"  {r['building_code']:8} {r['name']:20} {r['readings']:>8}   {span}")

    derived = await c.fetchrow("""
        SELECT (SELECT count(*) FROM plenum_cafm.eui_snapshots) AS snapshots,
               (SELECT count(*) FROM plenum_cafm.energy_anomalies) AS anomalies,
               (SELECT count(*) FROM plenum_cafm.meter_reading_gaps) AS gaps""")
    print(f"\nderived — written by the scan, not by an ingest:")
    print(f"  eui_snapshots {derived['snapshots']}   energy_anomalies {derived['anomalies']}"
          f"   meter_reading_gaps {derived['gaps']}")

    print(f"\n{'all links hold' if not bad else str(bad) + ' link check(s) failed'}")
    await c.close()


async def main() -> None:
    ap = argparse.ArgumentParser(description="Check the energy links. Reads only.")
    ap.add_argument("--db", required=True, help="database name, e.g. hoistra_test")
    args = ap.parse_args()
    await run(args.db)


if __name__ == "__main__":
    asyncio.run(main())
