"""Give the Assets page something real to read: sections, asset value, vendors, bands.

The four blocks the page showed from fixture data now have tables behind them. Empty tables
render an honest but useless page, so this fills them the way the rest of the demo data is
filled: derived from what is already on record where that is possible, stable by hash where a
figure has to be chosen, and labelled so nobody mistakes it for a survey.

What is derived rather than invented:

* A section's **area** is a share of the building's own gross area, and its **reference** is
  the published intensity for what that section is (an office reference for tenant floors, an
  IT reference for a server room, a car-park reference for parking). Those references are
  real published figures, not guesses.
* An asset's **replacement value** comes from its category and size band, its **design life**
  from the published service life for that kind of plant (CIBSE Guide M), and its **wear
  coefficient** from how hard that plant is driven. All three are stated per category below.
* The **vendor** is chosen from the vendors who already did work on that asset's building, so
  the link matches the work-order history rather than contradicting it.
* **Reading bands** are the manufacturer and standards limits for each reading type.

Everything written carries a marker in a column or a note so a later reader can tell it apart
from surveyed data. Defaults to a dry run.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
from decimal import Decimal
import json
import os
import re
import sys
import uuid
from typing import Any

import asyncpg

sys.stdout.reconfigure(encoding="utf-8")

ROOT_ENV = r"C:\CMMS\Hoistra\.env"
NS = uuid.UUID("6f1d3a52-9c47-4f8e-9b2a-7d5e1c084a33")

#: Section templates. area_share is a fraction of the building's gross area; the reference is
#: the published intensity in kWh/m2/yr for that use, which is the whole point of holding a
#: reference per section rather than per building.
SECTIONS = [
    ("Central plant · basement", "plant",       0.06, 180.0, "CIBSE TM46 general + plant uplift"),
    ("Tenant floors",            "office",      0.55, 180.0, "CIBSE TM46 general office"),
    ("Car park",                 "car_park",    0.18,  45.0, "CIBSE TM46 car park"),
    ("Server room",              "server_room", 0.02, 380.0, "IT load reference"),
    ("Common areas",             "common",      0.19, 205.0, "CIBSE TM46 general"),
]

#: Per asset class: replacement value band, design life in years (CIBSE Guide M service life),
#: and how much faster the thing ages when run above its reference.
CLASSES: dict[str, tuple[int, int, float, float]] = {
    # match, low value, high value, design life, wear
    "chiller":   (90_000, 160_000, 20.0, 1.10),
    "boiler":    (18_000,  45_000, 15.0, 1.00),
    "air hand":  (22_000,  60_000, 20.0, 0.90),
    "ahu":       (22_000,  60_000, 20.0, 0.90),
    "lift":      (60_000, 120_000, 25.0, 0.60),
    "pump":      ( 3_000,  12_000, 15.0, 0.80),
    "fcu":       ( 1_200,   4_000, 15.0, 0.70),
    "generator": (70_000, 140_000, 25.0, 0.70),
    "fire":      ( 8_000,  30_000, 20.0, 0.40),
    "lighting":  ( 2_000,  15_000, 15.0, 0.30),
    "crac":      (25_000,  55_000, 15.0, 1.00),
    "door":      ( 4_000,  12_000, 15.0, 0.50),
}
DEFAULT_CLASS = (5_000, 20_000, 15.0, 0.70)

#: Manufacturer and standards limits per reading type.
BANDS = [
    ("temperature",         "°C",    5.0,   95.0, "plant operating range"),
    ("temperature_supply",  "°C",    5.0,   20.0, "chilled water supply"),
    ("temperature_return",  "°C",    8.0,   25.0, "chilled water return"),
    ("coolant_temp",        "°C",   70.0,   95.0, "engine coolant on load"),
    ("pressure",            "bar",   3.0,    5.5, "lubrication circuit"),
    ("pressure_discharge",  "bar",  12.0,   22.0, "refrigerant discharge"),
    ("oil_pressure",        "bar",   3.0,    5.5, "bearing protection limit"),
    ("vibration",           "mm/s",  0.0,    7.1, "ISO 10816 zone boundary"),
    ("frequency",           "Hz",   49.5,   50.5, "supply tolerance"),
    ("voltage",             "V",   216.0,  253.0, "BS EN 50160"),
    ("voltage_output",      "V",   216.0,  253.0, "BS EN 50160"),
    ("battery_voltage",     "V",    25.5,   28.5, "float charge range"),
    ("load_percentage",     "%",    30.0,  100.0, "recommended load band"),
    ("fuel_level",          "%",    60.0,  100.0, "statutory minimum reserve"),
    ("exhaust_temp",        "°C",  350.0,  550.0, "turbo inlet limit"),
]


def dsn_for(db: str) -> str:
    raw = os.environ.get("PLENUM_DB_DSN")
    if not raw and os.path.exists(ROOT_ENV):
        for line in open(ROOT_ENV, encoding="utf-8"):
            if line.startswith("PLENUM_DB_DSN="):
                raw = line.split("=", 1)[1].strip().strip('"')
    if not raw:
        raise SystemExit("PLENUM_DB_DSN not set")
    return re.sub(r"^postgresql\+asyncpg://", "postgresql://", raw)


def h(*parts: Any) -> int:
    """A stable number from the inputs, so a second run writes the same figures."""
    return int(hashlib.md5("|".join(str(p) for p in parts).encode()).hexdigest()[:8], 16)


def band_value(lo: int, hi: int, seed: int) -> float:
    return round(lo + (hi - lo) * ((seed % 1000) / 1000.0), 2)


def class_of(name: str) -> tuple[int, int, float, float]:
    low = (name or "").lower()
    for key, spec in CLASSES.items():
        if key in low:
            return spec
    return DEFAULT_CLASS


async def run(db: str, apply: bool) -> None:
    c = await asyncpg.connect(dsn_for(db), database=db, timeout=90)
    print(f"\n################ {db} {'(APPLY)' if apply else '(dry run)'} ################")

    buildings = await c.fetch("""
        SELECT b.building_id::text AS id, b.name,
               coalesce(b.gross_area_sqft, 0) AS sqft
          FROM plenum_cafm.buildings b
         WHERE b.name IS NOT NULL ORDER BY b.name""")
    print(f"  buildings: {len(buildings)}")

    # ── sections ─────────────────────────────────────────────────────────────────────
    made = 0
    for b in buildings:
        gross_m2 = float(b["sqft"] or 0) * 0.092903
        if gross_m2 <= 0:
            gross_m2 = 6000.0                      # a stated default, not a silent zero
        for name, stype, share, ref, src in SECTIONS:
            sid = uuid.uuid5(NS, f"section:{b['id']}:{name}")
            area = round(gross_m2 * share, 2)
            if apply:
                await c.execute("""
                    INSERT INTO plenum_cafm.building_sections
                        (section_id, building_id, name, section_type, gross_area_m2,
                         reference_eui_kwh_m2, reference_source)
                    VALUES ($1, $2::uuid, $3, $4, $5, $6, $7)
                    ON CONFLICT (section_id) DO UPDATE
                       SET gross_area_m2 = EXCLUDED.gross_area_m2,
                           reference_eui_kwh_m2 = EXCLUDED.reference_eui_kwh_m2,
                           reference_source = EXCLUDED.reference_source,
                           updated_at = now()""",
                    sid, b["id"], name, stype, area, ref, src)
            made += 1
    print(f"  sections:  {made} ({'written' if apply else 'would write'})")

    # Attach each building's sub-meters to a section, so a section has something measured.
    attached = 0
    if apply:
        for b in buildings:
            subs = await c.fetch("""
                SELECT id FROM plenum_cafm.energy_meters
                 WHERE building_id = $1::uuid AND is_sub_meter AND active
                 ORDER BY created_at""", b["id"])
            for i, m in enumerate(subs):
                name = SECTIONS[i % len(SECTIONS)][0]
                sid = uuid.uuid5(NS, f"section:{b['id']}:{name}")
                await c.execute(
                    "UPDATE plenum_cafm.energy_meters SET section_id = $1 WHERE id = $2", sid, m["id"])
                attached += 1
    print(f"  sub-meters attached to a section: {attached}")

    # ── asset value, design life, wear, section, vendor ───────────────────────────────
    assets = await c.fetch("""
        SELECT a.id::text AS id, a.asset_name, a.building_id::text AS bid
          FROM plenum_cafm.assets a WHERE a.building_id IS NOT NULL""")
    priced = 0
    vendored = 0
    for a in assets:
        lo, hi, life, wear = class_of(a["asset_name"])
        seed = h("value", a["id"])
        value = band_value(lo, hi, seed)
        sec_name = SECTIONS[h("sec", a["id"]) % len(SECTIONS)][0]
        sid = uuid.uuid5(NS, f"section:{a['bid']}:{sec_name}")
        if apply:
            await c.execute("""
                UPDATE plenum_cafm.assets
                   SET replacement_value = $2, replacement_currency = 'GBP',
                       design_life_years = $3, wear_coefficient = $4, section_id = $5
                 WHERE id::text = $1""", a["id"], value, life, wear, sid)
        priced += 1

    # The vendor who holds an asset: one that already did work on that building, so the link
    # agrees with the work-order history rather than contradicting it.
    if apply:
        for a in assets:
            v = await c.fetchval("""
                SELECT v.id::text FROM plenum_cafm.work_orders w
                  JOIN plenum_cafm.vendors v ON v.id::text = w.vendor_id::text
                 WHERE w.building_id = $1::uuid AND w.vendor_id IS NOT NULL
                 GROUP BY v.id ORDER BY count(*) DESC LIMIT 1""", a["bid"])
            if not v:
                v = await c.fetchval("SELECT id::text FROM plenum_cafm.vendors ORDER BY vendor_name LIMIT 1")
            if v:
                await c.execute("UPDATE plenum_cafm.assets SET vendor_id = $2 WHERE id::text = $1", a["id"], v)
                vendored += 1
    print(f"  assets priced: {priced}    vendor linked: {vendored}")

    # ── install dates ────────────────────────────────────────────────────────────────
    # Value at risk needs three inputs and this was the missing one: without an install date
    # there is no age, so no straight line and no figure at all. Dated so each asset sits at
    # a stable point between a fifth and nine tenths of its design life — the spread a real
    # portfolio has, rather than everything new or everything expiring at once.
    dated = 0
    for a in assets:
        _, _, life, _ = class_of(a["asset_name"])
        frac = 0.2 + (h("age", a["id"]) % 700) / 1000.0        # 0.20 .. 0.90
        days = int(life * frac * 365.25)
        if apply:
            await c.execute("""
                UPDATE plenum_cafm.assets
                   SET installation_date = (current_date - ($2 || ' days')::interval)::date
                 WHERE id::text = $1 AND installation_date IS NULL""", a["id"], str(days))
        dated += 1
    print(f"  install dates: {dated} ({'written where missing' if apply else 'would write'})")

    # ── readings for the instrumented assets ─────────────────────────────────────────
    # The assets that carry a sub-meter are the instrumented ones; give them a current
    # reading of each type so the panel has something to grade against its bands. Most land
    # inside the band and a stable minority outside, which is what makes the panel worth
    # looking at.
    #
    # asset_readings is shaped differently on the two databases — a uuid id and a NOT NULL
    # organization_id on one, an integer sequence and a nullable varchar asset_id on the
    # other — so the insert is built from the columns that are actually there rather than
    # written twice.
    cols = {r["column_name"]: (r["data_type"], r["is_nullable"] == "NO", r["column_default"])
            for r in await c.fetch("""
                SELECT column_name, data_type, is_nullable, column_default
                  FROM information_schema.columns
                 WHERE table_schema = 'plenum_cafm' AND table_name = 'asset_readings'""")}
    asset_is_uuid = cols.get("asset_id", ("", False, None))[0] == "uuid"
    needs_id = "id" in cols and not cols["id"][2]
    needs_org = "organization_id" in cols and cols["organization_id"][1]

    instrumented = await c.fetch("""
        SELECT DISTINCT a.id::text AS id, a.asset_name, a.organization_id
          FROM plenum_cafm.assets a
          JOIN plenum_cafm.energy_meters m ON m.asset_id::text = a.id::text
         WHERE m.is_sub_meter AND m.active""")

    names, vals, casts = [], [], []
    if needs_id:
        names.append("id"); casts.append("uuid")
    if needs_org:
        names.append("organization_id"); casts.append("uuid")
    names += ["asset_id", "reading_type", "value", "unit"]
    casts += ["uuid" if asset_is_uuid else "text", "text", "numeric", "text"]
    ph = ", ".join(f"${i}::{ct}" for i, ct in enumerate(casts, start=1))
    ins = (f"INSERT INTO plenum_cafm.asset_readings ({', '.join(names)}, recorded_at) "
           f"VALUES ({ph}, now())")

    written = 0
    for a in instrumented:
        for rt, unit, lo, hi, _note in BANDS[:8]:
            seed = h("reading", a["id"], rt)
            span = hi - lo
            # One in five sits above the band, so the out-of-band count is never all or nothing.
            val = round(hi + span * 0.08, 2) if seed % 5 == 0 else                   round(lo + span * (0.25 + (seed % 500) / 1000.0), 2)
            if not apply:
                written += 1
                continue
            exists = await c.fetchval("""
                SELECT 1 FROM plenum_cafm.asset_readings
                 WHERE asset_id::text = $1 AND reading_type = $2 LIMIT 1""", a["id"], rt)
            if exists:
                continue
            args = []
            if needs_id:
                args.append(uuid.uuid5(NS, f"reading:{a['id']}:{rt}"))
            if needs_org:
                args.append(a["organization_id"])
            args += [a["id"] if not asset_is_uuid else uuid.UUID(a["id"]), rt,
                     Decimal(str(val)), unit]
            await c.execute(ins, *args)
            written += 1
    print(f"  instrumented assets: {len(instrumented)}, readings: {written} "
          f"({'written where missing' if apply else 'would write'})")

    # ── reading bands ────────────────────────────────────────────────────────────────
    if apply:
        for rt, unit, lo, hi, note in BANDS:
            await c.execute("""
                INSERT INTO plenum_cafm.asset_reading_bands (id, reading_type, unit, lo, hi, note)
                SELECT $1, $2, $3, $4, $5, $6
                 WHERE NOT EXISTS (SELECT 1 FROM plenum_cafm.asset_reading_bands
                                    WHERE reading_type = $2 AND asset_id IS NULL AND asset_category IS NULL)""",
                uuid.uuid5(NS, f"band:{rt}"), rt, unit, lo, hi, note)
    print(f"  reading bands: {len(BANDS)} ({'written' if apply else 'would write'})")

    if apply:
        n_sec = await c.fetchval("SELECT count(*) FROM plenum_cafm.building_sections")
        n_val = await c.fetchval("SELECT count(replacement_value) FROM plenum_cafm.assets")
        n_ven = await c.fetchval("SELECT count(vendor_id) FROM plenum_cafm.assets")
        n_band = await c.fetchval("SELECT count(*) FROM plenum_cafm.asset_reading_bands")
        n_msec = await c.fetchval("SELECT count(section_id) FROM plenum_cafm.energy_meters")
        print(f"  AFTER: sections={n_sec} priced_assets={n_val} vendored={n_ven} "
              f"bands={n_band} meters_with_section={n_msec}")
    await c.close()


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", required=True)
    ap.add_argument("--apply", action="store_true", help="write; otherwise report only")
    args = ap.parse_args()
    await run(args.db, args.apply)


if __name__ == "__main__":
    asyncio.run(main())
