"""FR-028 — Contract performance insights: cost variance, labour variance, trend.

Constitution Principle II: every figure traces to contributing_wo_ids.
"""
from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.contract_performance import InvoiceVerification, VendorWoScore


async def compute_insights(
    session: AsyncSession,
    *,
    vendor_id: UUID | None,
    organization_id: UUID | None,
    from_date: date | None,
    to_date: date | None,
) -> dict[str, Any]:
    """Compute cost variance, labour variance, and matched/flagged trend.

    Returns a dict with:
    - cost_variance_pct: (actual - estimated) / estimated × 100, or None
    - labour_variance_pct: from component_scores.labour_* if present, or None
    - matched_flagged_trend: list of matched_flagged_ratio from invoice verifications
    - contributing_wo_ids: WO codes whose scores drove the cost variance
    - period: {from_date, to_date}
    """
    # --- Query VendorWoScore rows for the period ---
    q = select(VendorWoScore)
    if vendor_id is not None:
        q = q.where(VendorWoScore.vendor_id == vendor_id)
    if organization_id is not None:
        q = q.where(VendorWoScore.organization_id == organization_id)
    if from_date is not None:
        q = q.where(VendorWoScore.score_month >= from_date)
    if to_date is not None:
        q = q.where(VendorWoScore.score_month <= to_date)

    rows: list[VendorWoScore] = (await session.execute(q)).scalars().all()

    if not rows:
        return {
            "ok": True,
            "message": "No data for the requested period",
            "cost_variance_pct": None,
            "labour_variance_pct": None,
            "matched_flagged_trend": [],
            "contributing_wo_ids": [],
            "period": {
                "from_date": from_date.isoformat() if from_date else None,
                "to_date": to_date.isoformat() if to_date else None,
            },
        }

    # --- Cost variance (FR-028) ---
    total_actual = 0.0
    total_estimated = 0.0
    contributing_wo_ids: list[str] = []

    for row in rows:
        if row.cost_actual is not None and row.cost_estimated is not None:
            total_actual += float(row.cost_actual)
            total_estimated += float(row.cost_estimated)
            if row.wo_code:
                contributing_wo_ids.append(row.wo_code)

    cost_variance_pct: float | None = None
    if total_estimated > 0:
        cost_variance_pct = round((total_actual - total_estimated) / total_estimated * 100, 2)

    # --- Labour variance from component_scores JSONB ---
    total_labour_invoiced = 0.0
    total_labour_attended = 0.0
    has_labour_data = False

    for row in rows:
        cs = row.component_scores or {}
        invoiced = cs.get("labour_hours_invoiced")
        attended = cs.get("labour_hours_attended")
        if invoiced is not None and attended is not None:
            total_labour_invoiced += float(invoiced)
            total_labour_attended += float(attended)
            has_labour_data = True

    labour_variance_pct: float | None = None
    if has_labour_data and total_labour_attended > 0:
        labour_variance_pct = round(
            (total_labour_invoiced - total_labour_attended) / total_labour_attended * 100, 2
        )

    # --- Matched/flagged trend from InvoiceVerification ---
    iv_q = select(InvoiceVerification.matched_flagged_ratio).order_by(
        InvoiceVerification.created_at
    )
    if vendor_id is not None:
        iv_q = iv_q.where(InvoiceVerification.vendor_id == vendor_id)
    if organization_id is not None:
        iv_q = iv_q.where(InvoiceVerification.organization_id == organization_id)
    if from_date is not None:
        iv_q = iv_q.where(InvoiceVerification.created_at >= from_date)
    if to_date is not None:
        iv_q = iv_q.where(InvoiceVerification.created_at <= to_date)

    iv_ratios = (await session.execute(iv_q)).scalars().all()
    matched_flagged_trend = [float(r) if r is not None else None for r in iv_ratios]

    return {
        "ok": True,
        "message": None,
        "cost_variance_pct": cost_variance_pct,
        "labour_variance_pct": labour_variance_pct,
        "matched_flagged_trend": matched_flagged_trend,
        "contributing_wo_ids": contributing_wo_ids,
        "period": {
            "from_date": from_date.isoformat() if from_date else None,
            "to_date": to_date.isoformat() if to_date else None,
        },
    }
