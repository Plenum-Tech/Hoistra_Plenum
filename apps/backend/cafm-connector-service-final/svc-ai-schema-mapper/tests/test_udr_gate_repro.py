"""Repro: at the pre-semantic gate, does build_run_activity_entry() generate the column
stages from the gate payload's column_intelligence? Mirrors _sync_run_activity's pure path
with the EXACT data shape from a live awaiting_review status payload (run 6319af33…).
Run: python tests/test_udr_gate_repro.py"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from udr.run_activity import (  # noqa: E402
    build_run_activity_entry,
    mapping_decisions_from_gate,
    stage_extras_from_column_intelligence,
    summary_metrics_from_column_intelligence,
)


def check(name, cond):
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        raise AssertionError(name)


# ── EXACT shape from the live pending_gate_payload (run 6319af33-…) ───────────────────
column_intelligence = {
    "summary": {
        "fk": 3, "pk": 5, "groups": 3, "shared": 22, "fk_groups": 2, "pk_groups": 0,
        "confidence": 0.9671, "shared_groups": 1, "columns_mapped": 24, "value_survivors": 6,
        "columns_analyzed": 30, "format_survivors": 89, "format_pairs_scored": 357,
    },
    "groups": [
        {"group_id": "G1", "canonical_name": "asset_id",
         "members": ["assets.id", "assets.asset_code", "workorders.asset_id"]},
        {"group_id": "G2", "canonical_name": "site_id",
         "members": ["assets.site_ref", "sites.site_id", "resources.site_ref"]},
        {"group_id": "G3", "canonical_name": "trade",
         "members": ["vendors.trade", "resources.trade"]},
    ],
    "fk_candidates": [
        {"src_table": "WorkOrders", "src_column": "asset_id", "dst_table": "Assets", "dst_column": "id"},
        {"src_table": "Assets", "src_column": "site_ref", "dst_table": "Sites", "dst_column": "site_id"},
        {"src_table": "Resources", "src_column": "site_ref", "dst_table": "Sites", "dst_column": "site_id"},
    ],
}
gate_payload = {
    "gate": "pre_semantic",
    "suggested_target_by_table": {
        "Sites": "sites", "Assets": "assets", "Vendors": "vendors",
        "Resources": "resources", "WorkOrders": "workorders",
    },
    "auto_approved_by_table": {},
    "review_items_by_table": {
        "Sites": [
            {"source_field": "city", "target_field": "city", "confidence": 0.98},
            {"source_field": "manager_email", "target_field": "manager_email", "confidence": 0.98},
        ],
        "Assets": [
            {"source_field": "asset_code", "target_field": "asset_code", "confidence": 0.98, "is_primary_key": True},
            {"source_field": "description", "target_field": "notes", "confidence": 0.96},
        ],
        "WorkOrders": [
            {"source_field": "priority", "target_field": "wo_priority", "confidence": 0.98, "b21_new_column": True},
        ],
    },
    "column_intelligence": column_intelligence,
}

# ── Mirror _sync_run_activity's pure path ─────────────────────────────────────────────
done_ids = {1, 2}                       # nodes 1 + 2 are status "complete" in node_logs
md = mapping_decisions_from_gate(gate_payload)
# CI compaction (exactly what _sync_run_activity persists)
ci = {
    "summary": column_intelligence["summary"],
    "groups": [
        {"group_id": g["group_id"], "members": g["members"][:3], "canonical_name": g["canonical_name"]}
        for g in column_intelligence["groups"]
    ],
    "fk_candidates": column_intelligence["fk_candidates"],
}
stage_extras = {
    # deterministic evidence from node 2's output (table_routing + overall_confidence 0.971)
    "deterministic": {
        "confidence": 0.971,
        "table": {"columns": ["Source table", "→ Destination"],
                  "rows": [["Assets", "assets"], ["Sites", "sites"], ["WorkOrders", "workorders"],
                           ["Vendors", "vendors"], ["Resources", "resources"]]},
    },
}
for stage, sx in stage_extras_from_column_intelligence(ci, mapping_decisions=md).items():
    stage_extras.setdefault(stage, {}).update(sx)

entry = build_run_activity_entry(
    "6319af33-1f29-4db6-926e-884b29bfd7fc",
    migration_status="awaiting_review",
    completed_node_ids=done_ids,
    pending_gate_type="pre_semantic",
    tables=5, columns=8, t1_mapped=24,
    mapping_decisions=md,
    stage_extras=stage_extras,
    extra_metrics=summary_metrics_from_column_intelligence(ci),
)

steps = entry["processing_log"]["steps"]
labels = [s["label"] for s in steps]
print("\n--- generated processing_log.steps ---")
for s in steps:
    extras = []
    if s.get("chips"):
        extras.append(f"chips={s['chips']}")
    if s.get("table"):
        extras.append(f"table[{len(s['table']['rows'])} rows]")
    print(f"  [{s['status']:9}] {s['stage']:24} {s['label']}  {' '.join(extras)}")
print("--- Section-1 metric chips ---")
print("  " + ", ".join(f"{m['value']} {m['label']}" for m in entry["refs"]["metrics"]))
print()

stages = {s["stage"] for s in steps}
check("deterministic stage generated", "deterministic" in stages)
check("column_within_source stage generated", "column_within_source" in stages)
check("column_canonicalisation stage generated", "column_canonicalisation" in stages)
check("column_to_destination stage generated", "column_to_destination" in stages)


def by_stage(st):
    return next(s for s in steps if s["stage"] == st)


check("column_within_source carries PK/FK/shared chips",
      "5 Primary Keys" in by_stage("column_within_source")["chips"]
      and "3 Foreign Keys" in by_stage("column_within_source")["chips"]
      and "22 Shared Attributes" in by_stage("column_within_source")["chips"])
check("column_canonicalisation carries the Group/Members/Canonical table",
      by_stage("column_canonicalisation")["table"]["columns"] == ["Group", "Members", "Canonical name"]
      and len(by_stage("column_canonicalisation")["table"]["rows"]) == 3)
check("column_to_destination carries the source→destination table",
      len(by_stage("column_to_destination")["table"]["rows"]) > 0)
check("Section-1 chips include primary/foreign/shared/columns mapped",
      {m["label"] for m in entry["refs"]["metrics"]} >= {"primary keys", "foreign keys", "shared attrs", "columns mapped"})
check("semantic shown as queued (no evidence, node 4 not done)",
      "semantic" in stages and by_stage("semantic")["status"] == "pending")

print("\nALL TESTS PASSED — the column stages ARE generated in processing_log.steps from this payload.")
