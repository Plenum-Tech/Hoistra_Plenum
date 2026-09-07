"""Ensure work_orders approval columns exist (idempotent safety net).

Revision ID: 006
Revises: 005
Create Date: 2026-05-27
"""
from alembic import op

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE plenum_cafm.work_orders "
        "ADD COLUMN IF NOT EXISTS estimated_cost NUMERIC(14, 2)"
    )
    op.execute(
        "ALTER TABLE plenum_cafm.work_orders "
        "ADD COLUMN IF NOT EXISTS asset_category VARCHAR(100)"
    )


def downgrade() -> None:
    op.drop_column("work_orders", "asset_category", schema="plenum_cafm")
    op.drop_column("work_orders", "estimated_cost", schema="plenum_cafm")
