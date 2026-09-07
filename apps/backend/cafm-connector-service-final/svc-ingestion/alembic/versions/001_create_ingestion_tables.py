"""Create Sprint 2 ingestion tables

Revision ID: 001
Revises:
Create Date: 2026-03-25

Creates 9 new tables in the plenum_cafm schema:
  - prompt_templates       (no FKs — created first)
  - prompt_ab_tests        (FK → prompt_templates)
  - query_audit_log        (no FK dependencies)
  - ingestion_documents    (FK → prompt_templates, users)
  - ingestion_audit_log    (FK → ingestion_documents, users)
  - review_queue           (FK → ingestion_documents, users)
  - corrections_log        (FK → ingestion_documents, users)
  - claude_api_usage       (FK → ingestion_documents, query_audit_log)
  - claude_budget_config   (no FKs)
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "plenum_cafm"


def upgrade() -> None:
    # ── 1. prompt_templates ───────────────────────────────────────────────────
    op.create_table(
        "prompt_templates",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("agent_id", sa.String(50), nullable=False),
        sa.Column("doc_type", sa.String(50), nullable=False),
        sa.Column("system_prompt", sa.Text, nullable=False),
        sa.Column("user_template", sa.Text, nullable=False),
        sa.Column("extraction_schema", JSONB, nullable=True),
        sa.Column("version", sa.String(20), nullable=False, server_default="1.0"),
        sa.Column("accuracy_score", sa.Numeric(5, 4), nullable=True),
        sa.Column("usage_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("avg_tokens", sa.Integer, nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "agent_id", "doc_type", "version", name="uq_prompt_template_version"
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_prompt_templates_agent_doc",
        "prompt_templates",
        ["agent_id", "doc_type"],
        schema=SCHEMA,
    )

    # ── 2. prompt_ab_tests ────────────────────────────────────────────────────
    op.create_table(
        "prompt_ab_tests",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "template_a_id",
            UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.prompt_templates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "template_b_id",
            UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.prompt_templates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(20), nullable=False, server_default="running"),
        sa.Column("accuracy_a", sa.Numeric(5, 4), nullable=True),
        sa.Column("accuracy_b", sa.Numeric(5, 4), nullable=True),
        sa.Column(
            "winner_id",
            UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.prompt_templates.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("docs_processed", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )

    # ── 3. query_audit_log ────────────────────────────────────────────────────
    op.create_table(
        "query_audit_log",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("query_text", sa.Text, nullable=False),
        sa.Column("intent_classified", sa.String(30), nullable=True),
        sa.Column("retrieval_tier", sa.String(10), nullable=True),
        sa.Column("docs_consulted", JSONB, nullable=True),
        sa.Column("model_used", sa.String(50), nullable=True),
        sa.Column("response_text", sa.Text, nullable=True),
        sa.Column("eval_score", sa.Numeric(4, 3), nullable=True),
        sa.Column("output_format", sa.String(10), nullable=True),
        sa.Column("tokens_in", sa.Integer, nullable=False, server_default="0"),
        sa.Column("tokens_out", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Numeric(10, 6), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_query_audit_log_user_id", "query_audit_log", ["user_id"], schema=SCHEMA
    )
    op.create_index(
        "ix_query_audit_log_timestamp", "query_audit_log", ["timestamp"], schema=SCHEMA
    )
    op.create_index(
        "ix_query_audit_log_retrieval_tier",
        "query_audit_log",
        ["retrieval_tier"],
        schema=SCHEMA,
    )

    # ── 4. ingestion_documents ────────────────────────────────────────────────
    op.create_table(
        "ingestion_documents",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=True),
        sa.Column("source_type", sa.String(20), nullable=False),
        sa.Column("agent_id", sa.String(50), nullable=False),
        sa.Column("original_filename", sa.String(500), nullable=False),
        sa.Column("blob_url", sa.Text, nullable=True),
        sa.Column("file_hash_sha256", sa.String(64), nullable=True),
        sa.Column("page_count", sa.Integer, nullable=True),
        sa.Column("intermediate_json", JSONB, nullable=True),
        sa.Column("final_json", JSONB, nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("confidence_overall", sa.String(10), nullable=True),
        sa.Column("eval_score", sa.Numeric(4, 3), nullable=True),
        sa.Column("model_used", sa.String(50), nullable=True),
        sa.Column(
            "prompt_template_id",
            UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.prompt_templates.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("tokens_in", sa.Integer, nullable=False, server_default="0"),
        sa.Column("tokens_out", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cache_read_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Numeric(10, 6), nullable=False, server_default="0"),
        sa.Column("processing_ms", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "uploaded_by",
            UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "uploaded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_ingestion_documents_tenant_status",
        "ingestion_documents",
        ["tenant_id", "status"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_ingestion_documents_file_hash",
        "ingestion_documents",
        ["file_hash_sha256"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_ingestion_documents_uploaded_at",
        "ingestion_documents",
        ["uploaded_at"],
        schema=SCHEMA,
    )

    # ── 5. ingestion_audit_log ────────────────────────────────────────────────
    op.create_table(
        "ingestion_audit_log",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "ingestion_id",
            UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.ingestion_documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("model_used", sa.String(50), nullable=True),
        sa.Column("prompt_version", sa.String(20), nullable=True),
        sa.Column("eval_score", sa.Numeric(4, 3), nullable=True),
        sa.Column("rules_violations", JSONB, nullable=True),
        sa.Column(
            "reviewer_id",
            UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("decision", sa.String(20), nullable=True),
        sa.Column("corrected_json", JSONB, nullable=True),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_ingestion_audit_log_ingestion_id",
        "ingestion_audit_log",
        ["ingestion_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_ingestion_audit_log_timestamp",
        "ingestion_audit_log",
        ["timestamp"],
        schema=SCHEMA,
    )

    # ── 6. review_queue ───────────────────────────────────────────────────────
    op.create_table(
        "review_queue",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "ingestion_id",
            UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.ingestion_documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("field_path", sa.String(255), nullable=True),
        sa.Column("extracted_value", sa.Text, nullable=True),
        sa.Column("confidence", sa.String(10), nullable=True),
        sa.Column("routing_reason", sa.String(255), nullable=True),
        sa.Column(
            "reviewer_id",
            UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("decision", sa.String(20), nullable=True),
        sa.Column("corrected_value", sa.Text, nullable=True),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_review_queue_ingestion_id", "review_queue", ["ingestion_id"], schema=SCHEMA
    )
    op.create_index(
        "ix_review_queue_status_created",
        "review_queue",
        ["status", "created_at"],
        schema=SCHEMA,
    )

    # ── 7. corrections_log ────────────────────────────────────────────────────
    op.create_table(
        "corrections_log",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "ingestion_id",
            UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.ingestion_documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("field_path", sa.String(255), nullable=False),
        sa.Column("original_value", sa.Text, nullable=True),
        sa.Column("corrected_value", sa.Text, nullable=True),
        sa.Column(
            "correction_type", sa.String(50), nullable=False, server_default="wrong_value"
        ),
        sa.Column(
            "reviewer_id",
            UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_corrections_log_ingestion_id",
        "corrections_log",
        ["ingestion_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_corrections_log_timestamp", "corrections_log", ["timestamp"], schema=SCHEMA
    )

    # ── 8. claude_api_usage ───────────────────────────────────────────────────
    op.create_table(
        "claude_api_usage",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "ingestion_id",
            UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.ingestion_documents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "query_id",
            UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.query_audit_log.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("service", sa.String(60), nullable=False),
        sa.Column("agent_id", sa.String(50), nullable=True),
        sa.Column("model", sa.String(50), nullable=False),
        sa.Column("tokens_in", sa.Integer, nullable=False, server_default="0"),
        sa.Column("tokens_out", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cache_read_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Numeric(10, 6), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_claude_api_usage_ingestion_id",
        "claude_api_usage",
        ["ingestion_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_claude_api_usage_timestamp", "claude_api_usage", ["timestamp"], schema=SCHEMA
    )
    op.create_index(
        "ix_claude_api_usage_service_model",
        "claude_api_usage",
        ["service", "model"],
        schema=SCHEMA,
    )

    # ── 9. claude_budget_config ───────────────────────────────────────────────
    op.create_table(
        "claude_budget_config",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("period", sa.String(20), nullable=False, server_default="monthly"),
        sa.Column("limit_usd", sa.Numeric(10, 2), nullable=False),
        sa.Column(
            "alert_threshold_pct", sa.Numeric(5, 2), nullable=False, server_default="80.00"
        ),
        sa.Column("auto_pause", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        schema=SCHEMA,
    )

    # ── Seed default budget config ─────────────────────────────────────────────
    op.execute(
        f"""
        INSERT INTO {SCHEMA}.claude_budget_config (id, period, limit_usd, alert_threshold_pct, auto_pause, is_active)
        VALUES (gen_random_uuid(), 'monthly', 500.00, 80.00, true, true)
        """
    )


def downgrade() -> None:
    # Drop in reverse order of creation (respect FK constraints)
    op.drop_table("claude_budget_config", schema=SCHEMA)
    op.drop_table("claude_api_usage", schema=SCHEMA)
    op.drop_table("corrections_log", schema=SCHEMA)
    op.drop_table("review_queue", schema=SCHEMA)
    op.drop_table("ingestion_audit_log", schema=SCHEMA)
    op.drop_table("ingestion_documents", schema=SCHEMA)
    op.drop_table("query_audit_log", schema=SCHEMA)
    op.drop_table("prompt_ab_tests", schema=SCHEMA)
    op.drop_table("prompt_templates", schema=SCHEMA)
