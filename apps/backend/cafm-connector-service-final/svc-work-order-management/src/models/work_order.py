import uuid as _uuid_mod
from sqlalchemy import Column, String, DateTime, Text, Boolean, Float, JSON, Integer, Numeric, func, Index
from .base import Base


class WorkOrder(Base):
    __tablename__ = "work_orders"
    __table_args__ = (
        Index("ix_wo_status",     "status"),
        Index("ix_wo_priority",   "priority"),
        Index("ix_wo_created_at", "created_at"),
        Index("ix_wo_asset",      "asset"),
        {"schema": "plenum_cafm"},
    )

    work_order_id       = Column(String(50),  primary_key=True)

    # Real plenum_cafm.work_orders columns (NOT NULL — must be supplied on insert)
    organization_id     = Column(Integer)                     # set from DEFAULT_ORGANIZATION_ID
    title               = Column(String(255))                 # mapped from issue_description

    source              = Column(String(50))
    source_reference    = Column(String(255))

    # Asset & location (string names, added as service-specific columns)
    asset               = Column(String(255))
    location            = Column(String(255))

    # Description
    issue_description   = Column(Text)
    task_description    = Column(Text)

    # Classification
    priority            = Column(String(20),  default="medium")
    request_type        = Column(String(50),  default="repair")
    asset_category      = Column(String(100))
    estimated_cost      = Column(Numeric(14, 2))
    status              = Column(String(50),  default="pending_approval")
    approval_type       = Column(String(50))

    # Requester
    requester_name      = Column(String(255))
    requester_email     = Column(String(255))
    requester_phone     = Column(String(50))

    # Preparation details
    vendor              = Column(String(255))
    manpower            = Column(JSON)
    scheduled_date      = Column(String(20))
    scheduled_time      = Column(String(20))
    estimated_duration  = Column(Float)
    inspection_required = Column(Boolean, default=False)
    special_requirements = Column(Text)

    # External references
    cmms_work_order_id  = Column(String(100))
    journey_log_id      = Column(String(100))

    # Audit timestamps
    created_at          = Column(DateTime(timezone=True), server_default=func.now())
    # created_by is UUID FK in plenum_cafm — not mapped here to avoid type mismatch
    approved_at         = Column(DateTime(timezone=True))
    prepared_at         = Column(DateTime(timezone=True))
    sent_to_cmms_at     = Column(DateTime(timezone=True))
