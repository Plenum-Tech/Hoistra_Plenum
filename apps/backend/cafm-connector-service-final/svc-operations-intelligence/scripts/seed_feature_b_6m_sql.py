#!/usr/bin/env python3
"""
Seed the 6-month Feature B dataset straight into plenum_cafm as linked relational rows.

The API seeder (seed_feature_b_6m.py) posts work order payloads directly to
/score/work-orders, which produces vendor_wo_scores whose work_order_id points at
nothing. B2 requires "drill-down to underlying work order records", so the work
orders have to exist as rows first. This script writes the full entity graph:

    organizations
      +-- locations, asset_categories
      +-- vendors ------------- compliance_certificates (A3 accreditations)
      |                           +-- drives vendors.block_state
      +-- assets -------------- asset_criticality (L1/L2/L3, HITL approved)
      +-- contract_sla_parameters -- contract_documents
            +-- work_orders (reactive + PPM)
                  |   wo_uuid + vendor_id + assigned_vendor + asset_id + contract_id
                  +-- ppm_visits.work_order_id
                  +-- cost_variance_alerts.work_order_ids

plenum_cafm exists in two shapes and this script adapts to whichever it finds:
the canonical ORM keys core tables by UUID, while tenants provisioned through
svc-work-order-management key organizations/locations by integer and assets/vendors
by varchar. Feature B tables are UUID-keyed in both, so work orders are referenced
by `work_orders.wo_uuid` rather than the primary key.

Afterwards the engine can score everything with no hand-crafted batches:

    POST /api/contract-performance/score/from-udr {"all_buckets": true,
                                                   "generate_scorecard": true}

Prerequisites (--apply-schema runs these for you):
  migrations/phase2_contract_performance.sql
  migrations/feature_b_udr_wo_columns.sql
  migrations/phase2_contract_performance_v2.sql

Usage:
  set DATABASE_URL=postgresql://user:pass@host:5432/plenum_agent
  python scripts/generate_feature_b_6m_data.py
  python scripts/seed_feature_b_6m_sql.py --apply-schema
  python scripts/seed_feature_b_6m_sql.py --verify-only
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA = ROOT / "testdata" / "feature_b_6m"
SCHEMA_FILES = [
    "phase2_contract_performance.sql",
    "feature_b_udr_wo_columns.sql",
    "phase2_contract_performance_v2.sql",
]

SITE_NAME = "Riverside House"
CATEGORIES = {
    "AHU": "Air Handling",
    "CHILLER": "Chillers",
    "BOILER": "Boilers & HWS",
    "PUMP": "Pumps",
    "FCU": "Fan Coil Units",
    "DB": "LV Distribution",
    "LIGHT": "Lighting",
    "UPS": "UPS & Standby Power",
    "PANEL": "Fire Systems",
    "DOOR": "Automatic Doors",
    "LIFT": "Lifts",
}
DEFAULT_CATEGORY = "General Plant"


def _uid(namespace: str, key: str) -> str:
    """Deterministic ids so re-running updates rows instead of duplicating them."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"feature-b-6m:{namespace}:{key}"))


def _dsn(raw: str) -> str:
    return (
        raw.replace("postgresql+asyncpg://", "postgresql://")
        .replace("postgres+asyncpg://", "postgresql://")
        .replace("postgresql+psycopg://", "postgresql://")
    )


def _load(data_dir: Path, name: str) -> Any:
    path = data_dir / name
    if not path.exists():
        raise SystemExit(f"Missing {path}. Run: python scripts/generate_feature_b_6m_data.py")
    return json.loads(path.read_text(encoding="utf-8"))


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _d(value: str | None) -> date | None:
    return date.fromisoformat(value[:10]) if value else None


def _category_for(asset_code: str) -> str:
    return CATEGORIES.get(asset_code.split("-")[0], DEFAULT_CATEGORY)


