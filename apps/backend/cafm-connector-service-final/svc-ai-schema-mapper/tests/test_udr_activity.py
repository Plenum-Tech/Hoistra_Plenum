"""Tests for the Feature 4 UDR activity-entry builder. Run: python tests/test_udr_activity.py"""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from types import SimpleNamespace  # noqa: E402

from udr.activity import (  # noqa: E402
    build_stage_meta,
    build_summary_metrics,
    build_udr_activity_entry,
    format_activity_timestamp,
    notif_color,
    UDR_STAGES,
)


def check(name, cond):
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        raise AssertionError(name)


# ── timestamp format (AL.1 AC3) ──────────────────────────────────────────────
check("timestamp 2:32 pm 24 April 26",
      format_activity_timestamp(datetime(2026, 4, 24, 14, 32)) == "2:32 pm 24 April 26")
check("timestamp 11:50 am 24 April 26",
      format_activity_timestamp(datetime(2026, 4, 24, 11, 50)) == "11:50 am 24 April 26")
check("timestamp midnight 12:05 am",
      format_activity_timestamp(datetime(2026, 1, 3, 0, 5)) == "12:05 am 3 January 26")

# ── notif colour mapping (AL.1 AC4) ──────────────────────────────────────────
check("completed->green", notif_color("completed") == "green")
check("escalated->orange", notif_color("escalated") == "orange")
check("failed->red", notif_color("failed") == "red")
check("pending->red", notif_color("pending_human_input") == "red")

# ── happy-path UDR entry (AL.6 AC2) ──────────────────────────────────────────
e = build_udr_activity_entry("run1", documents_ingested=3, table_count=20, column_count=101,
                             script_ref="UDR-SCRIPT-7")
check("outcome exact format (AL.6 AC2)",
      e["outcome"] == "3 documents ingested, UDR run successful, UDR script saved — 20 tables, 101 columns")
check("status completed", e["status"] == "completed" and e["notif_color"] == "green")
check("refs include run + script", e["refs"]["udr_run_id"] == "run1" and e["refs"]["script"] == "UDR-SCRIPT-7")
check("processing log covers every Feature-7 stage",
      len(e["processing_log"]["stages"]) == len(UDR_STAGES))
stage_keys = {s["stage"] for s in e["processing_log"]["stages"]}
check("stages include test1/test2/hierarchy", {"test1", "test2", "hierarchy", "preprocessing"} <= stage_keys)

# ── blocking tests -> pending + flag counts in outcome (AL.6 AC5) ─────────────
t1 = {"failed": 2, "passed": 8, "total_chunks": 10, "blocks_udr": True}
t2 = {"flagged_unexplained": 1, "explained": 0, "similarity_hits": 1, "blocks_udr": True}
eb = build_udr_activity_entry("run2", documents_ingested=1, table_count=5, column_count=12,
                              test1_report=t1, test2_report=t2)
check("blocked -> pending_human_input + red", eb["status"] == "pending_human_input" and eb["notif_color"] == "red")
check("outcome says blocked", "blocked — review required" in eb["outcome"])
check("outcome includes Test 1 flag count (plural)", "2 Test 1 items flagged for review" in eb["outcome"])
check("outcome includes Test 2 flag count (singular)", "1 Test 2 item flagged for review" in eb["outcome"])
check("test flag fields", eb["test1_flags"] == 2 and eb["test2_flags"] == 1)
# test summaries auto-filled into the stage nodes
t1_stage = next(s for s in eb["processing_log"]["stages"] if s["stage"] == "test1")
check("test1 stage summary mentions chunks", "chunks anchored to a primary key" in t1_stage["summary"])

# ── mapping-decision traceability passthrough (AL.6 AC4) ──────────────────────
md = [{"source_table": "Transportation", "source_column": "From City", "confidence": 0.98,
       "dest_table": "transportation", "dest_column": "from_city"}]
em = build_udr_activity_entry("run3", documents_ingested=1, table_count=1, column_count=4, mapping_decisions=md)
check("mapping_decisions traceable", em["processing_log"]["mapping_decisions"] == md)

# ── mapping-decision NORMALIZATION from producer-native keys (AL.6 AC4) ────────
# column-metadata producer emits column / dest_udr_table; graph edges emit src_*/dst_*.
# The builder must coerce both to the exact 5-key FE contract so nothing renders as
# "undefined.undefined".
raw = [
    {"source_table": "assets", "column": "make", "dest_udr_table": "ref_make"},
    {"src_entity": "work_orders", "src_column": "asset_id", "dst_entity": "assets",
     "dst_column": "id", "confidence": 0.97},
]
en = build_udr_activity_entry("run4", mapping_decisions=raw)
nd = en["processing_log"]["mapping_decisions"]
EXPECT_KEYS = {"source_table", "source_column", "confidence", "dest_table", "dest_column"}
check("normalized decisions expose exactly the 5 FE keys",
      all(set(d.keys()) == EXPECT_KEYS for d in nd))
check("column-metadata keys bridged (column/dest_udr_table)",
      nd[0]["source_table"] == "assets" and nd[0]["source_column"] == "make"
      and nd[0]["dest_table"] == "ref_make" and nd[0]["dest_column"] is None
      and nd[0]["confidence"] is None)
