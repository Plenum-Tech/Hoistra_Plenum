"""Add the two things the export cannot produce an Energy page without.

The workbook carried building_code on Energy_Meters, Maintenance_Plans and Building_Sections,
but nothing defined a building. So the one figure the whole Energy page divides by — floor area
— was not in the file at all. Ingest the workbook into an empty database and every EUI reads
"not computable", because consumption has nothing to be per square metre OF.

It also carried no readings. The half-hourly exports lived beside it as four CSVs, so the
energy story needed five files and an order to load them in. Sheets carry them now, so one
workbook is one building's complete record.

Both sheets are written from hoistra_test rather than composed by hand, so what the file says
is what actually loaded and produced the figures on the page.

Areas are given twice on purpose: gross_area_sqft is the column buildings actually stores, and
gross_internal_area_m2 is what a UK facilities manager reads. The writer takes the sqft column;
the m2 is there so nobody has to trust a conversion they cannot see.
"""
from __future__ import annotations

import asyncio
import os
import sys
import shutil
import datetime as dt

import asyncpg
import openpyxl

SRC = os.environ.get(
    "NORTHBRIDGE_WORKBOOK", r"C:\Users\balap\Downloads\northbridge_cmms_export.xlsx")
SQFT_PER_M2 = 10.7639

BUILDINGS_HEADER = [
    "building_code", "name", "site_ref", "country_code", "primary_use", "floors",
    "gross_area_sqft", "gross_internal_area_m2", "eui_kwh_m2", "hoist_score",
]
READINGS_HEADER = [
    "meter_ref", "building_code", "meter_type", "reading_at", "consumption_kwh",
    "period_minutes", "source",
]


def dsn() -> str:
    """The test DSN. Reads the repo .env when the environment does not carry it."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _env import hoistra_test_dsn
    return hoistra_test_dsn()


async def buildings(c) -> list[list]:
    out = []
    for r in await c.fetch("""
        SELECT b.building_code, b.name, b.site_id, l.country_code, b.primary_use, b.floors,
               b.gross_area_sqft::float AS sqft, b.eui_kwh_m2::float AS eui,
               b.hoist_score
          FROM plenum_cafm.buildings b
          LEFT JOIN plenum_cafm.locations l ON (
                l.id::text = b.location_id::text
             OR '00000000-0000-0000-0000-' || lpad(l.id::text, 12, '0') = b.location_id::text)
         ORDER BY b.building_code
    """):
        sqft = r["sqft"]
        out.append([
            r["building_code"], r["name"],
            r["site_id"] or ("S-" + (r["building_code"] or "").split("-")[-1]),
            r["country_code"], r["primary_use"], r["floors"],
            round(sqft, 2) if sqft else None,
            round(sqft / SQFT_PER_M2, 2) if sqft else None,
            r["eui"], r["hoist_score"],
        ])
    return out


async def readings(c) -> list[list]:
    rows = await c.fetch("""
        SELECT coalesce(m.mpan, m.mprn) AS meter_ref, b.building_code, m.meter_type,
               r.reading_at, r.consumption_kwh::float AS kwh, r.period_minutes, r.source
          FROM plenum_cafm.meter_readings r
          JOIN plenum_cafm.energy_meters m ON m.id = r.meter_id
          JOIN plenum_cafm.buildings b ON b.building_id = m.building_id
         ORDER BY b.building_code, meter_ref, r.reading_at
    """)
    return [[r["meter_ref"], r["building_code"], r["meter_type"],
             # Written as text, not as a datetime: Excel renders a timezone-aware cell in the
             # reader's own zone, and a half-hourly series read an hour out is a fault the
             # anomaly rules would then find in the data rather than in the spreadsheet.
             r["reading_at"].strftime("%Y-%m-%dT%H:%M:%SZ"),
             round(r["kwh"], 3), r["period_minutes"], r["source"]] for r in rows]


def replace_sheet(wb, name: str, header: list[str], rows: list[list], after: str | None = None):
    """Rewrite a sheet from scratch, keeping its place in the tab order where it had one."""
    index = wb.sheetnames.index(name) if name in wb.sheetnames else None
    if name in wb.sheetnames:
        del wb[name]
    ws = wb.create_sheet(name, index)
    ws.append(header)
    for r in rows:
        ws.append(r)
    if index is None and after and after in wb.sheetnames:
        wb.move_sheet(name, offset=wb.sheetnames.index(after) - wb.sheetnames.index(name) + 1)
    return ws


async def main() -> None:
    if not os.path.exists(SRC):
        raise SystemExit(f"workbook not found: {SRC}")

    c = await asyncpg.connect(dsn())
    try:
        blds = await buildings(c)
        rdgs = await readings(c)
    finally:
        await c.close()

    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = SRC.replace(".xlsx", f".before-buildings-readings-{stamp}.xlsx")
    shutil.copy2(SRC, backup)
    print(f"  backup  {os.path.basename(backup)}")

    wb = openpyxl.load_workbook(SRC)
    replace_sheet(wb, "Buildings", BUILDINGS_HEADER, blds, after="Sites")
    replace_sheet(wb, "Meter_Readings", READINGS_HEADER, rdgs, after="Energy_Meters")

    try:
        wb.save(SRC)
    except PermissionError:
        alt = SRC.replace(".xlsx", f"-updated-{stamp}.xlsx")
        wb.save(alt)
        print(f"\n  {os.path.basename(SRC)} is open in Excel - written to "
              f"{os.path.basename(alt)} instead")
        return

    print(f"\n  Buildings        {len(blds):>6} rows   {', '.join(BUILDINGS_HEADER[:6])} ...")
    for b in blds:
        print(f"    {b[0]}  {b[1]:16} {b[6]:>12,.0f} sqft = {b[7]:>9,.0f} m2  "
              f"{b[5]} floors  {b[3]}")
    print(f"\n  Meter_Readings   {len(rdgs):>6} rows")
    wb2 = openpyxl.load_workbook(SRC, read_only=True)
    print(f"\n  {os.path.basename(SRC)}  ->  {len(wb2.sheetnames)} sheets, "
          f"{os.path.getsize(SRC)/1024/1024:.1f} MB")
    for n in wb2.sheetnames:
        print(f"    {n}")


if __name__ == "__main__":
    asyncio.run(main())