# ─────────────────────────────────────────────────────────────────────────────
# Schema profile + generic upsert
# ─────────────────────────────────────────────────────────────────────────────
class SchemaProfile:
    """Live column types for plenum_cafm, so inserts match the tenant's actual shape."""

    def __init__(self, cur) -> None:
        cur.execute(
            """
            SELECT table_name, column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = 'plenum_cafm'
            """
        )
        self.types: dict[tuple[str, str], str] = {(t, c): d for t, c, d in cur.fetchall()}

    def type_of(self, table: str, column: str) -> str | None:
        return self.types.get((table, column))

    def is_uuid(self, table: str, column: str) -> bool:
        return self.type_of(table, column) == "uuid"

    def has(self, table: str, column: str) -> bool:
        return (table, column) in self.types

    @property
    def canonical(self) -> bool:
        return self.is_uuid("work_orders", "id")

    def describe(self) -> str:
        shape = "canonical (UUID keys)" if self.canonical else "legacy (integer/varchar keys)"
        return (
            f"{shape} | work_orders.id={self.type_of('work_orders', 'id')} "
            f"assets.id={self.type_of('assets', 'id')} "
            f"vendors.id={self.type_of('vendors', 'id')} "
            f"organizations.id={self.type_of('organizations', 'id')}"
        )


def upsert(
    cur,
    profile: SchemaProfile,
    table: str,
    values: dict[str, Any],
    *,
    conflict: str,
    skip_update: tuple[str, ...] = ("id", "created_at"),
) -> None:
    """INSERT .. ON CONFLICT DO UPDATE using only columns this tenant actually has.

    UUID columns get an explicit ::uuid cast because the values are passed as strings;
    everything else is adapted by the driver.
    """
    cols = [c for c in values if profile.has(table, c)]
    if not cols:
        return
    placeholders = ", ".join(
        "%s::uuid" if profile.is_uuid(table, c) else "%s" for c in cols
    )
    conflict_cols = {c.strip() for c in conflict.split(",")}
    updates = [c for c in cols if c not in skip_update and c not in conflict_cols]
    set_clause = (
        ", ".join(f"{c} = EXCLUDED.{c}" for c in updates) if updates else None
    )
    action = f"DO UPDATE SET {set_clause}" if set_clause else "DO NOTHING"
    sql = (
        f"INSERT INTO plenum_cafm.{table} ({', '.join(cols)}) "
        f"VALUES ({placeholders}) "
        f"ON CONFLICT ({conflict}) {action}"
    )
    cur.execute(sql, [values[c] for c in cols])


def _find_or_create(
    cur, profile: SchemaProfile, table: str, *, match: dict[str, Any], create: dict[str, Any]
) -> Any:
    """Resolve a core-table row id where the key space is not ours to choose."""
    where = " AND ".join(f"{k} = %s" for k in match)
    cur.execute(f"SELECT id FROM plenum_cafm.{table} WHERE {where} LIMIT 1", list(match.values()))
    row = cur.fetchone()
    if row:
        return row[0]
    payload = {**match, **create}
    cols = [c for c in payload if profile.has(table, c)]
    placeholders = ", ".join("%s" for _ in cols)
    cur.execute(
        f"INSERT INTO plenum_cafm.{table} ({', '.join(cols)}) "
        f"VALUES ({placeholders}) RETURNING id",
        [payload[c] for c in cols],
    )
    return cur.fetchone()[0]


# ─────────────────────────────────────────────────────────────────────────────
# Derived data
# ─────────────────────────────────────────────────────────────────────────────
def compute_block_state(
    accreditations: list[dict], vendor_id: str, as_of: date
) -> tuple[str, str | None, date | None, str | None]:
    """A3 accreditation floor: any lapsed vendor-scope certificate blocks the vendor.

    Derived from the certificates rather than asserted, so the block state is
    explainable by a row a reviewer can open.
    """
    lapsed = [
        a for a in accreditations if a["vendor_id"] == vendor_id and _d(a["expiry_date"]) < as_of
    ]
    if not lapsed:
        return "Clear", None, None, None
    worst = min(lapsed, key=lambda a: _d(a["expiry_date"]))
    expiry = _d(worst["expiry_date"])
    return (
        "Blocked",
        f"{worst['cert_type']} lapsed on {expiry.isoformat()} — accreditation not current.",
        expiry,
        worst["cert_type"],
    )


