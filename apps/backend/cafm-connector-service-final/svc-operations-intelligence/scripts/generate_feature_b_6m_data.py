#!/usr/bin/env python3
"""
Generate 6 months of Feature B (Contract Performance) synthetic test data.

Coverage (B1–B3):
  - Confirmed contract SLA parameters per vendor
  - Asset criticality (L1/L2/L3, HITL approved mix)
  - Admin score weight config
  - Completed work orders (~28/vendor/month) with SLA hit/miss, recalls, cost variance
  - PPM visits (±7 day compliance mix)
  - Monthly invoices (matched + flagged lines, some >£500)
  - Ready for POST /score/work-orders + /scorecards/monthly + /invoices/verify

Default window: 2026-02-01 … 2026-07-01 (six score months ending before Aug 2026).

Usage:
  python scripts/generate_feature_b_6m_data.py
  python scripts/generate_feature_b_6m_data.py --out testdata/feature_b_6m
  python scripts/seed_feature_b_6m.py --base-url http://localhost:8009
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import uuid
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

# Deterministic seed for reproducible datasets
RNG = random.Random(42)

ORG_ID = uuid.UUID("00000000-0000-0000-0000-0000000000b1")
PM_ID = uuid.UUID("00000000-0000-0000-0000-0000000000f1")

VENDORS = [
    {
        "vendor_id": str(uuid.UUID("00000000-0000-0000-0000-0000000000a1")),
        "vendor_name": "Apex Mechanical Services Ltd",
        "vendor_code": "APEX-MECH",
        "block_state": "Clear",
        "trade": "Mechanical / HVAC",
        "contract_ref": "FM-APEX-2026-01",
    },
    {
        "vendor_id": str(uuid.UUID("00000000-0000-0000-0000-0000000000a2")),
        "vendor_name": "BrightSpark Electrical Ltd",
        "vendor_code": "BRIGHT-ELEC",
        "block_state": "Clear",
        "trade": "Electrical",
        "contract_ref": "FM-BRIGHT-2026-01",
    },
    {
        "vendor_id": str(uuid.UUID("00000000-0000-0000-0000-0000000000a3")),
        "vendor_name": "SafeLift Engineering Ltd",
        "vendor_code": "SAFELIFT",
        # Blocked mid-period to exercise accreditation floor / score cap 60
        "block_state": "Blocked",
        "trade": "Lifts / LOLER",
        "contract_ref": "FM-SAFELIFT-2026-01",
        "blocked_from_month": "2026-05-01",
    },
]

PRIORITIES = ["P1", "P2", "P3", "P4"]
PARTS = {
    "FILTER-HEPA-20": 48.50,
    "BELT-A42": 22.00,
    "CONTACTOR-25A": 65.00,
    "LAMP-LED-4FT": 18.75,
    "SEAL-KIT-PUMP": 35.00,
    "BREAKER-32A": 42.00,
}


def month_starts(start: date, count: int) -> list[date]:
    out = []
    y, m = start.year, start.month
    for _ in range(count):
        out.append(date(y, m, 1))
        m += 1
        if m > 12:
            m = 1
            y += 1
    return out


def _uid(namespace: str, key: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"feature-b-6m:{namespace}:{key}"))


def build_contracts() -> list[dict]:
    contracts = []
    for v in VENDORS:
        # BrightSpark left as draft for B1 HITL (inline edit + Confirm in Vendors space)
        is_draft = "Bright" in v["vendor_name"]
        contracts.append(
            {
                "organization_id": str(ORG_ID),
                "vendor_id": v["vendor_id"],
                "contract_ref": v["contract_ref"],
                "status": "draft" if is_draft else "confirmed",
                "confirmed_by": None if is_draft else str(PM_ID),
                "confirmed_at": None if is_draft else "2026-01-15T10:00:00+00:00",
                "extracted": {
                    "sla_response_p1_hours": 1,
                    "sla_response_p2_hours": 4,
                    "sla_response_p3_hours": 24,
                    "sla_response_p4_hours": 72,
                    "sla_completion_p1_hours": 4,
                    "sla_completion_p2_hours": 24,
                    "sla_completion_p3_hours": 72,
                    "sla_completion_p4_hours": 168,
                    "labour_day_rate": 380.0 if "Apex" in v["vendor_name"] else 350.0,
                    "overtime_rate": 570.0,
                    "call_out_rate": 165.0,
                    "payment_terms": "Net 30",
                    "parts_pricing_json": dict(PARTS),
                    "kpi_clauses_json": {
                        "sla_miss_penalty_pct": 2.5,
                        "first_fix_bonus_pct": 1.0,
                        "note": "Penalty applies when monthly SLA attainment < 95%",
                    },
                    "ppm_obligations_json": {
                        "frequency": "monthly",
                        "tolerance_days": 7,
                        "assets_in_scope": "plant_room_and_lifts",
                    },
                    "task_criticality_json": {
                        "L1": "Life safety / business-critical plant — failure stops operations",
                        "L2": "Important plant — degraded service within 24h",
                        "L3": "Non-critical / cosmetic",
                    },
                },
                "defaults_used": [],
                "field_sources": {
                    "sla_response_p1_hours": "contract",
                    "labour_day_rate": "contract",
                    "parts_pricing_json": "contract",
                    "payment_terms": "default",
                },
            }
        )
    return contracts


def build_assets() -> list[dict]:
    assets = []
    specs = [
        ("AHU-01", "L1", True, "Primary AHU — load dependent"),
        ("AHU-02", "L2", True, "Secondary AHU"),
        ("CHILLER-01", "L1", True, "Main chiller — high sub-meter"),
        ("BOILER-01", "L1", True, "Heating plant"),
        ("DB-MAIN", "L1", True, "Main LV board"),
        ("DB-FLOOR3", "L2", True, "Floor 3 distribution"),
        ("LIFT-A", "L1", True, "Passenger lift A"),
        ("LIFT-B", "L2", True, "Goods lift B"),
        ("PUMP-HW-01", "L2", True, "HWS circulation"),
        ("FCU-301", "L3", True, "Fan coil office 301"),
        ("FCU-302", "L3", False, "Fan coil office 302 — unapproved → treat as L2"),
        ("LIGHT-CORE", "L3", False, "Core lighting — pending PM criticality approval"),
        ("UPS-IT", "L1", True, "IT UPS"),
        ("PANEL-FIRE", "L1", False, "Fire panel — pending PM criticality approval"),
        ("DOOR-AUTO-1", "L2", True, "Auto door reception"),
    ]
    for code, crit, approved, rationale in specs:
        aid = _uid("asset", code)
        assets.append(
            {
                "asset_id": aid,
                "asset_code": code,
                "organization_id": str(ORG_ID),
                "criticality": crit if approved else "L2",
                "proposed_criticality": crit,
                "source": "system",
                "rationale": rationale,
                "approved": approved,
                "approved_by": str(PM_ID) if approved else None,
                "approved_at": "2026-01-20T12:00:00+00:00" if approved else None,
            }
        )
    return assets


# Vendor-scope accreditations (Feature A3). These drive the B2 accreditation
# component (15%) and the block_state floor — SafeLift's LOLER competence lapses
# on 2026-04-30, which is what blocks the vendor from 2026-05-01 and caps its
# overall score at 60. Without these rows the block state would be an unexplained
# hardcoded flag.
ACCREDITATIONS = {
    "APEX-MECH": [
        ("Gas Safe Register", "GAS_SAFE", "Gas Safe Register", "2025-09-01", "2026-08-31"),
        ("Public Liability Insurance", "PUBLIC_LIABILITY", "Zurich UK", "2025-11-01", "2026-10-31"),
        ("SafeContractor Accreditation", "SAFE_CONTRACTOR", "Alcumus", "2026-01-10", "2027-01-09"),
    ],
    "BRIGHT-ELEC": [
        ("NICEIC Approved Contractor", "NICEIC", "NICEIC", "2025-10-15", "2026-10-14"),
        ("Public Liability Insurance", "PUBLIC_LIABILITY", "AXA UK", "2025-12-01", "2026-11-30"),
    ],
    "SAFELIFT": [
        # Lapses mid-window → vendor blocked from 2026-05-01
        ("LOLER Competent Person", "LOLER", "LEIA", "2025-05-01", "2026-04-30"),
        ("Public Liability Insurance", "PUBLIC_LIABILITY", "Aviva", "2025-12-01", "2026-11-30"),
    ],
}


def build_accreditations() -> list[dict]:
    """Vendor-scope compliance certificates backing the A3 accreditation floor."""
    rows: list[dict] = []
    for v in VENDORS:
        for name, type_code, issuer, issued, expires in ACCREDITATIONS[v["vendor_code"]]:
            ref = f"ACC-{v['vendor_code']}-{type_code}"
            rows.append(
                {
                    "id": _uid("accreditation", ref),
                    "organization_id": str(ORG_ID),
                    "vendor_id": v["vendor_id"],
                    "vendor_name": v["vendor_name"],
                    "certificate_ref": ref,
                    "cert_type": name,
                    "certificate_type_code": type_code,
                    "certificate_number": f"{type_code}-{v['vendor_code']}-2026",
                    "issuer": issuer,
                    "issue_date": issued,
                    "expiry_date": expires,
                    "next_due_date": expires,
                    "cert_scope": "Vendor",
                    "country_code": "UK",
                    "result": "Pass",
                }
            )
    return rows


def build_weights() -> dict:
    return {
        "organization_id": str(ORG_ID),
        "sla_response_pct": 25,
        "sla_completion_pct": 25,
        "first_fix_pct": 20,
        "recall_pct": 15,
        "accreditation_pct": 15,
        "blocked_score_cap": 60,
        "cost_variance_alert_pct": 15,
        "cost_variance_job_count": 3,
        "invoice_flag_adversary_gbp": 500,
    }


def _sla_hours(priority: str, kind: str) -> float:
    table = {
        "response": {"P1": 1, "P2": 4, "P3": 24, "P4": 72},
        "completion": {"P1": 4, "P2": 24, "P3": 72, "P4": 168},
    }
    return float(table[kind][priority])


# Trade-aligned asset pools — a lift engineer never gets an AHU work order.
VENDOR_ASSET_POOLS = {
    "APEX-MECH": ["AHU-01", "AHU-02", "CHILLER-01", "BOILER-01", "PUMP-HW-01", "FCU-301", "FCU-302"],
    "BRIGHT-ELEC": ["DB-MAIN", "DB-FLOOR3", "LIGHT-CORE", "UPS-IT", "PANEL-FIRE", "DOOR-AUTO-1"],
    "SAFELIFT": ["LIFT-A", "LIFT-B"],
}

# Assets each vendor holds a PPM obligation against (B2 PPM compliance).
VENDOR_PPM_ASSETS = {
    "APEX-MECH": ["AHU-01", "AHU-02", "CHILLER-01", "BOILER-01", "PUMP-HW-01"],
    "BRIGHT-ELEC": ["DB-MAIN", "DB-FLOOR3", "UPS-IT"],
    "SAFELIFT": ["LIFT-A", "LIFT-B"],
}

# Performance profile per vendor: response/completion miss rates, first-fix, overrun, recall.
VENDOR_PROFILES = {
    "APEX-MECH": {
        "miss_response": 0.12,
        "miss_complete": 0.10,
        "first_fix": 0.85,
        "overrun": 0.18,
        "recall": 0.06,
    },
    "BRIGHT-ELEC": {
        "miss_response": 0.20,
        "miss_complete": 0.18,
        "first_fix": 0.75,
        "overrun": 0.25,
        "recall": 0.12,
    },
    "SAFELIFT": {
        "miss_response": 0.30,
        "miss_complete": 0.28,
        "first_fix": 0.65,
        "overrun": 0.35,
        "recall": 0.20,
    },
}

RECALL_WINDOW_DAYS = 30


def build_work_orders(months: list[date], assets: list[dict]) -> list[dict]:
    """~28 completed reactive WOs per vendor per month.

    Recalls are *genuine*: a WO is only marked as a recall when the same vendor
    already completed a job on the same asset within RECALL_WINDOW_DAYS, and the
    originating WO code is recorded on `recall_of_wo_code`. The B2 recall-rate
    component is therefore reproducible from the raw records rather than asserted.
    """
    rows: list[dict] = []
    asset_by_code = {a["asset_code"]: a["asset_id"] for a in assets}
    # (vendor_code, asset_code) -> (last completion datetime, wo_code)
    last_completion: dict[tuple[str, str], tuple[datetime, str]] = {}
    wo_seq = 1000

    for month in months:
        days_in_month = monthrange(month.year, month.month)[1]
        for v in VENDORS:
            profile = VENDOR_PROFILES[v["vendor_code"]]
            pool = VENDOR_ASSET_POOLS[v["vendor_code"]]

            for i in range(28):
                day = 1 + (i % days_in_month)
                hour = 7 + (i % 10)
                reported = datetime(month.year, month.month, day, hour, 0, tzinfo=timezone.utc)
                priority = PRIORITIES[i % 4]
                asset_code = pool[i % len(pool)]
                asset_id = asset_by_code[asset_code]

                resp_h = _sla_hours(priority, "response")
                comp_h = _sla_hours(priority, "completion")

                miss_r = RNG.random() < profile["miss_response"]
                miss_c = RNG.random() < profile["miss_complete"]
                attend_delay = resp_h * (1.4 if miss_r else RNG.uniform(0.3, 0.85))
                complete_delay = comp_h * (1.35 if miss_c else RNG.uniform(0.4, 0.9))
                attended = reported + timedelta(hours=attend_delay)
                completed = reported + timedelta(hours=complete_delay)
                if completed <= attended:
                    completed = attended + timedelta(hours=1)

                # Recall only counts as a repeat fault inside the 30-day window.
                key = (v["vendor_code"], asset_code)
                prior = last_completion.get(key)
                recall = False
                recall_of = None
                if prior is not None:
                    prior_dt, prior_code = prior
                    # The earlier job must have closed before this one was raised,
                    # and inside the recall window, for this to be a repeat fault.
                    gap = reported - prior_dt
                    within_window = timedelta(0) <= gap <= timedelta(days=RECALL_WINDOW_DAYS)
                    if within_window and RNG.random() < profile["recall"]:
                        recall = True
                        recall_of = prior_code

                first_fix = False if recall else RNG.random() < profile["first_fix"]

                estimated = round(RNG.uniform(180, 900), 2)
                if RNG.random() < profile["overrun"]:
                    # Systematic overrun (>15%) drives the cost variance alert
                    actual = round(estimated * RNG.uniform(1.18, 1.45), 2)
                else:
                    actual = round(estimated * RNG.uniform(0.88, 1.12), 2)

                attendance_hours = max(0.5, round((completed - attended).total_seconds() / 3600, 2))
                wo_seq += 1
                wo_code = f"WO-{v['vendor_code'][:4]}-{month.strftime('%y%m')}-{wo_seq}"
                last_completion[key] = (completed, wo_code)

                rows.append(
                    {
                        "id": _uid("wo", wo_code),
                        "work_order_id": _uid("wo", wo_code),
                        "wo_code": wo_code,
                        "organization_id": str(ORG_ID),
                        "vendor_id": v["vendor_id"],
                        "vendor_name": v["vendor_name"],
                        "contract_ref": v["contract_ref"],
                        "score_month": month.isoformat(),
                        "priority": priority,
                        "status": "Completed",
                        "wo_type": "Reactive",
                        "maintenance_type": "Reactive",
                        "title": f"{priority} reactive attendance — {asset_code}",
                        "reported_at": reported.isoformat(),
                        "created_at": reported.isoformat(),
                        "attended_at": attended.isoformat(),
                        "response_at": attended.isoformat(),
                        "completed_at": completed.isoformat(),
                        "completion_date": completed.isoformat(),
                        "asset_id": asset_id,
                        "asset_code": asset_code,
                        "first_fix": first_fix,
                        "return_visit": not first_fix,
                        "recall": recall,
                        "recall_of_wo_code": recall_of,
                        "cost_actual": actual,
                        "actual_cost": actual,
                        "cost_estimated": estimated,
                        "estimated_cost": estimated,
                        "attendance_hours": attendance_hours,
                        "labour_hours": attendance_hours,
                        "duration_hours": attendance_hours,
                        "parts_cost": round(RNG.choice(list(PARTS.values())) * RNG.uniform(0.9, 1.2), 2),
                        "part_code": RNG.choice(list(PARTS.keys())),
                    }
                )
    return rows


PPM_TOLERANCE_DAYS = 7


def build_ppm_visits(months: list[date], assets: list[dict]) -> list[dict]:
    """Monthly PPM obligation per vendor, against that vendor's own plant.

    Each visit carries the wo_code of the PPM work order that delivered it, so
    `ppm_visits.work_order_id` resolves to a real `work_orders` row rather than
    floating free. A small share of visits are missed outright (no completion)
    so PPM compliance is not simply a lateness measure.
    """
    visits = []
    asset_by_code = {a["asset_code"]: a["asset_id"] for a in assets}
    for month in months:
        for v in VENDORS:
            for i, asset_code in enumerate(VENDOR_PPM_ASSETS[v["vendor_code"]]):
                sched_day = min(3 + i * 5, 28)
                scheduled = date(month.year, month.month, sched_day)
                # Apex mostly on-time; BrightSpark mixed; SafeLift worst
                if v["vendor_code"] == "APEX-MECH":
                    delta = RNG.choice([-2, -1, 0, 1, 2, 3, 5])
                    missed = RNG.random() < 0.02
                elif v["vendor_code"] == "BRIGHT-ELEC":
                    delta = RNG.choice([-3, 0, 2, 5, 8, 10, 12])
                    missed = RNG.random() < 0.06
                else:
                    delta = RNG.choice([0, 4, 8, 10, 14, 16])
                    missed = RNG.random() < 0.12

                ppm_ref = f"PPM-{v['vendor_code']}-{month.strftime('%Y%m')}-{asset_code}"
                wo_code = f"WO-PPM-{v['vendor_code'][:4]}-{month.strftime('%y%m')}-{asset_code}"
                completed = None if missed else scheduled + timedelta(days=delta)
                visits.append(
                    {
                        "ppm_ref": ppm_ref,
                        "id": _uid("ppm", ppm_ref),
                        "vendor_id": v["vendor_id"],
                        "vendor_name": v["vendor_name"],
                        "contract_ref": v["contract_ref"],
                        "organization_id": str(ORG_ID),
                        "score_month": month.isoformat(),
                        "asset_id": asset_by_code[asset_code],
                        "asset_code": asset_code,
                        "frequency": "monthly",
                        "tolerance_days": PPM_TOLERANCE_DAYS,
                        "scheduled_date": scheduled.isoformat(),
                        "completed_date": completed.isoformat() if completed else None,
                        "within_tolerance": (
                            False if completed is None else abs(delta) <= PPM_TOLERANCE_DAYS
                        ),
                        "status": "missed" if completed is None else "completed",
                        # PPM work order that delivered the visit
                        "wo_code": None if completed is None else wo_code,
                        "work_order_id": None if completed is None else _uid("wo", wo_code),
                    }
                )
    return visits


def build_ppm_work_orders(ppm_visits: list[dict]) -> list[dict]:
    """A completed PPM work order for every PPM visit that was actually attended."""
    rows: list[dict] = []
    for visit in ppm_visits:
        if not visit["wo_code"]:
            continue
        completed_d = date.fromisoformat(visit["completed_date"])
        # PPM crews attend on the morning of the visit and close the same day.
        attended = datetime(
            completed_d.year, completed_d.month, completed_d.day, 8, 0, tzinfo=timezone.utc
        )
        completed = attended + timedelta(hours=RNG.uniform(1.5, 4.0))
        hours = round((completed - attended).total_seconds() / 3600, 2)
        estimated = round(RNG.uniform(220, 480), 2)
        actual = round(estimated * RNG.uniform(0.92, 1.14), 2)
        rows.append(
            {
                "id": visit["work_order_id"],
                "work_order_id": visit["work_order_id"],
                "wo_code": visit["wo_code"],
                "organization_id": visit["organization_id"],
                "vendor_id": visit["vendor_id"],
                "vendor_name": visit["vendor_name"],
                "contract_ref": visit["contract_ref"],
                "score_month": visit["score_month"],
                "priority": "P4",
                "status": "Completed",
                "wo_type": "PPM",
                "maintenance_type": "Planned",
                "title": f"Monthly PPM — {visit['asset_code']}",
                "reported_at": attended.isoformat(),
                "created_at": attended.isoformat(),
                "attended_at": attended.isoformat(),
                "response_at": attended.isoformat(),
                "completed_at": completed.isoformat(),
                "completion_date": completed.isoformat(),
                "asset_id": visit["asset_id"],
                "asset_code": visit["asset_code"],
                "first_fix": True,
                "return_visit": False,
                "recall": False,
                "recall_of_wo_code": None,
                "cost_actual": actual,
                "actual_cost": actual,
                "cost_estimated": estimated,
                "estimated_cost": estimated,
                "attendance_hours": hours,
                "labour_hours": hours,
                "duration_hours": hours,
                "parts_cost": 0.0,
                "part_code": None,
                "ppm_ref": visit["ppm_ref"],
            }
        )
    return rows


def build_invoices(months: list[date], work_orders: list[dict]) -> list[dict]:
    invoices = []
    by_vendor_month: dict[tuple[str, str], list[dict]] = {}
    for wo in work_orders:
        key = (wo["vendor_id"], wo["score_month"])
        by_vendor_month.setdefault(key, []).append(wo)

    for month in months:
        for v in VENDORS:
            wos = by_vendor_month.get((v["vendor_id"], month.isoformat()), [])
            if not wos:
                continue
            # Two invoices per vendor per month
            for batch_idx, chunk in enumerate((wos[0:10], wos[10:20])):
                if not chunk:
                    continue
                lines = []
                wo_payload = []
                for j, wo in enumerate(chunk):
                    day_rate = 380.0 if "Apex" in v["vendor_name"] else 350.0
                    hourly = round(day_rate / 8.0, 2)
                    hours = float(wo["attendance_hours"])
                    # Inject discrepancies
                    labour_hours = hours
                    labour_rate = hourly
                    parts_cost = float(wo["parts_cost"])
                    part_code = wo["part_code"]
                    flag_kind = None
                    if j % 5 == 0:
                        # Labour hours > ±10%
                        labour_hours = round(hours * 1.18, 2)
                        flag_kind = "labour_hours"
                    elif j % 7 == 0:
                        # Rate exceeds contract
                        labour_rate = round(hourly * 1.25, 2)
                        flag_kind = "labour_rate"
                    elif j % 9 == 0:
                        # Parts > ±5% vs framework
                        parts_cost = round(PARTS[part_code] * 1.12, 2)
                        flag_kind = "parts_cost"
                    elif j == 1 and batch_idx == 0:
                        # Large £ flag for adversary gate (>£500)
                        labour_hours = round(hours + 20, 2)
                        flag_kind = "large_delta"

                    line_total = round(labour_hours * labour_rate + parts_cost, 2)
                    lines.append(
                        {
                            "line_id": f"{batch_idx+1}-{j+1}",
                            "wo_code": wo["wo_code"],
                            "labour_hours": labour_hours,
                            "hours": labour_hours,
                            "labour_rate": labour_rate,
                            "hourly_rate": labour_rate,
                            "parts_cost": parts_cost,
                            "parts_amount": parts_cost,
                            "part_code": part_code,
                            "amount": line_total,
                            "line_total": line_total,
                            "_injected_flag": flag_kind,
                        }
                    )
                    wo_payload.append(
                        {
                            "wo_code": wo["wo_code"],
                            "status": "Completed",
                            "completed_at": wo["completed_at"],
                            "attendance_hours": wo["attendance_hours"],
                            "labour_hours": wo["labour_hours"],
                            "duration_hours": wo["duration_hours"],
                            "parts_cost": PARTS.get(part_code, wo["parts_cost"]),
                            "parts_estimated": PARTS.get(part_code, wo["parts_cost"]),
                            "asset_id": wo["asset_id"],
                        }
                    )

                inv_ref = f"INV-{v['vendor_code']}-{month.strftime('%Y%m')}-{batch_idx+1:02d}"
                invoices.append(
                    {
                        "invoice_ref": inv_ref,
                        "vendor_id": v["vendor_id"],
                        "vendor_name": v["vendor_name"],
                        "organization_id": str(ORG_ID),
                        "score_month": month.isoformat(),
                        "labour_day_rate": 380.0 if "Apex" in v["vendor_name"] else 350.0,
                        "parts_pricing_json": dict(PARTS),
                        "lines": lines,
                        "work_orders": wo_payload,
                    }
                )
    return invoices


def build_score_batches(work_orders: list[dict], contracts: list[dict]) -> list[dict]:
    """Payloads for POST /api/contract-performance/score/work-orders.

    Only vendors with confirmed contracts are scored (draft BrightSpark is B1 HITL only).
    """
    confirmed_vendors = {
        c["vendor_id"] for c in contracts if c.get("status") == "confirmed"
    }
    batches: dict[tuple[str, str], list[dict]] = {}
    for wo in work_orders:
        if wo["vendor_id"] not in confirmed_vendors:
            continue
        key = (wo["vendor_id"], wo["score_month"])
        batches.setdefault(key, []).append(wo)
    out = []
    for (vendor_id, score_month), wos in sorted(batches.items()):
        out.append(
            {
                "vendor_id": vendor_id,
                "organization_id": str(ORG_ID),
                "score_month": score_month,
                "work_orders": wos,
            }
        )
    return out


def build_scorecard_requests(
    months: list[date], ppm_visits: list[dict], contracts: list[dict]
) -> list[dict]:
    confirmed = {c["vendor_id"] for c in contracts if c.get("status") == "confirmed"}
    out = []
    for month in months:
        for v in VENDORS:
            if v["vendor_id"] not in confirmed:
                continue
            visits = [
                {"scheduled_date": p["scheduled_date"], "completed_date": p["completed_date"]}
                for p in ppm_visits
                if p["vendor_id"] == v["vendor_id"] and p["score_month"] == month.isoformat()
            ]
            out.append(
                {
                    "vendor_id": v["vendor_id"],
                    "organization_id": str(ORG_ID),
                    "score_month": month.isoformat(),
                    "ppm_visits": visits,
                }
            )
    return out


def write_csv(path: Path, rows: list[dict], fieldnames: list[str] | None = None) -> None:
    if not rows:
        try:
            path.write_text("", encoding="utf-8")
        except PermissionError:
            alt = path.with_suffix(path.suffix + ".new")
            alt.write_text("", encoding="utf-8")
            print(f"WARN: locked {path.name}; wrote {alt.name}")
        return
    fields = fieldnames or list(rows[0].keys())
    target = path
    try:
        f = path.open("w", newline="", encoding="utf-8")
    except PermissionError:
        target = path.with_suffix(path.suffix + ".new")
        print(f"WARN: locked {path.name}; writing {target.name}")
        f = target.open("w", newline="", encoding="utf-8")
    with f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            flat = {k: (json.dumps(v) if isinstance(v, (dict, list)) else v) for k, v in r.items()}
            w.writerow({k: flat.get(k) for k in fields})


def generate(out_dir: Path, start: date, months_count: int) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    months = month_starts(start, months_count)
    contracts = build_contracts()
    assets = build_assets()
    weights = build_weights()
    accreditations = build_accreditations()
    reactive_work_orders = build_work_orders(months, assets)
    ppm = build_ppm_visits(months, assets)
    ppm_work_orders = build_ppm_work_orders(ppm)
    # Reactive jobs are what B2 scores against SLA; PPM jobs exist so every PPM
    # visit resolves to a real work order and the PPM compliance rate is auditable.
    work_orders = reactive_work_orders + ppm_work_orders
    invoices = build_invoices(months, reactive_work_orders)
    score_batches = build_score_batches(reactive_work_orders, contracts)
    scorecards = build_scorecard_requests(months, ppm, contracts)

    manifest = {
        "feature": "B",
        "name": "contract_performance_6m",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "organization_id": str(ORG_ID),
        "score_months": [m.isoformat() for m in months],
        "vendors": VENDORS,
        "counts": {
            "vendors": len(VENDORS),
            "accreditations": len(accreditations),
            "contracts": len(contracts),
            "assets": len(assets),
            "work_orders": len(work_orders),
            "work_orders_reactive": len(reactive_work_orders),
            "work_orders_ppm": len(ppm_work_orders),
            "ppm_visits": len(ppm),
            "invoices": len(invoices),
            "invoice_lines": sum(len(i["lines"]) for i in invoices),
            "score_batches": len(score_batches),
            "scorecard_requests": len(scorecards),
        },
        "test_scenarios": [
            "B1 confirmed SLA params + defaults labelling",
            "B1 asset criticality L1/L2/L3 with one unapproved → L2",
            "B2 Apex strong SLA vs Bright mid vs SafeLift weak",
            "B2 SafeLift block_state=Blocked → overall score capped at 60",
            "B2 cost variance >15% on 3+ jobs → Approvals alert",
            "B2 PPM ±7 day compliance mix",
            "B3 matched vs flagged invoice lines; flags >£500 for adversary",
            "B3 admin-editable weights in vendor_score_weight_config",
        ],
        "load_order": [
            "1. Ensure org + vendors exist (vendors.json)",
            "2. PUT /api/contract-performance/admin/weights",
            "3. POST /contracts/ingest + /contracts/{id}/confirm per contract",
            "4. POST /asset-criticality (+ approve)",
            "5. POST /score/work-orders per score_batches/*.json",
            "6. POST /invoices/verify per invoices/*.json",
            "7. POST /scorecards/monthly per scorecard_requests.json",
        ],
    }

    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (out_dir / "vendors.json").write_text(
        json.dumps({"organization_id": str(ORG_ID), "vendors": VENDORS}, indent=2),
        encoding="utf-8",
    )
    (out_dir / "contracts.json").write_text(json.dumps(contracts, indent=2), encoding="utf-8")
    (out_dir / "assets_criticality.json").write_text(json.dumps(assets, indent=2), encoding="utf-8")
    (out_dir / "weights.json").write_text(json.dumps(weights, indent=2), encoding="utf-8")
    (out_dir / "accreditations.json").write_text(
        json.dumps(accreditations, indent=2), encoding="utf-8"
    )
    (out_dir / "work_orders_6m.json").write_text(json.dumps(work_orders, indent=2), encoding="utf-8")
    (out_dir / "ppm_work_orders_6m.json").write_text(
        json.dumps(ppm_work_orders, indent=2), encoding="utf-8"
    )
    (out_dir / "ppm_visits_6m.json").write_text(json.dumps(ppm, indent=2), encoding="utf-8")
    (out_dir / "invoices_6m.json").write_text(json.dumps(invoices, indent=2), encoding="utf-8")
    (out_dir / "score_batches.json").write_text(json.dumps(score_batches, indent=2), encoding="utf-8")
    (out_dir / "scorecard_requests.json").write_text(json.dumps(scorecards, indent=2), encoding="utf-8")

    # Flat CSVs for spreadsheet review
    write_csv(
        out_dir / "work_orders_6m.csv",
        work_orders,
        [
            "wo_code",
            "vendor_name",
            "score_month",
            "wo_type",
            "priority",
            "asset_code",
            "asset_id",
            "reported_at",
            "attended_at",
            "completed_at",
            "first_fix",
            "recall",
            "recall_of_wo_code",
            "cost_estimated",
            "cost_actual",
            "attendance_hours",
            "part_code",
            "parts_cost",
        ],
    )
    write_csv(
        out_dir / "ppm_visits_6m.csv",
        ppm,
        [
            "ppm_ref",
            "vendor_name",
            "score_month",
            "asset_code",
            "frequency",
            "scheduled_date",
            "completed_date",
            "tolerance_days",
            "within_tolerance",
            "status",
            "wo_code",
        ],
    )
    write_csv(
        out_dir / "assets_criticality.csv",
        assets,
        [
            "asset_code",
            "asset_id",
            "criticality",
            "proposed_criticality",
            "approved",
            "rationale",
        ],
    )

    # Per-month score batch files for easy API looping
    batch_dir = out_dir / "score_batches"
    batch_dir.mkdir(exist_ok=True)
    for b in score_batches:
        name = f"{b['score_month']}_{b['vendor_id'][-2:]}.json"
        (batch_dir / name).write_text(json.dumps(b, indent=2), encoding="utf-8")

    inv_dir = out_dir / "invoices"
    inv_dir.mkdir(exist_ok=True)
    for inv in invoices:
        (inv_dir / f"{inv['invoice_ref']}.json").write_text(
            json.dumps(inv, indent=2), encoding="utf-8"
        )

    # CSVs shaped for Phase 1 UDR / schema-mapper migration upload
    mig = out_dir / "migration_csvs"
    mig.mkdir(exist_ok=True)
    write_csv(
        mig / "vendors.csv",
        [
            {
                "vendor_id": v["vendor_id"],
                "vendor_name": v["vendor_name"],
                "vendor_code": v["vendor_code"],
                "status": "active",
                "block_state": v["block_state"],
                "trade": v["trade"],
                "organization_id": str(ORG_ID),
            }
            for v in VENDORS
        ],
    )
    write_csv(
        mig / "assets.csv",
        [
            {
                "asset_id": a["asset_id"],
                "asset_code": a["asset_code"],
                "asset_name": a["asset_code"],
                "criticality": a["proposed_criticality"],
                "status": "active",
                "organization_id": str(ORG_ID),
                "notes": a["rationale"],
            }
            for a in assets
        ],
    )
    vendor_by_id = {v["vendor_id"]: v for v in VENDORS}
    wo_mig_rows = []
    for wo in work_orders:
        v = vendor_by_id[wo["vendor_id"]]
        wo_mig_rows.append(
            {
                "wo_id": wo["wo_code"],
                "wo_code": wo["wo_code"],
                "workorder_ref": wo["wo_code"],
                "asset_id": wo["asset_id"],  # UUID — required for Feature B criticality
                "asset_code": wo["asset_code"],
                "vendor_id": wo["vendor_id"],
                "vendor_name": wo["vendor_name"],
                "contract_ref": wo["contract_ref"],
                "organization_id": str(ORG_ID),
                "wo_title": wo["title"],
                "wo_type": wo["wo_type"],
                "maintenance_type": wo["maintenance_type"],
                "priority": wo["priority"],
                "status": wo["status"],
                "created_date": wo["reported_at"][:10],
                "reported_at": wo["reported_at"],
                "attended_at": wo["attended_at"],
                "completed_at": wo["completed_at"],
                "due_date": wo["completed_at"][:10],
                "first_fix": wo["first_fix"],
                "recall": wo["recall"],
                "recall_of_wo_code": wo["recall_of_wo_code"],
                "estimated_cost": wo["cost_estimated"],
                "actual_cost": wo["cost_actual"],
                "labour_hours": wo["labour_hours"],
                "part_code": wo["part_code"],
                "parts_cost": wo["parts_cost"],
                "technician": v["vendor_code"],
                "notes": f"score_month={wo['score_month']}",
            }
        )
    write_csv(mig / "work_orders.csv", wo_mig_rows)
    write_csv(
        mig / "ppm_visits.csv",
        [
            {
                "ppm_ref": p["ppm_ref"],
                "vendor_id": p["vendor_id"],
                "vendor_name": p["vendor_name"],
                "asset_id": p["asset_id"],
                "asset_code": p["asset_code"],
                "frequency": p["frequency"],
                "scheduled_date": p["scheduled_date"],
                "completed_date": p["completed_date"],
                "tolerance_days": p["tolerance_days"],
                "status": p["status"],
                "wo_code": p["wo_code"],
                "organization_id": p["organization_id"],
            }
            for p in ppm
        ],
    )
    write_csv(
        mig / "accreditations.csv",
        [
            {
                "certificate_ref": a["certificate_ref"],
                "vendor_id": a["vendor_id"],
                "vendor_name": a["vendor_name"],
                "cert_type": a["cert_type"],
                "certificate_number": a["certificate_number"],
                "issuer": a["issuer"],
                "issue_date": a["issue_date"],
                "expiry_date": a["expiry_date"],
                "cert_scope": a["cert_scope"],
                "organization_id": a["organization_id"],
            }
            for a in accreditations
        ],
    )
    write_csv(
        mig / "contracts.csv",
        [
            {
                "contract_ref": c["contract_ref"],
                "vendor_id": c["vendor_id"],
                "vendor_name": next(
                    v["vendor_name"] for v in VENDORS if v["vendor_id"] == c["vendor_id"]
                ),
                "status": c["status"],
                "labour_day_rate": c["extracted"]["labour_day_rate"],
                "sla_response_p1_hours": c["extracted"]["sla_response_p1_hours"],
                "sla_completion_p1_hours": c["extracted"]["sla_completion_p1_hours"],
                "payment_terms": c["extracted"]["payment_terms"],
                "organization_id": c["organization_id"],
            }
            for c in contracts
        ],
    )
    write_csv(
        mig / "invoices.csv",
        [
            {
                "invoice_ref": inv["invoice_ref"],
                "vendor_id": inv["vendor_id"],
                "vendor_name": inv["vendor_name"],
                "score_month": inv["score_month"],
                "line_count": len(inv["lines"]),
                "labour_day_rate": inv["labour_day_rate"],
                "organization_id": inv["organization_id"],
            }
            for inv in invoices
        ],
    )
    (mig / "README.md").write_text(
        """# Migration CSVs (Phase 1 UDR / Schema Mapper)

