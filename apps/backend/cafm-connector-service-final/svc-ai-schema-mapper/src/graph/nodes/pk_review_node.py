"""PK-review gate (Group A) — a dedicated HITL gate that runs BEFORE the pre-semantic
routing/mapping gate.

Shows the user duplicate-table detection (B6.1), unique-table identification (B7.1) and
primary-key detection (B8.1), and interrupts for primary-key approval. Once the user approves,
its resume applies the chosen PK per table and the graph advances to the pre-semantic gate
(Group B: deterministic / RAG / semantic table mapping + Step-1 routing).

Kept as its OWN node (single ``interrupt()``) rather than a second interrupt inside the
pre-semantic node — one interrupt per node is the proven LangGraph pattern and keeps the
resume semantics simple: pk_review resumes with ``{pk_overrides}``, then pre_semantic resumes
with the routing/field-mapping decisions.
"""

from datetime import datetime

from langgraph.types import interrupt

from ..state import MigrationState
from .pk_confirmation import build_pk_confirmation, normalize_pk_overrides

from cafm_shared.logging import get_logger
logger = get_logger(__name__)


async def pk_review_node(state: MigrationState) -> MigrationState:
    """Group A gate — confirm each table's primary key before routing/mapping."""
    migration_id = state.get("migration_id")

    # Never re-open a decided gate (mirrors pre_semantic_reviewed). Cleared on a restart from an
    # earlier node so a deliberate re-run still re-opens it.
    if state.get("pk_reviewed"):
        logger.info("[PK Gate] Already answered this run — proceeding to routing/mapping gate.")
        return state

    _pk_tables = state.get("full_tables") or state.get("parsed_tables") or {}
    pk_confirmation = build_pk_confirmation(_pk_tables)
    if not pk_confirmation:
        logger.info("[PK Gate] No tables to confirm PK — skipping gate.")
        state["pk_reviewed"] = True
        return state

    # ── Build the B6.1 / B7.1 / B8.1 report for the gate display ──────────
    # Dedup per table so primary-key uniqueness isn't skewed by exact-duplicate rows (the
    # pre-semantic gate runs before Node-5 dedup, so raw full_tables can still carry them).
    _analysis_tables: dict = {}
    try:
        from ...udr.preprocessing import remove_exact_duplicate_rows
        for _n, _r in (state.get("full_tables") or {}).items():
            _deduped, _ = remove_exact_duplicate_rows(list(_r or []))
            _analysis_tables[_n] = _deduped
    except Exception:  # pragma: no cover — fall back to raw rows
        _analysis_tables = {n: list(r or []) for n, r in (state.get("full_tables") or {}).items()}

    _routing = state.get("table_routing") or {}
    _matches = state.get("cafm_table_matches") or {}

    def _dest(src: str) -> str:
        return _routing.get(src) or _matches.get(src) or src

    table_resolution = None
    try:
        from ...udr.table_resolution import build_table_resolution
        try:
            from ...matchers.fm_ontology import fm_table_lookup as _alias_resolver
        except Exception:  # pragma: no cover
            _alias_resolver = None
        if _analysis_tables:
            table_resolution = build_table_resolution(
                _analysis_tables,
                dest_table_by_source={s: _dest(s) for s in _analysis_tables},
                confidence_by_source=state.get("cafm_table_match_confidence") or {},
                alias_resolver=_alias_resolver,
                # Node 1's identical-column merge — shown before B8.1 so the user can see the
                # PK is being picked from the merged column list.
                duplicate_column_report=state.get("duplicate_column_report") or {},
            )
    except Exception as exc:  # pragma: no cover — additive, never fatal
        logger.warning(f"[PK Gate] table-resolution report failed: {exc}")
        table_resolution = None

    pk_payload = {
        "gate": "pk_approval",
        # total_reviewable present (0) so the frontend's numeric gate-detection resolves it.
        "total_reviewable": 0,
        "pk_confirmation": pk_confirmation,
        "table_resolution": table_resolution,
        # Step 1 of Group A — PRIMARY-KEY IDENTIFICATION. Unique-table identification is the
        # SECOND gate (unique_table_review_node), shown only after the PK is confirmed here.
        "instructions": (
            "Step 1 — Primary-key identification. Review the detected primary key for each table "
            "and confirm or edit it. Approve to continue to unique-table identification."
        ),
    }
    state["pre_semantic_review_payload"] = pk_payload  # reuse the existing channel

    logger.info(
        f"[PK Gate] Interrupting for PK approval — {len(pk_confirmation)} table(s). "
        f"migration_id={migration_id}"
    )
    if migration_id:
        from .db_writer import write_gate_payload
        await write_gate_payload(migration_id, "pk_approval", pk_payload)

    resumed = interrupt(pk_payload)

    if migration_id:
        from .db_writer import clear_gate_payload
        await clear_gate_payload(migration_id)

    _pk_over = resumed.get("pk_overrides") if isinstance(resumed, dict) else None
    state["pk_confirmed_by_table"] = normalize_pk_overrides(_pk_over, pk_confirmation)
    state["pk_reviewed"] = True
    state["pre_semantic_review_payload"] = None

    _log = state.get("event_log")
    if isinstance(_log, list):
        _log.append({
            "timestamp": datetime.utcnow().isoformat(),
            "event": "gate_answered",
            "gate": "pk_approval",
            "detail": f"PK confirmed for {len(state['pk_confirmed_by_table'])} table(s)",
        })
    logger.info(f"[PK Gate] PK confirmed: {state['pk_confirmed_by_table']}")
    return state
