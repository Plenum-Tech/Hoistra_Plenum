"""
Contract Performance agent tools — wraps svc-operations-intelligence (port 8009).

PRD tool agent: contract-performance-engine → Orchestrator agent id `contract_performance` (Feature B).
Intent keywords (SSOT: phase2_intents.CONTRACT_PERFORMANCE_INTENT_KEYWORDS):
  SLA, contractor, performance, KPI, PPM completion rate, vendor score, first fix,
  recall, invoice, overrun, contract breach.

Phase 2 Feature B (B1–B3):
  - ingest_contract_parameters / update / confirm
  - upsert_asset_criticality / approve
  - score_vendor_work_orders
  - generate_vendor_scorecard / list_scorecards
  - list_contract_parameters (read-only: SLA targets, rates, KPI clauses, PPM obligations)
  - get/update_score_weights (admin)
  - verify_invoice / decide_invoice_line
  - list_contract_approvals / decide_contract_approval
"""
from __future__ import annotations

from typing import Any

import httpx
import structlog
from langchain_core.tools import tool

from ..config import settings
from ..http_client import request as _request

log = structlog.get_logger(__name__)

_TIMEOUT = 60.0
_SERVICE = "operations_intelligence"


def _base() -> str:
    return settings.operations_intelligence_base_url.rstrip("/")


def _err(exc: Exception, op: str) -> dict:
    if isinstance(exc, httpx.HTTPStatusError):
        body = exc.response.text or ""
        log.error(
            "contract_performance.http_error",
            operation=op,
            status_code=exc.response.status_code,
            body=body[:2000],
        )
        return {"error": body[:2000], "status_code": exc.response.status_code}
    log.error("contract_performance.error", operation=op, error=str(exc), exc_info=True)
    return {"error": str(exc)[:2000]}


@tool
async def ingest_contract_parameters(
    extracted: dict | None = None,
    vendor_id: str | None = None,
    vendor_name: str | None = None,
    contract_ref: str | None = None,
    organization_id: str | None = None,
    document_id: str | None = None,
) -> dict:
    """B1 — Ingest extracted FM contract SLA parameters (draft for PM edit/confirm).

    Fills silent fields with system defaults labelled 'Default — not contract-sourced'.

    Pass the vendor's name in ``vendor_name`` when the platform id is unknown — the service
    matches it against the register and registers a new vendor if there is no match. A name
    passed in ``vendor_id`` is also accepted rather than rejected.
    """
    try:
        resp = await _request(
            "POST",
            _base(),
            "/api/contract-performance/contracts/ingest",
            service=_SERVICE,
            timeout=_TIMEOUT,
            json={
                "extracted": extracted or {},
                "vendor_id": vendor_id,
                "vendor_name": vendor_name,
                "contract_ref": contract_ref,
                "organization_id": organization_id,
                "document_id": document_id,
            },
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "ingest_contract_parameters")


@tool
async def update_contract_parameters(
    parameters_id: str,
    updates: dict,
    actor: str = "pm",
) -> dict:
    """B1 — PM inline override of contract SLA fields (logged)."""
    try:
        resp = await _request(
            "PATCH",
            _base(),
            f"/api/contract-performance/contracts/{parameters_id}",
            service=_SERVICE,
            timeout=_TIMEOUT,
            json={"updates": updates, "actor": actor},
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "update_contract_parameters")


@tool
async def confirm_contract_parameters(
    parameters_id: str,
    confirmed_by: str | None = None,
) -> dict:
    """B1 — Confirm contract SLA parameters after PM review."""
    try:
        resp = await _request(
            "POST",
            _base(),
            f"/api/contract-performance/contracts/{parameters_id}/confirm",
            service=_SERVICE,
            timeout=_TIMEOUT,
            json={"confirmed_by": confirmed_by},
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "confirm_contract_parameters")


@tool
async def propose_asset_criticality(
    asset_id: str,
    asset_code: str | None = None,
    load_dependence: str | None = None,
    function_type: str | None = None,
    sub_meter_high: bool = False,
    organization_id: str | None = None,
) -> dict:
    """B1 — Propose L1/L2/L3 asset criticality from UDR signals (HITL required)."""
    try:
        resp = await _request(
            "POST",
            _base(),
            "/api/contract-performance/asset-criticality",
            service=_SERVICE,
            timeout=_TIMEOUT,
            json={
                "asset_id": asset_id,
                "asset_code": asset_code,
                "load_dependence": load_dependence,
                "function_type": function_type,
                "sub_meter_high": sub_meter_high,
                "organization_id": organization_id,
            },
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "propose_asset_criticality")


