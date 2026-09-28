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
    python db/tools/build_floor_submeters.py  <out>.xlsx --shares per-floor
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
    # country_code on the site: the energy engines read a building's country from its site (or
    # location, or raw_metadata), never from buildings.country_code - without it the building
    # is in no market and gets no benchmark, MEES or EPC tile.
    "Sites": ["site_id", "site_name", "city", "postcode", "region", "manager_email", "country_code"],
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
    # category_id, condition_updated_at and warranty_expiry are what the Assets page puts in an
    # asset's header line ("Chiller · installed 2009 · condition read 10 Jul · warranty to Mar
    # 2027"); without them every row read "No category set · condition never dated".
    "Assets": ["asset_code", "asset_name", "manufacturer", "model", "site_ref", "site_name", "status",
               "install_date", "maintained_by", "replacement_value", "replacement_currency", "design_life_years",
               "wear_coefficient", "condition_score", "criticality", "section_name", "building_code",
               "category_id", "condition_updated_at", "warranty_expiry"],
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
    # Plant telemetry and weather - not tables the migration writes. The platform reads these
    # four after the run (workbook_extras) into the chiller, degree-day and BMS stores the
    # energy scan and the investigation walk read.
    "Chiller_Design_Specs": ["asset_code", "building_code", "design_kw_per_rt", "design_capacity_rt",
                             "design_ambient_c", "design_chw_supply_c", "source", "notes"],
    "Chiller_Readings": ["asset_code", "building_code", "reading_at", "kw_input", "cooling_load_rt",
                         "ambient_c", "chw_supply_c", "chw_return_c"],
    "Weather_Degree_Days": ["building_code", "month", "hdd", "cdd", "base_temp_c", "station"],
    "BMS_Trends": ["building_code", "zone", "asset_code", "recorded_at", "heating_pct", "cooling_pct",
                   "zone_temp_c", "setpoint_c"],
}
ORDER = ["Sites", "Buildings", "Building_Sections", "Vendors", "Vendor_Contracts", "Technicians", "Assets",
         "Asset_Reading_Bands", "Asset_Readings", "Maintenance_Plans", "PPM_Visits", "Work_Orders",
         "Inspections", "Spare_Parts", "Energy_Meters", "Meter_Readings", "Compliance_Certificates",
         "Chiller_Design_Specs", "Chiller_Readings", "Weather_Degree_Days", "BMS_Trends"]

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
    # Not in the prototype: a second mechanical contractor, so the Vendors page compares two of
    # a trade. It holds the floor fan-coil banks; Apex keeps the central plant.
    ("FNMC", "Fenmoor Mechanical", "Mechanical", "Unit 4, Brickfield Lane, London E3 9ZZ", "+44 20 7946 0588", "Gas Safe 648213"),
]
VCODE = {v[1]: v[0] for v in VENDORS}
#: Asset class -> plenum_cafm.asset_categories.name. The writer has no by-name lookup for a
#: category, so the id of the company's own category is looked up in hoistra_test when the
#: workbook is built (--org-name) and written as category_id.
CATEGORY_OF_CLASS = {"Air handling": "HVAC · Air Handling", "Chiller": "HVAC · Chillers",
                     "Boiler": "HVAC · Boilers", "Fan coil": "HVAC · Terminal Units",
                     "Pump": "Mechanical · Pumps", "Generator": "Electrical · Standby Power",
                     "Lift": "Vertical Transport", "Fire panel": "Life Safety · Fire Detection",
                     "LV board": "Electrical · LV Distribution", "Water system": "Water Hygiene"}
CATEGORY_IDS: dict[str, str] = {}
CATEGORY_OF_CLASS["Lighting panel"] = "Electrical · Lighting Control"
#: The floors as plenum_cafm.floors and the floor companion name them.
FLOOR_NAMES = ["Basement", "Ground"] + [f"Level {i}" for i in range(1, FLOORS - 1)]
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
GEN_PERF = {"FNMC": {"sla_r": 92, "sla_c": 87, "firstfix": 83, "recall": 7, "invoice": 82, "trend": "stable"},
            "SFLT": {"sla_r": 82, "sla_c": 74, "firstfix": 70, "recall": 12, "invoice": 70, "trend": "declining"},
            "PCFR": {"sla_r": 85, "sla_c": 78, "firstfix": 72, "recall": 10, "invoice": 75, "trend": "declining"}}
GEN_CONTRACT = {"FNMC": {"ref": "FM-2025-MECH-02", "signed": "12 May 2025", "expires": "11 May 2028"},
                "SFLT": {"ref": "SL-2025-LIFT-01", "signed": "03 Mar 2025", "expires": "02 Mar 2027"},
                "PCFR": {"ref": "PC-2024-FIRE-02", "signed": "15 Jul 2024", "expires": "14 Jul 2027"}}
