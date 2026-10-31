"""A view is not a table, and the migration must not treat it as one.

On 28 Oct 2026 a migration of Vasiliki House died at step 9, statement 14 of 29:

    ALTER TABLE plenum_cafm.contracts ADD COLUMN contract_type VARCHAR(255)
    ERROR: ALTER action ADD COLUMN cannot be performed on relation "contracts"
    DETAIL: This operation is not supported for views.

plenum_cafm.contracts is a view over contract_sla_parameters joined to documents, created by
udr_building_graph.sql. Every statement in that step runs inside one transaction, so the single
failure rolled back the thirteen that had already succeeded and the run could never finish: any
mapping that reached "contracts" failed the same way every time.

The node never asked the catalogue what kind of relation it was writing to. Now it does, and a
column meant for a view goes to the table underneath it. These pin the rule that picks that
table, because getting it wrong would put a customer's column on the wrong relation.
"""
from __future__ import annotations

import logging
import sys
import types

import pytest

# The service's logging package is not installed in the test environment; the sibling writer
# tests stub it the same way so the node can be imported without its whole service chain.
if "cafm_shared" not in sys.modules:
    _shared = types.ModuleType("cafm_shared")
    _logging = types.ModuleType("cafm_shared.logging")
    _logging.get_logger = lambda name=None: logging.getLogger(name or "test")
    _shared.logging = _logging
    sys.modules["cafm_shared"] = _shared
    sys.modules["cafm_shared.logging"] = _logging

from src.graph.nodes.write_node import _base_table_for_view  # noqa: E402


class TestTheTableUnderAView:
    def test_a_view_over_one_table_has_one_answer(self):
        assert _base_table_for_view("active_assets", ["assets"]) == "assets"

    def test_the_contracts_view_resolves_to_the_table_that_shares_its_name(self):
        # The real case: a join over two tables, only one of which is about contracts.
        assert _base_table_for_view(
            "contracts", ["contract_sla_parameters", "documents"]) == "contract_sla_parameters"

    def test_plural_and_singular_count_as_the_same_word(self):
        assert _base_table_for_view("vendors", ["vendor_profiles", "buildings"]) == "vendor_profiles"

    def test_no_shared_word_is_no_answer_rather_than_a_guess(self):
        # Putting a customer's column on an unrelated table is worse than not placing it.
        assert _base_table_for_view("contracts", ["documents", "buildings"]) is None

    def test_two_equally_good_homes_is_no_answer_either(self):
        assert _base_table_for_view(
            "asset_meters", ["asset_readings", "meter_readings"]) is None

    def test_a_view_the_catalogue_cannot_explain_is_no_answer(self):
        assert _base_table_for_view("contracts", []) is None


class TestTheGuardIsWiredIn:
    """The chooser is only useful if the node consults it before building and before writing."""

    @pytest.fixture
    def source(self):
        import inspect

        from src.graph.nodes import write_node
        return inspect.getsource(write_node)

    def test_the_ddl_step_asks_the_catalogue_which_targets_are_views(self, source):
        assert "get_plenum_cafm_views" in source
        # and it must ask before it builds, or the bad statement is already in the list
        assert source.index("get_plenum_cafm_views") < source.index(
            "ddl_statements = _build_migration_ddl_statements(")

    def test_a_field_with_nowhere_to_go_is_kept_rather_than_dropped(self, source):
        # raw_metadata is where this platform puts what it cannot place; the values survive.
        assert '_e["storage_strategy"] = "raw_metadata"' in source

    def test_rows_are_redirected_too_so_the_insert_does_not_fail_next(self, source):
        # The same view refuses INSERT, so fixing only the DDL would move the failure one step.
        assert "_write_views" in source and "_base_table_for_view(" in source
