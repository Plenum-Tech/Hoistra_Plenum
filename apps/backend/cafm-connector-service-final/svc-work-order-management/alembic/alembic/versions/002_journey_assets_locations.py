"""BE2 — assets, locations, status history, journey log columns

Revision ID: 002
Revises: 001
Create Date: 2026-04-28
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = '002'
down_revision = '001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── wo_assets ─────────────────────────────────────────────────────────────
    op.create_table(
        'wo_assets',
        sa.Column('asset_id',      sa.String(50),  primary_key=True),
        sa.Column('asset_name',    sa.String(255), nullable=False),
        sa.Column('asset_type',    sa.String(100)),
        sa.Column('location',      sa.String(255)),
        sa.Column('manufacturer',  sa.String(255)),
        sa.Column('model',         sa.String(255)),
        sa.Column('serial_number', sa.String(255)),
        sa.Column('active',        sa.Boolean,     server_default='true'),
        sa.Column('created_at',    sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema='plenum_cafm',
    )
    op.create_index('idx_wo_assets_type',     'wo_assets', ['asset_type'], schema='plenum_cafm')
    op.create_index('idx_wo_assets_location', 'wo_assets', ['location'],   schema='plenum_cafm')

    # ── wo_locations ──────────────────────────────────────────────────────────
    op.create_table(
        'wo_locations',
        sa.Column('location_id', sa.String(50),  primary_key=True),
        sa.Column('name',        sa.String(255), nullable=False),
        sa.Column('building',    sa.String(255)),
        sa.Column('floor',       sa.String(100)),
        sa.Column('zone',        sa.String(100)),
        sa.Column('active',      sa.Boolean,     server_default='true'),
        sa.Column('created_at',  sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema='plenum_cafm',
    )

    # ── wo_status_history ────────────────────────────────────────────────────
    op.create_table(
        'wo_status_history',
        sa.Column('history_id',    sa.String(50),  primary_key=True),
        sa.Column('work_order_id', sa.String(50),  nullable=False),
        sa.Column('from_status',   sa.String(50)),
        sa.Column('to_status',     sa.String(50),  nullable=False),
        sa.Column('changed_by',    sa.String(255), server_default='system'),
        sa.Column('notes',         sa.Text),
        sa.Column('changed_at',    sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema='plenum_cafm',
    )
    op.create_index('idx_wo_history_woid', 'wo_status_history', ['work_order_id'], schema='plenum_cafm')

    # ── wo_journey_logs — new columns ────────────────────────────────────────
    op.add_column('wo_journey_logs', sa.Column('status',            sa.String(50),  server_default='active'), schema='plenum_cafm')
    op.add_column('wo_journey_logs', sa.Column('milestones',        JSONB,          server_default='[]'),     schema='plenum_cafm')
    op.add_column('wo_journey_logs', sa.Column('expected_timeline', JSONB,          server_default='{}'),     schema='plenum_cafm')


def downgrade() -> None:
    op.drop_column('wo_journey_logs', 'expected_timeline', schema='plenum_cafm')
    op.drop_column('wo_journey_logs', 'milestones',        schema='plenum_cafm')
    op.drop_column('wo_journey_logs', 'status',            schema='plenum_cafm')

    op.drop_index('idx_wo_history_woid', table_name='wo_status_history', schema='plenum_cafm')
    op.drop_table('wo_status_history', schema='plenum_cafm')
    op.drop_table('wo_locations',      schema='plenum_cafm')
    op.drop_index('idx_wo_assets_location', table_name='wo_assets', schema='plenum_cafm')
    op.drop_index('idx_wo_assets_type',     table_name='wo_assets', schema='plenum_cafm')
    op.drop_table('wo_assets',         schema='plenum_cafm')