GEN_TERMS = [("P1 response", "4 hours"), ("P2 response", "1 business day"), ("P3 response", "5 business days"),
             ("Completion target", "95%"), ("First-time fix target", "85%"), ("Recall window", "28 days")]
GEN_RATES = {"FNMC": (72, 108), "SFLT": (74, 111), "PCFR": (62, 93)}
#: One contract per vendor: scoring reads a vendor's newest confirmed parameter set, and two
#: sets signed the same day block it (FR-035), so Heating folds into Mechanical.
CONTRACTS = [
    ("APXM", "Mechanical PPM · Bishopsgate", "AHUs, chillers, pumps, FCUs, boilers", 32, 210000),
    ("APXL", "Lifts · Bishopsgate", "Passenger lifts: monthly service, LOLER support", 12, 38000),
    ("SFLT", "Lift LOLER support · Bishopsgate", "LOLER examination support and lift call-outs", 2, 9000),
    ("NGEL", "Electrical · Bishopsgate", "LV distribution, EICR, emergency lighting", 4, 21000),
    ("PCFR", "Fire and security · Bishopsgate", "Fire alarm, detection, FRA", 4, 16000),
    ("CLWC", "Water hygiene · Bishopsgate", "L8 monitoring, TMVs, tanks and calorifiers", 12, 14000),
    ("FNMC", "Terminal units PPM · Bishopsgate", "Fan coil units on every floor: filters, condensate, fan motors", 4, 48000),
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
    "Lighting panel": ["Lighting zone not switching off out of hours", "PIR sensors unresponsive — east core",
                       "Scene controller offline"],
    "Water system": ["TMV failed temperature check — L6", "Cold water tank above 20 °C",
                     "Calorifier flow below 60 °C", "Dead-leg flushing overdue — L14"],
}


