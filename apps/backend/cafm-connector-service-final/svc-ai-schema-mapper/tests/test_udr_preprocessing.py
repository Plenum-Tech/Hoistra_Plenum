"""Unit tests for Feature 7 Stage 1 pre-processing. Run: python tests/test_udr_preprocessing.py"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from udr.preprocessing import (  # noqa: E402
    AUTO_MERGE_MIN,
    REVIEW_MIN,
    nan_stats,
    remove_exact_duplicate_rows,
    find_column_merge_candidates,
    propose_column_name,
    preprocessing_summary,
)


def check(name, cond):
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        raise AssertionError(name)


# ── 7.2 AC1: nan_stats — all-null column + clean column + empty rows ────────────
rows_nan = [
    {"clean": "A1", "half": "x", "empty": None},
    {"clean": "A2", "half": None, "empty": None},
    {"clean": "A3", "half": "z", "empty": ""},
    {"clean": "A4", "half": None, "empty": None},
]
stats = nan_stats(rows_nan, ["clean", "half", "empty"])
check("nan_stats clean column == 0.0", stats["clean"] == 0.0)
check("nan_stats all-null column == 1.0", stats["empty"] == 1.0)
check("nan_stats half-null column == 0.5", stats["half"] == 0.5)
check("nan_stats empty rows -> 1.0", nan_stats([], ["x"])["x"] == 1.0)
check("nan_stats no columns -> {}", nan_stats(rows_nan, []) == {})


# ── 7.2 AC2: exact duplicate-row removal ────────────────────────────────────────
dup_rows = [
    {"id": "1", "v": "a"},
    {"id": "2", "v": "b"},
    {"id": "1", "v": "a"},          # exact duplicate of row 0
    {"v": "b", "id": "2"},          # same pairs as row 1, different key order
    {"id": "3", "v": "a"},          # not a dup (different id)
]
deduped, removed = remove_exact_duplicate_rows(dup_rows)
check("dedup removed_count == 2", removed == 2)
check("dedup keeps 3 rows", len(deduped) == 3)
check(
    "dedup order-preserving (first occurrences)",
    deduped == [{"id": "1", "v": "a"}, {"id": "2", "v": "b"}, {"id": "3", "v": "a"}],
)
check("dedup on empty -> ([], 0)", remove_exact_duplicate_rows([]) == ([], 0))
check(
    "dedup no duplicates -> removed 0",
    remove_exact_duplicate_rows([{"id": "1"}, {"id": "2"}]) == ([{"id": "1"}, {"id": "2"}], 0),
)


# ── 7.2 AC3/AC4: column-merge candidates ────────────────────────────────────────
# auto_merge: two columns with identical value sets -> overlap 1.0 (>= 0.95).
auto_rows = [
    {"code": "C1", "code_dup": "C1", "other": "Z1"},
    {"code": "C2", "code_dup": "C2", "other": "Z2"},
    {"code": "C3", "code_dup": "C3", "other": "Z3"},
]
auto = find_column_merge_candidates(auto_rows, ["code", "code_dup", "other"])
pair_cc = next(c for c in auto if {c["column_a"], c["column_b"]} == {"code", "code_dup"})
check("auto_merge overlap == 1.0", pair_cc["overlap"] == 1.0)
check("auto_merge action", pair_cc["action"] == "auto_merge")
check("auto_merge sample_a <= 3", len(pair_cc["sample_a"]) <= 3 and len(pair_cc["sample_b"]) <= 3)
# code vs other share nothing -> below REVIEW_MIN -> not a candidate.
check(
    "no-merge pair (<0.60) is skipped",
    not any({c["column_a"], c["column_b"]} == {"code", "other"} for c in auto),
)

# review pair: a has 10 distinct, b shares 7 of a's values -> a->b = 0.70.
#   b also has 3 of its own extras (so |b|=10, b->a = 7/10 = 0.70). max = 0.70 ⇒ review.
a_vals = [f"V{i}" for i in range(10)]          # V0..V9
b_vals = [f"V{i}" for i in range(7)] + ["X0", "X1", "X2"]   # 7 shared + 3 extras
review_rows = [{"a": a_vals[i], "b": b_vals[i]} for i in range(10)]
review = find_column_merge_candidates(review_rows, ["a", "b"])
pair_ab = next(c for c in review if {c["column_a"], c["column_b"]} == {"a", "b"})
check("review overlap == 0.70", pair_ab["overlap"] == 0.70)
check("review action", pair_ab["action"] == "review")

# ── boundary at exactly 0.95: a has 20 distinct, b shares 19 -> 19/20 = 0.95 ⇒ auto_merge.
a95 = [f"N{i}" for i in range(20)]                       # N0..N19
b95 = [f"N{i}" for i in range(19)] + ["EXTRA"]           # 19 shared + 1 extra
rows95 = [{"a": a95[i], "b": b95[i]} for i in range(20)]
c95 = find_column_merge_candidates(rows95, ["a", "b"])[0]
check("boundary 0.95 overlap", c95["overlap"] == 0.95)
check("boundary 0.95 -> auto_merge (>=0.95)", c95["action"] == "auto_merge")

# ── boundary at exactly 0.60: a has 5 distinct, b shares 3 -> 3/5 = 0.60 ⇒ review.
a60 = ["P0", "P1", "P2", "P3", "P4"]
b60 = ["P0", "P1", "P2", "Q0", "Q1"]                     # 3 shared + 2 extras (|b|=5)
rows60 = [{"a": a60[i], "b": b60[i]} for i in range(5)]
c60 = find_column_merge_candidates(rows60, ["a", "b"])[0]
check("boundary 0.60 overlap", c60["overlap"] == 0.60)
check("boundary 0.60 -> review (>=0.60, <0.95)", c60["action"] == "review")

# just-below boundary: 2/5 = 0.40 < 0.60 ⇒ skipped entirely.
a40 = ["P0", "P1", "P2", "P3", "P4"]
b40 = ["P0", "P1", "Q0", "Q1", "Q2"]
rows40 = [{"a": a40[i], "b": b40[i]} for i in range(5)]
check("below 0.60 -> no candidate", find_column_merge_candidates(rows40, ["a", "b"]) == [])
check("constants mirror spec", AUTO_MERGE_MIN == 0.95 and REVIEW_MIN == 0.60)


# ── 7.2 AC5/AC6: propose_column_name ────────────────────────────────────────────
# Heuristic: date samples -> event_date.
date_name = propose_column_name(["7/17/2009", "2/15/2016", "2009-07-17"])
check("date heuristic name", date_name["name"] == "event_date")
check("date heuristic source", date_name["source"] == "heuristic")
check("date heuristic confidence ~0.6", abs(date_name["confidence"] - 0.6) < 1e-9)

# Heuristic: id_code samples -> entity_id.
id_name = propose_column_name(["AHU-012", "AHU-013", "CHW-200"])
check("id_code heuristic name", id_name["name"] == "entity_id")
check("id_code heuristic source heuristic", id_name["source"] == "heuristic")

# Heuristic: boolean / integer / categorical / free_text mappings.
check("boolean heuristic -> flag", propose_column_name(["Yes", "No", "Y"])["name"] == "flag")
check("integer heuristic -> count_or_id", propose_column_name(["1", "2", "3"])["name"] == "count_or_id")
check("categorical heuristic -> category", propose_column_name(["Siemens", "Trane"])["name"] == "category")
check(
    "free_text heuristic -> description",
    propose_column_name(["a long sentence here", "another long phrase value"])["name"] == "description",
)
# All-empty samples fall back to description deterministically.
check("all-empty samples -> description", propose_column_name([None, "", None])["name"] == "description")

# AC5: an LLM callable overrides the heuristic and is marked source 'llm'.
llm_name = propose_column_name(["7/17/2009"], llm=lambda s: "inspection_date")
check("llm name used", llm_name["name"] == "inspection_date")
check("llm source", llm_name["source"] == "llm")
# A bare LLM name (no confidence given) is assumed to meet the AC5 0.80 gate.
check("llm bare-name confidence == 0.80 (gate)", llm_name["confidence"] == 0.80)


# ── 7.2 AC7: preprocessing_summary ──────────────────────────────────────────────
summary = preprocessing_summary(nan_removed=2, duplicate_rows_removed=2, columns_merged=1, flagged_for_review=1)
check("summary stage", summary["stage"] == "preprocessing")
check("summary nan_removed", summary["nan_removed"] == 2)
check("summary duplicate_rows_removed", summary["duplicate_rows_removed"] == 2)
check("summary columns_merged", summary["columns_merged"] == 1)
check("summary flagged_for_review", summary["flagged_for_review"] == 1)


print("\nALL TESTS PASSED")
