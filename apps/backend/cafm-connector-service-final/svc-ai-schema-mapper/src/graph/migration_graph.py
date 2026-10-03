"""LangGraph state machine construction for svc-AI-Schema-Mapper.

9-node migration pipeline with PostgreSQL checkpointer and HITL interrupt gates.
"""

import inspect
import logging
import weakref
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
from .nodes.udr_node import udr_node_and_release
from .nodes.udr_tests import test_2_node, tests_enabled

from cafm_shared.logging import get_logger
logger = get_logger(__name__)


# ── Node implementations are now imported from .nodes modules ──────────────


class MigrationCancelled(Exception):
    """The run was cancelled (DELETE /api/migration/{id}); raised at the next step boundary."""


async def _is_cancelled(migration_id) -> bool:
    """Whether the run was cancelled. One small read per step (compared as text, which works
    whichever type the two databases give the id).

    A failure to ask is "not cancelled": the check exists to stop a run someone asked to stop,
    and a database hiccup must not end a run nobody asked to stop."""
    if not migration_id:
        return False
    try:
        from sqlalchemy import text as _text
        from ..db import get_async_engine
        async with get_async_engine().connect() as conn:
            got = (await conn.execute(
                _text("SELECT status FROM plenum_cafm.migration_jobs WHERE id::text = :i"),
                {"i": str(migration_id)})).scalar()
        return got == "cancelled"
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"migration cancel check failed for {migration_id}: {exc}")
        return False
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


#: Bulk-row offload (graph/bulk_tables.py): node -> (hydrate before it runs, offload after).
#: The output step's intermediate_schema + output_sql_script (every row again, twice) are
#: offloaded too: left in the state they made the post-output checkpoint ~150 MB, saved for
#: many minutes while the job already read "paused" (migration f87078d7, 29 Sep 2026). Only
#: the write step reads them.
BULK_IO: "dict[str, tuple[list[str], list[str]]]" = {
    "ingest_node":               ([], ["full_tables"]),
    "deterministic_mapper_node": (["full_tables"], []),
    "pk_review_node":            (["full_tables"], []),
    "unique_table_review_node":  (["full_tables"], []),
    "pre_semantic_review_node":  (["full_tables"], []),
    "preprocess_node":           (["full_tables"], ["full_tables", "cleaned_tables"]),
    "hierarchy_node":            (["cleaned_tables"], []),
    "verify_hierarchy_node":     (["cleaned_tables"], []),
    "output_generator_node":     (["full_tables", "cleaned_tables"],
                                  ["intermediate_schema", "output_sql_script"]),
    "write_node":                (["cleaned_tables", "intermediate_schema", "output_sql_script"], []),
    "udr_node":                  (["full_tables", "cleaned_tables"], []),
    "test_2_node":               (["cleaned_tables"], []),
}


#: What a node reads when the engine does its step instead: the rows stay in the engine's files.
GO_BULK_IO: "dict[str, tuple[str, tuple[list[str], list[str]]]]" = {
    "preprocess_node": ("preprocess", ([], [])),
    "output_generator_node": ("outputs", ([], ["intermediate_schema", "output_sql_script"])),
    "write_node": ("write", (["intermediate_schema"], [])),
}


def bulk_io_for(name: str, state: Any, engine_steps: "frozenset[str] | None" = None) -> "tuple[list[str], list[str]]":
    """(hydrate before, offload after) for this node in this run."""
    from ..engine import selection

    if name in GO_BULK_IO and isinstance(state, dict):
        step, io = GO_BULK_IO[name]
        steps = selection.ENGINE_STEPS if engine_steps is None else engine_steps
        if state.get("engine") == selection.ENGINE_GO and step in steps:
            return io
    return BULK_IO.get(name, ([], []))


#: Step nodes that pause after they finish so a person can look before the run moves on. Gate
#: nodes (pk/unique/pre-semantic/human/verify/write) interrupt() themselves and are not listed.
#: A Go-engine run drops the pauses of the steps the engine does (engine/graph_proxy.py).
STEP_NODES = [
    "ingest_node",
    "deterministic_mapper_node",
    "semantic_mapper_node",
    "preprocess_node",
    "hierarchy_node",
    "output_generator_node",
]


class NodeFailed(RuntimeError):
    """A node reported failure in its state; the run must not carry on as if it had paused."""


