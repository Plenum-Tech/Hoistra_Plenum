"""If a table's key appears in another table, that is a foreign key — whatever the format gate says.

The stated generation rule for this data: each table has one primary key, and where that key
appears in another table it is a foreign key there. One pair broke it, 30 Oct:

    work_orders.priority  P1 P2 P3 P4   over 1803 rows
    sla_matrix.priority   P1 P2 P3 P4   the primary key of a 4-row table

    format score 0.000 → the gate FAILS → the pair is never grouped
    value overlap 1.000, referential integrity 1.000 → never measured

Columns are grouped only after passing a FORMAT gate, and the cardinality guard in column_format
re-labels a near-all-distinct column `id_code` while a repeating one stays `categorical`. A small
parent code table is all-distinct by construction — it is the key — and the child repeats. So the
guard gives the two sides different formats precisely when they are a key and a reference to it,
and the value evidence that would settle it is never looked at. Priority, status codes, type
codes: the whole class of small lookup parents.

So keys are resolved from the keys themselves. For every single-column primary key, any column in
another table whose values live in that key is a foreign key to it. Guarded two ways against
coincidence: the names must be related, or the key's values must be codes rather than numbers —
because two integer columns sharing a range is a coincidence, not a reference.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from udr.column_intelligence import build_column_intelligence  # noqa: E402


def _tables():
    """work_orders referencing a 4-row sla_matrix, the reported shape."""
    prio = ["P1", "P2", "P3", "P4"]
    return {
        "sla_matrix": {
            "rows": [{"priority": p, "definition": f"def {p}", "response_hours": i}
                     for i, p in enumerate(prio, 1)],
            "columns": ["priority", "definition", "response_hours"],
            "pk": ["priority"],
        },
        "work_orders": {
            "rows": [{"work_order_id": f"WO-{i:05d}", "priority": prio[i % 4],
                      "title": f"job {i}"} for i in range(200)],
            "columns": ["work_order_id", "priority", "title"],
            "pk": ["work_order_id"],
        },
    }


def _fks(ci):
    return {(f["src_table"], f["src_column"], f["dst_table"], f["dst_column"])
            for f in ci["fk_candidates"]}


class TestTheReportedPair:
    def test_the_reference_is_found(self):
        ci = build_column_intelligence(_tables())
        assert ("work_orders", "priority", "sla_matrix", "priority") in _fks(ci)

    def test_it_shows_at_the_gate_as_a_foreign_key(self):
        """An fk_candidate alone is invisible — B20.1 renders classification rows."""
        ci = build_column_intelligence(_tables())
        row = next((r for r in ci["classification"]
                    if r.get("has_pk") == "sla_matrix.priority"), None)
        assert row is not None, "no classification row references the sla_matrix key"
        assert "foreign" in row["verdict"].lower()
        assert "work_orders.priority" in row["members"]

    def test_the_formats_really_do_disagree(self):
        """The premise: if these ever matched on format, this pass would be redundant."""
        from udr.primitives import column_format
        t = _tables()
        assert column_format(t["work_orders"]["rows"], "priority") != \
            column_format(t["sla_matrix"]["rows"], "priority")


class TestItDoesNotInventReferences:
    def test_a_column_whose_values_do_not_resolve_is_left_alone(self):
        t = _tables()
        for r in t["work_orders"]["rows"]:
            r["priority"] = "X9"            # resolves into nothing
        ci = build_column_intelligence(t)
        assert ("work_orders", "priority", "sla_matrix", "priority") not in _fks(ci)

    def test_two_numeric_columns_sharing_a_range_are_not_a_reference(self):
        """1..4 appearing in an unrelated count column is a coincidence, not a key."""
        t = {
            "levels": {"rows": [{"level_no": i, "label": f"L{i}"} for i in range(1, 5)],
                       "columns": ["level_no", "label"], "pk": ["level_no"]},
            "readings": {"rows": [{"reading_id": f"R-{i:04d}", "faults_found": (i % 4) + 1}
                                  for i in range(60)],
                         "columns": ["reading_id", "faults_found"], "pk": ["reading_id"]},
        }
        ci = build_column_intelligence(t)
        assert ("readings", "faults_found", "levels", "level_no") not in _fks(ci)

    def test_a_table_does_not_reference_its_own_key(self):
        ci = build_column_intelligence(_tables())
        assert all(f["src_table"] != f["dst_table"] for f in ci["fk_candidates"])

    def test_a_column_already_grouped_is_not_claimed_twice(self):
        ci = build_column_intelligence(_tables())
        seen = [(f["src_table"], f["src_column"]) for f in ci["fk_candidates"]]
        assert len(seen) == len(set(seen)), "a column cannot be a foreign key into two places"

    def test_nothing_happens_without_declared_keys(self):
        t = _tables()
        for meta in t.values():
            meta.pop("pk", None)
        ci = build_column_intelligence(t)
        assert ("work_orders", "priority", "sla_matrix", "priority") not in _fks(ci)