Upload these via Single Door Orchestrator (`/ai`) — Feature Option **CSV migration**:

1. `vendors.csv`
2. `assets.csv`
3. `work_orders.csv`
4. `contracts.csv` (metadata only — Feature B SLA params still need ops-intel ingest)
5. `invoices.csv` (index only — line verify needs ops-intel `/invoices/verify`)

Approve table/column mapping through the migration gates, then complete UDR write.

## After CSV migration — Feature B score from UDR

1. Apply WO scoring columns (once):
   `migrations/feature_b_udr_wo_columns.sql` + `phase2_contract_performance.sql`
2. Complete UDR write for these CSVs via `/ai` migration.
3. Confirm contracts (B1) in `/vendors` dashboard.
4. Score migrated WOs (no hand-crafted batches needed):

```bash
curl -X POST http://localhost:8009/api/contract-performance/score/from-udr \\
  -H "Content-Type: application/json" \\
  -d '{"all_buckets": true, "generate_scorecard": true}'
```

Or click **Score from UDR** on `/vendors` (B2 tab).

| Dashboard | What you see |
|-----------|----------------|
| **B1** Contract ingestion | BrightSpark contract left **draft**; confirm in Vendors space. |
| **B2** Vendor scoring | Scorecards built from migrated `plenum_cafm.work_orders`. |
| **B3** Invoice verification | Invoice verify / Approvals rail. |
""",
        encoding="utf-8",
    )

    readme = f"""# Feature B — 6-month contract performance test data

