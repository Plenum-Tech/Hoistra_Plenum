#!/usr/bin/env python3
"""
Load Feature B 6-month testdata into svc-operations-intelligence via HTTP API.

Prerequisites:
  - ops-intelligence running (default http://localhost:8009)
  - phase2_contract_performance.sql applied
  - optional: vendors rows exist (script best-effort creates stubs via SQL if DATABASE_URL set)

Usage:
  python scripts/generate_feature_b_6m_data.py
  python scripts/seed_feature_b_6m.py --base-url http://localhost:8009
  python scripts/seed_feature_b_6m.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA = ROOT / "testdata" / "feature_b_6m"


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _ok(resp: httpx.Response) -> dict:
    try:
        body = resp.json()
    except Exception:
        body = {"raw": resp.text[:500]}
    if resp.status_code >= 400:
        raise RuntimeError(f"{resp.request.method} {resp.request.url} -> {resp.status_code}: {body}")
    return body if isinstance(body, dict) else {"data": body}


def ensure_vendors_sql(vendors_doc: dict) -> None:
    """Best-effort insert org/vendors when DATABASE_URL / DB_URL is available."""
    db_url = os.environ.get("DATABASE_URL") or os.environ.get("DB_URL")
    if not db_url:
        print("No DATABASE_URL — skipping SQL vendor bootstrap (API-only path).")
        return
    try:
        import psycopg
    except ImportError:
        print("psycopg not installed — skipping SQL vendor bootstrap.")
        return

    # asyncpg URL → psycopg
    dsn = db_url.replace("postgresql+asyncpg://", "postgresql://").replace(
        "postgres+asyncpg://", "postgresql://"
    )
    org = vendors_doc["organization_id"]
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO plenum_cafm.organizations (id, name, created_at, updated_at)
                VALUES (%s::uuid, %s, now(), now())
                ON CONFLICT (id) DO NOTHING
                """,
                (org, "Feature B Demo Org"),
            )
            for v in vendors_doc["vendors"]:
                cur.execute(
                    """
                    INSERT INTO plenum_cafm.vendors
                      (id, organization_id, vendor_name, vendor_code, status, block_state, created_at, updated_at)
                    VALUES
                      (%s::uuid, %s::uuid, %s, %s, 'active', %s, now(), now())
                    ON CONFLICT (id) DO UPDATE
                      SET vendor_name = EXCLUDED.vendor_name,
                          block_state = EXCLUDED.block_state,
                          updated_at = now()
                    """,
                    (
                        v["vendor_id"],
                        org,
                        v["vendor_name"],
                        v.get("vendor_code"),
                        v.get("block_state") or "Clear",
                    ),
                )
        conn.commit()
    print(f"SQL bootstrap: org + {len(vendors_doc['vendors'])} vendors upserted.")


def apply_feature_b_schema() -> None:
    """Apply Feature B + UDR WO scoring columns when DB_URL set."""
    db_url = os.environ.get("DATABASE_URL") or os.environ.get("DB_URL")
    if not db_url:
        print("No DATABASE_URL — skip schema apply (ensure phase2 + feature_b_udr_wo_columns.sql ran).")
        return
    try:
        import psycopg
    except ImportError:
        print("psycopg not installed — skip schema apply.")
        return
    dsn = db_url.replace("postgresql+asyncpg://", "postgresql://").replace(
        "postgres+asyncpg://", "postgresql://"
    )
    paths = [
        ROOT / "migrations" / "phase2_contract_performance.sql",
        ROOT / "migrations" / "feature_b_udr_wo_columns.sql",
    ]
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            for sql_path in paths:
                if not sql_path.exists():
                    print(f"Schema file missing: {sql_path}")
                    continue
                cur.execute(sql_path.read_text(encoding="utf-8"))
                print(f"Applied schema: {sql_path.name}")
        conn.commit()


