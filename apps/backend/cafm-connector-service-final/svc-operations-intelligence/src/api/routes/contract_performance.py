"""Contract Performance API — Features B1–B3."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ...db import get_session
from ...engines.contract_performance import conflicts as conflicts_svc
from ...engines.contract_performance import extract as extract_svc
from ...engines.contract_performance import insights as insights_svc
from ...engines.contract_performance import invoice as invoice_svc
from ...engines.contract_performance import parameters as params_svc
from ...engines.contract_performance import scoring as score_svc
from ...shared import approvals as approvals_svc
from ..schemas.contract_performance import (
    AssetCriticalityApproveRequest,
    AssetCriticalityRequest,
    ContractConfirmRequest,
    ContractExtractRequest,
    ContractIngestRequest,
    ContractUpdateRequest,
    FmStalenessRequest,
    InvoiceExtractVerifyRequest,
    InvoiceLineDecisionRequest,
    InvoiceVerifyRequest,
    QueueDecisionRequest,
    ScorecardRequest,
    ScoreFromUdrRequest,
    ScoreWorkOrdersRequest,
    UdrCriticalityRequest,
    InsightsResponse,
    WeightsUpdateRequest,
    WorkOrderConflictResolveRequest,
)

router = APIRouter(prefix="/api/contract-performance", tags=["contract-performance"])


# ── B1 Contract parameters ──────────────────────────────────────────


@router.post("/contracts/extract")
async def extract_contract(
    body: ContractExtractRequest,
    session: AsyncSession = Depends(get_session),
):
    """Relationships Agent handoff — extract from document text then draft-ingest."""
    return await extract_svc.extract_contract_parameters(
        session,
        source_text=body.source_text,
        pdf_base64=body.pdf_base64,
        extracted_fields=body.extracted_fields or None,
        organization_id=body.organization_id,
        vendor_id=body.vendor_id,
        document_id=body.document_id,
        contract_ref=body.contract_ref,
        signed_date=body.signed_date,
        auto_ingest=body.auto_ingest,
    )


@router.post("/migration/reconcile")
async def reconcile_migration(
    body: dict | None = None,
    session: AsyncSession = Depends(get_session),
):
    """Post-migration repair — deduplicate and vendor-link rows a migration just wrote.

    Body: ``{"vendor_links": {"work_orders": {wo_code: vendor_name}, ...}}``. Tables that
    keep vendor_name on the row resolve themselves and need no input.
    """
    from ...engines.contract_performance.post_migration import (
        reconcile_feature_b_migration,
    )

    payload = body or {}
    return await reconcile_feature_b_migration(
        session, vendor_links=payload.get("vendor_links") or None
    )


@router.post("/contracts/ingest")
async def ingest_contract(
    body: ContractIngestRequest,
    session: AsyncSession = Depends(get_session),
):
    return await params_svc.ingest_contract_parameters(
        session,
        extracted=body.extracted,
        organization_id=body.organization_id,
        vendor_id=body.vendor_id,
        vendor_name=body.vendor_name,
        contract_id=body.contract_id,
        document_id=body.document_id,
        contract_ref=body.contract_ref,
    )


@router.get("/contracts")
async def list_contracts(
    organization_id: UUID | None = None,
    vendor_id: UUID | None = None,
    status: str | None = None,
    limit: int = Query(100, le=500),
    session: AsyncSession = Depends(get_session),
):
    rows = await params_svc.list_contract_parameters(
        session,
        organization_id=organization_id,
        vendor_id=vendor_id,
        status=status,
        limit=limit,
    )
    return {"ok": True, "count": len(rows), "parameters": rows}


@router.get("/contracts/{parameters_id}")
async def get_contract(
    parameters_id: UUID,
    session: AsyncSession = Depends(get_session),
):
    return await params_svc.get_contract_parameters(session, parameters_id)


@router.patch("/contracts/{parameters_id}")
async def update_contract(
    parameters_id: UUID,
    body: ContractUpdateRequest,
    session: AsyncSession = Depends(get_session),
):
    return await params_svc.update_contract_parameters(
        session, parameters_id, body.updates, actor=body.actor
    )


@router.post("/contracts/{parameters_id}/confirm")
async def confirm_contract(
    parameters_id: UUID,
    body: ContractConfirmRequest,
    session: AsyncSession = Depends(get_session),
):
    return await params_svc.confirm_contract_parameters(
        session, parameters_id, confirmed_by=body.confirmed_by
    )


# ── B1 Asset criticality ────────────────────────────────────────────


@router.post("/asset-criticality")
async def upsert_criticality(
    body: AssetCriticalityRequest,
    session: AsyncSession = Depends(get_session),
):
    return await params_svc.upsert_asset_criticality(
        session,
        asset_id=body.asset_id,
        asset_code=body.asset_code,
        organization_id=body.organization_id,
        proposed=body.proposed,
        load_dependence=body.load_dependence,
        function_type=body.function_type,
        sub_meter_high=body.sub_meter_high,
        source=body.source,
    )


@router.post("/asset-criticality/{criticality_id}/approve")
async def approve_criticality(
    criticality_id: UUID,
    body: AssetCriticalityApproveRequest,
    session: AsyncSession = Depends(get_session),
):
    return await params_svc.approve_asset_criticality(
        session,
        criticality_id,
        criticality=body.criticality,
        approved_by=body.approved_by,
    )


@router.post("/asset-criticality/propose-from-udr")
async def propose_from_udr(
    body: UdrCriticalityRequest,
    session: AsyncSession = Depends(get_session),
):
    return await params_svc.propose_criticality_from_udr(
        session, organization_id=body.organization_id, limit=body.limit
    )


@router.get("/asset-criticality")
async def list_criticalities(
    organization_id: UUID | None = None,
    approved: bool | None = None,
    limit: int = Query(200, le=500),
    session: AsyncSession = Depends(get_session),
):
    rows = await params_svc.list_asset_criticalities(
        session, organization_id=organization_id, approved=approved, limit=limit
    )
    return {"ok": True, "count": len(rows), "items": rows}


# ── B2 Scoring / admin weights / scorecards ─────────────────────────


@router.get("/admin/weights")
async def get_weights(
    organization_id: UUID | None = None,
    session: AsyncSession = Depends(get_session),
):
    return {"ok": True, "weights": await score_svc.get_or_create_weights(session, organization_id)}


@router.put("/admin/weights")
async def put_weights(
    body: WeightsUpdateRequest,
    session: AsyncSession = Depends(get_session),
):
    updates = body.model_dump(exclude_none=True)
    org = updates.pop("organization_id", None)
    result = await score_svc.update_weights(session, updates, organization_id=org)
    if not result.get("ok"):
        raise HTTPException(status_code=422, detail=result)
    return result


@router.post("/score/work-orders")
async def score_work_orders(
    body: ScoreWorkOrdersRequest,
    session: AsyncSession = Depends(get_session),
):
    return await score_svc.score_completed_work_orders(
        session,
        body.work_orders,
        vendor_id=body.vendor_id,
        organization_id=body.organization_id,
        score_month=body.score_month,
    )


@router.post("/score/from-udr")
async def score_from_udr(
    body: ScoreFromUdrRequest,
    session: AsyncSession = Depends(get_session),
):
    """
    B2 — Score work orders ingested via Phase 1 UDR migration
    (`plenum_cafm.work_orders`). Prefer this after CSV/XLS migration instead of
    posting hand-crafted score batches.
    """
    if body.all_buckets:
        return await score_svc.score_all_from_udr(
            session,
            organization_id=body.organization_id,
            vendor_id=body.vendor_id,
            limit_per_month=body.limit,
            allow_default_parameters=body.allow_default_parameters,
        )
    return await score_svc.score_work_orders_from_udr(
        session,
        vendor_id=body.vendor_id,
        organization_id=body.organization_id,
        score_month=body.score_month,
        limit=body.limit,
        generate_scorecard=body.generate_scorecard,
        allow_default_parameters=body.allow_default_parameters,
    )


@router.get("/udr/work-order-buckets")
async def udr_work_order_buckets(
    vendor_id: UUID | None = None,
    organization_id: UUID | None = None,
    session: AsyncSession = Depends(get_session),
):
    """List vendor×month WO buckets available in migrated UDR tables."""
    buckets = await score_svc.list_udr_score_months(
        session, vendor_id=vendor_id, organization_id=organization_id
    )
    return {"ok": True, "count": len(buckets), "buckets": buckets}


@router.post("/scorecards/monthly")
async def monthly_scorecard(
    body: ScorecardRequest,
    session: AsyncSession = Depends(get_session),
):
    return await score_svc.generate_monthly_scorecard(
        session,
        vendor_id=body.vendor_id,
        score_month=body.score_month,
        organization_id=body.organization_id,
        ppm_visits=body.ppm_visits,
    )


@router.get("/scorecards")
async def list_scorecards(
    vendor_id: UUID | None = None,
    organization_id: UUID | None = None,
    limit: int = Query(50, le=200),
    session: AsyncSession = Depends(get_session),
):
    rows = await score_svc.list_scorecards(
        session, vendor_id=vendor_id, organization_id=organization_id, limit=limit
    )
    return {"ok": True, "count": len(rows), "scorecards": rows}


@router.get("/saved-space/summary")
async def vendors_saved_space_summary(
    organization_id: UUID | None = None,
    session: AsyncSession = Depends(get_session),
):
    """Vendors Saved Space — scorecards + pending Feature B approvals + weights."""
    # 20 hid any vendor whose scored months are older than the newest 20 rows
    # portfolio-wide (e.g. a vendor scored from 2023 reports vanished behind
    # three vendors' 2026 months). 200 covers years of monthly cards.
    cards = await score_svc.list_scorecards(
        session, organization_id=organization_id, limit=200
    )
    weights = await score_svc.get_or_create_weights(session, organization_id)
    approvals = await approvals_svc.list_queue(
        session, source_feature="B", status="pending", organization_id=organization_id
    )
    crit = await params_svc.list_asset_criticalities(
        session, organization_id=organization_id, approved=False, limit=50
    )
    return {
        "ok": True,
        "themes": {"vendors": "amber"},
        "scorecards": cards,
        "weights": weights,
        "pending_approvals": len(approvals),
        "approvals": [approvals_svc.queue_item_to_dict(i) for i in approvals[:10]],
        "unapproved_criticalities": crit,
        "kpis": {
            "scorecards_count": len(cards),
            "avg_overall": (
                round(
                    sum(c["overall_score"] for c in cards if c.get("overall_score") is not None)
                    / max(1, sum(1 for c in cards if c.get("overall_score") is not None)),
                    2,
                )
                if cards
                else None
            ),
            "pending_approvals": len(approvals),
            "unapproved_asset_criticalities": len(crit),
        },
    }


@router.post("/fm-staleness")
async def fm_staleness(
    body: FmStalenessRequest,
    session: AsyncSession = Depends(get_session),
):
    return await score_svc.upsert_fm_staleness(
        session,
        vendor_id=body.vendor_id,
        last_report_at=body.last_report_at,
        data_as_of=body.data_as_of,
        note=body.note,
        organization_id=body.organization_id,
    )


# ── B3 Invoice verification ─────────────────────────────────────────


@router.post("/invoices/extract-verify")
async def extract_verify_invoice(
    body: InvoiceExtractVerifyRequest,
    session: AsyncSession = Depends(get_session),
):
    """Orchestrator invoice upload — parse lines then verify vs ingested WOs."""
    return await extract_svc.extract_and_verify_invoice(
        session,
        source_text=body.source_text,
        lines=body.lines or None,
        work_orders=body.work_orders or None,
        invoice_ref=body.invoice_ref,
        invoice_ref_fallback=body.invoice_ref_fallback,
        vendor_id=body.vendor_id,
        organization_id=body.organization_id,
        document_id=body.document_id,
        labour_day_rate=body.labour_day_rate,
        labour_hour_rate=body.labour_hour_rate,
        parts_pricing_json=body.parts_pricing_json,
    )


@router.get("/invoices")
async def list_invoices(
    organization_id: UUID | None = None,
    building_id: UUID | None = Query(
        None, description="Only invoices filed against this building."
    ),
    vendor_id: UUID | None = None,
    invoice_ref: str | None = Query(
        None, description="Invoice number, partial and case-insensitive."
    ),
    status: str | None = None,
    limit: int = Query(100, le=500),
    session: AsyncSession = Depends(get_session),
):
    """Verified invoices with their building and vendor. Read-only.

    There was no way to read an invoice back: the feature exposed verify and decide and
    nothing that lists, so a question about a building's invoices could only be answered
    "none found" however many it had.
    """
    rows = await invoice_svc.list_invoices(
        session,
        organization_id=organization_id,
        building_id=building_id,
        vendor_id=vendor_id,
        invoice_ref=invoice_ref,
        status=status,
        limit=limit,
    )
    return {"ok": True, "count": len(rows), "invoices": rows}


@router.post("/invoices/verify")
async def verify_invoice(
    body: InvoiceVerifyRequest,
    session: AsyncSession = Depends(get_session),
):
    return await invoice_svc.verify_invoice(
        session,
        invoice_ref=body.invoice_ref,
        lines=body.lines,
        work_orders=body.work_orders,
        vendor_id=body.vendor_id,
        organization_id=body.organization_id,
        document_id=body.document_id,
        labour_day_rate=body.labour_day_rate,
        labour_hour_rate=body.labour_hour_rate,
        parts_pricing_json=body.parts_pricing_json,
    )


@router.post("/invoices/{verification_id}/lines/decide")
async def decide_line(
    verification_id: UUID,
    body: InvoiceLineDecisionRequest,
    session: AsyncSession = Depends(get_session),
):
    return await invoice_svc.decide_invoice_line(
        session,
        verification_id,
        line_id=body.line_id,
        decision=body.decision,
        pm_notes=body.pm_notes,
    )


# ── Approvals (Feature B) ───────────────────────────────────────────


@router.get("/approvals")
async def list_approvals(
    status: str = "pending",
    organization_id: UUID | None = None,
    session: AsyncSession = Depends(get_session),
):
    items = await approvals_svc.list_queue(
        session,
        source_feature="B",
        status=status,
        organization_id=organization_id,
    )
    return {
        "ok": True,
        "count": len(items),
        "items": [approvals_svc.queue_item_to_dict(i) for i in items],
    }


@router.post("/approvals/{item_id}/decide")
async def decide_approval(
    item_id: UUID,
    body: QueueDecisionRequest,
    session: AsyncSession = Depends(get_session),
):
    return await approvals_svc.decide_queue_item(
        session,
        item_id,
        decision=body.decision,
        pm_notes=body.pm_notes,
        prepare_email_handoff=body.prepare_email_handoff,
    )


# ── B Cross-cutting: WO conflict resolution (FR-039) ────────────────


@router.post("/work-orders/{wo_code}/resolve-conflict")
async def resolve_wo_conflict(
    wo_code: str,
    body: WorkOrderConflictResolveRequest,
    organization_id: UUID | None = Query(None),
    session: AsyncSession = Depends(get_session),
):
    """FR-039: PM accepts stored or incoming values for a conflicted WO."""
    return await conflicts_svc.resolve_wo_conflict(
        session,
        wo_code=wo_code,
        accept=body.accept,
        resolved_by=body.resolved_by,
        note=body.note,
        organization_id=organization_id,
    )


# ── B5 Insights (FR-028) ────────────────────────────────────────────


@router.get("/insights", response_model=InsightsResponse)
async def get_insights(
    vendor_id: UUID | None = Query(None),
    organization_id: UUID | None = Query(None),
    from_date: str | None = Query(None, description="YYYY-MM-DD"),
    to_date: str | None = Query(None, description="YYYY-MM-DD"),
    session: AsyncSession = Depends(get_session),
):
    """FR-028: cost variance, labour variance, and matched/flagged trend for a vendor."""
    from datetime import date as _date

    fd = _date.fromisoformat(from_date) if from_date else None
    td = _date.fromisoformat(to_date) if to_date else None
    result = await insights_svc.compute_insights(
        session,
        vendor_id=vendor_id,
        organization_id=organization_id,
        from_date=fd,
        to_date=td,
    )
    return result
