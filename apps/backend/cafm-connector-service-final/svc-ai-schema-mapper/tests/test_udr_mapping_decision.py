"""Tests for the combined column-mapping scorer (udr/mapping_decision.py).

Runnable: `python tests/test_udr_mapping_decision.py` (custom check(), not pytest).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from udr.mapping_decision import (  # noqa: E402
    entity_prefix_conflict,
    name_similarity,
    score_column_mapping,
    value_overlap,
)

_fails = 0


def check(name, cond):
    global _fails
    print(f"{'PASS' if cond else 'FAIL'} {name}")
    if not cond:
        _fails += 1


# A toy FM resolver: maps names to a coarse entity canon so ontology agreement can be tested.
def _resolver(name):
    n = str(name).lower()
    if "asset" in n or n in ("tagnum", "asset_no"):
        return ("asset_code", 0.9)
    if "vendor" in n or n == "supplier":
        return ("vendor_id", 0.9)
    if n in ("id", "site_id", "site_ref"):
        return ("", 0.0)  # generic — unknown entity
    return ("", 0.0)


print("[name_similarity]")
check("exact match → 1.0", name_similarity("vendor_name", "vendor_name") == 1.0)
check("normalised match → 1.0", name_similarity("asset id", "asset_id") == 1.0)
# Both asset_id and vendor_id read as a HIGH name match against the generic PK 'id'
# (qualified-PK alias) — by design the NAME dimension can't tell them apart; VALUE does.
check("qualified-PK alias 'asset_id' vs 'id' is HIGH (>=0.8)", name_similarity("asset_id", "id") >= 0.8)
check("qualified-PK alias 'vendor_id' vs 'id' is HIGH (>=0.8)", name_similarity("vendor_id", "id") >= 0.8)
check("unrelated names stay LOW ('asset_name' vs 'asset_code')",
      name_similarity("asset_name", "asset_code") < 0.8)

print("\n[value_overlap]")
check("disjoint values → 0.0", value_overlap(["A-001", "A-002"], ["V-001", "V-002"]) == 0.0)
check("identical values → 1.0", value_overlap(["V-001", "V-002"], ["V-001", "V-002"]) == 1.0)
check("destination unknown/empty → None", value_overlap(["A-001"], []) is None)

print("\n[Example 1 — asset codes vs vendor ids: DO NOT MAP → new_column]")
# Source column (canonical asset_id) with asset-code values; destination vendors.id has
# real vendor-id values. Name shares only 'id', values disjoint → new column.
ex1 = score_column_mapping(
    source_name="asset_id",
    source_values=["A-001", "A-002", "A-003"],
    dest_name="id",
    dest_values=["V-001", "V-002", "V-003"],
    same_table=True,
    field_resolver=_resolver,
)
print("  ", {k: ex1[k] for k in ("name_score", "value_score", "ontology_score", "final_score", "decision")})
check("Example 1 value_match is False", ex1["value_match"] is False)
check("Example 1 decision = new_column", ex1["decision"] == "new_column")

print("\n[Screenshot case — source literally 'id' (A-###) → dest 'id' (V-###)]")
# Even with an EXACT name match, disjoint values must block the auto-merge.
shot = score_column_mapping(
    source_name="id",
    source_values=["A-001", "A-002", "A-003"],
    dest_name="id",
    dest_values=["V-001", "V-002", "V-003"],
    same_table=True,
    field_resolver=_resolver,
)
print("  ", {k: shot[k] for k in ("name_score", "value_score", "final_score", "decision")})
check("exact-name but disjoint values does NOT auto-resolve", shot["decision"] != "auto_resolved")
check("exact-name but disjoint values → new_column", shot["decision"] == "new_column")

print("\n[Example 2 — vendor_id vs id, same values: auto_resolve]")
ex2 = score_column_mapping(
    source_name="vendor_id",
    source_values=["V-001", "V-002", "V-003"],
    dest_name="id",
    dest_values=["V-001", "V-002", "V-003"],
    same_table=True,
    field_resolver=None,  # no resolver → ontology unknown, must not block
)
print("  ", {k: ex2[k] for k in ("name_score", "value_score", "final_score", "decision")})
check("Example 2 value_match True", ex2["value_match"] is True)
check("Example 2 auto_resolved", ex2["decision"] == "auto_resolved")

print("\n[Empty destination table — value unknown, fall back to name + context]")
empty = score_column_mapping(
    source_name="site_name",
    source_values=["Bishopsgate Tower", "Riverside Campus"],
    dest_name="site_name",
    dest_values=[],          # fresh/empty target table
    same_table=True,
    field_resolver=None,
)
print("  ", {k: empty[k] for k in ("name_score", "value_score", "final_score", "decision")})
check("empty-dest value_score is None (un-evaluable)", empty["value_score"] is None)
check("empty-dest exact-name still auto_resolves (not blocked by missing data)",
      empty["decision"] == "auto_resolved")

print("\n[Ontology disagreement forces new_column even with some name overlap]")
onto = score_column_mapping(
    source_name="asset_id",
    source_values=["A-001"],
    dest_name="vendor_id",
    dest_values=["A-001"],   # contrived value match
    same_table=True,
    field_resolver=_resolver,  # asset_code vs vendor_id → disagree
)
check("ontology mismatch → new_column", onto["decision"] == "new_column")

print("\n[New records — same namespace, zero overlap: must NOT be demoted]")
# Migrating brand-new vendors (V-100…) into a vendors.id that already holds V-001…V-003.
# Distinct values don't overlap AT ALL, but the namespace prefix matches → real mapping.
newrec = score_column_mapping(
    source_name="vendor_id",
    source_values=["V-100", "V-101", "V-102"],
    dest_name="id",
    dest_values=["V-001", "V-002", "V-003"],
    same_table=True,
    field_resolver=None,
)
print("  ", {k: newrec[k] for k in ("name_score", "value_score", "value_match", "final_score", "decision")})
check("new ids (same V- namespace, no overlap) are NOT a value mismatch", newrec["value_match"] is not False)
check("new ids still auto_resolve (not wrongly demoted to new_column)", newrec["decision"] == "auto_resolved")

print("\n[Free-text / non-id columns must NOT be demoted on disjoint values]")
# site_name → site_name (exact name) but the destination holds DIFFERENT existing sites
# (no overlap, no id namespace). Value is inconclusive → excluded → name carries → auto.
ft = score_column_mapping(
    source_name="site_name", source_values=["Bishopsgate Tower", "Riverside Campus"],
    dest_name="site_name", dest_values=["Dubai Mall", "Marina Tower"],
    same_table=True, field_resolver=None,
)
print("  ", {k: ft[k] for k in ("name_score", "value_score", "final_score", "decision")})
check("free-text exact name, disjoint dest values ⇒ value inconclusive (None)", ft["value_score"] is None)
check("free-text exact name, disjoint dest values ⇒ auto_resolved (NOT new_column)",
      ft["decision"] == "auto_resolved")
cat = score_column_mapping(
    source_name="city", source_values=["London", "Manchester"],
    dest_name="city", dest_values=["Dubai", "Abu Dhabi"], same_table=True, field_resolver=None,
)
check("categorical exact name, disjoint ⇒ auto_resolved", cat["decision"] == "auto_resolved")

print("\n[Different namespace, zero overlap: confident mismatch]")
ns = score_column_mapping(
    source_name="id", source_values=["A-001", "A-002"],
    dest_name="id", dest_values=["V-001", "V-002"],
    same_table=True, field_resolver=None,
)
check("A- vs V- namespace ⇒ value_match False ⇒ new_column", ns["decision"] == "new_column")

if _fails:
    print(f"\n{_fails} CHECK(S) FAILED")
    sys.exit(1)
print("\n[entity_prefix_conflict — a canonical that abbreviates its OWN table is NOT a conflict]")
# A multi-word destination table can be referred to by its initialism: 'wo' = work_orders. A
# canonical like 'wo_description' → work_orders.description is the SAME entity, not a cross-entity
# merge — it must not be flagged (this was demoting fault_description/priority to new columns).
check("wo_description → work_orders.description is NOT a conflict ('wo' = work_orders)",
      entity_prefix_conflict("wo_description", "description", "work_orders") is None)
check("wo_priority → work_orders.priority is NOT a conflict",
      entity_prefix_conflict("wo_priority", "priority", "work_orders") is None)
check("wo_status → work_orders.status is NOT a conflict",
      entity_prefix_conflict("wo_status", "status", "work_orders") is None)
# Genuine cross-entity is still caught.
check("asset_id → vendors.id IS a conflict ('asset' ≠ vendor)",
      entity_prefix_conflict("asset_id", "id", "vendors") == "asset")
check("asset_id → work_orders.id IS a conflict ('asset' ≠ work_orders)",
      entity_prefix_conflict("asset_id", "id", "work_orders") == "asset")
# Same single-word entity (plural table) is not a conflict.
check("asset_id → assets.id is NOT a conflict (asset == assets)",
      entity_prefix_conflict("asset_id", "id", "assets") is None)
check("site_id → sites.id is NOT a conflict",
      entity_prefix_conflict("site_id", "id", "sites") is None)

if _fails:
    print(f"\n{_fails} TEST(S) FAILED")
    sys.exit(1)
print("\nALL TESTS PASSED")
