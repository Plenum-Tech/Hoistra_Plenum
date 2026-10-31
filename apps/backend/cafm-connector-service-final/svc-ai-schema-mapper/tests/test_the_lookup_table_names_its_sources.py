"""A lookup table should say which columns it was made from.

Asked for on 30 Oct: when columns are marked a shared attribute, the lookup table built from
them should carry the names of every table sharing that column.

Most of that was already true and only needed finding: the entry lists `source_columns`, and the
emitted DDL carries one ALTER … ADD FOREIGN KEY per member, so every contributing table is named
there and in the gate's "FK rewrites" column. What the *table* carried was nothing — a bare
`CREATE TABLE alarm_id (alarm_id TEXT PRIMARY KEY)`. Read in the database a month later, it gives
no clue where its rows came from or which columns are now constrained to it.

So the DDL now carries a COMMENT ON TABLE naming the group and its source columns. That survives
into the database, where the panel that explains it does not.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from udr.column_intelligence import synthesize_shared_attribute_table  # noqa: E402

MEMBERS = [("work_orders", "linked_alarm_id"), ("bms_alarms", "alarm_id")]


def _tables():
    return {
        "work_orders": {"rows": [{"linked_alarm_id": "ALM-0001"}, {"linked_alarm_id": "ALM-0002"}],
                        "columns": ["linked_alarm_id"]},
        "bms_alarms": {"rows": [{"alarm_id": "ALM-0001"}, {"alarm_id": "ALM-0003"}],
                       "columns": ["alarm_id"]},
    }


def _entry(canonical="alarm_id", gid="G25", members=None, tables=None):
    return synthesize_shared_attribute_table(
        tables if tables is not None else _tables(),
        canonical_name=canonical, group_id=gid,
        member_pairs=members if members is not None else MEMBERS,
        members_display=[f"{t}.{c}" for (t, c) in (members if members is not None else MEMBERS)])


class TestTheTableRecordsWhereItCameFrom:
    def test_the_comment_names_every_contributing_column(self):
        ddl = _entry()["create_ddl"]
        assert "COMMENT ON TABLE plenum_cafm.shared_alarms IS" in ddl
        assert "work_orders.linked_alarm_id" in ddl
        assert "bms_alarms.alarm_id" in ddl

    def test_the_comment_names_the_group_it_came_from(self):
        assert "G25" in _entry()["create_ddl"]

    def test_the_comment_is_applied_with_the_rest_of_the_block(self):
        """The panel shows create_ddl; the database gets ddl_block. Both must carry it."""
        entry = _entry()
        assert "COMMENT ON TABLE" in entry["ddl_block"]

    def test_the_create_statement_itself_is_unchanged(self):
        ddl = _entry()["create_ddl"]
        assert ddl.startswith("CREATE TABLE IF NOT EXISTS plenum_cafm.shared_alarms (")
        assert "id UUID PRIMARY KEY DEFAULT gen_random_uuid()" in ddl
        assert "alarm_id TEXT NOT NULL" in ddl
        assert "UNIQUE (organization_id, alarm_id)" in ddl

    def test_a_quote_in_a_column_name_cannot_break_out_of_the_comment(self):
        tables = {"a'b": {"rows": [{"c'd": "X-1"}, {"c'd": "X-2"}], "columns": ["c'd"]}}
        ddl = _entry(canonical="q_attr", members=[("a'b", "c'd")], tables=tables)["create_ddl"]
        body = ddl.split("IS ", 1)[1]
        assert body.count("'") % 2 == 0, "an odd number of quotes means the literal is unbalanced"
        assert "a''b.c''d" in body


class TestWhatWasAlreadyThere:
    def test_no_foreign_key_statement_is_emitted(self):
        """The unique backing the table is (organization_id, attribute); a composite unique
        cannot back a single-column FK, and these statements were never applied to anything."""
        assert _entry()["fk_ddl"] == []

    def test_the_entry_lists_the_source_columns_for_the_panel(self):
        assert _entry()["source_columns"] == [
            "work_orders.linked_alarm_id", "bms_alarms.alarm_id"]

    def test_the_fk_rewrites_read_source_to_lookup(self):
        assert _entry()["fk_rewrites"] == [
            {"source": "work_orders.linked_alarm_id", "references": "shared_alarms.alarm_id"},
            {"source": "bms_alarms.alarm_id", "references": "shared_alarms.alarm_id"},
        ]
