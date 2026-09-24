"""Check a test workbook's links before it is ingested, not after.

Every fault this dataset has hit was a broken link that the ingest reported as success: a meter
naming a section that did not exist, a section naming a floor by a name no floor had, an EPC
whose band sat in the wrong column, a work order with no title. The writer resolves what it can
and drops what it cannot, and it says "0 skipped" either way, so the first sign is a page that
reads empty hours later.

This is the §4 checklist from docs/test-data/GENERATE_BUILDING_WORKBOOK.md, run. Most of it is
internal — does this sheet's reference land in that sheet — and three checks need the database,
because a technician's login and a section's floor are rows only it has. Pass `--offline` to
skip those.

A failed line is a failed workbook, not a warning: exits non-zero so a build script can stop.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys

import openpyxl

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

RRN = re.compile(r"^\d{4}-\d{4}-\d{4}-\d{4}-\d{4}$")
BANDS = set("ABCDEFG")


def load(path: str) -> dict[str, list[dict]]:
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    out: dict[str, list[dict]] = {}
    for ws in wb.worksheets:
        it = ws.iter_rows(values_only=True)
        try:
            header = [str(h) for h in next(it)]
        except StopIteration:
            continue
        out[ws.title] = [dict(zip(header, r)) for r in it]
    wb.close()
    return out


class Checks:
    def __init__(self) -> None:
        self.failed = 0
        self.passed = 0

    def __call__(self, ok: bool, label: str, detail: str = "") -> None:
        if ok:
            self.passed += 1
            print(f"    ok   {label}")
        else:
            self.failed += 1
            print(f"    FAIL {label}" + (f"\n           {detail}" if detail else ""))

    def refs(self, rows: list[dict], col: str, into: set, label: str, *,
             required: bool = True) -> None:
        """Every non-empty value of `col` lands in `into`."""
        bad = sorted({str(r[col]) for r in rows
                      if r.get(col) not in (None, "") and str(r[col]) not in into})
        missing = [r for r in rows if r.get(col) in (None, "")] if required else []
        self(not bad and not missing, label,
             (f"{len(bad)} unresolved: {', '.join(bad[:5])}" if bad else "")
             + (f"  {len(missing)} row(s) with no value" if missing else ""))


def col(rows: list[dict], name: str) -> set:
    return {str(r[name]) for r in rows if r.get(name) not in (None, "")}


def run(path: str, db: dict | None) -> int:
    s = load(path)
    c = Checks()
    g = s.get
    print(f"\n  {os.path.basename(path)} - {len(s)} sheets\n")

    assets, vendors = g("Assets", []), g("Vendors", [])
    sections, meters = g("Building_Sections", []), g("Energy_Meters", [])
    contracts = g("Vendor_Contracts", [])
    vnames, acodes = col(vendors, "vendor_name"), col(assets, "asset_code")
    snames = col(sections, "name")

    c.refs(assets, "vendor_name", vnames, "every asset's vendor is a vendor on file")
    c.refs(assets, "section_name", snames, "every asset sits in a section this file defines")

    wos = g("Work_Orders", [])
    c.refs(wos, "asset_code", acodes, "every work order names an asset")
    c.refs(wos, "vendor", vnames, "every work order names a vendor")
    c(all(str(w.get("title") or "").strip() for w in wos),
      "every work order has a title",
      f"{sum(1 for w in wos if not str(w.get('title') or '').strip())} without one")

    ppm = g("PPM_Visits", [])
    c.refs(ppm, "asset_code", acodes, "every PPM visit names an asset")
    c.refs(ppm, "contract_name", col(contracts, "contract_name"),
           "every PPM visit names a contract this file defines")
    c.refs(g("Maintenance_Plans", []), "asset_code", acodes, "every maintenance plan names an asset")

    mrefs = col(meters, "meter_ref")
    readings = g("Meter_Readings", [])
    c.refs(readings, "meter_ref", mrefs, "every reading names a meter this file defines")
    per = {}
    for r in readings:
        per[str(r.get("meter_ref"))] = per.get(str(r.get("meter_ref")), 0) + 1
    c(len(set(per.values())) <= 2, "readings per meter are consistent",
      f"counts seen: {sorted(set(per.values()))}")

    # A sub-meter sits somewhere: on a section of this building, or on one of its assets.
    placed = [m for m in meters if str(m.get("is_sub_meter")).lower() in ("true", "1", "yes")]
    unplaced = [m for m in placed
                if not m.get("section_name") and not m.get("asset_code")]
    c(not unplaced, "every sub-meter names a section or an asset",
      f"{len(unplaced)}: {', '.join(str(m.get('meter_ref')) for m in unplaced[:5])}")
    c.refs([m for m in meters if m.get("section_name")], "section_name", snames,
           "every sub-meter's section is one this file defines")
    c.refs([m for m in meters if m.get("asset_code")], "asset_code", acodes,
           "every asset sub-meter names an asset")

    c.refs(g("Asset_Readings", []), "reading_type", col(g("Asset_Reading_Bands", []), "reading_type"),
           "every asset reading has a band to be judged against")

    certs = g("Compliance_Certificates", [])
    c.refs([x for x in certs if x.get("vendor_code")], "vendor_code", col(vendors, "vendor_code"),
           "every vendor-scope certificate names a vendor")
    c.refs([x for x in certs if x.get("asset_code")], "asset_code", acodes,
           "every asset-scope certificate names an asset")
    epcs = [x for x in certs if str(x.get("certificate_type_code")).upper() == "EPC"]
    c(all(str(x.get("energy_rating") or "").upper() in BANDS for x in epcs) and epcs,
      "every EPC carries a band A-G in energy_rating",
      f"{len(epcs)} EPC row(s)")
    c(all(RRN.match(str(x.get("certificate_number") or "")) for x in epcs),
      "every EPC's certificate number is a 20-digit RRN")

    ccs = {str(r[k]) for sheet in ("Buildings", "Compliance_Certificates", "Vendor_Contracts")
           for r in g(sheet, []) for k in ("country_code",) if r.get(k)}
    c(ccs <= {"UK"}, "every country code is UK, the code the packs are keyed on", f"saw {ccs}")

    b = (g("Buildings", []) or [{}])[0]
    gia = float(b.get("gross_internal_area_m2") or 0)
    ssum = sum(float(x.get("gross_area_m2") or 0) for x in sections)
    # The floor sections re-describe the whole building, so they alone should total the GIA;
    # the building's own five sections are a second, overlapping description of the same area.
    floors_only = [x for x in sections if str(x.get("name")) == str(x.get("floor_name"))]
    fsum = sum(float(x.get("gross_area_m2") or 0) for x in floors_only)
    c(abs(fsum - gia) < max(50.0, gia * 0.02) if floors_only else abs(ssum - gia) < gia * 0.02,
      "the floor sections total the building's floor area",
      f"floors {fsum:,.0f} m2 vs GIA {gia:,.0f} m2")

    if db is not None:
        c(all(str(t.get("user_id")) in db["users"] for t in g("Technicians", [])),
          "every technician's user_id is a real login in this database")
        code = str(b.get("building_code"))
        want = {str(x.get("floor_name")) for x in sections if x.get("floor_name")}
        have = db["floors"].get(code, set())
        c(want <= have, "every section names a floor this building has",
          f"missing from the database: {sorted(want - have)}")
        c(code in db["buildings"], f"building {code} is on file")

    print(f"\n  {c.passed} passed, {c.failed} failed")
    return c.failed


async def db_facts() -> dict:
    from _env import hoistra_test_dsn
    import asyncpg
    conn = await asyncpg.connect(hoistra_test_dsn(), timeout=15)
    try:
        users = {str(r["id"]) for r in await conn.fetch("SELECT id FROM plenum_cafm.users")}
        buildings = {r["building_code"] for r in
                     await conn.fetch("SELECT building_code FROM plenum_cafm.buildings")}
        floors: dict[str, set] = {}
        for r in await conn.fetch("""SELECT b.building_code bc, f.name FROM plenum_cafm.floors f
                                     JOIN plenum_cafm.buildings b ON b.building_id=f.building_id"""):
            floors.setdefault(r["bc"], set()).add(r["name"])
        return {"users": users, "buildings": buildings, "floors": floors}
    finally:
        await conn.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("workbooks", nargs="+")
    ap.add_argument("--offline", action="store_true", help="skip the checks that need the database")
    args = ap.parse_args()
    db = None if args.offline else asyncio.run(db_facts())
    failed = sum(run(p, db) for p in args.workbooks)
    if failed:
        raise SystemExit(f"\n  {failed} check(s) failed - this workbook is not ready to ingest")
    print("\n  every check passed")


if __name__ == "__main__":
    main()
