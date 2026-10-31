"""Merge the provisional energy building into the real one it turned out to be.

The orphaned energy site was adopted under a provisional building — PROV-E2A55DC1,
"Provisional · unidentified UK office" — precisely so it could be merged once somebody
identified it. It has been identified as Harbour View (B-07, Glasgow).

This is a merge rather than a rename. Harbour View already exists, so renaming the
provisional row would leave two buildings called Harbour View, which is the duplicate this
whole exercise has been removing. Instead every energy row is re-pointed onto the real
building and the provisional pair is deleted.

Six tables key their energy records on the building id, and plenum_cafm.meters keys the MPAN
register on it. All seven are moved together, inside one transaction, so the data is never
half-attributed.

The provisional row's evidenced facts move too, where the real building has no value of its
own — its 4,200 m² came from building_energy_profiles.gia_m2 and is as true of Harbour View
as it was of the placeholder. A value the real building already has is never overwritten:
the placeholder was always the weaker record.

    python db/tools/merge_provisional_building.py --to <dsn> --into B-07
    python db/tools/merge_provisional_building.py --to <dsn> --into B-07 --apply

Dry run by default. It refuses if the target does not exist, if the target IS the
provisional, or if anything it would move has already been moved.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
from typing import Any

import asyncpg

PROVISIONAL_UUID = "e2a55dc1-8d76-5fa2-93e8-f585cb31d6f4"
PROVISIONAL_SITE_KEY = "S-PROV-E2A55DC1"

#: Energy tables that name the building in `site_id`. The column is called site_id for
#: historical reasons; attribute_energy() reads a building's own id there and treats it as
#: its strongest case, which is why the adoption put the building id in it.
ENERGY_TABLES = (
    "energy_meters",
    "energy_anomalies",
    "eui_snapshots",
    "building_energy_profiles",
    "site_occupancy_logs",
    "energy_monthly_reports",
)


def _ssl_for(dsn: str) -> Any:
    host = re.search(r"@([^/:]+)", dsn)
    return "require" if host and not host.group(1).startswith(("localhost", "127.")) else False


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--to", required=True, help="Postgres DSN")
    ap.add_argument("--into", required=True, help="building_code of the real building")
    ap.add_argument("--apply", action="store_true", help="Write. Without it, nothing changes.")
    args = ap.parse_args()

    conn = await asyncpg.connect(args.to, ssl=_ssl_for(args.to))
    try:
        target = await conn.fetchrow(
            """SELECT building_id::text AS bid, site_id, name, building_code, gross_area_sqft,
                      raw_metadata
                 FROM plenum_cafm.buildings WHERE building_code = $1""",
            args.into,
        )
        if not target:
            print(f"REFUSING: no building with code {args.into!r}. Nothing written.")
            return
        if target["bid"] == PROVISIONAL_UUID:
            print("REFUSING: that IS the provisional building. Nothing written.")
            return
        source = await conn.fetchrow(
            """SELECT building_id::text AS bid, name, gross_area_sqft, raw_metadata
                 FROM plenum_cafm.buildings WHERE building_id = $1::uuid""",
            PROVISIONAL_UUID,
        )
        if not source:
            print("nothing to merge: the provisional building no longer exists.")
            return

        print(f"merging {source['name']}\n     into {target['name']} "
              f"({target['building_code']}, {target['bid'][:8]}…)\n")
        moving = 0
        for table in ENERGY_TABLES:
            n = await conn.fetchval(
                f'SELECT count(*) FROM plenum_cafm."{table}" WHERE site_id = $1::uuid',
                PROVISIONAL_UUID,
            )
            clash = await conn.fetchval(
                f'SELECT count(*) FROM plenum_cafm."{table}" WHERE site_id = $1::uuid',
                target["bid"],
            )
            moving += n
            print(f"   {table:<26} move {n:>3}"
                  + (f"   (target already has {clash})" if clash else ""))
        reg = await conn.fetchval(
            "SELECT count(*) FROM plenum_cafm.meters WHERE building_id = $1::uuid",
            PROVISIONAL_UUID)
        moving += reg
        print(f"   {'meters (MPAN register)':<26} move {reg:>3}")

        carry = (target["gross_area_sqft"] is None
                 and source["gross_area_sqft"] is not None)
        if carry:
            print(f"\n   carrying over gross_area_sqft = {source['gross_area_sqft']} "
                  f"({args.into} has none; it came from building_energy_profiles.gia_m2)")
        print(f"\n   then deleting the provisional building and its site "
              f"({PROVISIONAL_SITE_KEY})")

        if not args.apply:
            print(f"\ndry run — nothing written. --apply would move {moving} row(s).")
            return

        async with conn.transaction():
            for table in ENERGY_TABLES:
                await conn.execute(
                    f'UPDATE plenum_cafm."{table}" SET site_id = $1::uuid '
                    f"WHERE site_id = $2::uuid",
                    target["bid"], PROVISIONAL_UUID,
                )
            await conn.execute(
                "UPDATE plenum_cafm.meters SET building_id = $1::uuid "
                "WHERE building_id = $2::uuid",
                target["bid"], PROVISIONAL_UUID,
            )
            if carry:
                await conn.execute(
                    "UPDATE plenum_cafm.buildings SET gross_area_sqft = $1 "
                    "WHERE building_id = $2::uuid AND gross_area_sqft IS NULL",
                    source["gross_area_sqft"], target["bid"],
                )
            # Where the energy history came from, recorded on the building that now owns it.
            note = {
                "energy_history_merged_from": {
                    "provisional_building_id": PROVISIONAL_UUID,
                    "was": source["name"],
                    "evidence": json.loads(source["raw_metadata"] or "{}").get("evidence"),
                }
            }
            await conn.execute(
                "UPDATE plenum_cafm.buildings SET raw_metadata = "
                "COALESCE(raw_metadata, '{}'::jsonb) || $1::jsonb "
                "WHERE building_id = $2::uuid",
                json.dumps(note), target["bid"],
            )
            await conn.execute(
                "DELETE FROM plenum_cafm.buildings WHERE building_id = $1::uuid",
                PROVISIONAL_UUID)
            await conn.execute(
                "DELETE FROM plenum_cafm.sites WHERE site_id = $1", PROVISIONAL_SITE_KEY)

        left = await conn.fetchval(
            """SELECT count(*) FROM plenum_cafm.energy_meters WHERE site_id = $1::uuid""",
            PROVISIONAL_UUID)
        readings = await conn.fetchval(
            """SELECT count(*) FROM plenum_cafm.meter_readings mr
                 JOIN plenum_cafm.energy_meters em ON em.id = mr.meter_id
                WHERE em.site_id = $1::uuid""",
            target["bid"])
        anomalies = await conn.fetchval(
            "SELECT count(*) FROM plenum_cafm.energy_anomalies WHERE site_id = $1::uuid",
            target["bid"])
        buildings = await conn.fetchval("SELECT count(*) FROM plenum_cafm.buildings")
        print(f"\ndone. {target['name']} now holds {readings} readings and {anomalies} "
              f"anomalies; rows still on the provisional id: {left}; "
              f"buildings: {buildings}.")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
