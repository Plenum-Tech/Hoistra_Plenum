"""Put the meter register back to one row per supply, and get Harbour Point's supplies in.

Two faults, one cause. The migration writer inserted an Energy_Meters sheet row without first
asking whether that supply number was already on record, so re-ingesting a workbook that had
already been ingested gave Ashgrove Court four meters where it has two. Nothing in the schema
objects: energy_meters has no unique index on mpan or mprn, so the second row inserts as
cleanly as the first. The writer now matches on the supply number (see meter_link.find), which
stops new duplicates; this clears the ones already there.

The second fault is the other half of the same ingest. The per-building workbook that was
loaded was Ashgrove's, so Harbour Point's two supplies were never offered to the writer at all.
With no meters it has no readings, with no readings no EUI snapshot, and with no snapshot the
portfolio cards average one building.

Safe to run more than once. A duplicate is only removed when it carries no readings and a
sibling on the same supply number does; a meter is only created when its supply number is not
already on record. Both are matched on the supply number, which is what identifies a meter to
a utility and what the writer now keys on.
"""
from __future__ import annotations

import asyncio
import csv
import datetime as dt
import io
import os
import sys
import uuid

import asyncpg

CSV_DIR = os.environ.get(
    "NORTHBRIDGE_ENERGY_DIR",
    r"C:\Users\balap\Documents\test_data\northbridge\energy",
)

#: supply number -> (building_code, meter_type, which column carries it, the readings file)
SUPPLIES = {
    "NB-B-101-E0": ("B-101", "electricity", "mpan",
                    "harbour_point_electricity_halfhourly.csv"),
    "NB-B-101-G1": ("B-101", "gas", "mprn",
                    "harbour_point_gas_halfhourly.csv"),
}

TARIFF = {"electricity": "0.21", "gas": "0.062"}
CARBON = {"electricity": "0.207", "gas": "0.183"}


