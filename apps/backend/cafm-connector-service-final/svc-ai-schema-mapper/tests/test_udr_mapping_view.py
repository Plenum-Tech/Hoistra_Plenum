"""Tests for build_combined_mapping_view — the field-mapping gate's complete-state builder.
Run: python tests/test_udr_mapping_view.py"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from udr.mapping_view import build_combined_mapping_view  # noqa: E402


def check(name, cond):
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        raise AssertionError(name)


# ── The exact test migration: 24 Tier-1 approved, 0 Tier-2 auto/flagged, 5 unmappable ──
tier1_approved = {
    "Vendors": [
        {"source_field": "vendor_id", "target_field": "id", "confidence": 0.99, "tier": "T1_identity"},
        {"source_field": "vendor_name", "target_field": "vendor_name", "confidence": 0.98},
        {"source_field": "phone", "target_field": "phone", "confidence": 0.98},
        {"source_field": "country", "target_field": "country", "confidence": 0.98},
    ],
    # (other tables abbreviated to make 24 total across the run in practice; counts checked below)
    "Assets": [{"source_field": f"a{i}", "target_field": f"a{i}", "confidence": 0.98} for i in range(20)],
}
tier2_auto = {}
tier2_flagged = {}
tier2_unmappable = {
    "Vendors": ["trade"],
    "Sites": ["site_id"],
    "Assets": ["site_ref"],
    "WorkOrders": ["fault_description", "status"],
}
routing = {"Vendors": "vendors", "Assets": "assets", "Sites": "sites", "WorkOrders": "workorders"}

view = build_combined_mapping_view(
    tier1_approved, tier2_auto, tier2_flagged, tier2_unmappable, table_routing=routing
)
check("auto_accepted = 24 (Tier-1 approved)", len(view["auto_accepted"]) == 24)
check("flagged = 0", len(view["flagged"]) == 0)
check("unmappable = 5", len(view["unmappable"]) == 5)

# destination resolves: vendor_id → vendors.id (target_table from routing, target_field from mapping)
vid = next(r for r in view["auto_accepted"] if r["source_field"] == "vendor_id")
check("vendor_id → vendors.id", vid["source_table"] == "Vendors" and vid["target_table"] == "vendors" and vid["target_field"] == "id")
check("confidence carried", vid["confidence"] == 0.99)

# string unmappable entries are normalised
trade = next(r for r in view["unmappable"] if r["source_field"] == "trade")
check("string unmappable normalised (source_table set)", trade["source_table"] == "Vendors")

# ── Regression: no duplicates, approved never reappears as unmappable ──
auto_keys = {(r["source_table"], r["source_field"]) for r in view["auto_accepted"]}
unmap_keys = {(r["source_table"], r["source_field"]) for r in view["unmappable"]}
flag_keys = {(r["source_table"], r["source_field"]) for r in view["flagged"]}
check("auto vs unmappable disjoint", auto_keys.isdisjoint(unmap_keys))
check("auto vs flagged disjoint", auto_keys.isdisjoint(flag_keys))
all_rows = view["auto_accepted"] + view["flagged"] + view["unmappable"]
all_keys = [(r["source_table"], r["source_field"]) for r in all_rows]
check("no duplicate (table, field) across buckets", len(all_keys) == len(set(all_keys)))

# Tier-2 auto merges into auto_accepted alongside Tier-1
v2 = build_combined_mapping_view(
    {"A": [{"source_field": "x", "target_field": "x"}]},
    {"A": [{"source_field": "y", "target_field": "y", "confidence": 0.9}]},
    {}, {}, table_routing={"A": "a"},
)
check("Tier-1 + Tier-2 auto both in auto_accepted", len(v2["auto_accepted"]) == 2)

# A field present in BOTH tier1 approved and tier2 unmappable is claimed by tier1 only
v3 = build_combined_mapping_view(
    {"A": [{"source_field": "dup", "target_field": "d"}]}, {}, {}, {"A": ["dup"]}, table_routing={"A": "a"},
)
check("dup field kept in auto_accepted, dropped from unmappable",
      len(v3["auto_accepted"]) == 1 and len(v3["unmappable"]) == 0)

# Robust to None / non-dict inputs
check("None inputs → empty buckets",
      build_combined_mapping_view(None, None, None, None) == {"auto_accepted": [], "flagged": [], "unmappable": []})

print("\nALL TESTS PASSED")
