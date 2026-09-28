"""Bishopsgate Tower (Plenum Technologies) as an ingestible building workbook.

The 15 Sep 2026 design prototype (`Hoistra_1 (1).html`, a bundled page) describes a portfolio
called Plenum Technologies. Its data lives in five scripts inside the bundle - window.HOISTWAY,
HOISTRA_CC, HOISTRA_EN, HOISTRA_AS, HOISTRA_MX. This reads them out of the bundle and writes
Bishopsgate Tower, the building the prototype says most about, in the 17-sheet layout
docs/test-data/GENERATE_BUILDING_WORKBOOK.md specifies - the same layout as
northbridge_B-101_harbour_point.xlsx - so the Migration page ingests it in one run.

WHAT COMES FROM THE PROTOTYPE, unchanged except for the date shift below:
  the building, its region and use; its four sections with their areas and EUIs; the eight
  assets (names, classes, vendors, install years, L1 criticality, last PPM); the eight work
  orders and five inspections with their findings; the Mechanical PPM contract's figures;
  the LOLER, F-Gas and (lapsed) Fire Risk Assessment certificates; the vendors and the
  accreditations the compliance console says each holds or lacks; the AHU-3 and GEN-1 sensor
  readings with their limits and 24-hour histories; the 28.4p / 7.1p tariffs; the two energy
  anomalies, which the readings are shaped to reproduce - AHU-3's Saturday 02:00-06:00
  non-occupancy spike on the last three weekends (847 vs 210 kWh) and CHILLER-101's
  single-asset excursion (made by build_floor_submeters.py on the first chiller).

WHAT IS GENERATED, to the spec, because the prototype does not have it:
  two boilers, a fire panel and an LV board (the spec needs a boiler for gas and CP17, and
  something for the fire and electrical contracts to maintain); manufacturers and models; the
  remaining building certificates (EICR, EPC, fire alarm, CP17, TM44, L8); the PPM visit rows
  behind the contract figures; spare parts; two technicians; a year of half-hourly readings on
  the two incoming meters and 90 days on four section sub-meters.

TWO PLACES THE PROTOTYPE DISAGREES WITH ITSELF:
  area   a decision card says "142,000 ft2" (13,190 m2) but the sections alone exceed that
         (L12-L20 is 18,900 m2). The sections are followed: 22 floors, GIA 49,400 m2, with two
         generated sections (L1-L11 tenant floors, Ground and common) making up the rest.
  dates  the prototype is "as at 2 Sep 2026". Every date is moved forward by the gap to
         --as-of (default: today), so "LOLER expires in 23 days" is still true when ingested.

    python db/tools/build_bishopsgate_workbook.py "C:/Users/balap/Downloads/Hoistra_1 (1).html"
    python db/tools/build_floor_submeters.py  <out>.xlsx
    python db/tools/build_complete_workbook.py <out>.xlsx
    python db/tools/verify_workbook.py <out>-complete.xlsx

--org-name looks the organization and its users up in hoistra_test so Technicians.user_id is a
real login (the spec requires it). Without it, or before the company exists, user_id is left
blank and verify_workbook.py's database check says so.

Reads the prototype and, with --org-name, reads hoistra_test. Writes nothing to any database.
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import gzip
import json
import math
import os
import random
import re
import subprocess
import sys
import tempfile
import zlib

import openpyxl

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

BUILDING = "Bishopsgate Tower"
CODE, SITE, CO = "B-301", "S-301", "PT"
PROTOTYPE_AS_OF = dt.date(2026, 9, 2)
FLOORS = 22                    # Basement, Ground, Level 1 .. Level 20
GIA_M2 = 49_400.0
EUI = 214.0                    # HOISTWAY.buildings: Bishopsgate Tower eui 214 vs bench 180
ELEC_SHARE = 0.72
TARIFF = {"electricity": 0.284, "gas": 0.071}     # prototype: 28.4p/kWh contracted, 7.1p gas
CARBON = {"electricity": 0.207, "gas": 0.183}

# ── read the bundle ──────────────────────────────────────────────────────────────────────


def read_prototype(path: str) -> dict:
    """window.HOISTWAY & co., out of the bundled page, evaluated by node in a bare context."""
    src = open(path, encoding="utf-8").read()
    m = re.search(r'<script type="__bundler/manifest">\s*(.*?)\s*</script>', src, re.S)
    if not m:
        raise SystemExit("not a bundled page: no __bundler/manifest")
    scripts = []
    for entry in json.loads(m.group(1)).values():
        if not str(entry.get("mime", "")).endswith("javascript"):
            continue
        raw = base64.b64decode(entry["data"])
        for f in (gzip.decompress, zlib.decompress, lambda x: zlib.decompress(x, -15), lambda x: x):
            try:
                raw = f(raw)
                break
            except Exception:  # noqa: BLE001 - try the next encoding
                continue
        text = raw.decode("utf-8", "replace")
        if re.search(r"window\.(HOISTWAY|HOISTRA_(CC|EN|AS|MX|VP))\s*=", text):
            scripts.append(text)
    with tempfile.TemporaryDirectory() as tmp:
        for i, s in enumerate(scripts):
            open(os.path.join(tmp, f"{i}.js"), "w", encoding="utf-8").write(s)
        runner = os.path.join(tmp, "run.js")
        open(runner, "w", encoding="utf-8").write(
            "const fs=require('fs'),vm=require('vm');const w={};"
            "const c=vm.createContext({window:w,console,document:{addEventListener(){}}});"
            f"for(let i=0;i<{len(scripts)};i++)vm.runInContext(fs.readFileSync(__dirname+'/'+i+'.js','utf8'),c);"
            "process.stdout.write(JSON.stringify(w,(k,v)=>typeof v==='function'?undefined:v));")
        out = subprocess.run(["node", runner], capture_output=True, text=True, encoding="utf-8", check=True)
    data = json.loads(out.stdout)
    for k in ("HOISTWAY", "HOISTRA_CC", "HOISTRA_AS", "HOISTRA_MX", "HOISTRA_VP"):
        if k not in data:
            raise SystemExit(f"prototype data {k} not found in the bundle")
    return data


def pdate(s: str) -> dt.date:
    """'14 Jun 2026' (optionally with a trailing note) as a date."""
    m = re.search(r"(\d{1,2} [A-Z][a-z]{2} \d{4})", s or "")
    return dt.datetime.strptime(m.group(1), "%d %b %Y").date() if m else None


# ── the workbook ─────────────────────────────────────────────────────────────────────────

HEADERS = {
    "Sites": ["site_id", "site_name", "city", "postcode", "region", "manager_email"],
    "Buildings": ["building_code", "name", "site_ref", "country_code", "primary_use", "floors",
                  "gross_area_sqft", "gross_internal_area_m2", "eui_kwh_m2", "hoist_score"],
    "Building_Sections": ["building_code", "name", "section_type", "floor_name", "gross_area_m2",
                          "reference_eui_kwh_m2", "reference_source"],
    "Vendors": ["vendor_code", "vendor_name", "trade", "address", "phone", "country", "accreditation",
                "block_state", "block_reason", "blocked_accreditation_type"],
    "Vendor_Contracts": ["contract_name", "vendor_code", "vendor_name", "service_scope", "visits_per_year",
                         "contract_start", "contract_end", "contract_value", "country_code", "status", "sla_terms"],
    "Technicians": ["engineer_id", "full_name", "trade", "site_ref", "certification", "user_full_name",
                    "base_location", "user_id"],
    "Assets": ["asset_code", "asset_name", "manufacturer", "model", "site_ref", "site_name", "status",
               "install_date", "maintained_by", "replacement_value", "replacement_currency", "design_life_years",
               "wear_coefficient", "condition_score", "criticality", "section_name", "building_code"],
    "Asset_Reading_Bands": ["reading_type", "unit", "lo", "hi", "note"],
    "Asset_Readings": ["asset_code", "reading_type", "value", "unit", "recorded_at"],
    "Maintenance_Plans": ["sm_code", "asset_code", "building_code", "description", "maintenance_type",
                          "frequency_type", "frequency_value", "next_due_date", "status", "vendor_name"],
    "PPM_Visits": ["ppm_ref", "vendor_name", "asset_code", "task", "frequency", "scheduled_date",
                   "completed_date", "tolerance_days", "status", "contract_name", "building_code"],
    # The base workbook's spellings; build_complete_workbook.CANONICAL_HEADERS renames them to the
    # destination columns as it merges, exactly as it does for Northbridge's.
    "Work_Orders": ["wo_code", "vendor_name", "asset_code", "asset_name", "fault_description", "priority", "status",
                    "reported_at", "attended_at", "completed_at", "first_fix", "recall", "labour_hours",
                    "parts_cost", "cost_estimated", "cost_actual", "building_code", "wo_type", "sla_due_at", "title"],
    "Inspections": ["inspection_ref", "asset_code", "inspection_date", "inspector", "finding_type", "risk_level",
                    "observations", "recommendation", "corrective_action"],
    "Spare_Parts": ["part_code", "part_name", "unit_price", "stock_quantity", "reorder_level", "supplier"],
    "Energy_Meters": ["meter_ref", "building_code", "site_ref", "meter_type", "mpan", "mprn", "is_sub_meter",
                      "tariff_gbp_per_kwh", "carbon_kg_per_kwh", "active", "description"],
    "Meter_Readings": ["meter_ref", "building_code", "meter_type", "reading_at", "consumption_kwh",
                       "period_minutes", "source"],
    "Compliance_Certificates": ["certificate_number", "certificate_type_code", "cert_scope", "building_code",
                                "asset_code", "vendor_code", "vendor_name", "issuer", "inspector_name",
                                "inspector_accreditation_number", "issue_date", "expiry_date", "next_due_date",
                                "inspection_frequency_months", "result", "status", "country_code", "defects_found",
                                "remedial_actions", "remedial_status", "energy_rating", "energy_score"],
}
ORDER = ["Sites", "Buildings", "Building_Sections", "Vendors", "Vendor_Contracts", "Technicians", "Assets",
         "Asset_Reading_Bands", "Asset_Readings", "Maintenance_Plans", "PPM_Visits", "Work_Orders",
         "Inspections", "Spare_Parts", "Energy_Meters", "Meter_Readings", "Compliance_Certificates"]

#: The prototype's four sections (name, type, area, EUI) plus the two that make up the GIA.
SECTIONS = [
    # name, section_type, floor, area, reference, source, prototype EUI or None
    ("Central plant · basement", "plant", None, 1400, 180, "CIBSE TM46 general + plant uplift", 262),
    ("L4 East · tenant floor", "office", None, 2100, 180, "CIBSE TM46 general office", 231),
    ("L12–L20 · tenant floors", "office", None, 18900, 180, "CIBSE TM46 general office", 178),
    ("Car park", "car_park", None, 3200, 45, "CIBSE TM46 car park", 41),
    ("L1–L11 · tenant floors", "office", None, 21000, 180, "CIBSE TM46 general office", None),
    ("Ground · reception and common", "common", None, 2800, 205, "CIBSE TM46 general", None),
]

#: Vendors the prototype names for Bishopsgate, with a stable code and a trade.
VENDORS = [
    ("APXM", "Apex Mechanical", "Mechanical", "Unit 9, Gresham Works, London EC2V 7ZZ", "+44 20 7946 0412", "Gas Safe 612044"),
    ("APXL", "Apex Lifts", "Lifts", "3 Tannery Yard, London SE1 3ZZ", "+44 20 7946 0730", "LEIA member 2291"),
    ("SFLT", "SafeLift Engineering", "Lifts", "Bay 2, Riverside Park, Dartford DA1 9ZZ", "+44 1322 496 018", "LEIA member 3107"),
    ("NGEL", "Northgate Electrical", "Electrical", "41 Northgate Row, Manchester M3 9ZZ", "+44 161 496 0284", "NICEIC 034512"),
    ("PCFR", "ProudCastle Fire", "Fire", "Castle Court, Croydon CR0 9ZZ", "+44 20 7946 0957", "none current"),
    ("CLWC", "Clearwater Compliance", "Water hygiene", "12 Fleet Mews, Reading RG1 9ZZ", "+44 118 496 0661", "LCA member 1188"),
]
VCODE = {v[1]: v[0] for v in VENDORS}
#: The two Bishopsgate vendors the compliance console shows Blocked (HOISTRA_CC.vendors).
BLOCK = {
    "SFLT": ("Blocked", "Public liability insurance lapsed; LOLER competence not on record", "CONTRACTOR_PL_INSURANCE"),
    "PCFR": ("Blocked", "BAFE SP203-1 registration lapsed; SP101, SP105 and NSI Gold not on record", "BAFE_SP203_1"),
}

# ── vendor performance ───────────────────────────────────────────────────────────────────
#: HOISTRA_VP.V holds the full contract, terms, measured KPIs, breaches, certificates and
#: invoices for four of the six Bishopsgate vendors.
VP_ID = {"APXM": "v1", "NGEL": "v3", "APXL": "v4", "CLWC": "v6"}
#: The other two exist in the prototype only as compliance rows (both Blocked). Their KPIs are
#: generated to that posture: below the portfolio, and declining.
GEN_PERF = {"SFLT": {"sla_r": 82, "sla_c": 74, "firstfix": 70, "recall": 12, "invoice": 70, "trend": "declining"},
            "PCFR": {"sla_r": 85, "sla_c": 78, "firstfix": 72, "recall": 10, "invoice": 75, "trend": "declining"}}
GEN_CONTRACT = {"SFLT": {"ref": "SL-2025-LIFT-01", "signed": "03 Mar 2025", "expires": "02 Mar 2027"},
                "PCFR": {"ref": "PC-2024-FIRE-02", "signed": "15 Jul 2024", "expires": "14 Jul 2027"}}
GEN_TERMS = [("P1 response", "4 hours"), ("P2 response", "1 business day"), ("P3 response", "5 business days"),
             ("Completion target", "95%"), ("First-time fix target", "85%"), ("Recall window", "28 days")]
GEN_RATES = {"SFLT": (74, 111), "PCFR": (62, 93)}
#: One contract per vendor: scoring reads a vendor's newest confirmed parameter set, and two
#: sets signed the same day block it (FR-035), so Heating folds into Mechanical.
CONTRACTS = [
    ("APXM", "Mechanical PPM · Bishopsgate", "AHUs, chillers, pumps, FCUs, boilers", 32, 210000),
    ("APXL", "Lifts · Bishopsgate", "Passenger lifts: monthly service, LOLER support", 12, 38000),
    ("SFLT", "Lift LOLER support · Bishopsgate", "LOLER examination support and lift call-outs", 2, 9000),
    ("NGEL", "Electrical · Bishopsgate", "LV distribution, EICR, emergency lighting", 4, 21000),
    ("PCFR", "Fire and security · Bishopsgate", "Fire alarm, detection, FRA", 4, 16000),
    ("CLWC", "Water hygiene · Bishopsgate", "L8 monitoring, TMVs, tanks and calorifiers", 12, 14000),
]
CONTRACT_OF_VENDOR = {c[0]: c[1] for c in CONTRACTS}
#: Engine defaults for what a contract does not state (svc-operations-intelligence
#: engines/contract_performance/parameters.SYSTEM_DEFAULTS). The prototype's terms never give
#: completion hours, so every vendor is held to these; seed_bishopsgate_vendor_terms.py --prove
#: re-derives every outcome with the engine's own functions, so drift here shows up there.
DEFAULT_HOURS = {"response": {"p1": 1, "p2": 4, "p3": 24, "p4": 72},
                 "completion": {"p1": 4, "p2": 24, "p3": 72, "p4": 168}}
#: The prototype's vendor certificates by name -> UK pack type code (None: not in the pack).
CERT_CODE = {"Employers' Liability Insurance": "CONTRACTOR_EL_INSURANCE",
             "Public Liability Insurance": "CONTRACTOR_PL_INSURANCE", "F-Gas Company Certificate": "REFCOM",
             "SafeContractor / SSIP": "CHAS_SSIP", "ISO 45001": None, "ISO 14001": "ISO_14001",
             "NICEIC Approved Contractor": "NICEIC", "ISO 9001": "ISO_9001", "NAPIT Registration": "NAPIT",
             "LOLER Thorough Examination competence": "LOLER_CP", "LEIA Membership": "LEIA",
             "LCA Registration": "LCA"}
CERT_MONTHS = {"ISO_9001": 36, "ISO_14001": 36, "BAFE_SP203_1": 36}
FAULTS = {
    "Air handling": ["Supply fan tripped on overload", "Filter alarm — high differential pressure",
                     "Damper actuator not responding", "Drive belt slipping — fan speed low", "Condensate tray blocked"],
    "Chiller": ["Tripped on high discharge pressure", "Low chilled-water flow alarm", "Compressor short-cycling",
                "Condenser fan fault", "Refrigerant leak alarm"],
    "Pump": ["Mechanical seal weeping", "Pump noisy — bearing wear", "VSD fault on CHW pump"],
    "Fan coil": ["FCU not holding set point", "FCU condensate leak onto ceiling", "FCU fan noisy"],
    "Boiler": ["Boiler lockout — ignition failure", "Boiler low water pressure", "Flue gas analyser alarm"],
    "Lift": ["Out of service — landing door fault", "Levelling error at L12", "Alarm phone fault",
             "Car stopped between floors — passenger release"],
    "Generator": ["Failed to start on weekly test", "Battery charger fault", "Coolant low alarm"],
    "LV board": ["Outgoing way tripped — L7 lighting", "Thermography hot spot on busbar",
                 "Emergency lighting circuit fault"],
    "Fire panel": ["Panel fault — loop 2", "Detector contaminated — L9 east", "Sounder circuit fault"],
    "Water system": ["TMV failed temperature check — L6", "Cold water tank above 20 °C",
                     "Calorifier flow below 60 °C", "Dead-leg flushing overdue — L14"],
}


def js_round(x: float) -> int:
    """Math.round, which the Vendors page applies to every measured percentage."""
    return math.floor(x + 0.5)


def pkey(priority: str | None) -> str:
    """The engine's priority bucket (scoring._priority_key)."""
    p = (priority or "P3").upper().replace(" ", "")
    if p in {"P1", "HIGHEST", "CRITICAL", "1"}:
        return "p1"
    if p in {"P2", "HIGH", "2"}:
        return "p2"
    if p in {"P4", "LOW", "LOWEST", "4"}:
        return "p4"
    return "p3"


