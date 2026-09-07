from sqlalchemy import Boolean, Column, Integer, String, DateTime, func
from .base import Base


class ApproverRouting(Base):
    __tablename__ = "wo_approver_routing"
    __table_args__ = {"schema": "plenum_cafm"}

    id             = Column(Integer, primary_key=True, autoincrement=True)
    asset_category = Column(String(100), nullable=False)  # 'HVAC', 'Electrical', '*' for catch-all
    user_id        = Column(Integer, nullable=False)       # FK → plenum_cafm.users.id
    approver_role  = Column(String(100))
    active         = Column(Boolean, default=True)
    created_at     = Column(DateTime(timezone=True), server_default=func.now())
