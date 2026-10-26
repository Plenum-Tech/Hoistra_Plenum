"""Failure modes: the platform-wide taxonomy reviewers' notes are grouped into (Trace Lab Phase 5, spec §5).

A mode is born from human wrong notes, confirmed against a contract, may be frozen, and leaves the live set by
retire, merge or split. A company admin may only PROPOSE one: it waits as `proposed` until the platform team accepts
it (it becomes a candidate) or rejects it with a reason (it stays as `rejected`, never deleted). Neither a proposed
nor a rejected mode is live. Labels are never rewritten when the taxonomy changes: labels.resolve_cell reads them
through merges, splits and rescopes. Writes belong to the platform owner (a superadmin not acting for a
company); the routes enforce that (D7). A data-level mode (R16) is a fault in the records: it is routed to the
data owner and is never used for prompt fixes or judges.

The first half of this module is pure except load_graph, which reads Trace Lab's own database (schema tracelab) on
a connection the caller opens. The second half writes and reads modes there (db.lab_engine()). The chat's tables are
touched only through labels (the read-only main engine), and no SQL statement ever spans the two databases (G0).
Every function that changes a mode refuses a caller who is not a superadmin; the routes also require that the
superadmin is acting for no company (auth.require_platform). Refusals are review_errors, each with a plain sentence
from SENTENCES (no table, service or id in it).

The last section reads how the naming of failure modes is going: the curve of new modes per block of reviews, the
saturation hint, the final fair batch's note and the caller's re-review prompt (discovery). It counts reviews and modes
and shows no note text; a company's reading covers its own reviews only (acting_company).
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text

from . import db
from .review_errors import Conflict, Forbidden, Invalid, NotFound, Stale

STATUSES = ("proposed", "candidate", "confirmed", "frozen", "retired", "rejected")
LIVE_STATUSES = ("candidate", "confirmed", "frozen")
COMPLETENESS_STATUSES = ("confirmed", "frozen")
LEVELS = ("technical", "experience", "domain", "data")
JUDGE_LEVELS = ("technical", "experience", "domain")
LEVEL_LABELS = {"technical": "Technical: the records decide", "experience": "Experience: the product owner decides",
                "domain": "Domain: a facilities specialist decides",
                "data": "Data fault: the data owner fixes the records"}
AGENTS = ("orchestrator", "wo_engine", "compliance", "contract_performance", "energy_intelligence",
          "udr", "doc_rag", "migration")
AGENT_LABELS = {"orchestrator": "Orchestrator (routing, planning, write-up)", "wo_engine": "Work orders",
                "compliance": "Compliance", "contract_performance": "Vendor contracts",
                "energy_intelligence": "Energy", "udr": "Records lookup", "doc_rag": "Documents",
                "migration": "Data migration"}
EVALUATOR_TYPES = ("code", "judge")
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
MIN_POSITIVES = 3
MIN_CLOSE_NEGATIVES = 3
FIX_TEST = "Does fixing one fix the other?"
FIX_TEST_TEXT = ("Merge when one product change would fix both. Split when the examples need different fixes, "
                 "even if they use similar words.")
GROUPING_CARD_NOTE = "Proposed by AI from reviewers' notes. Read every supporting note before accepting."
CONTRACT_ITEMS = ("boundary", "evaluator_type", "requirement", "positives", "close_negatives")
_ID_COLS = ("id", "nearest_mode_id", "merged_into", "split_from", "created_by", "decided_by",
            "proposer_organization_id")


def is_uuid(v: Any) -> bool:
    try:
        uuid.UUID(str(v))
    except (ValueError, TypeError, AttributeError):
        return False
    return True


def json_value(v: Any) -> Any:
    """jsonb as a Python value whether the driver decoded it or not."""
    return json.loads(v) if isinstance(v, str) else v


def row_dict(r: Any) -> dict[str, Any]:
    m = dict(r)
    for k in _ID_COLS:
        if m.get(k) is not None:
            m[k] = str(m[k])
    m["created_from"] = [str(x) for x in (m.get("created_from") or [])]
    for k in ("requirement_ids", "positive_turn_ids", "close_negative_turn_ids"):
        m[k] = [str(x) for x in (m.get(k) or [])]
    return m


def _iso(v: Any) -> Any:
    return v.isoformat() if isinstance(v, datetime) else v


def meaning_at(mode: dict) -> datetime:
    """When the mode's current meaning began: the later of its last rescope and its last absorb."""
    absorbed = mode.get("absorbed_at")
    return max(mode["rescoped_at"], absorbed) if absorbed else mode["rescoped_at"]


def judge_eligible(mode: dict) -> bool:
    """R16: a data fault is fixed in the records, never by a prompt or measured by a judge."""
    return mode.get("level") in JUDGE_LEVELS


def payload(mode: dict) -> dict[str, Any]:
    out = {k: _iso(v) for k, v in mode.items()}
    out["agent_label"] = AGENT_LABELS.get(mode.get("agent") or "", "")
    out["level_label"] = LEVEL_LABELS.get(mode.get("level") or "", "")
    out["judge_eligible"] = judge_eligible(mode)
    return out


@dataclass(frozen=True)
class ModeGraph:
    modes: dict[str, dict]

    def live(self) -> list[dict]:
        """The modes a grid, a judge or a count may use: not retired, not merely proposed, not rejected."""
        return sorted((m for m in self.modes.values() if m["status"] in LIVE_STATUSES),
                      key=lambda m: (m["created_at"], m["id"]))

    def parts(self, mode_id: str) -> list[dict]:
        """The mode plus every mode merged into it, transitively."""
        out: list[dict] = []
        seen: set[str] = set()
        stack = [mode_id]
        while stack:
            mid = stack.pop()
            if mid in seen or mid not in self.modes:
                continue
            seen.add(mid)
            out.append(self.modes[mid])
            stack.extend(m["id"] for m in self.modes.values() if m.get("merged_into") == mid)
        return out

    def split_ancestors(self, mode_id: str) -> list[dict]:
        """Split parents, nearest first."""
        out: list[dict] = []
        seen = {mode_id}
        cur = self.modes.get(mode_id)
        while cur is not None and cur.get("split_from") and cur["split_from"] not in seen:
            seen.add(cur["split_from"])
            cur = self.modes.get(cur["split_from"])
            if cur is not None:
                out.append(cur)
        return out

    def root_of(self, mode_id: str) -> str | None:
        """The live mode a retired id resolves to through merges, or None."""
        seen: set[str] = set()
        cur = self.modes.get(mode_id)
        while cur is not None and cur["id"] not in seen:
            if cur["status"] in LIVE_STATUSES:
                return cur["id"]
            if cur["status"] != "retired":      # proposed or rejected: never merged into anything
                return None
            seen.add(cur["id"])
            cur = self.modes.get(cur.get("merged_into") or "")
        return None

    def relevant_ids(self, mode_ids: list[str]) -> list[str]:
        """Every mode whose label heads can decide a cell of these modes: their parts and nearest split ancestor."""
        out: set[str] = set()
        for mid in mode_ids:
            out.update(p["id"] for p in self.parts(mid))
            anc = self.split_ancestors(mid)
            if anc:
                out.add(anc[0]["id"])
        return sorted(out)


async def load_graph(conn) -> ModeGraph:
    rows = (await conn.execute(text("SELECT * FROM tracelab.modes"))).mappings().all()
    return ModeGraph({str(r["id"]): row_dict(r) for r in rows})


def contract_missing(mode: dict, cells: dict[str, Any], active: dict[str, dict]) -> list[str]:
    """[] when the mode may be confirmed. `cells` maps turn_id -> labels.Cell for this mode over its listed
    positives and close negatives; `active` is requirements.active_codes() covering its requirement_ids."""
    missing: list[str] = []
    if not (mode.get("boundary") or "").strip():
        missing.append("boundary")
    if mode.get("evaluator_type") not in EVALUATOR_TYPES:
        missing.append("evaluator_type")
    if not any(code in active for code in mode.get("requirement_ids") or []):
        missing.append("requirement")

    def _ok(turn_id: str, state: str) -> bool:
        c = cells.get(turn_id)
        return c is not None and c.state == state and c.approval == "single"

    if len({t for t in mode.get("positive_turn_ids") or [] if _ok(t, "present")}) < MIN_POSITIVES:
        missing.append("positives")
    negatives = {t for t in mode.get("close_negative_turn_ids") or [] if _ok(t, "absent")}
    if len(negatives) < MIN_CLOSE_NEGATIVES and not (mode.get("close_negatives_waiver") or "").strip():
        missing.append("close_negatives")
    return missing


