"""Build a floor-level sub-meter workbook beside a building's workbook.

A building's workbook meters it at the incoming supply: one electricity meter, one gas meter,
a year of half-hours on each. That answers "how much does the building use" and nothing about
where. This writes the companion file — `<workbook>-floorlevel_submeter.xlsx` — that puts a
sub-meter on every floor for both fuels, so the Energy page can read the building as
building → floor → meter the way the Assets page reads it as building → section → asset.

Three sheets, in the order the writer needs them:

  Building_Sections   one section per floor, named as the floor is ("Basement", "Ground",
                      "Level 1" …), carrying floor_name so the writer links floors.floor_id
  Energy_Meters       two sub-meters per floor (electricity, gas), is_sub_meter true,
                      section_name = the floor, tariff and carbon factor as the main meter's;
                      then one sub-meter per significant asset (chillers, AHUs, pumps, lifts
                      on electricity; boilers on gas) carrying asset_code and no section
  Meter_Readings      every half-hour of the trailing window for every sub-meter
  Work_Orders         one order per metered asset the main workbook left without one, so the
                      Assets drawer has work history and post_works_regression has an input;
                      the spike asset's order is dated to its excursion

The readings are derived from the main meters' readings in the building workbook, half-hour
by half-hour, so the floors reconcile with the incoming supply: each floor takes a fixed share
(plant-heavy in the basement, even across tenant floors) with a little noise, and the floors
together account for about 93% of electricity and 95% of gas — the remainder is lifts, risers
and common parts that a landlord does not sub-meter, and the Energy page reports that
coverage rather than pretending the floors are the building. One tenant floor carries a
deliberate night-time drift over the last five weeks so the anomaly scan has a floor-level
finding to make.

The asset meters are a second decomposition of the same supply — a chiller's electricity is
also Basement electricity — so they carry no section and the Energy page lists them by asset,
not on a floor. They are what puts a kWh figure against a piece of plant: the by-asset
consumption read, the asset-spike detector, and an anomaly that names the asset rather than
the building. One chiller carries a three-day excursion so that detector has a finding to make.

The window is 90 days by default (--days). A year per floor meter is 17,520 rows; twelve floors
and two fuels make that 420,000 rows in one sheet, which ingests slowly and says nothing more
than 90 days do about where the energy goes. The EUI is still computed from the main meters.

Reads the building workbook only. Writes nothing to any database.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import random
import sys

import openpyxl

DEFAULT_WORKBOOKS = [
    r"C:\Users\balap\Downloads\northbridge_B-101_harbour_point.xlsx",
    r"C:\Users\balap\Downloads\northbridge_B-102_ashgrove_court.xlsx",
]
SUFFIX = "-floorlevel_submeter"

SECTIONS_HEADER = ["building_code", "name", "section_type", "floor_name", "gross_area_m2",
                   "reference_eui_kwh_m2", "reference_source"]
METERS_HEADER = ["meter_ref", "building_code", "site_ref", "meter_type", "mpan", "mprn",
                 "is_sub_meter", "section_name", "asset_code", "tariff_gbp_per_kwh",
                 "carbon_kg_per_kwh", "active", "description"]
READINGS_HEADER = ["meter_ref", "building_code", "meter_type", "reading_at", "consumption_kwh",
                   "period_minutes", "source"]
#: The destination columns, not the export's own spelling: a header work_orders does not have is
#: added as a new column by the writer rather than refused, and the API then reads the empty
#: original. `vendor` and `estimated_cost`/`actual_cost` are what models/work_order.py declares.
WO_HEADER = ["wo_code", "vendor", "asset_code", "asset_name", "fault_description",
             "priority", "status", "reported_at", "attended_at", "completed_at", "first_fix",
             "recall", "labour_hours", "parts_cost", "estimated_cost", "actual_cost",
             "building_code", "wo_type", "sla_due_at", "title"]

#: What a planned attendance on each kind of plant is called and costs. Keyed by the type token
#: in the asset code, the same token ASSET_SHARE uses.
WO_BY_KIND = {
    "CHILLER": ("Compressor running hours above profile - refrigerant charge and head pressure "
                "checked, condenser coils cleaned", "P2", 4.5, 340),
    "AHU":     ("Supply fan current high against the schedule - filters replaced and belt "
                "tension reset", "P3", 3.0, 180),
    "PUMP":    ("Chilled water pump cycling short - strainer cleared and expansion vessel "
                "recharged", "P3", 2.5, 120),
    "LIFT":    ("Six-monthly service attendance - door operator adjusted, drive current logged",
                "Planned", 3.0, 0),
    "BOILER":  ("Combustion efficiency below target - burner serviced and flue gas analysed",
                "P2", 4.0, 260),
    "EXTRACT": ("Car park extract running outside the occupancy schedule - CO sensor "
                "recalibrated", "P3", 2.0, 95),
    "EML":     ("Monthly emergency lighting drop test", "Planned", 1.5, 0),
}

#: CIBSE TM46-style references per section type, kWh/m²/yr (electricity + fossil).
REFERENCE_EUI = {"plant": 180, "common": 205, "office": 180, "residential": 120}

#: What the floors account for of the incoming supply between them. The rest is lifts, risers,
#: external lighting and common parts a landlord does not sub-meter.
COVERAGE = {"electricity": 0.93, "gas": 0.95}
#: Basement and ground take a fixed slice; the tenant floors share what remains evenly.
FIXED_SHARE = {
    "electricity": {"Basement": 0.14, "Ground": 0.10},
    "gas": {"Basement": 0.62, "Ground": 0.08},
}
NOISE = 0.05
#: A tenant floor whose night-time electricity creeps up over the last five weeks, so the
#: scan has something to find at floor level. Applied to the highest tenant floor.
DRIFT_WEEKS = 5
DRIFT_NIGHT_UPLIFT = 0.25

#: Share of the incoming supply each kind of plant draws, by the type token in its asset code.
#: (first of that kind, each further one). Anything not listed — a distribution board, a fire
#: panel — is not metered: a board IS the supply, and a panel draws nothing worth a meter.
ASSET_SHARE = {
    "electricity": {
        "CHILLER": (0.12, 0.03), "AHU": (0.05, 0.05), "PUMP": (0.02, 0.02),
        "LIFT": (0.015, 0.015), "EXTRACT": (0.03, 0.03), "EML": (0.005, 0.005),
    },
    "gas": {"BOILER": (0.65, 0.0)},   # boilers share 65% of gas between them, see below
}
#: A three-day excursion on the first chiller (or the first metered asset when there is none).
SPIKE_DAYS = 3
SPIKE_UPLIFT = 0.40
SPIKE_ENDS_DAYS_BEFORE_LATEST = 10


def asset_kind(asset_code: str) -> str | None:
    """CHILLER from B-101-CHILLER-01: the token between the building code and the number."""
    parts = asset_code.split("-")
    return parts[2] if len(parts) >= 4 else None


def asset_shares(codes: list[str]) -> list[tuple[str, str, float]]:
    """(asset_code, fuel, share) for every asset worth a meter, in sheet order."""
    out, seen = [], {}
    boilers = [c for c in codes if asset_kind(c) == "BOILER"]
    for code in codes:
        kind = asset_kind(code)
        if kind == "BOILER":
            out.append((code, "gas", ASSET_SHARE["gas"]["BOILER"][0] / len(boilers)))
            continue
        spec = ASSET_SHARE["electricity"].get(kind or "")
        if not spec:
            continue
        n = seen.get(kind, 0)
        out.append((code, "electricity", spec[0] if n == 0 else spec[1]))
        seen[kind] = n + 1
    return out


def floor_names(n: int) -> list[str]:
    """Basement, Ground, Level 1 … the way plenum_cafm.floors names them for these buildings."""
    if n <= 0:
        return []
    names = ["Basement", "Ground"]
    names += [f"Level {i}" for i in range(1, max(0, n - 2) + 1)]
    return names[:n] if n >= 2 else names[:1]


def floor_code(name: str) -> str:
    if name == "Basement":
        return "B"
    if name == "Ground":
        return "G"
    return "L" + name.split()[-1].zfill(2)


def section_type_for(name: str, primary_use: str) -> str:
    if name == "Basement":
        return "plant"
    if name == "Ground":
        return "common"
    return "residential" if (primary_use or "").lower().startswith("resid") else "office"


def read_sheet(wb, name: str) -> tuple[list[str], list[list]]:
    ws = wb[name]
    rows = ws.iter_rows(values_only=True)
    header = [str(h) for h in next(rows)]
    return header, [list(r) for r in rows]


def shares(fuel: str, names: list[str]) -> dict[str, float]:
    fixed = FIXED_SHARE[fuel]
    tenants = [n for n in names if n not in fixed]
    remaining = COVERAGE[fuel] - sum(fixed.get(n, 0.0) for n in names if n in fixed)
    out = {n: fixed[n] for n in names if n in fixed}
    for n in tenants:
        out[n] = remaining / len(tenants) if tenants else 0.0
    return out


def _parse_at(v) -> dt.datetime:
    if isinstance(v, dt.datetime):
        return v if v.tzinfo else v.replace(tzinfo=dt.timezone.utc)
    s = str(v).replace("Z", "+00:00")
    d = dt.datetime.fromisoformat(s)
    return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)


def build(path: str, *, days: int, seed: int) -> str:
    rng = random.Random(seed)
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)

    bh, brows = read_sheet(wb, "Buildings")
    b = dict(zip(bh, brows[0]))
    code, name = str(b["building_code"]), str(b["name"])
    site_ref = b.get("site_ref")
    n_floors = int(float(b["floors"]))
    gia_m2 = float(b["gross_internal_area_m2"] or 0) or float(b["gross_area_sqft"]) / 10.7639
    use = str(b.get("primary_use") or "Commercial")
    names = floor_names(n_floors)
    area_each = round(gia_m2 / len(names), 1)

    mh, mrows = read_sheet(wb, "Energy_Meters")
    mains = {}
    for r in mrows:
        m = dict(zip(mh, r))
        if str(m.get("is_sub_meter", "false")).lower() in ("true", "1", "yes"):
            continue
        mains[str(m["meter_type"]).lower()] = m
    if not mains:
        raise SystemExit(f"{os.path.basename(path)}: Energy_Meters has no main meter")

    rh, rrows = read_sheet(wb, "Meter_Readings")
    ri = {h: i for i, h in enumerate(rh)}
    latest = max(_parse_at(r[ri["reading_at"]]) for r in rrows)
    window_start = latest - dt.timedelta(days=days)
    drift_start = latest - dt.timedelta(weeks=DRIFT_WEEKS)
    by_fuel: dict[str, list[tuple[dt.datetime, float, int]]] = {}
    for r in rrows:
        at = _parse_at(r[ri["reading_at"]])
        if at < window_start:
            continue
        by_fuel.setdefault(str(r[ri["meter_type"]]).lower(), []).append(
            (at, float(r[ri["consumption_kwh"]] or 0), int(r[ri["period_minutes"]] or 30)))
    for fuel in by_fuel:
        by_fuel[fuel].sort()

    # Sheets ---------------------------------------------------------------------------
    sections = []
    for fname in names:
        st = section_type_for(fname, use)
        sections.append([code, fname, st, fname, area_each, REFERENCE_EUI[st], f"CIBSE TM46 {st}"])

    meters, readings = [], []
    drift_floor = next((n for n in reversed(names) if n.startswith("Level")), None)
    for fuel, main in mains.items():
        if fuel not in by_fuel:
            print(f"  {name}: no {fuel} readings in the last {days} days — no {fuel} sub-meters")
            continue
        main_ref = str(main.get("meter_ref") or main.get("mpan") or main.get("mprn"))
        prefix = main_ref.rsplit("-", 1)[0]          # NB-B-101-E0 -> NB-B-101
        letter = "E" if fuel == "electricity" else "G"
        share = shares(fuel, names)
        for fname in names:
            ref = f"{prefix}-{letter}-{floor_code(fname)}"
            meters.append([
                ref, code, site_ref, fuel,
                ref if fuel == "electricity" else None, ref if fuel == "gas" else None,
                "true", fname, None,
                main.get("tariff_gbp_per_kwh"), main.get("carbon_kg_per_kwh"), "true",
                f"{name} {fname} {fuel} sub-meter",
            ])
            w = share[fname]
            for at, kwh, minutes in by_fuel[fuel]:
                v = kwh * w * (1.0 + rng.uniform(-NOISE, NOISE))
                if fuel == "electricity" and fname == drift_floor and at >= drift_start \
                        and (at.hour >= 22 or at.hour < 6):
                    v *= 1.0 + DRIFT_NIGHT_UPLIFT
                readings.append([ref, code, fuel, at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                 round(v, 3), minutes, "bms"])

    # Asset sub-meters: the plant, by asset, off the same supply.
    ah, arows = read_sheet(wb, "Assets")
    ai = {h: i for i, h in enumerate(ah)}
    codes = [str(r[ai["asset_code"]]) for r in arows if r[ai["asset_code"]]]
    asset_names = {str(r[ai["asset_code"]]): str(r[ai["asset_name"]]) for r in arows}
    metered = asset_shares(codes)
    spike_asset = next((c for c, f, _ in metered if asset_kind(c) == "CHILLER"),
                       next((c for c, f, _ in metered if f == "electricity"), None))
    spike_end = latest - dt.timedelta(days=SPIKE_ENDS_DAYS_BEFORE_LATEST)
    spike_start = spike_end - dt.timedelta(days=SPIKE_DAYS)
    n_asset_meters = 0
    for acode, fuel, w in metered:
        main = mains.get(fuel)
        if not main or fuel not in by_fuel:
            continue
        main_ref = str(main.get("meter_ref") or main.get("mpan") or main.get("mprn"))
        prefix = main_ref.rsplit("-", 1)[0]
        ref = f"{prefix}-A-{acode[len(code) + 1:]}"          # NB-B-101-A-CHILLER-01
        meters.append([
            ref, code, site_ref, fuel,
            ref if fuel == "electricity" else None, ref if fuel == "gas" else None,
            "true", None, acode,
            main.get("tariff_gbp_per_kwh"), main.get("carbon_kg_per_kwh"), "true",
            f"{asset_names.get(acode, acode)} {fuel} sub-meter",
        ])
        n_asset_meters += 1
        for at, kwh, minutes in by_fuel[fuel]:
            v = kwh * w * (1.0 + rng.uniform(-NOISE, NOISE))
            if acode == spike_asset and spike_start <= at < spike_end:
                v *= 1.0 + SPIKE_UPLIFT
            readings.append([ref, code, fuel, at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                             round(v, 3), minutes, "bms"])

    # Work orders for the metered plant the main workbook left without one. A metered asset
    # with no maintenance record reads as plant nobody attends, and it is the one case where
    # the energy page can price a fault that the maintenance page cannot account for.
    wh, wrows = read_sheet(wb, "Work_Orders")
    wi = {h: i for i, h in enumerate(wh)}
    have_wo = {str(r[wi["asset_code"]]) for r in wrows if r[wi["asset_code"]]}
    next_n = 1 + max((int(str(r[wi["wo_code"]]).rsplit("-", 1)[-1])
                      for r in wrows if r[wi["wo_code"]]), default=0)
    vendor_of = {str(r[ai["asset_code"]]): r[ai["maintained_by"]] for r in arows}

    work_orders = []
    for acode, fuel, _w in metered:
        if acode in have_wo:
            continue
        kind = asset_kind(acode)
        spec = WO_BY_KIND.get(kind or "")
        if not spec:
            continue
        fault, priority, hours, parts = spec
        # The spike asset's order is the excursion: reported the day after it starts, attended
        # the same day, completed the day it ends. Everything else is a routine attendance
        # inside the last fortnight, which is what post_works_regression reads.
        if acode == spike_asset:
            reported = spike_start + dt.timedelta(days=1)
            completed = spike_end
            priority = "P1"
            fault = ("Sustained overconsumption on the asset sub-meter - " + fault.lower())
        else:
            reported = latest - dt.timedelta(days=12 + (next_n % 5))
            completed = reported + dt.timedelta(days=1)
        work_orders.append([
            f"WO-{code}-{next_n}", vendor_of.get(acode), acode, asset_names.get(acode, acode),
            fault, priority, "Completed",
            reported.strftime("%Y-%m-%dT%H:%M"),
            (reported + dt.timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M"),
            completed.strftime("%Y-%m-%dT%H:%M"),
            "true", "false", hours, parts, round(hours * 68 + parts), round(hours * 68 + parts),
            code, "reactive" if priority != "Planned" else "planned",
            (reported + dt.timedelta(days=2)).strftime("%Y-%m-%d"),
            fault[:120],
        ])
        next_n += 1

    out_path = os.path.splitext(path)[0] + SUFFIX + ".xlsx"
    try:
        with open(out_path, "ab"):
            pass
    except PermissionError:
        out_path = os.path.splitext(path)[0] + SUFFIX + "-" + dt.datetime.now().strftime("%Y%m%d-%H%M") + ".xlsx"
        print(f"  {name}: target is open elsewhere - writing {os.path.basename(out_path)}")

    out = openpyxl.Workbook(write_only=True)
    for title, header, rows in (("Building_Sections", SECTIONS_HEADER, sections),
                                ("Energy_Meters", METERS_HEADER, meters),
                                ("Meter_Readings", READINGS_HEADER, readings),
                                ("Work_Orders", WO_HEADER, work_orders)):
        ws = out.create_sheet(title)
        ws.append(header)
        for r in rows:
            ws.append(r)
    out.save(out_path)

    print(f"  {name} ({code}): {len(names)} floors, {len(meters)} sub-meters "
          f"({n_asset_meters} on assets), {len(readings):,} readings over {days} days "
          f"({window_start.date()} to {latest.date()})")
    print(f"     work orders added for {len(work_orders)} metered asset(s) with none: "
          f"{', '.join(w[2] for w in work_orders)}")
    print(f"     asset meters: {', '.join(c for c, _, _ in metered)}; "
          f"3-day +{int(SPIKE_UPLIFT*100)}% excursion on {spike_asset} "
          f"{spike_start.date()} to {spike_end.date()}")
    print(f"     floors account for {int(COVERAGE['electricity']*100)}% of electricity, "
          f"{int(COVERAGE['gas']*100)}% of gas; night drift on {drift_floor} electricity "
          f"from {drift_start.date()}")
    print(f"     -> {out_path}")
    return out_path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("workbooks", nargs="*", default=DEFAULT_WORKBOOKS)
    ap.add_argument("--days", type=int, default=90, help="trailing window of half-hours per sub-meter")
    ap.add_argument("--seed", type=int, default=101)
    args = ap.parse_args()
    for i, p in enumerate(args.workbooks):
        if not os.path.exists(p):
            print(f"  missing: {p}", file=sys.stderr)
            continue
        build(p, days=args.days, seed=args.seed + i)


if __name__ == "__main__":
    main()
