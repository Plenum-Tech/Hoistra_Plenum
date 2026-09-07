"""MongoDB connector — pymongo with $changeStream support for CDC."""

from __future__ import annotations

from typing import Any, ClassVar

from cafm_connector.connectors.base import Connector, ConnectorConfig, SchemaInspector
from cafm_connector.core.exceptions import ConnectionError, QueryError
from cafm_connector.core.types import ConnectorState, DataSourceType, RawRow
from cafm_connector.schema.models import DataSourceSchema, TableSchema


class MongoDBSchemaInspector(SchemaInspector):
    def __init__(self, db, source_name: str, sample_size: int = 100) -> None:
        self._db = db
        self._source_name = source_name
        self._sample_size = sample_size

    async def list_tables(self) -> list[str]:
        return self._db.list_collection_names()

    async def discover_table(self, table_name: str) -> TableSchema:
        from cafm_connector.schema.models import ColumnSchema
        from cafm_connector.core.types import UnifiedDataType

        collection = self._db[table_name]
        # Sample documents to infer schema
        docs = list(collection.find().limit(self._sample_size))
        all_keys: dict[str, str] = {}
        for doc in docs:
            for k, v in doc.items():
                if k not in all_keys:
                    all_keys[k] = type(v).__name__

        columns = [
            ColumnSchema(
                name=k,
                unified_type=UnifiedDataType.UNKNOWN,
                native_type=python_type,
                nullable=True,
                primary_key=(k == "_id"),
            )
            for k, python_type in all_keys.items()
        ]
        return TableSchema(
            name=table_name,
            columns=columns,
            primary_key=["_id"],
        )

    async def discover_schema(self) -> DataSourceSchema:
        tables = [await self.discover_table(t) for t in await self.list_tables()]
        return DataSourceSchema(
            source_name=self._source_name,
            source_type=DataSourceType.MONGODB,
            tables=tables,
        )


class MongoDBConnector(Connector):
    """
    connection_params: host, port, database  (or url + database)
    credentials:       username, password, auth_source

    Supports $changeStream for CDC / incremental sync.
    """

    source_type: ClassVar[DataSourceType] = DataSourceType.MONGODB

    def __init__(self, config: ConnectorConfig) -> None:
        super().__init__(config)
        self._client = None
        self._db = None

    def _build_url(self) -> str:
        p = self._config.connection_params
        c = self._config.credentials
        if "url" in p:
            return p["url"]
        user   = c.get("username", "")
        pwd    = c.get("password", "")
        auth   = c.get("auth_source", "admin")
        host   = p.get("host", "localhost")
        port   = p.get("port", 27017)
        if user and pwd:
            return f"mongodb://{user}:{pwd}@{host}:{port}/?authSource={auth}"
        return f"mongodb://{host}:{port}/"

    async def connect(self) -> None:
        try:
            from pymongo import MongoClient
            self._state = ConnectorState.CONNECTING
            db_name = self._config.connection_params.get("database")
            if not db_name:
                raise ConnectionError("connection_params must include 'database'")
            self._client = MongoClient(
                self._build_url(),
                serverSelectionTimeoutMS=self._config.options.get("timeout_ms", 5000),
            )
            self._client.server_info()   # verify connectivity
            self._db = self._client[db_name]
            self._state = ConnectorState.CONNECTED
        except ConnectionError:
            raise
        except Exception as exc:
            self._state = ConnectorState.ERROR
            raise ConnectionError(f"MongoDB connect failed: {exc}") from exc

    async def disconnect(self) -> None:
        if self._client:
            self._client.close()
            self._client = None
            self._db = None
        self._state = ConnectorState.DISCONNECTED

    async def health_check(self) -> bool:
        if not self._client:
            return False
        try:
            self._client.server_info()
            return True
        except Exception:
            return False

    def get_schema_inspector(self) -> SchemaInspector:
        if not self._db:
            raise ConnectionError("Not connected")
        return MongoDBSchemaInspector(self._db, self.name)

    @staticmethod
    def _build_filter(filters: dict[str, Any] | None) -> dict[str, Any]:
        """Convert simple equality / operator filters to MongoDB query dict."""
        if not filters:
            return {}
        op_map = {"__gt": "$gt", "__gte": "$gte", "__lt": "$lt",
                  "__lte": "$lte", "__ne": "$ne", "__in": "$in", "__nin": "$nin"}
        q: dict[str, Any] = {}
        for key, value in filters.items():
            matched = False
            for suffix, op in op_map.items():
                if key.endswith(suffix):
                    field = key[: -len(suffix)]
                    q.setdefault(field, {})[op] = value
                    matched = True
                    break
            if not matched:
                q[key] = value
        return q

    async def fetch_rows(
        self,
        table: str,
        columns: list[str] | None = None,
        filters: dict[str, Any] | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[RawRow]:
        if not self._db:
            raise ConnectionError("Not connected")
        col = self._db[table]
        query = self._build_filter(filters)
        proj = None
        if columns:
            proj = {c: 1 for c in columns}
            if "_id" not in columns:
                proj["_id"] = 0
        try:
            cursor = col.find(query, proj)
            if offset:
                cursor = cursor.skip(offset)
            if limit is not None:
                cursor = cursor.limit(limit)
            rows = []
            for doc in cursor:
                if "_id" in doc:
                    doc["_id"] = str(doc["_id"])
                rows.append(doc)
            return rows
        except Exception as exc:
            raise QueryError(f"fetch_rows failed on {table}: {exc}") from exc

    async def count_rows(self, table: str, filters: dict[str, Any] | None = None) -> int:
        if not self._db:
            raise ConnectionError("Not connected")
        try:
            return self._db[table].count_documents(self._build_filter(filters))
        except Exception as exc:
            raise QueryError(f"count_rows failed: {exc}") from exc

    async def insert_rows(self, table: str, rows: list[RawRow]) -> int:
        if not self._db:
            raise ConnectionError("Not connected")
        result = self._db[table].insert_many(rows)
        return len(result.inserted_ids)

    async def watch_changes(self, collection: str):
        """
        Yield change stream events for CDC / incremental sync.
        Caller is responsible for consuming the generator.
        """
        if not self._db:
            raise ConnectionError("Not connected")
        with self._db[collection].watch() as stream:
            for change in stream:
                yield change
