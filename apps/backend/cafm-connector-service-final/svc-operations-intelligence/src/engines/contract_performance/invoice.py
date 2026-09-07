"""Feature B3 — Invoice verification against ingested work orders."""
from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.logging import get_logger
from ...models.contract_performance import InvoiceLine, InvoiceVerification
from ...shared.approvals import enqueue_approval, write_audit
from .parameters import contracted_hourly_rate
from ...swarm.adversary import validate_invoice_flag_delta
from .scoring import get_or_create_weights

log = get_logger(__name__)

LABOUR_HOURS_TOLERANCE = 0.10  # ±10%
PARTS_COST_TOLERANCE = 0.05  # ±5%


def _f(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def match_invoice_line(
    line: dict[str, Any],
    *,
    work_orders: dict[str, dict[str, Any]],
    labour_day_rate: float | None,
    labour_hour_rate: float | None = None,
    parts_framework: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Match one invoice line to ingested WO records.
    Returns matched|flagged with plain-English discrepancy and £ delta.
    """
    wo_code = str(line.get("wo_code") or line.get("work_order_code") or "").strip()
    wo = work_orders.get(wo_code) or work_orders.get(wo_code.upper())
    flags: list[str] = []
    delta_gbp = 0.0
    # One resolved figure for both money checks below, and the same one the Adversary
    # re-derives. See parameters.contracted_hourly_rate.
    contracted_hourly = contracted_hourly_rate(labour_day_rate, labour_hour_rate)

    if not wo:
        return {
            "line_id": line.get("line_id") or line.get("id"),
            "wo_code": wo_code or None,
            "status": "flagged",
            "decision": None,
            "discrepancy": f"No ingested completed work order found for {wo_code or 'missing WO code'}.",
            "delta_gbp": _f(line.get("amount")) or 0.0,
            "checks": {"wo_exists": False},
        }

    status = str(wo.get("status") or wo.get("wo_status") or "").lower()
    completed = status in {"closed", "complete", "completed", "done"} or bool(
        wo.get("completed_at") or wo.get("completion_date")
    )
    checks: dict[str, Any] = {"wo_exists": True, "wo_complete": completed}
    if not completed:
        flags.append(f"Work order {wo_code} is not reported complete (status={wo.get('status')}).")

    # Labour hours vs attendance duration ±10%
    inv_hours = _f(line.get("labour_hours") or line.get("hours"))
    attendance = _f(wo.get("attendance_hours") or wo.get("labour_hours") or wo.get("duration_hours"))
    checks["attendance_hours"] = attendance
    if inv_hours is not None and attendance is not None and attendance > 0:
        pct = abs(inv_hours - attendance) / attendance
        checks["labour_hours_pct_diff"] = round(pct * 100, 2)
        if pct > LABOUR_HOURS_TOLERANCE:
            labour_delta = abs(inv_hours - attendance) * (contracted_hourly or 0.0)
            delta_gbp += labour_delta
            flags.append(
                f"Labour hours {inv_hours} vs attendance {attendance} "
                f"(>{LABOUR_HOURS_TOLERANCE*100:.0f}% tolerance); ≈£{labour_delta:.2f}."
            )

    # Parts cost ±5% of contract framework
    inv_parts = _f(line.get("parts_cost") or line.get("parts_amount"))
    framework_parts = None
    part_code = line.get("part_code")
    if parts_framework and part_code and part_code in parts_framework:
        framework_parts = _f(parts_framework[part_code])
    if framework_parts is None:
        framework_parts = _f(wo.get("parts_cost") or wo.get("parts_estimated"))
    if inv_parts is not None and framework_parts is not None and framework_parts > 0:
        pct = abs(inv_parts - framework_parts) / framework_parts
        checks["parts_cost_pct_diff"] = round(pct * 100, 2)
        if pct > PARTS_COST_TOLERANCE:
            parts_delta = abs(inv_parts - framework_parts)
            delta_gbp += parts_delta
            flags.append(
                f"Parts cost £{inv_parts:.2f} vs framework/WO £{framework_parts:.2f} "
                f"(>{PARTS_COST_TOLERANCE*100:.0f}%); £{parts_delta:.2f}."
            )

    # Labour rate ≤ contracted rate
    inv_rate = _f(line.get("labour_rate") or line.get("hourly_rate"))
    if inv_rate is not None and contracted_hourly is not None:
        checks["labour_rate"] = inv_rate
        checks["contracted_hourly"] = round(contracted_hourly, 2)
        if inv_rate > contracted_hourly + 0.01:
            hours = inv_hours or attendance or 1.0
            rate_delta = (inv_rate - contracted_hourly) * hours
            delta_gbp += rate_delta
            flags.append(
                f"Labour rate £{inv_rate:.2f}/h exceeds contracted £{contracted_hourly:.2f}/h; "
                f"≈£{rate_delta:.2f}."
            )

    line_amount = _f(line.get("amount") or line.get("line_total"))
    if flags and delta_gbp == 0 and line_amount:
        delta_gbp = line_amount

    if flags:
        return {
            "line_id": line.get("line_id") or line.get("id"),
            "wo_code": wo_code,
            "status": "flagged",
            "decision": None,
            "discrepancy": " ".join(flags),
            "delta_gbp": round(delta_gbp, 2),
            "checks": checks,
            "pm_actions": ["approve", "challenge", "reject"],
        }

    return {
        "line_id": line.get("line_id") or line.get("id"),
        "wo_code": wo_code,
        "status": "matched",
        "decision": "auto_cleared",
        "discrepancy": None,
        "delta_gbp": 0.0,
        "checks": checks,
    }


def build_insights(
    lines: list[dict[str, Any]],
    *,
    work_orders: list[dict[str, Any]],
    labour_day_rate: float | None,
) -> dict[str, Any]:
    matched = [x for x in lines if x["status"] == "matched"]
    flagged = [x for x in lines if x["status"] == "flagged"]
    total_delta = sum(float(x.get("delta_gbp") or 0) for x in flagged)
    service_over = sum(
        float(x.get("delta_gbp") or 0)
        for x in flagged
        if "Labour" in (x.get("discrepancy") or "") or "rate" in (x.get("discrepancy") or "").lower()
    )
    parts_over = sum(
        float(x.get("delta_gbp") or 0)
        for x in flagged
        if "Parts" in (x.get("discrepancy") or "")
    )
    return {
        "matched_count": len(matched),
        "flagged_count": len(flagged),
        "matched_flagged_ratio": (
            round(len(matched) / (len(matched) + len(flagged)), 4)
            if (matched or flagged)
            else None
        ),
        "total_flagged_delta_gbp": round(total_delta, 2),
        "variance_service_gbp": round(service_over, 2),
        "variance_parts_gbp": round(parts_over, 2),
        "usage_notes": (
            "Labour/duration checked vs reported attendance; "
            "parts vs contract framework or WO estimate."
        ),
        "wo_count_referenced": len(work_orders),
        "contract_labour_day_rate": labour_day_rate,
        "vendor_performance_signal": {
            "matched_flagged_ratio": (
                round(len(matched) / (len(matched) + len(flagged)), 4)
                if (matched or flagged)
                else None
            ),
            "note": "Ratio feeds vendor scorecard as an input signal (B2).",
        },
    }


def _discrepancy_code(result: dict[str, Any]) -> str | None:
    """Map the plain-English discrepancy back to a queryable code."""
    if result["status"] != "flagged":
        return None
    checks = result.get("checks") or {}
    if checks.get("wo_exists") is False:
        return "wo_missing"
    if checks.get("wo_complete") is False:
        return "wo_not_complete"
    text_ = (result.get("discrepancy") or "").lower()
    if "labour rate" in text_:
        return "labour_rate"
    if "labour hours" in text_:
        return "labour_hours"
    if "parts cost" in text_:
        return "parts_cost"
    return "other"


async def _resolve_wo_ids(
    session: AsyncSession, wo_codes: list[str]
) -> dict[str, str]:
    """Map wo_code -> work_orders.wo_uuid so invoice lines link to real records.

    wo_uuid rather than id: work_orders.id is an integer serial in tenants built
    through svc-work-order-management, and invoice_lines.work_order_id is a UUID.
    """
    codes = [c for c in {c for c in wo_codes if c}]
    if not codes:
        return {}
    try:
        async with session.begin_nested():
            rows = (
                await session.execute(
                    text(
                        """
                        SELECT COALESCE(wo_code, workorder_ref) AS code,
                               wo_uuid::text AS id
                        FROM plenum_cafm.work_orders
                        WHERE COALESCE(wo_code, workorder_ref) = ANY(:codes)
                          AND wo_uuid IS NOT NULL
                        """
                    ),
                    {"codes": codes},
                )
            ).mappings().all()
        return {r["code"]: r["id"] for r in rows}
    except Exception as exc:  # noqa: BLE001
        log.debug("invoice.wo_resolve_failed", error=str(exc)[:200])
        return {}


async def _persist_invoice_lines(
    session: AsyncSession,
    *,
    verification_id: UUID,
    organization_id: UUID | None,
    vendor_id: UUID | None,
    invoice_ref: str | None,
    results: list[dict[str, Any]],
    raw_lines: list[dict[str, Any]],
    labour_day_rate: float | None,
    parts_framework: dict[str, Any],
    adversary_threshold: float,
) -> None:
    """Write the normalised per-line rows the PM queue and vendor score read from.

    invoice_verifications.lines_json keeps the raw verification payload; this makes
    the same data queryable and joinable to the work order it claims against.
    """
    wo_ids = await _resolve_wo_ids(session, [r.get("wo_code") for r in results])
    hourly = (labour_day_rate / 8.0) if labour_day_rate else None

    for idx, (result, raw) in enumerate(zip(results, raw_lines), start=1):
        wo_code = result.get("wo_code")
        checks = result.get("checks") or {}
        adversary = result.get("adversary") or {}
        part_code = raw.get("part_code")
        delta = _f(result.get("delta_gbp")) or 0.0
        session.add(
            InvoiceLine(
                invoice_verification_id=verification_id,
                organization_id=organization_id,
                vendor_id=vendor_id,
                invoice_ref=invoice_ref,
                line_no=idx,
                line_ref=str(result.get("line_id") or idx),
                work_order_id=UUID(wo_ids[wo_code]) if wo_code in wo_ids else None,
                wo_code=wo_code,
                description=raw.get("description"),
                labour_hours=_dec(raw.get("labour_hours") or raw.get("hours")),
                labour_rate=_dec(raw.get("labour_rate") or raw.get("hourly_rate")),
                labour_amount=_dec(
                    (_f(raw.get("labour_hours") or raw.get("hours")) or 0)
                    * (_f(raw.get("labour_rate") or raw.get("hourly_rate")) or 0)
                ),
                part_code=part_code,
                parts_qty=_dec(raw.get("parts_qty")),
                parts_cost=_dec(raw.get("parts_cost") or raw.get("parts_amount")),
                line_total=_dec(raw.get("amount") or raw.get("line_total")),
                reported_labour_hours=_dec(checks.get("attendance_hours")),
                contract_labour_rate=_dec(checks.get("contracted_hourly") or hourly),
                contract_parts_cost=_dec(parts_framework.get(part_code)),
                match_status=result["status"],
                discrepancy_code=_discrepancy_code(result),
                discrepancy_text=result.get("discrepancy"),
                delta_gbp=_dec(delta),
                adversary_reviewed=bool(adversary),
                adversary_agreed=adversary.get("approved") if adversary else None,
                adversary_delta_gbp=_dec(delta) if adversary else None,
                raw_metadata={
                    "checks": checks,
                    "adversary_threshold_gbp": adversary_threshold,
                    "approvals_queue_id": result.get("approvals_queue_id"),
                },
            )
        )


def _dec(value: Any) -> Decimal | None:
    f = _f(value)
    return Decimal(str(round(f, 2))) if f is not None else None


async def verify_invoice(
    session: AsyncSession,
    *,
    invoice_ref: str | None,
    lines: list[dict[str, Any]],
    work_orders: list[dict[str, Any]],
    vendor_id: UUID | None = None,
    organization_id: UUID | None = None,
    document_id: UUID | None = None,
    labour_day_rate: float | None = None,
    labour_hour_rate: float | None = None,
    parts_pricing_json: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    B3 — parse/match invoice lines; flags > £500 through Adversary before PM queue.
    """
    weights = await get_or_create_weights(session, organization_id)
    adversary_threshold = float(weights["invoice_flag_adversary_gbp"])

    wo_index: dict[str, dict[str, Any]] = {}
    for wo in work_orders:
        code = str(wo.get("wo_code") or wo.get("code") or "").strip()
        if code:
            wo_index[code] = wo
            wo_index[code.upper()] = wo

    results: list[dict[str, Any]] = []
    for line in lines:
        result = match_invoice_line(
            line,
            work_orders=wo_index,
            labour_day_rate=labour_day_rate,
            labour_hour_rate=labour_hour_rate,
            parts_framework=parts_pricing_json or {},
        )
        if result["status"] == "flagged" and float(result.get("delta_gbp") or 0) >= adversary_threshold:
            adv = validate_invoice_flag_delta(
                claimed_delta_gbp=float(result["delta_gbp"]),
                line=line,
                work_order=wo_index.get(str(result.get("wo_code") or "").upper())
                or wo_index.get(str(result.get("wo_code") or "")),
                labour_day_rate=labour_day_rate,
                labour_hour_rate=labour_hour_rate,
                parts_framework=parts_pricing_json or {},
            )
            result["adversary"] = {
                "approved": adv.approved,
                "reasons": adv.reasons,
                "checks": adv.checks,
                "threshold_gbp": adversary_threshold,
            }
            if adv.approved:
                item = await enqueue_approval(
                    session,
                    source_feature="B",
                    item_type="invoice_flag_high_value",
                    summary=(
                        f"Invoice {invoice_ref or ''} line {result.get('line_id')} "
                        f"flagged £{result['delta_gbp']:.2f} (>{adversary_threshold})"
                    ),
                    severity="high",
                    payload={
                        "invoice_ref": invoice_ref,
                        "line": result,
                        "adversary": result["adversary"],
                    },
                    organization_id=organization_id,
                    related_entity_type="invoice",
                    related_entity_id=None,
                )
                result["approvals_queue_id"] = str(item.id)
            else:
                result["adversary_blocked"] = True
                result["discrepancy"] = (
                    (result.get("discrepancy") or "")
                    + f" Adversary rejected delta arithmetic: {', '.join(adv.reasons)}."
                )
        elif result["status"] == "flagged":
            item = await enqueue_approval(
                session,
                source_feature="B",
                item_type="invoice_flag",
                summary=(
                    f"Invoice {invoice_ref or ''} line flagged £{result.get('delta_gbp', 0):.2f}"
                ),
                severity="medium",
                payload={"invoice_ref": invoice_ref, "line": result},
                organization_id=organization_id,
            )
            result["approvals_queue_id"] = str(item.id)

        results.append(result)

    insights = build_insights(results, work_orders=work_orders, labour_day_rate=labour_day_rate)
    matched_count = insights["matched_count"]
    flagged_count = insights["flagged_count"]
    ratio = insights["matched_flagged_ratio"]

    row = InvoiceVerification(
        id=uuid4(),
        organization_id=organization_id,
        vendor_id=vendor_id,
        invoice_ref=invoice_ref,
        document_id=document_id,
        status="completed",
        matched_count=matched_count,
        flagged_count=flagged_count,
        matched_flagged_ratio=Decimal(str(ratio)) if ratio is not None else None,
        lines_json=results,
        insights_json=insights,
    )
    session.add(row)
    await session.flush()
    await _persist_invoice_lines(
        session,
        verification_id=row.id,
        organization_id=organization_id,
        vendor_id=vendor_id,
        invoice_ref=invoice_ref,
        results=results,
        raw_lines=lines,
        labour_day_rate=labour_day_rate,
        parts_framework=parts_pricing_json or {},
        adversary_threshold=adversary_threshold,
    )
    await write_audit(
        session,
        actor="system",
        action_type="invoice.verify",
        source_feature="B",
        organization_id=organization_id,
        output_payload={
            "id": str(row.id),
            "matched": matched_count,
            "flagged": flagged_count,
        },
    )
    await session.commit()
    return {
        "ok": True,
        "verification_id": str(row.id),
        "invoice_ref": invoice_ref,
        "matched_count": matched_count,
        "flagged_count": flagged_count,
        "matched_flagged_ratio": ratio,
        "lines": results,
        "insights": insights,
    }


async def decide_invoice_line(
    session: AsyncSession,
    verification_id: UUID,
    *,
    line_id: str,
    decision: str,
    pm_notes: str | None = None,
) -> dict[str, Any]:
    """PM decision: approve | challenge | reject on a flagged line."""
    if decision not in {"approve", "challenge", "reject"}:
        return {"ok": False, "error": "invalid_decision"}
    row = await session.get(InvoiceVerification, verification_id)
    if not row:
        return {"ok": False, "error": "not_found"}
    lines = list(row.lines_json or [])
    found = False
    for line in lines:
        if str(line.get("line_id")) == str(line_id):
            line["decision"] = decision
            line["pm_notes"] = pm_notes
            found = True
            break
    if not found:
        return {"ok": False, "error": "line_not_found"}
    row.lines_json = lines
    await write_audit(
        session,
        actor="pm",
        action_type="invoice_line.pm_decision",
        source_feature="B",
        organization_id=row.organization_id,
        input_payload={"decision": decision, "note": pm_notes},
        output_payload={
            "line_id": str(line_id),
            "invoice_verification_id": str(verification_id),
        },
    )
    await session.commit()
    return {"ok": True, "verification_id": str(row.id), "line_id": line_id, "decision": decision}