#: What an engineer's service report on each kind of plant finds and recommends.
REPORT_FINDINGS = {
    "Air handling": ["Filters at 240 Pa - changed", "Drive belt tension low - adjusted", "Coil face fouled - cleaned",
                     "Fan bearing noise within limits"],
    "Chiller": ["Refrigerant charge within 2% of design", "Condenser coils fouled - cleaned",
                "Compressor 2 running amps 6% high", "Oil sample clean"],
    "Pump": ["Mechanical seal weeping - monitored", "Bearing temperature normal", "Coupling alignment within tolerance"],
    "Fan coil": ["Filters replaced on all units", "3 condensate trays cleaned", "2 fan motors noisy - replaced"],
    "Boiler": ["Combustion efficiency 89%", "Burner nozzle wear", "Flue gas CO 110 ppm"],
    "Lift": ["Door operator adjusted", "Ropes within discard criteria", "Levelling within 8 mm"],
    "Generator": ["Load bank test 80% for 2 h - passed", "Battery voltage 26.8 V", "Coolant topped up"],
    "LV board": ["Thermography - no hot spots", "Busbar torque check completed", "RCD tests passed"],
    "Fire panel": ["Loop 2 earth fault cleared", "25% of detectors tested", "Standby batteries three years old"],
    "Water system": ["Calorifier flow 61 C", "TMVs 43 C at outlets", "Cold water tanks 17 C"],
    "Lighting panel": ["Scene timings verified", "2 PIR sensors unresponsive - replaced", "Emergency changeover tested"],
}
REPORT_RECOMMEND = {
    "Air handling": "Replace drive belts at next visit; review weekend schedule",
    "Chiller": "Leak test within 3 months; clean condenser coils quarterly",
    "Pump": "Replace mechanical seal at next shutdown",
    "Fan coil": "Replace remaining original fan motors over the next year",
    "Boiler": "Burner service and flue gas analysis",
    "Lift": "Monitor door operator; re-adjust at next monthly visit",
    "Generator": "Replace starter batteries within 6 months",
    "LV board": "Repeat thermography in 12 months",
    "Fire panel": "Replace standby batteries next year",
    "Water system": "Remove L14 dead legs; monthly temperature monitoring",
    "Lighting panel": "Recommission out-of-hours scenes with the tenant",
}
#: An engineer's report by class and condition grade: findings (with {u} units, {k}/{k2} counts,
#: {tag} the floor tag) and the recommendation that follows from them. A grade 3 or 4 report
#: leaves its recommendation open; grade 1-2 reads "Monitor". Missing grades fall back to 2.
REPORT_STORIES = {
    "Fan coil": {
        1: (["Filters replaced on all {u} units", "Condensate trays clean", "Fan speeds verified on the BMS"], "Monitor at routine PPM"),
        2: (["Filters replaced on all {u} units", "{k} condensate trays cleaned", "Valve actuators stroked — all respond"], "Monitor at routine PPM"),
        3: (["Filters at 180 Pa on {k} of {u} units — replaced", "{k2} condensate pumps weak — trays near overflow",
             "Actuator on unit {tag}-{x} sticking at 40%"], "Replace the condensate pumps; free or replace the sticking actuator"),
        4: (["Heating and cooling valves both open on {k} units overnight", "Actuators on {tag}-04 and {tag}-09 stuck at 40%",
             "BMS night setback not applied to the floor"], "Replace the two actuators; reinstate night setback on the BMS"),
    },
    "Lighting panel": {
        1: (["Scene timings verified", "Emergency changeover tested", "All PIR sensors respond"], "Monitor at routine PPM"),
        2: (["Scene timings verified", "{k} PIR sensors unresponsive — replaced", "Emergency changeover tested"], "Monitor at routine PPM"),
        3: (["Out-of-hours scene holds lighting on to 23:00", "Astronomical clock {k}0 min slow", "{k2} PIR sensors unresponsive"],
            "Reset the out-of-hours scene with the tenant; correct the clock"),
    },
    "Chiller": {
        2: (["Refrigerant charge within 2% of design", "Oil sample clean", "Condenser approach 2.1 °C"], "Monitor at routine PPM"),
        3: (["Compressor 1 running amps 6% high", "Condenser approach 3.4 °C", "Oil acidity rising"],
            "Oil change and condenser brush-clean at the next visit"),
    },
    "Pump": {
        2: (["Bearing temperature normal", "Coupling alignment within tolerance", "Mechanical seal dry"], "Monitor at routine PPM"),
        3: (["Drive-end bearing vibration 4.6 mm/s (2.8 at commissioning)", "Mechanical seal weeping", "Motor current 9% above baseline"],
            "Replace the bearing and seal at the next shutdown"),
    },
    "Boiler": {
        2: (["Combustion efficiency 90%", "Flue gas CO 60 ppm", "Safety interlocks tested"], "Monitor"),
        3: (["Combustion efficiency 86% vs 91% commissioned", "Burner nozzle wear", "Flue gas CO 150 ppm"],
            "Burner service and flue gas analysis"),
    },
    "Fire panel": {
        2: (["25% of detectors tested — all passed", "Standby batteries 24.9 V", "No faults on the log"], "Monitor"),
        3: (["Loop 2 earth fault — intermittent", "Standby batteries three years old", "{k} detectors overdue their 12-month test"],
            "Clear the loop 2 fault; replace the standby batteries"),
    },
    "LV board": {
        2: (["Thermography — no hot spots", "Busbar torque check completed", "RCD trip times within limits"], "Repeat thermography in 12 months"),
        3: (["Thermography — 14 °C rise on outgoing way 7", "Busbar torque check completed", "Surge protection indicator amber"],
            "Re-terminate way 7; replace the SPD cartridge"),
    },
    "Generator": {
        2: (["Load bank test 80% for 2 h — passed", "Battery voltage 26.8 V", "Coolant topped up"], "Monitor"),
        3: (["Load bank test passed, 12 s to take load", "Starter battery 24.1 V under crank", "Coolant hose perished"],
            "Replace the starter batteries and the coolant hose"),
    },
    "Water system": {
        2: (["Calorifier flow 61 °C", "TMVs 43 °C at outlets", "Cold water tanks 17 °C"], "Monitor monthly temperatures"),
        3: (["Calorifier return 49 °C — below 50 °C", "3 TMVs failed the fail-safe test", "Dead leg on the L14 riser"],
            "Descale the calorifier; replace 3 TMV cartridges; remove the L14 dead leg"),
    },
}
#: The grade each asset's latest report gives, where it is not the default 2 — a mix of Threat,
#: Watch and In control, and each tied to what else the workbook says about the asset:
#: Level 20's fan coils fight overnight (BMS_Trends), its lighting holds on late (the night
#: drift on its sub-meter), PUMP-01 has a predictive order, DHW-01 the descale Clearwater bills.
REPORT_GRADE = {"PUMP-01": 3, "BOILER-01": 3, "FIRE-PANEL-01": 3, "DHW-01": 3, "FCU-L20": 4, "FCU-L07": 3,
                "FCU-L14": 3, "LCP-L20": 3, "LCP-L09": 3}
