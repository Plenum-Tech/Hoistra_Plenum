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
  9. plenum_cafm.documents rows that no file was ever uploaded under and that nothing
     references                                                       → removed (orphaned graph mints)
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

# ── Orphaned document rows ───────────────────────────────────────────────────
# A row in plenum_cafm.documents exists so a certificate, contract or invoice can reach a
# building through it. Engines minted one whenever they had no id to reuse, and an engine
# that ran twice — an invoice re-verified, a certificate re-filed — minted another. The
# mint is fixed at each site now; these are the rows the old behaviour left behind.
#
# A row qualifies only when BOTH of these hold, and both are facts rather than heuristics:
#   * no ingestion_documents row carries its id, so GET /api/documents/{id}/download
#     answers 404 for it and always will — there is no stored file and no extracted text;
#   * nothing points at it, including the graph_document_id a certificate keeps inside its
#     raw_metadata, which is a reference the schema does not declare as a column.
# Such a row can serve nothing and is reachable from nothing. All it does is count as a
# file on a building's Documents panel — seven of them for one invoice, on Harbour Point.
#
# The referencing columns are read from information_schema rather than listed blind, so a
# database missing one of them narrows the rule instead of skipping the step or, far worse,
# clearing rows that column still points at.
_DOCUMENT_REFERENCES: tuple[tuple[str, str], ...] = (
    ("compliance_certificates", "document_id"),
    ("compliance_certificates", "source_document_id"),
    ("compliance_vector_membership_audit", "document_id"),
    ("contract_documents", "document_id"),
    ("contract_sla_parameters", "document_id"),
    ("invoice_verifications", "document_id"),
    ("ppm_visits", "source_document_id"),
)


async def orphan_document_step(conn) -> tuple[str, str, str] | None:
    """Step 9, built against this database's own columns. None where documents is absent."""
    rows = await conn.fetch(
        """SELECT table_name, column_name FROM information_schema.columns
            WHERE table_schema = 'plenum_cafm'"""
    )
    have = {(str(r["table_name"]), str(r["column_name"])) for r in rows}
    if ("documents", "document_id") not in have:
        return None
    where = [
        "NOT EXISTS (SELECT 1 FROM plenum_cafm.ingestion_documents i"
        " WHERE i.id = d.document_id)"
    ]
    for table, column in _DOCUMENT_REFERENCES:
        if (table, column) in have:
            where.append(
                f"NOT EXISTS (SELECT 1 FROM plenum_cafm.{table} r"
                f" WHERE r.{column} = d.document_id)"
            )
    if ("compliance_certificates", "raw_metadata") in have:
        # Not a column, so no column check finds it: the certificate engine records the
        # graph row it reused under this key. A row named here is in use.
        where.append(
            "NOT EXISTS (SELECT 1 FROM plenum_cafm.compliance_certificates c"
            " WHERE c.raw_metadata->>'graph_document_id' = d.document_id::text)"
        )
    clause = "\n           AND ".join(where)
    return (
        "9 plenum_cafm.documents rows with no uploaded file and no reference (orphaned mints)",
        f"SELECT count(*) FROM plenum_cafm.documents d WHERE {clause}",
        f"DELETE FROM plenum_cafm.documents d WHERE {clause}",
    )


# ── Synthetic placement ──────────────────────────────────────────────────────
# The rows below carry nothing that names a building or a vendor — no site, no location, no
# placed asset — and the owner has confirmed the whole dataset is synthetic. They are placed by
# a STABLE spread: md5 of the row's own key modulo the number of candidate buildings/vendors,
# so the same row lands on the same building every run, every database, and a re-run changes
# nothing. Candidates are the organisation's named UK buildings (the MixedUse triplicates are
# excluded) and its uuid-keyed vendors. This is a test-data rule, and it is labelled as one.
UK_BUILDINGS = """
    uk AS (
        SELECT b.building_id, b.organization_id,
               ROW_NUMBER() OVER (PARTITION BY b.organization_id ORDER BY b.name, b.building_id) - 1 AS slot,
               COUNT(*) OVER (PARTITION BY b.organization_id) AS n
          FROM plenum_cafm.buildings b
          LEFT JOIN plenum_cafm.sites s ON s.id::text = b.site_id::text OR s.site_id::text = b.site_id::text
         WHERE coalesce(s.country_code, b.raw_metadata->>'country_code') IN ('UK', 'GB')
           AND b.name NOT LIKE 'MixedUse%')
"""
UUID_VENDORS = """
    vend AS (
        SELECT v.id::text AS vendor_id, v.organization_id,
               ROW_NUMBER() OVER (PARTITION BY v.organization_id ORDER BY v.vendor_name, v.id::text) - 1 AS slot,
               COUNT(*) OVER (PARTITION BY v.organization_id) AS n
          FROM plenum_cafm.vendors v
         WHERE v.id::text ~ '^[0-9a-f]{8}-[0-9a-f]{4}-')
"""