def term_hours(value: str | None) -> float | None:
    """'4 hours' -> 4, '1 business day' -> 24 (a business day counted as 24 elapsed hours)."""
    m = re.match(r"\s*([\d.]+)\s*(hours?|business days?)", value or "")
    if not m:
        return None
    n = float(m.group(1))
    return n if m.group(2).startswith("hour") else n * 24


def vendor_perf(vp: dict, hw: dict, vcode: str) -> dict:
    """Measured KPIs, trend, contract and terms for one vendor, from the prototype or generated."""
    if vcode in VP_ID:
        v = vp["V"][VP_ID[vcode]]
        m = dict(v["measured"])
        m["trend"] = next(x["trend"] for x in hw["vendors"] if x["id"] == VP_ID[vcode])
        terms = [dict(t) for t in v["terms"]]
        contract = dict(v["contract"])
        src = f"prototype HOISTRA_VP.V.{VP_ID[vcode]}"
    else:
        m = dict(GEN_PERF[vcode])
        std, ooh = GEN_RATES[vcode]
        terms = [dict(label=a, value=b, src="contract", clause=None, page=0) for a, b in GEN_TERMS]
        terms += [dict(label="Labour rate — standard", value=f"£{std} / hr", src="contract", clause=None, page=0),
                  dict(label="Labour rate — out of hours", value=f"£{ooh} / hr", src="contract", clause=None, page=0)]
        contract = dict(GEN_CONTRACT[vcode])
        src = "generated (the prototype has only this vendor's compliance row)"
    by = {t["label"]: t["value"] for t in terms}
    resp = {"p1": term_hours(by.get("P1 response")), "p2": term_hours(by.get("P2 response")),
            "p3": term_hours(by.get("P3 response")), "p4": None}
    resp = {k: (v if v is not None else DEFAULT_HOURS["response"][k]) for k, v in resp.items()}
    rate = lambda lbl: float(re.sub(r"[^\d.]", "", by[lbl])) if by.get(lbl) else None  # noqa: E731
    return dict(m=m, terms=terms, contract=contract, src=src, resp=resp, comp=dict(DEFAULT_HOURS["completion"]),
                std=rate("Labour rate — standard"), ooh=rate("Labour rate — out of hours"))


