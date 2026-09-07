"""PostgreSQL connector — SQLAlchemy async + psycopg2."""

from __future__ import annotations

from typing import Any, ClassVar

from sqlalchemy import MetaData, Table, create_engine, func, select, text

from cafm_connector.connectors.base import Connector, ConnectorConfig, SchemaInspector
from cafm_connector.core.exceptions import ConnectionError, QueryError
from cafm_connector.core.types import ConnectorState, DataSourceType, RawRow
from cafm_connector.schema.models import DataSourceSchema, TableSchema


class PostgreSQLSchemaInspector(SchemaInspector):
    def __init__(self, engine, source_name: str, schema: str = "public") -> None:
        self._engine = engine
        self._source_name = source_name
        self._schema = schema

    async def list_tables(self) -> list[str]:
        from sqlalchemy import inspect
        insp = inspect(self._engine)
        return insp.get_table_names(schema=self._schema)

    async def discover_table(self, table_name: str) -> TableSchema:
        from sqlalchemy import inspect
        from cafm_connector.schema.models import ColumnSchema
        from cafm_connector.core.types import UnifiedDataType
        insp = inspect(self._engine)
        cols = insp.get_columns(table_name, schema=self._schema)
        pk = insp.get_pk_constraint(table_name, schema=self._schema)
        pk_cols = pk.get("constrained_columns", [])
        columns = [
            ColumnSchema(
                name=c["name"],
                unified_type=UnifiedDataType.UNKNOWN,
                native_type=str(c["type"]),
                nullable=c.get("nullable", True),
                primary_key=c["name"] in pk_cols,
            )
            for c in cols
        ]
        return TableSchema(
            name=table_name,
            schema_name=self._schema,
            columns=columns,
            primary_key=pk_cols,
        )

    async def discover_schema(self) -> DataSourceSchema:
        from cafm_connector.core.types import DataSourceType
        tables = [await self.discover_table(t) for t in await self.list_tables()]
        return DataSourceSchema(
            source_name=self._source_name,
            source_type=DataSourceType.POSTGRESQL,
            tables=tables,
        )


class PostgreSQLConnector(Connector):
    """
    Expected connection_params:
        host, port, database, username  (non-sensitive)
    Expected credentials:
        password                         (encrypted by secrets backend)

    Or pass a full ``url`` in connection_params to skip individual fields.
    """

    source_type: ClassVar[DataSourceType] = DataSourceType.POSTGRESQL

    def __init__(self, config: ConnectorConfig) -> None:
        super().__init__(config)
        self._engine = None
        self._metadata = None
        self._schema: str = config.connection_params.get("schema", "public")

    def _build_url(self) -> str:
        p = self._config.connection_params
        c = self._config.credentials
        if "url" in p:
            return p["url"]
        host = p.get("host", "localhost")
        port = p.get("port", 5432)
        db   = p.get("database", "")
        user = p.get("username", "")
        pwd  = c.get("password", "")
        return f"postgresql+psycopg2://{user}:{pwd}@{host}:{port}/{db}"

    async def connect(self) -> None:
        try:
            self._state = ConnectorState.CONNECTING
            self._engine = create_engine(
                self._build_url(),
                pool_size=self._config.options.get("pool_size", 5),
                pool_pre_ping=True,
            )
            with self._engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            self._metadata = MetaData(schema=self._schema)
            self._metadata.reflect(bind=self._engine)
            self._state = ConnectorState.CONNECTED
        except Exception as exc:
            self._state = ConnectorState.ERROR
            raise ConnectionError(f"PostgreSQL connect failed: {exc}") from exc

    async def disconnect(self) -> None:
        if self._engine:
            self._engine.dispose()
            self._engine = None
            self._metadata = None
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
        return PostgreSQLSchemaInspector(self._engine, self.name, self._schema)

    def _get_table(self, name: str) -> Table:
        if not self._metadata:
            raise ConnectionError("Not connected")
        t = self._metadata.tables.get(f"{self._schema}.{name}") or \
            self._metadata.tables.get(name)
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
            raise QueryError(f"fetch_rows failed on {table}: {exc}") from exc

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
            raise QueryError(f"count_rows failed on {table}: {exc}") from exc

    async def insert_rows(self, table: str, rows: list[RawRow]) -> int:
        t = self._get_table(table)
        try:
            with self._engine.begin() as conn:
                conn.execute(t.insert(), rows)
            return len(rows)
        except Exception as exc:
            raise QueryError(f"insert_rows failed on {table}: {exc}") from exc

    async def execute_raw(self, query: str, params: dict[str, Any] | None = None) -> list[RawRow]:
        if not self._engine:
            raise ConnectionError("Not connected")
        try:
            with self._engine.connect() as conn:
                return [dict(r._mapping) for r in conn.execute(text(query), params or {})]
        except Exception as exc:
            raise QueryError(f"execute_raw failed: {exc}") from exc
