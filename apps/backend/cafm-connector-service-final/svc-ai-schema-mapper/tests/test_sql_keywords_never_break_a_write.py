"""A column or table name the system makes is always one PostgreSQL accepts, with no one fixing it.

29 Oct 2026: a workbook's meter_events sheet had a column called "end". The field-mapping gate
proposed a new column under that name, and the write stopped at statement 109 of 148:

    ALTER TABLE plenum_cafm.energy_anomalies ADD COLUMN IF NOT EXISTS end VARCHAR(255);
    syntax error at or near "end"

Every name check in the mapper looked only at the characters ([a-z_][a-z0-9_]*), and "end" is
made of good characters. 101 PostgreSQL words (end, order, user, group, default, check, limit,
from, to, ...) cannot stand bare as a column name (probed against PostgreSQL 16, pg_get_keywords
categories R and T). Some of them do not even fail in a SELECT: a bare `user` or `current_date`
silently returns the session user or today's date instead of the column.

Names the system makes are now repaired, never refused and never left for a person to rename:
a reserved word gets ``_value`` (a new TABLE gets ``_register``, as a view's name already
did), a leading digit gets ``col_``, and a name longer than PostgreSQL's 63 characters is cut
and given a short hash so two long headers never become one column. Every statement emitter
also quotes a keyword, so a name that reaches SQL some other way still parses.
"""
from __future__ import annotations

import logging
import sys
import types

import pytest

if "cafm_shared" not in sys.modules:
    _shared = types.ModuleType("cafm_shared")
    _logging = types.ModuleType("cafm_shared.logging")
    _logging.get_logger = lambda name=None: logging.getLogger(name or "test")
    _shared.logging = _logging
    sys.modules["cafm_shared"] = _shared
    sys.modules["cafm_shared.logging"] = _logging

from src import sql_identifiers as ids  # noqa: E402
from src.export.sql_exporter import _quote_ident  # noqa: E402
from src.graph.nodes import write_node as wn  # noqa: E402
from src.graph.nodes.schema_write_node import _safe_identifier  # noqa: E402
from src.udr.column_intelligence import _q  # noqa: E402


class TestANameTheSystemMakes:
    @pytest.mark.parametrize("raw,expect", [
        ("end", "end_value"),
        ("End", "end_value"),
        ("ORDER", "order_value"),
        ("user", "user_value"),
        ("current_date", "current_date_value"),
        ("2024 Cost", "col_2024_cost"),
        ("1col", "col_1col"),
        # Keywords PostgreSQL takes as a bare column name, and names already on plenum_cafm,
        # are left exactly as they are.
        ("time", "time"),
        ("timestamp", "timestamp"),
        ("row", "row"),
        ("name", "name"),
        ("status", "status"),
        ("end_date", "end_date"),
        ("Asset Ref", "asset_ref"),
    ])
    def test_is_one_postgres_accepts(self, raw, expect):
        assert wn._to_safe_identifier(raw) == expect
        assert _safe_identifier(raw) == expect

    @pytest.mark.parametrize("word", sorted(ids.RESERVED_WORDS))
    def test_is_never_a_reserved_word(self, word):
        out = wn._to_safe_identifier(word)
        assert out not in ids.RESERVED_WORDS
        assert out == f"{word}_value"

    def test_a_long_header_is_cut_to_63_and_two_long_headers_stay_apart(self):
        a = wn._to_safe_identifier("a" * 70)
        b = wn._to_safe_identifier("a" * 69 + "b")
        assert len(a) == 63 and len(b) == 63
        assert a.startswith("a" * 54 + "_")
        assert a != b
        assert wn._to_safe_identifier("a" * 70) == a, "the same header always gets the same name"

    @pytest.mark.parametrize("raw", [None, "", "   ", "!!!"])
    def test_with_nothing_usable_left_is_still_refused(self, raw):
        assert wn._to_safe_identifier(raw) is None
        assert _safe_identifier(raw) is None

    @pytest.mark.parametrize("raw", ["end", "Order", "2024 Cost", "a" * 90, "x-y z", "end_value"])
    def test_repairing_twice_changes_nothing(self, raw):
        once = wn._to_safe_identifier(raw)
        assert wn._to_safe_identifier(once) == once

    def test_a_new_column_button_name_is_repaired_the_same_way(self):
        assert wn._snake_ident("End") == "end_value"
        assert wn._snake_ident("Order #") == "order_value"
        assert wn._snake_ident("2024") == "col_2024"

    def test_a_new_table_named_by_a_keyword_becomes_a_register(self):
        assert wn.new_table_name("Order", set()) == "order_register"
        assert wn.new_table_name("orders", set()) == "orders"


