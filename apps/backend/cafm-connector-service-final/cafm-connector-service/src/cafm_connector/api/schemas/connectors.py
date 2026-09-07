"""API Pydantic schemas for all connector + import endpoints."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


# ── Common ────────────────────────────────────────────────────────────

class APIResponse(BaseModel):
    success: bool = True
    message: str = "OK"
    data: Any = None


class ErrorResponse(BaseModel):
    success: bool = False
    error: str
    detail: str | None = None


# ── POST /connectors/test ─────────────────────────────────────────────

class ConnectorTestRequest(BaseModel):
    source_type: str
    connection_params: dict[str, Any]
    credentials: dict[str, Any] = {}


class ConnectorTestResponse(BaseModel):
    success: bool
    latency_ms: float
    error: str | None = None


# ── POST /connectors ──────────────────────────────────────────────────

class ConnectorCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    source_type: str
    connection_params: dict[str, Any]
    credentials: dict[str, Any] = {}
    options: dict[str, Any] = {}
    description: str | None = None


class ConnectorResponse(BaseModel):
    id: str
    name: str
    source_type: str
    description: str | None
    is_active: bool
    created_at: str

    class Config:
        from_attributes = True


# ── GET /connectors ───────────────────────────────────────────────────

class ConnectorListResponse(BaseModel):
    connectors: list[ConnectorResponse]
    total: int


# ── POST /imports/preview ─────────────────────────────────────────────

class ImportPreviewRequest(BaseModel):
    connector_id: str | None = None
    table_name: str | None = None
    field_map: list[dict[str, str]] | None = None


class ColumnInfo(BaseModel):
    name: str
    sample_type: str


class ImportPreviewResponse(BaseModel):
    connector_id: str | None
    table: str
    available_tables: list[str]
    rows: list[dict[str, Any]]
    columns: list[ColumnInfo]
    row_count: int


# ── POST /imports/field-map ───────────────────────────────────────────

class FieldMappingEntry(BaseModel):
    source_field: str
    target_field: str
    transform_fn: str | None = None


class FieldMapRequest(BaseModel):
    connector_id: str
    mappings: list[FieldMappingEntry]


class FieldMapResponse(BaseModel):
    connector_id: str
    mappings_saved: int


# ── POST /imports/run ─────────────────────────────────────────────────

class ImportRunRequest(BaseModel):
    connector_id: str
    table_name: str | None = None
    conflict_mode: str = "skip"      # skip | overwrite | flag
    schedule: str = "one_off"        # one_off | cron
    cron_expr: str | None = None


class ImportRunResponse(BaseModel):
    job_id: str
    status: str
    queued_at: str


# ── GET /imports/{jobId}/status ───────────────────────────────────────

class JobStatusResponse(BaseModel):
    job_id: str
    connector_id: str
    status: str
    total_rows: int
    imported_rows: int
    skipped_rows: int
    error_count: int
    progress: float          # 0.0 – 100.0
    started_at: str | None
    finished_at: str | None
    duration_seconds: float | None
    is_rolled_back: bool


# ── GET /imports/{jobId}/log ──────────────────────────────────────────

class ErrorLogEntry(BaseModel):
    row_num: int
    error_msg: str
    raw_data: dict[str, Any]
    created_at: str


class JobLogResponse(BaseModel):
    job_id: str
    errors: list[ErrorLogEntry]
    total: int
