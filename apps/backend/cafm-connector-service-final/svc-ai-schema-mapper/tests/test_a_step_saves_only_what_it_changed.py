"""A step's checkpoint carries the state keys it changed, not the whole state.

Every node returns the whole state dict, and LangGraph wrote every key of it at every superstep —
30 MB of checkpoint for one workbook before the write (2 Oct 2026), on a 2.8 Mbit/s uplink. A key the
step neither replaced nor changed in place keeps its value in its channel without being written
again, so every later step, every route and every resume reads exactly what it read before.
"""
import asyncio
import copy

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

from src.graph.migration_graph import only_changed


class S(TypedDict, total=False):
    big: dict
    log: list
    count: int
    note: str
    fresh: str


class _Recording(MemorySaver):
    def __init__(self):
        super().__init__()
        self.written: list[set] = []

    async def aput_writes(self, config, writes, task_id, task_path=""):
        self.written.append({k for k, _ in writes if not k.startswith("__") and not k.startswith("branch:")})
        return await super().aput_writes(config, writes, task_id, task_path)


async def step_one(state):
    state["log"].append("one")          # changed in place
    state["count"] = state["count"] + 1  # replaced
    state["fresh"] = "new key"
    return state                          # big and note untouched


async def step_two(state):
    state["note"] = state["note"]        # the same value again: nothing to write
    return state


async def partial(state):
    return {"note": "partial"}           # a node that returns an update, not the state


def _graph(saver, wrap):
    g = StateGraph(S)
    for name, fn in (("one", step_one), ("two", step_two), ("three", partial)):
        g.add_node(name, wrap(fn))
    g.add_edge(START, "one")
    g.add_edge("one", "two")
    g.add_edge("two", "three")
    g.add_edge("three", END)
    return g.compile(checkpointer=saver)


START_STATE = {"big": {f"k{i}": "v" * 50 for i in range(500)}, "log": [], "count": 0, "note": "n"}


def _run(wrap):
    saver = _Recording()
    graph = _graph(saver, wrap)
    cfg = {"configurable": {"thread_id": "t"}}
    final = asyncio.run(graph.ainvoke(copy.deepcopy(START_STATE), cfg))
    history = [s.values for s in asyncio.run(_history(graph, cfg))]
    return final, history, saver.written


async def _history(graph, cfg):
    return [s async for s in graph.aget_state_history(cfg)]


def test_only_the_changed_keys_are_written():
    _, _, written = _run(only_changed)
    steps = [w for w in written if w]
    assert {"log", "count", "fresh"} in steps
    assert all("big" not in w for w in steps[1:]), steps   # the input step writes it once
    assert {"note"} in steps                                 # the partial update passes through


def test_every_checkpoint_reads_what_it_read_before():
    whole = lambda fn: fn  # noqa: E731 — today: the node's whole state is its update
    assert _run(only_changed)[:2] == _run(whole)[:2]
