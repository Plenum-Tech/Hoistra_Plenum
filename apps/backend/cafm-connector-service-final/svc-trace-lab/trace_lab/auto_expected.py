"""Expected answers drafted for live questions (14 Nov 2026, Hussain: "I want the system to add it automatically when I ask
a question on the live Hoistra platform ... an expected answer in the trace based on the spec file"; "only for the live
answers, don't change anything in the flow from Codex"; 18 Nov 2026: "i need expected ans on each question" and "the
expected ans should the exact values").

    live question synced (sync_live) --(draft_for_live)--> expected answer, source coding_agent
    --(a person: Accept on the session page, or Edit)--> counts (runs.CURRENT_EXPECTED_WHERE)

A live question's draft is the exact values read from the company's records, each with the query that read it
(drafted_from "records"; records_queries says which query a turn gets, records_run reads it the way Hoistra's records
tool does, records_values turns the read into values and a sentence in fixed words, no model). The read is made with the
asker's own access when Trace Lab can rebuild it from the turn's user id (asker_scope: Hoistra's own principal code,
read-only), else for the whole company, and every query says which. A turn with no company is never read.

When no query can be made (the turn is not about records, Hoistra's map of the records is not built, the planner cannot
be reached, the read failed) the draft is what a correct answer must DO under the approved SPEC.md (one model call; the
model is given the rules and the question only, never Hoistra's own answer, and writes no values), and the evidence says
why in `no_query`. A draft that holds no values because of a passing reason (no map yet, planner or read failed, no trace
filed yet) is read again without a model call, and the values replace it: after BACKOFF[0], BACKOFF[1], BACKOFF[2] (30
minutes, 2 hours, 8 hours) and never after that; each try is written on the draft itself (a new version, the words
unchanged: `tries`, `last_tried`, and `no_query` when the reason changed), so a restart forgets nothing. A terminal reason
(not about records, no readable plan, a plan that does not fit the database) is stored as it is and not read again.

Candidates are questions of live day runs asked by hand (not Codex scenario turns), however old. At most PER_ROUND a
round, newest first, of which at most PROPOSALS_PER_ROUND may need a planner call; a question that needs one and finds
the budget used waits for the next round. Nothing is drafted from SPEC.md until a version of it is approved; values need
no SPEC.md. A failed model call stores nothing and the question is tried next round. TRACE_LAB_AUTO_EXPECTED=false turns
it off.

A draft nobody touched is drafted again (a new version; the old one stays as history) when it was written by an older
prompt or reading (DRAFT_VERSION) or, for a SPEC.md draft, from an older SPEC.md; a draft that holds values is never
drafted again because SPEC.md changed. A person's answer, an accepted one or a reviewed one is never drafted over.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import structlog
from sqlalchemy import text
from src.agents import ontology_qa as oq

from . import (agent, answer_key, conversations, db, eval_store, labelling, loop, records_queries, records_run,
               records_values, runs)
from .annotations import _limits

log = structlog.get_logger(__name__)

PER_ROUND = 20
#: Of the PER_ROUND questions, how many may ask Hoistra's planner for a query (a model call each).
PROPOSALS_PER_ROUND = 10
#: What a draft was written by, stored in its evidence ("draft"). An untouched draft written by an older one is drafted
#: again. 1 (no "draft" key): the prompt that could answer "no rule of the specification applies"; 2: the SPEC.md prompt
#: that always writes what a correct answer contains; 3: exact values read from the records (drafted_from "records").
DRAFT_VERSION = 3
MAX_TOKENS = 600
ENV_SWITCH = "TRACE_LAB_AUTO_EXPECTED"
#: Who a draft is written by: the coding agent, as its other suggestions are.
AUTHOR = SimpleNamespace(email=agent.AGENT_EMAIL, user_id=None)

SYSTEM = ("You write the expected answer to one question put to a facilities-management assistant. You do not know the "
          "company's records and you never see the assistant's answer, so you write what a correct answer to this "
          "question must do and contain, never values: which items or figures it gives, how it groups or orders them, "
          "which records they come from, and what it says when a name in the question is not on record. Follow the "
          "answer specification (SPEC.md) and name the codes of its rules that apply to this question; when none "
          "applies, still write what a correct answer contains and name no codes. Never write that no rule applies, and "
          "never leave the expected answer empty. Plain sentences, at most six.")
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["expected", "rules"],
          "properties": {"expected": {"type": "string"}, "rules": {"type": "array", "items": {"type": "string"}}}}

#: Why a SPEC.md draft holds no values, when the reason passes and the values are read again later (BACKOFF): Hoistra's map
#: of the records is not built, the planner or the read failed, the turn's trace is not filed yet. Any other reason stays.
RETRY_REASONS = ("no_map", "planner_unavailable", "read_failed", "no_trace")
#: How long a read waits before it is tried again, by how many tries it has had: 30 minutes, 2 hours, 8 hours. After
#: the last, a draft is not read again (the round runs every minute, each read loads the records' catalogue, and a
#: proposal is a model call).
REST = timedelta(minutes=30)
BACKOFF = (REST, REST * 4, REST * 16)


def enabled() -> bool:
    return (os.environ.get(ENV_SWITCH) or "true").strip().lower() not in ("false", "0", "no", "off")


def _prompt(spec: dict[str, Any], question: str) -> str:
    rules = "\n\n".join(f"### {r.get('code')} · {r.get('title') or 'Rule'}\n{str(r.get('text') or '').strip()}"
                        for r in spec.get("rules") or [])
    return f"SPEC.md, version {spec.get('version')}:\n\n{rules}\n\nThe question:\n{question.strip()}"


# A question whose newest expected answer is still the coding agent's own, untouched: nobody accepted, edited or reviewed
# it. {draft} says which of those drafts are wanted.
_UNTOUCHED = (
    "SELECT q.id::text AS id, q.question, q.organization_id::text AS org, q.asked_at, e.id::text AS draft_id,"
    " e.evidence->>'drafted_from' AS drafted_from, e.expected, e.evidence"
    " FROM tracelab.lab_questions q JOIN tracelab.lab_runs r ON r.id = q.run_id"
    " JOIN LATERAL (SELECT x.id, x.source, x.expected, x.evidence FROM tracelab.lab_expected_answers x"
    "   WHERE x.question_id = q.id"
    "   ORDER BY x.created_at DESC, x.id DESC LIMIT 1) e ON true"
    " WHERE {where} AND e.source = 'coding_agent' AND {draft}"
    " AND NOT EXISTS (SELECT 1 FROM tracelab.lab_reviews v WHERE v.subject_kind = 'expected_answer' AND v.subject_id = e.id)"
    " ORDER BY q.asked_at DESC, q.id LIMIT :lim")

# An untouched draft that is out of date: written by an older prompt or reading (DRAFT_VERSION; none stored = 1) or, for a
# SPEC.md draft, from another version of SPEC.md than the approved one. A draft that holds values never goes out of date
# with SPEC.md. Without an approved spec the version of SPEC.md is not compared.
#
# A draft that is only ever put over by values (an older answer-key or values draft) is read again while it has had fewer
# than :cap tries (`tries` in its evidence); a SPEC.md draft is put over by the model, so the cap does not apply to it.
_TRIES = "COALESCE(NULLIF(e.evidence->>'tries', '')::int, 0)"
_OUTDATED_BY_VERSION = ("e.evidence->>'drafted_from' IN ('SPEC.md', 'answer_key', 'records')"
                        " AND COALESCE(e.evidence->>'draft', '1') <> :dv"
                        f" AND (e.evidence->>'drafted_from' = 'SPEC.md' OR {_TRIES} < :cap)")
_OUTDATED_BY_SPEC = ("e.evidence->>'drafted_from' IN ('SPEC.md', 'answer_key', 'records')"
                     " AND (COALESCE(e.evidence->>'draft', '1') <> :dv OR (e.evidence->>'drafted_from' = 'SPEC.md'"
                     " AND COALESCE(e.evidence->>'spec_version', '') <> :sv))"
                     f" AND (e.evidence->>'drafted_from' = 'SPEC.md' OR {_TRIES} < :cap)")
# An untouched, current SPEC.md draft that holds no values for a passing reason, with tries left: read again.
_RETRY_DRAFTS = ("e.evidence->>'drafted_from' = 'SPEC.md'"
                 " AND e.evidence->>'no_query' IN ('" + "', '".join(RETRY_REASONS) + "')"
                 f" AND {_TRIES} < :cap")
assert _OUTDATED_BY_SPEC.count(":dv") == 1 and _OUTDATED_BY_VERSION.count(":dv") == 1


async def draft_for_live(org: str | None = None, *, now: datetime | None = None,
                         limit: int = PER_ROUND) -> dict[str, Any]:
    """Draft an expected answer for each live question that has none, then draft again the untouched drafts that are out
    of date, then read again the values of the drafts that could not be read (see the module). Returns drafted (new
    questions), redrafted (out-of-date drafts), failed (model calls that did not answer), keyed (drafts that hold values,
    of any of those), replaced (SPEC.md drafts whose values could be read at last) and skipped (why nothing was tried,
    else None)."""
    if not enabled():
        return {"drafted": 0, "failed": 0, "keyed": 0, "replaced": 0, "redrafted": 0,
                "skipped": "Automatic expected answers are turned off."}
    now = now or datetime.now(timezone.utc)
    live = ["r.kind = 'production'", f"NOT {runs.SCENARIO_Q}"]
    p: dict[str, Any] = {"lim": max(1, int(limit))}
    if org is not None:
        live.append("q.organization_id = CAST(:org AS uuid)")
        p["org"] = org
    where = ' AND '.join(live)
    new_only = live + ["NOT EXISTS (SELECT 1 FROM tracelab.lab_expected_answers e WHERE e.question_id = q.id)"]
    async with db.lab_engine().connect() as conn:
        spec = await loop.approved_spec(conn)
        if spec is not None and not spec.get("rules"):
            spec = None
        todo = (await conn.execute(text(
            "SELECT q.id::text AS id, q.question, q.organization_id::text AS org, q.asked_at"
            " FROM tracelab.lab_questions q"
            f" JOIN tracelab.lab_runs r ON r.id = q.run_id WHERE {' AND '.join(new_only)}"
            " ORDER BY q.asked_at DESC, q.id LIMIT :lim"), p)).mappings().all()
        # A page well past the budget: drafts still backing off do not hide the ones that are due.
        page = dict(p, lim=max(p["lim"] * 5, 100), cap=len(BACKOFF))
        outdated = (await conn.execute(text(_UNTOUCHED.format(
            where=where, draft=_OUTDATED_BY_SPEC if spec is not None else _OUTDATED_BY_VERSION)),
            dict(page, dv=str(DRAFT_VERSION), **({"sv": str(spec.get("version"))} if spec is not None else {})))
        ).mappings().all()
        again = (await conn.execute(text(_UNTOUCHED.format(where=where, draft=_RETRY_DRAFTS)), page)).mappings().all()
    codes = loop.rule_codes(spec) if spec else set()
    tally = {"drafted": 0, "failed": 0, "keyed": 0, "redrafted": 0, "replaced": 0}
    # The newest questions first, new, out of date or to be read again (18 Nov 2026): a draft on what people just asked is
    # not left behind a backlog of older questions. A question in more than one list is worked once.
    cands: dict[str, tuple[Any, str]] = {q["id"]: (q, "retry") for q in again}
    cands.update({q["id"]: (q, "redraft") for q in outdated})
    cands.update({q["id"]: (q, "new") for q in todo})
    work = sorted((w for w in cands.values() if not (_only_values(*w) and not _due(w[0], now))),
                  key=lambda w: (w[0]["asked_at"], w[0]["id"]), reverse=True)[:p["lim"]]
    rnd: dict[str, Any] = {"proposals": PROPOSALS_PER_ROUND, "onto": None}
    for q, kind in work:
        if await _draft_one(q, kind, spec, codes, now, tally, rnd):
            tally[{"new": "drafted", "redraft": "redrafted", "retry": "replaced"}[kind]] += 1
    if tally["drafted"] or tally["redrafted"] or tally["replaced"]:
        log.info("trace_lab.auto_expected.drafted", drafted=tally["drafted"], redrafted=tally["redrafted"],
                 keyed=tally["keyed"], failed=tally["failed"], replaced=tally["replaced"])
    return {"drafted": tally["drafted"], "failed": tally["failed"], "keyed": tally["keyed"],
            "replaced": tally["replaced"], "redrafted": tally["redrafted"],
            "skipped": None if spec else "No version of SPEC.md is approved yet: only questions with a records query "
                                         "are drafted."}


def _only_values(q: Any, kind: str) -> bool:
    """A draft that is only ever put over by values (never by a SPEC.md draft): one to be read again, or an older values
    draft. When its values cannot be read, the try is written on it (_note_try) and it is read again later (_due)."""
    return kind == "retry" or (kind == "redraft" and q["drafted_from"] != "SPEC.md")


def _evidence_of(q: Any) -> dict[str, Any]:
    ev = q["evidence"]
    if isinstance(ev, str):
        try:
            ev = json.loads(ev)
        except ValueError:
            ev = None
    return dict(ev) if isinstance(ev, dict) else {}


def _due(q: Any, now: datetime) -> bool:
    """Whether a draft that could not be read is read again now: it has had fewer than len(BACKOFF) tries, and BACKOFF of
    them has passed since the last (a draft never tried is due at once)."""
    ev = _evidence_of(q)
    try:
        tries = int(ev.get("tries") or 0)
    except (TypeError, ValueError):
        tries = 0
    if tries >= len(BACKOFF):
        return False
    try:
        last = datetime.fromisoformat(str(ev["last_tried"]))
    except (KeyError, TypeError, ValueError):
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return now >= last + BACKOFF[tries]


async def _note_try(q: Any, reason: str | None, now: datetime) -> bool:
    """A draft read again that still holds no values: write the try on it, a new version with the same words and a new
    attempt count (`tries`, `last_tried`), and for a SPEC.md draft the latest reason (a terminal one ends the retries).
    False when a person, or another round, got there first."""
    ev = _evidence_of(q)
    try:
        tries = int(ev.get("tries") or 0) + 1
    except (TypeError, ValueError):
        tries = 1
    ev.update(tries=tries, last_tried=now.isoformat(), replaced_draft=q["draft_id"])
    if ev.get("drafted_from") == "SPEC.md" and reason:
        ev["no_query"] = reason
    async with db.lab_engine().begin() as conn:
        await _limits(conn)
        await conn.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": runs.SYNC_LOCK})
        await conn.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": runs.EXPECTED_LOCK})
        if not await _still_untouched(conn, q["id"], q["draft_id"]):
            return False
        await runs._insert_expected(conn, AUTHOR, question_id=q["id"], expected=q["expected"], expected_id=None,
                                    evidence=ev, source="coding_agent")
    return True


async def _draft_one(q: Any, kind: str, spec: dict[str, Any] | None, codes: set[str], now: datetime,
                     tally: dict[str, int], rnd: dict[str, Any]) -> bool:
    """One question's expected answer: the values read from the records when a query can be made for it, else (a new
    question or an out-of-date SPEC.md draft) a SPEC.md draft (one model call) that says why there are no values. Stored
    as a new version over the question's untouched draft (kinds "redraft" and "retry"), or as its first answer (kind
    "new"). False when nothing was stored: no query and no spec, a draft that holds values or that cannot be read again
    stays, the budget for a proposal is used (next round), the model failed (tried next round), or a person or another
    round got there first."""
    replaces = None if kind == "new" else q["draft_id"]
    got = await _read_values(q, now, rnd)
    if got["state"] == "wait":
        return False
    if got["state"] == "none" and _only_values(q, kind):
        if not got.get("kept"):                 # a verdict still remembered is not a new read: no try is counted
            await _note_try(q, got["reason"], now)
        return False
    if got["state"] == "values":
        evidence, expected, keyed = got["evidence"], got["expected"], True
    elif kind == "retry" or spec is None or (kind == "redraft" and q["drafted_from"] != "SPEC.md"):
        # Nothing to write from: no spec; or nothing new to put over what is there (values stay until others replace them).
        return False
    else:
        keyed = False
        try:
            out = await agent._ask(SYSTEM, _prompt(spec, q["question"] or ""), SCHEMA, MAX_TOKENS)
            expected = str((out or {}).get("expected") or "").strip()[:runs.EXPECTED_MAX]
            if not expected:
                raise ValueError("the model wrote no expected answer")
        except Exception as exc:  # noqa: BLE001 - one question's failure never stops the others; tried next round
            tally["failed"] += 1
            log.warning("trace_lab.auto_expected.draft_failed", question_id=q["id"], error=str(exc)[:200])
            return False
        cited = [c for c in dict.fromkeys(str(x).strip().upper() for x in (out.get("rules") or [])) if c in codes]
        evidence = {"drafted_from": "SPEC.md", "spec_version": spec.get("version"), "rules": cited,
                    "draft": DRAFT_VERSION}
        if got.get("reason"):
            evidence["no_query"] = got["reason"]
            if got["reason"] in RETRY_REASONS:
                evidence.update(tries=0, last_tried=now.isoformat())    # read again after BACKOFF[0]
    if replaces is not None:
        evidence = dict(evidence, replaced_draft=replaces)
    async with db.lab_engine().begin() as conn:
        await _limits(conn)
        await conn.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": runs.SYNC_LOCK})
        await conn.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": runs.EXPECTED_LOCK})
        if replaces is None:
            taken = (await conn.execute(text(
                "SELECT 1 FROM tracelab.lab_expected_answers WHERE question_id = CAST(:q AS uuid) LIMIT 1"),
                {"q": q["id"]})).first()
            if taken:
                return False                    # a person, or another round, wrote one while the model was asked
        elif not await _still_untouched(conn, q["id"], replaces):
            return False                        # a person, or another round, got there first
        await runs._insert_expected(conn, AUTHOR, question_id=q["id"], expected=expected, expected_id=None,
                                    evidence=evidence, source="coding_agent")
    if keyed:
        tally["keyed"] += 1
    return True


async def _still_untouched(conn: Any, question_id: str, draft_id: str) -> bool:
    """The draft is still the question's newest expected answer and nobody reviewed it (checked under the locks)."""
    newest = (await conn.execute(text(
        "SELECT x.id::text FROM tracelab.lab_expected_answers x WHERE x.question_id = CAST(:q AS uuid)"
        " ORDER BY x.created_at DESC, x.id DESC LIMIT 1"), {"q": question_id})).scalar_one_or_none()
    reviewed = (await conn.execute(text(
        "SELECT 1 FROM tracelab.lab_reviews v WHERE v.subject_kind = 'expected_answer'"
        " AND v.subject_id = CAST(:e AS uuid) LIMIT 1"), {"e": draft_id})).first()
    return newest == draft_id and not reviewed


