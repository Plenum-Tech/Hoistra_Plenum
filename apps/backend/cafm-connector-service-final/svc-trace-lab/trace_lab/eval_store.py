"""The eval store (11 Nov 2026, task plan B1): a conversation kept the way Langfuse keeps one.

    session        one conversation: a scenario asked once, every turn of it        tracelab.sessions
    trace          one turn: the question in, the answer out, how it ended          tracelab.traces
    observation    one step of a turn, naming the step it ran inside                tracelab.observations
                   (generation = a model call, tool, agent, span = any other step)

A turn and its spans arrive shaped like agent_trace_turns / agent_trace_spans rows, which is what the chat stores and
what capture.run_message returns. rows_for turns them into one trace row and one observation row per step, using
trace_tree.build_tree, so what is stored is exactly the tree the Trace tab and Langfuse show (values redacted the same
way). node_of turns a stored row back into the node the tree view reads; the two are pinned against each other by
tests, node for node.

Sessions get here by READING what the chat already recorded (ingest_from_main, the application database read-only):
either conversations whose session id names their run tag and scenario,

    scn__<run tag>__<scenario id>__<attempt>          for example  scn__baseline_v0__MT-014__1

or chat sessions named by the caller and filed under a run tag they give. Every turn of a session is read each time,
in the order it was asked, so a turn that arrives after an earlier read becomes the next turn rather than a lost one.

Every table is append-only and every insert is ON CONFLICT DO NOTHING: reading the same conversation twice adds
nothing, and copying these rows to another Trace Lab database (sync_push.py) can never overwrite.

G0: imports svc-deepagents (langfuse_export's value rules, through trace_tree), changes nothing in it, and writes
only Trace Lab's own tables.
"""
from __future__ import annotations

import json
import os
import re
import socket
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import structlog
from sqlalchemy import text

from src.agents import langfuse_export as lf

from . import conversations, db, expected_flags, labelling, runs, scores, spec_doc, trace_tree
from .review_errors import Invalid, NotFound

TAG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$")
SCENARIO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,59}$")
PREFIX = "scn"
SEP = "__"
#: An ordinary chat conversation: the app gives each chat a UUID as its session id. Read in only when
#: asked for (ingest_from_main(chat=True), 13 Nov 2026) and filed under CHAT_TAG with no scenario: a
#: question typed in the chat was recorded as a trace but never reached Sessions, which reads
#: scenario sessions only - so the person who asked it could not find it in the lab.
CHAT_ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
CHAT_TAG = "chat"
#: How far back the chat's conversations are looked for, by the automatic sync and by "Read chat
#: questions" when no date is given: every chat of a company is a conversation, so a week of them.
CHAT_SYNC_DAYS = 7
ATTEMPT_MAX = 99
SESSION_ID_MAX = 120
RUN_TAG_KINDS = ("baseline", "post_fix", "adhoc")
TRACE_STATUSES = ("ok", "error", "timeout", "interrupted")
BASELINE_TAG = "baseline_v0"
LEVELS = scores.LEVELS
log = structlog.get_logger(__name__)
LIST_MAX = 200
INGEST_SESSIONS = 500          # the most chat sessions one request may name
INGEST_PAGE = 25               # conversations read and filed at a time
TURNS = conversations.TURNS
SPANS = conversations.SPANS


def origin() -> str:
    """The machine this Trace Lab runs on (TRACE_LAB_ORIGIN, else the host name). A session copied to another Trace
    Lab keeps saying where it was asked."""
    raw = (os.environ.get("TRACE_LAB_ORIGIN") or "").strip() or socket.gethostname() or "local"
    return raw[:64]


# ── session ids ──────────────────────────────────────────────────────────────────────────

def ok_part(rx: re.Pattern[str], value: Any) -> bool:
    """A run tag or scenario id that can sit inside a session id and be read back: no separator in it, and no
    trailing underscore to run into the separator after it."""
    return isinstance(value, str) and rx.match(value) is not None and SEP not in value and not value.endswith("_")


def session_id_for(tag: str, scenario_id: str, attempt: int) -> str:
    """scn__<tag>__<scenario id>__<attempt>. ValueError for parts that would not read back."""
    if not ok_part(TAG_RE, tag):
        raise ValueError(f"not a run tag a session id can carry: {tag!r}")
    if not ok_part(SCENARIO_RE, scenario_id):
        raise ValueError(f"not a scenario id a session id can carry: {scenario_id!r}")
    if not isinstance(attempt, int) or isinstance(attempt, bool) or not 1 <= attempt <= ATTEMPT_MAX:
        raise ValueError(f"an attempt is a whole number from 1 to {ATTEMPT_MAX}")
    return SEP.join((PREFIX, tag, scenario_id, str(attempt)))


def parse_session_id(session_id: Any) -> tuple[str, str, int] | None:
    """(run tag, scenario id, attempt) of a scenario session, or None for any other session id."""
    if not isinstance(session_id, str):
        return None
    parts = session_id.split(SEP)
    if len(parts) != 4 or parts[0] != PREFIX:
        return None
    _, tag, scenario, attempt = parts
    if not ok_part(TAG_RE, tag) or not ok_part(SCENARIO_RE, scenario):
        return None
    if not attempt.isdigit() or attempt != str(int(attempt)) or not 1 <= int(attempt) <= ATTEMPT_MAX:
        return None
    return tag, scenario, int(attempt)


