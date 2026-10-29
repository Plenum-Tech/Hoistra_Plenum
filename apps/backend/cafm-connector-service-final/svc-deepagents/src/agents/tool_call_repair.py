"""A result for every tool call a stopped turn left unanswered, in what the model is sent.

A turn stopped part-way — the Stop button, a dropped socket, the service restarting — can leave the
thread with an AIMessage whose tool calls never got a ToolMessage. LangGraph then refuses every
later message on that thread before the model is asked (INVALID_CHAT_HISTORY), so the session can
never answer again (29 Oct 2026). The tools node answers every call of a step before the next model
call, so a call still unanswered when the model is about to be called was stopped.
"""
from __future__ import annotations

from typing import Any, Callable

from langchain_core.messages import AIMessage, ToolMessage

STOPPED_RESULT = ("Stopped before this call returned a result: the turn it belonged to was "
                  "interrupted. Nothing it would have read is known; run it again if it is still needed.")


def answer_stopped_calls(messages: list) -> list:
    """``messages`` with a ToolMessage straight after each unanswered tool call; the same list when
    nothing is unanswered."""
    answered = {m.tool_call_id for m in messages if isinstance(m, ToolMessage)}
    if not any(isinstance(m, AIMessage) and any(tc.get("id") not in answered for tc in (m.tool_calls or []))
               for m in messages):
        return messages
    out: list = []
    for m in messages:
        out.append(m)
        if isinstance(m, AIMessage):
            for tc in m.tool_calls or []:
                if tc.get("id") and tc["id"] not in answered:
                    out.append(ToolMessage(content=STOPPED_RESULT, tool_call_id=tc["id"], name=tc.get("name")))
    return out


def with_stopped_calls_answered(hook: Callable[[dict], dict]) -> Callable[[dict], dict]:
    """A pre_model_hook that sends the model what ``hook`` would, with stopped calls answered."""

    def wrapped(state: dict[str, Any]) -> dict[str, Any]:
        out = hook(state) or {}
        sent = out.get("llm_input_messages") or state.get("messages") or []
        fixed = answer_stopped_calls(list(sent))
        if fixed == list(sent):
            return out
        return {**out, "llm_input_messages": fixed}

    return wrapped
