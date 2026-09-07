"""Step 2 (pre-semantic gate) ↔ B21.1 alignment — regression tests.

Runnable: `python tests/test_udr_gate_alignment.py` (stdlib + the pure udr/* package; the real
FM resolver is loaded directly from matchers.fm_ontology, which is stdlib-only).
"""

import importlib
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from udr.column_intelligence import build_column_intelligence  # noqa: E402
from udr.gate_alignment import (  # noqa: E402
    align_buckets_to_b21,
    align_mapping_to_b21,
    b21_new_column_index,
)

# fm_ontology via a stub 'matchers' package (skip the heavy __init__ that imports anthropic).
_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
_pkg = types.ModuleType("matchers")
_pkg.__path__ = [os.path.join(_SRC, "matchers")]
sys.modules["matchers"] = _pkg
FM = importlib.import_module("matchers.fm_ontology")

_fails = 0


def check(name, cond):
    global _fails
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        _fails += 1


def mk(rows, pk):
    cols = []
    for r in rows:
        for k in r:
            if k not in cols:
                cols.append(k)
    return {"rows": rows, "columns": cols, "pk": pk}


# ── Build a real B21 report for the canonical failing shape ───────────────────────
# Two 'asset id' columns (sites + workorders) make 'asset_id' the shared descriptive name, so the
# asset-code group canonical is 'asset_id'. vendors.id holds A-### asset codes → B21 flags it a NEW
# column (asset codes ≠ vendor UUID PK), while phone / works.id stay clean mappings.
print("[gate-alignment] build B21 dest_mapping (real FM resolver active)")
_tables = {
    "Sites": mk([{"id": f"S-0{i}", "asset id": f"A-00{i}"} for i in range(1, 4)], ["id"]),
    "Vendors": mk([{"id": f"A-00{i}", "phone": f"0{i}"} for i in range(1, 4)], ["id"]),
    "WorkOrders": mk([{"id": f"W-100{i}", "asset id": v}
                      for i, v in enumerate(["A-001", "A-003", "A-002", "A-005"], 1)], ["id"]),
    "works": mk([{"id": f"A-00{i}", "tagnum": f"A-00{i}"} for i in range(1, 5)], ["id"]),
}
_dcc = {("Vendors", "id"): "id", ("Vendors", "phone"): "phone", ("works", "id"): "id",
        ("WorkOrders", "id"): "id", ("Sites", "id"): "id"}
_dest_cols = {"sites": {"id", "site_id"}, "vendors": {"id", "phone", "vendor_name"},
              "assets": {"id", "asset_code"}, "workorders": {"id", "asset_code"}}
_dtb = {"Sites": "sites", "Vendors": "vendors", "works": "assets", "WorkOrders": "workorders"}
_ci = build_column_intelligence(
    _tables, dest_table_by_source=_dtb, dest_col_by_source_col=_dcc,
    conf_by_source_col={k: 0.98 for k in _dcc}, field_resolver=FM.fm_field_lookup,
    dest_columns_by_table=_dest_cols,
)
_dm = _ci["dest_mapping"]
_vrow = next((r for r in _dm if r["source"] == "vendors.id"), None)
check("precondition: B21 flags vendors.id as a new column (canonical asset_id)",
      _vrow is not None and _vrow["outcome"] == "new column" and _vrow["canonical_name"] == "asset_id")

# ── Index ─────────────────────────────────────────────────────────────────────────
_idx = b21_new_column_index(_dm)
check("index keys by SOURCE-table-lower.column → canonical (vendors.id → asset_id)",
      _idx.get(("vendors", "id")) == "asset_id")
check("a clean mapping is NOT in the new-column index (vendors.phone absent)",
      ("vendors", "phone") not in _idx)

# ── align_buckets_to_b21: tier-1 (write) + display copies ───────────────────────────
# Mirror the node: tier-1 mappings (authoritative) + two display copies that must stay in sync.
_tier1 = {
    "Vendors": [{"source_field": "id", "target_field": "id"},
                {"source_field": "phone", "target_field": "phone"}],
    "works": [{"source_field": "id", "target_field": "id"}],
}
_review = {"Vendors": [{"source_field": "id", "target_field": "id"}]}
_auto = {"Vendors": [{"source_field": "phone", "target_field": "phone"}]}
_changed = align_buckets_to_b21(_dm, _tier1, _review, _auto)

check("returns the tier-1 change count (1: vendors.id)", _changed == 1)
_vid = _tier1["Vendors"][0]
check("write path: vendors.id retargeted to asset_id + flagged new column",
      _vid["target_field"] == "asset_id" and _vid.get("b21_new_column") is True)
check("write path: vendors.phone untouched (clean mapping stays existing)",
      _tier1["Vendors"][1]["target_field"] == "phone"
      and not _tier1["Vendors"][1].get("b21_new_column"))
check("write path: works.id untouched (same-entity id stays mapped)",
      _tier1["works"][0]["target_field"] == "id"
      and not _tier1["works"][0].get("b21_new_column"))
check("display copy (reviewable) aligned the same way",
      _review["Vendors"][0]["target_field"] == "asset_id"
      and _review["Vendors"][0].get("b21_new_column") is True)
check("display copy (auto-approved) clean mapping untouched",
      _auto["Vendors"][0]["target_field"] == "phone")

# ── idempotency: a second pass changes nothing ──────────────────────────────────────
_again = align_buckets_to_b21(_dm, _tier1, _review, _auto)
check("idempotent: re-running aligns 0 (already flagged)", _again == 0)

# ── no dest_mapping → no-op ─────────────────────────────────────────────────────────
_noop_tier1 = {"Vendors": [{"source_field": "id", "target_field": "id"}]}
check("no dest_mapping → 0 changes, mapping untouched",
      align_buckets_to_b21(None, _noop_tier1) == 0
      and _noop_tier1["Vendors"][0]["target_field"] == "id")

# ── unit: align_mapping_to_b21 skips when canonical equals current target ───────────
_eq = {"source_field": "tagnum", "target_field": "tagnum"}
check("no-op when B21 canonical equals the current target (tagnum→tagnum)",
      align_mapping_to_b21("works", _eq, {("works", "tagnum"): "tagnum"}) is False)

print()
if _fails:
    print(f"{_fails} TEST(S) FAILED")
    sys.exit(1)
print("ALL TESTS PASSED")