def filed_as(session_id: Any, *, chat: bool = False) -> tuple[str, str | None, int] | None:
    """(run tag, scenario id or None, attempt) a conversation is filed under, or None when it is not read.
    A scenario session says all three in its id. An ordinary chat conversation is read only when `chat`
    is asked for, under CHAT_TAG, linked to no scenario."""
    parsed = parse_session_id(session_id)
    if parsed is not None:
        return parsed
    if chat and isinstance(session_id, str) and CHAT_ID_RE.match(session_id):
        return CHAT_TAG, None, 1
    return None


@dataclass(frozen=True)
class SessionMeta:
    id: str
    organization_id: str
    run_tag: str | None
    scenario_id: str | None
    scenario_version: int | None
    attempt: int
    origin: str
    source: str                 # 'runner' (asked by Trace Lab) | 'chat' (asked through the chat and read back)
    user_id: str | None
    email: str | None
    role: str | None
    space: str | None
    started_at: datetime


# ── a turn and its spans as rows, and back ───────────────────────────────────────────

def _dt(v: Any) -> datetime | None:
    if isinstance(v, datetime):
        return v
    if isinstance(v, str) and v:
        try:
            return datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _text(v: Any) -> str | None:
    """A value for a text column: Postgres refuses the NUL character."""
    if v is None:
        return None
    return (v if isinstance(v, str) else json.dumps(v, default=str)).replace("\x00", "")


def _dumps(value: Any) -> str:
    """JSON for a jsonb column: Postgres refuses the NUL character inside text."""
    return json.dumps(value, default=str).replace("\\u0000", "")


def _jsonb(value: Any) -> str | None:
    return None if value is None else _dumps(value)


