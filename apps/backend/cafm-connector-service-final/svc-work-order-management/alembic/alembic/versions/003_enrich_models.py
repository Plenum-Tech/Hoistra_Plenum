"""BE2-03 — Enrich wo_assets, wo_locations, wo_journey_logs with extended fields

Revision ID: 003
Revises: 002
Create Date: 2026-04-28
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = '003'
down_revision = '002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── wo_assets: extended fields ────────────────────────────────────────────
    op.add_column('wo_assets', sa.Column('category',              sa.String(100)),  schema='plenum_cafm')
    op.add_column('wo_assets', sa.Column('subcategory',           sa.String(100)),  schema='plenum_cafm')
    op.add_column('wo_assets', sa.Column('installation_date',     sa.DateTime()),   schema='plenum_cafm')
    op.add_column('wo_assets', sa.Column('warranty_expiry',       sa.DateTime()),   schema='plenum_cafm')
    op.add_column('wo_assets', sa.Column('last_maintenance_date', sa.DateTime()),   schema='plenum_cafm')
    op.add_column('wo_assets', sa.Column('next_maintenance_date', sa.DateTime()),   schema='plenum_cafm')
    op.add_column('wo_assets', sa.Column('condition',             sa.String(20)),   schema='plenum_cafm')
    op.add_column('wo_assets', sa.Column('criticality_level',     sa.String(20)),   schema='plenum_cafm')
    op.add_column('wo_assets', sa.Column('operating_hours',       sa.Integer(),     server_default='0'), schema='plenum_cafm')
    op.add_column('wo_assets', sa.Column('expected_lifespan',     sa.Integer()),    schema='plenum_cafm')
    op.add_column('wo_assets', sa.Column('replacement_cost',      sa.Numeric(15, 2)), schema='plenum_cafm')
    op.add_column('wo_assets', sa.Column('maintenance_cost',      sa.Numeric(15, 2)), schema='plenum_cafm')
    op.add_column('wo_assets', sa.Column('occupancy_info',        JSONB),           schema='plenum_cafm')
    op.add_column('wo_assets', sa.Column('custom_fields',         JSONB),           schema='plenum_cafm')
    op.add_column('wo_assets', sa.Column('updated_at',            sa.DateTime(timezone=True), server_default=sa.func.now()), schema='plenum_cafm')

    op.create_index('idx_wo_assets_category',     'wo_assets', ['category'],         schema='plenum_cafm')
    op.create_index('idx_wo_assets_criticality',  'wo_assets', ['criticality_level'], schema='plenum_cafm')

    # ── wo_locations: extended fields ─────────────────────────────────────────
    op.add_column('wo_locations', sa.Column('floor_number',       sa.Integer()),    schema='plenum_cafm')
    op.add_column('wo_locations', sa.Column('room_number',        sa.String(50)),   schema='plenum_cafm')
    op.add_column('wo_locations', sa.Column('room_name',          sa.String(255)),  schema='plenum_cafm')
    op.add_column('wo_locations', sa.Column('area_sqm',           sa.Numeric(10, 2)), schema='plenum_cafm')
    op.add_column('wo_locations', sa.Column('occupancy_type',     sa.String(50)),   schema='plenum_cafm')
    op.add_column('wo_locations', sa.Column('access_restricted',  sa.Boolean(),     server_default='false'), schema='plenum_cafm')
    op.add_column('wo_locations', sa.Column('access_credentials', sa.Text()),       schema='plenum_cafm')
    op.add_column('wo_locations', sa.Column('updated_at',         sa.DateTime(timezone=True), server_default=sa.func.now()), schema='plenum_cafm')

    # ── wo_journey_logs: extended fields ──────────────────────────────────────
    op.add_column('wo_journey_logs', sa.Column('asset_id',                    sa.String(50)),  schema='plenum_cafm')
    op.add_column('wo_journey_logs', sa.Column('source_system',               sa.String(50)),  schema='plenum_cafm')
    op.add_column('wo_journey_logs', sa.Column('journey_status',              sa.String(50)),  schema='plenum_cafm')
    op.add_column('wo_journey_logs', sa.Column('assigned_technician_id',      sa.String(100)), schema='plenum_cafm')
    op.add_column('wo_journey_logs', sa.Column('assigned_technician_name',    sa.String(255)), schema='plenum_cafm')
    op.add_column('wo_journey_logs', sa.Column('team_members',                JSONB),          schema='plenum_cafm')
    op.add_column('wo_journey_logs', sa.Column('estimated_cost',              sa.Numeric(15, 2)), schema='plenum_cafm')
    op.add_column('wo_journey_logs', sa.Column('actual_cost',                 sa.Numeric(15, 2)), schema='plenum_cafm')
    op.add_column('wo_journey_logs', sa.Column('estimated_duration_hours',    sa.Integer()),   schema='plenum_cafm')
    op.add_column('wo_journey_logs', sa.Column('actual_duration_hours',       sa.Integer()),   schema='plenum_cafm')
    # BE2 timeline tracking: actual start/end timestamps for real duration calculation
    op.add_column('wo_journey_logs', sa.Column('actual_start',                sa.DateTime(timezone=True)), schema='plenum_cafm')
    op.add_column('wo_journey_logs', sa.Column('actual_end',                  sa.DateTime(timezone=True)), schema='plenum_cafm')
    op.add_column('wo_journey_logs', sa.Column('resources_used',              JSONB),          schema='plenum_cafm')
    op.add_column('wo_journey_logs', sa.Column('completion_quality_score',    sa.Integer()),   schema='plenum_cafm')
    op.add_column('wo_journey_logs', sa.Column('customer_satisfaction_score', sa.Integer()),   schema='plenum_cafm')
    op.add_column('wo_journey_logs', sa.Column('notes',                       sa.Text()),      schema='plenum_cafm')
    op.add_column('wo_journey_logs', sa.Column('status_change_history',       JSONB),          schema='plenum_cafm')
    op.add_column('wo_journey_logs', sa.Column('milestone_history',           JSONB),          schema='plenum_cafm')
    op.add_column('wo_journey_logs', sa.Column('created_by',                  sa.String(100)), schema='plenum_cafm')
    op.add_column('wo_journey_logs', sa.Column('updated_by',                  sa.String(100)), schema='plenum_cafm')

    op.create_index('idx_wo_jlog_asset_id',      'wo_journey_logs', ['asset_id'],      schema='plenum_cafm')
    op.create_index('idx_wo_jlog_journey_status', 'wo_journey_logs', ['journey_status'], schema='plenum_cafm')


def downgrade() -> None:
    # wo_journey_logs
    op.drop_index('idx_wo_jlog_journey_status', table_name='wo_journey_logs', schema='plenum_cafm')
    op.drop_index('idx_wo_jlog_asset_id',       table_name='wo_journey_logs', schema='plenum_cafm')
    for col in [
        'updated_by', 'created_by', 'milestone_history', 'status_change_history',
        'notes', 'customer_satisfaction_score', 'completion_quality_score',
        'resources_used', 'actual_end', 'actual_start',
        'actual_duration_hours', 'estimated_duration_hours',
        'actual_cost', 'estimated_cost', 'team_members',
        'assigned_technician_name', 'assigned_technician_id',
        'journey_status', 'source_system', 'asset_id',
    ]:
        op.drop_column('wo_journey_logs', col, schema='plenum_cafm')

    # wo_locations
    for col in [
        'updated_at', 'access_credentials', 'access_restricted',
        'occupancy_type', 'area_sqm', 'room_name', 'room_number', 'floor_number',
    ]:
        op.drop_column('wo_locations', col, schema='plenum_cafm')

    # wo_assets
    op.drop_index('idx_wo_assets_criticality', table_name='wo_assets', schema='plenum_cafm')
    op.drop_index('idx_wo_assets_category',    table_name='wo_assets', schema='plenum_cafm')
    for col in [
        'updated_at', 'custom_fields', 'occupancy_info',
        'maintenance_cost', 'replacement_cost', 'expected_lifespan',
        'operating_hours', 'criticality_level', 'condition',
        'next_maintenance_date', 'last_maintenance_date',
        'warranty_expiry', 'installation_date',
        'subcategory', 'category',
    ]:
        op.drop_column('wo_assets', col, schema='plenum_cafm')
