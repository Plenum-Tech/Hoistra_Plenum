"""Pydantic schemas — Feature B Contract Performance."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class ContractIngestRequest(BaseModel):
    extracted: dict[str, Any] = Field(default_factory=dict)
    organization_id: UUID | None = None
    vendor_id: UUID | None = None
    vendor_name: str | None = None
    contract_id: UUID | None = None
    document_id: UUID | None = None
    contract_ref: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _accept_vendor_name_in_vendor_id(cls, data: Any) -> Any:
        """A caller that knows the vendor by name should not have to know its id.

        Extraction reads "Gough and Kelly Ltd." off the contract, and that was being sent
        as vendor_id — which failed validation before any code could look the vendor up,
        so an ingest of a vendor already on the register was rejected outright. Treat a
        non-UUID value as the name it plainly is and let the service resolve it.
        """
        if not isinstance(data, dict):
            return data
        raw = data.get("vendor_id")
        if not isinstance(raw, str) or not raw.strip():
            return data
        try:
            UUID(raw.strip())
        except (ValueError, AttributeError, TypeError):
            data = dict(data)
            data["vendor_id"] = None
            if not data.get("vendor_name"):
                data["vendor_name"] = raw.strip()
        return data


class ContractUpdateRequest(BaseModel):
    updates: dict[str, Any]
    actor: str = "pm"


class ContractConfirmRequest(BaseModel):
    confirmed_by: UUID | None = None


class AssetCriticalityRequest(BaseModel):
    asset_id: UUID
    asset_code: str | None = None
    organization_id: UUID | None = None
    proposed: str | None = None
    load_dependence: str | None = None
    function_type: str | None = None
    sub_meter_high: bool = False
    source: str = "system"


class AssetCriticalityApproveRequest(BaseModel):
    criticality: str | None = None
    approved_by: UUID | None = None


class ScoreWorkOrdersRequest(BaseModel):
    work_orders: list[dict[str, Any]]
    vendor_id: UUID | None = None
    organization_id: UUID | None = None
    score_month: date | None = None


class ScorecardRequest(BaseModel):
    vendor_id: UUID
    score_month: date | None = None
    organization_id: UUID | None = None
    ppm_visits: list[dict[str, Any]] = Field(default_factory=list)


class WeightsUpdateRequest(BaseModel):
    organization_id: UUID | None = None
    sla_response_pct: float | None = None
    sla_completion_pct: float | None = None
    first_fix_pct: float | None = None
    recall_pct: float | None = None
    accreditation_pct: float | None = None
    blocked_score_cap: float | None = None
    cost_variance_alert_pct: float | None = None
    cost_variance_job_count: int | None = None
    invoice_flag_adversary_gbp: float | None = None


class InvoiceVerifyRequest(BaseModel):
    invoice_ref: str | None = None
    lines: list[dict[str, Any]]
    work_orders: list[dict[str, Any]]
    vendor_id: UUID | None = None
    organization_id: UUID | None = None
    document_id: UUID | None = None
    labour_day_rate: float | None = None
    # An explicit hourly rate; wins over day_rate/8 when the contract prices labour by
    # the hour. See engines.contract_performance.parameters.contracted_hourly_rate.
    labour_hour_rate: float | None = None
    parts_pricing_json: dict[str, Any] = Field(default_factory=dict)


class InvoiceLineDecisionRequest(BaseModel):
    line_id: str
    decision: str  # approve | challenge | reject
    pm_notes: str | None = None


class FmStalenessRequest(BaseModel):
    vendor_id: UUID | None = None
    last_report_at: datetime | None = None
    data_as_of: date | None = None
    note: str | None = None
    organization_id: UUID | None = None


class QueueDecisionRequest(BaseModel):
    decision: str
    pm_notes: str | None = None
    prepare_email_handoff: bool = True


class ContractExtractRequest(BaseModel):
    source_text: str | None = None
    # The extractor reads the PDF itself when given one, which is the only way it sees
    # tables and scanned pages — a contract's KPI schedule and rate card live in tables,
    # and flattening those to text interleaves the columns. The field existed on the
    # extractor but not on this request, so the ingestion path could never supply it and
    # the visual read was unreachable in production.
    pdf_base64: str | None = None
    extracted_fields: dict[str, Any] = Field(default_factory=dict)
    organization_id: UUID | None = None
    vendor_id: UUID | None = None
    document_id: UUID | None = None
    contract_ref: str | None = None
    signed_date: date | None = None
    auto_ingest: bool = True


class InvoiceExtractVerifyRequest(BaseModel):
    source_text: str | None = None
    lines: list[dict[str, Any]] = Field(default_factory=list)
    work_orders: list[dict[str, Any]] = Field(default_factory=list)
    invoice_ref: str | None = None
    vendor_id: UUID | None = None
    organization_id: UUID | None = None
    document_id: UUID | None = None
    labour_day_rate: float | None = None
    # An explicit hourly rate; wins over day_rate/8 when the contract prices labour by
    # the hour. See engines.contract_performance.parameters.contracted_hourly_rate.
    labour_hour_rate: float | None = None
    parts_pricing_json: dict[str, Any] = Field(default_factory=dict)


class UdrCriticalityRequest(BaseModel):
    organization_id: UUID | None = None
    limit: int = 100


class ScoreFromUdrRequest(BaseModel):
    """Score completed work orders already written by Phase 1 UDR migration."""

    vendor_id: UUID | None = None
    organization_id: UUID | None = None
    score_month: date | None = None
    limit: int = 500
    generate_scorecard: bool = True
    all_buckets: bool = False  # score every vendor×month present in UDR
    # US1 scenario 4: scoring is refused when no confirmed contract exists. Set this to
    # accept being scored against platform defaults instead.
    allow_default_parameters: bool = False


class WorkOrderConflictResolveRequest(BaseModel):
    accept: str  # "stored" | "incoming"
    resolved_by: UUID | None = None
    note: str | None = None


class InsightsRequest(BaseModel):
    vendor_id: UUID | None = None
    organization_id: UUID | None = None
    from_date: date | None = None
    to_date: date | None = None


class InsightsResponse(BaseModel):
    """FR-028: cost and labour variance insights with full WO traceability."""

    ok: bool = True
    message: str | None = None
    cost_variance_pct: float | None = None
    labour_variance_pct: float | None = None
    matched_flagged_trend: list[float | None] = Field(default_factory=list)
    contributing_wo_ids: list[str] = Field(default_factory=list)
    period: dict[str, str | None] = Field(default_factory=dict)