class TestTheMigrationDDL:
    def _sql(self, entries, mappings=None):
        return "\n".join(d["sql"] for d in wn._build_migration_ddl_statements(entries, mappings))

    def test_the_29_oct_column_is_created_under_a_name_that_parses(self):
        sql = self._sql([{
            "storage_strategy": "custom", "source_table": "meter_events", "source_field": "end",
            "target_table": "energy_anomalies", "custom_column_name": "end",
            "data_type": "VARCHAR(255)", "is_new_table": False, "nullable": True,
        }])
        assert "ALTER TABLE plenum_cafm.energy_anomalies ADD COLUMN IF NOT EXISTS end_value VARCHAR(255);" in sql
        assert " end " not in sql

    def test_a_new_table_takes_repaired_names_for_every_column(self):
        sql = self._sql(
            [{"storage_strategy": "custom", "source_table": "Orders", "source_field": "from",
              "target_table": "order", "custom_column_name": "from", "data_type": "TEXT",
              "is_new_table": True, "nullable": True}],
            {"Orders": [{"target_field": "group", "data_type": "TEXT", "sample_values": []}]},
        )
        assert "CREATE TABLE IF NOT EXISTS plenum_cafm.order_register" in sql
        assert "    group_value TEXT" in sql
        assert "    from_value TEXT" in sql

    @pytest.mark.parametrize("bad_type", ["string", "TEXT; DROP TABLE plenum_cafm.assets; --", "VARCHAR(255"])
    def test_a_type_postgres_would_refuse_falls_back_to_text(self, bad_type):
        sql = self._sql([{
            "storage_strategy": "custom", "target_table": "assets", "custom_column_name": "note",
            "data_type": bad_type, "is_new_table": False, "nullable": True,
        }])
        assert "ADD COLUMN IF NOT EXISTS note TEXT;" in sql
        assert "DROP" not in sql.upper()

    def test_the_writer_allows_the_column_under_the_name_it_was_created_with(self):
        approved = wn._collect_approved_new_columns({
            "table_routing": {"meter_events": "energy_anomalies"},
            "extra_fields_config": [{
                "storage_strategy": "custom", "source_table": "meter_events", "source_field": "end",
                "target_table": "energy_anomalies", "custom_column_name": "end",
            }],
            "column_dest_overrides": {"meter_events": {"Order": "__new__"}},
        })
        assert approved == {"energy_anomalies": {"end_value", "order_value"}}


class TestAConfirmedForeignKey:
    """``ADD CONSTRAINT IF NOT EXISTS`` is not PostgreSQL: every FK a person confirmed failed."""

    def _fk_sql(self, **kw):
        from src.graph.nodes.schema_write_node import _build_ddl_statements
        fk = {"user_confirmed": True, "source_table": "work_orders", "source_column": "vendor_id",
              "target_table": "vendors", "target_column": "id"}
        fk.update(kw)
        return "\n".join(d["sql"] for d in _build_ddl_statements([], [fk], {"assets"}))

    def test_is_added_once_without_the_clause_postgres_refuses(self):
        sql = self._fk_sql()
        assert "IF NOT EXISTS fk_" not in sql
        assert "conname = 'fk_work_orders_vendor_id_vendors'" in sql
        assert ("ALTER TABLE plenum_cafm.work_orders ADD CONSTRAINT fk_work_orders_vendor_id_vendors "
                "FOREIGN KEY (vendor_id) REFERENCES plenum_cafm.vendors(id);") in sql

    def test_names_are_repaired_like_any_other(self):
        sql = self._fk_sql(source_column="Order", target_column="end")
        assert "FOREIGN KEY (order_value) REFERENCES plenum_cafm.vendors(end_value);" in sql

    def test_a_name_with_nothing_usable_is_left_out(self):
        assert self._fk_sql(source_column="!!!") == ""


class TestQuotingInEveryEmitter:
    @pytest.mark.parametrize("name,expect", [
        ("end", '"end"'),
        ("End", '"end"'),
        ("time", '"time"'),        # works bare, but PostgreSQL's own quote_ident quotes it
        ("name", "name"),
        ("asset_ref", "asset_ref"),
        ("Abc", "Abc"),
        ("1abc", '"1abc"'),
        ('a"b', '"a""b"'),
    ])
    def test_a_keyword_is_quoted_and_nothing_else_changes(self, name, expect):
        assert _quote_ident(name) == expect
        assert _q(name) == expect
        assert ids.quote_ident(name) == expect
