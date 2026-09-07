"""Tests for Feature 7 instance-level traversal planner (pure).
Run: python tests/test_udr_instance_cloud.py"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from udr.instance_cloud import build_traversal_plan, is_safe_identifier  # noqa: E402


def check(name, cond):
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        raise AssertionError(name)


# ── identifier safety (every name placed in raw SQL must pass) ───────────────
check("plain table ok", is_safe_identifier("assets") is True)
check("underscore col ok", is_safe_identifier("asset_id") is True and is_safe_identifier("_x") is True)
check("leading digit rejected", is_safe_identifier("1bad") is False)
check("dash rejected", is_safe_identifier("a-b") is False)
check("space rejected", is_safe_identifier("drop table") is False)
check("semicolon/injection rejected", is_safe_identifier("x; DROP TABLE y") is False)
check("empty/None rejected", is_safe_identifier("") is False and is_safe_identifier(None) is False)

edges = [
    {"src_entity": "assets", "src_column": "site_id", "dst_entity": "sites", "dst_column": "site_id"},
    {"src_entity": "work_orders", "src_column": "asset_id", "dst_entity": "assets", "dst_column": "asset_id"},
]

# ── plan for 'assets' (has both an outbound FK and an inbound reference) ─────
pa = build_traversal_plan(edges, "assets")
check("assets pk inferred from inbound edge", pa["pk_column"] == "asset_id")
check("assets outbound -> sites via site_id",
      pa["outbound"] == [{"via_column": "site_id", "related_table": "sites", "related_pk_column": "site_id"}])
check("assets inbound <- work_orders via asset_id",
      pa["inbound"] == [{"related_table": "work_orders", "related_fk_column": "asset_id"}])

# ── plan for 'sites' (only inbound) ─────────────────────────────────────────
ps = build_traversal_plan(edges, "sites")
check("sites pk inferred", ps["pk_column"] == "site_id")
check("sites has no outbound", ps["outbound"] == [])
check("sites inbound <- assets", ps["inbound"] == [{"related_table": "assets", "related_fk_column": "site_id"}])

# ── explicit pk override + unsafe override rejected ─────────────────────────
check("explicit pk honoured", build_traversal_plan(edges, "assets", pk_column="custom_id")["pk_column"] == "custom_id")
check("unsafe pk override -> None", build_traversal_plan(edges, "assets", pk_column="a b")["pk_column"] is None)

# ── malicious edge identifiers are filtered out of the plan ─────────────────
evil = [
    {"src_entity": "assets", "src_column": "site_id", "dst_entity": "sites; DROP TABLE x", "dst_column": "site_id"},
    {"src_entity": "wo", "src_column": "asset id", "dst_entity": "assets", "dst_column": "asset_id"},
]
pe = build_traversal_plan(evil, "assets")
check("unsafe outbound table dropped", pe["outbound"] == [])
check("unsafe inbound column dropped", pe["inbound"] == [])

# ── no edges -> empty plan, pk None ─────────────────────────────────────────
pn = build_traversal_plan([], "assets")
check("no edges -> empty plan", pn["pk_column"] is None and pn["outbound"] == [] and pn["inbound"] == [])

print("\nALL TESTS PASSED")