def requirement_flags(mode: dict, confirmed_revisions: dict[str, int] | None, active: dict[str, dict]) -> list[dict]:
    """Requirements the mode cites that were revised since it was confirmed, or are no longer active."""
    flags: list[dict] = []
    for code in mode.get("requirement_ids") or []:
        a = active.get(code)
        if a is None:
            flags.append({"code": code, "kind": "not_active", "text": f"{code} is no longer an active requirement."})
        elif confirmed_revisions and code in confirmed_revisions and int(a["revision"]) > int(confirmed_revisions[code]):
            flags.append({"code": code, "kind": "revised", "confirmed_revision": int(confirmed_revisions[code]),
                          "active_revision": int(a["revision"]),
                          "text": "Requirement revised since this mode was confirmed: recheck its definition and labels."})
    return flags


# ── refusals ──────────────────────────────────────────────────────────────────────────

#: The sentence on screen for each refusal code the label and mode code can raise. No table, service or id in any.
SENTENCES: dict[str, str] = {
    "platform_only": "Only the platform team can do this.",
    "reviewers_only": "Only company admins and the platform team can do this.",
    "turn_not_found": "That answer was not found.",
    "mode_not_found": "That failure mode was not found.",
    "mode_not_live": "This failure mode was merged or retired, or it is still only a proposal.",
    "mode_frozen": "This failure mode is frozen: its meaning cannot change. Create a new mode instead.",
    "mode_proposed": "This failure mode is a company's proposal. The platform team must accept or reject it first.",
    "mode_rejected": "This proposal was rejected, so it cannot be changed.",
    "not_a_proposal": "That failure mode is not a pending proposal.",
    "name_taken": "A failure mode already has this name.",
    "bad_name": "A failure mode's name is lower case letters, digits and underscores, 3 to 64 characters, "
                "starting with a letter.",
    "bad_definition": "The definition must be between 10 and 1000 characters.",
    "unknown_level": "Choose one of the listed levels.",
    "unknown_agent": "Choose one of the listed agents.",
    "unknown_evaluator_type": "Choose code or judge.",
    "inactive_requirement": "A mode may cite only requirements that are active.",
    "unknown_nearest_mode": "The nearest mode must be another live failure mode.",
    "not_a_wrong_note": "A failure mode can start only from Wrong verdicts that carry a note.",
    "status_transition": "A failure mode cannot move to that status from where it is.",
    "contract_incomplete": "Complete the mode's contract before confirming it.",
    "reason_required": "Give a reason of up to 500 characters.",
    "merged": "This mode was merged into another; it cannot be restored.",
    "split_parent": "This mode was split; work with its children instead.",
    "merge_target": "Name exactly one target: an existing mode or a new one.",
    "merge_needs_a_source": "Choose at least one mode to merge.",
    "merge_into_itself": "A mode cannot be merged into itself.",
    "merge_needs_two_sources": "A new merged mode needs at least two sources.",
    "split_needs_two_to_five": "A split makes two to five new modes.",
    "duplicate_child_names": "Each new mode needs its own name.",
    "created_from_not_in_parent": "Each new mode must start from notes the split mode already holds.",
    "not_a_present_turn": "Only answers where the split mode is present can be assigned to a new mode.",
    "bad_child_index": "That assignment names a new mode that does not exist.",
    "unknown_status": "Choose proposed, rejected, accepted or all.",
    "unknown_source": "That label source is not one of the allowed kinds.",
    "anchor_required": "Select the text you are pointing at.",
    "span_not_in_turn": "That step is not part of this answer.",
    "not_in_spot_check": "That answer is not part of a spot check.",
    "conflicts_with_label": "A reviewer marked this failure absent on this answer. File it only if the note shows "
                            "the failure.",
    "bad_company": "That is not a company id.",
    "unknown_outcome": "Choose no mode fits or not a failure.",
    "not_a_failure_while_present": "This answer is marked as showing a failure mode, so it cannot be set aside as "
                                   "not a failure.",
}


def invalid(reason: str, **extra: Any) -> Invalid:
    return Invalid(reason, SENTENCES[reason], **extra)


def not_found(reason: str, **extra: Any) -> NotFound:
    return NotFound(reason, SENTENCES[reason], **extra)


def conflict(reason: str, **extra: Any) -> Conflict:
    return Conflict(reason, SENTENCES[reason], **extra)


def forbidden(reason: str, **extra: Any) -> Forbidden:
    return Forbidden(reason, SENTENCES[reason], **extra)


# ── writes (platform team only, except a company's proposal) ─────────────────────────

_LOCK_SQL = "SELECT pg_advisory_xact_lock(hashtext('tracelab.modes'))"
_EDITABLE = ("definition", "boundary", "nearest_mode_id", "level", "agent", "evaluator_type", "requirement_ids",
             "positive_turn_ids", "close_negative_turn_ids", "close_negatives_waiver")
_MEANING = ("definition", "boundary", "level", "agent", "evaluator_type")
_NOT_NULL = ("definition", "level", "agent")
_LISTS = ("requirement_ids", "positive_turn_ids", "close_negative_turn_ids")
_TRANSITIONS = {("candidate", "confirmed"), ("confirmed", "candidate"), ("confirmed", "frozen")}
SAFE_EVENT_KEYS = ("status", "name", "reason", "rescope", "role")
PROPOSAL_FILTERS = ("proposed", "rejected", "accepted", "all")
PROPOSAL_EDITABLE = ("name", "definition", "boundary", "level", "agent", "nearest_mode_id")
PROPOSER_DROPPED = ("evaluator_type", "requirement_ids")      # platform decisions: a proposal does not carry them
# Who proposed a mode and who decided it: read only by the platform owner and the proposing company (G0 2.4).
PROPOSER_IDENTITY = ("proposer_organization_id", "created_by", "created_by_email", "decided_by", "decided_by_email")
PROPOSAL_EVENTS = ("propose", "accept", "reject")             # the events whose actor is that proposer or decider
SNAPSHOT_REASON = "labelled"
REASON_MAX = 500


def _require_platform(principal) -> None:
    """Defence in depth: whatever the route did, only a superadmin changes the taxonomy."""
    if getattr(principal, "role", None) != "superadmin":
        raise forbidden("platform_only")


def _company_of(principal, org: str | None) -> str | None:
    """The company the caller acts for, or None for the platform owner (a superadmin acting for none). An admin acts
    only for their own company; anyone else, or an admin with no company, is refused."""
    if getattr(principal, "role", None) == "superadmin":
        return str(org) if org else None
    if not getattr(principal, "is_admin", False):
        raise forbidden("reviewers_only")
    own = str(principal.organization_id) if getattr(principal, "organization_id", None) else None
    company = str(org) if org else own
    if company is None or (own is not None and company != own):
        raise forbidden("reviewers_only")
    return company


async def _now(conn) -> datetime:
    return (await conn.execute(text("SELECT now()"))).scalar()


async def _lock(conn, mode_id: str) -> dict:
    if not is_uuid(mode_id):
        raise not_found("mode_not_found")
    r = (await conn.execute(text("SELECT * FROM tracelab.modes WHERE id = CAST(:id AS uuid) FOR UPDATE"),
                            {"id": str(uuid.UUID(str(mode_id)))})).mappings().first()
    if r is None:
        raise not_found("mode_not_found")
    return row_dict(r)


def _check_version(m: dict, base_version: int) -> None:
    if int(base_version) != int(m["version"]):
        raise Stale(payload(m))


def _require_live(m: dict, graph: "ModeGraph | None") -> None:
    if m["status"] == "retired":
        raise conflict("mode_not_live", live_mode_id=graph.root_of(m["id"]) if graph is not None else None)
    if m["status"] == "proposed":
        raise conflict("mode_proposed")
    if m["status"] == "rejected":
        raise conflict("mode_rejected")


def _require_unfrozen(m: dict) -> None:
    if m["status"] == "frozen":
        raise conflict("mode_frozen")


def _actor(principal) -> dict[str, Any]:
    return {"actor_id": principal.user_id, "actor_email": getattr(principal, "email", None)}