def build_cost_variance_alerts(work_orders: list[dict], weights: dict, org_id: str) -> list[dict]:
    """B2: systematic overrun — >threshold% variance on N+ jobs in one month."""
    threshold = float(weights["cost_variance_alert_pct"])
    min_jobs = int(weights["cost_variance_job_count"])
    buckets: dict[tuple[str, str], list[dict]] = {}
    for wo in work_orders:
        buckets.setdefault((wo["vendor_id"], wo["score_month"]), []).append(wo)

    alerts = []
    for (vendor_id, month), rows in sorted(buckets.items()):
        breaches, variances = [], []
        total_est = total_act = 0.0
        for wo in rows:
            est = float(wo["cost_estimated"] or 0)
            act = float(wo["cost_actual"] or 0)
            total_est += est
            total_act += act
            if est <= 0:
                continue
            pct = (act - est) / est * 100.0
            variances.append(pct)
            if pct > threshold:
                breaches.append(wo)
        if len(breaches) < min_jobs:
            continue
        alerts.append(
            {
                "id": _uid("cost_alert", f"{vendor_id}:{month}"),
                "organization_id": org_id,
                "vendor_id": vendor_id,
                "score_month": _d(month),
                "threshold_pct": threshold,
                "job_count_threshold": min_jobs,
                "breach_job_count": len(breaches),
                "scored_job_count": len(rows),
                "total_estimated": round(total_est, 2),
                "total_actual": round(total_act, 2),
                "total_delta": round(total_act - total_est, 2),
                "avg_variance_pct": round(sum(variances) / len(variances), 2) if variances else None,
                "max_variance_pct": round(max(variances), 2) if variances else None,
                "work_order_ids": json.dumps([w["id"] for w in breaches]),
                "status": "open",
                "detail_json": json.dumps(
                    {
                        "breaching_wo_codes": [w["wo_code"] for w in breaches],
                        "rule": f"variance > {threshold}% on {min_jobs}+ jobs in a calendar month",
                    }
                ),
            }
        )
    return alerts