#: Refitted in 2016 and looked after: grade 1 unless named above.
REPORT_GRADE_REFIT = 1
#: Open orders in the prototype's four kinds, each on an asset whose report explains it:
#: (number, asset suffix, priority, status, type, days ago, description).
OPEN_ORDERS = [
    ("4560", "FCU-L20", "P2", "In progress", "Reactive", 2,
     "Level 20 heating and cooling fighting overnight — two actuators stuck at 40%, BMS night setback missing"),
    ("4561", "PUMP-01", "P3", "Draft", "Predictive", 3,
     "CHW pump 1 — drive-end bearing vibration 4.6 mm/s against 2.8 baseline, rising for 3 weeks"),
    ("4562", "FIRE-PANEL-01", "P2", "Held", "Compliance", 6,
     "Blocked — accreditation: ProudCastle Fire's BAFE SP203-1 has lapsed; the loop 2 fault waits for an accredited contractor"),
    ("4563", "DHW-01", "P3", "Scheduled", "Planned", 1,
     "Calorifier descale and TMV cartridge replacement — booked"),
]


def report_story(rng, cls: str, grade: int, tag: str) -> tuple[list[str], str, int]:
    """(findings, recommendation, the grade the story is for) - a class with no grade-1 story
    tells its grade-2 one, and says grade 2."""
    st = REPORT_STORIES.get(cls)
    if not st:
        finds = rng.sample(REPORT_FINDINGS[cls], k=min(2 + (grade >= 3), len(REPORT_FINDINGS[cls])))
        return finds, REPORT_RECOMMEND[cls] if grade >= 3 else "Monitor at routine PPM", grade
    told = grade if grade in st else max((g for g in st if g <= grade), default=min(st))
    finds, rec = st[told]
    u = rng.randint(18, 26)
    vals = dict(u=u, k=rng.randint(2, 5), k2=rng.randint(2, 3), x=f"{rng.randint(1, 12):02d}", tag=tag)
    return [f.format(**vals) for f in finds], rec, told


#: Warranties the service reports name, by asset code suffix.
REPORT_WARRANTY = {"CHILLER-102": "Compressor 1 under 2-year parts warranty to Jan 2028 — compressor claimable",
                   "BOILER-01": "Burner under 5-year manufacturer warranty to Mar 2027 — nozzle claimable",
                   "PUMP-01": "Pump set under 3-year warranty to Aug 2027 — bearing and seal claimable",
                   "GEN-01": "Alternator under extended warranty to Jun 2027",
                   "FCU-L16": "EC fan motors under installer warranty to Nov 2026 — replacements claimable"}


def service_reports(rng, assets: list[dict], ppm_rows: list[list], ppm_header: list[str], code: str,
                    skip_codes: set[str], as_of: dt.date | None = None) -> tuple[list[list], list[list]]:
    """(work orders, inspection rows): the engineer's report on each asset's recent PPM visits -
    the two latest for plant, the latest for floor plant - each on the completed order it was
    written on (same asset, same day), so the Assets page reads order, date, vendor, grade,
    findings, the recommendation open or done, and any warranty the report names."""
    ix = {h: i for i, h in enumerate(ppm_header)}
    done: dict[str, list[dt.date]] = {}
    for r in ppm_rows:
        d = r[ix["completed_date"]]
        if d:
            done.setdefault(r[ix["asset_code"]], []).append(d)
    wos, reports, n = [], [], 7001
    for a in assets:
        if a["code"] in skip_codes:
            continue
        floor = str(a.get("src", "")).startswith("generated (floor")
        dates = sorted(done.get(a["code"], []))[-(1 if floor else 2):]
        if not dates and a["cls"] == "Generator" and as_of:
            # Run-tested, not on a PPM contract: the reports are the last two monthly run tests,
            # on the first Monday of each month.
            for back in (2, 1):
                m0 = (as_of.replace(day=1) - dt.timedelta(days=28 * back)).replace(day=1)
                dates.append(m0 + dt.timedelta(days=(7 - m0.weekday()) % 7))
        tail = a["code"][len(code) + 1:]
        top = int(a["cond"])
        for k, d in enumerate(dates):
            latest = k == len(dates) - 1
            # The visit before found it a grade better, and what it asked for was done.
            grade = max(1, min(5, top if latest else top - 1))
            risk = {5: "High", 4: "High", 3: "Medium"}.get(grade, "Low")
            open_ = latest and grade >= 3
            finds, rec, grade = report_story(rng, a["cls"], grade, tail.split("-")[-1])
            risk = {5: "High", 4: "High", 3: "Medium"}.get(grade, "Low")
            open_ = latest and grade >= 3
            # Two visits that found the same thing are one note, not two identical ones.
            if not latest and (finds, rec) == report_story(random.Random(0), a["cls"], top, tail.split("-")[-1])[:2]:
                continue
            w = REPORT_WARRANTY.get(tail)
            if w and latest:
                rec += f" ({w})"
            rep = dt.datetime.combine(d, dt.time(8, 0))
            wo = f"WO-{code}-R{n - 7000:03d}"     # its own series: the reactive orders run 6001 upward
            wos.append([wo, a["vendor"], a["code"], a["name"], "; ".join(finds), "Planned", "Completed",
                        iso(rep), iso(rep + dt.timedelta(hours=1)), iso(rep + dt.timedelta(hours=5)), "yes", "no", 4.0,
                        rng.choice([0, 45, 85, 120]), 420, 420, code, "Inspection",
                        iso(rep + dt.timedelta(days=5)), f"{a['name']} - service report"])
            reports.append([f"INS-{code}-R{n - 7000:03d}", a["code"], d, f"{a['vendor']} engineer", f"Condition grade {grade}", risk,
                            "; ".join(finds), rec, "true" if open_ else "false"])
            n += 1
    return wos, reports


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
    "Lighting panel": ("Helvar", "Imagine Router 910"),
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


