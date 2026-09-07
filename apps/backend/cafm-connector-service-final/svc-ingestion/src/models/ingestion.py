"""
svc-ingestion/src/models/ingestion.py

SQLAlchemy ORM models for all new Sprint 2 tables.
All tables live in the plenum_cafm PostgreSQL schema.

Tables:
  ingestion_documents    — every source file, extraction JSON, status, cost
  ingestion_audit_log    — full traceability per ingestion event
  prompt_templates       — Jinja2 templates per agent + doc type
  prompt_ab_tests        — A/B test tracking
  review_queue           — HITL items awaiting human decision
  corrections_log        — every human correction (feeds prompt refinement)
  claude_api_usage       — per-request cost tracking
  claude_budget_config   — budget guardrails
  query_audit_log        — every user query (svc-query)
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

SCHEMA = "plenum_cafm"


class IngestionBase(DeclarativeBase):
    pass


# ── ingestion_documents ────────────────────────────────────────────────────────


class IngestionDocument(IngestionBase):
    """
    Every source file ingested through svc-ingestion.
    One row per file, regardless of agent type.
    """

    __tablename__ = "ingestion_documents"
    __table_args__ = (
        Index("ix_ingestion_documents_tenant_status", "tenant_id", "status"),
        Index("ix_ingestion_documents_file_hash", "file_hash_sha256"),
        Index("ix_ingestion_documents_uploaded_at", "uploaded_at"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    # ── Source metadata ────────────────────────────────────────────────
    # source_type: pdf | excel | word | csv | xml | json | database | api
    source_type: Mapped[str] = mapped_column(String(20), nullable=False)
    agent_id: Mapped[str] = mapped_column(String(50), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    blob_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_hash_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # ── Extraction output ──────────────────────────────────────────────
    intermediate_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    final_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # ── Pipeline status ────────────────────────────────────────────────
    # status: queued | extracting | review | accepted | rejected
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    # confidence_overall: high | medium | low
    confidence_overall: Mapped[str | None] = mapped_column(String(10), nullable=True)
    eval_score: Mapped[float | None] = mapped_column(Numeric(4, 3), nullable=True)

    # ── Model + prompt tracking ────────────────────────────────────────
    model_used: Mapped[str | None] = mapped_column(String(50), nullable=True)
    prompt_template_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.prompt_templates.id", ondelete="SET NULL"),
        nullable=True,
    )

    # ── Token + cost accounting ────────────────────────────────────────
    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[float] = mapped_column(Numeric(10, 6), nullable=False, default=0)
    processing_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # ── Provenance ─────────────────────────────────────────────────────
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.users.id", ondelete="SET NULL"),
        nullable=True,
    )
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ── Relationships ──────────────────────────────────────────────────
    audit_logs: Mapped[list[IngestionAuditLog]] = relationship(
        "IngestionAuditLog", back_populates="document", cascade="all, delete-orphan"
    )
    review_items: Mapped[list[ReviewQueueItem]] = relationship(
        "ReviewQueueItem", back_populates="document", cascade="all, delete-orphan"
    )
    corrections: Mapped[list[CorrectionsLog]] = relationship(
        "CorrectionsLog", back_populates="document", cascade="all, delete-orphan"
    )
    api_usage: Mapped[list[ClaudeApiUsage]] = relationship(
        "ClaudeApiUsage", back_populates="document"
    )
    prompt_template: Mapped[PromptTemplate | None] = relationship(
        "PromptTemplate", back_populates="documents"
    )


# ── ingestion_audit_log ────────────────────────────────────────────────────────


class IngestionAuditLog(IngestionBase):
    """
    Full traceability log — one row per pipeline event per ingestion.
    Required for UAE compliance audits. Never deleted.
    """

    __tablename__ = "ingestion_audit_log"
    __table_args__ = (
        Index("ix_ingestion_audit_log_ingestion_id", "ingestion_id"),
        Index("ix_ingestion_audit_log_timestamp", "timestamp"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    ingestion_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.ingestion_documents.id", ondelete="CASCADE"),
        nullable=False,
    )

    # event_type: stage1_ingest | stage2_extract | stage3_eval | stage4_unify |
    #             review_decision | re_extract | rejected
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    model_used: Mapped[str | None] = mapped_column(String(50), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    eval_score: Mapped[float | None] = mapped_column(Numeric(4, 3), nullable=True)
    rules_violations: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Human review fields (populated when event_type = review_decision)
    reviewer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.users.id", ondelete="SET NULL"),
        nullable=True,
    )
    # decision: accept | correct | reject
    decision: Mapped[str | None] = mapped_column(String(20), nullable=True)
    corrected_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # ── Relationships ──────────────────────────────────────────────────
    document: Mapped[IngestionDocument] = relationship(
        "IngestionDocument", back_populates="audit_logs"
    )


# ── prompt_templates ───────────────────────────────────────────────────────────


class PromptTemplate(IngestionBase):
    """
    Versioned Jinja2 prompt templates, one per agent + document type combination.
    Accuracy score and usage stats tracked for A/B testing.
    """

    __tablename__ = "prompt_templates"
    __table_args__ = (
        UniqueConstraint("agent_id", "doc_type", "version", name="uq_prompt_template_version"),
        Index("ix_prompt_templates_agent_doc", "agent_id", "doc_type"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    agent_id: Mapped[str] = mapped_column(String(50), nullable=False)
    doc_type: Mapped[str] = mapped_column(String(50), nullable=False)
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    user_template: Mapped[str] = mapped_column(Text, nullable=False)
    extraction_schema: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    version: Mapped[str] = mapped_column(String(20), nullable=False, default="1.0")
    accuracy_score: Mapped[float | None] = mapped_column(Numeric(5, 4), nullable=True)
    usage_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    avg_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # ── Relationships ──────────────────────────────────────────────────
    documents: Mapped[list[IngestionDocument]] = relationship(
        "IngestionDocument", back_populates="prompt_template"
    )
    ab_tests_a: Mapped[list[PromptAbTest]] = relationship(
        "PromptAbTest",
        foreign_keys="PromptAbTest.template_a_id",
        back_populates="template_a",
    )
    ab_tests_b: Mapped[list[PromptAbTest]] = relationship(
        "PromptAbTest",
        foreign_keys="PromptAbTest.template_b_id",
        back_populates="template_b",
    )


# ── prompt_ab_tests ────────────────────────────────────────────────────────────


class PromptAbTest(IngestionBase):
    """
    Tracks A/B test between two prompt template versions.
    Winner auto-promotes when statistical significance is reached.
    """

    __tablename__ = "prompt_ab_tests"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    template_a_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.prompt_templates.id", ondelete="CASCADE"),
        nullable=False,
    )
    template_b_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.prompt_templates.id", ondelete="CASCADE"),
        nullable=False,
    )
    # status: running | completed | cancelled
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running")
    accuracy_a: Mapped[float | None] = mapped_column(Numeric(5, 4), nullable=True)
    accuracy_b: Mapped[float | None] = mapped_column(Numeric(5, 4), nullable=True)
    winner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.prompt_templates.id", ondelete="SET NULL"),
        nullable=True,
    )
    docs_processed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ── Relationships ──────────────────────────────────────────────────
    template_a: Mapped[PromptTemplate] = relationship(
        "PromptTemplate", foreign_keys=[template_a_id], back_populates="ab_tests_a"
    )
    template_b: Mapped[PromptTemplate] = relationship(
        "PromptTemplate", foreign_keys=[template_b_id], back_populates="ab_tests_b"
    )


# ── review_queue ───────────────────────────────────────────────────────────────


class ReviewQueueItem(IngestionBase):
    """
    HITL review queue. Items routed here when confidence is medium/low
    or eval_score < 0.90 or a rule violation is detected.
    """

    __tablename__ = "review_queue"
    __table_args__ = (
        Index("ix_review_queue_ingestion_id", "ingestion_id"),
        Index("ix_review_queue_status_created", "status", "created_at"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    ingestion_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.ingestion_documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    field_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    extracted_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    # confidence: high | medium | low
    confidence: Mapped[str | None] = mapped_column(String(10), nullable=True)
    routing_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Reviewer assignment
    reviewer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.users.id", ondelete="SET NULL"),
        nullable=True,
    )
    # status: pending | locked | decided
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    # decision: accept | correct | reject
    decision: Mapped[str | None] = mapped_column(String(20), nullable=True)
    corrected_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    locked_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ── Relationships ──────────────────────────────────────────────────
    document: Mapped[IngestionDocument] = relationship(
        "IngestionDocument", back_populates="review_items"
    )


# ── corrections_log ────────────────────────────────────────────────────────────


class CorrectionsLog(IngestionBase):
    """
    Every human correction made through the review queue.
    Aggregated weekly to drive prompt refinement suggestions.
    """

    __tablename__ = "corrections_log"
    __table_args__ = (
        Index("ix_corrections_log_ingestion_id", "ingestion_id"),
        Index("ix_corrections_log_timestamp", "timestamp"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    ingestion_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.ingestion_documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    field_path: Mapped[str] = mapped_column(String(255), nullable=False)
    original_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    corrected_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    # correction_type: wrong_value | missing_field | wrong_entity | hallucination | other
    correction_type: Mapped[str] = mapped_column(String(50), nullable=False, default="wrong_value")
    reviewer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.users.id", ondelete="CASCADE"),
        nullable=False,
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # ── Relationships ──────────────────────────────────────────────────
    document: Mapped[IngestionDocument] = relationship(
        "IngestionDocument", back_populates="corrections"
    )


# ── claude_api_usage ───────────────────────────────────────────────────────────


class ClaudeApiUsage(IngestionBase):
    """
    Per-request Claude API cost tracking.
    Linked to either an ingestion document or a query (not both).
    Powers the cost dashboard and budget guardrails.
    """

    __tablename__ = "claude_api_usage"
    __table_args__ = (
        Index("ix_claude_api_usage_ingestion_id", "ingestion_id"),
        Index("ix_claude_api_usage_timestamp", "timestamp"),
        Index("ix_claude_api_usage_service_model", "service", "model"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    ingestion_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.ingestion_documents.id", ondelete="SET NULL"),
        nullable=True,
    )
    query_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.query_audit_log.id", ondelete="SET NULL"),
        nullable=True,
    )
    # service: cafm-ingestion-service | cafm-query-service | cafm-connector-service
    service: Mapped[str] = mapped_column(String(60), nullable=False)
    agent_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    model: Mapped[str] = mapped_column(String(50), nullable=False)
    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[float] = mapped_column(Numeric(10, 6), nullable=False, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # ── Relationships ──────────────────────────────────────────────────
    document: Mapped[IngestionDocument | None] = relationship(
        "IngestionDocument", back_populates="api_usage"
    )
    query: Mapped[QueryAuditLog | None] = relationship(
        "QueryAuditLog", back_populates="api_usage"
    )


# ── claude_budget_config ───────────────────────────────────────────────────────


class ClaudeBudgetConfig(IngestionBase):
    """
    Budget guardrails for Claude API spend.
    Alert at threshold_pct; auto-pause ingestion at 100%.
    """

    __tablename__ = "claude_budget_config"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # period: daily | weekly | monthly
    period: Mapped[str] = mapped_column(String(20), nullable=False, default="monthly")
    limit_usd: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    alert_threshold_pct: Mapped[float] = mapped_column(
        Numeric(5, 2), nullable=False, default=80.0
    )
    auto_pause: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


# ── query_audit_log ────────────────────────────────────────────────────────────


class QueryAuditLog(IngestionBase):
    """
    Every user query through svc-query.
    Required for UAE compliance audits.
    """

    __tablename__ = "query_audit_log"
    __table_args__ = (
        Index("ix_query_audit_log_user_id", "user_id"),
        Index("ix_query_audit_log_timestamp", "timestamp"),
        Index("ix_query_audit_log_retrieval_tier", "retrieval_tier"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    query_text: Mapped[str] = mapped_column(Text, nullable=False)
    # intent_classified: tier1_structured | tier2_document | tier3_manual
    intent_classified: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # retrieval_tier: tier1 | tier2 | tier3
    retrieval_tier: Mapped[str | None] = mapped_column(String(10), nullable=True)
    docs_consulted: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    model_used: Mapped[str | None] = mapped_column(String(50), nullable=True)
    response_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    eval_score: Mapped[float | None] = mapped_column(Numeric(4, 3), nullable=True)
    # output_format: text | json | word | pdf
    output_format: Mapped[str | None] = mapped_column(String(10), nullable=True)
    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[float] = mapped_column(Numeric(10, 6), nullable=False, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.users.id", ondelete="SET NULL"),
        nullable=True,
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # ── Relationships ──────────────────────────────────────────────────
    api_usage: Mapped[list[ClaudeApiUsage]] = relationship(
        "ClaudeApiUsage", back_populates="query"
    )


# ── agent_audit_log ────────────────────────────────────────────────────────────


class AgentAuditLog(IngestionBase):
    """
    Layer 5 per-agent determinism audit — one row per agent invocation.

    Captures all 3 run outputs, EL-5.x eval results, hard rules fired,
    and the final confidence gate result. Used by Layer 6 EL-6.BOUND
    to verify audit_ids are resolvable.
    """

    __tablename__ = "agent_audit_log"
    __table_args__ = (
        Index("ix_agent_audit_log_agent_id", "agent_id"),
        Index("ix_agent_audit_log_asset_code", "asset_code"),
        Index("ix_agent_audit_log_timestamp", "timestamp"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # agent_id: asset|wo|pm|parts|inspection
    agent_id: Mapped[str] = mapped_column(String(50), nullable=False)
    domain: Mapped[str] = mapped_column(String(50), nullable=False)
    asset_code: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # ── EL-5.BOUND ─────────────────────────────────────────────────────
    bound_validation_passed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # ── EL-5.AGG — per-run outputs + validity ──────────────────────────
    run_1_output: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    run_2_output: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    run_3_output: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    run_1_valid: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    run_2_valid: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    run_3_valid: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # ── EL-5.VOTE ──────────────────────────────────────────────────────
    runs_agreed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    winner_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    winner_confidence: Mapped[float | None] = mapped_column(Numeric(4, 3), nullable=True)

    # ── EL-5.CONSTRAIN ─────────────────────────────────────────────────
    hard_rules_fired: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    final_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    confidence_gate_passed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    requires_human_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # ── Cost tracking ──────────────────────────────────────────────────
    model_used: Mapped[str | None] = mapped_column(String(50), nullable=True)
    tokens_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[float] = mapped_column(Numeric(10, 6), nullable=False, default=0)

    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
