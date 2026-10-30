"""A lookup table the run creates belongs in the download, rows and all.

Asked for 30 Oct: "while export also this new table data should be available". The exports are
built from the run's table records — output.json, one sheet per table in output.xlsx, a CSV per
destination table — and the synthesised lookup tables were in none of them. They reached
output.sql as a CREATE statement and stopped there: a table in the schema whose data appeared
nowhere in anything downloadable.

So the entry carries its values, the export adds the lookup tables to both record sets (the JSON
and the workbook are built from one, the CSVs and the SQL from the other), and the SQL artefact
carries INSERTs again — with the company id left as a bind variable, because the write supplies
the company and a script run by hand cannot know it.
"""
from __future__ import annotations

import logging
import os
import sys
import types

if "cafm_shared" not in sys.modules:
    _shared = types.ModuleType("cafm_shared")
    _logging = types.ModuleType("cafm_shared.logging")
    _logging.get_logger = lambda name=None: logging.getLogger(name or "test")
    _shared.logging = _logging
    sys.modules["cafm_shared"] = _shared
    sys.modules["cafm_shared.logging"] = _logging

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from src.graph.nodes.output_generator_node import lookup_records  # noqa: E402
from udr.column_intelligence import synthesize_shared_attribute_table  # noqa: E402


def _entry(**kw):
    e = {"table_name": "shared_statuses", "pk_column": "status",
         "values": ["Expired", "Expiring soon", "Valid"]}
    e.update(kw)
    return e


def _state(*entries):
    return {"column_intelligence": {"shared_attribute_tables": list(entries)}}


class TestTheRowsReachTheExport:
    def test_a_lookup_table_becomes_a_table_of_records(self):
        out = lookup_records(_state(_entry()))
        assert out == {"shared_statuses": [
            {"status": "Expired"}, {"status": "Expiring soon"}, {"status": "Valid"}]}

    def test_every_lookup_table_of_the_run(self):
        out = lookup_records(_state(
            _entry(),
            _entry(table_name="shared_currencies", pk_column="currency", values=["GBP"]),
            _entry(table_name="shared_alarms", pk_column="alarm_id", values=["ALM-1", "ALM-2"]),
        ))
        assert sorted(out) == ["shared_alarms", "shared_currencies", "shared_statuses"]
        assert out["shared_alarms"] == [{"alarm_id": "ALM-1"}, {"alarm_id": "ALM-2"}]

    def test_the_column_is_the_attribute_the_source_rows_carry(self):
        """Not id or organization_id: those are minted at write time and would be invented here."""
        rows = lookup_records(_state(_entry()))["shared_statuses"]
        assert all(set(r) == {"status"} for r in rows)


class TestItDoesNotInventTables:
    def test_an_entry_with_no_values_is_skipped(self):
        assert lookup_records(_state(_entry(values=None))) == {}
        assert lookup_records(_state({"table_name": "x", "pk_column": "y"})) == {}

    def test_an_entry_with_no_name_or_key_is_skipped(self):
        assert lookup_records(_state(_entry(table_name=""))) == {}
        assert lookup_records(_state(_entry(pk_column=""))) == {}

    def test_an_empty_value_list_still_names_the_table(self):
        assert lookup_records(_state(_entry(values=[]))) == {"shared_statuses": []}

    def test_nonsense_state(self):
        for bad in (None, {}, {"column_intelligence": None}, {"column_intelligence": "no"}):
            assert lookup_records(bad) == {}


class TestTheEntryActuallyCarriesValues:
    """lookup_records is only as good as what synthesize puts on the entry."""

    def _synth(self):
        v = ["Valid", "Expired", "Expiring soon"]
        tables = {"docs": {"rows": [{"status": x} for x in v * 20], "columns": ["status"]},
                  "certs": {"rows": [{"status": x} for x in v * 15], "columns": ["status"]}}
        return synthesize_shared_attribute_table(
            tables, canonical_name="status", group_id="G8",
            member_pairs=[("docs", "status"), ("certs", "status")])

    def test_the_values_are_on_the_entry(self):
        assert self._synth()["values"] == ["Expired", "Expiring soon", "Valid"]

    def test_and_they_survive_into_the_export(self):
        out = lookup_records({"column_intelligence": {"shared_attribute_tables": [self._synth()]}})
        assert out["shared_statuses"] == [
            {"status": "Expired"}, {"status": "Expiring soon"}, {"status": "Valid"}]

    def test_the_sql_artefact_carries_them_too(self):
        block = self._synth()["ddl_block"]
        assert "INSERT INTO plenum_cafm.shared_statuses (organization_id, status)" in block
        assert "(:org_id, 'Valid')" in block
        assert "ON CONFLICT (organization_id, status) DO NOTHING;" in block

    def test_a_quote_in_a_value_cannot_break_the_insert(self):
        v = ["O'Brien scope", "Plain"]
        tables = {"a": {"rows": [{"s": x} for x in v * 20], "columns": ["s"]},
                  "b": {"rows": [{"s": x} for x in v * 15], "columns": ["s"]}}
        e = synthesize_shared_attribute_table(
            tables, canonical_name="scope", group_id="G1",
            member_pairs=[("a", "s"), ("b", "s")])
        assert "(:org_id, 'O''Brien scope')" in e["ddl_block"]
