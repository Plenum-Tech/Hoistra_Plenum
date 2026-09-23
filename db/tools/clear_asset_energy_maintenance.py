"""Empty the assets, energy and maintenance tables so a fresh ingest can be judged on its own.

For testing an end-to-end ingest: whatever the page shows afterwards came from the file, not
from something already in the database. hoistra_test only — the DSN comes from HOISTRA_TEST_DSN
and the script refuses to run against a database whose name is not hoistra_test, because the
same statements against plenum_agent would empty the production portfolio.

WHAT IS KEPT, and why:

  organizations, users        nothing in the workbook recreates a login; deleting them locks
                              you out of the app you are testing in
  buildings, sites, locations the spine every ingested row keys on. assets, meters, work orders
                              and PPM visits all resolve a building_id, so clearing buildings
                              would make every ingested row land unlinked — the opposite of the
                              test. The workbook's Buildings sheet re-states them anyway.
  floors                      building_sections are cleared; floors are the fabric the sections
                              hang off and nothing re-creates them
  documents                   the uploaded files themselves. A certificate row is cleared with the
                              rest (see below); the document it was extracted from is not, because
                              the ingest does not recreate files.

WHAT CHANGED ON 23 SEP 2026: compliance_certificates is now cleared too. It was kept while the
workbooks carried no certificates, because clearing it would have destroyed rows nothing could
put back. The workbooks now carry a Compliance_Certificates sheet that states every certificate
on record, so a fresh ingest restores them - and leaving them behind meant the compliance figures
after an ingest were partly yesterday's, which is the thing this script exists to prevent.

Order matters: a child row is deleted before the parent it points at, or the foreign key stops
it. Everything runs in ONE transaction, so a failure anywhere leaves the database untouched.

Dry run by default. Pass --apply to delete.
"""
from __future__ import annotations

import argparse
import asyncio
import os

import asyncpg

#: Deleted in this order — children before parents. A table absent from the schema is skipped.
#: Grouped for the report, but emptied as one transaction.
PLAN: list[tuple[str, list[str]]] = [
    ("maintenance · work order children", [
        "work_order_tasks", "work_order_parts", "work_order_comments", "work_order_history",
        "work_order_attachments", "work_order_assets", "work_order_users",
        "work_order_resources", "misc_costs",
    ]),
    ("maintenance · the work itself", [
        "vendor_wo_scores", "maintenance_history", "work_orders",
        "ppm_visits", "maintenance_plans", "inspections",
        "scheduled_tasks", "scheduled_maintenance_assets", "scheduled_maintenance_parts",
    ]),
    ("maintenance · parts and people", [
        "inventory_transactions", "bom_group_parts", "receipt_line_items",
        "purchase_order_line_items", "purchase_orders", "receipts",
        "spare_parts", "technicians", "resources",
    ]),
    ("maintenance · vendors and contracts", [
        "contract_sla_parameters", "vendor_contacts", "vendor_contracts",
        "sla_policies", "vendors",
    ]),
    ("energy", [
        "meter_readings", "energy_anomalies", "eui_snapshots",
        "building_energy_profiles", "energy_ratings", "energy_meters", "meters",
    ]),
    ("compliance", [
        "compliance_certificates",
    ]),
    ("assets", [
        "asset_readings", "asset_documents", "asset_offline_log", "asset_warranties",
        "assets", "asset_reading_bands", "building_sections",
    ]),
    # Invoices are contract-performance data about a vendor. With the vendors gone they are
    # bills from nobody, and the scorecard was still counting them ("Invoices 1").
    ("vendors · invoices", [
        "invoice_verifications", "invoice_lines", "invoices",
    ]),
    # Figures derived from the rows above. Left behind, they are the previous run's numbers
    # sitting under the new run's data — a scorecard for a vendor that no longer exists is
    # what the Vendors page showed as "Vendor 6623", named by the only thing it had left, the
    # id. vendor_score_weight_config is NOT here: it is how a vendor is scored, not a score.
    ("derived · compliance and vendor run state", [
        "compliance_risk_snapshots", "compliance_scan_runs", "vendor_monthly_scorecards",
    ]),
    # The uploaded files as the database records them. The ingest deduplicates on SHA-256
    # against ingestion_documents, so leaving these means re-uploading the same PDFs is
    # skipped as a duplicate and the document path cannot be tested at all. The pipeline
    # tables that key on an ingestion go with it: they are the history of runs whose output
    # has just been deleted. The blobs in Azure storage are not touched.
    ("documents · ingested files and their pipeline history", [
        "document_chunks", "documents",
        "review_queue", "corrections_log", "ingestion_audit_log", "claude_api_usage",
        "ingestion_documents",
    ]),
    # An empty portfolio has nothing awaiting a decision. Everything the queue was raised
    # about is gone, so the whole queue goes, with the emails and tokens that hang off it.
    ("approvals · the whole queue", [
        "ops_email_log", "approval_action_tokens", "approvals_queue_items",
    ]),
]

