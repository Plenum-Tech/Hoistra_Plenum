"""Unit tests for connector registry and factory."""

from __future__ import annotations

import pytest

from cafm_connector.connectors.base import Connector, ConnectorConfig, SchemaInspector
from cafm_connector.connectors.registry import ConnectorRegistry
from cafm_connector.core.types import DataSourceType, RawRow
from cafm_connector.schema.models import DataSourceSchema, TableSchema


# ── Minimal stub connector for testing ───────────────────────────────

class StubSchemaInspector(SchemaInspector):
    async def list_tables(self):         return ["stub_table"]
    async def discover_table(self, t):   return TableSchema(name=t, columns=[])
    async def discover_schema(self):
        return DataSourceSchema(
            source_name="stub", source_type=DataSourceType.CSV, tables=[]
        )


class StubConnector(Connector):
    source_type = DataSourceType.CSV

    async def connect(self):               self._state = "connected"
    async def disconnect(self):            self._state = "disconnected"
    async def health_check(self) -> bool:  return True
    def get_schema_inspector(self):        return StubSchemaInspector()
    async def fetch_rows(self, *a, **k):   return [{"col": "val"}]
    async def count_rows(self, *a, **k):   return 1


# ── Tests ─────────────────────────────────────────────────────────────

def test_registry_singleton():
    r1 = ConnectorRegistry()
    r2 = ConnectorRegistry()
    assert r1 is r2


def test_register_and_create():
    ConnectorRegistry.reset()
    registry = ConnectorRegistry()
    registry.register(StubConnector)

    config = ConnectorConfig(
        name="test",
        source_type=DataSourceType.CSV,
        connection_params={"file_path": "/tmp/test.csv"},
    )
    connector = registry.create(config)
    assert isinstance(connector, StubConnector)
    assert connector.name == "test"


def test_register_duplicate_raises():
    ConnectorRegistry.reset()
    registry = ConnectorRegistry()
    registry.register(StubConnector)
    from cafm_connector.core.exceptions import ConnectorAlreadyRegisteredError
    with pytest.raises(ConnectorAlreadyRegisteredError):
        registry.register(StubConnector)


def test_register_duplicate_with_override():
    ConnectorRegistry.reset()
    registry = ConnectorRegistry()
    registry.register(StubConnector)
    registry.register(StubConnector, allow_override=True)  # no error
    assert registry.is_registered(DataSourceType.CSV)


def test_unknown_source_type_raises():
    ConnectorRegistry.reset()
    registry = ConnectorRegistry()
    from cafm_connector.core.exceptions import ConnectorNotFoundError
    with pytest.raises(ConnectorNotFoundError):
        registry.get(DataSourceType.POSTGRESQL)


@pytest.mark.asyncio
async def test_connector_session_lifecycle():
    ConnectorRegistry.reset()
    registry = ConnectorRegistry()
    registry.register(StubConnector)
    config = ConnectorConfig(
        name="sess_test",
        source_type=DataSourceType.CSV,
        connection_params={"file_path": "/tmp/x.csv"},
    )
    connector = registry.create(config)
    async with connector.session():
        assert connector.is_connected

    assert not connector.is_connected


@pytest.mark.asyncio
async def test_stream_rows_pagination():
    ConnectorRegistry.reset()
    registry = ConnectorRegistry()
    registry.register(StubConnector)
    config = ConnectorConfig(
        name="stream",
        source_type=DataSourceType.CSV,
        connection_params={"file_path": "/tmp/x.csv"},
    )
    connector = registry.create(config)
    batches = []
    async for batch in connector.stream_rows("stub_table", batch_size=1):
        batches.append(batch)
    assert len(batches) >= 1
