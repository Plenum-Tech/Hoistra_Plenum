"""Promoting a shared attribute to a foreign key has to do something.

At the classification gate a reviewer can switch a group's verdict. Demoting a foreign key to
a shared attribute worked: the enforcement is dropped and a reference table is built from the
run's own values, with foreign keys pointing at it. Promoting the other way did not.

The promote branch only added foreign keys when the row carried ``has_pk``, a member column
that is some table's primary key. A group is classified "Shared Attribute" precisely when no
member is a primary key, and ``has_pk`` is set only when one exists, so that condition was
unreachable for every row the gate offers the promotion on. What the branch did reach was the
line above it, which deleted the group's reference table. A reviewer who promoted a shared
attribute therefore lost the lookup table and the foreign keys it would have created, and got
a changed label in return.

The reference table is the thing to point at. These pin that: the promotion builds it when it
is not already there, references it from every member column, and never leaves the group with
neither a lookup table nor a foreign key.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from udr.column_intelligence import apply_classification_decisions  # noqa: E402


def _shared_row(gid: str = "G1"):
    """What the builder emits for a column shared by two tables and owned by neither."""
    return {
        "group_id": gid,
        "canonical_name": "asset_type",
        "members": ["assets.asset_type", "work_orders.asset_type"],
        "has_pk": None,            # by construction: a shared group has no PK member
        "canonical_has_pk": None,
        "fk_column": None,
        "ri": None,
        "verdict": "Shared Attribute",
    }


def _ci(rows=None, shared_tables=None, fk_candidates=None):
    return {
        "classification": rows if rows is not None else [_shared_row()],
        "shared_attribute_tables": shared_tables if shared_tables is not None else [],
        "fk_candidates": fk_candidates if fk_candidates is not None else [],
        "summary": {},
    }


def _tables():
    return {
        "assets": {"rows": [{"asset_type": "Chiller"}, {"asset_type": "AHU"}],
                   "columns": ["asset_type"]},
        "work_orders": {"rows": [{"asset_type": "Chiller"}], "columns": ["asset_type"]},
    }


class TestPromotingToAForeignKey:
    def test_the_verdict_changes_and_is_marked_as_the_reviewers(self):
        ci = _ci()
        out = apply_classification_decisions(ci, _tables(), overrides={"G1": "fk"})
        assert ci["classification"][0]["verdict"] == "Foreign Key relationship"
        assert ci["classification"][0]["human_override"] == "shared_to_fk"
        assert out["overrides_applied"]["G1"] == "shared_to_fk"

    def test_every_member_column_ends_up_referencing_something(self):
        # This is the bug: before, no foreign key was ever added on this path.
        ci = _ci()
        out = apply_classification_decisions(ci, _tables(), overrides={"G1": "fk"})
        assert out["fk_candidates_added"] == 2
        srcs = {(f["src_table"], f["src_column"]) for f in ci["fk_candidates"]}
        assert srcs == {("assets", "asset_type"), ("work_orders", "asset_type")}
        dsts = {(f["dst_table"], f["dst_column"]) for f in ci["fk_candidates"]}
        assert len(dsts) == 1, "every member points at the one reference table"
        assert all(f["confirmed"] for f in ci["fk_candidates"])

    def test_the_reference_table_is_built_when_it_is_not_already_there(self):
        ci = _ci()
        apply_classification_decisions(ci, _tables(), overrides={"G1": "fk"})
        assert len(ci["shared_attribute_tables"]) == 1
        entry = ci["shared_attribute_tables"][0]
        assert entry["from_group"] == "G1"
        dst = {(f["dst_table"], f["dst_column"]) for f in ci["fk_candidates"]}
        assert dst == {(entry["table_name"], entry["pk_column"])}

    def test_a_reference_table_already_built_is_reused_not_duplicated(self):
        # A reviewer who demotes a foreign key and then changes their mind: the reference table
        # the demotion built is the one the promotion points at, not a second copy of it.
        row = _shared_row()
        row.update(verdict="Foreign Key relationship", has_pk="assets.asset_type")
        ci = _ci(rows=[row])
        apply_classification_decisions(ci, _tables(), overrides={"G1": "shared"})
        assert len(ci["shared_attribute_tables"]) == 1
        built = dict(ci["shared_attribute_tables"][0])
        ci["classification"][0]["has_pk"] = None          # the demotion made it a shared group
        ci["classification"][0]["canonical_has_pk"] = None
        apply_classification_decisions(ci, _tables(), overrides={"G1": "fk"})
        assert len(ci["shared_attribute_tables"]) == 1
        assert ci["shared_attribute_tables"][0]["table_name"] == built["table_name"]
        assert {(f["dst_table"], f["dst_column"]) for f in ci["fk_candidates"]} == {
            (built["table_name"], built["pk_column"])}

    def test_the_promotion_never_leaves_the_group_with_nothing(self):
        # The old behaviour: lookup table deleted, no foreign key added, label changed.
        ci = _ci()
        apply_classification_decisions(ci, _tables(), overrides={"G1": "fk"})
        assert ci["shared_attribute_tables"] or ci["fk_candidates"]
        assert ci["fk_candidates"], "a promotion with no enforcement is the bug being fixed"

    def test_a_member_that_is_a_real_primary_key_is_still_referenced_directly(self):
        # The path that did work stays as it was: no lookup table, members point at the PK.
        row = _shared_row()
        row["has_pk"] = "assets.asset_type"
        ci = _ci(rows=[row])
        apply_classification_decisions(ci, _tables(), overrides={"G1": "fk"})
        assert ci["shared_attribute_tables"] == []
        assert [(f["dst_table"], f["dst_column"]) for f in ci["fk_candidates"]] == [
            ("assets", "asset_type")]
        assert [(f["src_table"], f["src_column"]) for f in ci["fk_candidates"]] == [
            ("work_orders", "asset_type")]

    def test_the_summary_chips_follow_the_new_verdict(self):
        ci = _ci()
        apply_classification_decisions(ci, _tables(), overrides={"G1": "fk"})
        assert ci["summary"]["fk_groups"] == 1
        assert ci["summary"]["shared_groups"] == 0

    @pytest.mark.parametrize("tables", [None, {}])
    def test_without_the_run_data_the_verdict_still_changes(self, tables):
        # The reference table needs values to build; the reviewer's decision is recorded anyway.
        ci = _ci()
        out = apply_classification_decisions(ci, tables, overrides={"G1": "fk"})
        assert ci["classification"][0]["verdict"] == "Foreign Key relationship"
        assert out["overrides_applied"]["G1"] == "shared_to_fk"


class TestDemotingStillWorks:
    def test_a_foreign_key_demoted_still_gets_its_reference_table(self):
        row = _shared_row()
        row.update(verdict="Foreign Key relationship", has_pk="assets.asset_type")
        ci = _ci(rows=[row], fk_candidates=[
            {"src_table": "work_orders", "src_column": "asset_type",
             "dst_table": "assets", "dst_column": "asset_type"}])
        out = apply_classification_decisions(ci, _tables(), overrides={"G1": "shared"})
        assert ci["classification"][0]["verdict"] == "Shared Attribute"
        assert out["fk_candidates_removed"] == 1
        assert len(ci["shared_attribute_tables"]) == 1
