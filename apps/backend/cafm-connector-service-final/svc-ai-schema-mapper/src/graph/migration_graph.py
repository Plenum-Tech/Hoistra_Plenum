"""LangGraph state machine construction for svc-AI-Schema-Mapper.

9-node migration pipeline with PostgreSQL checkpointer and HITL interrupt gates.
"""

import inspect
import logging
from typing import Any, Callable

from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver

# Try to import the postgres checkpointer (requires langgraph-checkpoint-postgres + psycopg3)
try:
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver as _AsyncPostgresSaver
    from psycopg_pool import AsyncConnectionPool as _AsyncConnectionPool
except ImportError:
    _AsyncPostgresSaver = None
    _AsyncConnectionPool = None

try:
    from langgraph.checkpoint.postgres import PostgresSaver as _SyncPostgresSaver
except ImportError:
    _SyncPostgresSaver = None

from .state import MigrationState
from ..db import get_sync_db_url
from .nodes.ingest_node import ingest_node
from .nodes.deterministic_mapper import deterministic_mapper_node
from .nodes.pre_semantic_review_node import pre_semantic_review_node
from .nodes.pk_review_node import pk_review_node
from .nodes.unique_table_review_node import unique_table_review_node
from .nodes.grouping_review_node import grouping_review_node  # B20.1 classification approval gate
from .nodes.column_mapping_review_node import column_mapping_review_node  # B14.1 column-mapping gate
from .nodes.semantic_mapper import semantic_mapper_node
from .nodes.human_review_node import human_review_node
from .nodes.preprocess_node import preprocess_node
from .nodes.hierarchy_node import hierarchy_node
from .nodes.verify_hierarchy_node import verify_hierarchy_node
from .nodes.output_generator_node import output_generator_node
from .nodes.write_node import write_node
from .nodes.udr_node import udr_node
from .nodes.udr_tests import test_2_node, tests_enabled

from cafm_shared.logging import get_logger
logger = get_logger(__name__)


# ── Node implementations are now imported from .nodes modules ──────────────
# All 9 nodes are fully implemented in Phase 3-6


# ── Conditional Edge Functions ────────────────────────────────────────────────

def _should_skip_semantic_mapper(state: MigrationState) -> str:
    """
    After the Pre-Semantic Review Gate:
    - Count total unresolved fields across all tables (includes any fields that
      were rejected at the pre-semantic gate and pushed back to unresolved).
    - If none remain → skip Node 3, go to Node 5 (preprocess).
    - Otherwise → proceed to Node 3 (semantic_mapper).
    """
    unresolved_by_table: dict = state.get("unresolved_by_table", {})
    total_unresolved = sum(len(fields) for fields in unresolved_by_table.values())
    if total_unresolved == 0:
        logger.info("[Pre-Semantic→5] No unresolved fields after pre-semantic gate; skipping semantic mapper")
        return "preprocess_node"
    logger.info(f"[Pre-Semantic→3] {total_unresolved} unresolved fields; proceeding to semantic mapper")
    return "semantic_mapper_node"


def _route_after_pre_semantic(state: MigrationState) -> str:
    """Two-pass reorder routing. PASS 1 (analysis built, Step-2 gate NOT yet answered) → run the
    B20 classification gate first. PASS 2 (Step-2 field-matching gate answered) → proceed to
    semantic mapping / preprocess. This yields the gate order B20 → B14.1 → Step-2 Column matching.
    """
    if not state.get("pre_semantic_reviewed"):
        return "grouping_review_node"
    return _should_skip_semantic_mapper(state)


def _route_after_semantic(state: MigrationState) -> str:
    """
    After Node 3 (semantic_mapper):
    - EL-3.0: If overall_confidence < 0.80 → FORCE GATE 1 (human_review_node)
    - Else if tier2_flagged_by_table has any entries → GATE 1 (human_review_node)
    - Otherwise → proceed to Node 5 (preprocess_node)
    """
    overall_confidence = state.get("overall_confidence", 1.0)
    flagged_by_table: dict = state.get("tier2_flagged_by_table", {})
    total_flagged = sum(len(items) for items in flagged_by_table.values())

    # EL-3.0: Force GATE 1 if confidence too low
    if overall_confidence < 0.80:
        logger.warning(
            f"[EL-3.0] overall_confidence={overall_confidence:.2f} < 0.80; "
            f"FORCING GATE 1 (human_review_node)"
        )
        return "human_review_node"

    # If customer must review flagged fields
    if total_flagged > 0:
        logger.info(
            f"[Node 3→4] {total_flagged} fields flagged for review; "
            f"proceeding to GATE 1 (human_review_node)"
        )
        return "human_review_node"

    # All fields mapped and confident
    logger.info(f"[Node 3→5] All fields mapped confidently; skipping GATE 1")
    return "preprocess_node"