# ─────────────────────────────────────────────────────────────────────────────
# Seed
# ─────────────────────────────────────────────────────────────────────────────
def seed(conn, data_dir: Path, *, as_of: date) -> dict[str, int]:
    manifest = _load(data_dir, "manifest.json")
    vendors = _load(data_dir, "vendors.json")["vendors"]
    accreditations = _load(data_dir, "accreditations.json")
    contracts = _load(data_dir, "contracts.json")
    assets = _load(data_dir, "assets_criticality.json")
    weights = _load(data_dir, "weights.json")
    work_orders = _load(data_dir, "work_orders_6m.json")
    ppm_visits = _load(data_dir, "ppm_visits_6m.json")

    org_uuid = manifest["organization_id"]
    counts: dict[str, int] = {}
    cur = conn.cursor()
    profile = SchemaProfile(cur)
    print(f"  {profile.describe()}")

    # ── organizations / locations / categories ───────────────────────────────
    # Feature B tables always use org_uuid. Core tables use whatever their key
    # space allows, which on a legacy tenant means reusing the existing org.
    org_row = {
        "id": org_uuid,
        "name": "Feature B Demo Org",
        "code": "FEATURE-B-DEMO",
        "status": "active",
    }
    if profile.is_uuid("organizations", "id"):
        upsert(cur, profile, "organizations", org_row, conflict="id")
        core_org = org_uuid
    else:
        core_org = _find_or_create(
            cur,
            profile,
            "organizations",
            match={"name": org_row["name"]},
            create={k: v for k, v in org_row.items() if k != "id"},
        )
    counts["organizations"] = 1

    site_row = {
        "id": _uid("location", SITE_NAME),
        "organization_id": core_org,
        "name": SITE_NAME,
        "code": "RIVERSIDE-HOUSE",
        "location_code": "RIVERSIDE-HOUSE",
        "type": "site",
        "location_type": "site",
        "level": 0,
        "city": "London",
        "country": "United Kingdom",
    }
    if profile.is_uuid("locations", "id"):
        site_id: Any = site_row["id"]
        upsert(cur, profile, "locations", site_row, conflict="id")
    else:
        site_id = _find_or_create(
            cur,
            profile,
            "locations",
            match={"name": SITE_NAME},
            create={k: v for k, v in site_row.items() if k != "id"},
        )
    counts["locations"] = 1

    # The canonical ORM calls it `name`; the UDR-provisioned schema calls it
    # `category_name`. Write both and match on whichever exists.
    cat_name_col = "name" if profile.has("asset_categories", "name") else "category_name"
    category_ids: dict[str, Any] = {}
    for name in sorted(set(CATEGORIES.values()) | {DEFAULT_CATEGORY}):
        code = name.upper().replace(" & ", "-").replace(" ", "-")
        cat_row = {
            "id": _uid("category", name),
            "organization_id": core_org,
            "name": name,
            "category_name": name,
            "code": code,
        }
        if profile.is_uuid("asset_categories", "id"):
            cid: Any = cat_row["id"]
            upsert(cur, profile, "asset_categories", cat_row, conflict="id")
        else:
            cid = _find_or_create(
                cur,
                profile,
                "asset_categories",
                match={cat_name_col: name},
                create={k: v for k, v in cat_row.items() if k not in ("id", cat_name_col)},
            )
        category_ids[name] = cid
    counts["asset_categories"] = len(category_ids)

    # ── vendors (block_state derived from accreditations) ────────────────────
    for v in vendors:
        state, reason, block_date, cert_type = compute_block_state(
            accreditations, v["vendor_id"], as_of
        )
        if state != v.get("block_state"):
            print(
                f"  note: {v['vendor_name']} block_state computed as {state} "
                f"(dataset said {v.get('block_state')}) — using computed value."
            )
        upsert(
            cur,
            profile,
            "vendors",
            {
                "id": v["vendor_id"],
                "organization_id": core_org,
                "vendor_name": v["vendor_name"],
                "vendor_code": v.get("vendor_code"),
                "status": "active",
                "trade": v.get("trade"),
                "specialty": v.get("trade"),
                "notes": v.get("trade"),
                "block_state": state,
                "block_reason": reason,
                "block_date": block_date,
                "blocked_accreditation_type": cert_type,
            },
            conflict="id",
        )
    counts["vendors"] = len(vendors)

    # ── compliance_certificates (A3 vendor accreditations) ───────────────────
    for a in accreditations:
        expiry = _d(a["expiry_date"])
        upsert(
            cur,
            profile,
            "compliance_certificates",
            {
                "id": a["id"],
                "org_id": org_uuid,
                "organization_id": org_uuid,
                "vendor_id": a["vendor_id"],
                "certificate_ref": a["certificate_ref"],
                "cert_type": a["cert_type"],
                "certificate_type_code": a["certificate_type_code"],
                "certificate_number": a["certificate_number"],
                "issuer": a["issuer"],
                "issue_date": _d(a["issue_date"]),
                "expiry_date": expiry,
                "next_due_date": _d(a["next_due_date"]),
                "cert_scope": a["cert_scope"],
                "country_code": a["country_code"],
                "result": a["result"],
                "status": "Lapsed" if expiry < as_of else "Current",
                "days_to_expiry": (expiry - as_of).days,
            },
            conflict="id",
        )
    counts["compliance_certificates"] = len(accreditations)

    # ── assets ───────────────────────────────────────────────────────────────
    for a in assets:
        category = _category_for(a["asset_code"])
        upsert(
            cur,
            profile,
            "assets",
            {
                "id": a["asset_id"],
                "organization_id": core_org,
                "location_id": site_id,
                "category_id": category_ids[category],
                "asset_name": f"{a['asset_code']} — {category}",
                "asset_code": a["asset_code"],
                "status": "active",
                "criticality": a["proposed_criticality"],
                "notes": a["rationale"],
            },
            conflict="id",
        )
    counts["assets"] = len(assets)

    # ── asset_criticality (B1 HITL) ──────────────────────────────────────────
    for a in assets:
        upsert(
            cur,
            profile,
            "asset_criticality",
            {
                "id": _uid("asset_criticality", a["asset_code"]),
                "organization_id": org_uuid,
                "asset_id": a["asset_id"],
                "asset_code": a["asset_code"],
                "criticality": a["criticality"],
                "proposed_criticality": a["proposed_criticality"],
                "source": a["source"],
                "rationale": a["rationale"],
                "approved": a["approved"],
                "approved_by": a["approved_by"],
                "approved_at": _dt(a["approved_at"]),
            },
            conflict="asset_id",
        )
    counts["asset_criticality"] = len(assets)

    # ── contract_sla_parameters + contract_documents (B1) ────────────────────
    contract_ids: dict[str, str] = {}
    for c in contracts:
        cid = _uid("contract", c["contract_ref"])
        contract_ids[c["contract_ref"]] = cid
        e = c["extracted"]
        upsert(
            cur,
            profile,
            "contract_sla_parameters",
            {
                "id": cid,
                "organization_id": org_uuid,
                "vendor_id": c["vendor_id"],
                "contract_ref": c["contract_ref"],
                "status": c["status"],
                **{k: e[k] for k in e if k.startswith("sla_")},
                "labour_day_rate": e["labour_day_rate"],
                "overtime_rate": e["overtime_rate"],
                "call_out_rate": e["call_out_rate"],
                "parts_pricing_json": json.dumps(e["parts_pricing_json"]),
                "payment_terms": e["payment_terms"],
                "kpi_clauses_json": json.dumps(e["kpi_clauses_json"]),
                "ppm_obligations_json": json.dumps(e["ppm_obligations_json"]),
                "task_criticality_json": json.dumps(e["task_criticality_json"]),
                "defaults_used": json.dumps(c["defaults_used"]),
                # PM inline edits land here; nothing has been overridden yet.
                "overrides_log": json.dumps([]),
                "field_sources": json.dumps(c["field_sources"]),
                "raw_extraction": json.dumps(e),
                "confirmed_by": c["confirmed_by"],
                "confirmed_at": _dt(c["confirmed_at"]),
            },
            conflict="id",
        )

        # B1 Relationships Agent: a contract is assembled from several documents
        for kind, rel in (
            ("contract", "primary"),
            ("framework_agreement", "supporting"),
            ("purchase_order", "supporting"),
        ):
            key = f"{c['contract_ref']}:{kind}"
            upsert(
                cur,
                profile,
                "contract_documents",
                {
                    "id": _uid("contract_doc", key),
                    "organization_id": org_uuid,
                    "vendor_id": c["vendor_id"],
                    "contract_id": cid,
                    "document_id": _uid("document", key),
                    "document_kind": kind,
                    "document_name": f"{c['contract_ref']} — {kind.replace('_', ' ')}.pdf",
                    "relationship": rel,
                    "confidence": 0.94 if rel == "primary" else 0.81,
                    "extraction_status": (
                        "extracted" if c["status"] == "confirmed" else "needs_review"
                    ),
                    "extracted_fields": json.dumps({"source": kind}),
                    "linked_by": c["confirmed_by"],
                    "linked_at": _dt(c["confirmed_at"]),
                },
                conflict="contract_id, document_id",
            )
    counts["contract_sla_parameters"] = len(contracts)
    counts["contract_documents"] = len(contracts) * 3

    # ── work_orders — the linkage that was missing ───────────────────────────
    # Conflict on wo_uuid, not id: id is a serial on legacy tenants and is not ours
    # to choose. wo_uuid is what every Feature B table references.
    for wo in work_orders:
        row: dict[str, Any] = {
            "wo_uuid": wo["id"],
            "organization_id": core_org,
            "asset_id": wo["asset_id"],
            "location_id": site_id,
            "wo_code": wo["wo_code"],
            "workorder_ref": wo["wo_code"],
            "work_order_id": wo["wo_code"],
            "title": wo["title"],
            "description": (
                f"Repeat fault — follow-on from {wo['recall_of_wo_code']}."
                if wo["recall"]
                else wo["title"]
            ),
            "priority": wo["priority"],
            "status": wo["status"],
            "maintenance_type": wo["maintenance_type"],
            "wo_type": wo["wo_type"],
            "assigned_vendor": wo["vendor_id"],
            "vendor_id": wo["vendor_id"],
            "contract_id": contract_ids.get(wo["contract_ref"]),
            "estimated_hours": wo["attendance_hours"],
            "actual_hours": wo["attendance_hours"],
            "labour_hours": wo["labour_hours"],
            "estimated_cost": wo["cost_estimated"],
            "actual_cost": wo["cost_actual"],
            "part_code": wo["part_code"],
            "parts_cost": wo["parts_cost"],
            "reported_at": _dt(wo["reported_at"]),
            "attended_at": _dt(wo["attended_at"]),
            "responded_at": _dt(wo["attended_at"]),
            "completed_at": _dt(wo["completed_at"]),
            "first_fix": wo["first_fix"],
            "recall": wo["recall"],
            "return_visit": wo["return_visit"],
            "notes": f"score_month={wo['score_month']}"
            + (f"; recall_of={wo['recall_of_wo_code']}" if wo["recall_of_wo_code"] else ""),
            "created_at": _dt(wo["reported_at"]),
        }
        if profile.is_uuid("work_orders", "id"):
            row["id"] = wo["id"]
        upsert(cur, profile, "work_orders", row, conflict="wo_uuid")
    counts["work_orders"] = len(work_orders)

    # ── ppm_visits (B2 PPM compliance) ───────────────────────────────────────
    for p in ppm_visits:
        upsert(
            cur,
            profile,
            "ppm_visits",
            {
                "id": p["id"],
                "organization_id": org_uuid,
                "vendor_id": p["vendor_id"],
                "contract_id": contract_ids.get(p["contract_ref"]),
                "asset_id": p["asset_id"],
                "asset_code": p["asset_code"],
                "work_order_id": p["work_order_id"],
                "wo_code": p["wo_code"],
                "ppm_ref": p["ppm_ref"],
                "frequency": p["frequency"],
                "score_month": _d(p["score_month"]),
                "scheduled_date": _d(p["scheduled_date"]),
                "completed_date": _d(p["completed_date"]),
                "tolerance_days": p["tolerance_days"],
                "status": p["status"],
                "source": "fm_report",
            },
            conflict="id",
            # variance_days / within_tolerance are generated columns
            skip_update=("id", "created_at", "variance_days", "within_tolerance"),
        )
    counts["ppm_visits"] = len(ppm_visits)

    # ── vendor_score_weight_config (B2 admin section) ────────────────────────
    upsert(
        cur,
        profile,
        "vendor_score_weight_config",
        {"id": _uid("weights", org_uuid), "organization_id": org_uuid, **weights},
        conflict="organization_id",
    )
    counts["vendor_score_weight_config"] = 1

    # ── cost_variance_alerts (B2 systematic overrun) ─────────────────────────
    alerts = build_cost_variance_alerts(
        [w for w in work_orders if w["wo_type"] == "Reactive"], weights, org_uuid
    )
    for al in alerts:
        upsert(cur, profile, "cost_variance_alerts", al, conflict="vendor_id, score_month")
    counts["cost_variance_alerts"] = len(alerts)

    # ── fm_report_staleness ──────────────────────────────────────────────────
    last_month = max(manifest["score_months"])
    for v in vendors:
        upsert(
            cur,
            profile,
            "fm_report_staleness",
            {
                "id": _uid("staleness", v["vendor_id"]),
                "organization_id": org_uuid,
                "vendor_id": v["vendor_id"],
                "last_report_at": _dt(f"{last_month}T09:00:00+00:00"),
                "data_as_of": _d(last_month),
                "note": "Synthetic 6-month FM report set; scores reflect vendor-reported data.",
            },
            conflict="id",
        )
    counts["fm_report_staleness"] = len(vendors)

    conn.commit()
    return counts


