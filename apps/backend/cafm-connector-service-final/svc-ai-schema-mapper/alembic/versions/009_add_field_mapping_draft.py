"""Add field_mapping_draft JSONB column to migration_jobs.

Stores Tier-2 semantic / field-mapping UI choices (table + column overrides)
so Field Structure Review can restore them after refresh or step transitions.

Revision ID: 009
Revises: 008
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "migration_jobs",
        sa.Column("field_mapping_draft", JSONB, nullable=True),
        schema="plenum_cafm",
    )


def downgrade() -> None:
    op.drop_column("migration_jobs", "field_mapping_draft", schema="plenum_cafm")