#: The main meters reach this far back beyond the year. The investigation compares its last 8
#: weeks with the same weeks a year earlier; a year of readings leaves last year's side empty.
HISTORY_WEEKS = 9
#: The chillers as sold: kW per RT at full load and capacity. No design ambient - a UK plant is
#: rated at Eurovent conditions London never reaches, and matching on it would drop every sample.
CHILLER_DESIGN = {"CHILLER-101": (0.62, 450.0), "CHILLER-102": (0.62, 450.0)}
#: London Heathrow monthly degree days, base 15.5 C (HDD, CDD). August and September are held
#: within a few per cent year on year, so the weather is ruled out as the cause of the drift.
DEGREE_DAYS = {1: (330, 0), 2: (290, 0), 3: (255, 1), 4: (175, 5), 5: (100, 22), 6: (40, 58),
               7: (15, 104), 8: (20, 97), 9: (45, 60), 10: (130, 6), 11: (240, 0), 12: (305, 0)}
#: BMS zones and the fan coils that serve them. Level 20's two zones fight overnight - heating
#: and cooling both calling 00:00-05:00 since the night drift began on its sub-meter.
BMS_ZONES = [("Ground reception", "FCU-G"), ("L4 East", "FCU-L04"), ("L12 open plan", "FCU-L12"),
             ("L16 open plan", "FCU-L16"), ("L20 North", "FCU-L20"), ("L20 South", "FCU-L20")]
BMS_DAYS = 14
CHILLER_WEEKS = 8


