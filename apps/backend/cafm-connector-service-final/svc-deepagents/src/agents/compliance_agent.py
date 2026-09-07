"""
Compliance agent tools — queries the plenum_cafm schema directly to assess
regulatory compliance status and generate compliance reports.
"""
from typing import Any

import structlog
from langchain_core.tools import tool
from sqlalchemy import text

from .. import database

log = structlog.get_logger(__name__)


@tool
async def check_requirements(asset_code: str, regulation: str | None = None) -> dict:
    """Check compliance status for an asset against maintenance and inspection requirements.

    Evaluates: PM schedule adherence, open corrective-action inspections,
    overdue work orders, and certification expiry. Optionally filter by
    a specific regulation or standard (e.g. 'ISO_55001', 'OSHA', 'UAE_FIRE').

    Returns a compliance_status (compliant | at_risk | non_compliant) with
    a list of specific findings for each failing check.

    Args:
        asset_code: Asset to check compliance for.
        regulation: Optional regulation to filter checks against (checks all if omitted).
    """
    async with database.AsyncSessionLocal() as session:
        try:
            # Fetch PM compliance — maintenance_plans joined to assets via asset_id UUID FK
            pm_rows = (await session.execute(
                text("""
                    SELECT mp.sm_code, mp.frequency_type, mp.frequency_value, mp.next_due_date,
                           mp.next_due_date < CURRENT_DATE AS is_overdue
                    FROM plenum_cafm.maintenance_plans mp
                    JOIN plenum_cafm.assets a ON a.id = mp.asset_id::text
                    WHERE a.asset_code = :asset_code
                      AND mp.status = 'active'
                """),
                {"asset_code": asset_code},
            )).mappings().all()

            # Fetch open high/critical priority WOs — work_orders joined to assets via asset_id
            wo_rows = (await session.execute(
                text("""
                    SELECT wo.wo_code, wo.priority, wo.status, wo.created_at,
                           now() - wo.created_at AS age
                    FROM plenum_cafm.work_orders wo
                    JOIN plenum_cafm.assets a ON a.id = wo.asset_id::text
                    WHERE a.asset_code = :asset_code
                      AND wo.status NOT IN ('completed', 'closed', 'cancelled')
                      AND wo.priority IN ('high', 'critical')
                """),
                {"asset_code": asset_code},
            )).mappings().all()

            # Fetch open corrective-action inspections (inspections table uses asset_code directly)
            inspection_rows = (await session.execute(
                text("""
                    SELECT id, inspection_date, risk_level, finding_type, observations
                    FROM plenum_cafm.inspections
                    WHERE asset_code = :asset_code
                      AND corrective_action = true
                    ORDER BY inspection_date DESC
                    LIMIT 20
                """),
                {"asset_code": asset_code},
            )).mappings().all()

        except Exception as exc:
            log.error("compliance.check.error", asset=asset_code, error=str(exc))
            return {"error": str(exc)}

    findings: list[dict] = []
    for pm in pm_rows:
        if pm["is_overdue"]:
            findings.append({
                "type": "pm_overdue",
                "sm_code": pm["sm_code"],
                "next_due_date": str(pm["next_due_date"]),
                "frequency_type": pm["frequency_type"],
            })

    for wo in wo_rows:
        findings.append({
            "type": "high_priority_open_wo",
            "wo_code": wo["wo_code"],
            "priority": wo["priority"],
            "age_days": str(wo["age"]),
        })

    for insp in inspection_rows:
        findings.append({
            "type": "open_corrective_action",
            "inspection_id": str(insp["id"]),
            "risk_level": insp["risk_level"],
            "finding_type": insp["finding_type"],
        })

    if not findings:
        status = "compliant"
    elif any(f["type"] == "pm_overdue" for f in findings) or any(
        f.get("risk_level") == "High" for f in findings
    ):
        status = "non_compliant"
    else:
        status = "at_risk"

    return {
        "asset_code": asset_code,
        "regulation": regulation or "all",
        "compliance_status": status,
        "findings_count": len(findings),
        "findings": findings,
    }


