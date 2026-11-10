"""The Trace tab (9 Nov 2026): one recorded answer as Langfuse shows it - a nested tree of every step, each with
its type, time, cost, input and output - for the question page in Trace Lab.

The steps come from Hoist Traces (agent_trace_turns / agent_trace_spans), which Trace Lab already reads, never
from Langfuse. The rules come from the Langfuse export itself (src/agents/langfuse_export.py: which steps are
model calls, which are agents and tools, how a step with no input or output of its own is filled), so the tab
and Langfuse show the same tree by construction. Every value is redacted the way the rest of Trace Lab is.

This module only reads. The tree is built in memory from rows read through conversations._read() (the main
database, READ ONLY) and conversations._lab_read() (Trace Lab's own evidence copies).
"""
from __future__ import annotations

import gzip
import json
from collections.abc import Mapping, Sequence
from typing import Any

from fastapi.encoders import jsonable_encoder
from fastapi.responses import Response
from sqlalchemy import text

from src.agents import langfuse_export as lf

from . import conversations, labelling, normalize, runs

TURN_ID_MAX = 200


def _sid(v: Any) -> str | None:
    return None if v is None else str(v)


def _cut(v: Any) -> bool:
    """Hoist Traces keeps a value over its bound as {"_truncated": "<the JSON text, cut>"}."""
    return isinstance(v, Mapping) and set(v) == {"_truncated"} and isinstance(v.get("_truncated"), str)


def _list(v: Any) -> list[str]:
    v = lf._jsonable(v)
    return [str(x) for x in v] if isinstance(v, list) else []


def _error(v: Any) -> str | None:
    return normalize.redact_text(str(v)[:2000]) if v else None


def _header(turn: Mapping[str, Any]) -> dict[str, Any]:
    turn_id = str(turn["turn_id"])
    tin = lf._int(turn.get("input_tokens")) or 0
    tout = lf._int(turn.get("output_tokens")) or 0
    tcache = lf._int(turn.get("cache_read_tokens")) or 0
    return {"turn_id": turn_id, "langfuse_trace_id": lf.otel_trace_id(turn_id),
            "session_id": _sid(turn.get("session_id")), "email": turn.get("email"), "route": turn.get("route"),
            "models": _list(turn.get("models")),
            "started_at": lf._iso(turn.get("started_at")), "ended_at": lf._iso(turn.get("ended_at")),
            "latency_ms": lf._int(turn.get("latency_ms")), "cost_usd": lf._num(turn.get("cost_usd")),
            "cost_complete": turn.get("cost_complete") is not False,
            "input_tokens": tin, "output_tokens": tout, "cache_read_tokens": tcache,
            "total_tokens": tin + tout + tcache,
            "ok": turn.get("ok") is not False, "error": _error(turn.get("error"))}


def _root(turn: Mapping[str, Any], span: Mapping[str, Any] | None, root_id: str) -> dict[str, Any]:
    """The trace itself, as Langfuse's root: named chat, the question in, the answer out, the turn's totals."""
    src = span or {}
    failed = turn.get("ok") is False or bool(turn.get("error"))
    latency = lf._int(turn.get("latency_ms"))
    return {"id": root_id, "parent_id": None, "depth": 0, "seq": lf._int(src.get("seq")), "kind": "turn",
            "type": "agent", "name": lf.TRACE_NAME, "agent": src.get("agent"), "model": None,
            "started_at": lf._iso(turn.get("started_at") or src.get("started_at")),
            "ended_at": lf._iso(turn.get("ended_at") or src.get("ended_at")),
            "latency_ms": latency if latency is not None else lf._int(src.get("latency_ms")),
            "cost_usd": lf._num(turn.get("cost_usd")),
            "input_tokens": lf._int(turn.get("input_tokens")), "output_tokens": lf._int(turn.get("output_tokens")),
            "cache_read_tokens": lf._int(turn.get("cache_read_tokens")),
            "ok": not failed, "error": _error(turn.get("error")), "level": "ERROR" if failed else "DEFAULT",
            "input": normalize.redact(turn.get("question") or None),
            "output": normalize.redact(turn.get("answer") or None),
            "filled": {}, "truncated": False}


def _node(s: Mapping[str, Any], parent: str, depth: int, filled: tuple[Any, Any, dict[str, Any]]) -> dict[str, Any]:
    inp, out, notes = filled
    kind = str(s.get("kind") or "span")
    gen = lf._is_generation(dict(s))
    failed = lf._failed(dict(s))
    return {"id": str(s["id"]), "parent_id": parent, "depth": depth, "seq": lf._int(s.get("seq")), "kind": kind,
            "type": "generation" if gen else lf.OBSERVATION_TYPES.get(kind, "span"),
            "name": str(s.get("name") or kind)[:200], "agent": s.get("agent"),
            "model": s.get("model") if gen else None,
            "started_at": lf._iso(s.get("started_at")), "ended_at": lf._iso(s.get("ended_at")),
            "latency_ms": lf._int(s.get("latency_ms")),
            "cost_usd": lf._num(s.get("cost_usd")) if gen else None,
            "input_tokens": lf._int(s.get("input_tokens")) if gen else None,
            "output_tokens": lf._int(s.get("output_tokens")) if gen else None,
            "cache_read_tokens": lf._int(s.get("cache_read_tokens")) if gen else None,
            "ok": not failed, "error": _error(s.get("error")), "level": "ERROR" if failed else "DEFAULT",
            "input": normalize.redact(inp), "output": normalize.redact(out), "filled": dict(notes),
            "truncated": _cut(s.get("input")) or _cut(s.get("output"))}


