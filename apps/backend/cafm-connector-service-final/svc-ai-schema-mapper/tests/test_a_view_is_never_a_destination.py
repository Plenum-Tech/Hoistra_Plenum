"""A view is somewhere data is read from, never somewhere a migration writes to.

plenum_cafm.contracts and plenum_cafm.invoices are VIEWS (udr_building_graph.sql) over
contract_sla_parameters and invoice_verifications. information_schema.columns lists a view's
columns beside a table's, and the two catalog helpers in db.py read it unfiltered — although both
say "base table" — so every consumer took the views for tables: Node 1 and Node 2 routed a sheet
called "Contracts" to plenum_cafm.contracts by name, the gate offered its unmapped fields as new
columns on it, and the write step died on

    ALTER TABLE plenum_cafm.contracts ADD COLUMN IF NOT EXISTS contract_type VARCHAR(255)
    -- ALTER action ADD COLUMN cannot be performed on relation "contracts"
    -- DETAIL: This operation is not supported for views.

after every other step had run (29 Oct 2026). A contract register belongs in vendor_contracts,
the table FM_ENTITY_TO_TABLE already names for the "contract" entity.
"""
from __future__ import annotations

import sqlite3

import pytest

import src.db as db


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _CatalogSession:
    """Runs the helper's SQL against a SQLite stand-in for Postgres' information_schema."""

    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, clause, params=None):
        return _Result(self._conn.execute(str(clause), params or {}).fetchall())


