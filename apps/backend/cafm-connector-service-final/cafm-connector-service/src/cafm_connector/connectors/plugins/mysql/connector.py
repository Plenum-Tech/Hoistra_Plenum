"""MySQL connector — SQLAlchemy + PyMySQL."""

from __future__ import annotations

from typing import Any, ClassVar

from sqlalchemy import MetaData, Table, create_engine, func, select, text

from cafm_connector.connectors.base import Connector, ConnectorConfig, SchemaInspector
from cafm_connector.core.exceptions import ConnectionError, QueryError
from cafm_connector.core.types import ConnectorState, DataSourceType, RawRow
from cafm_connector.schema.models import DataSourceSchema, TableSchema


class MySQLSchemaInspector(SchemaInspector):
    def __init__(self, engine, source_name: str) -> None:
        self._engine = engine
        self._source_name = source_name

    async def list_tables(self) -> list[str]:
        from sqlalchemy import inspect
        return inspect(self._engine).get_table_names()

    async def discover_table(self, table_name: str) -> TableSchema:
        from sqlalchemy import inspect
        from cafm_connector.schema.models import ColumnSchema
        from cafm_connector.core.types import UnifiedDataType
        insp = inspect(self._engine)
        cols = insp.get_columns(table_name)
        pk = insp.get_pk_constraint(table_name).get("constrained_columns", [])
        columns = [
            ColumnSchema(
                name=c["name"],
                unified_type=UnifiedDataType.UNKNOWN,
                native_type=str(c["type"]),
                nullable=c.get("nullable", True),
                primary_key=c["name"] in pk,
            )
            for c in cols
        ]
        return TableSchema(name=table_name, columns=columns, primary_key=pk)

    async def discover_schema(self) -> DataSourceSchema:
        tables = [await self.discover_table(t) for t in await self.list_tables()]
        return DataSourceSchema(
            source_name=self._source_name,
            source_type=DataSourceType.MYSQL,
            tables=tables,
        )


class MySQLConnector(Connector):
    """
    connection_params: host, port, database, username
    credentials:       password
    """

    source_type: ClassVar[DataSourceType] = DataSourceType.MYSQL

    def __init__(self, config: ConnectorConfig) -> None:
        super().__init__(config)
        self._engine = None
        self._metadata = None

    def _build_url(self) -> str:
        p = self._config.connection_params
        c = self._config.credentials
        if "url" in p:
            return p["url"]
        charset = p.get("charset", "utf8mb4")
        return (
            f"mysql+pymysql://{p.get('username', '')}:{c.get('password', '')}"
            f"@{p.get('host', 'localhost')}:{p.get('port', 3306)}"
            f"/{p.get('database', '')}?charset={charset}"
        )

    async def connect(self) -> None:
        try:
            self._state = ConnectorState.CONNECTING
            self._engine = create_engine(self._build_url(), pool_pre_ping=True)
            with self._engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            self._metadata = MetaData()
            self._metadata.reflect(bind=self._engine)
            self._state = ConnectorState.CONNECTED
        except Exception as exc:
            self._state = ConnectorState.ERROR
            raise ConnectionError(f"MySQL connect failed: {exc}") from exc

    async def disconnect(self) -> None:
        if self._engine:
            self._engine.dispose()
            self._engine = None
        self._state = ConnectorState.DISCONNECTED

    async def health_check(self) -> bool:
        if not self._engine:
            return False
        try:
            with self._engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return True
        except Exception:
            return False

    def get_schema_inspector(self) -> SchemaInspector:
        if not self._engine:
            raise ConnectionError("Not connected")
        return MySQLSchemaInspector(self._engine, self.name)

    def _get_table(self, name: str) -> Table:
        t = self._metadata.tables.get(name)
        if t is None:
            raise QueryError(f"Table '{name}' not found")
        return t

    async def fetch_rows(
        self,
        table: str,
        columns: list[str] | None = None,
        filters: dict[str, Any] | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[RawRow]:
        t = self._get_table(table)
        stmt = select(*[t.c[c] for c in columns]) if columns else select(t)
        if filters:
            for col, val in filters.items():
                stmt = stmt.where(t.c[col] == val)
        if limit is not None:
            stmt = stmt.limit(limit)
        if offset:
            stmt = stmt.offset(offset)
        try:
            with self._engine.connect() as conn:
                return [dict(r._mapping) for r in conn.execute(stmt)]
        except Exception as exc:
            raise QueryError(f"fetch_rows failed: {exc}") from exc

    async def count_rows(self, table: str, filters: dict[str, Any] | None = None) -> int:
        t = self._get_table(table)
        stmt = select(func.count()).select_from(t)
        if filters:
            for col, val in filters.items():
                stmt = stmt.where(t.c[col] == val)
        try:
            with self._engine.connect() as conn:
                return conn.execute(stmt).scalar() or 0
        except Exception as exc:
            raise QueryError(f"count_rows failed: {exc}") from exc
