"""Two genuine energy certificates, for their own buildings, under a test company.

Every other certificate in the Plenum Technologies test set is invented - its numbers are made
up on purpose, and no register lists them. These two are real: each is copied, field for field,
from its record on the GOV.UK "Find an energy certificate" register, and each sits on the
building the register names, never on Bishopsgate Tower:

  TM44 8835-5966-2832-1595-3906  RT5, Crossrail, Canary Wharf Estate, London E14 5AB
                                 inspected 18 May 2026 (Level 4), valid until 18 May 2031
  DEC  9920-1010-0626-0890-2091  Manchester City Council, Town Hall, Albert Square, M2 5DB
                                 issued 12 Oct 2012, operational rating 93 D, expired 30 Sep 2013

So the compliance console holds one certificate the register confirms as current and one it
confirms as expired - the live check's two honest answers - beside the invented ones it cannot
find. The rows are written unverified; --apply then runs the platform's own check
(ccc_verify.verify_stored_certificate), which looks each number up on the register and records
what it found, exactly as Verify now does.

Writes: sites S-302 / S-303, buildings B-302 / B-303 (organization set), the two
compliance_certificates. Keyed by uuid5, so a second run changes nothing but the check's
timestamp. Dry run by default; --apply writes. Refuses any database but hoistra_test.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
SVC = os.path.join(HERE, "..", "..", "apps", "backend", "cafm-connector-service-final", "svc-operations-intelligence")
NS = uuid.UUID("2f6a3c1e-7b9d-4e55-a1c8-9d0e4b7f3a21")
REGISTER = "GOV.UK Find an energy certificate"
URL = "https://find-energy-certificate.service.gov.uk/energy-certificate/"

#: As the register publishes them (read 29 Sep 2026). Nothing here is inferred.
BUILDINGS = [
    {"code": "B-302", "site": "S-302", "name": "RT5, Crossrail", "city": "London", "region": "Greater London",
     "postcode": "E14 5AB", "address": "RT5, Crossrail, Canary Wharf Estate, London E14 5AB",
     "area_m2": 1739.0, "area_basis": "treated floor area on the TM44 record",
     "cert": {"type": "TM44", "number": "8835-5966-2832-1595-3906", "issue": date(2026, 5, 18),
              "expiry": date(2031, 5, 18), "months": 60, "status": "Current", "result": "Inspected - Level 4",
              "inspector": "Steve McAnulla", "accreditation": "QUID204855", "issuer": "Vital Energy",
              "rating": None, "score": None,
              "record": {"inspection_level": "Level 4", "accreditation_scheme": "Quidos Limited",
                         "assessment_software": "Quidos, AIRS, v2.0", "total_effective_rated_output_kw": 1040,
                         "treated_floor_area_m2": 1739, "refrigerant": "HFC 134a", "refrigerant_charge_kg": 116,
                         "system": "4 x Geoclima chillers, 4 cooling towers, 4 x Dalair AHUs; Eton BMS"}}},
    {"code": "B-303", "site": "S-303", "name": "Manchester Town Hall", "city": "Manchester", "region": "North West",
     "postcode": "M2 5DB", "address": "Manchester City Council, Town Hall, Albert Square, Manchester M2 5DB",
     "area_m2": 19810.0, "area_basis": "total useful floor area on the DEC record",
     "cert": {"type": "DEC", "number": "9920-1010-0626-0890-2091", "issue": date(2012, 10, 12),
              "expiry": date(2013, 9, 30), "months": 12, "status": "Lapsed", "result": "Operational rating 93 D",
              "inspector": "Christopher Burrows", "accreditation": "STRO003726", "issuer": "Manchester City Council",
              "rating": "D", "score": 93,
              "record": {"accreditation_scheme": "Stroma Certification Ltd", "nominated_date": "2012-10-01",
                         "assessment_software": "SystemsLink, ORToolkit, v3.6", "main_heating_fuel": "Natural Gas",
                         "building_environment": "Heating and Natural Ventilation",
                         "annual_energy_kwh_m2": {"electricity": 85, "other_fuels": 237},
                         "typical_energy_kwh_m2": {"electricity": 130, "other_fuels": 143},
                         "previous_ratings": {"2012-10": "93 D", "2011-10": "89 D", "2010-10": "93 D"}}}},
]


async def main() -> None:
    from _env import hoistra_test_dsn
    import asyncpg

    ap = argparse.ArgumentParser()
    ap.add_argument("--org-name", default="Plenum Technologies")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    dsn = hoistra_test_dsn()
    c = await asyncpg.connect(dsn.replace("postgresql+asyncpg", "postgresql"), timeout=15)
    try:
        if await c.fetchval("SELECT current_database()") != "hoistra_test":
            raise SystemExit("refusing: hoistra_test only")
        org = await c.fetchval("SELECT id FROM plenum_cafm.organizations WHERE lower(name) = lower($1)", args.org_name)
        if not org:
            raise SystemExit(f"no organization named {args.org_name!r}")
        plan = []
        for b in BUILDINGS:
            taken = await c.fetchval("SELECT organization_id::text FROM plenum_cafm.buildings WHERE building_code = $1", b["code"])
            if taken and taken != str(org):
                raise SystemExit(f"{b['code']} already belongs to organization {taken}")
            bid = uuid.uuid5(NS, f"building:{org}:{b['code']}")
            cid = uuid.uuid5(NS, f"certificate:{org}:{b['cert']['number']}")
            plan.append((b, bid, cid))
            print(f"  {b['code']} {b['name']} ({b['postcode']}) - {b['cert']['type']} {b['cert']['number']}"
                  f" {b['cert']['status'].lower()}, valid until {b['cert']['expiry']}")
        if not args.apply:
            print("\n  dry run - pass --apply to write, then run the platform's register check on both")
            return
        async with c.transaction():
            for b, bid, cid in plan:
                await c.execute(
                    """INSERT INTO plenum_cafm.sites (site_id, site_name, city, country_code, region, postcode, status, timezone, created_at)
                       VALUES ($1, $2, $3, 'UK', $4, $5, 'active', 'Europe/London', now())
                       ON CONFLICT (site_id) DO UPDATE SET site_name = EXCLUDED.site_name, postcode = EXCLUDED.postcode""",
                    b["site"], b["name"], b["city"], b["region"], b["postcode"])
                meta = {"source": "seed_genuine_energy_certificates", "country": "UK", "country_code": "UK",
                        "address": b["address"], "postcode": b["postcode"],
                        "use_type": "Not stated on the register", "area_basis": b["area_basis"]}
                await c.execute(
                    """INSERT INTO plenum_cafm.buildings (building_id, name, building_code, organization_id, site_id, floors,
                              gross_area_sqft, country_code, gross_internal_area_m2, primary_use, raw_metadata, created_at, updated_at)
                       VALUES ($1, $2, $3, $4, $5, NULL, $6, 'UK', $7, 'Other'::plenum_cafm.building_primary_use, $8::jsonb, now(), now())
                       ON CONFLICT (building_id) DO UPDATE SET name = EXCLUDED.name, raw_metadata = EXCLUDED.raw_metadata,
                              updated_at = now()""",
                    bid, b["name"], b["code"], org, b["site"], round(b["area_m2"] * 10.7639, 2), str(b["area_m2"]), json.dumps(meta))
                k = b["cert"]
                cmeta = {"source": f"{REGISTER} - entered as published", "register_url": URL + k["number"],
                         "postcode": b["postcode"], "property_address": b["address"], "register_record": k["record"]}
                await c.execute(
                    """INSERT INTO plenum_cafm.compliance_certificates (id, organization_id, certificate_type_code, certificate_number,
                              cert_scope, building_id, building_code, building_name, issuer, issue_date, expiry_date, next_due_date,
                              inspection_frequency_months, inspector_name, inspector_accreditation_number, result, status,
                              country_code, energy_rating, energy_score, raw_metadata, created_at, updated_at)
                       VALUES ($1, $2, $3, $4, 'Building', $5, $6, $7, $8, $9, $10, $10, $11, $12, $13, $14, $15, 'UK', $16, $17,
                               $18::jsonb, now(), now())
                       ON CONFLICT (id) DO UPDATE SET status = EXCLUDED.status, expiry_date = EXCLUDED.expiry_date,
                              raw_metadata = plenum_cafm.compliance_certificates.raw_metadata || EXCLUDED.raw_metadata,
                              updated_at = now()""",
                    cid, org, k["type"], k["number"], bid, b["code"], b["name"], k["issuer"], k["issue"], k["expiry"],
                    k["months"], k["inspector"], k["accreditation"], k["result"], k["status"], k["rating"], k["score"],
                    json.dumps(cmeta))
        print("\n  written")
    finally:
        await c.close()

    # The platform's own check, persisted as Verify now persists it.
    os.environ["DB_URL"] = dsn if "+asyncpg" in dsn else dsn.replace("postgresql://", "postgresql+asyncpg://")
    sys.path.insert(0, os.path.abspath(SVC))
    from src.db import AsyncSessionLocal
    from src.engines.compliance.ccc_verify import verify_stored_certificate

    for b, _bid, cid in plan:
        async with AsyncSessionLocal() as s:
            r = await verify_stored_certificate(s, cid)
            await s.commit()
        ev = r.get("evidence") or {}
        print(f"  check {b['cert']['type']} {b['cert']['number']}: verified={r.get('verified')} status={r.get('status')}"
              f" | {ev.get('certificate_kind') or ev.get('message') or ''} | valid until {ev.get('valid_until')}")


if __name__ == "__main__":
    asyncio.run(main())
