import uuid
from sqlalchemy import Column, String, DateTime, Integer, func, Date, Text
from sqlalchemy.dialects.postgresql import UUID
from .base import Base


class PPMSchedule(Base):
    """Maps to plenum_cafm.maintenance_plans — the shared PPM table."""
    __tablename__ = "maintenance_plans"
    __table_args__ = {"schema": "plenum_cafm"}

    # Map Python attr 'schedule_id' → actual DB column 'id'
    schedule_id      = Column("id",              UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    asset_id         = Column(UUID(as_uuid=True))
    description      = Column(Text)
    maintenance_type = Column(String(100))
    frequency_type   = Column(String(50))    # time | meter
    frequency_value  = Column(Integer)
    next_due_date    = Column(Date)
    status           = Column(String(50),    server_default="active")
    created_at       = Column(DateTime,      server_default=func.now())

    # Synthetic compat — old code expected 'active' bool and 'frequency' string
    @property
    def active(self) -> bool:
        return (self.status or "active") == "active"

    @property
    def frequency(self) -> str | None:
        """Map frequency_value + frequency_type to a coarse label (for is_schedule_due)."""
        if self.frequency_type != "time" or not self.frequency_value:
            return None
        days = self.frequency_value
        if days <= 1:
            return "daily"
        if days <= 7:
            return "weekly"
        if days <= 31:
            return "monthly"
        if days <= 92:
            return "quarterly"
        return "annually"

    @property
    def last_executed(self):
        return None  # maintenance_plans tracks next_due_date, not last_executed
