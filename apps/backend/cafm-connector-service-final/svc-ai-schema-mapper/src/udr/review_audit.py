"""Feature 4 — live audit emit: human-review resolutions + evidence suggestions.

Pure builders (no DB / LLM / network) that the gate-submit path and the Node-10 emit use:

  * ``build_review_resolution_record`` — turns a gate submit's decisions into a compact,
    immutable human-review-resolution RECORD. This is NOT a separate Activity card: the
    record is appended to the single per-run activity row (``refs.review_resolutions``,
    append-only) and woven into that run's Processing Log as ONE "Human review resolution"
    step (see ``run_activity.weave_review_resolution_steps``), so the whole migration
    lifecycle stays in one activity. Returns ``None`` when a submit carries no field-level
    resolutions (e.g. the hierarchy / final gates).

  * ``build_missing_fk_suggestions`` — turns Test-2 unexplained-overlap flags into
    "Missing FK relationship detected → create a foreign key" refinement suggestions,
    merged into the completed run's refinements (same activity).

Being pure, both are unit-tested offline (``tests/test_udr_review_audit.py``); the async
side-effect (``append_run_review_resolution``) is invoked by the caller.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

# Field-mapping gate (Gate 1) decision actions → human-readable audit verbs.
_FLAGGED_VERB = {"accept": "Approved", "reject": "Rejected", "override": "Overridden"}
_UNMAPPED_VERB = {
    "custom": "New column",
    "raw_metadata": "Kept in raw_metadata",
    "skip": "Skipped",
}


def normalize_gate_decisions(decisions: Any, gate_type: str) -> list[dict]:
    """Flatten a gate-submit payload into review resolutions:
    ``[{source_table, source_field, decision, target}]``. Returns ``[]`` for gates /
    payloads with no field-level resolutions."""
    out: list[dict] = []
    if not isinstance(decisions, dict):
        return out

    if gate_type == "field_mapping":
        flagged = decisions.get("flagged")
        if isinstance(flagged, dict):
            for table, items in flagged.items():
                for it in items or []:
                    if not isinstance(it, dict) or not it.get("source_field"):
                        continue
                    action = str(it.get("action") or "").lower()
                    verb = _FLAGGED_VERB.get(action, action or "Resolved")
                    tgt = it.get("target_field")
                    decision = (
                        f"{verb} → {tgt}" if tgt and action in ("accept", "override") else verb
                    )
                    out.append(
                        {"source_table": table, "source_field": it["source_field"],
                         "decision": decision, "target": tgt}
                    )
        unmapped = decisions.get("unmapped")
        if isinstance(unmapped, dict):
            for table, items in unmapped.items():
                for it in items or []:
                    if not isinstance(it, dict) or not it.get("source_field"):
                        continue
                    action = str(it.get("action") or "").lower()
                    if action == "custom":
                        col = it.get("custom_column_name") or it.get("target_field")
                        tbl = it.get("target_table")
                        dest = f"{tbl}.{col}" if tbl and col else (col or "")
                        decision = f"New column → {dest}" if dest else "New column suggested"
                    else:
                        decision = _UNMAPPED_VERB.get(action, action or "Resolved")
                    out.append(
                        {"source_table": table, "source_field": it["source_field"],
                         "decision": decision, "target": it.get("custom_column_name")}
                    )

    elif gate_type == "pre_semantic":
        d = decisions.get("decisions") if isinstance(decisions.get("decisions"), dict) else decisions
        if isinstance(d, dict):
            for table, items in d.items():
                if not isinstance(items, list):
                    continue
                for it in items or []:
                    if not isinstance(it, dict) or not it.get("source_field"):
                        continue
                    dec = str(it.get("decision") or "").lower()
                    verb = (
                        "Approved" if dec == "approve"
                        else "Sent to semantic review" if dec == "semantic"
                        else (dec or "Resolved")
                    )
                    out.append(
                        {"source_table": table, "source_field": it["source_field"],
                         "decision": verb, "target": None}
                    )
    return out


def build_review_resolution_record(
    decisions: Any,
    *,
    gate_type: str,
    user: str | None = None,
    clock=None,
) -> dict | None:
    """Build a compact, immutable human-review-resolution RECORD from a gate submit, or
    ``None`` when the submit carries no field-level resolutions.

    Unlike a separate Activity card, this record is APPENDED to the single per-run activity
    row (``refs.review_resolutions``) and woven into its Processing Log as one step — so the
    resolution joins the SAME migration activity. The record carries everything that step
    needs: ``gate_type`` + ``at`` (its stable identity / chronological anchor), the
    ``total`` count, the ``approved`` field list (the human-readable "Approved:" block), and
    the full ``rows`` (Field · Decision · By) for the step's detail table."""
    resolutions = normalize_gate_decisions(decisions, gate_type)
    if not resolutions:
        return None
    now = (clock or datetime.utcnow)().isoformat()
    by = user or "Reviewer"
    n = len(resolutions)
    rows = [[f"{r['source_table']}.{r['source_field']}", r["decision"], by] for r in resolutions[:80]]
    # Fields the reviewer kept/approved (vs. sent to semantic / rejected / skipped) — the
    # "Approved:" list the run-activity step renders.
    approved = [
        f"{r['source_table']}.{r['source_field']}"
        for r in resolutions
        if str(r.get("decision") or "").startswith(("Approved", "Overridden", "New column"))
    ]
    return {
        "gate_type": gate_type,
        "user": by,
        "at": now,
        "total": n,
        "approved": approved,
        "rows": rows,
    }


def build_missing_fk_suggestions(test2_report: Any, *, limit: int = 5) -> list[dict]:
    """Turn Test-2 unexplained column-overlap flags into "Missing FK" refinement
    suggestions (``{id, kind, label, prompt, requires_approval}``)."""
    t2 = test2_report or {}
    flags = t2.get("flags") if isinstance(t2, dict) else None
    out: list[dict] = []
    for i, fl in enumerate(flags or []):
        if not isinstance(fl, dict):
            continue
        a = f"{fl.get('table_a')}.{fl.get('column_a')}"
        b = f"{fl.get('table_b')}.{fl.get('column_b')}"
        out.append(
            {
                "id": f"missing-fk-{i}",
                "kind": "refinement",
                "label": f"Missing FK relationship detected: {a} ↔ {b} — create a foreign key?",
                "prompt": f"Create a foreign-key relationship between {a} and {b}.",
                "requires_approval": True,
            }
        )
        if len(out) >= limit:
            break
    return out
