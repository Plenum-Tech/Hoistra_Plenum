"""The chat's turn with its finished answer laid out (orchestrator.stream, rerun_turn).

The answer goes out the moment the turn has recorded it (workflow_completed), unchanged. The
layout follows as its own `answer_view` event, so a slow, failed or abandoned layout never holds
back or loses the answer (27 Oct 2026: the answer waited up to 40 s for its layout, and a Stop
pressed in that wait discarded it). The layout's model call counts in what the turn cost.
"""
from __future__ import annotations

import asyncio
import json

from test_answer_presenter import ANSWER, GOOD, QUESTION

from src.agents import llm_cost
from src.agents.orchestrator import DeepAgentOrchestrator
from src.services import chat_threads


def _orchestrator(events, layout):
    o = object.__new__(DeepAgentOrchestrator)

    async def turn(user_message, session_id=None, extra_context=None):
        for e in events:
            yield e
    o._stream_turn = turn

    async def llm(system, user, role):
        return layout
    o._planner_llm = llm
    return o


def _drive(o, stored, costs=None):
    async def record_view(sid, view, cost=None, *, answer=None):
        assert answer == ANSWER  # stored on the turn this answer belongs to, never just the newest
        stored.append((sid, view, cost))

    async def go():
        real = chat_threads.record_view
        chat_threads.record_view = record_view
        try:
            if costs is not None:
                llm_cost.begin_turn("s-1")
            return [e async for e in o.stream(QUESTION, "s-1")]
        finally:
            chat_threads.record_view = real
    return asyncio.run(go())


def _clean(monkeypatch):
    monkeypatch.delenv("OPENUI_ANSWERS", raising=False)
    monkeypatch.delenv("OPENUI_PRESENTER", raising=False)


def test_the_answer_goes_out_first_and_unchanged_then_its_layout(monkeypatch):
    _clean(monkeypatch)
    done = {"type": "workflow_completed", "session_id": "s-1", "answer": ANSWER,
            "tool_calls": [{"tool": "phase2_engine:contract_performance", "input": {}, "output": "…"}]}
    stored: list = []
    out = _drive(_orchestrator([{"type": "tool_started", "tool": "x"}, done], GOOD), stored)
    assert [e["type"] for e in out] == ["tool_started", "workflow_completed", "reasoning", "answer_view"]
    assert out[1] == done  # the completed answer is exactly what the turn sent
    assert out[2]["label"] == "Layout" and out[2]["session_id"] == "s-1"
    assert out[3] == {"type": "answer_view", "session_id": "s-1", "view": {"v": 1, "lang": GOOD}, "cost": None}
    # stored on the turn, which recorded its answer before workflow_completed went out
    assert stored == [("s-1", {"v": 1, "lang": GOOD}, None)]


def test_a_layout_that_is_not_sent_says_so_and_changes_nothing(monkeypatch):
    _clean(monkeypatch)
    done = {"type": "workflow_completed", "session_id": "s-1", "answer": ANSWER, "tool_calls": []}
    stored: list = []
    out = _drive(_orchestrator([done], GOOD.replace("86.12", "86")), stored)
    assert out[0] == done
    assert out[-1] == {"type": "answer_view", "session_id": "s-1", "view": None, "cost": None}
    assert stored == []


def test_a_turn_with_its_own_layout_is_not_laid_out_again(monkeypatch):
    _clean(monkeypatch)
    own = {"tool": "openui_view", "input": {}, "output": {"v": 1, "lang": GOOD}}
    done = {"type": "workflow_completed", "session_id": "s-1", "answer": ANSWER, "tool_calls": [own]}
    stored: list = []
    out = _drive(_orchestrator([done], "never asked"), stored)
    assert out == [done] and stored == []


def test_a_slow_layout_never_holds_the_answer_back(monkeypatch):
    _clean(monkeypatch)
    from src.agents import answer_presenter as ap
    monkeypatch.setattr(ap, "TIMEOUT_S", 0.05)
    done = {"type": "workflow_completed", "session_id": "s-1", "answer": ANSWER, "tool_calls": []}
    o = _orchestrator([done], GOOD)

    async def slow(system, user, role):
        await asyncio.sleep(5)
        return GOOD
    o._planner_llm = slow
    seen: list = []

    async def go():
        async for e in o.stream(QUESTION, "s-1"):
            seen.append(e["type"])
            if e["type"] == "workflow_completed":
                return  # the reader has the answer; the stream may be dropped here
    asyncio.run(go())
    assert seen == ["workflow_completed"]


def test_the_cost_counts_the_layout_call_sent_or_not(monkeypatch):
    # The pipeline panel's cost is written before the answer is laid out; the layout's model call
    # is part of what the turn cost, live and in the stored thread (27 Oct 2026: "2 model calls,
    # $0.149" beside a turn that made a third, $0.0155 one).
    _clean(monkeypatch)
    usage = {"input_tokens": 3528, "output_tokens": 702, "cache_read": 0, "cache_write": 0}

    async def turn(user_message, session_id=None, extra_context=None):
        llm_cost.record("sub_agent", "gpt-5.6-terra", usage, 900)
        panel = {"tool": "compliance_pipeline", "input": {}, "output": {"steps": [], "cost": llm_cost.current().summary()}}
        yield {"type": "workflow_completed", "session_id": "s-1", "answer": ANSWER, "tool_calls": [panel]}

    async def llm(system, user, role):
        llm_cost.record(role, "gpt-5.6-terra", usage, 500)
        return GOOD
    o = object.__new__(DeepAgentOrchestrator)
    o._stream_turn, o._planner_llm = turn, llm
    stored: list = []
    out = _drive(o, stored, costs=[])
    assert out[0]["tool_calls"][0]["output"]["cost"]["calls"] == 1  # as the turn wrote it
    cost = out[-1]["cost"]
    assert cost["calls"] == 2 and set(cost["by_role"]) == {"sub_agent", "layout"}
    assert stored == [("s-1", {"v": 1, "lang": GOOD}, cost)]

    async def rejected(system, user, role):
        llm_cost.record(role, "gpt-5.6-terra", {"input_tokens": 10, "output_tokens": 10}, 100)
        return "not a program"
    o._planner_llm = rejected
    stored.clear()
    out = _drive(o, stored, costs=[])
    assert out[-1]["view"] is None and out[-1]["cost"]["calls"] == 2
    assert stored == [("s-1", None, out[-1]["cost"])]


def test_an_abandoned_layout_call_marks_the_cost_unknown(monkeypatch):
    _clean(monkeypatch)
    from src.agents import answer_presenter as ap
    monkeypatch.setattr(ap, "TIMEOUT_S", 0.05)
    o = object.__new__(DeepAgentOrchestrator)

    class Slow:
        async def ainvoke(self, messages):
            await asyncio.sleep(5)
    o._llm = Slow()

    async def go():
        ledger = llm_cost.begin_turn("s-1")
        view = await ap.present(QUESTION, ANSWER, o._planner_llm)
        return view, ledger.summary()
    view, cost = asyncio.run(go())
    assert view is None and cost["calls"] == 1 and cost["usd_complete"] is False


def test_the_stored_view_is_kept_as_the_reopened_chat_reads_it():
    entry = json.loads(json.dumps([{"tool": chat_threads.OPENUI_TOOL, "output": {"v": 1, "lang": GOOD}}]))
    assert chat_threads._card_payloads(entry) == entry
