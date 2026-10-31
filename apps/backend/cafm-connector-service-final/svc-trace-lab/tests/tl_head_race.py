"""Deterministic head race for the annotation write tests (a plain helper module, like tl_scratch).

Two writers of one head are serialized by `SELECT ... FOR UPDATE`. Under READ COMMITTED the one that waits wakes
up after the winner committed, finds the row it matched already superseded and gets NO row back. A plain
`asyncio.gather` of two writers only sometimes interleaves like that (the second often starts after the first
committed). `lose_the_race` makes it happen every time, on the scratch database only:

  1. a raw lab transaction locks the head row,
  2. the writer under test is started and queues behind that lock (checked in pg_stat_activity),
  3. the raw transaction writes the next version exactly as a winning writer would (supersede, then insert,
     the same statements annotations.py uses) and commits,
  4. the writer under test wakes up and is returned: its result, or the exception it raised.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import text

from trace_lab import annotations

WAIT_S = 10.0


async def _waiters(conn: Any) -> int:
    # pg_stat_activity is a per-transaction snapshot: the holder transaction would keep seeing the first answer
    # (no waiter) for as long as it stays open, so the snapshot is cleared before every look.
    await conn.execute(text("SELECT pg_stat_clear_snapshot()"))
    return int((await conn.execute(text(
        "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
        "AND wait_event_type = 'Lock' AND pid <> pg_backend_pid()"))).scalar_one())


async def lose_the_race(lab: Any, head_id: str, writer: Callable[[], Awaitable[Any]], *,
                        tombstone: bool = False) -> Any:
    """Run `writer()` so that it loses a race for the head `head_id`; return what it returned or raised.
    The winner writes the next version of that head (a tombstone when `tombstone`)."""
    task: asyncio.Future | None = None
    try:
        async with lab.begin() as holder:
            h = (await holder.execute(text(
                "SELECT * FROM tracelab.annotations WHERE id = CAST(:h AS uuid) AND superseded_by IS NULL FOR UPDATE"),
                {"h": head_id})).mappings().one()
            task = asyncio.ensure_future(writer())
            deadline = time.monotonic() + WAIT_S
            while await _waiters(holder) < 1:
                if task.done():
                    raise AssertionError(f"the writer finished without queuing behind the head lock: {task.result()!r}")
                if time.monotonic() > deadline:
                    raise AssertionError("the writer never queued behind the head lock")
                await asyncio.sleep(0.05)
            new_id = str(uuid.uuid4())
            await holder.execute(annotations._SUPERSEDE, {"new": new_id, "head": head_id})
            await holder.execute(annotations._INSERT, {
                "id": new_id, "lineage": str(h["lineage_id"]), "version": int(h["version"]) + 1, "supersedes": head_id,
                "kind": h["kind"], "org": str(h["organization_id"]), "ckey": h["conversation_key"],
                "tid": h["turn_id"], "me": str(h["reviewer_id"]), "email": h["reviewer_email"], "tomb": tombstone,
                "verdict": None if tombstone else h["verdict"], "note": None if tombstone else h["note"],
                "blind": None if tombstone else h["blind"], **annotations._NO_PIN})
        return (await asyncio.gather(task, return_exceptions=True))[0]
    finally:
        if task is not None and not task.done():
            # The holder failed and has already rolled back, which released the head lock. Let the writer finish by
            # itself: cancelling a writer in the middle of its transaction can leave that connection open on the
            # server, and an open connection blocks the next test's DROP SCHEMA for good.
            await asyncio.wait({task}, timeout=WAIT_S)
            if not task.done():
                task.cancel()
