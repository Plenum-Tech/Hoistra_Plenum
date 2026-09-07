"""BE2-03 — Add extended fields to wo_journey_logs (actual timestamps, cost, technician, health)

Revision ID: 004
Revises: 003
Create Date: 2026-04-30
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = '004'
down_revision = '003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('wo_journey_logs', sa.Column('actual_start',                sa.DateTime(timezone=True)), schema='plenum_cafm')
    op.add_column('wo_journey_logs', sa.Column('actual_end',                  sa.DateTime(timezone=True)), schema='plenum_cafm')
    op.add_column('wo_journey_logs', sa.Column('asset_id',                    sa.String(50)),              schema='plenum_cafm')
    op.add_column('wo_journey_logs', sa.Column('source_system',               sa.String(50)),              schema='plenum_cafm')
    op.add_column('wo_journey_logs', sa.Column('journey_status',              sa.String(50)),              schema='plenum_cafm')
    op.add_column('wo_journey_logs', sa.Column('assigned_technician_id',      sa.String(100)),             schema='plenum_cafm')
    op.add_column('wo_journey_logs', sa.Column('assigned_technician_name',    sa.String(255)),             schema='plenum_cafm')
    op.add_column('wo_journey_logs', sa.Column('team_members',                JSONB),                      schema='plenum_cafm')
    op.add_column('wo_journey_logs', sa.Column('estimated_cost',              sa.Numeric(15, 2)),          schema='plenum_cafm')
    op.add_column('wo_journey_logs', sa.Column('actual_cost',                 sa.Numeric(15, 2)),          schema='plenum_cafm')
    op.add_column('wo_journey_logs', sa.Column('estimated_duration_hours',    sa.Integer()),               schema='plenum_cafm')
    op.add_column('wo_journey_logs', sa.Column('actual_duration_hours',       sa.Integer()),               schema='plenum_cafm')
    op.add_column('wo_journey_logs', sa.Column('resources_used',              JSONB),                      schema='plenum_cafm')
    op.add_column('wo_journey_logs', sa.Column('completion_quality_score',    sa.Integer()),               schema='plenum_cafm')
    op.add_column('wo_journey_logs', sa.Column('customer_satisfaction_score', sa.Integer()),               schema='plenum_cafm')
    op.add_column('wo_journey_logs', sa.Column('notes',                       sa.Text()),                  schema='plenum_cafm')
    op.add_column('wo_journey_logs', sa.Column('status_change_history',       JSONB),                      schema='plenum_cafm')
    op.add_column('wo_journey_logs', sa.Column('milestone_history',           JSONB),                      schema='plenum_cafm')
    op.add_column('wo_journey_logs', sa.Column('created_by',                  sa.String(100)),             schema='plenum_cafm')
    op.add_column('wo_journey_logs', sa.Column('updated_by',                  sa.String(100)),             schema='plenum_cafm')

    op.create_index('idx_wo_jlog_asset_id',      'wo_journey_logs', ['asset_id'],       schema='plenum_cafm')
    op.create_index('idx_wo_jlog_journey_status', 'wo_journey_logs', ['journey_status'], schema='plenum_cafm')


def downgrade() -> None:
    op.drop_index('idx_wo_jlog_journey_status', table_name='wo_journey_logs', schema='plenum_cafm')
    op.drop_index('idx_wo_jlog_asset_id',       table_name='wo_journey_logs', schema='plenum_cafm')
    for col in [
        'updated_by', 'created_by', 'milestone_history', 'status_change_history',
        'notes', 'customer_satisfaction_score', 'completion_quality_score',
        'resources_used', 'actual_duration_hours', 'estimated_duration_hours',
        'actual_cost', 'estimated_cost', 'team_members',
        'assigned_technician_name', 'assigned_technician_id',
        'journey_status', 'source_system', 'asset_id',
        'actual_end', 'actual_start',
    ]:
        op.drop_column('wo_journey_logs', col, schema='plenum_cafm')