def _error_ends_go_step(name: str, out: dict, before: dict) -> bool:
    """A Go run has no pause after the steps the engine does (ingest, preprocess, output). The pause
    is what ended a Python run whose step recorded an error without status="failed" (a file that
    cannot be parsed, no rows, data lost in dedup): the worker failed it there with that error. On
    a run that started the step on the engine, the error the step records ends the run itself."""
    from ..engine import selection

    if name not in selection.go_auto_continue_nodes():
        return False
    if selection.ENGINE_GO not in (before.get("engine"), out.get("engine")):
        return False
    message = out.get("error_message")
    return bool(message) and message != before.get("error_message")


async def raise_if_node_failed(name: str, out: Any, before: "dict | None" = None) -> None:
    """End the run when a node hands back state with status="failed".

    Every step node (ingest, deterministic, semantic, preprocess, hierarchy, output) catches
    its own exceptions and returns state with status="failed" instead of raising. Nothing read
    that: LangGraph went on to the node's interrupt_after pause, the worker logged the graph as
    complete, and the job row kept its last status — "running", with no gate — which nothing
    can resume (30 Sep 2026, migration bce6b4d2: one refused connection while recording the
    ingest pause left the run "running" for half an hour). Record the failure on the row
    (retried) and raise, so the worker ends the run as failed and it can be retried from the step.
    """
    if not isinstance(out, dict):
        return
    if out.get("status") != "failed" and not (before is not None and _error_ends_go_step(name, out, before)):
        return
    message = str(out.get("error_message") or f"{name} failed")
    migration_id = out.get("migration_id")
    if migration_id:
        from .nodes.db_writer import write_error

        error_node = out.get("error_node")
        await write_error(
            str(migration_id),
            message,
            error_node if isinstance(error_node, int) else None,
        )
    raise NodeFailed(f"{name}: {message}")


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
    # so the state only ever carries a small ref. Map: BULK_IO (module level) — node →
    # (hydrate-before, offload-after).
    from .bulk_tables import hydrate as _bulk_hydrate, dehydrate as _bulk_dehydrate

    def _add_node(name: str, fn: Callable) -> None:
        async def _wrapped(state, _fn=fn, _name=name):
            _h, _o = bulk_io_for(_name, state)
            mig = state.get("migration_id") if isinstance(state, dict) else None
            # A cancelled run stops here, before its next step, instead of running to the end
            # and writing tables for a migration the user stopped. The step already running
            # when cancel arrived finishes; nothing after it starts.
            if await _is_cancelled(mig):
                logger.info(f"migration {mig} cancelled; not starting {_name}")
                raise MigrationCancelled(f"Migration {mig} was cancelled before {_name}")
            if _h:
                await _bulk_hydrate(state, _h)
            before = ({"engine": state.get("engine"), "error_message": state.get("error_message")}
                      if isinstance(state, dict) else None)
            try:
                result = _fn(state)
                if inspect.isawaitable(result):
                    result = await result
                out = result if isinstance(result, dict) else state
                await _bulk_dehydrate(out, mig, _o)
                # A node that caught its own error and returned status="failed" must not be
                # left to pause as if it had succeeded (see raise_if_node_failed).
                await raise_if_node_failed(_name, out, before)
                return out
            finally:
                # Runs on success AND on GraphInterrupt/error, so the state LangGraph may
                # snapshot at a gate pause is stripped of bulk rows too (clear is idempotent
                # once the success path above already offloaded, so no double upload).
                if isinstance(state, dict):
                    await _bulk_dehydrate(state, mig, _o)

        # Register under the SAME node name so interrupt_after / edges are unchanged; the update
        # carries only the keys the step changed (only_changed).
        graph.add_node(name, only_changed(_wrapped))

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
    # Node 10: Feature 7 UDR run + Feature 4 AL.6 emit; a completed Go run then frees its engine files
    _add_node("udr_node", udr_node_and_release)

    # Feature 7.10 — Test 2 (column overlap must be explained by a FK). Wired
    # only when UDR_TESTS_ENABLED is set, so the default pipeline is unchanged.
    # Not in STEP_NODES → it computes + records to state without pausing.
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

    # Compile with checkpointer (or None for memory-only)
    if checkpointer:
        logger.info("Compiling StateGraph with PostgreSQL checkpointer (interrupt_after=step nodes)")
    else:
        logger.warning("Compiling StateGraph WITHOUT checkpointer (memory-only)")
    compiled_graph = (
        graph.compile(checkpointer=checkpointer, interrupt_after=STEP_NODES)
        if checkpointer
        else graph.compile(interrupt_after=STEP_NODES)
    )

    return compiled_graph


from .checkpoint_serde import CompressedJsonPlus  # noqa: E402

import hashlib as _hashlib  # noqa: E402

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer as _JsonPlus  # noqa: E402

_FINGERPRINT_SERDE = _JsonPlus()


