"""LangGraph state machine for schema mapping pipeline.

9-node pipeline that maps external CMMS schemas to plenum_cafm canonical schema:
1. Ingest — parse schema definition (DB, YAML, JSON, DDL)
2. Deterministic Mapping — 4-tier strategy (exact, alias, regex, LLM)
2a. Pre-Semantic Review — HITL gate for T1 match approval before semantic mapping
2.5. Pre-Semantic Preprocessing — normalize field names, enrich descriptions
3. Semantic Mapping — embeddings-based cosine similarity matching
4. Human Review — HITL gate for field mapping approval/correction/custom mapping
5. Hierarchy Detection — detect FK relationships and build hierarchy tree
6. Verify Hierarchy — HITL gate for user approval/correction
7. Output Generation — generate final JsonMapperConfig

All nodes are async. State persisted via PostgreSQL checkpointer.
"""

import logging
from typing import Any

from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver

try:
    from langgraph.checkpoint.postgres import PostgresSaver
except ImportError:
    try:
        from langgraph.saver import PostgresSaver
    except ImportError:
        PostgresSaver = None

# Async Postgres checkpointer (Linux/Mac — psycopg3 async pool).
try:
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver as _AsyncPostgresSaver
    from psycopg_pool import AsyncConnectionPool as _AsyncConnectionPool
except ImportError:
    _AsyncPostgresSaver = None
    _AsyncConnectionPool = None

# Sync Postgres checkpointer (Windows dev fallback — psycopg2).
try:
    from langgraph.checkpoint.postgres import PostgresSaver as _SyncPostgresSaver
except ImportError:
    _SyncPostgresSaver = None

from ..db import get_sync_db_url

from .schema_state import SchemaMappingState
from .nodes.canonical_schema_node import canonical_schema_node
from .nodes.schema_ingest_node import schema_ingest_node
from .nodes.schema_deterministic_node import schema_deterministic_node
from .nodes.schema_pre_semantic_node import schema_pre_semantic_node
from .nodes.schema_preprocess_node import schema_preprocess_node
from .nodes.schema_semantic_node import schema_semantic_node
from .nodes.schema_human_review_node import schema_human_review_node
from .nodes.schema_hierarchy_node import schema_hierarchy_node
from .nodes.schema_verify_hierarchy_node import schema_verify_hierarchy_node
from .nodes.schema_output_node import schema_output_node
from .nodes.schema_artifacts_review_node import schema_artifacts_review_node
from .nodes.schema_write_node import schema_write_node

from cafm_shared.logging import get_logger
logger = get_logger(__name__)


# ── Conditional Edge Functions ────────────────────────────────────────────────


def _should_skip_semantic_mapper(state: SchemaMappingState) -> str:
    """
    After Node 2.5 (pre-semantic preprocessing):
    - If unmapped_after_t1 is empty → skip Node 3, go directly to Node 4
    - Otherwise → proceed to Node 3 (semantic mapping)
    """
    unmapped = state.get("unmapped_after_t1", [])
    if not unmapped:
        logger.info("[Schema 2.5→4] No unresolved fields after preprocessing; skipping semantic mapping")
        return "schema_human_review_node"

    logger.info(f"[Schema 2.5→3] {len(unmapped)} unresolved fields; proceeding to semantic mapping")
    return "schema_semantic_node"


