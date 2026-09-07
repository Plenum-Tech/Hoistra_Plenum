"""Activity log — every input and output message of the orchestrator and its agents.

Troubleshooting a compliance answer used to mean reading structlog lines, which are
short-lived, truncated, and never carry the payload unless COMPLIANCE_DEBUG_PAYLOADS is on.
This module records each step of a turn — what went into the model or tool, what came back,
how long it took, what it cost in tokens — as an append-only row in
``plenum_cafm.agent_activity_log``, keyed by the session the turn belongs to, so a question
answered badly on Tuesday can be replayed stage by stage on Friday.

Design rules:

* **Never breaks the turn.** Every write is best-effort. A logging failure is itself
  logged (to structlog) and swallowed.
* **Session comes from context.** ``set_current_session`` is called once at the entry
  points (invoke / stream); every deeper stage picks it up through a ``ContextVar`` so the
  compliance stages do not need a session_id threaded through their signatures.
* **Payloads are bounded.** JSON payloads are cut to ``ACTIVITY_LOG_PAYLOAD_CHARS`` with a
  marker, so a 90 KB portfolio JSON does not bloat the table; a full copy is still available
  through COMPLIANCE_DEBUG_PAYLOADS structlog lines when that is on.
* **Append-only.** Rows are never updated or deleted by the service.
"""
from __future__ import annotations

import asyncio
import contextvars
import json
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import structlog
from sqlalchemy import text

log = structlog.get_logger(__name__)

TABLE = "plenum_cafm.agent_activity_log"

_current_session: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "activity_session", default=None
)
_current_thread: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "activity_thread", default=None
)
# One turn = one user request through the orchestrator (or one UI action). Every row written
# while that request runs carries the same turn_id, so a session's trail can be split into
# transactions and one transaction replayed on its own.
_current_turn: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "activity_turn", default=None
)


def set_current_session(session_id: str | None, thread_id: str | None = None) -> None:
    _current_session.set(session_id)
    _current_thread.set(thread_id or session_id)


def get_current_session() -> str | None:
    return _current_session.get()


def start_turn(turn_id: str | None = None) -> str:
    """Begin a new transaction in this context (or adopt ``turn_id``). Returns the id."""
    tid = turn_id or ("turn-" + uuid.uuid4().hex[:16])
    _current_turn.set(tid)
    return tid


def current_turn() -> str | None:
    return _current_turn.get()


def ensure_turn() -> str:
    """The current turn id, starting one if this context has none yet."""
    return _current_turn.get() or start_turn()


def _usage_tokens(usage: Any) -> tuple[int | None, int | None]:
    if usage is None:
        return None, None
    if isinstance(usage, dict):
        return usage.get("input_tokens"), usage.get("output_tokens")
    return getattr(usage, "input_tokens", None), getattr(usage, "output_tokens", None)


def fire_exchange(
    *,
    agent: str,
    stage: str,
    system: Any = None,
    user: Any = None,
    output: Any = None,
    error: str | None = None,
    model: str | None = None,
    latency_ms: float | None = None,
    usage: Any = None,
    params: dict[str, Any] | None = None,
    summary_in: str | None = None,
    summary_out: str | None = None,
) -> None:
    """One model exchange as two rows: the INPUT (system prompt + user message + call
    parameters) and the OUTPUT (or ERROR). Used at every LLM call in the compliance path so
    the exact prompt and the exact answer of each sub-agent can be read back per turn."""
    fire(
        agent=agent, stage=stage, direction="input", model=model,
        summary=(summary_in or (str(user)[:300] if user is not None else stage)),
        payload={"system_prompt": system, "user_message": user, "params": params or {}},
    )
    in_tok, out_tok = _usage_tokens(usage)
    if error:
        fire(
            agent=agent, stage=stage, direction="error", model=model, ok=False, error=error,
            summary=(summary_out or error)[:300], latency_ms=latency_ms,
            input_tokens=in_tok, output_tokens=out_tok,
        )
    else:
        fire(
            agent=agent, stage=stage, direction="output", model=model, latency_ms=latency_ms,
            summary=(summary_out or (str(output)[:300] if output is not None else stage)),
            input_tokens=in_tok, output_tokens=out_tok,
            payload={"model_output": output},
        )


