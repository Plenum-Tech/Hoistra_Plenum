"""Expected answers drafted from SPEC.md for live questions (14 Nov 2026, Hussain: "I want the system to add it
automatically when I ask a question on the live Hoistra platform ... an expected answer in the trace based on the spec
file"; "only for the live answers, don't change anything in the flow from Codex").

    live question synced (sync_live) --(draft_for_live: one model call)--> expected answer, source coding_agent
    --(a person: Accept on the session page, or Edit)--> counts (runs.CURRENT_EXPECTED_WHERE)

What is drafted is what a correct answer must DO under the approved SPEC.md, citing the rules that apply. The model is
given the rules and the question only: never Hoistra's own answer, and it does not know the company's records, so it
writes no values. Candidates are questions of live day runs asked by hand (not Codex scenario turns), however old, with
no expected answer of any source; at most PER_ROUND model calls a round. Nothing is drafted until a version of SPEC.md is
approved. A failed call stores nothing and the question is tried next round. TRACE_LAB_AUTO_EXPECTED=false turns it off.

Every question gets one (18 Nov 2026, Hussain: "i need expected ans on each question"). The first prompt let the model
write "No rule of the specification applies to this question." as the whole expected answer, and that placeholder then
counted as an answer, so the question was never drafted again. Now the model always writes what a correct answer to
that question contains, citing rules only when some apply, and a draft nobody touched is drafted again (a new version;
the old one stays as history) when it was written by an older prompt (DRAFT_VERSION) or from an older SPEC.md. New
and old questions share a round, newest asked first. A person's answer, an accepted one or a reviewed one is
never drafted over.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import structlog
from sqlalchemy import text

from . import agent, answer_key, db, loop, runs
from .annotations import _limits

log = structlog.get_logger(__name__)

PER_ROUND = 20
#: The prompt a SPEC.md draft was written with, stored in its evidence ("draft"). An untouched draft written by an older
#: prompt is drafted again. 1 (no "draft" key): the prompt that could answer "no rule of the specification applies".
DRAFT_VERSION = 2
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


def enabled() -> bool:
    return (os.environ.get(ENV_SWITCH) or "true").strip().lower() not in ("false", "0", "no", "off")


def _prompt(spec: dict[str, Any], question: str) -> str:
    rules = "\n\n".join(f"### {r.get('code')} · {r.get('title') or 'Rule'}\n{str(r.get('text') or '').strip()}"
                        for r in spec.get("rules") or [])
    return f"SPEC.md, version {spec.get('version')}:\n\n{rules}\n\nThe question:\n{question.strip()}"


async def draft_for_live(org: str | None = None, *, now: datetime | None = None,
                         limit: int = PER_ROUND) -> dict[str, Any]:
    """Draft an expected answer for each live question that has none, then draft again the untouched drafts that are out
    of date (see the module). Returns drafted (new questions), redrafted, failed (model calls that did not answer),
    keyed (values from the answer key), replaced (SPEC.md drafts the key's values replaced) and skipped (why nothing
    was tried, else None)."""
    if not enabled():
        return {"drafted": 0, "failed": 0, "keyed": 0, "replaced": 0, "redrafted": 0,
                "skipped": "Automatic expected answers are turned off."}
    now = now or datetime.now(timezone.utc)
    live = ["r.kind = 'production'", f"NOT {runs.SCENARIO_Q}"]
    p: dict[str, Any] = {"lim": max(1, int(limit))}
    if org is not None:
        live.append("q.organization_id = CAST(:org AS uuid)")
        p["org"] = org
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
        stale = (await conn.execute(text(_STALE_SPEC_DRAFTS.format(where=' AND '.join(live))), p)).mappings().all()
    codes = loop.rule_codes(spec) if spec else set()
    tally = {"drafted": 0, "failed": 0, "keyed": 0, "redrafted": 0}
    replaced = await _replace_spec_drafts(stale, now)
    outdated: list[Any] = []
    if spec is not None:
        async with db.lab_engine().connect() as conn:
            outdated = list((await conn.execute(text(_OUTDATED_SPEC_DRAFTS.format(where=' AND '.join(live))), dict(
                p, dv=str(DRAFT_VERSION), sv=str(spec.get("version"))))).mappings().all())
    # The newest questions first, new or a redraft (18 Nov 2026): a placeholder on what people just asked is not left
    # behind a backlog of older questions that never had one.
    work = sorted([(q, None) for q in todo] + [(q, q["draft_id"]) for q in outdated],
                  key=lambda x: (x[0]["asked_at"], x[0]["id"]), reverse=True)[:p["lim"]]
    for q, replaces in work:
        if await _draft_one(q, spec, codes, now, tally, replaces=replaces):
            tally["redrafted" if replaces else "drafted"] += 1
    if tally["drafted"] or tally["redrafted"] or replaced:
        log.info("trace_lab.auto_expected.drafted", drafted=tally["drafted"], redrafted=tally["redrafted"],
                 keyed=tally["keyed"] + replaced, failed=tally["failed"], replaced=replaced)
    return {"drafted": tally["drafted"], "failed": tally["failed"], "keyed": tally["keyed"] + replaced,
            "replaced": replaced, "redrafted": tally["redrafted"],
            "skipped": None if spec else "No version of SPEC.md is approved yet: only answer-key questions are drafted."}


async def _draft_one(q: Any, spec: dict[str, Any] | None, codes: set[str], now: datetime, tally: dict[str, int], *,
                     replaces: str | None) -> bool:
    """One question's expected answer: the answer key's values when it knows the question, else a SPEC.md draft (one
    model call). Stored as a new version over `replaces` (an untouched out-of-date draft), or as the question's first
    answer when `replaces` is None. False when nothing was stored: no spec and no key, the model failed (tried next
    round), or a person or another round got there first."""
    # The answer key first (16 Nov 2026): values read from the records, each with its receipt.
    evidence = await _from_key(q, now)
    if evidence is not None:
        expected = evidence.pop("sentence")
        keyed = True
    elif spec is None:
        return False                            # no key for it, and nothing approved to draft it from yet
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


# A question whose newest expected answer is still the coding agent's SPEC.md draft, untouched: nobody accepted, edited or
# reviewed it. If the answer key now knows the question (its wording was taught after the draft), the values replace it.
_STALE_SPEC_DRAFTS = (
    "SELECT q.id::text AS id, q.question, q.organization_id::text AS org, q.asked_at, e.id::text AS draft_id"
    " FROM tracelab.lab_questions q JOIN tracelab.lab_runs r ON r.id = q.run_id"
    " JOIN LATERAL (SELECT x.id, x.source, x.evidence FROM tracelab.lab_expected_answers x WHERE x.question_id = q.id"
    "   ORDER BY x.created_at DESC, x.id DESC LIMIT 1) e ON true"
    " WHERE {where} AND e.source = 'coding_agent' AND e.evidence->>'drafted_from' = 'SPEC.md'"
    " AND NOT EXISTS (SELECT 1 FROM tracelab.lab_reviews v WHERE v.subject_kind = 'expected_answer' AND v.subject_id = e.id)"
    " ORDER BY q.asked_at DESC, q.id LIMIT :lim")

# An untouched SPEC.md draft that is out of date: written by an older prompt (DRAFT_VERSION; none stored = 1, the prompt
# that could answer "no rule of the specification applies") or from another version of SPEC.md than the approved one.
_OUTDATED_SPEC_DRAFTS = _STALE_SPEC_DRAFTS.replace(
    " ORDER BY q.asked_at", " AND (COALESCE(e.evidence->>'draft', '1') <> :dv"
                            " OR COALESCE(e.evidence->>'spec_version', '') <> :sv) ORDER BY q.asked_at")
assert _OUTDATED_SPEC_DRAFTS.count(":dv") == 1


async def _replace_spec_drafts(stale: Any, now: datetime) -> int:
    """Put the answer key's values over untouched SPEC.md drafts the key now knows. A new version, so the draft stays as
    history; never over a person's answer, an accepted one or a reviewed one (re-checked under the sync lock)."""
    replaced = 0
    for q in stale:
        if not answer_key.enabled() or answer_key.match(q["question"]) is None:
            continue
        evidence = await _from_key(q, now)
        if evidence is None:
            continue                            # the key could not read it: the SPEC draft stays
        expected = evidence.pop("sentence")
        async with db.lab_engine().begin() as conn:
            await _limits(conn)
            await conn.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": runs.SYNC_LOCK})
            await conn.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": runs.EXPECTED_LOCK})
            if not await _still_untouched(conn, q["id"], q["draft_id"]):
                continue                        # a person, or another round, got there first
            await runs._insert_expected(conn, AUTHOR, question_id=q["id"], expected=expected, expected_id=None,
                                        evidence=dict(evidence, replaced_draft=q["draft_id"]), source="coding_agent")
            replaced += 1
    return replaced


