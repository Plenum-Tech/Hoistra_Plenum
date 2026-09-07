"""REST API connector — httpx-based, supports Bearer/API key/Basic auth."""

from __future__ import annotations

from typing import Any, ClassVar

from cafm_connector.connectors.base import Connector, ConnectorConfig, SchemaInspector
from cafm_connector.core.exceptions import ConnectionError, QueryError
from cafm_connector.core.types import ConnectorState, DataSourceType, RawRow
from cafm_connector.schema.models import DataSourceSchema, TableSchema


class RESTSchemaInspector(SchemaInspector):
    """
    REST sources don't have formal schemas — we infer from a sample response.
    """

    def __init__(self, sample: list[dict], source_name: str, endpoint: str) -> None:
        self._sample = sample
        self._source_name = source_name
        self._endpoint = endpoint

    async def list_tables(self) -> list[str]:
        return [self._endpoint]

    async def discover_table(self, table_name: str) -> TableSchema:
        from cafm_connector.schema.models import ColumnSchema
        from cafm_connector.core.types import UnifiedDataType
        keys: dict[str, str] = {}
        for row in self._sample[:5]:
            for k, v in row.items():
                keys.setdefault(k, type(v).__name__)
        columns = [
            ColumnSchema(name=k, unified_type=UnifiedDataType.UNKNOWN, native_type=t, nullable=True)
            for k, t in keys.items()
        ]
        return TableSchema(name=table_name, columns=columns)

    async def discover_schema(self) -> DataSourceSchema:
        return DataSourceSchema(
            source_name=self._source_name,
            source_type=DataSourceType.REST,
            tables=[await self.discover_table(self._endpoint)],
        )


class RESTConnector(Connector):
    """
    connection_params:
        base_url       — root URL, e.g. "https://api.example.com/v1"
        endpoint       — specific endpoint path, e.g. "/assets"
        method         — GET (default) or POST
        params         — query params dict (e.g. page_size, format)
        data_key       — dotted path to array in response (e.g. "data.items")
        pagination     — "none" | "page" | "cursor"  (default "none")
        page_param     — query param name for page number (default "page")
        page_size      — rows per page (default 100)
    credentials:
        auth_type      — "none" | "bearer" | "api_key" | "basic"
        token          — Bearer token
        api_key        — API key value
        api_key_header — header name for API key (default "X-API-Key")
        username / password — for Basic auth
    """

    source_type: ClassVar[DataSourceType] = DataSourceType.REST

    def __init__(self, config: ConnectorConfig) -> None:
        super().__init__(config)
        self._client = None
        self._sample: list[dict] = []

    def _build_headers(self) -> dict[str, str]:
        c = self._config.credentials
        auth_type = c.get("auth_type", "none")
        if auth_type == "bearer":
            return {"Authorization": f"Bearer {c.get('token', '')}"}
        if auth_type == "api_key":
            header = c.get("api_key_header", "X-API-Key")
            return {header: c.get("api_key", "")}
        if auth_type == "basic":
            import base64
            creds = base64.b64encode(
                f"{c.get('username', '')}:{c.get('password', '')}".encode()
            ).decode()
            return {"Authorization": f"Basic {creds}"}
        return {}

    def _extract_data(self, response_json: Any) -> list[dict]:
        data_key = self._config.connection_params.get("data_key", "")
        raw = response_json
        if data_key:
            for part in data_key.split("."):
                raw = raw[part]
        if isinstance(raw, list):
            return raw
        return [raw]

    async def connect(self) -> None:
        try:
            import httpx
            self._state = ConnectorState.CONNECTING
            p = self._config.connection_params
            url = p["base_url"].rstrip("/") + p.get("endpoint", "")
            self._client = httpx.AsyncClient(
                headers=self._build_headers(),
                timeout=p.get("timeout", 30),
            )
            # Warm-up / health ping — fetch 1 row
            r = await self._client.get(url, params={**p.get("params", {}), p.get("page_size_param", "limit"): 1})
            r.raise_for_status()
            self._sample = self._extract_data(r.json())
            self._state = ConnectorState.CONNECTED
        except Exception as exc:
            self._state = ConnectorState.ERROR
            raise ConnectionError(f"REST connect failed: {exc}") from exc

    async def disconnect(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
        self._state = ConnectorState.DISCONNECTED

    async def health_check(self) -> bool:
        return self._client is not None and self._state == ConnectorState.CONNECTED

    def get_schema_inspector(self) -> SchemaInspector:
        p = self._config.connection_params
        endpoint = p.get("endpoint", "/")
        return RESTSchemaInspector(self._sample, self.name, endpoint)

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
        url = p["base_url"].rstrip("/") + p.get("endpoint", "")
        params = {**p.get("params", {})}
        if limit is not None:
            params[p.get("page_size_param", "limit")] = limit
        if offset:
            params[p.get("offset_param", "offset")] = offset
        if filters:
            params.update(filters)
        try:
            r = await self._client.get(url, params=params)
            r.raise_for_status()
            rows = self._extract_data(r.json())
            if columns:
                rows = [{k: row.get(k) for k in columns} for row in rows]
            return rows
        except Exception as exc:
            raise QueryError(f"REST fetch_rows failed: {exc}") from exc

    async def count_rows(self, table: str, filters: dict[str, Any] | None = None) -> int:
        rows = await self.fetch_rows(table, filters=filters)
        return len(rows)
