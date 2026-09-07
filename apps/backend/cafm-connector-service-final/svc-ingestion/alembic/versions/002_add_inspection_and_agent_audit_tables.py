"""Add inspection, agent_audit_log, and orchestration_audit_log tables

Revision ID: 002
Revises: 001
Create Date: 2026-03-26

Creates 4 new tables in the plenum_cafm schema:

  - inspections              (DOCX/PDF agent output — per CLAUDE.md §8)
  - agent_audit_log          (Layer 5 per-agent determinism audit — EL-5.x)
  - orchestration_audit_log  (Layer 6 decision audit — EL-6.x, INSERT only)
  - document_generation_log  (Layer 7 document gen audit — EL-7.DOC.x)

Also adds review_queue_id FK column to corrections_log (needed by entity
resolver Tier 4 accept path) and a resolved_by / resolved_at on review_queue.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "plenum_cafm"


def upgrade() -> None:
    # ── 1. inspections ────────────────────────────────────────────────────────
    # Source: CLAUDE.md §8 (DOCX Agent) and §8 (inspections table definition)
    op.create_table(
        "inspections",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "asset_code",
            sa.String(50),
            sa.ForeignKey(f"{SCHEMA}.assets.asset_code", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("inspector", sa.String(255), nullable=True),
        sa.Column("inspection_date", sa.Date, nullable=True),
        sa.Column("section", sa.String(10), nullable=True),          # A through G
        sa.Column("finding_type", sa.String(100), nullable=True),
        sa.Column("observations", sa.Text, nullable=True),
        sa.Column("risk_level", sa.String(20), nullable=True),       # High | Medium | Low
        sa.Column("corrective_action", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("source_file", sa.String(500), nullable=True),     # original blob URL
        sa.Column("findings_jsonb", JSONB, nullable=True),           # full raw extraction
        sa.Column(
            "ingestion_id",
            UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.ingestion_documents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_inspections_asset_code", "inspections", ["asset_code"], schema=SCHEMA
    )
    op.create_index(
        "ix_inspections_inspection_date",
        "inspections",
        ["inspection_date"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_inspections_risk_level", "inspections", ["risk_level"], schema=SCHEMA
    )
    op.create_index(
        "ix_inspections_corrective_action",
        "inspections",
        ["corrective_action"],
        schema=SCHEMA,
    )

    # ── 2. agent_audit_log ────────────────────────────────────────────────────
    # Source: CLAUDE.md §13 — Layer 5 per-agent determinism audit
    # Captures EL-5.BOUND, EL-5.AGG (per-run), EL-5.VOTE, EL-5.CONSTRAIN results.
    op.create_table(
        "agent_audit_log",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("agent_id", sa.String(50), nullable=False),        # asset|wo|pm|parts|inspection
        sa.Column("domain", sa.String(50), nullable=False),
        sa.Column("asset_code", sa.String(50), nullable=True),

        # EL-5.BOUND result
        sa.Column("bound_validation_passed", sa.Boolean, nullable=False, server_default="false"),

        # EL-5.AGG — individual run outputs (3 runs)
        sa.Column("run_1_output", JSONB, nullable=True),
        sa.Column("run_2_output", JSONB, nullable=True),
        sa.Column("run_3_output", JSONB, nullable=True),

        # EL-5.AGG — per-run validity flags
        sa.Column("run_1_valid", sa.Boolean, nullable=True),
        sa.Column("run_2_valid", sa.Boolean, nullable=True),
        sa.Column("run_3_valid", sa.Boolean, nullable=True),

        # EL-5.VOTE result
        sa.Column("runs_agreed", sa.Integer, nullable=True),
        sa.Column("winner_status", sa.String(50), nullable=True),
        sa.Column("winner_confidence", sa.Numeric(4, 3), nullable=True),

        # EL-5.CONSTRAIN — hard rules + gate
        sa.Column("hard_rules_fired", JSONB, nullable=True),         # which YAML rules overrode AI
        sa.Column("final_status", sa.String(50), nullable=True),
        sa.Column("confidence_gate_passed", sa.Boolean, nullable=True),  # EL-5.CONSTRAIN result
        sa.Column("requires_human_review", sa.Boolean, nullable=False, server_default="false"),

        # Usage tracking
        sa.Column("model_used", sa.String(50), nullable=True),
        sa.Column("tokens_total", sa.Integer, nullable=True),
        sa.Column("cost_usd", sa.Numeric(10, 6), nullable=True),

        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_agent_audit_log_agent_id", "agent_audit_log", ["agent_id"], schema=SCHEMA
    )
    op.create_index(
        "ix_agent_audit_log_asset_code", "agent_audit_log", ["asset_code"], schema=SCHEMA
    )
    op.create_index(
        "ix_agent_audit_log_timestamp", "agent_audit_log", ["timestamp"], schema=SCHEMA
    )
    op.create_index(
        "ix_agent_audit_log_requires_human_review",
        "agent_audit_log",
        ["requires_human_review"],
        schema=SCHEMA,
    )

    # ── 3. orchestration_audit_log ────────────────────────────────────────────
    # Source: CLAUDE.md §13 — Layer 6 full decision audit (INSERT ONLY — no UPDATE/DELETE)
    # Captures EL-6.BOUND, EL-6.AGG (per-run), EL-6.VOTE, EL-6.CONSTRAIN results.
    op.create_table(
        "orchestration_audit_log",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("asset_code", sa.String(50), nullable=True),

        # EL-6.BOUND result
        sa.Column("bound_passed", sa.Boolean, nullable=False, server_default="false"),

        # EL-6 action + confidence
        sa.Column("action", sa.String(50), nullable=False),          # create_wo|order_part|alert_critical|no_action|human_review
        sa.Column("priority", sa.String(20), nullable=True),         # low|medium|high|critical
        sa.Column("confidence", sa.Numeric(4, 3), nullable=True),
        sa.Column("reasoning", sa.Text, nullable=True),              # max 60 words

        # EL-6.AGG — per-run validity flags
        sa.Column("runs_agreed", sa.Integer, nullable=True),
        sa.Column("run_1_valid", sa.Boolean, nullable=True),
        sa.Column("run_2_valid", sa.Boolean, nullable=True),
        sa.Column("run_3_valid", sa.Boolean, nullable=True),

        # EL-6.CONSTRAIN results
        sa.Column("confidence_gate_passed", sa.Boolean, nullable=True),
        sa.Column("safety_passed", sa.Boolean, nullable=True),

        # Full agent result payload for audit trail
        sa.Column("agent_results_jsonb", JSONB, nullable=True),      # all 5 AgentResults serialised
        sa.Column("hard_rules_fired", JSONB, nullable=True),         # cross-agent rules that fired

        # Usage tracking
        sa.Column("model_used", sa.String(50), nullable=True),
        sa.Column("tokens_total", sa.Integer, nullable=True),
        sa.Column("cost_usd", sa.Numeric(10, 6), nullable=True),

        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_orchestration_audit_log_asset_code",
        "orchestration_audit_log",
        ["asset_code"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_orchestration_audit_log_action",
        "orchestration_audit_log",
        ["action"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_orchestration_audit_log_timestamp",
        "orchestration_audit_log",
        ["timestamp"],
        schema=SCHEMA,
    )
    # Enforce INSERT-only at DB level: revoke UPDATE/DELETE from app user.
    # The app role (plenum_app) must NOT have UPDATE or DELETE on this table.
    op.execute(
        f"REVOKE UPDATE, DELETE ON {SCHEMA}.orchestration_audit_log FROM plenum_app"
    )

    # ── 4. document_generation_log ────────────────────────────────────────────
    # Source: CLAUDE.md §13 — Layer 7 document generation audit
    # Captures EL-7.DOC.PLAN, EL-7.DOC.RENDER, EL-7.DOC.EVAL results.
    op.create_table(
        "document_generation_log",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("request_text", sa.Text, nullable=True),
        sa.Column("intent_type", sa.String(30), nullable=True),       # document_generate|template_fill
        sa.Column("document_type", sa.String(50), nullable=True),     # pm_schedule|wo_report|etc.

        # EL-7.DOC.PLAN
        sa.Column("document_plan_json", JSONB, nullable=True),        # full DocumentPlan executed
        sa.Column("plan_validation_passed", sa.Boolean, nullable=True),
        sa.Column("plan_runs_agreed", sa.Integer, nullable=True),

        # Render output
        sa.Column("output_format", sa.String(10), nullable=True),     # docx|xlsx|pdf
        sa.Column("output_blob_url", sa.Text, nullable=True),
        sa.Column("data_sources", JSONB, nullable=True),              # tables + row counts consulted
        sa.Column("render_ms", sa.Integer, nullable=True),

        # EL-7.DOC.RENDER + EL-7.DOC.EVAL
        sa.Column("spot_checks_run", sa.Integer, nullable=True),      # values checked
        sa.Column("spot_checks_passed", sa.Integer, nullable=True),   # values verified
        sa.Column("eval_score", sa.Numeric(4, 3), nullable=True),     # EL-7.DOC.EVAL score
        sa.Column("held_for_review", sa.Boolean, nullable=False, server_default="false"),  # eval_score < 0.85

        # Usage tracking
        sa.Column("model_used", sa.String(50), nullable=True),
        sa.Column("tokens_in", sa.Integer, nullable=True),
        sa.Column("tokens_out", sa.Integer, nullable=True),
        sa.Column("cost_usd", sa.Numeric(10, 6), nullable=True),

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
        "ix_document_generation_log_user_id",
        "document_generation_log",
        ["user_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_document_generation_log_timestamp",
        "document_generation_log",
        ["timestamp"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_document_generation_log_held_for_review",
        "document_generation_log",
        ["held_for_review"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_document_generation_log_document_type",
        "document_generation_log",
        ["document_type"],
        schema=SCHEMA,
    )

    # ── 5. Alter review_queue — add resolved_by / resolved_at columns ──────────
    # Needed by entity_resolver.py Tier 4 accept_manual_resolution()
    op.add_column(
        "review_queue",
        sa.Column("review_type", sa.String(50), nullable=True),      # entity_resolution|low_confidence|schema_mapping
        schema=SCHEMA,
    )
    op.add_column(
        "review_queue",
        sa.Column("payload", JSONB, nullable=True),                   # entity resolver context payload
        schema=SCHEMA,
    )
    op.add_column(
        "review_queue",
        sa.Column("resolved_value", sa.Text, nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "review_queue",
        sa.Column(
            "resolved_by",
            sa.String(255),
            nullable=True,
        ),
        schema=SCHEMA,
    )
    op.add_column(
        "review_queue",
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )

    # ── 6. Alter corrections_log — add review_queue_id FK ─────────────────────
    # Needed by entity_resolver.py Tier 4 accept_manual_resolution()
    op.add_column(
        "corrections_log",
        sa.Column(
            "review_queue_id",
            UUID(as_uuid=True),
            sa.ForeignKey(f"{SCHEMA}.review_queue.id", ondelete="SET NULL"),
            nullable=True,
        ),
        schema=SCHEMA,
    )
    op.add_column(
        "corrections_log",
        sa.Column("corrected_by", sa.String(255), nullable=True),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_corrections_log_review_queue_id",
        "corrections_log",
        ["review_queue_id"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    # Remove alterations first
    op.drop_index("ix_corrections_log_review_queue_id", table_name="corrections_log", schema=SCHEMA)
    op.drop_column("corrections_log", "corrected_by", schema=SCHEMA)
    op.drop_column("corrections_log", "review_queue_id", schema=SCHEMA)

    op.drop_column("review_queue", "resolved_at", schema=SCHEMA)
    op.drop_column("review_queue", "resolved_by", schema=SCHEMA)
    op.drop_column("review_queue", "resolved_value", schema=SCHEMA)
    op.drop_column("review_queue", "payload", schema=SCHEMA)
    op.drop_column("review_queue", "review_type", schema=SCHEMA)

    # Drop new tables in reverse FK order
    op.drop_table("document_generation_log", schema=SCHEMA)
    op.drop_table("orchestration_audit_log", schema=SCHEMA)
    op.drop_table("agent_audit_log", schema=SCHEMA)
    op.drop_table("inspections", schema=SCHEMA)
