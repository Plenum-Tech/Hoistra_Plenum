"""Correct what the seeded energy data got wrong, against the workbook as the record.

Two faults, both found by working the EUI arithmetic by hand and finding it disagreed with
what was stored.

1. Ashgrove Court's meters were seeded at GBP0.28/kWh on BOTH fuels. Gas does not cost 28p —
   the UK commercial band is 5.5p to 7.0p, and the workbook records 6.2p. A gas supply priced
   at the electricity rate overstates what a gap costs by a factor of four and a half, and
   since the benchmark card now prices excess at the contracted rate, that error goes straight
   to the money on the page. The workbook is the record; the meters are conformed to it.

2. Ashgrove holds 17,022 electricity and 17,018 gas readings where a year of half-hourly data
   is 17,520. The half-hourly exports have all 17,520 of each, so roughly 500 periods per meter
   were never loaded. That is not cosmetic either: the window is now measured in metered time,
   so a meter missing 500 half-hours reads as 11.65 months and its consumption is annualised
   upward to compensate. Ashgrove's EUI moved 173.20 -> 178.40 on that alone.

Safe to run more than once. A tariff is only written when it differs from the workbook, and a
reading is only loaded for a period the meter does not already hold.
"""
from __future__ import annotations

import asyncio
import csv
import datetime as dt
import io
import os
import uuid

import asyncpg
import openpyxl

WORKBOOK = os.environ.get(
    "NORTHBRIDGE_WORKBOOK", r"C:\Users\balap\Downloads\northbridge_cmms_export.xlsx")
CSV_DIR = os.environ.get(
    "NORTHBRIDGE_ENERGY_DIR", r"C:\Users\balap\Documents\test_data\northbridge\energy")

#: every half-hourly export, and the column its supply number is in
READING_FILES = [
    ("harbour_point_electricity_halfhourly.csv", "mpan"),
    ("harbour_point_gas_halfhourly.csv", "mprn"),
    ("ashgrove_court_electricity_halfhourly.csv", "mpan"),
    ("ashgrove_court_gas_halfhourly.csv", "mprn"),
]


def dsn() -> str:
    raw = os.environ.get("HOISTRA_TEST_DSN")
    if not raw:
        raise SystemExit("HOISTRA_TEST_DSN is not set; source the repo .env first")
    return raw.replace("+asyncpg", "")


def workbook_meters() -> dict[str, dict]:
    """supply number -> the row the workbook holds for it."""
    wb = openpyxl.load_workbook(WORKBOOK, read_only=True, data_only=True)
    ws = wb["Energy_Meters"]
    rows = list(ws.iter_rows(values_only=True))
    hdr = [str(h) if h is not None else "" for h in rows[0]]
    out = {}
    for r in rows[1:]:
        d = dict(zip(hdr, r))
        supply = (d.get("mpan") or d.get("mprn") or d.get("meter_ref") or "").strip()
        if supply:
            out[supply.lower()] = d
    return out


async def conform_tariffs(c) -> int:
    """Every meter charges what the workbook says it charges."""
    book = workbook_meters()
    fixed = 0
    for r in await c.fetch("""
        SELECT m.id, coalesce(m.mpan, m.mprn) AS supply, m.meter_type,
               m.tariff_gbp_per_kwh::float AS tariff, m.carbon_kg_per_kwh::float AS carbon,
               b.name AS building
          FROM plenum_cafm.energy_meters m
          LEFT JOIN plenum_cafm.buildings b ON b.building_id = m.building_id
         WHERE coalesce(m.mpan, m.mprn) IS NOT NULL
         ORDER BY b.name, supply
    """):
        want = book.get((r["supply"] or "").lower())
        if not want:
            print(f"    {r['supply']:14} not in the workbook - left as it is")
            continue
        tariff, carbon = float(want["tariff_gbp_per_kwh"]), float(want["carbon_kg_per_kwh"])
        if abs(r["tariff"] - tariff) < 1e-9 and abs(r["carbon"] - carbon) < 1e-9:
            print(f"    {r['supply']:14} {r['meter_type']:12} already {tariff} GBP/kWh")
            continue
        await c.execute(
            "UPDATE plenum_cafm.energy_meters "
            "   SET tariff_gbp_per_kwh = $1, carbon_kg_per_kwh = $2, updated_at = now() "
            " WHERE id = $3", tariff, carbon, r["id"])
        fixed += 1
        print(f"    {r['supply']:14} {r['meter_type']:12} {r['tariff']} -> {tariff} GBP/kWh"
              f"   ({r['building']})")
    return fixed


async def fill_reading_gaps(c) -> int:
    """Load every period an export holds that its meter does not."""
    loaded = 0
    for fname, col in READING_FILES:
        path = os.path.join(CSV_DIR, fname)
        if not os.path.exists(path):
            print(f"    {fname}: not found at {CSV_DIR}")
            continue
        with io.open(path, encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
        if not rows:
            continue
        supply = (rows[0].get(col) or "").strip()
        m = await c.fetchrow(
            "SELECT id, organization_id FROM plenum_cafm.energy_meters "
            " WHERE lower(coalesce(mpan, mprn)) = lower($1) LIMIT 1", supply)
        if m is None:
            print(f"    {supply:14} no meter on record - skipped")
            continue
        have = {r["reading_at"] for r in await c.fetch(
            "SELECT reading_at FROM plenum_cafm.meter_readings WHERE meter_id = $1", m["id"])}

        records = []
        for row in rows:
            if (row.get(col) or "").strip().lower() != supply.lower():
                continue
            at = dt.datetime.fromisoformat(row["reading_at"].replace("Z", "+00:00"))
            if at in have:
                continue
            records.append((uuid.uuid4(), m["organization_id"], m["id"], at,
                            int(row.get("period_minutes") or 30),
                            row["consumption_kwh"], "dcc"))
        if records:
            await c.copy_records_to_table(
                "meter_readings", schema_name="plenum_cafm", records=records,
                columns=["id", "organization_id", "meter_id", "reading_at",
                         "period_minutes", "consumption_kwh", "source"])
            loaded += len(records)
        print(f"    {supply:14} held {len(have):>6}, file has {len(rows):>6}, "
              f"loaded {len(records):>5} missing period(s)")
    return loaded


async def main() -> None:
    c = await asyncpg.connect(dsn())
    try:
        print("\n  tariffs, against the workbook")
        n = await conform_tariffs(c)
        print(f"    {n} corrected")

        print("\n  reading gaps")
        n = await fill_reading_gaps(c)
        print(f"    {n} readings loaded")

        print("\n  where that leaves each meter")
        for r in await c.fetch("""
            SELECT b.building_code, b.name, coalesce(m.mpan, m.mprn) AS supply, m.meter_type,
                   m.tariff_gbp_per_kwh::float AS tariff,
                   count(r.id) AS n,
                   round(sum(coalesce(r.period_minutes, 30))::numeric / 60 / 24, 1) AS days
              FROM plenum_cafm.energy_meters m
              JOIN plenum_cafm.buildings b ON b.building_id = m.building_id
              LEFT JOIN plenum_cafm.meter_readings r ON r.meter_id = m.id
             GROUP BY 1, 2, 3, 4, 5 ORDER BY 1, 3"""):
            print(f"    {r['building_code']}  {r['name']:16} {r['supply']:14} "
                  f"{r['meter_type']:12} {r['tariff']:>6} GBP/kWh  "
                  f"{r['n']:>6} readings = {r['days']} days")
    finally:
        await c.close()


if __name__ == "__main__":
    asyncio.run(main())
