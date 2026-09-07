"""Add gate payload columns to migration_jobs.

Adds:
  - pending_gate_type   VARCHAR(50)  — which HITL gate is currently waiting
  - pending_gate_payload JSONB       — the interrupt payload the frontend reads
  - error_timestamp     TIMESTAMPTZ  — when the error occurred

These columns allow the frontend to poll /status and know exactly which
gate is active, what to display, and what payload to POST back via /approve.

Revision ID: 006
Revises: 005
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "migration_jobs",
        sa.Column("pending_gate_type", sa.String(50), nullable=True),
        schema="plenum_cafm",
    )
    op.add_column(
        "migration_jobs",
        sa.Column("pending_gate_payload", JSONB, nullable=True),
        schema="plenum_cafm",
    )
    op.add_column(
        "migration_jobs",
        sa.Column(
            "error_timestamp",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        schema="plenum_cafm",
    )


def downgrade() -> None:
    op.drop_column("migration_jobs", "error_timestamp", schema="plenum_cafm")
    op.drop_column("migration_jobs", "pending_gate_payload", schema="plenum_cafm")
    op.drop_column("migration_jobs", "pending_gate_type", schema="plenum_cafm")
