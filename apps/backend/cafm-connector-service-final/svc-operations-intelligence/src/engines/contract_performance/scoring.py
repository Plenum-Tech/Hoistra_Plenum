"""Feature B2 — Vendor performance scoring, PPM, cost variance, monthly scorecard."""
from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.logging import get_logger
from ...models.contract_performance import (
    ContractSlaParameters,
    FmReportStaleness,
    InvoiceVerification,
    VendorMonthlyScorecard,
    VendorScoreWeightConfig,
    VendorWoScore,
)
from ...shared.approvals import enqueue_approval, write_audit
from .conflicts import detect_and_flag_wo_conflict
from .parameters import CRITICALITY_SLA_WEIGHT, effective_criticality

log = get_logger(__name__)

DEFAULT_WEIGHTS = {
    "sla_response_pct": 25.0,
    "sla_completion_pct": 25.0,
    "first_fix_pct": 20.0,
    "recall_pct": 15.0,
    "accreditation_pct": 15.0,
    "blocked_score_cap": 60.0,
    "cost_variance_alert_pct": 15.0,
    "cost_variance_job_count": 3,
    "invoice_flag_adversary_gbp": 500.0,
}


def weights_to_dict(row: VendorScoreWeightConfig | None) -> dict[str, Any]:
    if row is None:
        return dict(DEFAULT_WEIGHTS)
    return {
        "id": str(row.id),
        "organization_id": str(row.organization_id) if row.organization_id else None,
        "sla_response_pct": float(row.sla_response_pct),
        "sla_completion_pct": float(row.sla_completion_pct),
        "first_fix_pct": float(row.first_fix_pct),
        "recall_pct": float(row.recall_pct),
        "accreditation_pct": float(row.accreditation_pct),
        "blocked_score_cap": float(row.blocked_score_cap),
        "cost_variance_alert_pct": float(row.cost_variance_alert_pct),
        "cost_variance_job_count": int(row.cost_variance_job_count),
        "invoice_flag_adversary_gbp": float(row.invoice_flag_adversary_gbp),
    }


async def get_or_create_weights(
    session: AsyncSession,
    organization_id: UUID | None = None,
) -> dict[str, Any]:
    q = select(VendorScoreWeightConfig)
    if organization_id:
        q = q.where(VendorScoreWeightConfig.organization_id == organization_id)
    else:
        q = q.where(VendorScoreWeightConfig.organization_id.is_(None))
    row = (await session.execute(q.limit(1))).scalar_one_or_none()
    if row is None:
        row = VendorScoreWeightConfig(
            id=uuid4(),
            organization_id=organization_id,
            sla_response_pct=Decimal(str(DEFAULT_WEIGHTS["sla_response_pct"])),
            sla_completion_pct=Decimal(str(DEFAULT_WEIGHTS["sla_completion_pct"])),
            first_fix_pct=Decimal(str(DEFAULT_WEIGHTS["first_fix_pct"])),
            recall_pct=Decimal(str(DEFAULT_WEIGHTS["recall_pct"])),
            accreditation_pct=Decimal(str(DEFAULT_WEIGHTS["accreditation_pct"])),
            blocked_score_cap=Decimal(str(DEFAULT_WEIGHTS["blocked_score_cap"])),
            cost_variance_alert_pct=Decimal(str(DEFAULT_WEIGHTS["cost_variance_alert_pct"])),
            cost_variance_job_count=int(DEFAULT_WEIGHTS["cost_variance_job_count"]),
            invoice_flag_adversary_gbp=Decimal(str(DEFAULT_WEIGHTS["invoice_flag_adversary_gbp"])),
        )
        session.add(row)
        await session.flush()
        await session.commit()
    return weights_to_dict(row)


async def update_weights(
    session: AsyncSession,
    updates: dict[str, Any],
    *,
    organization_id: UUID | None = None,
) -> dict[str, Any]:
    """Admin section — all score component weights editable."""
    q = select(VendorScoreWeightConfig)
    if organization_id:
        q = q.where(VendorScoreWeightConfig.organization_id == organization_id)
    else:
        q = q.where(VendorScoreWeightConfig.organization_id.is_(None))
    row = (await session.execute(q.limit(1))).scalar_one_or_none()
    if row is None:
        await get_or_create_weights(session, organization_id)
        return await update_weights(session, updates, organization_id=organization_id)

    pct_keys = (
        "sla_response_pct",
        "sla_completion_pct",
        "first_fix_pct",
        "recall_pct",
        "accreditation_pct",
        "blocked_score_cap",
        "cost_variance_alert_pct",
        "invoice_flag_adversary_gbp",
    )
    for k in pct_keys:
        if k in updates and updates[k] is not None:
            setattr(row, k, Decimal(str(updates[k])))
    if "cost_variance_job_count" in updates and updates["cost_variance_job_count"] is not None:
        row.cost_variance_job_count = int(updates["cost_variance_job_count"])

    # FR-030: hard rejection — components must total exactly 100
    component_sum = float(
        row.sla_response_pct
        + row.sla_completion_pct
        + row.first_fix_pct
        + row.recall_pct
        + row.accreditation_pct
    )
    if round(component_sum, 2) != 100.0:
        await session.rollback()
        return {
            "ok": False,
            "error": "weights_must_total_100",
            "component_sum": round(component_sum, 2),
        }
    row.updated_at = datetime.now(timezone.utc)
    await write_audit(
        session,
        actor="admin",
        action_type="vendor_score_weights.update",
        source_feature="B",
        organization_id=organization_id,
        input_payload=updates,
        detail={"component_sum": component_sum},
    )
    await session.commit()
    result = weights_to_dict(row)
    result["component_sum"] = component_sum
    result["ok"] = True
    return result


def _as_uuid_or_none(value: Any) -> UUID | None:
    """A UUID for the score row, or None when the source identifier is not one.

    plenum_cafm.work_orders.id is an INTEGER — the table carries a separate wo_uuid — and
    assets.id is a VARCHAR. UUID(str(...)) on either raises ValueError, so scoring a work
    order that came from the real table aborted the whole month rather than scoring it.
    The score row keeps its wo_code either way, which is what the drill-down joins on, so
    dropping an unusable id costs nothing and losing the month costs everything.
    """
    if value in (None, ""):
        return None
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


def _priority_key(priority: str | None) -> str:
    p = (priority or "P3").upper().replace(" ", "")
    if p in {"P1", "HIGHEST", "CRITICAL", "1"}:
        return "p1"
    if p in {"P2", "HIGH", "2"}:
        return "p2"
    if p in {"P4", "LOW", "LOWEST", "4"}:
        return "p4"
    return "p3"


def sla_target_hours(params: dict[str, Any], priority: str | None, kind: str) -> float | None:
    key = f"sla_{kind}_{_priority_key(priority)}_hours"
    val = params.get(key)
    return float(val) if val is not None else None


def hours_between(start: datetime | None, end: datetime | None) -> float | None:
    if not start or not end:
        return None
    return max(0.0, (end - start).total_seconds() / 3600.0)


