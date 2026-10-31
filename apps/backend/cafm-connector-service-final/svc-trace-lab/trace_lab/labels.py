"""Labels: one present/absent decision per (answer, failure mode), latest wins (Trace Lab Phase 5, spec §6).

Rows are never rewritten. A change is a new row that supersedes the head (`superseded_by` set once, by
trigger); a clear is a tombstone. A live mode's cell is resolved at read time from the heads of the mode, its
merged parts and its nearest split ancestor (resolve_cell, spec §5.4), so a merge or split never touches a
label. Human-origin (R10): human-written labels, plus labels taken from an AI suggestion or proposal one at a
time (approval='single'). Bulk-approved labels are never human-origin and never reach a judge's dev or test
split.

The first half of this module is pure (the cell resolution). The second half reads and writes: labels live in Trace
Lab's own database (db.lab_engine(), schema tracelab); the chat's turns and spans are READ through the read-only main
engine, and no SQL statement ever spans the two (Golden Rule G0). A turn is looked up on one engine and its id handed
to the other as `= ANY(CAST(:ids AS text[]))`. A turn's evidence is copied into the lab database (evidence.snapshot)
before a label is written on it, so the quote a label keeps outlives the chat's 90-day span purge; the label also keeps
its own copy of the quoted text and the step's name, kind and agent.

The last section is the Labels grid: reviewed answers x live modes, how complete it is, the sample fractions and the
fair rates beside it, and the human-origin cells a judge may learn from. Every figure there counts only the rows the
caller is allowed to see (the blind rule), and the platform owner's views read every company.
"""
from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from . import db, taxonomy
from .review_errors import Stale

SOURCES = ("human_structured_label", "accepted_suggestion", "rejected_suggestion", "accepted_label_proposal",
           "human_open_code")
HUMAN_ORIGIN_SOURCES = ("human_structured_label", "human_open_code")
PROPOSAL_SOURCES = ("accepted_suggestion", "rejected_suggestion", "accepted_label_proposal")
STATES = ("present", "absent", "unchecked")
APPROVALS = ("single", "bulk")
SAMPLE_FRACTION_COPY = ("Share of reviewed answers — not a rate for all answers: targeted batches change "
                        "the mix.")
WITHDRAWN_NOTE = "The note this label came from was withdrawn."


class _AnyHead:
    def __repr__(self) -> str:
        return "ANY_HEAD"


#: `base` for server-side writes inside one transaction (open-code labels when a mode is created, split
#: assignments, an accepted grouping): supersede whatever head is there. Never taken from a client.
ANY_HEAD: Any = _AnyHead()


@dataclass(frozen=True)
class Head:
    label_id: str
    turn_id: str
    mode_id: str
    present: bool | None
    tombstone: bool
    source: str
    approval: str
    created_at: datetime
    decided_by_email: str | None
    quote: str | None = None
    note: str | None = None
    annotation_id: str | None = None


@dataclass(frozen=True)
class Cell:
    state: str
    label_id: str | None
    source: str | None
    approval: str | None
    via_mode_id: str | None
    human_origin: bool
    decided_by_email: str | None = None
    decided_at: datetime | None = None


UNCHECKED = Cell("unchecked", None, None, None, None, False)


def is_human_origin(source: str | None, approval: str | None = "single") -> bool:
    """R10: human-written sources always; AI-derived sources only when decided one at a time."""
    if source in HUMAN_ORIGIN_SOURCES:
        return True
    return source in PROPOSAL_SOURCES and approval == "single"


def _cell(h: Head, state: str, via: str | None) -> Cell:
    return Cell(state, h.label_id, h.source, h.approval, via, is_human_origin(h.source, h.approval),
                h.decided_by_email, h.created_at)


def resolve_cell(graph: taxonomy.ModeGraph, heads: dict[tuple[str, str], Head], turn_id: str, mode: dict) -> Cell:
    """The six rules of spec §5.4, first match wins."""
    mid = mode["id"]
    own = heads.get((turn_id, mid))
    if own is not None and own.created_at >= taxonomy.meaning_at(mode):
        if own.tombstone:
            return UNCHECKED                                                      # 1: an explicit clear wins
        return _cell(own, "present" if own.present else "absent", None)         # 2: decided on the full meaning
    counting: list[tuple[str, Head | None]] = []
    for part in graph.parts(mid) or [mode]:
        h = heads.get((turn_id, part["id"]))
        rescoped = mode["rescoped_at"] if part["id"] == mid else part["rescoped_at"]
        counts = h is not None and not h.tombstone and h.created_at >= rescoped
        counting.append((part["id"], h if counts else None))
    present = [(pid, h) for pid, h in counting if h is not None and h.present]
    if present:                                                                   # 3: any part present
        pid, h = max(present, key=lambda x: (x[0] == mid, x[1].created_at))
        return _cell(h, "present", None if pid == mid else pid)
    if counting and all(h is not None and h.present is False for _pid, h in counting):   # 4: every part absent
        pid, h = max(counting, key=lambda x: x[1].created_at)
        return _cell(h, "absent", None if pid == mid else pid)
    ancestors = graph.split_ancestors(mid)
    if ancestors:                                                                 # 5: split parent's absent
        a = ancestors[0]
        h = heads.get((turn_id, a["id"]))
        if (h is not None and not h.tombstone and h.present is False and h.created_at >= a["rescoped_at"]
                and h.created_at < mode["created_at"]):
            return _cell(h, "absent", a["id"])
    return UNCHECKED                                                              # 6


def resolve(graph: taxonomy.ModeGraph, heads: dict[tuple[str, str], Head], turn_ids: list[str],
            mode_ids: list[str]) -> dict[tuple[str, str], Cell]:
    out: dict[tuple[str, str], Cell] = {}
    for mid in mode_ids:
        mode = graph.modes.get(mid)
        if mode is None:
            continue
        for t in turn_ids:
            out[(t, mid)] = resolve_cell(graph, heads, t, mode)
    return out


