"""The asset register behind the Assets page: floors, sections, assets, vendors, bands.

seed_asset_intelligence.py prices assets that already exist. hoistra_test has none — two
buildings, four meters and nothing else — so there was nothing for it to price. This writes
the register itself, shaped so every panel on the Assets page has a table behind it:

* **floors** and **building_sections**, because the page groups assets under a section and
  reads a section's own reference rather than the building's. A section that genuinely sits
  on one floor carries a floor_id; one that spans the building carries NULL, which is the
  truth rather than a guess.
* **assets** with the five columns the arithmetic needs — replacement value, design life,
  wear coefficient, install date and condition grade. Miss any one and value-at-risk reports
  "not computable" instead of a figure, which is correct and useless for a demo.
* **vendors**, because an asset with no vendor renders "Open the asset to read its vendor"
  and then has nothing to show.
* **asset_reading_bands**, global rows only. bands_for() binds its category parameter to
  assets.criticality rather than to a category name, so a category-scoped band would only
  match an asset whose criticality is literally "chiller". Global rows and per-asset rows are
  the two that work.

Readings are NOT written here. They are live data and they move; refresh_asset_readings.py
owns them.

Everything is keyed by uuid5, so a second run updates rather than duplicates. Defaults to a
dry run. Refuses to touch plenum_agent.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
import uuid
from decimal import Decimal

import asyncpg

sys.stdout.reconfigure(encoding="utf-8")

ROOT_ENV = r"C:\CMMS\Hoistra\.env"
NS = uuid.UUID("6f1d3a52-9c47-4f8e-9b2a-7d5e1c084a33")   # same namespace as the sibling seeder
PRODUCTION = "plenum_agent"
SQFT_TO_M2 = 0.092903

#: Sections per primary use. share is a fraction of the building's gross area; the reference
#: is the published intensity in kWh/m2/yr for that use. The last field names the floor a
#: section sits on, or None where it spans the building.
SECTIONS_BY_USE = {
    "Commercial": [
        ("Central plant \u00b7 basement", "plant", 0.06, 180.0, "CIBSE TM46 general + plant uplift", "Basement"),
        ("Tenant floors", "office", 0.55, 180.0, "CIBSE TM46 general office", None),
        ("Car park", "car_park", 0.18, 45.0, "CIBSE TM46 car park", "Basement"),
        ("Server room", "server_room", 0.02, 380.0, "IT load reference", "Level 2"),
        ("Common areas", "common", 0.19, 205.0, "CIBSE TM46 general", None),
    ],
    "Residential": [
        ("Central plant \u00b7 basement", "plant", 0.06, 180.0, "CIBSE TM46 general + plant uplift", "Basement"),
        ("Residential floors", "residential", 0.62, 185.0, "CIBSE TM46 residential", None),
        ("Car park", "car_park", 0.20, 45.0, "CIBSE TM46 car park", "Basement"),
        ("Common areas", "common", 0.12, 205.0, "CIBSE TM46 general", None),
    ],
}

#: Reading bands. Manufacturer and standards limits per reading type — the same fifteen the
#: sibling seeder writes, so the two agree about what "in band" means.
BANDS = [
    ("temperature", "\u00b0C", 5.0, 95.0, "plant operating range"),
    ("temperature_supply", "\u00b0C", 5.0, 20.0, "chilled water supply"),
    ("temperature_return", "\u00b0C", 8.0, 25.0, "chilled water return"),
    ("coolant_temp", "\u00b0C", 70.0, 95.0, "engine coolant on load"),
    ("pressure", "bar", 3.0, 5.5, "lubrication circuit"),
    ("pressure_discharge", "bar", 12.0, 22.0, "refrigerant discharge"),
    ("oil_pressure", "bar", 3.0, 5.5, "bearing protection limit"),
    ("vibration", "mm/s", 0.0, 7.1, "ISO 10816 zone boundary"),
    ("frequency", "Hz", 49.5, 50.5, "supply tolerance"),
    ("voltage", "V", 216.0, 253.0, "BS EN 50160"),
    ("voltage_output", "V", 216.0, 253.0, "BS EN 50160"),
    ("battery_voltage", "V", 25.5, 28.5, "float charge range"),
    ("load_percentage", "%", 30.0, 100.0, "recommended load band"),
    ("fuel_level", "%", 60.0, 100.0, "statutory minimum reserve"),
    ("exhaust_temp", "\u00b0C", 350.0, 550.0, "turbo inlet limit"),
]

#: What each class of plant is worth, how long it is meant to last (CIBSE Guide M service
#: life), how much faster it ages when driven hard, and which sensors it actually carries.
#:
#: The sensor list is per class on purpose. A generator has a coolant temperature and a fuel
#: level; a fan coil has neither. Giving every asset the same eight readings is the kind of
#: detail that makes a demo look generated.
CLASSES = {
    "chiller": (128_000, 20.0, 1.10, ["temperature", "temperature_supply", "temperature_return",
                                      "pressure", "pressure_discharge", "oil_pressure",
                                      "vibration", "load_percentage"]),
    "crac": (41_000, 15.0, 1.00, ["temperature", "temperature_supply", "temperature_return",
                                  "pressure", "pressure_discharge", "oil_pressure",
                                  "vibration", "load_percentage"]),
    "boiler": (31_000, 15.0, 1.00, ["temperature", "temperature_supply", "temperature_return",
                                    "pressure", "load_percentage"]),
    "ahu": (38_000, 20.0, 0.90, ["temperature", "temperature_supply", "temperature_return",
                                 "vibration", "load_percentage"]),
    "pump": (8_500, 15.0, 0.80, ["temperature", "pressure", "oil_pressure", "vibration"]),
    "generator": (105_000, 25.0, 0.70, ["coolant_temp", "oil_pressure", "fuel_level",
                                        "exhaust_temp", "battery_voltage", "frequency",
                                        "voltage_output", "load_percentage"]),
    "fcu": (2_600, 15.0, 0.70, ["temperature", "temperature_supply", "temperature_return"]),
    "lift": (92_000, 25.0, 0.60, []),
    "fire": (19_000, 20.0, 0.40, []),
    "lighting": (9_000, 15.0, 0.30, []),
    "door": (7_500, 15.0, 0.50, []),
}

#: The register itself. Per asset: code, name, class, section, criticality, condition grade
#: (1 as new, 5 end of life) and age as a fraction of design life.
#:
#: The grades are spread deliberately. A register where everything is grade 1 shows a page of
#: zeros; one where everything is grade 5 shows a page of alarms. Neither is worth reading.
ROSTER = {
    "B-101": [   # Harbour Point, Commercial
        ("HP-CH-01", "Chiller 1 \u00b7 Roof Plant", "chiller", "Central plant \u00b7 basement", "high", 4, 0.72),
        ("HP-CH-02", "Chiller 2 \u00b7 Roof Plant", "chiller", "Central plant \u00b7 basement", "high", 2, 0.35),
        ("HP-AHU-01", "AHU 1 \u00b7 Levels 1-4", "ahu", "Tenant floors", "high", 3, 0.58),
        ("HP-AHU-02", "AHU 2 \u00b7 Levels 5-8", "ahu", "Tenant floors", "medium", 2, 0.41),
        ("HP-AHU-03", "AHU 3 \u00b7 Levels 9-12", "ahu", "Tenant floors", "medium", 3, 0.66),
        ("HP-BLR-01", "Boiler 1 \u00b7 LTHW", "boiler", "Central plant \u00b7 basement", "high", 4, 0.81),
        ("HP-PMP-01", "Pump \u00b7 Chilled Water Primary", "pump", "Central plant \u00b7 basement", "medium", 3, 0.63),
        ("HP-PMP-02", "Pump \u00b7 LTHW Circulation", "pump", "Central plant \u00b7 basement", "low", 2, 0.29),
        ("HP-GEN-01", "Generator \u00b7 Standby 800kVA", "generator", "Central plant \u00b7 basement", "high", 5, 0.88),
        ("HP-CRC-01", "CRAC Cabinet \u00b7 Comms Room 2", "crac", "Server room", "high", 5, 0.48),
        ("HP-LFT-01", "Lift Car 1 \u00b7 Tower", "lift", "Common areas", "high", 3, 0.52),
        ("HP-LFT-02", "Lift Car 2 \u00b7 Tower", "lift", "Common areas", "high", 2, 0.52),
        ("HP-FIR-01", "Fire Alarm Panel \u00b7 Main", "fire", "Common areas", "high", 1, 0.24),
        ("HP-LTG-01", "Lighting Control \u00b7 Car Park", "lighting", "Car park", "low", 2, 0.44),
        ("HP-DOR-01", "Door Access Controller \u00b7 Lobby", "door", "Common areas", "low", 3, 0.71),
    ],
    "B-102": [   # Ashgrove Court, Residential
        ("AC-BLR-01", "Boiler 1 \u00b7 Communal LTHW", "boiler", "Central plant \u00b7 basement", "high", 4, 0.77),
        ("AC-BLR-02", "Boiler 2 \u00b7 Communal LTHW", "boiler", "Central plant \u00b7 basement", "high", 3, 0.55),
        ("AC-PMP-01", "Pump \u00b7 Heating Circuit", "pump", "Central plant \u00b7 basement", "medium", 3, 0.61),
        ("AC-AHU-01", "AHU \u00b7 Communal Ventilation", "ahu", "Common areas", "medium", 2, 0.38),
        ("AC-GEN-01", "Generator \u00b7 Life Safety", "generator", "Central plant \u00b7 basement", "high", 4, 0.83),
        ("AC-FCU-01", "FCU \u00b7 Residents Lounge", "fcu", "Common areas", "low", 2, 0.33),
        ("AC-LFT-01", "Lift Car 1 \u00b7 Core", "lift", "Common areas", "high", 3, 0.57),
        ("AC-FIR-01", "Fire Alarm Panel \u00b7 Main", "fire", "Common areas", "high", 1, 0.19),
        ("AC-LTG-01", "Lighting Control \u00b7 Car Park", "lighting", "Car park", "low", 2, 0.47),
        ("AC-DOR-01", "Door Access Controller \u00b7 Entrance", "door", "Common areas", "low", 2, 0.42),
    ],
}

#: Vendors who hold the assets. Real trades, invented firms — nothing here claims to be a
#: company that exists.
VENDORS = [
    ("NB-V-001", "Kestrel Mechanical Services Ltd", "Manchester", ["chiller", "crac", "ahu", "fcu"]),
    ("NB-V-002", "Arden Heating and Plant Ltd", "Leeds", ["boiler", "pump"]),
    ("NB-V-003", "Apex Lift Engineering Ltd", "Birmingham", ["lift"]),
    ("NB-V-004", "Sentinel Fire and Security Ltd", "Bristol", ["fire", "door"]),
    ("NB-V-005", "Halcyon Power Systems Ltd", "Sheffield", ["generator", "lighting"]),
]

#: Asset categories, one per class, so the register groups the way a CAFM register does.
CATEGORY_OF = {
    "chiller": "HVAC \u00b7 Chillers", "crac": "HVAC \u00b7 Precision Cooling",
    "boiler": "HVAC \u00b7 Boilers", "ahu": "HVAC \u00b7 Air Handling",
    "fcu": "HVAC \u00b7 Terminal Units", "pump": "Mechanical \u00b7 Pumps",
    "generator": "Electrical \u00b7 Standby Power", "lift": "Vertical Transport",
    "fire": "Life Safety \u00b7 Fire Detection", "lighting": "Electrical \u00b7 Lighting Control",
    "door": "Security \u00b7 Access Control",
}


def dsn_for() -> str:
    """The server. The database is named separately so this cannot silently follow the DSN."""
    raw = os.environ.get("PLENUM_DB_DSN")
    if not raw and os.path.exists(ROOT_ENV):
        for line in open(ROOT_ENV, encoding="utf-8"):
            if line.startswith("PLENUM_DB_DSN="):
                raw = line.split("=", 1)[1].strip().strip('"')
    if not raw:
        raise SystemExit("PLENUM_DB_DSN not set")
    return re.sub(r"^postgresql\+asyncpg://", "postgresql://", raw)


def floors_for(count: int) -> list[tuple[str, int]]:
    """Named floors, basement first. The names are what sections reference."""
    out = [("Basement", -1), ("Ground", 0)]
    for i in range(1, max(count - 1, 1)):
        out.append((f"Level {i}", i))
    return out[:max(count, 2)]


async def run(db: str, apply: bool) -> None:
    if db == PRODUCTION:
        raise SystemExit(
            f"refusing to write to {PRODUCTION}. This writes an invented asset register; "
            f"it belongs in a test database."
        )
    c = await asyncpg.connect(dsn_for(), database=db, timeout=90)
    mode = "APPLY" if apply else "dry run"
    print(f"\n################ {db} ({mode}) ################")

    buildings = await c.fetch("""
        SELECT building_id::text AS id, building_code, name, primary_use::text AS use,
               coalesce(floors, 6) AS floors, coalesce(gross_area_sqft, 0) AS sqft,
               organization_id, location_id
          FROM plenum_cafm.buildings
         WHERE building_code = ANY($1::text[])
         ORDER BY building_code""", list(ROSTER.keys()))
    if not buildings:
        raise SystemExit(f"none of {sorted(ROSTER)} are in {db}. Hoist the buildings first.")
    print(f"  buildings: {', '.join(b['building_code'] + ' ' + b['name'] for b in buildings)}")

    org = buildings[0]["organization_id"]

    # -- vendors -------------------------------------------------------------------------
    vendor_id: dict[str, uuid.UUID] = {}
    for code, vname, city, _classes in VENDORS:
        vid = uuid.uuid5(NS, f"vendor:{code}")
        vendor_id[code] = vid
        if apply:
            await c.execute("""
                INSERT INTO plenum_cafm.vendors (id, organization_id, vendor_name, vendor_code,
                                                 city, country, status)
                VALUES ($1, $2, $3, $4, $5, 'United Kingdom', 'active')
                ON CONFLICT (id) DO UPDATE SET vendor_name = EXCLUDED.vendor_name,
                                               city = EXCLUDED.city, updated_at = now()""",
                vid, org, vname, code, city)
    print(f"  vendors:   {len(VENDORS)}")

    # -- categories ----------------------------------------------------------------------
    category_id: dict[str, uuid.UUID] = {}
    for cls, cname in CATEGORY_OF.items():
        cid = uuid.uuid5(NS, f"category:{cname}")
        category_id[cls] = cid
        if apply:
            await c.execute("""
                INSERT INTO plenum_cafm.asset_categories (id, organization_id, name, description)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name""",
                cid, org, cname, f"{cls} plant")
    print(f"  categories: {len(CATEGORY_OF)}")

    # -- floors and sections -------------------------------------------------------------
    section_id: dict[tuple[str, str], uuid.UUID] = {}
    n_floors = n_sections = 0
    for b in buildings:
        floor_id: dict[str, uuid.UUID] = {}
        for fname, level in floors_for(int(b["floors"])):
            fid = uuid.uuid5(NS, f"floor:{b['id']}:{fname}")
            floor_id[fname] = fid
            if apply:
                await c.execute("""
                    INSERT INTO plenum_cafm.floors (floor_id, building_id, level, name,
                                                    gross_area_sqft)
                    VALUES ($1, $2::uuid, $3, $4, $5)
                    ON CONFLICT (floor_id) DO UPDATE SET level = EXCLUDED.level,
                                                         name = EXCLUDED.name""",
                    fid, b["id"], level, fname,
                    Decimal(str(round(float(b["sqft"] or 0) / max(int(b["floors"]), 1), 2))))
            n_floors += 1

        gross_m2 = float(b["sqft"] or 0) * SQFT_TO_M2 or 6000.0
        for name, stype, share, ref, src, on_floor in SECTIONS_BY_USE[b["use"]]:
            sid = uuid.uuid5(NS, f"section:{b['id']}:{name}")
            section_id[(b["building_code"], name)] = sid
            if apply:
                await c.execute("""
                    INSERT INTO plenum_cafm.building_sections
                        (section_id, organization_id, building_id, floor_id, name, section_type,
                         gross_area_m2, reference_eui_kwh_m2, reference_source)
                    VALUES ($1, $2, $3::uuid, $4, $5, $6, $7, $8, $9)
                    ON CONFLICT (section_id) DO UPDATE
                       SET floor_id = EXCLUDED.floor_id,
                           gross_area_m2 = EXCLUDED.gross_area_m2,
                           reference_eui_kwh_m2 = EXCLUDED.reference_eui_kwh_m2,
                           reference_source = EXCLUDED.reference_source, updated_at = now()""",
                    sid, org, b["id"], floor_id.get(on_floor) if on_floor else None,
                    name, stype, Decimal(str(round(gross_m2 * share, 2))),
                    Decimal(str(ref)), src)
            n_sections += 1
    print(f"  floors:    {n_floors}")
    print(f"  sections:  {n_sections}")

    # -- assets --------------------------------------------------------------------------
    # health_score falls as the condition grade rises, because the page orders the tree by
    # health ascending and a register whose worst plant sorts last is a register nobody
    # scrolls to. 100 at grade 1, 30 at grade 5.
    by_code = {b["building_code"]: b for b in buildings}
    n_assets = 0
    for bcode, rows in ROSTER.items():
        b = by_code.get(bcode)
        if b is None:
            print(f"  ! {bcode} is not in this database, skipped")
            continue
        for code, aname, cls, sec_name, crit, grade, age_frac in rows:
            value, life, wear, _sensors = CLASSES[cls]
            aid = uuid.uuid5(NS, f"asset:{code}")
            days = int(life * age_frac * 365.25)
            health = max(30, 100 - (grade - 1) * 18)
            sid = section_id.get((bcode, sec_name))
            if sid is None:
                raise SystemExit(f"{code} names section {sec_name!r}, which {bcode} has not got")
            if apply:
                await c.execute("""
                    INSERT INTO plenum_cafm.assets
                        (id, organization_id, building_id, location_id, section_id, category_id,
                         asset_name, asset_code, status, criticality,
                         health_score, condition_score, condition_updated_at,
                         installation_date, replacement_value, replacement_currency,
                         design_life_years, wear_coefficient, vendor_id)
                    VALUES ($1, $2, $3::uuid, $4, $5, $6, $7, $8, 'active', $9::text, $10, $11,
                            now(), (current_date - ($12 || ' days')::interval)::date,
                            $13, 'GBP', $14, $15, $16)
                    ON CONFLICT (id) DO UPDATE
                       SET asset_name = EXCLUDED.asset_name, section_id = EXCLUDED.section_id,
                           category_id = EXCLUDED.category_id, criticality = EXCLUDED.criticality,
                           health_score = EXCLUDED.health_score,
                           condition_score = EXCLUDED.condition_score,
                           condition_updated_at = now(),
                           installation_date = EXCLUDED.installation_date,
                           replacement_value = EXCLUDED.replacement_value,
                           design_life_years = EXCLUDED.design_life_years,
                           wear_coefficient = EXCLUDED.wear_coefficient,
                           vendor_id = EXCLUDED.vendor_id, updated_at = now()""",
                    aid, org, b["id"], b["location_id"],
                    sid, category_id[cls], aname, code, crit, health, grade, str(days),
                    Decimal(str(value)), Decimal(str(life)), Decimal(str(wear)),
                    str(vendor_id[vendor_for(cls)]))
            n_assets += 1
    print(f"  assets:    {n_assets}")

    # -- reading bands -------------------------------------------------------------------
    if apply:
        for rt, unit, lo, hi, note in BANDS:
            await c.execute("""
                INSERT INTO plenum_cafm.asset_reading_bands
                       (id, organization_id, reading_type, unit, lo, hi, note)
                SELECT $1, $2, $3, $4, $5, $6, $7
                 WHERE NOT EXISTS (
                     SELECT 1 FROM plenum_cafm.asset_reading_bands
                      WHERE reading_type = $3 AND asset_id IS NULL AND asset_category IS NULL)""",
                uuid.uuid5(NS, f"band:{rt}"), org, rt, unit,
                Decimal(str(lo)), Decimal(str(hi)), note)
    print(f"  bands:     {len(BANDS)}")

    if apply:
        after = await c.fetchrow("""
            SELECT (SELECT count(*) FROM plenum_cafm.floors) f,
                   (SELECT count(*) FROM plenum_cafm.building_sections) s,
                   (SELECT count(*) FROM plenum_cafm.assets) a,
                   (SELECT count(*) FROM plenum_cafm.vendors) v,
                   (SELECT count(*) FROM plenum_cafm.asset_reading_bands) b,
                   (SELECT count(*) FROM plenum_cafm.assets
                     WHERE replacement_value IS NOT NULL AND design_life_years IS NOT NULL
                       AND installation_date IS NOT NULL) priced""")
        print(f"\n  AFTER: floors={after['f']} sections={after['s']} assets={after['a']} "
              f"vendors={after['v']} bands={after['b']}")
        print(f"         assets with all three value-at-risk inputs: {after['priced']}")
        print("\n  Next: refresh_asset_readings.py --db " + db + " --backfill 2 --apply")
    else:
        print("\n  dry run. Nothing was written. Add --apply.")
    await c.close()


def vendor_for(cls: str) -> str:
    for code, _name, _city, classes in VENDORS:
        if cls in classes:
            return code
    return VENDORS[0][0]


async def main() -> None:
    ap = argparse.ArgumentParser(description="Seed the Northbridge asset register.")
    ap.add_argument("--db", required=True, help="database name, e.g. hoistra_test")
    ap.add_argument("--apply", action="store_true", help="write; otherwise report only")
    args = ap.parse_args()
    await run(args.db, args.apply)


if __name__ == "__main__":
    asyncio.run(main())
