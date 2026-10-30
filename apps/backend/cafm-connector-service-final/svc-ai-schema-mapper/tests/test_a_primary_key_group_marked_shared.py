"""Marking a primary-key group as a shared attribute has to build the lookup table.

Reported 30 Oct: alarm_id and schedule_id were marked "Shared Attribute" at the classification
gate and no lookup table appeared. The answer was not wrong and the panel was not stale — the
answer was discarded.

A group is given one of three verdicts. "Foreign Key relationship" when a member is some table's
primary key AND another is a detected foreign key; "Primary Key group" when a member is a primary
key and no member was detected as a foreign key; "Shared Attribute" when no member is a key at
all. bms_alarms.alarm_id is the primary key of bms_alarms and work_orders.linked_alarm_id was not
detected as a foreign key into it, so the group is a Primary Key group — which is exactly the
case a person looks at and says "no, that is a shared attribute, give it its own table".

The gate offers that choice on these rows. Applying it did nothing: the demotion branch was
guarded on `"foreign" in verdict`, true only for the third verdict. For a Primary Key group the
override matched no branch, so the verdict did not change, no lookup table was built, no foreign
keys were rewritten, and nothing was recorded as applied. The same guard dropped the other
direction too — a Primary Key group marked "Foreign Key relationship", which the gate also
offers, since it enables that choice whenever the row has a primary key member.

Once the table is built it already names every contributing table: one ALTER … ADD FOREIGN KEY
per member column, which is what "it should have both table names" asks for. These pin that.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from udr.column_intelligence import apply_classification_decisions  # noqa: E402


def _pk_group_row(gid: str = "G25"):
    """What the builder emits for alarm_id: one member is a PK, none is a detected FK."""
    return {
        "group_id": gid,
        "canonical_name": "alarm_id",
        "members": ["work_orders.linked_alarm_id", "bms_alarms.alarm_id"],
        "has_pk": "bms_alarms.alarm_id",
        "canonical_has_pk": "bms_alarms.alarm_id",
        "fk_column": None,
        "ri": None,
        "verdict": "Primary Key group",
    }


def _ci(rows=None, shared_tables=None, fk_candidates=None):
    return {
        "classification": rows if rows is not None else [_pk_group_row()],
        "shared_attribute_tables": shared_tables if shared_tables is not None else [],
        "fk_candidates": fk_candidates if fk_candidates is not None else [],
        "summary": {},
    }


def _tables():
    return {
        "work_orders": {"rows": [{"linked_alarm_id": "ALM-0001"}, {"linked_alarm_id": "ALM-0002"}],
                        "columns": ["linked_alarm_id"]},
        "bms_alarms": {"rows": [{"alarm_id": "ALM-0001"}, {"alarm_id": "ALM-0003"}],
                       "columns": ["alarm_id"]},
    }


class TestAPrimaryKeyGroupDemotedToShared:
    def test_the_verdict_actually_changes(self):
        ci = _ci()
        out = apply_classification_decisions(ci, _tables(), overrides={"G25": "shared"})
        assert ci["classification"][0]["verdict"] == "Shared Attribute"
        assert out["overrides_applied"]["G25"] == "pk_to_shared"

    def test_the_lookup_table_is_built(self):
        # The reported bug: no table appeared under "New tables from shared attributes".
        ci = _ci()
        out = apply_classification_decisions(ci, _tables(), overrides={"G25": "shared"})
        assert out["lookup_tables_added"] == ["alarm_id"]
        assert len(ci["shared_attribute_tables"]) == 1
        assert ci["shared_attribute_tables"][0]["from_group"] == "G25"

    def test_the_lookup_table_names_every_table_that_shares_the_column(self):
        ci = _ci()
        apply_classification_decisions(ci, _tables(), overrides={"G25": "shared"})
        entry = ci["shared_attribute_tables"][0]
        assert set(entry["source_columns"]) == {
            "work_orders.linked_alarm_id", "bms_alarms.alarm_id"}
        named = " ".join(entry["fk_ddl"])
        assert "work_orders" in named and "bms_alarms" in named, (
            "the emitted DDL must carry a foreign key from every contributing table")
        assert len(entry["fk_ddl"]) == 2

    def test_its_rows_are_the_union_of_both_columns(self):
        ci = _ci()
        apply_classification_decisions(ci, _tables(), overrides={"G25": "shared"})
        entry = ci["shared_attribute_tables"][0]
        assert entry["distinct_count"] == 3, "ALM-0001, ALM-0002 and ALM-0003"

    def test_the_old_key_stops_being_enforced(self):
        """It is no longer bms_alarms' key to own, so a foreign key into it must not survive."""
        ci = _ci(fk_candidates=[{"src_table": "work_orders", "src_column": "linked_alarm_id",
                                 "dst_table": "bms_alarms", "dst_column": "alarm_id"}])
        out = apply_classification_decisions(ci, _tables(), overrides={"G25": "shared"})
        assert out["fk_candidates_removed"] == 1
        assert ci["fk_candidates"] == []

    def test_the_row_no_longer_claims_a_primary_key_member(self):
        """Left in place, has_pk sends a later promotion down the wrong branch."""
        ci = _ci()
        apply_classification_decisions(ci, _tables(), overrides={"G25": "shared"})
        row = ci["classification"][0]
        assert not row.get("has_pk") and not row.get("canonical_has_pk")

    def test_the_summary_chips_follow(self):
        ci = _ci()
        apply_classification_decisions(ci, _tables(), overrides={"G25": "shared"})
        assert ci["summary"]["pk_groups"] == 0
        assert ci["summary"]["shared_groups"] == 1

    def test_a_group_already_shared_is_not_rebuilt_twice(self):
        ci = _ci()
        apply_classification_decisions(ci, _tables(), overrides={"G25": "shared"})
        out = apply_classification_decisions(ci, _tables(), overrides={"G25": "shared"})
        assert len(ci["shared_attribute_tables"]) == 1
        assert out["lookup_tables_added"] == []


class TestAPrimaryKeyGroupPromotedToForeignKey:
    """The gate enables this whenever the row has a PK member, so it must do something."""

    def test_the_members_point_at_the_real_primary_key(self):
        ci = _ci()
        out = apply_classification_decisions(ci, _tables(), overrides={"G25": "fk"})
        assert ci["classification"][0]["verdict"] == "Foreign Key relationship"
        assert out["overrides_applied"]["G25"] == "pk_to_fk"
        assert [(f["src_table"], f["src_column"], f["dst_table"], f["dst_column"])
                for f in ci["fk_candidates"]] == [
            ("work_orders", "linked_alarm_id", "bms_alarms", "alarm_id")]

    def test_the_key_member_is_not_made_a_foreign_key_onto_itself(self):
        ci = _ci()
        apply_classification_decisions(ci, _tables(), overrides={"G25": "fk"})
        assert all(f["src_table"] != "bms_alarms" for f in ci["fk_candidates"])


@pytest.mark.parametrize("target", ["shared", "fk"])
def test_no_answer_at_this_gate_is_silently_discarded(target):
    """The shape of the bug, independent of these two verdicts: an override always applies."""
    ci = _ci()
    out = apply_classification_decisions(ci, _tables(), overrides={"G25": target})
    assert out["overrides_applied"], (
        f"marking a Primary Key group '{target}' was accepted by the gate and then dropped")
