"""One workbook per building that carries the whole estate: assets, energy and maintenance.

The test data has been two files since floor sub-metering arrived — the building workbook, then
the `-floorlevel_submeter` companion — and the order they go in matters, because the companion's
meters resolve against sections and assets the first file writes. Ingesting them the wrong way
round, or forgetting the second, is the most common way a run looks finished and is not.

This merges them into `<workbook>-complete.xlsx`: every sheet of both, the building's rows first
and the floor and asset rows after, so a single ingest walks parents before children within each
sheet as well as across them. Nothing is invented here and nothing is dropped — run
`build_floor_submeters.py` first and this is exactly the two files, in one.

Where a sheet exists in both files with different columns — Energy_Meters gains `section_name`
and `asset_code` in the companion — the columns are unioned and a row that never had the column
carries an empty cell rather than a guess.

What the result exercises, end to end:

  assets       12 assets, their sections, reading bands and readings, value and criticality
  energy       2 incoming meters with a year of half-hours, 34 sub-meters with 90 days,
               placed on 12 floors and on 10 pieces of plant
  maintenance  12 work orders, 64 PPM visits, 12 plans, 4 inspections, 6 vendors, 4 contracts
  compliance   16 certificates across building, asset and vendor scope

Reads the two workbooks only. Writes nothing to any database.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys

import openpyxl

SUFFIX = "-complete"
COMPANION = "-floorlevel_submeter"

DEFAULT_WORKBOOKS = [
    r"C:\Users\balap\Downloads\northbridge_B-101_harbour_point.xlsx",
    r"C:\Users\balap\Downloads\northbridge_B-102_ashgrove_court.xlsx",
]

#: Headers whose spelling is not the destination column's. The writer adds any header the table
#: does not have as a NEW column rather than refusing it, so a near-miss silently splits one fact
#: across two columns and every reader of the canonical one sees NULL. Found by asking the
#: database which page-read columns were empty after an ingest; keyed by sheet because the same
#: fact has a different column name on different tables (a work order has `vendor`, an asset has
#: `vendor_name`).
CANONICAL_HEADERS: dict[str, dict[str, str]] = {
    "Work_Orders": {
        "cost_estimated": "estimated_cost",   # numeric, models/work_order.py:55
        "cost_actual": "actual_cost",         # numeric
        "vendor_name": "vendor",              # varchar, models/work_order.py:65
    },
    # `maintained_by` is not one of the hints reference_link resolves vendor_id from, so every
    # asset arrived with no vendor and the drawer had none to show.
    "Assets": {"maintained_by": "vendor_name"},
}


def canonical(sheet: str, header: list[str]) -> list[str]:
    """`header` under the names the destination table actually has."""
    rename = CANONICAL_HEADERS.get(sheet) or {}
    return [rename.get(c, c) for c in header]


#: The order a single ingest should meet the sheets in: a table that others point at comes
#: before the tables that point at it. The writer sorts its own destinations, but a file whose
#: sheets already read in dependency order is one a person can check by eye.
SHEET_ORDER = [
    "Sites", "Buildings", "Building_Sections", "Vendors", "Vendor_Contracts", "Technicians",
    "Assets", "Asset_Reading_Bands", "Asset_Readings",
    "Maintenance_Plans", "PPM_Visits", "Work_Orders", "Inspections", "Spare_Parts",
    "Energy_Meters", "Meter_Readings",
    "Compliance_Certificates",
]


def read_sheets(path: str) -> dict[str, tuple[list[str], list[list]]]:
    """Every sheet as (header, rows). Read-only, so a 180k-row sheet does not sit in memory
    twice."""
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    out: dict[str, tuple[list[str], list[list]]] = {}
    for ws in wb.worksheets:
        it = ws.iter_rows(values_only=True)
        try:
            header = [str(h) for h in next(it)]
        except StopIteration:
            continue
        out[ws.title] = (canonical(ws.title, header), [list(r) for r in it])
    wb.close()
    return out


def merge(first: tuple[list[str], list[list]] | None,
          second: tuple[list[str], list[list]] | None) -> tuple[list[str], list[list]]:
    """Two versions of one sheet, as one. Columns unioned in first-seen order; a row that never
    carried a column gets an empty cell, which is not the same as a zero or a guess."""
    parts = [p for p in (first, second) if p]
    header: list[str] = []
    for h, _ in parts:
        for col in h:
            if col not in header:
                header.append(col)
    rows: list[list] = []
    for h, rs in parts:
        idx = {c: i for i, c in enumerate(h)}
        for r in rs:
            rows.append([r[idx[c]] if c in idx and idx[c] < len(r) else None for c in header])
    return header, rows


def build(main_path: str) -> str | None:
    companion_path = os.path.splitext(main_path)[0] + COMPANION + ".xlsx"
    if not os.path.exists(companion_path):
        print(f"  {os.path.basename(main_path)}: no companion beside it "
              f"({os.path.basename(companion_path)}) - run build_floor_submeters.py first",
              file=sys.stderr)
        return None

    main = read_sheets(main_path)
    comp = read_sheets(companion_path)

    # Sheet order: the known order first, then anything either file has that is not in it, so a
    # sheet added to the source workbooks later still lands rather than being silently dropped.
    names = [n for n in SHEET_ORDER if n in main or n in comp]
    names += [n for n in list(main) + list(comp) if n not in names]

    out_path = os.path.splitext(main_path)[0] + SUFFIX + ".xlsx"
    try:
        with open(out_path, "ab"):
            pass
    except PermissionError:
        out_path = (os.path.splitext(main_path)[0] + SUFFIX + "-"
                    + dt.datetime.now().strftime("%Y%m%d-%H%M") + ".xlsx")
        print(f"  target is open elsewhere - writing {os.path.basename(out_path)}")

    out = openpyxl.Workbook(write_only=True)
    report = []
    for name in names:
        header, rows = merge(main.get(name), comp.get(name))
        ws = out.create_sheet(name)
        ws.append(header)
        for r in rows:
            ws.append(r)
        report.append((name, len(rows), len(main.get(name, ([], []))[1]),
                       len(comp.get(name, ([], []))[1])))
    out.save(out_path)

    print(f"\n  {os.path.basename(out_path)}")
    print(f"    {len(names)} sheets, {sum(r[1] for r in report):,} rows\n")
    renamed = {s: r for s, r in CANONICAL_HEADERS.items() if s in names}
    if renamed:
        print("    headers renamed to the columns the API reads:")
        for sheet, r in renamed.items():
            print(f"      {sheet}: " + ", ".join(f"{k} -> {v}" for k, v in r.items()))
        print()
    for name, total, a, b in report:
        extra = f"   ({a:,} building + {b:,} floor/asset)" if a and b else ""
        print(f"      {name:26} {total:>7,}{extra}")
    return out_path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("workbooks", nargs="*", default=DEFAULT_WORKBOOKS,
                    help="the building workbooks; each needs its -floorlevel_submeter companion")
    args = ap.parse_args()
    made = []
    for p in args.workbooks:
        if not os.path.exists(p):
            print(f"  missing: {p}", file=sys.stderr)
            continue
        got = build(p)
        if got:
            made.append(got)
    if made:
        print(f"\n  {len(made)} complete workbook(s). Ingest one per building - each is "
              f"self-contained,\n  and clear first: meter_readings has no natural key.")


if __name__ == "__main__":
    main()
