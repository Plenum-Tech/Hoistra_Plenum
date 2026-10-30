"""A lookup table the gate promises must be created, not left in an artifact.

Asked for 30 Oct: "every new table creation like shared table column need to be continue next
steps". The classification gate shows the tables a run will create and their DDL; the write then
created only the ones a PERSON had re-classified. Everything the classifier found on its own —
shared_statuses, shared_currencies, shared_data_provenances — was, in the writer's own words,
"left to the SQL artifact": a downloadable script that the schema-aligned write (every run with
a building, and every engine run) never applies. Promised at step 3, absent from the database.

The lookup ENTRY is the decision. synthesize_shared_attribute_table returns None for anything
that should not be normalised — a yes/no flag, a date, contact data, an entity's alternate key —
so a group with an entry is one that was asked for, by the classifier or by the person at the
gate, and a group without one was refused on purpose.

Keying on the entry also closes the hole in the other direction: a boolean group demoted by hand
has no entry, and the old condition (human_override == "fk_to_shared") would still have built it
a table whose primary key is "true" and "false".
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

from src.graph.nodes.write_node import _lookup_groups_to_write  # noqa: E402


def _ci(rows, tables):
    return {"classification": rows, "shared_attribute_tables": tables}


def _row(gid, **kw):
    return {"group_id": gid, "canonical_name": kw.pop("name", "x"),
            "members": kw.pop("members", ["a.x", "b.x"]),
            "verdict": kw.pop("verdict", "Shared Attribute"), **kw}


def _table(gid, name):
    return {"from_group": gid, "table_name": name, "pk_column": name.replace("shared_", "")}


def _ids(out):
    return [r["group_id"] for r in out]


class TestWhatTheWriteNowCreates:
    def test_a_shared_attribute_nobody_touched_is_created(self):
        """The reported case: found by the classifier, shown at the gate, never written."""
        ci = _ci([_row("G8", name="status")], [_table("G8", "shared_statuses")])
        assert _ids(_lookup_groups_to_write(ci)) == ["G8"]

    def test_all_three_of_the_vasiliki_tables(self):
        rows = [_row("G8", name="status"), _row("G9", name="data_provenance"),
                _row("G14", name="currency")]
        tables = [_table("G8", "shared_statuses"), _table("G9", "shared_data_provenances"),
                  _table("G14", "shared_currencies")]
        assert _ids(_lookup_groups_to_write(_ci(rows, tables))) == ["G8", "G9", "G14"]

    def test_a_demoted_foreign_key_is_still_created(self):
        ci = _ci([_row("G25", name="alarm_id", human_override="fk_to_shared")],
                 [_table("G25", "shared_alarms")])
        assert _ids(_lookup_groups_to_write(ci)) == ["G25"]

    def test_a_promoted_shared_attribute_keeps_its_reference_table(self):
        ci = _ci([_row("G1", name="asset_type", human_override="shared_to_fk",
                       verdict="Foreign Key relationship")],
                 [_table("G1", "shared_asset_types")])
        assert _ids(_lookup_groups_to_write(ci)) == ["G1"]


class TestWhatItStillLeavesAlone:
    def test_a_group_with_no_table_is_not_invented(self):
        """A boolean group: synthesis refused it, so there is nothing to create."""
        ci = _ci([_row("G24", name="boolean_attr_G24")], [])
        assert _lookup_groups_to_write(ci) == []

    def test_a_boolean_group_demoted_by_hand_still_gets_nothing(self):
        # The old rule keyed on human_override and would have built a true/false table.
        ci = _ci([_row("G24", name="flag", human_override="fk_to_shared")], [])
        assert _lookup_groups_to_write(ci) == [], (
            "a two-row table whose primary key is 'true' and 'false' is never what anyone meant")

    def test_a_plain_foreign_key_group_is_not_a_lookup_table(self):
        ci = _ci([_row("G5", name="contract_id", verdict="Foreign Key relationship")], [])
        assert _lookup_groups_to_write(ci) == []

    def test_a_table_whose_group_is_gone_is_not_written_from_nothing(self):
        """A rejected group's row is removed; its stale entry must not resurrect it."""
        ci = _ci([], [_table("G8", "shared_statuses")])
        assert _lookup_groups_to_write(ci) == []


class TestItSurvivesNonsense:
    def test_no_column_intelligence(self):
        for bad in (None, {}, [], "nope", 7):
            assert _lookup_groups_to_write(bad) == []

    def test_rows_and_tables_that_are_not_dicts(self):
        ci = {"classification": ["x", None, _row("G8")], "shared_attribute_tables": [None, 3, _table("G8", "s")]}
        assert _ids(_lookup_groups_to_write(ci)) == ["G8"]
