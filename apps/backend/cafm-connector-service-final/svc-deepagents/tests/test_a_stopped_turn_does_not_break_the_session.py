"""A turn stopped part-way never breaks the conversation it was in (29 Oct 2026).

"HOW MANY FLOORS IN JASMEET BUILDING" was stopped while its udr task waited on a cold ontology
build. The thread kept the orchestrator's AIMessage calling `task` with no ToolMessage after it,
and every later message in that session failed before the model was asked: "Found AIMessages
with tool_calls that do not have a corresponding ToolMessage" (LangGraph INVALID_CHAT_HISTORY).
The session could never answer again. Before each model call the view now carries a result for
every call left unanswered, placed straight after the call — the stored history is not rewritten.
"""
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.prebuilt.chat_agent_executor import _validate_chat_history

from src.agents.tool_call_repair import answer_stopped_calls, with_stopped_calls_answered

STOPPED = AIMessage(content="", tool_calls=[{"name": "task", "args": {"agent": "udr"}, "id": "call_1"}])


def _history():
    return [SystemMessage(content="sys"), HumanMessage(content="HOW MANY FLOORS IN JASMEET BUILDING"),
            STOPPED, HumanMessage(content="HOW MANY FLOORS IN JASMEET BUILDING")]


def test_an_unanswered_call_gets_a_result_straight_after_it():
    out = answer_stopped_calls(_history())
    assert [type(m).__name__ for m in out] == ["SystemMessage", "HumanMessage", "AIMessage", "ToolMessage", "HumanMessage"]
    assert out[3].tool_call_id == "call_1" and "stopped" in out[3].content.lower()
    _validate_chat_history(out)        # the check that refused every later message


def test_a_history_with_every_call_answered_is_left_alone():
    msgs = [HumanMessage(content="q"), STOPPED, ToolMessage(content="ok", tool_call_id="call_1"), AIMessage(content="a")]
    assert answer_stopped_calls(msgs) is msgs


def test_the_hook_repairs_what_the_inner_hook_sends():
    def inner(state):
        return {"llm_input_messages": state["messages"][1:]}

    out = with_stopped_calls_answered(inner)({"messages": _history()})
    sent = out["llm_input_messages"]
    assert sent[0].content == "HOW MANY FLOORS IN JASMEET BUILDING" and isinstance(sent[2], ToolMessage)
    _validate_chat_history(sent)


def test_the_hook_repairs_the_state_when_the_inner_hook_sends_nothing():
    out = with_stopped_calls_answered(lambda state: {})({"messages": _history()})
    _validate_chat_history(out["llm_input_messages"])
    assert "messages" not in out       # the stored history is not rewritten


# ── the real agent: a thread left with a stopped call answers the next message ─────────────────

import asyncio

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver
from langgraph.prebuilt import create_react_agent


class _Model(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kw):
        return self


@tool
def task(agent: str) -> str:
    """Run a sub-agent."""
    return "89 floors"


def _agent(hook):
    return create_react_agent(model=_Model(responses=[AIMessage(content="JASMEET has 89 floors.")]),
                              tools=[task], checkpointer=MemorySaver(), pre_model_hook=hook)


def _stopped_thread(agent, cfg):
    asyncio.run(agent.aupdate_state(cfg, {"messages": [HumanMessage(content="HOW MANY FLOORS IN JASMEET"), STOPPED]},
                                    as_node="agent"))


def test_without_the_repair_the_next_message_is_refused():
    agent, cfg = _agent(lambda state: {}), {"configurable": {"thread_id": "t1"}}
    _stopped_thread(agent, cfg)
    with pytest.raises(ValueError, match="corresponding ToolMessage"):
        asyncio.run(agent.ainvoke({"messages": [HumanMessage(content="HOW MANY FLOORS IN JASMEET")]}, cfg))


def test_with_the_repair_the_next_message_is_answered():
    agent, cfg = _agent(with_stopped_calls_answered(lambda state: {})), {"configurable": {"thread_id": "t2"}}
    _stopped_thread(agent, cfg)
    out = asyncio.run(agent.ainvoke({"messages": [HumanMessage(content="HOW MANY FLOORS IN JASMEET")]}, cfg))
    assert out["messages"][-1].content == "JASMEET has 89 floors."