async def _event(conn, mode_id: str, action: str, detail: dict, *, actor_id: Any, actor_email: str | None) -> None:
    await conn.execute(text(
        "INSERT INTO tracelab.mode_events (mode_id, action, detail, actor_id, actor_email) "
        "VALUES (CAST(:m AS uuid), :a, CAST(:d AS jsonb), CAST(:u AS uuid), :e)"),
        {"m": mode_id, "a": action, "d": json.dumps(detail, default=str), "u": str(actor_id), "e": actor_email or None})


async def _name_free(conn, name: str, exclude: tuple[str, ...] = ()) -> None:
    """A name is held by every mode that is not retired or rejected, a company's pending proposal included."""
    if not NAME_RE.match(name or ""):
        raise invalid("bad_name", name=name)
    r = (await conn.execute(text("SELECT CAST(id AS text) FROM tracelab.modes "
                                 "WHERE name = :n AND status NOT IN ('retired', 'rejected')"), {"n": name})).first()
    if r is not None and r[0] not in set(exclude):
        raise conflict("name_taken")


def _check_draft(draft: dict) -> None:
    if not NAME_RE.match(str(draft.get("name") or "")):
        raise invalid("bad_name", name=draft.get("name"))
    if not 10 <= len(str(draft.get("definition") or "")) <= 1000:
        raise invalid("bad_definition")
    if draft.get("level") not in LEVELS:
        raise invalid("unknown_level", level=draft.get("level"))
    if draft.get("agent") not in AGENTS:
        raise invalid("unknown_agent", agent=draft.get("agent"))


async def _validate(conn, fields: dict) -> None:
    from . import requirements

    if "agent" in fields and fields["agent"] not in AGENTS:
        raise invalid("unknown_agent", agent=fields["agent"])
    if "level" in fields and fields["level"] not in LEVELS:
        raise invalid("unknown_level", level=fields["level"])
    if fields.get("evaluator_type") is not None and fields["evaluator_type"] not in EVALUATOR_TYPES:
        raise invalid("unknown_evaluator_type", evaluator_type=fields["evaluator_type"])
    codes = list(fields.get("requirement_ids") or [])
    if codes:
        active = await requirements.active_codes(conn, codes)
        missing = [c for c in codes if c not in active]
        if missing:
            raise invalid("inactive_requirement", codes=missing)
    nearest = fields.get("nearest_mode_id")
    if nearest:
        r = (await conn.execute(text("SELECT status FROM tracelab.modes WHERE id = CAST(:i AS uuid)"),
                                {"i": nearest})).first() if is_uuid(nearest) else None
        if r is None or r[0] not in LIVE_STATUSES:
            raise invalid("unknown_nearest_mode", nearest_mode_id=nearest)


_NOTE_SQL = ("SELECT id, lineage_id, turn_id, organization_id, note, quote, span_id, span_name, span_kind, span_agent, "
             "field FROM tracelab.annotations WHERE id = ANY(CAST(:ids AS uuid[])) AND kind = 'verdict' "
             "AND NOT tombstone AND verdict = 'wrong'")


async def _wrong_notes(conn, ids: list[str], *, org: str | None = None) -> list[dict]:
    """Each id must be a non-tombstone `wrong` verdict version (spec §5.1 created_from), in `org` when given (a
    company's proposal cites only its own notes)."""
    clean: list[str] = []
    for i in ids or []:
        if not is_uuid(i):
            raise invalid("not_a_wrong_note", ids=[i])
        clean.append(str(uuid.UUID(str(i))))
    clean = list(dict.fromkeys(clean))
    if not clean:
        raise invalid("not_a_wrong_note", ids=[])
    sql, params = _NOTE_SQL, {"ids": clean}
    if org:
        sql, params["org"] = sql + " AND organization_id = CAST(:org AS uuid)", org
    found = {str(r["id"]): dict(r) for r in (await conn.execute(text(sql), params)).mappings().all()}
    bad = [i for i in clean if i not in found]
    if bad:
        raise invalid("not_a_wrong_note", ids=bad)
    return [found[i] for i in clean]


async def _insert_mode(conn, principal, draft: dict, *, created_from: list[str], split_from: str | None = None,
                       absorbed: bool = False, status: str = "candidate", proposer_org: str | None = None) -> dict:
    r = (await conn.execute(text(
        "INSERT INTO tracelab.modes (id, name, definition, boundary, nearest_mode_id, status, evaluator_type, "
        "level, agent, requirement_ids, created_from, split_from, absorbed_at, proposer_organization_id, "
        "created_by, created_by_email) VALUES "
        "(CAST(:id AS uuid), :name, :definition, :boundary, CAST(:nearest AS uuid), :status, :ev, :level, :agent, "
        "CAST(:reqs AS text[]), CAST(:cf AS uuid[]), CAST(:sf AS uuid), "
        "CASE WHEN CAST(:absorbed AS boolean) THEN now() END, CAST(:po AS uuid), CAST(:by AS uuid), :email) "
        "RETURNING *"),
        {"id": str(uuid.uuid4()), "name": draft["name"], "definition": draft["definition"],
         "boundary": draft.get("boundary") or "", "nearest": draft.get("nearest_mode_id") or None, "status": status,
         "ev": draft.get("evaluator_type"), "level": draft["level"], "agent": draft["agent"],
         "reqs": list(draft.get("requirement_ids") or []), "cf": list(created_from), "sf": split_from,
         "absorbed": absorbed, "po": proposer_org, "by": str(principal.user_id),
         "email": getattr(principal, "email", None) or None})).mappings().first()
    return row_dict(r)


async def _snapshot_notes(notes: list[dict]) -> None:
    """Keep the evidence of every note's answer in the lab database before a label cites it (G0 2.6). Raises when it
    cannot be kept, and then nothing is filed."""
    from . import evidence

    by_org: dict[str, list[str]] = {}
    for n in notes:
        by_org.setdefault(str(n["organization_id"]), []).append(n["turn_id"])
    for org, turn_ids in by_org.items():
        await evidence.snapshot(turn_ids, organization_id=org, reason=SNAPSHOT_REASON)


async def _file_notes(conn, principal, mode_id: str, notes: list[dict]) -> int:
    """Each origin note's answer gets a present human_open_code label, in the creating transaction."""
    from . import labels

    await _snapshot_notes(notes)
    turns = await labels.turn_rows([n["turn_id"] for n in notes])
    for n in notes:
        t = {"turn_id": n["turn_id"], "organization_id": str(n["organization_id"]),
             "session_id": (turns.get(n["turn_id"]) or {}).get("session_id")}
        await labels.write(conn, principal, turn=t, mode_id=mode_id, present=True, source="human_open_code",
                           base=labels.ANY_HEAD, note=n["note"], quote=n["quote"], span=labels.span_of(n),
                           annotation_id=str(n["id"]))
    return len(notes)


async def contract_check(conn, mode: dict, graph: ModeGraph) -> list[str]:
    from . import labels, requirements

    active = await requirements.active_codes(conn, list(mode.get("requirement_ids") or []))
    turns = sorted(set(mode.get("positive_turn_ids") or []) | set(mode.get("close_negative_turn_ids") or []))
    heads = await labels.load_heads(conn, turns, graph.relevant_ids([mode["id"]])) if turns else {}
    cells = {t: labels.resolve_cell(graph, heads, t, mode) for t in turns}
    return contract_missing(mode, cells, active)


async def create_in(conn, principal, draft: dict, *, created_from: list[str],
                    org: str | None = None) -> tuple[dict, int]:
    """Create a mode in the caller's LAB transaction. The platform owner makes a candidate and its origin notes are
    filed as present labels; a company admin (or a superadmin acting for a company) makes a PROPOSAL: status
    `proposed`, the proposer's company, only that company's own wrong notes, no platform-only fields, no labels."""
    proposer = _company_of(principal, org)
    _check_draft(draft)
    fields = {k: v for k, v in draft.items() if not (proposer and k in PROPOSER_DROPPED)}
    await conn.execute(text(_LOCK_SQL))
    await _validate(conn, fields)
    notes = await _wrong_notes(conn, created_from, org=proposer)
    await _name_free(conn, draft["name"])
    mode = await _insert_mode(conn, principal, fields, created_from=[str(n["id"]) for n in notes],
                              status="proposed" if proposer else "candidate", proposer_org=proposer)
    if proposer:
        await _event(conn, mode["id"], "propose", {"name": mode["name"], "created_from": mode["created_from"],
                                                   "role": "company_admin"}, **_actor(principal))
        return mode, 0
    await _event(conn, mode["id"], "create", {"name": mode["name"], "created_from": mode["created_from"]},
                 **_actor(principal))
    return mode, await _file_notes(conn, principal, mode["id"], notes)