@pytest.fixture
def catalog(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.execute("ATTACH DATABASE ':memory:' AS information_schema")
    conn.execute("CREATE TABLE information_schema.tables "
                 "(table_schema TEXT, table_name TEXT, table_type TEXT)")
    conn.execute("CREATE TABLE information_schema.columns (table_schema TEXT, table_name TEXT, "
                 "column_name TEXT, data_type TEXT, ordinal_position INTEGER)")
    relations = {
        ("vendor_contracts", "BASE TABLE"): [("contract_name", "text"), ("contract_start", "date")],
        ("contract_sla_parameters", "BASE TABLE"): [("contract_ref", "character varying")],
        ("contracts", "VIEW"): [("contract_ref", "character varying"), ("start_date", "date")],
        ("invoices", "VIEW"): [("invoice_ref", "text")],
    }
    for (name, kind), cols in relations.items():
        conn.execute("INSERT INTO information_schema.tables VALUES ('plenum_cafm', ?, ?)",
                     (name, kind))
        for pos, (col, dtype) in enumerate(cols, 1):
            conn.execute("INSERT INTO information_schema.columns VALUES ('plenum_cafm', ?, ?, ?, ?)",
                         (name, col, dtype, pos))
    # Another schema's view of the same name must not leak in either.
    conn.execute("INSERT INTO information_schema.tables VALUES ('public', 'vendor_contracts', 'VIEW')")
    monkeypatch.setattr(db, "get_async_session_factory", lambda: (lambda: _CatalogSession(conn)))
    return conn


async def test_the_column_catalog_lists_tables_and_not_views(catalog):
    cols = await db.get_plenum_cafm_columns_by_table()
    assert set(cols) == {"vendor_contracts", "contract_sla_parameters"}
    assert cols["vendor_contracts"] == {"contract_name", "contract_start"}


async def test_the_type_catalog_lists_tables_and_not_views(catalog):
    types_ = await db.get_plenum_cafm_column_types_by_table()
    assert set(types_) == {"vendor_contracts", "contract_sla_parameters"}
    assert types_["vendor_contracts"] == {"contract_name": "text", "contract_start": "date"}


async def test_the_view_catalog_lists_views(monkeypatch):
    seen = {}

    class _Session(_CatalogSession):
        async def execute(self, clause, params=None):
            seen["sql"] = str(clause)
            return _Result([("contracts",), ("invoices",)])

    monkeypatch.setattr(db, "get_async_session_factory", lambda: (lambda: _Session(None)))
    assert await db.get_plenum_cafm_view_names() == {"contracts", "invoices"}
    assert "relkind" in seen["sql"]


# A NEW table named like a view: CREATE TABLE IF NOT EXISTS sees the view, skips with a NOTICE,
# and every row then fails on "cannot insert into view". An "Invoices" sheet has no table of its
# own, so this is the ordinary case, not a corner one. The new table takes "<name>_register".

def test_a_new_table_never_takes_a_views_name():
    from src.graph.nodes.write_node import new_table_name

    views = {"contracts", "invoices"}
    assert new_table_name("Invoices", views) == "invoices_register"
    assert new_table_name("invoice_lines", views) == "invoice_lines"
    assert new_table_name("Work Orders", views) == "work_orders"
    assert new_table_name("Invoices", set()) == "invoices"


@pytest.fixture
def views(monkeypatch):
    async def _views():
        return {"contracts", "invoices"}

    async def _empty(*_a, **_k):
        return {}

    async def _tables():
        return ["vendor_contracts", "vendors"]

    from src.graph.nodes import human_review_node as hr, pre_semantic_review_node as psr

    monkeypatch.setattr(db, "get_plenum_cafm_view_names", _views)
    monkeypatch.setattr(db, "get_plenum_cafm_columns_by_table", _empty)
    monkeypatch.setattr(db, "get_plenum_cafm_sample_values_by_table", _empty)
    monkeypatch.setattr(psr, "_list_plenum_cafm_tables", _tables)
    monkeypatch.setattr(hr, "_list_plenum_cafm_tables", _tables)

    async def _no_registry(**_k):  # the real one writes the tracked registry JSON in the tree
        return None

    monkeypatch.setattr(hr, "registry_append", _no_registry)
    return psr, hr


async def test_the_routing_gate_moves_a_new_table_off_a_view(views, monkeypatch):
    psr, _ = views
    monkeypatch.setattr(psr, "interrupt", lambda _p: {"decisions": {}, "table_overrides": {
        "Invoices": {"target_table": "invoices", "is_new_table": True}}})
    out = await psr.pre_semantic_review_node({
        "migration_id": None, "event_log": [], "table_routing": {"Invoices": "invoices"},
        "cafm_table_matches": {"Invoices": None}, "unresolved_by_table": {},
        "tier1_mappings_by_table": {"Invoices": [
            {"source_field": "Ref", "target_field": "invoice_ref", "tier": "T1_alias"}]},
        "full_tables": {"Invoices": [{"Ref": "INV-1"}]}, "extra_fields_config": [],
    })
    assert out["table_routing"]["Invoices"] == "invoices_register"
    assert out["pending_table_overrides"]["Invoices"]["target_table"] == "invoices_register"


async def test_the_field_mapping_gate_moves_a_new_table_off_a_view(views, monkeypatch):
    _, hr = views
    monkeypatch.setattr(hr, "interrupt", lambda _p: {"flagged": {"Invoices": [
        {"action": "override", "source_field": "Amount", "target_field": "amount",
         "is_new_column": True, "is_new_table": True, "target_table": "Invoices"}]},
        "unmapped": {}})
    out = await hr.human_review_node({
        "migration_id": None, "event_log": [], "table_routing": {"Invoices": "invoices"},
        "tier2_flagged_by_table": {"Invoices": [
            {"source_field": "Amount", "target_field": "amount", "confidence": 0.75,
             "tier": "T2_semantic"}]},
        "tier2_unmappable_by_table": {}, "extra_fields_config": [],
        "full_tables": {"Invoices": [{"Amount": "10.00"}]},
    })
    assert out["table_routing"]["Invoices"] == "invoices_register"
    assert [e["target_table"] for e in out["extra_fields_config"] if e.get("is_new_table")] \
        == ["invoices_register"]


def test_a_contracts_sheet_is_routed_to_vendor_contracts():
    from src.graph.nodes.deterministic_mapper import route_by_sheet_name

    assert route_by_sheet_name("Contracts") == "vendor_contracts"
    assert route_by_sheet_name("Service Contracts") == "vendor_contracts"
