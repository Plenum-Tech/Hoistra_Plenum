"""Hoist Traces in Langfuse - every chat turn sent to the self-hosted Langfuse, in Langfuse's own model.

Asked for by Aasim (9 Nov 2026): he reads conversations in Langfuse, so each turn Hoist Traces stores
(src/agents/trace.py) is also sent there, mapped one to one:

    a conversation (session_id)            -> a Langfuse session
    a turn (agent_trace_turns row)         -> a trace named "chat": question in, answer out, who asked,
                                              route and space as tags, company and totals as metadata;
                                              its root observation is the orchestrator (type agent)
    an llm / router span                   -> a generation: model, tokens, dollars, start and end
                                              (only when it carries a model, tokens or a cost - a
                                              model stage without its own call is a span)
    an agent span                          -> an agent observation
    a tool span                            -> a tool observation
    a stage / db / plan / step span        -> a span
    every one of them                      -> nested under its parent, as in Hoist Traces
    a span that failed                     -> level ERROR with its error as the status message

How it is sent. Langfuse v3 and v4 take traces over OpenTelemetry (POST {host}/api/public/otel/v1/traces,
OTLP as JSON) and v4 refuses anything else - so that is the way, built by hand: no SDK, no new
dependency. OpenTelemetry ids are hex of a fixed length, so the trace id is derived from the turn id
(the same turn always lands on the same trace; the turn id is in the metadata as hoistra_turn_id).
A Langfuse with no OpenTelemetry route (v2, it answers 404) gets the older ingestion API instead
(POST {host}/api/public/ingestion), where the trace id IS the turn id. Both with basic auth: the
project's public and secret keys.

It is off unless LANGFUSE_HOST (or LANGFUSE_BASE_URL) and both keys are set. It runs after the turn
has ended, in the background, and never raises: a Langfuse that is slow, down or refusing only writes
a warning to the log. Langfuse gets exactly the turns Hoist Traces stores - a skill-lab replay or a
Trace Lab rerun is not a chat and is not sent. Past conversations: scripts/langfuse_backfill.py.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import httpx
import structlog

log = structlog.get_logger(__name__)

OTEL_PATH = "/api/public/otel/v1/traces"
INGESTION_PATH = "/api/public/ingestion"
#: Events (or spans) per request, and the bytes one request may carry (Langfuse refuses ~4.5 MB).
BATCH_EVENTS = 50
BATCH_BYTES = 3_000_000
TIMEOUT_S = 15.0
#: Span kinds that are a model call - a Langfuse generation. Everything else is a span.
GENERATION_KINDS = ("llm", "router")
#: Hoist Traces span kind -> Langfuse observation type (OpenTelemetry path).
#: (llm and router spans are generations only when they carry a model call - see _is_generation)
OBSERVATION_TYPES = {"agent": "agent", "tool": "tool", "turn": "agent"}
TRACE_NAME = "chat"
SERVICE_NAME = "hoistra-deepagents"

_pending: set[asyncio.Task] = set()


def settings() -> dict[str, Any] | None:
    """The Langfuse to send to, or None when it is not configured (then nothing is sent)."""
    host = (os.environ.get("LANGFUSE_HOST") or os.environ.get("LANGFUSE_BASE_URL") or "").strip().rstrip("/")
    public = (os.environ.get("LANGFUSE_PUBLIC_KEY") or "").strip()
    secret = (os.environ.get("LANGFUSE_SECRET_KEY") or "").strip()
    if not (host and public and secret):
        return None
    return {"host": host, "public": public, "secret": secret,
            "environment": _environment(os.environ.get("LANGFUSE_ENVIRONMENT")),
            "release": (os.environ.get("LANGFUSE_RELEASE") or "").strip() or None}


def enabled() -> bool:
    return settings() is not None


def _environment(v: str | None) -> str | None:
    """Langfuse accepts lower-case letters, digits, '-' and '_' (at most 40), not starting 'langfuse'."""
    s = "".join(c for c in (v or "").strip().lower() if c.isalnum() or c in "-_")[:40]
    return s if s and not s.startswith("langfuse") else None


def _iso(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, str):
        try:
            v = datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            return v
    if not isinstance(v, datetime):
        return None
    if v.tzinfo is None:
        v = v.replace(tzinfo=timezone.utc)
    return v.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _num(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _int(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _jsonable(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, str):
        try:
            return json.loads(v)       # a JSONB column read as text
        except ValueError:
            return v
    try:
        return json.loads(json.dumps(v, default=lambda x: float(x) if isinstance(x, Decimal) else str(x)))
    except (TypeError, ValueError):
        return str(v)


def _clean(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if v is not None}


def _event(kind: str, body: dict[str, Any]) -> dict[str, Any]:
    return {"id": str(uuid.uuid4()), "type": kind, "timestamp": _iso(datetime.now(timezone.utc)), "body": body}


def batch_for(turn: dict[str, Any], spans: list[dict[str, Any]], *, environment: str | None = None,
              release: str | None = None) -> list[dict[str, Any]]:
    """One turn as Langfuse ingestion events: its trace, then its observations in sequence order.

    `turn` and `spans` are shaped like agent_trace_turns / agent_trace_spans rows (the live hook builds
    the same dicts from the Turn in memory), plus an optional `instance` - the space it was asked in."""
    trace_id = str(turn["turn_id"])
    ordered = _ordered(spans)
    root_ids = {str(s["id"]) for s in ordered if s.get("kind") == "turn"}
    known = {str(s["id"]) for s in ordered}
    trace_body = _clean({
        "id": trace_id, "timestamp": _iso(turn.get("started_at")), "name": TRACE_NAME,
        "sessionId": turn.get("session_id"), "userId": _user(turn),
        "input": turn.get("question") or None, "output": turn.get("answer") or None,
        "tags": _tags(turn), "environment": environment, "release": release,
        "metadata": _trace_metadata(turn, ordered),
    })
    events = [_event("trace-create", trace_body)]
    io = filled_io(turn, ordered)

    for s in ordered:
        if str(s["id"]) in root_ids:
            continue
        kind = str(s.get("kind") or "span")
        parent = _str(s.get("parent_id"))
        inp, out, notes = io[str(s["id"])]
        body = _clean({
            "id": str(s["id"]), "traceId": trace_id,
            "parentObservationId": parent if parent and parent in known and parent not in root_ids else None,
            "name": str(s.get("name") or kind)[:200],
            "startTime": _iso(s.get("started_at")), "endTime": _iso(s.get("ended_at")),
            "input": inp, "output": out,
            "level": "ERROR" if _failed(s) else "DEFAULT",
            "statusMessage": str(s["error"])[:2000] if s.get("error") else None,
            "environment": environment, "version": release,
            "metadata": {**_span_metadata(s), **notes},
        })
        gen = _is_generation(s)
        if gen:
            details, usd = _usage(s)
            usage = dict(details)
            if details:
                usage.update(total=(details.get("input") or 0) + (details.get("output") or 0), unit="TOKENS")
            if usd is not None:
                usage["totalCost"] = usd
            body.update(_clean({"model": s.get("model"), "usage": usage or None, "usageDetails": details or None,
                                "costDetails": {"total": usd} if usd is not None else None}))
        events.append(_event("generation-create" if gen else "span-create", body))
    return events


# ── what both ways share ──────────────────────────────────────────────────────────────

def _ordered(spans: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return sorted(spans or [], key=lambda s: _int(s.get("seq")) or 0)


def _user(turn: dict[str, Any]) -> str | None:
    return turn.get("email") or _str(turn.get("user_id"))


def _tags(turn: dict[str, Any]) -> list[str]:
    route, instance = turn.get("route"), turn.get("instance")
    return [t for t in (f"route:{route}" if route else None, f"instance:{instance}" if instance else None,
                        "error" if turn.get("ok") is False else None) if t]


def _is_generation(s: dict[str, Any]) -> bool:
    """A model call: an llm or router span that carries a model, tokens or a cost. A model stage with none
    of them (the compliance "plan" stage stays open for the whole turn; its call is a span of its own)
    is a span, or Langfuse shows it as a model call as long as the turn."""
    return str(s.get("kind") or "") in GENERATION_KINDS and any(
        s.get(k) is not None and s.get(k) != "" for k in ("model", "input_tokens", "output_tokens", "cost_usd"))


def _failed(s: dict[str, Any]) -> bool:
    return s.get("ok") is False or bool(s.get("error"))


def _shown(v: Any) -> Any:
    """A value as Langfuse should show it. Hoist Traces keeps a payload over its bound as
    {"_truncated": "<the JSON text, cut>"}; Langfuse showed that as one escaped string, so it is
    shown as the text itself, marked as cut."""
    v = _jsonable(v)
    if isinstance(v, dict) and set(v) == {"_truncated"} and isinstance(v["_truncated"], str):
        return v["_truncated"] + " … [cut: Hoist Traces keeps 16,000 characters]"
    return v


def filled_io(turn: dict[str, Any], ordered: list[dict[str, Any]]) -> dict[str, tuple[Any, Any, dict[str, Any]]]:
    """Every step's input and output for Langfuse, so none shows "undefined" (9 Nov 2026).

    Hoist Traces records some steps with no input or no output of their own: an agent span exists only
    to group its steps, a stage like `fetch` gets only its output row, a stage like `plan` stays open
    while its model call is a separate span, a failed tool returns nothing. What fills the gap, in order:

      input   an agent: the question of the turn
              a step with steps of its own: the first of them that has an input
              else: the output of the step before it under the same parent (what it worked from)
              else: the question of the turn
      output  a failed step: its error
              a step with steps of its own: the last of them that has an output
              else: the output of a later step of the same name under the same parent (its model call)
              else: what is known of it - finished or failed, and how long it took

    Anything filled is named in the step's metadata (input_from / output_from), so it is never mistaken
    for what the step itself recorded."""
    question = turn.get("question") or None
    kids: dict[str, list[dict[str, Any]]] = {}
    for s in ordered:
        kids.setdefault(str(s.get("parent_id")), []).append(s)
    out: dict[str, tuple[Any, Any, dict[str, Any]]] = {}
    for s in ordered:
        sid, kind, notes = str(s["id"]), str(s.get("kind") or "span"), {}
        inp, res = _shown(s.get("input")), _shown(s.get("output"))
        children = [c for c in kids.get(sid, []) if c is not s]
        siblings = [c for c in kids.get(str(s.get("parent_id")), []) if c is not s]
        seq = _int(s.get("seq")) or 0
        if inp is None:
            first = next((c for c in children if c.get("input") is not None), None)
            before = [c for c in siblings if (_int(c.get("seq")) or 0) < seq and c.get("output") is not None]
            if kind == "agent" and question:
                inp, notes["input_from"] = question, "the question"
            elif first is not None:
                inp, notes["input_from"] = _shown(first["input"]), f"its first step ({first.get('name')})"
            elif before:
                inp, notes["input_from"] = _shown(before[-1]["output"]), f"the step before ({before[-1].get('name')})"
            elif question:
                inp, notes["input_from"] = question, "the question"
        if res is None:
            last = next((c for c in reversed(children) if c.get("output") is not None), None)
            same = next((c for c in siblings if (_int(c.get("seq")) or 0) > seq and c.get("name") == s.get("name")
                         and c.get("output") is not None), None)
            if s.get("error"):
                res, notes["output_from"] = {"error": str(s["error"])[:2000]}, "its error"
            elif last is not None:
                res, notes["output_from"] = _shown(last["output"]), f"its last step ({last.get('name')})"
            elif same is not None:
                res, notes["output_from"] = _shown(same["output"]), f"its model call ({same.get('name')})"
            else:
                res = _clean({"status": "failed" if _failed(s) else "finished", "latency_ms": _int(s.get("latency_ms"))})
                notes["output_from"] = "no output was recorded"
        out[sid] = (inp, res, notes)
    return out


def _trace_metadata(turn: dict[str, Any], ordered: list[dict[str, Any]]) -> dict[str, Any]:
    root = next((s for s in ordered if s.get("kind") == "turn"), None)
    root_in = _jsonable(root.get("input")) if root else None
    return _clean({
        "hoistra_turn_id": str(turn["turn_id"]),
        "organization_id": _str(turn.get("organization_id")), "user_id": _str(turn.get("user_id")),
        "route": turn.get("route"), "instance": turn.get("instance"),
        "mode": root_in.get("mode") if isinstance(root_in, dict) else None,
        "ended_at": _iso(turn.get("ended_at")), "latency_ms": _int(turn.get("latency_ms")),
        "llm_calls": _int(turn.get("llm_calls")), "tool_calls": _int(turn.get("tool_calls")),
        "cost_usd": _num(turn.get("cost_usd")), "cost_complete": turn.get("cost_complete"),
        "input_tokens": _int(turn.get("input_tokens")), "output_tokens": _int(turn.get("output_tokens")),
        "cache_read_tokens": _int(turn.get("cache_read_tokens")),
        "models": _jsonable(turn.get("models")), "tools": _jsonable(turn.get("tools")),
        "ok": turn.get("ok"), "error": turn.get("error"), "source": "hoistra",
    })


def _span_metadata(s: dict[str, Any]) -> dict[str, Any]:
    return _clean({"kind": str(s.get("kind") or "span"), "agent": s.get("agent"), "seq": _int(s.get("seq")),
                   "latency_ms": _int(s.get("latency_ms")), "cache_read_tokens": _int(s.get("cache_read_tokens"))})


def _usage(s: dict[str, Any]) -> tuple[dict[str, int], float | None]:
    return _clean({"input": _int(s.get("input_tokens")), "output": _int(s.get("output_tokens"))}), _num(s.get("cost_usd"))


# ── OpenTelemetry (Langfuse v3 and v4) ────────────────────────────────────────────────

def otel_trace_id(turn_id: str) -> str:
    """The OpenTelemetry trace id (32 hex) of a turn - the same turn always gives the same id."""
    return hashlib.sha256(f"hoistra-turn:{turn_id}".encode()).hexdigest()[:32]


def _span_id(turn_id: str, span_id: Any) -> str:
    return hashlib.sha256(f"hoistra-span:{turn_id}:{span_id}".encode()).hexdigest()[:16]


def _nanos(v: Any) -> str | None:
    s = _iso(v)
    if not s:
        return None
    d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return str(int(d.timestamp() * 1_000_000) * 1000)


def _attr(key: str, v: Any) -> dict[str, Any] | None:
    if v is None:
        return None
    if isinstance(v, bool):
        val: dict[str, Any] = {"boolValue": v}
    elif isinstance(v, int):
        val = {"intValue": str(v)}
    elif isinstance(v, float):
        val = {"doubleValue": v}
    elif isinstance(v, str):
        val = {"stringValue": v}
    elif isinstance(v, (list, tuple)) and all(isinstance(x, str) for x in v):
        val = {"arrayValue": {"values": [{"stringValue": x} for x in v]}}
    else:
        val = {"stringValue": json.dumps(_jsonable(v))}
    return {"key": key, "value": val}


def _attrs(pairs: dict[str, Any]) -> list[dict[str, Any]]:
    return [a for a in (_attr(k, v) for k, v in pairs.items()) if a]


def _io(v: Any) -> str | None:
    v = _jsonable(v)
    if v is None or v == "":
        return None
    return v if isinstance(v, str) else json.dumps(v)


def otel_spans_for(turn: dict[str, Any], spans: list[dict[str, Any]], *, environment: str | None = None,
                   release: str | None = None) -> list[dict[str, Any]]:
    """One turn as OpenTelemetry spans carrying Langfuse's attributes, root first.

    The root is the turn's own "turn" span (one is made from the turn row when its spans were purged);
    a span whose parent is missing hangs on the root."""
    turn_id = str(turn["turn_id"])
    trace_id = otel_trace_id(turn_id)
    ordered = _ordered(spans)
    root = next((s for s in ordered if s.get("kind") == "turn"), None)
    root_key = str(root["id"]) if root else "root"
    root_sid = _span_id(turn_id, root_key)
    known = {str(s["id"]) for s in ordered}
    io = filled_io(turn, ordered)
    t0 = _nanos(turn.get("started_at")) or _nanos((root or {}).get("started_at")) or _nanos(datetime.now(timezone.utc))
    t1 = _nanos(turn.get("ended_at")) or _nanos((root or {}).get("ended_at")) or t0
    # On every observation, so a filter on session, user or tag finds each of them (Langfuse v4).
    common = {"langfuse.session.id": turn.get("session_id"), "langfuse.user.id": _user(turn),
              "langfuse.trace.tags": _tags(turn) or None, "langfuse.environment": environment,
              "langfuse.release": release}

    root_attrs = dict(common)
    root_attrs.update({
        "langfuse.trace.name": TRACE_NAME,
        "langfuse.trace.input": turn.get("question") or None, "langfuse.trace.output": turn.get("answer") or None,
        "langfuse.observation.type": "agent",
        "langfuse.observation.input": turn.get("question") or None,
        "langfuse.observation.output": turn.get("answer") or None,
        "langfuse.observation.level": "ERROR" if turn.get("ok") is False else "DEFAULT",
        "langfuse.observation.status_message": str(turn["error"])[:2000] if turn.get("error") else None,
    })
    for k, v in _trace_metadata(turn, ordered).items():
        root_attrs[f"langfuse.trace.metadata.{k}"] = v
    out = [_otel_span(trace_id, root_sid, None, TRACE_NAME, t0, t1, root_attrs,
                      error=turn.get("error") if turn.get("ok") is False or turn.get("error") else None)]

    for s in ordered:
        if s is root:
            continue
        kind = str(s.get("kind") or "span")
        parent = _str(s.get("parent_id"))
        parent_sid = _span_id(turn_id, parent) if parent and parent in known and parent != root_key else root_sid
        a = dict(common)
        a.update({
            "langfuse.observation.type": "generation" if _is_generation(s) else OBSERVATION_TYPES.get(kind, "span"),
            "langfuse.observation.input": _io(io[str(s["id"])][0]), "langfuse.observation.output": _io(io[str(s["id"])][1]),
            "langfuse.observation.level": "ERROR" if _failed(s) else "DEFAULT",
            "langfuse.observation.status_message": str(s["error"])[:2000] if s.get("error") else None,
            "langfuse.version": release,
        })
        for k, v in {**_span_metadata(s), **io[str(s["id"])][2]}.items():
            a[f"langfuse.observation.metadata.{k}"] = v
        if _is_generation(s):
            details, usd = _usage(s)
            a.update({"langfuse.observation.model.name": s.get("model"),
                      "langfuse.observation.usage_details": json.dumps(details) if details else None,
                      "langfuse.observation.cost_details": json.dumps({"total": usd}) if usd is not None else None})
        st = _nanos(s.get("started_at")) or t0
        out.append(_otel_span(trace_id, _span_id(turn_id, s["id"]), parent_sid, str(s.get("name") or kind)[:200],
                              st, _nanos(s.get("ended_at")) or st, a, error=s.get("error") if _failed(s) else None,
                              failed=_failed(s)))
    return out


def _otel_span(trace_id: str, span_id: str, parent: str | None, name: str, t0: str, t1: str,
               attrs: dict[str, Any], *, error: Any = None, failed: bool = False) -> dict[str, Any]:
    span = {"traceId": trace_id, "spanId": span_id, "name": name, "kind": 1,
            "startTimeUnixNano": t0, "endTimeUnixNano": t1, "attributes": _attrs(attrs),
            "status": {"code": 2, "message": str(error)[:2000]} if (error or failed) else {"code": 1}}
    if parent:
        span["parentSpanId"] = parent
    return span


def otel_payload(spans: list[dict[str, Any]]) -> dict[str, Any]:
    """OTLP/JSON request body for some spans of one service."""
    return {"resourceSpans": [{
        "resource": {"attributes": _attrs({"service.name": SERVICE_NAME})},
        "scopeSpans": [{"scope": {"name": "hoistra.trace", "version": "1"}, "spans": spans}],
    }]}


def _str(v: Any) -> str | None:
    return str(v) if v is not None else None


def _transport() -> httpx.AsyncBaseTransport | None:
    """The HTTP transport; tests put a mock here."""
    return None


def _chunks(events: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    out: list[list[dict[str, Any]]] = []
    cur: list[dict[str, Any]] = []
    size = 0
    for e in events:
        n = len(json.dumps(e, default=str))
        if cur and (len(cur) >= BATCH_EVENTS or size + n > BATCH_BYTES):
            out.append(cur)
            cur, size = [], 0
        cur.append(e)
        size += n
    if cur:
        out.append(cur)
    return out


def _client(cfg: dict[str, Any]) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=_transport(), timeout=TIMEOUT_S, auth=(cfg["public"], cfg["secret"]))


async def _post_ingestion(client: httpx.AsyncClient, cfg: dict[str, Any], events: list[dict[str, Any]]) -> bool:
    accepted = True
    for chunk in _chunks(events):
        r = await client.post(cfg["host"] + INGESTION_PATH, content=json.dumps({"batch": chunk}, default=str),
                              headers={"Content-Type": "application/json"})
        if r.status_code not in (200, 201, 207):
            log.warning("langfuse.rejected", path=INGESTION_PATH, status=r.status_code, body=r.text[:300])
            return False
        errors = []
        try:
            errors = (r.json() or {}).get("errors") or []
        except ValueError:
            pass
        if errors:
            accepted = False
            log.warning("langfuse.partly_rejected", rejected=len(errors), first=str(errors[0])[:300])
    return accepted


async def _post_otel(client: httpx.AsyncClient, cfg: dict[str, Any], spans: list[dict[str, Any]]) -> bool | None:
    """True when Langfuse took every span, False when it refused, None when it has no OpenTelemetry route."""
    accepted = True
    for chunk in _chunks(spans):
        r = await client.post(cfg["host"] + OTEL_PATH, content=json.dumps(otel_payload(chunk)),
                              headers={"Content-Type": "application/json"})
        if r.status_code in (404, 405):
            return None
        if r.status_code not in (200, 201, 202, 207):
            log.warning("langfuse.rejected", path=OTEL_PATH, status=r.status_code, body=r.text[:300])
            return False
        try:
            partial = (r.json() or {}).get("partialSuccess") or {}
        except ValueError:
            partial = {}
        if int(partial.get("rejectedSpans") or 0):
            accepted = False
            log.warning("langfuse.partly_rejected", rejected=partial.get("rejectedSpans"),
                        message=str(partial.get("errorMessage"))[:300])
    return accepted


async def send_turn(turn: dict[str, Any], spans: list[dict[str, Any]], cfg: dict[str, Any] | None = None) -> bool:
    """Send one turn to Langfuse: OpenTelemetry, or the ingestion API on a Langfuse without it (v2).
    True when Langfuse took all of it; never raises."""
    cfg = cfg or settings()
    if not cfg:
        return False
    env, rel = cfg.get("environment"), cfg.get("release")
    try:
        async with _client(cfg) as client:
            ok = await _post_otel(client, cfg, otel_spans_for(turn, spans, environment=env, release=rel))
            if ok is None:
                ok = await _post_ingestion(client, cfg, batch_for(turn, spans, environment=env, release=rel))
            return ok
    except Exception as exc:  # noqa: BLE001 - Langfuse is never the turn's problem
        log.warning("langfuse.send_failed", turn_id=str(turn.get("turn_id")), error=f"{type(exc).__name__}: {str(exc)[:300]}")
        return False


async def send(events: list[dict[str, Any]], cfg: dict[str, Any] | None = None) -> bool:
    """Post ingestion-API events to Langfuse. True when every event was accepted; never raises."""
    cfg = cfg or settings()
    if not cfg or not events:
        return False
    try:
        async with _client(cfg) as client:
            return await _post_ingestion(client, cfg, events)
    except Exception as exc:  # noqa: BLE001 - Langfuse is never the turn's problem
        log.warning("langfuse.send_failed", error=f"{type(exc).__name__}: {str(exc)[:300]}")
        return False


def export_turn(t: Any, answer: str, ledger_summary: dict[str, Any] | None) -> None:
    """Send a finished live turn in the background (called by trace._flush, inside the turn's context)."""
    cfg = settings()
    if cfg is None:
        return
    try:
        from . import spaces, trace

        tot = trace.totals(t, ledger_summary)
        root = t.root
        turn = {"turn_id": t.turn_id, "session_id": t.session_id, "organization_id": t.organization_id,
                "user_id": t.user_id, "email": t.email, "question": t.question, "answer": (answer or "")[:20000],
                "route": t.route, "instance": spaces.current(), "started_at": root.started_at,
                "ended_at": root.ended_at, "latency_ms": root.latency_ms, "ok": root.ok, "error": root.error, **tot}
        spans = [{"id": s.id, "parent_id": s.parent_id, "seq": s.seq, "kind": s.kind, "name": s.name, "agent": s.agent,
                  "model": s.model, "started_at": s.started_at, "ended_at": s.ended_at, "latency_ms": s.latency_ms,
                  "input": s.input, "output": s.output, "input_tokens": s.input_tokens, "output_tokens": s.output_tokens,
                  "cache_read_tokens": s.cache_read, "cost_usd": s.cost_usd, "ok": s.ok, "error": s.error}
                 for s in t.spans]
        task = asyncio.get_running_loop().create_task(send_turn(turn, spans, cfg))
        _pending.add(task)
        task.add_done_callback(_pending.discard)
    except Exception as exc:  # noqa: BLE001
        log.warning("langfuse.export_failed", turn_id=getattr(t, "turn_id", None), error=str(exc)[:300])
