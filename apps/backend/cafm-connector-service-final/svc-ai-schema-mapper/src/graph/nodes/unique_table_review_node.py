"""Unique-table review gate (Group A, step 2) — runs AFTER the primary-key gate.

The workflow shows PRIMARY-KEY IDENTIFICATION first (pk_review_node / B8.1) and, once the user
has confirmed each table's PK, this gate shows UNIQUE-TABLE IDENTIFICATION (B7.1) with each
table's CONFIRMED primary key, and interrupts for a second, mandatory approval. Only after this
approval does the graph advance to the pre-semantic routing/mapping gate (Group B).

Its own node (single ``interrupt()``) mirroring pk_review_node — one interrupt per node is the
proven LangGraph pattern. Resume is a simple confirm (no per-table edits: the PK was already
decided at the previous gate), so the resume payload is ignored beyond acknowledging approval.
"""

from datetime import datetime

from langgraph.types import interrupt

from ..state import MigrationState

from cafm_shared.logging import get_logger
logger = get_logger(__name__)


async def unique_table_review_node(state: MigrationState) -> MigrationState:
    """Group A step 2 — confirm unique-table identification (with confirmed PKs) before routing."""
    migration_id = state.get("migration_id")

    # Never re-open a decided gate (mirrors pk_reviewed / pre_semantic_reviewed).
    if state.get("unique_table_reviewed"):
        logger.info("[Unique-Table Gate] Already answered this run — proceeding to routing gate.")
        return state

    _tables = state.get("full_tables") or state.get("parsed_tables") or {}
    if not _tables:
        logger.info("[Unique-Table Gate] No tables — skipping gate.")
        state["unique_table_reviewed"] = True
        return state

    # ── Build the B6.1 / B7.1 report, PKs overlaid from the previous gate's decision ──────
    # Dedup per table so the cards match the data the PK gate scored (pre-Node-5 dedup).
    _analysis_tables: dict = {}
    try:
        from ...udr.preprocessing import remove_exact_duplicate_rows
        for _n, _r in _tables.items():
            _deduped, _ = remove_exact_duplicate_rows(list(_r or []))
            _analysis_tables[_n] = _deduped
    except Exception:  # pragma: no cover — fall back to raw rows
        _analysis_tables = {n: list(r or []) for n, r in _tables.items()}

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
                duplicate_column_report=state.get("duplicate_column_report") or {},
                # Overlay the PK the user CONFIRMED at the previous gate so B7.1's unique-table
                # cards show the approved primary key (the whole point of showing it second).
                pk_override_by_table=state.get("pk_confirmed_by_table") or {},
            )
    except Exception as exc:  # pragma: no cover — additive, never fatal
        logger.warning(f"[Unique-Table Gate] table-resolution report failed: {exc}")
        table_resolution = None

    payload = {
        "gate": "unique_table_approval",
        "total_reviewable": 0,  # numeric so the FE's gate-detection resolves it
        "table_resolution": table_resolution,
        # The confirmed PK per table, so the gate can label each unique-table card with its PK.
        "pk_confirmed_by_table": state.get("pk_confirmed_by_table") or {},
        "instructions": (
            "Step 2 — Unique-table identification. Review the identified unique tables (each shown "
            "with its confirmed primary key). Approve to continue to table routing / mapping."
        ),
    }
    state["pre_semantic_review_payload"] = payload  # reuse the existing gate-payload channel

    logger.info(
        f"[Unique-Table Gate] Interrupting for unique-table approval — "
        f"{len(_analysis_tables)} table(s). migration_id={migration_id}"
    )
    if migration_id:
        from .db_writer import write_gate_payload
        await write_gate_payload(migration_id, "unique_table_approval", payload)

    interrupt(payload)

    if migration_id:
        from .db_writer import clear_gate_payload
        await clear_gate_payload(migration_id)

    state["unique_table_reviewed"] = True
    state["pre_semantic_review_payload"] = None

    _log = state.get("event_log")
    if isinstance(_log, list):
        _log.append({
            "timestamp": datetime.utcnow().isoformat(),
            "event": "gate_answered",
            "gate": "unique_table_approval",
            "detail": f"Unique-table identification approved for {len(_analysis_tables)} table(s)",
        })
    logger.info("[Unique-Table Gate] Approved.")
    return state
