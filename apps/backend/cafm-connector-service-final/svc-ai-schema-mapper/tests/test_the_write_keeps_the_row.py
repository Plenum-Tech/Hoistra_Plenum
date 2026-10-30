"""One bad cell must not cost the row, and a sheet's own keys must survive the write.

A Vasiliki House run's write preview, 30 Oct, promised 18,000 rows and could write almost none:

    work_orders   id   1,803  WO-00001  → uuid      "written empty"
    work_orders               1,803 rows           "id is required and the file has no value"

The writer was refusing rows for the absence of a value it had removed itself, one step earlier.
It kept the sheet's id, skipped minting a uuid because an id was present, then dropped that id
as unfit for a uuid column — and failed the row for having none. 2,270 work-order lines, 1,803
work orders, 350 energy reports, 137 assets and every vendor and contract went the same way.

Four smaller faults took whole rows for one cell: an "Office" the building enum does not list,
prose in a json column, three columns too narrow for their values, and bookkeeping timestamps
the source could not have carried. And every child lost its parent, because a workbook keys its
rows to itself (floors.building_id = BLD-CDF-0001) and those parents are made by the same run.
"""
from __future__ import annotations

import logging
import os
import re
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

from src.graph.nodes.write_node import (  # noqa: E402
    _COERCE_TYPE_MISMATCH as MISMATCH,
    _coerce_value_for_db_type as coerce,
    _is_uuid_like,
    _system_default_for_db_type as system_default,
)

NODE = os.path.join(os.path.dirname(__file__), "..", "src", "graph", "nodes", "write_node.py")


def _source() -> str:
    with open(NODE, encoding="utf-8") as fh:
        return fh.read()


class TestTheIdIsMintedAfterTheUnfitOneIsDropped:
    def test_a_work_order_code_still_does_not_fit_a_uuid(self):
        assert coerce("WO-00001", "uuid") is MISMATCH

    def test_the_mint_runs_after_the_coercion_not_before(self):
        src = _source()
        coerce_at = src.index('_coerced = _coerce_value_for_db_type(')
        mint_at = src.index('filtered["id"] = str(uuid.uuid4())')
        assert coerce_at < mint_at, (
            "the uuid is minted before the unfit id is dropped, so every row with a text key "
            "fails as 'id is required'")

    def test_the_sheet_key_is_parked_where_the_table_has_a_column_for_it(self):
        from src.graph.nodes.write_node import _SOURCE_KEY_COLUMNS
        assert _SOURCE_KEY_COLUMNS[("buildings", "building_id")] == "source_building_id"
        assert 'filtered[_src_col] = str(filtered[k])' in _source()


class TestAChildFindsTheParentThisRunCreated:
    def test_the_register_exists_and_is_consulted_before_coercion(self):
        src = _source()
        assert "_run_key_ids" in src
        lookup = src.index("_run_key_ids.get(")
        coerce_at = src.index('_coerced = _coerce_value_for_db_type(')
        assert lookup < coerce_at, "the swap must happen before uuid coercion rejects the key"

    def test_a_key_claimed_twice_resolves_to_nothing(self):
        src = _source()
        assert "None if _own_key in _run_key_ids else" in src, (
            "an ambiguous key must map to None; a floor under the wrong building is worse than "
            "a floor under none")

    @pytest.mark.parametrize("text,expected", [
        ("BLD-CDF-0001", False), ("LVL-B", False), ("AST-0001", False),
        ("3f2a9c1e-5b7d-4e8a-9c21-7d4e5f6a8b90", True),
    ])
    def test_only_a_non_uuid_key_needs_registering(self, text, expected):
        assert _is_uuid_like(text) is expected


class TestOneBadCellDoesNotCostTheRow:
    def test_a_value_the_enum_does_not_list_is_set_aside_not_fatal(self):
        labels = {"office": "office", "retail": "retail"}
        assert coerce("Office", "USER-DEFINED", enum_labels=labels) == "office"   # case-insensitive
        assert coerce("OFFICE", "USER-DEFINED", enum_labels=labels) == "office"
        assert coerce("Hospital", "USER-DEFINED", enum_labels=labels) is MISMATCH

    @pytest.mark.parametrize("length,value", [(20, "A" * 40), (50, "B" * 80), (64, "C" * 100)])
    def test_too_long_is_trimmed_rather_than_refused(self, length, value):
        out = coerce(value, "character varying", max_len=length)
        assert out is not MISMATCH and len(out) == length

    def test_a_value_that_fits_is_untouched(self):
        assert coerce("Annual gas safety", "character varying", max_len=64) == "Annual gas safety"

    def test_prose_in_a_json_column_is_stored_as_json(self):
        out = coerce("field survey, 2019", "jsonb")
        assert out == '"field survey, 2019"'
        import json
        assert json.loads(out) == "field survey, 2019"

    def test_real_json_passes_through_unchanged(self):
        assert coerce('{"source":"survey"}', "jsonb") == '{"source":"survey"}'


class TestTimestampsTheSourceCouldNotCarry:
    @pytest.mark.parametrize("col", ["created_at", "updated_at", "inserted_at"])
    def test_bookkeeping_timestamps_are_filled(self, col):
        from datetime import datetime
        out = system_default(col, "timestamp without time zone", None)
        assert isinstance(out, datetime)

    @pytest.mark.parametrize("col", ["reading_at", "period_start", "report_month"])
    def test_a_business_date_is_still_refused(self, col):
        from src.graph.nodes.write_node import _NO_SYSTEM_DEFAULT
        assert system_default(col, "timestamp without time zone", None) is _NO_SYSTEM_DEFAULT, (
            "inventing when a meter was read would be a fiction, not a default")


def test_the_metadata_the_fixes_need_is_actually_selected():
    """Width and enum type have to come back from information_schema or none of this fires."""
    src = _source()
    assert src.count("character_maximum_length") >= 2, "both column queries need the width"
    assert src.count("udt_name") >= 2, "both column queries need the enum type name"
    assert "db_col_maxlen" in src and "db_enum_labels" in src