def _fingerprint(key: str, value: Any) -> Any:
    """What tells whether a step changed a state key: the value itself for a primitive; for
    anything else the object (a replaced value is a change) and its serialized bytes (a value
    changed in place is a change). Bulk rows held inline, and anything that will not serialize,
    always count as changed — written as they always were."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return (type(value), value)
    from .bulk_tables import BULK_CHANNELS

    if key in BULK_CHANNELS and value:
        return object()
    try:
        data = _FINGERPRINT_SERDE.dumps_typed(value)[1] or b""
        return (type(value), id(value), _hashlib.blake2b(data, digest_size=16).digest())
    except Exception:  # noqa: BLE001
        return object()


def only_changed(fn: Callable) -> Callable:
    """Wrap a node so its update carries only the state keys it changed.

    Nodes return the whole state dict, and LangGraph writes every key of a node's update to the
    checkpoint — the whole state, twice, at every superstep (30 MB of checkpoint for one workbook
    before the write, 2 Oct 2026). A key the node neither replaced nor changed in place keeps its
    value in its channel unwritten, so every later node, every route and every resume reads exactly
    what it read before. A node that returns an update rather than its state is passed through."""
    async def _changed_only(state):
        before = {k: _fingerprint(k, v) for k, v in state.items()} if isinstance(state, dict) else None
        out = fn(state)
        if inspect.isawaitable(out):
            out = await out
        if before is None or out is not state or not isinstance(out, dict):
            return out
        return {k: v for k, v in out.items() if k not in before or _fingerprint(k, v) != before[k]}

    return _changed_only




#: The checkpoint pool each event loop opened (its tables set up), and the lock its first build
#: holds: a pool is bound to its loop, and jobs starting together must not each open one.
_POOLS: "weakref.WeakKeyDictionary[Any, Any]" = weakref.WeakKeyDictionary()
_POOL_LOCKS: "weakref.WeakKeyDictionary[Any, Any]" = weakref.WeakKeyDictionary()


async def get_migration_graph() -> Any:
    """
    Factory function: Build the migration graph with a checkpointer.

    Priority:
      1. AsyncPostgresSaver  (Linux/Mac — psycopg3 async pool requires SelectorEventLoop)
      2. SyncPostgresSaver   (Windows dev — psycopg2, no event-loop restrictions)
      3. MemorySaver         (always available — state lost on process restart)

    Called once at app startup via the lifespan context manager (async).
    """
    import asyncio as _asyncio
    import sys as _sys

    # The pool is opened (and setup() run) once per event loop: a worker job used to open a new pool
    # of Azure connections every time and never closed it (2 Oct 2026). Each call still gets its
    # own saver — a saver runs its operations one at a time behind its lock, so jobs sharing one
    # would wait for each other's checkpoints.
    _loop = _asyncio.get_running_loop()
    _lock = _POOL_LOCKS.get(_loop)
    if _lock is None:
        _lock = _POOL_LOCKS[_loop] = _asyncio.Lock()
    async with _lock:
        _pool = _POOLS.get(_loop)
        if _pool is not None:
            graph = build_migration_graph(_AsyncPostgresSaver(_pool, serde=CompressedJsonPlus()))
            from ..engine.graph_proxy import MigrationGraphProxy

            return MigrationGraphProxy(graph, STEP_NODES)
        return await _build_migration_graph(_loop)


async def _build_migration_graph(_loop) -> Any:
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
                min_size=1,
                max_size=10,  # one per job ARQ may run at once (max_jobs), sharing this pool
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
            try:
                checkpointer = _AsyncPostgresSaver(pool, serde=CompressedJsonPlus())
                await checkpointer.setup()
            except Exception:
                checkpointer = None
                await pool.close()  # a pool whose setup failed is not left open
                raise
            _POOLS[_loop] = pool
            logger.info("AsyncPostgresSaver initialised and tables set up")
        except Exception as e:
            logger.warning(f"AsyncPostgresSaver init failed: {e}")

    # ── Option 2: sync postgres checkpointer ──────────────────────────────
    if checkpointer is None and _SyncPostgresSaver is not None:
        logger.info(f"Trying SyncPostgresSaver with DB: {sync_db_url[:50]}...")
        try:
            cp = _SyncPostgresSaver.from_conn_string(sync_db_url)
            cp.serde = CompressedJsonPlus()
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
        checkpointer = MemorySaver(serde=CompressedJsonPlus())

    graph = build_migration_graph(checkpointer)
    logger.info("Migration graph compiled and ready")
    from ..engine.graph_proxy import MigrationGraphProxy

    return MigrationGraphProxy(graph, STEP_NODES)
