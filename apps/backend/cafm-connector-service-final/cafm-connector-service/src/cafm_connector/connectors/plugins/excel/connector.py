"""Excel connector — openpyxl/pandas, multi-sheet aware."""

from __future__ import annotations

import os
from typing import Any, ClassVar

from cafm_connector.connectors.base import Connector, ConnectorConfig, SchemaInspector
from cafm_connector.core.exceptions import ConnectionError, QueryError
from cafm_connector.core.types import ConnectorState, DataSourceType, RawRow
from cafm_connector.schema.models import DataSourceSchema, TableSchema
from cafm_connector.storage.download import download_to_temp, is_blob_url


class ExcelSchemaInspector(SchemaInspector):
    def __init__(self, file_path: str, source_name: str) -> None:
        self._path = file_path
        self._source_name = source_name

    async def list_tables(self) -> list[str]:
        import pandas as pd
        return pd.ExcelFile(self._path).sheet_names

    async def discover_table(self, table_name: str) -> TableSchema:
        import pandas as pd
        from cafm_connector.schema.models import ColumnSchema
        from cafm_connector.core.types import UnifiedDataType
        df = pd.read_excel(self._path, sheet_name=table_name, nrows=5)
        columns = [
            ColumnSchema(
                name=str(c),
                unified_type=UnifiedDataType.UNKNOWN,
                native_type=str(df[c].dtype),
                nullable=True,
            )
            for c in df.columns
        ]
        return TableSchema(name=table_name, columns=columns)

    async def discover_schema(self) -> DataSourceSchema:
        tables = [await self.discover_table(t) for t in await self.list_tables()]
        return DataSourceSchema(
            source_name=self._source_name,
            source_type=DataSourceType.EXCEL,
            tables=tables,
        )


class ExcelConnector(Connector):
    """
    connection_params:
        file_path   — local path to .xlsx/.xls OR Azure Blob URL (https://...)
        sheet_name  — optional, defaults to first sheet
    """

    source_type: ClassVar[DataSourceType] = DataSourceType.EXCEL

    def __init__(self, config: ConnectorConfig) -> None:
        super().__init__(config)
        self._raw_file_path: str       = config.connection_params["file_path"]
        self._sheet_name:    str | None = config.connection_params.get("sheet_name")
        self._local_path:    str       = ""
        self._temp_downloaded: bool    = False

    async def connect(self) -> None:
        try:
            if is_blob_url(self._raw_file_path):
                self._local_path = download_to_temp(self._raw_file_path)
                self._temp_downloaded = True
            else:
                self._local_path = self._raw_file_path
                if not os.path.exists(self._local_path):
                    self._state = ConnectorState.ERROR
                    raise ConnectionError(f"Excel file not found: {self._local_path}")
            self._state = ConnectorState.CONNECTED
        except ConnectionError:
            raise
        except Exception as exc:
            self._state = ConnectorState.ERROR
            raise ConnectionError(f"Excel connect failed: {exc}") from exc

    async def disconnect(self) -> None:
        if self._temp_downloaded and self._local_path and os.path.exists(self._local_path):
            os.unlink(self._local_path)
            self._temp_downloaded = False
        self._local_path = ""
        self._state = ConnectorState.DISCONNECTED

    async def health_check(self) -> bool:
        return os.path.exists(self._local_path) if self._local_path else False

    def get_schema_inspector(self) -> SchemaInspector:
        return ExcelSchemaInspector(self._local_path, self.name)

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
            sheet = table or self._sheet_name
            df = pd.read_excel(
                self._local_path,
                sheet_name=sheet,
                usecols=columns,
                skiprows=range(1, offset + 1) if offset else None,
                nrows=limit,
            )
            if filters:
                for col, val in filters.items():
                    df = df[df[col] == val]
            return df.to_dict(orient="records")
        except Exception as exc:
            raise QueryError(f"Excel fetch_rows failed: {exc}") from exc

    async def count_rows(self, table: str, filters: dict[str, Any] | None = None) -> int:
        try:
            import pandas as pd
            df = pd.read_excel(self._local_path, sheet_name=table or self._sheet_name)
            if filters:
                for col, val in filters.items():
                    df = df[df[col] == val]
            return len(df)
        except Exception as exc:
            raise QueryError(f"count_rows failed: {exc}") from exc

