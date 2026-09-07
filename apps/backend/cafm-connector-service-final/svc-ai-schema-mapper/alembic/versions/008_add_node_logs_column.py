"""Add node_logs JSONB column to migration_jobs and schema_mapping_jobs.

Each node appends its completion entry so the frontend can display per-node
logs and output summaries without a separate endpoint.

Revision ID: 008
Revises: 007
Create Date: 2026-04-19
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "migration_jobs",
        sa.Column("node_logs", JSONB, nullable=True, server_default="'[]'::jsonb"),
        schema="plenum_cafm",
    )
    op.add_column(
        "schema_mapping_jobs",
        sa.Column("node_logs", JSONB, nullable=True, server_default="'[]'::jsonb"),
        schema="plenum_cafm",
    )


def downgrade() -> None:
    op.drop_column("schema_mapping_jobs", "node_logs", schema="plenum_cafm")
    op.drop_column("migration_jobs", "node_logs", schema="plenum_cafm")
