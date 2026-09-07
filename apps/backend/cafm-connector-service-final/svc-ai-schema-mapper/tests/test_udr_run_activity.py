"""Tests for the progressive per-run Activity builder + decision merge.
Run: python tests/test_udr_run_activity.py"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from udr.activity_persist import _merge_decisions, _merge_review_resolutions  # noqa: E402
from udr.run_activity import (  # noqa: E402
    build_partial_table_resolution,
    build_run_activity_entry,
    mapping_decisions_from_gate,
    stage_extras_from_column_intelligence,
    summary_metrics_from_column_intelligence,
    weave_review_resolution_steps,
)


def check(name, cond):
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        raise AssertionError(name)


def stage_labels(entry):
    return [s["label"] for s in entry["processing_log"]["steps"]]


# ── mapping_decisions_from_gate ───────────────────────────────────────────────────
gate = {
    "suggested_target_by_table": {"Assets": "assets", "Vendors": "vendors"},
    "auto_approved_by_table": {
        "Assets": [
            {"source_field": "asset_code", "target_field": "asset_code", "confidence": 0.98},
            {"source_field": "model", "target_field": "model", "confidence": 0.95},
        ]
    },
    "review_items_by_table": {
        "Vendors": [
            {"source_field": "city", "target_field": "city", "confidence": 0.98},  # has target → real mapping
            {"source_field": "trade", "confidence": 0.74},                          # no target → Review Required
            {"source_field": "newthing", "confidence": 0.71, "b21_new_column": True},
        ]
    },
}
md = mapping_decisions_from_gate(gate)
check("5 decisions from gate", len(md) == 5)
check("auto-approved resolves to dest", md[0]["dest_table"] == "assets" and md[0]["dest_column"] == "asset_code")
check("auto-approved confidence", md[0]["confidence"] == 0.98)
# review items that carry a target render the REAL mapping (not "Review Required")
city = next(d for d in md if d["source_column"] == "city")
check("review item WITH target → real mapping", city["dest_table"] == "vendors" and city["dest_column"] == "city")
trade = next(d for d in md if d["source_column"] == "trade")
check("review item WITHOUT target → Review Required", trade["dest_table"] is None and trade["dest_column"] == "Review Required")
newt = next(d for d in md if d["source_column"] == "newthing")
check("b21_new_column → New Column Suggested", newt["dest_column"] == "New Column Suggested")
check("non-dict gate → []", mapping_decisions_from_gate(None) == [])

# ── _merge_decisions append-only / immutable ──────────────────────────────────────
old = [{"source_table": "Assets", "source_column": "asset_code", "dest_column": "asset_code"}]
new = [
    {"source_table": "Assets", "source_column": "asset_code", "dest_column": "CHANGED"},  # existing → must NOT overwrite
    {"source_table": "Vendors", "source_column": "trade", "dest_column": "Review Required"},  # new → append
]
merged = _merge_decisions(old, new)
check("merge keeps existing row immutable", merged[0]["dest_column"] == "asset_code")
check("merge appends the new row", len(merged) == 2 and merged[1]["source_column"] == "trade")

# ── _merge_review_resolutions append-only / keyed by gate_type:at ──────────────────
r1 = {"gate_type": "pre_semantic", "at": "t1", "total": 3}
r1b = {"gate_type": "pre_semantic", "at": "t1", "total": 99}  # same key → must NOT duplicate
r2 = {"gate_type": "field_mapping", "at": "t2", "total": 5}   # different key → append
rmerged = _merge_review_resolutions([r1], [r1b, r2])
check("review-resolution merge dedupes by gate_type:at", len(rmerged) == 2)
check("review-resolution merge keeps the first record immutable", rmerged[0]["total"] == 3)
check("review-resolution merge appends a new gate", rmerged[1]["gate_type"] == "field_mapping")

# ── run entry weaves the resolution between deterministic and semantic ─────────────
run_steps = build_run_activity_entry(
    "run1", migration_status="complete", completed_node_ids={1, 2, 4, 5, 6, 7, 8, 9},
)["processing_log"]["steps"]
woven_run = weave_review_resolution_steps(
    run_steps, [{"gate_type": "pre_semantic", "at": "t1", "total": 2, "approved": ["A.x"], "rows": [["A.x", "Approved", "Bob"]]}]
)
wlabels = [s["label"] for s in woven_run]
check("resolution woven into the SAME run steps (one activity)",
      "Human review resolution" in wlabels
      and wlabels.index("Layer 1 · Deterministic table mapping (exact + Levenshtein + RAG/alias)")
          < wlabels.index("Human review resolution")
          < wlabels.index("Layer 1 · Semantic table mapping (NLP + metadata)"))

# ── build_run_activity_entry — mid-run (nodes 1,2 done, running) ──────────────────
mid = build_run_activity_entry(
    "run1", migration_status="running", completed_node_ids={1, 2},
    node_meta={1: {"at": "t1", "duration_ms": 100}, 2: {"at": "t2", "duration_ms": 200}},
    tables=5, columns=30, t1_mapped=26,
)
check("mid-run status running", mid["status"] == "running")
labels = stage_labels(mid)
check("first step is decomposition", labels[0] == "Query decomposition")
check("includes pre-processing (node 1)", any("Pre-processing" in l for l in labels))
check("includes unique table identification (node 1)", any("Unique table identification" in l for l in labels))
check("includes deterministic mapping (node 2)", any("Deterministic table mapping" in l for l in labels))
_mid_steps = {s.get("stage"): s for s in mid["processing_log"]["steps"]}
check("semantic shown as QUEUED mid-run (node 4 not done)", _mid_steps.get("semantic", {}).get("status") == "pending")
check("post-write Test 1 shown as QUEUED mid-run", _mid_steps.get("test1", {}).get("status") == "pending")
check("Run started lifecycle step present", any(s.get("stage") == "run_started" for s in mid["processing_log"]["steps"]))
check("entry_kind=run marker present", mid["refs"]["entry_kind"] == "run")
check("metrics carry dest. tables + columns + auto-mapped",
      {m["label"] for m in mid["refs"]["metrics"]} >= {"dest. tables", "dest. columns", "auto-mapped"})

# ── live richness: stages read like the completed card (description + count chips) ──
def step_by_stage(entry, stage):
    return next((s for s in entry["processing_log"]["steps"] if s.get("stage") == stage), None)

det = step_by_stage(mid, "deterministic")
check("deterministic stage carries a business description", bool(det and det.get("text")))
check("deterministic stage shows the Tier-1 count chip",
      bool(det and any("auto-mapped (Tier 1)" in c for c in (det.get("chips") or []))))
pre = step_by_stage(mid, "preprocessing")
check("preprocessing chip shows columns identified", bool(pre and any("30 column" in c for c in (pre.get("chips") or []))))
uniq = step_by_stage(mid, "unique_tables")
check("unique_tables chip shows tables identified", bool(uniq and any("5 table" in c for c in (uniq.get("chips") or []))))

# ── running stage + per-stage extras (deterministic confidence + routing table) ─────
rich = build_run_activity_entry(
    "run1", migration_status="running", completed_node_ids={1}, running_node_ids={2},
    tables=5, columns=30,
    stage_extras={"deterministic": {"confidence": 0.96,
                                    "table": {"columns": ["Source table", "→ Destination"], "rows": [["Assets", "assets"]]}}},
)
det2 = step_by_stage(rich, "deterministic")
check("currently-running stage renders status=running", bool(det2 and det2["status"] == "running"))
check("stage_extras confidence attached to the stage", bool(det2 and det2.get("confidence") == 0.96))
check("stage_extras routing table attached to the stage", bool(det2 and det2.get("table", {}).get("rows") == [["Assets", "assets"]]))
check("not-yet-reached semantic shown as QUEUED",
      bool(step_by_stage(rich, "semantic")) and step_by_stage(rich, "semantic")["status"] == "pending")

# ── Column Intelligence → per-stage extras (PK/FK/shared, groups, canonical, mapping) ──
ci = {
    "summary": {"pk": 5, "fk": 3, "shared": 22, "groups": 3,
                "format_pairs_scored": 357, "format_survivors": 89, "columns_mapped": 21},
    "fk_candidates": [{}, {}, {}],
    "groups": [
        {"group_id": "G1", "members": ["assets.id", "assets.asset_code", "workorders.asset_id"], "canonical_name": "asset_id"},
        {"group_id": "G2", "members": ["assets.site_ref", "sites.site_id"], "canonical_name": "site_id"},
    ],
}
ci_md = [
    {"source_table": "Assets", "source_column": "asset_code", "dest_table": "assets", "dest_column": "asset_code", "confidence": 0.98},
    {"source_table": "Vendors", "source_column": "trade", "dest_table": None, "dest_column": "Review Required", "confidence": 0.74},
]
cex = stage_extras_from_column_intelligence(ci, mapping_decisions=ci_md)
# B17/B18 similarity keeps ONLY format-gate survivors + FK candidates (PK/FK/Shared + groups moved).
cws_chips = cex["column_within_source"]["chips"]
check("CI chip format-gate survivors", any("cross-table pairs" in c for c in cws_chips))
check("CI chip FK candidates", "3 FK candidates" in cws_chips)
check("PK/FK/Shared NOT on similarity stage", "5 Primary Keys" not in cws_chips)
# B20 classification carries the PK/FK/Shared chips.
cls_chips = cex["column_classification"]["chips"]
check("classification chip 5 Primary Keys", "5 Primary Keys" in cls_chips)
check("classification chip 3 Foreign Keys", "3 Foreign Keys" in cls_chips)
check("classification chip 22 Shared Attributes", "22 Shared Attributes" in cls_chips)
# B19 grouping carries the group count + the Group/Members/Canonical table.
check("grouping chip similar-column groups", "3 similar-column groups" in cex["column_grouping"]["chips"])
canon_tbl = cex["column_grouping"]["table"]
check("grouping table header", canon_tbl["columns"] == ["Group", "Members", "Canonical name"])
check("grouping table G1 row", canon_tbl["rows"][0] == ["G1", "assets.id, assets.asset_code, workorders.asset_id", "asset_id"])
# Unified names now carries just the "N groups unified" chip (no table).
check("unified-names chip", "3 groups unified" in cex["column_canonicalisation"]["chips"])
c2d = cex["column_to_destination"]
check("columns-mapped chip", "21 columns mapped to destination" in c2d["chips"])
check("column→dest table shows only resolved mappings", c2d["table"]["rows"] == [["asset_code (Assets)", "assets.asset_code", "98%"]])

sm = summary_metrics_from_column_intelligence(ci)
sm_map = {m["label"]: m["value"] for m in sm}
check("summary metrics = pk/fk/shared/columns mapped",
      sm_map == {"primary keys": 5, "foreign keys": 3, "shared attrs": 22, "columns mapped": 21})

# build_run_activity_entry merges extra_metrics into Section-1 + echoes extra_refs, and the
# column stage carries the CI table once its covering node has run.
e2 = build_run_activity_entry(
    "run1", migration_status="running", completed_node_ids={1, 2, 4, 5},
    stage_extras=cex, extra_metrics=sm, extra_refs={"column_intelligence": ci},
)
e2_metric_labels = {m["label"] for m in e2["refs"]["metrics"]}
check("extra_metrics merged into Section-1 chips",
      {"primary keys", "foreign keys", "shared attrs", "columns mapped"} <= e2_metric_labels)
check("extra_refs column_intelligence echoed into refs", e2["refs"].get("column_intelligence") is ci)
e2_c2d = step_by_stage(e2, "column_to_destination")
check("column_to_destination step carries the mapping table", bool(e2_c2d and e2_c2d.get("table", {}).get("rows")))
e2_cls = step_by_stage(e2, "column_classification")
check("column_classification step carries PK/FK/shared chips",
      bool(e2_cls and "5 Primary Keys" in (e2_cls.get("chips") or [])))

# ── AT THE PRE-SEMANTIC GATE: column stages must appear from CI evidence even though their
#    covering node (5) has NOT run (only node 1 done, paused awaiting review). This is the
#    exact bug — metrics loaded but column stages + tables missing. ──
gate_entry = build_run_activity_entry(
    "run1", migration_status="awaiting_review", completed_node_ids={1},
    pending_gate_type="pre_semantic",
    stage_extras={
        "deterministic": {"confidence": 0.97,
                          "table": {"columns": ["Source table", "→ Destination"], "rows": [["Assets", "assets"]]}},
        **cex,  # column_within_source / column_canonicalisation / column_to_destination (from CI)
    },
    extra_metrics=sm,
)
gate_stages = {s["stage"] for s in gate_entry["processing_log"]["steps"]}
check("gate row shows deterministic from evidence (node 2 not in done_ids)", "deterministic" in gate_stages)
check("gate row shows column_within_source from CI evidence", "column_within_source" in gate_stages)
check("gate row shows column_canonicalisation from CI evidence", "column_canonicalisation" in gate_stages)
check("gate row shows column_to_destination from CI evidence", "column_to_destination" in gate_stages)
check("gate row shows semantic as QUEUED (no evidence, node 4 not done)",
      bool(step_by_stage(gate_entry, "semantic")) and step_by_stage(gate_entry, "semantic")["status"] == "pending")
check("gate row shows table_prefixing as QUEUED (no evidence)",
      bool(step_by_stage(gate_entry, "table_prefixing")) and step_by_stage(gate_entry, "table_prefixing")["status"] == "pending")
_gcws = step_by_stage(gate_entry, "column_within_source")
check("evidence-only column stage reads as completed", _gcws and _gcws["status"] == "completed")
check("evidence-only grouping stage carries its Group/Members/Canonical table",
      bool(step_by_stage(gate_entry, "column_grouping").get("table", {}).get("rows")))

# ── at a gate → pending_human_input + mapping decisions carried ───────────────────
atgate = build_run_activity_entry(
    "run1", migration_status="awaiting_review", completed_node_ids={1, 2},
    pending_gate_type="field_mapping", mapping_decisions=md,
)
check("gate status pending_human_input", atgate["status"] == "pending_human_input")
check("mapping_decisions carried into the entry", len(atgate["processing_log"]["mapping_decisions"]) == 5)
# Center step 3 ("Schema Analysis — Semantic Review") must have a matching RHS audit step so
# both panels read in the same sequence (…2.12 → 3). Present once field-mapping decisions exist.
_sr = step_by_stage(atgate, "semantic_review")
check("gate entry has a durable Semantic Review step (center step 3)", bool(_sr))
check("Semantic Review reads running while at the field-mapping gate", bool(_sr) and _sr["status"] == "running")
check("Semantic Review step carries the source→destination decision table", bool(_sr and _sr.get("table", {}).get("rows")))
# A run with NO field-mapping decisions has no Semantic Review step (nothing to review yet).
_nosr = build_run_activity_entry("run1", migration_status="running", completed_node_ids={1})
check("no Semantic Review step before any field-mapping decisions", step_by_stage(_nosr, "semantic_review") is None)

# ── Node 3 ("Table & Column Mapping") is a background job ONLY after the user continues. ──
# While PAUSED at the table/column routing gate the user is on that step in the center panel and
# nothing is executing, so the Activity-Log card is REMOVED.
_at_presem = build_run_activity_entry(
    "run1", migration_status="awaiting_review", completed_node_ids={1, 2},
    pending_gate_type="pre_semantic", mapping_decisions=md,
)
check("Table & Column Mapping card is REMOVED while paused at the routing gate",
      step_by_stage(_at_presem, "semantic_review") is None)
check("its decisions still surface via the B21.1 destination-column stage",
      bool(step_by_stage(_at_presem, "column_to_destination")))
# CAFM-004/006 — loop-linking: a decision stage carries an inline deep-link ("→ Action Item") into
# the Actions-required tab while the run awaits review.
check("a decision stage carries a loop-link into the Actions-required tab (CAFM-004)",
      any((s.get("action_ref") or {}).get("id") in ("merge", "fk", "field")
          for s in _at_presem["processing_log"]["steps"]))
# A RUNNING (not-paused) run carries no loop-links — nothing is awaiting the user.
_running_run = build_run_activity_entry("run1", migration_status="running", completed_node_ids={1, 2})
check("a running run has NO loop-links (nothing awaiting review)",
      not any(s.get("action_ref") for s in _running_run["processing_log"]["steps"]))
check("the 'Awaiting your review' lifecycle step is 'awaiting', not 'running'",
      any(s.get("stage") == "awaiting_review" and s.get("status") == "awaiting"
          for s in _at_presem["processing_log"]["steps"]))
# Once the user CONTINUES (gate cleared, run processing), node 3 becomes a live async job → the
# step re-appears reading "running" (the FE's "Async — running" card).
_after_continue = build_run_activity_entry(
    "run1", migration_status="running", completed_node_ids={1, 2}, mapping_decisions=md,
)
_cont_sr = step_by_stage(_after_continue, "semantic_review")
check("Table & Column Mapping runs ASYNC after the user continues (status 'running')",
      bool(_cont_sr) and _cont_sr["status"] == "running")
# A non-gate stage genuinely in flight still reads "running" (real async work is unchanged).
_inflight = build_run_activity_entry(
    "run1", migration_status="running", completed_node_ids={1}, running_node_ids={2},
)
_det = step_by_stage(_inflight, "deterministic")
check("a genuinely in-flight (non-gate) stage still reads 'running'",
      bool(_det) and _det["status"] == "running")

# ── terminal → post-write stages now included, status completed ───────────────────
done = build_run_activity_entry(
    "run1", migration_status="complete", completed_node_ids={1, 2, 4, 5, 6, 7, 8, 9},
)
check("terminal status completed", done["status"] == "completed")
tlabels = stage_labels(done)
check("terminal includes Test 1 (post-write)", any(l.startswith("Test 1") for l in tlabels))
check("terminal includes Job 2 sanctity (post-write)", any("Sanctity" in l for l in tlabels))

# ── job lifecycle event steps (started / paused / completed / failed / cancelled) ─────
check("completed run has a 'Migration completed' lifecycle step",
      any(s.get("stage") == "run_completed" for s in done["processing_log"]["steps"]))
check("paused run has an 'Awaiting your review' step naming the gate",
      any(s.get("stage") == "awaiting_review" and "field mapping" in (s.get("text") or "")
          for s in atgate["processing_log"]["steps"]))

failed = build_run_activity_entry(
    "run1", migration_status="failed", completed_node_ids={1, 2},
    error_message="deterministic mapper crashed: boom",
)
check("failed run status failed", failed["status"] == "failed")
_frun = next((s for s in failed["processing_log"]["steps"] if s.get("stage") == "run_failed"), None)
check("failed run has a 'Run failed' step carrying the error",
      bool(_frun) and "boom" in (_frun.get("error", {}).get("technical_reason") or ""))
check("failed run_failed step has structured error (category + suggested actions)",
      bool(_frun.get("error", {}).get("category")) and bool(_frun.get("error", {}).get("suggested_actions")))
check("failed run leaves post-write stages QUEUED (never reached)",
      next((s for s in failed["processing_log"]["steps"] if s.get("stage") == "test1"), {}).get("status") == "pending")

cancelled = build_run_activity_entry("run1", migration_status="cancelled", completed_node_ids={1})
check("cancelled run status cancelled", cancelled["status"] == "cancelled")
check("cancelled run has a 'Run cancelled' step",
      any(s.get("stage") == "run_cancelled" for s in cancelled["processing_log"]["steps"]))

# ── auto-advancing step pauses are NOT "action required" (running, no awaiting_review step) ──
autostep = build_run_activity_entry(
    "run1", migration_status="step_paused", completed_node_ids={1}, pending_gate_type="step_1_ingest",
)
check("auto-advance step pause is 'running', not pending", autostep["status"] == "running")
check("auto-advance step pause has no 'Awaiting your review' step",
      not any(s.get("stage") == "awaiting_review" for s in autostep["processing_log"]["steps"]))
realgate = build_run_activity_entry(
    "run1", migration_status="step_paused", completed_node_ids={1, 2}, pending_gate_type="pre_semantic",
)
check("real HITL gate is pending_human_input", realgate["status"] == "pending_human_input")
check("real HITL gate has an 'Awaiting your review' step",
      any(s.get("stage") == "awaiting_review" for s in realgate["processing_log"]["steps"]))

# ── Run-start entry: with NO node completed yet but Node 1 (ingest) in flight, the log must
# already show the first pipeline stage RUNNING (not an all-queued / empty log). This is what
# the inline start paths now emit the moment the run begins, so the Activity Log is populated in
# lock-step with the Migration Panel instead of staying empty through the whole ingest window.
runstart = build_run_activity_entry(
    "run1", migration_status="running", completed_node_ids=set(), running_node_ids={1},
)
check("run-start entry status is running", runstart["status"] == "running")
_rs_steps = runstart["processing_log"]["steps"]
check("run-start log is non-empty (has Run started)",
      any(s.get("stage") == "run_started" for s in _rs_steps))
check("run-start shows the ingest stage RUNNING (File Ingestion in flight)",
      any(s.get("stage") in ("preprocessing", "unique_tables") and s.get("status") == "running"
          for s in _rs_steps))
check("run-start has NO completed pipeline stage yet",
      not any(s.get("stage") in {"deterministic", "semantic", "hierarchy"} and s.get("status") == "completed"
              for s in _rs_steps))

# ── build_partial_table_resolution — B7.1 EARLY (during-run) cards from ingestion preview ──
# The frontend renders each table-resolution section independently, so the partial must carry
# metadata_cards (B7.1) while OMITTING pk_detection / deterministic / rag_alias / semantic /
# final_decisions (those only exist at node 3). Samples come from the small preview rows; the
# row_count reflects the FULL file (passed separately), not the preview length.
_preview = {
    "meter_readings": [
        {"reading_id": "R00000001", "site_id": "S013", "asset_id": "A0018588", "reading_type": "RuntimeHours"},
        {"reading_id": "R00000002", "site_id": "S037", "asset_id": "A0052563", "reading_type": "Vibration"},
    ],
    "sites": [
        {"site_id": "S001", "site_name": "OfficeCampus 001", "site_type": "OfficeCampus", "country": "UAE"},
        {"site_id": "S002", "site_name": "Tower 002", "site_type": "Tower", "country": "UAE"},
    ],
}
_partial = build_partial_table_resolution(_preview, row_counts={"meter_readings": 500000, "sites": 200})

check("partial carries B7.1 metadata_cards", len(_partial.get("metadata_cards") or []) == 2)
check("partial is flagged partial", _partial.get("partial") is True)
check("partial counts.tables == number of source tables", _partial.get("counts", {}).get("tables") == 2)
check("partial OMITS pk_detection (node-3 only)", "pk_detection" not in _partial)
check("partial OMITS deterministic mapping (node-3 only)", "deterministic" not in _partial)
check("partial OMITS final_decisions (node-3 only)", "final_decisions" not in _partial)

_mr = next(c for c in _partial["metadata_cards"] if c["table"] == "meter_readings")
check("card row_count is the FULL-file count, not the preview length", _mr["row_count"] == 500000)
check("card column_count reflects all preview columns", _mr["column_count"] == 4)
check("card PK is empty until node 3 detects it", _mr["primary_key"] == [])
check("card samples carry up to 3 values per column",
      all(len(s["values"]) <= 3 for s in _mr["samples"]))
check("card samples preserve first-seen column order", _mr["samples"][0]["column"] == "reading_id")
check("card sample values are stringified from the preview rows",
      _mr["samples"][0]["values"] == ["R00000001", "R00000002"])

# Empty / junk input → {} so the caller emits nothing (status field stays None until real cards).
check("empty tables → {}", build_partial_table_resolution({}) == {})
check("non-dict tables → {}", build_partial_table_resolution(None) == {})  # type: ignore[arg-type]

# NaN / blank cells are skipped when sampling so a card never previews an empty/NaN value.
_dirty = build_partial_table_resolution(
    {"t": [{"a": "nan", "b": ""}, {"a": "x1", "b": "y1"}]},
)
_ta = next(s for s in _dirty["metadata_cards"][0]["samples"] if s["column"] == "a")
check("NaN/blank sample values are skipped", _ta["values"] == ["x1"])

# ── activity_persist._is_connection_error + _run_in_session (passed-session paths) ───────
# A dropped/stale DB connection (cloud idle-close, or a fresh SSL connect the server drops:
# "unexpected connection_lost() call") is transient — the 2s-polled activity reads must retry
# once on a fresh session instead of surfacing a 500. (The fresh-session RETRY branch imports
# ..db, which only resolves in the real `src.*` package — not this top-level test harness — so
# here we cover the detector + the two branches that DON'T touch the DB: happy path and the
# non-connection-error re-raise that must NOT retry.)
import asyncio as _asyncio  # noqa: E402
from sqlalchemy.exc import OperationalError as _OperationalError  # noqa: E402
from udr.activity_persist import _is_connection_error, _run_in_session  # noqa: E402

check("ConnectionError is a connection error", _is_connection_error(ConnectionError("boom")))
check("asyncpg connection_lost message is a connection error",
      _is_connection_error(RuntimeError("unexpected connection_lost() call")))
check("'connection was closed' is a connection error",
      _is_connection_error(Exception("the connection was closed")))
check("SSL 'unexpected eof while reading' is a connection error (mid-query drop)",
      _is_connection_error(Exception("consuming input failed: SSL error: unexpected eof while reading")))
check("OSError is a connection error", _is_connection_error(OSError("reset")))
check("SQLAlchemy OperationalError is a connection error",
      _is_connection_error(_OperationalError("stmt", {}, Exception("down"))))
check("a plain ValueError is NOT a connection error", not _is_connection_error(ValueError("bad id")))


class _FakeSession:
    async def commit(self):
        pass


# passed-session happy path: fn succeeds → returned as-is, no retry, no DB import.
_calls_ok = {"n": 0}


async def _fn_ok(_s):
    _calls_ok["n"] += 1
    return "ok"


_res_ok = _asyncio.new_event_loop().run_until_complete(_run_in_session(_fn_ok, _FakeSession()))
check("passed-session happy path returns fn result", _res_ok == "ok")
check("passed-session happy path runs fn exactly once", _calls_ok["n"] == 1)

# A NON-connection error must NOT be retried — it re-raises immediately (never reaching ..db).
_calls_err = {"n": 0}


async def _fn_value_error(_s):
    _calls_err["n"] += 1
    raise ValueError("real query bug")


_raised = False
try:
    _asyncio.new_event_loop().run_until_complete(_run_in_session(_fn_value_error, _FakeSession()))
except ValueError:
    _raised = True
check("a non-connection error is re-raised, not retried", _raised)
check("a non-connection error runs fn exactly once (no retry)", _calls_err["n"] == 1)

# ── One column-intelligence stage per left-panel card (B13→B21) ──────────────────────────
# The Activity Log must carry a 1:1 log entry for each "Column Intelligence Pipeline" card, in
# the SAME order and with the SAME names — so grouping (B19) / classification (B20) / metadata
# (B14/B15) are their own stages, not folded into column_within_source / column_to_destination.
_ci_sample = {
    "summary": {
        "pk": 4, "fk": 4, "shared": 36, "groups": 5,
        "format_survivors": 144, "format_pairs_scored": 654,
        "columns_analyzed": 50, "columns_mapped": 37,
    },
    "groups": [
        {"group_id": "G1", "members": ["pm_templates.estimated_minutes", "vendors.sla_response_mins"], "canonical_name": "sla_response"},
    ],
    "fk_candidates": [{"src": "a"}, {"src": "b"}, {"src": "c"}, {"src": "d"}],
}
_extras = stage_extras_from_column_intelligence(_ci_sample, mapping_decisions=[])

check("B17/B18 similarity stage carries format-gate survivors",
      any("cross-table pairs" in c for c in _extras.get("column_within_source", {}).get("chips", [])))
check("B19 grouping is its OWN stage", "column_grouping" in _extras)
check("B19 grouping chip counts similar-column groups",
      any("similar-column group" in c for c in _extras["column_grouping"].get("chips", [])))
check("B20 classification is its OWN stage with PK/FK/Shared chips",
      _extras.get("column_classification", {}).get("chips") == ["4 Primary Keys", "4 Foreign Keys", "36 Shared Attributes"])
check("Unified names stage carries 'N groups unified'",
      any("unified" in c for c in _extras.get("column_canonicalisation", {}).get("chips", [])))
check("B14/B15 metadata is its OWN stage", "column_metadata" in _extras)
check("B21 destination mapping carries 'N columns mapped'",
      any("mapped to destination" in c for c in _extras.get("column_to_destination", {}).get("chips", [])))
# PK/FK/Shared must NOT double-appear on the similarity stage (they moved to classification).
check("similarity stage no longer carries PK/FK/Shared chips",
      not any("Primary Key" in c for c in _extras.get("column_within_source", {}).get("chips", [])))

# build_run_activity_entry emits all seven column cards, in left-panel order, with card names.
# IMPORTANT: only node 1 is done (the real pre-semantic gate) — the column stages' covering node
# (5) has NOT run, so each must read as COMPLETED purely from its CI evidence. Otherwise it stays
# "pending" and the frontend filters it out (the "B13.1 not there" bug). Testing status, not just
# label presence, is what catches that — build_run_activity_entry lists pending stages too.
_entry = build_run_activity_entry(
    "run1", migration_status="step_paused", completed_node_ids={1},
    pending_gate_type="pre_semantic", stage_extras=_extras, columns=50,
)
_steps_by_stage = {s.get("stage"): s for s in _entry["processing_log"]["steps"]}
_expected_col = [
    ("table_prefixing", "B13.1 · Table prefixing"),
    ("column_within_source", "B17.1 · B18.1 · Column similarity within source · format + value-pattern"),
    ("column_grouping", "B19.1 · Group similar columns (name-agnostic) + unified name"),
    ("column_classification", "B20.1 · Classify each group · PK / FK / Shared"),
    ("column_canonicalisation", "Unified column names"),
    ("column_metadata", "B14.1 · B15.1 · Column metadata — 4 dimensions + canonical name"),
    ("column_to_destination", "B21.1 · Destination column mapping"),
]
for _stage, _lbl in _expected_col:
    _st = _steps_by_stage.get(_stage)
    check(f"activity log has a matching entry: {_lbl!r}", bool(_st) and _st.get("label") == _lbl)
    # Completed from evidence (node 5 NOT done) → the frontend renders it, not filters it.
    check(f"{_stage} reads as completed from CI evidence at the gate", _st and _st.get("status") == "completed")
# …and in the SAME sequence as the cards.
_labels = [s.get("label") for s in _entry["processing_log"]["steps"]]
_idx = [_labels.index(_lbl) for _, _lbl in _expected_col]
check("the seven column stages appear in left-panel order", _idx == sorted(_idx))

print("\nALL TESTS PASSED")
