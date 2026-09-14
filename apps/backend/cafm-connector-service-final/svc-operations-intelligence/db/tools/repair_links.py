"""Repair the links the services query on, where the data itself says what the link is.

Every fix here is deterministic: a row is linked only when exactly one parent fits, and the
rule that picked it is one the code already applies elsewhere (a site with one building IS
that building; a work-order code carries its contract reference; a certificate's document
belongs to the certificate's building). Nothing is guessed, nothing is invented, and a row
with two candidates is left alone and counted.

    python db/tools/repair_links.py --db hoistra_test            # dry run — counts only
    python db/tools/repair_links.py --db plenum_agent --apply    # one transaction

Fixes, in dependency order:
  1. user_buildings rows whose user no longer exists                 → deleted
  2. vendor_wo_scores.vendor_id missing or unknown                   → the vendor of the contract the
     work-order code names (WO-<REF>-…), when that contract has one vendor
  3. work_orders.vendor_id null                                      → same rule
  4. plenum_cafm.documents rows for every ingested document that a certificate, contract or
     invoice references but the graph never registered                → inserted, placed on the
     certificate's building when every certificate on it agrees
  5. locations.building_id                                           → the building of the location's site
  6. assets.building_id                                              → via assets.site_id, else via its location
  7. work_orders.building_id                                         → via site, else location, else asset
  8. eui_snapshots / building_energy_profiles keyed on a site that is not a building, not a
     site and has no meter                                            → deleted (derived / dead)
The audit log is append-only by trigger and is reported, never touched.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys

import asyncpg

sys.stdout.reconfigure(encoding="utf-8")

ROOT_ENV = os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "..", "..", ".env")


def dsn_for(db: str) -> str:
    raw = os.environ.get("PLENUM_DB_DSN")
    if not raw and os.path.exists(ROOT_ENV):
        for line in open(ROOT_ENV, encoding="utf-8"):
            if line.startswith("PLENUM_DB_DSN="):
                raw = line.split("=", 1)[1].strip().strip('"')
    if not raw:
        raise SystemExit("PLENUM_DB_DSN not set")
    # The database name is handed to asyncpg separately: a password may contain "/" or "?".
    return re.sub(r"^postgresql\+asyncpg://", "postgresql://", raw)


# Each step: (name, count_sql, apply_sql). count_sql returns one integer — how many rows the
# apply would change. apply_sql performs it. Both use the same predicate so the dry run is
# exactly what --apply will do.
CONTRACT_VENDOR = """
    /* the one vendor a contract reference names, for references that name exactly one */
    contract_vendor AS (
        SELECT contract_ref, min(vendor_id::text) AS vendor_id
          FROM plenum_cafm.contract_sla_parameters
         WHERE contract_ref IS NOT NULL AND vendor_id IS NOT NULL
         GROUP BY contract_ref HAVING count(DISTINCT vendor_id) = 1)