def build_migration_graph(
    checkpointer: Any,
) -> Any:
    """
    Construct and compile the 9-node StateGraph.

    Args:
        checkpointer: PostgresSaver instance for state persistence

    Returns:
        Compiled StateGraph ready for astream_events() / ainvoke()
    """

    # Create the StateGraph
    graph = StateGraph(MigrationState)

    # ── Bulk-row offload wrapper (graph/bulk_tables.py) ───────────────────────
    # full_tables / cleaned_tables hold the ENTIRE dataset and used to ride in the checkpoint
    # on every node → past ~1 GB a single Postgres value dies with "invalid memory alloc
    # request size N" (seen on a 1.887M-row run where the post-preprocess checkpoint carried
    # BOTH). Wrap each node so the bulk rows are HYDRATED from Blob before a node that reads
    # them runs, and DEHYDRATED (offloaded + cleared) from the state before the checkpoint —
    # so the state only ever carries a small ref. Map: node → (hydrate-before, offload-after).
    from .bulk_tables import hydrate as _bulk_hydrate, dehydrate as _bulk_dehydrate

    _BULK_IO: "dict[str, tuple[list[str], list[str]]]" = {
        "ingest_node":               ([], ["full_tables"]),
        "deterministic_mapper_node": (["full_tables"], []),
        "pk_review_node":            (["full_tables"], []),
        "unique_table_review_node":  (["full_tables"], []),
        "pre_semantic_review_node":  (["full_tables"], []),
        "preprocess_node":           (["full_tables"], ["full_tables", "cleaned_tables"]),
        "hierarchy_node":            (["cleaned_tables"], []),
        "verify_hierarchy_node":     (["cleaned_tables"], []),
        "output_generator_node":     (["full_tables", "cleaned_tables"], []),
        "write_node":                (["cleaned_tables"], []),
        "udr_node":                  (["full_tables", "cleaned_tables"], []),
        "test_2_node":               (["cleaned_tables"], []),
    }

    def _add_node(name: str, fn: Callable) -> None:
        _hydrate_ch, _offload_ch = _BULK_IO.get(name, ([], []))

        async def _wrapped(state, _fn=fn, _h=_hydrate_ch, _o=_offload_ch):
            mig = state.get("migration_id") if isinstance(state, dict) else None
            if _h:
                await _bulk_hydrate(state, _h)
            try:
                result = _fn(state)
                if inspect.isawaitable(result):
                    result = await result
                out = result if isinstance(result, dict) else state
                await _bulk_dehydrate(out, mig, _o)
                return out
            finally:
                # Runs on success AND on GraphInterrupt/error, so the state LangGraph may
                # snapshot at a gate pause is stripped of bulk rows too (clear is idempotent
                # once the success path above already offloaded, so no double upload).
                if isinstance(state, dict):
                    await _bulk_dehydrate(state, mig, _o)

        # Register under the SAME node name so interrupt_after / edges are unchanged.
        graph.add_node(name, _wrapped)

    # ── Register all nodes ────────────────────────────────────────────
    _add_node("ingest_node", ingest_node)
    _add_node("deterministic_mapper_node", deterministic_mapper_node)
    _add_node("pk_review_node", pk_review_node)  # Group A step 1 — PK identification + approval
    _add_node("unique_table_review_node", unique_table_review_node)  # Group A step 2 — unique tables + approval
    _add_node("pre_semantic_review_node", pre_semantic_review_node)  # Group B gate — routing/mapping
    _add_node("grouping_review_node", grouping_review_node)  # B20.1 gate — FK/Shared classification approval
    _add_node("column_mapping_review_node", column_mapping_review_node)  # B14.1 gate — per-column dest mapping
    _add_node("semantic_mapper_node", semantic_mapper_node)
    _add_node("human_review_node", human_review_node)
    _add_node("preprocess_node", preprocess_node)
    _add_node("hierarchy_node", hierarchy_node)
    _add_node("verify_hierarchy_node", verify_hierarchy_node)
    _add_node("output_generator_node", output_generator_node)
    _add_node("write_node", write_node)
    _add_node("udr_node", udr_node)  # Node 10: Feature 7 UDR run + Feature 4 AL.6 emit

    # Feature 7.10 — Test 2 (column overlap must be explained by a FK). Wired
    # only when UDR_TESTS_ENABLED is set, so the default pipeline is unchanged.
    # Not in _STEP_NODES → it computes + records to state without pausing.
    _udr_tests_on = tests_enabled()
    if _udr_tests_on:
        _add_node("test_2_node", test_2_node)

    # ── Connect edges (START → Node 1, then chain) ────────────────────
    graph.add_edge(START, "ingest_node")

    # PK + Unique-table gates run RIGHT AFTER ingestion (they only need the source data), BEFORE the
    # deterministic column mapping — so the user sees the PK cards immediately after the Null/NaN
    # card instead of waiting ~11s for Node 2's LLM mapping. Matches the spec order:
    #   PK (B8.1) → Unique tables (B7.1) → deterministic mapping (B9/B10/B11) → routing → …
    graph.add_edge("ingest_node", "pk_review_node")
    graph.add_edge("pk_review_node", "unique_table_review_node")
    graph.add_edge("unique_table_review_node", "deterministic_mapper_node")
    graph.add_edge("deterministic_mapper_node", "pre_semantic_review_node")
    # Pre-Semantic (Group B) → B20.1 classification approval gate → (conditional to Node 3/5).
    # column_intelligence (classification, fk_candidates, shared-attribute tables) is built + stored
    # by pre_semantic, so the classification gate runs right after and APPLIES the user's FK/Shared
    # selection. Self-skips (no interrupt) when there is no classification.
    # Two-pass reorder so the gate order is B20 → B14.1 → Step-2 Column matching:
    #   pre_semantic PASS 1 (build analysis, no interrupt) → grouping_review (B20) →
    #   column_mapping_review (B14.1) → BACK to pre_semantic PASS 2 (Step-2 field-matching gate) →
    #   semantic / preprocess.
    graph.add_conditional_edges(
        "pre_semantic_review_node",
        _route_after_pre_semantic,
        {
            "grouping_review_node": "grouping_review_node",   # pass 1 → B20
            "semantic_mapper_node": "semantic_mapper_node",   # pass 2 done → semantic
            "preprocess_node": "preprocess_node",             # pass 2 done → skip semantic
        },
    )
    graph.add_edge("grouping_review_node", "column_mapping_review_node")
    graph.add_edge("column_mapping_review_node", "pre_semantic_review_node")  # loop back → pass 2

    # Conditional: Node 3 → 4 (GATE 1) or 5?
    graph.add_conditional_edges(
        "semantic_mapper_node",
        _route_after_semantic,
        {
            "human_review_node": "human_review_node",
            "preprocess_node": "preprocess_node",
        },
    )

    # If customer approved/rejected in GATE 1, continue
    graph.add_edge("human_review_node", "preprocess_node")

    # Continue: Node 5 → 6 → 7 → 8 → 9 → END
    graph.add_edge("preprocess_node", "hierarchy_node")
    graph.add_edge("hierarchy_node", "verify_hierarchy_node")
    if _udr_tests_on:
        # verify_hierarchy → test_2 → output  (FKs are known after verify)
        graph.add_edge("verify_hierarchy_node", "test_2_node")
        graph.add_edge("test_2_node", "output_generator_node")
    else:
        graph.add_edge("verify_hierarchy_node", "output_generator_node")
    graph.add_edge("output_generator_node", "write_node")
    # Node 9 → Node 10 (UDR + Activity-Log emit) → END. UDR is a non-fatal post-write
    # reporting side-effect; it self-skips unless the write completed.
    graph.add_edge("write_node", "udr_node")
    graph.add_edge("udr_node", END)

    # Nodes that should pause after completion so the user can review output
    # before the pipeline advances.  Gate nodes (pre_semantic_review_node,
    # human_review_node, verify_hierarchy_node, write_node) already use
    # interrupt() internally — they are NOT listed here to avoid double-pausing.
    _STEP_NODES = [
        "ingest_node",
        "deterministic_mapper_node",
        "semantic_mapper_node",
        "preprocess_node",
        "hierarchy_node",
        "output_generator_node",
    ]

    # Compile with checkpointer (or None for memory-only)
    if checkpointer:
        logger.info("Compiling StateGraph with PostgreSQL checkpointer (interrupt_after=step nodes)")
    else:
        logger.warning("Compiling StateGraph WITHOUT checkpointer (memory-only)")
    compiled_graph = (
        graph.compile(checkpointer=checkpointer, interrupt_after=_STEP_NODES)
        if checkpointer
        else graph.compile(interrupt_after=_STEP_NODES)
    )

    return compiled_graph


