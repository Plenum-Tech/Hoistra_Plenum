"""ORM models for Compliance Engine (A1–A5) tables in plenum_cafm."""
from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import Boolean, Date, DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import SCHEMA, Base


class CountryCertificatePack(Base):
    __tablename__ = "country_certificate_packs"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    pack_id: Mapped[str] = mapped_column(String(80), nullable=False)
    country_code: Mapped[str] = mapped_column(String(8), nullable=False)
    pack_version: Mapped[str] = mapped_column(String(40), nullable=False, default="1.0")
    certificate_type_code: Mapped[str] = mapped_column(String(80), nullable=False)
    certificate_type_name: Mapped[str] = mapped_column(String(255), nullable=False)
    certificate_scope: Mapped[str] = mapped_column(String(20), nullable=False)
    trade_category: Mapped[str | None] = mapped_column(String(120))
    regulation_reference: Mapped[str | None] = mapped_column(String(255))
    regulation_url: Mapped[str | None] = mapped_column(Text)
    frequency_months: Mapped[int | None] = mapped_column(Integer)
    issuing_body: Mapped[str | None] = mapped_column(String(255))
    required_contractor_accreditation: Mapped[str | None] = mapped_column(String(255))
    verification_url: Mapped[str | None] = mapped_column(Text)
    key_fields_schema: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    alert_thresholds: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ComplianceVerificationSource(Base):
    """CCC §8.8 — per certificate_type_code verification channel config."""

    __tablename__ = "compliance_verification_sources"
    __table_args__ = {"schema": SCHEMA}

    certificate_type_code: Mapped[str] = mapped_column(String(80), primary_key=True)
    # Registers are national: the same code means a different authority per country
    # (e.g. UAE PUBLIC_LIABILITY -> CBUAE, not the UK FCA). Part of the composite PK.
    country_code: Mapped[str] = mapped_column(
        String(8), primary_key=True, nullable=False, server_default="UK"
    )
    cert_scope: Mapped[str] = mapped_column(String(20), nullable=False)
    verification_group: Mapped[str] = mapped_column(String(40), nullable=False)
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    register: Mapped[str | None] = mapped_column(String(120))
    source_url: Mapped[str | None] = mapped_column(Text)
    api_available: Mapped[str] = mapped_column(String(12), nullable=False)
    refresh_cadence: Mapped[str | None] = mapped_column(String(20))
    notes: Mapped[str | None] = mapped_column(Text)
    code_aliases: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ComplianceCertificate(Base):
    __tablename__ = "compliance_certificates"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    organization_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    certificate_ref: Mapped[str | None] = mapped_column(String(160))
    cert_type: Mapped[str | None] = mapped_column(String(80))
    certificate_type_code: Mapped[str | None] = mapped_column(String(80))
    certificate_number: Mapped[str | None] = mapped_column(String(160))
    asset_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    site_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    # The site key when plenum_cafm.sites does NOT key on a UUID. That table keys on
    # site_id VARCHAR(50) in this deployment, so a UUID-only column could never hold a link
    # to it — which is why every building certificate had site_id NULL and per-site coverage
    # collapsed into one portfolio bucket. site_id still holds a UUID-shaped key where the
    # table has one; exactly one of the two is set. See engines/compliance/site_links.py.
    site_ref: Mapped[str | None] = mapped_column(String(120))
    # Building/site the (Building-scope) certificate belongs to, captured from the document.
    building_name: Mapped[str | None] = mapped_column(String(255))
    building_reference: Mapped[str | None] = mapped_column(String(120))
    # The graph link (migrations/udr_building_graph.sql). building_name is what the DOCUMENT
    # said; building_id is the building that was resolved from it, and raw_metadata
    # ["building_link"] records on what basis — a code match and a single-building-site
    # inference are different claims and must not read alike.
    building_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    vendor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    duty_holder_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    issuer: Mapped[str | None] = mapped_column(String(255))
    issue_date: Mapped[date | None] = mapped_column(Date)
    expiry_date: Mapped[date | None] = mapped_column(Date)
    next_due_date: Mapped[date | None] = mapped_column(Date)
    inspection_frequency_months: Mapped[int | None] = mapped_column(Integer)
    inspector_name: Mapped[str | None] = mapped_column(String(255))
    inspector_accreditation_number: Mapped[str | None] = mapped_column(String(160))
    result: Mapped[str | None] = mapped_column(String(40))
    defects_found: Mapped[str | None] = mapped_column(Text)
    remedial_actions: Mapped[str | None] = mapped_column(Text)
    remedial_status: Mapped[str | None] = mapped_column(String(40), default="Closed")
    document_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    source_document_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    cert_scope: Mapped[str | None] = mapped_column(String(20))
    country_code: Mapped[str | None] = mapped_column(String(8), default="UK")
    # Sub-national grouping for reporting, from broad to narrow. `state` is the country's
    # first-level division, spelled out in full — England / Scotland / Wales / Northern
    # Ireland, Washington, Dubai — never an abbreviation, so a grouped report reads as
    # written. `region` is the city or county below it (London, Norfolk), which is the
    # level FM reporting actually operates at.
    state: Mapped[str | None] = mapped_column(String(64))
    region: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str | None] = mapped_column(String(40))
    authenticity_warning: Mapped[str | None] = mapped_column(Text)
    days_to_expiry: Mapped[int | None] = mapped_column(Integer)
    insurance_risk_flag: Mapped[bool | None] = mapped_column(Boolean, default=False)
    raw_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ApprovalsQueueItem(Base):
    __tablename__ = "approvals_queue_items"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    source_feature: Mapped[str] = mapped_column(String(1), nullable=False)
    item_type: Mapped[str] = mapped_column(String(80), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="pending")
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    related_entity_type: Mapped[str | None] = mapped_column(String(80))
    related_entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    email_draft: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    pm_notes: Mapped[str | None] = mapped_column(Text)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OpsAuditLog(Base):
    """Append-only audit trail. DB trigger forbids UPDATE/DELETE (see migrations)."""

    __tablename__ = "ops_audit_log"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    actor: Mapped[str] = mapped_column(String(160), nullable=False)
    action_type: Mapped[str] = mapped_column(String(120), nullable=False)
    source_feature: Mapped[str | None] = mapped_column(String(1))
    input_hash: Mapped[str | None] = mapped_column(String(64))
    output_hash: Mapped[str | None] = mapped_column(String(64))
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ComplianceScanRun(Base):
    __tablename__ = "compliance_scan_runs"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    scope: Mapped[str] = mapped_column(String(80), nullable=False, default="all")
    scope_filter: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    building_scanned: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    vendor_scanned: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    alerts_created: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    blocks_set: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    adversary_passed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    adversary_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="completed")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class OpsEmailLog(Base):
    __tablename__ = "ops_email_log"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    queue_item_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    to_address: Mapped[str] = mapped_column(String(255), nullable=False)
    subject: Mapped[str] = mapped_column(String(500), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="queued")
    error_message: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BuildingCountryPack(Base):
    __tablename__ = "building_country_packs"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    site_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    country_code: Mapped[str] = mapped_column(String(8), nullable=False, default="UK")
    pack_version: Mapped[str] = mapped_column(String(40), nullable=False, default="1.1")
    activated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ResourceSkill(Base):
    __tablename__ = "resource_skills"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    vendor_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    resource_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    operative_name: Mapped[str | None] = mapped_column(String(255))
    skill_type_code: Mapped[str] = mapped_column(String(80), nullable=False)
    certificate_number: Mapped[str | None] = mapped_column(String(160))
    issue_date: Mapped[date | None] = mapped_column(Date)
    expiry_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str | None] = mapped_column(String(40))
    days_to_expiry: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ApprovalActionToken(Base):
    __tablename__ = "approval_action_tokens"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    queue_item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    token: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    action: Mapped[str] = mapped_column(String(40), nullable=False, default="approve")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ComplianceRiskSnapshot(Base):
    __tablename__ = "compliance_risk_snapshots"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False)
    vendors_blocked: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    high_risk_lt_30: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    medium_risk_lt_90: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