# ── The values ────────────────────────────────────────────────────────────────────────────────────────────────────────

#: Reads rest, so a round that runs every minute does not repeat them (each loads the records' catalogue, the read-only pool
#: is small, and a proposal is a model call). When Hoistra's map of the records is not built yet, every read rests
#: ("*", REST). A question whose read ended with no values keeps its verdict (_settled: until when, and why) for
#: BACKOFF[n] after its n-th such verdict in this process (_looks), whether or not a draft could be stored from it (no
#: approved spec, the SPEC.md call failing): the next round does not ask the planner again. What a draft that could not be
#: read remembers across restarts is on the draft itself (_note_try).
_resting: dict[str, datetime] = {}
_settled: dict[str, tuple[datetime, str | None]] = {}
_looks: dict[str, int] = {}


def _none(reason: str | None, qid: str | None = None, now: datetime | None = None) -> dict[str, Any]:
    """No values for this question, and why; with a question and the time, the verdict is kept (see _settled)."""
    if now is not None and qid is not None:
        if reason == "no_map":
            _resting["*"] = now + REST
        else:
            n = _looks[qid] = _looks.get(qid, 0) + 1
            _settled[qid] = (now + BACKOFF[min(n, len(BACKOFF)) - 1], reason)
    return {"state": "none", "reason": reason}


