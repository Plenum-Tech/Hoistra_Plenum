"""A primary-key group marked shared is refused, and says so; marked a foreign key, it links.

9 Nov 2026: the half of this file that built shared_alarms when alarm_id was marked shared is
reversed -- see TestAPrimaryKeyGroupIsNeverMadeShared. The history below is why an answer at this
gate must never be silently dropped, which still holds: it is now refused, with a reason.

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


class TestAPrimaryKeyGroupIsNeverMadeShared:
    """Reversed 9 Nov 2026. Building shared_alarms beside bms_alarms gave two tables keyed on the
    same alarm ids (run 5ea53444: shared_buildings and shared_vendors beside buildings and
    vendors). A column that is already some table's key can only link to that table."""

    def test_the_answer_is_refused_and_says_why(self):
        ci = _ci()
        out = apply_classification_decisions(ci, _tables(), overrides={"G25": "shared"})
        row = ci["classification"][0]
        assert row["override_refused"] == "already_a_key"
        assert "bms_alarms.alarm_id" in out["overrides_refused"]["G25"]
        assert "G25" not in out["overrides_applied"]

    def test_the_verdict_and_the_key_stay(self):
        ci = _ci()
        apply_classification_decisions(ci, _tables(), overrides={"G25": "shared"})
        row = ci["classification"][0]
        assert row["verdict"] == "Primary Key group"
        assert row["has_pk"] == row["canonical_has_pk"] == "bms_alarms.alarm_id"
        assert "human_override" not in row

    def test_no_second_table_is_planned(self):
        ci = _ci()
        out = apply_classification_decisions(ci, _tables(), overrides={"G25": "shared"})
        assert ci["shared_attribute_tables"] == []
        assert out["lookup_tables_added"] == []

    def test_the_existing_foreign_key_survives(self):
        fk = {"src_table": "work_orders", "src_column": "linked_alarm_id",
              "dst_table": "bms_alarms", "dst_column": "alarm_id"}
        ci = _ci(fk_candidates=[dict(fk)])
        apply_classification_decisions(ci, _tables(), overrides={"G25": "shared"})
        assert ci["fk_candidates"] == [fk]

    def test_a_primary_key_group_with_no_member_named_is_still_refused(self):
        """The verdict alone is enough: a Primary Key group is somebody's key."""
        ci = _ci(rows=[dict(_pk_group_row(), has_pk=None, canonical_has_pk=None)])
        out = apply_classification_decisions(ci, _tables(), overrides={"G25": "shared"})
        assert "G25" in out["overrides_refused"]
        assert ci["shared_attribute_tables"] == []


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
    """The shape of the 30 Oct bug: an answer is either applied or refused with a reason."""
    ci = _ci()
    out = apply_classification_decisions(ci, _tables(), overrides={"G25": target})
    assert out["overrides_applied"] or out.get("overrides_refused"), (
        f"marking a Primary Key group '{target}' was accepted by the gate and then dropped")
