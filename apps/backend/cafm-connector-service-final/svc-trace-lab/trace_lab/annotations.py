"""Open coding: a reviewer's verdict on one answer, and comments on a conversation (Trace Lab Phase 4).

A verdict is right or wrong. Wrong carries a note on the first failure and an anchor: the step (span id,
or the turn's own question/answer), a verbatim quote and the quote's occurrence number. Right is stored
with the note "no failure observed" and no anchor. Rows are versions: an edit is a new row, a delete is a
tombstone row, and a write whose `base` is not the current head is refused with review_errors.Stale (409).
Other reviewers' verdicts stay hidden until the caller has saved one (blind until saved); the read functions
here enforce that, not the UI.

Two databases, never one SQL statement across them (Golden Rule G0). The chat's turns and spans are READ
through db.main_engine() (read-only, company scoped); everything Trace Lab keeps - annotations, their events,
sample batches, evidence snapshots - lives in the lab database and is read and written through db.lab_engine().
Where a question needs both, the turn ids are read from one side and handed to the other as
`= ANY(CAST(:ids AS text[]))`. A turn's evidence is copied into the lab database (evidence.snapshot) before a
verdict is written on it, so the note and its quote outlive the chat's 90-day span purge; the anchor also
keeps its own copy of the quoted text and the step's name, kind and agent.

The text a quote is checked against is the block text of the normalized, redacted turn
(normalize.normalize_turn): a step's text is normalize.block_text, which normalize_turn copies onto each span
message as `block`; the question and the answer are the user and assistant messages' `text`. These are the
strings GET /annotations returns as `blocks` and the Conversations view renders, so a selection is always a
substring of what the server checks. The turn is normalized from the same inputs the view uses (the stored row
and spans, and for a live turn the earliest activity row), so the question is the asker's own words in both.

Golden answers can be judged too: a golden turn lives in the lab database (golden_results and its evidence
snapshot), so its turn and conversation keys are resolved there, never in the chat's tables.

Phase 5 adds what happens to a Wrong note afterwards (the last section): it is filed under a failure mode (a present
human_open_code label, with the note and its quote on the label), or set aside by the platform team as "no mode fits" or
"not a failure" (an append-only dispose event on its lineage that ends as soon as the note is edited), and the unfiled
list shows the rest, oldest first, under the same blind rule. Editing or deleting a note closes the grouping proposals
that cite its old version.
"""
from __future__ import annotations

import json
import re
import uuid
from collections.abc import Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from src.agents.trace import _iso

from . import conversations, db, evidence, normalize
from .review_errors import Forbidden, Invalid, NotFound, Stale

VERDICTS = ("right", "wrong")
FIELDS = ("question", "answer", "input", "output")
TURN_FIELDS = ("question", "answer")
NO_FAILURE_NOTE = "no failure observed"
NOTE_MAX = 2000
QUOTE_MAX = 2000
TIMEOUT = "SET LOCAL statement_timeout = '10s'"
#: How long a write waits for another writer's lock on the same head before it gives up with a 409. A winner holds
#: the lock for a few milliseconds; a writer never queues behind another reviewer for longer than this.
LOCK_WAIT_MS = 2000
LOCK_NOT_AVAILABLE = "55P03"
#: Why a turn was copied into the evidence tables (evidence_turns.reason, at most 40 characters).
SNAPSHOT_REASON = "annotated"
MAX_TURNS_OF_KEY = 500
REVIEWED_MODES = ("reviewed", "unreviewed", "mine")
#: The messages whose `text` is the turn's own question and answer block.
_TURN_TEXT_ROLES = {"question": "user", "answer": "assistant"}
_HEAD_RACES = ("ux_tl_ann_verdict_head", "ux_tl_ann_supersedes", "ux_tl_ann_lineage_head")
_ALIAS_RE = re.compile(r"^[a-z_][a-z0-9_]{0,30}$")


@dataclass(frozen=True)
class Anchor:
    span_id: str | None
    field: str
    quote: str
    occurrence: int = 1


# ── pure helpers ───────────────────────────────────────────────────────────────────────

def locate_quote(text_: str, quote: str, occurrence: int) -> int | None:
    """Code-point start of the n-th (1-based) occurrence; overlapping matches count. None if absent."""
    if not quote or occurrence < 1:
        return None
    pos = -1
    for _ in range(occurrence):
        pos = text_.find(quote, pos + 1)
        if pos < 0:
            return None
    return pos


def block_key(span_id: str | None, field: str) -> str:
    """'<span id>:<field>' for a step, ':question' / ':answer' for the turn's own text."""
    return (":" if field in TURN_FIELDS else f"{span_id or ''}:") + field


