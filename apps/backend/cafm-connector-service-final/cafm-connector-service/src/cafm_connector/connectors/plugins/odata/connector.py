"""OData connector — supports OData v2/v4 via httpx + $filter/$top/$skip."""

from __future__ import annotations

from typing import Any, ClassVar

from cafm_connector.connectors.base import Connector, ConnectorConfig, SchemaInspector
from cafm_connector.core.exceptions import ConnectionError, QueryError
from cafm_connector.core.types import ConnectorState, DataSourceType, RawRow
from cafm_connector.schema.models import DataSourceSchema, TableSchema


class ODataSchemaInspector(SchemaInspector):
    def __init__(self, entity_sets: list[str], source_name: str) -> None:
        self._entity_sets = entity_sets
        self._source_name = source_name

    async def list_tables(self) -> list[str]:
        return self._entity_sets

    async def discover_table(self, table_name: str) -> TableSchema:
        from cafm_connector.schema.models import ColumnSchema
        from cafm_connector.core.types import UnifiedDataType
        # Columns are discovered at runtime from $metadata — return minimal schema
        columns = [
            ColumnSchema(
                name="value",
                unified_type=UnifiedDataType.JSON,
                native_type="odata_entity",
                nullable=True,
            )
        ]
        return TableSchema(name=table_name, columns=columns)

    async def discover_schema(self) -> DataSourceSchema:
        tables = [await self.discover_table(es) for es in self._entity_sets]
        return DataSourceSchema(
            source_name=self._source_name,
            source_type=DataSourceType.ODATA,
            tables=tables,
        )


class ODataConnector(Connector):
    """
    connection_params:
        base_url    — service root URL (e.g. "https://services.odata.org/V4/Northwind/")
        version     — "v2" or "v4" (default "v4")
        entity_set  — default entity set for fetch_rows
    credentials:
        auth_type   — "none" | "bearer" | "basic"
        token / username / password
    """

    source_type: ClassVar[DataSourceType] = DataSourceType.ODATA

    def __init__(self, config: ConnectorConfig) -> None:
        super().__init__(config)
        self._client = None
        self._entity_sets: list[str] = []
        self._version: str = config.connection_params.get("version", "v4")

    def _build_headers(self) -> dict[str, str]:
        c = self._config.credentials
        headers = {"Accept": "application/json"}
        auth_type = c.get("auth_type", "none")
        if auth_type == "bearer":
            headers["Authorization"] = f"Bearer {c.get('token', '')}"
        elif auth_type == "basic":
            import base64
            creds = base64.b64encode(
                f"{c.get('username', '')}:{c.get('password', '')}".encode()
            ).decode()
            headers["Authorization"] = f"Basic {creds}"
        return headers

    def _build_odata_filter(self, filters: dict[str, Any]) -> str:
        """Convert simple equality filters to OData $filter expression."""
        clauses = [f"{k} eq '{v}'" if isinstance(v, str) else f"{k} eq {v}"
                   for k, v in filters.items()]
        return " and ".join(clauses)

    async def connect(self) -> None:
        try:
            import httpx
            self._state = ConnectorState.CONNECTING
            p = self._config.connection_params
            base_url = p["base_url"].rstrip("/")
            self._client = httpx.AsyncClient(
                headers=self._build_headers(),
                timeout=p.get("timeout", 30),
            )
            # Discover entity sets from $metadata
            meta_url = f"{base_url}/$metadata"
            r = await self._client.get(meta_url, headers={"Accept": "*/*"})
            r.raise_for_status()
            # Simple XML parse for EntitySet names
            import re
            self._entity_sets = re.findall(r'Name="(\w+)"[^/]*EntityType=', r.text)
            if not self._entity_sets:
                # Fallback: try $metadata via JSON (v4)
                r2 = await self._client.get(f"{base_url}/", params={"$format": "json"})
                data = r2.json()
                self._entity_sets = [
                    item["name"] for item in data.get("value", [])
                    if item.get("kind") == "EntitySet"
                ]
            self._state = ConnectorState.CONNECTED
        except Exception as exc:
            self._state = ConnectorState.ERROR
            raise ConnectionError(f"OData connect failed: {exc}") from exc

    async def disconnect(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
        self._state = ConnectorState.DISCONNECTED

    async def health_check(self) -> bool:
        return self._client is not None and self._state == ConnectorState.CONNECTED

    def get_schema_inspector(self) -> SchemaInspector:
        return ODataSchemaInspector(self._entity_sets, self.name)

    async def fetch_rows(
        self,
        table: str,
        columns: list[str] | None = None,
        filters: dict[str, Any] | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[RawRow]:
        if not self._client:
            raise ConnectionError("Not connected")
        p = self._config.connection_params
        base_url = p["base_url"].rstrip("/")
        entity_set = table or p.get("entity_set", "")
        url = f"{base_url}/{entity_set}"
        params: dict[str, Any] = {"$format": "json"}
        if columns:
            params["$select"] = ",".join(columns)
        if filters:
            params["$filter"] = self._build_odata_filter(filters)
        if limit is not None:
            params["$top"] = limit
        if offset:
            params["$skip"] = offset
        try:
            r = await self._client.get(url, params=params)
            r.raise_for_status()
            data = r.json()
            # OData v4 wraps results in "value"
            rows = data.get("value", data) if isinstance(data, dict) else data
            return rows if isinstance(rows, list) else [rows]
        except Exception as exc:
            raise QueryError(f"OData fetch_rows failed on {entity_set}: {exc}") from exc

    async def count_rows(self, table: str, filters: dict[str, Any] | None = None) -> int:
        if not self._client:
            raise ConnectionError("Not connected")
        p = self._config.connection_params
        base_url = p["base_url"].rstrip("/")
        entity_set = table or p.get("entity_set", "")
        url = f"{base_url}/{entity_set}/$count"
        params: dict[str, Any] = {}
        if filters:
            params["$filter"] = self._build_odata_filter(filters)
        try:
            r = await self._client.get(url, params=params)
            r.raise_for_status()
            return int(r.text.strip())
        except Exception:
            rows = await self.fetch_rows(table, filters=filters)
            return len(rows)
