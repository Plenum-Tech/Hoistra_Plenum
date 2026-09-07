"""Tests for the human-review-resolution + FK-suggestion emit builders.
Run: python tests/test_udr_review_audit.py"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

try:  # Windows console is cp1252 — print the → in stage labels without crashing.
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from datetime import datetime  # noqa: E402

from udr.review_audit import (  # noqa: E402
    build_missing_fk_suggestions,
    build_review_resolution_record,
    normalize_gate_decisions,
)
from udr.run_activity import weave_review_resolution_steps  # noqa: E402


def check(name, cond):
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        raise AssertionError(name)


# ── field-mapping gate: flagged accept/override/reject + unmapped custom/skip ─────
fm = {
    "flagged": {
        "Vendors": [
            {"action": "accept", "source_field": "name", "target_field": "supplier_name"},
            {"action": "override", "source_field": "city", "target_field": "location"},
            {"action": "reject", "source_field": "junk"},
        ]
    },
    "unmapped": {
        "Vendors": [
            {"action": "custom", "source_field": "trade", "target_table": "vendors", "custom_column_name": "trade"},
            {"action": "skip", "source_field": "notes2"},
        ]
    },
}
res = normalize_gate_decisions(fm, "field_mapping")
check("field_mapping flattens all 5 resolutions", len(res) == 5)
by_field = {r["source_field"]: r["decision"] for r in res}
check("accept → Approved → target", by_field["name"] == "Approved → supplier_name")
check("override → Overridden → target", by_field["city"] == "Overridden → location")
check("reject → Rejected (no target)", by_field["junk"] == "Rejected")
check("custom → New column → table.col", by_field["trade"] == "New column → vendors.trade")
check("skip → Skipped", by_field["notes2"] == "Skipped")

# ── pre-semantic gate: approve / semantic ─────────────────────────────────────────
ps = {"decisions": {"Assets": [
    {"source_field": "asset_code", "decision": "approve"},
    {"source_field": "weird", "decision": "semantic"},
]}}
rps = normalize_gate_decisions(ps, "pre_semantic")
check("pre_semantic flattens 2", len(rps) == 2)
check("approve → Approved", rps[0]["decision"] == "Approved")
check("semantic → Sent to semantic review", rps[1]["decision"] == "Sent to semantic review")

# ── non-resolution gates / empty → None record ─────────────────────────────────────
check("hierarchy gate yields no resolutions", normalize_gate_decisions({"confirmed": True}, "hierarchy") == [])
check("empty field_mapping → None record", build_review_resolution_record({}, gate_type="field_mapping") is None)

# ── build_review_resolution_record shape (compact, folds into the run row) ──────────
rec = build_review_resolution_record(
    fm, gate_type="field_mapping", user="Alice", clock=lambda: datetime(2026, 6, 29, 10, 30),
)
check("record carries gate_type + user + total", rec["gate_type"] == "field_mapping" and rec["user"] == "Alice" and rec["total"] == 5)
check("record carries a chronological 'at'", isinstance(rec["at"], str) and rec["at"].startswith("2026-06-29T10:30"))
# approved list = accept / override / new-column (NOT reject / skip), in order
check("approved excludes rejected/skipped", rec["approved"] == ["Vendors.name", "Vendors.city", "Vendors.trade"])
check("record rows are Field/Decision/By", rec["rows"][0] == ["Vendors.name", "Approved → supplier_name", "Alice"])
check("record has 5 rows", len(rec["rows"]) == 5)
# default user
rdef = build_review_resolution_record(fm, gate_type="field_mapping")
check("default reviewer", rdef["user"] == "Reviewer")

# ── weave_review_resolution_steps — one run activity, resolution folded into the log ─
base_steps = [
    {"stage": "decomposition", "kind": "thought", "label": "Query decomposition"},
    {"stage": "preprocessing", "kind": "action", "label": "Pre-processing"},
    {"stage": "unique_tables", "kind": "thought", "label": "Unique table identification"},
    {"stage": "deterministic", "kind": "action", "label": "Deterministic table mapping"},
]
ps_rec = build_review_resolution_record(
    {"decisions": {"Assets": [{"source_field": "asset_code", "decision": "approve"}]}},
    gate_type="pre_semantic", user="Bob", clock=lambda: datetime(2026, 6, 29, 10, 31),
)
woven = weave_review_resolution_steps(base_steps, [ps_rec])
wl = [s["label"] for s in woven]
det_i = wl.index("Deterministic table mapping")
check("resolution step inserted right after deterministic", woven[det_i + 1]["label"] == "Human review resolution")
check("resolution step is chain-of-thought", woven[det_i + 1]["kind"] == "thought")
hr = woven[det_i + 1]
check("resolution text shows approved field + resume", "Assets.asset_code" in hr["text"] and "Migration resumed." in hr["text"])
check("resolution step has a Field/Decision/By table", hr["table"]["columns"] == ["Field", "Decision", "By"])
# idempotent — re-weaving the same record never duplicates the step
again = weave_review_resolution_steps(woven, [ps_rec])
check("weave is idempotent (no duplicate)", sum(1 for s in again if s.get("stage") == "human_review_resolution") == 1)
# stays between deterministic and semantic once the later stage appears
with_sem = weave_review_resolution_steps(woven + [{"stage": "semantic", "kind": "action", "label": "Semantic table mapping"}], [ps_rec])
l2 = [s["label"] for s in with_sem]
check("resolution sits between deterministic and semantic",
      l2.index("Deterministic table mapping") < l2.index("Human review resolution") < l2.index("Semantic table mapping"))

# ── build_missing_fk_suggestions ──────────────────────────────────────────────────
t2 = {"flags": [
    {"table_a": "assets", "column_a": "asset_code", "table_b": "work_orders", "column_b": "asset_id", "overlap": 0.8},
    {"table_a": "parts", "column_a": "part_code", "table_b": "work_order_parts", "column_b": "part_id", "overlap": 0.6},
]}
sugs = build_missing_fk_suggestions(t2)
check("two FK suggestions", len(sugs) == 2)
check("suggestion is a refinement", sugs[0]["kind"] == "refinement" and sugs[0]["requires_approval"] is True)
check("suggestion names both sides", "assets.asset_code" in sugs[0]["label"] and "work_orders.asset_id" in sugs[0]["label"])
check("suggestion prompt actionable", sugs[0]["prompt"].startswith("Create a foreign-key relationship"))
check("no flags → no suggestions", build_missing_fk_suggestions({"flags": []}) == [])
check("missing report → no suggestions", build_missing_fk_suggestions(None) == [])

print("\nALL TESTS PASSED")
