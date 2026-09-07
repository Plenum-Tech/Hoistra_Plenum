"""Tiny timing decorator for LangGraph nodes (Activity-Log Section 2 per-stage durations).

Wraps an async node, measures how long it took, and writes ``<key>_duration_ms`` +
``<key>_at`` onto the returned state so a later node (udr_node) can surface them in the
Processing Log. The keys must be declared channels on ``MigrationState`` or LangGraph drops
them. Never alters the node's own result.
"""
from __future__ import annotations

import functools
import time
from datetime import datetime


def timed_node(key: str):
    def decorator(fn):
        @functools.wraps(fn)
        async def wrapper(state, *args, **kwargs):
            t0 = time.perf_counter()
            try:
                result = await fn(state, *args, **kwargs)
            finally:
                ms = round((time.perf_counter() - t0) * 1000, 2)
            target = result if isinstance(result, dict) else state
            try:
                target[f"{key}_duration_ms"] = ms
                target[f"{key}_at"] = datetime.utcnow().isoformat()
            except Exception:  # never let timing break a node
                pass
            return result

        return wrapper

    return decorator
