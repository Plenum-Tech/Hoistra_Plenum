"""XML connector — lxml streaming parser."""

from __future__ import annotations

import os
from typing import Any, ClassVar

from cafm_connector.connectors.base import Connector, ConnectorConfig, SchemaInspector
from cafm_connector.core.exceptions import ConnectionError, QueryError
from cafm_connector.core.types import ConnectorState, DataSourceType, RawRow
from cafm_connector.schema.models import DataSourceSchema, TableSchema
from cafm_connector.storage.download import download_to_temp, is_blob_url


class XMLSchemaInspector(SchemaInspector):
    def __init__(self, rows: list[dict], source_name: str, tag: str) -> None:
        self._rows = rows
        self._source_name = source_name
        self._tag = tag

    async def list_tables(self) -> list[str]:
        return [self._tag]

    async def discover_table(self, table_name: str) -> TableSchema:
        from cafm_connector.schema.models import ColumnSchema
        from cafm_connector.core.types import UnifiedDataType
        keys: set[str] = set()
        for row in self._rows[:5]:
            keys.update(row.keys())
        columns = [
            ColumnSchema(name=k, unified_type=UnifiedDataType.UNKNOWN, native_type="str", nullable=True)
            for k in sorted(keys)
        ]
        return TableSchema(name=table_name, columns=columns)

    async def discover_schema(self) -> DataSourceSchema:
        return DataSourceSchema(
            source_name=self._source_name,
            source_type=DataSourceType.XML,
            tables=[await self.discover_table(self._tag)],
        )


class XMLConnector(Connector):
    """
    connection_params:
        file_path    — local XML file path OR Azure Blob URL (https://...)
        record_tag   — XML element tag representing one row (e.g. "item", "record")
        namespaces   — optional dict of XML namespace prefixes
    """

    source_type: ClassVar[DataSourceType] = DataSourceType.XML

    def __init__(self, config: ConnectorConfig) -> None:
        super().__init__(config)
        self._rows: list[dict] = []
        self._tag: str = config.connection_params.get("record_tag", "record")
        self._temp_path: str | None = None

    def _parse_element(self, el) -> dict:
        row: dict[str, Any] = dict(el.attrib)
        for child in el:
            tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            row[tag] = child.text or ""
        return row

    async def connect(self) -> None:
        try:
            self._state = ConnectorState.CONNECTING
            from lxml import etree
            p = self._config.connection_params
            ns = p.get("namespaces", {})

            file_path = p["file_path"]
            if is_blob_url(file_path):
                self._temp_path = download_to_temp(file_path)
                local_path = self._temp_path
            else:
                local_path = file_path

            tree = etree.parse(local_path)
            self._rows = [
                self._parse_element(el)
                for el in tree.findall(f".//{self._tag}", namespaces=ns)
            ]
            self._state = ConnectorState.CONNECTED
        except ConnectionError:
            raise
        except Exception as exc:
            self._state = ConnectorState.ERROR
            raise ConnectionError(f"XML connect failed: {exc}") from exc

    async def disconnect(self) -> None:
        self._rows = []
        if self._temp_path and os.path.exists(self._temp_path):
            os.unlink(self._temp_path)
            self._temp_path = None
        self._state = ConnectorState.DISCONNECTED

    async def health_check(self) -> bool:
        return self._state == ConnectorState.CONNECTED

    def get_schema_inspector(self) -> SchemaInspector:
        return XMLSchemaInspector(self._rows, self.name, self._tag)

    async def fetch_rows(
        self,
        table: str,
        columns: list[str] | None = None,
        filters: dict[str, Any] | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[RawRow]:
        data = self._rows[offset:]
        if filters:
            data = [r for r in data if all(r.get(k) == v for k, v in filters.items())]
        if limit is not None:
            data = data[:limit]
        if columns:
            data = [{k: r.get(k) for k in columns} for r in data]
        return data

    async def count_rows(self, table: str, filters: dict[str, Any] | None = None) -> int:
        if not filters:
            return len(self._rows)
        return len([r for r in self._rows if all(r.get(k) == v for k, v in filters.items())])

