"""Migration graph runs in flight in this process, by migration id."""
from __future__ import annotations

import asyncio


#: Migration graph runs in flight in THIS process, by migration id. A node writes its
#: step_paused row before LangGraph has finished the run and saved the pause checkpoint, so for a
#: moment the database says "paused" while the run that paused is still going. An /advance that
#: lands in that window used to flip the job to running and start a second run on the same
#: thread while the first was still finishing; the first then wrote step_paused again and the
#: migration sat there, because every client advances a given pause only once (migration
#: 117b1beb, 24 Sep 2026, stuck after preprocessing until advanced by hand).
MIGRATION_RUNS: dict[str, asyncio.Task] = {}


def track_migration_run(migration_id, coro) -> asyncio.Task:
    """Start a migration graph run as a task and remember it until it ends."""
    task = asyncio.create_task(coro)
    key = str(migration_id)
    MIGRATION_RUNS[key] = task

    def _forget(done: asyncio.Task, key: str = key) -> None:
        if MIGRATION_RUNS.get(key) is done:
            MIGRATION_RUNS.pop(key, None)

    task.add_done_callback(_forget)
    return task


async def await_migration_run(migration_id, timeout: float = 120.0) -> bool:
    """Wait for this migration's in-flight run to finish. False if it is still going after
    ``timeout`` seconds. Never raises: the run's own outcome is recorded by the run."""
    task = MIGRATION_RUNS.get(str(migration_id))
    if task is None or task.done():
        return True
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout)
    except asyncio.TimeoutError:
        return False
    except BaseException:  # noqa: BLE001 - the run failed; that is its own report
        pass
    return True