def _called_records(nodes: list) -> bool:
    return any(isinstance(n, dict) and n.get("name") == records_queries.RECORDS_TOOL and records_queries._is_tool(n)
               for n in nodes)


class AskerUnreadable(RuntimeError):
    """The asker's access could not be read (the application database, the principal code, an id that is not one). It is a
    failed read, to be tried again: never read as "no restriction"."""


async def asker_scope(org: str, user_id: str | None) -> dict[str, Any]:
    """The access the person who asked had, rebuilt read-only: {"scope": "asker", "building_ids": [...] | None, "email"}
    (None = every building of the company, as an administrator), or {"scope": "company", "building_ids": None, "email":
    None} when it cannot be rebuilt for a reason that will not change (no user id on the turn, no such account, a person
    of another company) and the read is for the whole company. A read that fails (the database, Hoistra's principal code, an
    id that is not an id) raises AskerUnreadable and is logged as a warning: the caller tries again later.

    Hoistra's own principal code builds the caller (svc-deepagents services.principal._from_me, from what the identity
    service's /api/auth/me says), and Trace Lab asks the same two questions the identity service asks, through its
    read-only connection to the application database: the account (role, company), and for a plain user the buildings
    allocated to them (user_buildings). Admins and superadmins are not bounded by allocation."""
    company: dict[str, Any] = {"scope": "company", "building_ids": None, "email": None}
    if not user_id:
        return company
    try:
        from src.services.principal import _from_me

        async with conversations._read() as conn:
            row = (await conn.execute(text(
                f"SELECT u.id::text AS id, u.email, u.organization_id::text AS org, u.platform_role AS role"
                f" FROM {labelling.USERS} u WHERE u.id::text = :u"), {"u": user_id})).mappings().first()
            if row is None:
                return company
            role = str(row["role"] or "user")
            ids = None
            if role == "user":
                if row["org"] != org:
                    return company              # a person of another company is not who asked here
                ids = [str(r[0]) for r in (await conn.execute(text(
                    "SELECT ub.building_id::text FROM plenum_cafm.user_buildings ub WHERE ub.user_id::text = :u"),
                    {"u": user_id})).all()]
        who = _from_me({"user": {"id": row["id"], "email": row["email"], "organization_id": row["org"], "role": role,
                                 "building_ids": ids}})
    except Exception as exc:  # noqa: BLE001 - a failed read is not "no restriction"
        log.warning("trace_lab.auto_expected.asker_unreadable", user_id=user_id, error=str(exc)[:200])
        raise AskerUnreadable(str(exc)[:200]) from exc
    return {"scope": "asker", "email": who.email or None,
            "building_ids": None if who.building_ids is None else [str(b) for b in who.building_ids]}


