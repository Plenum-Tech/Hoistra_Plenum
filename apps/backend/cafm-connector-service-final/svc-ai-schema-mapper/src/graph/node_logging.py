"""Per-node input/output processing logs (dev-time observability).

``log_io(label)`` wraps an async LangGraph node so every node emits, at each level:
  - DEBUG  "[label] IN"   a compact snapshot of the INPUT state (counts + scalars)
  - INFO   "[label] OUT"  a milestone: elapsed ms + resulting status
  - DEBUG  "[label] OUT"  a compact snapshot of the OUTPUT state

The DEBUG I/O detail only renders when the service runs with DEBUG=true
(configure_logging(debug=True) -> root log level DEBUG). At INFO the milestone
still shows but the verbose I/O lines are suppressed, so production stays clean.
Set DEBUG=true (env) on the service to see the full per-node input/output trace.

Wrapping happens at graph-registration time (build_*_graph), so no node body is
touched and the instrumentation is consistent across all nodes.
"""
from __future__ import annotations

import functools
import time
from typing import Any, Callable

from cafm_shared.logging import get_logger

logger = get_logger("graph.node_io")

# Interrupt exceptions are control flow (HITL pause), never errors -> don't log as error.
_INTERRUPT_NAMES = {"GraphInterrupt", "NodeInterrupt", "Interrupt", "GraphBubbleUp"}

# State fields worth surfacing. list/dict/set -> length; scalars -> value.
_SUMMARY_KEYS = (
    "status", "current_node", "current_node_name", "error_message", "error_node",
    "new_schema_name", "external_cmms_name", "external_schema_source", "pending_gate_type",
    "external_tables", "canonical_tables", "table_count", "total_columns",
    "tier1_mappings", "tier2_auto_mapped", "tier2_flagged", "tier2_unmappable",
    "unmapped_after_t1", "extra_fields_config",
    "detected_foreign_keys", "detected_hierarchies", "junction_tables",
    "fetched_objects", "preprocessed_tables", "write_results",
    "total_records_fetched", "total_records_written", "fiix_ingestion_id",
)


def _summarize(state: Any) -> dict:
    """Compact, log-safe view of a node's state (no large value dumps)."""
    if not isinstance(state, dict):
        return {"state_type": type(state).__name__}
    out: dict[str, Any] = {"keys": len(state)}
    for k in _SUMMARY_KEYS:
        if k in state:
            v = state[k]
            out[k] = f"{type(v).__name__}[{len(v)}]" if isinstance(v, (list, dict, set, tuple)) else v
    return out


def log_io(label: str) -> Callable:
    """Decorator: log a node's INPUT (DEBUG), milestone (INFO), and OUTPUT (DEBUG)."""

    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        async def wrapper(state, *args, **kwargs):
            logger.debug(
                f"[{label}] IN", node=label,
                **{f"in_{k}": v for k, v in _summarize(state).items()},
            )
            t0 = time.perf_counter()
            try:
                result = await fn(state, *args, **kwargs)
            except Exception as exc:  # noqa: BLE001
                if type(exc).__name__ in _INTERRUPT_NAMES:
                    logger.debug(f"[{label}] PAUSE (HITL interrupt)", node=label)
                    raise
                logger.exception(f"[{label}] ERROR: {exc}", node=label)
                raise
            elapsed = round((time.perf_counter() - t0) * 1000, 1)
            snap = _summarize(result if isinstance(result, dict) else state)
            logger.info(
                f"[{label}] OUT status={snap.get('status', '?')} ({elapsed}ms)",
                node=label, elapsed_ms=elapsed,
            )
            logger.debug(
                f"[{label}] OUT", node=label,
                **{f"out_{k}": v for k, v in snap.items()},
            )
            return result

        return wrapper

    return decorator


def wrap_node(graph, name: str, fn: Callable) -> None:
    """Register a node on a StateGraph wrapped with input/output logging."""
    graph.add_node(name, log_io(name)(fn))