def _sla_component_with_criticality(
    met: bool | None,
    weight: float,
    criticality: str,
) -> tuple[float, float]:
    """
    PRD: L1 failures weighted 3× vs L3.
    Returns (earned_points, extra_penalty).
    Unmet L3: lose `weight`. Unmet L1: lose `weight` + 2×weight extra = 3× L3 impact.
    """
    if criticality == "L1":
        fail_mult = 3.0
    elif criticality == "L2":
        fail_mult = 1.5
    else:
        fail_mult = 1.0

    if met is None:
        return weight * 0.5, 0.0
    if met:
        return weight, 0.0
    # Unmet: base earn 0; extra penalty makes total impact = weight * fail_mult
    extra = weight * (fail_mult - 1.0)
    return 0.0, extra


def compute_wo_component_scores(
    *,
    sla_response_met: bool | None,
    sla_completion_met: bool | None,
    first_fix: bool | None,
    recall: bool | None,
    accreditation_current: bool | None,
    weights: dict[str, Any],
    criticality: str = "L2",
) -> dict[str, Any]:
    """
    Weighted components (PRD B2):
      SLA response 25%, completion 25%, first fix 20%, recall 15%, accreditation 15%.
    SLA misses scaled by asset criticality (L1 failures weighted 3× vs L3).
    """
    w_resp = float(weights["sla_response_pct"])
    w_comp = float(weights["sla_completion_pct"])
    w_ff = float(weights["first_fix_pct"])
    w_rec = float(weights["recall_pct"])
    w_acc = float(weights["accreditation_pct"])
    crit_w = CRITICALITY_SLA_WEIGHT.get(criticality, 1.0)

    def bool_score(met: bool | None, weight: float) -> float:
        if met is None:
            return weight * 0.5
        return weight if met else 0.0

    recall_met = None if recall is None else (not recall)

    resp_part, resp_pen = _sla_component_with_criticality(
        sla_response_met, w_resp, criticality
    )
    comp_part, comp_pen = _sla_component_with_criticality(
        sla_completion_met, w_comp, criticality
    )
    ff = bool_score(first_fix, w_ff)
    rec = bool_score(recall_met, w_rec)
    acc = bool_score(accreditation_current, w_acc)

    overall = min(
        100.0,
        max(0.0, resp_part + comp_part + ff + rec + acc - resp_pen - comp_pen),
    )
    return {
        "sla_response": round(resp_part, 2),
        "sla_completion": round(comp_part, 2),
        "first_fix": round(ff, 2),
        "recall": round(rec, 2),
        "accreditation": round(acc, 2),
        "criticality": criticality,
        "criticality_weight": crit_w,
        "criticality_extra_penalty": round(resp_pen + comp_pen, 2),
        "overall_uncapped": round(overall, 2),
    }


def apply_block_cap(overall: float, *, vendor_blocked: bool, cap: float) -> tuple[float, bool]:
    """Any lapsed accreditation (block_state=Blocked) caps score at 60."""
    if vendor_blocked and overall > cap:
        return cap, True
    return overall, False


def detect_recall(
    *,
    asset_id: str | None,
    completed_at: datetime | None,
    prior_completions: list[dict[str, Any]],
    window_days: int = 30,
) -> bool:
    """Repeat fault within 30 days on same asset."""
    if not asset_id or not completed_at:
        return False
    window_start = completed_at - timedelta(days=window_days)
    for prior in prior_completions:
        if str(prior.get("asset_id")) != str(asset_id):
            continue
        prior_at = prior.get("completed_at")
        if isinstance(prior_at, str):
            prior_at = datetime.fromisoformat(prior_at.replace("Z", "+00:00"))
        if prior_at and window_start <= prior_at < completed_at:
            return True
    return False


def compute_ppm_compliance(
    visits: list[dict[str, Any]],
    *,
    tolerance_days: int = 7,
) -> float | None:
    """
    (PPM visits completed within ±7 days of schedule ÷ visits scheduled) × 100
    Each visit: {scheduled_date: date, completed_date: date|None}
    """
    if not visits:
        return None
    scheduled = len(visits)
    ok = 0
    for v in visits:
        sched = v.get("scheduled_date")
        done = v.get("completed_date")
        if isinstance(sched, str):
            sched = date.fromisoformat(sched[:10])
        if isinstance(done, str):
            done = date.fromisoformat(done[:10])
        if sched and done and abs((done - sched).days) <= tolerance_days:
            ok += 1
    return round(100.0 * ok / scheduled, 2)


async def _vendor_blocked(session: AsyncSession, vendor_id: UUID | None) -> bool:
    if not vendor_id:
        return False
    try:
        # SAVEPOINT so a missing block_state column (pre-Phase-2 vendors table)
        # rolls back to here instead of aborting the whole scoring transaction.
        async with session.begin_nested():
            row = (
                await session.execute(
                    text(
                        "SELECT COALESCE(block_state, 'Clear') FROM plenum_cafm.vendors WHERE id = :vid"
                    ),
                    {"vid": str(vendor_id)},
                )
            ).scalar_one_or_none()
        return (row or "Clear") == "Blocked"
    except Exception:  # noqa: BLE001
        return False


async def _accreditation_current(
    session: AsyncSession, vendor_id: UUID | None
) -> tuple[bool, date | None]:
    """Return (is_current, earliest_lapse_date).

    FR-038: The lapse_date is used by callers to apply the score cap only to
    work orders raised on or after that date, not to the whole month.
    Returns (True, None) when accreditation is current.
    """
    if not vendor_id:
        return True, None
    try:
        from ...models import ComplianceCertificate

        q = select(ComplianceCertificate).where(
            ComplianceCertificate.vendor_id == vendor_id,
            ComplianceCertificate.cert_scope == "Vendor",
        )
        async with session.begin_nested():
            certs = list((await session.execute(q)).scalars().all())
        if not certs:
            return True, None  # no certs tracked → don't punish
        today = date.today()
        earliest_lapse: date | None = None
        lapsed = False
        for c in certs:
            if c.expiry_date and c.expiry_date < today:
                status = (c.status or "").lower()
                if status in {"lapsed", "expired"} or c.expiry_date < today:
                    lapsed = True
                    if earliest_lapse is None or c.expiry_date < earliest_lapse:
                        earliest_lapse = c.expiry_date
        if lapsed:
            return False, earliest_lapse
        return True, None
    except Exception:  # noqa: BLE001
        return True, None


