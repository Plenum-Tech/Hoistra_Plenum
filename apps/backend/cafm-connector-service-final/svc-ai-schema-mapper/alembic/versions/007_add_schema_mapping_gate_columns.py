"""Add gate payload columns to schema_mapping_jobs.

Mirrors the same columns added to migration_jobs in migration 006,
so the frontend can display HITL gate payloads for the schema mapping pipeline.

Revision ID: 007
Revises: 006
Create Date: 2026-04-13
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "schema_mapping_jobs",
        sa.Column("pending_gate_type", sa.String(50), nullable=True),
        schema="plenum_cafm",
    )
    op.add_column(
        "schema_mapping_jobs",
        sa.Column("pending_gate_payload", JSONB, nullable=True),
        schema="plenum_cafm",
    )
    op.add_column(
        "schema_mapping_jobs",
        sa.Column("error_timestamp", sa.DateTime(timezone=True), nullable=True),
        schema="plenum_cafm",
    )


def downgrade() -> None:
    op.drop_column("schema_mapping_jobs", "error_timestamp", schema="plenum_cafm")
    op.drop_column("schema_mapping_jobs", "pending_gate_payload", schema="plenum_cafm")
    op.drop_column("schema_mapping_jobs", "pending_gate_type", schema="plenum_cafm")
