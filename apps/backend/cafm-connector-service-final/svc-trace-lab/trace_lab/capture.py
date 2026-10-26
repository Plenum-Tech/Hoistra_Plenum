"""One golden message answered by the real chat, captured in memory (G0 addendum 2.5).

run_message drives svc-deepagents' orchestrator.stream() - the chat UI's own path - until it ends
(workflow_completed, gate_interrupt or error), exactly as the WebSocket route consumes it, without its
usage receipt. Before it starts, in a task of its own (so every ContextVar stream() sets dies with it):

* the caller ContextVars skill_lab.replay sets - caller_principal, caller_organization_id and
  caller_authorization - set the way the chat's WebSocket route sets them (the acting company only for a
  superadmin); replay_guard.REPLAY is never set;
* context_budget.MODE None (live behaviour), STATS fresh (its counters are evidence),
  skill_overlays.CANDIDATE None;
* a fresh activity turn id, and src.agents.trace's current Turn pre-set to an in-memory trace.Turn, so
  the normal hooks (activity_log.fire -> trace.on_activity, the stream's model events, the cost ledger)
  fill the span tree. Nothing is written: the trace store is not ready in this process;
* a fresh llm_cost ledger; stream() may open its own, and both are counted;
* proxy.refusals.begin/end around it, so every request the proxy refused is returned with the answer.

The result is a CapturedTurn: the answer, the turn and span rows shaped like agent_trace_turns and
agent_trace_spans (so normalize.normalize_turn reads them unchanged), the ledger, cost, latency, the
refusals and the answer's cards. The runner stores it in Trace Lab's own database.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import structlog

from . import proxy

log = structlog.get_logger(__name__)

MESSAGE_TIMEOUT_S = float(os.getenv("TRACE_LAB_GOLDEN_MESSAGE_TIMEOUT_S") or 240)
CARDS_MAX_BYTES = 64_000
ANSWER_MAX = 20_000
TURN_PREFIX = "turn-g"
CARD_TOOL = "compliance_response"
TERMINAL_EVENTS = ("workflow_completed", "gate_interrupt", "error")


@dataclass
class CapturedTurn:
    answer: str
    error: str | None
    turn_id: str
    spans: list[dict[str, Any]]
    ledger: list[dict[str, Any]]
    usd: float
    ms: int
    refused: list[dict[str, Any]]
    cards: list[dict[str, Any]]
    question: str = ""
    session_id: str = ""
    turn: dict[str, Any] = field(default_factory=dict)
    ended_by: str = "none"            # workflow_completed | gate_interrupt | error | timeout | exception | none
    upstream_failures: list[dict[str, Any]] = field(default_factory=list)
    tool_errors: list[dict[str, Any]] = field(default_factory=list)
    tool_names: list[str] = field(default_factory=list)
    cards_cut: bool = False
    choices: list[Any] | None = None
    history: list[dict[str, str]] = field(default_factory=list)


def new_turn_id() -> str:
    """A golden turn id: like the chat's own ("turn-" + 16 hex), marked g, never in plenum_cafm."""
    return TURN_PREFIX + uuid.uuid4().hex[:16]


def acting_org_for(principal: Any, organization_id: str | None) -> str | None:
    """caller_organization_id as the chat's WebSocket route sets it (routes/workflow.py _resolve_acting_org):
    the named company for a superadmin, None for anyone else (their own company comes from their token)."""
    if organization_id and getattr(principal, "role", None) == "superadmin":
        return str(organization_id)
    return None


def cards_of(tool_calls: list[Any]) -> tuple[list[dict[str, Any]], bool]:
    """The compliance_response payloads shown with the answer, whole, up to 64 KB of JSON; True when one
    did not fit."""
    cards: list[dict[str, Any]] = []
    size, cut = 0, False
    for tc in tool_calls or []:
        if isinstance(tc, dict) and tc.get("tool") == CARD_TOOL and isinstance(tc.get("output"), dict):
            n = len(json.dumps(tc["output"], default=str))
            if size + n > CARDS_MAX_BYTES:
                cut = True
                continue
            cards.append(tc["output"])
            size += n
    return cards, cut