async def create(principal, draft: dict, *, created_from: list[str], org: str | None = None) -> dict:
    async with db.lab_engine().begin() as conn:
        mode, written = await create_in(conn, principal, dict(draft), created_from=created_from, org=org)
    return {"mode": payload(mode), "labels_written": written}


async def edit(principal, mode_id: str, changes: dict, *, base_version: int, rescope: bool = False) -> dict:
    from . import proposals, requirements

    _require_platform(principal)
    changes = dict(changes or {})
    target_status = changes.pop("status", None)
    fields: dict[str, Any] = {}
    for k, v in changes.items():
        if k not in _EDITABLE or (v is None and (k in _NOT_NULL or k in _LISTS)):
            continue
        if k == "boundary":
            v = v or ""
        elif k in _LISTS:
            v = [str(x) for x in dict.fromkeys(v)]
        elif k == "nearest_mode_id" and v == "":
            v = None
        fields[k] = v
    async with db.lab_engine().begin() as conn:
        graph = await load_graph(conn)
        m = await _lock(conn, mode_id)
        _require_live(m, graph)
        _check_version(m, base_version)
        old = m["status"]
        target = target_status or old
        meaning_changed = any(k in fields and fields[k] != m.get(k) for k in _MEANING)
        if old == "frozen" and (meaning_changed or rescope or target != "frozen"):
            _require_unfrozen(m)
        if target != old and (old, target) not in _TRANSITIONS:
            raise invalid("status_transition", **{"from": old, "to": target})  # `from` is a Python keyword
        if fields.get("nearest_mode_id") == m["id"]:
            raise invalid("unknown_nearest_mode", nearest_mode_id=m["id"])
        await _validate(conn, fields)
        new = dict(m, **fields)
        now = await _now(conn)
        closed = 0
        if rescope:
            new["rescoped_at"] = now
            if target == "confirmed" and old == "confirmed":
                target = "candidate"
            closed += await proposals.close_for_mode(conn, m["id"], "mode_changed")
        revisions = None
        if target == "confirmed" and old != "confirmed":
            missing = await contract_check(conn, new, ModeGraph({**graph.modes, m["id"]: new}))
            if missing:
                raise invalid("contract_incomplete", missing=missing)
            revisions = {c: a["revision"] for c, a in
                         (await requirements.active_codes(conn, new["requirement_ids"])).items()}
        confirmed_at = now if (target == "confirmed" and old != "confirmed") else (
            None if target == "candidate" else m.get("confirmed_at"))
        frozen_at = now if (target == "frozen" and old != "frozen") else m.get("frozen_at")
        r = (await conn.execute(text(
            "UPDATE tracelab.modes SET definition = :definition, boundary = :boundary, "
            "nearest_mode_id = CAST(:nearest AS uuid), level = :level, agent = :agent, evaluator_type = :ev, "
            "requirement_ids = CAST(:reqs AS text[]), positive_turn_ids = CAST(:pos AS text[]), "
            "close_negative_turn_ids = CAST(:neg AS text[]), close_negatives_waiver = :waiver, status = :status, "
            "rescoped_at = :rescoped, confirmed_at = :confirmed, frozen_at = :frozen, version = version + 1, "
            "updated_at = now() WHERE id = CAST(:id AS uuid) RETURNING *"),
            {"definition": new["definition"], "boundary": new.get("boundary") or "",
             "nearest": new.get("nearest_mode_id"), "level": new["level"], "agent": new["agent"],
             "ev": new.get("evaluator_type"), "reqs": new["requirement_ids"], "pos": new["positive_turn_ids"],
             "neg": new["close_negative_turn_ids"], "waiver": new.get("close_negatives_waiver"), "status": target,
             "rescoped": new["rescoped_at"], "confirmed": confirmed_at, "frozen": frozen_at,
             "id": m["id"]})).mappings().first()
        updated = row_dict(r)
        detail: dict[str, Any] = {"changes": {k: [m.get(k), v] for k, v in fields.items() if v != m.get(k)},
                                  "rescope": bool(rescope)}
        if target != old:
            detail["status"] = [old, target]
        if revisions is not None:
            detail["requirement_revisions"] = revisions
        await _event(conn, m["id"], "edit", detail, **_actor(principal))
    return {"mode": payload(updated), "closed_proposals": closed}


async def rename(principal, mode_id: str, name: str, *, base_version: int) -> dict:
    _require_platform(principal)
    async with db.lab_engine().begin() as conn:
        await conn.execute(text(_LOCK_SQL))
        graph = await load_graph(conn)
        m = await _lock(conn, mode_id)
        _require_live(m, graph)
        _check_version(m, base_version)
        if name == m["name"]:
            return {"mode": payload(m)}
        await _name_free(conn, name, exclude=(m["id"],))
        r = (await conn.execute(text("UPDATE tracelab.modes SET name = :n, version = version + 1, "
                                     "updated_at = now() WHERE id = CAST(:id AS uuid) RETURNING *"),
                                {"n": name, "id": m["id"]})).mappings().first()
        await _event(conn, m["id"], "rename", {"name": [m["name"], name]}, **_actor(principal))
    return {"mode": payload(row_dict(r))}


def _reason(reason: Any) -> str:
    reason = (reason or "").strip() if isinstance(reason, str) or reason is None else ""
    if not 1 <= len(reason) <= REASON_MAX:
        raise invalid("reason_required")
    return reason


async def retire(principal, mode_id: str, *, reason: str, base_version: int) -> dict:
    from . import proposals

    _require_platform(principal)
    reason = _reason(reason)
    async with db.lab_engine().begin() as conn:
        graph = await load_graph(conn)
        m = await _lock(conn, mode_id)
        _require_live(m, graph)
        _check_version(m, base_version)
        closed = await proposals.close_for_mode(conn, m["id"], "mode_changed")
        r = (await conn.execute(text("UPDATE tracelab.modes SET status = 'retired', retired_at = now(), "
                                     "version = version + 1, updated_at = now() WHERE id = CAST(:id AS uuid) "
                                     "RETURNING *"), {"id": m["id"]})).mappings().first()
        await _event(conn, m["id"], "retire", {"reason": reason, "status": [m["status"], "retired"]},
                     **_actor(principal))
    return {"mode": payload(row_dict(r)), "closed_proposals": closed}


async def restore(principal, mode_id: str, *, base_version: int) -> dict:
    """Only a mode retired by retire. Frozen stays frozen; anything else returns as a candidate."""
    _require_platform(principal)
    async with db.lab_engine().begin() as conn:
        await conn.execute(text(_LOCK_SQL))
        graph = await load_graph(conn)
        m = await _lock(conn, mode_id)
        _check_version(m, base_version)
        if m["status"] != "retired":
            return {"mode": payload(m)}
        if m.get("merged_into"):
            raise conflict("merged", live_mode_id=graph.root_of(m["id"]))
        child = (await conn.execute(text("SELECT 1 FROM tracelab.modes WHERE split_from = CAST(:id AS uuid) "
                                         "LIMIT 1"), {"id": m["id"]})).first()
        if child is not None:
            raise conflict("split_parent")
        await _name_free(conn, m["name"])
        status = "frozen" if m.get("frozen_at") else "candidate"
        r = (await conn.execute(text(
            "UPDATE tracelab.modes SET status = :s, retired_at = NULL, "
            "confirmed_at = CASE WHEN :s = 'frozen' THEN confirmed_at END, version = version + 1, updated_at = now() "
            "WHERE id = CAST(:id AS uuid) RETURNING *"), {"s": status, "id": m["id"]})).mappings().first()
        await _event(conn, m["id"], "restore", {"status": ["retired", status]}, **_actor(principal))
    return {"mode": payload(row_dict(r))}


async def mark_frozen(conn, mode_id: str, *, reason: str, actor_id: str) -> None:
    """Judges freeze a mode when they freeze a judge version against it. One-way; a no-op when already frozen."""
    m = await _lock(conn, mode_id)
    if m["status"] == "frozen":
        return
    if m["status"] not in LIVE_STATUSES:
        raise conflict("mode_not_live")
    await conn.execute(text("UPDATE tracelab.modes SET status = 'frozen', frozen_at = now(), "
                            "version = version + 1, updated_at = now() WHERE id = CAST(:id AS uuid)"), {"id": m["id"]})
    await _event(conn, m["id"], "edit", {"status": [m["status"], "frozen"], "reason": reason, "by": "judge_freeze"},
                 actor_id=actor_id, actor_email=None)