Generated for org `{ORG_ID}` covering score months:
{chr(10).join('- ' + m.isoformat() for m in months)}

## Vendors
| Vendor | Profile | Block |
|--------|---------|-------|
| Apex Mechanical | Strong SLA / high first-fix | Clear |
| BrightSpark Electrical | Mid performance / more overruns | Clear (contract **draft** for B1 HITL) |
| SafeLift Engineering | Weaker SLA; blocked from May 2026 | Blocked (cap 60) |

## Counts
- Work orders: **{len(work_orders)}** (~28 × 3 vendors × 6 months)
- PPM visits: **{len(ppm)}**
- Invoices: **{len(invoices)}**
- Assets: **{len(assets)}** (one unapproved criticality for B1)

## End-to-end (CSV migration → Feature B dashboards)

### 1) Apply Feature B schema (creates contract-performance tables)
```bash
# Requires DATABASE_URL / DB_URL
psql "$DATABASE_URL" -f migrations/phase2_contract_performance.sql
# or let the seeder apply it:
python scripts/seed_feature_b_6m.py --base-url http://localhost:8009 --dry-run
# (real seed applies schema when DATABASE_URL is set; use --no-schema to skip)
```

### 2) CSV migration (Phase 1 UDR) — Feature Option CSV
Upload folder `migration_csvs/` via `/ai` Single Door:
`vendors.csv`, `assets.csv`, `work_orders.csv`, `contracts.csv`, `invoices.csv`