def build_tree(turn: Mapping[str, Any], spans: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """One turn row and its span rows as {"turn": header, "nodes": [...], "purged": bool}.

    Nodes are depth-first, parent before children, siblings by seq; one root (the turn span, or one made from the
    turn row when its spans were purged). A span whose parent is unknown hangs from the root, as in the export."""
    t = dict(turn)
    ordered = lf._ordered([dict(s) for s in spans])
    root = next((s for s in ordered if s.get("kind") == "turn"), None)
    root_id = str(root["id"]) if root else "root"
    known = {str(s["id"]) for s in ordered}
    io = lf.filled_io(t, ordered)
    kids: dict[str, list[dict[str, Any]]] = {}
    for s in ordered:
        if s is root:
            continue
        p = _sid(s.get("parent_id"))
        kids.setdefault(p if p and p in known and p != str(s["id"]) else root_id, []).append(s)

    nodes = [_root(t, root, root_id)]
    seen = {root_id}

    def walk(pid: str, depth: int) -> None:
        for s in kids.get(pid, []):
            sid = str(s["id"])
            if sid in seen:
                continue
            seen.add(sid)
            nodes.append(_node(s, pid, depth, io[sid]))
            walk(sid, depth + 1)

    walk(root_id, 1)
    for s in ordered:                         # a parent cycle never reaches the root: shown under it
        if s is not root and str(s["id"]) not in seen:
            seen.add(str(s["id"]))
            nodes.append(_node(s, root_id, 1, io[str(s["id"])]))
            walk(str(s["id"]), 2)
    return {"turn": _header(t), "nodes": nodes, "purged": not ordered}


# ── reading ───────────────────────────────────────────────────────────────────────────────

async def _raw(principal: Any, org: str, turn_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]] | None:
    """One turn row and its spans as stored, or None when the caller's company view can't read it. A golden or
    scenario turn is read from Trace Lab's own evidence copies (by company); a live one from Hoist Traces."""
    if not isinstance(turn_id, str) or not turn_id or len(turn_id) > TURN_ID_MAX:
        return None
    if conversations.is_golden_turn(turn_id) or conversations.is_scenario_turn(turn_id):
        async with conversations._lab_read() as conn:
            turn = (await conn.execute(text(
                "SELECT turn FROM tracelab.evidence_turns WHERE turn_id = :t AND organization_id = CAST(:org AS uuid)"),
                {"t": turn_id, "org": org})).scalar()
            if turn is None:
                return None
            spans = [dict(conversations._jsonb(s)) for (s,) in (await conn.execute(text(
                "SELECT span FROM tracelab.evidence_spans WHERE turn_id = :t ORDER BY seq"), {"t": turn_id})).all()]
        return {**dict(conversations._jsonb(turn)), "turn_id": turn_id}, spans
    scope, p = conversations.scope_sql(principal, org)
    p["tid"] = turn_id
    async with conversations._read() as conn:
        turn = (await conn.execute(text(
            f"SELECT tt.* FROM {conversations.TURNS} tt WHERE {scope} AND tt.turn_id = :tid"), p)).mappings().first()
        if turn is None:
            return None
        spans = [dict(s) for s in (await conn.execute(text(
            f"SELECT * FROM {conversations.SPANS} WHERE turn_id = :tid ORDER BY seq"), {"tid": turn_id})).mappings().all()]
    return dict(turn), spans


async def question_tree(m: labelling.Member, code: str, *, turn_id: str | None = None) -> dict[str, Any]:
    """The Trace tab of one question: its chosen answer (the linked `turn_id`, else the newest linked answer, as the
    question page picks it) as {"turn_id", "missing", "tree"}. The access is the question page's own: an active
    member, a question in a run the caller may see, the answer read in that answer's company view."""
    labelling.require_active(m)
    async with conversations._lab_read() as conn:
        run, row = await runs._question_row(conn, m, code)
        links = [dict(r) for r in (await conn.execute(text(
            "SELECT turn_id, organization_id::text AS organization_id FROM tracelab.lab_question_traces "
            "WHERE question_id = CAST(:qid AS uuid) ORDER BY created_at DESC, turn_id"), {"qid": row["id"]})).mappings().all()]
    pick = next((t for t in links if t["turn_id"] == turn_id), links[0] if links else None)
    if pick is None:
        return {"turn_id": None, "missing": False, "tree": None}
    mm = labelling.for_org(m, pick["organization_id"] or row.get("organization_id") or run.get("organization_id"))
    got = await _raw(labelling.company_view(mm.principal, mm.org), mm.org, pick["turn_id"])
    if got is None:
        return {"turn_id": pick["turn_id"], "missing": True, "tree": None}
    return {"turn_id": pick["turn_id"], "missing": False, "tree": build_tree(*got)}


#: Below this a compressed answer saves too little to be worth the work.
_GZIP_FROM = 2048


def json_answer(payload: Mapping[str, Any], accept_encoding: str | None) -> Response:
    """The /tree answer as JSON, gzipped when the browser accepts gzip (10 Nov 2026).

    The tree carries every step's input and output, so a long answer is megabytes of JSON that
    compresses about tenfold. Production's nginx compresses nothing, and switching it on there would
    change every response of the app; this compresses only this route's answer.
    """
    body = json.dumps(jsonable_encoder(payload), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    accepts = any(p.split(";")[0].strip().lower() == "gzip" for p in str(accept_encoding or "").split(","))
    if accepts and len(body) >= _GZIP_FROM:
        return Response(gzip.compress(body, compresslevel=5), media_type="application/json",
                        headers={"Content-Encoding": "gzip", "Vary": "Accept-Encoding"})
    return Response(body, media_type="application/json", headers={"Vary": "Accept-Encoding"})