def wo_outcome(row: list, perf: dict) -> tuple[bool, bool, bool, bool] | None:
    """(response met, completion met, first fix, recall) for a Work_Orders row, the engine's way."""
    rep, att, comp = (dt.datetime.fromisoformat(x) if x else None for x in (row[7], row[8], row[9]))
    if not comp:
        return None
    k = pkey(row[5])
    hours = lambda a, b: (b - a).total_seconds() / 3600 if a and b else None  # noqa: E731
    r, c = hours(rep, att), hours(rep, comp)
    return (r is not None and r <= perf["resp"][k], c is not None and c <= perf["comp"][k],
            str(row[10]).lower() in ("yes", "true", "1"), str(row[11]).lower() in ("yes", "true", "1"))


def month_window(as_of: dt.date, back: int) -> tuple[dt.datetime, dt.datetime]:
    """The calendar month `back` months before as_of's, ending yesterday for the current one."""
    y, m = as_of.year, as_of.month - back
    while m <= 0:
        y, m = y - 1, m + 12
    start = dt.datetime(y, m, 1, 7, 0)
    if back == 0:
        end = dt.datetime.combine(as_of - dt.timedelta(days=1), dt.time(17, 0))
    else:
        nxt = dt.date(y + (m == 12), m % 12 + 1, 1)
        end = dt.datetime.combine(nxt - dt.timedelta(days=1), dt.time(17, 0))
    return start, end


def month_back(as_of: dt.date, d: dt.date) -> int:
    return (as_of.year - d.year) * 12 + as_of.month - d.month


def exact_counts(targets: dict[str, int], have: dict[str, int], n_have: int, lo: int, hi: int):
    """The smallest month size whose positives display as exactly `targets` once rounded, given
    the rows already in that month. Misses must nest - a missed response is also a missed
    completion, a recall is also a failed first fix - as they do in a real job."""
    for n in range(max(lo, n_have + 4), hi + 1):
        ks = {}
        for key, t in targets.items():
            cand = [c for c in range(n + 1) if js_round(100 * c / n) == t
                    and have[key] <= c <= have[key] + (n - n_have)]
            if not cand:
                break
            ks[key] = cand[-1]
        else:
            new = n - n_have
            miss = {k: (n - ks[k]) - (n_have - have[k]) for k in ks}
            if miss["comp"] >= miss["resp"] and miss["ff"] >= miss["norecall"] and max(miss.values()) <= new:
                return n, ks
    raise SystemExit(f"no month size in {lo}..{hi} shows {targets} exactly")


