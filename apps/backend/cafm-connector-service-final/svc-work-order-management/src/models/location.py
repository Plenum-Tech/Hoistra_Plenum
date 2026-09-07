import uuid
from sqlalchemy import Column, String, DateTime, func
from sqlalchemy.dialects.postgresql import UUID
from .base import Base


class Location(Base):
    __tablename__ = "locations"
    __table_args__ = {"schema": "plenum_cafm"}

    # Map Python attr 'location_id' → actual DB column 'id'
    location_id = Column("id",         UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name        = Column(String(255),  nullable=False)
    type        = Column(String(100))
    created_at  = Column(DateTime(timezone=True), server_default=func.now())

    # Synthetic properties so existing response schema serialises cleanly
    @property
    def active(self) -> bool:
        return True

    @property
    def building(self):
        return None

    @property
    def floor(self):
        return None

    @property
    def zone(self):
        return None