def build_schema_mapping_graph(checkpointer: PostgresSaver = None) -> Any:
    """
    Construct and compile the 6-node schema mapping StateGraph.

    Args:
        checkpointer: Optional PostgresSaver instance for state persistence

    Returns:
        Compiled StateGraph ready for astream_events() / ainvoke()
    """

    # Create the StateGraph
    graph = StateGraph(SchemaMappingState)

    # ── Register all nodes (0-8, including 2a and 2.5) ───────────────────────
    # wrap_node() registers each node wrapped with input/output processing logs
    # (DEBUG IN/OUT snapshot + INFO milestone per node). See graph/node_logging.py.
    from .node_logging import wrap_node
    wrap_node(graph, "canonical_schema_node", canonical_schema_node)
    wrap_node(graph, "schema_ingest_node", schema_ingest_node)
    wrap_node(graph, "schema_deterministic_node", schema_deterministic_node)
    wrap_node(graph, "schema_pre_semantic_node", schema_pre_semantic_node)
    wrap_node(graph, "schema_preprocess_node", schema_preprocess_node)
    wrap_node(graph, "schema_semantic_node", schema_semantic_node)
    wrap_node(graph, "schema_human_review_node", schema_human_review_node)
    wrap_node(graph, "schema_hierarchy_node", schema_hierarchy_node)
    wrap_node(graph, "schema_verify_hierarchy_node", schema_verify_hierarchy_node)
    wrap_node(graph, "schema_output_node", schema_output_node)
    wrap_node(graph, "schema_artifacts_review_node", schema_artifacts_review_node)
    wrap_node(graph, "schema_write_node", schema_write_node)

    # ── Connect edges ──────────────────────────────────────────────────────
    # START → Node 0 (Fetch canonical schema)
    graph.add_edge(START, "canonical_schema_node")

    # Node 0 → Node 1 (Ingest external schema)
    graph.add_edge("canonical_schema_node", "schema_ingest_node")

    # Node 1 → Node 2 (Deterministic Mapping)
    graph.add_edge("schema_ingest_node", "schema_deterministic_node")

    # Node 2 → Node 2a (Pre-Semantic Review HITL gate — always runs)
    graph.add_edge("schema_deterministic_node", "schema_pre_semantic_node")

    # Node 2a → Node 2.5 (Pre-Semantic Preprocessing — always runs after gate)
    graph.add_edge("schema_pre_semantic_node", "schema_preprocess_node")

    # Conditional: Node 2.5 → Node 3 (Semantic) OR Node 4 (Human Review)?
    # If no unresolved fields remain after preprocessing, skip semantic mapper
    graph.add_conditional_edges(
        "schema_preprocess_node",
        _should_skip_semantic_mapper,
        {
            "schema_semantic_node": "schema_semantic_node",
            "schema_human_review_node": "schema_human_review_node",
        },
    )

    # Node 3 → Node 4 (Human Review — HITL GATE for field mapping approval)
    graph.add_edge("schema_semantic_node", "schema_human_review_node")

    # Node 4 → Node 5 (Hierarchy Detection)
    graph.add_edge("schema_human_review_node", "schema_hierarchy_node")

    # Node 5 → Node 6 (Verify Hierarchy — HITL GATE)
    graph.add_edge("schema_hierarchy_node", "schema_verify_hierarchy_node")

    # Node 6 → Node 7 (Output Generation)
    graph.add_edge("schema_verify_hierarchy_node", "schema_output_node")

    # Node 7 → Node 7.5 (Artifacts Review Gate — HITL)
    graph.add_edge("schema_output_node", "schema_artifacts_review_node")

    # Node 7.5 → Node 8 (Write to database)
    graph.add_edge("schema_artifacts_review_node", "schema_write_node")

    # Node 8 → END
    graph.add_edge("schema_write_node", END)

    # Nodes that pause after completion (interrupt_after) for node-by-node review.
    # Gate nodes (schema_human_review_node, schema_verify_hierarchy_node) use
    # interrupt() inside the node body — they do NOT need interrupt_after.
    # schema_preprocess_node is intentionally excluded — it is a silent background
    # enrichment step with no user decisions; it runs automatically between Node 2
    # and Node 3 without pausing.
    _STEP_NODES = [
        "canonical_schema_node",
        "schema_ingest_node",
        "schema_deterministic_node",
        "schema_semantic_node",
        "schema_hierarchy_node",
        "schema_output_node",
    ]

    # Compile with checkpointer (or None for memory-only)
    if checkpointer:
        logger.info("Compiling schema mapping graph with PostgreSQL checkpointer")
        compiled_graph = graph.compile(
            checkpointer=checkpointer,
            interrupt_after=_STEP_NODES,
        )
    else:
        logger.info("Compiling schema mapping graph with memory checkpointer (development mode)")
        compiled_graph = graph.compile(interrupt_after=_STEP_NODES)

    logger.info(
        "Schema mapping graph compiled: 12 nodes (incl. Node 2a pre-semantic gate + Node 2.5 preprocessing + Node 7.5 artifacts review), "
        "node-by-node step pauses, 1 conditional edge (semantic mapper skip), "
        "4 HITL gates (pre-semantic approval + field mapping approval + hierarchy verification + artifacts review)"
    )

    return compiled_graph