def gen_vendor_work_orders(rng, as_of, assets, vp, hw, base_wos, months=6) -> tuple[list[list], dict]:
    """Completed work orders per vendor per month, so the engine's scorecards come out as the
    prototype's figures: the newest month exactly (the Vendors page shows it), the five before
    drifting the way the prototype says each vendor is trending."""
    perf = {v[0]: vendor_perf(vp, hw, v[0]) for v in VENDORS}
    by_vendor = {}
    for a in assets:
        by_vendor.setdefault(VCODE[a["vendor"]], []).append(a)
    by_vendor.setdefault("SFLT", [a for a in assets if a["cls"] == "Lift"])
    out, report, seq = [], {}, 6001
    # Named rows: the prototype's Bishopsgate breaches, and the work behind its invoice lines.
    named = {  # (vendor, months back) -> [(wo number, asset suffix, priority, outcome, desc, labour, parts)]
        ("APXM", 0): [("4188", "CHILLER-101", "P2", (True, False, True, False), "CHILLER-101 low flow — completion missed by 2 days", 5.5, 240),
                      ("4204", "AHU-03", "P2", (True, True, False, False), "AHU-3 fan vibration — three visits to resolve", 4.0, 180),
                      ("4290", "CHILLER-101", "P2", (True, True, False, True), "CHILLER-101 tripped again — returned day 11", 3.0, 0)],
        ("APXL", 0): [("4377", "LIFT-4471", "P1", (False, False, True, False), "Lift out of service — response 5.1 h against 4 h", 2.5, 95)],
        ("APXM", month_back(as_of, dt.date(2026, 8, 1))): [
            ("4262", "AHU-07", "P3", (True, True, True, False), "AHU-7 coil replacement — out-of-hours shutdown", 32.0, 0)],
        ("APXM", month_back(as_of, dt.date(2026, 7, 1))): [
            ("4239", "CHILLER-102", "P3", (True, True, True, False), "Compressor seal kit replaced", 6.0, 1208)],
        ("APXL", month_back(as_of, dt.date(2026, 7, 1))): [
            ("4318", "LIFT-4471", "P2", (True, True, True, False), "Call-out — car stopped at L2", 1.0, 0)],
        ("CLWC", month_back(as_of, dt.date(2026, 7, 1))): [
            ("4325", "DHW-01", "P3", (True, True, True, False), "Calorifier descale and TMV re-set, all floors", 18.0, 0)],
    }
    for vcode, *_ in VENDORS:
        pf, pool = perf[vcode], by_vendor.get(vcode) or []
        vname = next(v[1] for v in VENDORS if v[0] == vcode)          # the vendor doing the job, not the asset's
        m0 = pf["m"]
        targets0 = {"resp": m0["sla_r"], "comp": m0["sla_c"], "ff": m0["firstfix"], "norecall": 100 - m0["recall"]}
        drift = {"declining": 1.5, "improving": -1.5}.get(m0["trend"], 0.0)
        report[vcode] = {"targets": targets0, "trend": m0["trend"], "months": {}}
        for back in range(months):
            start, end = month_window(as_of, back)
            existing = []
            for w in base_wos:
                if VCODE.get(w[1]) != vcode or not w[9]:
                    continue
                c = dt.datetime.fromisoformat(w[9])
                if start.date() <= c.date() <= end.date():
                    existing.append(wo_outcome(w, pf))
            fixed = named.get((vcode, back), [])
            have = {"resp": sum(o[0] for o in existing) + sum(f[3][0] for f in fixed),
                    "comp": sum(o[1] for o in existing) + sum(f[3][1] for f in fixed),
                    "ff": sum(o[2] for o in existing) + sum(f[3][2] for f in fixed),
                    "norecall": sum(not o[3] for o in existing) + sum(not f[3][3] for f in fixed)}
            n_have = len(existing) + len(fixed)
            if back == 0:
                n, ks = exact_counts(targets0, have, n_have, 30, 100)
                n0 = n
            else:
                t = {k: min(99, max(40, v + drift * back * (1 if k != "norecall" else 0.35))) for k, v in targets0.items()}
                n = max(n_have + 6, n0 + rng.randint(-4, 4))
                ks = {k: min(have[k] + (n - n_have), max(have[k], round(v * n / 100))) for k, v in t.items()}
            new = n - n_have
            resp_miss = (n - ks["resp"]) - (n_have - have["resp"])
            comp_miss = (n - ks["comp"]) - (n_have - have["comp"])
            ff_miss = (n - ks["ff"]) - (n_have - have["ff"])
            recalls = (n - ks["norecall"]) - (n_have - have["norecall"])
            # SLA outcomes nest (a missed response is a missed completion, except on P4, whose
            # completion window outlasts its response window); so do recall within first fix.
            sla = [(False, False)] * min(resp_miss, comp_miss) + [(False, True)] * max(0, resp_miss - comp_miss) \
                + [(True, False)] * max(0, comp_miss - resp_miss)
            sla += [(True, True)] * (new - len(sla))
            fix = [(False, True)] * recalls + [(False, False)] * (ff_miss - recalls)
            fix += [(True, False)] * (new - len(fix))
            rng.shuffle(sla)
            rng.shuffle(fix)
            rows = [(f[0], f[1], f[2], f[3], f[4], f[5], f[6]) for f in fixed]
            for (rm, cm), (ff, rc) in zip(sla, fix):
                a = rng.choice(pool)
                pr = "P4" if (not rm and cm) else rng.choices(["P1", "P2", "P3"], weights=[12, 38, 50])[0]
                rows.append((str(seq), a["code"][len(CODE) + 1:], pr, (rm, cm, ff, rc),
                             rng.choice(FAULTS.get(a["cls"], ["Reactive fault"])), round(rng.uniform(1.5, 6.5), 1),
                             rng.choice([0, 0, 45, 85, 120, 240, 380])))
                seq += 1
            for num, asuf, pr, (rm, cm, ff, rc), desc, labour, parts in rows:
                a = next(x for x in assets if x["code"] == f"{CODE}-{asuf}")
                k = pkey(pr)
                rt, ct = pf["resp"][k], pf["comp"][k]
                if rm:
                    att_h = (min(rt, ct) if cm else rt) * rng.uniform(0.2, 0.8)
                else:
                    att_h = 5.1 if num == "4377" else rt * rng.uniform(1.15, 1.8)
                if cm:
                    comp_h = rng.uniform(max(att_h + 0.25, ct * 0.35), ct * 0.97)
                else:
                    comp_h = ct + 48 if num == "4188" else max(att_h + 0.5, ct * rng.uniform(1.15, 2.0))
                span = (end - start).total_seconds() / 3600 - comp_h
                rep = start + dt.timedelta(hours=rng.uniform(0, max(1.0, span)))
                rep = rep.replace(minute=rep.minute // 5 * 5, second=0, microsecond=0)
                att, comp = rep + dt.timedelta(hours=att_h), rep + dt.timedelta(hours=comp_h)
                est = rng.choice([180, 240, 320, 450, 620, 880]) if labour < 20 else 3900
                act = round(est * rng.uniform(0.85, 1.25))
                title = f"{a['name']} — {desc}" if not desc.startswith(a["name"].split(" ")[0]) else desc
                out.append([f"WO-{CODE}-{num}", vname, a["code"], a["name"], desc, pr, "Completed",
                            iso(rep), iso(att), iso(comp), "yes" if ff else "no", "yes" if rc else "no", labour, parts,
                            est, act, CODE, "Reactive", iso(rep + dt.timedelta(hours=ct)), title[:120]])
            report[vcode]["months"][start.strftime("%Y-%m")] = {"n": n, **ks}
    return out, {"perf": perf, "report": report}


#: Asset code per prototype asset name, and the four generated ones.
ASSET_CODE = {
    "AHU-3": "AHU-03", "CHILLER-101": "CHILLER-101", "CHILLER-102": "CHILLER-102",
    "CHW pump P1": "PUMP-01", "FCU L4-12": "FCU-12", "AHU-7": "AHU-07",
    "Lift Asset-4471": "LIFT-4471", "Standby generator GEN-1": "GEN-01",
}
MAKE = {  # generated: manufacturer, model
    "Air handling": ("Trane", "CLCP 034"), "Chiller": ("Carrier", "30XA 1002"), "Pump": ("Grundfos", "NBG 125"),
    "Fan coil": ("Daikin", "FWD 10"), "Lift": ("Otis", "Gen2 Premier"), "Generator": ("Cummins", "C825 D5"),
    "Boiler": ("Hoval", "UltraGas 450"), "Fire panel": ("Advanced", "MxPro 5"), "LV board": ("Schneider", "Prisma P"),
    "Water system": ("Andrews", "MAXXflo Evo calorifier"),
}

#: Normal ranges. The first fifteen are the spec's (and Northbridge's) and are also GEN-1's
#: own limits in the prototype; the rest are AHU-3's limits from the prototype's IoT feed.
BANDS = [
    ("temperature", "°C", 5, 95, "plant operating range"), ("temperature_supply", "°C", 5, 20, "chilled water supply"),
    ("temperature_return", "°C", 8, 25, "chilled water return"), ("coolant_temp", "°C", 70, 95, "generator coolant"),
    ("pressure", "bar", 3, 5.5, "system pressure"), ("pressure_discharge", "bar", 12, 22, "compressor discharge"),
    ("oil_pressure", "bar", 3, 5.5, "engine oil"), ("vibration", "mm/s", 0, 7.1, "ISO 10816 class II"),
    ("frequency", "Hz", 49.5, 50.5, "output frequency"), ("voltage", "V", 216, 253, "supply voltage"),
    ("voltage_output", "V", 216, 253, "output voltage"), ("battery_voltage", "V", 25.5, 28.5, "starter battery"),
    ("load_percentage", "%", 30, 100, "load on test"), ("fuel_level", "%", 60, 100, "day tank"),
    ("exhaust_temp", "°C", 350, 550, "exhaust gas"),
    ("supply_air_temp", "°C", 13, 16, "AHU supply air"), ("return_air_temp", "°C", 21, 24, "AHU return air"),
    ("humidity", "%", 40, 60, "relative humidity"), ("filter_dp", "Pa", 50, 250, "filter differential pressure"),
    ("fan_current", "A", 12, 17.5, "fan motor current"), ("fan_vibration", "mm/s", 0, 4.5, "AHU fan vibration"),
    ("co2", "ppm", 400, 1000, "zone CO2"), ("run_hours_week", "h", 60, 84, "run hours per week"),
]
#: The prototype's IoT reading names, as band reading types.
IOT_TYPE = {
    "Coolant temp": "coolant_temp", "Oil pressure": "oil_pressure", "Battery voltage": "battery_voltage",
    "Fuel level": "fuel_level", "Frequency on test": "frequency", "Vibration": "vibration",
    "Exhaust temp": "exhaust_temp", "Load on test": "load_percentage",
    "Supply air temp": "supply_air_temp", "Return air temp": "return_air_temp", "Relative humidity": "humidity",
    "Filter ΔP": "filter_dp", "Fan motor current": "fan_current", "Fan vibration": "fan_vibration",
    "CO₂ (zone)": "co2", "Run hours / week": "run_hours_week",
}


#: AHU-3's non-occupancy spike, from the prototype: Saturday 02:00-06:00 on the last three
#: weekends, 847 kWh against a 210 kWh weekend baseline - the excess spread over 8 half-hours.
SPIKE_KWH = (847 - 210) / 8
#: The prototype's four section sub-meters: section -> meter tag.
SECTION_METERS = {"Central plant · basement": "PLANT", "L4 East · tenant floor": "L4E",
                  "L12–L20 · tenant floors": "L12L20", "Car park": "CARPARK"}


def _slots(as_of: dt.date) -> tuple[list[dt.datetime], set[dt.datetime]]:
    """Every half-hour of the year ending yesterday 23:30Z, and AHU-3's spike half-hours."""
    end_slot = dt.datetime.combine(as_of - dt.timedelta(days=1), dt.time(23, 30), tzinfo=dt.timezone.utc)
    slots = [end_slot - dt.timedelta(minutes=30 * k) for k in range(17519, -1, -1)]
    saturdays = sorted({s.date() for s in slots if s.weekday() == 5})[-3:]
    return slots, {s for s in slots if s.date() in saturdays and 2 <= s.hour < 6}


def _elec_shape(s: dt.datetime) -> float:
    day = 1.0 if 7 <= s.hour < 19 else 0.4
    return day * (0.55 if s.weekday() >= 5 else 1.0) * (1.15 if s.month in (6, 7, 8) else 1.0)


def _gas_shape(s: dt.datetime) -> float:
    month = {11: 2.5, 12: 2.5, 1: 2.5, 2: 2.5, 3: 2.2, 4: 1.6, 5: 1.2, 10: 1.8, 9: 1.1}.get(s.month, 1.0)
    day = 1.0 if 6 <= s.hour < 18 else (0.05 if s.month in (6, 7, 8) else 0.15)
    return month * day * (0.7 if s.weekday() >= 5 else 1.0)


def finish(companion: str, as_of: dt.date, seed: int) -> dict:
    """Add what the floor companion cannot derive: the prototype's four section sub-meters,
    sized to the section EUIs it states, and AHU-3's spike on AHU-3's own asset meter.

    Run after build_floor_submeters.py and before build_complete_workbook.py."""
    rng = random.Random(seed + 7)
    wb = openpyxl.load_workbook(companion)
    em, mr = wb["Energy_Meters"], wb["Meter_Readings"]
    mh = [c.value for c in em[1]]
    rh = [c.value for c in mr[1]]
    ri = {h: i for i, h in enumerate(rh)}
    existing = {r[0] for r in em.iter_rows(min_row=2, values_only=True)}
    slots, spike_slots = _slots(as_of)
    window = [s for s in slots if s >= slots[-1] - dt.timedelta(days=90)]   # both ends, as build_floor_submeters.py counts them
    added = 0
    for sname, tag in SECTION_METERS.items():
        ref = f"{CO}-{CODE}-S-{tag}-E"
        if ref in existing:
            continue
        row = dict(meter_ref=ref, building_code=CODE, site_ref=SITE, meter_type="electricity", mpan=ref, mprn=None,
                   is_sub_meter="true", section_name=sname, asset_code=None,
                   tariff_gbp_per_kwh=TARIFF["electricity"], carbon_kg_per_kwh=CARBON["electricity"],
                   active="true", description=f"{sname} sub-meter · BMS trend")
        em.append([row.get(h) for h in mh])
        eui = next(x[6] for x in SECTIONS if x[0] == sname)
        area = next(x[3] for x in SECTIONS if x[0] == sname)
        # Sized over its own 90-day window, which is what the page annualises: sizing over the
        # year put a summer window 7% above the section EUI the prototype states.
        base = eui * area * (len(window) / 17520) / sum(_elec_shape(s) for s in window)
        for s in window:
            v = base * _elec_shape(s) * rng.uniform(0.95, 1.05)
            if sname == "L4 East · tenant floor" and s in spike_slots:
                v += SPIKE_KWH                                   # AHU-3 serves L4 East
            vals = {"meter_ref": ref, "building_code": CODE, "meter_type": "electricity",
                    "reading_at": s.strftime("%Y-%m-%dT%H:%M:%SZ"), "consumption_kwh": round(v, 3),
                    "period_minutes": 30, "source": "bms"}
            mr.append([vals.get(h) for h in rh])
            added += 1
    # AHU-3's asset meter carries the spike it is blamed for.
    # Each Saturday night totals the prototype's 847 kWh on AHU-3's own meter.
    ahu = f"{CO}-{CODE}-A-AHU-03"
    nights: dict[dt.date, list] = {}
    for row in mr.iter_rows(min_row=2):
        if row[ri["meter_ref"]].value != ahu:
            continue
        at = dt.datetime.fromisoformat(str(row[ri["reading_at"]].value).replace("Z", "+00:00"))
        if at in spike_slots:
            nights.setdefault(at.date(), []).append(row[ri["consumption_kwh"]])
    bumped = 0
    for cells in nights.values():
        have = sum(float(c.value or 0) for c in cells)
        for c in cells:
            c.value = round(float(c.value or 0) + (847 - have) / len(cells), 3)
            bumped += 1
    wb.save(companion)
    return {"section_readings": added, "ahu3_spike_halfhours": bumped}


def iso(d) -> str | None:
    if d is None:
        return None
    return d.strftime("%Y-%m-%dT%H:%M") if isinstance(d, dt.datetime) else d.isoformat()


def build(proto: dict, out_path: str, as_of: dt.date, org_users: list[tuple[str, str]], seed: int) -> dict:
    rng = random.Random(seed)
    shift = as_of - PROTOTYPE_AS_OF
    sh = lambda d: (d + shift) if d else None                     # noqa: E731
    H, CC, AS, MX = proto["HOISTWAY"], proto["HOISTRA_CC"], proto["HOISTRA_AS"], proto["HOISTRA_MX"]
    isb = lambda o: o.get("b") == BUILDING or o.get("building") == BUILDING or o.get("holder") == BUILDING  # noqa: E731
    classes = AS["classes"]
    bld_cc = next(b for b in CC["buildings"] if b["name"] == BUILDING)
    sheets: dict[str, list[list]] = {k: [] for k in ORDER}
    origin: list[tuple[str, str, str]] = []                       # (sheet, row, where it came from)

    # Sites, Buildings, sections
    sheets["Sites"].append([SITE, BUILDING, "London", "EC2M 9ZZ", bld_cc["state"], "fm@plenum-technologies.example"])
    sheets["Buildings"].append([CODE, BUILDING, SITE, "UK", bld_cc["use"], FLOORS, round(GIA_M2 * 10.7639, 2),
                                GIA_M2, None, None])
    for name, stype, floor, area, ref, src, _ in SECTIONS:
        sheets["Building_Sections"].append([CODE, name, stype, floor, area, ref, src])
    proto_secs = {s["sec"]: s for s in AS["sections"] if s["b"] == BUILDING}
    origin += [("Building_Sections", s[0], "prototype" if s[0] in proto_secs else "generated to make up the GIA")
               for s in SECTIONS]

    # Vendors and contracts
    VP = proto["HOISTRA_VP"]
    for code, name, trade, addr, phone, acc in VENDORS:
        sheets["Vendors"].append([code, name, trade, addr, phone, "United Kingdom", acc, *BLOCK.get(code, ("Clear", None, None))])
    ppm_proto = next(p for p in MX["ppm"] if "Bishopsgate" in p["contract"])
    for vcode, cname, scope, visits, value in CONTRACTS:
        pf = vendor_perf(VP, H, vcode)
        ct = pf["contract"]
        # The terms travel with the contract row as JSON: seed_bishopsgate_vendor_terms.py hands
        # them to the engine's contract ingest, which is what turns them into a parameter set.
        sla = json.dumps({"contract_ref": ct["ref"], "signed": sh(pdate(ct["signed"])).isoformat(),
                          "expires": sh(pdate(ct["expires"])).isoformat(), "pages": ct.get("pages"),
                          "source": pf["src"], "business_day_hours": 24, "terms": pf["terms"]}, ensure_ascii=False)
        sheets["Vendor_Contracts"].append([cname, vcode, next(v[1] for v in VENDORS if v[0] == vcode), scope,
                                           visits, sh(pdate(ct["signed"])), sh(pdate(ct["expires"])), value, "UK",
                                           "active", sla])
        origin.append(("Vendor_Contracts", f"{cname} ({ct['ref']})", pf["src"]))

    # Technicians - logins from the org when it exists
    techs = [("E-301", "Priya Raman", "Mechanical", "Gas Safe"), ("E-302", "Tom Okafor", "Electrical", "NICEIC")]
    for i, (eid, nm, trade, cert) in enumerate(techs):
        uid, uname = org_users[i % len(org_users)] if org_users else (None, nm)
        sheets["Technicians"].append([eid, nm, trade, SITE, cert, uname, SITE, uid])

    # Assets: the prototype's eight, then four generated
    insp_by_asset: dict[str, list[dict]] = {}
    for i in MX["inspections"]:
        if isb(i):
            insp_by_asset.setdefault(i["asset"], []).append(i)
    assets = []
    for a in [x for x in AS["assets"] if isb(x)] + [g for g in AS["iot"] if isb(g) and g["name"] not in
                                                     [y["name"] for y in AS["assets"]]]:
        cls = a["cls"].split(" · ")[0]
        code = f"{CODE}-{ASSET_CODE[a['name']]}"
        cl = classes.get(cls, {})
        cond = max([i["cond"] for i in insp_by_asset.get(a["name"], [])] or [2])
        sec = a["sec"] if a["sec"] in proto_secs else "Central plant · basement"
        assets.append(dict(code=code, name=a["name"], cls=cls, sec=sec, vendor=a["vendor"],
                           installed=dt.date(int(a["installed"]), 3, 15), l1=a.get("l1"),
                           life=a.get("designLife") or cl.get("life", 20), value=cl.get("replace", 20000),
                           wear=cl.get("wear", 1.0), cond=cond, ppm=a.get("ppm"), src="prototype"))
    for code, name, cls, sec, vendor, yr, val, life, l1 in [
        ("BOILER-01", "Boiler 1 — central plant", "Boiler", "Central plant · basement", "Apex Mechanical", 2012, 60000, 20, True),
        ("BOILER-02", "Boiler 2 — central plant", "Boiler", "Central plant · basement", "Apex Mechanical", 2012, 60000, 20, False),
        ("FIRE-PANEL-01", "Fire alarm panel — ground", "Fire panel", "Ground · reception and common", "ProudCastle Fire", 2016, 14000, 15, True),
        ("DB-01", "Main LV switchboard", "LV board", "Central plant · basement", "Northgate Electrical", 2009, 85000, 30, True),
        ("DHW-01", "Domestic water — tanks, calorifiers and TMVs", "Water system", "Central plant · basement",
         "Clearwater Compliance", 2011, 45000, 25, True),
    ]:
        assets.append(dict(code=f"{CODE}-{code}", name=name, cls=cls, sec=sec, vendor=vendor, installed=dt.date(yr, 3, 15),
                           l1=l1, life=life, value=val, wear=0.9, cond=2, ppm=None, src="generated"))
    # The chiller the floor-meter builder puts its excursion on is the FIRST chiller in the
    # sheet: CHILLER-101, whose anomaly the prototype names.
    assets.sort(key=lambda a: (0 if a["code"].endswith("CHILLER-101") else 1))
    for a in assets:
        make = MAKE.get(a["cls"], ("Generic", "-"))
        sheets["Assets"].append([a["code"], a["name"], make[0], make[1], SITE, BUILDING, "Active", a["installed"],
                                 a["vendor"], a["value"], "GBP", a["life"], a["wear"], a["cond"],
                                 "high" if a["l1"] else "medium", a["sec"], CODE])
        origin.append(("Assets", a["code"], a["src"]))
    code_of = {a["name"]: a["code"] for a in assets}

    # Readings bands and asset readings
    for b in BANDS:
        sheets["Asset_Reading_Bands"].append(list(b))
    last_hour = dt.datetime.combine(as_of - dt.timedelta(days=1), dt.time(23, 0))
    for g in [x for x in AS["iot"] if isb(x)]:
        for r in g["readings"]:
            hist = r.get("hist") or [r["v"]]
            for k, v in enumerate(hist):
                t = last_hour - dt.timedelta(hours=len(hist) - 1 - k)
                sheets["Asset_Readings"].append([code_of[g["name"]], IOT_TYPE[r["k"]], v, r["u"], t.strftime("%Y-%m-%dT%H:%M:%S")])
    GEN_READ = {"Chiller": [("temperature_supply", "°C", 7.0, 0.4), ("temperature_return", "°C", 12.5, 0.5),
                            ("pressure_discharge", "bar", 17.0, 1.2), ("load_percentage", "%", 64, 8)],
                "Pump": [("pressure", "bar", 4.2, 0.2), ("vibration", "mm/s", 3.1, 0.6)],
                "Air handling": [("supply_air_temp", "°C", 14.6, 0.5), ("filter_dp", "Pa", 205, 18), ("fan_current", "A", 15.2, 0.7)],
                "Boiler": [("temperature", "°C", 72, 3), ("pressure", "bar", 3.8, 0.2)]}
    iot_names = {g["name"] for g in AS["iot"] if isb(g)}
    band = {b[0]: b for b in BANDS}
    for a in assets:
        if a["name"] in iot_names or a["cls"] not in GEN_READ:
            continue
        flagged = a["name"] in insp_by_asset          # assets with a finding run a little outside band
        for rtype, unit, mean, sd in GEN_READ[a["cls"]]:
            for h in range(48):
                t = last_hour - dt.timedelta(hours=47 - h)
                v = rng.gauss(mean, sd)
                if flagged and rng.random() < 0.06:
                    v = band[rtype][3] * 1.04
                sheets["Asset_Readings"].append([a["code"], rtype, round(v, 2), unit, t.strftime("%Y-%m-%dT%H:%M:%S")])

    # Maintenance plans and PPM visits
    freq = {"AHU-03": ("Monthly", 1), "AHU-07": ("Quarterly", 3), "CHILLER-101": ("Quarterly", 3),
            "CHILLER-102": ("Quarterly", 3), "PUMP-01": ("Quarterly", 3), "FCU-12": ("Quarterly", 3),
            "LIFT-4471": ("Monthly", 1), "BOILER-01": ("Quarterly", 3), "BOILER-02": ("Quarterly", 3),
            "FIRE-PANEL-01": ("Quarterly", 3), "DB-01": ("Quarterly", 3), "GEN-01": ("Monthly", 1),
            "DHW-01": ("Monthly", 1)}
    contract_of = lambda a: CONTRACT_OF_VENDOR[VCODE[a["vendor"]]]  # noqa: E731
    next_mech = sh(pdate(ppm_proto["next"]))
    # The prototype's Mechanical figures: 32 planned, 29 done (2 of them late), 1 missed,
    # 3 deferrals. Statuses are dealt in that proportion across the Mechanical visits.
    mech_status = ["Missed"] + ["Deferred"] * 2 + ["Late"] * 2
    mech_i = 0
    for a in assets:
        tail = a["code"][len(CODE) + 1:]
        fname, months = freq.get(tail, ("Quarterly", 3))
        if a["cls"] == "Generator":
            continue                                   # GEN-1 is run-tested, not on a PPM contract
        nd = next_mech if contract_of(a).startswith("Mechanical") else as_of + dt.timedelta(days=rng.randint(10, 80))
        sheets["Maintenance_Plans"].append([f"PPM-{a['code']}", a["code"], CODE,
                                            f"{contract_of(a)} — {a['name']}", "preventive", "months", months, nd,
                                            "active", a["vendor"]])
        n = 12 // months
        for k in range(n):
            sched = nd - dt.timedelta(days=int(30.44 * months * (k + 1)))
            st = "Completed"
            if contract_of(a).startswith("Mechanical") and mech_i < len(mech_status) and k in (1, 3, 5):
                st = mech_status[mech_i]
                mech_i += 1
            done = None
            if st == "Completed":
                done = sched + dt.timedelta(days=rng.randint(-3, 2))
            elif st == "Late":
                done, st = sched + dt.timedelta(days=rng.randint(18, 26)), "Completed"
            task = {"Air handling": "AHU service — filters, belts, coils", "Chiller": "Chiller service — refrigerant, condenser, oil",
                    "Pump": "Pump service — seals, bearings, alignment", "Fan coil": "FCU service — filter, condensate, fan",
                    "Lift": "Lift maintenance visit", "Boiler": "Boiler service — combustion analysis, flue",
                    "Fire panel": "Fire alarm service — detectors, sounders, panel", "LV board": "LV board thermography and inspection",
                    "Water system": "L8 monitoring — temperatures, TMVs, tank inspection"}[a["cls"]]
            sheets["PPM_Visits"].append([f"PPM-{a['code']}-{k + 1:02d}", a["vendor"], a["code"], task, fname, sched, done,
                                         14, st, contract_of(a), CODE])

    # Work orders: the prototype's four, then the four its inspections sit on
    now = dt.datetime.combine(as_of, dt.time(9, 0))
    wos = []
    for w in [x for x in H["workorders"] if isb(x)]:
        insp = next((i for i in MX["inspections"] if i["wo"] == w["id"]), None)
        a = code_of[w["asset"]]
        est = float(re.sub(r"[^\d.]", "", w["est"]))
        if w["status"] == "Completed" and insp:
            rep = dt.datetime.combine(sh(pdate(insp["date"])), dt.time(8, 20))
            att, comp, act = rep + dt.timedelta(hours=2), rep + dt.timedelta(hours=7), est
            desc = "; ".join(insp["findings"])
        else:
            rep = now - dt.timedelta(days={"WO-4527": 3, "WO-4533": 1, "WO-4551": 1}.get(w["id"], 2))
            att = rep + dt.timedelta(hours=3) if w["status"] == "In progress" else None
            comp, act = None, None
            desc = {"WO-4527": "AHU-3 supply fan — bearing degradation signature: motor current 19% above 30-day baseline for 72 h",
                    "WO-4533": "LOLER thorough examination for Lift Asset-4471 before expiry",
                    "WO-4551": "FCU L4-12 not holding set point — tenant complaint, L4 East"}.get(w["id"], w["type"])
        sla_h = {"P1": 24, "P2": 48, "P3": 120}.get(w["priority"], 120)
        wos.append([f"WO-{CODE}-{w['id'][3:]}", w["vendor"], a, w["asset"], desc, w["priority"], w["status"], iso(rep), iso(att),
                    iso(comp), "yes" if comp else None, "no" if comp else None, 6.5 if comp else None, 380 if comp else None,
                    est, act, CODE, w["type"], iso(rep + dt.timedelta(hours=sla_h)), desc[:120]])
    for i in [x for x in MX["inspections"] if isb(x) and x["wo"] not in [w["id"] for w in H["workorders"]]]:
        rep = dt.datetime.combine(sh(pdate(i["date"])), dt.time(8, 0))
        desc = "; ".join(i["findings"])
        wos.append([f"WO-{CODE}-{i['wo'][3:]}", i["vendor"], code_of[i["asset"]], i["asset"], desc, "Planned", "Completed",
                    iso(rep), iso(rep + dt.timedelta(hours=1)), iso(rep + dt.timedelta(hours=5)), "yes", "no", 4.0, 120,
                    420, 420, CODE, i["type"], iso(rep + dt.timedelta(days=5)), f"{i['type']} visit — {i['asset']}"])
    gen_wos, vendor_report = gen_vendor_work_orders(rng, as_of, assets, VP, H, wos)
    wos = wos + gen_wos
    origin.append(("Work_Orders", f"{len(gen_wos)} vendor work orders",
                   "generated so the scorecards reproduce HOISTRA_VP's measured KPIs"))
    sheets["Work_Orders"] = wos

    # Inspections
    for i in [x for x in MX["inspections"] if isb(x)]:
        sheets["Inspections"].append([f"INS-{CODE}-{i['wo'][3:]}", code_of[i["asset"]], sh(pdate(i["date"])),
                                      f"{i['vendor']} engineer", "Condition",
                                      {4: "High", 3: "Medium"}.get(i["cond"], "Low"), "; ".join(i["findings"]),
                                      i["rec"] + (f" ({i['warranty']})" if i.get("warranty") else ""),
                                      "false" if i["recDone"] else "true"])

    # Spare parts - filters below reorder, as the prototype's "filter change deferred — stock"
    for p in [("PRT-AHU-FILTER", "AHU filter set (G4 + F7)", 165, 1, 4, "Apex Mechanical"),
              ("PRT-AHU-BELT", "AHU drive belt set", 140, 2, 4, "Apex Mechanical"),
              ("PRT-CH-CONTACTOR", "Chiller compressor contactor", 310, 3, 2, "Apex Mechanical"),
              ("PRT-R134A", "Refrigerant R134a (12 kg)", 420, 4, 2, "Apex Mechanical"),
              ("PRT-BOILER-IGN", "Boiler ignition electrode", 85, 5, 3, "Apex Mechanical"),
              ("PRT-LIFT-DOORBELT", "Lift door operator belt", 95, 3, 2, "Apex Lifts")]:
        sheets["Spare_Parts"].append(list(p))

    # Energy: the two incoming meters and a year of half-hours. The four section sub-meters go in
    # the floor companion (finish()), because build_floor_submeters.py derives the floors from
    # every reading in this file by fuel - a sub-meter here would be split across the floors as
    # if it were supply.
    meters = [(f"{CO}-{CODE}-E0", "electricity", "Bishopsgate Tower incoming electricity supply"),
              (f"{CO}-{CODE}-G1", "gas", "Bishopsgate Tower incoming gas supply")]
    for ref, fuel, desc in meters:
        sheets["Energy_Meters"].append([ref, CODE, SITE, fuel, ref if fuel == "electricity" else None,
                                        ref if fuel == "gas" else None, "false", TARIFF[fuel], CARBON[fuel],
                                        "true", desc])
    slots, spike_slots = _slots(as_of)
    works_done = max(dt.datetime.fromisoformat(w[9]) for w in wos if w[9]).replace(tzinfo=dt.timezone.utc)
    end_slot = slots[-1]
    annual = {"electricity": EUI * GIA_M2 * ELEC_SHARE, "gas": EUI * GIA_M2 * (1 - ELEC_SHARE)}
    readings = []
    for ref, fuel, _ in meters:
        shape = _elec_shape if fuel == "electricity" else _gas_shape
        base = annual[fuel] / sum(shape(s) for s in slots)
        for s in slots:
            v = base * shape(s) * rng.uniform(0.94, 1.06)
            if fuel == "electricity":
                if not (7 <= s.hour < 19) and s >= end_slot - dt.timedelta(weeks=6):
                    v *= 1.15                                    # baseline_drift
                if works_done <= s < works_done + dt.timedelta(weeks=3):
                    v *= 1.08                                    # post_works_regression
                if s in spike_slots:
                    v += SPIKE_KWH                               # AHU-3 non-occupancy spike
            readings.append([ref, CODE, fuel, s.strftime("%Y-%m-%dT%H:%M:%SZ"), round(v, 3), 30, "dcc"])
    sheets["Meter_Readings"] = readings

    # Compliance certificates
    certs = []

    def cert(num, typ, scope, asset, vcode, issuer, inspector, acc, issue, months, result, status="valid",
             defects=None, remedial=None, rstatus=None, rating=None, score=None, exp=None):
        exp = exp or issue + dt.timedelta(days=int(30.44 * months))
        vname = next((v[1] for v in VENDORS if v[0] == vcode), None)
        certs.append([num, typ, scope, CODE, asset, vcode, vname, issuer, inspector, acc, issue, exp, exp, months,
                      result, status, "UK", defects, remedial, rstatus, rating, score])

    lol = next(c for c in H["certificates"] if isb(c) and c["type"] == "LOLER")
    fgas = next(c for c in H["certificates"] if isb(c) and c["type"] == "F-Gas")
    fra = next(c for c in CC["certs"] if isb(c) and c["nm"] == "Fire Risk Assessment")
    add = lambda d, m: d - dt.timedelta(days=int(30.44 * m))      # noqa: E731 - issue from expiry
    cert(f"LOLER-{CODE}-4471", "LOLER", "Asset", code_of[lol["asset"]], "APXL", "Apex Lifts", "Dan Reyes", "LEIA 2291",
         add(sh(pdate(lol["expiry"])), 6), 6, "Satisfactory")
    cert(f"FGAS-{CODE}-CH101", "FGAS", "Asset", code_of[fgas["asset"]], "APXM", "Apex Mechanical", "Sam Whitlock",
         "REFCOM 55120", add(sh(pdate(fgas["expiry"])), 12), 12, "Leak check passed")
    cert(f"FRA-{CODE}-2025", "FRA", "Building", None, "PCFR", "ProudCastle Fire", "Helen Marsh", "IFE 88231",
         add(sh(pdate(fra["exp"])), 12), 12, "Significant findings", status="expired",
         defects="Compartmentation breaches at L12 riser", remedial="Fire-stop riser penetrations; re-assess",
         rstatus="open")
    # generated building certificates
    cert(f"EICR-{CODE}-2024-0611", "EICR", "Building", None, "NGEL", "Northgate Electrical", "Owen Price", "NICEIC 034512",
         sh(dt.date(2024, 6, 11)), 60, "Satisfactory")
    cert("0310-4471-2090-6655-2281", "EPC", "Building", None, None, "Kestrel Energy Assessors", "Ruth Callaghan",
         "EPC/NDEA 0219934", sh(dt.date(2019, 4, 2)), 120, "D", rating="D", score=88)
    cert(f"FAS-{CODE}-2026-03", "FIRE_ALARM_SERVICE", "Building", None, "PCFR", "ProudCastle Fire", "Helen Marsh", "IFE 88231",
         sh(dt.date(2026, 4, 2)), 6, "Satisfactory")
    cert(f"FAS-{CODE}-2026-08", "FIRE_ALARM_SERVICE", "Building", None, "PCFR", "ProudCastle Fire", "Helen Marsh", "IFE 88231",
         sh(dt.date(2026, 8, 20)), 6, "Satisfactory with 1 observation", defects="Two detectors on L7 slow to respond",
         remedial="Replace detectors L7-14, L7-15", rstatus="open")
    cert(f"CP17-{CODE}-2026", "CP17", "Building", None, "APXM", "Apex Mechanical", "Sam Whitlock", "Gas Safe 612044",
         sh(dt.date(2026, 1, 20)), 12, "Pass")
    cert(f"TM44-{CODE}-2022", "TM44", "Building", None, "APXM", "Apex Mechanical", "Kiran Shah", "TM44/ACIA 4412",
         sh(dt.date(2022, 5, 9)), 60, "Inspected")
    cert(f"L8-{CODE}-2026", "L8_RISK", "Building", None, "CLWC", "Clearwater Compliance", "Maya Ellis", "LCA 1188",
         sh(dt.date(2025, 11, 3)), 24, "Satisfactory")
    # vendor certificates. The four vendors HOISTRA_VP details carry its certificate list, with
    # its expiry dates (shifted); what it marks "Not on record" is left out, so it shows as a gap.
    n = 0
    for vcode, vid in VP_ID.items():
        for c in VP["V"][vid]["certs"]:
            typ = CERT_CODE.get(c["name"])
            if not typ or c["status"] == "Not on record":
                continue
            months = CERT_MONTHS.get(typ, 12)
            exp = sh(pdate(c["exp"]))
            n += 1
            cert(f"{typ}-{vcode}-{n:02d}", typ, "Vendor", None, vcode, c["ver"].split("— ")[-1], None, None,
                 exp - dt.timedelta(days=int(30.44 * months)), months, c["req"], exp=exp)
    # ...and what else the compliance console says they hold; then SafeLift and ProudCastle,
    # which it shows Blocked - each with the lapsed accreditation that blocks it (generated).
    vend = [("APXM", "GAS_SAFE", "Registered", 12, dt.date(2026, 2, 1), "valid"),
            ("CLWC", "ISO_9001", "Certificated", 36, dt.date(2024, 6, 1), "valid"),
            ("SFLT", "LEIA", "Member", 12, dt.date(2026, 2, 12), "valid"),
            ("SFLT", "CONTRACTOR_EL_INSURANCE", "GBP 5,000,000", 12, dt.date(2025, 11, 20), "valid"),
            ("SFLT", "CONTRACTOR_PL_INSURANCE", "GBP 5,000,000 — lapsed, renewal not supplied", 12, dt.date(2025, 7, 18), "expired"),
            ("PCFR", "CONTRACTOR_PL_INSURANCE", "GBP 5,000,000", 12, dt.date(2026, 5, 1), "valid"),
            ("PCFR", "BAFE_SP203_1", "Registration lapsed", 36, dt.date(2023, 7, 8), "expired")]
    for vcode, typ, result, months, issued, status in vend:
        vname = next(v[1] for v in VENDORS if v[0] == vcode)
        n += 1
        cert(f"{typ}-{vcode}-{n:02d}", typ, "Vendor", None, vcode, vname, None, None, sh(issued), months, result,
             status=status)
    sheets["Compliance_Certificates"] = certs

    # write
    wb = openpyxl.Workbook(write_only=True)
    for name in ORDER:
        ws = wb.create_sheet(name)
        ws.append(HEADERS[name])
        for r in sheets[name]:
            ws.append(r)
    wb.save(out_path)
    return {"sheets": {k: len(v) for k, v in sheets.items()}, "origin": origin, "shift_days": shift.days,
            "technician_users": [u for u in org_users[:2]] if org_users else [],
            "vendors": vendor_report["report"]}


async def org_logins(org_name: str) -> tuple[str | None, list[tuple[str, str]]]:
    from _env import hoistra_test_dsn
    import asyncpg
    c = await asyncpg.connect(hoistra_test_dsn().replace("postgresql+asyncpg", "postgresql"), timeout=15)
    try:
        org = await c.fetchval("SELECT id::text FROM plenum_cafm.organizations WHERE lower(name) = lower($1)", org_name)
        if not org:
            return None, []
        rows = await c.fetch("""SELECT id::text, coalesce(full_name, email) FROM plenum_cafm.users
                                 WHERE organization_id::text = $1 AND coalesce(status,'') <> 'deleted'
                                 ORDER BY created_at""", org)
        return org, [(r[0], r[1]) for r in rows]
    finally:
        await c.close()


def main() -> None:
    import asyncio

    ap = argparse.ArgumentParser()
    ap.add_argument("prototype", help="the bundled prototype page, e.g. 'Hoistra_1 (1).html'")
    ap.add_argument("--out", default=r"C:\Users\balap\Downloads\plenum_technologies_B-301_bishopsgate_tower.xlsx")
    ap.add_argument("--as-of", default=None, help="the date the workbook is built for (default today)")
    ap.add_argument("--org-name", default=None, help="look up this organization's logins for Technicians.user_id")
    ap.add_argument("--seed", type=int, default=301)
    ap.add_argument("--finish", action="store_true",
                    help="after build_floor_submeters.py: add the section sub-meters and AHU-3's spike to the companion")
    args = ap.parse_args()
    as_of = dt.date.fromisoformat(args.as_of) if args.as_of else dt.date.today()
    users: list[tuple[str, str]] = []
    if args.org_name:
        org, users = asyncio.run(org_logins(args.org_name))
        print(f"  organization {args.org_name!r}: {org or 'NOT FOUND'} · {len(users)} login(s)")
    if args.finish:
        comp = os.path.splitext(args.out)[0] + "-floorlevel_submeter.xlsx"
        print(f"  {os.path.basename(comp)}: {finish(comp, as_of, args.seed)}")
        return
    proto = read_prototype(args.prototype)
    out = build(proto, args.out, as_of, users, args.seed)
    print(f"\n  {os.path.basename(args.out)}  (dates shifted +{out['shift_days']} days from the prototype)")
    for k, n in out["sheets"].items():
        print(f"    {k:26} {n:>7,}")
    gen = [o for o in out["origin"] if o[2].startswith("generated")]
    print(f"\n  generated rows the prototype does not have: {len(gen)} listed in origin; "
          f"technician logins: {len(out['technician_users']) or 'none (blank user_id)'}")
    print("\n  vendor months (n, response met, completion met, first fix, not recalled):")
    for vcode, r in out["vendors"].items():
        latest = max(r["months"])
        m = r["months"][latest]
        print(f"    {vcode}  {r['trend']:9}  latest {latest}: n={m['n']:>2}  "
              + "  ".join(f"{k} {m[k]}/{m['n']}={js_round(100 * m[k] / m['n'])}% (target {r['targets'][k]})"
                          for k in ("resp", "comp", "ff", "norecall")))


if __name__ == "__main__":
    main()
