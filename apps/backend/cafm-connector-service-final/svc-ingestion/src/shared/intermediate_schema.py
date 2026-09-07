"""
svc-ingestion/src/shared/intermediate_schema.py

The contract between every agent (Stage 2) and the shared pipeline (Stages 3 + 4).
Every agent MUST produce an IntermediateSchema before eval and unification can run.

Matches the JSON structure defined in CLAUDE.md Section 4 exactly.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator, model_validator


# ── Enumerations ──────────────────────────────────────────────────────────────


class SourceType(str, Enum):
    PDF = "pdf"
    EXCEL = "excel"
    WORD = "word"
    CSV = "csv"
    XML = "xml"
    JSON = "json"
    DATABASE = "database"
    API = "api"


class AgentId(str, Enum):
    PDF = "pdf-agent"
    EXCEL = "excel-agent"
    WORD = "word-agent"
    CSV = "csv-agent"
    XML_JSON = "xml-json-agent"
    DATABASE = "database-agent"
    API = "api-agent"


class ExtractionMethod(str, Enum):
    CLAUDE_VISION = "claude-vision"
    OPENPYXL_CLAUDE = "openpyxl+claude"
    PANDOC_CLAUDE = "pandoc+claude"
    PANDAS_CLAUDE = "pandas+claude"
    LXML_CLAUDE = "lxml+claude"
    NONE = "none"


class ModelUsed(str, Enum):
    SONNET = "claude-sonnet-4-6"
    OPUS = "claude-opus-4-6"
    HAIKU = "claude-haiku-4-5"
    NONE = "none"


class ConfidenceLevel(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


# ── Entity sub-models ─────────────────────────────────────────────────────────


class AssetEntity(BaseModel):
    """Maps to the `assets` table in plenum_cafm."""

    asset_code: str | None = None
    serial_number: str | None = None
    name: str | None = None
    category: str | None = None
    location: str | None = None
    manufacturer: str | None = None
    model_number: str | None = None
    installation_date: str | None = None  # ISO 8601 — normalised by entity resolver
    warranty_expiry: str | None = None
    status: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def at_least_one_identifier(self) -> "AssetEntity":
        if not self.asset_code and not self.serial_number and not self.name:
            raise ValueError("AssetEntity must have at least one of: asset_code, serial_number, name")
        return self


class WorkOrderEntity(BaseModel):
    """Maps to the `work_orders` table in plenum_cafm."""

    work_order_number: str | None = None
    title: str | None = None
    description: str | None = None
    asset_code: str | None = None
    asset_serial: str | None = None
    priority: str | None = None
    status: str | None = None
    technician_name: str | None = None
    technician_id: str | None = None
    scheduled_date: str | None = None
    completed_date: str | None = None
    estimated_hours: float | None = None
    actual_hours: float | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class ReadingEntity(BaseModel):
    """Maps to the `asset_readings` table in plenum_cafm."""

    asset_code: str | None = None
    asset_serial: str | None = None
    reading_type: str | None = None
    value: float | None = None
    unit: str | None = None          # normalised to unit code by entity resolver
    reading_date: str | None = None  # normalised to UTC epoch ms
    technician_name: str | None = None
    notes: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def value_required(self) -> "ReadingEntity":
        if self.value is None and self.reading_type is None:
            raise ValueError("ReadingEntity must have at least value or reading_type")
        return self


class FindingEntity(BaseModel):
    """Inspection findings — stored in intermediate JSON and mapped to work_orders or asset_readings."""

    finding_id: str | None = None
    asset_code: str | None = None
    severity: str | None = None   # Critical / Major / Minor / Observation
    description: str | None = None
    recommendation: str | None = None
    location: str | None = None
    photo_refs: list[str] = Field(default_factory=list)
    extra: dict[str, Any] = Field(default_factory=dict)


class TechnicianEntity(BaseModel):
    """Maps to the `technicians` table in plenum_cafm."""

    employee_id: str | None = None
    name: str | None = None
    email: str | None = None
    phone: str | None = None
    specialisation: str | None = None
    certification: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def at_least_one_identifier(self) -> "TechnicianEntity":
        if not self.employee_id and not self.name and not self.email:
            raise ValueError("TechnicianEntity must have at least one of: employee_id, name, email")
        return self


class VendorEntity(BaseModel):
    """Maps to the `vendors` table in plenum_cafm."""

    vendor_code: str | None = None
    name: str | None = None
    contact_name: str | None = None
    email: str | None = None
    phone: str | None = None
    address: str | None = None
    contract_number: str | None = None
    contract_start: str | None = None
    contract_end: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def at_least_one_identifier(self) -> "VendorEntity":
        if not self.vendor_code and not self.name:
            raise ValueError("VendorEntity must have at least one of: vendor_code, name")
        return self


class CertificateEntity(BaseModel):
    """Compliance certificates — stored in asset_documents and flagged for multi-pass voting."""

    certificate_number: str | None = None
    certificate_type: str | None = None
    asset_code: str | None = None
    issued_by: str | None = None
    issued_date: str | None = None
    expiry_date: str | None = None
    is_valid: bool | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class SparePartEntity(BaseModel):
    """Maps to the `spare_parts` table in plenum_cafm."""

    part_number: str | None = None
    name: str | None = None
    quantity: float | None = None
    unit: str | None = None
    unit_cost: float | None = None
    supplier: str | None = None
    asset_code: str | None = None
    work_order_number: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def at_least_one_identifier(self) -> "SparePartEntity":
        if not self.part_number and not self.name:
            raise ValueError("SparePartEntity must have at least one of: part_number, name")
        return self


# ── Entities container ────────────────────────────────────────────────────────


class EntitiesBlock(BaseModel):
    """All extracted entities grouped by type."""

    assets: list[AssetEntity] = Field(default_factory=list)
    work_orders: list[WorkOrderEntity] = Field(default_factory=list)
    readings: list[ReadingEntity] = Field(default_factory=list)
    findings: list[FindingEntity] = Field(default_factory=list)
    technicians: list[TechnicianEntity] = Field(default_factory=list)
    vendors: list[VendorEntity] = Field(default_factory=list)
    certificates: list[CertificateEntity] = Field(default_factory=list)
    spare_parts: list[SparePartEntity] = Field(default_factory=list)

    @property
    def total_count(self) -> int:
        return (
            len(self.assets)
            + len(self.work_orders)
            + len(self.readings)
            + len(self.findings)
            + len(self.technicians)
            + len(self.vendors)
            + len(self.certificates)
            + len(self.spare_parts)
        )


# ── Confidence + eval ─────────────────────────────────────────────────────────


class ConfidenceResult(BaseModel):
    """Output from Stage 3 (eval_layer + confidence_router)."""

    overall: ConfidenceLevel = ConfidenceLevel.LOW
    per_field: dict[str, ConfidenceLevel] = Field(default_factory=dict)
    eval_score: float = Field(default=0.0, ge=0.0, le=1.0)
    rules_passed: bool = False
    rules_violations: list[str] = Field(default_factory=list)

    @field_validator("eval_score")
    @classmethod
    def round_score(cls, v: float) -> float:
        return round(v, 3)


# ── Audit info ────────────────────────────────────────────────────────────────


class AuditInfo(BaseModel):
    """Per-extraction cost and token accounting."""

    prompt_template_id: UUID | None = None
    prompt_version: str | None = None
    passes: int = Field(default=1, ge=1)
    tokens_in: int = Field(default=0, ge=0)
    tokens_out: int = Field(default=0, ge=0)
    cache_read_tokens: int = Field(default=0, ge=0)
    cost_usd: float = Field(default=0.0, ge=0.0)
    cost_aed: float = Field(default=0.0, ge=0.0)
    processing_ms: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def compute_cost_aed(self) -> "AuditInfo":
        """Auto-compute AED cost from USD (1 USD ≈ 3.67 AED)."""
        if self.cost_usd > 0 and self.cost_aed == 0.0:
            self.cost_aed = round(self.cost_usd * 3.67, 6)
        return self


# ── Top-level intermediate schema ─────────────────────────────────────────────


class IntermediateSchema(BaseModel):
    """
    The contract between every agent (Stage 2) and the shared pipeline (Stages 3 + 4).

    Produced by: pdf_agent, excel_agent, word_agent, csv_agent, xml_json_agent
    Consumed by: eval_layer, confidence_router, unifier, audit
    """

    # ── Identity ──────────────────────────────────────────────────────────────
    ingestion_id: UUID = Field(default_factory=uuid4)
    source_type: SourceType
    agent_id: AgentId
    source_filename: str
    source_blob_url: str | None = None

    # ── Extraction metadata ───────────────────────────────────────────────────
    extracted_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    extraction_method: ExtractionMethod
    model_used: ModelUsed

    # ── Payload ───────────────────────────────────────────────────────────────
    entities: EntitiesBlock = Field(default_factory=EntitiesBlock)

    # ── Eval output (populated by Stage 3) ───────────────────────────────────
    confidence: ConfidenceResult = Field(default_factory=ConfidenceResult)

    # ── Cost / audit (populated during extraction and Stage 3) ───────────────
    audit: AuditInfo = Field(default_factory=AuditInfo)

    @field_validator("source_filename")
    @classmethod
    def filename_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("source_filename must not be empty")
        return v.strip()

    @model_validator(mode="after")
    def agent_matches_source_type(self) -> "IntermediateSchema":
        """Validate agent_id is consistent with source_type."""
        valid_pairs: dict[AgentId, set[SourceType]] = {
            AgentId.PDF: {SourceType.PDF},
            AgentId.EXCEL: {SourceType.EXCEL},
            AgentId.WORD: {SourceType.WORD},
            AgentId.CSV: {SourceType.CSV},
            AgentId.XML_JSON: {SourceType.XML, SourceType.JSON},
            AgentId.DATABASE: {SourceType.DATABASE},
            AgentId.API: {SourceType.API},
        }
        allowed = valid_pairs.get(self.agent_id, set())
        if allowed and self.source_type not in allowed:
            raise ValueError(
                f"agent_id '{self.agent_id}' is not valid for source_type '{self.source_type}'"
            )
        return self

    model_config = {"use_enum_values": False}