async def _turn_context(q: Any) -> dict[str, Any] | None:
    """What a question's turn holds for reading its values: its turn id (the sync's link first), its trace's nodes, and the
    person who asked (the chat's own record of the turn). None when the turn is another company's than the question's:
    nothing is read for it this round. A turn whose trace is not filed yet has no nodes (`filed` False); one the chat
    has no record of has no asker. A read that fails raises."""
    out: dict[str, Any] = {"turn_id": None, "nodes": [], "filed": False, "user_id": None}
    async with conversations._lab_read() as conn:
        out["turn_id"] = (await conn.execute(text(
            "SELECT l.turn_id FROM tracelab.lab_question_traces l WHERE l.question_id = CAST(:q AS uuid)"
            " ORDER BY (l.source = 'sync') DESC, l.created_at, l.turn_id LIMIT 1"), {"q": q["id"]})).scalar_one_or_none()
        if out["turn_id"] is not None:
            filed = (await conn.execute(text("SELECT t.organization_id::text FROM tracelab.traces t WHERE t.id = :t"),
                                        {"t": out["turn_id"]})).first()
            if filed is not None:
                if filed[0] != q["org"]:
                    return None
                out["nodes"], out["filed"] = await eval_store.nodes_of(conn, out["turn_id"]), True
    if out["turn_id"] is not None:
        async with conversations._read() as conn:       # a failed read raises: the asker is not guessed
            turn = (await conn.execute(text(
                f"SELECT tt.user_id::text AS user_id, tt.organization_id::text AS org FROM {conversations.TURNS} tt"
                " WHERE tt.turn_id = :t"), {"t": out["turn_id"]})).mappings().first()
        if turn is not None:
            if turn["org"] != q["org"]:
                return None
            out["user_id"] = turn["user_id"]
    return out