#: A key that could not read a question rests this long before it is tried again (16 Nov 2026 review): the round runs
#: every minute, each read loads the records' catalogue, and the read-only pool is small. When Hoistra's map of the
#: records is not built yet, every key rests ("*").
REST = timedelta(minutes=30)
_resting: dict[str, datetime] = {}


def _rests(qid: str, now: datetime) -> bool:
    return any(_resting.get(k) is not None and _resting[k] > now for k in ("*", qid))


async def _from_key(q: Any, now: datetime) -> dict[str, Any] | None:
    """The answer key's expected answer for this question (answer_key), or None: no key knows it, the key is off, the
    records could not be read, or they do not hold a name the question gives - then the SPEC.md draft is used."""
    found = answer_key.match(q["question"]) if answer_key.enabled() else None
    if found is None or not q["org"] or _rests(q["id"], now):
        return None
    key, params = found
    try:
        out = await answer_key.run(key, params, q["org"])
    except answer_key.NotReady as exc:
        _resting["*"] = now + REST
        log.info("trace_lab.answer_key.not_ready", why=str(exc)[:200])
        return None
    except Exception as exc:  # noqa: BLE001 - the SPEC.md draft is the fallback
        _resting[q["id"]] = now + REST
        log.info("trace_lab.answer_key.unread", question_id=q["id"], key=key.id, why=str(exc)[:200])
        return None
    if not out.get("found") or not out.get("facts"):
        _resting[q["id"]] = now + REST
        return None
    return {"drafted_from": "answer_key", "key": key.id, "title": key.title, "params": params,
            "facts": out["facts"], "receipts": out["receipts"], "read_at": out.get("read_at"), "plan": out.get("plan"),
            "sentence": answer_key.sentence(key, params, out["facts"])[:runs.EXPECTED_MAX]}
