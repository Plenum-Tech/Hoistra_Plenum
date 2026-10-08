"""An advance waits for the run that wrote the pause.

A node writes its step_paused row before LangGraph has finished the run, so for a moment the
database says "paused" while the run that paused is still going. On 24 Sep 2026 an /advance
landed in that window (migration 117b1beb, after preprocessing): it started a second run on the
same thread, the first finished and wrote step_paused again, and the migration sat there -
every client advances a given pause only once.

The wait above only sees runs in the app's own process. On 29 Sep 2026 (migration f87078d7,
289,606 rows) the run that paused was in the ARQ worker: the output step wrote its pause, then
spent many minutes saving its checkpoint, and an /advance 4 s later resumed the graph in the
app from the checkpoint BEFORE the output step - so the whole output step ran a second time
beside the first. So an advance also waits for the saved checkpoint itself to be past the step
that paused, whichever process is saving it.
"""
from __future__ import annotations

import asyncio

from src.migration_runs import MIGRATION_RUNS as _MIGRATION_RUNS
from src.migration_runs import await_migration_run as _await_migration_run
from src.migration_runs import track_migration_run as _track_migration_run
from src.migration_runs import STEP_PAUSE_NODES, await_checkpoint_past, paused_node_for


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


class _Task:
    def __init__(self, name):
        self.name = name


class _Snap:
    def __init__(self, nxt, tasks=None):
        self.next = nxt
        self.tasks = tuple(_Task(n) for n in (tasks if tasks is not None else nxt))


class _Graph:
    """A graph whose saved checkpoint reads each ``nexts`` in turn, then the last for ever."""

    def __init__(self, *nexts, error=None):
        self.nexts = list(nexts)
        self.reads = 0
        self.error = error

    async def aget_state(self, config):
        self.reads += 1
        if self.error:
            raise self.error
        return _Snap(self.nexts[min(self.reads, len(self.nexts)) - 1])


CFG = {"configurable": {"thread_id": "m-9"}}


def test_every_step_that_pauses_names_the_node_that_wrote_the_pause():
    assert paused_node_for("step_8_output_generation") == "output_generator_node"
    assert paused_node_for("step_1_ingest") == "ingest_node"
    assert set(STEP_PAUSE_NODES.values()) == {
        "ingest_node", "deterministic_mapper_node", "semantic_mapper_node",
        "preprocess_node", "hierarchy_node", "output_generator_node"}
    assert paused_node_for("human_review") is None and paused_node_for(None) is None


def test_an_advance_waits_until_the_checkpoint_is_past_the_step_that_paused():
    g = _Graph(("output_generator_node",), ("output_generator_node",), ("write_node",))
    assert asyncio.run(await_checkpoint_past(g, CFG, "output_generator_node",
                                             timeout=5, interval=0.001)) is True
    assert g.reads == 3


def test_a_checkpoint_still_unsaved_after_the_wait_refuses_the_advance():
    g = _Graph(("output_generator_node",))
    assert asyncio.run(await_checkpoint_past(g, CFG, "output_generator_node",
                                             timeout=0.02, interval=0.001)) is False


def test_a_step_already_saved_is_advanced_at_once():
    g = _Graph(("write_node",))
    assert asyncio.run(await_checkpoint_past(g, CFG, "output_generator_node", timeout=5)) is True
    assert g.reads == 1


def test_a_checkpoint_that_cannot_be_read_does_not_block_the_advance():
    # The advance's own ainvoke reads the same checkpoint and reports its own error; refusing
    # here would leave a run that can never be continued.
    g = _Graph(error=RuntimeError("checkpointer down"))
    assert asyncio.run(await_checkpoint_past(g, CFG, "output_generator_node", timeout=5)) is True



def test_a_node_whose_writes_are_saved_but_not_its_checkpoint_is_still_waited_on():
    # LangGraph 0.3 leaves a task out of snapshot.next once its writes are saved
    # (pregel/__init__.py: `if not t.writes`), and those writes are saved apart from — and
    # before — the checkpoint after the node. In that gap next looked past the step; the
    # advance flipped the job to running, the replay applied the saved writes without re-running
    # the node, so no pause row came and the run sat "running" with nothing pending.
    class _Gap(_Graph):
        async def aget_state(self, config):
            self.reads += 1
            return _Snap((), tasks=("output_generator_node",)) if self.reads < 3 else _Snap(("write_node",))

    g = _Gap()
    assert asyncio.run(await_checkpoint_past(g, CFG, "output_generator_node",
                                             timeout=5, interval=0.001)) is True
    assert g.reads == 3
