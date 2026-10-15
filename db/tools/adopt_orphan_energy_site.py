"""Give the orphaned energy site a building record, so its readings resolve.

One site in production exists only in the energy tables: a building profile (office,
4,200 m²), 8 EUI snapshots, 8 anomalies carrying £17k of exposure, an August monthly report
with a rendered PDF, an occupancy note, 3 meters and 11,108 half-hourly readings — all
keyed on site_id e2a55dc1-8d76-5fa2-93e8-f585cb31d6f4, which matches no row in `sites` and
none in `buildings`. So every reading is unreachable from any building and every anomaly
reads "Unattributed site".

Which real building it is cannot be derived. Its uuid does not regenerate from any site or
building key under the migration's namespace; the meters carry no asset, no name and no
metadata; a sweep of every text, uuid and jsonb column in the schema finds the value in
seven places, all of them energy tables, none naming anything; and size cannot discriminate
because gross_area_sqft is populated on 1 of 618 buildings.

So this records what IS known and claims nothing more: a UK office of 4,200 m², named
provisionally, with the evidence for each field written into raw_metadata. It is a
placeholder to be merged into a real building once someone identifies it — not an assertion
that a new building exists.

The building takes the orphan's OWN uuid as its building_id. That is the whole trick: every
one of those six energy tables already points at it, so they all resolve the moment the row
exists and not a single existing row is modified. Undoing this is deleting what it added.

    python db/tools/adopt_orphan_energy_site.py --to <dsn>           # dry run
    python db/tools/adopt_orphan_energy_site.py --to <dsn> --apply

Dry run by default. It refuses outright if the building_id is already taken, and skips any
part already done, so running it twice is safe.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
from typing import Any

import asyncpg

#: The orphan. Also the building_id the new row takes, so nothing has to be re-pointed.
SITE_UUID = "e2a55dc1-8d76-5fa2-93e8-f585cb31d6f4"

SITE_KEY = "S-PROV-E2A55DC1"
BUILDING_CODE = "PROV-E2A55DC1"
#: Provisional in the name itself, so it cannot be mistaken for an identified property in a
#: list, an export or a report.
NAME = "Provisional · unidentified UK office (energy site e2a55dc1)"

#: 4,200 m² as the profile records it. Converted because buildings stores square feet.
GIA_M2 = 4200.0
SQFT_PER_M2 = 10.763910416709722

#: Every field below is a fact the energy data states, with where it came from. Anything not
#: evidenced is left null rather than filled in plausibly — floors is absent because an
#: occupancy note mentioning "floor 3" proves at least three floors, not exactly three.
PROVENANCE = {
    "provisional": True,
    "reason": "energy site with no buildings/sites row; real identity unknown",
    "orphan_site_id": SITE_UUID,
    "evidence": {
        "building_type": "building_energy_profiles.building_type = 'office'",
        "gia_m2": "building_energy_profiles.gia_m2 = 4200.00",
        "country": "MPAN/MPRN meter identifiers, GBP tariff, CIBSE TM46 benchmarks",
        "floors": "site_occupancy_logs notes 'floor 3' — at least 3, exact count unknown",
    },
    "merge_when_identified": (
        "Re-point this building_id onto the real building, or rename this row in place; "
        "the energy tables key on the building_id and follow either way."
    ),
}

#: The three meters that carry readings. mpan_mprn holds either number — the register has
#: one column, because a meter has an MPAN or an MPRN and never both.
METERS = (
    ("1200023305687", "electricity"),
    ("1200023305688", "electricity"),
    ("9130847265", "gas"),
)


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
        taken = await conn.fetchrow(
            "SELECT name, building_code FROM plenum_cafm.buildings WHERE building_id = $1::uuid",
            SITE_UUID,
        )
        if taken and taken["building_code"] != BUILDING_CODE:
            print(f"REFUSING: building_id {SITE_UUID} already belongs to "
                  f"{taken['name']!r} ({taken['building_code']}). Nothing written.")
            return

        # What currently points at the orphan, and therefore what this fixes.
        counts = {}
        for table in ("energy_meters", "energy_anomalies", "eui_snapshots",
                      "building_energy_profiles", "site_occupancy_logs",
                      "energy_monthly_reports"):
            counts[table] = await conn.fetchval(
                f'SELECT count(*) FROM plenum_cafm."{table}" WHERE site_id = $1::uuid', SITE_UUID
            )
        readings = await conn.fetchval(
            """SELECT count(*) FROM plenum_cafm.meter_readings mr
                 JOIN plenum_cafm.energy_meters em ON em.id = mr.meter_id
                WHERE em.site_id = $1::uuid""",
            SITE_UUID,
        )

        print(f"orphan site {SITE_UUID}")
        for t, n in counts.items():
            print(f"   {t:<26} {n}")
        print(f"   {'meter_readings (via meters)':<26} {readings}")

        has_site = await conn.fetchval(
            "SELECT count(*) FROM plenum_cafm.sites WHERE site_id = $1", SITE_KEY)
        has_bld = bool(taken)
        have_meters = await conn.fetchval(
            "SELECT count(*) FROM plenum_cafm.meters WHERE building_id = $1::uuid", SITE_UUID)

        sqft = round(GIA_M2 * SQFT_PER_M2, 2)
        print(f"\nwould create:")
        print(f"   sites      {SITE_KEY:<18} {NAME}" + ("   (exists)" if has_site else ""))
        print(f"   buildings  {BUILDING_CODE:<18} building_id = the orphan's own uuid, "
              f"{sqft} sqft" + ("   (exists)" if has_bld else ""))
        for ref, kind in METERS:
            print(f"   meters     {ref:<18} {kind}"
                  + ("   (already registered)" if have_meters else ""))

        print(f"\nno existing row is modified: the six tables above already point at this "
              f"uuid and resolve as soon as the building row exists.")

        if not args.apply:
            print("\ndry run — nothing written.")
            return

        async with conn.transaction():
            await conn.execute(
                """INSERT INTO plenum_cafm.sites (site_id, site_name, country, country_code,
                                                  site_type, status)
                   VALUES ($1, $2, 'United Kingdom', 'UK', 'office', 'active')
                   ON CONFLICT (site_id) DO NOTHING""",
                SITE_KEY, NAME,
            )
            await conn.execute(
                """INSERT INTO plenum_cafm.buildings
                       (building_id, site_id, name, building_code, gross_area_sqft, raw_metadata)
                   VALUES ($1::uuid, $2, $3, $4, $5, $6::jsonb)
                   ON CONFLICT (building_id) DO NOTHING""",
                SITE_UUID, SITE_KEY, NAME, BUILDING_CODE, sqft, json.dumps(PROVENANCE),
            )
            for ref, kind in METERS:
                await conn.execute(
                    """INSERT INTO plenum_cafm.meters (building_id, mpan_mprn, meter_type, unit)
                       SELECT $1::uuid, $2, $3, 'kWh'
                        WHERE NOT EXISTS (SELECT 1 FROM plenum_cafm.meters
                                           WHERE mpan_mprn = $2 AND building_id = $1::uuid)""",
                    SITE_UUID, ref, kind,
                )

        reachable = await conn.fetchval(
            """SELECT count(*) FROM plenum_cafm.meter_readings mr
                 JOIN plenum_cafm.energy_meters em ON em.id = mr.meter_id
                 JOIN plenum_cafm.buildings b ON b.building_id = em.site_id""",
        )
        anomalies = await conn.fetchval(
            """SELECT count(*) FROM plenum_cafm.energy_anomalies a
                 JOIN plenum_cafm.buildings b ON b.building_id = a.site_id"""
        )
        print(f"\ndone. readings now reachable from a building: {reachable}; "
              f"anomalies that resolve: {anomalies}; "
              f"meter register rows: "
              f"{await conn.fetchval('SELECT count(*) FROM plenum_cafm.meters')}.")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