@tool
async def approve_asset_criticality(
    criticality_id: str,
    criticality: str | None = None,
    approved_by: str | None = None,
) -> dict:
    """B1 — PM approve/edit asset criticality (unapproved assets score as L2)."""
    try:
        resp = await _request(
            "POST",
            _base(),
            f"/api/contract-performance/asset-criticality/{criticality_id}/approve",
            service=_SERVICE,
            timeout=_TIMEOUT,
            json={"criticality": criticality, "approved_by": approved_by},
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "approve_asset_criticality")


@tool
async def score_vendor_work_orders(
    work_orders: list[dict],
    vendor_id: str | None = None,
    organization_id: str | None = None,
    score_month: str | None = None,
) -> dict:
    """B2 — Score ingested completed work orders against contract SLA for a vendor.

    Components: SLA response 25%, completion 25%, first fix 20%, recall 15%,
    accreditation 15%. Blocked vendors capped at 60.
    Prefer `score_work_orders_from_udr` when WOs were migrated via Phase 1 UDR CSV.
    """
    try:
        resp = await _request(
            "POST",
            _base(),
            "/api/contract-performance/score/work-orders",
            service=_SERVICE,
            timeout=_TIMEOUT,
            json={
                "work_orders": work_orders,
                "vendor_id": vendor_id,
                "organization_id": organization_id,
                "score_month": score_month,
            },
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "score_vendor_work_orders")


@tool
async def score_work_orders_from_udr(
    vendor_id: str | None = None,
    organization_id: str | None = None,
    score_month: str | None = None,
    all_buckets: bool = True,
    limit: int = 500,
) -> dict:
    """B2 — Score completed work orders from Phase 1 UDR migration tables
    (`plenum_cafm.work_orders` written by CSV/XLS migration).

    Use after FM company reports are migrated via Single Door / Schema Mapper.
    Set all_buckets=true to score every vendor×month present in UDR.
    """
    try:
        resp = await _request(
            "POST",
            _base(),
            "/api/contract-performance/score/from-udr",
            service=_SERVICE,
            timeout=max(_TIMEOUT, 180.0),
            json={
                "vendor_id": vendor_id,
                "organization_id": organization_id,
                "score_month": score_month,
                "all_buckets": all_buckets,
                "limit": limit,
                "generate_scorecard": True,
            },
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "score_work_orders_from_udr")


@tool
async def generate_vendor_scorecard(
    vendor_id: str,
    score_month: str | None = None,
    ppm_visits: list[dict] | None = None,
    organization_id: str | None = None,
) -> dict:
    """B2 — Generate monthly vendor scorecard (overall, trend, components, PPM)."""
    try:
        resp = await _request(
            "POST",
            _base(),
            "/api/contract-performance/scorecards/monthly",
            service=_SERVICE,
            timeout=_TIMEOUT,
            json={
                "vendor_id": vendor_id,
                "score_month": score_month,
                "ppm_visits": ppm_visits or [],
                "organization_id": organization_id,
            },
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "generate_vendor_scorecard")


@tool
async def list_contract_parameters(
    status: str | None = None,
    organization_id: str | None = None,
) -> dict:
    """B1 — List extracted contract parameters: SLA response/completion targets by priority
    (P1/P2/P3/P4, in hours), labour day rate, overtime rate, call-out rate, payment terms,
    parts pricing framework, KPI penalty/bonus clauses, PPM schedule obligations, task
    criticality (L1/L2/L3), per contract with its vendor_id and draft/confirmed status.

    **Call this for ANY question about contract terms, SLA targets/hours, rates, payment
    terms, KPI clauses or PPM obligations** — e.g. "which vendor has SLA completion under
    25h for P2" is answered by comparing `sla_completion_p2_hours` across the returned
    `parameters`. Read-only. `status` filters to "draft" or "confirmed" (omit for all).

    Each row carries `vendor_name` — ALWAYS refer to vendors by name in answers
    (fall back to the contract_ref); never present a raw vendor_id UUID to the user.

    Each row also carries `building_name` and `building_reference` — the property the
    contract covers, resolved through the document it was extracted from. Use these to
    answer "what contracts are on <building>". A null building_name means that contract
    could not be placed against a property, NOT that it belongs to whichever building was
    asked about: say it is unplaced rather than attributing it.
    """
    try:
        params: dict[str, Any] = {}
        if status:
            params["status"] = status
        if organization_id:
            params["organization_id"] = organization_id
        resp = await _request(
            "GET",
            _base(),
            "/api/contract-performance/contracts",
            service=_SERVICE,
            timeout=_TIMEOUT,
            params=params,
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "list_contract_parameters")


