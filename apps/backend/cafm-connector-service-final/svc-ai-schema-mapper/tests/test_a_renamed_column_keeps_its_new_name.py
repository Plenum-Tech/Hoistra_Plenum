"""A column re-targeted at the mapping gate has to come out of preprocessing renamed.

The gate hands back {source_table: {source_field: destination}}. Those answers become a rename
map, and the preprocessor reads that map with an exact key: ``mapping_dict_by_table.get(table_name)``
against the name the sheet actually has. The map was keyed by the name the gate sent instead, so
a spelling that differed only in case matched nothing and the rename vanished without a word.

It did not vanish every time, which is why it survived. The loop that re-targets tier mappings
registers the rename a second time under the sheet's real name, so any field a tier mapping
already named came out right. What was left was the field no tier mapping named, which is
exactly the field someone opens the gate to re-target in the first place.
"""
from __future__ import annotations

import logging
import os
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

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.graph.nodes.preprocess_node import (  # noqa: E402
    _apply_column_dest_overrides,
    preprocess_tables,
)

SHEET = "WorkOrders"


def _parsed():
    return {SHEET: [{"WO Ref": "W1", "Cost": "10"}, {"WO Ref": "W2", "Cost": "20"}]}


def _columns_after(state, parsed):
    """The rename map the node builds, applied the way the node applies it."""
    renames = _apply_column_dest_overrides(state)
    mapping: dict[str, dict[str, str]] = {}
    for table, cols in renames.items():
        mapping.setdefault(table, {}).update(cols)
    out = preprocess_tables(parsed, mapping, {})
    return sorted(out.cleaned_tables[SHEET][0].keys())


@pytest.mark.parametrize("gate_key", [SHEET, "workorders", "WORKORDERS"])
@pytest.mark.parametrize("tier_mapping", [True, False], ids=["mapped", "unmapped"])
def test_the_rename_lands_however_the_gate_spelled_the_sheet(gate_key, tier_mapping):
    parsed = _parsed()
    state = {
        "parsed_tables": parsed,
        "column_dest_overrides": {gate_key: {"WO Ref": "work_order_id"}},
        "tier1_mappings_by_table": (
            {SHEET: [{"source_field": "WO Ref", "target_field": "something_else"}]}
            if tier_mapping else {}),
    }
    cols = _columns_after(state, parsed)
    assert "work_order_id" in cols, f"rename lost for gate key {gate_key!r}"
    assert "WO Ref" not in cols
    assert "Cost" in cols, "an untouched column stays as it is"


def test_a_new_column_lands_under_its_snake_cased_name():
    parsed = _parsed()
    state = {"parsed_tables": parsed,
             "column_dest_overrides": {"workorders": {"WO Ref": "__new__"}}}
    assert "wo_ref" in _columns_after(state, parsed)


def test_the_tier_mapping_is_re_targeted_as_well_as_the_data():
    # Both halves matter: the data carries the new name and the mapping agrees with it.
    parsed = _parsed()
    tier = {SHEET: [{"source_field": "WO Ref", "target_field": "something_else"}]}
    state = {"parsed_tables": parsed, "tier1_mappings_by_table": tier,
             "column_dest_overrides": {"workorders": {"WO Ref": "work_order_id"}}}
    _apply_column_dest_overrides(state)
    assert tier[SHEET][0]["target_field"] == "work_order_id"
    assert tier[SHEET][0]["column_mapping_override"] is True


def test_a_sheet_the_run_does_not_have_is_left_alone():
    parsed = _parsed()
    state = {"parsed_tables": parsed,
             "column_dest_overrides": {"Invoices": {"Ref": "invoice_ref"}}}
    renames = _apply_column_dest_overrides(state)
    assert renames == {"Invoices": {"Ref": "invoice_ref"}}
    assert _columns_after(state, parsed) == ["Cost", "WO Ref"]


def test_nothing_to_apply_is_an_empty_map():
    assert _apply_column_dest_overrides({"parsed_tables": _parsed()}) == {}
    assert _apply_column_dest_overrides({"column_dest_overrides": {}}) == {}