def seed(
    base_url: str,
    data_dir: Path,
    *,
    dry_run: bool = False,
    skip_vendor_sql: bool = False,
    apply_schema: bool = True,
) -> None:
    base = base_url.rstrip("/")
    contracts = _load(data_dir / "contracts.json")
    assets = _load(data_dir / "assets_criticality.json")
    weights = _load(data_dir / "weights.json")
    score_batches = _load(data_dir / "score_batches.json")
    invoices = _load(data_dir / "invoices_6m.json")
    scorecards = _load(data_dir / "scorecard_requests.json")
    vendors_doc = _load(data_dir / "vendors.json")
    confirmed_vendor_ids = {
        c["vendor_id"] for c in contracts if c.get("status") == "confirmed"
    }

    if dry_run:
        print(
            json.dumps(
                {
                    "base_url": base,
                    "contracts": len(contracts),
                    "assets": len(assets),
                    "score_batches": len(score_batches),
                    "invoices": len(invoices),
                    "scorecards": len(scorecards),
                    "migration_csvs": str(data_dir / "migration_csvs"),
                },
                indent=2,
            )
        )
        return

    if apply_schema:
        apply_feature_b_schema()
    if not skip_vendor_sql:
        ensure_vendors_sql(vendors_doc)

    with httpx.Client(timeout=120.0) as client:
        print("-> weights")
        _ok(
            client.put(
                f"{base}/api/contract-performance/admin/weights",
                json=weights,
            )
        )

        contract_ids: dict[str, str] = {}
        for c in contracts:
            want_confirm = c.get("status") == "confirmed"
            print(
                f"-> ingest{'/confirm' if want_confirm else ' (draft B1 HITL)'} {c['contract_ref']}"
            )
            body = {
                "extracted": c["extracted"],
                "organization_id": c["organization_id"],
                "vendor_id": c["vendor_id"],
                "contract_ref": c["contract_ref"],
            }
            ingested = _ok(
                client.post(f"{base}/api/contract-performance/contracts/ingest", json=body)
            )
            params = ingested.get("parameters") or ingested.get("contract") or {}
            cid = (
                ingested.get("id")
                or params.get("id")
                or (ingested.get("contract") or {}).get("id")
            )
            if not cid:
                # list and find
                listed = _ok(
                    client.get(
                        f"{base}/api/contract-performance/contracts",
                        params={"vendor_id": c["vendor_id"]},
                    )
                )
                items = (
                    listed.get("contracts")
                    or listed.get("parameters")
                    or listed.get("items")
                    or []
                )
                if isinstance(items, dict):
                    items = items.get("items") or items.get("parameters") or []
                cid = next(
                    (
                        x.get("id")
                        for x in items
                        if isinstance(x, dict) and x.get("contract_ref") == c["contract_ref"]
                    ),
                    None,
                )
            if not cid:
                raise RuntimeError(f"No contract id after ingest for {c['contract_ref']}: {ingested}")
            contract_ids[c["vendor_id"]] = str(cid)
            if want_confirm:
                _ok(
                    client.post(
                        f"{base}/api/contract-performance/contracts/{cid}/confirm",
                        json={"confirmed_by": c.get("confirmed_by")},
                    )
                )

        for a in assets:
            print(f"-> asset criticality {a['asset_code']}")
            created = _ok(
                client.post(
                    f"{base}/api/contract-performance/asset-criticality",
                    json={
                        "asset_id": a["asset_id"],
                        "asset_code": a["asset_code"],
                        "organization_id": a["organization_id"],
                        "proposed": a.get("proposed_criticality") or a["criticality"],
                        "source": a.get("source") or "system",
                    },
                )
            )
            crit = (
                created.get("asset_criticality")
                or created.get("criticality")
                or created.get("item")
                or created
            )
            aid = created.get("id") or (crit.get("id") if isinstance(crit, dict) else None)
            if a.get("approved") and aid:
                _ok(
                    client.post(
                        f"{base}/api/contract-performance/asset-criticality/{aid}/approve",
                        json={
                            "criticality": a["criticality"],
                            "approved_by": a.get("approved_by"),
                        },
                    )
                )

        for batch in score_batches:
            print(
                f"-> score WOs {batch['score_month']} vendor ...{batch['vendor_id'][-4:]} "
                f"({len(batch['work_orders'])} jobs)"
            )
            _ok(
                client.post(
                    f"{base}/api/contract-performance/score/work-orders",
                    json=batch,
                )
            )

        for inv in invoices:
            if inv["vendor_id"] not in confirmed_vendor_ids:
                print(f"-> skip invoice {inv['invoice_ref']} (vendor contract still draft)")
                continue
            print(f"-> invoice verify {inv['invoice_ref']}")
            _ok(
                client.post(
                    f"{base}/api/contract-performance/invoices/verify",
                    json={
                        "invoice_ref": inv["invoice_ref"],
                        "lines": inv["lines"],
                        "work_orders": inv["work_orders"],
                        "vendor_id": inv["vendor_id"],
                        "organization_id": inv["organization_id"],
                        "labour_day_rate": inv["labour_day_rate"],
                        "parts_pricing_json": inv["parts_pricing_json"],
                    },
                )
            )

        for sc in scorecards:
            if sc["vendor_id"] not in confirmed_vendor_ids:
                print(f"-> skip scorecard {sc['score_month']} (draft vendor)")
                continue
            print(f"-> scorecard {sc['score_month']} ...{sc['vendor_id'][-4:]}")
            resp = client.post(
                f"{base}/api/contract-performance/scorecards/monthly",
                json=sc,
            )
            if resp.status_code >= 400:
                print(f"  ! scorecard warn: {resp.status_code} {resp.text[:200]}")
            else:
                body = resp.json()
                scard = body.get("scorecard") or body
                print(
                    f"  overall={scard.get('overall_score')} "
                    f"ppm={scard.get('ppm_compliance_pct')} "
                    f"capped={scard.get('block_capped')}"
                )

    print("Done. Feature B 6-month dataset loaded.")
    print("Check: GET /api/contract-performance/scorecards?organization_id=" + vendors_doc["organization_id"])
    print("       GET /api/contract-performance/saved-space/summary")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base-url", default=os.environ.get("OPS_BASE_URL", "http://localhost:8009"))
    ap.add_argument("--data", type=Path, default=DEFAULT_DATA)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--generate-first", action="store_true", help="Run generator before seed")
    ap.add_argument(
        "--skip-vendor-sql",
        action="store_true",
        help="Skip org/vendor SQL upsert (use after CSV migration already wrote vendors)",
    )
    ap.add_argument(
        "--no-schema",
        action="store_true",
        help="Skip applying phase2_contract_performance.sql",
    )
    args = ap.parse_args()

    if args.generate_first or not (args.data / "manifest.json").exists():
        from generate_feature_b_6m_data import generate
        from datetime import date

        print(f"Generating dataset -> {args.data}")
        generate(args.data, date(2026, 2, 1), 6)

    if not (args.data / "manifest.json").exists():
        print(f"Dataset missing at {args.data}. Run generate_feature_b_6m_data.py first.", file=sys.stderr)
        sys.exit(1)

    seed(
        args.base_url,
        args.data,
        dry_run=args.dry_run,
        skip_vendor_sql=args.skip_vendor_sql,
        apply_schema=not args.no_schema,
    )


if __name__ == "__main__":
    main()