# ── a company's proposal: the platform team accepts or rejects ─────────────────────────

def _proposal_edits(edits: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in dict(edits or {}).items():
        if k not in PROPOSAL_EDITABLE or (v is None and k != "nearest_mode_id"):
            continue
        out[k] = ((v or "") if k == "boundary" else (None if k == "nearest_mode_id" and v == "" else v))
    return out


async def accept_proposal(principal, mode_id: str, *, base_version: int | None = None,
                          edits: dict | None = None) -> dict:
    """Platform only. A proposed mode becomes a candidate (the company's proposal is kept as its origin) and its
    origin notes are filed as present labels. The platform may tidy the name, definition, boundary, level, agent or
    nearest mode while accepting. NotFound, Conflict (not a pending proposal, name taken), Stale, Invalid."""
    _require_platform(principal)
    fixes = _proposal_edits(edits)
    async with db.lab_engine().begin() as conn:
        await conn.execute(text(_LOCK_SQL))
        m = await _lock(conn, mode_id)
        if m["status"] != "proposed":
            raise conflict("not_a_proposal")
        if base_version is not None:
            _check_version(m, base_version)
        new = dict(m, **fixes)
        _check_draft(new)
        await _validate(conn, {k: v for k, v in fixes.items() if k in ("level", "agent", "nearest_mode_id")})
        if new["name"] != m["name"]:
            await _name_free(conn, new["name"], exclude=(m["id"],))
        r = (await conn.execute(text(
            "UPDATE tracelab.modes SET name = :name, definition = :definition, boundary = :boundary, "
            "level = :level, agent = :agent, nearest_mode_id = CAST(:nearest AS uuid), status = 'candidate', "
            "decided_by = CAST(:by AS uuid), decided_by_email = :email, decided_at = now(), "
            "version = version + 1, updated_at = now() WHERE id = CAST(:id AS uuid) RETURNING *"),
            {"name": new["name"], "definition": new["definition"], "boundary": new.get("boundary") or "",
             "level": new["level"], "agent": new["agent"], "nearest": new.get("nearest_mode_id"),
             "by": str(principal.user_id), "email": getattr(principal, "email", None) or None,
             "id": m["id"]})).mappings().first()
        updated = row_dict(r)
        detail: dict[str, Any] = {"status": ["proposed", "candidate"], "name": updated["name"],
                                  "changes": {k: [m.get(k), v] for k, v in fixes.items() if v != m.get(k)}}
        await _event(conn, m["id"], "accept", detail, **_actor(principal))
        ids = [str(i) for i in m["created_from"]]
        notes = [dict(n) for n in (await conn.execute(text(_NOTE_SQL), {"ids": ids})).mappings().all()] if ids else []
        written = await _file_notes(conn, principal, m["id"], notes)
    return {"mode": payload(updated), "labels_written": written}


async def reject_proposal(principal, mode_id: str, reason: Any, *, base_version: int | None = None) -> dict:
    """Platform only. The proposal stays as a `rejected` row with the reason (it is never deleted), and its name is
    free again."""
    from . import proposals

    _require_platform(principal)
    reason = _reason(reason)
    async with db.lab_engine().begin() as conn:
        await conn.execute(text(_LOCK_SQL))
        m = await _lock(conn, mode_id)
        if m["status"] != "proposed":
            raise conflict("not_a_proposal")
        if base_version is not None:
            _check_version(m, base_version)
        await proposals.close_for_mode(conn, m["id"], "mode_changed")
        r = (await conn.execute(text(
            "UPDATE tracelab.modes SET status = 'rejected', rejected_reason = :reason, decided_by = CAST(:by AS uuid), "
            "decided_by_email = :email, decided_at = now(), version = version + 1, updated_at = now() "
            "WHERE id = CAST(:id AS uuid) RETURNING *"),
            {"reason": reason, "by": str(principal.user_id), "email": getattr(principal, "email", None) or None,
             "id": m["id"]})).mappings().first()
        await _event(conn, m["id"], "reject", {"reason": reason, "status": ["proposed", "rejected"]},
                     **_actor(principal))
    return {"mode": payload(row_dict(r))}


def _note_out(n: Any) -> dict[str, Any]:
    return {"annotation_id": n["annotation_id"], "turn_id": n["turn_id"], "organization_id": n["organization_id"],
            "reviewer_email": n["reviewer_email"], "note": n["note"], "quote": n["quote"],
            "step_label": AGENT_LABELS.get(n.get("span_agent") or "", "Answer"),
            "created_at": n["created_at"].isoformat() if n.get("created_at") else None}


async def list_proposals(principal, org: str | None, *, status: str = "proposed", limit: int = 200) -> list[dict]:
    """The platform's review queue of company proposals. A company admin (or a superadmin acting for a company) sees
    only that company's; the platform owner sees every company's. `status`: proposed (pending), rejected, accepted
    (decided in, now an ordinary mode) or all. Each row is the mode plus the notes it was proposed from. A company
    caller sees another reviewer's note only where the blind rule lets them (spec 2.3), as `get_mode` does."""
    from . import annotations

    if status not in PROPOSAL_FILTERS:
        raise invalid("unknown_status", given=status)
    company = _company_of(principal, org)
    async with db.lab_engine().connect() as conn:
        graph = await load_graph(conn)
        rows = [m for m in graph.modes.values() if m.get("proposer_organization_id")
                and (company is None or m["proposer_organization_id"] == company)
                and (status == "all" or (m["status"] in ("proposed", "rejected") and m["status"] == status)
                     or (status == "accepted" and m["status"] not in ("proposed", "rejected")))]
        rows.sort(key=lambda m: (m["created_at"], m["id"]))
        rows = rows[:max(1, int(limit))]
        ids = list(dict.fromkeys(n for m in rows for n in m["created_from"]))
        found = {str(r["annotation_id"]): dict(r) for r in (await conn.execute(text(
            "SELECT CAST(id AS text) AS annotation_id, turn_id, CAST(organization_id AS text) AS organization_id, "
            "CAST(reviewer_id AS text) AS reviewer_id, reviewer_email, note, quote, span_agent, created_at "
            "FROM tracelab.annotations WHERE id = ANY(CAST(:a AS uuid[]))"), {"a": ids})).mappings().all()} if ids else {}
        members = None
        if company is not None:
            # Once the platform edits or merges an accepted proposal it lists answers and notes of other companies,
            # so a company caller gets the same scrubbed row list_modes and get_mode give (G0 2.4).
            turns, own_notes = await _org_members(
                conn, company, sorted({t for m in rows for t in m["positive_turn_ids"] + m["close_negative_turn_ids"]}),
                ids)
            # Blind review (spec 2.3): another reviewer's note stays hidden until the caller may see others' work on
            # that answer; a note the caller wrote is always theirs to read. The same rule as get_mode, on the same
            # lab connection.
            seen = await annotations.visible_others(
                conn, principal, company, sorted({found[n]["turn_id"] for n in own_notes if n in found}))
            me = str(principal.user_id)
            members = (turns, {n for n in own_notes if n in found
                               and (found[n]["turn_id"] in seen or found[n]["reviewer_id"] == me)})
    out = []
    for m in rows:
        notes = [found[n] for n in m["created_from"] if n in found and (members is None or n in members[1])]
        p = payload(m)
        out.append({**(_scrub(p, *members) if members is not None else p), "notes": [_note_out(n) for n in notes]})
    return out


# ── reads ─────────────────────────────────────────────────────────────────────────────

async def _confirm_revisions(conn, mode_ids: list[str]) -> dict[str, dict[str, int]]:
    """The requirement revisions each mode's latest confirm recorded."""
    if not mode_ids:
        return {}
    rows = (await conn.execute(text(
        "SELECT DISTINCT ON (mode_id) CAST(mode_id AS text) AS mode_id, detail FROM tracelab.mode_events "
        "WHERE mode_id = ANY(CAST(:m AS uuid[])) AND action = 'edit' "
        "AND detail -> 'requirement_revisions' IS NOT NULL ORDER BY mode_id, seq DESC"),
        {"m": list(mode_ids)})).mappings().all()
    return {r["mode_id"]: dict((json_value(r["detail"]) or {}).get("requirement_revisions") or {}) for r in rows}


async def _org_members(conn, org: str, turn_ids: list[str], note_ids: list[str]) -> tuple[set[str], set[str]]:
    """Which of these answers and notes belong to the company. The answers' companies come from the chat (read-only
    main engine, else the lab's snapshot); the notes' from the lab database. Two reads, never one statement."""
    from . import labels

    turns: set[str] = set()
    notes: set[str] = set()
    if turn_ids:
        turns = {t for t, i in (await labels.turn_rows(turn_ids)).items() if i.get("organization_id") == org}
    if note_ids:
        notes = {str(r[0]) for r in (await conn.execute(text(
            "SELECT id FROM tracelab.annotations WHERE id = ANY(CAST(:a AS uuid[])) "
            "AND organization_id = CAST(:o AS uuid)"), {"a": list(note_ids), "o": org})).all()}
    return turns, notes


def _scrub(p: dict, turns: set[str], notes: set[str]) -> dict:
    """A company caller sees how many answers and notes a platform-wide mode lists, and only its own of them."""
    out = dict(p)
    out["positive_count"] = len(p.get("positive_turn_ids") or [])
    out["close_negative_count"] = len(p.get("close_negative_turn_ids") or [])
    out["created_from_count"] = len(p.get("created_from") or [])
    out["positive_turn_ids"] = [t for t in p.get("positive_turn_ids") or [] if t in turns]
    out["close_negative_turn_ids"] = [t for t in p.get("close_negative_turn_ids") or [] if t in turns]
    out["created_from"] = [n for n in p.get("created_from") or [] if n in notes]
    return out


def _proposer_visible(mode: dict, owner: bool, org: str | None) -> bool:
    """A mode a company proposed keeps who proposed it. Only the platform owner and that company read it; for every
    other caller (another company, a superadmin acting for another company, or no company at all) it stays hidden.
    A mode the platform made names no company, so there is nothing to hide."""
    proposer = mode.get("proposer_organization_id")
    return bool(owner) or not proposer or str(proposer) == str(org or "")


def _hide_proposer(p: dict, visible: bool) -> dict:
    """The mode as a caller may read it: the proposing company, the proposer and the platform decider are blanked
    unless `visible` (from _proposer_visible). Returns a copy."""
    if visible:
        return p
    out = dict(p)
    for k in PROPOSER_IDENTITY:
        if k in out:
            out[k] = None
    return out


async def list_modes(principal, org: str | None, *, include_retired: bool = False) -> list[dict]:
    """Modes with counts over the scope's reviewed answers, the contract's missing items and requirement flags. A
    company's pending or rejected proposal is not a mode yet: it is read through list_proposals."""
    from . import labels, requirements

    owner = labels.is_owner(principal, org)
    async with db.lab_engine().connect() as conn:
        graph = await load_graph(conn)
        modes = [m for m in graph.modes.values() if m["status"] in LIVE_STATUSES
                 or (include_retired and m["status"] == "retired")]
        live = [m for m in modes if m["status"] in LIVE_STATUSES]
        live_ids = [m["id"] for m in live]
        turn_ids = [r["turn_id"] for r in await labels.reviewed_rows(conn, org)]
        rel = graph.relevant_ids(live_ids)
        heads = await labels.load_heads(conn, turn_ids, rel) if turn_ids and live_ids else {}
        cells = labels.resolve(graph, heads, turn_ids, live_ids)
        listed = sorted({t for m in live for t in m["positive_turn_ids"] + m["close_negative_turn_ids"]})
        cheads = await labels.load_heads(conn, listed, rel) if listed and live_ids else {}
        active = await requirements.active_codes(conn, sorted({c for m in modes for c in m["requirement_ids"]}))
        revisions = await _confirm_revisions(conn, live_ids)
        members = None
        if org:
            members = await _org_members(
                conn, org, sorted({t for m in modes for t in m["positive_turn_ids"] + m["close_negative_turn_ids"]}),
                sorted({n for m in modes for n in m["created_from"]}))
    out: list[dict] = []
    for m in sorted(modes, key=lambda x: (x["status"] == "retired", x["created_at"], x["id"])):
        p = payload(m)
        if m["status"] != "retired":
            counts = {"present": 0, "absent": 0, "unchecked": 0}
            for t in turn_ids:
                counts[cells[(t, m["id"])].state] += 1
            mc = {t: labels.resolve_cell(graph, cheads, t, m)
                  for t in m["positive_turn_ids"] + m["close_negative_turn_ids"]}
            p.update(counts=counts, contract_missing=contract_missing(m, mc, active),
                     requirement_flags=requirement_flags(m, revisions.get(m["id"]), active), live_mode_id=m["id"])
        else:
            p.update(counts=None, contract_missing=None, requirement_flags=[], live_mode_id=graph.root_of(m["id"]))
        p = _scrub(p, *members) if members is not None else p
        out.append(_hide_proposer(p, _proposer_visible(m, owner, org)))
    return out


async def get_mode(principal, org: str | None, mode_id: str) -> dict | None:
    """The mode drawer: events, listed positives and close negatives with their current cells, close-negative
    candidates, the notes it came from or that were filed under it, and the contract checklist. A company's pending or
    rejected proposal opens only for the platform owner and for the proposing company."""
    from . import annotations, labels, requirements

    if not is_uuid(mode_id):
        return None
    owner = labels.is_owner(principal, org)
    async with db.lab_engine().connect() as conn:
        graph = await load_graph(conn)
        m = graph.modes.get(str(uuid.UUID(str(mode_id))))
        if m is None:
            return None
        if m["status"] in ("proposed", "rejected") and not owner and m.get("proposer_organization_id") != org:
            return None
        live = m["status"] in LIVE_STATUSES
        events = (await conn.execute(text(
            "SELECT seq, action, detail, actor_email, at FROM tracelab.mode_events "
            "WHERE mode_id = CAST(:m AS uuid) ORDER BY seq DESC LIMIT 200"), {"m": m["id"]})).mappings().all()
        rel = graph.relevant_ids([m["id"]])
        listed = list(dict.fromkeys(m["positive_turn_ids"] + m["close_negative_turn_ids"]))
        lheads = await labels.load_heads(conn, listed, rel) if listed else {}
        cells = {t: labels.resolve_cell(graph, lheads, t, m) for t in listed}
        own = await labels.load_heads(conn, None, [m["id"]], org=org)
        candidates = [t for (t, _mid), h in own.items()
                      if h.present is False and not h.tombstone and h.approval == "single" and t not in listed][:50]
        info = await labels.turn_rows(listed + candidates, with_question=True)
        part_ids = [p["id"] for p in graph.parts(m["id"])]
        part_heads = await labels.load_heads(conn, None, part_ids, org=org)
        note_ids = list(dict.fromkeys(
            [n for p in graph.parts(m["id"]) for n in p["created_from"]]
            + [h.annotation_id for h in part_heads.values() if h.source == "human_open_code" and h.annotation_id]))
        notes: list[dict] = []
        if note_ids:
            notes = [dict(r) for r in (await conn.execute(text(
                "SELECT CAST(id AS text) AS annotation_id, turn_id, CAST(organization_id AS text) AS organization_id, "
                "CAST(reviewer_id AS text) AS reviewer_id, reviewer_email, note, quote, span_agent, created_at "
                "FROM tracelab.annotations WHERE id = ANY(CAST(:a AS uuid[]))"),
                {"a": note_ids})).mappings().all()]
        contract = await contract_check(conn, m, graph) if live else None
        active = await requirements.active_codes(conn, m["requirement_ids"])
        revisions = (await _confirm_revisions(conn, [m["id"]])).get(m["id"])
        visible: set[str] | None = None
        if not owner:
            notes = [n for n in notes if n["organization_id"] == org]
            seen = await annotations.visible_others(conn, principal, org, sorted({n["turn_id"] for n in notes}))
            notes = [n for n in notes if n["turn_id"] in seen or n["reviewer_id"] == str(principal.user_id)]
            visible = {t for t, i in info.items() if i.get("organization_id") == org}

    def _keep(t: str) -> bool:
        return visible is None or t in visible

    def _item(t: str, want: str) -> dict:
        c, i = cells.get(t), info.get(t) or {}
        return {"turn_id": t, "question": i.get("question"), "state": c.state if c else None,
                "approval": c.approval if c else None, "source": c.source if c else None,
                "ok": bool(c and c.state == want and c.approval == "single")}

    mode_out = payload(m)
    if visible is not None:
        mode_out = _scrub(mode_out, visible, {n["annotation_id"] for n in notes})
    mode_out["requirement_flags"] = requirement_flags(m, revisions, active)
    shows_proposer = _proposer_visible(m, owner, org)
    mode_out = _hide_proposer(mode_out, shows_proposer)
    return {
        "mode": mode_out,
        "events": [{"seq": e["seq"], "action": e["action"],
                    "actor_email": e["actor_email"] if shows_proposer or e["action"] not in PROPOSAL_EVENTS else None,
                    "at": e["at"].isoformat() if e["at"] else None,
                    "detail": (json_value(e["detail"]) or {}) if owner else
                    {k: v for k, v in (json_value(e["detail"]) or {}).items() if k in SAFE_EVENT_KEYS}}
                   for e in events],
        "positives": [_item(t, "present") for t in m["positive_turn_ids"] if _keep(t)],
        "close_negatives": [_item(t, "absent") for t in m["close_negative_turn_ids"] if _keep(t)],
        "close_negative_candidates": [{"turn_id": t, "question": (info.get(t) or {}).get("question")}
                                      for t in candidates if _keep(t)],
        "notes": [_note_out(n) for n in notes],
        "contract": None if contract is None else {"ready": not contract, "missing": contract},
    }


# ── merge and split (advisory lock, then every mode re-checked inside the transaction) ─

async def merge(principal, source_ids: list[str], *, into: str | None, new: dict | None,
                base_versions: dict[str, int]) -> dict:
    """Sources go into a live target T or a new mode N (N needs at least two sources). Labels are not rewritten:
    they resolve through `merged_into` (spec §5.4)."""
    from . import proposals

    _require_platform(principal)
    sources = list(dict.fromkeys(str(s) for s in source_ids or []))
    if (into is None) == (new is None):
        raise invalid("merge_target")
    if not sources:
        raise invalid("merge_needs_a_source")
    if into is not None and str(into) in sources:
        raise invalid("merge_into_itself")
    if new is not None:
        if len(sources) < 2:
            raise invalid("merge_needs_two_sources")
        _check_draft(new)
    bases = {str(k): int(v) for k, v in (base_versions or {}).items()}
    async with db.lab_engine().begin() as conn:
        await conn.execute(text(_LOCK_SQL))
        graph = await load_graph(conn)
        ids = sorted(set(sources) | ({str(into)} if into is not None else set()))
        locked = {i: await _lock(conn, i) for i in ids}
        for i in ids:
            m = locked[i]
            _require_live(m, graph)
            _require_unfrozen(m)
            if bases.get(i) != int(m["version"]):
                raise Stale(payload(m))
        union = list(dict.fromkeys(x for i in ([str(into)] if into is not None else []) + sources
                                   for x in locked[i]["created_from"]))
        closed = 0
        for i in ids:
            closed += await proposals.close_for_mode(conn, i, "mode_changed")
        await conn.execute(text("UPDATE tracelab.modes SET status = 'retired', retired_at = now(), "
                                "version = version + 1, updated_at = now() WHERE id = ANY(CAST(:s AS uuid[]))"),
                           {"s": sources})
        if new is not None:
            await _validate(conn, new)
            await _name_free(conn, new["name"])
            target = await _insert_mode(conn, principal, new, created_from=union, absorbed=True)
        else:
            r = (await conn.execute(text(
                "UPDATE tracelab.modes SET absorbed_at = now(), created_from = CAST(:cf AS uuid[]), "
                "version = version + 1, updated_at = now() WHERE id = CAST(:t AS uuid) RETURNING *"),
                {"cf": union, "t": str(into)})).mappings().first()
            target = row_dict(r)
        await conn.execute(text("UPDATE tracelab.modes SET merged_into = CAST(:t AS uuid) "
                                "WHERE id = ANY(CAST(:s AS uuid[]))"), {"t": target["id"], "s": sources})
        for s in sources:
            await _event(conn, s, "merge", {"role": "source", "into": target["id"]}, **_actor(principal))
        await _event(conn, target["id"], "merge", {"role": "target", "sources": sources, "new": new is not None},
                     **_actor(principal))
    return {"mode": payload(target), "closed_proposals": closed}


async def split(principal, mode_id: str, children: list[dict], assignments: dict[str, list[int]], *,
                base_version: int) -> dict:
    """Parent P becomes 2-5 candidate children, each taking its own created_from from P's notes. The owner
    assigns P's present answers to children (a human_structured_label each, basis via split); unassigned
    answers read unchecked on the children until someone decides them."""
    from . import evidence, labels, proposals

    _require_platform(principal)
    kids = [dict(c) for c in children or []]
    if not 2 <= len(kids) <= 5:
        raise invalid("split_needs_two_to_five")
    names = [c.get("name") for c in kids]
    if len(set(names)) != len(names):
        raise invalid("duplicate_child_names")
    for c in kids:
        _check_draft(c)
    plan = {str(t): [int(i) for i in (idx or [])] for t, idx in (assignments or {}).items()}
    async with db.lab_engine().begin() as conn:
        await conn.execute(text(_LOCK_SQL))
        graph = await load_graph(conn)
        p = await _lock(conn, mode_id)
        _require_live(p, graph)
        _require_unfrozen(p)
        _check_version(p, base_version)
        parent_notes = set(p["created_from"])
        for c in kids:
            await _validate(conn, c)
            cf = [str(x) for x in c.get("created_from") or []]
            if not cf or any(x not in parent_notes for x in cf):
                raise invalid("created_from_not_in_parent", name=c["name"])
        relevant = graph.relevant_ids([p["id"]])
        heads = await labels.load_heads(conn, None, relevant)
        present = {t for (t, _m) in heads if labels.resolve_cell(graph, heads, t, p).state == "present"}
        for t, idx in plan.items():
            if t not in present:
                raise invalid("not_a_present_turn", turn_id=t)
            if any(i < 0 or i >= len(kids) for i in idx):
                raise invalid("bad_child_index", turn_id=t)
        closed = await proposals.close_for_mode(conn, p["id"], "mode_changed")
        r = (await conn.execute(text("UPDATE tracelab.modes SET status = 'retired', retired_at = now(), "
                                     "version = version + 1, updated_at = now() WHERE id = CAST(:id AS uuid) "
                                     "RETURNING *"), {"id": p["id"]})).mappings().first()
        parent = row_dict(r)
        made: list[dict] = []
        for c in kids:
            await _name_free(conn, c["name"])
            made.append(await _insert_mode(conn, principal, c, created_from=[str(x) for x in c["created_from"]],
                                           split_from=p["id"]))
        assigned = {t: idx for t, idx in plan.items() if idx}
        turns = await labels.turn_info_from_labels(conn, list(assigned), relevant)
        by_org: dict[str, list[str]] = {}
        for t in assigned:
            if turns.get(t, {}).get("organization_id"):
                by_org.setdefault(turns[t]["organization_id"], []).append(t)
        for org, turn_ids in by_org.items():            # a labelled answer's evidence is kept (idempotent)
            await evidence.snapshot(turn_ids, organization_id=org, reason=SNAPSHOT_REASON)
        written = 0
        for t, idx in assigned.items():
            info = turns.get(t)
            if info is None or not info.get("organization_id"):
                continue
            for i in sorted(set(idx)):
                await labels.write(conn, principal, turn=info, mode_id=made[i]["id"], present=True,
                                   source="human_structured_label", base=labels.ANY_HEAD,
                                   basis={"via": "split", "from_mode": p["id"]})
                written += 1
        await _event(conn, p["id"], "split", {"role": "parent", "children": [c["id"] for c in made],
                                              "assigned": written, "closed_proposals": closed}, **_actor(principal))
        for c in made:
            await _event(conn, c["id"], "split", {"role": "child", "from": p["id"]}, **_actor(principal))
    return {"parent": payload(parent), "children": [payload(c) for c in made], "labels_written": written}


# ── discovery, saturation and the re-review prompt (spec §4, Phase 5 rows) ────────────────

def acting_company(principal, org: str | None) -> str | None:
    """The company a read acts for, under the route's own check (defence in depth): None for the platform owner (a
    superadmin naming no company), the named company for a superadmin acting for one, and for a company admin only their
    own (naming none means their own). A plain user, or an admin with no company or naming another, is refused
    (403 reviewers_only). The grid, progress and discovery reads call this first, so no caller can read across companies
    whatever route reached them."""
    return _company_of(principal, org)


def founding_reviews(reviews: list[dict], graph: ModeGraph, note_lineage: dict[str, str]) -> dict[str, int]:
    """Live mode -> index of its founding review: the earliest review among the lineages of the notes in
    created_from, for the mode and every mode merged into it. Modes retired without a merge do not count."""
    pos = {r["lineage_id"]: i for i, r in enumerate(reviews)}
    out: dict[str, int] = {}
    for m in graph.live():
        idx = [pos[note_lineage[n]] for p in graph.parts(m["id"]) for n in p.get("created_from") or []
               if n in note_lineage and note_lineage[n] in pos]
        if idx:
            out[m["id"]] = min(idx)
    return out


def discovery_curve(n_reviews: int, founding: dict[str, int], *, block: int) -> list[dict]:
    """New modes per block of `block` reviews, oldest first; the last block may be unfinished."""
    if block < 1:
        raise ValueError("a block holds at least one review")
    points: list[dict] = []
    cumulative = 0
    for b in range((n_reviews + block - 1) // block):
        lo, hi = b * block, min((b + 1) * block, n_reviews)
        new = sum(1 for i in founding.values() if lo <= i < hi)
        cumulative += new
        points.append({"block": b + 1, "reviews_through": hi, "new_modes": new, "cumulative_modes": cumulative})
    return points


def saturation(points: list[dict], *, block: int, n_reviews: int) -> dict:
    """The hint: at least three blocks of reviews, the last two full blocks quiet, and nothing new in an unfinished
    block after them either, so "the last 2 x block reviews produced no new failure mode" is always true."""
    full = [p for p in points if p["reviews_through"] - (p["block"] - 1) * block == block]
    unfinished = [p for p in points if p not in full]
    hint = (n_reviews >= 3 * block and len(full) >= 2 and full[-1]["new_modes"] == 0 and full[-2]["new_modes"] == 0
            and all(p["new_modes"] == 0 for p in unfinished))
    return {"hint": hint, "text": (f"The last {2 * block} reviews produced no new failure mode. Naming may be "
                                   "saturated: run depth scans for the modes you have instead of more breadth "
                                   "batches.") if hint else ""}


def final_uniform_text(k: int) -> str:
    if k < 2:
        return ""
    return (f"The final fair batch produced {k} new failure modes. Review another batch before treating the list "
            "as stable.")


def re_review(reviews: list[dict], modes_created: dict[str, datetime], reviewer_id: str) -> dict:
    """The caller's own prompt: two or more modes created since their fifth review, with their first five answers."""
    mine = sorted((r for r in reviews if r["reviewer_id"] == reviewer_id), key=lambda r: r["first_at"])
    if len(mine) < 5:
        return {"count": 0, "text": "", "turn_ids": []}
    fifth = mine[4]["first_at"]
    n = sum(1 for at in modes_created.values() if at > fifth)
    if n < 2:
        return {"count": n, "text": "", "turn_ids": []}
    return {"count": n, "text": f"You have found {n} failure modes since you reviewed your first answers. "
                                "Worth a second pass.", "turn_ids": [r["turn_id"] for r in mine[:5]]}


# A review is a reviewer's live verdict on an answer (right or wrong, never a cleared one): its position in time is when
# that reviewer first wrote it (the first version of its lineage). Golden answers are left out in Python
# (conversations.is_golden_turn), since a verdict row carries no golden marker.
_REVIEWS_SQL = """
SELECT CAST(h.lineage_id AS text) AS lineage_id, h.turn_id, CAST(h.reviewer_id AS text) AS reviewer_id,
       f.created_at AS first_at
  FROM tracelab.annotations h
  JOIN tracelab.annotations f ON f.id = h.lineage_id
 WHERE h.kind = 'verdict' AND h.superseded_by IS NULL AND NOT h.tombstone {org}
 ORDER BY f.created_at, h.lineage_id
"""


async def _final_uniform(conn, org: str | None) -> set[str]:
    """Turns of complete, non-withdrawn final_uniform batches (every item has its reviews)."""
    where = "AND b.organization_id = CAST(:org AS uuid)" if org else ""
    rows = (await conn.execute(text(
        "SELECT CAST(b.id AS text) AS id, b.reviews_required, i.turn_id, "
        "(SELECT count(DISTINCT a.reviewer_id) FROM tracelab.annotations a WHERE a.turn_id = i.turn_id "
        "AND a.kind = 'verdict' AND a.superseded_by IS NULL AND NOT a.tombstone) AS n "
        "FROM tracelab.sample_batches b JOIN tracelab.sample_items i ON i.batch_id = b.id "
        f"WHERE b.plan_step = 'final_uniform' AND b.withdrawn_at IS NULL {where}"),
        {"org": org} if org else {})).mappings().all()
    batches: dict[str, list] = {}
    for r in rows:
        batches.setdefault(r["id"], []).append(r)
    done: set[str] = set()
    for items in batches.values():
        if all(int(i["n"]) >= int(i["reviews_required"]) for i in items):
            done.update(i["turn_id"] for i in items)
    return done


async def discovery(principal, org: str | None, *, block: int = 10) -> dict:
    """Progress 'Discovery': the curve of new failure modes per block of reviews, which batch each came from, the
    saturation hint, the final fair batch's own note, and the caller's re-review prompt. Counts only: no note text."""
    from . import conversations

    org = acting_company(principal, org)
    block = max(5, min(50, int(block)))
    async with db.lab_engine().connect() as conn:
        graph = await load_graph(conn)
        where = "AND h.organization_id = CAST(:org AS uuid)" if org else ""
        reviews = [dict(r) for r in (await conn.execute(text(_REVIEWS_SQL.format(org=where)),
                                                        {"org": org} if org else {})).mappings().all()
                   if not conversations.is_golden_turn(r["turn_id"])]
        note_ids = sorted({n for m in graph.modes.values() for n in m["created_from"]})
        lineage: dict[str, str] = {}
        if note_ids:
            lineage = {str(r[0]): str(r[1]) for r in (await conn.execute(text(
                "SELECT id, lineage_id FROM tracelab.annotations WHERE id = ANY(CAST(:a AS uuid[]))"),
                {"a": note_ids})).all()}
        turn_ids = sorted({r["turn_id"] for r in reviews})
        batch_of: dict[str, str] = {}
        if turn_ids:
            bw = "AND organization_id = CAST(:org AS uuid)" if org else ""
            batch_of = {r[0]: str(r[1]) for r in (await conn.execute(text(
                f"SELECT turn_id, batch_id FROM tracelab.sample_items WHERE turn_id = ANY(CAST(:t AS text[])) {bw}"),
                {"t": turn_ids, **({"org": org} if org else {})})).all()}
        final_turns = await _final_uniform(conn, org)
    founding = founding_reviews(reviews, graph, lineage)
    points = discovery_curve(len(reviews), founding, block=block)
    by_batch: dict[str | None, int] = {}
    for i in founding.values():
        b = batch_of.get(reviews[i]["turn_id"])
        by_batch[b] = by_batch.get(b, 0) + 1
    sat = saturation(points, block=block, n_reviews=len(reviews))
    sat["final_uniform_text"] = final_uniform_text(
        sum(1 for i in founding.values() if reviews[i]["turn_id"] in final_turns)) if final_turns else ""
    rr = re_review(reviews, {m["id"]: m["created_at"] for m in graph.live()}, str(principal.user_id))
    return {"points": points,
            "by_batch": [{"batch_id": b, "new_modes": n}
                         for b, n in sorted(by_batch.items(), key=lambda x: (x[0] is None, str(x[0])))],
            "saturation": sat, "re_review": rr, "reviews": len(reviews), "block": block}


async def progress_extension(principal, org: str | None, *, block: int = 10) -> dict:
    """The failure-mode rows of GET /progress (spec §4): label completeness, per-mode first-failure vs any-instance
    counts, the discovery curve of new modes, the saturation hint, the re-review prompt and the unfiled-note count.
    Each part reads the company `org` for the caller (acting_company refuses a caller outside it, whatever route got
    here); the platform owner acting for none reads every company."""
    from . import annotations, labels

    completeness = await labels.completeness(principal, org)
    per_mode = await labels.per_mode(principal, org)
    disc = await discovery(principal, org, block=block)
    unfiled = await annotations.unfiled_notes(principal, org, limit=1, offset=0)
    return {"completeness": completeness, "modes": per_mode, "discovery": disc["points"],
            "discovery_by_batch": disc["by_batch"], "saturation": disc["saturation"], "re_review": disc["re_review"],
            "unfiled_notes": unfiled["total"]}
