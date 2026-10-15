"""The agent keeps its own working context under budget, and nothing it needs is lost by accident.

Measured 5 Oct 2026 in Hoist Traces: the orchestrator loop and the compliance agent grew 21-24k
tokens inside one question, up to 75k, because every tool result rode along with every later
model call. The fix lets the model release results it has finished, keeping short notes; the
hook only ever applies a release that shrinks the context, and cuts mechanically only as a last
resort.
"""
from __future__ import annotations

from typing import Any

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent

from src.agents import context_budget as cb


@pytest.fixture
def mode():
    """Set the context mode for one test and put it back after."""
    tokens = []

    def set_(m):
        tokens.append(cb.MODE.set(m))
    yield set_
    for t in reversed(tokens):
        cb.MODE.reset(t)

BIG = "row " * 3000          # ~3k tokens of register rows
NOTES = "r1 read_register: 2 lapsed - FRA Bishopsgate (2026-06-29), EICR B-301 (2026-08-14)."


def _turn(*results: str, names=("read_register",)) -> list:
    msgs: list[Any] = [HumanMessage(content="which fire certificates have lapsed?", id="h1")]
    for i, r in enumerate(results, start=1):
        name = names[(i - 1) % len(names)]
        msgs.append(AIMessage(content="", id=f"a{i}", tool_calls=[{"id": f"c{i}", "name": name, "args": {}}]))
        msgs.append(ToolMessage(content=r, tool_call_id=f"c{i}", name=name, id=f"t{i}"))
    return msgs


def _compact(msgs: list, release: list[str], notes: str) -> list:
    n = len([m for m in msgs if isinstance(m, ToolMessage)]) + 1
    return msgs + [
        AIMessage(content="", id=f"ac{n}", tool_calls=[{"id": f"cc{n}", "name": cb.COMPACT_TOOL,
                                                        "args": {"notes": notes, "release": release}}]),
        ToolMessage(content=cb.PENDING, tool_call_id=f"cc{n}", name=cb.COMPACT_TOOL, id=f"tc{n}"),
    ]


# ── labels and the gate ──────────────────────────────────────────────────────────────────

def test_results_are_labelled_in_arrival_order_and_labels_never_move():
    msgs = _turn(BIG, "small", names=("read_register", "list_work_orders"))
    labels = cb.label_results(msgs)
    assert [(r["label"], r["name"]) for r in labels] == [("r1", "read_register"), ("r2", "list_work_orders")]
    assert labels[0]["tokens"] > 2000


def test_a_release_with_shorter_notes_replaces_the_result_and_keeps_the_notes():
    msgs = _compact(_turn(BIG), ["r1"], NOTES)
    before = cb.working_tokens(msgs)
    out, events = cb.apply_compactions(msgs)
    assert events[0]["accepted"] and events[0]["released"] == ["r1"]
    assert out[2].content.startswith(cb.RELEASED) and out[2].id == "t1"   # replaced in place
    assert NOTES in out[-1].content
    assert cb.working_tokens(out) < before - 2000


def test_notes_as_long_as_the_result_are_refused_and_nothing_is_released():
    msgs = _compact(_turn("short result"), ["r1"], "a much longer note " * 20)
    out, events = cb.apply_compactions(msgs)
    assert not events[0]["accepted"] and "not shorter" in events[0]["reason"]
    assert out[2].content == "short result"
    assert out[-1].content.startswith("Not applied")


def test_unknown_or_already_released_labels_are_refused():
    out, events = cb.apply_compactions(_compact(_turn(BIG), ["r9"], NOTES))
    assert not events[0]["accepted"]
    once, _ = cb.apply_compactions(_compact(_turn(BIG), ["r1"], NOTES))
    again, events = cb.apply_compactions(_compact(once, ["r1"], NOTES))
    assert not events[0]["accepted"]


def test_a_compaction_is_applied_once():
    out, _ = cb.apply_compactions(_compact(_turn(BIG), ["r1"], NOTES))
    same, events = cb.apply_compactions(out)
    assert events == [] and same is out


# ── overflow and notice ──────────────────────────────────────────────────────────────────