def plant_telemetry(rng, as_of: dt.date, code: str) -> dict[str, list[list]]:
    """The chiller, BMS and weather records the energy scan and the investigation walk read.

    CHILLER-101's condenser fouls across the eight weeks: 1.00x design at the start, 1.20x at
    the end, and 1.40x over the three days its asset meter spikes (build_floor_submeters.py) -
    over the 15% the rule allows. CHILLER-102 stays within 3% of design. The ambient is a warm,
    even late summer, so the two halves of the window compare at matched ambient."""
    import build_floor_submeters as bfs
    latest = dt.datetime.combine(as_of - dt.timedelta(days=1), dt.time(23, 30), tzinfo=dt.timezone.utc)
    spike_end = latest - dt.timedelta(days=bfs.SPIKE_ENDS_DAYS_BEFORE_LATEST)
    spike_start = spike_end - dt.timedelta(days=bfs.SPIKE_DAYS)
    drift_start = latest - dt.timedelta(weeks=bfs.DRIFT_WEEKS)
    out: dict[str, list[list]] = {"Chiller_Design_Specs": [], "Chiller_Readings": [],
                                  "Weather_Degree_Days": [], "BMS_Trends": []}
    for tail, (kwrt, cap) in CHILLER_DESIGN.items():
        out["Chiller_Design_Specs"].append([f"{code}-{tail}", code, kwrt, cap, None, 6.0, "manufacturer data sheet",
                                            "Full-load kW/RT at Eurovent rating conditions"])
    first = (latest - dt.timedelta(weeks=CHILLER_WEEKS)).replace(minute=0)
    span = (latest - first).total_seconds()
    t = first
    while t <= latest:
        if t.weekday() < 5 and 7 <= t.hour < 19:
            amb = 17.5 + 4.5 * math.sin(math.pi * (t.hour - 9) / 12) + rng.uniform(-1.2, 1.2)
            for tail, (kwrt, cap) in CHILLER_DESIGN.items():
                load = cap * min(0.85, max(0.45, 0.50 + 0.025 * (amb - 16) + rng.uniform(-0.04, 0.04)))
                if tail == "CHILLER-101":
                    factor = 1.40 if spike_start <= t < spike_end else 1.0 + 0.20 * (t - first).total_seconds() / span
                else:
                    factor = 1.02 + rng.uniform(-0.01, 0.01)
                kw = load * kwrt * factor * (1 + 0.01 * (amb - 18)) * rng.uniform(0.98, 1.02)
                out["Chiller_Readings"].append([f"{code}-{tail}", code, t.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                                round(kw, 1), round(load, 1), round(amb, 1),
                                                round(6.0 + (factor - 1) * 1.5 + rng.uniform(-0.2, 0.2), 2),
                                                round(11.8 + rng.uniform(-0.4, 0.4), 2)])
        t += dt.timedelta(hours=1)
    m = dt.date(as_of.year - 2, as_of.month, 1)
    while m <= as_of.replace(day=1):
        hdd, cdd = DEGREE_DAYS[m.month]
        k = 1 + (rng.uniform(-0.03, 0.03) if m.month in (8, 9) else rng.uniform(-0.12, 0.12))
        out["Weather_Degree_Days"].append([code, m.isoformat(), round(hdd * k, 1), round(cdd * k, 1), 15.5,
                                           "London Heathrow"])
        m = (m + dt.timedelta(days=32)).replace(day=1)
    t = (latest - dt.timedelta(days=BMS_DAYS)).replace(minute=0) + dt.timedelta(minutes=30)
    while t <= latest:
        occupied = t.weekday() < 5 and 7 <= t.hour < 19
        for zone, tail in BMS_ZONES:
            fight = zone.startswith("L20") and t >= drift_start and t.hour < 5
            if fight:
                h, c, zt, sp = rng.uniform(30, 45), rng.uniform(25, 40), rng.uniform(20.6, 21.4), 21.0
            elif occupied:
                h, c = rng.uniform(0, 4), rng.uniform(25, 60)
                zt, sp = rng.uniform(21.6, 23.0), 22.0
            else:
                h, c, zt, sp = rng.uniform(0, 3), rng.uniform(0, 3), rng.uniform(19.5, 21.0), 18.0
            out["BMS_Trends"].append([code, zone, f"{code}-{tail}", t.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                      round(h, 1), round(c, 1), round(zt, 1), sp])
        t += dt.timedelta(minutes=15)
    return out


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
    sheets["Sites"].append([SITE, BUILDING, "London", "EC2M 9ZZ", bld_cc["state"], "fm@plenum-technologies.example", "UK"])
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
    # Every floor's own plant, so a floor section opens onto what is on that floor: a fan-coil
    # bank on each occupied floor and a lighting control panel on every floor. Their section is
    # the floor itself (the floor companion's section names), not one of the prototype's zones.
    # L12-L20 were refitted in 2016, the rest is the 2009 fit-out.
    for fl in FLOOR_NAMES:
        tag = "B" if fl == "Basement" else "G" if fl == "Ground" else "L" + fl.split()[-1].zfill(2)
        refit = fl.startswith("Level") and int(fl.split()[-1]) >= 12
        if fl != "Basement":
            assets.append(dict(code=f"{CODE}-FCU-{tag}", name=f"Fan coil units — {fl}", cls="Fan coil", sec=fl,
                               vendor="Fenmoor Mechanical", installed=dt.date(2016 if refit else 2009, 5, 20), l1=False,
                               life=15, value=38000 if fl != "Ground" else 22000, wear=1.0, cond=2, ppm=None,
                               src="generated (floor plant)"))
        assets.append(dict(code=f"{CODE}-LCP-{tag}", name=f"Lighting control panel — {fl}", cls="Lighting panel", sec=fl,
                           vendor="Northgate Electrical", installed=dt.date(2016 if refit else 2009, 5, 20), l1=False,
                           life=20, value=6500, wear=0.8, cond=2, ppm=None, src="generated (floor plant)"))
    reported = {i["asset"] for i in MX["inspections"] if isb(i)}
    for a in assets:
        if a["name"] in reported:
            continue                                   # the prototype's own report sets its grade
        tail = a["code"][len(CODE) + 1:]
        refit = a["src"].startswith("generated (floor") and tail.split("-")[-1][:1] == "L" and int(tail[-2:]) >= 12
        a["cond"] = REPORT_GRADE.get(tail, REPORT_GRADE_REFIT if refit else a["cond"])
    # The chiller the floor-meter builder puts its excursion on is the FIRST chiller in the
    # sheet: CHILLER-101, whose anomaly the prototype names.
    assets.sort(key=lambda a: (0 if a["code"].endswith("CHILLER-101") else 1))
    for a in assets:
        make = MAKE.get(a["cls"], ("Generic", "-"))
        sheets["Assets"].append([a["code"], a["name"], make[0], make[1], SITE, BUILDING, "Active", a["installed"],
                                 a["vendor"], a["value"], "GBP", a["life"], a["wear"], a["cond"],
                                 "high" if a["l1"] else "medium", a["sec"], CODE,
                                 CATEGORY_IDS.get(CATEGORY_OF_CLASS.get(a["cls"], "")), None, None])
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
    freq.update({a["code"][len(CODE) + 1:]: ("Quarterly", 3) for a in assets if a["cls"] in ("Fan coil", "Lighting panel")})
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
            if sched >= as_of:
                # A visit not yet due is scheduled, not done: a completed date after today put a
                # lift's "latest" scorecard month in November with one job in it.
                st = "Scheduled"
            if st == "Completed":
                done = min(sched + dt.timedelta(days=rng.randint(-3, 2)), as_of - dt.timedelta(days=1))
            elif st == "Late":
                done, st = min(sched + dt.timedelta(days=rng.randint(18, 26)), as_of - dt.timedelta(days=1)), "Completed"
            task = {"Air handling": "AHU service — filters, belts, coils", "Chiller": "Chiller service — refrigerant, condenser, oil",
                    "Pump": "Pump service — seals, bearings, alignment", "Fan coil": "FCU service — filter, condensate, fan",
                    "Lift": "Lift maintenance visit", "Boiler": "Boiler service — combustion analysis, flue",
                    "Fire panel": "Fire alarm service — detectors, sounders, panel", "LV board": "LV board thermography and inspection",
                    "Water system": "L8 monitoring — temperatures, TMVs, tank inspection",
                    "Lighting panel": "Lighting controls check — scenes, sensors, emergency changeover"}[a["cls"]]
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
    # The service reports behind every asset's notes, on their own completed orders - added
    # only where the prototype has none: an asset it names reads exactly its notes, as its
    # Assets page does, and a generated report cannot contradict them (CHILLER-101's coils
    # "cleaned" two days before WO-4421 finds them fouled). They go in
    # BEFORE the vendors' reactive work, so the exact month counts below account for them.
    proto_insp = {code_of[i["asset"]] for i in MX["inspections"] if isb(i)}
    rep_wos, rep_rows = service_reports(rng, assets, sheets["PPM_Visits"], HEADERS["PPM_Visits"], CODE,
                                        skip_codes=proto_insp, as_of=as_of)
    wos = wos + rep_wos
    vendor_of = {a["code"]: a["vendor"] for a in assets}
    name_of = {a["code"]: a["name"] for a in assets}
    for num, tail, pri, status, typ, back, desc in OPEN_ORDERS:
        ac = f"{CODE}-{tail}"
        rep = now - dt.timedelta(days=back)
        att = rep + dt.timedelta(hours=3) if status == "In progress" else None
        sla_h = {"P1": 24, "P2": 48, "P3": 120}[pri]
        wos.append([f"WO-{CODE}-{num}", vendor_of[ac], ac, name_of[ac], desc, pri, status, iso(rep), iso(att), None, None, None,
                    None, None, {"P2": 650, "P3": 420}[pri], None, CODE, typ, iso(rep + dt.timedelta(hours=sla_h)), desc[:120]])
    gen_wos, vendor_report = gen_vendor_work_orders(rng, as_of, assets, VP, H, wos)
    wos = wos + gen_wos
    origin.append(("Work_Orders", f"{len(gen_wos)} vendor work orders",
                   "generated so the scorecards reproduce HOISTRA_VP's measured KPIs"))
    sheets["Work_Orders"] = wos

    # Inspections: the prototype's five, then the service report on every asset's recent PPM
    for i in [x for x in MX["inspections"] if isb(x)]:
        sheets["Inspections"].append([f"INS-{CODE}-{i['wo'][3:]}", code_of[i["asset"]], sh(pdate(i["date"])),
                                      f"{i['vendor']} engineer", f"Condition grade {i['cond']}",
                                      {4: "High", 3: "Medium"}.get(i["cond"], "Low"), "; ".join(i["findings"]),
                                      i["rec"] + (f" ({i['warranty']})" if i.get("warranty") else ""),
                                      "false" if i["recDone"] else "true"])
    sheets["Inspections"].extend(rep_rows)

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
    history = [slots[0] - dt.timedelta(minutes=30 * k) for k in range(HISTORY_WEEKS * 7 * 48, 0, -1)]
    for ref, fuel, _ in meters:
        shape = _elec_shape if fuel == "electricity" else _gas_shape
        base = annual[fuel] / sum(shape(s) for s in slots)
        # The weeks before the year: the same building a year earlier, 3% leaner.
        for s in history:
            v = base * shape(s) * rng.uniform(0.94, 1.06) * 0.97
            readings.append([ref, CODE, fuel, s.strftime("%Y-%m-%dT%H:%M:%SZ"), round(v, 3), 30, "dcc"])
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
    sheets.update(plant_telemetry(random.Random(seed + 11), as_of, CODE))

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
            ("FNMC", "GAS_SAFE", "Registered", 12, dt.date(2026, 4, 14), "valid"),
            ("FNMC", "CONTRACTOR_PL_INSURANCE", "GBP 10,000,000", 12, dt.date(2026, 1, 9), "valid"),
            ("CLWC", "ISO_9001", "Certificated", 36, dt.date(2024, 6, 1), "valid"),
            ("SFLT", "LEIA", "Member", 12, dt.date(2026, 2, 12), "valid"),
            ("SFLT", "CONTRACTOR_EL_INSURANCE", "GBP 5,000,000", 12, dt.date(2025, 11, 20), "valid"),
            ("SFLT", "CONTRACTOR_PL_INSURANCE", "Lapsed — renewal not supplied", 12, dt.date(2025, 7, 18), "expired"),
            ("PCFR", "CONTRACTOR_PL_INSURANCE", "GBP 5,000,000", 12, dt.date(2026, 5, 1), "valid"),
            ("PCFR", "BAFE_SP203_1", "Registration lapsed", 36, dt.date(2023, 7, 8), "expired")]
    for vcode, typ, result, months, issued, status in vend:
        vname = next(v[1] for v in VENDORS if v[0] == vcode)
        n += 1
        cert(f"{typ}-{vcode}-{n:02d}", typ, "Vendor", None, vcode, vname, None, None, sh(issued), months, result,
             status=status)
    sheets["Compliance_Certificates"] = certs

    # An asset's condition is as current as the last time someone looked at it: its latest
    # inspection, else its last completed PPM visit. A component warranty an inspection names
    # ("Compressor 2 under OEM warranty to Mar 2027") is the asset's warranty_expiry.
    ah = HEADERS["Assets"]
    ih, ph = HEADERS["Inspections"], HEADERS["PPM_Visits"]
    seen: dict[str, dt.date] = {}
    for r in sheets["Inspections"]:
        d = r[ih.index("inspection_date")]
        c = r[ih.index("asset_code")]
        if d and (c not in seen or d > seen[c]):
            seen[c] = d
    for r in sheets["PPM_Visits"]:
        d, c = r[ph.index("completed_date")], r[ph.index("asset_code")]
        if d and c not in seen:
            seen[c] = max(d, seen.get(c, d))
    warranty = {}
    for i in MX["inspections"]:
        m = re.search(r"warranty to ([A-Z][a-z]{2}) (\d{4})", i.get("warranty") or "")
        if isb(i) and m:
            mon = dt.datetime.strptime(m.group(1), "%b").month
            nxt = dt.date(int(m.group(2)) + (mon == 12), mon % 12 + 1, 1)
            warranty[code_of[i["asset"]]] = nxt - dt.timedelta(days=1)
    for r in sheets["Assets"]:
        c = r[ah.index("asset_code")]
        r[ah.index("condition_updated_at")] = seen.get(c)
        r[ah.index("warranty_expiry")] = warranty.get(c)

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


async def category_ids(org_name: str) -> dict[str, str]:
    """The company's OWN categories (seed_bishopsgate_building.py creates them): a category is
    per organisation, and an asset pointing at another company's is a link across tenants."""
    from _env import hoistra_test_dsn
    import asyncpg
    c = await asyncpg.connect(hoistra_test_dsn().replace("postgresql+asyncpg", "postgresql"), timeout=15)
    try:
        return {r[1]: r[0] for r in await c.fetch(
            """SELECT c.id::text, c.name FROM plenum_cafm.asset_categories c
                 JOIN plenum_cafm.organizations o ON o.id = c.organization_id
                WHERE lower(o.name) = lower($1)""", org_name)}
    finally:
        await c.close()


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
        CATEGORY_IDS.update(asyncio.run(category_ids(args.org_name)))
        print(f"  asset categories of {args.org_name!r}: {len(CATEGORY_IDS)}"
              + ("" if CATEGORY_IDS else " - run seed_bishopsgate_building.py --apply first"))
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
