"""Tests for value-centric column analysis (matchers/value_signature.py): merge columns by
DATA not name, and classify a column's meaning from its values. Pure.
Run: python tests/test_value_signature.py"""
import importlib
import os
import sys
import types

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
sys.path.insert(0, _SRC)
_pkg = types.ModuleType("matchers")
_pkg.__path__ = [os.path.join(_SRC, "matchers")]
sys.modules["matchers"] = _pkg

V = importlib.import_module("matchers.value_signature")


def check(name, cond):
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        raise AssertionError(name)


# An assets sheet where ASSETNUM and asset_ref hold the SAME values (different headers),
# site_ref is a different column, notes is blank.
RECORDS = [
    {"ASSETNUM": "A-001", "asset_ref": "A-001", "site_ref": "S-01", "status": "Active", "notes": ""},
    {"ASSETNUM": "A-002", "asset_ref": "A-002", "site_ref": "S-01", "status": "Active", "notes": ""},
    {"ASSETNUM": "A-003", "asset_ref": "A-003", "site_ref": "S-02", "status": "Inactive", "notes": ""},
    {"ASSETNUM": "A-004", "asset_ref": "A-004", "site_ref": "S-02", "status": "Active", "notes": ""},
]
COLS = ["ASSETNUM", "asset_ref", "site_ref", "status", "notes"]

# ── (2) different names, same values → one merge group ───────────────────────────
groups = V.find_duplicate_column_groups(RECORDS, COLS)
check("ASSETNUM and asset_ref detected as one group", [set(g) for g in groups] == [{"ASSETNUM", "asset_ref"}])
check("identical columns have jaccard 1.0",
      V.jaccard(V.value_set(RECORDS, "ASSETNUM"), V.value_set(RECORDS, "asset_ref")) == 1.0)
check("unrelated columns have jaccard 0.0",
      V.jaccard(V.value_set(RECORDS, "ASSETNUM"), V.value_set(RECORDS, "site_ref")) == 0.0)
check("blank column never groups", all("notes" not in g for g in groups))
check("low-overlap columns are NOT merged", all(set(g) != {"site_ref", "status"} for g in groups))

# ── (3) decide meaning from values ───────────────────────────────────────────────
check("ASSETNUM values classed as code", V.value_class(RECORDS, "ASSETNUM") == "code")
check("status values classed as enum", V.value_class(RECORDS, "status") == "enum")
check("ASSETNUM looks like an identity column", V.is_identity_like(RECORDS, "ASSETNUM") is True)
check("status does NOT look like identity", V.is_identity_like(RECORDS, "status") is False)

# ── near-duplicate (>=95% overlap) still merges; partial overlap does not ─────────
near = [{"a": f"X-{i}", "b": f"X-{i}"} for i in range(40)]
near.append({"a": "X-99", "b": "X-OTHER"})  # 1 mismatch out of 41 → ~0.95
check("near-identical (>=95%) columns merge", [set(g) for g in V.find_duplicate_column_groups(near, ["a", "b"])] == [{"a", "b"}])
half = [{"a": str(i), "b": str(i if i % 2 else i + 100)} for i in range(20)]
check("half-overlapping columns do NOT merge", V.find_duplicate_column_groups(half, ["a", "b"]) == [])

# ── Challenge 1: partial-similarity pairs (60-95%) flagged for review, not auto-merged ──
partial = [{"a": str(i), "b": str(i if i % 4 else i + 100)} for i in range(20)]  # ~75%
near = V.find_near_duplicate_pairs(partial, ["a", "b"])
check("partial-overlap pair is flagged for review", len(near) == 1 and {near[0]["column_a"], near[0]["column_b"]} == {"a", "b"})
check("review pair carries overlap in 0.60-0.95 band", 0.60 <= near[0]["overlap"] < 0.95)
check("identical columns are NOT in the review band (they auto-merge)",
      V.find_near_duplicate_pairs([{"x": f"A-{i}", "y": f"A-{i}"} for i in range(10)], ["x", "y"]) == [])
check("unrelated columns are NOT flagged", V.find_near_duplicate_pairs(RECORDS, ["ASSETNUM", "site_ref"]) == [])

print("\nALL TESTS PASSED")