# ─────────────────────────────────────────────────────────────────────────────
# Verification
# ─────────────────────────────────────────────────────────────────────────────
LINKAGE_CHECKS = [
    (
        "seeded work orders missing wo_uuid",
        """SELECT count(*) FROM plenum_cafm.work_orders
           WHERE wo_code LIKE 'WO-%' AND wo_uuid IS NULL""",
    ),
    (
        "work orders with no vendor row",
        """SELECT count(*) FROM plenum_cafm.work_orders wo
           LEFT JOIN plenum_cafm.vendors v ON v.id::text = wo.vendor_id::text
           WHERE wo.vendor_id IS NOT NULL AND v.id IS NULL""",
    ),
    (
        "work orders with no asset row",
        """SELECT count(*) FROM plenum_cafm.work_orders wo
           LEFT JOIN plenum_cafm.assets a ON a.id::text = wo.asset_id::text
           WHERE wo.asset_id IS NOT NULL AND a.id IS NULL""",
    ),
    (
        "work orders where vendor_id <> assigned_vendor",
        """SELECT count(*) FROM plenum_cafm.work_orders
           WHERE wo_code LIKE 'WO-%'
             AND vendor_id::text IS DISTINCT FROM assigned_vendor::text""",
    ),
    (
        "seeded work orders with no contract",
        """SELECT count(*) FROM plenum_cafm.work_orders wo
           LEFT JOIN plenum_cafm.contract_sla_parameters c ON c.id = wo.contract_id
           WHERE wo.wo_code LIKE 'WO-%' AND c.id IS NULL""",
    ),
    (
        "seeded assets with no criticality record",
        """SELECT count(*) FROM plenum_cafm.asset_criticality ac
           LEFT JOIN plenum_cafm.assets a ON a.id::text = ac.asset_id::text
           WHERE a.id IS NULL""",
    ),
    (
        "completed PPM visits with no work order",
        """SELECT count(*) FROM plenum_cafm.ppm_visits
           WHERE status = 'completed' AND work_order_id IS NULL""",
    ),
    (
        "PPM visits pointing at a missing work order",
        """SELECT count(*) FROM plenum_cafm.ppm_visits p
           LEFT JOIN plenum_cafm.work_orders wo ON wo.wo_uuid = p.work_order_id
           WHERE p.work_order_id IS NOT NULL AND wo.wo_uuid IS NULL""",
    ),
    (
        "scored work orders not resolving to a work order row",
        "SELECT count(*) FROM plenum_cafm.v_feature_b_wo_linkage WHERE NOT work_order_linked",
    ),
    (
        "invoice lines not resolving to a work order row",
        """SELECT count(*) FROM plenum_cafm.invoice_lines il
           LEFT JOIN plenum_cafm.work_orders wo ON wo.wo_uuid = il.work_order_id
           WHERE il.work_order_id IS NOT NULL AND wo.wo_uuid IS NULL""",
    ),
]

