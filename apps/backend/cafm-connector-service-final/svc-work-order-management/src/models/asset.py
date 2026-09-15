import uuid
from sqlalchemy import Column, Date, Integer, String, DateTime, func
from sqlalchemy.dialects.postgresql import JSONB
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
    building_id   = Column(UUID(as_uuid=True))
    # Real columns on plenum_cafm.assets that were never mapped here, so the Assets page had
    # to read them from the unauthenticated connector service or not at all. Additive: the
    # columns already exist, nothing to migrate.
    # Deliberately untyped keys: category_id and location_id are uuid in one database and
    # integer in another. Comparisons cast both sides to text so neither spelling breaks.
    category_id       = Column(String)
    location_id       = Column(String)
    criticality       = Column(String(50))
    # integer 0-100 with a check constraint on the table, not a float.
    health_score      = Column(Integer)
    installation_date = Column(Date)
    warranty_expiry   = Column(Date)
    # A score with no date is worse than no score: it reads as current when it may be an
    # import from two years ago. The three travel together for that reason.
    condition_score      = Column(Integer)
    condition_provenance = Column(JSONB)
    condition_updated_at = Column(DateTime(timezone=True))
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
