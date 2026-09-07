"""Classification-approval gate (B20.1) — HITL approval of the PK / FK / Shared assignments.

Runs AFTER the pre-semantic routing/mapping gate (where column-intelligence — classification,
fk_candidates and the B22.1 shared-attribute lookup tables — is built and stored on state). Shows
every group's classification (Primary Key groups included, for observability) and interrupts —
the pipeline does NOT proceed to the next steps until the human answers. On resume it APPLIES the
user's decisions via ``udr.column_intelligence.apply_classification_decisions``:
  • de-selected group            → nothing of it is applied (no lookup table, no fk_candidate),
  • FK re-classified as Shared   → FK not enforced; its B22.1 lookup table is synthesised instead,
  • Shared re-classified as FK   → lookup table not created; enforced when a PK member exists.

Every decision is recorded on state (``classification_rejected`` / ``classification_overrides``)
and in the event log, and is re-applied to the post-write UDR report so the final analysis always
reflects the human-approved classification.

Kept as its OWN node (single ``interrupt()``) mirroring pk_review_node.
"""

from datetime import datetime

from langgraph.types import interrupt

from ..state import MigrationState

from cafm_shared.logging import get_logger
logger = get_logger(__name__)


async def grouping_review_node(state: MigrationState) -> MigrationState:
    """B20.1 gate — confirm / edit the FK + Shared classification before it is applied."""
    migration_id = state.get("migration_id")

    # Never re-open a decided gate (mirrors pre_semantic_reviewed / pk_reviewed). Cleared on a
    # restart from an earlier node so a deliberate re-run still re-opens it.
    if state.get("grouping_reviewed"):
        logger.info("[Classification Gate] Already answered this run — proceeding.")
        return state

    ci = state.get("column_intelligence") or {}
    classification = ci.get("classification") or []
    if not classification:
        logger.info("[Classification Gate] No classification — skipping gate.")
        state["grouping_reviewed"] = True
        return state

    fk_rows = [c for c in classification if "foreign" in str(c.get("verdict", "")).lower()]
    shared_rows = [c for c in classification if "shared" in str(c.get("verdict", "")).lower()]
    pk_rows = [c for c in classification if "primary" in str(c.get("verdict", "")).lower()]

    payload = {
        "gate": "classification_approval",
        "total_reviewable": len(fk_rows) + len(shared_rows),
        "pk_count": len(pk_rows),
        "classification": classification,
        "shared_attribute_tables": ci.get("shared_attribute_tables") or [],
        "fk_candidates": ci.get("fk_candidates") or [],
        "instructions": (
            "Review the primary keys, foreign-key relationships and shared attributes (B20.1). "
            "For each FK / shared attribute, keep the detected classification, switch it (a FK "
            "demoted to Shared Attribute is not enforced and gets a lookup table instead; a "
            "Shared Attribute promoted to FK skips its lookup table), or exclude it entirely. "
            "The pipeline does not continue until you approve."
        ),
    }
    state["pre_semantic_review_payload"] = payload  # reuse the existing gate-payload channel

    logger.info(
        f"[Classification Gate] Interrupting — {len(fk_rows)} FK · {len(shared_rows)} shared. "
        f"migration_id={migration_id}"
    )
    if migration_id:
        from .db_writer import write_gate_payload
        await write_gate_payload(migration_id, "classification_approval", payload)

    resumed = interrupt(payload)

    if migration_id:
        from .db_writer import clear_gate_payload
        await clear_gate_payload(migration_id)

    # ── Apply the decisions (rejections + FK ↔ Shared re-classifications) ─────
    rejected = resumed.get("rejected_groups") if isinstance(resumed, dict) else None
    rejected_set = {str(g) for g in rejected} if isinstance(rejected, list) else set()
    overrides_in = resumed.get("verdict_overrides") if isinstance(resumed, dict) else None
    overrides_in = overrides_in if isinstance(overrides_in, dict) else {}

    applied: dict = {}
    if (rejected_set or overrides_in) and isinstance(ci, dict):
        from ...udr.column_intelligence import apply_classification_decisions

        # Table data in the {name: {"rows", "columns"}} shape the lookup synthesis needs —
        # so a FK group demoted to Shared Attribute gets its B22.1 lookup table built from
        # the run's real values. Best-effort: the verdict change applies regardless.
        tables_meta: dict = {}
        for _name, _rows in (state.get("full_tables") or {}).items():
            _rows = list(_rows or [])
            _cols: list[str] = []
            _seen: set[str] = set()
            for _r in _rows:
                if isinstance(_r, dict):
                    for _k in _r:
                        if _k not in _seen:
                            _seen.add(_k)
                            _cols.append(_k)
            tables_meta[_name] = {"rows": _rows, "columns": _cols}

        applied = apply_classification_decisions(
            ci, tables_meta or None,
            rejected=sorted(rejected_set),
            overrides=overrides_in,
        )
        state["column_intelligence"] = ci
        state["classification_rejected"] = applied.get("rejected_applied") or []
        state["classification_overrides"] = applied.get("overrides_applied") or {}
        logger.info(f"[Classification Gate] Applied — {applied}")

    state["grouping_reviewed"] = True
    state["pre_semantic_review_payload"] = None

    _overrides_applied = applied.get("overrides_applied") or {}
    _log = state.get("event_log")
    if isinstance(_log, list):
        _log.append({
            "timestamp": datetime.utcnow().isoformat(),
            "event": "gate_answered",
            "gate": "classification_approval",
            "detail": (
                f"Classification approved ({len(fk_rows)} FK, {len(shared_rows)} shared, "
                f"{len(pk_rows)} PK"
                + (f", {len(rejected_set)} rejected" if rejected_set else "")
                + (f", {len(_overrides_applied)} re-classified" if _overrides_applied else "")
                + ")"
            ),
            # Full decision record (audit): which groups were excluded / re-classified, and
            # what the re-classification produced (lookup tables, FK candidates).
            "decisions": {
                "rejected_groups": sorted(rejected_set),
                "verdict_overrides": _overrides_applied,
                "lookup_tables_added": applied.get("lookup_tables_added") or [],
                "fk_candidates_added": applied.get("fk_candidates_added") or 0,
                "fk_candidates_removed": applied.get("fk_candidates_removed") or 0,
            },
        })
    logger.info(
        f"[Classification Gate] Answered — rejected={sorted(rejected_set) or []} "
        f"overrides={_overrides_applied or {}}"
    )
    return state
