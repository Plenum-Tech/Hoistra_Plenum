"""Seed a whole new company into a Hoistra database so every page has something to show —
without touching a row that belongs to anyone else.

One organisation (Northbridge Estates Ltd, UK), two buildings, and everything the pages
read for them: the people who sign in and which buildings they may see; the sites, buildings
and locations; the asset register with condition grades and readings; a year of work orders
with the timestamps vendor scoring needs; PPM visits and inspection reports; four vendors
with contracts and SLA terms; building and vendor certificates against the UK pack with a
spread of current, expiring and lapsed; four meters per building with twelve months of
half-hourly readings, a floor-area profile and degree days; a few open anomalies and
decision-queue items so Home and Energy are not blank before the engines have run.

Every id is uuid5 of a stable key and every insert is ON CONFLICT DO NOTHING, so a second
run writes nothing new. Nothing existing is read for its content, let alone changed. Dry run
unless --apply:

    python db/tools/seed_new_company.py --db hoistra_test            # counts only
    python db/tools/seed_new_company.py --db hoistra_test --apply
    python db/tools/seed_new_company.py --db hoistra_test --apply --validate   # + EUI snapshots for the two buildings

Sign-in after --apply (synthetic accounts, printed again at the end):
    ops.director@northbridge-estates.example  /  Northbridge-2026!   (admin)
    fm@northbridge-estates.example             /  Northbridge-2026!   (both buildings)
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
import uuid
from datetime import date, datetime, timedelta, timezone

import asyncpg

sys.stdout.reconfigure(encoding="utf-8")

ROOT_ENV = os.path.join(os.path.dirname(os.path.abspath(__file__)), *([".."] * 6), ".env")
NS = uuid.UUID("7c2e9d4a-1f3b-5a68-9c0d-2e5b7f8a1d33")
SEED = "seed:new-company"
TODAY = date(2026, 9, 21)
NOW = datetime(2026, 9, 21, 9, 0, tzinfo=timezone.utc)
PASSWORD = "Northbridge-2026!"

ORG_ID = uuid.uuid5(NS, "org:northbridge")
ORG = {"id": ORG_ID, "name": "Northbridge Estates Ltd", "country_code": "UK", "industry": "Property management",
       "address": "1 Northbridge Wharf, London E14 9GE", "timezone": "Europe/London"}

# ── the portfolio ───────────────────────────────────────────────────────────────────────
BUILDINGS = [
    {"code": "B-101", "name": "Harbour Point", "city": "London", "region": "Greater London", "postcode": "E14 9GE",
     "use": "Commercial", "use_key": "office", "floors": 12, "gia_m2": 14200.0, "hoist": 74,
     "address": "1 Northbridge Wharf, London E14 9GE", "eui": 118.0, "elec_eui": 95.0, "gas_eui": 120.0},
    {"code": "B-102", "name": "Ashgrove Court", "city": "Manchester", "region": "Greater Manchester", "postcode": "M15 4FN",
     "use": "Residential", "use_key": "residential", "floors": 9, "gia_m2": 8600.0, "hoist": 66,
     "address": "22 Ashgrove Road, Manchester M15 4FN", "eui": 142.0, "elec_eui": 60.0, "gas_eui": 100.0},
]
for b in BUILDINGS:
    b["id"] = uuid.uuid5(NS, f"building:{b['code']}")
    b["location_id"] = uuid.uuid5(NS, f"location:{b['code']}")
    b["gfa_sqft"] = round(b["gia_m2"] * 10.7639, 2)

# (type code, name, manufacturer, model, criticality, design life, replacement £, vendor code, trade)
ASSET_TYPES = {
    "office": [
        ("AHU-01", "Air handling unit 1 — Levels 1-6", "Trane", "CLCP 034", "high", 20, 86000, "MERI", "Mechanical"),
        ("AHU-02", "Air handling unit 2 — Levels 7-12", "Trane", "CLCP 034", "high", 20, 86000, "MERI", "Mechanical"),
        ("CHILLER-01", "Chiller 1 — roof plant", "Carrier", "30XA-452", "high", 20, 210000, "MERI", "Mechanical"),
        ("BOILER-01", "Boiler 1 — basement plant", "Hamworthy", "Wessex ModuMax 254", "high", 15, 42000, "MERI", "Gas"),
        ("LIFT-01", "Passenger lift 1", "Kone", "MonoSpace 700", "high", 25, 145000, "KSTL", "Lifts"),
        ("LIFT-02", "Passenger lift 2", "Kone", "MonoSpace 700", "high", 25, 145000, "KSTL", "Lifts"),
        ("FIRE-PANEL-01", "Fire alarm panel — main", "Advanced", "MxPro 5", "high", 15, 18500, "GRDF", "Fire"),
        ("PUMP-01", "Chilled water pump 1", "Grundfos", "TPE 100-240", "medium", 15, 9800, "MERI", "Mechanical"),
        ("DB-01", "Main distribution board", "Schneider", "Prisma iPM", "high", 25, 36000, "BRLT", "Electrical"),
    ],
    "residential": [
        ("BOILER-01", "Communal boiler 1", "Vaillant", "ecoTEC plus 120", "high", 15, 28000, "MERI", "Gas"),
        ("BOILER-02", "Communal boiler 2", "Vaillant", "ecoTEC plus 120", "high", 15, 28000, "MERI", "Gas"),
        ("LIFT-01", "Passenger lift — core A", "Otis", "Gen2 Comfort", "high", 25, 128000, "KSTL", "Lifts"),
        ("LIFT-02", "Passenger lift — core B", "Otis", "Gen2 Comfort", "high", 25, 128000, "KSTL", "Lifts"),
        ("FIRE-PANEL-01", "Fire alarm panel — lobby", "Kentec", "Syncro AS", "high", 15, 14200, "GRDF", "Fire"),
        ("PUMP-01", "Cold water booster set", "Grundfos", "Hydro Multi-E", "medium", 15, 11500, "MERI", "Mechanical"),
        ("EXTRACT-01", "Car park extract fan", "Vent-Axia", "ACM 400", "low", 15, 6400, "MERI", "Mechanical"),
        ("EML-01", "Emergency lighting — central battery", "Eaton", "CEAG ZB-S", "medium", 12, 9600, "BRLT", "Electrical"),
    ],
}

VENDORS = [
    ("MERI", "Meridian Mechanical Ltd", "Mechanical", "Reading", "GAS_SAFE", 380),
    ("BRLT", "Brightline Electrical", "Electrical", "Leeds", "NICEIC", 340),
    ("KSTL", "Kestrel Lift Services", "Lifts", "Birmingham", "LEIA", 420),
    ("GRDF", "Guardian Fire Systems", "Fire", "London", "BAFE_SP203_1", 360),
]

USERS = [
    ("ops.director", "Priya Natarajan", "admin", "Operations Director", "both"),
    ("fm", "Daniel Okafor", "user", "Facilities Manager", "both"),
    ("planner", "Hannah Reid", "user", "Maintenance Planner", "B-101"),
    ("inspector", "Tomasz Wolski", "user", "Compliance Inspector", "B-102"),
    ("tech.mech", "Sam Whitlock", "user", "Mechanical Technician", "both"),
    ("tech.elec", "Aisha Bello", "user", "Electrical Technician", "both"),
]

# UK pack certificate types this portfolio carries, by scope. (months, issuer, result)
BUILDING_CERTS = [
    ("EICR", 60, "Brightline Electrical (NICEIC)", "Satisfactory"),
    ("FRA", 12, "Guardian Fire Systems", "Tolerable"),
    ("FIRE_ALARM_SERVICE", 6, "Guardian Fire Systems", "Satisfactory"),
    ("EMERGENCY_LIGHTING", 1, "Brightline Electrical", "Satisfactory"),
    ("CP17", 12, "Meridian Mechanical (Gas Safe)", "Satisfactory"),
    ("BOILER_SERVICE", 6, "Meridian Mechanical (Gas Safe)", "Satisfactory"),
    ("L8_RISK", 24, "Clearwater Hygiene", "Satisfactory"),
    ("PAT", 12, "Brightline Electrical", "Satisfactory"),
    ("EPC", 120, "Accredited Energy Assessor", None),
]
# how far from today each certificate's expiry lands, per (building index, type) — a spread on purpose
EXPIRY_OFFSET_DAYS = {
    "EICR": (740, 1105), "FRA": (212, 38), "FIRE_ALARM_SERVICE": (-19, 96), "EMERGENCY_LIGHTING": (11, 24),
    "CP17": (301, 149), "BOILER_SERVICE": (88, -6), "L8_RISK": (402, 655), "PAT": (57, 233), "EPC": (2190, 1460),
}
EPC_BAND = {"B-101": ("C", 62), "B-102": ("D", 71)}
LIFT_LOLER_OFFSET = {"LIFT-01": 121, "LIFT-02": -33}  # second lift's LOLER has lapsed


def h(key: str, n: int) -> int:
    return int(hashlib.md5(key.encode()).hexdigest()[:8], 16) % max(n, 1)


def hf(key: str) -> float:
    return h(key, 10_000) / 10_000.0


def uid(key: str) -> uuid.UUID:
    return uuid.uuid5(NS, key)


def dsn_for(db: str) -> str:
    raw = os.environ.get("PLENUM_DB_DSN")
    if not raw:
        with open(ROOT_ENV, encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("PLENUM_DB_DSN="):
                    raw = line.split("=", 1)[1].strip().strip('"')
    if not raw:
        raise SystemExit("PLENUM_DB_DSN not set")
    raw = re.sub(r"^postgresql\+\w+://", "postgresql://", raw).split("?")[0]
    return raw.rsplit("/", 1)[0] + f"/{db}"


def status_for(expiry: date) -> tuple[str, int]:
    days = (expiry - TODAY).days
    return ("Current" if days > 0 else "Lapsed"), days


class Seeder:
    def __init__(self, c: asyncpg.Connection, apply: bool) -> None:
        self.c = c
        self.apply = apply
        self.counts: dict[str, int] = {}
        self.cols: dict[str, set[str]] = {}

    def note(self, what: str, n: int = 1) -> None:
        self.counts[what] = self.counts.get(what, 0) + n

    async def columns(self, table: str) -> set[str]:
        if table not in self.cols:
            rows = await self.c.fetch(
                "SELECT column_name FROM information_schema.columns WHERE table_schema='plenum_cafm' AND table_name=$1", table)
            self.cols[table] = {r["column_name"] for r in rows}
        return self.cols[table]

    async def insert(self, table: str, row: dict, conflict: str = "") -> None:
        """INSERT only the columns this database has; ON CONFLICT DO NOTHING."""
        have = await self.columns(table)
        vals = {k: v for k, v in row.items() if k in have and v is not None}
        self.note(table)
        if not self.apply:
            return
        cols = list(vals)
        ph = ", ".join(f"${i + 1}" for i in range(len(cols)))
        casts = {k: ("::jsonb" if isinstance(vals[k], str) and k in ("raw_metadata", "payload", "detail_json", "findings_jsonb",
                                                                     "component_scores", "condition_provenance", "use_mix",
                                                                     "provenance_json", "task_criticality_json") else "")
                 for k in cols}
        ph = ", ".join(f"${i + 1}{casts[k]}" for i, k in enumerate(cols))
        sql = f"INSERT INTO plenum_cafm.{table} ({', '.join(cols)}) VALUES ({ph}) ON CONFLICT {conflict or ''} DO NOTHING"
        await self.c.execute(sql, *[vals[k] for k in cols])

    # ── tenancy ──────────────────────────────────────────────────────────────────────
    async def organisation(self) -> None:
        await self.insert("organizations", {
            "id": ORG_ID, "name": ORG["name"], "industry": ORG["industry"], "address": ORG["address"], "country": "United Kingdom",
            "timezone": ORG["timezone"], "status": "active", "lifecycle": "active", "country_code": "UK",
            "admin_email": "ops.director@northbridge-estates.example", "created_at": NOW.replace(tzinfo=None), "updated_at": NOW.replace(tzinfo=None),
        })

    async def people(self) -> None:
        import bcrypt
        pw_hash = bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt(rounds=12)).decode()
        roles = {}
        for name in ("Admin", "Facilities Manager", "Maintenance Planner", "Compliance Inspector", "Technician"):
            rid = uid(f"role:{name}")
            roles[name] = rid
            await self.insert("roles", {"id": rid, "organization_id": ORG_ID, "name": name, "description": f"{name} at {ORG['name']}"},
                              "(organization_id, name)")
        for local, full, platform_role, job, scope in USERS:
            uid_ = uid(f"user:{local}")
            email = f"{local}@northbridge-estates.example"
            await self.insert("users", {
                "id": uid_, "organization_id": ORG_ID, "full_name": full, "email": email, "password_hash": pw_hash,
                "status": "active", "role": job, "platform_role": platform_role, "job_title": job, "email_verified": True,
                "email_verified_at": NOW, "activated_at": NOW, "can_ingest": platform_role == "admin" or local == "fm",
                "is_group": False, "failed_login_count": 0, "created_at": NOW.replace(tzinfo=None), "updated_at": NOW.replace(tzinfo=None),
                "password_changed_at": NOW,
            }, "(email)")
            role_name = {"admin": "Admin", "user": {"fm": "Facilities Manager", "planner": "Maintenance Planner",
                                                     "inspector": "Compliance Inspector"}.get(local, "Technician")}[platform_role]
            await self.insert("user_roles", {"id": uid(f"user_role:{local}"), "user_id": uid_, "role_id": roles[role_name]}, "(user_id, role_id)")
            for b in BUILDINGS:
                if scope == "both" or scope == b["code"]:
                    await self.insert("user_buildings", {"user_id": uid_, "building_id": b["id"], "granted_by": uid("user:ops.director"), "granted_at": NOW},
                                      "(user_id, building_id)")
            if local.startswith("tech."):
                await self.insert("technicians", {"id": uid(f"technician:{local}"), "organization_id": ORG_ID, "user_id": uid_,
                                                  "base_location": "Harbour Point" if local.endswith("mech") else "Ashgrove Court",
                                                  "availability_status": "available", "performance_score": 82 + h(local, 15)}, "(user_id)")

    # ── portfolio ────────────────────────────────────────────────────────────────────
    async def portfolio(self) -> None:
        for b in BUILDINGS:
            await self.insert("sites", {
                "site_id": b["code"], "site_name": b["name"], "site_code": b["code"], "city": b["city"], "country": "United Kingdom",
                "timezone": "Europe/London", "status": "active", "site_type": b["use"], "floors": str(b["floors"]),
                "gfa_sqm": str(int(b["gia_m2"])), "postcode": b["postcode"], "name": b["name"], "region": b["region"],
                "building_name": b["name"], "building_code": b["code"], "country_code": "UK", "use_type": b["use"],
                "metering_route": "Half-hourly supply + BMS sub-meters", "metering_granularity": "sub-metered",
                "benchmark_standard": "CIBSE TM46", "benchmark_kwh_per_m2": b["elec_eui"] + b["gas_eui"],
                "eui_kwh_per_m2": b["eui"], "hoist_score": b["hoist"], "address": b["address"], "created_at": NOW.replace(tzinfo=None),
            }, "(site_id)")
            await self.insert("locations", {
                "id": b["location_id"], "organization_id": ORG_ID, "name": f"{b['name']} - site", "type": "site", "level": 0,
                "address": b["address"], "city": b["city"], "country": "United Kingdom", "timezone": "Europe/London",
                "site_id": b["code"], "building_id": b["id"], "country_code": "UK", "region": b["region"], "postcode": b["postcode"],
                "created_at": NOW.replace(tzinfo=None), "updated_at": NOW,
            })
            await self.insert("buildings", {
                "building_id": b["id"], "site_id": b["code"], "location_id": b["location_id"], "name": b["name"],
                "primary_use": b["use"], "floors": b["floors"], "gross_area_sqft": b["gfa_sqft"], "eui_kwh_m2": b["eui"],
                "building_code": b["code"], "hoist_score": b["hoist"], "organization_id": ORG_ID,
                "raw_metadata": json.dumps({"country_code": "UK", "seed": SEED, "synthetic": True, "city": b["city"]}),
                "created_at": NOW, "updated_at": NOW,
            }, "(building_id)")
            await self.insert("building_energy_profiles", {
                "id": uid(f"profile:{b['code']}"), "organization_id": ORG_ID, "building_id": b["id"], "gia_m2": b["gia_m2"],
                "building_type": b["use_key"], "tm46_electricity_benchmark": b["elec_eui"], "tm46_gas_benchmark": b["gas_eui"],
                "created_at": NOW, "updated_at": NOW,
            }, "(building_id)")
            sections = [("Plant room", "plant", 0.06, 220.0), ("Car park", "car_park", 0.14, 45.0),
                        ("Office floors" if b["use_key"] == "office" else "Apartments", "office" if b["use_key"] == "office" else "residential",
                         0.80, b["elec_eui"])]
            b["sections"] = []
            for name, stype, share, ref in sections:
                sid = uid(f"section:{b['code']}:{stype}")
                b["sections"].append((sid, stype))
                await self.insert("building_sections", {
                    "section_id": sid, "organization_id": ORG_ID, "building_id": b["id"], "name": name, "section_type": stype,
                    "gross_area_m2": round(b["gia_m2"] * share, 2), "reference_eui_kwh_m2": ref, "reference_source": f"CIBSE TM46 {stype}",
                    "created_at": NOW, "updated_at": NOW,
                })
            for m in range(12):
                month = date(TODAY.year, TODAY.month, 1) - timedelta(days=30 * m)
                month = month.replace(day=1)
                season = (1 + __import__("math").cos(2 * 3.14159 * (month.month - 1) / 12)) / 2  # 1 in January, 0 in July
                await self.insert("weather_degree_days", {
                    "id": uid(f"wdd:{b['code']}:{month.isoformat()}"), "organization_id": ORG_ID, "building_id": b["id"], "month": month,
                    "hdd": round(40 + 300 * season, 1), "cdd": round(60 * (1 - season), 1), "base_temp_c": 15.5,
                    "station": "synthetic", "source": "manual", "created_at": NOW,
                })

    # ── vendors ──────────────────────────────────────────────────────────────────────
    async def vendors(self) -> None:
        self.vendor_ids = {}
        self.contracts = {}
        for code, name, trade, city, accreditation, day_rate in VENDORS:
            vid = uid(f"vendor:{code}")
            self.vendor_ids[code] = vid
            await self.insert("vendors", {
                "id": vid, "organization_id": ORG_ID, "vendor_name": name, "vendor_code": code, "city": city, "country": "UK",
                "phone": f"+44 20 7{h(code, 900) + 100:03d} {h(code + 'x', 9000) + 1000:04d}", "website": f"https://{code.lower()}.example",
                "status": "active", "block_state": "Clear", "notes": f"{trade} contractor · {SEED}",
                "created_at": NOW.replace(tzinfo=None), "updated_at": NOW.replace(tzinfo=None),
            }, "(organization_id, vendor_code)")
            await self.insert("vendor_contacts", {
                "id": uid(f"contact:{code}"), "vendor_id": vid, "name": f"{trade} account manager", "designation": "Account manager",
                "email": f"accounts@{code.lower()}.example", "phone": f"+44 7{h(code + 'm', 900000000) + 100000000:09d}",
            })
            scope = "both" if code != "MERI" else "B-101"  # Meridian holds the office plant; the residential gas is a second contract
            names = [("both", f"{trade} PPM & reactive — portfolio")] if scope == "both" else \
                    [("B-101", f"{trade} PPM — Harbour Point"), ("B-102", f"Communal heating PPM — Ashgrove Court")]
            for bscope, cname in names:
                cid = uid(f"contract:{code}:{bscope}")
                self.contracts[(code, bscope)] = cid
                start = date(2025, 4, 1) + timedelta(days=h(cname, 120))
                await self.insert("vendor_contracts", {
                    "id": cid, "organization_id": ORG_ID, "vendor_id": vid, "contract_name": cname, "contract_start": start,
                    "contract_end": start.replace(year=start.year + 3), "contract_value": 18000 + h(cname + "v", 60) * 1000,
                    "status": "active", "country_code": "UK", "service_scope": f"{trade} planned and reactive maintenance",
                    "visits_per_year": {"Lifts": 12, "Fire": 4, "Electrical": 2, "Mechanical": 4}[trade],
                    "sla_terms": json.dumps({"p1_response_h": 2, "p2_response_h": 8, "p3_response_h": 48, "p1_complete_h": 8, "p2_complete_h": 48, "p3_complete_h": 120}),
                    "created_at": NOW.replace(tzinfo=None),
                })
                await self.insert("contract_sla_parameters", {
                    "id": uid(f"sla:{code}:{bscope}"), "organization_id": ORG_ID, "vendor_id": vid, "contract_id": cid,
                    "contract_ref": cname, "status": "confirmed", "signed_date": start,
                    "sla_response_p1_hours": 2.0, "sla_response_p2_hours": 8.0, "sla_response_p3_hours": 48.0, "sla_response_p4_hours": 120.0,
                    "sla_completion_p1_hours": 8.0, "sla_completion_p2_hours": 48.0, "sla_completion_p3_hours": 120.0, "sla_completion_p4_hours": 336.0,
                    "labour_day_rate": float(day_rate), "labour_hour_rate": round(day_rate / 8, 2), "overtime_rate": round(day_rate / 8 * 1.5, 2),
                    "call_out_rate": 150.0, "payment_terms": "Net 30", "confirmed_by": uid("user:ops.director"), "confirmed_at": NOW,
                    "task_criticality_json": json.dumps({"L1": "Life safety / statutory / critical plant failure", "L2": "Business critical", "L3": "Routine"}),
                    "created_at": NOW, "updated_at": NOW,
                })
            # vendor-scope certificate: one lapsing soon (Kestrel) so the register has a warning
            off = {"MERI": 402, "BRLT": 288, "KSTL": 25, "GRDF": 164}[code]
            expiry = TODAY + timedelta(days=off)
            status, days = status_for(expiry)
            cert_id = uid(f"cert:vendor:{code}")
            await self.insert("compliance_certificates", {
                "id": cert_id, "org_id": ORG_ID, "organization_id": ORG_ID, "certificate_ref": f"{accreditation}-NB-{code}",
                "cert_type": accreditation, "certificate_type_code": accreditation, "certificate_number": f"{accreditation}-NB-{code}",
                "issuer": {"GAS_SAFE": "Gas Safe Register", "NICEIC": "NICEIC", "LEIA": "LEIA", "BAFE_SP203_1": "BAFE"}[accreditation],
                "issue_date": expiry.replace(year=expiry.year - 1), "expiry_date": expiry, "next_due_date": expiry, "inspection_frequency_months": 12,
                "result": "Registered", "remedial_status": "Closed", "cert_scope": "Vendor", "country_code": "UK", "status": status,
                "days_to_expiry": days, "insurance_risk_flag": days <= 30, "vendor_id": vid,
                "raw_metadata": json.dumps({"synthetic": True, "seed": SEED, "vendor_name": name}), "created_at": NOW, "updated_at": NOW,
            })

    # ── assets ───────────────────────────────────────────────────────────────────────
    async def assets(self) -> None:
        self.assets = {}
        for b in BUILDINGS:
            b["assets"] = []
            plant = next(s for s in b["sections"] if s[1] == "plant")[0]
            for n, (tcode, name, make, model, crit, life, value, vcode, trade) in enumerate(ASSET_TYPES[b["use_key"]]):
                code = f"{b['code']}-{tcode}"
                aid = uid(f"asset:{code}")
                grade = 1 + h(code + "grade", 5)  # 1 good … 5 poor
                installed = date(2012 + h(code + "y", 12), 1 + h(code + "m", 12), 1 + h(code + "d", 28))
                row = {
                    "id": aid, "organization_id": ORG_ID, "location_id": b["location_id"], "asset_name": name, "asset_code": code,
                    "serial_number": f"SN-{h(code, 10**7):07d}", "manufacturer": make, "make": make, "model": model, "model_number": model,
                    "installation_date": installed, "warranty_expiry": installed.replace(year=installed.year + 2), "status": "active",
                    "is_online": True, "is_site": False, "criticality": crit, "health_score": max(20, 100 - grade * 15 - h(code + "h", 10)),
                    "condition_score": grade, "condition_updated_at": NOW - timedelta(days=h(code + "cu", 120)),
                    "condition_provenance": json.dumps({"source": SEED, "graded_by": "seed"}), "building_id": b["id"],
                    "section_id": plant if tcode.split("-")[0] in ("AHU", "CHILLER", "BOILER", "PUMP", "DB", "EML") else None,
                    "replacement_value": float(value), "replacement_currency": "GBP", "design_life_years": float(life), "wear_coefficient": 1.0,
                    "vendor_id": str(self.vendor_ids[vcode]), "notes": f"{trade} · maintained by {vcode} · {SEED}",
                    "raw_metadata": json.dumps({"synthetic": True, "seed": SEED, "trade": trade}),
                    "created_at": NOW.replace(tzinfo=None), "updated_at": NOW.replace(tzinfo=None),
                }
                await self.insert("assets", row, "(id)")
                a = {"id": aid, "code": code, "tcode": tcode, "name": name, "vendor": vcode, "trade": trade, "grade": grade, "building": b}
                b["assets"].append(a)
                self.assets[code] = a
                # readings on plant: the condition view reads pressure / temperature against bands
                if tcode.split("-")[0] in ("CHILLER", "BOILER", "PUMP"):
                    for k in range(6):
                        when = NOW - timedelta(days=7 * k + h(code + str(k), 5))
                        for rtype, unit, base in (("pressure", "bar", 4.2), ("temperature_supply", "°C", 74.0 if "BOILER" in tcode else 6.5)):
                            drift = (grade - 1) * 0.08 * (k / 6.0)
                            await self.insert("asset_readings", {
                                "id": uid(f"reading:{code}:{rtype}:{k}"), "organization_id": ORG_ID, "asset_id": aid, "reading_type": rtype,
                                "value": round(base * (1 + drift) * (0.97 + 0.06 * hf(code + rtype + str(k))), 2), "unit": unit,
                                "recorded_at": when.replace(tzinfo=None),
                            })
        # a few spare parts and the parts fitted on work
        self.parts = []
        for code, name, price, supplier, months, stock, reorder in (
            ("PRT-AHU-BELT", "AHU drive belt set", 140.0, "MERI", 12, 6, 4), ("PRT-BOILER-IGN", "Boiler ignition electrode", 85.0, "MERI", 12, 2, 3),
            ("PRT-LIFT-ROPE", "Lift hoist rope set", 2400.0, "KSTL", 36, 1, 1), ("PRT-FIRE-DET", "Optical smoke detector", 48.0, "GRDF", 24, 30, 20),
            ("PRT-PUMP-SEAL", "Pump mechanical seal", 210.0, "MERI", 18, 0, 2)):
            pid = uid(f"part:{code}")
            self.parts.append((pid, code, price))
            await self.insert("spare_parts", {
                "id": pid, "organization_id": ORG_ID, "part_name": name, "part_code": code, "description": f"{name} · {SEED}",
                "unit_price": price, "stock_quantity": stock, "reorder_level": reorder, "max_quantity": reorder * 4, "unit_of_measure": "each",
                "supplier_id": self.vendor_ids[supplier], "warranty_months": months, "created_at": NOW.replace(tzinfo=None), "updated_at": NOW.replace(tzinfo=None),
            }, "(organization_id, part_code)")

    # ── certificates on the buildings ─────────────────────────────────────────────────
    async def certificates(self) -> None:
        for i, b in enumerate(BUILDINGS):
            for tcode, months, issuer, result in BUILDING_CERTS:
                expiry = TODAY + timedelta(days=EXPIRY_OFFSET_DAYS[tcode][i])
                issued = expiry - timedelta(days=int(months * 30.4))
                status, days = status_for(expiry)
                cid = uid(f"cert:{b['code']}:{tcode}")
                doc_id = uid(f"doc:{b['code']}:{tcode}")
                await self.insert("documents", {
                    "document_id": doc_id, "building_id": b["id"], "doc_type": "compliance_certificate",
                    "title": f"{tcode} — {b['name']}", "file_name": f"{b['code']}_{tcode}_{issued.isoformat()}.pdf",
                    "uploaded_at": datetime.combine(issued, datetime.min.time(), tzinfo=timezone.utc), "created_at": NOW,
                }, "(document_id)")
                band = EPC_BAND[b["code"]] if tcode == "EPC" else None
                await self.insert("compliance_certificates", {
                    "id": cid, "org_id": ORG_ID, "organization_id": ORG_ID, "certificate_ref": f"{tcode}-{b['code']}-{issued.year}",
                    "cert_type": tcode, "certificate_type_code": tcode, "certificate_number": f"{tcode}-{b['code']}-{issued.year}",
                    "issuer": issuer, "issue_date": issued, "expiry_date": expiry, "next_due_date": expiry, "inspection_frequency_months": months,
                    "result": (band[0] if band else result), "remedial_status": "Closed" if result != "Tolerable" else "Open",
                    "remedial_actions": "Two fire doors to be re-hung; signage at level 3" if result == "Tolerable" else None,
                    "cert_scope": "Building", "country_code": "UK", "status": status, "days_to_expiry": days, "insurance_risk_flag": days <= 0,
                    "document_id": doc_id, "source_document_id": doc_id, "building_id": b["id"], "building_name": b["name"],
                    "building_reference": b["code"], "site_ref": b["code"], "region": b["region"],
                    "energy_rating": band[0] if band else None, "energy_score": band[1] if band else None,
                    "raw_metadata": json.dumps({"synthetic": True, "seed": SEED, "site_label": b["name"]}), "created_at": NOW, "updated_at": NOW,
                })
            # LOLER per lift: one current, one lapsed
            for a in b["assets"]:
                if not a["tcode"].startswith("LIFT"):
                    continue
                expiry = TODAY + timedelta(days=LIFT_LOLER_OFFSET[a["tcode"]])
                status, days = status_for(expiry)
                await self.insert("compliance_certificates", {
                    "id": uid(f"cert:{a['code']}:LOLER"), "org_id": ORG_ID, "organization_id": ORG_ID, "certificate_ref": f"LOLER-{a['code']}",
                    "cert_type": "LOLER", "certificate_type_code": "LOLER", "certificate_number": f"LOLER-{a['code']}", "issuer": "Kestrel Lift Services (LEIA)",
                    "issue_date": expiry - timedelta(days=182), "expiry_date": expiry, "next_due_date": expiry, "inspection_frequency_months": 6,
                    "result": "Satisfactory" if days > 0 else "Thorough examination overdue", "remedial_status": "Closed", "cert_scope": "Building",
                    "country_code": "UK", "status": status, "days_to_expiry": days, "insurance_risk_flag": days <= 0, "asset_id": a["id"],
                    "vendor_id": self.vendor_ids["KSTL"], "building_id": b["id"], "building_name": b["name"], "building_reference": b["code"],
                    "site_ref": b["code"], "raw_metadata": json.dumps({"synthetic": True, "seed": SEED}), "created_at": NOW, "updated_at": NOW,
                })

    # ── work, visits, inspections ─────────────────────────────────────────────────────
    async def work(self) -> None:
        fm = uid("user:fm")
        faults = [("Pump leaking on {a}", "medium"), ("{a} tripping on high pressure", "high"), ("Noise and vibration from {a}", "medium"),
                  ("{a} not starting after power cut", "high"), ("Water ingress near {a}", "medium"), ("Annual service — {a}", "low"),
                  ("Fault LED on {a}", "medium"), ("Filter change — {a}", "low"), ("Door not levelling — {a}", "high"), ("Sensor drift on {a}", "low")]
        for b in BUILDINGS:
            n_wo = 30
            for k in range(n_wo):
                a = b["assets"][h(f"{b['code']}:wo:{k}:asset", len(b["assets"]))]
                title_t, prio = faults[h(f"{b['code']}:wo:{k}:fault", len(faults))]
                raised = NOW - timedelta(days=5 + h(f"{b['code']}:wo:{k}:when", 355), hours=h(f"{k}", 10))
                r = h(f"{b['code']}:wo:{k}:state", 100)
                status = "closed" if r < 60 else "open" if r < 80 else "pending_approval" if r < 90 else "blocked"
                is_ppm = title_t.startswith(("Annual", "Filter")) or h(f"{b['code']}:wo:{k}:ppm", 100) < 15
                resp_h = {"high": 1.5, "medium": 6, "low": 30}[prio] * (0.6 + 0.9 * hf(f"{k}:r"))
                attend = raised + timedelta(hours=resp_h)
                sla_due = raised + timedelta(hours={"high": 8, "medium": 48, "low": 120}[prio])
                completed = attend + timedelta(hours=2 + h(f"{k}:c", 40)) if status == "closed" else None
                est = 300 + h(f"{k}:est", 2400)
                actual = round(est * (0.85 + 0.35 * hf(f"{k}:act")), 2) if completed else None
                first_fix = completed is not None and h(f"{k}:ff", 100) < 78
                wo_id = uid(f"wo:{b['code']}:{k}")
                vendor = self.vendor_ids[a["vendor"]]
                await self.insert("work_orders", {
                    "id": wo_id, "organization_id": ORG_ID, "asset_id": a["id"], "location_id": b["location_id"], "building_id": b["id"],
                    "wo_code": f"WO-{b['code']}-{k + 1:02d}", "title": title_t.format(a=a["name"]), "description": f"{title_t.format(a=a['name'])} reported by site team.",
                    "priority": prio, "status": status, "wo_type": "PPM" if is_ppm else "Reactive", "maintenance_type": "planned" if is_ppm else "reactive",
                    "created_by": fm, "requested_by_id": fm, "vendor_id": vendor, "assigned_vendor": vendor, "vendor": a["vendor"],
                    "asset": a["name"], "asset_category": a["trade"], "estimated_cost": float(est), "actual_cost": actual,
                    "estimated_hours": round(2 + h(f"{k}:eh", 6) * 0.5, 1), "actual_hours": round((completed - attend).total_seconds() / 3600, 1) if completed else None,
                    "created_at": raised.replace(tzinfo=None), "raised_at": raised, "reported_at": raised, "responded_at": attend - timedelta(minutes=20),
                    "attended_at": attend, "completed_at": completed.replace(tzinfo=None) if completed else None, "closed_at": completed,
                    "sla_due_at": sla_due.replace(tzinfo=None), "first_fix": first_fix if completed else None, "recall": (not first_fix) if completed else None,
                    "return_visit": (not first_fix) if completed else None, "conflict_flag": False, "inspection_required": prio == "high" and status != "closed",
                    "updated_at": (completed or attend).replace(tzinfo=None), "notes": SEED,
                    "contract_id": self.contracts.get((a["vendor"], "both")) or self.contracts.get((a["vendor"], b["code"])),
                })
                if completed and k % 7 == 0 and self.parts:
                    pid, pcode, price = self.parts[h(f"{k}:part", len(self.parts))]
                    await self.insert("work_order_parts", {
                        "id": uid(f"wop:{b['code']}:{k}"), "work_order_id": wo_id, "part_id": pid, "asset_id": a["id"], "quantity_used": 1,
                        "unit_cost": price, "fitted_at": completed.date(), "warranty_expiry": completed.date().replace(year=completed.year + 1),
                        "invoiced_value": round(price * 1.35, 2), "currency": "GBP",
                    })
            # PPM visits: monthly for lifts, quarterly for the rest, over the past year
            for a in b["assets"]:
                freq, step = ("Monthly", 30) if a["tcode"].startswith("LIFT") else ("Quarterly", 91)
                contract = self.contracts.get((a["vendor"], "both")) or self.contracts.get((a["vendor"], b["code"]))
                for i in range(12 if step == 30 else 4):
                    sched = TODAY - timedelta(days=step * i + h(f"{a['code']}:v:{i}", 9))
                    r = h(f"{a['code']}:visit:{i}", 100)
                    status = "done" if r < 78 else "missed" if r < 90 else "deferred"
                    var = h(f"{a['code']}:var:{i}", 21) - 6 if status == "done" else None
                    await self.insert("ppm_visits", {
                        "id": uid(f"visit:{a['code']}:{i}"), "organization_id": ORG_ID, "vendor_id": self.vendor_ids[a["vendor"]], "contract_id": contract,
                        "asset_id": a["id"], "asset_code": a["code"], "ppm_ref": f"PPM-{a['code']}-{i + 1:02d}", "frequency": freq,
                        "scheduled_date": sched, "completed_date": (sched + timedelta(days=var)) if var is not None else None,
                        "tolerance_days": 14, "variance_days": var, "within_tolerance": (abs(var) <= 14) if var is not None else None,
                        "status": status, "source": SEED, "raw_metadata": json.dumps({"synthetic": True}), "deferred": status == "deferred",
                        "deferred_to": (sched + timedelta(days=21)) if status == "deferred" else None,
                        "deferral_reason": "Access to plant room refused by tenant" if status == "deferred" else None,
                        "score_month": sched.replace(day=1), "created_at": NOW, "updated_at": NOW,
                    })
            # inspection reports on the worst-graded assets
            worst = sorted(b["assets"], key=lambda x: -x["grade"])[:4]
            for j, a in enumerate(worst):
                risk = "High" if a["grade"] >= 4 else "Medium" if a["grade"] == 3 else "Low"
                await self.insert("inspections", {
                    "id": uid(f"inspection:{a['code']}"), "asset_id": a["id"], "asset_code": a["code"], "inspector": "Site engineer",
                    "inspection_date": TODAY - timedelta(days=12 + 17 * j), "finding_type": "Condition", "risk_level": risk,
                    "observations": {"High": "Excessive wear and intermittent faults; efficiency below commissioned figure",
                                     "Medium": "Ageing components; performance drifting from design", "Low": "Serviceable; minor cosmetic wear"}[risk],
                    "recommendation": {"High": "Plan replacement within 12 months; increase PPM frequency", "Medium": "Overhaul at next service",
                                       "Low": "Monitor at routine PPM"}[risk],
                    "corrective_action": risk != "Low", "source_file": SEED,
                    "findings_jsonb": json.dumps({"grade": a["grade"], "synthetic": True}), "created_at": NOW,
                })

    # ── energy ───────────────────────────────────────────────────────────────────────
    async def energy(self) -> None:
        for b in BUILDINGS:
            meters = [("electricity", False, None, None, b["elec_eui"] * 0.70), ("gas", False, None, None, b["gas_eui"])]
            subs = [a for a in b["assets"] if a["tcode"].split("-")[0] in ("AHU", "CHILLER", "BOILER", "LIFT")][:2]
            plant = next(s for s in b["sections"] if s[1] == "plant")[0]
            for a in subs:
                meters.append(("electricity", True, a["id"], plant, b["elec_eui"] * 0.15))
            for i, (fuel, sub, asset_id, section, eui) in enumerate(meters):
                mid = uid(f"meter:{b['code']}:{fuel}:{i}")
                ident = f"NB-{b['code']}-{fuel[:1].upper()}{i}"
                await self.insert("energy_meters", {
                    "id": mid, "organization_id": ORG_ID, "building_id": b["id"], "asset_id": asset_id, "meter_type": fuel,
                    "mpan": ident if fuel == "electricity" else None, "mprn": ident if fuel == "gas" else None,
                    "tariff_gbp_per_kwh": 0.28 if fuel == "electricity" else 0.07, "carbon_kg_per_kwh": 0.207 if fuel == "electricity" else 0.183,
                    "is_sub_meter": sub, "active": True, "section_id": section,
                    "raw_metadata": json.dumps({"synthetic": True, "seed": SEED, "simulate": True, "sim_note": "half-hourly synthetic feed"}),
                    "created_at": NOW, "updated_at": NOW,
                }, "(id)")
                factor = 0.85 + 0.40 * hf(f"{mid}:factor")  # some buildings over their benchmark, some within
                annual_kwh = eui * factor * b["gia_m2"]
                per_half_hour = annual_kwh / (365 * 48)
                self.note("meter_readings (half-hourly rows)", 365 * 48)
                if not self.apply:
                    continue
                await self.c.execute(f"""
                    INSERT INTO plenum_cafm.meter_readings
                        (id, organization_id, meter_id, reading_at, period_minutes, consumption_kwh, source, quality_flag, created_at)
                    SELECT gen_random_uuid(), $2::uuid, $1::uuid, ts, 30,
                           round(($3::numeric
                             * CASE WHEN $4::text = 'gas'
                                    THEN (0.30 + 1.40 * (1 + cos(2 * pi() * (extract(doy FROM ts) - 15) / 365.25)) / 2)
                                         * (CASE WHEN extract(hour FROM ts) BETWEEN 5 AND 21 THEN 1.25 ELSE 0.5 END)
                                    ELSE (CASE WHEN extract(isodow FROM ts) >= 6 THEN 0.5 ELSE 1.0 END)
                                         * (CASE WHEN extract(hour FROM ts) BETWEEN 7 AND 18 THEN 1.45 ELSE 0.55 END)
                                         * (0.92 + 0.16 * (1 + cos(2 * pi() * (extract(doy FROM ts) - 15) / 365.25)) / 2)
                               END
                             * (0.94 + 0.12 * (('x' || substr(md5($1::text || ts::text), 1, 6))::bit(24)::int % 1000) / 1000.0)
                           )::numeric, 3),
                           'simulator', NULL, now()
                      FROM generate_series($5::timestamptz - interval '365 days', $5::timestamptz - interval '30 minutes', interval '30 minutes') AS ts
                    ON CONFLICT (meter_id, reading_at) DO NOTHING""",
                    str(mid), str(ORG_ID), per_half_hour, fuel, NOW.replace(minute=0, second=0, microsecond=0))
            # two open anomalies per building so the Energy page and Home have something before a scan
            main = uid(f"meter:{b['code']}:electricity:0")
            for kind, pct, kwh, days_ago in (("weekend_spike", 38.0, 4200.0, 6), ("nonocc_spike", 27.0, 2900.0, 13)):
                await self.insert("energy_anomalies", {
                    "id": uid(f"anomaly:{b['code']}:{kind}"), "organization_id": ORG_ID, "building_id": b["id"], "meter_id": main,
                    "anomaly_type": kind, "detected_at": NOW - timedelta(days=days_ago), "window_start": NOW - timedelta(days=days_ago + 7),
                    "window_end": NOW - timedelta(days=days_ago), "metric_pct": pct, "excess_kwh": kwh, "annualised_excess_kwh": kwh * 52 / 7 * 0.6,
                    "financial_gbp": round(kwh * 0.28, 2), "tariff_used": 0.28, "currency": "GBP", "status": "open",
                    "detail_json": json.dumps({"synthetic": True, "seed": SEED, "note": f"{kind} on the main electricity supply"}),
                    "created_at": NOW, "updated_at": NOW,
                })
                await self.insert("approvals_queue_items", {
                    "id": uid(f"queue:{b['code']}:{kind}"), "organization_id": ORG_ID, "source_feature": "E", "item_type": "energy_anomaly",
                    "summary": f"{b['name']}: {kind.replace('_', ' ')} on main supply, +{pct:.0f}% (£{kwh * 0.28:,.0f})", "severity": "medium" if pct < 30 else "high",
                    "status": "pending", "related_entity_type": "energy_anomaly", "related_entity_id": uid(f"anomaly:{b['code']}:{kind}"),
                    "payload": json.dumps({"synthetic": True, "building": b["name"], "anomaly_type": kind}), "created_at": NOW, "updated_at": NOW,
                })

    async def run(self) -> None:
        await self.organisation()
        await self.people()
        await self.portfolio()
        await self.vendors()
        await self.assets()
        await self.certificates()
        await self.work()
        await self.energy()


async def validate(db: str) -> None:
    """EUI snapshots for the two new buildings only, through the service's own benchmark engine."""
    os.environ["DB_URL"] = re.sub(r"^postgresql://", "postgresql+asyncpg://", dsn_for(db))
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
    from src import db as _db  # noqa: E402
    from src.engines.energy import benchmarks  # noqa: E402
    async with _db.AsyncSessionLocal() as s:
        rep = await benchmarks.validate(s, building_ids=[b["id"] for b in BUILDINGS], organization_id=ORG_ID, persist=True)
        print("  validation:", rep.get("summary"), "snapshots written:", rep.get("snapshots_written"))
    await _db.engine.dispose()


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="hoistra_test")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--validate", action="store_true", help="after --apply, write EUI snapshots for the two buildings")
    args = ap.parse_args()
    c = await asyncpg.connect(dsn_for(args.db), timeout=120)
    print(f"\n################ {args.db} · {ORG['name']} {'(APPLY)' if args.apply else '(dry run)'} ################")
    exists = await c.fetchval("SELECT count(*) FROM plenum_cafm.organizations WHERE id = $1", ORG_ID)
    print(f"  organisation {ORG_ID} {'already present — re-run adds only what is missing' if exists else 'not present — will be created'}")
    s = Seeder(c, args.apply)
    try:
        if args.apply:
            async with c.transaction():
                await s.run()
        else:
            await s.run()
    finally:
        await c.close()
    print("  rows " + ("written" if args.apply else "that would be written") + " (ON CONFLICT DO NOTHING — existing ids untouched):")
    for k, v in sorted(s.counts.items()):
        print(f"    {k:36} {v:>8,}")
    if args.apply and args.validate:
        await validate(args.db)
    if args.apply:
        print("\n  Sign in (synthetic accounts):")
        for local, full, role, job, scope in USERS:
            print(f"    {local + '@northbridge-estates.example':44} {PASSWORD}   {role:5} {job} · {scope}")
        print(f"\n  Buildings: " + ", ".join(f"{b['name']} ({b['code']}, {b['id']})" for b in BUILDINGS))


if __name__ == "__main__":
    asyncio.run(main())
