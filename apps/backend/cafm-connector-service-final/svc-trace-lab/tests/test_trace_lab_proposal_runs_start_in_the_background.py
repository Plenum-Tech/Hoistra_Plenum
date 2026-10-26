"""A run is started as a background task of this process: in an EMPTY context (nothing of the request that started it
follows it), kept referenced until it ends, and never able to raise into the request. No database, no network."""
from __future__ import annotations

import asyncio
import contextvars

from trace_lab import proposals


async def test_a_run_starts_in_an_empty_context_and_is_kept_until_it_ends(monkeypatch):
    request_scope = contextvars.ContextVar("request_scope", default="empty")
    seen: list[tuple[str, str]] = []
    release = asyncio.Event()

    async def fake_execute(run_id):
        seen.append((run_id, request_scope.get()))
        await release.wait()

    monkeypatch.setattr(proposals, "execute", fake_execute)
    request_scope.set("the request that started the run")
    task = proposals.run_in_background("run-1")
    await asyncio.sleep(0)
    assert task in proposals._RUNNING and not task.done()          # referenced: it cannot be collected mid-run
    release.set()
    await task
    await asyncio.sleep(0)
    assert seen == [("run-1", "empty")] and task not in proposals._RUNNING


async def test_a_run_that_cannot_start_never_raises_into_the_request(monkeypatch):
    async def explode(run_id):
        raise RuntimeError("the lab database is gone")

    monkeypatch.setattr(proposals, "execute", explode)
    task = proposals.run_in_background("run-2")                    # returns at once: the error stays in the task
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert task.done() and isinstance(task.exception(), RuntimeError)
    assert task not in proposals._RUNNING
