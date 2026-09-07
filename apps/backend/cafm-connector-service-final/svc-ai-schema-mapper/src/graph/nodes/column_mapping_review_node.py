"""Column-mapping approval gate (B14.1) — HITL per-column approval of the matched source→
destination column mapping.

Runs AFTER the B20 classification gate, once column-intelligence (including ``dest_mapping`` — the
closest destination-table column each source column resolved to) is fully built. Shows every
source column with its matched destination column and interrupts — the pipeline does NOT proceed to
semantic mapping / write until the human approves. On resume it records per-column overrides:

  • approve (default) → keep the matched destination column,
  • re-target         → map the source column to a DIFFERENT destination column,
  • exclude / new     → do NOT merge into the matched column; create it as a NEW column instead.

The overrides are stored on state (``column_dest_overrides``) and applied to the final field
mappings at output generation. Kept as its OWN node (single ``interrupt()``) mirroring the other
Group-A / classification gates.
"""

from datetime import datetime

from langgraph.types import interrupt

from ..state import MigrationState

from cafm_shared.logging import get_logger
logger = get_logger(__name__)


async def column_mapping_review_node(state: MigrationState) -> MigrationState:
    """B14.1 gate — confirm / override each source→destination column mapping before write."""
    migration_id = state.get("migration_id")

    if state.get("column_mapping_reviewed"):
        logger.info("[Column-Mapping Gate] Already answered this run — proceeding.")
        return state

    ci = state.get("column_intelligence") or {}
    dest_mapping = ci.get("dest_mapping") or []
    if not dest_mapping:
        logger.info("[Column-Mapping Gate] No dest_mapping — skipping gate.")
        state["column_mapping_reviewed"] = True
        return state

    # Per-destination-table column lists for the re-target dropdown (best-effort; empty just
    # disables re-target on the frontend, leaving approve / exclude).
    dest_columns_by_table = ci.get("dest_columns_by_table") or {}

    payload = {
        "gate": "column_mapping_approval",
        "total_reviewable": len(dest_mapping),
        # Each row: {source, matched_column, dest_table, outcome, scores, canonical_name}.
        "dest_mapping": dest_mapping,
        "dest_columns_by_table": dest_columns_by_table,
        "instructions": (
            "Review the matched destination column for every source column (B14.1). Keep the "
            "match, re-target it to a different destination column, or exclude it (created as a "
            "NEW column instead of merged). The pipeline does not continue until you approve."
        ),
    }
    state["pre_semantic_review_payload"] = payload  # reuse the existing gate-payload channel

    logger.info(
        f"[Column-Mapping Gate] Interrupting — {len(dest_mapping)} column(s). "
        f"migration_id={migration_id}"
    )
    if migration_id:
        from .db_writer import write_gate_payload
        await write_gate_payload(migration_id, "column_mapping_approval", payload)

    resumed = interrupt(payload)

    if migration_id:
        from .db_writer import clear_gate_payload
        await clear_gate_payload(migration_id)

    # Resume payload: {"overrides": {source_table: {source_field: "<dest_col>" | "__new__"}}}.
    overrides_in = resumed.get("overrides") if isinstance(resumed, dict) else None
    overrides: dict[str, dict[str, str]] = {}
    if isinstance(overrides_in, dict):
        for _tbl, _cols in overrides_in.items():
            if not isinstance(_cols, dict):
                continue
            _clean = {str(c): str(v) for c, v in _cols.items() if v}
            if _clean:
                overrides[str(_tbl)] = _clean

    state["column_dest_overrides"] = overrides
    state["column_mapping_reviewed"] = True
    state["pre_semantic_review_payload"] = None

    # Emit the CONFIRMED per-column mapping as a completed-step snapshot. Without this the gate
    # left no record, so after approving there was nothing showing WHICH column was confirmed,
    # re-targeted, or created as a new column — only the gate itself, which is then cleared.
    _n_overrides = sum(len(v) for v in overrides.values())
    if migration_id:
        _confirmed: list[dict] = []
        for _row in dest_mapping:
            if not isinstance(_row, dict):
                continue
            _src = str(_row.get("source") or "")
            _tbl = _row.get("source_column") and _src.split(".")[0] or _src.split(".")[0]
            _col = _row.get("source_column") or ".".join(_src.split(".")[1:])
            _ovr = (overrides.get(_tbl) or {}).get(_col)
            if _ovr == "__new__":
                _target, _outcome = None, "new column (user)"
            elif _ovr:
                _target, _outcome = _ovr, "re-targeted (user)"
            else:
                _target, _outcome = _row.get("matched_column"), (_row.get("outcome") or "confirmed")
            _confirmed.append({
                "source_table": _tbl,
                "source_field": _col,
                "dest_table": _row.get("dest_table"),
                "target_field": _target,
                "outcome": _outcome,
                "is_primary_key": bool(_row.get("is_primary_key")),
                "format": _row.get("format"),
                "source_tables": _row.get("source_tables") or [_tbl],
            })
        _n_new = sum(1 for c in _confirmed if "new column" in str(c.get("outcome") or ""))
        try:
            from .schema_db_writer import migration_append_node_log_auto
            await migration_append_node_log_auto(
                migration_id, 3, "Column Mapping Confirmed", datetime.utcnow(), datetime.utcnow(),
                output={
                    "column_mapping_confirmed": True,
                    "columns_confirmed": len(_confirmed),
                    "columns_overridden": _n_overrides,
                    "new_columns": _n_new,
                    "confirmed_columns": _confirmed,
                },
                logs=[
                    "Gate B14.1: column mapping confirmed",
                    f"{len(_confirmed)} column(s) confirmed · {_n_overrides} overridden · {_n_new} new",
                ],
            )
        except Exception as _exc:  # pragma: no cover — best-effort, never block the pipeline
            logger.warning(f"[Column-Mapping Gate] snapshot emit failed: {_exc}")

    _n_overrides = sum(len(v) for v in overrides.values())
    _log = state.get("event_log")
    if isinstance(_log, list):
        _log.append({
            "timestamp": datetime.utcnow().isoformat(),
            "event": "gate_answered",
            "gate": "column_mapping_approval",
            "detail": (
                f"Column mapping approved ({len(dest_mapping)} columns"
                + (f", {_n_overrides} overridden" if _n_overrides else "")
                + ")"
            ),
            "decisions": {"column_dest_overrides": overrides},
        })
    logger.info(f"[Column-Mapping Gate] Answered — overrides={overrides or {}}")
    return state
