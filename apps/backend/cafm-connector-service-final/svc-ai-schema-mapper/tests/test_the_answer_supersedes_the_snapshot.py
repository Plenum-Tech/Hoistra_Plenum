"""The page must read the report as it stands AFTER the gate is answered.

The classification gate's payload is built before the person answers and cleared on resume, so
the only post-answer source the page has is the completed-step record. Two faults in a row kept
that from working, and both looked identical from the outside — a lookup table the run had in
fact created, missing from the panel:

  1. the record's write raised on a missing argument and `except Exception` ate it;
  2. once it wrote, it published `shared_attribute_tables`, a key nothing reads.

The status endpoint builds the report with `_udr_node_output_field(job, "udr_column_intelligence")`,
which walks node_logs and keeps the LAST entry carrying that key. So the post-answer record has
to carry the whole report under that same key — then it supersedes the step-3 snapshot, and the
B22.1 panel renders the tables the answer created, with the DDL and values it needs.
"""
from __future__ import annotations

import ast
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

NODE = os.path.join(os.path.dirname(__file__), "..", "src", "graph", "nodes",
                    "grouping_review_node.py")
APP = os.path.join(os.path.dirname(__file__), "..", "src", "app.py")


def _read(p):
    with open(p, encoding="utf-8") as fh:
        return fh.read()


class TestTheRecordUsesTheKeyTheEndpointReads:
    def test_the_confirmation_publishes_the_whole_report(self):
        src = _read(NODE)
        at = src.index('"Classification Confirmed"')
        block = src[at:at + 2000]
        assert '"udr_column_intelligence": ci' in block, (
            "the post-answer record must carry the report under the key the status endpoint "
            "reads, or the page keeps showing the snapshot taken before the answer")

    def test_the_endpoint_still_reads_that_key_and_keeps_the_last_one(self):
        """If either half of this contract moves, the record stops superseding the snapshot."""
        src = _read(APP)
        assert '_udr_node_output_field(job, "udr_column_intelligence")' in src
        fn = src[src.index("def _udr_node_output_field"):]
        fn = fn[:fn.index("\n\n\n")]
        assert "found = out[key]" in fn and "return found" in fn, (
            "the endpoint no longer keeps the LAST matching entry, so a later record would not "
            "override the earlier snapshot")


class TestTheLastEntryIsTheOneServed:
    def test_a_later_record_supersedes_an_earlier_one(self):
        """Exercise the real selector with the two entries this run writes."""
        from src.app import _udr_node_output_field

        class Job:
            node_logs = [
                {"node_name": "Pre-Semantic Review",
                 "output": {"udr_column_intelligence": {"shared_attribute_tables": [{"table_name": "status"}]}}},
                {"node_name": "Classification Confirmed",
                 "output": {"udr_column_intelligence": {"shared_attribute_tables": [
                     {"table_name": "status"}, {"table_name": "alarm_id"}]}}},
            ]

        served = _udr_node_output_field(Job(), "udr_column_intelligence")
        assert [t["table_name"] for t in served["shared_attribute_tables"]] == ["status", "alarm_id"]

    def test_without_the_later_record_the_snapshot_is_what_shows(self):
        """The bug, pinned: publishing under any other key leaves the stale list served."""
        from src.app import _udr_node_output_field

        class Job:
            node_logs = [
                {"output": {"udr_column_intelligence": {"shared_attribute_tables": [{"table_name": "status"}]}}},
                {"output": {"shared_attribute_tables": [{"table_name": "alarm_id"}]}},
            ]

        served = _udr_node_output_field(Job(), "udr_column_intelligence")
        assert [t["table_name"] for t in served["shared_attribute_tables"]] == ["status"]


def test_the_record_still_carries_the_summary_of_what_changed():
    block = _read(NODE)
    at = block.index('"Classification Confirmed"')
    block = block[at:at + 2000]
    for key in ("lookup_tables_added", "overrides_applied", "rejected_applied"):
        assert f'"{key}"' in block, f"{key} is what tells us an answer was applied at all"