def test_overflow_cuts_the_oldest_large_result_first_and_says_so():
    msgs = _turn(BIG, BIG)
    out, shrunk = cb.shrink_overflow(msgs, limit=4000)
    assert shrunk >= 1 and out[2].content.startswith("[cut to fit")
    assert cb.working_tokens(out) < cb.working_tokens(msgs)


def test_no_notice_below_half_the_budget():
    assert cb.notice(_turn("small"), budget=24000) is None


def test_past_half_the_notice_lists_what_can_be_released_with_the_instructions():
    text = cb.notice(_turn(BIG), budget=5000)
    assert "Results you can release: r1 read_register" in text
    assert "compact_context" in text      # the skill instructions ride with it
    assert "Compact now" in cb.notice(_turn(BIG, BIG), budget=6000)


# ── the hook ─────────────────────────────────────────────────────────────────────────────

def test_the_hook_stores_the_compaction_and_only_shows_the_notice(mode):
    mode("self")
    hook = cb.make_hook(agent="test", trim_max_tokens=None, budget=5000)
    out = hook({"messages": _compact(_turn(BIG, BIG), ["r1"], NOTES)})
    stored = out["messages"][1:]            # after RemoveMessage(REMOVE_ALL)
    assert stored[2].content.startswith(cb.RELEASED)
    assert "[Context:" not in stored[-1].content          # the notice is never stored
    assert "[Context:" in out["llm_input_messages"][-1].content


def test_with_self_management_off_the_hook_is_the_old_trim(mode):
    mode("trim")
    hook = cb.make_hook(agent="test", trim_max_tokens=None, budget=5000)
    assert hook({"messages": _compact(_turn(BIG), ["r1"], NOTES)}) == {}


def test_stats_count_what_happened_in_the_turn(mode):
    mode("self")
    stats = cb.new_stats()
    token = cb.STATS.set(stats)
    try:
        cb.make_hook(agent="test", trim_max_tokens=None, budget=5000)(
            {"messages": _compact(_turn(BIG, BIG), ["r1"], NOTES)})
    finally:
        cb.STATS.reset(token)
    assert stats["compactions"] == 1 and stats["tokens_freed"] > 2000 and stats["notices"] == 1


# ── end to end, through a real LangGraph agent ───────────────────────────────────────────

class _Scripted(BaseChatModel):
    """Plays a fixed list of replies and records what it was sent each step."""

    replies: list = []
    seen: list = []

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        self.seen.append(list(messages))
        return ChatResult(generations=[ChatGeneration(message=self.replies.pop(0))])


@tool
def read_register() -> str:
    """Read the whole certificate register."""
    return BIG


def test_through_a_real_agent_a_compaction_shrinks_what_every_later_step_is_sent(mode):
    mode("self")
    model = _Scripted(replies=[
        AIMessage(content="", tool_calls=[{"id": "x1", "name": "read_register", "args": {}}]),
        AIMessage(content="", tool_calls=[{"id": "x2", "name": cb.COMPACT_TOOL,
                                           "args": {"notes": NOTES, "release": ["r1"]}}]),
        AIMessage(content="Two fire certificates have lapsed."),
    ], seen=[])
    agent = create_react_agent(model, tools=[read_register, cb.compact_context],
                               pre_model_hook=cb.make_hook(agent="test", trim_max_tokens=None, budget=6000))
    out = agent.invoke({"messages": [HumanMessage(content="which fire certificates have lapsed?")]})

    sent = [cb.working_tokens(s) for s in model.seen]
    assert "[Context:" in model.seen[1][-1].content          # nudged after reading the register
    assert sent[2] < sent[1] - 2000                           # the last step is sent far less
    stored = [m for m in out["messages"] if isinstance(m, ToolMessage)]
    assert stored[0].content.startswith(cb.RELEASED)          # and the history itself shrank
    assert NOTES in stored[1].content
    assert out["messages"][-1].content == "Two fire certificates have lapsed."


def test_the_planner_never_plans_a_compaction():
    from src.agents.planner import catalogue_line
    assert catalogue_line(cb.COMPACT_TOOL, "Free your working context") is None
