"""Tests for apply_canonical_overrides — re-stamping a built CI with the latest user pins.
Run: python tests/test_udr_canonical_overrides.py"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

try:  # Windows console is cp1252 — print arrows in check names without crashing.
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from udr.column_intelligence import apply_canonical_overrides  # noqa: E402


def check(name, cond):
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        raise AssertionError(name)


def fresh_ci():
    return {
        "groups": [
            {"group_id": "G2", "canonical_name": "id",
             "members": ["sites.id", "vendors.id", "workorders.asset_no"],
             "member_keys": [{"table": "Sites", "column": "id"},
                             {"table": "Vendors", "column": "id"},
                             {"table": "WorkOrders", "column": "asset_no"}]},
        ],
        "column_canonical": {"Sites.id": "id", "Vendors.id": "id", "WorkOrders.asset_no": "asset_no"},
    }


# ── group-level pin (G2 → asset_id) renames the group + EVERY member ──────────────────
ci = fresh_ci()
apply_canonical_overrides(ci, {"G2": "asset_id"})
check("group canonical_name updated", ci["groups"][0]["canonical_name"] == "asset_id")
check("Vendors.id re-stamped to asset_id (the reported bug)", ci["column_canonical"]["Vendors.id"] == "asset_id")
check("Sites.id re-stamped", ci["column_canonical"]["Sites.id"] == "asset_id")
check("WorkOrders.asset_no re-stamped", ci["column_canonical"]["WorkOrders.asset_no"] == "asset_id")

# ── column-level pin (table.col → name) sets exactly that entry ───────────────────────
ci2 = fresh_ci()
apply_canonical_overrides(ci2, {"Vendors.id": "vendor_pk"})
check("column-level pin sets just that column", ci2["column_canonical"]["Vendors.id"] == "vendor_pk")
check("column-level pin leaves siblings alone", ci2["column_canonical"]["Sites.id"] == "id")

# ── both kinds combined; column-level applied after group wins for that column ────────
ci3 = fresh_ci()
apply_canonical_overrides(ci3, {"G2": "asset_id", "Vendors.id": "vendor_pk"})
check("group pin applied to members", ci3["column_canonical"]["Sites.id"] == "asset_id")
check("column pin overrides group for its own column", ci3["column_canonical"]["Vendors.id"] == "vendor_pk")

# ── no-op / robustness ────────────────────────────────────────────────────────────────
check("empty overrides → unchanged", apply_canonical_overrides(fresh_ci(), {})["column_canonical"]["Vendors.id"] == "id")
check("blank pin ignored", apply_canonical_overrides(fresh_ci(), {"G2": "  "})["groups"][0]["canonical_name"] == "id")
check("non-dict ci returned as-is", apply_canonical_overrides(None, {"G2": "x"}) is None)
check("missing column_canonical is created", "column_canonical" in apply_canonical_overrides({"groups": []}, {"a.b": "c"}))
check("unknown group id ignored (no crash)", apply_canonical_overrides(fresh_ci(), {"G99": "x"})["column_canonical"]["Vendors.id"] == "id")

print("\nALL TESTS PASSED")
