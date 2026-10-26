"""Trace Lab conversations: company-scoped, read-only SQL over svc-deepagents' trace tables, grouped into
conversations.

Golden Rule G0: Trace Lab reads plenum_cafm.agent_trace_turns, agent_trace_spans, chat_threads and
agent_activity_log and never writes them. Every read goes through db.main_engine() (the read-only main
engine: default_transaction_read_only=on) inside a transaction that also starts with SET TRANSACTION READ
ONLY and SET LOCAL statement_timeout = '10s', because the database is shared. Every query is
company-scoped with scope_sql(), which reuses svc-deepagents' own trace._scope_sql. Provenance is inferred
at read time (provenance.INFERRED_CHANNEL_SQL, provenance.classify) and labelled "inferred".
"""
from __future__ import annotations

import base64
import binascii
import json
import os
import re
from collections.abc import Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator

from sqlalchemy import text

from src.agents import trace

from . import db, normalize, provenance

TURNS = trace.TURNS
SPANS = trace.SPANS
THREADS = "plenum_cafm.chat_threads"
ACTIVITY = "plenum_cafm.agent_activity_log"
UPDATE_LOOKBACK_MINUTES = 30
MAX_WINDOW_DAYS = 366
MAX_TURN_HEADERS = 500
#: The schema version that adds the review tables (0003_review.sql): the `reviewed` filter needs them.
REVIEW_VERSION = 3
CHANNEL_SOURCES = ("recorded", "client", "inferred")
SESSION_KINDS = ("observed", "synthetic", "inferred")
REVIEWED = ("any", "reviewed", "unreviewed", "mine")
_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_KEY_RE = re.compile(r"^[str]:[^\x00-\x1f]{1,200}$")
_ALIAS_RE = re.compile(r"^[a-z_][a-z0-9_]{0,30}$")
_SCOPE_COLUMNS = re.compile(r"(?<![\w.:])(organization_id|user_id)\b")

#: Over agent_trace_turns tt. normalize.group_key / session_kind implement the same rules.
GROUP_KEY_SQL = ("CASE WHEN tt.session_id IS NULL OR btrim(tt.session_id) = '' THEN 't:' || tt.turn_id "
                 "WHEN tt.session_id LIKE 'rerun%' THEN 'r:' || tt.turn_id "
                 "ELSE 's:' || tt.session_id END")
#: Needs `th` from THREAD_JOIN.
SESSION_KIND_SQL = ("CASE WHEN tt.session_id IS NULL OR btrim(tt.session_id) = '' THEN 'inferred' "
                    "WHEN tt.session_id LIKE 'golden-%' OR tt.session_id LIKE 'report-%' OR tt.session_id LIKE 'docs-%' "
                    "OR tt.session_id LIKE 'ing-%' OR tt.session_id LIKE 'rerun%' OR tt.session_id LIKE 'skilllab-%' "
                    "THEN 'synthetic' WHEN th.id IS NOT NULL THEN 'observed' ELSE 'inferred' END")
#: A chat thread counts only inside the turn's own company, so another company's title never shows.
THREAD_JOIN = (f"LEFT JOIN {THREADS} th ON th.id = tt.session_id "
               "AND th.organization_id IS NOT DISTINCT FROM tt.organization_id")
#: The asker's words out of a wrapped REST question: the text after the last marker (orchestrator.py).
_ASKER_WORDS_SQL = r"regexp_replace(coalesce(tt.question, ''), '^.*\n\n---\n\n\*\*Current user message:\*\*\n', '')"
#: Every live turn's provenance is inferred (addendum §2.6).
_SOURCE_SQL = "CAST('inferred' AS text)"


class BadRequest(ValueError):
    """A query the routes answer with 400 and this reason."""

    def __init__(self, reason: str, error: str) -> None:
        super().__init__(error)
        self.reason = reason
        self.error = error


@dataclass(frozen=True)
class TurnFilter:
    since: datetime
    until: datetime
    run: str = "live"                         # Phase 2: only "live"; golden runs arrive in Phase 3
    channels: tuple[str, ...] = ()
    channel_sources: tuple[str, ...] = ()
    session_kinds: tuple[str, ...] = ()
    status: str | None = None                 # "ok" | "error"
    model: str | None = None
    q: str | None = None
    user: str | None = None
    reviewed: str = "any"                     # reviewed | unreviewed | mine (Phase 4 open coding) | any
    include_deleted: bool = False


@dataclass(frozen=True)
class ModelCallFilter(TurnFilter):
    agent: str | None = None
    kind: str | None = None                   # "llm" | "router"


def _aware(d: datetime | None) -> datetime | None:
    if d is None:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _iso(v: Any) -> str | None:
    if isinstance(v, datetime):
        return v.isoformat()
    return None if v is None else str(v)


def _num(v: Any) -> float | None:
    return None if v is None else float(v)


def _as_list(v: Any) -> list[Any]:
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except ValueError:
            return []
    return list(v) if isinstance(v, (list, tuple)) else []


def _csv(value: str | None, allowed: Sequence[str], name: str) -> tuple[str, ...]:
    if not value:
        return ()
    items = tuple(dict.fromkeys(x.strip() for x in str(value).split(",") if x.strip()))
    bad = [x for x in items if x not in allowed]
    if bad:
        raise BadRequest(f"bad_{name}", f"Unknown {name.replace('_', ' ')}: {bad[0][:40]}.")
    return items


def make_filter(*, since: datetime | None = None, until: datetime | None = None, run: str | None = "live",
                channel: str | None = None, channel_source: str | None = None, session_kind: str | None = None,
                status: str | None = None, model: str | None = None, q: str | None = None, user: str | None = None,
                reviewed: str = "any", deleted: str = "hide", agent: str | None = None, kind: str | None = None,
                calls: bool = False, now: datetime | None = None) -> TurnFilter:
    """The route's query parameters as a filter. Raises BadRequest with a reason the route returns."""
    now = _aware(now) or datetime.now(timezone.utc)
    start = _aware(since) or now - timedelta(days=30)
    end = _aware(until) or now + timedelta(days=1)
    if end <= start:
        raise BadRequest("bad_window", "The end of the period must be after its start.")
    if end - start > timedelta(days=MAX_WINDOW_DAYS):
        raise BadRequest("window_too_wide", "Choose a period of at most 366 days.")
    run_id = (run or "live").strip()
    if run_id != "live":
        if not _UUID_RE.match(run_id):
            raise BadRequest("bad_run", "That run is not known.")
        run_id = run_id.lower()
        if calls:
            raise BadRequest("golden_calls_unavailable",
                             "The model calls of a test run are shown inside its conversations.")
    if reviewed not in REVIEWED:
        raise BadRequest("bad_reviewed", "Unknown review filter.")
    if status not in (None, "", "ok", "error"):
        raise BadRequest("bad_status", "Status is ok or error.")
    if deleted not in ("hide", "include"):
        raise BadRequest("bad_deleted", "Deleted is hide or include.")
    fields = dict(since=start, until=end, run=run_id,
                  channels=_csv(channel, provenance.CHANNELS, "channel"),
                  channel_sources=_csv(channel_source, CHANNEL_SOURCES, "channel_source"),
                  session_kinds=_csv(session_kind, SESSION_KINDS, "session_kind"),
                  status=status or None, model=(model or "").strip() or None, q=(q or "").strip() or None,
                  user=(user or "").strip() or None, reviewed=reviewed, include_deleted=deleted == "include")
    if calls:
        if kind not in (None, "", "llm", "router"):
            raise BadRequest("bad_kind", "Kind is llm or router.")
        return ModelCallFilter(**fields, agent=(agent or "").strip() or None, kind=kind or None)
    return TurnFilter(**fields)


