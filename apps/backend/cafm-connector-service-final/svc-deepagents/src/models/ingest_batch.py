"""Persisted bulk ingestion batch jobs (Phase D3)."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class IngestBatch(Base):
    __tablename__ = "deepagents_ingest_batches"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(128), index=True)
    organization_id: Mapped[str] = mapped_column(String(36))
    cmms_name: Mapped[str] = mapped_column(String(64), default="Custom")
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    total_files: Mapped[int] = mapped_column(Integer, default=0)
    completed_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    items: Mapped[list] = mapped_column(JSONB, default=list)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=_utcnow
    )

    def to_dict(self) -> dict:
        return {
            "batch_id": self.id,
            "session_id": self.session_id,
            "organization_id": self.organization_id,
            "cmms_name": self.cmms_name,
            "status": self.status,
            "total_files": self.total_files,
            "completed_count": self.completed_count,
            "failed_count": self.failed_count,
            "items": self.items or [],
            "error_message": self.error_message,
            "progress_pct": round(
                100.0 * (self.completed_count + self.failed_count) / self.total_files, 1
            )
            if self.total_files
            else 0.0,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
