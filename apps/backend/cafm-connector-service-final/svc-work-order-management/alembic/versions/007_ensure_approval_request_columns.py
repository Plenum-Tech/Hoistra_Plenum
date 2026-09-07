"""Ensure wo_approval_requests multi-step columns exist (idempotent).

Revision ID: 007
Revises: 006
Create Date: 2026-05-27
"""
from alembic import op

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for ddl in (
        "ALTER TABLE plenum_cafm.wo_approval_requests ADD COLUMN IF NOT EXISTS level INTEGER",
        "ALTER TABLE plenum_cafm.wo_approval_requests ADD COLUMN IF NOT EXISTS step_order INTEGER",
        "ALTER TABLE plenum_cafm.wo_approval_requests ADD COLUMN IF NOT EXISTS risk_score INTEGER",
        "ALTER TABLE plenum_cafm.wo_approval_requests ADD COLUMN IF NOT EXISTS match_score INTEGER",
        "ALTER TABLE plenum_cafm.wo_approval_requests ADD COLUMN IF NOT EXISTS suggestion_source VARCHAR(20)",
        "ALTER TABLE plenum_cafm.wo_approval_requests ADD COLUMN IF NOT EXISTS unblocked_at TIMESTAMPTZ",
    ):
        op.execute(ddl)


def downgrade() -> None:
    pass
