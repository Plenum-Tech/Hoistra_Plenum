"""An advance waits for the run that wrote the pause.

A node writes its step_paused row before LangGraph has finished the run, so for a moment the
database says "paused" while the run that paused is still going. On 24 Sep 2026 an /advance
landed in that window (migration 117b1beb, after preprocessing): it started a second run on the
same thread, the first finished and wrote step_paused again, and the migration sat there -
every client advances a given pause only once.
"""
from __future__ import annotations

import asyncio

from src.migration_runs import MIGRATION_RUNS as _MIGRATION_RUNS
from src.migration_runs import await_migration_run as _await_migration_run
from src.migration_runs import track_migration_run as _track_migration_run


def test_an_advance_waits_until_the_run_in_flight_has_finished():
    async def scenario():
        order: list[str] = []

        async def run():
            await asyncio.sleep(0.05)
            order.append("run finished")

        _track_migration_run("m-1", run())
        assert await _await_migration_run("m-1", timeout=5) is True
        order.append("advance decides")
        return order

    assert asyncio.run(scenario()) == ["run finished", "advance decides"]


def test_a_finished_run_is_forgotten():
    async def scenario():
        task = _track_migration_run("m-2", asyncio.sleep(0))
        await task
        await asyncio.sleep(0)
        return "m-2" in _MIGRATION_RUNS

    assert asyncio.run(scenario()) is False


def test_nothing_in_flight_means_no_wait():
    assert asyncio.run(_await_migration_run("never-started", timeout=0.01)) is True


def test_a_run_still_going_after_the_timeout_is_reported_not_waited_on_forever():
    async def scenario():
        _track_migration_run("m-3", asyncio.sleep(1))
        return await _await_migration_run("m-3", timeout=0.01)

    assert asyncio.run(scenario()) is False


def test_a_run_that_failed_does_not_fail_the_advance():
    async def scenario():
        async def boom():
            raise RuntimeError("node error")

        _track_migration_run("m-4", boom())
        return await _await_migration_run("m-4", timeout=5)

    assert asyncio.run(scenario()) is True


def test_a_newer_run_is_not_forgotten_when_an_older_one_ends():
    async def scenario():
        old = _track_migration_run("m-5", asyncio.sleep(0.01))
        new = _track_migration_run("m-5", asyncio.sleep(0.2))
        await old
        await asyncio.sleep(0)
        still = _MIGRATION_RUNS.get("m-5") is new
        await new
        return still

    assert asyncio.run(scenario()) is True