def cell_payload(cell: Cell, graph: taxonomy.ModeGraph | None = None, *, base: str | None = None,
                 proposal: dict | None = None, note_withdrawn: bool = False) -> dict[str, Any]:
    """What the grid and the turn chips read. `base` is the mode's own head id, the value a write sends back."""
    via = cell.via_mode_id
    out: dict[str, Any] = {
        "state": cell.state, "label_id": cell.label_id, "source": cell.source, "human_origin": cell.human_origin,
        "via": via, "via_name": (graph.modes.get(via) or {}).get("name") if (graph is not None and via) else None,
        "decided_by": cell.decided_by_email, "decided_at": cell.decided_at.isoformat() if cell.decided_at else None,
        "approval": cell.approval, "base": base}
    if cell.state == "unchecked" and proposal:
        out["proposal"] = proposal
    if note_withdrawn:
        out["note_withdrawn"] = True
    return out


# ── reading and writing heads ─────────────────────────────────────────────────────────

SNAPSHOT_REASON = "labelled"        # evidence_turns.reason of a turn first kept because it was labelled
LOCK_WAIT_MS = 2000                 # how long a write waits for another writer's lock on one head before it is a 409
HEAD_RACES = ("ux_tl_labels_head", "ux_tl_labels_supersedes")
CHUNK = 2000                        # turn ids per query
QUESTION_FETCH = 4000               # characters of a question read from the chat (it keeps at most this many)
QUESTION_SHOWN = 200                # characters of a question a list or drawer shows


def is_owner(principal, org: str | None) -> bool:
    """The platform taxonomy owner: a superadmin not acting for a company (D7)."""
    return getattr(principal, "role", None) == "superadmin" and not org


def _iso(v: Any) -> Any:
    return v.isoformat() if isinstance(v, datetime) else v


