"""Hoist Traces - every turn as a tree of spans, with cost, kept per company.

Two records of a turn already existed and were never joined (2 Oct 2026): the activity log
(flat input/output rows, tokens and latency but no dollars and no company) and the cost ledger
(dollars by role, in memory only - it reached the browser and nothing else). This module is the
join, and the record a dashboard reads:

    plenum_cafm.agent_trace_turns   one row per turn: who asked, what, the answer, totals
                                    (llm calls, tool calls, cost, tokens, latency), ok/error,
                                    and the reader's feedback when given
    plenum_cafm.agent_trace_spans   the tree: turn -> agent/router/stage -> llm and tool calls,
                                    each with parent, timings, model, tokens, cost, bounded
                                    input and output

Nothing calls this directly from the agents. It listens where the data already flows:

  * `activity_log.fire`      every INPUT row opens a span, its OUTPUT/ERROR row closes it
                             (tool rows pair by name; a stage's model rows carry tokens)
  * `llm_cost.Ledger.record` attaches dollars and tokens to the span the call belongs to
  * the stream loop          `on_chat_model_start/end` events - the general agent loop's own
                             model calls, which no activity row records

and flushes once, when the turn's own output row arrives. One write, best-effort, never on the
turn's path. Spans group under an auto-created agent span per `agent` name, so a compliance turn
reads compliance -> fetch/plan/analyst..., a general turn reads orchestrator loop -> model/tool.
Inputs and outputs are cut to BOUND chars each; the activity log keeps the longer copy.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from decimal import Decimal
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
from sqlalchemy import text

log = structlog.get_logger(__name__)

TURNS = "plenum_cafm.agent_trace_turns"
SPANS = "plenum_cafm.agent_trace_spans"
#: Characters kept of any one input or output.
BOUND = 16000
#: Spans with payloads are deleted after this many days; turn rows stay (TRACE_RETENTION_DAYS).
DEFAULT_RETENTION_DAYS = 90
_ready = False

_DDL = [
    f"""
    CREATE TABLE IF NOT EXISTS {TURNS} (
        turn_id            TEXT PRIMARY KEY,
        session_id         TEXT,
        organization_id    UUID,
        user_id            UUID,
        email              TEXT,
        question           TEXT,
        answer             TEXT,
        route              TEXT,
        started_at         TIMESTAMPTZ NOT NULL,
        ended_at           TIMESTAMPTZ,
        latency_ms         INTEGER,
        llm_calls          INTEGER NOT NULL DEFAULT 0,
        tool_calls         INTEGER NOT NULL DEFAULT 0,
        cost_usd           NUMERIC(12,6),
        cost_complete      BOOLEAN,
        input_tokens       INTEGER,
        output_tokens      INTEGER,
        cache_read_tokens  INTEGER,
        models             JSONB,
        tools              JSONB,
        ok                 BOOLEAN NOT NULL DEFAULT TRUE,
        error              TEXT,
        feedback_rating    TEXT,
        feedback_comment   TEXT,
        feedback_by        UUID,
        feedback_at        TIMESTAMPTZ,
        created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
    f"""
    CREATE TABLE IF NOT EXISTS {SPANS} (
        id                 UUID PRIMARY KEY,
        turn_id            TEXT NOT NULL,
        session_id         TEXT,
        organization_id    UUID,
        parent_id          UUID,
        seq                INTEGER NOT NULL,
        kind               TEXT NOT NULL,
        name               TEXT NOT NULL,
        agent              TEXT,
        model              TEXT,
        started_at         TIMESTAMPTZ,
        ended_at           TIMESTAMPTZ,
        latency_ms         INTEGER,
        input              JSONB,
        output             JSONB,
        input_tokens       INTEGER,
        output_tokens      INTEGER,
        cache_read_tokens  INTEGER,
        cost_usd           NUMERIC(12,6),
        ok                 BOOLEAN NOT NULL DEFAULT TRUE,
        error              TEXT
    )""",
    f"CREATE INDEX IF NOT EXISTS ix_trace_turns_org_time ON {TURNS} (organization_id, started_at DESC)",
    f"CREATE INDEX IF NOT EXISTS ix_trace_turns_user_time ON {TURNS} (user_id, started_at DESC)",
    f"CREATE INDEX IF NOT EXISTS ix_trace_spans_turn ON {SPANS} (turn_id, seq)",
]


def ready() -> bool:
    return _ready


