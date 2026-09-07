"""ORM models for Feature B — Contract Performance Engine."""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, Date, DateTime, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import SCHEMA, Base


class ContractSlaParameters(Base):
    __tablename__ = "contract_sla_parameters"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    vendor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    contract_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    document_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    contract_ref: Mapped[str | None] = mapped_column(String(160))
    signed_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="draft")
    sla_response_p1_hours: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    sla_response_p2_hours: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    sla_response_p3_hours: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    sla_response_p4_hours: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    sla_completion_p1_hours: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    sla_completion_p2_hours: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    sla_completion_p3_hours: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    sla_completion_p4_hours: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    labour_day_rate: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    # An hourly rate stated by the contract. Kept separate from the day rate rather than
    # normalised into one: a contract priced per hour has no day rate to infer, and
    # dividing an assumed one by eight invents a threshold no contract agreed to.
    labour_hour_rate: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    overtime_rate: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    call_out_rate: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    parts_pricing_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    payment_terms: Mapped[str | None] = mapped_column(Text)
    kpi_clauses_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    ppm_obligations_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    task_criticality_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    defaults_used: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    overrides_log: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    field_sources: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    raw_extraction: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    confirmed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AssetCriticality(Base):
    __tablename__ = "asset_criticality"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    asset_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    asset_code: Mapped[str | None] = mapped_column(String(120))
    criticality: Mapped[str] = mapped_column(String(8), nullable=False, default="L2")
    proposed_criticality: Mapped[str | None] = mapped_column(String(8))
    source: Mapped[str] = mapped_column(String(40), nullable=False, default="system")
    rationale: Mapped[str | None] = mapped_column(Text)
    approved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class VendorScoreWeightConfig(Base):
    __tablename__ = "vendor_score_weight_config"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    sla_response_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False, default=25)
    sla_completion_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False, default=25)
    first_fix_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False, default=20)
    recall_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False, default=15)
    accreditation_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False, default=15)
    blocked_score_cap: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False, default=60)
    cost_variance_alert_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False, default=15)
    cost_variance_job_count: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    invoice_flag_adversary_gbp: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=500)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class VendorWoScore(Base):
    __tablename__ = "vendor_wo_scores"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    vendor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    work_order_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    wo_code: Mapped[str | None] = mapped_column(String(120))
    asset_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    score_month: Mapped[date | None] = mapped_column(Date)
    sla_response_met: Mapped[bool | None] = mapped_column(Boolean)
    sla_completion_met: Mapped[bool | None] = mapped_column(Boolean)
    first_fix: Mapped[bool | None] = mapped_column(Boolean)
    recall: Mapped[bool | None] = mapped_column(Boolean)
    accreditation_current: Mapped[bool | None] = mapped_column(Boolean)
    component_scores: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    overall_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    capped_by_block: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    cost_actual: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    cost_estimated: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    cost_variance_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class VendorMonthlyScorecard(Base):
    __tablename__ = "vendor_monthly_scorecards"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    vendor_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    score_month: Mapped[date] = mapped_column(Date, nullable=False)
    overall_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    trend_delta: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    component_breakdown: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    ppm_compliance_pct: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    matched_flagged_ratio: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    wo_score_ids: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    block_capped: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FmReportStaleness(Base):
    __tablename__ = "fm_report_staleness"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    vendor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    last_report_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    data_as_of: Mapped[date | None] = mapped_column(Date)
    note: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class InvoiceVerification(Base):
    __tablename__ = "invoice_verifications"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    vendor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    invoice_ref: Mapped[str | None] = mapped_column(String(160))
    document_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="pending")
    matched_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    flagged_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    matched_flagged_ratio: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    lines_json: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    insights_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PpmVisit(Base):
    """B2 — a scheduled PPM obligation and its reported completion.

    `variance_days` and `within_tolerance` are generated columns in Postgres; they
    are read-only here and must not be assigned.
    """

    __tablename__ = "ppm_visits"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    vendor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    contract_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    asset_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    asset_code: Mapped[str | None] = mapped_column(String(120))
    work_order_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    wo_code: Mapped[str | None] = mapped_column(String(120))
    ppm_ref: Mapped[str | None] = mapped_column(String(120))
    frequency: Mapped[str | None] = mapped_column(String(40))
    score_month: Mapped[date | None] = mapped_column(Date)
    scheduled_date: Mapped[date] = mapped_column(Date, nullable=False)
    completed_date: Mapped[date | None] = mapped_column(Date)
    tolerance_days: Mapped[int] = mapped_column(Integer, nullable=False, default=7)
    # DB-generated (see phase2_contract_performance_v2.sql §2) — never assign these.
    variance_days: Mapped[int | None] = mapped_column(Integer)
    within_tolerance: Mapped[bool | None] = mapped_column(Boolean)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="scheduled")
    source: Mapped[str] = mapped_column(String(40), nullable=False, default="fm_report")
    source_document_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    raw_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class InvoiceLine(Base):
    """B3 — one invoice line matched against an ingested work order record."""

    __tablename__ = "invoice_lines"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    invoice_verification_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    vendor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    invoice_ref: Mapped[str | None] = mapped_column(String(160))
    line_no: Mapped[int | None] = mapped_column(Integer)
    line_ref: Mapped[str | None] = mapped_column(String(80))
    work_order_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    wo_code: Mapped[str | None] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text)
    labour_hours: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    labour_rate: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    labour_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    part_code: Mapped[str | None] = mapped_column(String(80))
    parts_qty: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    parts_cost: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    line_total: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    reported_labour_hours: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    contract_labour_rate: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    contract_parts_cost: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    match_status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    discrepancy_code: Mapped[str | None] = mapped_column(String(60))
    discrepancy_text: Mapped[str | None] = mapped_column(Text)
    delta_gbp: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    adversary_reviewed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    adversary_agreed: Mapped[bool | None] = mapped_column(Boolean)
    adversary_delta_gbp: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    pm_decision: Mapped[str | None] = mapped_column(String(20))
    pm_decided_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    pm_decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pm_note: Mapped[str | None] = mapped_column(Text)
    raw_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CostVarianceAlert(Base):
    """B2 — systematic overrun alert (>threshold_pct on job_count_threshold+ jobs/month)."""

    __tablename__ = "cost_variance_alerts"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    vendor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    score_month: Mapped[date] = mapped_column(Date, nullable=False)
    threshold_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False, default=15)
    job_count_threshold: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    breach_job_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    scored_job_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_estimated: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    total_actual: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    total_delta: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    avg_variance_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    max_variance_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    work_order_ids: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="open")
    approvals_queue_item_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    acknowledged_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    detail_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ContractDocument(Base):
    """B1 — one-to-many mapping between source documents and a Contract entity."""

    __tablename__ = "contract_documents"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    vendor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    contract_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    document_kind: Mapped[str] = mapped_column(String(40), nullable=False, default="contract")
    document_name: Mapped[str | None] = mapped_column(String(400))
    blob_url: Mapped[str | None] = mapped_column(Text)
    relationship: Mapped[str] = mapped_column(String(40), nullable=False, default="primary")
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    extraction_status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    extracted_fields: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    vector_chunk_ids: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    linked_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    linked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
