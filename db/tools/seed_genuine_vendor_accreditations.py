"""Four real firms and their genuine accreditations, as vendors of the test company.

Every Bishopsgate vendor is invented and none of its numbers is on a register. These four are
real: each accreditation is copied from its holder's own certificate (held in plenum_agent) and
filed under that holder - never under an invented vendor:

  C&H Fire Protection Ltd, Basildon      BAFE SP101 104293, BAFE SP105 104021 (BAFE company 303101)
  ProudCastle Solutions Ltd, Ibstock     BAFE SP203-1 101599 (NSI00561) - expired 3 Jan 2026 on the
                                         certificate on file; no renewal on file
  AIB Solutions Limited, Newcastle ST5   HSE asbestos licence 052305041 (standard) to 30 Dec 2026
  Eco Star C G Construction LLC, WA      Washington contractor licence ECOSTSC758NN to 15 Aug 2027

The checks that verify them read plenum_cafm.compliance_verification_register_rows - public
register data (the BAFE lists, the HSE licensed-contractor list via UKATA, Washington's
data.wa.gov contractor licences). hoistra_test had none of it, so this copies that table from
plenum_agent (read only there), then writes the vendors and certificates unverified and runs the
platform's own check (ccc_verify.verify_stored_certificate) on each. What it cannot check -
BAFE's website-only register for 101599 - it leaves at "check on the register".

These are registrations of the FIRM, not documents: a pass says the company holds the
registration, which is all those registers can say. Keyed by uuid5; a second run changes nothing
but the checks' timestamps. Dry run by default; --apply writes. hoistra_test only.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import uuid
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
SVC = os.path.join(HERE, "..", "..", "apps", "backend", "cafm-connector-service-final", "svc-operations-intelligence")
NS = uuid.UUID("7c1d2e8a-43b6-4f0e-9a5d-2b8e61f04c77")
TODAY = date(2026, 9, 29)

#: (code, name, trade, address, city, postcode, country, accreditation line) - addresses as the
#: registers publish them.
VENDORS = [
    ("CHFP", "C&H Fire Protection Ltd", "Fire", None, "Basildon", None, "United Kingdom",
     "BAFE SP101 / SP105 (BAFE 303101)"),
    ("PCSL", "ProudCastle Solutions Ltd", "Fire", None, "Ibstock", None, "United Kingdom",
     "BAFE SP203-1 101599 (NSI00561)"),
    ("AIBS", "AIB Solutions Limited", "Asbestos", "Unit 804, Centre 500, Lowfield Drive", "Newcastle, Staffs", "ST5 0UU",
     "United Kingdom", "HSE asbestos licence 052305041"),
    ("ECSC", "Eco Star C G Construction LLC", "General contractor", None, "Vancouver, WA", None, "United States",
     "WA contractor licence ECOSTSC758NN"),
]
#: (vendor code, type code, certificate number, accreditation number the register lists, issuer,
#:  issue, expiry, source document)
CERTS = [
    ("CHFP", "BAFE_SP101", "104293", "303101", "BAFE (British Approvals for Fire Equipment)",
     date(2025, 4, 7), date(2027, 10, 26), "BAFE_SP101.pdf"),
    ("CHFP", "BAFE_SP105", "104021", "303101", "BAFE (British Approvals for Fire Equipment)",
     date(2024, 1, 4), date(2027, 1, 3), "BAFE_SP105.pdf"),
    ("PCSL", "BAFE_SP203_1", "101599", "NSI00561", "BAFE (British Approvals for Fire Equipment)",
     date(2022, 12, 22), date(2026, 1, 3), "Bafe - Proudcastle Solutions Ltd.pdf"),
    ("AIBS", "HSE_ASBESTOS_LICENCE", "052305041", None, "Health and Safety Executive (HSE)",
     date(2023, 12, 31), date(2026, 12, 30), "Asbestos_HSE_Licence_2023-26.pdf"),
    ("ECSC", "US_STATE_CONTRACTOR_LICENSE", "ECOSTSC758NN", None, "Washington State Department of Labor & Industries",
     None, date(2027, 8, 15), "data.wa.gov contractor licence"),
]


def status_of(expiry: date | None) -> str:
    if expiry is None:
        return "Current"
    if expiry < TODAY:
        return "Lapsed"
    return "Expiring Soon" if (expiry - TODAY).days <= 90 else "Current"


async def main() -> None:
    from _env import hoistra_test_dsn
    import asyncpg

    ap = argparse.ArgumentParser()
    ap.add_argument("--org-name", default="Plenum Technologies")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    dsn = hoistra_test_dsn()
    base = dsn.replace("postgresql+asyncpg", "postgresql")
    c = await asyncpg.connect(base, timeout=15)
    src = await asyncpg.connect(re.sub(r"/hoistra_test(\?|$)", r"/plenum_agent\1", base), timeout=15)
    try:
        if await c.fetchval("SELECT current_database()") != "hoistra_test":
            raise SystemExit("refusing: hoistra_test only")
        if await src.fetchval("SELECT current_database()") != "plenum_agent":
            raise SystemExit("could not reach plenum_agent for the register data")
        org = await c.fetchval("SELECT id FROM plenum_cafm.organizations WHERE lower(name) = lower($1)", args.org_name)
        if not org:
            raise SystemExit(f"no organization named {args.org_name!r}")
        async with src.transaction(readonly=True):
            reg = await src.fetch("""SELECT id, certificate_type_code, lookup_key, payload::text, source_file, ingested_at
                                       FROM plenum_cafm.compliance_verification_register_rows""")
        have = await c.fetchval("SELECT count(*) FROM plenum_cafm.compliance_verification_register_rows")
        print(f"  register data: {len(reg)} public register rows in plenum_agent, {have} in hoistra_test")
        for v in VENDORS:
            print(f"  vendor {v[0]} {v[1]} ({v[4]})")
        for k in CERTS:
            print(f"    {k[1]} {k[2]} (register key {k[3] or k[2]}) - {status_of(k[6]).lower()}, expires {k[6]}")
        if not args.apply:
            print("\n  dry run - pass --apply to write, then run the platform's register checks")
            return
        async with c.transaction():
            await c.executemany(
                """INSERT INTO plenum_cafm.compliance_verification_register_rows
                          (id, certificate_type_code, lookup_key, payload, source_file, ingested_at)
                   VALUES ($1, $2, $3, $4::jsonb, $5, $6) ON CONFLICT (id) DO NOTHING""",
                [tuple(r) for r in reg])
            vid = {}
            for code, name, trade, addr, city, pc, country, acc in VENDORS:
                vid[code] = uuid.uuid5(NS, f"vendor:{org}:{code}")
                await c.execute(
                    """INSERT INTO plenum_cafm.vendors (id, organization_id, vendor_name, vendor_code, address, city, postal_code,
                              country, notes, status, trade, accreditation, block_state, created_at, updated_at)
                       VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, 'active', $10, $11, 'Clear', now(), now())
                       ON CONFLICT (id) DO UPDATE SET vendor_name = EXCLUDED.vendor_name, accreditation = EXCLUDED.accreditation,
                              updated_at = now()""",
                    vid[code], org, name, code, addr, city, pc, country,
                    "Real firm - genuine registration copied from its own certificate, for register-check testing. "
                    "Not a supplier to any test building.", trade, acc)
            cids = []
            for vcode, typ, num, acc, issuer, issue, expiry, doc in CERTS:
                cid = uuid.uuid5(NS, f"certificate:{org}:{typ}:{num}")
                cids.append((cid, vcode, typ, num))
                name = next(v[1] for v in VENDORS if v[0] == vcode)
                meta = {"source": "genuine registration, copied from the holder's own certificate (plenum_agent)",
                        "source_document": doc, "vendor_name": name, "company_name": name}
                await c.execute(
                    """INSERT INTO plenum_cafm.compliance_certificates (id, organization_id, certificate_type_code, certificate_number,
                              cert_scope, vendor_id, vendor_name, vendor_code, issuer, issue_date, expiry_date, next_due_date,
                              inspector_accreditation_number, status, country_code, raw_metadata, created_at, updated_at)
                       VALUES ($1, $2, $3, $4, 'Vendor', $5, $6, $7, $8, $9, $10, $10, $11, $12, $13, $14::jsonb, now(), now())
                       ON CONFLICT (id) DO UPDATE SET status = EXCLUDED.status,
                              raw_metadata = plenum_cafm.compliance_certificates.raw_metadata || EXCLUDED.raw_metadata,
                              updated_at = now()""",
                    cid, org, typ, num, vid[vcode], name, vcode, issuer, issue, expiry, acc, status_of(expiry),
                    "US" if vcode == "ECSC" else "UK", json.dumps(meta))
        now_have = await c.fetchval("SELECT count(*) FROM plenum_cafm.compliance_verification_register_rows")
        print(f"\n  written - register data in hoistra_test: {now_have} rows")
    finally:
        await c.close()
        await src.close()

    os.environ["DB_URL"] = dsn if "+asyncpg" in dsn else dsn.replace("postgresql://", "postgresql+asyncpg://")
    sys.path.insert(0, os.path.abspath(SVC))
    from src.db import AsyncSessionLocal
    from src.engines.compliance.ccc_verify import verify_stored_certificate

    for cid, vcode, typ, num in cids:
        async with AsyncSessionLocal() as s:
            r = await verify_stored_certificate(s, cid)
            await s.commit()
        ev = r.get("evidence") or {}
        print(f"  check {vcode} {typ} {num}: status={r.get('status')} verified={r.get('verified')} channel={r.get('channel')}"
              f" | {ev.get('company_name') or ev.get('message') or ''}")


if __name__ == "__main__":
    asyncio.run(main())