check("graph-edge keys bridged (src_*/dst_*)",
      nd[1]["source_table"] == "work_orders" and nd[1]["source_column"] == "asset_id"
      and nd[1]["dest_table"] == "assets" and nd[1]["dest_column"] == "id"
      and nd[1]["confidence"] == 0.97)

# ── per-stage execution metrics (AL.6 point 12) + expanded summary (point 7) ──────
def _fake_result(*, test2_blocks=False, test1_blocks=False):
    return SimpleNamespace(
        classification={
            ("assets", "id"): {"classification": "PK"},
            ("work_orders", "asset_id"): {"classification": "FK"},
            ("work_orders", "site"): {"classification": "SHARED_ATTRIBUTE"},
            ("assets", "name"): {"classification": "ATTRIBUTE"},
        },
        consolidated_groups=[{"members": ["works", "work_orders"]}],
        unique_table_candidates=[{"table": "maybe_dup"}],
        mapping_decisions=[
            {"source_table": "assets", "source_column": "id", "dest_table": "assets",
             "dest_column": "id", "confidence": 0.98},
        ],
        column_intelligence={
            "summary": {"groups": 3, "columns_mapped": 7, "format_survivors": 2, "format_pairs_scored": 5},
            "groups": [{"group_id": "g1", "members": ["a", "b"], "canonical_name": "asset_id"}],
            "fk_candidates": [],
        },
        test1_report={"total_chunks": 10, "passed": 9, "failed": 1, "blocks_udr": test1_blocks},
        test2_report=(
            {"flags": [{"table_a": "a", "column_a": "x", "table_b": "b", "column_b": "y", "overlap": 0.5}],
             "similarity_hits": 1, "explained": 0, "flagged_unexplained": 1, "blocks_udr": True}
            if test2_blocks else
            {"flags": [], "similarity_hits": 4, "explained": 4, "flagged_unexplained": 0, "blocks_udr": False}
        ),
        relationship_count=2,
        table_count=4,
        column_count=12,
        stage_times={},
        stage_durations={},
        table_metadata=[],
    )


fake = _fake_result()
meta = build_stage_meta(fake)

# every UDR stage carries agent · tool · status (the audit record, not a bare log line)
check("every stage has agent/tool/status",
      all(meta[k].get("agent") and meta[k].get("tool") and meta[k].get("status") for k, _ in UDR_STAGES))

# input/output counts derived ONLY from run data on the derivable stages
check("hierarchy in/out = tables->relationships",
      meta["hierarchy"]["input_count"] == 4 and meta["hierarchy"]["output_count"] == 2)
check("column_to_destination in/out = columns->mapped",
      meta["column_to_destination"]["input_count"] == 12 and meta["column_to_destination"]["output_count"] == 7)
check("column_within_source out = classified (PK+FK+Shared)",
      meta["column_within_source"]["input_count"] == 12 and meta["column_within_source"]["output_count"] == 3)
check("unique_tables in/out (source->unique, +collapsed)",
      meta["unique_tables"]["input_count"] == 5 and meta["unique_tables"]["output_count"] == 4)
check("test1 in/out = chunks->passed",
      meta["test1"]["input_count"] == 10 and meta["test1"]["output_count"] == 9)
check("test2 in/out = hits->explained",
      meta["test2"]["input_count"] == 4 and meta["test2"]["output_count"] == 4)
check("non-blocking test1 status completed", meta["test1"]["status"] == "completed")

# blocked tests flip the stage status (so the FE renders the blocked-note)
meta_b = build_stage_meta(_fake_result(test2_blocks=True))
check("blocking test2 -> status blocked", meta_b["test2"]["status"] == "blocked")
meta_t1b = build_stage_meta(_fake_result(test1_blocks=True))
check("blocking test1 -> status error", meta_t1b["test1"]["status"] == "error")

# steps carry the stage metrics through to the FE contract
e2 = build_udr_activity_entry(
    "runX", documents_ingested=2, table_count=4, column_count=12,
    test1_report=fake.test1_report, test2_report=fake.test2_report,
    stage_meta=meta, extra_metrics=build_summary_metrics(fake),
)
steps = e2["processing_log"]["steps"]
h_step = next(s for s in steps if s.get("stage") == "hierarchy")
check("hierarchy step carries agent + in/out + status",
      h_step["agent"] == "Relationship Grapher" and h_step["input_count"] == 4
      and h_step["output_count"] == 2 and h_step["status"] == "completed")
d_step = steps[0]
check("decomposition step carries planner metrics",
      d_step["stage"] == "decomposition" and d_step["agent"] == "Query Planner"
      and d_step["output_count"] == len(UDR_STAGES))

# expanded Activity-Summary metric set (point 7), de-duped against the base chips
labels = [m["label"] for m in e2["refs"]["metrics"]]
check("base summary chips still present",
      {"documents", "dest. tables", "dest. columns"} <= set(labels))
check("expanded summary chips present",
      {"primary keys", "foreign keys", "shared attrs", "relationships",
       "auto-consolidated", "review", "columns mapped", "chunks"} <= set(labels))
check("summary metric labels are unique (de-duped)", len(labels) == len(set(labels)))

sm = build_summary_metrics(fake)
check("build_summary_metrics omits zero metrics",
      all(isinstance(m["value"], int) and m["value"] > 0 for m in sm))
check("review chip tone is red",
      next(m for m in sm if m["label"] == "review")["tone"] == "red")

print("\nALL TESTS PASSED")