### 3) Seed Feature B engine (B1/B2/B3 APIs)
```bash
python scripts/seed_feature_b_6m.py --base-url http://localhost:8009 --data {out_dir.as_posix()} --skip-vendor-sql
```

### 4) Open Feature B dashboards
- UI: `/ai?space=vendors` (Vendors Saved Space)
- **B1** — draft BrightSpark contract + pending asset criticality
- **B2** — scorecards / KPIs for Apex + SafeLift
- **B3** — invoice verification via Approvals / chat tools

## Load via API only (skip CSV if UDR already has core tables)
```bash
python scripts/seed_feature_b_6m.py --base-url http://localhost:8009 --data {out_dir.as_posix()}
```

## Manual API sequence
1. `PUT /api/contract-performance/admin/weights` ← `weights.json`
2. For each contract in `contracts.json`: ingest; confirm only if `status=confirmed`
3. For each asset: `POST /asset-criticality` → approve if `approved`
4. For each item in `score_batches.json`: `POST /score/work-orders`
5. For each invoice (confirmed vendors): `POST /invoices/verify`
6. For each scorecard request (confirmed vendors): `POST /scorecards/monthly`
"""
    (out_dir / "README.md").write_text(readme, encoding="utf-8")
    return manifest


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "testdata" / "feature_b_6m",
    )
    ap.add_argument("--start", default="2026-02-01", help="First score month YYYY-MM-DD")
    ap.add_argument("--months", type=int, default=6)
    args = ap.parse_args()
    start = date.fromisoformat(args.start)
    manifest = generate(args.out, start, args.months)
    print(json.dumps(manifest["counts"], indent=2))
    print(f"Wrote dataset -> {args.out}")


if __name__ == "__main__":
    main()
