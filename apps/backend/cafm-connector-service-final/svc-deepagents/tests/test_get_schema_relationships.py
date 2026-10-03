"""The schema tool described every table and never said how any of them related.

The UDR sub-agent writes its own SQL from get_schema(). Asked for Riverside Court's meter
readings it joined meter_readings to `meters` — the obvious parent, by name — and reported
zero readings and 0.0 kWh for a building holding fifty. meter_readings.meter_id references
energy_meters.id, a foreign key the database has declared all along; `meters` is a separate
register whose meter_id never matches a reading. Nothing in the tool's output could have
told the model that, so it guessed, and the guess had a name that fit.

These pin the additive shape (nothing that read "tables" breaks), the relationships coming
from the database rather than a hand-written list, and that the docstring tells the model
which of the two to trust.
"""
from __future__ import annotations

import asyncio

from src.agents import udr_agent


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


class FakeSession:
    """Answers the columns query and the foreign-key query by what each one selects."""

    def __init__(self, columns, fks):
        self.columns, self.fks = columns, fks
        self.sql: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, stmt):
        sql = str(stmt)
        self.sql.append(sql)
        if "FOREIGN KEY" in sql:
            return _Result(self.fks)
        return _Result(self.columns)


COLUMNS = [
    {"table_name": "meter_readings", "column_name": "meter_id", "data_type": "uuid"},
    {"table_name": "meter_readings", "column_name": "consumption_kwh", "data_type": "numeric"},
    {"table_name": "energy_meters", "column_name": "id", "data_type": "uuid"},
    {"table_name": "meters", "column_name": "meter_id", "data_type": "uuid"},
]
FKS = [
    {"from_table": "meter_readings", "from_column": "meter_id",
     "to_table": "energy_meters", "to_column": "id"},
    {"from_table": "meters", "from_column": "building_id",
     "to_table": "buildings", "to_column": "building_id"},
]


def schema(columns=COLUMNS, fks=FKS, monkeypatch=None):
    monkeypatch.setattr(udr_agent, "_schema_cache", None)
    monkeypatch.setattr(udr_agent.database, "AsyncSessionLocal", lambda: FakeSession(columns, fks))
    return asyncio.run(udr_agent.get_schema.ainvoke({}))


def test_relationships_come_from_the_declared_foreign_keys(monkeypatch):
    out = schema(monkeypatch=monkeypatch)
    assert {"from": "meter_readings.meter_id", "to": "energy_meters.id"} in out["relationships"]


def test_the_trap_that_produced_zero_is_now_visible(monkeypatch):
    # The model can see that readings reference energy_meters and that nothing references
    # meters.meter_id — the two facts it needed and did not have.
    out = schema(monkeypatch=monkeypatch)
    targets = {r["to"] for r in out["relationships"]}
    assert "energy_meters.id" in targets
    assert "meters.meter_id" not in targets


def test_tables_keep_their_shape(monkeypatch):
    # Additive. Anything reading result["tables"] as {table: ["col (type)", ...]} still does.
    out = schema(monkeypatch=monkeypatch)
    assert out["tables"]["meter_readings"] == ["meter_id (uuid)", "consumption_kwh (numeric)"]


def test_a_failed_relationship_query_still_returns_the_columns(monkeypatch):
    # The column list is worth having on its own. Relationships are the addition, so a
    # database that cannot answer that query degrades to the old behaviour, not to nothing.
    class Half(FakeSession):
        async def execute(self, stmt):
            if "FOREIGN KEY" in str(stmt):
                raise RuntimeError("permission denied for information_schema")
            return await super().execute(stmt)

    monkeypatch.setattr(udr_agent, "_schema_cache", None)
    monkeypatch.setattr(udr_agent.database, "AsyncSessionLocal", lambda: Half(COLUMNS, FKS))
    out = asyncio.run(udr_agent.get_schema.ainvoke({}))
    assert out["tables"]["energy_meters"] == ["id (uuid)"]
    assert out["relationships"] == []


def test_the_relationships_are_scoped_to_the_schema(monkeypatch):
    # The query names plenum_cafm, and it asks for foreign keys and nothing else.
    s = FakeSession(COLUMNS, FKS)
    monkeypatch.setattr(udr_agent, "_schema_cache", None)
    monkeypatch.setattr(udr_agent.database, "AsyncSessionLocal", lambda: s)
    asyncio.run(udr_agent.get_schema.ainvoke({}))
    fk_sql = next(q for q in s.sql if "FOREIGN KEY" in q)
    assert "plenum_cafm" in fk_sql
    assert "referential" in fk_sql.lower() or "constraint_type = 'FOREIGN KEY'" in fk_sql


def test_the_docstring_tells_the_model_not_to_join_by_name():
    # The tool's docstring is the model's only instruction on how to use the output. It has
    # to say what the trap is, or the relationships list is just more data to ignore.
    doc = udr_agent.get_schema.description or ""
    assert "relationships" in doc
    assert "NEVER ON TABLE-NAME SIMILARITY" in doc
    assert "energy_meters" in doc and "meters.meter_id" in doc


def test_the_result_is_cached_as_before(monkeypatch):
    # Two queries per refresh now instead of one, so the cache matters slightly more.
    s = FakeSession(COLUMNS, FKS)
    monkeypatch.setattr(udr_agent, "_schema_cache", None)
    monkeypatch.setattr(udr_agent.database, "AsyncSessionLocal", lambda: s)
    asyncio.run(udr_agent.get_schema.ainvoke({}))
    asyncio.run(udr_agent.get_schema.ainvoke({}))
    assert len(s.sql) == 2  # columns + foreign keys, once — the second call hit the cache