async def ensure_tables() -> bool:
    global _ready
    try:
        from ..database import _get_engine

        async with _get_engine().begin() as conn:
            for stmt in _DDL:
                await conn.execute(text(stmt))
        _ready = True
        log.info("trace.ready")
    except Exception as exc:  # noqa: BLE001
        _ready = False
        log.warning("trace.tables_failed", error=str(exc)[:300])
    return _ready


async def purge(days: int | None = None) -> int:
    """Drop span payloads older than the retention window. Turn rows (and their totals) stay."""
    if not _ready:
        return 0
    import os

    d = days or int(os.environ.get("TRACE_RETENTION_DAYS") or DEFAULT_RETENTION_DAYS)
    from ..database import _get_engine

    async with _get_engine().begin() as conn:
        res = await conn.execute(text(f"DELETE FROM {SPANS} WHERE started_at < now() - make_interval(days => :d)"), {"d": d})
    if res.rowcount:
        log.info("trace.purged", spans=res.rowcount, older_than_days=d)
    return res.rowcount or 0


# ── the turn being recorded ───────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _bound(v: Any) -> Any:
    """JSON-safe and cut to BOUND characters; long strings inside are shortened first."""
    if v is None:
        return None
    try:
        s = json.dumps(v, default=str)
    except (TypeError, ValueError):
        s = json.dumps(str(v))
    if len(s) <= BOUND:
        return json.loads(s)
    if isinstance(v, str):
        return v[:BOUND] + f"… [{len(v) - BOUND} more chars]"
    if isinstance(v, dict):
        out = {}
        for k, x in v.items():
            if isinstance(x, str) and len(x) > BOUND // 4:
                out[k] = x[:BOUND // 4] + f"… [{len(x) - BOUND // 4} more chars]"
            else:
                out[k] = x
        s2 = json.dumps(out, default=str)
        return json.loads(s2) if len(s2) <= BOUND else {"_truncated": s2[:BOUND]}
    return {"_truncated": s[:BOUND]}


class Span:
    __slots__ = ("id", "parent_id", "seq", "kind", "name", "agent", "model", "t0", "t1", "started_at", "ended_at",
                 "input", "output", "input_tokens", "output_tokens", "cache_read", "cost_usd", "ok", "error", "role")

    def __init__(self, *, kind: str, name: str, agent: str | None, parent_id: str | None, seq: int,
                 model: str | None = None, input: Any = None, role: str | None = None) -> None:
        self.id = str(uuid.uuid4())
        self.parent_id, self.seq, self.kind, self.name, self.agent = parent_id, seq, kind, name, agent
        self.model, self.role = model, role
        self.t0, self.t1 = time.perf_counter(), None
        self.started_at, self.ended_at = _now(), None
        self.input, self.output = input, None
        self.input_tokens = self.output_tokens = self.cache_read = None
        self.cost_usd, self.ok, self.error = None, True, None

    @property
    def open(self) -> bool:
        return self.t1 is None

    def close(self, *, output: Any = None, ok: bool = True, error: str | None = None, latency_ms: float | None = None,
              model: str | None = None, input_tokens: int | None = None, output_tokens: int | None = None) -> None:
        self.t1 = time.perf_counter()
        self.ended_at = _now()
        if latency_ms is not None:
            self.started_at = self.ended_at - timedelta(milliseconds=float(latency_ms))
            self.t0 = self.t1 - float(latency_ms) / 1000
        if output is not None:
            self.output = output
        self.ok, self.error = ok and not error, error
        self.model = model or self.model
        if input_tokens is not None:
            self.input_tokens = input_tokens
        if output_tokens is not None:
            self.output_tokens = output_tokens

    @property
    def latency_ms(self) -> int | None:
        return int(round((self.t1 - self.t0) * 1000)) if self.t1 is not None else None


class Turn:
    def __init__(self, turn_id: str, session_id: str | None) -> None:
        self.turn_id, self.session_id = turn_id, session_id
        self.started = _now()
        self.question: str = ""
        self.route: str | None = None
        self.spans: list[Span] = []
        self.groups: dict[str, Span] = {}
        self.runs: dict[str, Span] = {}      # model runs by run_id (stream events)
        self.flushed = False
        from ..http_client import caller_organization_id
        from ..services.principal import caller_principal

        p = caller_principal.get()
        self.organization_id = caller_organization_id.get() or (str(p.organization_id) if p and p.organization_id else None)
        self.user_id = str(p.user_id) if p else None
        self.email = p.email if p else None
        self.root = self._add(Span(kind="turn", name="turn", agent="orchestrator", parent_id=None, seq=0))

    def _add(self, s: Span) -> Span:
        self.spans.append(s)
        return s

    def new(self, **kw: Any) -> Span:
        return self._add(Span(seq=len(self.spans), **kw))

    def group(self, agent: str) -> Span:
        """The agent span the stages and calls of `agent` hang under; the root for the orchestrator."""
        if agent in ("orchestrator", "turn"):
            return self.root
        g = self.groups.get(agent)
        if g is None:
            g = self.groups[agent] = self.new(kind="agent", name=agent, agent=agent, parent_id=self.root.id)
        return g

    def last_open(self, pred) -> Span | None:
        for s in reversed(self.spans):
            if s.open and s is not self.root and pred(s):
                return s
        return None


_turn: ContextVar[Turn | None] = ContextVar("hoist_trace_turn", default=None)


def current() -> Turn | None:
    return _turn.get()


def begin(turn_id: str | None, session_id: str | None) -> Turn | None:
    if not _ready:
        return None
    t = Turn(turn_id or ("turn-" + uuid.uuid4().hex[:16]), session_id)
    _turn.set(t)
    return t


def _kind_for(agent: str, stage: str, model: str | None) -> str:
    if stage == "tool":
        return "tool"
    if "router" in agent or stage == "router" or stage == "classify_engine":
        return "router"
    if model or stage in ("analyst", "planner", "plan", "review", "summary", "prompt", "classify_document", "extract_document"):
        return "llm"
    return "stage"


# ── listeners ─────────────────────────────────────────────────────────────────────────

def on_activity(kw: dict[str, Any]) -> None:
    """Called by activity_log.fire with the row it is about to write."""
    try:
        agent, stage, direction = str(kw.get("agent") or ""), str(kw.get("stage") or ""), str(kw.get("direction") or "")
        payload = kw.get("payload") if isinstance(kw.get("payload"), dict) else {}
        t = _turn.get()
        if agent == "orchestrator" and stage == "turn":
            if direction == "input":
                if t is None or t.flushed:
                    t = begin(kw.get("turn_id"), kw.get("session_id"))
                if t is None:
                    return
                inp = payload.get("input") if isinstance(payload.get("input"), dict) else {}
                t.question = str(payload.get("message") or inp.get("latest_user_message") or kw.get("summary") or "")[:4000]
                t.root.input = _bound({"message": t.question, "mode": payload.get("mode")})
                return
            if t is None:
                return
            answer = payload.get("answer") or ("gate_interrupt" if "gate_interrupt" in payload else "") or kw.get("summary") or ""
            t.root.close(output=_bound({"answer": answer}), ok=bool(kw.get("ok", True)), error=kw.get("error"),
                         latency_ms=kw.get("latency_ms"))
            _flush(t, answer=str(answer), tool_calls=payload.get("tool_calls") or [])
            return
        if t is None or t.flushed:
            return
        if stage == "tool":
            name = str(payload.get("tool") or kw.get("summary") or "tool")
            if direction == "input":
                t.new(kind="tool", name=name, agent=agent, parent_id=t.group("orchestrator").id if agent.startswith("tool:") else t.group(agent).id,
                      input=_bound(payload.get("input")))
            else:
                s = t.last_open(lambda x: x.kind == "tool" and x.name == name)
                if s is None:
                    s = t.new(kind="tool", name=name, agent=agent, parent_id=t.root.id)
                s.close(output=_bound(payload.get("output")), ok=bool(kw.get("ok", True)), error=kw.get("error"))
            return
        model = kw.get("model")
        kind = _kind_for(agent, stage, model)
        # A router is one call that picks the agent: it sits on the root, not in a group of one.
        parent = t.root.id if kind == "router" else t.group(agent).id
        if direction == "input":
            t.new(kind=kind, name=stage, agent=agent, parent_id=parent, model=model, input=_bound(payload or kw.get("summary")), role=stage)
            return
        s = t.last_open(lambda x: x.agent == agent and x.name == stage)
        if s is None:
            s = t.new(kind=kind, name=stage, agent=agent, parent_id=parent, model=model, role=stage)
        s.close(output=_bound(payload.get("model_output") if "model_output" in payload else (payload or kw.get("summary"))),
                ok=bool(kw.get("ok", True)), error=kw.get("error"), latency_ms=kw.get("latency_ms"), model=model,
                input_tokens=kw.get("input_tokens"), output_tokens=kw.get("output_tokens"))
    except Exception as exc:  # noqa: BLE001 - never the turn's problem
        log.warning("trace.on_activity_failed", error=str(exc)[:200])


def on_llm_cost(entry: dict[str, Any]) -> None:
    """Called by the cost ledger: dollars and tokens for one model call, by role."""
    try:
        t = _turn.get()
        if t is None or t.flushed:
            return
        role = str(entry.get("role") or "llm")
        # The span this call belongs to: the newest llm/router span of that role or agent still
        # without a cost. The compliance stages name their role as the stage; routers as the agent.
        target = None
        for s in reversed(t.spans):
            if s.kind in ("llm", "router", "stage") and s.cost_usd is None and (s.role == role or s.agent == role or s.name == role
                                                                                  or (s.agent or "").endswith(role)):
                target = s
                break
        if target is None:
            # A model call no activity row announced: inside the tool running now (the record
            # engine's planner, a sub-agent) it hangs under that tool; otherwise under its role.
            tool = t.last_open(lambda x: x.kind == "tool")
            parent = tool.id if tool else t.group(role).id
            target = t.new(kind="llm", name=role, agent=(tool.agent if tool else role), parent_id=parent, model=entry.get("model"), role=role)
            target.close(latency_ms=entry.get("ms"))
        target.kind = "llm" if target.kind == "stage" else target.kind
        target.model = entry.get("model") or target.model
        target.cost_usd = entry.get("usd")
        target.input_tokens = entry.get("input_tokens", target.input_tokens)
        target.output_tokens = entry.get("output_tokens", target.output_tokens)
        target.cache_read = entry.get("cache_read", target.cache_read)
    except Exception as exc:  # noqa: BLE001
        log.warning("trace.on_llm_cost_failed", error=str(exc)[:200])


def on_prompt(*, system_prompt: str | None, user_message: str, conversation: str | None, working_set: dict | None,
              memories: str | None, extra_context: str | None, scope_block: str | None = None) -> None:
    """What the orchestrator model was given for this turn, as one span under the root: the
    system prompt, the thread so far, the working set, the memories recalled, the page context
    and the question. Without it a trace showed every call but not what shaped the first one."""
    try:
        t = _turn.get()
        if t is None or t.flushed:
            return
        s = t.new(kind="stage", name="prompt assembled", agent="orchestrator", parent_id=t.root.id,
                  input=_bound({"user_message": user_message, "system_prompt": system_prompt,
                                "recent_conversation": conversation or None, "working_set": working_set or None,
                                "working_set_block": scope_block or None, "memories_recalled": memories or None,
                                "page_context": extra_context or None}))
        s.close(output=_bound({"system_prompt_chars": len(system_prompt or ""), "conversation_chars": len(conversation or ""),
                               "memories": bool(memories), "working_set": bool(working_set)}))
    except Exception as exc:  # noqa: BLE001
        log.warning("trace.on_prompt_failed", error=str(exc)[:200])


#: Result rows kept on a query span; the tool's own output carries what the model saw.
SQL_ROWS = 50


def on_sql(sql: str, params: Any, rows: list | None, ms: float | None, *, label: str = "", error: str | None = None) -> None:
    """One SQL statement a tool ran, as a child of the tool span open right now (or the root).

    The record tools compile SQL from the ontology and return summarised records; the statement
    itself and what the database answered were in no log. This keeps both: the dashboard shows
    the query under the tool that ran it, with the first SQL_ROWS rows."""
    try:
        t = _turn.get()
        if t is None or t.flushed or not sql:
            return
        parent = t.last_open(lambda x: x.kind == "tool") or t.root
        name = (label or "").strip() or (sql.strip().split(None, 4)[:4] and " ".join(sql.strip().split()[:4]))
        s = t.new(kind="db", name=str(name)[:120], agent=parent.agent, parent_id=parent.id,
                  input=_bound({"sql": sql, "params": params if isinstance(params, dict) else {}, "label": label or None}))
        out: dict[str, Any] = {"row_count": len(rows) if rows is not None else None}
        if rows:
            out["rows"] = rows[:SQL_ROWS]
            if len(rows) > SQL_ROWS:
                out["truncated"] = len(rows) - SQL_ROWS
        s.close(output=_bound(out), ok=not error, error=error, latency_ms=ms)
    except Exception as exc:  # noqa: BLE001
        log.warning("trace.on_sql_failed", error=str(exc)[:200])


def on_model_start(run_id: str, model: str | None, depth: int, messages_summary: dict | None = None) -> None:
    """A model run inside the LangGraph loop (stream events): the orchestrator's own thinking,
    or a sub-agent's when nested deeper."""
    try:
        t = _turn.get()
        if t is None or t.flushed or not run_id:
            return
        parent = t.group("orchestrator loop") if depth <= 2 else t.group("sub-agent")
        t.runs[run_id] = t.new(kind="llm", name="model", agent=parent.name, parent_id=parent.id, model=model, role="agent",
                               input=_bound(messages_summary) if messages_summary else None)
    except Exception as exc:  # noqa: BLE001
        log.warning("trace.on_model_start_failed", error=str(exc)[:200])


def on_model_end(run_id: str, output_msg: Any) -> None:
    try:
        t = _turn.get()
        if t is None or t.flushed:
            return
        s = t.runs.pop(run_id, None)
        if s is None:
            return
        um = getattr(output_msg, "usage_metadata", None) or {}
        details = um.get("input_token_details") if isinstance(um, dict) else None
        cached = int(details.get("cache_read") or 0) if isinstance(details, dict) else 0
        in_tok = max(0, int(um.get("input_tokens") or 0) - cached) if um else None
        out_tok = int(um.get("output_tokens") or 0) if um else None
        meta = getattr(output_msg, "response_metadata", None) or {}
        model = meta.get("model_name") or meta.get("model") or s.model
        tool_calls = getattr(output_msg, "tool_calls", None) or []
        content = getattr(output_msg, "content", "")
        s.close(output=_bound({"content": content if isinstance(content, str) else str(content),
                               "tool_calls": [{"name": c.get("name"), "args": c.get("args")} for c in tool_calls if isinstance(c, dict)]}),
                model=model, input_tokens=in_tok, output_tokens=out_tok)
        s.cache_read = cached or None
        if um and model:
            from .llm_cost import price_usd
            s.cost_usd = price_usd(model, {"input_tokens": in_tok or 0, "output_tokens": out_tok or 0, "cache_read": cached})
    except Exception as exc:  # noqa: BLE001
        log.warning("trace.on_model_end_failed", error=str(exc)[:200])


# ── flush ─────────────────────────────────────────────────────────────────────────────

def totals(t: Turn, ledger_summary: dict[str, Any] | None) -> dict[str, Any]:
    """The turn's headline numbers. The ledger's dollars are the ones the reader already saw in
    the chat, so they win when a ledger ran; the spans' own sum is the fallback."""
    llm = [s for s in t.spans if s.kind in ("llm", "router")]
    tools = [s for s in t.spans if s.kind == "tool"]
    span_usd = sum(float(s.cost_usd) for s in llm if s.cost_usd is not None)
    out = {
        "llm_calls": len(llm), "tool_calls": len(tools),
        "input_tokens": sum(int(s.input_tokens or 0) for s in llm), "output_tokens": sum(int(s.output_tokens or 0) for s in llm),
        "cache_read_tokens": sum(int(s.cache_read or 0) for s in llm),
        "cost_usd": round(span_usd, 6), "cost_complete": all(s.cost_usd is not None for s in llm) if llm else True,
        "models": sorted({s.model for s in llm if s.model}), "tools": sorted({s.name for s in tools}),
    }
    if ledger_summary and ledger_summary.get("calls"):
        out["cost_usd"] = round(max(float(ledger_summary.get("usd") or 0), span_usd), 6)
        out["cost_complete"] = bool(ledger_summary.get("usd_complete", True)) and out["cost_complete"]
        out["input_tokens"] = max(out["input_tokens"], int(ledger_summary.get("input_tokens") or 0))
        out["output_tokens"] = max(out["output_tokens"], int(ledger_summary.get("output_tokens") or 0))
        out["cache_read_tokens"] = max(out["cache_read_tokens"], int(ledger_summary.get("cache_read") or 0))
        out["models"] = sorted(set(out["models"]) | {str(m) for m in ledger_summary.get("models") or []})
    return out


def _flush(t: Turn, *, answer: str, tool_calls: list) -> None:
    if t.flushed:
        return
    t.flushed = True
    # Anything still open closes with the turn.
    for s in t.spans:
        if s.open:
            s.close()
    from . import llm_cost

    ledger = llm_cost.current()
    summary = ledger.summary() if ledger else None
    try:
        asyncio.get_running_loop().create_task(_write(t, answer, tool_calls, summary))
    except RuntimeError:
        pass


async def _write(t: Turn, answer: str, tool_calls: list, ledger_summary: dict[str, Any] | None) -> None:
    if not _ready:
        return
    try:
        from ..database import _get_engine

        tot = totals(t, ledger_summary)
        root = t.root
        async with _get_engine().begin() as conn:
            await conn.execute(text(f"""
                INSERT INTO {TURNS} (turn_id, session_id, organization_id, user_id, email, question, answer, route, started_at, ended_at,
                                     latency_ms, llm_calls, tool_calls, cost_usd, cost_complete, input_tokens, output_tokens,
                                     cache_read_tokens, models, tools, ok, error)
                VALUES (:turn_id, :sid, CAST(:org AS uuid), CAST(:uid AS uuid), :email, :q, :a, :route, :t0, :t1, :lat, :llm, :tools_n,
                        :usd, :complete, :tin, :tout, :tcache, CAST(:models AS jsonb), CAST(:tools AS jsonb), :ok, :err)
                ON CONFLICT (turn_id) DO UPDATE SET answer = EXCLUDED.answer, ended_at = EXCLUDED.ended_at,
                        latency_ms = EXCLUDED.latency_ms, llm_calls = EXCLUDED.llm_calls, tool_calls = EXCLUDED.tool_calls,
                        cost_usd = EXCLUDED.cost_usd, ok = EXCLUDED.ok, error = EXCLUDED.error"""),
                {"turn_id": t.turn_id, "sid": t.session_id, "org": t.organization_id, "uid": t.user_id, "email": t.email,
                 "q": t.question, "a": (answer or "")[:20000], "route": t.route, "t0": root.started_at, "t1": root.ended_at,
                 "lat": root.latency_ms, "llm": tot["llm_calls"], "tools_n": tot["tool_calls"], "usd": tot["cost_usd"],
                 "complete": tot["cost_complete"], "tin": tot["input_tokens"], "tout": tot["output_tokens"],
                 "tcache": tot["cache_read_tokens"], "models": json.dumps(tot["models"]), "tools": json.dumps(tot["tools"]),
                 "ok": root.ok, "err": root.error})
            for s in t.spans:
                await conn.execute(text(f"""
                    INSERT INTO {SPANS} (id, turn_id, session_id, organization_id, parent_id, seq, kind, name, agent, model, started_at,
                                         ended_at, latency_ms, input, output, input_tokens, output_tokens, cache_read_tokens, cost_usd, ok, error)
                    VALUES (CAST(:id AS uuid), :turn_id, :sid, CAST(:org AS uuid), CAST(:parent AS uuid), :seq, :kind, :name, :agent, :model,
                            :t0, :t1, :lat, CAST(:inp AS jsonb), CAST(:out AS jsonb), :tin, :tout, :tcache, :usd, :ok, :err)"""),
                    {"id": s.id, "turn_id": t.turn_id, "sid": t.session_id, "org": t.organization_id, "parent": s.parent_id, "seq": s.seq,
                     "kind": s.kind, "name": s.name[:200], "agent": s.agent, "model": s.model, "t0": s.started_at, "t1": s.ended_at,
                     "lat": s.latency_ms, "inp": json.dumps(s.input, default=str) if s.input is not None else None,
                     "out": json.dumps(s.output, default=str) if s.output is not None else None,
                     "tin": s.input_tokens, "tout": s.output_tokens, "tcache": s.cache_read, "usd": s.cost_usd, "ok": s.ok, "err": s.error})
        log.info("trace.written", turn_id=t.turn_id, spans=len(t.spans), cost_usd=tot["cost_usd"])
    except Exception as exc:  # noqa: BLE001
        log.warning("trace.write_failed", turn_id=t.turn_id, error=str(exc)[:300])


# ── reading, for the dashboard ────────────────────────────────────────────────────────

def _scope_sql(principal, org: str | None) -> tuple[str, dict[str, Any]]:
    """An admin reads the company's turns; anyone else their own; a superadmin the company they act for."""
    params: dict[str, Any] = {}
    parts = []
    if org:
        parts.append("organization_id = CAST(:org AS uuid)")
        params["org"] = org
    elif principal.role != "superadmin":
        parts.append("FALSE")
    if not principal.is_admin:
        parts.append("user_id = CAST(:uid AS uuid)")
        params["uid"] = str(principal.user_id)
    return (" AND ".join(parts) or "TRUE"), params


def _iso(d: dict[str, Any]) -> dict[str, Any]:
    for k, v in list(d.items()):
        if isinstance(v, datetime):
            d[k] = v.isoformat()
        elif isinstance(v, uuid.UUID):
            d[k] = str(v)
        elif isinstance(v, Decimal):
            d[k] = float(v)
    return d


async def list_turns(principal, org: str | None, *, since: datetime | None = None, until: datetime | None = None,
                     q: str | None = None, status: str | None = None, model: str | None = None, user: str | None = None,
                     limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
    if not _ready:
        return []
    where, p = _scope_sql(principal, org)
    if since:
        where += " AND started_at >= :since"
        p["since"] = since
    if until:
        where += " AND started_at < :until"
        p["until"] = until
    if q:
        where += " AND (question ILIKE :q OR answer ILIKE :q)"
        p["q"] = "%" + q.strip() + "%"
    if status == "ok":
        where += " AND ok"
    elif status == "error":
        where += " AND NOT ok"
    if model:
        where += " AND models ? :model"
        p["model"] = model
    if user and principal.is_admin:
        where += " AND email ILIKE :user"
        p["user"] = "%" + user.strip() + "%"
    p["lim"], p["off"] = max(1, min(int(limit), 500)), max(0, int(offset))
    from ..database import _get_engine

    async with _get_engine().connect() as conn:
        rows = (await conn.execute(text(f"""
            SELECT turn_id, session_id, email, left(question, 300) AS question, left(answer, 300) AS answer, started_at, ended_at,
                   latency_ms, llm_calls, tool_calls, cost_usd, cost_complete, input_tokens, output_tokens, cache_read_tokens,
                   models, tools, ok, error, feedback_rating
              FROM {TURNS} WHERE {where} ORDER BY started_at DESC LIMIT :lim OFFSET :off"""), p)).mappings().all()
    return [_iso(dict(r)) for r in rows]


async def get_turn(principal, org: str | None, turn_id: str) -> dict[str, Any] | None:
    if not _ready:
        return None
    where, p = _scope_sql(principal, org)
    p["tid"] = turn_id
    from ..database import _get_engine

    async with _get_engine().connect() as conn:
        head = (await conn.execute(text(f"SELECT * FROM {TURNS} WHERE turn_id = :tid AND {where}"), p)).mappings().first()
        if head is None:
            return None
        spans = (await conn.execute(text(f"SELECT * FROM {SPANS} WHERE turn_id = :tid ORDER BY seq"), {"tid": turn_id})).mappings().all()
    out = _iso(dict(head))
    out["spans"] = [_iso(dict(s)) for s in spans]
    return out


async def stats(principal, org: str | None, *, since: datetime | None = None, until: datetime | None = None) -> dict[str, Any]:
    """Cost, calls, latency and reliability over the window: by day, by model, by tool, by agent."""
    if not _ready:
        return {"ok": False, "error": "The trace store is not available."}
    where, p = _scope_sql(principal, org)
    p["since"] = since or (_now() - timedelta(days=30))
    p["until"] = until or (_now() + timedelta(days=1))
    where += " AND started_at >= :since AND started_at < :until"
    from ..database import _get_engine

    async with _get_engine().connect() as conn:
        head = (await conn.execute(text(f"""
            SELECT count(*) AS turns, count(*) FILTER (WHERE NOT ok) AS failed, coalesce(sum(cost_usd), 0) AS cost_usd,
                   coalesce(sum(llm_calls), 0) AS llm_calls, coalesce(sum(tool_calls), 0) AS tool_calls,
                   coalesce(sum(input_tokens), 0) AS input_tokens, coalesce(sum(output_tokens), 0) AS output_tokens,
                   coalesce(sum(cache_read_tokens), 0) AS cache_read_tokens,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms) AS p50_ms,
                   percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95_ms,
                   count(*) FILTER (WHERE feedback_rating = 'up') AS thumbs_up,
                   count(*) FILTER (WHERE feedback_rating = 'down') AS thumbs_down,
                   count(DISTINCT user_id) AS users
              FROM {TURNS} WHERE {where}"""), p)).mappings().first()
        by_day = (await conn.execute(text(f"""
            SELECT date_trunc('day', started_at) AS day, count(*) AS turns, count(*) FILTER (WHERE NOT ok) AS failed,
                   coalesce(sum(cost_usd), 0) AS cost_usd, percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms) AS p50_ms,
                   percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95_ms
              FROM {TURNS} WHERE {where} GROUP BY 1 ORDER BY 1"""), p)).mappings().all()
        by_model = (await conn.execute(text(f"""
            SELECT s.model, count(*) AS calls, coalesce(sum(s.cost_usd), 0) AS cost_usd, coalesce(sum(s.input_tokens), 0) AS input_tokens,
                   coalesce(sum(s.output_tokens), 0) AS output_tokens, coalesce(sum(s.cache_read_tokens), 0) AS cache_read_tokens,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY s.latency_ms) AS p50_ms, count(*) FILTER (WHERE NOT s.ok) AS failed
              FROM {SPANS} s JOIN {TURNS} t ON t.turn_id = s.turn_id
             WHERE s.kind IN ('llm', 'router') AND s.model IS NOT NULL AND {where.replace('organization_id', 't.organization_id').replace('user_id', 't.user_id').replace('started_at', 't.started_at')}
             GROUP BY 1 ORDER BY cost_usd DESC"""), p)).mappings().all()
        by_tool = (await conn.execute(text(f"""
            SELECT s.name, count(*) AS calls, percentile_cont(0.5) WITHIN GROUP (ORDER BY s.latency_ms) AS p50_ms,
                   percentile_cont(0.95) WITHIN GROUP (ORDER BY s.latency_ms) AS p95_ms, count(*) FILTER (WHERE NOT s.ok) AS failed
              FROM {SPANS} s JOIN {TURNS} t ON t.turn_id = s.turn_id
             WHERE s.kind = 'tool' AND {where.replace('organization_id', 't.organization_id').replace('user_id', 't.user_id').replace('started_at', 't.started_at')}
             GROUP BY 1 ORDER BY calls DESC LIMIT 40"""), p)).mappings().all()
        by_agent = (await conn.execute(text(f"""
            SELECT coalesce(s.agent, 'orchestrator') AS agent, count(*) AS spans, coalesce(sum(s.cost_usd), 0) AS cost_usd,
                   count(*) FILTER (WHERE NOT s.ok) AS failed
              FROM {SPANS} s JOIN {TURNS} t ON t.turn_id = s.turn_id
             WHERE s.kind IN ('llm', 'router', 'tool') AND {where.replace('organization_id', 't.organization_id').replace('user_id', 't.user_id').replace('started_at', 't.started_at')}
             GROUP BY 1 ORDER BY cost_usd DESC"""), p)).mappings().all()
    return {"ok": True, "since": p["since"].isoformat(), "until": p["until"].isoformat(), "totals": _iso(dict(head)),
            "by_day": [_iso(dict(r)) for r in by_day], "by_model": [_iso(dict(r)) for r in by_model],
            "by_tool": [_iso(dict(r)) for r in by_tool], "by_agent": [_iso(dict(r)) for r in by_agent]}


async def set_feedback(principal, org: str | None, turn_id: str, rating: str | None, comment: str | None) -> bool:
    """A reader's verdict on an answer: the label that makes a turn training data."""
    if not _ready:
        return False
    where, p = _scope_sql(principal, org)
    p.update({"tid": turn_id, "rating": rating, "comment": (comment or "")[:2000] or None, "by": str(principal.user_id)})
    from ..database import _get_engine

    async with _get_engine().begin() as conn:
        res = await conn.execute(text(f"""
            UPDATE {TURNS} SET feedback_rating = :rating, feedback_comment = :comment, feedback_by = CAST(:by AS uuid), feedback_at = now()
             WHERE turn_id = :tid AND {where}"""), p)
    return bool(res.rowcount)


async def export_rows(principal, org: str | None, *, since: datetime | None, until: datetime | None,
                      rated_only: bool = False, limit: int = 5000):
    """Turns with their spans, oldest first, for a JSONL dataset (admin)."""
    if not _ready:
        return
    where, p = _scope_sql(principal, org)
    if since:
        where += " AND started_at >= :since"
        p["since"] = since
    if until:
        where += " AND started_at < :until"
        p["until"] = until
    if rated_only:
        where += " AND feedback_rating IS NOT NULL"
    p["lim"] = max(1, min(int(limit), 20000))
    from ..database import _get_engine

    async with _get_engine().connect() as conn:
        turns = (await conn.execute(text(f"SELECT * FROM {TURNS} WHERE {where} ORDER BY started_at ASC LIMIT :lim"), p)).mappings().all()
        for t in turns:
            spans = (await conn.execute(text(f"SELECT seq, parent_id, kind, name, agent, model, latency_ms, input, output, input_tokens,"
                                             f" output_tokens, cache_read_tokens, cost_usd, ok, error FROM {SPANS} WHERE turn_id = :tid ORDER BY seq"),
                                        {"tid": t["turn_id"]})).mappings().all()
            row = _iso(dict(t))
            row["spans"] = [_iso(dict(s)) for s in spans]
            yield row