def enabled() -> bool:
    v = os.getenv("ACTIVITY_LOG_ENABLED", "true").strip().lower()
    return v not in {"0", "false", "no", "off"}


def payload_limit() -> int:
    try:
        return max(2000, int(os.getenv("ACTIVITY_LOG_PAYLOAD_CHARS", "64000")))
    except ValueError:
        return 64000


# ── pure helpers ──────────────────────────────────────────────────────────────────────


def _default(o: Any) -> Any:
    if isinstance(o, (datetime,)):
        return o.isoformat()
    if isinstance(o, (set, frozenset)):
        return sorted(str(x) for x in o)
    if hasattr(o, "model_dump"):
        try:
            return o.model_dump()
        except Exception:  # noqa: BLE001
            pass
    if hasattr(o, "content") and hasattr(o, "type"):  # LangChain messages
        return {"type": getattr(o, "type", None), "content": getattr(o, "content", None)}
    return str(o)


def bound_payload(payload: Any, limit: int | None = None) -> dict[str, Any] | None:
    """A JSON-safe payload cut to the size cap. Returns None for None.

    Large strings inside are shortened first (they are usually the prompt or the answer);
    if the whole object is still over the cap, it is replaced by a preview of its JSON so the
    row stays small and says so.
    """
    if payload is None:
        return None
    cap = limit or payload_limit()
    if not isinstance(payload, dict):
        payload = {"value": payload}
    try:
        raw = json.dumps(payload, default=_default, ensure_ascii=False)
    except Exception as exc:  # noqa: BLE001
        return {"unserialisable": str(exc)[:200], "repr": repr(payload)[:2000]}
    if len(raw) <= cap:
        return json.loads(raw)
    # Shorten the long strings first, keeping the structure intact.
    per_string = max(500, cap // 8)

    def cut(v: Any) -> Any:
        if isinstance(v, str):
            return v if len(v) <= per_string else v[:per_string] + f"…[+{len(v) - per_string} chars]"
        if isinstance(v, dict):
            return {k: cut(x) for k, x in v.items()}
        if isinstance(v, list):
            head = [cut(x) for x in v[:200]]
            return head + ([f"…[+{len(v) - 200} items]"] if len(v) > 200 else [])
        return v

    shortened = cut(json.loads(raw))
    raw2 = json.dumps(shortened, default=_default, ensure_ascii=False)
    if len(raw2) <= cap:
        shortened["_truncated"] = True
        shortened["_original_chars"] = len(raw)
        return shortened
    return {"_truncated": True, "_original_chars": len(raw), "preview": raw[: cap - 200]}


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── DB ────────────────────────────────────────────────────────────────────────────────

_DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    id              UUID PRIMARY KEY,
    session_id      VARCHAR(120),
    thread_id       VARCHAR(120),
    turn_id         VARCHAR(64),
    agent           VARCHAR(60)  NOT NULL,   -- orchestrator | compliance | compliance_router | tool:<domain> | frontend
    stage           VARCHAR(60)  NOT NULL,   -- turn | plan | fetch | prompt | analyst | review | summary | tool | router | action
    direction       VARCHAR(10)  NOT NULL,   -- input | output | error
    summary         TEXT,
    payload         JSONB,
    model           VARCHAR(120),
    latency_ms      INTEGER,
    input_tokens    INTEGER,
    output_tokens   INTEGER,
    ok              BOOLEAN NOT NULL DEFAULT TRUE,
    error           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""
_IDX = [
    f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS turn_id VARCHAR(64)",
    f"CREATE INDEX IF NOT EXISTS ix_agent_activity_session ON {TABLE} (session_id, created_at)",
    f"CREATE INDEX IF NOT EXISTS ix_agent_activity_turn ON {TABLE} (session_id, turn_id, created_at)",
    f"CREATE INDEX IF NOT EXISTS ix_agent_activity_agent_stage ON {TABLE} (agent, stage, created_at DESC)",
]


async def ensure_table() -> bool:
    """Create the table if missing. Returns True when the log is usable."""
    if not enabled():
        log.info("activity_log.disabled")
        return False
    try:
        from ..database import _get_engine

        async with _get_engine().begin() as conn:
            await conn.execute(text("CREATE SCHEMA IF NOT EXISTS plenum_cafm"))
            await conn.execute(text(_DDL))
            for stmt in _IDX:
                await conn.execute(text(stmt))
        log.info("activity_log.ready", table=TABLE)
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("activity_log.table_failed", error=str(exc)[:300])
        return False


async def record(
    *,
    agent: str,
    stage: str,
    direction: str,
    summary: str | None = None,
    payload: Any = None,
    model: str | None = None,
    latency_ms: float | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    ok: bool = True,
    error: str | None = None,
    session_id: str | None = None,
    thread_id: str | None = None,
    turn_id: str | None = None,
) -> str | None:
    """Append one activity row. Returns the row id, or None when nothing was written."""
    sid = session_id or _current_session.get()
    tid = thread_id or _current_thread.get() or sid
    turn = turn_id or _current_turn.get()
    bounded = bound_payload(payload)
    # Always a structlog line, so the console trail exists even without the table.
    log.info(
        f"activity.{agent}.{stage}.{direction}",
        session_id=sid,
        turn_id=turn,
        summary=(summary or "")[:300],
        model=model,
        latency_ms=round(latency_ms) if latency_ms is not None else None,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        ok=ok,
        error=(error or "")[:300] or None,
        payload_chars=len(json.dumps(bounded, default=str)) if bounded is not None else 0,
    )
    if not enabled():
        return None
    row_id = str(uuid.uuid4())
    try:
        from ..database import _get_engine

        async with _get_engine().begin() as conn:
            await conn.execute(
                text(
                    f"""
                    INSERT INTO {TABLE}
                        (id, session_id, thread_id, turn_id, agent, stage, direction, summary,
                         payload, model, latency_ms, input_tokens, output_tokens, ok, error,
                         created_at)
                    VALUES
                        (:id, :session_id, :thread_id, :turn_id, :agent, :stage, :direction,
                         :summary, CAST(:payload AS JSONB), :model, :latency_ms, :input_tokens,
                         :output_tokens, :ok, :error, :created_at)
                    """
                ),
                {
                    "id": row_id,
                    "session_id": (sid or "")[:120] or None,
                    "thread_id": (tid or "")[:120] or None,
                    "turn_id": (turn or "")[:64] or None,
                    "agent": agent[:60],
                    "stage": stage[:60],
                    "direction": direction[:10],
                    "summary": (summary or "")[:4000] or None,
                    "payload": json.dumps(bounded, default=_default, ensure_ascii=False)
                    if bounded is not None
                    else None,
                    "model": (model or "")[:120] or None,
                    "latency_ms": int(round(latency_ms)) if latency_ms is not None else None,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "ok": bool(ok),
                    "error": (error or "")[:4000] or None,
                    "created_at": _now(),
                },
            )
        return row_id
    except Exception as exc:  # noqa: BLE001 — logging must never break the turn
        log.warning("activity_log.write_failed", agent=agent, stage=stage, error=str(exc)[:300])
        return None


def fire(**kwargs: Any) -> None:
    """Schedule ``record`` without awaiting it — for call sites on a hot path.

    The ContextVar session is captured here, at call time, so the task sees the right one.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    kwargs.setdefault("session_id", _current_session.get())
    kwargs.setdefault("thread_id", _current_thread.get())
    kwargs.setdefault("turn_id", _current_turn.get())
    # A raw provider `usage` object is accepted here and mapped to token counts, and any
    # unknown keyword is dropped rather than raised — a logging call must never take a turn down.
    if "usage" in kwargs:
        in_tok, out_tok = _usage_tokens(kwargs.pop("usage"))
        kwargs.setdefault("input_tokens", in_tok)
        kwargs.setdefault("output_tokens", out_tok)
    allowed = {"agent", "stage", "direction", "summary", "payload", "model", "latency_ms",
               "input_tokens", "output_tokens", "ok", "error", "session_id", "thread_id", "turn_id"}
    dropped = [k for k in kwargs if k not in allowed]
    for k in dropped:
        kwargs.pop(k)
    if dropped:
        log.warning("activity_log.unknown_kwargs_dropped", keys=dropped)
    task = loop.create_task(record(**kwargs))
    _pending.add(task)
    task.add_done_callback(_pending.discard)


_pending: set[asyncio.Task] = set()


class timed:
    """``with activity_log.timed() as t: ...; t.ms`` — elapsed milliseconds for a stage."""

    def __enter__(self) -> "timed":
        self._t0 = time.perf_counter()
        self.ms = 0.0
        return self

    def __exit__(self, *exc: Any) -> None:
        self.ms = (time.perf_counter() - self._t0) * 1000


async def list_activity(
    session_id: str,
    *,
    agent: str | None = None,
    stage: str | None = None,
    turn_id: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Rows for one session (optionally one turn), oldest first — the replayable trail."""
    if not enabled():
        return []
    from ..database import _get_engine

    sql = f"SELECT * FROM {TABLE} WHERE session_id = :sid"
    params: dict[str, Any] = {"sid": session_id, "lim": max(1, min(int(limit), 2000))}
    if turn_id:
        sql += " AND turn_id = :turn"
        params["turn"] = turn_id
    if agent:
        sql += " AND agent = :agent"
        params["agent"] = agent
    if stage:
        sql += " AND stage = :stage"
        params["stage"] = stage
    sql += " ORDER BY created_at ASC, id ASC LIMIT :lim"
    async with _get_engine().connect() as conn:
        rows = (await conn.execute(text(sql), params)).mappings().all()
    out = []
    for r in rows:
        d = dict(r)
        for k in ("id",):
            d[k] = str(d[k])
        if isinstance(d.get("created_at"), datetime):
            d["created_at"] = d["created_at"].isoformat()
        out.append(d)
    return out


async def list_turns(session_id: str, limit: int = 200) -> list[dict[str, Any]]:
    """The transactions of one session: one row per turn with its first input, last
    output, stages touched and whether anything failed."""
    if not enabled():
        return []
    from ..database import _get_engine

    sql = f"""
        SELECT turn_id,
               min(created_at) AS started_at, max(created_at) AS ended_at,
               count(*) AS rows, bool_and(ok) AS all_ok,
               array_agg(DISTINCT agent || '/' || stage) AS stages,
               (array_agg(summary ORDER BY created_at ASC)
                  FILTER (WHERE direction = 'input' AND agent = 'orchestrator'))[1] AS first_input,
               (array_agg(summary ORDER BY created_at DESC)
                  FILTER (WHERE direction = 'output' AND agent = 'orchestrator'))[1] AS final_output,
               sum(coalesce(input_tokens, 0)) AS input_tokens,
               sum(coalesce(output_tokens, 0)) AS output_tokens
        FROM {TABLE}
        WHERE session_id = :sid
        GROUP BY turn_id ORDER BY min(created_at) ASC LIMIT :lim
    """
    async with _get_engine().connect() as conn:
        rows = (await conn.execute(text(sql), {"sid": session_id, "lim": max(1, min(int(limit), 2000))})).mappings().all()
    out = []
    for r in rows:
        d = dict(r)
        for k in ("started_at", "ended_at"):
            if isinstance(d.get(k), datetime):
                d[k] = d[k].isoformat()
        out.append(d)
    return out


async def recent_sessions(limit: int = 50) -> list[dict[str, Any]]:
    """Sessions with activity, newest first — the entry point when you only know 'yesterday'."""
    if not enabled():
        return []
    from ..database import _get_engine

    sql = f"""
        SELECT session_id, min(created_at) AS started_at, max(created_at) AS last_at,
               count(*) AS rows, bool_and(ok) AS all_ok,
               count(DISTINCT turn_id) AS turns,
               array_agg(DISTINCT agent) AS agents
        FROM {TABLE}
        WHERE session_id IS NOT NULL
        GROUP BY session_id ORDER BY max(created_at) DESC LIMIT :lim
    """
    async with _get_engine().connect() as conn:
        rows = (await conn.execute(text(sql), {"lim": max(1, min(int(limit), 500))})).mappings().all()
    out = []
    for r in rows:
        d = dict(r)
        for k in ("started_at", "last_at"):
            if isinstance(d.get(k), datetime):
                d[k] = d[k].isoformat()
        out.append(d)
    return out
