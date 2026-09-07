from sqlalchemy import Column, String, DateTime, Text, Integer, func
from .base import Base


class ApprovalRequest(Base):
    __tablename__ = "wo_approval_requests"
    __table_args__ = {"schema": "plenum_cafm"}

    request_id      = Column(String(50),  primary_key=True)
    work_order_id   = Column(String(50),  nullable=False)
    approval_type   = Column(String(50))   # preparation | final | simple
    approver        = Column(String(255))  # approver email (notifications)
    status          = Column(String(20),  default="pending")  # pending | approved | rejected
    notes           = Column(Text)
    requested_at    = Column(DateTime(timezone=True), server_default=func.now())
    responded_at    = Column(DateTime(timezone=True))
    level           = Column(Integer)
    step_order      = Column(Integer)
    risk_score      = Column(Integer)
    match_score     = Column(Integer)
    suggestion_source = Column(String(20))
    unblocked_at    = Column(DateTime(timezone=True))
