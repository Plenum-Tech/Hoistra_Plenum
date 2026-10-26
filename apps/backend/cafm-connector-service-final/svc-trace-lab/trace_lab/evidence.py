"""Evidence snapshots: a copy of a chat turn and its spans in Trace Lab's own database (G0 addendum 2.6).

svc-deepagents deletes a turn's spans after 90 days. Whenever Trace Lab relies on a turn - it is drawn into a
review batch, annotated, labelled, proposed for, judged or monitored - the caller first calls
snapshot(turn_ids, organization_id=..., reason=...), which copies the turn row and all its span rows into
tracelab.evidence_turns and tracelab.evidence_spans. load() is how readers fetch evidence afterwards: the live
row in the chat's tables while the chat still has the turn and its spans, otherwise the snapshot.

Two databases, never one SQL statement across them:

* The turn and span rows are READ from the application database through db.main_engine() (the read-only
  engine), in transactions that start with SET TRANSACTION READ ONLY, always restricted to one company. A turn
  of another company is never read into a snapshot or returned by load(); a missing or purged turn is skipped.
* They are WRITTEN only to Trace Lab's own database through db.lab_engine(), with INSERT ... ON CONFLICT DO
  NOTHING: the evidence tables are append-only, a second snapshot copies nothing, and the first capture stands.

Rows are copied as the chat stored them - nothing is truncated or redacted here - as plain JSON (datetimes as
ISO text, UUIDs as text, numerics as numbers), the same shape for a live row and a snapshot row, so a reader
cannot tell them apart except by EvidenceTurn.source. A turn whose spans were already purged when it is first
captured is kept without spans (the turn row itself stays in the chat).
"""
from __future__ import annotations

import json
import math
import uuid
from collections.abc import Iterable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, AsyncIterator

import structlog
from sqlalchemy import text

from src.agents import trace

from . import db

log = structlog.get_logger(__name__)

TURNS = trace.TURNS
SPANS = trace.SPANS
CHUNK = 25                      # turns read from the chat per query: spans can be large
MAX_ID_LENGTH = 200             # evidence_turns.turn_id CHECK
MAX_REASON_LENGTH = 40          # evidence_turns.reason CHECK
SOURCES = ("live", "snapshot")


@dataclass(frozen=True)
class EvidenceTurn:
    """A turn row and its spans (in sequence order), as plain JSON, and where they came from."""

    turn: dict[str, Any]
    spans: list[dict[str, Any]]
    source: str                 # "live" | "snapshot"


# ── shapes ───────────────────────────────────────────────────────────────────────────────

def _default(o: Any) -> Any:
    if isinstance(o, datetime):
        return (o if o.tzinfo else o.replace(tzinfo=timezone.utc)).isoformat()
    if isinstance(o, date):
        return o.isoformat()
    if isinstance(o, uuid.UUID):
        return str(o)
    if isinstance(o, Decimal):
        f = float(o)
        return f if math.isfinite(f) else None
    raise TypeError(f"{type(o).__name__} is not JSON serialisable")


def plain(row: Mapping[str, Any]) -> dict[str, Any]:
    """A database row as plain JSON values (jsonb columns are already Python JSON). Text is never shortened."""
    return json.loads(json.dumps(dict(row), default=_default))


def _jsonb(v: Any) -> Any:
    return json.loads(v) if isinstance(v, str) else v


def _span_order(s: Mapping[str, Any]) -> tuple[int, str]:
    return (int(s.get("seq") or 0), str(s.get("id")))


def _org(organization_id: Any) -> str:
    try:
        return str(uuid.UUID(str(organization_id)))
    except (ValueError, TypeError, AttributeError):
        raise ValueError("a company id is required") from None


def _clean_ids(turn_ids: Iterable[Any]) -> list[str]:
    """The usable ids, once each, in the order given. Anything that cannot be a stored turn id is dropped."""
    return list(dict.fromkeys(t for t in (turn_ids or ()) if isinstance(t, str) and 1 <= len(t) <= MAX_ID_LENGTH))


