"""Dynamic approval engine — rules, thresholds, suggestions, multi-step requests.

Revision ID: 005
Revises: 004
Create Date: 2026-05-25
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, ARRAY

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "wo_approval_rules",
        sa.Column("rule_id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("dimension", sa.String(20), nullable=False),
        sa.Column("match_value", sa.String(50)),
        sa.Column("match_operator", sa.String(10)),
        sa.Column("match_threshold", sa.Numeric()),
        sa.Column("match_threshold_upper", sa.Numeric()),
        sa.Column("weight", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema="plenum_cafm",
    )

    op.create_table(
        "wo_approval_thresholds",
        sa.Column("level", sa.Integer(), primary_key=True),
        sa.Column("min_score", sa.Integer(), nullable=False),
        sa.Column("max_score", sa.Integer()),
        sa.Column("required_roles", ARRAY(sa.Text())),
        schema="plenum_cafm",
    )

    op.create_table(
        "wo_approval_suggestions",
        sa.Column("suggestion_id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("work_order_id", sa.String(50), nullable=False),
        sa.Column("fingerprint", sa.String(64)),
        sa.Column("source", sa.String(20)),
        sa.Column("confidence", sa.String(10)),
        sa.Column("match_score", sa.Integer()),
        sa.Column("risk_score", sa.Integer()),
        sa.Column("suggested_chain", JSONB),
        sa.Column("accepted", sa.Boolean()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema="plenum_cafm",
    )
    op.create_index(
        "idx_suggestions_fingerprint",
        "wo_approval_suggestions",
        ["fingerprint"],
        schema="plenum_cafm",
    )
    op.create_index(
        "idx_suggestions_wo",
        "wo_approval_suggestions",
        ["work_order_id"],
        schema="plenum_cafm",
    )

    for col, col_type in [
        ("level", sa.Integer()),
        ("step_order", sa.Integer()),
        ("risk_score", sa.Integer()),
        ("match_score", sa.Integer()),
        ("suggestion_source", sa.String(20)),
        ("unblocked_at", sa.DateTime(timezone=True)),
    ]:
        op.add_column("wo_approval_requests", sa.Column(col, col_type), schema="plenum_cafm")

    op.add_column(
        "work_orders",
        sa.Column("estimated_cost", sa.Numeric(14, 2)),
        schema="plenum_cafm",
    )
    op.add_column(
        "work_orders",
        sa.Column("asset_category", sa.String(100)),
        schema="plenum_cafm",
    )

    # Default scoring rules
    op.execute("""
        INSERT INTO plenum_cafm.wo_approval_rules
          (dimension, match_value, match_operator, weight) VALUES
          ('priority', 'urgent', 'eq', 40),
          ('priority', 'high',   'eq', 25),
          ('priority', 'medium', 'eq', 15),
          ('priority', 'low',    'eq', 10),
          ('work_type', 'safety',     'eq', 25),
          ('work_type', 'electrical', 'eq', 20),
          ('work_type', 'hvac',       'eq', 15),
          ('work_type', 'plumbing',   'eq', 10),
          ('work_type', 'general',    'eq', 5),
          ('work_type', 'repair',     'eq', 5),
          ('building', 'mechanical room', 'eq', 15),
          ('building', 'generator room',  'eq', 15),
          ('building', 'lobby',           'eq', 10)
    """)
    op.execute("""
        INSERT INTO plenum_cafm.wo_approval_rules
          (dimension, match_operator, match_threshold, match_threshold_upper, weight) VALUES
          ('cost', 'lte',     5000,    NULL,  5),
          ('cost', 'between', 5000,    25000, 15),
          ('cost', 'between', 25000,   100000, 30),
          ('cost', 'gte',     100000,  NULL,  45)
    """)
    op.execute("""
        INSERT INTO plenum_cafm.wo_approval_thresholds (level, min_score, max_score, required_roles) VALUES
          (1, 0,  39,  ARRAY['Maintenance Supervisor']),
          (2, 40, 69,  ARRAY['Maintenance Supervisor', 'Operations Manager']),
          (3, 70, NULL, ARRAY['Maintenance Supervisor', 'Operations Manager', 'Facilities Director'])
    """)


def downgrade() -> None:
    op.drop_column("work_orders", "asset_category", schema="plenum_cafm")
    op.drop_column("work_orders", "estimated_cost", schema="plenum_cafm")
    for col in (
        "unblocked_at",
        "suggestion_source",
        "match_score",
        "risk_score",
        "step_order",
        "level",
    ):
        op.drop_column("wo_approval_requests", col, schema="plenum_cafm")
    op.drop_index("idx_suggestions_wo", table_name="wo_approval_suggestions", schema="plenum_cafm")
    op.drop_index("idx_suggestions_fingerprint", table_name="wo_approval_suggestions", schema="plenum_cafm")
    op.drop_table("wo_approval_suggestions", schema="plenum_cafm")
    op.drop_table("wo_approval_thresholds", schema="plenum_cafm")
    op.drop_table("wo_approval_rules", schema="plenum_cafm")