def _spread(key_expr: str) -> str:
    """A stable 0..2^31 integer from a row key — the same key always gives the same slot."""
    return f"(('x' || substr(md5({key_expr}), 1, 8))::bit(32)::int & 2147483647)"


SYNTHETIC_STEPS = [
    ("9 [synthetic] assets.building_id ← stable spread over the organisation's UK buildings",
     f"""WITH {UK_BUILDINGS}
         SELECT count(*) FROM plenum_cafm.assets a
          WHERE a.building_id IS NULL
            AND EXISTS (SELECT 1 FROM uk WHERE uk.organization_id::text = a.organization_id::text OR a.organization_id IS NULL)""",
     f"""WITH {UK_BUILDINGS}
         UPDATE plenum_cafm.assets a SET building_id = uk.building_id
           FROM uk
          WHERE a.building_id IS NULL
            AND (uk.organization_id::text = a.organization_id::text
                 OR (a.organization_id IS NULL AND uk.organization_id = (SELECT organization_id FROM uk GROUP BY 1 ORDER BY count(*) DESC LIMIT 1)))
            AND uk.slot = {_spread('a.id::text')} % uk.n"""),

    ("10 [synthetic] work_orders.building_id ← its asset's building, else a stable spread over UK buildings",
     f"""WITH {UK_BUILDINGS}
         SELECT count(*) FROM plenum_cafm.work_orders w
          WHERE w.building_id IS NULL
            AND (EXISTS (SELECT 1 FROM plenum_cafm.assets a WHERE a.id::text = w.asset_id::text AND a.building_id IS NOT NULL)
                 OR EXISTS (SELECT 1 FROM uk WHERE uk.organization_id::text = w.organization_id::text OR w.organization_id IS NULL))""",
     f"""WITH {UK_BUILDINGS},
         target AS (
            SELECT w.id, coalesce(
                (SELECT a.building_id FROM plenum_cafm.assets a WHERE a.id::text = w.asset_id::text),
                (SELECT uk.building_id FROM uk
                  WHERE (uk.organization_id::text = w.organization_id::text
                         OR (w.organization_id IS NULL AND uk.organization_id = (SELECT organization_id FROM uk GROUP BY 1 ORDER BY count(*) DESC LIMIT 1)))
                    AND uk.slot = {_spread('w.id::text')} % uk.n LIMIT 1)) AS building_id
              FROM plenum_cafm.work_orders w WHERE w.building_id IS NULL)
         UPDATE plenum_cafm.work_orders w SET building_id = t.building_id
           FROM target t WHERE t.id = w.id AND t.building_id IS NOT NULL"""),

    ("11 [synthetic] work_orders.vendor_id ← stable spread over the organisation's uuid-keyed vendors",
     f"""WITH {UUID_VENDORS}
         SELECT count(*) FROM plenum_cafm.work_orders w
          WHERE w.vendor_id IS NULL
            AND EXISTS (SELECT 1 FROM vend WHERE vend.organization_id::text = w.organization_id::text OR w.organization_id IS NULL)""",
     f"""WITH {UUID_VENDORS}
         UPDATE plenum_cafm.work_orders w SET vendor_id = CAST(vend.vendor_id AS uuid)
           FROM vend
          WHERE w.vendor_id IS NULL
            AND (vend.organization_id::text = w.organization_id::text
                 OR (w.organization_id IS NULL AND vend.organization_id = (SELECT organization_id FROM vend GROUP BY 1 ORDER BY count(*) DESC LIMIT 1)))
            AND vend.slot = {_spread('w.id::text')} % vend.n"""),

    ("12 [synthetic] vendor_wo_scores.work_order_id that can never resolve by type → cleared (wo_code is the key)",
     """SELECT count(*) FROM plenum_cafm.vendor_wo_scores s
         WHERE s.work_order_id IS NOT NULL
           AND NOT EXISTS (SELECT 1 FROM plenum_cafm.work_orders w WHERE w.id::text = s.work_order_id::text)
           AND EXISTS (SELECT 1 FROM plenum_cafm.work_orders w WHERE w.work_order_id = s.wo_code)""",
     """UPDATE plenum_cafm.vendor_wo_scores s SET work_order_id = NULL
         WHERE s.work_order_id IS NOT NULL
           AND NOT EXISTS (SELECT 1 FROM plenum_cafm.work_orders w WHERE w.id::text = s.work_order_id::text)
           AND EXISTS (SELECT 1 FROM plenum_cafm.work_orders w WHERE w.work_order_id = s.wo_code)"""),

    ("13 [synthetic] organizations ← a retired row for every tenant the append-only audit log still names",
     """SELECT count(DISTINCT a.organization_id) FROM plenum_cafm.ops_audit_log a
         WHERE a.organization_id IS NOT NULL
           AND NOT EXISTS (SELECT 1 FROM plenum_cafm.organizations o WHERE o.id::text = a.organization_id::text)""",
     """INSERT INTO plenum_cafm.organizations (id, name, status)
        SELECT DISTINCT a.organization_id, 'Retired scratch tenant ' || right(a.organization_id::text, 4), 'inactive'
          FROM plenum_cafm.ops_audit_log a
         WHERE a.organization_id IS NOT NULL
           AND NOT EXISTS (SELECT 1 FROM plenum_cafm.organizations o WHERE o.id::text = a.organization_id::text)"""),

    # The same insert for a deployment whose organizations.code is NOT NULL with no default
    # (production). Step 13 fails there inside its savepoint and this one runs; on a
    # deployment without a code column this one is the step that skips.
    ("13b [synthetic] organizations ← the same retired row, with the code column production requires",
     """SELECT count(DISTINCT a.organization_id) FROM plenum_cafm.ops_audit_log a
         WHERE a.organization_id IS NOT NULL
           AND NOT EXISTS (SELECT 1 FROM plenum_cafm.organizations o WHERE o.id::text = a.organization_id::text)""",
     """INSERT INTO plenum_cafm.organizations (id, name, code, status)
        SELECT DISTINCT a.organization_id, 'Retired scratch tenant ' || right(a.organization_id::text, 4),
               'RETIRED-' || upper(right(a.organization_id::text, 4)), 'inactive'
          FROM plenum_cafm.ops_audit_log a
         WHERE a.organization_id IS NOT NULL
           AND NOT EXISTS (SELECT 1 FROM plenum_cafm.organizations o WHERE o.id::text = a.organization_id::text)"""),
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
    ap.add_argument("--synthetic", action="store_true",
                    help="also run the stable-spread placement steps (test data only — see SYNTHETIC_STEPS)")
    args = ap.parse_args()
    steps = STEPS + (SYNTHETIC_STEPS if args.synthetic else [])
    conn = await asyncpg.connect(dsn_for(args.db), database=args.db, timeout=30)
    # Built from information_schema, so it can only be appended once the connection exists.
    # Placed ahead of the synthetic steps: those put rows on buildings, this removes rows no
    # building, engine or download route can reach.
    orphans = await orphan_document_step(conn)
    if orphans:
        steps = steps[: len(STEPS)] + [orphans] + steps[len(STEPS):]
    print(f"== {args.db} — {'APPLY' if args.apply else 'dry run'}")
    total = 0
    tx = conn.transaction()
    await tx.start()
    try:
        for name, count_sql, apply_sql in steps:
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
