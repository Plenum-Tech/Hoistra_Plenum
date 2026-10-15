"""A file ingested with a building selected files every row under that building, new tables too.

Until 6 Oct 2026 the building chosen at upload reached five tables only - assets, work orders,
energy meters, building sections and compliance certificates - and a table a migration created
(a petty-cash sheet, say) was made with neither organization_id nor building_id: its rows said
neither whose they were nor where they belonged, and no company or building filter could hold
them. Now every table a migration creates has both columns, and with a building selected every
table the file writes to takes the building - except company-wide master data (vendors, users,
the buildings themselves), where a single building would hide the row from colleagues.
"""
from __future__ import annotations

import logging
import sys
import types

if "cafm_shared" not in sys.modules:
    _shared = types.ModuleType("cafm_shared")
    _logging = types.ModuleType("cafm_shared.logging")
    _logging.get_logger = lambda name=None: logging.getLogger(name or "test")
    _shared.logging = _logging
    sys.modules["cafm_shared"] = _shared
    sys.modules["cafm_shared.logging"] = _logging

from src.graph.nodes import write_node as wn  # noqa: E402

WN_SRC = open(wn.__file__, encoding="utf-8").read()
BUILDING = "5b0c6f0e-1f7e-4b43-9d0b-2a3c6d1e9f10"


class TestWhichTablesAreFiledUnderABuilding:
    def test_the_five_that_always_were_still_are_with_or_without_a_selection(self):
        for t in ("assets", "work_orders", "energy_meters", "building_sections", "compliance_certificates"):
            assert wn.files_under_building(t, None) and wn.files_under_building(t, BUILDING)

    def test_with_a_building_selected_a_new_table_is_filed_under_it(self):
        assert wn.files_under_building("petty_cash", BUILDING)
        assert wn.files_under_building("spare_parts", BUILDING)

    def test_without_a_selection_a_new_table_is_not_guessed_at(self):
        assert not wn.files_under_building("petty_cash", None)

    def test_company_wide_master_data_never_is(self):
        for t in ("vendors", "users", "buildings", "sites", "organizations", "asset_categories"):
            assert not wn.files_under_building(t, BUILDING), t


class TestANewTableIsCreatedWithBothColumns:
    def _ddl(self, mappings):
        cfg = [{"storage_strategy": "custom", "target_table": "Petty Cash", "is_new_table": True,
                "new_table_pk": "id", "source_table": "petty"}]
        out = wn._build_migration_ddl_statements(cfg, {"petty": mappings})
        return out[0]["sql"]

    def test_organization_and_building_columns_are_part_of_the_table(self):
        sql = self._ddl([{"target_field": "amount", "sample_values": ["12.50", "8.00"]},
                         {"target_field": "category", "sample_values": ["Cleaning"]}])
        assert "CREATE TABLE IF NOT EXISTS plenum_cafm.petty_cash" in sql
        assert "    organization_id UUID" in sql and "    building_id UUID" in sql
        assert "amount" in sql and "category" in sql

    def test_a_sheet_column_called_building_id_is_not_emitted_twice(self):
        sql = self._ddl([{"target_field": "building_id", "sample_values": ["Bishopsgate"]},
                         {"target_field": "amount", "sample_values": ["5"]}])
        assert sql.count("building_id") == 1


class TestTheWriterAppliesIt:
    """The writer runs against a database, so these hold its decisions in the source."""

    def test_a_selected_building_sends_every_table_through_the_path_that_links_buildings(self):
        assert 'if state.get("building_id"):\n            # A building selected at upload' in WN_SRC
        assert "_needs = set(_routed)" in WN_SRC

    def test_the_column_is_added_before_any_row_is_written(self):
        i = WN_SRC.index("_link = files_under_building(safe_table, _default_building)")
        j = WN_SRC.index("for row in records:", i)
        block = WN_SRC[i:j]
        assert 'missing_columns["building_id"] = "UUID"' in block
        assert 'missing_columns["organization_id"] = "UUID"' in block

    def test_a_core_table_keeps_the_two_system_columns_rather_than_dropping_them_as_unknown(self):
        assert '_system = {"building_id", "organization_id"}' in WN_SRC

    def test_every_linked_table_resolves_each_row(self):
        assert 'if _link and ("building_id" in db_cols or "building_id" in missing_columns)' in WN_SRC