def block_texts(record: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Every annotatable block of a normalized turn: {key: {span_id, field, text}}. First message wins.

    A step's text is the message's `block` (normalize.block_text); a message whose `block` is None has no
    step text and is skipped. The question and the answer are the user and assistant messages' redacted
    `text`. Nothing is formatted here."""
    out: dict[str, dict[str, Any]] = {}
    for m in record.get("messages") or ():
        anchor = m.get("anchor") or None
        if not anchor or anchor.get("field") not in FIELDS:
            continue
        field = anchor["field"]
        if field in TURN_FIELDS:
            if m.get("role") != _TURN_TEXT_ROLES[field]:
                continue
            span_id, value = None, m.get("text") or ""
        else:
            value = m.get("block")
            if value is None:
                continue
            span_id = str(anchor.get("span_id")) if anchor.get("span_id") else None
        key = block_key(span_id, field)
        if key not in out:
            out[key] = {"span_id": span_id, "field": field, "text": str(value)}
    return out


def _whole_number(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v >= 1


def validate_verdict(verdict: str, note: str | None, anchor: Anchor | None) -> tuple[str, Anchor | None]:
    """The note and anchor to store, or review_errors.Invalid. Right -> NO_FAILURE_NOTE and no anchor."""
    if verdict not in VERDICTS:
        raise Invalid("bad_verdict", "A verdict is Right or Wrong.")
    if verdict == "right":
        if anchor is not None:
            raise Invalid("anchor_on_right")
        return NO_FAILURE_NOTE, None
    if note is None or not note.strip():
        raise Invalid("note_required")
    if len(note) > NOTE_MAX:
        raise Invalid("note_required", f"Keep the note under {NOTE_MAX} characters.")
    if anchor is None or not anchor.quote or not anchor.quote.strip() or len(anchor.quote) > QUOTE_MAX:
        raise Invalid("anchor_required")
    if anchor.field not in FIELDS or not _whole_number(anchor.occurrence):
        raise Invalid("anchor_required")
    if anchor.field in TURN_FIELDS:
        anchor = Anchor(None, anchor.field, anchor.quote, anchor.occurrence)
    elif not anchor.span_id:
        raise Invalid("span_not_in_turn")
    return note, anchor


def validate_comment(note: str | None) -> str:
    if note is None or not note.strip():
        raise Invalid("note_required", "Write the comment first.")
    if len(note) > NOTE_MAX:
        raise Invalid("note_required", f"Keep the comment under {NOTE_MAX} characters.")
    return note


def key_where(conversation_key: str, alias: str = "tt") -> tuple[str, dict[str, str]]:
    """The turns of one conversation key (conversations.GROUP_KEY_SQL: t:<turn>, r:<turn>, s:<session>)."""
    kind, _, value = str(conversation_key).partition(":")
    if not value or kind not in ("t", "r", "s"):
        raise NotFound("turn_not_found")
    if kind == "s":
        return (f"{alias}.session_id = :tl_key_v AND {alias}.session_id NOT LIKE 'rerun%'", {"tl_key_v": value})
    return (f"{alias}.turn_id = :tl_key_v", {"tl_key_v": value})


def _check_alias(alias: str) -> str:
    if not isinstance(alias, str) or not _ALIAS_RE.match(alias):
        raise ValueError(f"bad alias {alias!r}")
    return alias


def company_clause(org: str | None, alias: str) -> tuple[str, dict[str, str]]:
    """One company's rows of a lab table aliased `alias`; TRUE for the platform view (a superadmin not acting)."""
    _check_alias(alias)
    if not org:
        return "TRUE", {}
    return f"{alias}.organization_id = CAST(:tl_org AS uuid)", {"tl_org": str(uuid.UUID(str(org)))}


def reviewed_predicate(alias: str, mode: str, ids: Sequence[str] | set[str]) -> tuple[str, dict[str, list[str]]]:
    """SQL over the chat's turns aliased `alias` for the conversation list's `reviewed` filter, given the turn
    ids reviewed_turn_ids() read from the lab database. Never a join across the two databases."""
    _check_alias(alias)
    if mode not in REVIEWED_MODES:
        raise ValueError(f"unknown reviewed mode: {mode}")
    member = f"{alias}.turn_id = ANY(CAST(:tl_rev_ids AS text[]))"
    return (f"NOT ({member})" if mode == "unreviewed" else member), {"tl_rev_ids": sorted(ids)}


def visible_set(*, me: str, is_owner: bool, turn_ids: Sequence[str], heads: Sequence[Mapping[str, Any]],
                items: Mapping[str, Mapping[str, Any]], revealed: set[str]) -> set[str]:
    """Turns on which the caller may see other reviewers' verdicts (spec "Open coding" 2.3).

    heads = live verdict heads (any reviewer, tombstones included); items = {turn_id: sample item with
    reviews_required}; revealed = turns the caller has a 'reveal' event on."""
    if is_owner:
        return set(turn_ids)
    live: dict[str, set[str]] = {}
    for h in heads:
        if not h.get("tombstone"):
            live.setdefault(h["turn_id"], set()).add(str(h["reviewer_id"]))
    out = set()
    for tid in turn_ids:
        reviewers = live.get(tid, set())
        item = items.get(tid)
        if me in reviewers or tid in revealed or (item and len(reviewers) >= int(item["reviews_required"])):
            out.add(tid)
    return out


def annotation_out(row: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """One stored version as the API returns it."""
    if row is None:
        return None
    r = _iso(dict(row))
    anchor = None
    if r.get("quote") is not None:
        anchor = {"span_id": r.get("span_id"), "field": r.get("field"), "quote": r.get("quote"),
                  "occurrence": r.get("quote_occurrence"), "start": r.get("quote_start"),
                  "span_name": r.get("span_name"), "span_kind": r.get("span_kind"), "span_agent": r.get("span_agent")}
    return {"id": r["id"], "lineage_id": r["lineage_id"], "version": r["version"], "supersedes": r.get("supersedes"),
            "kind": r["kind"], "turn_id": r.get("turn_id"), "conversation_key": r["conversation_key"],
            "reviewer_id": r["reviewer_id"], "reviewer_email": r.get("reviewer_email"), "tombstone": bool(r["tombstone"]),
            "verdict": r.get("verdict"), "note": r.get("note"), "anchor": anchor, "blind": r.get("blind"),
            "created_at": r.get("created_at")}


def _lock_busy(exc: BaseException) -> bool:
    """True when `exc` is Postgres refusing to wait any longer for a lock (SQLSTATE 55P03: lock_timeout reached,
    or NOWAIT). Looked up through the driver wrappers; the class name is the fallback."""
    seen: Any = exc
    for _ in range(5):
        if seen is None:
            break
        if getattr(seen, "sqlstate", None) == LOCK_NOT_AVAILABLE or type(seen).__name__ == "LockNotAvailableError":
            return True
        seen = getattr(seen, "orig", None) or getattr(seen, "__cause__", None)
    return False


async def _limits(conn: Any) -> None:
    """The transaction's time limits: the statement limit, and a short wait for another writer's head lock."""
    await conn.execute(text(TIMEOUT))
    await conn.execute(text(f"SET LOCAL lock_timeout = '{int(LOCK_WAIT_MS)}ms'"))


def _constraint(exc: BaseException) -> str | None:
    seen: Any = exc
    for _ in range(5):
        if seen is None:
            break
        name = getattr(seen, "constraint_name", None)
        if name:
            return str(name)
        seen = getattr(seen, "orig", None) or getattr(seen, "__cause__", None)
    msg = str(exc)
    return next((n for n in _HEAD_RACES if n in msg), None)


# ── the two databases ──────────────────────────────────────────────────────────────────

TURNS = conversations.TURNS
SPANS = conversations.SPANS


@asynccontextmanager
async def _read_main() -> AsyncIterator[Any]:
    """A read-only, 10-second-bounded transaction on the read-only main engine (the chat's tables)."""
    async with db.main_engine().begin() as conn:
        await conn.execute(text("SET TRANSACTION READ ONLY"))
        await conn.execute(text(TIMEOUT))
        yield conn


@asynccontextmanager
async def _read_lab() -> AsyncIterator[Any]:
    """A read-only, 10-second-bounded transaction on Trace Lab's own database."""
    async with db.lab_engine().begin() as conn:
        await conn.execute(text("SET TRANSACTION READ ONLY"))
        await conn.execute(text(TIMEOUT))
        yield conn


def _json(v: Any) -> Any:
    return json.loads(v) if isinstance(v, str) else v


def _id(v: Any) -> str | None:
    """An id compared as text, whatever its case; None for nothing."""
    return str(v).strip().lower() if v else None


def _is_owner(principal: Any, org: str | None) -> bool:
    """The platform owner: a superadmin acting for no company. They see every reviewer's work."""
    return principal.role == "superadmin" and not org


def _scope(principal: Any, org: str | None, alias: str = "tt") -> tuple[str, dict[str, Any]]:
    """The chat's company scope (conversations.scope_sql); the platform view of a superadmin naming no company
    reads every company's turns; anyone else without a company reads nothing."""
    if org:
        return conversations.scope_sql(principal, org, alias)
    return ("TRUE", {}) if principal.role == "superadmin" else ("FALSE", {})


def _owned(principal: Any, turn: Mapping[str, Any]) -> bool:
    """trace._scope_sql's second rule for a row that comes from a snapshot: an admin reads the company's turns,
    anyone else only their own."""
    return bool(principal.is_admin) or str(turn.get("user_id")) == str(principal.user_id)


# ── reads shared by the writes ──────────────────────────────────────────────────────────

async def _scoped_turn(principal: Any, org: str | None, turn_id: str) -> dict[str, Any] | None:
    """The turn (a plain JSON row) when it is in the caller's scope, else None (404 turn_not_found, which also
    covers another company's turn and a turn with no company). A live turn is read from the chat's tables; a
    turn the chat no longer holds, and every golden answer, from its evidence snapshot in the lab database."""
    if not isinstance(turn_id, str) or not 1 <= len(turn_id) <= evidence.MAX_ID_LENGTH:
        return None
    if not conversations.is_golden_turn(turn_id):
        where, p = _scope(principal, org)
        async with _read_main() as conn:
            row = (await conn.execute(text(
                f"SELECT tt.* FROM {TURNS} tt "
                f"WHERE tt.turn_id = :tl_tid AND tt.organization_id IS NOT NULL AND {where}"),
                {**p, "tl_tid": turn_id})).mappings().first()
        if row is not None:
            return evidence.plain(row)
    cw, cp = company_clause(org, "e")
    async with _read_lab() as conn:
        snap = (await conn.execute(text(
            f"SELECT e.turn FROM tracelab.evidence_turns e WHERE e.turn_id = :tl_tid AND {cw}"),
            {**cp, "tl_tid": turn_id})).scalar_one_or_none()
    turn = _json(snap) if snap is not None else None
    return dict(turn) if isinstance(turn, Mapping) and _owned(principal, turn) else None


async def _golden_result(turn_id: str, org: str | None) -> dict[str, Any] | None:
    """The golden_results row of a golden answer: what the Conversations view uses for its recorded provenance."""
    cw, cp = company_clause(org, "r")
    async with _read_lab() as conn:
        row = (await conn.execute(text(
            f"SELECT r.run_id, r.case_key, r.rep, r.msg_idx, r.question FROM tracelab.golden_results r "
            f"WHERE r.turn_id = :tl_tid AND {cw} ORDER BY r.created_at LIMIT 1"), {**cp, "tl_tid": turn_id})).mappings().first()
    return dict(row) if row else None


async def _turn_record(turn: Mapping[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """The normalized turn and its raw span rows, built from the same inputs the Conversations view uses: the
    live row and spans (else the snapshot, when the chat purged the spans), a golden turn's recorded
    provenance, and a live turn's earliest activity row."""
    tid, org = str(turn["turn_id"]), turn.get("organization_id")
    have = (await evidence.load([tid], organization_id=org)).get(tid)
    row, spans = (have.turn, have.spans) if have is not None else (dict(turn), [])
    meta = activity = None
    if conversations.is_golden_turn(tid):
        golden = await _golden_result(tid, org)
        meta = conversations.golden_meta(row, golden) if golden else None
    elif row.get("session_id"):
        async with _read_main() as conn:
            activity = await conversations._activity_input(conn, str(row["session_id"]), tid)
    return normalize.normalize_turn(row, spans, meta=meta, activity_input=activity), spans


async def _pin(turn: Mapping[str, Any], anchor: Anchor) -> dict[str, Any]:
    """Check the quote against the block it names; return the columns the row copies."""
    record, spans = await _turn_record(turn)
    span = None
    if anchor.span_id and anchor.field not in TURN_FIELDS:       # a question or answer is the turn's own, never a step's
        span = next((s for s in spans if _id(s.get("id")) == _id(anchor.span_id)), None)
        if span is None:
            raise Invalid("span_not_in_turn")
    block = block_texts(record).get(block_key(str(span["id"]) if span is not None else None, anchor.field))
    start = locate_quote(block["text"], anchor.quote, anchor.occurrence) if block else None
    if start is None:
        raise Invalid("quote_not_found")
    if span is None:
        return {"span_id": None, "field": anchor.field, "quote": anchor.quote, "occ": anchor.occurrence,
                "start": start, "sname": "turn", "skind": anchor.field, "sagent": None}
    return {"span_id": str(span["id"]), "field": anchor.field, "quote": anchor.quote, "occ": anchor.occurrence,
            "start": start, "sname": span.get("name"), "skind": span.get("kind"), "sagent": span.get("agent")}


_NO_PIN = {"span_id": None, "field": None, "quote": None, "occ": None, "start": None,
           "sname": None, "skind": None, "sagent": None}

_INSERT = text("""
    INSERT INTO tracelab.annotations
        (id, lineage_id, version, supersedes, kind, organization_id, conversation_key, turn_id, reviewer_id,
         reviewer_email, tombstone, verdict, note, span_id, field, quote, quote_occurrence, quote_start,
         span_name, span_kind, span_agent, blind)
    VALUES (CAST(:id AS uuid), CAST(:lineage AS uuid), :version, CAST(:supersedes AS uuid), :kind,
            CAST(:org AS uuid), :ckey, :tid, CAST(:me AS uuid), :email, :tomb, :verdict, :note,
            CAST(:span_id AS uuid), :field, :quote, :occ, :start, :sname, :skind, :sagent, :blind)
    RETURNING *""")
_SUPERSEDE = text("""
    UPDATE tracelab.annotations SET superseded_by = CAST(:new AS uuid), superseded_at = clock_timestamp()
     WHERE id = CAST(:head AS uuid) AND superseded_by IS NULL""")
_EVENT = text("""
    INSERT INTO tracelab.annotation_events
        (organization_id, conversation_key, turn_id, lineage_id, annotation_id, action, detail, actor_id, actor_email)
    VALUES (CAST(:org AS uuid), :ckey, :tid, CAST(:lineage AS uuid), CAST(:aid AS uuid), :action,
            CAST(:detail AS jsonb), CAST(:me AS uuid), :email)""")


_VERDICT_HEAD = ("SELECT * FROM tracelab.annotations WHERE kind = 'verdict' AND turn_id = :tl_tid "
                 "AND reviewer_id = CAST(:tl_me AS uuid) AND superseded_by IS NULL")


async def _verdict_head(conn: Any, turn_id: str, me: str, *, lock: bool) -> dict[str, Any] | None:
    """The caller's live verdict head for a turn. With `lock` the row is taken FOR UPDATE; a writer that lost the
    race for it waits on that lock, and under READ COMMITTED the winner's supersede then makes the locked read
    match NOTHING. That empty result does not mean "no head": the live head is re-read without a lock (in the same
    transaction, so it sees what the winner committed) and returned, so the caller compares `base` against it and
    answers 409 with it. The head returned that way is not locked; the supersede UPDATE (`superseded_by IS NULL`,
    rowcount checked) and the head indexes still keep a write on top of it correct."""
    params = {"tl_tid": turn_id, "tl_me": me}
    row = (await conn.execute(text(_VERDICT_HEAD + (" FOR UPDATE" if lock else "")), params)).mappings().first()
    if row is None and lock:
        row = (await conn.execute(text(_VERDICT_HEAD), params)).mappings().first()
    return dict(row) if row else None


async def _current_verdict(turn_id: str, me: str) -> dict[str, Any] | None:
    async with _read_lab() as conn:
        return annotation_out(await _verdict_head(conn, turn_id, me, lock=False))


async def _blind(conn: Any, org: str, turn_id: str, me: str, head: Mapping[str, Any] | None) -> bool:
    """Version 1: blind unless the caller revealed the turn or its sample item was already complete.
    Later versions: blind only if no other reviewer had a current verdict (spec 2.3)."""
    others = (await conn.execute(text(
        "SELECT count(DISTINCT reviewer_id) FROM tracelab.annotations WHERE kind = 'verdict' "
        "AND turn_id = :tl_tid AND superseded_by IS NULL AND NOT tombstone AND reviewer_id <> CAST(:tl_me AS uuid)"),
        {"tl_tid": turn_id, "tl_me": me})).scalar_one()
    if head is not None:
        return int(others) == 0
    revealed = (await conn.execute(text(
        "SELECT EXISTS (SELECT 1 FROM tracelab.annotation_events WHERE turn_id = :tl_tid "
        "AND actor_id = CAST(:tl_me AS uuid) AND action = 'reveal')"), {"tl_tid": turn_id, "tl_me": me})).scalar_one()
    required = (await conn.execute(text(
        "SELECT b.reviews_required FROM tracelab.sample_items si JOIN tracelab.sample_batches b "
        "ON b.id = si.batch_id WHERE si.organization_id = CAST(:tl_org AS uuid) AND si.turn_id = :tl_tid"),
        {"tl_org": org, "tl_tid": turn_id})).scalar_one_or_none()
    complete = required is not None and int(others) >= int(required)
    return not (bool(revealed) or complete)


# ── verdicts ───────────────────────────────────────────────────────────────────────────

async def save_verdict(principal: Any, org: str, turn_id: str, *, verdict: str, note: str | None,
                       anchor: Anchor | None, base: str | None) -> dict[str, Any]:
    """Write the caller's verdict as a new version (spec "Open coding" 2.4). review_errors.NotFound, Invalid, Stale.

    Order: the quote is checked against the turn read from the chat (or its snapshot), the turn's evidence is
    copied into the lab database (evidence.snapshot raises when it cannot be kept, and then nothing is
    written), and only then does the one lab transaction supersede the head and insert the new version."""
    note, anchor = validate_verdict(verdict, note, anchor)
    me = str(principal.user_id)
    new_id = str(uuid.uuid4())
    turn = await _scoped_turn(principal, org, turn_id)
    if turn is None:
        raise NotFound("turn_not_found")
    pin = await _pin(turn, anchor) if anchor else dict(_NO_PIN)
    await evidence.snapshot([turn_id], organization_id=org, reason=SNAPSHOT_REASON)
    ckey = normalize.group_key(turn.get("session_id"), turn_id)
    try:
        async with db.lab_engine().begin() as conn:
            await _limits(conn)
            head = await _verdict_head(conn, turn_id, me, lock=True)
            if (_id(head["id"]) if head else None) != _id(base):
                raise Stale(annotation_out(head))
            blind = await _blind(conn, org, turn_id, me, head)
            if head is not None:
                res = await conn.execute(_SUPERSEDE, {"new": new_id, "head": str(head["id"])})
                if res.rowcount != 1:
                    raise Stale(annotation_out(head))
            lineage = str(head["lineage_id"]) if head else new_id
            row = (await conn.execute(_INSERT, {
                "id": new_id, "lineage": lineage, "version": (int(head["version"]) + 1) if head else 1,
                "supersedes": str(head["id"]) if head else None, "kind": "verdict", "org": org, "ckey": ckey,
                "tid": turn_id, "me": me, "email": principal.email or None, "tomb": False, "verdict": verdict,
                "note": note, "blind": blind, **pin})).mappings().one()
            await conn.execute(_EVENT, {
                "org": org, "ckey": ckey, "tid": turn_id, "lineage": lineage, "aid": new_id,
                "action": "edit" if head else "create",
                "detail": json.dumps({"verdict": verdict, "version": row["version"]}), "me": me,
                "email": principal.email or None})
            if head is not None:                     # grouping proposals citing the old version close (spec §7.7 rule 4)
                from . import proposals

                await proposals.close_for_annotation(conn, str(head["id"]))
    except IntegrityError as exc:
        if _constraint(exc) in _HEAD_RACES:
            raise Stale(await _current_verdict(turn_id, me)) from exc
        raise
    except DBAPIError as exc:
        if _lock_busy(exc):                                 # another writer holds the head: 409 now, never a long wait
            raise Stale(await _current_verdict(turn_id, me)) from exc
        raise
    return annotation_out(row)


async def delete_verdict(principal: Any, org: str, turn_id: str, *, base: str) -> dict[str, Any]:
    """Write a tombstone version of the caller's verdict. Stale (409, carrying the live head) when `base` is not the
    live head, including when a concurrent write or delete just replaced it; NotFound(annotation_not_found) only
    when there is no live head at all, or `base` names the caller's own tombstone (nothing left to delete)."""
    me = str(principal.user_id)
    new_id = str(uuid.uuid4())
    if await _scoped_turn(principal, org, turn_id) is None:
        raise NotFound("turn_not_found")
    try:
        async with db.lab_engine().begin() as conn:
            await _limits(conn)
            head = await _verdict_head(conn, turn_id, me, lock=True)
            if head is None:
                raise NotFound("annotation_not_found")
            if _id(head["id"]) != _id(base):               # a double click or a second tab: 409 with the head, even a tombstone
                raise Stale(annotation_out(head))
            if head["tombstone"]:                           # the caller's own tombstone: nothing left to delete
                raise NotFound("annotation_not_found")
            res = await conn.execute(_SUPERSEDE, {"new": new_id, "head": str(head["id"])})
            if res.rowcount != 1:
                raise Stale(annotation_out(head))
            row = (await conn.execute(_INSERT, {
                "id": new_id, "lineage": str(head["lineage_id"]), "version": int(head["version"]) + 1,
                "supersedes": str(head["id"]), "kind": "verdict", "org": org, "ckey": head["conversation_key"],
                "tid": turn_id, "me": me, "email": principal.email or None, "tomb": True, "verdict": None,
                "note": None, "blind": None, **_NO_PIN})).mappings().one()
            await conn.execute(_EVENT, {
                "org": org, "ckey": head["conversation_key"], "tid": turn_id, "lineage": str(head["lineage_id"]),
                "aid": new_id, "action": "delete", "detail": json.dumps({"version": row["version"]}), "me": me,
                "email": principal.email or None})
            from . import proposals                 # a deleted note: grouping proposals citing it close too (rule 4)

            await proposals.close_for_annotation(conn, str(head["id"]))
    except IntegrityError as exc:
        if _constraint(exc) in _HEAD_RACES:
            raise Stale(await _current_verdict(turn_id, me)) from exc
        raise
    except DBAPIError as exc:
        if _lock_busy(exc):                                 # another writer holds the head: 409 now, never a long wait
            raise Stale(await _current_verdict(turn_id, me)) from exc
        raise
    return annotation_out(row)


# ── comments ───────────────────────────────────────────────────────────────────────────

async def _golden_turns_of_key(principal: Any, org: str | None, key: str) -> list[dict[str, Any]]:
    """A golden conversation's turns, from the lab database (golden_results and the answer's evidence)."""
    if not principal.is_admin:
        return []
    cw, cp = company_clause(org, "r")
    async with _read_lab() as conn:
        rows = (await conn.execute(text(
            f"SELECT r.turn_id, r.session_id, r.organization_id, e.captured_at AS started_at "
            f"FROM tracelab.golden_results r JOIN tracelab.evidence_turns e ON e.turn_id = r.turn_id "
            f"WHERE r.session_id = :tl_sid AND {cw} ORDER BY r.msg_idx, r.turn_id LIMIT {MAX_TURNS_OF_KEY}"),
            {**cp, "tl_sid": key[2:]})).mappings().all()
    return [evidence.plain(r) for r in rows]


async def _turns_of_key(principal: Any, org: str | None, conversation_key: str) -> list[dict[str, Any]]:
    """The turns of one conversation (at most 500, oldest first), as plain JSON. A live conversation is read from
    the chat's tables, and its key must be the key its turns really group under (conversations.GROUP_KEY_SQL),
    so `t:<turn>` never names a turn that belongs to a session; a golden conversation is read from the lab
    database. db-level NotFound for a key that cannot name a conversation."""
    key = conversation_key if isinstance(conversation_key, str) else ""
    if not conversations._KEY_RE.match(key):
        raise NotFound("turn_not_found")
    if conversations.is_golden_key(key):
        return await _golden_turns_of_key(principal, org, key)
    kw, kp = key_where(key)
    where, p = _scope(principal, org)
    async with _read_main() as conn:
        rows = (await conn.execute(text(
            f"SELECT tt.turn_id, tt.session_id, tt.organization_id, tt.user_id, tt.started_at "
            f"FROM {TURNS} tt WHERE {kw} AND tt.organization_id IS NOT NULL AND {where} "
            f"AND {conversations.GROUP_KEY_SQL} = :tl_key ORDER BY tt.started_at, tt.turn_id LIMIT {MAX_TURNS_OF_KEY}"),
            {**p, **kp, "tl_key": key})).mappings().all()
    return [evidence.plain(r) for r in rows]


_COMMENT_HEAD = ("SELECT * FROM tracelab.annotations WHERE kind = 'comment' AND lineage_id = CAST(:tl_lid AS uuid) "
                 "AND organization_id = CAST(:tl_org AS uuid) AND superseded_by IS NULL")


async def _comment_head(conn: Any, org: str, lineage_id: str) -> dict[str, Any] | None:
    """The live head of one comment lineage, taken FOR UPDATE. Like `_verdict_head`: when the locked read matches
    nothing because a winner superseded the row while this writer waited, the live head is re-read without a lock
    and returned, so the caller answers 409 with it instead of "not found". None only when no live head exists."""
    try:
        uuid.UUID(str(lineage_id))
    except ValueError:
        return None
    params = {"tl_lid": str(lineage_id), "tl_org": org}
    row = (await conn.execute(text(_COMMENT_HEAD + " FOR UPDATE"), params)).mappings().first()
    if row is None:
        row = (await conn.execute(text(_COMMENT_HEAD), params)).mappings().first()
    return dict(row) if row else None


async def _current_comment(org: str, lineage_id: str, me: str) -> dict[str, Any] | None:
    """The live head of a comment lineage for `Stale.current`, read without a lock. Only the author is ever handed
    their comment back: for anyone else it is review_errors.Forbidden, as on the normal path."""
    try:
        uuid.UUID(str(lineage_id))
    except ValueError:
        return None
    async with _read_lab() as conn:
        cur = (await conn.execute(text(_COMMENT_HEAD), {"tl_lid": str(lineage_id), "tl_org": org})).mappings().first()
    if cur is not None and str(cur["reviewer_id"]) != me:
        raise Forbidden("not_author")
    return annotation_out(dict(cur)) if cur else None


async def add_comment(principal: Any, org: str, conversation_key: str, *, note: str,
                      turn_id: str | None) -> dict[str, Any]:
    """A comment on a conversation, or on one of its answers. NotFound when the conversation (or the answer in
    it) is not in the caller's scope; Invalid for an empty or over-long note. A comment on an answer keeps that
    answer's evidence in the lab database, like a verdict does."""
    note = validate_comment(note)
    me = str(principal.user_id)
    new_id = str(uuid.uuid4())
    turns = await _turns_of_key(principal, org, conversation_key)
    if not turns or (turn_id and turn_id not in {t["turn_id"] for t in turns}):
        raise NotFound("turn_not_found")
    if turn_id:
        await evidence.snapshot([turn_id], organization_id=org, reason=SNAPSHOT_REASON)
    async with db.lab_engine().begin() as conn:
        await conn.execute(text(TIMEOUT))
        row = (await conn.execute(_INSERT, {
            "id": new_id, "lineage": new_id, "version": 1, "supersedes": None, "kind": "comment", "org": org,
            "ckey": conversation_key, "tid": turn_id or None, "me": me, "email": principal.email or None,
            "tomb": False, "verdict": None, "note": note, "blind": None, **_NO_PIN})).mappings().one()
        await conn.execute(_EVENT, {
            "org": org, "ckey": conversation_key, "tid": turn_id or None, "lineage": new_id, "aid": new_id,
            "action": "create", "detail": json.dumps({"kind": "comment", "version": 1}), "me": me,
            "email": principal.email or None})
    return annotation_out(row)


async def _comment_version(principal: Any, org: str, lineage_id: str, *, note: str | None, base: str,
                           tombstone: bool) -> dict[str, Any]:
    me = str(principal.user_id)
    new_id = str(uuid.uuid4())
    try:
        async with db.lab_engine().begin() as conn:
            await _limits(conn)
            head = await _comment_head(conn, org, lineage_id)
            if head is None:
                raise NotFound("annotation_not_found")
            if str(head["reviewer_id"]) != me:             # checked first: another person's comment is never handed back as `current`
                raise Forbidden("not_author")
            if _id(head["id"]) != _id(base):               # a double click or a second tab: 409 with the head, even a tombstone
                raise Stale(annotation_out(head))
            if head["tombstone"]:                           # the author's own tombstone: nothing left to change
                raise NotFound("annotation_not_found")
            res = await conn.execute(_SUPERSEDE, {"new": new_id, "head": str(head["id"])})
            if res.rowcount != 1:
                raise Stale(annotation_out(head))
            row = (await conn.execute(_INSERT, {
                "id": new_id, "lineage": str(head["lineage_id"]), "version": int(head["version"]) + 1,
                "supersedes": str(head["id"]), "kind": "comment", "org": org, "ckey": head["conversation_key"],
                "tid": head.get("turn_id"), "me": me, "email": principal.email or None, "tomb": tombstone,
                "verdict": None, "note": None if tombstone else note, "blind": None, **_NO_PIN})).mappings().one()
            await conn.execute(_EVENT, {
                "org": org, "ckey": head["conversation_key"], "tid": head.get("turn_id"),
                "lineage": str(head["lineage_id"]), "aid": new_id, "action": "delete" if tombstone else "edit",
                "detail": json.dumps({"kind": "comment", "version": row["version"]}), "me": me,
                "email": principal.email or None})
    except IntegrityError as exc:
        if _constraint(exc) in _HEAD_RACES:
            raise Stale(await _current_comment(org, lineage_id, me)) from exc
        raise
    except DBAPIError as exc:
        if _lock_busy(exc):                                 # another writer holds the head: 409 now, never a long wait
            raise Stale(await _current_comment(org, lineage_id, me)) from exc
        raise
    return annotation_out(row)


async def edit_comment(principal: Any, org: str, lineage_id: str, *, note: str, base: str) -> dict[str, Any]:
    return await _comment_version(principal, org, lineage_id, note=validate_comment(note), base=base, tombstone=False)


async def delete_comment(principal: Any, org: str, lineage_id: str, *, base: str) -> dict[str, Any]:
    return await _comment_version(principal, org, lineage_id, note=None, base=base, tombstone=True)


# ── reads ──────────────────────────────────────────────────────────────────────────────

async def _states(conn: Any, principal: Any, org: str | None, turn_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Each turn's verdict state for the caller. `conn` is a connection to the LAB database: everything read here
    (verdict heads, sample items and their batches, reveal events) lives there."""
    me = str(principal.user_id)
    cw, cp = company_clause(org, "a")
    heads = [dict(r) for r in (await conn.execute(text(
        f"SELECT a.* FROM tracelab.annotations a WHERE a.kind = 'verdict' AND a.superseded_by IS NULL "
        f"AND a.turn_id = ANY(CAST(:tl_tids AS text[])) AND {cw} ORDER BY a.created_at, a.id"),
        {**cp, "tl_tids": turn_ids})).mappings().all()]
    iw, ip = company_clause(org, "si")
    items = {r["turn_id"]: dict(r) for r in (await conn.execute(text(
        f"SELECT si.turn_id, si.batch_id::text AS batch_id, si.position, si.reason, b.plan_step, b.strategy, "
        f"b.reviews_required, b.counts_for_prevalence, b.withdrawn_at "
        f"FROM tracelab.sample_items si JOIN tracelab.sample_batches b ON b.id = si.batch_id "
        f"WHERE si.turn_id = ANY(CAST(:tl_tids AS text[])) AND {iw}"), {**ip, "tl_tids": turn_ids})).mappings().all()}
    revealed = {r[0] for r in (await conn.execute(text(
        "SELECT DISTINCT turn_id FROM tracelab.annotation_events WHERE action = 'reveal' "
        "AND actor_id = CAST(:tl_me AS uuid) AND turn_id = ANY(CAST(:tl_tids AS text[]))"),
        {"tl_me": me, "tl_tids": turn_ids})).all()}
    visible = visible_set(me=me, is_owner=_is_owner(principal, org), turn_ids=turn_ids,
                          heads=heads, items=items, revealed=revealed)
    out: dict[str, dict[str, Any]] = {}
    for tid in turn_ids:
        mine = next((h for h in heads if h["turn_id"] == tid and str(h["reviewer_id"]) == me), None)
        others = [h for h in heads if h["turn_id"] == tid and str(h["reviewer_id"]) != me and not h["tombstone"]]
        item = items.get(tid)
        shown = tid in visible
        out[tid] = {
            "mine": annotation_out(mine), "others": [annotation_out(h) for h in others] if shown else None,
            "others_count": len(others), "others_hidden": bool(others) and not shown,
            "reviews_required": int(item["reviews_required"]) if item else None,
            "sample_item": _iso({k: item[k] for k in ("batch_id", "position", "reason", "plan_step", "strategy",
                                                       "counts_for_prevalence", "withdrawn_at")}) if item else None,
        }
    return out


async def for_conversation(principal: Any, org: str | None, conversation_key: str, *,
                           turn_id: str | None = None) -> dict[str, Any]:
    """GET /annotations: every turn's verdict state, the conversation's comments, and - for `turn_id` - the
    annotatable block texts of that turn. NotFound when nothing in scope matches. The turns come from the
    chat (or the lab database for a golden conversation); the annotations from the lab database."""
    me = str(principal.user_id)
    turns = await _turns_of_key(principal, org, conversation_key)
    if not turns or (turn_id and turn_id not in {t["turn_id"] for t in turns}):
        raise NotFound("turn_not_found")
    tids = [t["turn_id"] for t in turns]
    blocks = None
    if turn_id:
        record, _ = await _turn_record(next(t for t in turns if t["turn_id"] == turn_id))
        blocks = {turn_id: {k: v["text"] for k, v in block_texts(record).items()}}
    cw, cp = company_clause(org, "a")
    async with _read_lab() as conn:
        states = await _states(conn, principal, org, tids)
        rows = [dict(r) for r in (await conn.execute(text(
            f"SELECT a.* FROM tracelab.annotations a WHERE a.kind = 'comment' AND a.superseded_by IS NULL "
            f"AND NOT a.tombstone AND a.conversation_key = :tl_key AND {cw} ORDER BY a.created_at, a.id"),
            {**cp, "tl_key": conversation_key})).mappings().all()]
    blind_somewhere = not _is_owner(principal, org) and any(s["others"] is None for s in states.values())
    mine = [r for r in rows if str(r["reviewer_id"]) == me]
    others = [r for r in rows if str(r["reviewer_id"]) != me]
    comments = [annotation_out(r) for r in (mine if blind_somewhere else rows)]
    return {"ok": True, "conversation_key": conversation_key, "turns": states, "comments": comments,
            "comments_hidden": len(others) if blind_somewhere else 0, "blocks": blocks}


async def _known_turns(principal: Any, org: str | None, turn_ids: list[str]) -> list[str]:
    """The ids among turn_ids that are in the caller's scope: live turns from the chat's tables, then (for the
    rest) evidence snapshots in the lab database, which is where golden answers live."""
    where, p = _scope(principal, org)
    async with _read_main() as conn:
        live = {r[0] for r in (await conn.execute(text(
            f"SELECT tt.turn_id FROM {TURNS} tt WHERE tt.turn_id = ANY(CAST(:tl_tids AS text[])) "
            f"AND tt.organization_id IS NOT NULL AND {where}"), {**p, "tl_tids": turn_ids})).all()}
    rest = [t for t in turn_ids if t not in live]
    kept: set[str] = set()
    if rest:
        cw, cp = company_clause(org, "e")
        async with _read_lab() as conn:
            for tid, turn in (await conn.execute(text(
                    f"SELECT e.turn_id, e.turn FROM tracelab.evidence_turns e "
                    f"WHERE e.turn_id = ANY(CAST(:tl_tids AS text[])) AND {cw}"), {**cp, "tl_tids": rest})).all():
                body = _json(turn)
                if isinstance(body, Mapping) and _owned(principal, body):
                    kept.add(tid)
    return [t for t in turn_ids if t in live or t in kept]


async def for_turns(principal: Any, org: str | None, turn_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Verdict states for the given turns that are in the caller's scope (others' blind as above)."""
    ids = evidence._clean_ids(turn_ids)
    if not ids:
        return {}
    known = await _known_turns(principal, org, ids)
    if not known:
        return {}
    async with _read_lab() as conn:
        return await _states(conn, principal, org, known)


async def history(principal: Any, org: str | None, turn_id: str) -> dict[str, Any]:
    """Every verdict version and event on one turn; other reviewers' only once visible to the caller."""
    me = str(principal.user_id)
    if await _scoped_turn(principal, org, turn_id) is None:
        raise NotFound("turn_not_found")
    cw, cp = company_clause(org, "a")
    ew, ep = company_clause(org, "ev")
    async with _read_lab() as conn:
        visible = await visible_others(conn, principal, org, [turn_id])
        versions = [dict(r) for r in (await conn.execute(text(
            f"SELECT a.* FROM tracelab.annotations a WHERE a.kind = 'verdict' AND a.turn_id = :tl_tid AND {cw} "
            f"ORDER BY a.created_at, a.version"), {**cp, "tl_tid": turn_id})).mappings().all()]
        events = [dict(r) for r in (await conn.execute(text(
            f"SELECT ev.seq, ev.lineage_id, ev.annotation_id, ev.action, ev.detail, ev.actor_id, ev.actor_email, ev.at "
            f"FROM tracelab.annotation_events ev WHERE ev.turn_id = :tl_tid AND {ew} ORDER BY ev.at, ev.seq"),
            {**ep, "tl_tid": turn_id})).mappings().all()]
    show = turn_id in visible
    return {"ok": True, "turn_id": turn_id,
            "versions": [annotation_out(v) for v in versions if show or str(v["reviewer_id"]) == me],
            "events": [_iso(e) for e in events if show or str(e["actor_id"]) == me]}


async def visible_others(conn: Any, principal: Any, org: str | None, turn_ids: list[str]) -> set[str]:
    """The turns on which the caller may see other reviewers' verdicts. `conn` is a LAB database connection."""
    if not turn_ids:
        return set()
    states = await _states(conn, principal, org, list(turn_ids))
    return {tid for tid, s in states.items() if s["others"] is not None}


async def reviewed_turns(conn: Any, org: str | None) -> set[str]:
    """Turns with at least one live, non-tombstone verdict head. `conn` is a LAB database connection."""
    cw, cp = company_clause(org, "a")
    return {r[0] for r in (await conn.execute(text(
        f"SELECT DISTINCT a.turn_id FROM tracelab.annotations a WHERE a.kind = 'verdict' "
        f"AND a.superseded_by IS NULL AND NOT a.tombstone AND {cw}"), cp)).all()}


async def reviewed_turn_ids(org: str | None, *, reviewer_id: str | None = None, mode: str = "reviewed") -> set[str]:
    """The turn ids the conversation list's `reviewed` filter turns on, read from the lab database: the turns with a
    live verdict from anyone (reviewed; unreviewed is every other turn, so it takes this same set to leave out)
    or from `reviewer_id` alone (mine). Hand the result to reviewed_predicate for the chat's side."""
    if mode not in REVIEWED_MODES:
        raise ValueError(f"unknown reviewed mode: {mode}")
    if mode == "mine" and not reviewer_id:
        raise ValueError("mine needs a reviewer")
    cw, cp = company_clause(org, "a")
    mine = " AND a.reviewer_id = CAST(:tl_rev AS uuid)" if mode == "mine" else ""
    params = {**cp, **({"tl_rev": str(uuid.UUID(str(reviewer_id)))} if mode == "mine" else {})}
    async with _read_lab() as conn:
        rows = (await conn.execute(text(
            f"SELECT DISTINCT a.turn_id FROM tracelab.annotations a WHERE a.kind = 'verdict' "
            f"AND a.superseded_by IS NULL AND NOT a.tombstone AND {cw}{mine}"), params)).all()
    return {r[0] for r in rows}


# ── filing notes under failure modes, dispositions, unfiled notes (Phase 5, spec §6.5) ─────

DISPOSE_OUTCOMES = ("no_mode_fits", "not_a_failure")
DISPOSE_NOTE_MAX = 1000
UNFILED_QUESTION_CHARS = 300
UNFILED_ANSWER_CHARS = 600
_TEXT_FETCH_QUESTION = 4000          # the chat keeps at most this much of a question
_TEXT_FETCH_ANSWER = 3000            # enough to redact, then keep the first UNFILED_ANSWER_CHARS
_TEXT_CHUNK = 200                    # answers read from the chat per query

#: A reviewer's live Wrong note, minus any whose lineage already carries a human_open_code label (it is filed) and any
#: whose latest dispose / undispose event since the head was written is a dispose. Lab tables only: an answer's own
#: text is read separately from the chat (_answer_texts), never joined here.
_UNFILED_SQL = """
SELECT CAST(h.id AS text) AS annotation_id, CAST(h.lineage_id AS text) AS lineage_id, h.turn_id,
       h.conversation_key, CAST(h.organization_id AS text) AS organization_id, h.reviewer_email,
       CAST(h.reviewer_id AS text) AS reviewer_id, h.note, h.quote, CAST(h.span_id AS text) AS span_id, h.field,
       h.span_name, h.span_kind, h.span_agent, h.created_at
  FROM tracelab.annotations h
 WHERE h.kind = 'verdict' AND h.superseded_by IS NULL AND NOT h.tombstone AND h.verdict = 'wrong' {where}
   AND NOT EXISTS (SELECT 1 FROM tracelab.labels l JOIN tracelab.annotations v ON v.id = l.annotation_id
                    WHERE v.lineage_id = h.lineage_id AND l.source = 'human_open_code')
   AND coalesce((SELECT e.action FROM tracelab.annotation_events e
                  WHERE e.lineage_id = h.lineage_id AND e.action IN ('dispose', 'undispose') AND e.at >= h.created_at
                  ORDER BY e.seq DESC LIMIT 1), 'undispose') <> 'dispose'
 ORDER BY h.created_at, h.id
"""


async def _answer_texts(conn: Any, rows: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, str]]:
    """turn_id -> {"question", "answer"} for the answers these notes are on, as a person reads them: the asker's own
    question (the wrapped prompt repaired, as the Conversations screen shows it), redacted, cut to what a grouping
    prompt needs. Two reads on two engines, never one statement: the chat's rows on the read-only main engine, else (a
    purged turn, a golden answer) the answer's evidence snapshot on `conn`, the caller's LAB connection. A turn whose
    company is not the note's company gives no text."""
    ids = sorted({str(r["turn_id"]) for r in rows})
    found: dict[str, dict[str, Any]] = {}
    for i in range(0, len(ids), _TEXT_CHUNK):
        async with _read_main() as main:
            for r in (await main.execute(text(
                    f"SELECT tt.turn_id, CAST(tt.organization_id AS text) AS organization_id, "
                    f"left(coalesce(tt.question, ''), {_TEXT_FETCH_QUESTION}) AS question, "
                    f"left(coalesce(tt.answer, ''), {_TEXT_FETCH_ANSWER}) AS answer "
                    f"FROM {TURNS} tt WHERE tt.turn_id = ANY(CAST(:tl_ids AS text[]))"),
                    {"tl_ids": ids[i:i + _TEXT_CHUNK]})).mappings().all():
                found[r["turn_id"]] = dict(r)
    rest = [t for t in ids if t not in found]
    for i in range(0, len(rest), _TEXT_CHUNK):
        for r in (await conn.execute(text(
                f"SELECT e.turn_id, CAST(e.organization_id AS text) AS organization_id, "
                f"left(coalesce(e.turn->>'question', ''), {_TEXT_FETCH_QUESTION}) AS question, "
                f"left(coalesce(e.turn->>'answer', ''), {_TEXT_FETCH_ANSWER}) AS answer "
                f"FROM tracelab.evidence_turns e WHERE e.turn_id = ANY(CAST(:tl_ids AS text[]))"),
                {"tl_ids": rest[i:i + _TEXT_CHUNK]})).mappings().all():
            found[r["turn_id"]] = dict(r)
    out: dict[str, dict[str, str]] = {}
    for r in rows:
        have = found.get(str(r["turn_id"]))
        if have is None or have["organization_id"] != str(r["organization_id"]):
            continue
        question, _ = normalize.repair_question({"question": have["question"]}, None, None)
        out[str(r["turn_id"])] = {
            "question": normalize.redact_text(question)[:UNFILED_QUESTION_CHARS],
            "answer": normalize.redact_text(have["answer"])[:UNFILED_ANSWER_CHARS]}
    return out


async def unfiled_rows(conn: Any, org: str | None, *, turn_ids: list[str] | None = None,
                       with_text: bool = True) -> list[dict[str, Any]]:
    """Live wrong-verdict heads with no human_open_code label on any version of their lineage and no dispose event
    since the head was written, oldest first. `conn` is a LAB connection. With `with_text` each row also carries the
    answer's `question` and `answer` (see _answer_texts; "" when the answer cannot be read). No blind filter here:
    callers apply it (or are the platform's own jobs)."""
    where: list[str] = []
    params: dict[str, Any] = {}
    if org:
        where.append("AND h.organization_id = CAST(:org AS uuid)")
        params["org"] = org
    if turn_ids is not None:
        where.append("AND h.turn_id = ANY(CAST(:tids AS text[]))")
        params["tids"] = list(turn_ids)
    found = (await conn.execute(text(_UNFILED_SQL.format(where=" ".join(where))), params)).mappings().all()
    rows = [dict(r) for r in found]
    if with_text and rows:
        texts = await _answer_texts(conn, rows)
        for r in rows:
            t = texts.get(str(r["turn_id"]), {})
            r["question"], r["answer"] = t.get("question", ""), t.get("answer", "")
    return rows


def step_label(row: Mapping[str, Any]) -> str:
    """Where a note points, in words a reviewer knows: the agent's name when the step has one, else the turn's own
    question or answer."""
    from . import taxonomy

    agent = str(row.get("span_agent") or "").rpartition(":")[2]        # a tool step's agent reads "tool:<agent>"
    if agent in taxonomy.AGENT_LABELS:
        return taxonomy.AGENT_LABELS[agent]
    if row.get("span_id"):
        return "A step"
    return "Question" if row.get("field") == "question" else "Answer"


async def unfiled_notes(principal: Any, org: str | None, *, limit: int = 50, offset: int = 0) -> dict[str, Any]:
    """The notes no one has filed under a mode or set aside, oldest first. Another reviewer's note stays hidden until
    the caller has judged that answer too (the blind rule); `hidden` counts them."""
    from . import labels, taxonomy

    org = taxonomy.acting_company(principal, org)
    async with db.lab_engine().connect() as conn:
        rows = await unfiled_rows(conn, org, with_text=False)
        hidden = 0
        if not labels.is_owner(principal, org):
            seen = await visible_others(conn, principal, org, sorted({r["turn_id"] for r in rows}))
            me = str(principal.user_id)
            kept = [r for r in rows if r["reviewer_id"] == me or r["turn_id"] in seen]
            hidden, rows = len(rows) - len(kept), kept
    start = max(0, int(offset))
    page = rows[start:start + max(1, min(int(limit), 200))]
    return {"notes": [{"annotation_id": r["annotation_id"], "lineage_id": r["lineage_id"], "turn_id": r["turn_id"],
                       "conversation_key": r["conversation_key"], "reviewer_email": r["reviewer_email"],
                       "note": r["note"], "quote": r["quote"], "span_name": r["span_name"],
                       "span_agent": r["span_agent"], "step_label": step_label(r),
                       "created_at": r["created_at"].isoformat() if r["created_at"] else None} for r in page],
            "total": len(rows), "hidden": hidden}


async def _live_head_id(conn: Any, lineage_id: Any) -> str | None:
    r = (await conn.execute(text("SELECT CAST(id AS text) FROM tracelab.annotations WHERE lineage_id = :l "
                                 "AND superseded_by IS NULL"), {"l": str(lineage_id)})).first()
    return r[0] if r else None


async def _wrong_note_to_file(principal: Any, org: str | None, annotation_id: str) -> dict[str, Any]:
    """The note, if the caller may file it: a live Wrong version in the caller's company (the platform owner: any),
    and (blind rule) another reviewer's only on an answer the caller has judged. NotFound, Stale (the note was edited
    or deleted since: it carries the live version's id), Invalid."""
    from . import labels, taxonomy

    async with _read_lab() as conn:
        a = (await conn.execute(text("SELECT * FROM tracelab.annotations WHERE id = CAST(:a AS uuid)"),
                                {"a": annotation_id})).mappings().first()
        if a is None or a["kind"] != "verdict" or (org and str(a["organization_id"]) != org):
            raise NotFound("annotation_not_found")
        if a["superseded_by"] is not None:
            raise Stale({"annotation_id": await _live_head_id(conn, a["lineage_id"])})
        if a["tombstone"] or a["verdict"] != "wrong":
            raise taxonomy.invalid("not_a_wrong_note", annotation_id=annotation_id)
        if not labels.is_owner(principal, org) and str(a["reviewer_id"]) != str(principal.user_id):
            if a["turn_id"] not in await visible_others(conn, principal, org, [a["turn_id"]]):
                raise NotFound("annotation_not_found")          # not yet visible to this caller: never confirm it exists
    return dict(a)


async def file_note(principal: Any, org: str | None, annotation_id: str, mode_id: str, *, base: str | None,
                    override_label: bool = False) -> dict[str, Any]:
    """File a reviewer's Wrong note under a mode: a present human_open_code label on its answer, with the note and
    its quote on the label. It is refused (409 conflicts_with_label) when a person marked the mode absent on this
    answer, unless `override_label`. `base` is the mode's own head label on this answer, as the grid last showed it.

    Order, as for set_label: the note and the answer are checked first (the chat read-only), the answer's evidence is
    copied into the lab database (it raises when it cannot be kept, and then nothing is written), and only then does
    the one lab transaction write the label. review_errors.NotFound, Invalid, Conflict, Stale."""
    from . import labels, taxonomy

    org = taxonomy.acting_company(principal, org)
    if not taxonomy.is_uuid(annotation_id):
        raise NotFound("annotation_not_found")
    if not taxonomy.is_uuid(mode_id):
        raise taxonomy.not_found("mode_not_found")
    annotation_id, mode_id = str(uuid.UUID(str(annotation_id))), str(uuid.UUID(str(mode_id)))
    note = await _wrong_note_to_file(principal, org, annotation_id)
    turn = await labels.scoped_turn(principal, org, note["turn_id"])
    await evidence.snapshot([turn["turn_id"]], organization_id=turn["organization_id"], reason=labels.SNAPSHOT_REASON)
    try:
        async with db.lab_engine().begin() as conn:
            await _limits(conn)
            live = (await conn.execute(text(
                "SELECT superseded_by IS NULL AS live, CAST(lineage_id AS text) AS lineage_id "
                "FROM tracelab.annotations WHERE id = CAST(:a AS uuid) FOR SHARE"), {"a": annotation_id})).mappings().first()
            if live is None or not live["live"]:                 # edited or deleted while the answer was being read
                raise Stale({"annotation_id": await _live_head_id(conn, live["lineage_id"]) if live else None})
            head = (await conn.execute(text(
                "SELECT source, present, approval, tombstone FROM tracelab.labels WHERE turn_id = :t "
                "AND mode_id = CAST(:m AS uuid) AND superseded_by IS NULL"),
                {"t": note["turn_id"], "m": mode_id})).mappings().first()
            if (head is not None and not head["tombstone"] and head["present"] is False
                    and labels.is_human_origin(head["source"], head["approval"]) and not override_label):
                raise taxonomy.conflict("conflicts_with_label")
            label = await labels.write(conn, principal, turn=turn, mode_id=mode_id, present=True,
                                       source="human_open_code", base=base, note=note["note"], quote=note["quote"],
                                       span=labels.span_of(note), annotation_id=annotation_id)
            graph = await taxonomy.load_graph(conn)
            mode = graph.modes[mode_id]
            heads = await labels.load_heads(conn, [note["turn_id"]], graph.relevant_ids([mode_id]))
            cell = labels.resolve_cell(graph, heads, note["turn_id"], mode)
    except IntegrityError as exc:
        if labels.is_head_race(exc):
            raise Stale(await labels.current_head(note["turn_id"], mode_id)) from None
        raise
    except DBAPIError as exc:
        if _lock_busy(exc):                                       # another writer holds the head: 409 now, never a long wait
            raise Stale(await labels.current_head(note["turn_id"], mode_id)) from exc
        raise
    return {"cell": labels.cell_payload(cell, graph, base=label["id"]), "label": label}


def _require_platform(principal: Any) -> None:
    """Defence in depth under the route's own check: only the platform team sets a note aside or brings it back."""
    from . import taxonomy

    if getattr(principal, "role", None) != "superadmin":
        raise taxonomy.forbidden("platform_only")


def _event_out(r: Mapping[str, Any]) -> dict[str, Any]:
    from . import taxonomy

    return {"seq": r["seq"], "action": r["action"], "detail": taxonomy.json_value(r["detail"]) or {},
            "at": r["at"].isoformat() if r["at"] else None}


async def dispose_in(conn: Any, principal: Any, annotation_id: str, *, outcome: str, note: str) -> dict[str, Any]:
    """Set a Wrong note aside inside the caller's LAB transaction: an append-only dispose event on its lineage. The
    note leaves the unfiled list until it is brought back or edited. `no_mode_fits`: no failure mode describes it.
    `not_a_failure`: the answer was fine, refused while the answer resolves present for any mode. The caller has
    already decided who may do this (dispose_note asks for the platform team)."""
    from . import labels, taxonomy

    if outcome not in DISPOSE_OUTCOMES:
        raise taxonomy.invalid("unknown_outcome", outcome=outcome)
    if not taxonomy.is_uuid(annotation_id):
        raise NotFound("annotation_not_found")
    a = (await conn.execute(text("SELECT * FROM tracelab.annotations WHERE id = CAST(:a AS uuid)"),
                            {"a": str(uuid.UUID(str(annotation_id)))})).mappings().first()
    if a is None or a["kind"] != "verdict":
        raise NotFound("annotation_not_found")
    if a["superseded_by"] is not None:
        raise Stale({"annotation_id": await _live_head_id(conn, a["lineage_id"])})
    if a["tombstone"] or a["verdict"] != "wrong":
        raise taxonomy.invalid("not_a_wrong_note", annotation_id=annotation_id)
    if outcome == "not_a_failure":
        present = await labels.turn_present_modes(conn, a["turn_id"])
        if present:
            raise taxonomy.invalid("not_a_failure_while_present", modes=present)
    r = (await conn.execute(text(
        "INSERT INTO tracelab.annotation_events (organization_id, conversation_key, turn_id, lineage_id, "
        "annotation_id, action, detail, actor_id, actor_email) VALUES (CAST(:o AS uuid), :ck, :t, CAST(:l AS uuid), "
        "CAST(:a AS uuid), 'dispose', CAST(:d AS jsonb), CAST(:u AS uuid), :e) RETURNING seq, action, detail, at"),
        {"o": str(a["organization_id"]), "ck": a["conversation_key"], "t": a["turn_id"], "l": str(a["lineage_id"]),
         "a": str(a["id"]), "d": json.dumps({"outcome": outcome, "note": (note or "").strip()[:DISPOSE_NOTE_MAX]}),
         "u": str(principal.user_id), "e": getattr(principal, "email", None) or None})).mappings().first()
    return _event_out(r)


async def dispose_note(principal: Any, annotation_id: str, *, outcome: str, note: str) -> dict[str, Any]:
    """Platform only. NotFound, Invalid, Stale (the note was edited since: it carries the live version's id)."""
    _require_platform(principal)
    async with db.lab_engine().begin() as conn:
        await _limits(conn)
        return {"event": await dispose_in(conn, principal, annotation_id, outcome=outcome, note=note)}


async def undispose_note(principal: Any, annotation_id: str) -> dict[str, Any]:
    """Platform only: bring a set-aside note back to the unfiled list."""
    from . import taxonomy

    _require_platform(principal)
    if not taxonomy.is_uuid(annotation_id):
        raise NotFound("annotation_not_found")
    async with db.lab_engine().begin() as conn:
        await _limits(conn)
        a = (await conn.execute(text("SELECT * FROM tracelab.annotations WHERE id = CAST(:a AS uuid)"),
                                {"a": str(uuid.UUID(str(annotation_id)))})).mappings().first()
        if a is None or a["kind"] != "verdict":
            raise NotFound("annotation_not_found")
        r = (await conn.execute(text(
            "INSERT INTO tracelab.annotation_events (organization_id, conversation_key, turn_id, lineage_id, "
            "annotation_id, action, detail, actor_id, actor_email) VALUES (CAST(:o AS uuid), :ck, :t, "
            "CAST(:l AS uuid), CAST(:a AS uuid), 'undispose', '{}'::jsonb, CAST(:u AS uuid), :e) "
            "RETURNING seq, action, detail, at"),
            {"o": str(a["organization_id"]), "ck": a["conversation_key"], "t": a["turn_id"],
             "l": str(a["lineage_id"]), "a": str(a["id"]), "u": str(principal.user_id),
             "e": getattr(principal, "email", None) or None})).mappings().first()
    return {"event": _event_out(r)}