@tool
async def list_vendor_scorecards(
    vendor_id: str | None = None,
    organization_id: str | None = None,
    limit: int = 50,
) -> dict:
    """B2 — List vendor monthly scorecards: overall score, SLA response/completion, first-fix,
    recall, accreditation breakdown, PPM ±7d and month-on-month trend.

    **Call this for ANY question about vendor performance / scorecards / "how are the vendors
    performing" / SLA / KPI** — no vendor_id needed to list all vendors. The UI renders the
    returned `scorecards` as dashboard cards in the center chat, so pull the live numbers rather
    than describing them from memory.
    """
    try:
        params: dict[str, Any] = {"limit": limit}
        if vendor_id:
            params["vendor_id"] = vendor_id
        if organization_id:
            params["organization_id"] = organization_id
        resp = await _request(
            "GET",
            _base(),
            "/api/contract-performance/scorecards",
            service=_SERVICE,
            timeout=_TIMEOUT,
            params=params,
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "list_vendor_scorecards")


@tool
async def get_score_weights(organization_id: str | None = None) -> dict:
    """B2 admin — get vendor score component weights (editable)."""
    try:
        params: dict[str, Any] = {}
        if organization_id:
            params["organization_id"] = organization_id
        resp = await _request(
            "GET",
            _base(),
            "/api/contract-performance/admin/weights",
            service=_SERVICE,
            timeout=_TIMEOUT,
            params=params,
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "get_score_weights")


@tool
async def update_score_weights(
    sla_response_pct: float | None = None,
    sla_completion_pct: float | None = None,
    first_fix_pct: float | None = None,
    recall_pct: float | None = None,
    accreditation_pct: float | None = None,
    blocked_score_cap: float | None = None,
    cost_variance_alert_pct: float | None = None,
    cost_variance_job_count: int | None = None,
    invoice_flag_adversary_gbp: float | None = None,
    organization_id: str | None = None,
) -> dict:
    """B2 admin — update all vendor score parameter components."""
    try:
        payload: dict[str, Any] = {"organization_id": organization_id}
        for k, v in {
            "sla_response_pct": sla_response_pct,
            "sla_completion_pct": sla_completion_pct,
            "first_fix_pct": first_fix_pct,
            "recall_pct": recall_pct,
            "accreditation_pct": accreditation_pct,
            "blocked_score_cap": blocked_score_cap,
            "cost_variance_alert_pct": cost_variance_alert_pct,
            "cost_variance_job_count": cost_variance_job_count,
            "invoice_flag_adversary_gbp": invoice_flag_adversary_gbp,
        }.items():
            if v is not None:
                payload[k] = v
        resp = await _request(
            "PUT",
            _base(),
            "/api/contract-performance/admin/weights",
            service=_SERVICE,
            timeout=_TIMEOUT,
            json=payload,
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "update_score_weights")


@tool
async def verify_vendor_invoice(
    lines: list[dict],
    work_orders: list[dict],
    invoice_ref: str | None = None,
    vendor_id: str | None = None,
    labour_day_rate: float | None = None,
    parts_pricing_json: dict | None = None,
    organization_id: str | None = None,
) -> dict:
    """B3 — Verify invoice lines against ingested WOs; flags >£500 via Adversary."""
    try:
        resp = await _request(
            "POST",
            _base(),
            "/api/contract-performance/invoices/verify",
            service=_SERVICE,
            timeout=_TIMEOUT,
            json={
                "invoice_ref": invoice_ref,
                "lines": lines,
                "work_orders": work_orders,
                "vendor_id": vendor_id,
                "labour_day_rate": labour_day_rate,
                "parts_pricing_json": parts_pricing_json or {},
                "organization_id": organization_id,
            },
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "verify_vendor_invoice")