SUMMARY_SQL = """
SELECT v.vendor_name,
       v.block_state,
       count(*) FILTER (WHERE wo.wo_type = 'Reactive') AS reactive_wos,
       count(*) FILTER (WHERE wo.wo_type = 'PPM')      AS ppm_wos,
       count(*) FILTER (WHERE wo.recall)               AS recalls,
       min(wo.completed_at)::date                      AS first_completion,
       max(wo.completed_at)::date                      AS last_completion
FROM plenum_cafm.work_orders wo
JOIN plenum_cafm.vendors v ON v.id::text = wo.vendor_id::text
WHERE wo.wo_code LIKE 'WO-%'
GROUP BY v.vendor_name, v.block_state
ORDER BY v.vendor_name
"""


ORPHAN_SCORES_SQL = """
SELECT s.id, s.wo_code, s.vendor_id::text, s.score_month
FROM plenum_cafm.vendor_wo_scores s
LEFT JOIN plenum_cafm.work_orders wo
       ON wo.wo_uuid = s.work_order_id
       OR (s.work_order_id IS NULL AND wo.wo_code = s.wo_code)
WHERE wo.wo_uuid IS NULL
ORDER BY s.created_at
"""


def prune_orphan_scores(conn) -> int:
    """Delete vendor scores that resolve to no work order at all.

    B2 requires drill-down from a score to the work order it came from, so a score
    with no reachable work order cannot be defended and should not sit in a scorecard.
    Destructive, so it is opt-in via --prune-orphan-scores.
    """
    cur = conn.cursor()
    cur.execute(ORPHAN_SCORES_SQL)
    ids = [r[0] for r in cur.fetchall()]
    if not ids:
        return 0
    cur.execute("DELETE FROM plenum_cafm.vendor_wo_scores WHERE id = ANY(%s)", (ids,))
    conn.commit()
    return len(ids)


