"""Unit tests for Feature 7 Stage-7 reference-table auto-creation.
Run: python tests/test_udr_reference_tables.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

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


# ── fixtures ────────────────────────────────────────────────────────────────
# assets carries the shared attribute `make` plus a co-located descriptor
# `manufacturer` that is functionally dependent on it (Siemens -> Siemens AG,
# Trane -> Trane Inc). `country` is also FD. `model` is NOT FD (varies per row).
assets = [
    {"asset_id": "A1", "make": "Siemens", "manufacturer": "Siemens AG", "country": "DE", "model": "M1"},
    {"asset_id": "A2", "make": "Siemens", "manufacturer": "Siemens AG", "country": "DE", "model": "M2"},
    {"asset_id": "A3", "make": "Trane", "manufacturer": "Trane Inc", "country": "US", "model": "M3"},
]
# orders also references `make` (a second occurrence — no descriptors).
orders = [
    {"order_id": "O1", "make": "Siemens"},
    {"order_id": "O2", "make": "Trane"},
]

tables = {
    "assets": {"rows": assets, "columns": ["asset_id", "make", "manufacturer", "country", "model"], "pk": ["asset_id"]},
    "orders": {"rows": orders, "columns": ["order_id", "make"], "pk": ["order_id"]},
}


# ── 1) build_reference_table: distinct PK rows + descriptor carried ──────────
ref = build_reference_table(tables, "make", source_table="assets")
check("ref table name == make (no collision)", ref["table"] == "make")
check("ref pk == [make]", ref["pk"] == ["make"])
check("ref source_attribute == make", ref["source_attribute"] == "make")

ref_pk_values = {r["make"] for r in ref["rows"]}
check("ref has 2 distinct PK rows", len(ref["rows"]) == 2 and ref_pk_values == {"Siemens", "Trane"})

# Functionally-dependent descriptors carried; per-row `model` dropped.
check("descriptor manufacturer carried", "manufacturer" in ref["columns"])
check("descriptor country carried", "country" in ref["columns"])
check("non-FD model NOT carried", "model" not in ref["columns"])

by_value = {r["make"]: r for r in ref["rows"]}
check("Siemens -> Siemens AG", by_value["Siemens"]["manufacturer"] == "Siemens AG")
check("Trane -> Trane Inc", by_value["Trane"]["manufacturer"] == "Trane Inc")
check("Siemens -> DE", by_value["Siemens"]["country"] == "DE")

# PK values are unique + non-null (valid anchor) — the whole point of promotion.
check("ref PK values unique", len(ref_pk_values) == len(ref["rows"]))
check("ref PK values non-null", all(r["make"] for r in ref["rows"]))

# Collision: if a table literally named "make" exists, the ref is "make_ref".
collide_tables = dict(tables)
collide_tables["make"] = {"rows": [], "columns": ["make"], "pk": ["make"]}
ref_c = build_reference_table(collide_tables, "make", source_table="assets")
check("collision -> make_ref", ref_c["table"] == "make_ref")


# ── 2) promote_shared_attribute: fk_rewrites for two tables sharing make ─────
promo = promote_shared_attribute(tables, [("assets", "make"), ("orders", "make")])
check("promotion builds ref table", promo["reference_table"]["table"] == "make")
# Richest source (assets, with descriptors) was chosen for enrichment.
check("promotion picked rich source (descriptors present)",
      "manufacturer" in promo["reference_table"]["columns"])

rewrites = {(fk["table"], fk["column"]): fk for fk in promo["fk_rewrites"]}
check("two fk rewrites produced", len(promo["fk_rewrites"]) == 2)
check("assets.make -> make.make",
      rewrites[("assets", "make")]["references_table"] == "make"
      and rewrites[("assets", "make")]["references_column"] == "make")
check("orders.make -> make.make",
      rewrites[("orders", "make")]["references_table"] == "make"
      and rewrites[("orders", "make")]["references_column"] == "make")


# ── 3) remediate_test1_failures: synthetic Test-1 report ─────────────────────
# A chunk anchored on the shared attribute value "Siemens" found in assets.make.
# It is NOT a PK anywhere, so Test 1 fails it.
chunks = [
    {"chunk_id": "C1", "entity_id": "A1"},        # valid: A1 is a PK in assets
    {"chunk_id": "C2", "entity_id": "Siemens"},   # invalid: shared attribute, not a PK
]
report_before = run_test1_chunk_pk(chunks, tables)
check("Test1 fails before remediation (Siemens not a PK)", report_before["failed"] == 1)
check("Test1 blocks UDR before remediation", report_before["blocks_udr"] is True)
fail = report_before["failures"][0]
check("failure value == Siemens", fail["association_value"] == "Siemens")
check("failure found_in includes assets.make",
      {"table": "assets", "column": "make"} in fail["found_in"])

remediation = remediate_test1_failures(tables, report_before)
check("remediation built 1 reference table", len(remediation["reference_tables"]) == 1)
rt = remediation["reference_tables"][0]
check("ref pk == [make]", rt["pk"] == ["make"])
check("ref pk rows contain Siemens", "Siemens" in {r["make"] for r in rt["rows"]})
check("retag is identity (Siemens -> Siemens)", remediation["retag"].get("Siemens") == "Siemens")
check("remediation produced fk rewrites", len(remediation["fk_rewrites"]) >= 1)

# ── 4) Re-test with the new reference table wired into the tables dict ───────
# Adding the ref table (pk=[make]) makes "Siemens" a real PK value -> chunk passes.
retest_tables = dict(tables)
retest_tables[rt["table"]] = {
    "rows": rt["rows"],
    "columns": rt["columns"],
    "pk": rt["pk"],
}
report_after = run_test1_chunk_pk(chunks, retest_tables)
check("Test1 PASSES after remediation", report_after["failed"] == 0)
check("Test1 no longer blocks UDR", report_after["blocks_udr"] is False)
check("pass_rate == 1.0 after remediation", report_after["pass_rate"] == 1.0)


# ── 5) edge cases ────────────────────────────────────────────────────────────
# Empty tables -> empty ref (no rows, still well-formed).
empty_ref = build_reference_table({}, "make")
check("empty tables -> empty ref rows", empty_ref["rows"] == [] and empty_ref["pk"] == ["make"])

# All-null attribute column -> no distinct PK values.
null_tables = {"t": {"rows": [{"make": None}, {"make": ""}], "columns": ["make"], "pk": []}}
null_ref = build_reference_table(null_tables, "make")
check("all-null attribute -> 0 ref rows", null_ref["rows"] == [])

# Single distinct value -> exactly one PK row.
single_tables = {"t": {"rows": [{"make": "Siemens"}, {"make": "Siemens"}], "columns": ["make"], "pk": []}}
single_ref = build_reference_table(single_tables, "make")
check("single distinct value -> 1 ref row", len(single_ref["rows"]) == 1)

# Inconsistent descriptor (same make, different manufacturer) is NOT carried.
inconsistent = {
    "t": {
        "rows": [
            {"make": "Siemens", "manufacturer": "Siemens AG"},
            {"make": "Siemens", "manufacturer": "Siemens GmbH"},  # disagrees -> drop column
        ],
        "columns": ["make", "manufacturer"],
        "pk": [],
    }
}
inc_ref = build_reference_table(inconsistent, "make", source_table="t")
check("inconsistent descriptor dropped", "manufacturer" not in inc_ref["columns"])

# Empty attribute_columns -> no-op promotion.
empty_promo = promote_shared_attribute(tables, [])
check("empty promotion -> None ref + no rewrites",
      empty_promo["reference_table"] is None and empty_promo["fk_rewrites"] == [])

# Report with no failures -> empty remediation.
clean_report = run_test1_chunk_pk([{"chunk_id": "C1", "entity_id": "A1"}], tables)
clean_remediation = remediate_test1_failures(tables, clean_report)
check("clean report -> no reference tables",
      clean_remediation["reference_tables"] == [] and clean_remediation["retag"] == {})


print("\nALL TESTS PASSED")
