"""Unified schema models — source-agnostic representations of tables and columns."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from cafm_connector.core.types import DataSourceType, UnifiedDataType


class ColumnSchema(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    unified_type: UnifiedDataType
    native_type: str                    # e.g. "varchar(255)", "int4", "ISODate"
    nullable: bool = True
    primary_key: bool = False
    unique: bool = False
    default_value: str | None = None
    max_length: int | None = None
    description: str | None = None
    sample_values: list[Any] = Field(default_factory=list, max_length=5)


class IndexSchema(BaseModel):
    model_config = ConfigDict(frozen=True)
    name: str
    columns: list[str]
    unique: bool = False


class ForeignKeySchema(BaseModel):
    model_config = ConfigDict(frozen=True)
    name: str
    columns: list[str]
    referenced_table: str
    referenced_columns: list[str]


class TableSchema(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    schema_name: str | None = None
    columns: list[ColumnSchema]
    primary_key: list[str] = Field(default_factory=list)
    indexes: list[IndexSchema] = Field(default_factory=list)
    foreign_keys: list[ForeignKeySchema] = Field(default_factory=list)
    row_count: int | None = None
    description: str | None = None

    def get_column(self, name: str) -> ColumnSchema | None:
        return next((c for c in self.columns if c.name == name), None)

    @property
    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]


class DataSourceSchema(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_name: str
    source_type: DataSourceType
    tables: list[TableSchema]
    discovered_at: datetime = Field(default_factory=datetime.utcnow)
    version: str = "1.0"
    metadata: dict[str, Any] = Field(default_factory=dict)

    def get_table(self, name: str) -> TableSchema | None:
        return next((t for t in self.tables if t.name == name), None)

    @property
    def table_names(self) -> list[str]:
        return [t.name for t in self.tables]