#: Rows that point at something being deleted but must themselves survive: the reference is
#: cleared instead. A certificate is a record in its own right and outlives the asset row.
DETACH: list[tuple[str, str]] = []

#: Queue items raised BY the things being deleted. Leaving them behind is how the approvals
#: queue filled with 174 items pointing at nothing earlier in this project.
APPROVAL_PREFIXES = ("energy_anomaly_", "contract_params_", "certificate")


def dsn() -> str:
    raw = os.environ.get("HOISTRA_TEST_DSN")
    if not raw:
        raise SystemExit("HOISTRA_TEST_DSN is not set; source the repo .env first")
    return raw.replace("+asyncpg", "")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="delete; otherwise report only")
    args = ap.parse_args()

    c = await asyncpg.connect(dsn())
    try:
        db = await c.fetchval("SELECT current_database()")
        if db != "hoistra_test":
            raise SystemExit(
                f"refusing to run against '{db}'. This script is for hoistra_test only; the "
                f"same statements would empty a production portfolio.")
        print(f"\n  database: {db}\n")

        present = {r["table_name"] for r in await c.fetch(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='plenum_cafm'")}

        total = 0
        tx = c.transaction()
        await tx.start()
        try:
            for label, tables in PLAN:
                shown = False
                for t in tables:
                    if t not in present:
                        continue
                    n = await c.fetchval(f"SELECT count(*) FROM plenum_cafm.{t}")
                    if not n:
                        continue
                    if not shown:
                        print(f"  {label}")
                        shown = True
                    print(f"    {t:30} {n:>8,}")
                    total += n
                    if args.apply:
                        await c.execute(f"DELETE FROM plenum_cafm.{t}")
                if shown:
                    print()

            for t, col in DETACH:
                if t not in present:
                    continue
                n = await c.fetchval(
                    f"SELECT count(*) FROM plenum_cafm.{t} WHERE {col} IS NOT NULL")
                if n:
                    print(f"  kept, reference cleared")
                    print(f"    {t}.{col:<20} {n:>8,}  (the row stays; it no longer names an asset)\n")
                    if args.apply:
                        await c.execute(
                            f"UPDATE plenum_cafm.{t} SET {col} = NULL WHERE {col} IS NOT NULL")

            if "approvals_queue_items" in present:
                like = " OR ".join(f"item_type LIKE '{p}%'" for p in APPROVAL_PREFIXES)
                n = await c.fetchval(
                    f"SELECT count(*) FROM plenum_cafm.approvals_queue_items WHERE {like}")
                if n:
                    print("  approvals raised by the above")
                    print(f"    approvals_queue_items          {n:>8,}\n")
                    total += n
                    if args.apply:
                        await c.execute(
                            f"DELETE FROM plenum_cafm.approvals_queue_items WHERE {like}")

            if args.apply:
                await tx.commit()
            else:
                await tx.rollback()
        except Exception:
            await tx.rollback()
            raise

        print(f"  {total:,} row(s) {'deleted' if args.apply else 'would be deleted'}")
        if not args.apply:
            print("  dry run - pass --apply to delete")
            return

        print("\n  what remains\n")
        for t in ("organizations", "users", "buildings", "sites", "locations", "floors",
                  "compliance_verification_sources", "country_certificate_packs",
                  "vendor_score_weight_config",
                  "compliance_certificates", "documents", "ingestion_documents",
                  "assets", "energy_meters", "meter_readings", "work_orders", "ppm_visits",
                  "vendors", "invoices", "vendor_monthly_scorecards", "spare_parts",
                  "approvals_queue_items"):
            if t in present:
                n = await c.fetchval(f"SELECT count(*) FROM plenum_cafm.{t}")
                print(f"    {t:30} {n:>8,}")
    finally:
        await c.close()


if __name__ == "__main__":
    asyncio.run(main())
