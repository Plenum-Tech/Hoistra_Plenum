"""
SQLAlchemy ORM models — persisted in PostgreSQL.

Tables:
  connectors        — saved connector configs (credentials encrypted)
  import_jobs       — one row per import run
  import_errors     — per-row errors from a job
  field_maps        — source→target column mapping per connector
  assets            — extended with QR + source tracking (US-01 spec)
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)

from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _uuid() -> str:
    return str(uuid4())


class Base(DeclarativeBase):
    pass


# ── Connectors ────────────────────────────────────────────────────────

class ConnectorModel(Base):
    __tablename__ = "connectors"
    __table_args__ = {"schema": "plenum_cafm"}

    id: Mapped[str]             = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str]           = mapped_column(String(255), unique=True, nullable=False)
    source_type: Mapped[str]    = mapped_column(String(50), nullable=False)
    # Non-sensitive params stored as JSON plaintext
    connection_params: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # Sensitive credentials — AES-256 encrypted blob (or Vault path)
    config_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    options: Mapped[dict]       = mapped_column(JSON, nullable=False, default=dict)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None]  = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime]    = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime]    = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
    is_active: Mapped[bool]     = mapped_column(Boolean, default=True)

    import_jobs: Mapped[list[ImportJobModel]] = relationship(
        "ImportJobModel", back_populates="connector", cascade="all, delete-orphan"
    )
    field_maps: Mapped[list[FieldMapModel]] = relationship(
        "FieldMapModel", back_populates="connector", cascade="all, delete-orphan"
    )


# ── Import jobs ───────────────────────────────────────────────────────

class ImportJobModel(Base):
    __tablename__ = "import_jobs"
    __table_args__ = {"schema": "plenum_cafm"}

    id: Mapped[str]             = mapped_column(String(36), primary_key=True, default=_uuid)
    connector_id: Mapped[str]   = mapped_column(
        String(36), ForeignKey("plenum_cafm.connectors.id"), nullable=False
    )
    status: Mapped[str]         = mapped_column(String(50), nullable=False, default="queued")
    table_name: Mapped[str | None]   = mapped_column(String(255), nullable=True)
    conflict_mode: Mapped[str]  = mapped_column(String(20), default="skip")
    schedule: Mapped[str]       = mapped_column(String(20), default="one_off")
    cron_expr: Mapped[str | None]    = mapped_column(String(100), nullable=True)

    total_rows:    Mapped[int]  = mapped_column(Integer, default=0)
    imported_rows: Mapped[int]  = mapped_column(Integer, default=0)
    skipped_rows:  Mapped[int]  = mapped_column(Integer, default=0)
    error_count:   Mapped[int]  = mapped_column(Integer, default=0)

    started_at:    Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at:   Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at:    Mapped[datetime]        = mapped_column(DateTime, server_default=func.now())
    created_by: Mapped[str | None]         = mapped_column(String(255), nullable=True)

    # Soft-delete on failure — no partial commits
    is_rolled_back: Mapped[bool] = mapped_column(Boolean, default=False)

    connector: Mapped[ConnectorModel]      = relationship("ConnectorModel", back_populates="import_jobs")
    errors: Mapped[list[ImportErrorModel]] = relationship(
        "ImportErrorModel", back_populates="job", cascade="all, delete-orphan"
    )


# ── Import errors (per row) ───────────────────────────────────────────

class ImportErrorModel(Base):
    __tablename__ = "import_errors"
    __table_args__ = {"schema": "plenum_cafm"}

    id: Mapped[str]           = mapped_column(String(36), primary_key=True, default=_uuid)
    job_id: Mapped[str]       = mapped_column(
        String(36), ForeignKey("plenum_cafm.import_jobs.id"), nullable=False
    )
    row_num: Mapped[int]      = mapped_column(Integer, nullable=False)
    raw_data: Mapped[dict]    = mapped_column(JSON, nullable=False, default=dict)
    error_msg: Mapped[str]    = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    job: Mapped[ImportJobModel] = relationship("ImportJobModel", back_populates="errors")


# ── Field maps ────────────────────────────────────────────────────────

class FieldMapModel(Base):
    __tablename__ = "field_maps"
    __table_args__ = {"schema": "plenum_cafm"}

    id: Mapped[str]              = mapped_column(String(36), primary_key=True, default=_uuid)
    connector_id: Mapped[str]    = mapped_column(
        String(36), ForeignKey("plenum_cafm.connectors.id"), nullable=False
    )
    source_field: Mapped[str]    = mapped_column(String(255), nullable=False)
    target_field: Mapped[str]    = mapped_column(String(255), nullable=False)
    # Optional transformation function name (e.g. "to_uppercase", "strip_whitespace")
    transform_fn: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime]     = mapped_column(DateTime, server_default=func.now())

    connector: Mapped[ConnectorModel] = relationship("ConnectorModel", back_populates="field_maps")


# ── Assets (extended per US-01) ───────────────────────────────────────

class AssetModel(Base):
    __tablename__ = "assets"
    __table_args__ = {"schema": "plenum_cafm"}

    id: Mapped[str]           = mapped_column(String(36), primary_key=True, default=_uuid)
    asset_id: Mapped[str]     = mapped_column(String(255), unique=True, nullable=False)
    name: Mapped[str]         = mapped_column(String(500), nullable=False)
    serial_no: Mapped[str | None]    = mapped_column(String(255), nullable=True)
    category: Mapped[str | None]     = mapped_column(String(255), nullable=True)
    facility_id: Mapped[str | None]  = mapped_column(String(255), nullable=True)

    # US-01: QR code generated post-import
    qr_code_svg: Mapped[str | None]  = mapped_column(Text, nullable=True)
    qr_code_url: Mapped[str | None]  = mapped_column(String(1000), nullable=True)

    # US-01: Traceability — which connector/job created this asset
    source_connector_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("plenum_cafm.connectors.id"), nullable=True
    )
    source_job_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("plenum_cafm.import_jobs.id"), nullable=True
    )

    # US-01: Duplicate detection hash — SHA-256 of (asset_id + serial_no + name)
    dedup_hash: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)

    # Soft-delete support
    is_deleted: Mapped[bool]  = mapped_column(Boolean, default=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    # Extra fields stored as JSON for flexibility
    extra: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


# ── Uploaded Files ────────────────────────────────────────────────────
#
# Tracks every file uploaded by the frontend for file-based connectors
# (CSV, Excel, JSON, XML, Parquet).  The blob_url is stored in the DB
# and also written into the connector's connection_params["file_path"]
# so the import worker can download it at job-run time.

class UploadedFileModel(Base):
    __tablename__ = "uploaded_files"
    __table_args__ = {"schema": "plenum_cafm"}

    id: Mapped[str]              = mapped_column(String(36), primary_key=True, default=_uuid)

    # Original filename as supplied by the browser
    original_filename: Mapped[str]  = mapped_column(String(500), nullable=False)

    # MIME / extension info
    content_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    file_extension: Mapped[str | None] = mapped_column(String(20), nullable=True)
    file_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Azure Blob Storage location
    blob_name: Mapped[str]       = mapped_column(String(1000), nullable=False)  # full blob path
    blob_url: Mapped[str]        = mapped_column(String(2000), nullable=False)  # public/SAS URL

    # Optional link back to the connector that was created from this upload
    connector_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("plenum_cafm.connectors.id"), nullable=True
    )

    uploaded_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime]    = mapped_column(DateTime, server_default=func.now())
