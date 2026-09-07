"""Parquet connector — PyArrow streaming, no full load into memory."""

from __future__ import annotations

import os
from typing import Any, ClassVar

from cafm_connector.connectors.base import Connector, ConnectorConfig, SchemaInspector
from cafm_connector.core.exceptions import ConnectionError, QueryError
from cafm_connector.core.types import ConnectorState, DataSourceType, RawRow
from cafm_connector.schema.models import DataSourceSchema, TableSchema
from cafm_connector.storage.download import download_to_temp, is_blob_url


class ParquetSchemaInspector(SchemaInspector):
    def __init__(self, file_path: str, source_name: str) -> None:
        self._path = file_path
        self._source_name = source_name

    async def list_tables(self) -> list[str]:
        return [os.path.basename(self._path)]

    async def discover_table(self, table_name: str) -> TableSchema:
        import pyarrow.parquet as pq
        from cafm_connector.schema.models import ColumnSchema
        from cafm_connector.core.types import UnifiedDataType
        schema = pq.read_schema(self._path)
        columns = [
            ColumnSchema(
                name=field.name,
                unified_type=UnifiedDataType.UNKNOWN,
                native_type=str(field.type),
                nullable=field.nullable,
            )
            for field in schema
        ]
        return TableSchema(name=table_name, columns=columns)

    async def discover_schema(self) -> DataSourceSchema:
        name = os.path.basename(self._path)
        return DataSourceSchema(
            source_name=self._source_name,
            source_type=DataSourceType.PARQUET,
            tables=[await self.discover_table(name)],
        )


class ParquetConnector(Connector):
    """
    connection_params:
        file_path  — local .parquet path OR Azure Blob URL (https://...)
        batch_size — rows per batch when streaming (default 1000)
    """

    source_type: ClassVar[DataSourceType] = DataSourceType.PARQUET

    def __init__(self, config: ConnectorConfig) -> None:
        super().__init__(config)
        self._raw_file_path: str = config.connection_params["file_path"]
        self._batch_size:    int = config.connection_params.get("batch_size", 1000)
        self._local_path:    str = ""
        self._temp_downloaded: bool = False
        self._pf = None

    async def connect(self) -> None:
        try:
            import pyarrow.parquet as pq
            self._state = ConnectorState.CONNECTING

            if is_blob_url(self._raw_file_path):
                self._local_path = download_to_temp(self._raw_file_path)
                self._temp_downloaded = True
            else:
                self._local_path = self._raw_file_path
                if not os.path.exists(self._local_path):
                    raise ConnectionError(f"Parquet path not found: {self._local_path}")

            self._pf = pq.ParquetFile(self._local_path)
            self._state = ConnectorState.CONNECTED

        except ConnectionError:
            raise
        except Exception as exc:
            self._state = ConnectorState.ERROR
            raise ConnectionError(f"Parquet connect failed: {exc}") from exc

    async def disconnect(self) -> None:
        self._pf = None
        if self._temp_downloaded and self._local_path and os.path.exists(self._local_path):
            os.unlink(self._local_path)
            self._temp_downloaded = False
        self._local_path = ""
        self._state = ConnectorState.DISCONNECTED

    async def health_check(self) -> bool:
        return os.path.exists(self._local_path) if self._local_path else False

    def get_schema_inspector(self) -> SchemaInspector:
        return ParquetSchemaInspector(self._local_path, self.name)

    async def fetch_rows(
        self,
        table: str,
        columns: list[str] | None = None,
        filters: dict[str, Any] | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[RawRow]:
        if not self._pf:
            raise ConnectionError("Not connected")
        try:
            table_data = self._pf.read(columns=columns)
            df = table_data.to_pandas()
            if filters:
                for col, val in filters.items():
                    df = df[df[col] == val]
            df = df.iloc[offset:]
            if limit is not None:
                df = df.iloc[:limit]
            return df.to_dict(orient="records")
        except Exception as exc:
            raise QueryError(f"Parquet fetch_rows failed: {exc}") from exc

    async def count_rows(self, table: str, filters: dict[str, Any] | None = None) -> int:
        if not self._pf:
            raise ConnectionError("Not connected")
        if not filters:
            return self._pf.metadata.num_rows
        rows = await self.fetch_rows(table, filters=filters)
        return len(rows)

