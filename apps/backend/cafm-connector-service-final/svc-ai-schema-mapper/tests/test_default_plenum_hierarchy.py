"""Tests for single-table Plenum default hierarchy column hints."""

import importlib.util
from pathlib import Path

_mod_path = (
    Path(__file__).resolve().parents[1] / "src" / "hierarchy" / "default_plenum_hierarchy.py"
)
_spec = importlib.util.spec_from_file_location("default_plenum_hierarchy", _mod_path)
assert _spec and _spec.loader
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
_import_table_column_hints = _mod._import_table_column_hints


def _location_hint_columns(columns: list[str]) -> set[str]:
    hints = _import_table_column_hints("data", columns)
    return {h["source_column"] for h in hints if h.get("target_table") == "locations"}


def test_location_id_suffix_matches_intentional_columns():
    assert _location_hint_columns(
        ["location_id", "parent_location_id", "floor_location_id", "locationId", "parentLocationId"]
    ) == {
        "location_id",
        "parent_location_id",
        "floor_location_id",
        "locationId",
        "parentLocationId",
    }


def test_location_id_does_not_match_allocation_substring_false_positive():
    assert _location_hint_columns(["allocation_id", "allocationid"]) == set()


def test_location_id_does_not_match_negated_location_column_names():
    assert _location_hint_columns(["not_a_location_id", "non_location_id"]) == set()


def test_site_id_suffix_still_matches_without_location_substring_bleed():
    hints = _import_table_column_hints("data", ["site_id", "parent_site_id"])
    site_cols = {h["source_column"] for h in hints if h.get("target_table") == "sites"}
    assert site_cols == {"site_id", "parent_site_id"}