def _tool_error(tool: Any, output: Any) -> dict[str, Any] | None:
    """A tool output shaped like an error ({"error": ..., "status_code": n}), parsed from JSON text too."""
    out = output
    if isinstance(out, str) and out.strip().startswith("{"):
        try:
            out = json.loads(out)
        except ValueError:
            return None
    if not isinstance(out, dict) or not (out.get("error") or out.get("status_code")):
        return None
    try:
        code = int(out["status_code"]) if out.get("status_code") is not None else None
    except (TypeError, ValueError):
        code = None
    return {"tool": str(tool or "")[:120], "status_code": code, "error": str(out.get("error") or "")[:300]}


def _iso(v: Any) -> str | None:
    if isinstance(v, datetime):
        return (v if v.tzinfo else v.replace(tzinfo=timezone.utc)).isoformat()
    return None if v is None else str(v)


def _jsonable(v: Any) -> Any:
    return json.loads(json.dumps(v, default=str))


def turn_row(t: Any, *, answer: str, error: str | None, ledger_summary: dict[str, Any] | None) -> dict[str, Any]:
    """The turn as svc-deepagents' trace._write would have stored it in agent_trace_turns (totals by its
    own trace.totals), plus created_at."""
    from src.agents import trace

    tot = trace.totals(t, ledger_summary)
    root = t.root
    return _jsonable({
        "turn_id": t.turn_id, "session_id": t.session_id, "organization_id": t.organization_id,
        "user_id": t.user_id, "email": t.email, "question": t.question, "answer": (answer or "")[:ANSWER_MAX],
        "route": t.route, "started_at": _iso(root.started_at), "ended_at": _iso(root.ended_at),
        "created_at": _iso(datetime.now(timezone.utc)), "latency_ms": root.latency_ms,
        "llm_calls": tot["llm_calls"], "tool_calls": tot["tool_calls"], "cost_usd": tot["cost_usd"],
        "cost_complete": tot["cost_complete"], "input_tokens": tot["input_tokens"],
        "output_tokens": tot["output_tokens"], "cache_read_tokens": tot["cache_read_tokens"],
        "models": tot["models"], "tools": tot["tools"], "ok": bool(root.ok) and not error,
        "error": root.error or error,
    })


def span_rows(t: Any) -> list[dict[str, Any]]:
    """The span tree as trace._write would have stored it in agent_trace_spans, in sequence order."""
    return [_jsonable({
        "id": s.id, "turn_id": t.turn_id, "session_id": t.session_id, "organization_id": t.organization_id,
        "parent_id": s.parent_id, "seq": s.seq, "kind": s.kind, "name": (s.name or "")[:200], "agent": s.agent,
        "model": s.model, "started_at": _iso(s.started_at), "ended_at": _iso(s.ended_at),
        "latency_ms": s.latency_ms, "input": s.input, "output": s.output, "input_tokens": s.input_tokens,
        "output_tokens": s.output_tokens, "cache_read_tokens": s.cache_read, "cost_usd": s.cost_usd,
        "ok": s.ok, "error": s.error,
    }) for s in sorted(t.spans, key=lambda x: x.seq)]


def _close(t: Any, *, answer: str, error: str | None) -> None:
    """Close a Turn that stream() never closed (it raised, or ran out of time) the way trace._flush does,
    without its write."""
    if t.flushed:
        return
    t.root.close(output={"answer": (answer or "")[:16000]}, ok=error is None, error=error)
    for s in t.spans:
        if s.open:
            s.close()
    t.flushed = True
    t.spans.sort(key=lambda x: x.seq)


async def run_message(orchestrator: Any, *, question: str, session_id: str, page_context: str | None,
                      principal: Any, organization_id: str | None, authorization: str | None) -> CapturedTurn:
    """Answer one golden message for real and capture it. Never raises for a chat failure: errors,
    timeouts and exceptions come back in the CapturedTurn."""
    return await asyncio.create_task(_run(orchestrator, question=question, session_id=session_id,
                                          page_context=page_context, principal=principal,
                                          organization_id=organization_id, authorization=authorization))