def dsn() -> str:
    """The test DSN. Reads the repo .env when the environment does not carry it."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _env import hoistra_test_dsn
    return hoistra_test_dsn()


async def drop_duplicates(c) -> int:
    """Remove a second row for a supply that already has one, when the second is empty.

    Ordered by reading count then created_at, so the row kept is the one the readings hang off
    and, where neither has any, the one that was there first. A duplicate carrying readings is
    reported and left alone: merging two populated rows is a judgement about which readings are
    real, and that is not a decision to make silently.
    """
    rows = await c.fetch("""
        SELECT m.id, coalesce(m.mpan, m.mprn) AS supply, m.meter_type, b.name AS building,
               (SELECT count(*) FROM plenum_cafm.meter_readings r WHERE r.meter_id = m.id) AS n
          FROM plenum_cafm.energy_meters m
          LEFT JOIN plenum_cafm.buildings b ON b.building_id = m.building_id
         WHERE coalesce(m.mpan, m.mprn) IS NOT NULL
         ORDER BY supply, n DESC, m.created_at
    """)
    seen: dict[str, asyncpg.Record] = {}
    removed = 0
    for r in rows:
        keep = seen.get(r["supply"])
        if keep is None:
            seen[r["supply"]] = r
            continue
        if r["n"]:
            print(f"    {r['supply']:14} duplicate carries {r['n']} readings - LEFT ALONE, "
                  f"decide by hand which is the real supply")
            continue
        await c.execute("DELETE FROM plenum_cafm.energy_meters WHERE id = $1", r["id"])
        removed += 1
        print(f"    {r['supply']:14} removed empty duplicate {str(r['id'])[:8]} "
              f"({r['building']}), kept {str(keep['id'])[:8]} with {keep['n']} readings")
    if not removed:
        print("    no empty duplicates")
    return removed


async def ensure_meters(c) -> dict[str, uuid.UUID]:
    """Every supply in SUPPLIES has exactly one meter afterwards. Returns supply -> id."""
    out: dict[str, uuid.UUID] = {}
    for supply, (bcode, mtype, col, _csv) in SUPPLIES.items():
        found = await c.fetchval(
            "SELECT id FROM plenum_cafm.energy_meters "
            " WHERE lower(coalesce(mpan, mprn)) = lower($1) LIMIT 1", supply)
        if found:
            out[supply] = found
            print(f"    {supply:14} already on record as {str(found)[:8]}")
            continue
        b = await c.fetchrow(
            "SELECT building_id, organization_id FROM plenum_cafm.buildings "
            " WHERE building_code = $1", bcode)
        if b is None:
            print(f"    {supply:14} SKIPPED - no building {bcode}")
            continue
        new_id = await c.fetchval(f"""
            INSERT INTO plenum_cafm.energy_meters
                   (id, organization_id, building_id, meter_type, {col},
                    tariff_gbp_per_kwh, carbon_kg_per_kwh, is_sub_meter, active,
                    building_code, description)
            VALUES (gen_random_uuid(), $1, $2, $3, $4, $5, $6, false, true, $7, $8)
            RETURNING id
        """, b["organization_id"], b["building_id"], mtype, supply,
             TARIFF[mtype], CARBON[mtype], bcode,
             f"{bcode} incoming {mtype} supply")
        out[supply] = new_id
        print(f"    {supply:14} created {str(new_id)[:8]} on {bcode} as the incoming {mtype}")
    return out


async def load_readings(c, meters: dict[str, uuid.UUID]) -> None:
    """Load a half-hourly export, skipping any period the meter already holds.

    The overlap check is per meter and per timestamp rather than a blanket "has readings", so a
    file that extends an existing series adds only its new periods.
    """
    for supply, (_bcode, _mtype, col, fname) in SUPPLIES.items():
        mid = meters.get(supply)
        if mid is None:
            continue
        path = os.path.join(CSV_DIR, fname)
        if not os.path.exists(path):
            print(f"    {supply:14} no file at {path}")
            continue
        org = await c.fetchval(
            "SELECT organization_id FROM plenum_cafm.energy_meters WHERE id = $1", mid)
        have = {r["reading_at"] for r in await c.fetch(
            "SELECT reading_at FROM plenum_cafm.meter_readings WHERE meter_id = $1", mid)}

        records, skipped = [], 0
        with io.open(path, encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                if (row.get(col) or "").strip().lower() != supply.lower():
                    skipped += 1
                    continue
                at = dt.datetime.fromisoformat(row["reading_at"].replace("Z", "+00:00"))
                if at in have:
                    skipped += 1
                    continue
                records.append((uuid.uuid4(), org, mid, at,
                                int(row.get("period_minutes") or 30),
                                row["consumption_kwh"], "dcc"))
        if records:
            await c.copy_records_to_table(
                "meter_readings", schema_name="plenum_cafm", records=records,
                columns=["id", "organization_id", "meter_id", "reading_at",
                         "period_minutes", "consumption_kwh", "source"])
        print(f"    {supply:14} {len(records):>6} readings loaded from {fname}"
              + (f", {skipped} already held or not this meter" if skipped else ""))


async def main() -> None:
    c = await asyncpg.connect(dsn())
    try:
        print("\n  duplicates")
        await drop_duplicates(c)
        print("\n  supplies")
        meters = await ensure_meters(c)
        print("\n  readings")
        await load_readings(c, meters)

        print("\n  register now")
        for r in await c.fetch("""
            SELECT coalesce(m.mpan, m.mprn) AS supply, m.meter_type, b.building_code, b.name,
                   (SELECT count(*) FROM plenum_cafm.meter_readings r
                     WHERE r.meter_id = m.id) AS n
              FROM plenum_cafm.energy_meters m
              LEFT JOIN plenum_cafm.buildings b ON b.building_id = m.building_id
             ORDER BY b.building_code, supply"""):
            print(f"    {r['building_code'] or '-':6} {r['name'] or '(no building)':16} "
                  f"{r['supply']:14} {r['meter_type']:12} {r['n']:>6} readings")
    finally:
        await c.close()


if __name__ == "__main__":
    asyncio.run(main())
