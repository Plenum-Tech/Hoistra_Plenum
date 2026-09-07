"""Tests for the adversarial-verify fixes to Stage 1 / Stage 7. Run: python tests/test_udr_phase1_fixes.py"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from udr.preprocessing import (  # noqa: E402
    find_column_merge_candidates,
    find_cross_table_merge_candidates,
    find_same_name_divergent_columns,
    propose_column_name,
)
from udr.reference_tables import (  # noqa: E402
    build_reference_table,
    promote_shared_attribute,
    remediate_test1_failures,
)
from udr.validation import run_test1_chunk_pk  # noqa: E402


def check(name, cond):
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        raise AssertionError(name)


# ── Fix: duplicate column name must not self-pair ─────────────────────────────
rows = [{"ID": "1"}, {"ID": "2"}]
cand = find_column_merge_candidates(rows, ["ID", "ID"])
check("no self-pair from duplicate column name",
      all(not (c["column_a"] == c["column_b"]) for c in cand))

# ── Fix: decimal heuristic name ──────────────────────────────────────────────
check("decimal -> amount_or_measure",
      propose_column_name(["3.14", "2.71", "1.41"])["name"] == "amount_or_measure")

# ── Fix: LLM None/empty falls back to heuristic; dict confidence preserved ────
r_none = propose_column_name(["x"], llm=lambda s: None)
check("llm None -> heuristic fallback", r_none["source"] == "heuristic")
r_empty = propose_column_name(["x"], llm=lambda s: "")
check("llm '' -> heuristic fallback", r_empty["source"] == "heuristic")
r_raise = propose_column_name(["x"], llm=lambda s: (_ for _ in ()).throw(ValueError("boom")))
check("llm raise -> heuristic fallback", r_raise["source"] == "heuristic")
r_dict = propose_column_name(["x"], llm=lambda s: {"name": "foo", "confidence": 0.91})
check("llm dict name+conf preserved", r_dict == {"name": "foo", "confidence": 0.91, "source": "llm"})
r_str = propose_column_name(["x"], llm=lambda s: "bar")
check("llm bare str gets gate confidence 0.80",
      r_str["name"] == "bar" and r_str["confidence"] == 0.80 and r_str["source"] == "llm")

# ── New AC5: cross-table diff-name / same-value merge ────────────────────────
ct = {
    "assets": {"rows": [{"asset_no": "AHU-1"}, {"asset_no": "AHU-2"}, {"asset_no": "AHU-3"}],
               "columns": ["asset_no"]},
    "tickets": {"rows": [{"equipment_id": "AHU-1"}, {"equipment_id": "AHU-2"}, {"equipment_id": "AHU-3"}],
                "columns": ["equipment_id"]},
    "other": {"rows": [{"city": "Sharjah"}, {"city": "Ajman"}], "columns": ["city"]},
}
merges = find_cross_table_merge_candidates(ct)
pair = next((m for m in merges if {m["column_a"], m["column_b"]} == {"asset_no", "equipment_id"}), None)
check("cross-table merge finds asset_no~equipment_id", pair is not None and pair["overlap"] == 1.0)
check("cross-table merge proposes a name", pair["proposed_name"] and pair["name_source"] in ("llm", "heuristic"))
check("cross-table merge excludes unrelated city",
      not any("city" in {m["column_a"], m["column_b"]} for m in merges))

# ── New AC6: same-name / divergent-values split ──────────────────────────────
sn = {
    "a": {"rows": [{"id": "A1"}, {"id": "A2"}, {"id": "A3"}], "columns": ["id"]},
    "b": {"rows": [{"id": "X1"}, {"id": "X2"}, {"id": "X3"}], "columns": ["id"]},
}
splits = find_same_name_divergent_columns(sn)
check("same-name divergent split detected", any(s["column_name"] == "id" and s["overlap"] < 0.30 for s in splits))

# ── Fix: promote shared attr across DIFFERENTLY-named columns unions values ───
ref_tables = {
    "assets": {"rows": [{"asset_id": "A1", "make": "Siemens"}, {"asset_id": "A2", "make": "Siemens"},
                        {"asset_id": "A3", "make": "Trane"}], "columns": ["asset_id", "make"], "pk": ["asset_id"]},
    "orders": {"rows": [{"order_id": "O1", "vendor": "Bosch"}, {"order_id": "O2", "vendor": "Bosch"}],
               "columns": ["order_id", "vendor"], "pk": ["order_id"]},
}
prom = promote_shared_attribute(ref_tables, [("assets", "make"), ("orders", "vendor")])
ref = prom["reference_table"]
pk_vals = {r[ref["pk"][0]] for r in ref["rows"]}
check("differently-named promotion unions Siemens+Trane+Bosch", pk_vals == {"Siemens", "Trane", "Bosch"})
check("fk_rewrites cover both columns",
      {(fr["table"], fr["column"]) for fr in prom["fk_rewrites"]} == {("assets", "make"), ("orders", "vendor")})

# ── Fix: remediate does NOT retag dangling (ghost) values ────────────────────
report = {
    "failures": [
        {"chunk_id": "c1", "association_value": "Siemens", "found_in": [{"table": "assets", "column": "make"}]},
        {"chunk_id": "c2", "association_value": "GHOST", "found_in": []},
    ]
}
rem = remediate_test1_failures(ref_tables, report)
check("ghost not retagged", "GHOST" not in rem["retag"])
check("ghost reported unresolved", "GHOST" in rem["unresolved"])
check("located value retagged", rem["retag"].get("Siemens") == "Siemens")
check("reference table built for located value", any("Siemens" in {r[t["pk"][0]] for r in t["rows"]}
      for t in rem["reference_tables"] for _ in [0]) if rem["reference_tables"] else False)

# the promoted ref makes the previously-failing 'Siemens' chunk pass Test 1 on re-run
patched = dict(ref_tables)
for t in rem["reference_tables"]:
    patched[t["table"]] = {"rows": t["rows"], "columns": t["columns"], "pk": t["pk"]}
r1 = run_test1_chunk_pk([{"chunk_id": "c1", "entity_id": "Siemens"}], patched)
check("Siemens chunk PASSES after remediation", r1["failed"] == 0)

print("\nALL TESTS PASSED")