async def _load_confirmed_params(
    session: AsyncSession,
    vendor_id: UUID | None,
) -> dict[str, Any]:
    from sqlalchemy import nulls_last

    q = (
        select(ContractSlaParameters)
        .where(ContractSlaParameters.status == "confirmed")
        .order_by(
            nulls_last(ContractSlaParameters.signed_date.desc()),
            ContractSlaParameters.confirmed_at.desc(),
        )
        .limit(2)  # Fetch 2 to detect same-signed-date ties (FR-035)
    )
    if vendor_id:
        q = q.where(ContractSlaParameters.vendor_id == vendor_id)
    rows = list((await session.execute(q)).scalars().all())
    if not rows:
        from .parameters import merge_extraction_with_defaults

        # US1 scenario 4: no confirmed contract means every SLA target is a platform
        # default. merge_extraction_with_defaults already knows which fields those are —
        # discarding that (merged, _, _) is what let scoring silently measure a vendor
        # against numbers their contract never committed to.
        merged, defaults_used, field_sources = merge_extraction_with_defaults({})
        merged["_contract_confirmed"] = False
        merged["_defaults_used"] = list(defaults_used)
        merged["_field_sources"] = dict(field_sources)
        return merged

    top = rows[0]
    # Marked on both paths so a caller never has to infer provenance from absence.
    # Detect same-signed-date tie (FR-035): flag to PM, block scoring
    if len(rows) == 2 and top.signed_date is not None and rows[1].signed_date == top.signed_date:
        try:
            await enqueue_approval(
                session,
                source_feature="B",
                item_type="overlapping_contracts_tie",
                summary=(
                    f"Two confirmed contracts for vendor {vendor_id} share the same "
                    f"signed_date {top.signed_date} — PM must resolve which governs."
                ),
                severity="high",
                payload={
                    "vendor_id": str(vendor_id) if vendor_id else None,
                    "signed_date": top.signed_date.isoformat(),
                    "contract_ids": [str(rows[0].id), str(rows[1].id)],
                },
                organization_id=top.organization_id,
                related_entity_type="contract_sla_parameters",
                related_entity_id=top.id,
            )
        except Exception:  # noqa: BLE001
            pass  # Don't block scoring due to approval enqueue failure; log occurs inside

    from .parameters import params_to_dict

    confirmed = params_to_dict(top)
    confirmed["_contract_confirmed"] = True
    confirmed["_contract_parameters_id"] = str(top.id)
    return confirmed


def score_work_order_pure(
    wo: dict[str, Any],
    *,
    params: dict[str, Any],
    weights: dict[str, Any],
    prior_completions: list[dict[str, Any]] | None = None,
    accreditation_current: bool = True,
    vendor_blocked: bool = False,
    criticality: str = "L2",
    lapse_date: date | None = None,
) -> dict[str, Any]:
    """Pure scoring for unit tests — no DB."""
    reported_at = _parse_dt(wo.get("reported_at") or wo.get("created_at"))
    attended_at = _parse_dt(wo.get("attended_at") or wo.get("response_at"))
    completed_at = _parse_dt(wo.get("completed_at") or wo.get("completion_date"))
    priority = wo.get("priority") or wo.get("wo_priority")

    # FR-038: cap applies only to WOs raised on/after the accreditation lapse date
    post_lapse = (
        lapse_date is not None
        and reported_at is not None
        and reported_at.date() >= lapse_date
    )

    resp_target = sla_target_hours(params, priority, "response")
    comp_target = sla_target_hours(params, priority, "completion")
    resp_hours = hours_between(reported_at, attended_at)
    comp_hours = hours_between(reported_at, completed_at)

    sla_response_met = None if resp_hours is None or resp_target is None else resp_hours <= resp_target
    sla_completion_met = None if comp_hours is None or comp_target is None else comp_hours <= comp_target

    # FR-036: compute recall first so first_fix can reference the resolved value
    recall = wo.get("recall")
    if recall is None:
        recall = detect_recall(
            asset_id=str(wo["asset_id"]) if wo.get("asset_id") else None,
            completed_at=completed_at,
            prior_completions=prior_completions or [],
        )

    first_fix = wo.get("first_fix")
    if first_fix is None:
        # FR-036: a recall event means the job was NOT a first fix
        first_fix = not bool(wo.get("return_visit")) and not bool(recall)

    components = compute_wo_component_scores(
        sla_response_met=sla_response_met,
        sla_completion_met=sla_completion_met,
        first_fix=bool(first_fix) if first_fix is not None else None,
        recall=bool(recall),
        accreditation_current=accreditation_current,
        weights=weights,
        criticality=criticality,
    )
    overall, capped = apply_block_cap(
        components["overall_uncapped"],
        vendor_blocked=vendor_blocked and post_lapse,
        cap=float(weights["blocked_score_cap"]),
    )
    components["lapse_cap_applied"] = post_lapse and capped
    components["lapse_date"] = lapse_date.isoformat() if lapse_date else None

    cost_actual = wo.get("cost_actual") or wo.get("actual_cost")
    cost_estimated = wo.get("cost_estimated") or wo.get("estimated_cost")
    variance_pct = None
    if cost_actual is not None and cost_estimated not in (None, 0, 0.0):
        variance_pct = round(
            100.0 * (float(cost_actual) - float(cost_estimated)) / float(cost_estimated), 2
        )

    return {
        "sla_response_met": sla_response_met,
        "sla_completion_met": sla_completion_met,
        "first_fix": bool(first_fix) if first_fix is not None else None,
        "recall": bool(recall),
        "accreditation_current": accreditation_current,
        "component_scores": components,
        "overall_score": round(overall, 2),
        "capped_by_block": capped,
        "cost_actual": float(cost_actual) if cost_actual is not None else None,
        "cost_estimated": float(cost_estimated) if cost_estimated is not None else None,
        "cost_variance_pct": variance_pct,
        "wo_code": wo.get("wo_code") or wo.get("code"),
        "work_order_id": wo.get("work_order_id") or wo.get("id"),
        "asset_id": wo.get("asset_id"),
    }


def _parse_dt(v: Any) -> datetime | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v
    if isinstance(v, date) and not isinstance(v, datetime):
        return datetime(v.year, v.month, v.day, tzinfo=timezone.utc)
    if isinstance(v, str):
        return datetime.fromisoformat(v.replace("Z", "+00:00"))
    return None


