"""Feature 7.6 AC6 + 7.7 AC5 — every column classified (PK/FK/Shared) AND assigned a
destination before the UDR is finalised; an unclassified/unassigned column blocks.
Run: python tests/test_udr_coverage.py"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from udr.primitives import classify_columns  # noqa: E402
from udr.validation import run_coverage_check  # noqa: E402


def check(name, cond):
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        raise AssertionError(name)


# ── classify_columns now classifies EVERY column, including singletons (7.6 AC6) ──
tables = {
    "assets": {
        "rows": [{"id": "1", "note": "alpha"}, {"id": "2", "note": "beta"}],
        "columns": ["id", "note"],   # 'note' is a singleton — not similar to anything
        "pk": ["id"],
    },
}
cls = classify_columns(tables, {"assets": ["id"]})
check("PK column classified PK", cls[("assets", "id")]["classification"] == "PK")
check("singleton column still classified (Shared)", cls[("assets", "note")]["classification"] == "SHARED_ATTRIBUTE")
check("every column has a classification", all(("assets", c) in cls for c in ["id", "note"]))

# ── coverage check: complete graph passes ──
full_graph = {
    "columns": [
        {"source_table": "assets", "column": "id", "classification": "PK", "dest_udr_table": "assets"},
        {"source_table": "assets", "column": "note", "classification": "SHARED_ATTRIBUTE", "dest_udr_table": "assets"},
    ]
}
ok = run_coverage_check(tables, full_graph)
check("complete coverage passes", ok["complete"] is True and ok["blocks_udr"] is False)
check("100% classified + assigned", ok["classified_pct"] == 1.0 and ok["assigned_pct"] == 1.0)

# ── unclassified column blocks (7.6 AC6) ──
g_unclassified = {
    "columns": [
        {"source_table": "assets", "column": "id", "classification": "PK", "dest_udr_table": "assets"},
        {"source_table": "assets", "column": "note", "classification": None, "dest_udr_table": "assets"},
    ]
}
r1 = run_coverage_check(tables, g_unclassified)
check("unclassified column blocks UDR", r1["blocks_udr"] is True)
check("unclassified column listed", r1["unclassified"] == [{"table": "assets", "column": "note"}])

# ── unassigned column blocks (7.7 AC5) ──
g_unassigned = {
    "columns": [
        {"source_table": "assets", "column": "id", "classification": "PK", "dest_udr_table": "assets"},
        {"source_table": "assets", "column": "note", "classification": "SHARED_ATTRIBUTE", "dest_udr_table": ""},
    ]
}
r2 = run_coverage_check(tables, g_unassigned)
check("unassigned column blocks UDR", r2["blocks_udr"] is True)
check("unassigned column listed", r2["unassigned"] == [{"table": "assets", "column": "note"}])

# ── a column missing entirely from the graph counts as both unclassified + unassigned ──
g_missing = {"columns": [{"source_table": "assets", "column": "id", "classification": "PK", "dest_udr_table": "assets"}]}
r3 = run_coverage_check(tables, g_missing)
check("missing column blocks", r3["blocks_udr"] is True and r3["classified"] == 1 and r3["assigned"] == 1)

print("\nALL TESTS PASSED")