def _chunks(items: Sequence[str], n: int = CHUNK) -> Iterable[list[str]]:
    for i in range(0, len(items), n):
        yield list(items[i:i + n])


# ── reads ────────────────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def _read_main() -> AsyncIterator[Any]:
    """A read-only, 10-second-bounded transaction on the read-only main engine."""
    async with db.main_engine().begin() as conn:
        await conn.execute(text("SET TRANSACTION READ ONLY"))
        await conn.execute(text("SET LOCAL statement_timeout = '10s'"))
        yield conn


@asynccontextmanager
async def _read_lab() -> AsyncIterator[Any]:
    """A read-only, 10-second-bounded transaction on Trace Lab's own database."""
    async with db.lab_engine().begin() as conn:
        await conn.execute(text("SET TRANSACTION READ ONLY"))
        await conn.execute(text("SET LOCAL statement_timeout = '10s'"))
        yield conn


async def _live(ids: list[str], org: str) -> dict[str, tuple[dict[str, Any], list[dict[str, Any]]]]:
    """The company's turns among ids, with their spans, from the chat's tables (one chunk, one transaction). The
    spans are read only for turns the company owns."""
    async with _read_main() as conn:
        rows = (await conn.execute(text(
            f"SELECT * FROM {TURNS} WHERE organization_id = CAST(:org AS uuid) AND turn_id = ANY(CAST(:ids AS text[]))"),
            {"org": org, "ids": ids})).mappings().all()
        owned = [r["turn_id"] for r in rows]
        span_rows = (await conn.execute(text(
            f"SELECT * FROM {SPANS} WHERE turn_id = ANY(CAST(:ids AS text[])) ORDER BY turn_id, seq, id"),
            {"ids": owned})).mappings().all() if owned else []
    spans: dict[str, list[dict[str, Any]]] = {t: [] for t in owned}
    for s in span_rows:
        spans[s["turn_id"]].append(plain(s))
    return {r["turn_id"]: (plain(r), sorted(spans[r["turn_id"]], key=_span_order)) for r in rows}


async def _captured(ids: list[str]) -> set[str]:
    """Which of ids already have a snapshot (any company: a turn id is stored once)."""
    async with _read_lab() as conn:
        rows = (await conn.execute(text(
            "SELECT turn_id FROM tracelab.evidence_turns WHERE turn_id = ANY(CAST(:ids AS text[]))"),
            {"ids": ids})).mappings().all()
    return {r["turn_id"] for r in rows}


async def _snapshots(ids: list[str], org: str) -> dict[str, tuple[dict[str, Any], list[dict[str, Any]]]]:
    """The company's snapshots among ids, with their spans in sequence order."""
    async with _read_lab() as conn:
        rows = (await conn.execute(text(
            "SELECT turn_id, turn FROM tracelab.evidence_turns"
            " WHERE organization_id = CAST(:org AS uuid) AND turn_id = ANY(CAST(:ids AS text[]))"),
            {"org": org, "ids": ids})).mappings().all()
        owned = [r["turn_id"] for r in rows]
        span_rows = (await conn.execute(text(
            "SELECT turn_id, span_id, seq, span FROM tracelab.evidence_spans WHERE turn_id = ANY(CAST(:ids AS text[]))"
            " ORDER BY turn_id, seq, span_id"), {"ids": owned})).mappings().all() if owned else []
    spans: dict[str, list[dict[str, Any]]] = {t: [] for t in owned}
    for s in span_rows:
        spans[s["turn_id"]].append(dict(_jsonb(s["span"])))
    return {r["turn_id"]: (dict(_jsonb(r["turn"])), sorted(spans[r["turn_id"]], key=_span_order)) for r in rows}