async def _read_values(q: Any, now: datetime, rnd: dict[str, Any]) -> dict[str, Any]:
    """The values of one question, read from the records: {"state": "values", "evidence", "expected"}, or {"state": "none",
    "reason"} when no query can be made or read (the reason is stored; see RETRY_REASONS), or {"state": "wait"} when
    nothing is decided this round (another company's turn, the proposal budget used up, the lab database unreadable).

    The queries come from records_queries (Hoistra's own, Trace Lab's fixed one, a proposal); each is read with the
    asker's access (asker_scope) and becomes one block of the evidence, in order. One failed read fails them all: no half
    an answer is stored."""
    qid, org = q["id"], q["org"]
    question = (q["question"] or "").strip()
    if not org:
        return _none("no_company")              # a turn with no company is never read
    if (_resting.get("*") or now) > now:
        return dict(_none("no_map"), kept=True)  # the map is not built: nothing is read until the rest is over
    kept = _settled.get(qid)
    if kept is not None and kept[0] > now:
        return dict(_none(kept[1]), kept=True)  # this question's read ended with no values a moment ago: not asked again
    try:
        ctx = await _turn_context(q)
        if ctx is None:
            log.warning("trace_lab.auto_expected.turn_of_another_company", question_id=qid)
            return {"state": "wait"}
        nodes = ctx["nodes"]
        propose = rnd["proposals"] > 0 and bool(question)           # never a proposal for a blank question
        got = await records_queries.queries_for(nodes, question, propose=propose)
    except Exception as exc:  # noqa: BLE001 - a failed read, tried again later
        log.warning("trace_lab.auto_expected.queries_failed", question_id=qid, error=str(exc)[:200])
        return _none("read_failed", qid, now)
    # Only a planner call spends the budget: a question that is not about records, or has a query already, does not.
    if propose and (any(x["source"] == "proposed" for x in got["queries"]) or got["none"] == "planner_unavailable"
                    or (got["none"] == "not_about_records" and records_queries.data_tools(nodes))):
        rnd["proposals"] -= 1
    queries = [x for x in got["queries"] if x["source"] != "answer_key" or answer_key.enabled()]
    if not queries:
        reason = None if got["queries"] else got["none"]
        if reason is None and not got["queries"]:
            if not question:
                reason = "no_question"
            elif not propose:
                # The budget for a proposal is used: a question the planner would be asked about waits for the next round.
                if not (nodes and not records_queries.data_tools(nodes)):
                    return {"state": "wait"}
                reason = "not_about_records"
        if reason == "not_about_records" and _called_records(nodes):
            reason = "records_plan_unreadable"      # the turn used the records tool, and its plan could not be found
        elif reason == "not_about_records" and not ctx["filed"]:
            reason = "no_trace"                     # nothing is known of the turn's tools until its trace is filed
        return _none(reason, qid, now)
    try:
        asker = await asker_scope(org, ctx["user_id"])
    except Exception as exc:  # noqa: BLE001 - the asker is not guessed: a failed read, tried again later
        log.warning("trace_lab.auto_expected.asker_failed", question_id=qid, error=str(exc)[:200])
        return _none("read_failed", qid, now)
    scope = None
    if asker["scope"] == "asker":
        if asker["building_ids"] is not None and not asker["building_ids"]:
            return _none("asker_has_no_buildings", qid, now)    # allocated to nothing: Hoistra refuses such a read too
        scope = {"building_ids": asker["building_ids"], "email": asker["email"]}
    blocks = []
    try:
        if rnd.get("onto") is None:
            rnd["onto"] = await records_run.load_map()
        onto = rnd["onto"]
        for item in queries:
            ask = (item.get("question") or question).strip() or question
            try:
                run = await records_run.run_query(item["plan"], question=ask, org=org, scope=scope)
            except ValueError as exc:               # "the plan does not fit this database": the same on every try
                log.info("trace_lab.auto_expected.plan_does_not_fit", question_id=qid, why=str(exc)[:200])
                return _none("plan_does_not_fit", qid, now)
            plan = run.get("plan") or item["plan"]  # the plan as run: the steps can refocus it (Ruling 11)
            facts = records_values.values_of(onto, plan, run)
            recorded = records_queries.recorded_sql(nodes, item.get("tool_id")) if item.get("tool_id") else []
            blocks.append({
                "source": item["source"], "question": ask, "plan": plan, "found": bool(run.get("found")),
                "unresolved": list(run.get("unresolved") or []), "notes": list(run.get("notes") or []), "facts": facts,
                "receipts": list(run.get("receipts") or []), "read_at": run.get("read_at"),
                "sentence": records_values.sentence_of(onto, plan, run, facts),
                "scope": asker["scope"], "scope_email": asker["email"],
                "changed": records_values.changed_since(run.get("receipts") or [], recorded)})
    except records_run.NotReady as exc:
        log.info("trace_lab.auto_expected.not_ready", why=str(exc)[:200])
        return _none("no_map", qid, now)
    except oq.NotAllowed:
        return _none("asker_has_no_buildings", qid, now)
    except Exception as exc:  # noqa: BLE001 - the SPEC.md draft is the fallback
        log.info("trace_lab.auto_expected.unread", question_id=qid, why=str(exc)[:200])
        return _none("read_failed", qid, now)
    evidence = answer_key.jsonable({"drafted_from": "records", "draft": DRAFT_VERSION, "read_at": blocks[-1]["read_at"],
                                    "queries": blocks})
    return {"state": "values", "evidence": evidence,
            "expected": " ".join(b["sentence"] for b in blocks)[:runs.EXPECTED_MAX]}
