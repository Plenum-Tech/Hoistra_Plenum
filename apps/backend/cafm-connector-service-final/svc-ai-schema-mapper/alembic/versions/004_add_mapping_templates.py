"""Add mapping_templates table for storing reusable CMMS mappings.

Revision ID: 004
Revises: 003
Create Date: 2026-04-03 12:00:00.000000

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic
revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create mapping_templates table in plenum_cafm schema."""
    op.create_table(
        "mapping_templates",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_system", sa.String(100), nullable=False),
        sa.Column("table_name", sa.String(100), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("config_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
        schema="plenum_cafm",
    )

    # Create indexes for fast lookup
    op.create_index(
        "ix_mapping_templates_organization_id",
        "mapping_templates",
        ["organization_id"],
        schema="plenum_cafm",
    )
    op.create_index(
        "ix_mapping_templates_source_system",
        "mapping_templates",
        ["source_system"],
        schema="plenum_cafm",
    )
    op.create_index(
        "ix_mapping_templates_table_name",
        "mapping_templates",
        ["table_name"],
        schema="plenum_cafm",
    )
    op.create_index(
        "ix_mapping_templates_is_active",
        "mapping_templates",
        ["is_active"],
        schema="plenum_cafm",
    )

    # Composite index for common lookup pattern
    op.create_index(
        "ix_mapping_templates_org_system_table_active",
        "mapping_templates",
        ["organization_id", "source_system", "table_name", "is_active"],
        schema="plenum_cafm",
    )


def downgrade() -> None:
    """Drop mapping_templates table."""
    op.drop_index(
        "ix_mapping_templates_org_system_table_active",
        schema="plenum_cafm",
        table_name="mapping_templates",
    )
    op.drop_index(
        "ix_mapping_templates_is_active",
        schema="plenum_cafm",
        table_name="mapping_templates",
    )
    op.drop_index(
        "ix_mapping_templates_table_name",
        schema="plenum_cafm",
        table_name="mapping_templates",
    )
    op.drop_index(
        "ix_mapping_templates_source_system",
        schema="plenum_cafm",
        table_name="mapping_templates",
    )
    op.drop_index(
        "ix_mapping_templates_organization_id",
        schema="plenum_cafm",
        table_name="mapping_templates",
    )
    op.drop_table("mapping_templates", schema="plenum_cafm")