@tool
async def decide_invoice_line(
    verification_id: str,
    line_id: str,
    decision: str,
    pm_notes: str | None = None,
) -> dict:
    """B3 — PM decision on flagged invoice line: approve | challenge | reject."""
    try:
        resp = await _request(
            "POST",
            _base(),
            f"/api/contract-performance/invoices/{verification_id}/lines/decide",
            service=_SERVICE,
            timeout=_TIMEOUT,
            json={"line_id": line_id, "decision": decision, "pm_notes": pm_notes},
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "decide_invoice_line")


@tool
async def list_contract_approvals(
    status: str = "pending",
    organization_id: str | None = None,
) -> dict:
    """List Feature B Approvals queue items."""
    try:
        params: dict[str, Any] = {"status": status}
        if organization_id:
            params["organization_id"] = organization_id
        resp = await _request(
            "GET",
            _base(),
            "/api/contract-performance/approvals",
            service=_SERVICE,
            timeout=_TIMEOUT,
            params=params,
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "list_contract_approvals")


@tool
async def decide_contract_approval(
    item_id: str,
    decision: str,
    pm_notes: str | None = None,
) -> dict:
    """PM decision on a Contract Performance queue item: approve | edit | dismiss."""
    try:
        resp = await _request(
            "POST",
            _base(),
            f"/api/contract-performance/approvals/{item_id}/decide",
            service=_SERVICE,
            timeout=_TIMEOUT,
            json={"decision": decision, "pm_notes": pm_notes},
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "decide_contract_approval")


@tool
async def extract_contract_from_document(
    source_text: str,
    vendor_id: str | None = None,
    contract_ref: str | None = None,
    organization_id: str | None = None,
    auto_ingest: bool = True,
) -> dict:
    """B1 Relationships Agent — extract SLA params from contract/PO text, draft-ingest for PM edit."""
    try:
        resp = await _request(
            "POST",
            _base(),
            "/api/contract-performance/contracts/extract",
            service=_SERVICE,
            timeout=_TIMEOUT,
            json={
                "source_text": source_text,
                "vendor_id": vendor_id,
                "contract_ref": contract_ref,
                "organization_id": organization_id,
                "auto_ingest": auto_ingest,
            },
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "extract_contract_from_document")


@tool
async def extract_and_verify_invoice(
    source_text: str | None = None,
    lines: list[dict] | None = None,
    work_orders: list[dict] | None = None,
    invoice_ref: str | None = None,
    vendor_id: str | None = None,
    labour_day_rate: float | None = None,
    organization_id: str | None = None,
) -> dict:
    """B3 — Parse invoice (text or lines) and verify against ingested work orders."""
    try:
        resp = await _request(
            "POST",
            _base(),
            "/api/contract-performance/invoices/extract-verify",
            service=_SERVICE,
            timeout=_TIMEOUT,
            json={
                "source_text": source_text,
                "lines": lines or [],
                "work_orders": work_orders or [],
                "invoice_ref": invoice_ref,
                "vendor_id": vendor_id,
                "labour_day_rate": labour_day_rate,
                "organization_id": organization_id,
            },
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "extract_and_verify_invoice")


@tool
async def propose_asset_criticality_from_udr(
    organization_id: str | None = None,
    limit: int = 100,
) -> dict:
    """B1 — Read assets from UDR and propose L1/L2/L3 criticality for PM HITL approval."""
    try:
        resp = await _request(
            "POST",
            _base(),
            "/api/contract-performance/asset-criticality/propose-from-udr",
            service=_SERVICE,
            timeout=_TIMEOUT,
            json={"organization_id": organization_id, "limit": limit},
        )
        return resp.json()
    except Exception as exc:
        return _err(exc, "propose_asset_criticality_from_udr")


CONTRACT_PERFORMANCE_TOOLS = [
    extract_contract_from_document,
    ingest_contract_parameters,
    update_contract_parameters,
    confirm_contract_parameters,
    propose_asset_criticality,
    propose_asset_criticality_from_udr,
    approve_asset_criticality,
    score_vendor_work_orders,
    score_work_orders_from_udr,
    generate_vendor_scorecard,
    list_contract_parameters,
    list_vendor_scorecards,
    get_score_weights,
    update_score_weights,
    extract_and_verify_invoice,
    verify_vendor_invoice,
    decide_invoice_line,
    list_contract_approvals,
    decide_contract_approval,
]
