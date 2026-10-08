"""A Go-engine run does not stop after the steps the engine does in seconds."""
from typing import TypedDict

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from src.engine import selection
from src.engine.graph_proxy import MigrationGraphProxy

NODES = ["ingest_node", "deterministic_mapper_node", "preprocess_node", "hierarchy_node"]


class S(TypedDict, total=False):
    migration_id: str
    engine: str
    source_filename: str
    trail: list


def _graph():
    g = StateGraph(S)
    for n in NODES:
        g.add_node(n, lambda s, n=n: {"trail": [*(s.get("trail") or []), n]})
    g.add_edge(START, NODES[0])
    for a, b in zip(NODES, NODES[1:]):
        g.add_edge(a, b)
    g.add_edge(NODES[-1], END)
    return g.compile(checkpointer=MemorySaver(), interrupt_after=NODES)


@pytest.fixture(autouse=True)
def all_steps_go(monkeypatch):
    monkeypatch.setattr(selection, "ENGINE_STEPS", frozenset({"parse", "preprocess", "outputs", "write"}))
    monkeypatch.setattr(selection, "engine_available", lambda: True)
    monkeypatch.delenv("MIGRATION_ENGINE", raising=False)


async def test_a_go_run_skips_the_engine_step_pauses():
    proxy = MigrationGraphProxy(_graph(), NODES)
    cfg = {"configurable": {"thread_id": "g1"}}
    out = await proxy.ainvoke({"migration_id": "g1", "source_filename": "a.csv"}, cfg)
    assert out["trail"] == ["ingest_node", "deterministic_mapper_node"]  # no stop after ingest
    out = await proxy.ainvoke(None, cfg)  # resume reads the engine from the checkpoint
    assert out["trail"][-1] == "hierarchy_node"  # no stop after preprocess
    assert out["engine"] == "go"


async def test_a_python_run_keeps_every_pause():
    proxy = MigrationGraphProxy(_graph(), NODES)
    cfg = {"configurable": {"thread_id": "p1"}}
    out = await proxy.ainvoke({"migration_id": "p1", "source_filename": "a.xls"}, cfg)
    assert out["trail"] == ["ingest_node"]
    assert out["engine"] == "python"


async def test_an_explicit_interrupt_after_is_left_alone():
    proxy = MigrationGraphProxy(_graph(), NODES)
    cfg = {"configurable": {"thread_id": "e1"}}
    out = await proxy.ainvoke({"migration_id": "e1", "engine": "go"}, cfg, interrupt_after=["ingest_node"])
    assert out["trail"] == ["ingest_node"]


async def test_other_attributes_reach_the_graph():
    proxy = MigrationGraphProxy(_graph(), NODES)
    cfg = {"configurable": {"thread_id": "a1"}}
    await proxy.ainvoke({"migration_id": "a1", "engine": "python"}, cfg)
    snap = await proxy.aget_state(cfg)
    assert snap.values["engine"] == "python"


class _FlakyState:
    """The compiled graph, with its first few checkpoint reads failing (an Azure pool blip)."""

    def __init__(self, graph, failures: int):
        self.graph, self.failures, self.calls = graph, failures, 0

    async def aget_state(self, config):
        self.calls += 1
        if self.calls <= self.failures:
            raise TimeoutError("QueuePool limit reached, connection timed out")
        return await self.graph.aget_state(config)

    async def ainvoke(self, *a, **k):
        return await self.graph.ainvoke(*a, **k)


async def test_a_checkpoint_read_that_fails_once_is_retried(monkeypatch):
    from src.engine import graph_proxy

    monkeypatch.setattr(graph_proxy, "STATE_READ_RETRY_DELAYS_S", (0, 0))
    flaky = _FlakyState(_graph(), failures=1)
    proxy = MigrationGraphProxy(flaky, NODES)
    cfg = {"configurable": {"thread_id": "r1"}}
    await proxy.ainvoke({"migration_id": "r1", "source_filename": "a.csv"}, cfg)
    out = await proxy.ainvoke(None, cfg)
    assert out["trail"][-1] == "hierarchy_node"  # still a Go run: no stop after preprocess


async def test_a_run_whose_engine_cannot_be_read_fails_instead_of_guessing(monkeypatch):
    # Guessing the compiled pauses for a Go run would stop it after a step that writes no step
    # pause: the job stays "running" with nothing to advance it. A checkpoint that cannot be read
    # fails the invocation instead, which ends the run visibly with a retry.
    from src.engine import graph_proxy

    monkeypatch.setattr(graph_proxy, "STATE_READ_RETRY_DELAYS_S", (0, 0))
    flaky = _FlakyState(_graph(), failures=99)
    proxy = MigrationGraphProxy(flaky, NODES)
    cfg = {"configurable": {"thread_id": "r2"}}
    with pytest.raises(TimeoutError):
        await proxy.ainvoke(None, cfg)
    assert flaky.calls == 3