async def score_completed_work_orders(
    session: AsyncSession,
    work_orders: list[dict[str, Any]],
    *,
    vendor_id: UUID | None = None,
    organization_id: UUID | None = None,
    score_month: date | None = None,
    allow_default_parameters: bool = False,
) -> dict[str, Any]:
    """Score ingested completed WOs against confirmed contract SLA."""
    weights = await get_or_create_weights(session, organization_id)
    params = await _load_confirmed_params(session, vendor_id)

    # US1 scenario 4 — a score is a measurement against a commitment. With no confirmed
    # contract there is no commitment, only platform defaults, and a scorecard built on
    # those reads as though the vendor had been held to terms they never agreed. Refuse,
    # and name what is missing. allow_default_parameters is the deliberate opt-out for a
    # client who has not loaded a contract yet and accepts being scored on defaults.
    if not params.get("_contract_confirmed") and not allow_default_parameters:
        return {
            "ok": False,
            "error": "contract_parameters_unconfirmed",
            "vendor_id": str(vendor_id) if vendor_id else None,
            "message": (
                "No confirmed contract parameters for this vendor. Scoring would measure "
                "against platform defaults rather than the contract. Confirm the extracted "
                "parameters, or re-run with allow_default_parameters=true."
            ),
            "unconfirmed_parameters": list(params.get("_defaults_used") or []),
            "scored": 0,
        }

    blocked = await _vendor_blocked(session, vendor_id)
    acc_ok, lapse_date = await _accreditation_current(session, vendor_id)
    if score_month is None:
        today = date.today()
        score_month = date(today.year, today.month, 1)

    prior: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    alert_pct = float(weights["cost_variance_alert_pct"])
    job_threshold = int(weights["cost_variance_job_count"])

    eligible_work_orders: list[dict[str, Any]] = []
    for wo in work_orders:
        wo_ref = wo.get("wo_code") or wo.get("code") or wo.get("id")
        reported = _parse_dt(wo.get("reported_at") or wo.get("created_at"))
        attended = _parse_dt(wo.get("attended_at") or wo.get("response_at"))
        completed = _parse_dt(wo.get("completed_at") or wo.get("completion_date"))

        # FR-033: exclude self-contradictory timestamps
        if reported and completed and completed < reported:
            exclusions.append(
                {
                    "wo_ref": wo_ref,
                    "reason": "contradictory_timestamps",
                    "detail": "completed_at before reported_at",
                }
            )
            continue
        if reported and attended and attended < reported:
            exclusions.append(
                {
                    "wo_ref": wo_ref,
                    "reason": "contradictory_timestamps",
                    "detail": "attended_at before reported_at",
                }
            )
            continue
        # FR-033: exclude WOs with no completion evidence at all
        if attended is None and completed is None:
            exclusions.append(
                {
                    "wo_ref": wo_ref,
                    "reason": "missing_dates",
                    "detail": "both attended_at and completed_at are absent",
                }
            )
            continue
        # FR-039: skip conflicted WOs — flag for PM review, do not score
        if wo_ref:
            conflict_result = await detect_and_flag_wo_conflict(
                session,
                wo_code=str(wo_ref),
                incoming_fields={
                    "actual_cost": wo.get("actual_cost") or wo.get("cost_actual"),
                    "estimated_cost": wo.get("estimated_cost") or wo.get("cost_estimated"),
                    "attended_at": str(attended) if attended else None,
                    "completed_at": str(completed) if completed else None,
                },
                organization_id=organization_id,
            )
            if conflict_result["conflict"]:
                exclusions.append(
                    {
                        "wo_ref": wo_ref,
                        "reason": "conflict_flagged",
                        "detail": "re-ingestion values differ from stored; pending PM review",
                    }
                )
                continue
        eligible_work_orders.append(wo)

    for wo in eligible_work_orders:
        asset_id = wo.get("asset_id")
        asset_uuid: UUID | None = None
        if asset_id:
            try:
                asset_uuid = UUID(str(asset_id))
            except Exception:
                resolved = await _resolve_asset_id(session, asset_id)
                if resolved:
                    asset_uuid = UUID(resolved)
                    wo["asset_id"] = resolved
        crit = await effective_criticality(session, asset_uuid)
        scored = score_work_order_pure(
            wo,
            params=params,
            weights=weights,
            prior_completions=prior,
            accreditation_current=acc_ok,
            vendor_blocked=blocked,
            criticality=crit,
            lapse_date=lapse_date,
        )
        prior.append(
            {
                "asset_id": wo.get("asset_id"),
                "completed_at": wo.get("completed_at") or wo.get("completion_date"),
            }
        )

        row = VendorWoScore(
            id=uuid4(),
            organization_id=organization_id,
            vendor_id=vendor_id,
            work_order_id=_as_uuid_or_none(scored.get("work_order_id")),
            wo_code=scored.get("wo_code"),
            asset_id=_as_uuid_or_none(scored.get("asset_id")),
            score_month=score_month,
            sla_response_met=scored["sla_response_met"],
            sla_completion_met=scored["sla_completion_met"],
            first_fix=scored["first_fix"],
            recall=scored["recall"],
            accreditation_current=scored["accreditation_current"],
            component_scores=scored["component_scores"],
            overall_score=Decimal(str(scored["overall_score"])),
            capped_by_block=scored["capped_by_block"],
            cost_actual=Decimal(str(scored["cost_actual"])) if scored.get("cost_actual") is not None else None,
            cost_estimated=Decimal(str(scored["cost_estimated"])) if scored.get("cost_estimated") is not None else None,
            cost_variance_pct=Decimal(str(scored["cost_variance_pct"])) if scored.get("cost_variance_pct") is not None else None,
        )
        session.add(row)
        await session.flush()
        scored["id"] = str(row.id)
        results.append(scored)

    # Month-window cost variance (PRD: 3+ jobs in a month), not just this batch
    month_overruns = 0
    if vendor_id:
        month_rows = list(
            (
                await session.execute(
                    select(VendorWoScore).where(
                        VendorWoScore.vendor_id == vendor_id,
                        VendorWoScore.score_month == score_month,
                    )
                )
            ).scalars().all()
        )
        month_overruns = sum(
            1
            for r in month_rows
            if r.cost_variance_pct is not None and float(r.cost_variance_pct) > alert_pct
        )
    else:
        month_overruns = sum(
            1
            for s in results
            if s.get("cost_variance_pct") is not None and s["cost_variance_pct"] > alert_pct
        )

    alert = None
    if month_overruns >= job_threshold:
        item = await enqueue_approval(
            session,
            source_feature="B",
            item_type="cost_variance_alert",
            summary=(
                f"Systematic cost overrun: {month_overruns} jobs >{alert_pct}% variance "
                f"in {score_month.isoformat()} for vendor {vendor_id}"
            ),
            severity="high",
            payload={
                "vendor_id": str(vendor_id) if vendor_id else None,
                "overrun_count": month_overruns,
                "score_month": score_month.isoformat(),
                "window": "month",
            },
            organization_id=organization_id,
            related_entity_type="vendor",
            related_entity_id=vendor_id,
        )
        alert = str(item.id)

    await write_audit(
        session,
        actor="system",
        action_type="vendor_wo.score_batch",
        source_feature="B",
        organization_id=organization_id,
        detail={
            "count": len(results),
            "vendor_id": str(vendor_id) if vendor_id else None,
            "month_overruns": month_overruns,
        },
    )
    await session.commit()
    return {
        "ok": True,
        "count": len(results),
        "scores": results,
        "exclusions": exclusions,
        "excluded_count": len(exclusions),
        "vendor_blocked": blocked,
        "month_cost_overruns": month_overruns,
        "cost_variance_alert_id": alert,
    }


def blend_invoice_ratio_into_score(wo_avg: float, ratio: float | None) -> float:
    """
    PRD B3: matched-to-flagged ratio is an input signal to the vendor score.
    Blend 15% signal: overall = 0.85 * wo_avg + 0.15 * (ratio * 100).
    """
    if ratio is None:
        return round(wo_avg, 2)
    blended = 0.85 * wo_avg + 0.15 * (float(ratio) * 100.0)
    return round(min(100.0, max(0.0, blended)), 2)


