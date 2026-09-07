"""Shared enumerations and type aliases used across the connector service."""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class DataSourceType(StrEnum):
    """All 12 supported data source types per US-01."""
    POSTGRESQL = "postgresql"
    MYSQL      = "mysql"
    MSSQL      = "mssql"
    MONGODB    = "mongodb"
    CSV        = "csv"
    EXCEL      = "excel"
    JSON       = "json"
    XML        = "xml"
    PARQUET    = "parquet"
    REST       = "rest"
    SOAP       = "soap"
    ODATA      = "odata"


class ConnectorState(StrEnum):
    DISCONNECTED = "disconnected"
    CONNECTING   = "connecting"
    CONNECTED    = "connected"
    ERROR        = "error"


class ImportJobStatus(StrEnum):
    """Lifecycle states of an import job (stored in Redis + DB)."""
    QUEUED     = "queued"
    RUNNING    = "running"
    COMPLETED  = "completed"
    FAILED     = "failed"
    CANCELLED  = "cancelled"
    ROLLED_BACK = "rolled_back"


class ConflictMode(StrEnum):
    """How to handle duplicate rows during import."""
    SKIP      = "skip"
    OVERWRITE = "overwrite"
    FLAG      = "flag"


class ImportSchedule(StrEnum):
    """Import frequency options."""
    ONE_OFF = "one_off"
    CRON    = "cron"


class UnifiedDataType(StrEnum):
    """Source-agnostic column types — every connector maps to one of these."""
    STRING   = "string"
    TEXT     = "text"
    INTEGER  = "integer"
    FLOAT    = "float"
    DECIMAL  = "decimal"
    BOOLEAN  = "boolean"
    DATE     = "date"
    DATETIME = "datetime"
    TIME     = "time"
    BINARY   = "binary"
    JSON     = "json"
    ARRAY    = "array"
    UUID     = "uuid"
    UNKNOWN  = "unknown"


# A single raw row from any data source — just a plain dict
RawRow = dict[str, Any]
