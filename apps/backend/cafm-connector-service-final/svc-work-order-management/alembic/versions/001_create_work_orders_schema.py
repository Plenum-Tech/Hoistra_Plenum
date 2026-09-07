"""create work orders schema

Revision ID: 001
Revises:
Create Date: 2026-04-28
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = '001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS plenum_cafm")

    op.create_table(
        'work_orders',
        sa.Column('work_order_id',        sa.String(50),  primary_key=True),
        sa.Column('source',               sa.String(50),  nullable=False),
        sa.Column('source_reference',     sa.String(255)),
        sa.Column('asset',                sa.String(255)),
        sa.Column('location',             sa.String(255)),
        sa.Column('issue_description',    sa.Text),
        sa.Column('task_description',     sa.Text),
        sa.Column('priority',             sa.String(20),  server_default='medium'),
        sa.Column('request_type',         sa.String(50),  server_default='repair'),
        sa.Column('status',               sa.String(50),  server_default='pending_approval'),
        sa.Column('approval_type',        sa.String(50)),
        sa.Column('requester_name',       sa.String(255)),
        sa.Column('requester_email',      sa.String(255)),
        sa.Column('requester_phone',      sa.String(50)),
        sa.Column('vendor',               sa.String(255)),
        sa.Column('manpower',             JSONB),
        sa.Column('scheduled_date',       sa.String(20)),
        sa.Column('scheduled_time',       sa.String(20)),
        sa.Column('estimated_duration',   sa.Float),
        sa.Column('inspection_required',  sa.Boolean,     server_default='false'),
        sa.Column('special_requirements', sa.Text),
        sa.Column('cmms_work_order_id',   sa.String(100)),
        sa.Column('journey_log_id',       sa.String(100)),
        sa.Column('created_at',           sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('created_by',           sa.String(255)),
        sa.Column('approved_at',          sa.DateTime(timezone=True)),
        sa.Column('prepared_at',          sa.DateTime(timezone=True)),
        sa.Column('sent_to_cmms_at',      sa.DateTime(timezone=True)),
        schema='plenum_cafm',
    )

    op.create_table(
        'wo_approval_requests',
        sa.Column('request_id',     sa.String(50),  primary_key=True),
        sa.Column('work_order_id',  sa.String(50),  nullable=False),
        sa.Column('approval_type',  sa.String(50)),
        sa.Column('approver',       sa.String(255)),
        sa.Column('status',         sa.String(20),  server_default='pending'),
        sa.Column('notes',          sa.Text),
        sa.Column('requested_at',   sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('responded_at',   sa.DateTime(timezone=True)),
        schema='plenum_cafm',
    )

    op.create_table(
        'wo_journey_logs',
        sa.Column('jlog_id',        sa.String(50),  primary_key=True),
        sa.Column('work_order_id',  sa.String(50),  nullable=False),
        sa.Column('events',         JSONB,          server_default='[]'),
        sa.Column('current_step',   sa.String(100)),
        sa.Column('deviations',     JSONB,          server_default='[]'),
        sa.Column('completed',      sa.String(10),  server_default='false'),
        sa.Column('created_at',     sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at',     sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema='plenum_cafm',
    )

    op.create_table(
        'ppm_schedules',
        sa.Column('schedule_id',                sa.String(50),  primary_key=True),
        sa.Column('asset_id',                   sa.String(255), nullable=False),
        sa.Column('asset_name',                 sa.String(255)),
        sa.Column('location',                   sa.String(255)),
        sa.Column('task_description',           sa.String(500)),
        sa.Column('task_type',                  sa.String(100)),
        sa.Column('frequency',                  sa.String(20)),
        sa.Column('priority',                   sa.String(20),  server_default='medium'),
        sa.Column('estimated_duration_minutes', sa.Integer,     server_default='60'),
        sa.Column('required_skills',            JSONB,          server_default='[]'),
        sa.Column('required_tools',             JSONB,          server_default='[]'),
        sa.Column('required_parts',             JSONB,          server_default='[]'),
        sa.Column('safety_requirements',        JSONB,          server_default='[]'),
        sa.Column('active',                     sa.Boolean,     server_default='true'),
        sa.Column('last_executed',              sa.DateTime(timezone=True)),
        sa.Column('created_at',                 sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema='plenum_cafm',
    )

    # Indexes for common query patterns
    op.create_index('idx_wo_status',     'work_orders', ['status'],     schema='plenum_cafm')
    op.create_index('idx_wo_source',     'work_orders', ['source'],     schema='plenum_cafm')
    op.create_index('idx_wo_created_at', 'work_orders', ['created_at'], schema='plenum_cafm')
    op.create_index('idx_wo_requester',  'work_orders', ['requester_email'], schema='plenum_cafm')


def downgrade() -> None:
    op.drop_table('ppm_schedules',          schema='plenum_cafm')
    op.drop_table('wo_journey_logs',        schema='plenum_cafm')
    op.drop_table('wo_approval_requests',   schema='plenum_cafm')
    op.drop_table('work_orders',            schema='plenum_cafm')
