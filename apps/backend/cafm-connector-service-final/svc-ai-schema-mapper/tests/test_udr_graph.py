"""Tests for the Feature 7 relationship-graph builder. Run: python tests/test_udr_graph.py"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from udr.primitives import classify_columns  # noqa: E402
from udr.graph import build_relationship_graph, build_work_cloud, neighbors  # noqa: E402

assets = [
    {"asset_id": "A1", "make": "Siemens", "site_code": "S1"},
    {"asset_id": "A2", "make": "Siemens", "site_code": "S1"},
    {"asset_id": "A3", "make": "Trane", "site_code": "S2"},
]
sites = [
    {"site_code": "S1", "city": "Sharjah"},
    {"site_code": "S2", "city": "Ajman"},
]
work_orders = [
    {"wo_no": "W1", "asset_id": "A1"},
    {"wo_no": "W2", "asset_id": "A2"},
    {"wo_no": "W3", "asset_id": "A3"},
]
tables = {
    "assets": {"rows": assets, "columns": ["asset_id", "make", "site_code"], "pk": ["asset_id"]},
    "sites": {"rows": sites, "columns": ["site_code", "city"], "pk": ["site_code"]},
    "work_orders": {"rows": work_orders, "columns": ["wo_no", "asset_id"], "pk": ["wo_no"]},
}
pk_by_table = {"assets": ["asset_id"], "sites": ["site_code"], "work_orders": ["wo_no"]}


def check(name, cond):
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        raise AssertionError(name)


cls = classify_columns(tables, pk_by_table)
graph = build_relationship_graph(
    tables, cls, dest_table_by_source={"assets": "assets", "sites": "sites", "work_orders": "work_orders"}
)

rels = graph["relationships"]
# expect FK edges: assets.site_code -> sites.site_code, work_orders.asset_id -> assets.asset_id
edge_set = {(r["src_entity"], r["src_column"], r["dst_entity"], r["dst_column"]) for r in rels}
check("edge assets.site_code -> sites.site_code",
      ("assets", "site_code", "sites", "site_code") in edge_set)
check("edge work_orders.asset_id -> assets.asset_id",
      ("work_orders", "asset_id", "assets", "asset_id") in edge_set)
check("all edges provenance schema", all(r["provenance"] == "schema" for r in rels))
check("all edges confidence >= 0.95", all(r["confidence"] >= 0.95 for r in rels))

# final table metadata
meta = {m["table"]: m for m in graph["tables"]}
check("assets fk_count 1", meta["assets"]["fk_count"] == 1 and meta["assets"]["fk_columns"] == ["site_code"])
check("sites fk_count 0", meta["sites"]["fk_count"] == 0)
check("sites pk_associations include assets",
      any(a["from_table"] == "assets" for a in meta["sites"]["pk_associations"]))
check("assets column_count 3 + samples",
      meta["assets"]["column_count"] == 3 and len(meta["assets"]["samples_by_column"]["make"]) <= 3)

# final column metadata (4 dims)
cmeta = {(m["source_table"], m["column"]): m for m in graph["columns"]}
check("col meta assets.site_code FK + dest", cmeta[("assets", "site_code")]["classification"] == "FK"
      and cmeta[("assets", "site_code")]["dest_udr_table"] == "assets")

# work cloud
nb = {n["table"] for n in neighbors(graph, "assets")}
check("assets work cloud includes sites + work_orders", {"sites", "work_orders"} <= nb)

# ── F7-3 work cloud (rich, both directions, from edge list) ──────────────────
cloud = build_work_cloud(rels, "assets")
check("work cloud entity + related types",
      cloud["entity"] == "assets" and set(cloud["related_entity_types"]) == {"sites", "work_orders"})
by_related = {i["related_entity"]: i for i in cloud["relationships"]}
check("out edge assets -> sites carries columns + metadata",
      by_related["sites"]["direction"] == "out"
      and by_related["sites"]["via_column"] == "site_code"
      and by_related["sites"]["related_column"] == "site_code"
      and by_related["sites"]["rel_type"] == "REFERENCES"
      and by_related["sites"]["provenance"] == "schema"
      and by_related["sites"]["confidence"] >= 0.95)
check("in edge work_orders -> assets is direction 'in'",
      by_related["work_orders"]["direction"] == "in"
      and by_related["work_orders"]["via_column"] == "asset_id"
      and by_related["work_orders"]["related_column"] == "asset_id")
# an entity with no edges -> empty cloud (not an error)
empty_cloud = build_work_cloud(rels, "nonexistent")
check("unknown entity -> empty work cloud",
      empty_cloud["related_entity_types"] == [] and empty_cloud["relationships"] == [])

print("\nALL TESTS PASSED")
