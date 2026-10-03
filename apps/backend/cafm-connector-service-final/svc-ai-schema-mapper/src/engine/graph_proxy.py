"""The compiled migration graph, minus a Go run's needless step pauses.

LangGraph's ``interrupt_after`` is fixed when a graph is compiled, but ``ainvoke`` accepts its own.
Every caller (worker run/resume, the app's inline runs, /advance, rerun-from) gets the graph from
migration_graph.get_migration_graph(), which returns this proxy, so the decision lives here once.
A caller that somehow bypasses it keeps today's pauses — the safe side.
"""
from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any

from . import selection

#: Waits between checkpoint reads when one fails: a pool blip is retried, a database that stays
#: unreachable fails the invocation (see _engine_of).
STATE_READ_RETRY_DELAYS_S = (0.5, 2.0)


class MigrationGraphProxy:
    def __init__(self, compiled: Any, step_nodes: Sequence[str]) -> None:
        self._graph = compiled
        self._step_nodes = list(step_nodes)

    async def ainvoke(self, input: Any, config: Any = None, **kwargs: Any) -> Any:
        if "interrupt_after" not in kwargs and await self._engine_of(input, config) == selection.ENGINE_GO:
            skip = selection.go_auto_continue_nodes()
            kwargs["interrupt_after"] = [n for n in self._step_nodes if n not in skip]
        return await self._graph.ainvoke(input, config, **kwargs)

    async def _engine_of(self, input: Any, config: Any) -> "str | None":
        if isinstance(input, dict):
            if input.get("engine"):
                return input["engine"]
            if input.get("migration_id") and ("source_filename" in input or "source_blob_path" in input):
                # A fresh run: decide now, and put it on the state so every resume agrees.
                input["engine"] = selection.choose_engine(
                    source_filename=input.get("source_filename"),
                    source_blob_path=input.get("source_blob_path"),
                )
                return input["engine"]
        if not config:
            return None
        # Not knowing is not the safe side for a Go run: the compiled pauses would stop it after a
        # step that writes no step pause, "running" with nothing to advance it. A read that keeps
        # failing fails the invocation, which ends the run visibly with a retry.
        for delay in (*STATE_READ_RETRY_DELAYS_S, None):
            try:
                snap = await self._graph.aget_state(config)
                break
            except Exception:  # noqa: BLE001 — retried, then raised
                if delay is None:
                    raise
                await asyncio.sleep(delay)
        values = getattr(snap, "values", None) or {}
        return values.get("engine")

    def __getattr__(self, name: str) -> Any:
        return getattr(self._graph, name)
