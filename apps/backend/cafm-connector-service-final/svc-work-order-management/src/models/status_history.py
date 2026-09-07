from sqlalchemy import Column, String, DateTime, Text, func, Index
from .base import Base


class StatusHistory(Base):
    __tablename__ = "wo_status_history"
    __table_args__ = (
        Index("ix_sh_work_order_id", "work_order_id"),
        Index("ix_sh_changed_at",    "changed_at"),
        {"schema": "plenum_cafm"},
    )

    history_id    = Column(String(50),  primary_key=True)
    work_order_id = Column(String(50),  nullable=False)
    from_status   = Column(String(50))
    to_status     = Column(String(50),  nullable=False)
    changed_by    = Column(String(255), default="system")
    notes         = Column(Text)
    changed_at    = Column(DateTime(timezone=True), server_default=func.now())