async def _run(orchestrator: Any, *, question: str, session_id: str, page_context: str | None, principal: Any,
               organization_id: str | None, authorization: str | None) -> CapturedTurn:
    from src.agents import activity_log, context_budget, llm_cost, trace
    from src.http_client import caller_authorization, caller_organization_id
    from src.services import skill_overlays
    from src.services.principal import caller_principal

    caller_principal.set(principal)
    caller_organization_id.set(acting_org_for(principal, organization_id))
    caller_authorization.set(authorization)
    context_budget.MODE.set(None)
    context_budget.STATS.set(context_budget.new_stats())
    skill_overlays.CANDIDATE.set(None)
    activity_log.set_current_session(session_id, session_id)
    turn_id = activity_log.start_turn(new_turn_id())
    t = trace.Turn(turn_id, session_id)             # after the caller vars: it reads them
    t.question = (question or "")[:4000]
    trace._turn.set(t)
    first = llm_cost.begin_turn(session_id)
    final: dict[str, Any] | None = None
    tool_names: list[str] = []
    tool_errors: list[dict[str, Any]] = []
    error: str | None = None
    ended_by = "none"
    t0 = time.perf_counter()
    proxy.refusals.begin(f"{session_id}:{turn_id}", authorization=authorization)
    try:
        try:
            async with asyncio.timeout(MESSAGE_TIMEOUT_S):
                async for ev in orchestrator.stream(user_message=question, session_id=session_id,
                                                    extra_context=page_context):
                    kind = ev.get("type") if isinstance(ev, dict) else None
                    if kind == "tool_completed":
                        tool_names.append(str(ev.get("tool") or ""))
                        err = _tool_error(ev.get("tool"), ev.get("output"))
                        if err is not None and len(tool_errors) < 50:
                            tool_errors.append(err)
                    elif kind in TERMINAL_EVENTS:
                        final = ev
        except TimeoutError:
            ended_by, error = "timeout", f"timed out after {int(MESSAGE_TIMEOUT_S)} s"
        except Exception as exc:  # noqa: BLE001 - a failed message is a result
            ended_by, error = "exception", f"{type(exc).__name__}: {str(exc)[:280]}"
    finally:
        upstream = proxy.refusals.upstream_failures()
        refused = proxy.refusals.end()
    ms = int((time.perf_counter() - t0) * 1000)
    answer, choices, cards, cut = "", None, [], False
    if final is not None and final.get("type") == "workflow_completed":
        ended_by = "workflow_completed"
        answer = str(final.get("answer") or "")
        choices = final.get("choices") or None
        calls = final.get("tool_calls") or []
        cards, cut = cards_of(calls)
        tool_names += [str(tc.get("tool")) for tc in calls if isinstance(tc, dict) and tc.get("tool")]
    elif final is not None and final.get("type") == "gate_interrupt":
        ended_by = "gate_interrupt"
    elif final is not None and final.get("type") == "error":
        ended_by, error = "error", str(final.get("error") or "the chat reported an error")[:300]
    elif ended_by == "none":
        error = "the chat ended without an answer"
    _close(t, answer=answer, error=error)
    last = llm_cost.current()
    entries = list(first.entries) + (list(last.entries) if last is not None and last is not first else [])
    summary = llm_cost.Ledger(session_id=session_id, entries=entries).summary()
    row = turn_row(t, answer=answer, error=error, ledger_summary=summary)
    log.info("trace_lab.capture.done", session_id=session_id, turn_id=turn_id, ended_by=ended_by, ms=ms,
             usd=row["cost_usd"], refused=len(refused))
    return CapturedTurn(
        answer=answer[:ANSWER_MAX], error=error, turn_id=turn_id, spans=span_rows(t), ledger=_jsonable(entries),
        usd=float(row["cost_usd"] or 0.0), ms=ms, refused=refused, cards=_jsonable(cards), question=question,
        session_id=session_id, turn=row, ended_by=ended_by, upstream_failures=upstream, tool_errors=tool_errors,
        tool_names=sorted({n for n in tool_names if n})[:60], cards_cut=cut,
        choices=_jsonable(choices) if choices else None)
