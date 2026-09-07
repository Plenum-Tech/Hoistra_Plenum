import uuid
from sqlalchemy import Column, String, DateTime, func
from sqlalchemy.dialects.postgresql import UUID
from .base import Base


class Asset(Base):
    __tablename__ = "assets"
    __table_args__ = {"schema": "plenum_cafm"}

    # Map Python attr 'asset_id' → actual DB column 'id'
    asset_id      = Column("id",            UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    asset_name    = Column(String(255),     nullable=False)
    asset_code    = Column(String(150))
    manufacturer  = Column(String(150))
    model         = Column(String(150))
    serial_number = Column(String(150))
    status        = Column(String(50),      server_default="active")
    created_at    = Column(DateTime(timezone=True), server_default=func.now())

    # Synthetic properties so response schema serialises cleanly
    # active: derived from status; True unless explicitly set to a non-active value
    @property
    def active(self) -> bool:
        inactive = {"inactive", "retired", "deleted", "decommissioned"}
        return (self.status or "active").lower() not in inactive

    @property
    def asset_type(self):
        return None

    @property
    def location(self):
        return None
