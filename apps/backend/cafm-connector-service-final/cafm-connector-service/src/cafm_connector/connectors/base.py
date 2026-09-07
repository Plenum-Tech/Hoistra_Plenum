"""
Abstract base classes for all connector plugins.

Every connector plugin must:
  1. Subclass ``Connector``
  2. Set a class-level ``source_type: ClassVar[DataSourceType]``
  3. Implement all abstract methods
  4. Register itself via pyproject.toml entry_points (auto-discovery)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict

from cafm_connector.core.types import ConnectorState, DataSourceType, RawRow
from cafm_connector.schema.models import DataSourceSchema, TableSchema


# ── Configuration model ────────────────────────────────────────────────

class ConnectorConfig(BaseModel):
    """
    Configuration passed to every connector on construction.

    ``connection_params`` is source-specific (host/port/db for SQL,
    file path for CSV, URL for REST, etc.).
    ``credentials`` are kept separately so they can be encrypted by
    the secrets backend before being written to the DB.
    """
    model_config = ConfigDict(extra="forbid")

    name: str
    source_type: DataSourceType
    connection_params: dict[str, Any]       # Non-sensitive params
    credentials: dict[str, Any] = {}        # Sensitive — encrypted at rest
    options: dict[str, Any] = {}            # Tuning: pool_size, timeout, etc.


# ── Schema inspector ───────────────────────────────────────────────────

class SchemaInspector(ABC):
    """Introspects a data source and returns unified schema models."""

    @abstractmethod
    async def list_tables(self) -> list[str]:
        """List all tables / collections / sheets / endpoints."""

    @abstractmethod
    async def discover_table(self, table_name: str) -> TableSchema:
        """Discover schema for one table."""

    @abstractmethod
    async def discover_schema(self) -> DataSourceSchema:
        """Discover schema for the entire source (all tables)."""


# ── Abstract connector ─────────────────────────────────────────────────

class Connector(ABC):
    """
    Abstract base for all 12 data source connectors.

    Lifecycle:
        1. __init__(config)  — store config, do NOT connect yet
        2. connect()         — establish connection
        3. fetch_rows() ...  — use the connector
        4. disconnect()      — release resources

    Use ``async with connector.session():`` for automatic lifecycle.
    """

    source_type: ClassVar[DataSourceType]

    def __init__(self, config: ConnectorConfig) -> None:
        self._config = config
        self._state  = ConnectorState.DISCONNECTED

    # ── Properties ────────────────────────────────────────────────

    @property
    def name(self) -> str:
        return self._config.name

    @property
    def state(self) -> ConnectorState:
        return self._state

    @property
    def is_connected(self) -> bool:
        return self._state == ConnectorState.CONNECTED

    # ── Lifecycle ──────────────────────────────────────────────────

    @abstractmethod
    async def connect(self) -> None:
        """Open connection to the data source."""

    @abstractmethod
    async def disconnect(self) -> None:
        """Release all resources."""

    @abstractmethod
    async def health_check(self) -> bool:
        """Return True if the connection is alive."""

    @asynccontextmanager
    async def session(self) -> AsyncIterator[Connector]:
        """Context manager: auto connect/disconnect."""
        await self.connect()
        try:
            yield self
        finally:
            await self.disconnect()

    # ── Schema ─────────────────────────────────────────────────────

    @abstractmethod
    def get_schema_inspector(self) -> SchemaInspector:
        """Return a SchemaInspector bound to this connection."""

    # ── Read ───────────────────────────────────────────────────────

    @abstractmethod
    async def fetch_rows(
        self,
        table: str,
        columns: list[str] | None = None,
        filters: dict[str, Any] | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[RawRow]:
        """Fetch rows from a table / collection / sheet."""

    @abstractmethod
    async def count_rows(
        self,
        table: str,
        filters: dict[str, Any] | None = None,
    ) -> int:
        """Count rows matching filters."""

    async def stream_rows(
        self,
        table: str,
        columns: list[str] | None = None,
        filters: dict[str, Any] | None = None,
        batch_size: int = 1000,
    ) -> AsyncIterator[list[RawRow]]:
        """
        Stream rows in batches — avoids loading entire source into memory.
        Default implementation paginates fetch_rows; override for efficiency.
        """
        offset = 0
        while True:
            batch = await self.fetch_rows(
                table,
                columns=columns,
                filters=filters,
                limit=batch_size,
                offset=offset,
            )
            if not batch:
                break
            yield batch
            if len(batch) < batch_size:
                break
            offset += batch_size

    # ── Write (optional — not all sources are writable) ────────────

    async def insert_rows(self, table: str, rows: list[RawRow]) -> int:
        raise NotImplementedError(f"{self.source_type} connector is read-only")

    async def update_rows(
        self, table: str, filters: dict[str, Any], updates: dict[str, Any]
    ) -> int:
        raise NotImplementedError(f"{self.source_type} connector is read-only")

    async def delete_rows(self, table: str, filters: dict[str, Any]) -> int:
        raise NotImplementedError(f"{self.source_type} connector is read-only")

    # ── Raw query (optional) ───────────────────────────────────────

    async def execute_raw(
        self, query: str, params: dict[str, Any] | None = None
    ) -> list[RawRow]:
        raise NotImplementedError(f"{self.source_type} does not support raw queries")
