"""Tests for content-aware table routing (matchers/table_router.py): rank destination
tables by column overlap so the gate can offer the right table to select even when the
sheet NAME is misleading. Pure. Run: python tests/test_table_router.py"""
import importlib
import os
import sys
import types

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
sys.path.insert(0, _SRC)
_pkg = types.ModuleType("matchers")
_pkg.__path__ = [os.path.join(_SRC, "matchers")]
sys.modules["matchers"] = _pkg

R = importlib.import_module("matchers.table_router")


def check(name, cond):
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        raise AssertionError(name)


def top(cols):
    r = R.rank_target_tables(cols)
    return r[0]["table"] if r else None


# work_tasks.csv: NAME says work-orders, COLUMNS are clearly assets → must rank assets #1
ASSET_COLS = ["ASSETNUM", "asset_ref", "EQKTX", "manufacturer", "model", "site_ref", "status"]
res = R.rank_target_tables(ASSET_COLS)
check("asset-shaped sheet ranks 'assets' first", res and res[0]["table"] == "assets")
check("assets match is strong (>=5/7)", res[0]["mapped"] >= 5)
check("result carries mapped/total/pct", {"table", "mapped", "total", "pct"} <= set(res[0]))

# other sheets resolve to the obvious table
check("vendor columns -> vendors", top(["vendor_id", "vendor_name", "trade", "phone"]) == "vendors")
check("site columns -> sites", top(["site_ref", "site_name", "city", "postcode"]) == "sites")
check("work-order columns -> work_orders",
      top(["WONUM", "asset_no", "fault_description", "wo_status", "priority", "date_raised"]) == "work_orders")

# safety: empty / junk input doesn't crash or invent matches
check("empty columns -> no candidates", R.rank_target_tables([]) == [])
check("nonsense columns -> no candidates", R.rank_target_tables(["zzz_qqq", "blah_123"]) == [])

# internal/audit tables are never offered as a routing target
check("never offers a non-domain table",
      all(c["table"] not in R._NON_DOMAIN
          for cols in [ASSET_COLS, ["status", "created_at", "id"]]
          for c in R.rank_target_tables(cols)))

print("\nALL TESTS PASSED")