def encode_cursor(t: datetime, k: str) -> str:
    raw = json.dumps({"t": _aware(t).isoformat(), "k": k}, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(cursor: str | None) -> tuple[datetime, str] | None:
    if not cursor:
        return None
    try:
        if len(cursor) > 400:
            raise ValueError("cursor too long")
        d = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        t, k = datetime.fromisoformat(d["t"]), d["k"]
        if not isinstance(k, str) or not k or len(k) > 300:
            raise ValueError("bad key")
        return _aware(t), k
    except (ValueError, KeyError, TypeError, binascii.Error, UnicodeDecodeError) as exc:
        raise BadRequest("bad_cursor", "That page link is no longer valid. Reload the list.") from exc


def scope_sql(principal: Any, org: str | None, alias: str = "tt") -> tuple[str, dict[str, Any]]:
    """svc-deepagents' own trace._scope_sql (an admin reads the company's turns, anyone else their own),
    its columns qualified with `alias` so a join cannot make them ambiguous. Trace Lab always names a
    company: a missing one is refused rather than read as "every company"."""
    if not _ALIAS_RE.match(alias or ""):
        raise ValueError(f"bad alias {alias!r}")
    if not org:
        raise ValueError("Trace Lab reads are always scoped to one company")
    where, params = trace._scope_sql(principal, org)
    return _SCOPE_COLUMNS.sub(lambda m: f"{alias}.{m.group(1)}", where), dict(params)


def _like(s: str) -> str:
    return "%" + s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _base_sql(scope: str) -> str:
    """One row per turn in the window and in scope: its inferred channel, group key, session kind and
    thread. Needs :since and :until plus the scope's own parameters."""
    return f"""
    SELECT tt.turn_id, tt.session_id, tt.started_at, tt.created_at, tt.ok, tt.cost_usd, tt.email, tt.models,
           tt.tool_calls, tt.question, tt.answer, left({_ASKER_WORDS_SQL}, 300) AS q300,
           {provenance.INFERRED_CHANNEL_SQL} AS channel, {_SOURCE_SQL} AS channel_source,
           {GROUP_KEY_SQL} AS group_key, {SESSION_KIND_SQL} AS session_kind,
           th.title AS thread_title, th.deleted_at AS thread_deleted_at
      FROM {TURNS} tt
      {THREAD_JOIN}
     WHERE {scope} AND tt.started_at >= :since AND tt.started_at < :until"""


def _run_sql(f: TurnFilter, *, channel: str = "channel") -> str:
    """Live traffic only. Golden runs (Phase 3) live in Trace Lab's own database, never in these tables."""
    return f"{channel} <> 'golden'"


def _deleted_sql(f: TurnFilter) -> str:
    return "TRUE" if f.include_deleted else "thread_deleted_at IS NULL"


def _hit_sql(f: TurnFilter, p: dict[str, Any], reviewed: tuple[str, Any] | None = None) -> str:
    """The AND of the turn filters over _base_sql's columns (TRUE when there are none). `reviewed` is
    (alias of the turn rows, turn ids reviewed_turn_ids read from the lab database): the reviewed filter is
    those ids handed over as a parameter, never a join across the two databases."""
    parts: list[str] = []
    if reviewed is not None and f.reviewed != "any":
        from . import annotations  # Phase 4 open coding; imported here because annotations imports this module

        pred, pred_params = annotations.reviewed_predicate(reviewed[0], f.reviewed, reviewed[1])
        parts.append(pred)
        p.update(pred_params)
    if f.channels:
        parts.append("channel = ANY(CAST(:f_channels AS text[]))")
        p["f_channels"] = list(f.channels)
    if f.channel_sources:
        parts.append("channel_source = ANY(CAST(:f_sources AS text[]))")
        p["f_sources"] = list(f.channel_sources)
    if f.session_kinds:
        parts.append("session_kind = ANY(CAST(:f_kinds AS text[]))")
        p["f_kinds"] = list(f.session_kinds)
    if f.status == "ok":
        parts.append("ok")
    elif f.status == "error":
        parts.append("NOT ok")
    if f.model:
        parts.append("jsonb_exists(models, :f_model)")
        p["f_model"] = f.model
    if f.q:
        parts.append("(question ILIKE :f_q OR answer ILIKE :f_q OR turn_id = :f_q_exact OR session_id = :f_q_exact)")
        p["f_q"], p["f_q_exact"] = _like(f.q), f.q
    if f.user:
        parts.append("email ILIKE :f_user")
        p["f_user"] = _like(f.user)
    return " AND ".join(parts) or "TRUE"


def _call_filters_sql(f: ModelCallFilter, p: dict[str, Any], *, channel: str, source: str) -> str:
    parts: list[str] = []
    if f.channels:
        parts.append(f"{channel} = ANY(CAST(:f_channels AS text[]))")
        p["f_channels"] = list(f.channels)
    if f.channel_sources:
        parts.append(f"{source} = ANY(CAST(:f_sources AS text[]))")
        p["f_sources"] = list(f.channel_sources)
    if f.status == "ok":
        parts.append("s.ok")
    elif f.status == "error":
        parts.append("NOT s.ok")
    if f.model:
        parts.append("s.model = :f_model")
        p["f_model"] = f.model
    if getattr(f, "agent", None):
        parts.append("s.agent = :f_agent")
        p["f_agent"] = f.agent
    if getattr(f, "kind", None):
        parts.append("s.kind = :f_kind")
        p["f_kind"] = f.kind
    if f.q:
        parts.append("(tt.question ILIKE :f_q OR tt.answer ILIKE :f_q OR tt.turn_id = :f_q_exact OR tt.session_id = :f_q_exact)")
        p["f_q"], p["f_q_exact"] = _like(f.q), f.q
    return " AND ".join(parts) or "TRUE"


@asynccontextmanager
async def _read() -> AsyncIterator[Any]:
    """A read-only, 10-second-bounded transaction on Trace Lab's read-only main engine."""
    async with db.main_engine().begin() as conn:
        await conn.execute(text("SET TRANSACTION READ ONLY"))
        await conn.execute(text("SET LOCAL statement_timeout = '10s'"))
        yield conn


# ── shaping ──────────────────────────────────────────────────────────────────────────────────

_CLOCK_SQL = "SELECT now() AS as_of, now() - interval '2 seconds' AS watermark"


def _conversation_row(r: Mapping[str, Any], extra: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    kind = r["session_kind"]
    first_q = normalize.redact_text(r["first_question"] or "")
    flags: list[str] = []
    if kind == "inferred":
        flags.append("inferred_session")
    elif kind == "synthetic":
        flags.append("synthetic_session")
    if r["thread_deleted_at"] is not None:
        flags.append("thread_deleted")
    e = extra.get(r["group_key"], {"models": set(), "tool_count": 0})
    return {"key": r["group_key"], "session_id": r["session_id"], "session_kind": kind,
            "session_label": normalize.session_label(kind, r["session_id"], title=r["title"] or first_q, golden=None),
            "title": r["title"], "run_id": "live", "golden": None,
            "first_turn_at": _iso(r["first_turn_at"]), "last_turn_at": _iso(r["last_turn_at"]),
            "turns_in_window": int(r["turns_in_window"]), "matching_turns": int(r["matching_turns"]),
            "failed_turns": int(r["failed_turns"]), "first_question": first_q,
            "channels": sorted(r["channels"] or []), "channel_sources": sorted(r["channel_sources"] or []),
            "models": sorted(e["models"]), "tool_count": int(e["tool_count"]), "cost_usd": _num(r["cost_usd"]) or 0.0,
            "asker": {"email": r["asker_email"]}, "flags": flags, "reviewed_count": None}


# ── conversations ────────────────────────────────────────────────────────────────────────────

async def _reviewed_ids(principal: Any, org: str, f: TurnFilter) -> set[str] | None:
    """The turn ids the `reviewed` filter turns on, read from the lab database (None for "any"). Refused with
    400 reviewed_filter_unavailable while the review tables (migration 0003, version 3) are not applied; the
    refusal comes before any database is read."""
    if f.reviewed == "any":
        return None
    st = db.STATE
    if st is None or (st.applied_version or 0) < REVIEW_VERSION:
        raise BadRequest("reviewed_filter_unavailable", "Filtering by review is not available right now.")
    from . import annotations  # Phase 4 open coding; imported here because annotations imports this module

    return await annotations.reviewed_turn_ids(org, reviewer_id=str(principal.user_id), mode=f.reviewed)


async def list_conversations(principal: Any, org: str, f: TurnFilter, *, cursor: str | None, limit: int) -> dict[str, Any]:
    """Conversations with at least one turn matching the filter, newest last turn first, cursor-paged
    on (last_turn_at, key), with the exact total from the same grouping."""
    reviewed_ids = await _reviewed_ids(principal, org, f)
    if f.run != "live":
        return await golden_list_conversations(org, f, cursor=cursor, limit=limit, reviewed_ids=reviewed_ids)
    pos = decode_cursor(cursor)
    limit = max(1, min(int(limit), 100))
    scope, p = scope_sql(principal, org)
    p.update(since=f.since, until=f.until, limit_plus_one=limit + 1,
             ct=pos[0] if pos else None, ck=pos[1] if pos else None)
    hit = _hit_sql(f, p, reviewed=("b", reviewed_ids) if reviewed_ids is not None else None)
    matched = "max(CASE WHEN turn_id = :f_q_exact THEN turn_id END)" if f.q else "CAST(NULL AS text)"
    sql = f"""
    WITH base AS ({_base_sql(scope)}),
    scoped AS (SELECT b.*, ({hit}) AS hit FROM base b WHERE {_run_sql(f)} AND {_deleted_sql(f)}),
    g AS (
      SELECT group_key, max(session_kind) AS session_kind, max(session_id) AS session_id, max(thread_title) AS title,
             min(started_at) AS first_turn_at, max(started_at) AS last_turn_at, count(*) AS turns_in_window,
             count(*) FILTER (WHERE hit) AS matching_turns, count(*) FILTER (WHERE NOT ok) AS failed_turns,
             coalesce(sum(cost_usd), 0) AS cost_usd, array_agg(DISTINCT channel) AS channels,
             array_agg(DISTINCT channel_source) AS channel_sources,
             (array_agg(q300 ORDER BY started_at, turn_id))[1] AS first_question,
             (array_agg(email ORDER BY started_at, turn_id))[1] AS asker_email,
             max(thread_deleted_at) AS thread_deleted_at, {matched} AS matched_turn_id
        FROM scoped GROUP BY group_key HAVING count(*) FILTER (WHERE hit) > 0
    ), g2 AS (SELECT g.*, count(*) OVER () AS total FROM g)
    SELECT * FROM g2
     WHERE CAST(:ct AS timestamptz) IS NULL
        OR (last_turn_at, group_key) < (CAST(:ct AS timestamptz), CAST(:ck AS text))
     ORDER BY last_turn_at DESC, group_key DESC
     LIMIT :limit_plus_one"""
    extra: dict[str, dict[str, Any]] = {}
    async with _read() as conn:
        clock = (await conn.execute(text(_CLOCK_SQL))).mappings().one()
        rows = [dict(r) for r in (await conn.execute(text(sql), p)).mappings().all()]
        page = rows[:limit]
        if page:
            # Models and tool counts for this page's conversations only.
            scope2, p2 = scope_sql(principal, org)
            p2.update(since=f.since, until=f.until, keys=[r["group_key"] for r in page])
            sql2 = f"""
            WITH base AS ({_base_sql(scope2)})
            SELECT group_key, models, tool_calls FROM base
             WHERE {_run_sql(f)} AND {_deleted_sql(f)} AND group_key = ANY(CAST(:keys AS text[]))"""
            for r in (await conn.execute(text(sql2), p2)).mappings().all():
                e = extra.setdefault(r["group_key"], {"models": set(), "tool_count": 0})
                e["models"].update(str(m) for m in _as_list(r["models"]))
                e["tool_count"] += int(r["tool_calls"] or 0)
    nxt = encode_cursor(page[-1]["last_turn_at"], page[-1]["group_key"]) if len(rows) > limit else None
    total = int(rows[0]["total"]) if rows else (0 if pos is None else None)
    return {"ok": True, "as_of": _iso(clock["as_of"]), "watermark": _iso(clock["watermark"]), "total": total,
            "next_cursor": nxt,
            "matched_turn_id": next((r["matched_turn_id"] for r in rows if r.get("matched_turn_id")), None),
            "conversations": [_conversation_row(r, extra) for r in page]}


async def conversation_updates(principal: Any, org: str, f: TurnFilter, *, watermark: datetime) -> dict[str, Any]:
    """New turns since a watermark. Turn rows land when the turn is flushed, so created_at is the watermark;
    the started_at look-back keeps the query on ix_trace_turns_org_time. Advisory: Refresh shows the truth."""
    if f.run != "live":
        return await golden_updates(org, f, watermark=watermark)
    wm = _aware(watermark)
    scope, p = scope_sql(principal, org)
    p.update(since=wm - timedelta(minutes=UPDATE_LOOKBACK_MINUTES), until=f.until, wm=wm)
    hit = _hit_sql(f, p)
    sql = f"""
    WITH base AS ({_base_sql(scope)})
    SELECT count(*) AS new_turns, count(DISTINCT group_key) AS new_conversations, max(created_at) AS latest
      FROM base
     WHERE created_at > CAST(:wm AS timestamptz) AND {_run_sql(f)} AND {_deleted_sql(f)} AND ({hit})"""
    async with _read() as conn:
        clock = (await conn.execute(text(_CLOCK_SQL))).mappings().one()
        r = (await conn.execute(text(sql), p)).mappings().one()
    return {"ok": True, "new_turns": int(r["new_turns"] or 0), "new_conversations": int(r["new_conversations"] or 0),
            "latest": _iso(r["latest"]), "watermark": _iso(clock["watermark"]), "as_of": _iso(clock["as_of"])}


async def facets(principal: Any, org: str, f: TurnFilter) -> dict[str, Any]:
    """Counts by channel, channel source, session kind and model over the window (deleted threads per f)."""
    if f.run != "live":
        return await golden_facets(org, f)
    scope, p = scope_sql(principal, org)
    p.update(since=f.since, until=f.until)
    sql = f"""
    WITH base AS ({_base_sql(scope)}),
    scoped AS (SELECT * FROM base WHERE {_run_sql(f)} AND {_deleted_sql(f)})
    SELECT 'channel' AS facet, channel AS value, count(*) AS n FROM scoped GROUP BY channel
    UNION ALL SELECT 'channel_source', channel_source, count(*) FROM scoped GROUP BY channel_source
    UNION ALL SELECT 'session_kind', session_kind, count(DISTINCT group_key) FROM scoped GROUP BY session_kind
    UNION ALL SELECT 'model', m.model, count(*) FROM scoped
              CROSS JOIN LATERAL jsonb_array_elements_text(
                  CASE WHEN jsonb_typeof(scoped.models) = 'array' THEN scoped.models ELSE CAST('[]' AS jsonb) END) AS m(model)
              GROUP BY m.model"""
    async with _read() as conn:
        clock = (await conn.execute(text(_CLOCK_SQL))).mappings().one()
        rows = (await conn.execute(text(sql), p)).mappings().all()
    names = {"channel": ("channels", "turns"), "channel_source": ("channel_sources", "turns"),
             "session_kind": ("session_kinds", "conversations"), "model": ("models", "turns")}
    out: dict[str, list[dict[str, Any]]] = {"channels": [], "channel_sources": [], "session_kinds": [], "models": []}
    for r in rows:
        bucket, unit = names[r["facet"]]
        out[bucket].append({"value": r["value"], unit: int(r["n"])})
    for bucket in out.values():
        bucket.sort(key=lambda x: (-int(x.get("turns", x.get("conversations", 0))), str(x["value"])))
    return {"ok": True, "as_of": _iso(clock["as_of"]), **out}


async def _activity_input(conn: Any, session_id: str, turn_id: str) -> dict[str, Any] | None:
    """The earliest orchestrator/turn/input activity row of a turn, read on its (session_id, turn_id,
    created_at) index. A hint only: the log may be off (ACTIVITY_LOG_ENABLED) or the table absent, so a
    failure reads as None inside a savepoint that keeps the read transaction usable."""
    try:
        async with conn.begin_nested():
            row = (await conn.execute(text(f"""
                SELECT payload, created_at FROM {ACTIVITY}
                 WHERE session_id = :sid AND turn_id = :tid AND agent = 'orchestrator' AND stage = 'turn'
                   AND direction = 'input'
                 ORDER BY created_at ASC LIMIT 1"""), {"sid": session_id[:120], "tid": turn_id[:64]})).mappings().first()
    except Exception:  # noqa: BLE001 - optional evidence
        return None
    if row is None:
        return None
    payload = row["payload"]
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except ValueError:
            payload = None
    return {"payload": payload if isinstance(payload, Mapping) else {}, "created_at": _iso(row["created_at"])}


async def get_conversation(principal: Any, org: str, key: str, *, turn_id: str | None = None,
                           include_deleted: bool = False) -> dict[str, Any] | None:
    """One conversation: every turn header (at most 500) and the active turn normalized in full."""
    if not isinstance(key, str) or not _KEY_RE.match(key):
        return None
    if is_golden_key(key):
        return await golden_conversation(org, key, turn_id=turn_id)
    scope, p = scope_sql(principal, org)
    p.update(key=key, limit=MAX_TURN_HEADERS + 1)
    if key[0] == "s":
        p["sid"] = key[2:]
        narrow = "tt.session_id = :sid"
    else:
        p["tid"] = key[2:]
        narrow = "tt.turn_id = :tid"
    sql = f"""
    SELECT tt.*, th.title AS thread_title, th.deleted_at AS thread_deleted_at, (th.id IS NOT NULL) AS thread_exists
      FROM {TURNS} tt
      {THREAD_JOIN}
     WHERE {scope} AND {narrow} AND {GROUP_KEY_SQL} = :key
     ORDER BY tt.started_at, tt.turn_id
     LIMIT :limit"""
    async with _read() as conn:
        rows = [dict(r) for r in (await conn.execute(text(sql), p)).mappings().all()]
        if not rows:
            return None
        deleted = rows[0]["thread_deleted_at"] is not None
        if deleted and not include_deleted:
            return None
        truncated = len(rows) > MAX_TURN_HEADERS
        rows = rows[:MAX_TURN_HEADERS]
        active = next((r for r in rows if r["turn_id"] == turn_id), None) if turn_id else rows[0]
        if active is None:
            return None
        spans = [dict(s) for s in (await conn.execute(
            text(f"SELECT * FROM {SPANS} WHERE turn_id = :tid ORDER BY seq"), {"tid": active["turn_id"]})).mappings().all()]
        activity = None
        if active.get("session_id"):
            activity = await _activity_input(conn, str(active["session_id"]), str(active["turn_id"]))
    acts = {active["turn_id"]: activity} if activity else {}
    att = normalize.attempts(rows, None, acts)
    first = rows[0]
    kind = normalize.session_kind(first.get("session_id"), thread_exists=bool(first.get("thread_exists")))
    first_q, _ = normalize.repair_question(first, None, acts.get(first["turn_id"]))
    label = normalize.session_label(kind, first.get("session_id"),
                                    title=first.get("thread_title") or normalize.redact_text(first_q), golden=None)
    headers = [normalize.turn_header(r, None, turn_index=i + 1, attempt=att[r["turn_id"]],
                                     activity_input=acts.get(r["turn_id"]))
               for i, r in enumerate(rows)]
    index = next(i for i, r in enumerate(rows) if r is active)
    record = normalize.normalize_turn(active, spans, activity_input=activity,
                                      conversation={"key": key, "session_kind": kind, "session_label": label,
                                                    "turn_index": index + 1, "turn_count": len(rows),
                                                    **att[active["turn_id"]], "run_id": "live",
                                                    "thread_deleted": deleted})
    flags: list[str] = []
    if kind == "inferred":
        flags.append("inferred_session")
    elif kind == "synthetic":
        flags.append("synthetic_session")
    if deleted:
        flags.append("thread_deleted")
    return {"key": key, "session_id": first.get("session_id"), "session_kind": kind, "session_label": label,
            "title": first.get("thread_title"), "thread_deleted": deleted,
            "run": {"run_id": "live", "kind": "live"}, "turn_count": len(rows),
            "turns_truncated": truncated, "turns": headers, "active": record, "flags": flags,
            "normalizer_version": normalize.NORMALIZER_VERSION}


async def get_turn(principal: Any, org: str, turn_id: str) -> dict[str, Any] | None:
    """One normalized turn and its conversation key. A deleted thread's turn stays readable by its id;
    the record carries the thread_deleted flag."""
    if not isinstance(turn_id, str) or not turn_id or len(turn_id) > 80:
        return None
    if is_golden_turn(turn_id):
        return await golden_turn(org, turn_id)
    scope, p = scope_sql(principal, org)
    p["tid"] = turn_id
    async with _read() as conn:
        row = (await conn.execute(text(f"SELECT {GROUP_KEY_SQL} AS group_key FROM {TURNS} tt "
                                       f"WHERE {scope} AND tt.turn_id = :tid"), p)).mappings().first()
    if row is None:
        return None
    conv = await get_conversation(principal, org, row["group_key"], turn_id=turn_id, include_deleted=True)
    if conv is None:
        return None
    return {"trace": conv["active"], "conversation_key": row["group_key"]}


# ── runs ─────────────────────────────────────────────────────────────────────────────────────

async def list_runs(principal: Any, org: str, *, since: datetime, until: datetime) -> dict[str, Any]:
    """The "All live traffic" pseudo-run over the window. Golden runs join this list in Phase 3."""
    scope, p = scope_sql(principal, org)
    p.update(since=since, until=until)
    sql = f"""
    WITH base AS ({_base_sql(scope)}), live AS (SELECT * FROM base WHERE channel <> 'golden')
    SELECT (SELECT min(started_at) FROM live) AS started_at, (SELECT max(started_at) FROM live) AS ended_at,
           (SELECT count(DISTINCT group_key) FROM live) AS conversation_count,
           (SELECT count(*) FROM live) AS turn_count, (SELECT coalesce(sum(cost_usd), 0) FROM live) AS cost_usd,
           (SELECT array_agg(DISTINCT m.model) FROM live
                   CROSS JOIN LATERAL jsonb_array_elements_text(
                       CASE WHEN jsonb_typeof(live.models) = 'array' THEN live.models ELSE CAST('[]' AS jsonb) END) AS m(model)
           ) AS models"""
    async with _read() as conn:
        r = (await conn.execute(text(sql), p)).mappings().one()
    live = {"run_id": "live", "kind": "live", "name": "All live traffic", "recorded": False, "status": "live",
            "started_at": _iso(r["started_at"]), "ended_at": _iso(r["ended_at"]),
            "conversation_count": int(r["conversation_count"] or 0), "turn_count": int(r["turn_count"] or 0),
            "models": sorted(str(m) for m in (r["models"] or [])), "cost_usd": _num(r["cost_usd"]) or 0.0}
    golden = await golden_runs_for(org, since=since, until=until)
    return {"ok": True, "golden_available": True, "runs": [live, *golden]}


# ── model calls ──────────────────────────────────────────────────────────────────────────────

def _retention_days() -> int:
    try:
        return int(os.environ.get("TRACE_RETENTION_DAYS") or trace.DEFAULT_RETENTION_DAYS)
    except ValueError:
        return trace.DEFAULT_RETENTION_DAYS


def _calls_from(scope: str) -> str:
    return f"""
      FROM {SPANS} s
      JOIN {TURNS} tt ON tt.turn_id = s.turn_id
     WHERE s.kind IN ('llm', 'router') AND {scope} AND tt.started_at >= :since AND tt.started_at < :until"""


_CALL_COLUMNS = f"""
       s.id AS span_id, s.turn_id, s.kind, s.name, s.agent, s.model, s.started_at, s.latency_ms,
       s.input_tokens, s.output_tokens, s.cache_read_tokens, s.cost_usd, s.ok, left(s.error, 600) AS error,
       CASE WHEN jsonb_typeof(s.input) = 'object' AND (jsonb_exists(s.input, 'system_prompt')
                                                     OR jsonb_exists(s.input, 'user_message')) THEN 'exchange'
            WHEN jsonb_typeof(s.input) = 'object' AND jsonb_exists(s.input, 'last_human_message') THEN 'loop_summary'
            WHEN s.input IS NULL THEN 'none' ELSE 'other' END AS request_kind,
       left(CASE WHEN jsonb_typeof(s.output) = 'object'
                 THEN coalesce(s.output->>'content', s.output->>'model_output', CAST(s.output AS text))
                 ELSE s.output #>> '{{}}' END, 2000) AS response_preview,
       coalesce(jsonb_typeof(s.output) = 'object' AND jsonb_exists(s.output, 'tool_calls')
                AND jsonb_exists(s.output, 'content'), false) AS loop_output,
       CASE WHEN jsonb_typeof(s.output) = 'object' AND jsonb_typeof(s.output->'tool_calls') = 'array'
            THEN jsonb_array_length(s.output->'tool_calls') ELSE 0 END AS tool_call_requests,
       left({_ASKER_WORDS_SQL}, 600) AS question_excerpt,
       {GROUP_KEY_SQL} AS conversation_key"""


_THINKING_MARK = re.compile(r"""['"](?:thinking|redacted_thinking|encrypted_content|signature)['"]\s*:""")


def _preview(raw: str | None) -> str | None:
    """A short, redacted reply preview with model reasoning removed. A content list cut short by the SQL
    cannot be parsed; if it still shows a reasoning block, the preview says so instead of showing it."""
    if raw is None:
        return None
    s = normalize.strip_reasoning(raw)
    s = s if isinstance(s, str) else json.dumps(s, ensure_ascii=False, default=str)
    if _THINKING_MARK.search(s):
        return normalize.REASONING_HIDDEN
    return normalize.redact_text(s)[:200]


def _call_row(r: Mapping[str, Any]) -> dict[str, Any]:
    return {"span_id": str(r["span_id"]), "turn_id": r["turn_id"], "conversation_key": r["conversation_key"],
            "started_at": _iso(r["started_at"]), "kind": r["kind"], "name": r["name"], "agent": r["agent"],
            "label": normalize.call_label(r["kind"], r["name"], r["request_kind"], bool(r["loop_output"]),
                                          int(r["tool_call_requests"] or 0)),
            "model": r["model"], "latency_ms": r["latency_ms"], "input_tokens": r["input_tokens"],
            "output_tokens": r["output_tokens"], "cache_read_tokens": r["cache_read_tokens"],
            "cost_usd": _num(r["cost_usd"]), "ok": bool(r["ok"]),
            "error": normalize.redact_text(r["error"])[:300] if r["error"] else None,
            "request_kind": r["request_kind"],
            "response_preview": _preview(r["response_preview"]),
            "question_excerpt": normalize.redact_text(r["question_excerpt"] or "")[:160]}


async def list_model_calls(principal: Any, org: str, f: ModelCallFilter, *, cursor: str | None, limit: int) -> dict[str, Any]:
    """llm and router spans only: agent group spans hold a whole sub-agent run's cost and would double-count.
    Spans older than the trace's retention window are gone (svc-deepagents purges them)."""
    pos = decode_cursor(cursor)
    if pos is not None and not _UUID_RE.match(pos[1]):
        raise BadRequest("bad_cursor", "That page link is no longer valid. Reload the list.")
    limit = max(1, min(int(limit), 200))
    scope, p = scope_sql(principal, org)
    p.update(since=f.since, until=f.until)
    channel = provenance.INFERRED_CHANNEL_SQL
    where = f"{_run_sql(f, channel=channel)} AND {_call_filters_sql(f, p, channel=channel, source=_SOURCE_SQL)}"
    page_sql = f"""SELECT {_CALL_COLUMNS} {_calls_from(scope)} AND {where}
       AND (CAST(:ct AS timestamptz) IS NULL OR (s.started_at, s.id) < (CAST(:ct AS timestamptz), CAST(:ck AS uuid)))
     ORDER BY s.started_at DESC, s.id DESC
     LIMIT :limit_plus_one"""
    agg_sql = f"""SELECT count(*) AS calls, count(*) FILTER (WHERE NOT s.ok) AS errors,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY s.latency_ms) AS latency_p50_ms,
           percentile_cont(0.95) WITHIN GROUP (ORDER BY s.latency_ms) AS latency_p95_ms,
           coalesce(sum(s.input_tokens), 0) AS input_tokens, coalesce(sum(s.output_tokens), 0) AS output_tokens,
           coalesce(sum(s.cache_read_tokens), 0) AS cache_read_tokens, coalesce(sum(s.cost_usd), 0) AS cost_usd,
           count(*) FILTER (WHERE s.cost_usd IS NULL) AS unpriced_calls {_calls_from(scope)} AND {where}"""
    by_model_sql = f"""SELECT s.model, count(*) AS calls, count(*) FILTER (WHERE NOT s.ok) AS errors,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY s.latency_ms) AS latency_p50_ms,
           coalesce(sum(s.cost_usd), 0) AS cost_usd {_calls_from(scope)} AND {where}
     GROUP BY s.model ORDER BY count(*) DESC, s.model"""
    page_params = dict(p, ct=pos[0] if pos else None, ck=pos[1] if pos else None, limit_plus_one=limit + 1)
    async with _read() as conn:
        clock = (await conn.execute(text(_CLOCK_SQL))).mappings().one()
        rows = [dict(r) for r in (await conn.execute(text(page_sql), page_params)).mappings().all()]
        agg = dict((await conn.execute(text(agg_sql), p)).mappings().one())
        by_model = [dict(r) for r in (await conn.execute(text(by_model_sql), p)).mappings().all()]
    days = _retention_days()
    page = rows[:limit]
    nxt = (encode_cursor(page[-1]["started_at"], str(page[-1]["span_id"]))
           if len(rows) > limit and page[-1]["started_at"] is not None else None)
    tin, tout, tcache = (int(agg[k] or 0) for k in ("input_tokens", "output_tokens", "cache_read_tokens"))
    return {"ok": True, "as_of": _iso(clock["as_of"]), "next_cursor": nxt, "retention_days": days,
            "spans_available_since": _iso(clock["as_of"] - timedelta(days=days)),
            "aggregate": {"calls": int(agg["calls"] or 0), "errors": int(agg["errors"] or 0),
                          "latency_p50_ms": _num(agg["latency_p50_ms"]), "latency_p95_ms": _num(agg["latency_p95_ms"]),
                          "input_tokens": tin, "output_tokens": tout, "cache_read_tokens": tcache,
                          "total_tokens": tin + tout + tcache, "cost_usd": _num(agg["cost_usd"]) or 0.0,
                          "unpriced_calls": int(agg["unpriced_calls"] or 0),
                          "by_model": [{"model": m["model"], "calls": int(m["calls"]), "errors": int(m["errors"]),
                                        "latency_p50_ms": _num(m["latency_p50_ms"]), "cost_usd": _num(m["cost_usd"]) or 0.0}
                                       for m in by_model]},
            "calls": [_call_row(r) for r in page]}


async def get_model_call(principal: Any, org: str, span_id: str) -> dict[str, Any] | None:
    """One model-call card, read from its turn normalized, so the group and the prompt note are exactly
    what the Conversations view shows."""
    if not isinstance(span_id, str) or not _UUID_RE.match(span_id):
        return None
    scope, p = scope_sql(principal, org)
    p["sid"] = span_id
    async with _read() as conn:
        head = (await conn.execute(text(f"""
            SELECT tt.*, {GROUP_KEY_SQL} AS conversation_key
              FROM {SPANS} s
              JOIN {TURNS} tt ON tt.turn_id = s.turn_id
             WHERE s.id = CAST(:sid AS uuid) AND s.kind IN ('llm', 'router') AND {scope}"""), p)).mappings().first()
        if head is None:
            return None
        turn = dict(head)
        spans = [dict(s) for s in (await conn.execute(
            text(f"SELECT * FROM {SPANS} WHERE turn_id = :tid ORDER BY seq"), {"tid": turn["turn_id"]})).mappings().all()]
    record = normalize.normalize_turn(turn, spans, conversation={"key": turn["conversation_key"]})
    want = span_id.lower()
    card = next((m for m in record["model_calls"] if m["span_id"] == want), None)
    if card is None:                         # the span sits in a discarded duplicate tree
        span = next((s for s in spans if str(s.get("id")).lower() == want), None)
        if span is None:
            return None
        card = normalize.model_call_card(span, turn_id=str(turn["turn_id"]), group=None, prompt_ref=None)
    return {"model_call": card, "turn_id": turn["turn_id"], "conversation_key": turn["conversation_key"]}


# ── golden runs (Phase 3 Task 13): read from Trace Lab's own database, normalized like live turns ──────

#: A golden turn id (capture.TURN_PREFIX): live turn ids are "turn-" + hex, so "turn-g" never collides.
GOLDEN_TURN_PREFIX = "turn-g"
GOLDEN_KEY_PREFIX = "s:golden-"


def is_golden_key(key: Any) -> bool:
    return isinstance(key, str) and key.startswith(GOLDEN_KEY_PREFIX) and bool(_KEY_RE.match(key))


def is_golden_turn(turn_id: Any) -> bool:
    return isinstance(turn_id, str) and turn_id.startswith(GOLDEN_TURN_PREFIX) and len(turn_id) <= 80


@asynccontextmanager
async def _lab_read() -> AsyncIterator[Any]:
    """A read-only, 10-second-bounded transaction on Trace Lab's own database."""
    async with db.lab_engine().begin() as conn:
        await conn.execute(text("SET TRANSACTION READ ONLY"))
        await conn.execute(text("SET LOCAL statement_timeout = '10s'"))
        yield conn


def _jsonb(v: Any) -> Any:
    return json.loads(v) if isinstance(v, str) else v


def golden_meta(turn: Mapping[str, Any], row: Mapping[str, Any]) -> dict[str, Any]:
    """normalize_turn's recorded provenance for a golden turn: channel golden, recorded, the case's page
    context and the starter's role. Never the expected answer, the outcome or the grade."""
    g = turn.get("golden") if isinstance(turn.get("golden"), Mapping) else {}
    return {"channel": "golden", "channel_source": "recorded", "classifier_version": None, "origin": "golden_run",
            "origin_params": {"run_id": str(row["run_id"]), "case_key": row["case_key"], "rep": row["rep"],
                              "msg_idx": row["msg_idx"]},
            "page": None, "context_text": g.get("page_context"), "asker_role": g.get("asker_role"),
            "asker_scope": None, "asker_question": row["question"] or "", "golden_run_id": str(row["run_id"]),
            "golden_case_key": row["case_key"], "rep": row["rep"]}


def _golden_hit_sql(f: TurnFilter, p: dict[str, Any], reviewed_ids: set[str] | None = None) -> str:
    """The AND of the turn filters a golden turn can meet (golden turns are channel golden, recorded,
    synthetic sessions). A golden answer can be reviewed too: the same ids, over the result rows `r`."""
    parts: list[str] = []
    if reviewed_ids is not None and f.reviewed != "any":
        from . import annotations  # Phase 4 open coding; imported here because annotations imports this module

        pred, pred_params = annotations.reviewed_predicate("r", f.reviewed, reviewed_ids)
        parts.append(pred)
        p.update(pred_params)
    if (f.channels and "golden" not in f.channels) or (f.channel_sources and "recorded" not in f.channel_sources) \
            or (f.session_kinds and "synthetic" not in f.session_kinds):
        parts.append("FALSE")
    if f.status == "ok":
        parts.append("coalesce(CAST(e.turn->>'ok' AS boolean), true)")
    elif f.status == "error":
        parts.append("NOT coalesce(CAST(e.turn->>'ok' AS boolean), true)")
    if f.model:
        parts.append("jsonb_exists(coalesce(e.turn->'models', CAST('[]' AS jsonb)), :f_model)")
        p["f_model"] = f.model
    if f.q:
        parts.append("(r.question ILIKE :f_q OR r.answer ILIKE :f_q OR r.turn_id = :f_q_exact OR r.session_id = :f_q_exact)")
        p["f_q"], p["f_q_exact"] = _like(f.q), f.q
    if f.user:
        parts.append("(e.turn->>'email') ILIKE :f_user")
        p["f_user"] = _like(f.user)
    return " AND ".join(parts) or "TRUE"


_GOLDEN_FROM = ("FROM tracelab.golden_results r JOIN tracelab.evidence_turns e ON e.turn_id = r.turn_id"
                " WHERE r.run_id = CAST(:run AS uuid) AND r.organization_id = CAST(:org AS uuid)")


async def golden_list_conversations(org: str, f: TurnFilter, *, cursor: str | None, limit: int,
                                     reviewed_ids: set[str] | None = None) -> dict[str, Any]:
    """One conversation per golden session (a case's question and its follow-ups, one rep) of the run
    f.run, from the lab database; the same row shape and cursor as the live list."""
    pos = decode_cursor(cursor)
    limit = max(1, min(int(limit), 100))
    p: dict[str, Any] = {"run": f.run, "org": org, "limit_plus_one": limit + 1,
                         "ct": pos[0] if pos else None, "ck": pos[1] if pos else None}
    hit = _golden_hit_sql(f, p, reviewed_ids)
    sql = f"""
    WITH base AS (SELECT r.session_id, r.case_key, r.rep, r.msg_idx, r.question, e.captured_at, e.turn, ({hit}) AS hit
                  {_GOLDEN_FROM}),
    g AS (
      SELECT 's:' || session_id AS group_key, session_id, min(case_key) AS case_key, min(rep) AS rep,
             min(captured_at) AS first_turn_at, max(captured_at) AS last_turn_at, count(*) AS turns_in_window,
             count(*) FILTER (WHERE hit) AS matching_turns,
             count(*) FILTER (WHERE NOT coalesce(CAST(turn->>'ok' AS boolean), true)) AS failed_turns,
             coalesce(sum(CAST(turn->>'cost_usd' AS numeric)), 0) AS cost_usd,
             coalesce(sum(CAST(turn->>'tool_calls' AS integer)), 0) AS tool_count,
             (array_agg(question ORDER BY msg_idx))[1] AS first_question,
             (array_agg(turn->>'email' ORDER BY msg_idx))[1] AS asker_email
        FROM base GROUP BY session_id HAVING count(*) FILTER (WHERE hit) > 0
    ), g2 AS (SELECT g.*, count(*) OVER () AS total FROM g)
    SELECT * FROM g2
     WHERE CAST(:ct AS timestamptz) IS NULL
        OR (last_turn_at, group_key) < (CAST(:ct AS timestamptz), CAST(:ck AS text))
     ORDER BY last_turn_at DESC, group_key DESC
     LIMIT :limit_plus_one"""
    async with _lab_read() as conn:
        clock = (await conn.execute(text(_CLOCK_SQL))).mappings().one()
        rows = [dict(r) for r in (await conn.execute(text(sql), p)).mappings().all()]
        page = rows[:limit]
        models: dict[str, set[str]] = {}
        if page:
            for r in (await conn.execute(text(
                    f"SELECT r.session_id, e.turn->'models' AS models {_GOLDEN_FROM}"
                    " AND r.session_id = ANY(CAST(:sids AS text[]))"),
                    {"run": f.run, "org": org, "sids": [r["session_id"] for r in page]})).mappings().all():
                models.setdefault(r["session_id"], set()).update(str(m) for m in _as_list(_jsonb(r["models"])))
    out = []
    for r in page:
        golden = {"run_id": f.run, "case_key": r["case_key"], "rep": int(r["rep"])}
        out.append({"key": r["group_key"], "session_id": r["session_id"], "session_kind": "synthetic",
                    "session_label": normalize.session_label("synthetic", r["session_id"], title=None, golden=golden),
                    "title": None, "run_id": f.run, "golden": golden,
                    "first_turn_at": _iso(r["first_turn_at"]), "last_turn_at": _iso(r["last_turn_at"]),
                    "turns_in_window": int(r["turns_in_window"]), "matching_turns": int(r["matching_turns"]),
                    "failed_turns": int(r["failed_turns"]), "first_question": normalize.redact_text(r["first_question"] or ""),
                    "channels": ["golden"], "channel_sources": ["recorded"],
                    "models": sorted(models.get(r["session_id"], set())), "tool_count": int(r["tool_count"]),
                    "cost_usd": _num(r["cost_usd"]) or 0.0, "asker": {"email": r["asker_email"]},
                    "flags": ["synthetic_session"], "reviewed_count": None})
    nxt = encode_cursor(page[-1]["last_turn_at"], page[-1]["group_key"]) if len(rows) > limit else None
    total = int(rows[0]["total"]) if rows else (0 if pos is None else None)
    return {"ok": True, "as_of": _iso(clock["as_of"]), "watermark": _iso(clock["watermark"]), "total": total,
            "next_cursor": nxt, "matched_turn_id": None, "conversations": out}


async def golden_updates(org: str, f: TurnFilter, *, watermark: datetime) -> dict[str, Any]:
    """New golden turns of the run since a watermark (a run in progress adds answers as items finish)."""
    p: dict[str, Any] = {"run": f.run, "org": org, "wm": _aware(watermark)}
    hit = _golden_hit_sql(f, p)
    async with _lab_read() as conn:
        clock = (await conn.execute(text(_CLOCK_SQL))).mappings().one()
        r = (await conn.execute(text(
            f"SELECT count(*) AS new_turns, count(DISTINCT r.session_id) AS new_conversations,"
            f" max(e.captured_at) AS latest {_GOLDEN_FROM} AND e.captured_at > CAST(:wm AS timestamptz) AND ({hit})"),
            p)).mappings().one()
    return {"ok": True, "new_turns": int(r["new_turns"] or 0), "new_conversations": int(r["new_conversations"] or 0),
            "latest": _iso(r["latest"]), "watermark": _iso(clock["watermark"]), "as_of": _iso(clock["as_of"])}


async def golden_facets(org: str, f: TurnFilter) -> dict[str, Any]:
    async with _lab_read() as conn:
        clock = (await conn.execute(text(_CLOCK_SQL))).mappings().one()
        rows = (await conn.execute(text(f"SELECT r.session_id, e.turn->'models' AS models {_GOLDEN_FROM}"),
                                   {"run": f.run, "org": org})).mappings().all()
    turns, sessions, models = len(rows), len({r["session_id"] for r in rows}), {}
    for r in rows:
        for m in _as_list(_jsonb(r["models"])):
            models[str(m)] = models.get(str(m), 0) + 1
    return {"ok": True, "as_of": _iso(clock["as_of"]),
            "channels": [{"value": "golden", "turns": turns}] if turns else [],
            "channel_sources": [{"value": "recorded", "turns": turns}] if turns else [],
            "session_kinds": [{"value": "synthetic", "conversations": sessions}] if sessions else [],
            "models": sorted(({"value": m, "turns": n} for m, n in models.items()),
                             key=lambda x: (-x["turns"], x["value"]))}


async def golden_conversation(org: str, key: str, *, turn_id: str | None = None) -> dict[str, Any] | None:
    """One golden conversation from the lab database: every turn header and the active turn, normalized
    with the same normalize_turn as live turns (recorded provenance, never the expected answer)."""
    if not is_golden_key(key):
        return None
    sid = key[2:]
    async with _lab_read() as conn:
        rows = [dict(r) for r in (await conn.execute(text(
            "SELECT r.run_id, r.case_key, r.rep, r.msg_idx, r.question, r.turn_id, e.turn"
            " FROM tracelab.golden_results r JOIN tracelab.evidence_turns e ON e.turn_id = r.turn_id"
            " WHERE r.organization_id = CAST(:org AS uuid) AND r.session_id = :sid ORDER BY r.msg_idx LIMIT :limit"),
            {"org": org, "sid": sid, "limit": MAX_TURN_HEADERS})).mappings().all()]
        if not rows:
            return None
        active = next((r for r in rows if r["turn_id"] == turn_id), None) if turn_id else rows[0]
        if active is None:
            return None
        spans = [_jsonb(s) for (s,) in (await conn.execute(text(
            "SELECT span FROM tracelab.evidence_spans WHERE turn_id = :tid ORDER BY seq"),
            {"tid": active["turn_id"]})).all()]
    turns = [dict(_jsonb(r["turn"])) for r in rows]
    metas = {r["turn_id"]: golden_meta(t, r) for r, t in zip(rows, turns)}
    att = normalize.attempts(turns, metas, None)
    run_id = str(rows[0]["run_id"])
    golden = {"run_id": run_id, "case_key": rows[0]["case_key"], "rep": int(rows[0]["rep"])}
    label = normalize.session_label("synthetic", sid, title=None, golden=golden)
    headers = [normalize.turn_header(t, metas[t["turn_id"]], turn_index=i + 1, attempt=att[t["turn_id"]])
               for i, t in enumerate(turns)]
    index = next(i for i, r in enumerate(rows) if r is active)
    record = normalize.normalize_turn(turns[index], spans, meta=metas[active["turn_id"]],
                                      conversation={"key": key, "session_kind": "synthetic", "session_label": label,
                                                    "turn_index": index + 1, "turn_count": len(rows),
                                                    **att[active["turn_id"]], "run_id": run_id,
                                                    "thread_deleted": False})
    return {"key": key, "session_id": sid, "session_kind": "synthetic", "session_label": label, "title": None,
            "thread_deleted": False, "run": {"run_id": run_id, "kind": "golden"}, "golden": golden,
            "turn_count": len(rows), "turns_truncated": False, "turns": headers, "active": record,
            "flags": ["synthetic_session"], "normalizer_version": normalize.NORMALIZER_VERSION}


async def golden_turn(org: str, turn_id: str) -> dict[str, Any] | None:
    if not is_golden_turn(turn_id):
        return None
    async with _lab_read() as conn:
        sid = (await conn.execute(text(
            "SELECT session_id FROM tracelab.golden_results WHERE turn_id = :t AND organization_id = CAST(:org AS uuid)"),
            {"t": turn_id, "org": org})).scalar()
    if not sid:
        return None
    conv = await golden_conversation(org, "s:" + str(sid), turn_id=turn_id)
    return None if conv is None else {"trace": conv["active"], "conversation_key": conv["key"]}


async def golden_runs_for(org: str, *, since: datetime, until: datetime) -> list[dict[str, Any]]:
    """The company's golden runs started in the window, newest first, for the run picker."""
    async with _lab_read() as conn:
        rows = (await conn.execute(text(
            "SELECT r.id, r.label, r.status, r.purpose, r.k, r.set_id, r.started_at, r.finished_at,"
            " r.started_by_email, r.usd_spent, r.params->>'chat_model' AS chat_model,"
            " (SELECT count(DISTINCT g.session_id) FROM tracelab.golden_results g JOIN tracelab.evidence_turns e"
            "   ON e.turn_id = g.turn_id WHERE g.run_id = r.id) AS conversation_count,"
            " (SELECT count(*) FROM tracelab.golden_results g JOIN tracelab.evidence_turns e"
            "   ON e.turn_id = g.turn_id WHERE g.run_id = r.id) AS turn_count"
            " FROM tracelab.golden_runs r WHERE r.organization_id = CAST(:org AS uuid)"
            " AND r.started_at >= :since AND r.started_at < :until ORDER BY r.started_at DESC LIMIT 200"),
            {"org": org, "since": _aware(since), "until": _aware(until)})).mappings().all()
    return [{"run_id": str(r["id"]), "kind": "golden", "name": r["label"], "recorded": True, "status": r["status"],
             "purpose": r["purpose"], "k": r["k"], "set_id": str(r["set_id"]) if r["set_id"] else None,
             "started_at": _iso(r["started_at"]), "ended_at": _iso(r["finished_at"]),
             "started_by_email": r["started_by_email"], "conversation_count": int(r["conversation_count"] or 0),
             "turn_count": int(r["turn_count"] or 0), "models": [r["chat_model"]] if r["chat_model"] else [],
             "cost_usd": _num(r["usd_spent"]) or 0.0} for r in rows]
