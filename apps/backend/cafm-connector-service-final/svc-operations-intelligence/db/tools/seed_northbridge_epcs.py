"""The EPCs behind the ratings and duties band: MEES now, MEES 2030, EPCs on file.

Those three tiles read one thing — the most recent EPC per building in
plenum_cafm.compliance_certificates, scoped to the building, with a band on it. With none on
file every tile reads zero, which is indistinguishable from a portfolio that is fully
compliant. "0 below EPC E" and "0 with an EPC on file" are the same number saying opposite
things, and only the second half of the sentence tells you which.

So the bands here are chosen to make the distinction visible rather than to make the page
green:

    Harbour Point   band F   below E, so enforceable TODAY. Also below B and over 1,000 m²,
                             so it counts against the 2030 proposal as well. Its certificate
                             expires inside the year, which is the third tile's whole point.
    Ashgrove Court  band C   lawful to let now, and still below B, so 2030 catches it too.

A page where every duty reads zero teaches nobody what the duty is.

MEES is the Minimum Energy Efficiency Standard: E is enforceable now for lettable
non-domestic property in England and Wales, and B is the 2030 proposal. Both thresholds and
the 1,000 m² qualifier live in engines/compliance/epc_rating.py, not here.

Every number in this file is invented. The reference is RRN-shaped so the register link is
exercised, but it resolves to nothing on the real GOV.UK service — which is correct for a
test database, and why the demo's verification step uses genuine certificates from
production instead.

Defaults to a dry run. Refuses to touch plenum_agent.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
import uuid
from datetime import date, timedelta

import asyncpg

sys.stdout.reconfigure(encoding="utf-8")

ROOT_ENV = r"C:\CMMS\Hoistra\.env"
NS = uuid.UUID("6f1d3a52-9c47-4f8e-9b2a-7d5e1c084a33")
PRODUCTION = "plenum_agent"

#: building_code -> (band, score, issued years ago, valid for years, assessor)
#:
#: An EPC runs ten years. Harbour Point's was issued nine years and four months ago, so it
#: expires in about eight months: on file, still current, and inside the twelve-month window.
EPCS = {
    "B-101": ("F", 128, 9.35, 10.0, "Calderbank Energy Assessors Ltd"),
    "B-102": ("C", 68, 2.5, 10.0, "Calderbank Energy Assessors Ltd"),
}


def dsn_for() -> str:
    raw = os.environ.get("PLENUM_DB_DSN")
    if not raw and os.path.exists(ROOT_ENV):
        for line in open(ROOT_ENV, encoding="utf-8"):
            if line.startswith("PLENUM_DB_DSN="):
                raw = line.split("=", 1)[1].strip().strip('"')
    if not raw:
        raise SystemExit("PLENUM_DB_DSN not set")
    return re.sub(r"^postgresql\+asyncpg://", "postgresql://", raw)


def rrn_for(code: str) -> str:
    """A lodgement reference of the right shape: five groups of four digits.

    Shape matters because it is what the register link is built from. These digits are
    invented and resolve to nothing on the real service.
    """
    digits = f"{abs(hash(code)) % 10**20:020d}"
    return "-".join(digits[i:i + 4] for i in range(0, 20, 4))


async def run(db: str, apply: bool) -> None:
    if db == PRODUCTION:
        raise SystemExit(
            f"refusing to write to {PRODUCTION}. These are invented certificates with "
            f"invented lodgement references; they belong in a test database."
        )
    c = await asyncpg.connect(dsn_for(), database=db, timeout=60)
    print(f"\n################ {db} ({'APPLY' if apply else 'dry run'}) ################\n")

    buildings = await c.fetch("""
        SELECT building_id::text AS id, building_code, name, organization_id,
               round(coalesce(gross_area_sqft, 0) * 0.092903) AS gia_m2
          FROM plenum_cafm.buildings
         WHERE building_code = ANY($1::text[]) ORDER BY building_code""", list(EPCS))
    if not buildings:
        raise SystemExit(f"none of {sorted(EPCS)} are in {db}")

    today = date.today()
    written = 0
    for b in buildings:
        band, score, issued_yrs, life_yrs, assessor = EPCS[b["building_code"]]
        issue = today - timedelta(days=int(issued_yrs * 365.25))
        expiry = issue + timedelta(days=int(life_yrs * 365.25))
        cid = uuid.uuid5(NS, f"epc:{b['building_code']}")
        months_left = round((expiry - today).days / 30.44, 1)

        duties = []
        if band > "E":
            duties.append("below E — unlawful to let now")
        if band > "B" and float(b["gia_m2"] or 0) > 1000:
            duties.append("below B and over 1,000 m² — caught by the 2030 proposal")
        if 0 < (expiry - today).days <= 365:
            duties.append("expires inside 12 months")
        if not duties:
            duties.append("no duty outstanding")

        print(f"  {b['building_code']} {b['name']:18} band {band} · score {score} · "
              f"{b['gia_m2']} m²")
        print(f"      issued {issue}   expires {expiry}  ({months_left} months left)")
        for d in duties:
            print(f"      - {d}")

        if apply:
            await c.execute("""
                INSERT INTO plenum_cafm.compliance_certificates
                    (id, organization_id, building_id, building_name, cert_scope,
                     certificate_type_code, certificate_number, energy_rating, energy_score,
                     issuer, issue_date, expiry_date, status)
                VALUES ($1, $2, $3::uuid, $4, 'Building', 'EPC', $5, $6, $7, $8, $9, $10,
                        'valid')
                ON CONFLICT (id) DO UPDATE
                   SET energy_rating = EXCLUDED.energy_rating,
                       energy_score = EXCLUDED.energy_score,
                       issue_date = EXCLUDED.issue_date,
                       expiry_date = EXCLUDED.expiry_date,
                       certificate_number = EXCLUDED.certificate_number,
                       status = 'valid'""",
                cid, b["organization_id"], b["id"], b["name"],
                rrn_for(b["building_code"]), band, score, assessor, issue, expiry)
        written += 1

    print(f"\n  {written} EPC(s) {'written' if apply else 'would be written'}")
    if apply:
        after = await c.fetchrow("""
            SELECT count(*) FILTER (WHERE energy_rating > 'E')            AS below_e,
                   count(*) FILTER (WHERE energy_rating > 'B')            AS below_b,
                   count(*) FILTER (WHERE expiry_date >= current_date)    AS current,
                   count(*) FILTER (WHERE expiry_date >= current_date
                                      AND expiry_date < current_date + 365) AS expiring,
                   count(*) AS total
              FROM plenum_cafm.compliance_certificates
             WHERE cert_scope = 'Building' AND certificate_type_code = 'EPC'
               AND building_id IS NOT NULL""")
        print(f"\n  the tiles should now read:")
        print(f"    MEES enforceable now   {after['below_e']}   "
              f"below EPC E · {after['total']} with an EPC on file")
        print(f"    MEES proposed 2030     {after['below_b']}   below EPC B · over 1,000 m²")
        print(f"    EPCs on file           {after['current']} / {after['total']}   "
              f"{after['expiring']} expiring inside 12 months")
    else:
        print("  dry run. Nothing was written. Add --apply.")
    await c.close()


async def main() -> None:
    ap = argparse.ArgumentParser(description="Seed the Northbridge EPCs.")
    ap.add_argument("--db", required=True, help="database name, e.g. hoistra_test")
    ap.add_argument("--apply", action="store_true", help="write; otherwise report only")
    args = ap.parse_args()
    await run(args.db, args.apply)


if __name__ == "__main__":
    asyncio.run(main())