def rows_for(turn: Mapping[str, Any], spans: Sequence[Mapping[str, Any]], *, status: str | None = None,
             extra_metadata: Mapping[str, Any] | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """One turn as (its trace row, its observation rows in the tree's own order). Pure.

    The observations are every node of trace_tree.build_tree, the root included: the root is the turn itself (depth
    0, no parent), so the stored tree needs nothing else to be drawn. `status` is how the turn ended when the caller
    knows more than the record does (a timeout); otherwise ok unless the turn failed."""
    tree = trace_tree.build_tree(turn, spans)
    header, nodes = tree["turn"], tree["nodes"]
    answer = str(turn.get("answer") or "")
    metadata = {"answer_words": len(answer.split()),
                "tools": [n["name"] for n in nodes if n["type"] == "tool"],
                "purged": bool(tree["purged"]), **dict(extra_metadata or {})}
    state = status or ("ok" if header["ok"] else "error")
    if state not in TRACE_STATUSES:
        raise ValueError(f"not a trace status: {state!r}")
    trace = {"id": str(turn["turn_id"]), "name": lf.TRACE_NAME, "input": _text(nodes[0]["input"]),
             "output": _text(nodes[0]["output"]), "status": state, "header": header, "metadata": metadata,
             "started_at": _dt(header.get("started_at")), "ended_at": _dt(header.get("ended_at"))}
    observations = []
    for pos, n in enumerate(nodes):
        tokens = {"input": n["input_tokens"], "output": n["output_tokens"], "cache_read": n["cache_read_tokens"]}
        observations.append({
            "id": n["id"], "parent_observation_id": n["parent_id"], "seq": n["seq"], "depth": n["depth"],
            "type": n["type"], "name": n["name"], "agent": n["agent"], "model": n["model"],
            "start_time": _dt(n["started_at"]), "end_time": _dt(n["ended_at"]), "latency_ms": n["latency_ms"],
            "level": n["level"], "status_message": n["error"], "input": n["input"], "output": n["output"],
            "usage": tokens if any(v is not None for v in tokens.values()) else None, "cost_usd": n["cost_usd"],
            "metadata": {"pos": pos, "kind": n["kind"], "filled": n["filled"], "truncated": n["truncated"]}})
    return trace, observations


def node_of(row: Mapping[str, Any]) -> dict[str, Any]:
    """An observation row as the node the tree view reads (trace_tree._node's shape, key for key)."""
    meta = row.get("metadata") or {}
    usage = row.get("usage") or {}
    return {"id": row["id"], "parent_id": row.get("parent_observation_id"), "depth": row["depth"],
            "seq": row.get("seq"), "kind": meta.get("kind"), "type": row["type"], "name": row["name"],
            "agent": row.get("agent"), "model": row.get("model"), "started_at": lf._iso(row.get("start_time")),
            "ended_at": lf._iso(row.get("end_time")), "latency_ms": row.get("latency_ms"),
            "cost_usd": lf._num(row.get("cost_usd")), "input_tokens": usage.get("input"),
            "output_tokens": usage.get("output"), "cache_read_tokens": usage.get("cache_read"),
            "ok": row["level"] != "ERROR", "error": row.get("status_message"), "level": row["level"],
            "input": row.get("input"), "output": row.get("output"), "filled": dict(meta.get("filled") or {}),
            "truncated": bool(meta.get("truncated"))}


# ── writing ──────────────────────────────────────────────────────────────────────────

_INSERT_SESSION = text(
    "INSERT INTO tracelab.sessions (id, organization_id, run_tag, scenario_id, scenario_version, attempt, origin,"
    " source, user_id, email, role, space, started_at) VALUES (:id, CAST(:org AS uuid), :tag, :scenario, :version,"
    " :attempt, :origin, :source, CAST(:uid AS uuid), :email, :role, :space, :started) ON CONFLICT (id) DO NOTHING")
_INSERT_TRACE = text(
    "INSERT INTO tracelab.traces (id, session_id, turn_index, organization_id, name, input, output, status, header,"
    " metadata, started_at, ended_at) VALUES (:id, :sid, :idx, CAST(:org AS uuid), :name, :input, :output, :status,"
    " CAST(:header AS jsonb), CAST(:metadata AS jsonb), :started, :ended) ON CONFLICT (id) DO NOTHING")
_INSERT_OBSERVATION = text(
    "INSERT INTO tracelab.observations (trace_id, id, parent_observation_id, seq, depth, type, name, agent, model,"
    " start_time, end_time, latency_ms, level, status_message, input, output, usage, cost_usd, metadata)"
    " VALUES (:trace, :id, :parent, :seq, :depth, :type, :name, :agent, :model, :start, :end, :latency, :level,"
    " :message, CAST(:input AS jsonb), CAST(:output AS jsonb), CAST(:usage AS jsonb), :cost, CAST(:metadata AS jsonb))"
    " ON CONFLICT (trace_id, id) DO NOTHING")


class OtherCompany(Exception):
    """A session id that is already stored for another company. Nothing of the caller's is filed under it."""


async def write_session(conn: Any, meta: SessionMeta,
                        traces: Sequence[tuple[Mapping[str, Any], Sequence[Mapping[str, Any]]]]) -> dict[str, int]:
    """Store one session with its traces (rows_for results, in the order asked) inside the caller's transaction, and
    say how many rows of each kind were new. A row that is already there is left as it is.

    A trace not stored yet is added as the session's NEXT turn (after the highest turn stored), so a turn that
    reaches the chat's records late is appended rather than colliding with a stored turn's number. A session id
    stored for another company raises OtherCompany: the id is the same on every machine by design, and one company's
    turns must never be filed under another's session."""
    if not meta.id or len(meta.id) > SESSION_ID_MAX:
        raise ValueError("a session id has between 1 and 120 characters")
    new = {"sessions": 0, "traces": 0, "observations": 0}
    r = await conn.execute(_INSERT_SESSION, {
        "id": meta.id, "org": meta.organization_id, "tag": meta.run_tag, "scenario": meta.scenario_id,
        "version": meta.scenario_version, "attempt": meta.attempt, "origin": meta.origin, "source": meta.source,
        "uid": meta.user_id, "email": meta.email, "role": meta.role, "space": meta.space, "started": meta.started_at})
    new["sessions"] += max(r.rowcount, 0)
    owner = (await conn.execute(text("SELECT organization_id::text FROM tracelab.sessions WHERE id = :id"),
                                {"id": meta.id})).scalar()
    if str(owner) != str(meta.organization_id):
        raise OtherCompany(meta.id)
    stored = {row[0]: row[1] for row in (await conn.execute(text(
        "SELECT id, turn_index FROM tracelab.traces WHERE session_id = :id"), {"id": meta.id})).all()}
    index = max(stored.values(), default=0)
    for trace, observations in traces:
        if trace["id"] not in stored:
            r = await conn.execute(_INSERT_TRACE, {
                "id": trace["id"], "sid": meta.id, "idx": index + 1, "org": meta.organization_id, "name": trace["name"],
                "input": trace["input"], "output": trace["output"], "status": trace["status"],
                "header": _dumps(trace["header"]), "metadata": _dumps(trace["metadata"]),
                "started": trace["started_at"], "ended": trace["ended_at"]})
            if not r.rowcount:
                continue                    # that trace id is another session's: its steps are not ours to add to
            index += 1
            stored[trace["id"]] = index
            new["traces"] += 1
        for o in observations:
            r = await conn.execute(_INSERT_OBSERVATION, {
                "trace": trace["id"], "id": o["id"], "parent": o["parent_observation_id"], "seq": o["seq"],
                "depth": o["depth"], "type": o["type"], "name": o["name"], "agent": o["agent"], "model": o["model"],
                "start": o["start_time"], "end": o["end_time"], "latency": o["latency_ms"], "level": o["level"],
                "message": _text(o["status_message"]), "input": _jsonb(o["input"]), "output": _jsonb(o["output"]),
                "usage": _jsonb(o["usage"]), "cost": o["cost_usd"], "metadata": _dumps(o["metadata"])})
            new["observations"] += max(r.rowcount, 0)
    return new


# ── run tags ─────────────────────────────────────────────────────────────────────────

async def _ensure_tag(conn: Any, tag: str, *, kind: str | None = None, note: str | None = None,
                      by: labelling.Member | None = None) -> None:
    await conn.execute(text(
        "INSERT INTO tracelab.run_tags (tag, kind, origin, note, created_by, created_by_email)"
        " VALUES (:tag, :kind, :origin, :note, CAST(:by AS uuid), :email) ON CONFLICT DO NOTHING"),
        {"tag": tag, "kind": kind or ("baseline" if tag == BASELINE_TAG else "adhoc"), "origin": origin(),
         "note": note, "by": by.user_id if by else None, "email": by.email if by else None})


def _tag_out(r: Mapping[str, Any]) -> dict[str, Any]:
    return {"tag": r["tag"], "kind": r["kind"], "origin": r["origin"], "note": r["note"],
            "spec_version": r["spec_version"], "code_revision": r["code_revision"],
            "created_at": lf._iso(r["created_at"]), "sessions": int(r.get("sessions") or 0)}


async def create_run_tag(m: labelling.Member, tag: str, *, kind: str = "adhoc", note: str | None = None) -> dict[str, Any]:
    """A new run tag, or the one already there under that name (asking twice is not an error). Curators only."""
    runs.require_curator(m)
    if not ok_part(TAG_RE, tag):
        raise Invalid("bad_run_tag", "A run tag is 1 to 40 letters, digits, dots, dashes or single underscores, "
                                     "starting with a letter or digit, for example baseline_v0.")
    if kind not in RUN_TAG_KINDS:
        raise Invalid("bad_run_tag_kind", "A run tag is a baseline, a post_fix run or adhoc.")
    async with db.lab_engine().begin() as conn:
        await _ensure_tag(conn, tag, kind=kind, note=(note or "").strip()[:500] or None, by=m)
        row = (await conn.execute(text("SELECT r.*, 0 AS sessions FROM tracelab.run_tags r WHERE r.tag = :t"),
                                  {"t": tag})).mappings().one()
    return _tag_out(row)


async def list_run_tags(m: labelling.Member) -> list[dict[str, Any]]:
    """The run tags the caller can see sessions under (every tag for the platform team and a consultant), newest
    first, each with how many of its sessions the caller may open."""
    labelling.require_active(m)
    scope, p = runs._scope(m, "s")
    seen = "TRUE" if m.all_companies else "(x.sessions > 0 OR x.created_by = CAST(:me AS uuid))"
    async with conversations._lab_read() as conn:
        rows = (await conn.execute(text(
            "SELECT * FROM (SELECT r.*, (SELECT count(*) FROM tracelab.sessions s WHERE s.run_tag = r.tag"
            f" AND {scope}) AS sessions FROM tracelab.run_tags r) x WHERE {seen} ORDER BY x.created_at DESC, x.tag"),
            {**p, "me": m.user_id})).mappings().all()
    return [_tag_out(r) for r in rows]


# ── reading what the chat recorded ────────────────────────────────────────────────────

async def _scenario_link(conn: Any, scenario_id: str | None, org: str) -> tuple[str, int] | None:
    """The newest version of a scenario that the session's company may see (its own, or one that names no company),
    or None when there is none. Another company's version is never the one a session is linked to."""
    if not scenario_id:
        return None
    version = (await conn.execute(text(
        "SELECT max(version) FROM tracelab.scenarios WHERE scenario_id = :id"
        " AND (organization_id IS NULL OR organization_id = CAST(:org AS uuid))"),
        {"id": scenario_id, "org": org})).scalar()
    return (scenario_id, int(version)) if version is not None else None


#: How long the automatic sync leaves a conversation it could not file (waiting, failed) before reading it again.
HOLD_S = 15 * 60
_HELD: dict[str, float] = {}


def _clock() -> float:
    return time.monotonic()


async def _read_page(owners: Mapping[str, str]) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    """The turns (of each conversation's own company) and their spans, for one page of conversations."""
    async with conversations._read() as conn:
        turns = [dict(r) for r in (await conn.execute(text(
            f"SELECT tt.* FROM {TURNS} tt WHERE tt.session_id = ANY(CAST(:ids AS text[]))"
            " ORDER BY tt.session_id, tt.started_at, tt.turn_id"), {"ids": list(owners)})).mappings().all()]
        turns = [t for t in turns if str(t.get("organization_id")) == owners.get(t["session_id"])]
        spans: dict[str, list[dict[str, Any]]] = {}
        if turns:
            for s in (await conn.execute(text(
                    f"SELECT * FROM {SPANS} WHERE turn_id = ANY(CAST(:tids AS text[])) ORDER BY turn_id, seq"),
                    {"tids": [t["turn_id"] for t in turns]})).mappings().all():
                spans.setdefault(s["turn_id"], []).append(dict(s))
    return turns, spans


async def _curators(pairs: set[tuple[str, str]]) -> set[str]:
    """The session ids, of (company, session id) pairs, whose first asker may curate Trace Lab in that company: the
    platform team, a company admin, an active consultant, or an active Trace Lab admin of the company."""
    if not pairs:
        return set()
    ids = sorted({sid for _o, sid in pairs})
    async with conversations._read() as conn:
        askers = {r[0]: str(r[1]) if r[1] else None for r in (await conn.execute(text(
            f"SELECT DISTINCT ON (tt.session_id) tt.session_id, tt.user_id FROM {TURNS} tt"
            " WHERE tt.session_id = ANY(CAST(:ids AS text[])) ORDER BY tt.session_id, tt.started_at, tt.turn_id"),
            {"ids": ids})).all()}
        uids = sorted({u for u in askers.values() if u})
        roles: dict[str, tuple[str | None, str | None]] = {}
        if uids:
            try:                                # the accounts table is the sign-in service's: optional here
                async with conn.begin_nested():
                    roles = {str(r[0]): (r[1], str(r[2]) if r[2] else None) for r in (await conn.execute(text(
                        f"SELECT id, platform_role, organization_id FROM {labelling.USERS}"
                        " WHERE id = ANY(CAST(:u AS uuid[]))"), {"u": uids})).all()}
            except Exception as exc:            # noqa: BLE001
                log.warning("trace_lab.eval.accounts_unavailable", error=type(exc).__name__)
    if not uids:
        return set()
    async with conversations._lab_read() as conn:
        consultants = {str(r[0]) for r in (await conn.execute(text(
            "SELECT DISTINCT ON (user_id) user_id, status FROM tracelab.lab_platform_members"
            " WHERE user_id = ANY(CAST(:u AS uuid[])) ORDER BY user_id, created_at DESC, id DESC"),
            {"u": uids})).all() if r[1] == "active"}
        admins = {(str(r[0]), str(r[1])) for r in (await conn.execute(text(
            "SELECT organization_id, user_id FROM tracelab.lab_members WHERE superseded_by IS NULL"
            " AND lab_role = 'admin' AND status = 'active' AND user_id = ANY(CAST(:u AS uuid[]))"), {"u": uids})).all()}
    out = set()
    for org, sid in pairs:
        uid = askers.get(sid)
        if not uid:
            continue
        role, own = roles.get(uid, (None, None))
        if (role == "superadmin" or (role == "admin" and own == org) or uid in consultants
                or (org, uid) in admins):
            out.add(sid)
    return out


async def ingest_from_main(*, since: datetime, run_tag: str | None = None,
                           sessions: Mapping[str, str | None] | None = None, org: str | None = None,
                           by: labelling.Member | None = None, chat: bool = False,
                           scenarios: bool = True, remember: bool = False,
                           curators_only: bool = False) -> dict[str, int]:
    """File the conversations the chat recorded as sessions, and say what happened:

        sessions, traces, observations   rows that were new
        waiting          conversations whose scenario is not stored yet: not filed, so the next read can link them
        other_company    conversations whose session id is already another company's session: never filed under it
        skipped          conversations that could not be read as a scenario session (a malformed id, no company)
        failed           conversations that could not be filed; the others still were

    Without `sessions`: every conversation with a turn since `since` whose session id is a scenario session id; its
    run tag, scenario and attempt come from the id. With `sessions` ({chat session id: scenario id or None}) and
    `run_tag`: exactly those conversations, filed under that tag (None files one linked to no scenario). `org`
    narrows either to one company. With `chat` (and no `sessions`): the ordinary chat conversations since
    `since` as well - a session id the app made (a UUID) - filed under the run tag "chat", linked to no scenario.
    `scenarios=False` leaves the scenario sessions out: the automatic sync reads chat conversations only, because a
    scenario still being asked must not be filed until a person reads it in (a session row means it is finished).

    Every conversation is read, INGEST_PAGE at a time, and only those with turns the store does not have. The
    application database is only read. A run tag that is not there yet is made (baseline_v0 as a baseline).

    The automatic sync (13 Nov 2026 review) passes `remember`: a conversation left waiting or failed is held for HOLD_S
    and not read again until then ("held"); and `curators_only`: a scenario session is filed only when its asker may
    curate Trace Lab, so a session id anyone can type is never filed by itself ("not_curator")."""
    named = dict(sessions or {})
    if named:
        if not ok_part(TAG_RE, run_tag):
            raise Invalid("bad_run_tag", "Name the run tag these sessions belong to, for example baseline_v0.")
        for sid, scenario in named.items():
            if not isinstance(sid, str) or not 1 <= len(sid) <= SESSION_ID_MAX:
                raise Invalid("bad_session_id", "A session id has between 1 and 120 characters.")
            if scenario is not None and not ok_part(SCENARIO_RE, scenario):
                raise Invalid("bad_scenario_id", f"{scenario!r} is not a scenario id.")
    company = " AND tt.organization_id = CAST(:org AS uuid)" if org else ""
    p: dict[str, Any] = {"org": org} if org else {}
    out = {"sessions": 0, "traces": 0, "observations": 0, "waiting": 0, "other_company": 0, "skipped": 0, "failed": 0,
           "held": 0, "not_curator": 0}

    # Which conversations there are, per company, and how many turns each has.
    async with conversations._read() as conn:
        if named:
            which, wp = "tt.session_id = ANY(CAST(:ids AS text[]))", {"ids": list(named)}
        else:
            kinds = (["x.session_id LIKE 'scn\\_\\_%'"] if scenarios else []) + (["x.session_id ~ :chat_id"] if chat else [])
            if not kinds:
                return out
            ids = " OR ".join(kinds)
            which = f"tt.session_id IN (SELECT x.session_id FROM {TURNS} x WHERE ({ids}) AND x.started_at >= :since)"
            wp = {"since": since, **({"chat_id": CHAT_ID_RE.pattern} if chat else {})}
        found = [dict(r) for r in (await conn.execute(text(
            f"SELECT tt.session_id, tt.organization_id::text AS org, count(*) AS turns FROM {TURNS} tt"
            f" WHERE {which}{company} GROUP BY tt.session_id, tt.organization_id"
            " ORDER BY tt.session_id, tt.organization_id"), {**p, **wp})).mappings().all()]
    if not found:
        return out
    async with conversations._lab_read() as conn:
        have = {r[0]: (r[1], int(r[2])) for r in (await conn.execute(text(
            "SELECT s.id, s.organization_id::text, (SELECT count(*) FROM tracelab.traces t WHERE t.session_id = s.id)"
            " FROM tracelab.sessions s WHERE s.id = ANY(CAST(:ids AS text[]))"),
            {"ids": sorted({f["session_id"] for f in found})})).all()}

    todo: list[tuple[str, str, str, str | None, int]] = []        # (session id, company, run tag, scenario, attempt)
    claimed: dict[str, str] = {sid: owner for sid, (owner, _n) in have.items()}
    for f in found:
        sid, owner = f["session_id"], f["org"]
        if owner is None:
            out["skipped"] += 1                 # a turn of no company belongs to nobody's Trace Lab
            continue
        if named:
            tag, scenario, attempt = str(run_tag), named.get(sid), 1
        else:
            parsed = filed_as(sid, chat=chat)
            if parsed is None:
                out["skipped"] += 1
                continue
            tag, scenario, attempt = parsed
        if claimed.setdefault(sid, owner) != owner:
            out["other_company"] += 1
            continue
        if sid in have and have[sid][1] >= int(f["turns"]):
            continue                            # nothing new in it
        if remember and _HELD.get(sid, 0) > _clock():
            out["held"] += 1                    # left waiting or failed a short while ago: read again later
            continue
        todo.append((sid, owner, tag, scenario, attempt))

    if curators_only and todo:
        allowed = await _curators({(owner, sid) for sid, owner, _t, sc, _a in todo if sc is not None})
        kept = []
        for item in todo:
            if item[3] is not None and item[0] not in allowed:
                out["not_curator"] += 1
                continue
            kept.append(item)
        todo = kept

    here = origin()
    for at in range(0, len(todo), INGEST_PAGE):
        page = todo[at:at + INGEST_PAGE]
        owners = {sid: owner for sid, owner, *_ in page}
        try:
            turns, spans = await _read_page(owners)
        except Exception as exc:                # noqa: BLE001 - one slow page must not stop the others
            out["failed"] += len(page)
            log.warning("trace_lab.eval.ingest_page_failed", sessions=len(page), error=type(exc).__name__)
            if remember:
                for sid in owners:
                    _HELD[sid] = _clock() + HOLD_S
            continue
        by_session: dict[str, list[dict[str, Any]]] = {}
        for t in turns:
            by_session.setdefault(t["session_id"], []).append(t)
        for sid, owner, tag, scenario, attempt in page:
            its = by_session.get(sid) or []
            if not its:
                continue
            first = its[0]
            try:
                async with db.lab_engine().begin() as conn:
                    link = await _scenario_link(conn, scenario, owner)
                    if scenario and link is None:
                        out["waiting"] += 1     # filed by a later read, once its scenario is stored: a session
                        if remember:            # row cannot be changed, so it is never filed without its link
                            _HELD[sid] = _clock() + HOLD_S
                        continue
                    await _ensure_tag(conn, tag, by=by)
                    meta = SessionMeta(
                        id=sid, organization_id=owner, run_tag=tag, scenario_id=link[0] if link else None,
                        scenario_version=link[1] if link else None, attempt=attempt, origin=here, source="chat",
                        user_id=str(first["user_id"]) if first.get("user_id") else None, email=first.get("email"),
                        role=None, space=None, started_at=_dt(first.get("started_at")))
                    new = await write_session(conn, meta, [rows_for(t, spans.get(t["turn_id"], [])) for t in its])
            except OtherCompany:
                out["other_company"] += 1
                continue
            except Exception as exc:            # noqa: BLE001 - one conversation must not stop the others
                out["failed"] += 1
                if remember:
                    _HELD[sid] = _clock() + HOLD_S
                log.warning("trace_lab.eval.ingest_failed", session=sid, error=type(exc).__name__)
                continue
            for k, v in new.items():
                out[k] += v
    return out


# ── reads ────────────────────────────────────────────────────────────────────────────

_SESSION_COLS = ("s.id, s.organization_id::text AS organization_id, s.run_tag, s.scenario_id, s.scenario_version,"
                 " s.attempt, s.origin, s.source, s.email, s.role, s.space, s.started_at, s.created_at, sc.type AS type")
_SESSION_STATS = (
    "(SELECT count(*) FROM tracelab.traces t WHERE t.session_id = s.id) AS turns,"
    " (EXISTS (SELECT 1 FROM tracelab.traces t WHERE t.session_id = s.id AND t.status <> 'ok')"
    "  OR EXISTS (SELECT 1 FROM tracelab.observations o JOIN tracelab.traces t ON t.id = o.trace_id"
    "             WHERE t.session_id = s.id AND o.level = 'ERROR')) AS failed,"
    f" (SELECT count(*) FROM tracelab.traces t WHERE t.session_id = s.id AND {expected_flags.flagged_sql('t')})"
    " AS expected_flagged")


def _stats(spec_version: str | None) -> str:
    """The per-session columns worked out in SQL: its turns, whether it failed, and whether the scenario version it
    was asked with is out of date against the current specification (bind :spec_current when one is loaded)."""
    return f"{_SESSION_STATS}, {spec_doc.stale_sql('sc', spec_version)} AS stale"


_SESSION_FROM = ("FROM tracelab.sessions s LEFT JOIN tracelab.scenarios sc"
                 " ON sc.scenario_id = s.scenario_id AND sc.version = s.scenario_version")
_OBSERVATION_COLS = ("o.id, o.parent_observation_id, o.seq, o.depth, o.type, o.name, o.agent, o.model, o.start_time,"
                     " o.end_time, o.latency_ms, o.level, o.status_message, o.input::text AS input,"
                     " o.output::text AS output, o.usage::text AS usage, o.cost_usd, o.metadata::text AS metadata")


def _loads(v: Any) -> Any:
    return json.loads(v) if isinstance(v, str) else v


def _session_out(r: Mapping[str, Any]) -> dict[str, Any]:
    out = {"id": r["id"], "organization_id": r["organization_id"], "run_tag": r["run_tag"],
           "scenario_id": r["scenario_id"], "scenario_version": r["scenario_version"], "attempt": r["attempt"],
           "origin": r["origin"], "source": r["source"], "email": r["email"], "role": r["role"], "space": r["space"],
           "type": r["type"], "started_at": lf._iso(r["started_at"]),
           "scores": {level: None for level in LEVELS}}
    if "turns" in r:
        out["turns"] = int(r["turns"] or 0)
        out["status"] = "failed" if r["failed"] else "ok"
        out["stale"] = bool(r["stale"])
        out["expected_flagged"] = int(r.get("expected_flagged") or 0)
    return out


async def list_sessions(m: labelling.Member, *, run_tag: str | None = None, type: str | None = None,  # noqa: A002
                        status: str | None = None, level: str | None = None, verdict: str | None = None,
                        needs_sme: bool = False, stale: bool = False, origin: str | None = None,
                        all_attempts: bool = False, before: datetime | None = None, limit: int = 50) -> dict[str, Any]:
    """The sessions the caller may open, newest first: {"sessions": [...], "next": cursor or None}.

    A scenario asked more than once under one run tag shows its latest attempt only, unless all_attempts. A session's
    status is "failed" when a turn did not end ok or any step in it failed, else "ok"."""
    labelling.require_active(m)
    if status not in (None, "", "ok", "failed"):
        raise Invalid("bad_status", "A session's status is ok or failed.")
    scope, p = runs._scope(m, "s")
    inner, outer = [scope], ["(CAST(:all AS boolean) OR x.rn = 1 OR x.scenario_id IS NULL OR x.run_tag IS NULL)"]
    p["all"] = bool(all_attempts)
    if run_tag:
        inner.append("s.run_tag = :run_tag")
        p["run_tag"] = run_tag
    if type:
        inner.append("sc.type = :type")
        p["type"] = type
    if origin:
        inner.append("s.origin = :origin")
        p["origin"] = origin
    if status:
        outer.append("x.failed = CAST(:failed AS boolean)")
        p["failed"] = status == "failed"
    if before is not None:
        outer.append("x.started_at < :before")
        p["before"] = before
    if stale:
        outer.append("x.stale")
    extra = ""
    if level or verdict:
        if level not in LEVELS:
            raise Invalid("bad_level", "Sessions are filtered by the logic, ux or domain level.")
        if verdict not in scores.LIST_VERDICTS:
            raise Invalid("bad_verdict", "A level's verdict is pass, fail or not_evaluated.")
        extra += f", ({scores.LEVEL_VERDICT_SQL}) AS level_verdict"
        outer.append("x.level_verdict = :verdict")
        p["level"], p["verdict"] = level, verdict
    if needs_sme:
        extra += f", ({scores.NEEDS_SME_SQL}) AS needs_sme"
        outer.append("x.needs_sme")
    p["lim"] = max(1, min(int(limit or 50), LIST_MAX))
    async with conversations._lab_read() as conn:
        spec_version, _hashes = await spec_doc.current_hashes(conn)
        if spec_version is not None:
            p["spec_current"] = spec_version
        rows = (await conn.execute(text(
            f"SELECT * FROM (SELECT {_SESSION_COLS}, {_stats(spec_version)}{extra}, row_number() OVER (PARTITION BY s.run_tag,"
            f" s.scenario_id ORDER BY s.attempt DESC, s.created_at DESC, s.id DESC) AS rn {_SESSION_FROM}"
            f" WHERE {' AND '.join(inner)}) x WHERE {' AND '.join(outer)}"
            " ORDER BY x.started_at DESC, x.id DESC LIMIT :lim"), p)).mappings().all()
        sessions = [_session_out(r) for r in rows]
        # Each session's verdict per level, from its traces' current scores.
        ids = [x["id"] for x in sessions]
        traces = (await conn.execute(text(
            "SELECT t.session_id, t.id FROM tracelab.traces t WHERE t.session_id = ANY(CAST(:ids AS text[]))"
            " ORDER BY t.session_id, t.turn_index"), {"ids": ids})).all() if ids else []
        current = await scores.current_scores(conn, [t[1] for t in traces])
        of: dict[str, list[dict[str, Any]]] = {}
        for sid, tid in traces:
            of.setdefault(sid, []).append(current[tid])
        for x in sessions:
            x["scores"] = scores.session_scores(of.get(x["id"], []))
    more = len(sessions) == p["lim"]
    return {"sessions": sessions, "next": sessions[-1]["started_at"] if more else None}


async def _session_row(conn: Any, m: labelling.Member, session_id: str) -> Mapping[str, Any]:
    scope, p = runs._scope(m, "s")
    spec_version, _hashes = await spec_doc.current_hashes(conn)
    if spec_version is not None:
        p["spec_current"] = spec_version
    row = (await conn.execute(text(
        f"SELECT {_SESSION_COLS}, {_stats(spec_version)} {_SESSION_FROM} WHERE s.id = :id AND {scope}"),
        {**p, "id": session_id})).mappings().first()
    if row is None:
        raise NotFound("session_not_found", "That session is not in Trace Lab.")
    return row


async def get_session(m: labelling.Member, session_id: str) -> dict[str, Any]:
    """One session: itself, the scenario version it was asked with (its turns and their expected outcomes), its
    traces in turn order and their scores."""
    labelling.require_active(m)
    async with conversations._lab_read() as conn:
        row = await _session_row(conn, m, session_id)
        scenario = None
        if row["scenario_id"]:
            sc = (await conn.execute(text(
                "SELECT scenario_id, version, type, spec_sections, role, space, turns::text AS turns,"
                " scope::text AS scope, faults::text AS faults, grounded_on::text AS grounded_on,"
                " domain_rules::text AS domain_rules, spec_version, spec_hashes::text AS spec_hashes, status"
                " FROM tracelab.scenarios WHERE scenario_id = :id AND version = :v"
                " AND (organization_id IS NULL OR organization_id = CAST(:org AS uuid))"),
                {"id": row["scenario_id"], "v": row["scenario_version"], "org": row["organization_id"]})).mappings().first()
            if sc is not None:
                scenario = {**dict(sc), **{k: _loads(sc[k]) for k in
                                           ("turns", "scope", "faults", "grounded_on", "domain_rules", "spec_hashes")},
                            "spec_sections": list(sc["spec_sections"] or [])}
                _version, hashes = await spec_doc.current_hashes(conn)
                scenario["stale"] = spec_doc.stale_sections(scenario["spec_hashes"], hashes, scenario["spec_sections"])
        traces = [{"id": t["id"], "turn_index": t["turn_index"], "name": t["name"], "input": t["input"],
                   "output": t["output"], "status": t["status"], "metadata": _loads(t["metadata"]) or {},
                   "started_at": lf._iso(t["started_at"]), "ended_at": lf._iso(t["ended_at"]),
                   "scores": {level: None for level in LEVELS}}
                  for t in (await conn.execute(text(
                      "SELECT id, turn_index, name, input, output, status, metadata::text AS metadata, started_at,"
                      " ended_at FROM tracelab.traces WHERE session_id = :id"
                      " AND organization_id = CAST(:org AS uuid) ORDER BY turn_index"),
                      {"id": session_id, "org": row["organization_id"]})).mappings().all()]
        ids = [t["id"] for t in traces]
        current, past = await scores.current_scores(conn, ids), await scores.history(conn, ids)
        flags = await expected_flags.flagged_traces(conn, ids)
        for t in traces:
            t["scores"], t["score_history"] = current[t["id"]], past[t["id"]]
            f = flags.get(t["id"]) or {}
            t["expected_flag"] = {"flagged": bool(f), "count": int(f.get("count") or 0),
                                  "mine": str(m.user_id) in (f.get("people") or [])}
    return {"session": _session_out(row), "scenario": scenario, "traces": traces}


async def nodes_of(conn: Any, trace_id: str) -> list[dict[str, Any]]:
    """A trace's observations as the tree view's nodes, in the tree's own order."""
    rows = (await conn.execute(text(
        f"SELECT {_OBSERVATION_COLS} FROM tracelab.observations o WHERE o.trace_id = :id"
        " ORDER BY (o.metadata->>'pos')::int NULLS LAST, o.seq NULLS LAST, o.id"), {"id": trace_id})).mappings().all()
    return [node_of({**dict(r), **{k: _loads(r[k]) for k in ("input", "output", "usage", "metadata")}}) for r in rows]


async def trace_tree_of(m: labelling.Member, trace_id: str) -> dict[str, Any]:
    """One trace as the tree view reads it: {"turn": header, "nodes": [...], "purged": bool}."""
    labelling.require_active(m)
    scope, p = runs._scope(m, "t")
    async with conversations._lab_read() as conn:
        t = (await conn.execute(text(
            f"SELECT t.header::text AS header, t.metadata::text AS metadata FROM tracelab.traces t"
            f" WHERE t.id = :id AND {scope}"), {**p, "id": trace_id})).mappings().first()
        if t is None:
            raise NotFound("trace_not_found", "That trace is not in Trace Lab.")
        nodes = await nodes_of(conn, trace_id)
    return {"turn": _loads(t["header"]), "nodes": nodes, "purged": bool((_loads(t["metadata"]) or {}).get("purged"))}


async def trace_notes(m: labelling.Member, trace_id: str) -> dict[str, Any]:
    """What the session page needs to show and write notes on a trace's spans: {"trace_id", "organization_id",
    "record": the chat's own record of the turn (its messages give each span the note keys Trace Lab has always used;
    built from the stored trace when only Trace Lab has the turn; None when it is nowhere), "labels": everyone's live notes on it, "me"}. Notes are written through the
    existing /lab/turns/{turn_id}/step-labels calls, as the trace's company."""
    labelling.require_active(m)
    scope, p = runs._scope(m, "t")
    async with conversations._lab_read() as conn:
        org = (await conn.execute(text(
            f"SELECT t.organization_id::text FROM tracelab.traces t WHERE t.id = :id AND {scope}"),
            {**p, "id": trace_id})).scalar()
    if org is None:
        raise NotFound("trace_not_found", "That trace is not in Trace Lab.")
    mm = labelling.for_org(m, org)
    got = await conversations.get_turn(labelling.company_view(mm.principal, mm.org), mm.org, trace_id)
    if got is None:                           # copied from another machine: the record is built from the stored trace
        from . import stored_turn
        got = await stored_turn.get(org, trace_id)
    return {"trace_id": trace_id, "organization_id": org, "record": got["trace"] if got else None,
            "labels": await labelling.labels_for_turn(mm, trace_id), "me": m.user_id}