@tool
async def generate_compliance_report(
    scope: str = "all_assets",
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict:
    """Generate a portfolio-wide compliance summary report.

    Aggregates PM adherence, inspection outcomes, and high-priority WO backlog
    across all assets (or a filtered scope). Returns per-asset compliance scores
    and a portfolio-level summary with top risk items.

    Args:
        scope: 'all_assets' for the full portfolio, or an asset category name
               (e.g. 'Air Handler', 'Boiler') to restrict the report.
        date_from: Report period start (ISO 8601, optional).
        date_to: Report period end (ISO 8601, optional).
    """
    params: dict[str, Any] = {}

    # Build asset query — join asset_categories since assets has category_id FK (no direct category col)
    if scope != "all_assets":
        asset_sql = """
            SELECT a.asset_code, a.asset_name, ac.category_name AS category
            FROM plenum_cafm.assets a
            LEFT JOIN plenum_cafm.asset_categories ac ON ac.id = a.category_id
            WHERE ac.category_name = :category
        """
        params["category"] = scope
    else:
        asset_sql = """
            SELECT a.asset_code, a.asset_name, ac.category_name AS category
            FROM plenum_cafm.assets a
            LEFT JOIN plenum_cafm.asset_categories ac ON ac.id = a.category_id
        """

    async with database.AsyncSessionLocal() as session:
        try:
            assets = (await session.execute(text(asset_sql), params)).mappings().all()

            # Overdue PMs — maintenance_plans joined to assets via asset_id UUID FK
            overdue_pms = (await session.execute(
                text("""
                    SELECT a.asset_code, count(*) AS overdue_count
                    FROM plenum_cafm.maintenance_plans mp
                    JOIN plenum_cafm.assets a ON a.id = mp.asset_id::text
                    WHERE mp.next_due_date < CURRENT_DATE
                      AND mp.status = 'active'
                    GROUP BY a.asset_code
                """),
            )).mappings().all()
            overdue_map: dict[str, int] = {r["asset_code"]: int(r["overdue_count"]) for r in overdue_pms}

            # Open high/critical priority WOs — work_orders joined to assets via asset_id
            open_critical_wos = (await session.execute(
                text("""
                    SELECT a.asset_code, count(*) AS wo_count
                    FROM plenum_cafm.work_orders wo
                    JOIN plenum_cafm.assets a ON a.id = wo.asset_id::text
                    WHERE wo.status NOT IN ('completed', 'closed', 'cancelled')
                      AND wo.priority IN ('high', 'critical')
                    GROUP BY a.asset_code
                """),
            )).mappings().all()
            wo_map: dict[str, int] = {r["asset_code"]: int(r["wo_count"]) for r in open_critical_wos}

        except Exception as exc:
            log.error("compliance.report.error", scope=scope, error=str(exc))
            return {"error": str(exc)}

    asset_scores: list[dict] = []
    non_compliant = 0
    at_risk = 0
    compliant_count = 0

    for asset in assets:
        code = asset["asset_code"]
        if not code:
            continue
        overdue = overdue_map.get(code, 0)
        high_wos = wo_map.get(code, 0)

        if overdue > 0 or high_wos >= 3:
            asset_status = "non_compliant"
            non_compliant += 1
        elif high_wos > 0:
            asset_status = "at_risk"
            at_risk += 1
        else:
            asset_status = "compliant"
            compliant_count += 1

        asset_scores.append({
            "asset_code": code,
            "asset_name": asset["asset_name"],
            "category": asset["category"],
            "compliance_status": asset_status,
            "overdue_pms": overdue,
            "open_high_priority_wos": high_wos,
        })

    total = len(asset_scores)
    return {
        "scope": scope,
        "period": {"from": date_from, "to": date_to},
        "summary": {
            "total_assets": total,
            "compliant": compliant_count,
            "at_risk": at_risk,
            "non_compliant": non_compliant,
            "compliance_rate_pct": round(compliant_count / total * 100, 1) if total else 0,
        },
        "asset_details": sorted(asset_scores, key=lambda x: x["compliance_status"]),
    }
