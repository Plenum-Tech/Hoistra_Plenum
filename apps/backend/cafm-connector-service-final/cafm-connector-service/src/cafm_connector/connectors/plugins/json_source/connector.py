"""JSON connector — local file, blob URL, or remote HTTP endpoint."""

from __future__ import annotations

import os
from typing import Any, ClassVar

from cafm_connector.connectors.base import Connector, ConnectorConfig, SchemaInspector
from cafm_connector.core.exceptions import ConnectionError, QueryError
from cafm_connector.core.types import ConnectorState, DataSourceType, RawRow
from cafm_connector.schema.models import DataSourceSchema, TableSchema
from cafm_connector.storage.download import download_to_temp, is_blob_url


class JSONSchemaInspector(SchemaInspector):
    def __init__(self, data: list[dict], source_name: str) -> None:
        self._data = data
        self._source_name = source_name

    async def list_tables(self) -> list[str]:
        return ["root"]

    async def discover_table(self, table_name: str) -> TableSchema:
        from cafm_connector.schema.models import ColumnSchema
        from cafm_connector.core.types import UnifiedDataType
        keys: dict[str, str] = {}
        for row in self._data[:5]:
            for k, v in row.items():
                keys.setdefault(k, type(v).__name__)
        columns = [
            ColumnSchema(name=k, unified_type=UnifiedDataType.UNKNOWN, native_type=t, nullable=True)
            for k, t in keys.items()
        ]
        return TableSchema(name="root", columns=columns)

    async def discover_schema(self) -> DataSourceSchema:
        return DataSourceSchema(
            source_name=self._source_name,
            source_type=DataSourceType.JSON,
            tables=[await self.discover_table("root")],
        )


class JSONConnector(Connector):
    """
    connection_params:
        file_path  — local JSON file path OR Azure Blob URL (https://...)
        url        — remote JSON API endpoint (mutually exclusive with file_path)
        root_key   — optional dotted path to array within JSON (e.g. "data.items")
    """

    source_type: ClassVar[DataSourceType] = DataSourceType.JSON

    def __init__(self, config: ConnectorConfig) -> None:
        super().__init__(config)
        self._data: list[dict] = []
        self._temp_path: str | None = None  # temp file path if blob was downloaded

    async def connect(self) -> None:
        try:
            self._state = ConnectorState.CONNECTING
            p = self._config.connection_params

            if "file_path" in p:
                file_path = p["file_path"]
                if is_blob_url(file_path):
                    # Download blob to temp file, then read it
                    self._temp_path = download_to_temp(file_path)
                    local_path = self._temp_path
                else:
                    local_path = file_path

                import json
                with open(local_path, encoding="utf-8") as f:
                    raw = json.load(f)

            else:
                # Remote JSON API
                import httpx
                r = httpx.get(p["url"], timeout=30)
                r.raise_for_status()
                raw = r.json()

            # Drill into nested key if specified
            root_key = p.get("root_key", "")
            if root_key:
                for part in root_key.split("."):
                    raw = raw[part]

            self._data = raw if isinstance(raw, list) else [raw]
            self._state = ConnectorState.CONNECTED

        except ConnectionError:
            raise
        except Exception as exc:
            self._state = ConnectorState.ERROR
            raise ConnectionError(f"JSON connect failed: {exc}") from exc

    async def disconnect(self) -> None:
        self._data = []
        if self._temp_path and os.path.exists(self._temp_path):
            os.unlink(self._temp_path)
            self._temp_path = None
        self._state = ConnectorState.DISCONNECTED

    async def health_check(self) -> bool:
        return self._state == ConnectorState.CONNECTED

    def get_schema_inspector(self) -> SchemaInspector:
        return JSONSchemaInspector(self._data, self.name)

    async def fetch_rows(
        self,
        table: str,
        columns: list[str] | None = None,
        filters: dict[str, Any] | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[RawRow]:
        data = self._data[offset:]
        if filters:
            data = [r for r in data if all(r.get(k) == v for k, v in filters.items())]
        if limit is not None:
            data = data[:limit]
        if columns:
            data = [{k: r.get(k) for k in columns} for r in data]
        return data

    async def count_rows(self, table: str, filters: dict[str, Any] | None = None) -> int:
        if not filters:
            return len(self._data)
        return len([r for r in self._data if all(r.get(k) == v for k, v in filters.items())])