def verify(conn) -> bool:
    cur = conn.cursor()
    print("\nLinkage checks (all must be 0):")
    ok = True
    for label, sql in LINKAGE_CHECKS:
        try:
            cur.execute(sql)
            n = cur.fetchone()[0]
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            print(f"  SKIP  {label}: {str(exc).splitlines()[0][:90]}")
            continue
        if n:
            ok = False
        print(f"  {'OK  ' if n == 0 else 'FAIL'}  {label}: {n}")

    cur.execute(ORPHAN_SCORES_SQL)
    orphans = cur.fetchall()
    if orphans:
        print(f"\n  {len(orphans)} score(s) resolve to no work order — re-run with "
              f"--prune-orphan-scores to remove them:")
        for _id, wo_code, vendor, month in orphans[:10]:
            print(f"    wo_code={wo_code}  vendor={vendor}  month={month}")

    print("\nSeeded work orders by vendor:")
    cur.execute(SUMMARY_SQL)
    for r in cur.fetchall():
        print(
            f"  {r[0]:32s} block={str(r[1]):<8s} reactive={r[2]:<4d} ppm={r[3]:<4d} "
            f"recalls={r[4]:<3d} {r[5]} -> {r[6]}"
        )
    return ok


def apply_schema(conn) -> None:
    conn.add_notice_handler(lambda d: print(f"    {d.message_primary}"))
    cur = conn.cursor()
    for name in SCHEMA_FILES:
        path = ROOT / "migrations" / name
        if not path.exists():
            print(f"  missing migration: {path}")
            continue
        cur.execute(path.read_text(encoding="utf-8"))
        print(f"  applied {name}")
    conn.commit()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", type=Path, default=DEFAULT_DATA)
    ap.add_argument("--database-url", default=None, help="Overrides DATABASE_URL / DB_URL")
    ap.add_argument("--apply-schema", action="store_true", help="Apply Feature B migrations first")
    ap.add_argument("--verify-only", action="store_true", help="Run linkage checks and exit")
    ap.add_argument(
        "--prune-orphan-scores",
        action="store_true",
        help="Delete vendor_wo_scores rows that resolve to no work order (destructive)",
    )
    ap.add_argument(
        "--as-of",
        default=date.today().isoformat(),
        help="Date used to decide accreditation lapse / block_state (YYYY-MM-DD)",
    )
    args = ap.parse_args()

    raw = args.database_url or os.environ.get("DATABASE_URL") or os.environ.get("DB_URL")
    if not raw:
        raise SystemExit("Set DATABASE_URL (or pass --database-url).")
    try:
        import psycopg
    except ImportError:
        raise SystemExit("psycopg is required: pip install 'psycopg[binary]'") from None

    with psycopg.connect(_dsn(raw)) as conn:
        if args.apply_schema:
            print("Applying Feature B migrations...")
            apply_schema(conn)

        if not args.verify_only:
            print(f"Seeding from {args.data} ...")
            counts = seed(conn, args.data, as_of=date.fromisoformat(args.as_of))
            print("\nRows written:")
            for k, v in counts.items():
                print(f"  {k:28s} {v}")

        if args.prune_orphan_scores:
            n = prune_orphan_scores(conn)
            print(f"\nPruned {n} orphaned vendor score(s).")

        ok = verify(conn)

    print(
        "\nNext: POST /api/contract-performance/score/from-udr "
        '{"all_buckets": true, "generate_scorecard": true}'
    )
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
