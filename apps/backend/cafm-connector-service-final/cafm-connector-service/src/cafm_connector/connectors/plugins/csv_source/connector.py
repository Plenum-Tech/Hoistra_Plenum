"""CSV connector — streaming parse via pandas, no full load into memory."""

from __future__ import annotations

import os
from typing import Any, ClassVar

from cafm_connector.connectors.base import Connector, ConnectorConfig, SchemaInspector
from cafm_connector.core.exceptions import ConnectionError, QueryError
from cafm_connector.core.types import ConnectorState, DataSourceType, RawRow
from cafm_connector.schema.models import DataSourceSchema, TableSchema
from cafm_connector.storage.download import download_to_temp, is_blob_url


class CSVSchemaInspector(SchemaInspector):
    def __init__(self, file_path: str, source_name: str, delimiter: str = ",") -> None:
        self._path = file_path
        self._source_name = source_name
        self._delimiter = delimiter

    async def list_tables(self) -> list[str]:
        return [os.path.basename(self._path)]

    async def discover_table(self, table_name: str) -> TableSchema:
        import pandas as pd
        from cafm_connector.schema.models import ColumnSchema
        from cafm_connector.core.types import UnifiedDataType
        df = pd.read_csv(self._path, nrows=5, delimiter=self._delimiter)
        columns = [
            ColumnSchema(
                name=c,
                unified_type=UnifiedDataType.UNKNOWN,
                native_type=str(df[c].dtype),
                nullable=True,
            )
            for c in df.columns
        ]
        return TableSchema(name=table_name, columns=columns)

    async def discover_schema(self) -> DataSourceSchema:
        name = os.path.basename(self._path)
        return DataSourceSchema(
            source_name=self._source_name,
            source_type=DataSourceType.CSV,
            tables=[await self.discover_table(name)],
        )


class CSVConnector(Connector):
    """
    connection_params:
        file_path  — local absolute path OR Azure Blob URL (https://...)
        delimiter  — default ","
        encoding   — default "utf-8"
    """

    source_type: ClassVar[DataSourceType] = DataSourceType.CSV

    def __init__(self, config: ConnectorConfig) -> None:
        super().__init__(config)
        self._raw_file_path: str = config.connection_params["file_path"]
        self._delimiter: str = config.connection_params.get("delimiter", ",")
        self._encoding:  str = config.connection_params.get("encoding", "utf-8")
        # Resolved local path (may be a temp file if downloaded from blob)
        self._local_path: str = ""
        self._temp_downloaded: bool = False

    async def connect(self) -> None:
        try:
            if is_blob_url(self._raw_file_path):
                self._local_path = download_to_temp(self._raw_file_path)
                self._temp_downloaded = True
            else:
                self._local_path = self._raw_file_path
                if not os.path.exists(self._local_path):
                    self._state = ConnectorState.ERROR
                    raise ConnectionError(f"CSV file not found: {self._local_path}")
            self._state = ConnectorState.CONNECTED
        except ConnectionError:
            raise
        except Exception as exc:
            self._state = ConnectorState.ERROR
            raise ConnectionError(f"CSV connect failed: {exc}") from exc

    async def disconnect(self) -> None:
        if self._temp_downloaded and self._local_path and os.path.exists(self._local_path):
            os.unlink(self._local_path)
            self._temp_downloaded = False
        self._local_path = ""
        self._state = ConnectorState.DISCONNECTED

    async def health_check(self) -> bool:
        return os.path.exists(self._local_path) if self._local_path else False

    def get_schema_inspector(self) -> SchemaInspector:
        return CSVSchemaInspector(self._local_path, self.name, self._delimiter)

    async def fetch_rows(
        self,
        table: str,
        columns: list[str] | None = None,
        filters: dict[str, Any] | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[RawRow]:
        try:
            import pandas as pd
            df = pd.read_csv(
                self._local_path,
                delimiter=self._delimiter,
                encoding=self._encoding,
                usecols=columns,
                skiprows=range(1, offset + 1) if offset else None,
                nrows=limit,
            )
            if filters:
                for col, val in filters.items():
                    df = df[df[col] == val]
            return df.to_dict(orient="records")
        except Exception as exc:
            raise QueryError(f"CSV fetch_rows failed: {exc}") from exc

    async def count_rows(self, table: str, filters: dict[str, Any] | None = None) -> int:
        try:
            import pandas as pd
            df = pd.read_csv(self._local_path, delimiter=self._delimiter)
            if filters:
                for col, val in filters.items():
                    df = df[df[col] == val]
            return len(df)
        except Exception as exc:
            raise QueryError(f"count_rows failed: {exc}") from exc

