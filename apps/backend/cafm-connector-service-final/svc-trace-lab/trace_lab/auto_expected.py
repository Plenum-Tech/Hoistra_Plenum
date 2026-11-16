"""Expected answers drafted from SPEC.md for live questions (14 Nov 2026, Hussain: "I want the system to add it
automatically when I ask a question on the live Hoistra platform ... an expected answer in the trace based on the spec
file"; "only for the live answers, don't change anything in the flow from Codex").

    live question synced (sync_live) --(draft_for_live: one model call)--> expected answer, source coding_agent
    --(a person: Accept on the session page, or Edit)--> counts (runs.CURRENT_EXPECTED_WHERE)

What is drafted is what a correct answer must DO under the approved SPEC.md, citing the rules that apply. The model is
given the rules and the question only: never Hoistra's own answer, and it does not know the company's records, so it
writes no values. Candidates are questions of live day runs asked by hand (not Codex scenario turns), in the last DAYS
days, with no expected answer of any source; at most PER_ROUND a round. Nothing is drafted until a version of SPEC.md is
approved. A failed call stores nothing and the question is tried next round. TRACE_LAB_AUTO_EXPECTED=false turns it off.
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
DAYS = 7
MAX_TOKENS = 600
ENV_SWITCH = "TRACE_LAB_AUTO_EXPECTED"
#: Who a draft is written by: the coding agent, as its other suggestions are.
AUTHOR = SimpleNamespace(email=agent.AGENT_EMAIL, user_id=None)

SYSTEM = ("You write the expected answer to one question put to a facilities-management assistant, from its answer "
          "specification (SPEC.md) only. You do not know the company's records and you never see the assistant's "
          "answer, so you write what a correct answer must do and contain, never values. Name the codes of the rules "
          "that apply to this question. When no rule applies, say that no rule of the specification applies to it "
          "and name no codes. Plain sentences, at most six.")
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
    """Draft an expected answer for each live question that has none (see the module). Returns drafted, failed (model
    calls that did not answer) and skipped (why nothing was tried, else None)."""
    if not enabled():
        return {"drafted": 0, "failed": 0, "keyed": 0, "replaced": 0, "skipped": "Automatic expected answers are turned off."}
    now = now or datetime.now(timezone.utc)
    where = ["r.kind = 'production'", f"NOT {runs.SCENARIO_Q}", "q.asked_at >= :since",
             "NOT EXISTS (SELECT 1 FROM tracelab.lab_expected_answers e WHERE e.question_id = q.id)"]
    p: dict[str, Any] = {"since": now - timedelta(days=DAYS), "lim": max(1, int(limit))}
    if org is not None:
        where.append("q.organization_id = CAST(:org AS uuid)")
        p["org"] = org
    async with db.lab_engine().connect() as conn:
        spec = await loop.approved_spec(conn)
        if spec is not None and not spec.get("rules"):
            spec = None
        todo = (await conn.execute(text(
            "SELECT q.id::text AS id, q.question, q.organization_id::text AS org FROM tracelab.lab_questions q"
            f" JOIN tracelab.lab_runs r ON r.id = q.run_id WHERE {' AND '.join(where)}"
            " ORDER BY q.asked_at DESC, q.id LIMIT :lim"), p)).mappings().all()
        stale = (await conn.execute(text(_STALE_SPEC_DRAFTS.format(where=' AND '.join(where[:3] + where[4:]))), p)).mappings().all()
    codes = loop.rule_codes(spec) if spec else set()
    drafted = failed = keyed = 0
    replaced = await _replace_spec_drafts(stale, now)
    for q in todo:
        # The answer key first (16 Nov 2026): values read from the records, each with its receipt.
        evidence = await _from_key(q, now)
        if evidence is not None:
            expected = evidence.pop("sentence")
            keyed += 1
        elif spec is None:
            continue                            # no key for it, and nothing approved to draft it from yet
        else:
            try:
                out = await agent._ask(SYSTEM, _prompt(spec, q["question"] or ""), SCHEMA, MAX_TOKENS)
                expected = str((out or {}).get("expected") or "").strip()[:runs.EXPECTED_MAX]
                if not expected:
                    raise ValueError("the model wrote no expected answer")
            except Exception as exc:  # noqa: BLE001 - one question's failure never stops the others; tried next round
                failed += 1
                log.warning("trace_lab.auto_expected.draft_failed", question_id=q["id"], error=str(exc)[:200])
                continue
            cited = [c for c in dict.fromkeys(str(x).strip().upper() for x in (out.get("rules") or [])) if c in codes]
            evidence = {"drafted_from": "SPEC.md", "spec_version": spec.get("version"), "rules": cited}
        async with db.lab_engine().begin() as conn:
            await _limits(conn)
            await conn.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": runs.SYNC_LOCK})
            await conn.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": runs.EXPECTED_LOCK})
            taken = (await conn.execute(text(
                "SELECT 1 FROM tracelab.lab_expected_answers WHERE question_id = CAST(:q AS uuid) LIMIT 1"),
                {"q": q["id"]})).first()
            if taken:
                continue                        # a person, or another round, wrote one while the model was asked
            await runs._insert_expected(conn, AUTHOR, question_id=q["id"], expected=expected, expected_id=None,
                                        evidence=evidence, source="coding_agent")
            drafted += 1
    if drafted or replaced:
        log.info("trace_lab.auto_expected.drafted", drafted=drafted, keyed=keyed + replaced, failed=failed, replaced=replaced)
    return {"drafted": drafted, "failed": failed, "keyed": keyed + replaced, "replaced": replaced,
            "skipped": None if spec else "No version of SPEC.md is approved yet: only answer-key questions are drafted."}


# A question whose newest expected answer is still the coding agent's SPEC.md draft, untouched: nobody accepted, edited or
# reviewed it. If the answer key now knows the question (its wording was taught after the draft), the values replace it.
_STALE_SPEC_DRAFTS = (
    "SELECT q.id::text AS id, q.question, q.organization_id::text AS org, e.id::text AS draft_id"
    " FROM tracelab.lab_questions q JOIN tracelab.lab_runs r ON r.id = q.run_id"
    " JOIN LATERAL (SELECT x.id, x.source, x.evidence FROM tracelab.lab_expected_answers x WHERE x.question_id = q.id"
    "   ORDER BY x.created_at DESC, x.id DESC LIMIT 1) e ON true"
    " WHERE {where} AND e.source = 'coding_agent' AND e.evidence->>'drafted_from' = 'SPEC.md'"
    " AND NOT EXISTS (SELECT 1 FROM tracelab.lab_reviews v WHERE v.subject_kind = 'expected_answer' AND v.subject_id = e.id)"
    " ORDER BY q.asked_at DESC, q.id LIMIT :lim")


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
            newest = (await conn.execute(text(
                "SELECT x.id::text FROM tracelab.lab_expected_answers x WHERE x.question_id = CAST(:q AS uuid)"
                " ORDER BY x.created_at DESC, x.id DESC LIMIT 1"), {"q": q["id"]})).scalar_one_or_none()
            reviewed = (await conn.execute(text(
                "SELECT 1 FROM tracelab.lab_reviews v WHERE v.subject_kind = 'expected_answer'"
                " AND v.subject_id = CAST(:e AS uuid) LIMIT 1"), {"e": q["draft_id"]})).first()
            if newest != q["draft_id"] or reviewed:
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
