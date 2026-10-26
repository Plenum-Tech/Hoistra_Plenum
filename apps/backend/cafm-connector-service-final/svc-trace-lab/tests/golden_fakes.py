"""Test helpers for golden runs: a recorded orchestrator and principals (tests only, never imported by
trace_lab). RecordedOrchestrator.stream() plays one scripted message the way svc-deepagents'
orchestrator.stream() does it: it calls the same hooks (activity_log.fire for the turn and tool rows,
trace.on_plan / on_sql / on_model_start / on_model_end, llm_cost.begin_turn and record) so the REAL trace
listeners fill whatever Turn Trace Lab pre-set, and it ends with the REAL workflow_completed payload
(session_workspace.workflow_stream_completion_payload). No model, no network, no database."""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any

ORG = "00000000-0000-0000-0000-0000000000a1"
OTHER_ORG = "00000000-0000-0000-0000-0000000000b2"
USER = "00000000-0000-0000-0000-0000000000c3"
BEARER = "Bearer starter-token"
LOOP_MODEL = "gpt-5.6-terra"


def principal(role: str = "admin", org: str | None = ORG, user: str = USER) -> Any:
    from src.services.principal import Principal

    return Principal(user_id=uuid.UUID(user), email=f"{role}@example.com",
                     organization_id=uuid.UUID(org) if org else None, role=role, can_ingest=True, building_ids=None)


@dataclass
class Msg:
    """One scripted answer. kind: "answer" (default), "error", "gate", "raise" or "hang"."""
    answer: str = "There were 759 work orders in August 2026."
    kind: str = "answer"
    tools: list[tuple[str, Any]] = field(default_factory=lambda: [("udr_query_table", {"rows": [{"n": 759}]})])
    cards: list[dict] = field(default_factory=list)
    error: str = "boom"
    refuse: bool = False                       # note a refusal the way the proxy does
    upstream_status: int | None = None         # note an upstream failure the way the proxy does
    router_usd_tokens: int = 1000              # a call on the CALLER's ledger, before stream opens its own


class RecordedOrchestrator:
    def __init__(self, script: list[Msg]) -> None:
        self.script = script                       # the caller's list: a test may add to it later
        self.seen: list[dict[str, Any]] = []

    async def stream(self, user_message: str, session_id: str | None = None, extra_context: str | None = None):
        from langchain_core.messages import AIMessage

        from src.agents import activity_log, llm_cost, replay_guard, trace
        from src.agents.session_workspace import workflow_stream_completion_payload
        from src.http_client import caller_authorization, caller_organization_id
        from src.services.principal import caller_principal
        from trace_lab import proxy

        sid = session_id or str(uuid.uuid4())
        msg = self.script.pop(0)
        activity_log.set_current_session(sid, sid)            # stream() does this first (orchestrator.py)
        activity_log.ensure_turn()
        self.seen.append({"message": user_message, "session": sid, "context": extra_context,
                          "turn": activity_log.current_turn(), "replay": replay_guard.is_replay(),
                          "principal": caller_principal.get(), "auth": caller_authorization.get(),
                          "org": caller_organization_id.get(), "trace_turn": trace.current()})
        activity_log.fire(agent="orchestrator", stage="turn", direction="input", summary=user_message[:300],
                          payload={"message": user_message, "extra_context": extra_context, "mode": "stream"})
        llm_cost.record("router", "claude-sonnet-5", {"input_tokens": msg.router_usd_tokens, "output_tokens": 0}, 5)
        if msg.kind == "raise":
            raise RuntimeError(msg.error)
        if msg.kind == "hang":
            await asyncio.sleep(3600)
        llm_cost.begin_turn(sid)                               # stream() opens its own ledger (orchestrator.py)
        trace.on_plan({"mode": "single", "steps": [{"target": "udr", "why": "a count"}]}, source="router")
        if msg.refuse:
            proxy.refusals.note({"service": "udr", "method": "PATCH", "path": "/api/tables/work_orders/records/1",
                                 "reason": "not on Trace Lab's list of read-only requests",
                                 "label": proxy.label_for("udr")})
        if msg.upstream_status:
            proxy.refusals.note_upstream({"service": "udr", "method": "GET", "path": "/api/tables",
                                          "status": msg.upstream_status, "error": None},
                                         authorization=caller_authorization.get())
        calls = []
        for name, out in msg.tools:
            activity_log.fire(agent="tool:udr", stage="tool", direction="input", summary=name,
                              payload={"tool": name, "input": {"q": user_message[:40]}})
            yield {"type": "tool_started", "tool": name, "domain": "udr", "input": {"q": user_message[:40]}}
            trace.on_sql("SELECT count(*) FROM work_orders", {}, [{"n": 759}], 3.0, label="count work orders")
            activity_log.fire(agent="tool:udr", stage="tool", direction="output", summary=name,
                              payload={"tool": name, "output": out})
            calls.append({"tool": name, "input": {}, "output": out})
            yield {"type": "tool_completed", "tool": name, "domain": "udr", "output": out}
        trace.on_model_start("run-loop-1", LOOP_MODEL, 1, {"messages": 3})
        trace.on_model_end("run-loop-1", AIMessage(content=msg.answer, response_metadata={"model_name": LOOP_MODEL},
                                                   usage_metadata={"input_tokens": 2000, "output_tokens": 100,
                                                                   "total_tokens": 2100}))
        llm_cost.record("evaluator", "claude-sonnet-5", {"input_tokens": 500, "output_tokens": 50}, 4)
        if msg.kind == "error":
            activity_log.fire(agent="orchestrator", stage="turn", direction="error", summary=msg.error, ok=False,
                              error=msg.error, payload={"tool_calls": calls})
            yield {"type": "error", "error": msg.error, "session_id": sid}
            return
        if msg.kind == "gate":
            activity_log.fire(agent="orchestrator", stage="turn", direction="output", summary="gate_interrupt",
                              payload={"gate_interrupt": {"gate": "map_fields"}, "tool_calls": calls})
            yield {"type": "gate_interrupt", "payload": {"gate": "map_fields"}, "session_id": sid}
            return
        activity_log.fire(agent="orchestrator", stage="turn", direction="output", summary=msg.answer[:300],
                          payload={"answer": msg.answer, "tool_calls": calls, "mode": "stream"}, latency_ms=25)
        cards = [{"tool": "compliance_response", "input": {}, "output": c} for c in msg.cards]
        yield workflow_stream_completion_payload(sid, answer=msg.answer, tool_calls=calls + cards)
