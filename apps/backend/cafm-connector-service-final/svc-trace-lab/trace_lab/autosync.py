"""Trace Lab syncs the chat's new answers by itself (3 Nov 2026).

Until now a run of live questions appeared only when someone pressed "Sync now". This loop does the same sync for every
company every TRACE_LAB_AUTO_SYNC_S seconds (default 120; 0 turns it off) once the schema is ready. It is the same
runs.sync_live the button runs: it only ever adds questions for answers it has not seen (one lock, the synced-turn
index), so older runs, notes, labels, expected answers and failure modes are never touched. A failed round is logged
and the next one tries again; nothing here can stop the service or raise into a request.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog

from . import db

log = structlog.get_logger(__name__)

#: Rounds of one tick: a backlog of more than this many batches is finished by the next tick.
MAX_BATCHES = 5
FIRST_DELAY_S = 20.0


def every_s() -> float:
    try:
        return max(0.0, float(os.environ.get("TRACE_LAB_AUTO_SYNC_S", "120")))
    except ValueError:
        return 120.0


STATUS: dict[str, Any] = {"enabled": False, "every_s": 0, "last_run_at": None, "last_ok": None,
                          "last_new_questions": 0, "last_error": None, "next_at": None}
_task: asyncio.Task | None = None


async def tick(now: datetime | None = None) -> dict[str, Any]:
    """One round: sync every company until nothing is left or MAX_BATCHES ran."""
    from . import runs

    total = 0
    started = now or datetime.now(timezone.utc)
    try:
        for _ in range(MAX_BATCHES):
            out = await runs.sync_live(None)
            total += int(out.get("new_questions") or 0)
            if not out.get("more"):
                break
        STATUS.update(last_run_at=started.isoformat(), last_ok=True, last_new_questions=total, last_error=None)
        if total:
            log.info("trace_lab.autosync.synced", new_questions=total)
    except Exception as exc:  # noqa: BLE001 - the next round tries again
        STATUS.update(last_run_at=started.isoformat(), last_ok=False, last_error=str(exc)[:300])
        log.warning("trace_lab.autosync.failed", error=str(exc)[:300])
    return dict(STATUS)


async def _loop(every: float) -> None:
    await asyncio.sleep(min(FIRST_DELAY_S, every))
    while True:
        if db.STATE is not None and getattr(db.STATE, "status", None) == "ready":
            await tick()
        STATUS["next_at"] = (datetime.now(timezone.utc) + timedelta(seconds=every)).isoformat()
        await asyncio.sleep(every)


def start() -> bool:
    """Start the loop (once) when TRACE_LAB_AUTO_SYNC_S is above 0. Called from startup, never raises."""
    global _task
    every = every_s()
    STATUS.update(enabled=every > 0, every_s=int(every))
    if every <= 0 or (_task is not None and not _task.done()):
        return False
    try:
        _task = asyncio.get_running_loop().create_task(_loop(every), name="trace-lab-autosync")
        STATUS["next_at"] = (datetime.now(timezone.utc) + timedelta(seconds=min(FIRST_DELAY_S, every))).isoformat()
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("trace_lab.autosync.start_failed", error=str(exc)[:200])
        return False


async def stop() -> None:
    global _task
    t, _task = _task, None
    if t is not None and not t.done():
        t.cancel()
        try:
            await t
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass


def status() -> dict[str, Any]:
    return dict(STATUS)