async def get_migration_graph() -> Any:
    """
    Factory function: Build the migration graph with a checkpointer.

    Priority:
      1. AsyncPostgresSaver  (Linux/Mac — psycopg3 async pool requires SelectorEventLoop)
      2. SyncPostgresSaver   (Windows dev — psycopg2, no event-loop restrictions)
      3. MemorySaver         (always available — state lost on process restart)

    Called once at app startup via the lifespan context manager (async).
    """
    import sys as _sys
    checkpointer = None
    sync_db_url = get_sync_db_url()

    # ── Option 1: async postgres checkpointer (non-Windows only) ──────────
    # On Windows the default ProactorEventLoop breaks psycopg3's async pool even
    # after setting WindowsSelectorEventLoopPolicy, because AsyncConnectionPool
    # spins background threads that acquire their own ProactorEventLoop.
    # Skip AsyncPostgresSaver on Windows and go straight to SyncPostgresSaver.
    if _sys.platform != "win32" and _AsyncPostgresSaver is not None and _AsyncConnectionPool is not None:
        logger.info(f"Initializing AsyncPostgresSaver with DB: {sync_db_url[:50]}...")
        try:
            pool = _AsyncConnectionPool(
                conninfo=sync_db_url,
                max_size=4,
                max_lifetime=300,
                # TCP keepalives keep the checkpointer's connection alive through the long,
                # LLM-heavy nodes. Without them a cloud DB / firewall silently drops the idle
                # socket and the next checkpoint write (aput_writes) dies with
                # "SSL error: unexpected eof while reading", FAILING the whole migration run.
                kwargs={
                    "autocommit": True,
                    "prepare_threshold": 0,
                    "keepalives": 1,
                    "keepalives_idle": 30,
                    "keepalives_interval": 10,
                    "keepalives_count": 5,
                },
                check=_AsyncConnectionPool.check_connection,
                open=False,
            )
            await pool.open()
            checkpointer = _AsyncPostgresSaver(pool)
            await checkpointer.setup()
            logger.info("AsyncPostgresSaver initialised and tables set up")
        except Exception as e:
            logger.warning(f"AsyncPostgresSaver init failed: {e}")

    # ── Option 2: sync postgres checkpointer ──────────────────────────────
    if checkpointer is None and _SyncPostgresSaver is not None:
        logger.info(f"Trying SyncPostgresSaver with DB: {sync_db_url[:50]}...")
        try:
            cp = _SyncPostgresSaver.from_conn_string(sync_db_url)
            cp.setup()
            checkpointer = cp
            logger.info("SyncPostgresSaver initialised and tables set up")
        except Exception as e:
            logger.warning(f"SyncPostgresSaver init failed: {e}")

    # ── Option 3: in-memory checkpointer (development fallback) ───────────
    if checkpointer is None:
        logger.warning(
            "No PostgresSaver available (langgraph-checkpoint-postgres not installed). "
            "Falling back to MemorySaver — checkpoint state will be LOST on process restart. "
            "Install langgraph-checkpoint-postgres and psycopg[binary] for persistence."
        )
        checkpointer = MemorySaver()

    graph = build_migration_graph(checkpointer)
    logger.info("Migration graph compiled and ready")
    return graph
