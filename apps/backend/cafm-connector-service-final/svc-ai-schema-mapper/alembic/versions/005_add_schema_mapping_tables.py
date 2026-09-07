"""Migration 005 — Create schema mapping pipeline tables.

Creates two tables for the 6-node schema mapping pipeline:
- schema_mapping_jobs (master record with per-node progress tracking)
- schema_mapping_field_mappings (immutable audit trail of field mappings)

Both tables live in plenum_cafm schema.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create schema_mapping_jobs and schema_mapping_field_mappings tables."""

    # Create schema_mapping_jobs table
    op.create_table(
        "schema_mapping_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.func.gen_random_uuid()),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("external_cmms_name", sa.String(100), nullable=False),
        sa.Column("schema_source", sa.String(50), nullable=False),  # database_url|yaml_file|json_file|ddl_sql
        sa.Column("schema_format", sa.String(20), nullable=False),  # sql|yaml|json
        sa.Column("status", sa.String(30), nullable=False, server_default="ingest"),  # ingest|deterministic|semantic|hierarchy|verify|output|complete|error
        sa.Column("current_node", sa.Integer(), nullable=False, server_default="1"),  # 1-6
        sa.Column("progress_pct", sa.Float(), nullable=False, server_default="0.0"),  # 0-100
        sa.Column("total_tables", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_fields", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tier1_mapped", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tier2_auto_mapped", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tier2_flagged", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unmapped", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("detected_fk_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("hierarchy_depth", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("implicit_hierarchy_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("final_mapping_config", postgresql.JSONB(), nullable=True),
        sa.Column("final_summary", postgresql.JSONB(), nullable=True),
        sa.Column("mapping_coverage_pct", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("node_state_json", postgresql.JSONB(), nullable=True),  # Full SchemaMappingState for resuming
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        schema="plenum_cafm",
    )

    # Create indexes for schema_mapping_jobs
    op.create_index(
        "idx_schema_mapping_jobs_org_id",
        "schema_mapping_jobs",
        ["organization_id"],
        schema="plenum_cafm",
    )
    op.create_index(
        "idx_schema_mapping_jobs_status",
        "schema_mapping_jobs",
        ["status"],
        schema="plenum_cafm",
    )
    op.create_index(
        "idx_schema_mapping_jobs_current_node",
        "schema_mapping_jobs",
        ["current_node"],
        schema="plenum_cafm",
    )
    op.create_index(
        "idx_schema_mapping_jobs_started_at",
        "schema_mapping_jobs",
        ["started_at"],
        schema="plenum_cafm",
        postgresql_using="btree",
    )

    # Create schema_mapping_field_mappings table
    op.create_table(
        "schema_mapping_field_mappings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.func.gen_random_uuid()),
        sa.Column(
            "schema_mapping_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("plenum_cafm.schema_mapping_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_field", sa.String(255), nullable=False),
        sa.Column("source_table", sa.String(255), nullable=False),
        sa.Column("target_field", sa.String(255), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),  # 0.0-1.0
        sa.Column("tier", sa.String(30), nullable=False),  # T1_exact|T1_alias|T1_regex|T2_semantic|unmapped
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("mapped_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        schema="plenum_cafm",
    )

    # Create indexes for schema_mapping_field_mappings
    op.create_index(
        "idx_schema_mapping_field_mappings_schema_id",
        "schema_mapping_field_mappings",
        ["schema_mapping_id"],
        schema="plenum_cafm",
    )
    op.create_index(
        "idx_schema_mapping_field_mappings_target_field",
        "schema_mapping_field_mappings",
        ["target_field"],
        schema="plenum_cafm",
    )
    op.create_index(
        "idx_schema_mapping_field_mappings_tier",
        "schema_mapping_field_mappings",
        ["tier"],
        schema="plenum_cafm",
    )


def downgrade() -> None:
    """Drop schema_mapping tables."""
    op.drop_table("schema_mapping_field_mappings", schema="plenum_cafm")
    op.drop_table("schema_mapping_jobs", schema="plenum_cafm")