def label_payload(r: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in dict(r).items():
        if isinstance(v, uuid.UUID):
            out[k] = str(v)
        elif k == "basis":
            out[k] = taxonomy.json_value(v) or {}
        else:
            out[k] = _iso(v)
    return out


def _head(r: Any) -> Head:
    return Head(label_id=str(r["id"]), turn_id=r["turn_id"], mode_id=str(r["mode_id"]), present=r["present"],
                tombstone=bool(r["tombstone"]), source=r["source"], approval=r["approval"],
                created_at=r["created_at"], decided_by_email=r["decided_by_email"], quote=r["quote"], note=r["note"],
                annotation_id=str(r["annotation_id"]) if r["annotation_id"] else None)


async def load_heads(conn, turn_ids: list[str] | None, mode_ids: list[str], *,
                     org: str | None = None) -> dict[tuple[str, str], Head]:
    """Live heads (superseded_by IS NULL) for these modes; turn_ids None means every turn. `conn` is a LAB connection."""
    if not mode_ids or (turn_ids is not None and not turn_ids):
        return {}
    where = ["superseded_by IS NULL", "mode_id = ANY(CAST(:m AS uuid[]))"]
    params: dict[str, Any] = {"m": list(mode_ids)}
    if turn_ids is not None:
        where.append("turn_id = ANY(CAST(:t AS text[]))")
        params["t"] = list(turn_ids)
    if org:
        where.append("organization_id = CAST(:org AS uuid)")
        params["org"] = org
    rows = (await conn.execute(text(
        "SELECT id, turn_id, mode_id, present, tombstone, source, approval, created_at, decided_by_email, quote, "
        "note, annotation_id FROM tracelab.labels WHERE " + " AND ".join(where)), params)).mappings().all()
    return {(r["turn_id"], str(r["mode_id"])): _head(r) for r in rows}


async def turn_info_from_labels(conn, turn_ids: list[str], mode_ids: list[str]) -> dict[str, dict]:
    """The company and session of answers that already carry a label of these modes, read from the labels themselves
    (a label keeps both), so a split needs no second look at the chat. `conn` is a LAB connection."""
    if not turn_ids or not mode_ids:
        return {}
    rows = (await conn.execute(text(
        "SELECT DISTINCT ON (turn_id) turn_id, session_id, CAST(organization_id AS text) AS organization_id "
        "FROM tracelab.labels WHERE turn_id = ANY(CAST(:t AS text[])) AND mode_id = ANY(CAST(:m AS uuid[])) "
        "ORDER BY turn_id, created_at DESC, id"), {"t": list(turn_ids), "m": list(mode_ids)})).mappings().all()
    return {r["turn_id"]: dict(r) for r in rows}


@asynccontextmanager
async def _read_main() -> AsyncIterator[Any]:
    """A read-only, 10-second-bounded transaction on the read-only main engine (the chat's tables)."""
    async with db.main_engine().begin() as conn:
        await conn.execute(text("SET TRANSACTION READ ONLY"))
        await conn.execute(text("SET LOCAL statement_timeout = '10s'"))
        yield conn


def shown_question(raw: Any) -> str:
    """A stored question as a person may read it in a list or a drawer (spec 3.9): the asker's own words (a wrapped
    prompt is cut back to what was typed, as the Conversations screen shows it), with secret values redacted, then cut
    to QUESTION_SHOWN characters. The redaction comes first, so a secret that straddles the cut cannot survive in part."""
    from . import normalize

    question, _flags = normalize.repair_question({"question": str(raw or "")}, None, None)
    return normalize.redact_text(question)[:QUESTION_SHOWN]


def _with_shown_question(row: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(row)
    if "question" in out:
        out["question"] = shown_question(out["question"])
    return out


async def turn_rows(turn_ids: list[str], *, with_question: bool = False) -> dict[str, dict]:
    """turn_id -> {turn_id, session_id, organization_id[, question]} for the answers the chat still holds, else the
    answer's evidence snapshot in the lab database. An unknown id is absent. Two reads on two engines. The question,
    when asked for, is the redacted text of shown_question, never the stored column."""
    from . import conversations, evidence

    ids = [t for t in dict.fromkeys(turn_ids or []) if isinstance(t, str) and 1 <= len(t) <= evidence.MAX_ID_LENGTH]
    out: dict[str, dict] = {}
    extra = f", left(coalesce(question, ''), {QUESTION_FETCH}) AS question" if with_question else ""
    for i in range(0, len(ids), CHUNK):
        async with _read_main() as conn:
            for r in (await conn.execute(text(
                    f"SELECT turn_id, session_id, CAST(organization_id AS text) AS organization_id{extra} "
                    f"FROM {conversations.TURNS} WHERE turn_id = ANY(CAST(:t AS text[]))"),
                    {"t": ids[i:i + CHUNK]})).mappings().all():
                out[r["turn_id"]] = _with_shown_question(r)
    rest = [t for t in ids if t not in out]
    for i in range(0, len(rest), CHUNK):
        qcol = f", left(coalesce(turn->>'question', ''), {QUESTION_FETCH}) AS question" if with_question else ""
        async with db.lab_engine().connect() as conn:
            for r in (await conn.execute(text(
                    "SELECT turn_id, turn->>'session_id' AS session_id, CAST(organization_id AS text) AS organization_id"
                    f"{qcol} FROM tracelab.evidence_turns WHERE turn_id = ANY(CAST(:t AS text[]))"),
                    {"t": rest[i:i + CHUNK]})).mappings().all():
                out[r["turn_id"]] = _with_shown_question(r)
    return out


async def scoped_turn(principal, org: str | None, turn_id: str) -> dict:
    """The answer, if the caller may see it: a company admin's own company, a superadmin's named company (or every
    company in the platform view). Another company's answer, an unknown one, and one with no company all read as not
    found (a turn without a company cannot be labelled). A turn the chat has purged is read from its snapshot."""
    from . import annotations

    if getattr(principal, "role", None) != "superadmin":
        # The reads below also fall back to a snapshot, which only checks the company named. A company admin therefore
        # reads one company's answers only: their own, named or implied by the route, never another's or "all".
        own = str(principal.organization_id) if getattr(principal, "organization_id", None) else None
        if own is None or not org or str(org) != own:
            raise taxonomy.not_found("turn_not_found")
    turn = await annotations._scoped_turn(principal, org, turn_id)
    if turn is None or not turn.get("organization_id"):
        raise taxonomy.not_found("turn_not_found")
    return turn


def span_of(row: Any) -> dict[str, Any]:
    """The copied evidence fields of an annotation or proposal row (D5)."""
    r = dict(row)
    return {"span_id": str(r["span_id"]) if r.get("span_id") else None, "span_name": r.get("span_name"),
            "span_kind": r.get("span_kind"), "span_agent": r.get("span_agent")}


async def span_meta(span_id: str | None, field: str | None, turn: dict) -> dict[str, Any]:
    """Name, kind and agent of the step a proposal's evidence points at, read through evidence.load (the live spans
    while the chat has them, else the snapshot). A step that is gone keeps its id and no name."""
    if not span_id:
        return {"span_id": None, "span_name": "turn", "span_kind": field or "answer", "span_agent": None}
    gone = {"span_id": span_id, "span_name": None, "span_kind": None, "span_agent": None}
    if not taxonomy.is_uuid(span_id) or not turn.get("organization_id"):
        return gone
    from . import evidence

    have = (await evidence.load([turn["turn_id"]], organization_id=turn["organization_id"])).get(turn["turn_id"])
    wanted = str(uuid.UUID(str(span_id)))
    s = next((s for s in (have.spans if have else []) if str(s.get("id")).lower() == wanted), None)
    if s is None:
        return gone
    return {"span_id": wanted, "span_name": s.get("name"), "span_kind": s.get("kind"), "span_agent": s.get("agent")}


def anchor_of(anchor: Any):
    """An annotations.Anchor from a dict or a request model, or Invalid. A step's block needs the step; the turn's own
    question and answer take none."""
    from .annotations import FIELDS, QUOTE_MAX, TURN_FIELDS, Anchor

    if isinstance(anchor, Mapping):
        a = dict(anchor)
    elif hasattr(anchor, "model_dump"):
        a = anchor.model_dump()
    else:
        raise taxonomy.invalid("anchor_required")
    field, quote, occurrence = a.get("field"), a.get("quote"), a.get("occurrence")
    occurrence = 1 if occurrence is None else occurrence
    if (field not in FIELDS or not isinstance(quote, str) or not quote.strip() or len(quote) > QUOTE_MAX
            or not isinstance(occurrence, int) or isinstance(occurrence, bool) or occurrence < 1):
        raise taxonomy.invalid("anchor_required")
    if field in TURN_FIELDS:
        return Anchor(None, field, quote, occurrence)
    span_id = a.get("span_id")
    if not isinstance(span_id, str) or not span_id.strip():
        raise taxonomy.invalid("span_not_in_turn")
    return Anchor(span_id, field, quote, occurrence)


async def evidence_from_anchor(turn: dict, anchor: Any) -> dict[str, Any]:
    """A person's optional quote on a label: verbatim in the block the reviewer saw (annotations._pin checks it
    against the normalized, redacted turn, exactly as the Conversations view shows it), or refused."""
    from . import annotations

    pin = await annotations._pin(turn, anchor_of(anchor))
    return {"quote": pin["quote"], "span": {"span_id": pin["span_id"], "span_name": pin["sname"],
                                            "span_kind": pin["skind"], "span_agent": pin["sagent"]}}


async def write(conn, principal, *, turn: dict, mode_id: str, present: bool | None, source: str, base: Any,
                note: str | None = None, quote: str | None = None, span: dict | None = None,
                proposal_id: str | None = None, annotation_id: str | None = None, approval: str = "single",
                basis: dict | None = None, spot_check: dict | None = None) -> dict:
    """Write one label inside the caller's LAB transaction (spec §6.6 steps 1-6). Raises Stale on a changed head.
    `turn` is {turn_id, organization_id, session_id} of an answer the caller has already scoped and snapshotted."""
    from . import proposals

    if source not in SOURCES or approval not in APPROVALS:
        raise taxonomy.invalid("unknown_source", source=source, approval=approval)
    if not turn.get("organization_id"):
        raise taxonomy.not_found("turn_not_found")
    if not taxonomy.is_uuid(mode_id):
        raise taxonomy.not_found("mode_not_found")
    mode_id = str(uuid.UUID(str(mode_id)))
    m = (await conn.execute(text("SELECT status FROM tracelab.modes WHERE id = CAST(:m AS uuid)"),
                            {"m": mode_id})).mappings().first()
    if m is None:
        raise taxonomy.not_found("mode_not_found")
    if m["status"] not in taxonomy.LIVE_STATUSES:
        graph = await taxonomy.load_graph(conn)
        raise taxonomy.conflict("mode_not_live", live_mode_id=graph.root_of(mode_id))
    head_sql = ("SELECT * FROM tracelab.labels WHERE turn_id = :t AND mode_id = CAST(:m AS uuid) "
                "AND superseded_by IS NULL")
    head = (await conn.execute(text(head_sql + " FOR UPDATE"), {"t": turn["turn_id"], "m": mode_id})).mappings().first()
    if head is None:
        # A writer that waited on the winner's lock matches nothing once the winner superseded the row: the live
        # head is read again, unlocked, so the 409 carries it.
        head = (await conn.execute(text(head_sql), {"t": turn["turn_id"], "m": mode_id})).mappings().first()
    head_id = str(head["id"]) if head is not None else None
    if base is not ANY_HEAD and (str(base).strip().lower() if base else None) != head_id:
        raise Stale(label_payload(head) if head is not None else None)
    decision: str | None = None
    if source == "human_structured_label" and present is not None and proposal_id is None and spot_check is None:
        pend = (await conn.execute(text(
            "SELECT CAST(id AS text) AS id, suggestion FROM tracelab.proposals WHERE kind = 'cell' "
            "AND status = 'pending' AND turn_id = :t AND mode_id = CAST(:m AS uuid)"),
            {"t": turn["turn_id"], "m": mode_id})).mappings().first()
        if pend is not None:                      # assumed seen, whatever the client reports (§6.2)
            proposal_id = pend["id"]
            if (pend["suggestion"] == "present") == bool(present):
                source, decision = "accepted_label_proposal", "accepted"
            else:
                decision = "dismissed"
    elif proposal_id is not None:
        decision = "accepted" if source in ("accepted_suggestion", "accepted_label_proposal") else "dismissed"
    new_id = str(uuid.uuid4())
    if head is not None:
        res = await conn.execute(text("UPDATE tracelab.labels SET superseded_by = CAST(:n AS uuid) "
                                      "WHERE id = CAST(:h AS uuid) AND superseded_by IS NULL"),
                                 {"n": new_id, "h": head_id})
        if res.rowcount != 1:
            raise Stale(label_payload(head))
    sp = span or {}
    row = (await conn.execute(text(
        "INSERT INTO tracelab.labels (id, organization_id, turn_id, session_id, mode_id, present, tombstone, "
        "source, note, quote, span_id, span_name, span_kind, span_agent, proposal_id, annotation_id, approval, "
        "basis, decided_by, decided_by_email, supersedes) VALUES (CAST(:id AS uuid), CAST(:org AS uuid), :turn, "
        ":session, CAST(:mode AS uuid), :present, :tomb, :source, :note, :quote, CAST(:span_id AS uuid), "
        ":span_name, :span_kind, :span_agent, CAST(:proposal AS uuid), CAST(:ann AS uuid), :approval, "
        "CAST(:basis AS jsonb), CAST(:by AS uuid), :email, CAST(:sup AS uuid)) RETURNING *"),
        {"id": new_id, "org": turn["organization_id"], "turn": turn["turn_id"], "session": turn.get("session_id"),
         "mode": mode_id, "present": present, "tomb": present is None, "source": source,
         "note": (note or None) and str(note)[:2000], "quote": (quote or None) and str(quote)[:2000],
         "span_id": sp.get("span_id"), "span_name": sp.get("span_name"), "span_kind": sp.get("span_kind"),
         "span_agent": sp.get("span_agent"), "proposal": proposal_id, "ann": annotation_id, "approval": approval,
         "basis": json.dumps(basis or {}, default=str), "by": str(principal.user_id),
         "email": getattr(principal, "email", None) or None, "sup": head_id})).mappings().first()
    await proposals.close_for_cell(conn, turn["turn_id"], mode_id, "human_decision_exists",
                                   keep=proposal_id if decision else None)
    if decision is not None:
        res = await conn.execute(text(
            "UPDATE tracelab.proposals SET status = :s, decided_by = CAST(:u AS uuid), decided_by_email = :e, "
            "decided_at = now(), label_id = CAST(:l AS uuid) WHERE id = CAST(:p AS uuid) AND status = 'pending'"),
            {"s": decision, "u": str(principal.user_id), "e": getattr(principal, "email", None) or None,
             "l": new_id, "p": proposal_id})
        if res.rowcount != 1:
            raise Stale(None)
    return label_payload(row)


async def current_head(turn_id: str, mode_id: str) -> dict | None:
    async with db.lab_engine().connect() as conn:
        r = (await conn.execute(text("SELECT * FROM tracelab.labels WHERE turn_id = :t AND "
                                     "mode_id = CAST(:m AS uuid) AND superseded_by IS NULL"),
                                {"t": turn_id, "m": mode_id})).mappings().first()
    return label_payload(r) if r is not None else None


def is_head_race(exc: BaseException) -> bool:
    """True when `exc` is the database refusing a second live head (or a second successor) for one cell: another
    writer got there first. Looked up through the driver wrappers; the message is the fallback."""
    seen: Any = exc
    for _ in range(5):
        if seen is None:
            break
        if getattr(seen, "constraint_name", None) in HEAD_RACES:
            return True
        seen = getattr(seen, "orig", None) or getattr(seen, "__cause__", None)
    return any(n in str(exc) for n in HEAD_RACES)


async def _limits(conn) -> None:
    """The transaction's time limits: the statement limit, and a short wait for another writer's head lock."""
    await conn.execute(text("SET LOCAL statement_timeout = '10s'"))
    await conn.execute(text(f"SET LOCAL lock_timeout = '{int(LOCK_WAIT_MS)}ms'"))


def blind_head(current: Any) -> dict[str, Any] | None:
    """What a stale 409 may carry to a caller who may not read the cell yet (spec 2.3, 6.1): the head's id, which the
    client needs as its new base, and no value, note, quote, name or email. None when there is no head to name."""
    if not isinstance(current, Mapping) or not current.get("id"):
        return None
    return {"id": current["id"]}


async def _may_read_cells(principal, org: str | None, turn_id: str) -> bool:
    """The blind rule for one answer's labels, as cells_for_turn applies it: the platform owner always, anyone else
    only once they judged the answer, revealed it, or saw it through a finished sample (annotations.visible_others)."""
    from . import annotations

    if is_owner(principal, org):
        return True
    async with db.lab_engine().connect() as conn:
        return turn_id in await annotations.visible_others(conn, principal, org, [turn_id])


async def set_label(principal, org: str | None, turn_id: str, mode_id: str, *, present: bool | None,
                    base: str | None, note: str | None = None, anchor: Any = None,
                    spot_check_seed: str | None = None) -> dict:
    """A person clicks a cell (or clears it, or decides a spot-check item with the suggestion hidden).

    A stale write is a 409 carrying the live head, but only to a caller who may read that label: one who has not judged
    the answer (and is not the platform owner) gets just the head's id (blind_head), so the 409 cannot show them the
    note, quote, value or name of a label the blind rule still hides. review_errors.NotFound, Invalid, Conflict, Stale."""
    try:
        return await _set_label(principal, org, turn_id, mode_id, present=present, base=base, note=note, anchor=anchor,
                                spot_check_seed=spot_check_seed)
    except Stale as exc:
        if exc.current is None:
            raise
        try:
            may_read = await _may_read_cells(principal, org, turn_id)
        except Exception:                      # noqa: BLE001 - a lookup that fails is answered as if the caller were blind
            may_read = False
        if may_read:
            raise
        raise Stale(blind_head(exc.current)) from None


async def _set_label(principal, org: str | None, turn_id: str, mode_id: str, *, present: bool | None,
                     base: str | None, note: str | None = None, anchor: Any = None,
                     spot_check_seed: str | None = None) -> dict:
    """set_label's work. Order: the answer is scoped and the quote checked against it (the chat, read-only), the
    answer's evidence is copied into the lab database (it raises when it cannot be kept, and then nothing is written),
    and only then does the one lab transaction supersede the head and insert the new label."""
    from . import annotations, evidence

    if not taxonomy.is_uuid(mode_id):
        raise taxonomy.not_found("mode_not_found")
    mode_id = str(uuid.UUID(str(mode_id)))
    turn = await scoped_turn(principal, org, turn_id)
    pinned = await evidence_from_anchor(turn, anchor) if anchor is not None else {}
    await evidence.snapshot([turn["turn_id"]], organization_id=turn["organization_id"], reason=SNAPSHOT_REASON)
    try:
        async with db.lab_engine().begin() as conn:
            await _limits(conn)
            spot, basis = None, None
            if spot_check_seed:
                spot = (await conn.execute(text(
                    "SELECT CAST(id AS text) AS id, kind, suggestion FROM tracelab.proposals "
                    "WHERE status = 'pending' AND kind IN ('cell', 'depth_scan') AND turn_id = :t "
                    "AND mode_id = CAST(:m AS uuid) AND flags->>'spot_check_seed' = :seed"),
                    {"t": turn_id, "m": mode_id, "seed": spot_check_seed})).mappings().first()
                if spot is None or present is None:
                    raise taxonomy.invalid("not_in_spot_check", turn_id=turn_id, mode_id=mode_id)
                spot = dict(spot)
                basis = {"spot_check": {"seed": spot_check_seed, "proposal_id": spot["id"], "kind": spot["kind"]}}
            label = await write(conn, principal, turn=turn, mode_id=mode_id, present=present,
                                source="human_structured_label", base=base, note=note, quote=pinned.get("quote"),
                                span=pinned.get("span"), basis=basis, spot_check=spot)
            graph = await taxonomy.load_graph(conn)
            mode = graph.modes[mode_id]
            heads = await load_heads(conn, [turn_id], graph.relevant_ids([mode["id"]]))
            cell = resolve_cell(graph, heads, turn_id, mode)
    except IntegrityError as exc:
        if is_head_race(exc):
            raise Stale(await current_head(turn_id, mode_id)) from None
        raise
    except DBAPIError as exc:
        if annotations._lock_busy(exc):                       # another writer holds the head: 409 now, never a long wait
            raise Stale(await current_head(turn_id, mode_id)) from exc
        raise
    return {"cell": cell_payload(cell, graph, base=label["id"]), "label": label}


async def history(principal, org: str | None, turn_id: str, mode_id: str) -> dict:
    """The supersedes chain for one cell, newest first: {"chain": [...], "hidden": False}. The chain is made of the
    labels themselves (value, note, quote, name), so it follows the blind rule as cells_for_turn does: for a caller who
    is not the platform owner and has not judged the answer it is empty and `hidden` is True (spec 2.3, 6.1)."""
    from . import annotations

    if not taxonomy.is_uuid(mode_id):
        raise taxonomy.not_found("mode_not_found")
    await scoped_turn(principal, org, turn_id)
    async with db.lab_engine().connect() as conn:
        if not is_owner(principal, org):
            if turn_id not in await annotations.visible_others(conn, principal, org, [turn_id]):
                return {"chain": [], "hidden": True}
        rows = (await conn.execute(text(
            "SELECT * FROM tracelab.labels WHERE turn_id = :t AND mode_id = CAST(:m AS uuid) "
            "ORDER BY created_at DESC, (superseded_by IS NULL) DESC"),
            {"t": turn_id, "m": str(uuid.UUID(str(mode_id)))})).mappings().all()
    return {"chain": [label_payload(r) for r in rows], "hidden": False}


async def cells_for_turn(principal, org: str | None, turn_id: str) -> dict:
    """Every live mode's cell on one answer, for the Conversations turn chips. Hidden while the answer is still
    blind for a company caller (spec §2.3)."""
    from . import annotations, proposals

    await scoped_turn(principal, org, turn_id)
    async with db.lab_engine().connect() as conn:
        if not is_owner(principal, org):
            if turn_id not in await annotations.visible_others(conn, principal, org, [turn_id]):
                return {"cells": [], "hidden": True}
        graph = await taxonomy.load_graph(conn)
        live = graph.live()
        ids = [m["id"] for m in live]
        heads = await load_heads(conn, [turn_id], graph.relevant_ids(ids)) if ids else {}
        pend = await proposals.pending_cells(conn, [turn_id], ids)
    out = []
    for m in live:
        own = heads.get((turn_id, m["id"]))
        out.append({"mode_id": m["id"], "name": m["name"], "status": m["status"],
                    **cell_payload(resolve_cell(graph, heads, turn_id, m), graph,
                                   base=own.label_id if own else None, proposal=pend.get((turn_id, m["id"])))})
    return {"cells": out, "hidden": False}


async def reviewed_rows(conn, org: str | None, *, batch_id: str | None = None, since: datetime | None = None,
                        until: datetime | None = None) -> list[dict]:
    """Reviewed answers (at least one live, non-tombstone verdict) in scope, newest first. `conn` is a LAB connection.
    The verdicts and the batches are read there; the chat's own row (company, session, question, start) is read on the
    read-only main engine for those turn ids, and the two are joined here. Golden answers and answers with no company
    are left out (spec §4)."""
    from . import annotations, conversations

    if batch_id is not None and not taxonomy.is_uuid(batch_id):
        return []
    reviewed = sorted(t for t in await annotations.reviewed_turns(conn, org) if not conversations.is_golden_turn(t))
    batch_of: dict[tuple[str, str], dict] = {}
    for i in range(0, len(reviewed), CHUNK):
        where, params = "", {"ids": reviewed[i:i + CHUNK]}
        if batch_id:
            where, params["b"] = " AND si.batch_id = CAST(:b AS uuid)", str(uuid.UUID(str(batch_id)))
        for r in (await conn.execute(text(
                "SELECT si.turn_id, CAST(si.organization_id AS text) AS organization_id, "
                "CAST(si.batch_id AS text) AS batch_id, sb.plan_step, sb.strategy, sb.withdrawn_at "
                "FROM tracelab.sample_items si JOIN tracelab.sample_batches sb ON sb.id = si.batch_id "
                f"WHERE si.turn_id = ANY(CAST(:ids AS text[])){where}"), params)).mappings().all():
            batch_of[(r["turn_id"], r["organization_id"])] = dict(r)
    ids = sorted({t for (t, _o) in batch_of}) if batch_id else reviewed
    where_main = ["tt.turn_id = ANY(CAST(:ids AS text[]))", "tt.organization_id IS NOT NULL"]
    extra: dict[str, Any] = {}
    if org:
        where_main.append("tt.organization_id = CAST(:org AS uuid)")
        extra["org"] = org
    if since is not None:
        where_main.append("tt.started_at >= :since")
        extra["since"] = since
    if until is not None:
        where_main.append("tt.started_at < :until")
        extra["until"] = until
    rows: list[dict] = []
    for i in range(0, len(ids), CHUNK):
        async with _read_main() as main:
            found = (await main.execute(text(
                "SELECT tt.turn_id, tt.session_id, CAST(tt.organization_id AS text) AS organization_id, "
                "left(coalesce(tt.question, ''), 200) AS question, tt.started_at, "
                f"{conversations.GROUP_KEY_SQL} AS conversation_key FROM {conversations.TURNS} tt "
                f"WHERE {' AND '.join(where_main)}"), {"ids": ids[i:i + CHUNK], **extra})).mappings().all()
        for r in found:
            b = batch_of.get((r["turn_id"], r["organization_id"]))
            if batch_id and b is None:
                continue
            rows.append({**dict(r), "batch_id": b["batch_id"] if b else None, "plan_step": b["plan_step"] if b else None,
                         "strategy": b["strategy"] if b else None, "withdrawn_at": b["withdrawn_at"] if b else None})
    rows.sort(key=lambda r: r["turn_id"])
    rows.sort(key=lambda r: r["started_at"], reverse=True)
    return rows


# ── the grid, completeness and the cells the judges may use ────────────────────────────────

async def _grid(conn, org: str | None, *, statuses: tuple[str, ...], mode_ids: list[str] | None = None,
                batch_id: str | None = None, since: datetime | None = None, until: datetime | None = None):
    """The reviewed answers and the live modes of `statuses`, with every cell resolved. `conn` is a LAB connection;
    reviewed_rows reads the chat's own rows (company, session, question) on the read-only main engine. The question is
    shown redacted, as the Conversations view shows it."""
    from . import normalize

    graph = await taxonomy.load_graph(conn)
    wanted = {str(uuid.UUID(str(x))) if taxonomy.is_uuid(x) else str(x) for x in mode_ids or []}
    modes = [m for m in graph.live() if m["status"] in statuses and (not wanted or m["id"] in wanted)]
    rows = [{**r, "question": normalize.redact_text(r.get("question") or "")}
            for r in await reviewed_rows(conn, org, batch_id=batch_id, since=since, until=until)]
    ids = [r["turn_id"] for r in rows]
    mids = [m["id"] for m in modes]
    heads = await load_heads(conn, ids, graph.relevant_ids(mids)) if ids and mids else {}
    return graph, modes, rows, heads, resolve(graph, heads, ids, mids)


def completeness_counts(rows: list[dict], modes: list[dict], cells: dict, *, preview: int = 25) -> dict:
    """Reviewed answers x modes: how many cells are decided, which answers still miss some."""
    missing_rows: list[dict] = []
    done = 0
    for r in rows:
        miss = sum(1 for m in modes if cells[(r["turn_id"], m["id"])].state == "unchecked")
        done += len(modes) - miss
        if miss:
            missing_rows.append({"turn_id": r["turn_id"], "question": r.get("question"), "missing": miss})
    required = len(rows) * len(modes)
    return {"reviewed_turns": len(rows), "modes": len(modes), "required": required, "done": done,
            "missing": required - done, "missing_preview": missing_rows[:preview],
            "more": max(0, len(missing_rows) - preview)}


async def completeness(principal, org: str | None, *,
                       statuses: tuple[str, ...] = taxonomy.COMPLETENESS_STATUSES) -> dict:
    """Progress 'Completeness': decided cells of reviewed answers x confirmed and frozen modes, in one company or (the
    platform owner, acting for none) every company."""
    org = taxonomy.acting_company(principal, org)
    async with db.lab_engine().connect() as conn:
        _graph, modes, rows, _heads, cells = await _grid(conn, org, statuses=tuple(statuses))
    return completeness_counts(rows, modes, cells)


def _wilson(present: int, n: int) -> tuple[float | None, float | None, float | None]:
    from . import stats

    if not n:
        return None, None, None
    low, high = stats.wilson_interval(present, n)
    return round(present / n, 4), round(float(low), 4), round(float(high), 4)


async def _verdicts(conn, turn_ids: list[str]) -> dict[str, list[dict]]:
    if not turn_ids:
        return {}
    rows = (await conn.execute(text(
        "SELECT turn_id, verdict, CAST(reviewer_id AS text) AS reviewer_id FROM tracelab.annotations "
        "WHERE kind = 'verdict' AND superseded_by IS NULL AND NOT tombstone AND turn_id = ANY(CAST(:t AS text[]))"),
        {"t": list(turn_ids)})).mappings().all()
    out: dict[str, list[dict]] = {}
    for r in rows:
        out.setdefault(r["turn_id"], []).append(dict(r))
    return out


async def _withdrawn_notes(conn, annotation_ids: list[str]) -> set[str]:
    """Notes whose lineage head is now a tombstone: their labels stay, flagged (spec §6.5)."""
    ids = sorted({a for a in annotation_ids if a})
    if not ids:
        return set()
    rows = (await conn.execute(text(
        "SELECT CAST(a.id AS text) FROM tracelab.annotations a JOIN tracelab.annotations h "
        "ON h.lineage_id = a.lineage_id AND h.superseded_by IS NULL WHERE a.id = ANY(CAST(:a AS uuid[])) "
        "AND h.tombstone"), {"a": ids})).all()
    return {r[0] for r in rows}


async def matrix(principal, org: str | None, *, batch_id: str | None = None, since: datetime | None = None,
                 until: datetime | None = None, state: str = "all",
                 statuses: tuple[str, ...] = taxonomy.COMPLETENESS_STATUSES, mode_ids: list[str] | None = None,
                 limit: int = 50, offset: int = 0, filter_org: str | None = None) -> dict:
    """GET /labels/matrix (spec §6.3). Rows: reviewed answers the caller may see under the blind rule (the platform
    owner sees every row). The shares, the fair rates and the totals are computed over those rows only, so nothing
    about an answer that stays hidden leaks through a count."""
    from . import annotations, proposals

    org = taxonomy.acting_company(principal, org)
    owner = is_owner(principal, org)
    scope = org
    if owner and filter_org:                                  # only the platform owner may pick one company to look at
        if not taxonomy.is_uuid(filter_org):
            raise taxonomy.invalid("bad_company")
        scope = str(uuid.UUID(str(filter_org)))
    limit, offset = max(1, min(int(limit), 200)), max(0, int(offset))
    async with db.lab_engine().connect() as conn:
        graph, modes, rows, heads, cells = await _grid(conn, scope, statuses=tuple(statuses), mode_ids=mode_ids,
                                                       batch_id=batch_id, since=since, until=until)
        ids = [r["turn_id"] for r in rows]
        visible = set(ids) if owner else await annotations.visible_others(conn, principal, org, ids)
        shown = [r for r in rows if r["turn_id"] in visible]
        mids = [m["id"] for m in modes]
        miss = {r["turn_id"]: sum(1 for mid in mids if cells[(r["turn_id"], mid)].state == "unchecked") for r in shown}
        filtered = [r for r in shown if state != "missing" or miss[r["turn_id"]] > 0]
        page = filtered[offset:offset + limit]
        page_ids = [r["turn_id"] for r in page]
        verdicts = await _verdicts(conn, page_ids)
        pending = await proposals.pending_cells(conn, page_ids, mids)
        by_label = {h.label_id: h for h in heads.values()}
        withdrawn = await _withdrawn_notes(conn, [h.annotation_id for (t, _m), h in heads.items()
                                                  if t in set(page_ids) and h.annotation_id])
    me = str(principal.user_id)
    out_rows = []
    for r in page:
        t = r["turn_id"]
        vs = verdicts.get(t, [])
        row_cells = {}
        for m in modes:
            c = cells[(t, m["id"])]
            own = heads.get((t, m["id"]))
            dec = by_label.get(c.label_id) if c.label_id else None
            row_cells[m["id"]] = cell_payload(c, graph, base=own.label_id if own else None,
                                              proposal=pending.get((t, m["id"])),
                                              note_withdrawn=bool(dec and dec.annotation_id in withdrawn))
        out_rows.append({
            "turn_id": t, "conversation_key": r["conversation_key"], "organization_id": r["organization_id"],
            "question": r["question"], "started_at": _iso(r["started_at"]),
            "batch": {"id": r["batch_id"], "plan_step": r["plan_step"]} if r["batch_id"] else None,
            "verdicts": {"right": sum(1 for v in vs if v["verdict"] == "right"),
                         "wrong": sum(1 for v in vs if v["verdict"] == "wrong"),
                         "mine": next((v["verdict"] for v in vs if v["reviewer_id"] == me), None)},
            "cells": row_cells})
    totals = completeness_counts(shown, modes, cells)
    fractions: dict[str, dict] = {}
    fair: dict[str, dict] = {}
    for m in modes:
        present = decided = u_present = u_n = 0
        for r in shown:
            c = cells[(r["turn_id"], m["id"])]
            if c.state == "unchecked":
                continue
            decided += 1
            present += c.state == "present"
            if r["strategy"] == "uniform" and r["withdrawn_at"] is None:
                u_n += 1
                u_present += c.state == "present"
        fractions[m["id"]] = {"present": present, "decided": decided,
                              "fraction": round(present / decided, 4) if decided else None, "kind": "sample_fraction"}
        rate, low, high = _wilson(u_present, u_n)
        fair[m["id"]] = {"present": u_present, "n": u_n, "rate": rate, "ci_low": low, "ci_high": high,
                         "kind": "uniform_batches_only"}
    batches = sorted({(r["batch_id"], r["plan_step"]) for r in shown if r["batch_id"]}, key=lambda x: x[0])
    return {
        "ok": True, "available": True, "served_at": datetime.now(tz=timezone.utc).isoformat(),
        "modes": [{"id": m["id"], "name": m["name"], "status": m["status"], "definition": m["definition"],
                   "boundary": m["boundary"], "agent": m["agent"],
                   "agent_label": taxonomy.AGENT_LABELS.get(m["agent"], ""), "level": m["level"],
                   "judge_eligible": taxonomy.judge_eligible(m)} for m in modes],
        "rows": out_rows, "hidden_rows": len(rows) - len(shown), "total_rows": len(filtered),
        "totals": {k: totals[k] for k in ("reviewed_turns", "required", "done", "missing")},
        "missing_preview": [{"turn_id": x["turn_id"], "missing": x["missing"]} for x in totals["missing_preview"]],
        "sample_fractions": fractions, "fair_sample": fair, "fraction_copy": SAMPLE_FRACTION_COPY,
        "batches": [{"id": b, "plan_step": s} for b, s in batches]}


async def per_mode(principal, org: str | None) -> list[dict]:
    """Progress 'Per mode': first-failure count (a reviewer's note filed under it) beside any-instance count."""
    from . import proposals

    org = taxonomy.acting_company(principal, org)
    async with db.lab_engine().connect() as conn:
        graph, modes, rows, heads, cells = await _grid(conn, org, statuses=taxonomy.LIVE_STATUSES)
        pending = await proposals.pending_counts(conn, org)
    out = []
    for m in modes:
        parts = graph.parts(m["id"])
        first = anyi = decided = 0
        for r in rows:
            t = r["turn_id"]
            c = cells[(t, m["id"])]
            anyi += c.state == "present"
            decided += c.state != "unchecked"
            if any((h := heads.get((t, p["id"]))) is not None and not h.tombstone and h.present
                   and h.source == "human_open_code" and h.created_at >= p["rescoped_at"] for p in parts):
                first += 1
        out.append({"mode_id": m["id"], "name": m["name"], "status": m["status"], "level": m["level"],
                    "first_failure": first, "any_instance": anyi, "decided": decided,
                    "sample_fraction": {"present": anyi, "decided": decided,
                                        "fraction": round(anyi / decided, 4) if decided else None,
                                        "kind": "sample_fraction"},
                    "pending_suggestions": pending.get(m["id"], 0)})
    return out


async def decided_cells(conn, graph: taxonomy.ModeGraph, mode: dict, *, org: str | None = None) -> list[dict]:
    """Every decided cell of a live mode, newest-decided last, with its provenance. Proposals use this for
    positives, close negatives and canaries; human_origin_cells filters it for the judges. `conn` is a LAB
    connection; each answer's company and session come from turn_rows (the chat, else the evidence snapshot)."""
    heads = await load_heads(conn, None, graph.relevant_ids([mode["id"]]), org=org)
    turns = sorted({t for (t, _m) in heads})
    info = await turn_rows(turns)
    by_label = {h.label_id: h for h in heads.values()}
    out = []
    for t in turns:
        c = resolve_cell(graph, heads, t, mode)
        if c.state == "unchecked":
            continue
        h = by_label.get(c.label_id)
        i = info.get(t) or {}
        out.append({"turn_id": t, "organization_id": i.get("organization_id"), "session_id": i.get("session_id"),
                    "present": c.state == "present", "label": 1 if c.state == "present" else 0,
                    "label_id": c.label_id, "source": c.source, "approval": c.approval,
                    "human_origin": c.human_origin, "inherited": c.via_mode_id is not None,
                    "via_mode_id": c.via_mode_id, "human_written": c.source in HUMAN_ORIGIN_SOURCES,
                    "decided_at": _iso(c.decided_at), "decided_by_email": c.decided_by_email,
                    "quote": h.quote if h else None, "note": h.note if h else None})
    return sorted(out, key=lambda c: (c["decided_at"] or "", c["turn_id"]))


async def human_origin_cells(mode_id: str, *, org: str | None = None) -> list[dict]:
    """The only label source a judge's dev or test split may use (R10): resolved cells whose deciding label is
    human-origin. `inherited` marks cells decided through a merged part or a split parent; `human_written` marks
    human_structured_label / human_open_code so a judge can be stricter still. A data-fault mode gives [] (R16)."""
    if not taxonomy.is_uuid(mode_id):
        return []
    async with db.lab_engine().connect() as conn:
        graph = await taxonomy.load_graph(conn)
        mode = graph.modes.get(str(uuid.UUID(str(mode_id))))
        if mode is None or mode["status"] not in taxonomy.LIVE_STATUSES or not taxonomy.judge_eligible(mode):
            return []
        cells = await decided_cells(conn, graph, mode, org=org)
    return [c for c in cells if c["human_origin"]]


async def uniform_human_stats(conn, org: str | None, mode_ids: list[str]) -> dict[str, dict]:
    """Human-origin decided cells on uniform, non-withdrawn batch items, per mode, with a Wilson interval
    (bulk audit rule 4). `conn` is a LAB connection."""
    graph = await taxonomy.load_graph(conn)
    rows = [r for r in await reviewed_rows(conn, org) if r["strategy"] == "uniform" and r["withdrawn_at"] is None]
    ids = [r["turn_id"] for r in rows]
    live = [m for m in dict.fromkeys(mode_ids)
            if m in graph.modes and graph.modes[m]["status"] in taxonomy.LIVE_STATUSES]
    heads = await load_heads(conn, ids, graph.relevant_ids(live)) if ids and live else {}
    out: dict[str, dict] = {}
    for mid in live:
        n = present = 0
        for t in ids:
            c = resolve_cell(graph, heads, t, graph.modes[mid])
            if c.state != "unchecked" and c.human_origin:
                n += 1
                present += c.state == "present"
        _rate, low, high = _wilson(present, n)
        out[mid] = {"n": n, "present": present, "low": low, "high": high}
    return out


async def undecided_after(conn, org: str | None, turn_ids: list[str], writing: set[tuple[str, str]]) -> int:
    """Unchecked completeness cells left in these answers once `writing` is decided (bulk audit rule 8)."""
    if not turn_ids:
        return 0
    graph = await taxonomy.load_graph(conn)
    modes = [m for m in graph.live() if m["status"] in taxonomy.COMPLETENESS_STATUSES]
    mids = [m["id"] for m in modes]
    heads = await load_heads(conn, list(turn_ids), graph.relevant_ids(mids), org=org) if mids else {}
    cells = resolve(graph, heads, list(turn_ids), mids)
    return sum(1 for (t, mid), c in cells.items() if c.state == "unchecked" and (t, mid) not in writing)


async def present_turn_ids(conn, mode_id: str, org: str | None) -> list[str]:
    """Answers that resolve present for a live mode (the seeds a depth scan enriches from, spec §3.3)."""
    graph = await taxonomy.load_graph(conn)
    mode = graph.modes.get(str(mode_id))
    if mode is None or mode["status"] not in taxonomy.LIVE_STATUSES:
        return []
    heads = await load_heads(conn, None, graph.relevant_ids([mode["id"]]), org=org)
    return sorted({t for (t, _m) in heads if resolve_cell(graph, heads, t, mode).state == "present"})


async def turn_present_modes(conn, turn_id: str) -> list[str]:
    """Live modes an answer resolves present for (a note cannot be disposed as not_a_failure while any)."""
    graph = await taxonomy.load_graph(conn)
    live = graph.live()
    heads = await load_heads(conn, [turn_id], graph.relevant_ids([m["id"] for m in live])) if live else {}
    return [m["id"] for m in live if resolve_cell(graph, heads, turn_id, m).state == "present"]