async def load_ppm_visits_for_month(
    session: AsyncSession,
    *,
    vendor_id: UUID | None,
    score_month: date,
) -> list[dict[str, Any]]:
    """Pull scheduled vs completed PPM visits from plenum_cafm for ±7d compliance.

    Reads `ppm_visits` (the canonical Feature B table) first, then falls back to
    inferring visits from `scheduled_maintenance` / `scheduled_pm` for tenants
    that have not yet ingested PPM completion reports.
    """
    start, end = month_bounds(score_month)
    visits: list[dict[str, Any]] = []

    try:
        # SAVEPOINT so a missing table cannot abort the caller's transaction.
        async with session.begin_nested():
            rows = (
                await session.execute(
                    text(
                        """
                        SELECT
                          scheduled_date::date AS scheduled_date,
                          completed_date::date AS completed_date,
                          tolerance_days
                        FROM plenum_cafm.ppm_visits
                        WHERE scheduled_date BETWEEN :start AND :end
                          AND (CAST(:vid AS uuid) IS NULL OR vendor_id = CAST(:vid AS uuid))
                        ORDER BY scheduled_date
                        LIMIT 500
                        """
                    ),
                    {"start": start, "end": end, "vid": str(vendor_id) if vendor_id else None},
                )
            ).mappings().all()
        if rows:
            return [dict(r) for r in rows]
    except Exception as exc:  # noqa: BLE001
        # Table absent until phase2_contract_performance_v2.sql is applied.
        log.debug("ppm.visits_table_unavailable", error=str(exc)[:200])

    # Try scheduled_maintenance + work_orders (best-effort; schema may vary).
    # SAVEPOINT here too: without one, a failure in this fallback aborts the caller's
    # transaction, and the scorecard write that follows fails with
    # InFailedSQLTransactionError — a PPM lookup problem surfacing as a scoring crash.
    try:
        async with session.begin_nested():
            rows = (
                    await session.execute(
                    text(
                        """
                        SELECT
                          sm.id::text AS sm_id,
                          COALESCE(sm.next_due_date, sm.last_date)::date AS scheduled_date,
                          (
                            SELECT MIN(wo.completed_at)::date
                            FROM plenum_cafm.work_orders wo
                            WHERE wo.asset_id = sm.asset_id
                              AND LOWER(COALESCE(wo.wo_type, wo.maintenance_type, '')) LIKE '%pm%'
                              AND wo.completed_at IS NOT NULL
                              AND wo.completed_at::date BETWEEN :start - INTERVAL '14 days'
                                                            AND :end + INTERVAL '14 days'
                              AND (CAST(:vid AS uuid) IS NULL OR wo.vendor_id = CAST(:vid AS uuid))
                          ) AS completed_date
                        FROM plenum_cafm.scheduled_maintenance sm
                        WHERE COALESCE(sm.next_due_date, sm.last_date)::date
                              BETWEEN :start AND :end
                        LIMIT 500
                        """
                    ),
                    {"start": start, "end": end, "vid": str(vendor_id) if vendor_id else None},
                )
            ).mappings().all()
        for r in rows:
            visits.append(
                {
                    "scheduled_date": r["scheduled_date"],
                    "completed_date": r["completed_date"],
                }
            )
    except Exception:  # noqa: BLE001
        try:
            # Last resort, and the last place a missing table can poison the caller.
            # Without this savepoint the failure aborts the transaction and the scorecard
            # write that follows dies with InFailedSQLTransactionError — a PPM lookup
            # surfacing as a scoring crash several frames away.
            async with session.begin_nested():
                rows = (
                    await session.execute(
                        text(
                            """
                            SELECT
                              scheduled_date::date AS scheduled_date,
                              completed_date::date AS completed_date
                            FROM plenum_cafm.scheduled_pm
                            WHERE scheduled_date::date BETWEEN :start AND :end
                            LIMIT 500
                            """
                        ),
                        {"start": start, "end": end},
                    )
                ).mappings().all()
            for r in rows:
                visits.append(dict(r))
        except Exception:  # noqa: BLE001
            log.warning("ppm.load_failed", vendor_id=str(vendor_id) if vendor_id else None)
    return visits


