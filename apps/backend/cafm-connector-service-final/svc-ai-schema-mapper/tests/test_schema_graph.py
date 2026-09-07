"""Tests for graph-driven retrieval planning (matchers/schema_graph.py): a NL question →
related table cluster + FK paths, so the RAG layer knows what to pull. Pure.
Run: python tests/test_schema_graph.py"""
import importlib
import os
import sys
import types

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
sys.path.insert(0, _SRC)
_pkg = types.ModuleType("matchers")
_pkg.__path__ = [os.path.join(_SRC, "matchers")]
sys.modules["matchers"] = _pkg

SG = importlib.import_module("matchers.schema_graph")


def check(name, cond):
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        raise AssertionError(name)


# ── the FK graph is derived correctly ────────────────────────────────────────────
g = SG.build_schema_graph()
check("graph has tables", len(g["tables"]) >= 100)
assets = g["tables"]["assets"]
fk_cols = {f["column"]: f["references"] for f in assets["fks"]}
check("assets.site_id -> sites", fk_cols.get("site_id") == "sites")
check("assets.location_id -> locations", fk_cols.get("location_id") == "locations")
check("assets PK is id", assets["pk"] == "id")
check("certificates.asset_id -> assets",
      any(f["column"] == "asset_id" and f["references"] == "assets" for f in g["tables"]["certificates"]["fks"]))
check("assets is referenced_by certificates",
      any(r["table"] == "certificates" for r in g["referenced_by"]["assets"]))


def rels(r):
    return {(x["from_table"], x["from_column"], x["to_table"]) for x in r["relationships"]}


# ── NL query -> related cluster + FK paths ────────────────────────────────────────
r = SG.expand_query("location based asset details", depth=1)
check("asset query matches the assets table", "assets" in r["matched_tables"])
check("asset query pulls sites + locations",
      {"sites", "locations"} <= set(r["related_tables"]))
check("relationship: assets.site_id -> sites", ("assets", "site_id", "sites") in rels(r))
check("relationship: assets.location_id -> locations", ("assets", "location_id", "locations") in rels(r))
check("primary keys carried for the cluster", r["primary_keys"].get("assets") == "id")

r2 = SG.expand_query("work order vendor assigned", depth=1)
check("WO query matches work_orders", "work_orders" in r2["matched_tables"])
check("WO query pulls assets", "assets" in r2["related_tables"])
check("relationship: work_orders.asset_id -> assets", ("work_orders", "asset_id", "assets") in rels(r2))

r3 = SG.expand_query("compliance certificate for asset", depth=1)
check("certificate query pulls assets", "assets" in (r3["matched_tables"] + r3["related_tables"]))
check("relationship: certificates.asset_id -> assets", ("certificates", "asset_id", "assets") in rels(r3))

# ── junk / empty query degrades gracefully ───────────────────────────────────────
check("nonsense query -> no cluster", SG.expand_query("zzz qqq")["matched_tables"] == [])

print("\nALL TESTS PASSED")
