"""Put Bishopsgate Tower and its floors under Plenum Technologies before its workbook is ingested.

The workbook (build_bishopsgate_workbook.py) carries the building's rows but cannot create its
floors: plenum_cafm.floors is not a table a workbook writes, and the floor sub-meters are placed
by joining their section's floor_id to it. Without these rows every floor meter lands under
"unplaced" on the Energy page. Northbridge's floors came the same way, from
svc-operations-intelligence/db/tools/seed_northbridge_assets.py.

Writes, for the organization named --org-name (created first in the Super Admin console):
  buildings   B-301 Bishopsgate Tower, organization set - the workbook's Buildings row then
              matches it instead of creating a second one
  floors      Basement, Ground, Level 1 .. Level 20 - the names build_floor_submeters.py uses

Keyed by uuid5, so a second run changes nothing. Dry run by default; --apply writes. Refuses any
database but hoistra_test. Creates no users: the company's admin is invited from the console.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

NS = uuid.UUID("b4c1f0d2-3a57-4e0c-9d61-5f2e8a7c3301")
CODE, NAME = "B-301", "Bishopsgate Tower"
FLOORS = ["Basement", "Ground"] + [f"Level {i}" for i in range(1, 21)]
GIA_M2 = 49_400.0


async def main() -> None:
    from _env import hoistra_test_dsn
    import asyncpg

    ap = argparse.ArgumentParser()
    ap.add_argument("--org-name", default="Plenum Technologies")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    c = await asyncpg.connect(hoistra_test_dsn().replace("postgresql+asyncpg", "postgresql"), timeout=15)
    try:
        db = await c.fetchval("SELECT current_database()")
        if db != "hoistra_test":
            raise SystemExit(f"refusing to run against {db!r}: hoistra_test only")
        org = await c.fetchval("SELECT id FROM plenum_cafm.organizations WHERE lower(name) = lower($1)", args.org_name)
        if not org:
            raise SystemExit(f"no organization named {args.org_name!r} - create it in the Super Admin console first")
        cols = {r[0] for r in await c.fetch(
            "SELECT column_name FROM information_schema.columns WHERE table_schema='plenum_cafm' AND table_name='buildings'")}
        taken = await c.fetchval(
            "SELECT organization_id::text FROM plenum_cafm.buildings WHERE building_code = $1 LIMIT 1", CODE)
        if taken and taken != str(org):
            raise SystemExit(f"{CODE} already belongs to organization {taken}")
        bid = await c.fetchval("SELECT building_id FROM plenum_cafm.buildings WHERE building_code = $1 "
                               "AND organization_id = $2", CODE, org) or uuid.uuid5(NS, f"building:{org}:{CODE}")
        print(f"\n  organization {args.org_name}: {org}\n  building {CODE} {NAME}: {bid}")
        print(f"  floors: {len(FLOORS)} ({FLOORS[0]} .. {FLOORS[-1]})")
        if not args.apply:
            print("\n  dry run - pass --apply to write")
            return
        async with c.transaction():
            fields = {"building_id": bid, "name": NAME, "building_code": CODE, "organization_id": org}
            if "site_id" in cols:
                fields["site_id"] = "S-301"      # the workbook's Sites.site_id; an existing building is not updated by the ingest
            if "floors" in cols:
                fields["floors"] = len(FLOORS)
            if "gross_area_sqft" in cols:
                fields["gross_area_sqft"] = round(GIA_M2 * 10.7639, 2)
            if "country_code" in cols:
                fields["country_code"] = "UK"
            if "gross_internal_area_m2" in cols:
                fields["gross_internal_area_m2"] = str(GIA_M2)
            # The ingest skips a building that already exists, so the use and country the energy
            # engines read are set here - the way the UI records them on Northbridge's buildings.
            # Without them the building has no market, no TM46 benchmark and no MEES tiles.
            if "primary_use" in cols:
                fields["primary_use"] = "Commercial"
            if "raw_metadata" in cols:
                fields["raw_metadata"] = json.dumps({
                    "country": "UK", "country_code": "UK", "use_type": "Commercial",
                    "use_mix": [{"pct": 100.0, "use": "office"}], "metering_granularity": "half-hourly",
                    "source": "seed_bishopsgate_building"})
            names = list(fields)
            ph = ", ".join(f"${i + 1}" + ("::jsonb" if n == "raw_metadata" else
                                          "::plenum_cafm.building_primary_use" if n == "primary_use" else "")
                           for i, n in enumerate(names))
            upd = ", ".join(f"{n} = EXCLUDED.{n}" for n in names if n != "building_id")
            await c.execute(
                f"INSERT INTO plenum_cafm.buildings ({', '.join(names)}, created_at, updated_at) "
                f"VALUES ({ph}, now(), now()) ON CONFLICT (building_id) DO UPDATE SET {upd}, updated_at = now()",
                *fields.values())
            for level, fname in enumerate(FLOORS, start=-1):
                await c.execute(
                    """INSERT INTO plenum_cafm.floors (floor_id, building_id, level, name, gross_area_sqft)
                       VALUES ($1, $2, $3, $4, $5)
                       ON CONFLICT (floor_id) DO UPDATE SET level = EXCLUDED.level, name = EXCLUDED.name""",
                    uuid.uuid5(NS, f"floor:{bid}:{fname}"), bid, level, fname,
                    round(GIA_M2 * 10.7639 / len(FLOORS), 2))
        n = await c.fetchval("SELECT count(*) FROM plenum_cafm.floors WHERE building_id = $1", bid)
        print(f"\n  written: building {CODE}, {n} floors")
    finally:
        await c.close()


if __name__ == "__main__":
    asyncio.run(main())