"""
WO_REF = "substring({col} FROM '^WO-([A-Za-z]+-[0-9]+)-')"

STEPS = [
    ("1 user_buildings → users (dangling allocations)",
     "SELECT count(*) FROM plenum_cafm.user_buildings ub WHERE NOT EXISTS (SELECT 1 FROM plenum_cafm.users u WHERE u.id = ub.user_id)",
     "DELETE FROM plenum_cafm.user_buildings ub WHERE NOT EXISTS (SELECT 1 FROM plenum_cafm.users u WHERE u.id = ub.user_id)"),

    ("2 vendor_wo_scores.vendor_id → vendors (via the contract the WO code names)",
     f"""WITH {CONTRACT_VENDOR}
         SELECT count(*) FROM plenum_cafm.vendor_wo_scores s
           JOIN contract_vendor cv ON cv.contract_ref = {WO_REF.format(col='s.wo_code')}
           JOIN plenum_cafm.vendors v ON v.id::text = cv.vendor_id
          WHERE s.vendor_id IS NULL OR NOT EXISTS (SELECT 1 FROM plenum_cafm.vendors x WHERE x.id::text = s.vendor_id::text)""",
     f"""WITH {CONTRACT_VENDOR}
         UPDATE plenum_cafm.vendor_wo_scores s SET vendor_id = CAST(cv.vendor_id AS uuid)
           FROM contract_vendor cv JOIN plenum_cafm.vendors v ON v.id::text = cv.vendor_id
          WHERE cv.contract_ref = {WO_REF.format(col='s.wo_code')}
            AND (s.vendor_id IS NULL OR NOT EXISTS (SELECT 1 FROM plenum_cafm.vendors x WHERE x.id::text = s.vendor_id::text))"""),

    ("3 work_orders.vendor_id → vendors (via the contract the WO code names)",
     f"""WITH {CONTRACT_VENDOR}
         SELECT count(*) FROM plenum_cafm.work_orders w
           JOIN contract_vendor cv ON cv.contract_ref = {WO_REF.format(col='w.work_order_id')}
           JOIN plenum_cafm.vendors v ON v.id::text = cv.vendor_id
          WHERE w.vendor_id IS NULL""",
     f"""WITH {CONTRACT_VENDOR}
         UPDATE plenum_cafm.work_orders w SET vendor_id = CAST(cv.vendor_id AS uuid)
           FROM contract_vendor cv JOIN plenum_cafm.vendors v ON v.id::text = cv.vendor_id
          WHERE cv.contract_ref = {WO_REF.format(col='w.work_order_id')} AND w.vendor_id IS NULL"""),

    ("4 documents ← ingestion_documents referenced by certificates / contracts / invoices",
     """WITH refs AS (
            SELECT c.document_id, 'compliance_certificate' AS doc_type, min(c.certificate_type_code) AS title,
                   CASE WHEN count(DISTINCT c.building_id) = 1 THEN min(c.building_id::text)::uuid END AS building_id
              FROM plenum_cafm.compliance_certificates c WHERE c.document_id IS NOT NULL GROUP BY c.document_id
            UNION ALL
            SELECT p.document_id, 'service_contract', min(p.contract_ref), NULL FROM plenum_cafm.contract_sla_parameters p
             WHERE p.document_id IS NOT NULL GROUP BY p.document_id
            UNION ALL
            SELECT iv.document_id, 'invoice', NULL, NULL FROM plenum_cafm.invoice_verifications iv
             WHERE iv.document_id IS NOT NULL GROUP BY iv.document_id)
        SELECT count(DISTINCT r.document_id) FROM refs r
          JOIN plenum_cafm.ingestion_documents i ON i.id = r.document_id
         WHERE NOT EXISTS (SELECT 1 FROM plenum_cafm.documents d WHERE d.document_id = r.document_id)""",
     """WITH refs AS (
            SELECT c.document_id, 'compliance_certificate' AS doc_type, min(c.certificate_type_code) AS title,
                   CASE WHEN count(DISTINCT c.building_id) = 1 THEN min(c.building_id::text)::uuid END AS building_id
              FROM plenum_cafm.compliance_certificates c WHERE c.document_id IS NOT NULL GROUP BY c.document_id
            UNION ALL
            SELECT p.document_id, 'service_contract', min(p.contract_ref), NULL FROM plenum_cafm.contract_sla_parameters p
             WHERE p.document_id IS NOT NULL GROUP BY p.document_id
            UNION ALL
            SELECT iv.document_id, 'invoice', NULL, NULL FROM plenum_cafm.invoice_verifications iv
             WHERE iv.document_id IS NOT NULL GROUP BY iv.document_id),
        one AS (SELECT DISTINCT ON (document_id) * FROM refs ORDER BY document_id, doc_type)
        INSERT INTO plenum_cafm.documents (document_id, building_id, doc_type, title, file_name, blob_url, uploaded_at)
        SELECT r.document_id, r.building_id, r.doc_type, coalesce(r.title, i.original_filename), i.original_filename, i.blob_url,
               coalesce(i.uploaded_at, now())
          FROM one r JOIN plenum_cafm.ingestion_documents i ON i.id = r.document_id
         WHERE NOT EXISTS (SELECT 1 FROM plenum_cafm.documents d WHERE d.document_id = r.document_id)"""),

    ("5 locations.building_id ← the building of the location's site",
     """SELECT count(*) FROM plenum_cafm.locations l
         WHERE l.building_id IS NULL AND l.site_id IS NOT NULL
           AND (SELECT count(*) FROM plenum_cafm.buildings b WHERE b.site_id = l.site_id) = 1""",
     """UPDATE plenum_cafm.locations l SET building_id = (SELECT b.building_id FROM plenum_cafm.buildings b WHERE b.site_id = l.site_id)
         WHERE l.building_id IS NULL AND l.site_id IS NOT NULL
           AND (SELECT count(*) FROM plenum_cafm.buildings b WHERE b.site_id = l.site_id) = 1"""),

    ("6 assets.building_id ← via site, else via location",
     """SELECT count(*) FROM plenum_cafm.assets a
         WHERE a.building_id IS NULL AND coalesce(
           (SELECT b.building_id FROM plenum_cafm.buildings b WHERE b.site_id = a.site_id::text
              AND (SELECT count(*) FROM plenum_cafm.buildings x WHERE x.site_id = a.site_id::text) = 1),
           (SELECT l.building_id FROM plenum_cafm.locations l WHERE l.id::text = a.location_id::text)) IS NOT NULL""",
     """UPDATE plenum_cafm.assets a SET building_id = coalesce(
           (SELECT b.building_id FROM plenum_cafm.buildings b WHERE b.site_id = a.site_id::text
              AND (SELECT count(*) FROM plenum_cafm.buildings x WHERE x.site_id = a.site_id::text) = 1),
           (SELECT l.building_id FROM plenum_cafm.locations l WHERE l.id::text = a.location_id::text))
         WHERE a.building_id IS NULL AND coalesce(
           (SELECT b.building_id FROM plenum_cafm.buildings b WHERE b.site_id = a.site_id::text
              AND (SELECT count(*) FROM plenum_cafm.buildings x WHERE x.site_id = a.site_id::text) = 1),
           (SELECT l.building_id FROM plenum_cafm.locations l WHERE l.id::text = a.location_id::text)) IS NOT NULL"""),

    ("7 work_orders.building_id ← via site, else location, else asset",
     """SELECT count(*) FROM plenum_cafm.work_orders w
         WHERE w.building_id IS NULL AND coalesce(
           (SELECT b.building_id FROM plenum_cafm.buildings b WHERE b.site_id = w.site_id::text
              AND (SELECT count(*) FROM plenum_cafm.buildings x WHERE x.site_id = w.site_id::text) = 1),
           (SELECT l.building_id FROM plenum_cafm.locations l WHERE l.id::text = w.location_id::text),
           (SELECT a.building_id FROM plenum_cafm.assets a WHERE a.id::text = w.asset_id::text)) IS NOT NULL""",
     """UPDATE plenum_cafm.work_orders w SET building_id = coalesce(
           (SELECT b.building_id FROM plenum_cafm.buildings b WHERE b.site_id = w.site_id::text
              AND (SELECT count(*) FROM plenum_cafm.buildings x WHERE x.site_id = w.site_id::text) = 1),
           (SELECT l.building_id FROM plenum_cafm.locations l WHERE l.id::text = w.location_id::text),
           (SELECT a.building_id FROM plenum_cafm.assets a WHERE a.id::text = w.asset_id::text))
         WHERE w.building_id IS NULL AND coalesce(
           (SELECT b.building_id FROM plenum_cafm.buildings b WHERE b.site_id = w.site_id::text
              AND (SELECT count(*) FROM plenum_cafm.buildings x WHERE x.site_id = w.site_id::text) = 1),
           (SELECT l.building_id FROM plenum_cafm.locations l WHERE l.id::text = w.location_id::text),
           (SELECT a.building_id FROM plenum_cafm.assets a WHERE a.id::text = w.asset_id::text)) IS NOT NULL"""),

    ("8a eui_snapshots on a site that is no building, no site, no meter (dead)",
     """SELECT count(*) FROM plenum_cafm.eui_snapshots e
         WHERE NOT EXISTS (SELECT 1 FROM plenum_cafm.buildings b WHERE b.building_id = e.site_id)
           AND NOT EXISTS (SELECT 1 FROM plenum_cafm.sites s WHERE s.id::text = e.site_id::text)
           AND NOT EXISTS (SELECT 1 FROM plenum_cafm.energy_meters m WHERE m.site_id = e.site_id)""",
     """DELETE FROM plenum_cafm.eui_snapshots e
         WHERE NOT EXISTS (SELECT 1 FROM plenum_cafm.buildings b WHERE b.building_id = e.site_id)
           AND NOT EXISTS (SELECT 1 FROM plenum_cafm.sites s WHERE s.id::text = e.site_id::text)
           AND NOT EXISTS (SELECT 1 FROM plenum_cafm.energy_meters m WHERE m.site_id = e.site_id)"""),

    ("8b building_energy_profiles on a site that is no building, no site, no meter (dead)",
     """SELECT count(*) FROM plenum_cafm.building_energy_profiles p
         WHERE NOT EXISTS (SELECT 1 FROM plenum_cafm.buildings b WHERE b.building_id = p.site_id)
           AND NOT EXISTS (SELECT 1 FROM plenum_cafm.sites s WHERE s.id::text = p.site_id::text)
           AND NOT EXISTS (SELECT 1 FROM plenum_cafm.energy_meters m WHERE m.site_id = p.site_id)""",
     """DELETE FROM plenum_cafm.building_energy_profiles p
         WHERE NOT EXISTS (SELECT 1 FROM plenum_cafm.buildings b WHERE b.building_id = p.site_id)
           AND NOT EXISTS (SELECT 1 FROM plenum_cafm.sites s WHERE s.id::text = p.site_id::text)
           AND NOT EXISTS (SELECT 1 FROM plenum_cafm.energy_meters m WHERE m.site_id = p.site_id)"""),
]

REPORT_ONLY = [
    ("audit rows naming an organization that does not exist (append-only; reported, not changed)",
     "SELECT count(*) FROM plenum_cafm.ops_audit_log a WHERE a.organization_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM plenum_cafm.organizations o WHERE o.id::text = a.organization_id::text)"),
    ("work orders still without a building after step 7 (no site, location or placed asset)",
     "SELECT count(*) FROM plenum_cafm.work_orders WHERE building_id IS NULL"),
    ("assets still without a building after step 6",
     "SELECT count(*) FROM plenum_cafm.assets WHERE building_id IS NULL"),
    ("vendor_wo_scores whose wo_code names no work order (cannot be linked)",
     "SELECT count(*) FROM plenum_cafm.vendor_wo_scores s WHERE s.wo_code IS NOT NULL AND NOT EXISTS (SELECT 1 FROM plenum_cafm.work_orders w WHERE w.work_order_id = s.wo_code)"),
]


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    conn = await asyncpg.connect(dsn_for(args.db), database=args.db, timeout=30)
    print(f"== {args.db} — {'APPLY' if args.apply else 'dry run'}")
    total = 0
    tx = conn.transaction()
    await tx.start()
    try:
        for name, count_sql, apply_sql in STEPS:
            # Each step in its own savepoint: a column one database lacks skips that step, not
            # every step after it (a failed statement otherwise aborts the whole transaction).
            sp = conn.transaction()
            await sp.start()
            try:
                n = await conn.fetchval(count_sql)
                if args.apply and n:
                    status = await conn.execute(apply_sql)
                    print(f"  {name}: {n} → {status}")
                else:
                    print(f"  {name}: {n}")
                await sp.commit()
                total += int(n or 0)
            except Exception as exc:  # noqa: BLE001 — a table this database lacks is a fact, not a failure
                await sp.rollback()
                print(f"  {name}: skipped ({str(exc).splitlines()[0][:90]})")
        print("  --")
        for name, sql in REPORT_ONLY:
            sp = conn.transaction()
            await sp.start()
            try:
                print(f"  {name}: {await conn.fetchval(sql)}")
                await sp.commit()
            except Exception as exc:  # noqa: BLE001
                await sp.rollback()
                print(f"  {name}: skipped ({str(exc).splitlines()[0][:90]})")
        if args.apply:
            await tx.commit()
            print(f"  committed — {total} rows changed")
        else:
            await tx.rollback()
            print(f"  dry run — {total} rows would change; re-run with --apply")
    except Exception:
        await tx.rollback()
        raise
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