def get_schema_mapping_graph() -> Any:
    """
    Build the schema mapping graph with no checkpointer (legacy entry point).

    Prefer get_schema_mapping_graph_async() — it returns a graph backed by the
    same Postgres-first checkpointer chain the migration graph uses, so saved
    UDR scripts can resume / rerun from a stored phase across restarts.
    """
    return build_schema_mapping_graph(checkpointer=None)


async def get_schema_mapping_graph_async() -> Any:
    """
    Factory function: Build the schema mapping graph with a checkpointer.

    Priority (mirrors migration_graph.get_migration_graph):
      1. AsyncPostgresSaver  (Linux/Mac — psycopg3 async pool requires SelectorEventLoop)
      2. SyncPostgresSaver   (Windows dev — psycopg2, no event-loop restrictions)
      3. MemorySaver         (always available — state lost on process restart)

    Called once at app startup via the lifespan context manager (async). State
    persistence unblocks Feature 4 rerun/reset-to-phase endpoints.
    """
    import sys as _sys
    checkpointer = None
    sync_db_url = get_sync_db_url()

    # ── Option 1: async postgres checkpointer (non-Windows only) ──────────
    if _sys.platform != "win32" and _AsyncPostgresSaver is not None and _AsyncConnectionPool is not None:
        logger.info(f"[Schema] Initializing AsyncPostgresSaver with DB: {sync_db_url[:50]}...")
        try:
            pool = _AsyncConnectionPool(
                conninfo=sync_db_url,
                max_size=4,
                max_lifetime=300,
                # TCP keepalives keep the checkpointer connection alive through long, LLM-heavy
                # nodes so a cloud DB / firewall doesn't silently drop the idle socket (→
                # "SSL error: unexpected eof while reading" on the next checkpoint write).
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
            logger.info("[Schema] AsyncPostgresSaver initialised and tables set up")
        except Exception as e:
            logger.warning(f"[Schema] AsyncPostgresSaver init failed: {e}")

    # ── Option 2: sync postgres checkpointer ──────────────────────────────
    if checkpointer is None and _SyncPostgresSaver is not None:
        logger.info(f"[Schema] Trying SyncPostgresSaver with DB: {sync_db_url[:50]}...")
        try:
            cp = _SyncPostgresSaver.from_conn_string(sync_db_url)
            cp.setup()
            checkpointer = cp
            logger.info("[Schema] SyncPostgresSaver initialised and tables set up")
        except Exception as e:
            logger.warning(f"[Schema] SyncPostgresSaver init failed: {e}")

    # ── Option 3: in-memory checkpointer (development fallback) ───────────
    if checkpointer is None:
        logger.warning(
            "[Schema] No PostgresSaver available — falling back to MemorySaver. "
            "Schema mapping state will be LOST on process restart. "
            "Install langgraph-checkpoint-postgres and psycopg[binary] for persistence."
        )
        checkpointer = MemorySaver()

    graph = build_schema_mapping_graph(checkpointer)
    logger.info("Schema mapping graph compiled and ready")
    return graph