async def load(turn_ids: Sequence[str], *, organization_id: str) -> dict[str, EvidenceTurn]:
    """Evidence for each id the company can see: the live chat row and spans when the chat still has the turn
    with its spans (source "live"), else the snapshot (source "snapshot"), else absent from the result. A turn
    the chat still has but whose spans are purged comes from the snapshot when there is one. Rows of another
    company are never returned."""
    org = _org(organization_id)
    ids = _clean_ids(turn_ids)
    live: dict[str, tuple[dict[str, Any], list[dict[str, Any]]]] = {}
    for chunk in _chunks(ids):
        live.update(await _live(chunk, org))
    out: dict[str, EvidenceTurn] = {t: EvidenceTurn(turn, spans, "live") for t, (turn, spans) in live.items() if spans}
    rest = [t for t in ids if t not in out]
    snaps = await _snapshots(rest, org) if rest else {}
    for tid in rest:
        snap, row = snaps.get(tid), live.get(tid)
        if snap is not None and (snap[1] or row is None):
            out[tid] = EvidenceTurn(snap[0], snap[1], "snapshot")
        elif row is not None:
            out[tid] = EvidenceTurn(row[0], row[1], "live")
    return {t: out[t] for t in ids if t in out}


# ── snapshot ─────────────────────────────────────────────────────────────────────────────

_INSERT_TURN = ("INSERT INTO tracelab.evidence_turns (turn_id, organization_id, turn, reason)"
                " VALUES (:t, CAST(:o AS uuid), CAST(:j AS jsonb), :r) ON CONFLICT (turn_id) DO NOTHING RETURNING turn_id")
_INSERT_SPAN = ("INSERT INTO tracelab.evidence_spans (turn_id, span_id, seq, span)"
                " VALUES (:t, :s, :q, CAST(:j AS jsonb)) ON CONFLICT (turn_id, span_id) DO NOTHING")


async def _store(live: Mapping[str, tuple[dict[str, Any], list[dict[str, Any]]]], org: str, reason: str) -> int:
    """Insert each turn and, only for a turn this call really inserted, its spans: one transaction, so a turn
    is never stored without the spans that were read with it. Returns the turns newly stored."""
    stored = 0
    async with db.lab_engine().begin() as conn:
        await conn.execute(text("SET LOCAL statement_timeout = '30s'"))
        for tid, (turn, spans) in live.items():
            res = await conn.execute(text(_INSERT_TURN), {"t": tid, "o": org, "j": json.dumps(turn), "r": reason})
            if not res.mappings().all():            # another caller captured it first: theirs stands
                continue
            stored += 1
            if spans:
                await conn.execute(text(_INSERT_SPAN), [
                    {"t": tid, "s": str(s["id"]), "q": max(0, int(s.get("seq") or 0)), "j": json.dumps(s)}
                    for s in spans])
    return stored


async def snapshot(turn_ids: Sequence[str], *, organization_id: str, reason: str) -> int:
    """Copy each turn (and all its spans) of the company that has no snapshot yet from the chat into Trace
    Lab's evidence tables. A turn of another company, a missing turn or a purged one is skipped; one already
    captured is not read again. Idempotent. `reason` (1-40 characters: sampled, annotated, labelled, judged ...)
    is kept with the first capture. Returns how many turns were newly captured. Raises when a database fails:
    the caller must not go on relying on a turn whose evidence was not kept."""
    org = _org(organization_id)
    if not isinstance(reason, str) or not 1 <= len(reason) <= MAX_REASON_LENGTH:
        raise ValueError(f"reason must be 1 to {MAX_REASON_LENGTH} characters")
    ids = _clean_ids(turn_ids)
    if not ids:
        return 0
    have = await _captured(ids)
    todo = [t for t in ids if t not in have]
    captured = 0
    for chunk in _chunks(todo):
        live = await _live(chunk, org)
        if live:
            captured += await _store(live, org, reason)
    log.info("trace_lab.evidence.snapshot", asked=len(ids), already=len(ids) - len(todo), captured=captured, reason=reason)
    return captured
