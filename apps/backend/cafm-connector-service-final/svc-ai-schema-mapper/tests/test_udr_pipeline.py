"""Tests for the UDR pipeline orchestrator + persistence/emit wiring.
Pure (no DB): run: python tests/test_udr_pipeline.py"""
import asyncio
import inspect
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from udr import emit as E  # noqa: E402
from udr import persistence as P  # noqa: E402
from udr.pipeline import UdrRunResult, run_udr_pipeline  # noqa: E402


def check(name, cond):
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        raise AssertionError(name)


# ── fixture: assets.site_id references sites.site_id (a PK) ───────────────────
sites = [
    {"site_id": "S1", "site_name": "Tower A"},
    {"site_id": "S2", "site_name": "Tower B"},
    {"site_id": "S3", "site_name": "Tower C"},
]
assets = [
    {"asset_id": "A1", "site_id": "S1", "make": "Carrier"},
    {"asset_id": "A2", "site_id": "S1", "make": "Trane"},
    {"asset_id": "A3", "site_id": "S2", "make": "Carrier"},
    {"asset_id": "A4", "site_id": "S3", "make": "Daikin"},
]
res = run_udr_pipeline(
    {"assets": assets, "sites": sites},
    run_id="run-1",
    documents_ingested=2,
    dest_table_by_source={"assets": "assets", "sites": "sites"},
)

check("returns UdrRunResult", isinstance(res, UdrRunResult))
check("2 tables", res.table_count == 2)
check("5 columns total", res.column_count == 5)

# ── primary keys detected per table (S2) ─────────────────────────────────────
tbl = {t["table"]: t for t in res.table_metadata}
check("assets PK = asset_id", tbl["assets"]["primary_key"] == ["asset_id"])
check("sites PK = site_id", tbl["sites"]["primary_key"] == ["site_id"])

# ── one FK edge assets.site_id → sites.site_id (S10) ─────────────────────────
rels = res.graph["relationships"]
check("exactly one relationship edge", len(rels) == 1 and res.relationship_count == 1)
edge = rels[0]
check(
    "edge assets.site_id -> sites.site_id (REFERENCES/schema)",
    edge["src_entity"] == "assets"
    and edge["src_column"] == "site_id"
    and edge["dst_entity"] == "sites"
    and edge["dst_column"] == "site_id"
    and edge["rel_type"] == "REFERENCES"
    and edge["provenance"] == "schema",
)
check("edge confidence (referential integrity) >= 0.95", edge["confidence"] >= 0.95)

# ── Test 1 vacuous pass (no chunks in structured flow) ───────────────────────
check(
    "Test 1 vacuous pass (0 chunks, not blocking)",
    res.test1_report["total_chunks"] == 0
    and res.test1_report["pass_rate"] == 1.0
    and res.test1_report["blocks_udr"] is False,
)

# ── Test 2 ran; the site_id overlap is explained by the FK (not flagged) ─────
check("Test 2 ran", res.test2_report["test"] == "test2_column_fk")
check("Test 2 saw the site_id similarity", res.test2_report["similarity_hits"] >= 1)
check("Test 2 not blocked (overlap explained by FK)", res.test2_report["blocks_udr"] is False)
check("run not blocked -> completed", res.status == "completed" and res.blocked is False)

# ── mapping_decisions: one per column, exact 5-key contract (AL.6 AC4) ────────
EXPECT = {"source_table", "source_column", "confidence", "dest_table", "dest_column"}
check("mapping_decisions: 5 entries", len(res.mapping_decisions) == 5)
check("decisions have exactly the 5 FE keys", all(set(d.keys()) == EXPECT for d in res.mapping_decisions))
check("decisions carry dest_table", all(d["dest_table"] in ("assets", "sites") for d in res.mapping_decisions))

# ── blocking propagation: a fabricated chunk pointing at a non-PK blocks ──────
blocked = run_udr_pipeline(
    {"assets": assets, "sites": sites},
    run_id="run-2",
    chunks=[{"chunk_id": "c1", "entity_id": "NOT_A_PK"}],
)
check("bad chunk -> Test 1 blocks", blocked.test1_report["blocks_udr"] is True)
check("blocked run -> pending_human_input", blocked.status == "pending_human_input" and blocked.blocked is True)

# ── persistence pure mapper ──────────────────────────────────────────────────
cols = P._rel_to_columns(edge, run_id="run-1", organization_id=None)
check(
    "edge -> UdrEntityRelationship columns",
    cols["run_id"] == "run-1"
    and cols["src_entity"] == "assets"
    and cols["dst_column"] == "site_id"
    and cols["rel_type"] == "REFERENCES"
    and cols["provenance"] == "schema"
    and cols["confidence"] >= 0.95,
)
# a degenerate edge missing column names must not become NULL (would defeat ON CONFLICT)
sentinel = P._rel_to_columns({"src_entity": "a", "dst_entity": "b"}, run_id="r")
check("NULL edge columns coerced to '' (total unique key)",
      sentinel["src_column"] == "" and sentinel["dst_column"] == "")

# ── async surface + DB-free short-circuit ────────────────────────────────────
check("emit_udr_activity is async", inspect.iscoroutinefunction(E.emit_udr_activity))
check("persist_udr_run is async", inspect.iscoroutinefunction(P.persist_udr_run))
check("persist_run_graph is async", inspect.iscoroutinefunction(P.persist_run_graph))
check("persist_relationships([]) short-circuits to 0 (no DB)",
      asyncio.run(P.persist_relationships([], run_id="r")) == 0)

# ── F7-3 work cloud: async + unscoped cross-tenant guard (no DB) ──────────────
check("query_work_cloud is async", inspect.iscoroutinefunction(P.query_work_cloud))
unscoped = asyncio.run(P.query_work_cloud("assets"))
check("unscoped work cloud returns empty (no cross-tenant scan)",
      unscoped["entity"] == "assets"
      and unscoped["related_entity_types"] == []
      and unscoped["relationships"] == [])

print("\nALL TESTS PASSED")
