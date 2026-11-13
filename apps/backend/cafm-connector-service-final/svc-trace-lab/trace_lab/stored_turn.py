"""A turn Trace Lab holds only in its eval store, as a record notes can be written on (14 Nov 2026).

A session asked on another machine and copied here (sync_push) has its turns in tracelab.sessions / traces /
observations and not in this environment's chat (plenum_cafm.agent_trace_turns), so conversations.get_turn answers
None for it. Notes need two things of a turn: the step keys that say where a note sits, and a check that a key names a
step of that turn. For a chat-recorded turn the chat's normalized record gives both. For a stored-only turn this
module builds the same record, with the same message ids (`<turn>:<span>:<part>`, as normalize.py makes them), from
the stored observations, so a note keeps its key whichever way the turn is read.

Nothing is written here and no chat row is made up: the record carries `stored: True`, and the evidence copy
(evidence.snapshot) is skipped for it, since that copies the chat's rows and there are none. The note's conversation
key is the stored session's id. Reads Trace Lab's own database only, as the company the caller is acting for.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import text

from . import conversations, eval_store

#: observation kind -> (message role, message part), as normalize.normalize_turn names them
_PARTS = {"llm": ("model_call", "call"), "router": ("model_call", "call"), "tool": ("tool_call", "call"),
          "db": ("db_query", "query"), "plan": ("plan", "plan"), "step": ("step", "step")}


def record_from(trace_id: str, question: str | None, answer: str | None,
                nodes: list[dict[str, Any]]) -> dict[str, Any]:
    """The record of a stored trace: its question, one message per step and an entry per agent. Pure."""
    root = next((n for n in nodes if n.get("parent_id") is None), None)
    rid = str(root["id"]) if root else None

    def msg(span: str | None, role: str, part: str, **more: Any) -> dict[str, Any]:
        return {"id": f"{trace_id}:{span or 'turn'}:{part}", "role": role, "turn_id": trace_id, "span_id": span, **more}

    messages = [msg(rid, "user", "user", text=question or "")]
    groups: list[dict[str, Any]] = []
    for n in nodes:
        sid = str(n["id"])
        if n is root:
            continue
        if n.get("type") == "agent" or n.get("kind") == "agent":
            groups.append({"span_id": sid, "name": n.get("name"), "parent_id": n.get("parent_id")})
            continue
        if n.get("kind") == "tool":
            messages.append(msg(sid, "tool_call", "call", name=n.get("name")))
            messages.append(msg(sid, "tool_result", "result", name=n.get("name")))
            continue
        role, part = _PARTS.get(str(n.get("kind") or ""), ("system_note", "note"))
        messages.append(msg(sid, role, part, name=n.get("name")))
    messages.append(msg(rid, "assistant", "answer", text=answer or ""))
    return {"id": trace_id, "trace_id": trace_id, "stored": True, "messages": messages, "groups": groups}


async def get(org: str | None, trace_id: str) -> dict[str, Any] | None:
    """{"trace": record, "conversation_key": the stored session's id, "stored": True} for a trace of company `org`
    that Trace Lab holds, else None (also for another company's trace: the company is part of the lookup)."""
    if not org or not isinstance(trace_id, str) or not trace_id or len(trace_id) > 200:
        return None
    async with conversations._lab_read() as conn:
        t = (await conn.execute(text(
            "SELECT t.session_id, t.input, t.output FROM tracelab.traces t"
            " WHERE t.id = :id AND t.organization_id = CAST(:org AS uuid)"), {"id": trace_id, "org": org})).mappings().first()
        if t is None:
            return None
        nodes = await eval_store.nodes_of(conn, trace_id)
    key = str(t["session_id"] or trace_id)[:400] or trace_id
    return {"trace": record_from(trace_id, t["input"], t["output"], nodes), "conversation_key": key, "stored": True}
