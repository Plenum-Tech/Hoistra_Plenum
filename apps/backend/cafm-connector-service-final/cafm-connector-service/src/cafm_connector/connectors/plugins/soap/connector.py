"""SOAP connector — zeep WSDL client."""

from __future__ import annotations

from typing import Any, ClassVar

from cafm_connector.connectors.base import Connector, ConnectorConfig, SchemaInspector
from cafm_connector.core.exceptions import ConnectionError, QueryError
from cafm_connector.core.types import ConnectorState, DataSourceType, RawRow
from cafm_connector.schema.models import DataSourceSchema, TableSchema


class SOAPSchemaInspector(SchemaInspector):
    """Lists WSDL operations as 'tables'."""

    def __init__(self, operations: list[str], source_name: str) -> None:
        self._operations = operations
        self._source_name = source_name

    async def list_tables(self) -> list[str]:
        return self._operations

    async def discover_table(self, table_name: str) -> TableSchema:
        from cafm_connector.schema.models import ColumnSchema
        from cafm_connector.core.types import UnifiedDataType
        # SOAP operation params are dynamic — return a minimal schema
        columns = [
            ColumnSchema(
                name="result",
                unified_type=UnifiedDataType.JSON,
                native_type="soap_response",
                nullable=True,
            )
        ]
        return TableSchema(name=table_name, columns=columns)

    async def discover_schema(self) -> DataSourceSchema:
        tables = [await self.discover_table(op) for op in self._operations]
        return DataSourceSchema(
            source_name=self._source_name,
            source_type=DataSourceType.SOAP,
            tables=tables,
        )


class SOAPConnector(Connector):
    """
    connection_params:
        wsdl_url     — URL to the WSDL definition
        service      — optional service name (zeep)
        port         — optional port name (zeep)
        operation    — WSDL operation to call for fetch_rows
        operation_params — dict of parameters passed to the SOAP operation
    credentials:
        username / password — for WS-Security Basic auth
    """

    source_type: ClassVar[DataSourceType] = DataSourceType.SOAP

    def __init__(self, config: ConnectorConfig) -> None:
        super().__init__(config)
        self._client = None
        self._operations: list[str] = []

    async def connect(self) -> None:
        try:
            from zeep import Client
            from zeep.transports import Transport
            import requests

            self._state = ConnectorState.CONNECTING
            p = self._config.connection_params
            c = self._config.credentials

            session = requests.Session()
            if c.get("username"):
                session.auth = (c["username"], c.get("password", ""))

            transport = Transport(session=session, timeout=p.get("timeout", 30))
            self._client = Client(wsdl=p["wsdl_url"], transport=transport)

            # Discover available operations
            service = self._client.wsdl.services
            self._operations = []
            for svc in service.values():
                for port in svc.ports.values():
                    for op_name in port.binding._operations:
                        self._operations.append(op_name)

            self._state = ConnectorState.CONNECTED
        except Exception as exc:
            self._state = ConnectorState.ERROR
            raise ConnectionError(f"SOAP connect failed: {exc}") from exc

    async def disconnect(self) -> None:
        self._client = None
        self._state = ConnectorState.DISCONNECTED

    async def health_check(self) -> bool:
        return self._client is not None

    def get_schema_inspector(self) -> SchemaInspector:
        return SOAPSchemaInspector(self._operations, self.name)

    def _response_to_rows(self, response: Any) -> list[RawRow]:
        """Flatten SOAP response into a list of dicts."""
        if response is None:
            return []
        if isinstance(response, list):
            return [dict(r) if hasattr(r, "__dict__") else {"value": r} for r in response]
        if hasattr(response, "__dict__"):
            inner = {k: v for k, v in vars(response).items() if not k.startswith("_")}
            # If one of the values is a list, treat that as the rows
            for v in inner.values():
                if isinstance(v, list):
                    return self._response_to_rows(v)
            return [inner]
        return [{"value": response}]

    async def fetch_rows(
        self,
        table: str,                  # treated as the WSDL operation name
        columns: list[str] | None = None,
        filters: dict[str, Any] | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[RawRow]:
        if not self._client:
            raise ConnectionError("Not connected")
        p = self._config.connection_params
        operation = table or p.get("operation")
        op_params = {**p.get("operation_params", {}), **(filters or {})}
        try:
            fn = getattr(self._client.service, operation)
            response = fn(**op_params)
            rows = self._response_to_rows(response)
            rows = rows[offset:]
            if limit is not None:
                rows = rows[:limit]
            if columns:
                rows = [{k: r.get(k) for k in columns} for r in rows]
            return rows
        except Exception as exc:
            raise QueryError(f"SOAP call '{operation}' failed: {exc}") from exc

    async def count_rows(self, table: str, filters: dict[str, Any] | None = None) -> int:
        rows = await self.fetch_rows(table, filters=filters)
        return len(rows)
