"""A gate being answered does not reopen while its answer is applied.

On resume LangGraph runs the gate node again from the top, so it wrote its payload again — status
back to awaiting_review, the gate pending — a moment before interrupt() handed it the answer. A
page polling in that moment showed the gate it had just answered (3 Oct 2026: "I press continue
and it loads this again"). The node answered in this resume no longer re-announces; any gate after
it — the same node's second pass included — announces as always.
"""
import asyncio

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from typing_extensions import TypedDict

from src.graph.nodes import db_writer


class S(TypedDict, total=False):
    answers: list


def _graph():
    async def gate(state):
        await db_writer.write_gate_payload("11111111-1111-4111-8111-111111111111", "pre_semantic", {"pass": 1})
        first = interrupt({"pass": 1})
        await db_writer.write_gate_payload("11111111-1111-4111-8111-111111111111", "pre_semantic", {"pass": 2})
        second = interrupt({"pass": 2})
        return {"answers": [first, second]}

    g = StateGraph(S)
    g.add_node("gate", gate)
    g.add_edge(START, "gate")
    g.add_edge("gate", END)
    return g.compile(checkpointer=MemorySaver())


def test_the_gate_being_answered_is_not_announced_again(monkeypatch):
    written = []

    async def record(label, migration_id, statement):
        written.append(label)

    async def no_sync(*a, **k):
        return None
    monkeypatch.setattr(db_writer, "_write_with_retries", record)
    from src.graph.nodes import schema_db_writer
    monkeypatch.setattr(schema_db_writer, "_sync_run_activity", no_sync)

    graph = _graph()
    cfg = {"configurable": {"thread_id": "t"}}

    async def run():
        await graph.ainvoke({}, cfg)                       # pass 1 announced, paused
        assert written == ["write_gate_payload(pre_semantic)"]
        with db_writer.resuming_gate("pre_semantic"):
            await graph.ainvoke(Command(resume="a1"), cfg)  # pass 1 answered: no re-announce; pass 2 announced
        assert written == ["write_gate_payload(pre_semantic)", "write_gate_payload(pre_semantic)"]
        with db_writer.resuming_gate("pre_semantic"):
            out = await graph.ainvoke(Command(resume="a2"), cfg)
        return out

    out = asyncio.run(run())
    assert out["answers"] == ["a1", "a2"]
    assert len(written) == 2                               # neither answered pass announced again


def test_without_a_resume_every_gate_announces(monkeypatch):
    written = []

    async def record(label, migration_id, statement):
        written.append(label)
    monkeypatch.setattr(db_writer, "_write_with_retries", record)
    from src.graph.nodes import schema_db_writer

    async def no_sync(*a, **k):
        return None
    monkeypatch.setattr(schema_db_writer, "_sync_run_activity", no_sync)
    graph = _graph()
    cfg = {"configurable": {"thread_id": "u"}}
    asyncio.run(graph.ainvoke({}, cfg))
    asyncio.run(graph.ainvoke(Command(resume="a1"), cfg))   # resumed outside resume_migration: as before
    assert written == ["write_gate_payload(pre_semantic)"] * 3