async def generate_monthly_scorecard(
    session: AsyncSession,
    *,
    vendor_id: UUID,
    score_month: date | None = None,
    organization_id: UUID | None = None,
    ppm_visits: list[dict[str, Any]] | None = None,
    exclusions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Monthly vendor scorecard (1st of month job for preceding month)."""
    if score_month is None:
        today = date.today()
        first_this = date(today.year, today.month, 1)
        score_month = (first_this - timedelta(days=1)).replace(day=1)

    q = select(VendorWoScore).where(
        VendorWoScore.vendor_id == vendor_id,
        VendorWoScore.score_month == score_month,
    )
    rows = list((await session.execute(q)).scalars().all())
    if not rows:
        return {"ok": False, "error": "no_wo_scores_for_month", "score_month": score_month.isoformat()}

    # FR-031: snapshot weights at scoring time so future changes don't alter this record
    weights = await get_or_create_weights(session, organization_id)

    overalls = [float(r.overall_score) for r in rows if r.overall_score is not None]
    wo_avg = round(sum(overalls) / len(overalls), 2) if overalls else 0.0
    block_capped = any(r.capped_by_block for r in rows)

    keys = ["sla_response", "sla_completion", "first_fix", "recall", "accreditation"]
    breakdown: dict[str, Any] = {}
    for k in keys:
        vals = [
            float((r.component_scores or {}).get(k, 0))
            for r in rows
            if r.component_scores
        ]
        breakdown[k] = round(sum(vals) / len(vals), 2) if vals else 0.0

    # FR-031: immutable snapshot of weights used to produce this scorecard
    breakdown["weights_snapshot"] = {
        "sla_response_pct": weights["sla_response_pct"],
        "sla_completion_pct": weights["sla_completion_pct"],
        "first_fix_pct": weights["first_fix_pct"],
        "recall_pct": weights["recall_pct"],
        "accreditation_pct": weights["accreditation_pct"],
    }

    # FR-033: carry forward exclusions from the scoring run
    breakdown["exclusions"] = list(exclusions or [])

    # US1 scenario 4 — state what the score was measured against. A scorecard produced
    # from platform defaults is a different claim from one produced against a signed
    # contract, and the reader cannot tell them apart unless the card says so.
    sla_params = await _load_confirmed_params(session, vendor_id)
    breakdown["contract_confirmed"] = bool(sla_params.get("_contract_confirmed"))
    if not sla_params.get("_contract_confirmed"):
        breakdown["parameter_source"] = "Default - not contract-sourced"
        breakdown["defaults_used"] = list(sla_params.get("_defaults_used") or [])
    else:
        breakdown["parameter_source"] = "Contract-sourced"
        breakdown["contract_parameters_id"] = sla_params.get("_contract_parameters_id")

    if ppm_visits is None:
        ppm_visits = await load_ppm_visits_for_month(
            session, vendor_id=vendor_id, score_month=score_month
        )
    ppm = compute_ppm_compliance(ppm_visits or [])

    iq = (
        select(InvoiceVerification)
        .where(InvoiceVerification.vendor_id == vendor_id)
        .order_by(InvoiceVerification.created_at.desc())
        .limit(20)
    )
    invoices = list((await session.execute(iq)).scalars().all())
    matched = sum(i.matched_count for i in invoices)
    flagged = sum(i.flagged_count for i in invoices)
    ratio = None
    if matched + flagged > 0:
        ratio = round(matched / (matched + flagged), 4)

    # Blend invoice ratio into overall (PRD B3 input signal)
    overall = blend_invoice_ratio_into_score(wo_avg, ratio)
    if block_capped:
        overall = min(overall, float(weights["blocked_score_cap"]))

    breakdown["invoice_match_signal"] = round((ratio or 0) * 100, 2) if ratio is not None else None
    breakdown["wo_avg_before_invoice_blend"] = wo_avg

    prev_month = (score_month - timedelta(days=1)).replace(day=1)
    prev = (
        await session.execute(
            select(VendorMonthlyScorecard).where(
                VendorMonthlyScorecard.vendor_id == vendor_id,
                VendorMonthlyScorecard.score_month == prev_month,
            )
        )
    ).scalar_one_or_none()
    trend = None
    if prev and prev.overall_score is not None:
        trend = round(overall - float(prev.overall_score), 2)

    existing = (
        await session.execute(
            select(VendorMonthlyScorecard).where(
                VendorMonthlyScorecard.vendor_id == vendor_id,
                VendorMonthlyScorecard.score_month == score_month,
            )
        )
    ).scalar_one_or_none()

    payload = {
        "overall_score": overall,
        "trend_delta": trend,
        "component_breakdown": breakdown,
        "ppm_compliance_pct": ppm,
        "matched_flagged_ratio": ratio,
        "wo_score_ids": [str(r.id) for r in rows],
        "block_capped": block_capped,
    }

    if existing:
        for k, v in payload.items():
            if k == "overall_score":
                existing.overall_score = Decimal(str(v))
            elif k == "trend_delta":
                existing.trend_delta = Decimal(str(v)) if v is not None else None
            elif k == "ppm_compliance_pct":
                existing.ppm_compliance_pct = Decimal(str(v)) if v is not None else None
            elif k == "matched_flagged_ratio":
                existing.matched_flagged_ratio = Decimal(str(v)) if v is not None else None
            elif k == "component_breakdown":
                existing.component_breakdown = v
            elif k == "wo_score_ids":
                existing.wo_score_ids = v
            elif k == "block_capped":
                existing.block_capped = v
        card = existing
    else:
        card = VendorMonthlyScorecard(
            id=uuid4(),
            organization_id=organization_id,
            vendor_id=vendor_id,
            score_month=score_month,
            overall_score=Decimal(str(overall)),
            trend_delta=Decimal(str(trend)) if trend is not None else None,
            component_breakdown=breakdown,
            ppm_compliance_pct=Decimal(str(ppm)) if ppm is not None else None,
            matched_flagged_ratio=Decimal(str(ratio)) if ratio is not None else None,
            wo_score_ids=[str(r.id) for r in rows],
            block_capped=block_capped,
        )
        session.add(card)

    await write_audit(
        session,
        actor="system",
        action_type="vendor_scorecard.generate",
        source_feature="B",
        organization_id=organization_id,
        output_payload={
            "vendor_id": str(vendor_id),
            "score_month": score_month.isoformat(),
            "overall": overall,
            "wo_avg": wo_avg,
            "invoice_ratio": ratio,
        },
    )
    await session.commit()
    return {
        "ok": True,
        "scorecard": {
            "id": str(card.id),
            "vendor_id": str(vendor_id),
            "score_month": score_month.isoformat(),
            "overall_score": overall,
            "wo_avg_before_invoice_blend": wo_avg,
            "trend_delta": trend,
            "component_breakdown": breakdown,
            "ppm_compliance_pct": ppm,
            "matched_flagged_ratio": ratio,
            "wo_count": len(rows),
            "block_capped": block_capped,
            "drilldown_wo_score_ids": [str(r.id) for r in rows],
        },
    }


async def list_scorecards(
    session: AsyncSession,
    *,
    vendor_id: UUID | None = None,
    organization_id: UUID | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    q = select(VendorMonthlyScorecard).order_by(VendorMonthlyScorecard.score_month.desc()).limit(limit)
    if vendor_id:
        q = q.where(VendorMonthlyScorecard.vendor_id == vendor_id)
    if organization_id:
        q = q.where(VendorMonthlyScorecard.organization_id == organization_id)
    rows = list((await session.execute(q)).scalars().all())

    # Resolve vendor names (vendors.id is varchar; scorecard.vendor_id is uuid → cast both to text).
    vendor_names: dict[str, str] = {}
    vids = [str(r.vendor_id) for r in rows if r.vendor_id]
    if vids:
        try:
            from sqlalchemy import bindparam

            stmt = text(
                "SELECT id::text AS id, vendor_name FROM plenum_cafm.vendors WHERE id::text IN :ids"
            ).bindparams(bindparam("ids", expanding=True))
            vrows = (await session.execute(stmt, {"ids": vids})).mappings().all()
            vendor_names = {str(v["id"]): v.get("vendor_name") for v in vrows if v.get("vendor_name")}
        except Exception as exc:  # noqa: BLE001
            log.warning("scorecards.vendor_name_lookup_failed", error=str(exc)[:200])

    return [
        {
            "id": str(r.id),
            "vendor_id": str(r.vendor_id),
            "vendor_name": vendor_names.get(str(r.vendor_id)),
            "score_month": r.score_month.isoformat(),
            "overall_score": float(r.overall_score) if r.overall_score is not None else None,
            "trend_delta": float(r.trend_delta) if r.trend_delta is not None else None,
            "component_breakdown": r.component_breakdown,
            "ppm_compliance_pct": float(r.ppm_compliance_pct) if r.ppm_compliance_pct is not None else None,
            "matched_flagged_ratio": float(r.matched_flagged_ratio) if r.matched_flagged_ratio is not None else None,
            "block_capped": r.block_capped,
            "wo_score_ids": r.wo_score_ids,
        }
        for r in rows
    ]


async def upsert_fm_staleness(
    session: AsyncSession,
    *,
    vendor_id: UUID | None,
    last_report_at: datetime | None,
    data_as_of: date | None,
    note: str | None = None,
    organization_id: UUID | None = None,
) -> dict[str, Any]:
    """PRD Q5 Option A — flag only when FM reports are stale."""
    row = FmReportStaleness(
        id=uuid4(),
        organization_id=organization_id,
        vendor_id=vendor_id,
        last_report_at=last_report_at,
        data_as_of=data_as_of,
        note=note or "FM report cadence: staleness flag only (no score penalty in v1.2)",
        updated_at=datetime.now(timezone.utc),
    )
    session.add(row)
    await session.commit()
    stale = False
    if data_as_of and (date.today() - data_as_of).days > 45:
        stale = True
        await enqueue_approval(
            session,
            source_feature="B",
            item_type="fm_report_staleness",
            summary=f"FM reports stale for vendor {vendor_id} — data as of {data_as_of}",
            severity="low",
            payload={"vendor_id": str(vendor_id) if vendor_id else None, "data_as_of": data_as_of.isoformat()},
            organization_id=organization_id,
        )
        await session.commit()
    return {"ok": True, "stale": stale, "id": str(row.id)}


def month_bounds(d: date) -> tuple[date, date]:
    last = monthrange(d.year, d.month)[1]
    return date(d.year, d.month, 1), date(d.year, d.month, last)


def _iso(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, date):
        return v.isoformat()
    return str(v)


async def _resolve_asset_id(session: AsyncSession, raw: Any) -> str | None:
    """Map UUID or asset_code → assets.id UUID string."""
    if raw is None or raw == "":
        return None
    s = str(raw).strip()
    try:
        return str(UUID(s))
    except Exception:
        pass
    try:
        async with session.begin_nested():
            row = (
                await session.execute(
                    text(
                        """
                        SELECT id::text AS id
                        FROM plenum_cafm.assets
                        WHERE asset_code = :code
                           OR name = :code
                        LIMIT 1
                        """
                    ),
                    {"code": s},
                )
            ).mappings().first()
            return row["id"] if row else None
    except Exception:  # noqa: BLE001
        return None


_WO_COLUMNS: dict[str, str] | None = None


async def _work_order_columns(session: AsyncSession) -> dict[str, str]:
    """column_name -> data_type for plenum_cafm.work_orders (cached per process)."""
    global _WO_COLUMNS
    if _WO_COLUMNS is None:
        rows = (
            await session.execute(
                text(
                    """
                    SELECT column_name, data_type
                    FROM information_schema.columns
                    WHERE table_schema = 'plenum_cafm' AND table_name = 'work_orders'
                    """
                )
            )
        ).all()
        _WO_COLUMNS = {name: dtype for name, dtype in rows}
    return _WO_COLUMNS


def _coalesce(cols: dict[str, str], *candidates: str, cast: str = "") -> str | None:
    """COALESCE over whichever candidate columns this tenant actually has.

    The cast is applied to each branch rather than the whole expression: the
    candidates often have different types (wo_uuid is uuid while id may be integer),
    and COALESCE requires every branch to share one type.
    """
    present = [c for c in candidates if c in cols]
    if not present:
        return None
    suffix = f"::{cast}" if cast else ""
    if len(present) == 1:
        return f"wo.{present[0]}{suffix}"
    return "COALESCE(" + ", ".join(f"wo.{c}{suffix}" for c in present) + ")"


async def _build_udr_wo_query(
    session: AsyncSession,
    *,
    vendor_id: UUID | None,
    organization_id: UUID | None,
    start: date | None,
    end: date | None,
    limit: int,
) -> tuple[str, dict[str, Any]]:
    """Build the completed-work-order SELECT and its bindings from live columns.

    The `work_orders` table differs substantially between tenants — the canonical ORM
    shape, the UDR shape and the svc-work-order-management shape share only a subset of
    columns. Referencing a missing column aborts the whole statement, which previously
    forced a fallback query that dropped first_fix, recall and labour hours and made
    every SLA look met. Selecting per-column keeps whatever the tenant does have.

    SQL and parameters are built together because the casts decide the binding types:
    asyncpg infers each parameter from its CAST and rejects a value of another type
    (a str where `CAST(:start_d AS date)` promised a date, for instance).
    """
    cols = await _work_order_columns(session)
    params: dict[str, Any] = {"lim": int(limit)}

    def expr(alias: str, *candidates: str, cast: str = "", default: str = "NULL") -> str:
        found = _coalesce(cols, *candidates, cast=cast)
        if found is None:
            return f"{default} AS {alias}"
        # A literal default still applies when the columns exist but are null.
        if not default.startswith("NULL"):
            found = f"COALESCE({found}, {default})"
        return f"{found} AS {alias}"

    # Prefer the stable wo_uuid handle so Feature B rows reference a real work order
    # even where work_orders.id is an integer serial.
    select_parts = [
        expr("id", "wo_uuid", "id", cast="text"),
        expr("wo_code", "wo_code", "workorder_ref", "work_order_id", "id", cast="text"),
        expr("status", "status", "wo_status", default="'Completed'"),
        expr("priority", "priority", "wo_priority", default="'P3'"),
        expr(
            "reported_at",
            "reported_at", "raised_date", "created_at", "created_date",
            cast="timestamptz",
        ),
        expr("attended_at", "attended_at", "responded_at", "response_at", cast="timestamptz"),
        expr("completed_at", "completed_at", cast="timestamptz"),
        expr("first_fix", "first_fix", default="NULL::boolean"),
        expr("recall", "recall", "return_visit", default="NULL::boolean"),
        expr("actual_cost", "actual_cost", "cost_actual", default="NULL::numeric"),
        expr("estimated_cost", "estimated_cost", "cost_estimated", default="NULL::numeric"),
        expr("asset_id", "asset_id", cast="text"),
        expr("vendor_id", "vendor_id", "assigned_vendor", cast="text"),
        expr("organization_id", "organization_id", cast="text"),
        expr("part_code", "part_code", cast="text"),
        expr("parts_cost", "parts_cost", default="NULL::numeric"),
        expr("labour_hours", "labour_hours", "actual_hours", default="NULL::numeric"),
    ]

    status_expr = _coalesce(cols, "status", "wo_status") or "'completed'"
    where = [
        "wo.completed_at IS NOT NULL",
        f"LOWER(COALESCE({status_expr}, 'completed')) "
        "IN ('completed', 'closed', 'complete', 'done')",
    ]
    # FR-039: exclude WOs flagged as conflicted until PM resolves them
    if "conflict_flag" in cols:
        where.append("(wo.conflict_flag IS NOT TRUE)")

    # vendor_id is UUID in every known shape; assigned_vendor may be text. Compare
    # against the column's own type so the index stays usable.
    if cols.get("vendor_id") == "uuid":
        where.append("(CAST(:vid AS uuid) IS NULL OR wo.vendor_id = CAST(:vid AS uuid))")
        params["vid"] = vendor_id
    elif "vendor_id" in cols:
        where.append("(CAST(:vid AS text) IS NULL OR wo.vendor_id::text = CAST(:vid AS text))")
        params["vid"] = str(vendor_id) if vendor_id else None
    else:
        where.append(
            "(CAST(:vid AS text) IS NULL OR wo.assigned_vendor::text = CAST(:vid AS text))"
        )
        params["vid"] = str(vendor_id) if vendor_id else None

    # Only filter on organization when it is a UUID; an integer organization_id
    # belongs to a different key space and would exclude every row.
    if cols.get("organization_id") == "uuid":
        where.append(
            "(CAST(:oid AS uuid) IS NULL OR wo.organization_id = CAST(:oid AS uuid) "
            "OR wo.organization_id IS NULL)"
        )
        params["oid"] = organization_id

    where.append(
        "(CAST(:start_d AS date) IS NULL OR wo.completed_at::date "
        "BETWEEN CAST(:start_d AS date) AND CAST(:end_d AS date))"
    )
    params["start_d"] = start
    params["end_d"] = end

    sql = (
        "SELECT\n  "
        + ",\n  ".join(select_parts)
        + "\nFROM plenum_cafm.work_orders wo\nWHERE "
        + "\n  AND ".join(where)
        + "\nORDER BY wo.completed_at ASC\nLIMIT :lim"
    )
    return sql, params


async def fetch_completed_work_orders_from_udr(
    session: AsyncSession,
    *,
    vendor_id: UUID | None = None,
    organization_id: UUID | None = None,
    score_month: date | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """
    Load completed FM work orders from Phase 1 UDR (`plenum_cafm.work_orders`)
    for Feature B scoring. Column names COALESCE across ontology + Feature B aliases.
    """
    start: date | None = None
    end: date | None = None
    if score_month is not None:
        start, end = month_bounds(score_month)

    sql, params = await _build_udr_wo_query(
        session,
        vendor_id=vendor_id,
        organization_id=organization_id,
        start=start,
        end=end,
        limit=limit,
    )
    # No reduced-column fallback: the query already degrades per column, and the
    # old fallback dropped first_fix, recall and labour hours, which silently
    # turned every SLA into a pass. A failure here is a bug worth seeing.
    # The savepoint keeps a failure from aborting the caller's transaction.
    try:
        async with session.begin_nested():
            rows = (await session.execute(text(sql), params)).mappings().all()
    except Exception as exc:  # noqa: BLE001
        log.warning("udr.wo_fetch_failed", error=str(exc)[:240], sql=sql[:400])
        return []

    out: list[dict[str, Any]] = []
    for r in rows:
        asset_id = await _resolve_asset_id(session, r.get("asset_id"))
        out.append(
            {
                "work_order_id": r["id"],
                "id": r["id"],
                "wo_code": r["wo_code"],
                "status": r["status"],
                "priority": r["priority"],
                "reported_at": _iso(r["reported_at"]),
                "attended_at": _iso(r["attended_at"]),
                "completed_at": _iso(r["completed_at"]),
                "first_fix": r["first_fix"],
                "recall": r["recall"],
                "cost_actual": float(r["actual_cost"]) if r["actual_cost"] is not None else None,
                "cost_estimated": float(r["estimated_cost"]) if r["estimated_cost"] is not None else None,
                "actual_cost": float(r["actual_cost"]) if r["actual_cost"] is not None else None,
                "estimated_cost": float(r["estimated_cost"]) if r["estimated_cost"] is not None else None,
                "asset_id": asset_id,
                "vendor_id": r["vendor_id"],
                "organization_id": r["organization_id"],
                "part_code": r.get("part_code"),
                "parts_cost": float(r["parts_cost"]) if r.get("parts_cost") is not None else None,
                "labour_hours": float(r["labour_hours"]) if r.get("labour_hours") is not None else None,
            }
        )
    return out


async def list_udr_score_months(
    session: AsyncSession,
    *,
    vendor_id: UUID | None = None,
    organization_id: UUID | None = None,
) -> list[dict[str, Any]]:
    """Distinct vendor × month buckets available in UDR completed WOs."""
    try:
        cols = await _work_order_columns(session)
        # Skip the organization filter unless organization_id is a UUID here; an
        # integer organization_id is a different key space and matches nothing.
        org_clause = (
            "AND (CAST(:oid AS uuid) IS NULL OR wo.organization_id = CAST(:oid AS uuid) "
            "OR wo.organization_id IS NULL)"
            if cols.get("organization_id") == "uuid"
            else ""
        )
        # UUID objects, not strings: the CAST tells asyncpg to expect uuid.
        params: dict[str, Any] = {"vid": vendor_id}
        # Only bound when the clause is present — asyncpg cannot type a parameter
        # the statement never references.
        if org_clause:
            params["oid"] = organization_id

        sql = f"""
            SELECT
              wo.vendor_id::text AS vendor_id,
              date_trunc('month', wo.completed_at)::date AS score_month,
              COUNT(*)::int AS wo_count
            FROM plenum_cafm.work_orders wo
            WHERE wo.completed_at IS NOT NULL
              AND wo.vendor_id IS NOT NULL
              AND (CAST(:vid AS uuid) IS NULL OR wo.vendor_id = CAST(:vid AS uuid))
              {org_clause}
            GROUP BY 1, 2
            ORDER BY 2 DESC, 1
            LIMIT 120
        """
        async with session.begin_nested():
            rows = (await session.execute(text(sql), params)).mappings().all()
        return [
            {
                "vendor_id": r["vendor_id"],
                "score_month": r["score_month"].isoformat()
                if hasattr(r["score_month"], "isoformat")
                else str(r["score_month"]),
                "wo_count": int(r["wo_count"]),
            }
            for r in rows
        ]
    except Exception as exc:  # noqa: BLE001
        log.warning("udr.list_months_failed", error=str(exc)[:200])
        return []


async def score_work_orders_from_udr(
    session: AsyncSession,
    *,
    vendor_id: UUID | None = None,
    organization_id: UUID | None = None,
    score_month: date | None = None,
    limit: int = 500,
    generate_scorecard: bool = True,
    allow_default_parameters: bool = False,
) -> dict[str, Any]:
    """B2 — Read completed WOs from UDR migration tables, score, optionally scorecard."""
    work_orders = await fetch_completed_work_orders_from_udr(
        session,
        vendor_id=vendor_id,
        organization_id=organization_id,
        score_month=score_month,
        limit=limit,
    )
    if not work_orders:
        return {
            "ok": True,
            "source": "udr",
            "count": 0,
            "scores": [],
            "message": "No completed work orders found in plenum_cafm.work_orders for filters",
        }

    # Infer vendor/month from first row when omitted
    if vendor_id is None and work_orders[0].get("vendor_id"):
        try:
            vendor_id = UUID(str(work_orders[0]["vendor_id"]))
        except Exception:
            pass
    if score_month is None and work_orders[0].get("completed_at"):
        try:
            completed = _parse_dt(work_orders[0]["completed_at"])
            if completed:
                score_month = date(completed.year, completed.month, 1)
        except Exception:
            pass

    scored = await score_completed_work_orders(
        session,
        work_orders,
        vendor_id=vendor_id,
        organization_id=organization_id,
        score_month=score_month,
        allow_default_parameters=allow_default_parameters,
    )
    scored["source"] = "udr"
    scored["udr_wo_count"] = len(work_orders)

    # A blocked run produced no scores, so there is nothing to build a scorecard from.
    if scored.get("ok") is False:
        return scored

    if generate_scorecard and vendor_id and score_month:
        card = await generate_monthly_scorecard(
            session,
            vendor_id=vendor_id,
            score_month=score_month,
            organization_id=organization_id,
            ppm_visits=None,  # load from UDR inside generate
        )
        scored["scorecard"] = card.get("scorecard") or card
    return scored


async def score_all_from_udr(
    session: AsyncSession,
    *,
    organization_id: UUID | None = None,
    vendor_id: UUID | None = None,
    limit_per_month: int = 500,
    allow_default_parameters: bool = False,
) -> dict[str, Any]:
    """Score every vendor×month bucket present in migrated UDR work orders."""
    buckets = await list_udr_score_months(
        session, vendor_id=vendor_id, organization_id=organization_id
    )
    results: list[dict[str, Any]] = []
    for b in buckets:
        try:
            vid = UUID(str(b["vendor_id"]))
            sm = date.fromisoformat(str(b["score_month"])[:10])
        except Exception:
            continue
        one = await score_work_orders_from_udr(
            session,
            vendor_id=vid,
            organization_id=organization_id,
            score_month=sm,
            limit=limit_per_month,
            generate_scorecard=True,
            allow_default_parameters=allow_default_parameters,
        )
        results.append(
            {
                "vendor_id": str(vid),
                "score_month": sm.isoformat(),
                "udr_wo_count": b["wo_count"],
                "scored": one.get("count") or one.get("udr_wo_count") or 0,
                "ok": bool(one.get("ok", True)),
            }
        )
    return {
        "ok": True,
        "source": "udr",
        "buckets": len(results),
        "results": results,
    }
