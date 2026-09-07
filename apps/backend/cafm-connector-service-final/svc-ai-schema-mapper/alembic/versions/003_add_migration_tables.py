"""Migration 003 — Create svc-AI-Schema-Mapper tables.

This is the first migration for svc-ai-schema-mapper. It creates:
- migration_jobs
- migration_field_mappings
- migration_hierarchy

All tables live in plenum_cafm schema.
This is a separate Alembic history from svc-ingestion (which has its own 001/002).
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "003"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create migration_jobs, migration_field_mappings, migration_hierarchy tables."""

    # Create migration_jobs table
    op.create_table(
        "migration_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cmms_name", sa.String(100), nullable=False),
        sa.Column("source_filename", sa.String(500), nullable=False),
        sa.Column("source_blob_url", sa.Text(), nullable=True),
        sa.Column("mapping_doc_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(30), nullable=False, server_default="running"),
        sa.Column("current_step", sa.String(100), nullable=True),
        sa.Column("progress_pct", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("t1_mapped_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("t2_auto_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("t2_human_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("t2_multi_merge_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unmapped_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_fields", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_records", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("orphan_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cycle_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_json_url", sa.Text(), nullable=True),
        sa.Column("output_csv_url", sa.Text(), nullable=True),
        sa.Column("output_sql_url", sa.Text(), nullable=True),
        sa.Column("migration_report_url", sa.Text(), nullable=True),
        sa.Column("mapping_flow_url", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        schema="plenum_cafm",
    )
    op.create_index(
        op.f("ix_plenum_cafm_migration_jobs_organization_id"),
        "migration_jobs",
        ["organization_id"],
        schema="plenum_cafm",
    )
    op.create_index(
        op.f("ix_plenum_cafm_migration_jobs_status"),
        "migration_jobs",
        ["status"],
        schema="plenum_cafm",
    )

    # Create migration_field_mappings table
    op.create_table(
        "migration_field_mappings",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("migration_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_field", sa.String(255), nullable=False),
        sa.Column("source_fields", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("merge_strategy", sa.String(50), nullable=True),
        sa.Column("target_field", sa.String(255), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("tier", sa.String(30), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("sample_values", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("transformation", sa.String(100), nullable=True),
        sa.Column("reviewer_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("langsmith_run_id", sa.String(100), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["migration_id"],
            ["plenum_cafm.migration_jobs.id"],
            ondelete="CASCADE",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.PrimaryKeyConstraint("id"),
        schema="plenum_cafm",
    )
    op.create_index(
        op.f("ix_plenum_cafm_migration_field_mappings_migration_id"),
        "migration_field_mappings",
        ["migration_id"],
        schema="plenum_cafm",
    )
    op.create_index(
        op.f("ix_plenum_cafm_migration_field_mappings_target_field"),
        "migration_field_mappings",
        ["target_field"],
        schema="plenum_cafm",
    )

    # Create migration_hierarchy table
    op.create_table(
        "migration_hierarchy",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("migration_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_table", sa.String(255), nullable=False),
        sa.Column("source_column", sa.String(255), nullable=False),
        sa.Column("target_table", sa.String(255), nullable=False),
        sa.Column("relationship_type", sa.String(50), nullable=False),
        sa.Column("direction", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("data_match_rate", sa.Float(), nullable=False),
        sa.Column("reasoning", sa.Text(), nullable=False),
        sa.Column("customer_confirmed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["migration_id"],
            ["plenum_cafm.migration_jobs.id"],
            ondelete="CASCADE",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.PrimaryKeyConstraint("id"),
        schema="plenum_cafm",
    )
    op.create_index(
        op.f("ix_plenum_cafm_migration_hierarchy_migration_id"),
        "migration_hierarchy",
        ["migration_id"],
        schema="plenum_cafm",
    )


def downgrade() -> None:
    """Drop all svc-AI-Schema-Mapper tables."""
    op.drop_index(
        op.f("ix_plenum_cafm_migration_hierarchy_migration_id"),
        table_name="migration_hierarchy",
        schema="plenum_cafm",
    )
    op.drop_table("migration_hierarchy", schema="plenum_cafm")

    op.drop_index(
        op.f("ix_plenum_cafm_migration_field_mappings_target_field"),
        table_name="migration_field_mappings",
        schema="plenum_cafm",
    )
    op.drop_index(
        op.f("ix_plenum_cafm_migration_field_mappings_migration_id"),
        table_name="migration_field_mappings",
        schema="plenum_cafm",
    )
    op.drop_table("migration_field_mappings", schema="plenum_cafm")

    op.drop_index(
        op.f("ix_plenum_cafm_migration_jobs_status"),
        table_name="migration_jobs",
        schema="plenum_cafm",
    )
    op.drop_index(
        op.f("ix_plenum_cafm_migration_jobs_organization_id"),
        table_name="migration_jobs",
        schema="plenum_cafm",
    )
    op.drop_table("migration_jobs", schema="plenum_cafm")
