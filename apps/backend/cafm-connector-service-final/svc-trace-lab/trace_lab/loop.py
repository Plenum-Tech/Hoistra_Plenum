"""The correction loop of the review page (3 Nov 2026, migration 0013).

    traces -> human notes -> failure modes -> the answer specification (SPEC.md) -> the prompt, skill or code
    correction -> regression runs -> a rerun, compared with what was answered before

Who decides what:

* People write the notes (step_labels, always under the signed-in person) and the labels (lab_label_cells source
  'manual').
* A coding agent may PROPOSE: a failure mode (a lab_failure_modes row written without a person, created_by null), a
  label (a lab_label_cells row with source 'coding_agent') or an expected answer (lab_expected_answers source
  'coding_agent'), and a specification version. None of it counts until a person decides: a failure mode or an
  expected answer is approved or rejected once (lab_reviews), and an agent's label is only ever a suggestion that a
  person accepts by setting the same value themselves.
* The specification in force is the newest APPROVED version. Its rules are numbered (S-1, S-2 ...) and each names the
  failure mode it guards against. It downloads as SPEC.md; the coding agent commits it and records each correction
  against the rule or mode it fixes (lab_corrections).
* An expected answer is derived from the approved specification and the company's records: its evidence names a rule
  of the specification in force (`spec_rule`) and the records it was read from (`data`). It is never the model's own
  earlier answer: one that repeats an answer recorded for the question is refused.
* A regression run of a failure mode holds the questions people marked positive for it, with their derived expected
  answers and the answer that was marked; asking them again adds the new answer beside the old one, and the
  comparison checks both against the expected values.

Every table is append-only; nothing here writes to Hoistra's database.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from . import conversations, db, golden, grading, labelling, runs
from .annotations import _limits
from .review_errors import Forbidden, Invalid, NotFound

log = structlog.get_logger(__name__)

KINDS = ("failure_mode", "expected_answer", "spec_version")
DECISIONS = ("approved", "rejected")
CORRECTION_KINDS = ("prompt", "skill", "code", "data", "spec")
RULE_CODE = re.compile(r"^S-(\d{1,4})$")
RULES_MAX = 200
RULE_TEXT_MAX = 4000
INTRO_MAX = 20000
NOTE_MAX = 2000
REGRESSION_MAX = 50
RERUN_MAX = 5
#: How alike an expected answer may be to an answer recorded for its question before it is refused as a copy.
COPY_RATIO = 0.85
#: Browser events one person may send in a minute; more are dropped, never an error.
EVENTS_PER_MINUTE = 120
EVENT_KINDS = ("answer_shown", "error", "stopped")
SPEC_TITLE = "Hoistra answer specification"
#: Spaces a regression run may be asked in (agents/spaces.py keys).
SPACES = ("full", "ops", "comp", "assets")


def _iso(v: Any) -> str | None:
    return v.isoformat() if isinstance(v, datetime) else (str(v) if v is not None else None)


def _uuid(v: Any) -> str | None:
    try:
        return str(uuid.UUID(str(v))) if v not in (None, "") else None
    except ValueError:
        return None


def _json(v: Any) -> Any:
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


# ── decisions ──────────────────────────────────────────────────────────────────────────────────

async def _decisions(conn: Any, kind: str, ids: list[str]) -> dict[str, dict[str, Any]]:
    if not ids:
        return {}
    rows = (await conn.execute(text(
        "SELECT subject_id::text AS subject_id, decision, note, reviewer_email, created_at FROM tracelab.lab_reviews "
        "WHERE subject_kind = :kind AND subject_id = ANY(CAST(:ids AS uuid[]))"), {"kind": kind, "ids": ids})).mappings().all()
    return {r["subject_id"]: {"decision": r["decision"], "note": r.get("note"), "by": r.get("reviewer_email"),
                              "at": _iso(r["created_at"])} for r in rows}


async def _insert_decision(conn: Any, m: labelling.Member, kind: str, subject_id: str, decision: str,
                           note: str | None) -> None:
    try:
        async with conn.begin_nested():
            await conn.execute(text(
                "INSERT INTO tracelab.lab_reviews (subject_kind, subject_id, decision, note, reviewer_id, reviewer_email, "
                "reviewer_role) VALUES (:kind, CAST(:sid AS uuid), :decision, :note, CAST(:by AS uuid), :email, :role)"),
                {"kind": kind, "sid": subject_id, "decision": decision, "note": note, "by": m.user_id, "email": m.email,
                 "role": m.role})
    except IntegrityError:
        raise Invalid("already_decided", "Someone has already decided this proposal.") from None


# ── failure modes: what counts, and what is only proposed ─────────────────────────────────────────

async def _mode_rows(conn: Any) -> list[dict[str, Any]]:
    return [dict(r) for r in (await conn.execute(text(
        "SELECT coalesce(lineage_id, id)::text AS lineage_id, id::text AS id, name, definition, tombstone, "
        "created_by::text AS created_by, created_by_email, created_at FROM tracelab.lab_failure_modes "
        "ORDER BY created_at, id"))).mappings().all()]


async def resolve_modes(conn: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(live, proposed). A version counts when a person wrote it on the page, or a person approved it. The live mode of
    a lineage is its newest counting version (gone when that is a retirement); a newer version nobody has decided is a
    proposal beside it: a new mode, a change, or a retirement."""
    rows = await _mode_rows(conn)
    reviews = await _decisions(conn, "failure_mode", [r["id"] for r in rows if not r["created_by"]])
    by: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by.setdefault(r["lineage_id"], []).append(r)
    live: list[dict[str, Any]] = []
    proposed: list[dict[str, Any]] = []
    for lin, versions in by.items():
        counting = [v for v in versions if v["created_by"] or reviews.get(v["id"], {}).get("decision") == "approved"]
        eff = counting[-1] if counting else None
        newest = versions[-1]
        pending = (newest if not newest["created_by"] and newest["id"] not in reviews
                   and (eff is None or newest["created_at"] >= eff["created_at"]) and newest is not eff else None)
        since = _iso(versions[0]["created_at"])
        if eff is not None and not eff["tombstone"]:
            live.append({"lineage_id": lin, "id": eff["id"], "name": eff["name"], "definition": eff.get("definition"),
                         "by": eff.get("created_by_email"), "at": _iso(eff["created_at"]), "since": since,
                         "status": "confirmed", "change_proposed": pending is not None, "_first": versions[0]["created_at"]})
        if pending is not None:
            kind = "retire" if pending["tombstone"] else ("change" if eff is not None and not eff["tombstone"] else "new")
            proposed.append({"lineage_id": lin, "id": pending["id"], "kind": kind, "name": pending.get("name"),
                             "definition": pending.get("definition"), "by": pending.get("created_by_email"),
                             "at": _iso(pending["created_at"]),
                             "current": None if eff is None or eff["tombstone"] else
                             {"name": eff["name"], "definition": eff.get("definition")}})
    live.sort(key=lambda x: (x.pop("_first"), x["lineage_id"]))
    proposed.sort(key=lambda x: x["at"] or "")
    return live, proposed


async def live_modes(conn: Any) -> list[dict[str, Any]]:
    return (await resolve_modes(conn))[0]


# ── expected answers ─────────────────────────────────────────────────────────────────────────────

#: The current expected answer of question `q`: the newest one a person wrote or imported, or a coding agent's that a
#: person approved.
CURRENT_EXPECTED_WHERE = runs.CURRENT_EXPECTED_WHERE


def derivation(evidence: Any, rule_codes: set[str]) -> dict[str, Any]:
    """Whether an expected answer says what it was derived from: a rule of the specification in force and the records
    it was read from."""
    ev = _json(evidence)
    ev = ev if isinstance(ev, dict) else {}
    rule = str(ev.get("spec_rule") or "").strip().upper() or None
    data = ev.get("data") if ev.get("data") not in (None, "", [], {}) else ev.get("reference")
    data = data if data not in (None, "", [], {}) else None
    problems = []
    if not rule:
        problems.append("names no rule of the specification")
    elif rule not in rule_codes:
        problems.append(f"{rule} is not a rule of the approved specification")
    if data is None:
        problems.append("names no company records it was read from")
    return {"derived": not problems, "spec_rule": rule, "data": data, "problems": problems}


def _norm(s: Any) -> str:
    s = re.sub(r"[*_`#|>\-]+", " ", str(s or "").lower())
    return " ".join(s.split())


def copies(expected: str, answers: list[str]) -> bool:
    """True when the expected answer is, near enough, an answer the model gave for the question."""
    e = _norm(expected)
    if not e:
        return False
    for a in answers:
        a = _norm(a)
        if not a:
            continue
        if e == a or (len(e) >= 80 and e in a):
            return True
        sm = SequenceMatcher(None, e, a, autojunk=False)
        if sm.real_quick_ratio() >= COPY_RATIO and sm.quick_ratio() >= COPY_RATIO and sm.ratio() >= COPY_RATIO:
            return True
    return False


async def turn_answers(turn_ids: list[str]) -> dict[str, dict[str, Any]]:
    """{turn_id: {answer, cards}} for recorded answers: Trace Lab's own evidence (scenario, golden, regression and kept
    copies) first, then the chat's records, read-only."""
    ids = [t for t in dict.fromkeys(turn_ids) if t]
    out: dict[str, dict[str, Any]] = {}
    if not ids:
        return out
    async with db.lab_engine().connect() as conn:
        for r in (await conn.execute(text(
                "SELECT turn_id, turn FROM tracelab.evidence_turns WHERE turn_id = ANY(:ids)"), {"ids": ids})).mappings().all():
            t = _json(r["turn"]) or {}
            out[r["turn_id"]] = {"answer": str(t.get("answer") or t.get("final_answer") or ""),
                                 "cards": t.get("cards") or []}
    rest = [t for t in ids if t not in out and not t.startswith("browser:")]
    if rest:
        try:
            async with conversations._read() as conn:
                for r in (await conn.execute(text(
                        f"SELECT turn_id, answer FROM {conversations.TURNS} WHERE turn_id = ANY(:ids)"),
                        {"ids": rest})).mappings().all():
                    out[r["turn_id"]] = {"answer": str(r.get("answer") or ""), "cards": []}
        except Exception as exc:  # noqa: BLE001 - an unreadable record is an answer not seen, not a failure
            log.warning("loop.turn_answers_failed", error=str(exc)[:200])
    return out


async def question_turns(conn: Any, question_id: str) -> list[str]:
    return [r[0] for r in (await conn.execute(text(
        "SELECT turn_id FROM tracelab.lab_question_traces WHERE question_id = CAST(:q AS uuid) ORDER BY created_at, turn_id"),
        {"q": question_id})).all()]


async def refuse_copy(conn: Any, question_id: str, expected: str) -> None:
    answers = await turn_answers(await question_turns(conn, question_id))
    if copies(expected, [a["answer"] for a in answers.values()]):
        raise Invalid("copied_from_answer", "That is the answer the model gave. An expected answer is written from the "
                                            "specification and the company's records, not from the model's answer.")


# ── the specification ────────────────────────────────────────────────────────────────────────────

def _version_out(r: Any, review: dict[str, Any] | None) -> dict[str, Any]:
    return {"id": r["id"], "version": r["version_no"], "intro": r.get("intro"), "rules": _json(r["rules"]) or [],
            "body": r["body"], "source": r["source"], "by": r.get("created_by_email"), "at": _iso(r["created_at"]),
            "based_on": r.get("based_on"),
            "status": (review or {}).get("decision") or "proposed", "review": review}


async def _versions(conn: Any) -> list[dict[str, Any]]:
    rows = (await conn.execute(text(
        "SELECT id::text AS id, version_no, intro, rules, body, based_on::text AS based_on, source, created_by_email, "
        "created_at FROM tracelab.lab_spec_versions ORDER BY version_no DESC"))).mappings().all()
    reviews = await _decisions(conn, "spec_version", [r["id"] for r in rows])
    return [_version_out(r, reviews.get(r["id"])) for r in rows]


async def approved_spec(conn: Any) -> dict[str, Any] | None:
    return next((v for v in await _versions(conn) if v["status"] == "approved"), None)


def rule_codes(spec: dict[str, Any] | None) -> set[str]:
    return {str(r.get("code")).upper() for r in (spec or {}).get("rules") or [] if r.get("code")}


def render(version: int | None, intro: str | None, rules: list[dict[str, Any]], modes: dict[str, str],
           *, approved_by: str | None = None, approved_at: str | None = None) -> str:
    """SPEC.md: the rules people approved, each with the failure mode it guards against."""
    head = f"# {SPEC_TITLE}\n\n"
    head += (f"Version {version}" if version else "Draft") + (
        f" · approved {approved_at[:10]} by {approved_by}" if approved_by and approved_at else "") + "\n\n"
    head += ("Written from the failure modes people confirmed in Hoist Trace Lab. Expected answers are derived from "
             "these rules and the company's records, never from an answer the model gave.\n\n")
    if intro:
        head += intro.strip() + "\n\n"
    out = [head + "## Rules\n"]
    for r in rules:
        out.append(f"\n### {r['code']} · {r.get('title') or modes.get(r.get('mode_lineage_id') or '', '') or 'Rule'}\n\n"
                   f"{str(r.get('text') or '').strip()}\n")
        if r.get("mode_lineage_id"):
            out.append(f"\nFailure mode: {modes.get(r['mode_lineage_id'], 'retired mode')} (`{r['mode_lineage_id']}`)\n")
    return "".join(out)


async def spec_state(m: labelling.Member) -> dict[str, Any]:
    labelling.require_active(m)
    async with db.lab_engine().connect() as conn:
        versions = await _versions(conn)
    approved = next((v for v in versions if v["status"] == "approved"), None)
    return {"approved": approved, "pending": [v for v in versions if v["status"] == "proposed"],
            "history": versions}


async def spec_markdown(m: labelling.Member) -> str | None:
    labelling.require_active(m)
    async with db.lab_engine().connect() as conn:
        spec = await approved_spec(conn)
    return spec["body"] if spec else None


async def _positives(conn: Any, lineage: str | None = None) -> dict[str, list[str]]:
    """{mode lineage: [question ids]} that a PERSON marked positive (their newest value). Agents' suggestions never count."""
    rows = (await conn.execute(text("""
        SELECT mode, question_id FROM (
          SELECT DISTINCT ON (c.question_id, c.mode_lineage_id, coalesce(c.author_id::text, c.author_email))
                 c.mode_lineage_id::text AS mode, c.question_id::text AS question_id, c.value
            FROM tracelab.lab_label_cells c
           WHERE c.source = 'manual' AND (CAST(:lin AS uuid) IS NULL OR c.mode_lineage_id = CAST(:lin AS uuid))
           ORDER BY c.question_id, c.mode_lineage_id, coalesce(c.author_id::text, c.author_email), c.created_at DESC, c.id DESC
        ) x WHERE value = 'positive'"""), {"lin": lineage})).mappings().all()
    out: dict[str, list[str]] = {}
    for r in rows:
        out.setdefault(r["mode"], [])
        if r["question_id"] not in out[r["mode"]]:
            out[r["mode"]].append(r["question_id"])
    return out


async def draft_spec(m: labelling.Member) -> dict[str, Any]:
    """The next version to propose: the rules in force, and a new rule for each confirmed failure mode no rule covers yet,
    worded from the mode's definition. Nothing is stored; a person edits it and proposes it."""
    runs.require_taxonomist(m)
    async with db.lab_engine().connect() as conn:
        spec = await approved_spec(conn)
        modes = await live_modes(conn)
        pos = await _positives(conn)
    rules = [dict(r) for r in (spec or {}).get("rules") or []]
    covered = {r.get("mode_lineage_id") for r in rules}
    live_ids = {md["lineage_id"] for md in modes}
    for r in rules:
        if r.get("mode_lineage_id") and r["mode_lineage_id"] not in live_ids:
            r["mode_retired"] = True
    top = max([int(RULE_CODE.match(str(r.get("code"))).group(1)) for r in rules
               if RULE_CODE.match(str(r.get("code") or ""))] or [0])
    for md in modes:
        if md["lineage_id"] in covered:
            continue
        top += 1
        rules.append({"code": f"S-{top}", "mode_lineage_id": md["lineage_id"], "title": md["name"],
                      "text": ("An answer must not show this failure: "
                               + (md.get("definition") or md["name"]).strip().rstrip(".") + "."),
                      "new": True, "positives": len(pos.get(md["lineage_id"], []))})
    return {"based_on": spec["id"] if spec else None, "base_version": spec["version"] if spec else None,
            "intro": (spec or {}).get("intro"), "rules": rules}


def _clean_rules(rules: Any, live_ids: set[str]) -> list[dict[str, Any]]:
    if not isinstance(rules, list) or not rules:
        raise Invalid("no_rules", "A specification needs at least one rule.")
    if len(rules) > RULES_MAX:
        raise Invalid("too_many_rules", f"Keep the specification under {RULES_MAX} rules.")
    seen: set[str] = set()
    out = []
    for i, r in enumerate(rules, start=1):
        if not isinstance(r, dict):
            raise Invalid("bad_rule", f"Rule {i} is not readable.")
        code = str(r.get("code") or "").strip().upper()
        if not RULE_CODE.match(code):
            raise Invalid("bad_rule_code", f"Rule {i}: a rule code looks like S-1.")
        if code in seen:
            raise Invalid("duplicate_rule", f"{code} is used twice.")
        seen.add(code)
        body = runs._text(r.get("text"), RULE_TEXT_MAX, "rule", f"{code} has no text.")
        lin = _uuid(r.get("mode_lineage_id"))
        if r.get("mode_lineage_id") and lin not in live_ids and not r.get("mode_retired"):
            raise Invalid("mode_not_confirmed", f"{code} names a failure mode that is not confirmed.")
        out.append({"code": code, "mode_lineage_id": lin, "title": runs._opt(r.get("title"), 200, "title"), "text": body})
    return out


async def propose_spec(m: labelling.Member, *, rules: Any, intro: str | None = None,
                       based_on: str | None = None) -> dict[str, Any]:
    """A person proposes a version (from the draft, edited). It is the specification in force once approved."""
    runs.require_taxonomist(m)
    intro = runs._opt(intro, INTRO_MAX, "intro")
    async with db.lab_engine().begin() as conn:
        await _limits(conn)
        modes = await live_modes(conn)
        clean = _clean_rules(rules, {md["lineage_id"] for md in modes})
        names = {md["lineage_id"]: md["name"] for md in modes}
        body = render(None, intro, clean, names)
        row = (await conn.execute(text(
            "INSERT INTO tracelab.lab_spec_versions (intro, rules, body, based_on, source, created_by, created_by_email) "
            "VALUES (:intro, CAST(:rules AS jsonb), :body, CAST(:based AS uuid), 'manual', CAST(:by AS uuid), :email) "
            "RETURNING id::text AS id, version_no"),
            {"intro": intro, "rules": json.dumps(clean), "body": body, "based": _uuid(based_on), "by": m.user_id,
             "email": m.email})).mappings().one()
    return {"id": row["id"], "version": row["version_no"], "status": "proposed"}


# ── deciding proposals ──────────────────────────────────────────────────────────────────────────

async def proposals(m: labelling.Member) -> dict[str, Any]:
    """Everything waiting for a person: failure modes and specification versions (platform team and consultant decide),
    and expected answers of the questions the caller may see (curators decide)."""
    labelling.require_active(m)
    where, p = runs._scope(m, "r")
    async with db.lab_engine().connect() as conn:
        _live, modes = await resolve_modes(conn)
        versions = [v for v in await _versions(conn) if v["status"] == "proposed"]
        spec = await approved_spec(conn)
        rows = (await conn.execute(text(f"""
            SELECT e.id::text AS id, e.expected, e.expected_id, e.evidence, e.author_email, e.created_at,
                   r.run_no, q.seq_no, q.question, q.id::text AS question_id
              FROM tracelab.lab_expected_answers e JOIN tracelab.lab_questions q ON q.id = e.question_id
              JOIN tracelab.lab_runs r ON r.id = q.run_id
             WHERE e.source = 'coding_agent' AND {where}
               AND NOT EXISTS (SELECT 1 FROM tracelab.lab_reviews v WHERE v.subject_kind = 'expected_answer'
                                AND v.subject_id = e.id)
               AND NOT EXISTS (SELECT 1 FROM tracelab.lab_expected_answers n WHERE n.question_id = e.question_id
                                AND n.created_at > e.created_at AND n.source <> 'coding_agent')
             ORDER BY e.created_at DESC LIMIT 200"""), p)).mappings().all()
    codes = rule_codes(spec)
    expected = [{"id": r["id"], "question": runs.question_code(r["run_no"], r["seq_no"]), "question_text": r["question"],
                 "expected": r["expected"], "expected_id": r.get("expected_id"), "evidence": _json(r.get("evidence")),
                 "by": r.get("author_email"), "at": _iso(r["created_at"]),
                 "derivation": derivation(r.get("evidence"), codes)} for r in rows]
    return {"modes": modes, "spec": versions, "expected": expected,
            "can_decide_modes": m.active and m.all_companies, "can_decide_expected": runs.can_curate(m)}


async def decide(m: labelling.Member, kind: str, subject_id: str, *, decision: str, note: str | None = None) -> dict[str, Any]:
    """A person approves or rejects one proposal. Final: a second decision is refused."""
    if kind not in KINDS:
        raise NotFound("proposal_not_found", "That proposal is not in Trace Lab.")
    if decision not in DECISIONS:
        raise Invalid("bad_decision", "Choose approve or reject.")
    sid = _uuid(subject_id)
    if sid is None:
        raise NotFound("proposal_not_found", "That proposal is not in Trace Lab.")
    note = runs._opt(note, NOTE_MAX, "note")
    if kind in ("failure_mode", "spec_version"):
        runs.require_taxonomist(m)
    else:
        runs.require_curator(m)
    async with db.lab_engine().begin() as conn:
        await _limits(conn)
        if (await _decisions(conn, kind, [sid])).get(sid):
            raise Invalid("already_decided", "Someone has already decided this proposal.")
        if kind == "failure_mode":
            _live, pending = await resolve_modes(conn)
            if not any(x["id"] == sid for x in pending):
                raise NotFound("proposal_not_found", "That failure mode is not waiting for a decision.")
        elif kind == "spec_version":
            versions = await _versions(conn)
            v = next((x for x in versions if x["id"] == sid), None)
            if v is None or v["status"] != "proposed":
                raise NotFound("proposal_not_found", "That specification version is not waiting for a decision.")
            if decision == "approved":
                live_ids = {md["lineage_id"] for md in await live_modes(conn)}
                for r in v["rules"]:
                    if r.get("mode_lineage_id") and r["mode_lineage_id"] not in live_ids and not r.get("mode_retired"):
                        raise Invalid("mode_not_confirmed",
                                      f"{r['code']} names a failure mode that is not confirmed any more.")
        else:
            where, p = runs._scope(m, "r")
            row = (await conn.execute(text(
                "SELECT e.id::text AS id, e.expected, e.evidence, e.source, e.question_id::text AS question_id "
                "FROM tracelab.lab_expected_answers e JOIN tracelab.lab_questions q ON q.id = e.question_id "
                f"JOIN tracelab.lab_runs r ON r.id = q.run_id WHERE e.id = CAST(:sid AS uuid) AND {where}"),
                {**p, "sid": sid})).mappings().first()
            if row is None or row["source"] != "coding_agent":
                raise NotFound("proposal_not_found", "That expected answer is not waiting for a decision.")
            if decision == "approved":
                d = derivation(row["evidence"], rule_codes(await approved_spec(conn)))
                if not d["derived"]:
                    raise Invalid("not_derived", "An expected answer is approved only when it says which rule of the "
                                                 "approved specification and which company records it was derived "
                                                 "from: it " + "; ".join(d["problems"]) + ".")
                await refuse_copy(conn, row["question_id"], row["expected"])
        await _insert_decision(conn, m, kind, sid, decision, note)
    return {"kind": kind, "id": sid, "decision": decision}


# ── corrections ─────────────────────────────────────────────────────────────────────────────────

async def list_corrections(m: labelling.Member, *, mode: str | None = None) -> list[dict[str, Any]]:
    labelling.require_active(m)
    async with db.lab_engine().connect() as conn:
        rows = (await conn.execute(text(
            "SELECT id::text AS id, mode_lineage_id::text AS mode, spec_rule, kind, reference, description, source, "
            "created_by_email, created_at FROM tracelab.lab_corrections "
            "WHERE CAST(:lin AS uuid) IS NULL OR mode_lineage_id = CAST(:lin AS uuid) ORDER BY created_at DESC LIMIT 500"),
            {"lin": _uuid(mode)})).mappings().all()
    return [{"id": r["id"], "mode": r.get("mode"), "spec_rule": r.get("spec_rule"), "kind": r["kind"],
             "reference": r["reference"], "description": r.get("description"), "source": r["source"],
             "by": r.get("created_by_email"), "at": _iso(r["created_at"])} for r in rows]


async def add_correction(m: labelling.Member, *, kind: str, reference: str, mode: str | None = None,
                         spec_rule: str | None = None, description: str | None = None) -> dict[str, Any]:
    """Record the change made for a failure mode or a rule: a commit, a pull request, a prompt or skill file."""
    runs.require_taxonomist(m)
    if kind not in CORRECTION_KINDS:
        raise Invalid("bad_kind", "Choose prompt, skill, code, data or spec.")
    reference = runs._text(reference, 500, "reference", "Name the change: a commit, a pull request or a file.")
    description = runs._opt(description, 2000, "description")
    lin = _uuid(mode)
    rule = (spec_rule or "").strip().upper() or None
    if not lin and not rule:
        raise Invalid("no_target", "Say which failure mode or rule this change corrects.")
    async with db.lab_engine().begin() as conn:
        await _limits(conn)
        if lin and not any(md["lineage_id"] == lin for md in await live_modes(conn)):
            raise NotFound("mode_not_found", "That failure mode is not confirmed in Trace Lab.")
        if rule and rule not in rule_codes(await approved_spec(conn)):
            raise Invalid("rule_not_found", f"{rule} is not a rule of the approved specification.")
        row = (await conn.execute(text(
            "INSERT INTO tracelab.lab_corrections (mode_lineage_id, spec_rule, kind, reference, description, source, "
            "created_by, created_by_email) VALUES (CAST(:lin AS uuid), :rule, :kind, :ref, :descr, 'manual', "
            "CAST(:by AS uuid), :email) RETURNING id::text AS id, created_at"),
            {"lin": lin, "rule": rule, "kind": kind, "ref": reference, "descr": description, "by": m.user_id,
             "email": m.email})).mappings().one()
    return {"id": row["id"], "mode": lin, "spec_rule": rule, "kind": kind, "reference": reference,
            "description": description, "at": _iso(row["created_at"])}


# ── regression runs ─────────────────────────────────────────────────────────────────────────────

async def _current_expected(conn: Any, question_id: str) -> dict[str, Any] | None:
    r = (await conn.execute(text(
        "SELECT e.id::text AS id, e.expected, e.expected_id, e.evidence FROM tracelab.lab_expected_answers e "
        f"WHERE e.question_id = CAST(:q AS uuid) AND {CURRENT_EXPECTED_WHERE} ORDER BY e.created_at DESC, e.id DESC LIMIT 1"),
        {"q": question_id})).mappings().first()
    return dict(r) if r else None


async def make_regression(m: labelling.Member, lineage_id: str, *, company: str | None = None,
                          space: str = "full") -> dict[str, Any]:
    """A run of the questions people marked positive for a confirmed failure mode, in one company, each with its derived
    expected answer (when it has one) and the answer that was marked, ready to be asked again after a correction."""
    runs.require_curator(m)
    if space not in SPACES:
        raise Invalid("bad_space", "Choose the instance to ask in: full, ops, comp or assets.")
    lin = _uuid(lineage_id)
    org = _uuid(company) if m.all_companies else m.org
    if org is None:
        raise Invalid("company_required", "Choose the company whose questions make up the regression run.")
    async with db.lab_engine().begin() as conn:
        await _limits(conn)
        modes = {md["lineage_id"]: md for md in await live_modes(conn)}
        if lin not in modes:
            raise NotFound("mode_not_found", "That failure mode is not confirmed in Trace Lab.")
        spec = await approved_spec(conn)
        rule = next((r for r in (spec or {}).get("rules") or [] if r.get("mode_lineage_id") == lin), None)
        if rule is None:
            raise Invalid("not_in_spec", "Add this failure mode to the specification and have the version approved "
                                         "first: expected answers are derived from its rule.")
        codes = rule_codes(spec)
        qids = (await _positives(conn, lin)).get(lin, [])
        rows = (await conn.execute(text(
            "SELECT q.id::text AS id, q.question, q.followups, q.role, q.intent, r.run_no, q.seq_no "
            "FROM tracelab.lab_questions q JOIN tracelab.lab_runs r ON r.id = q.run_id "
            "WHERE q.id = ANY(CAST(:ids AS uuid[])) AND coalesce(q.organization_id, r.organization_id) = CAST(:org AS uuid) "
            "ORDER BY q.asked_at DESC NULLS LAST, r.run_no DESC, q.seq_no DESC"), {"ids": qids, "org": org})).mappings().all()
        picked, seen = [], set()
        for r in rows:
            key = " ".join(r["question"].lower().split())
            if key in seen:
                continue
            seen.add(key)
            picked.append(dict(r))
            if len(picked) >= REGRESSION_MAX:
                break
        if not picked:
            raise Invalid("no_positives", "Nobody has marked a question of this company positive for this failure mode.")
        made = await runs._insert_run(conn, m, kind="synthetic", org=org, source="regression",
                                      title=f"Regression · {modes[lin]['name']} · {rule['code']}"[:runs.TITLE_MAX])
        with_expected = 0
        for seq, q in enumerate(picked, start=1):
            origin = runs.question_code(q["run_no"], q["seq_no"])
            row = {"question": q["question"], "followups": _json(q.get("followups")), "role": q.get("role"),
                   "intent": q.get("intent"), "scenario_ref": origin}
            new_id = await runs._insert_question(conn, m, run_id=made["id"], seq=seq, row=row, org=org, source="regression")
            exp = await _current_expected(conn, q["id"])
            if exp and derivation(exp.get("evidence"), codes)["derived"]:
                ev = _json(exp.get("evidence")) or {}
                await runs._insert_expected(conn, m, question_id=new_id, expected=exp["expected"],
                                            expected_id=exp.get("expected_id"),
                                            evidence={**ev, "copied_from": origin}, source="manual")
                with_expected += 1
            turns = await question_turns(conn, q["id"])
            if turns:
                await conn.execute(text(
                    "INSERT INTO tracelab.lab_question_traces (question_id, turn_id, organization_id, source, created_by, "
                    "created_by_email) VALUES (CAST(:q AS uuid), :t, CAST(:org AS uuid), 'manual', CAST(:by AS uuid), "
                    ":email)"), {"q": new_id, "t": turns[-1], "org": org, "by": m.user_id, "email": m.email})
        await conn.execute(text(
            "INSERT INTO tracelab.lab_regression_runs (run_id, mode_lineage_id, spec_version_id, spec_rule, space, "
            "created_by, created_by_email) VALUES (CAST(:run AS uuid), CAST(:lin AS uuid), CAST(:spec AS uuid), :rule, "
            ":space, CAST(:by AS uuid), :email)"),
            {"run": made["id"], "lin": lin, "spec": spec["id"], "rule": rule["code"], "space": space, "by": m.user_id,
             "email": m.email})
    return {"run": runs.run_code(made["run_no"]), "questions": len(picked), "with_expected": with_expected,
            "mode": lin, "spec_rule": rule["code"], "space": space}


async def _regression_row(conn: Any, run_id: str) -> dict[str, Any] | None:
    r = (await conn.execute(text(
        "SELECT mode_lineage_id::text AS mode, spec_rule, space, spec_version_id::text AS spec_version_id, created_at "
        "FROM tracelab.lab_regression_runs WHERE run_id = CAST(:r AS uuid)"), {"r": run_id})).mappings().first()
    return dict(r) if r else None


class NotSafe(RuntimeError):
    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


async def rerun(m: labelling.Member, code: str, *, start: int = 0, count: int = RERUN_MAX,
                authorization: str | None = None) -> dict[str, Any]:
    """Ask a regression run's questions again, a few per request (each is a real chat turn and costs money). Each new
    answer is kept in Trace Lab's evidence and linked to its question beside the answer it is compared with."""
    from . import capture, orchestrator_host, scenario_store

    runs.require_curator(m)
    count = max(1, min(int(count or RERUN_MAX), RERUN_MAX))
    start = max(0, int(start or 0))
    async with db.lab_engine().connect() as conn:
        run = await runs._run_row(conn, m, code)
        reg = await _regression_row(conn, run["id"])
        if reg is None:
            raise Invalid("not_regression", "Only a regression run is asked again from here.")
        qs = [dict(r) for r in (await conn.execute(text(
            "SELECT id::text AS id, seq_no, question FROM tracelab.lab_questions WHERE run_id = CAST(:r AS uuid) "
            "ORDER BY seq_no"), {"r": run["id"]})).mappings().all()]
    org = run["organization_id"]
    batch = qs[start:start + count]
    if not batch:
        return {"asked": 0, "next_start": None, "total": len(qs), "results": []}
    problems = orchestrator_host.assert_isolated()
    if problems:
        raise NotSafe(problems)
    await orchestrator_host.ensure_overlays_readonly()
    orchestrator = orchestrator_host.get_orchestrator()
    mm = labelling.for_org(m, org)
    asker = labelling.company_view(mm.principal, org)
    # The instruction changes a person approved but has not made live are read by THIS rerun only (Bala, 5 Nov
    # 2026): the agents everyone else talks to keep the live text until a person makes the change live.
    from . import agent as _agent

    candidate = await _agent.under_test()
    results = []
    for q in batch:
        sid = f"regression-{uuid.uuid4().hex[:12]}"
        qcode = runs.question_code(run["run_no"], q["seq_no"])
        try:
            ct = await capture.run_message(orchestrator, question=q["question"], session_id=sid, page_context=None,
                                           principal=asker, organization_id=org, authorization=authorization,
                                           space=reg["space"], candidate=candidate or None)
            tid = scenario_store.turn_id_for(sid, str(getattr(ct, "turn_id", "") or sid))
            turn = dict(getattr(ct, "turn", None) or {})
            turn.setdefault("answer", getattr(ct, "answer", "") or "")
            item = {"turn_id": tid, "turn": turn, "spans": list(getattr(ct, "spans", None) or []),
                    "cards": list(getattr(ct, "cards", None) or []), "refused": list(getattr(ct, "refused", None) or []),
                    "result": {"scenario_id": qcode, "space": reg["space"], "engine": None, "role": None, "style": None,
                               "difficulty": None, "in_module": True, "gate_ok": None}}
            async with db.lab_engine().begin() as conn:
                await _limits(conn)
                await scenario_store._evidence(conn, org, tid, item)
                await conn.execute(text(
                    "INSERT INTO tracelab.lab_question_traces (question_id, turn_id, organization_id, source, created_by, "
                    "created_by_email) VALUES (CAST(:q AS uuid), :t, CAST(:org AS uuid), 'manual', CAST(:by AS uuid), "
                    ":email) ON CONFLICT (question_id, turn_id) DO NOTHING"),
                    {"q": q["id"], "t": tid, "org": org, "by": m.user_id, "email": m.email})
            results.append({"question": qcode, "turn_id": tid, "ok": not getattr(ct, "error", None),
                            "error": getattr(ct, "error", None)})
        except Exception as exc:  # noqa: BLE001 - one question must not end the batch
            log.warning("loop.rerun_question_failed", question=qcode, error=str(exc)[:300])
            results.append({"question": qcode, "ok": False, "error": str(exc)[:300]})
        finally:
            orchestrator_host.release_session(sid)
    nxt = start + len(batch)
    return {"asked": len(batch), "next_start": nxt if nxt < len(qs) else None, "total": len(qs), "results": results,
            "under_test": sorted(f"skills/{k[0]}/{k[1]}.md" for k in (candidate or {}))}


def _items(expected: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The values an answer must carry: the derived evidence's `values` when given, else read from the expected text."""
    if not expected:
        return []
    ev = _json(expected.get("evidence")) or {}
    vals = ev.get("values") if isinstance(ev, dict) else None
    try:
        if isinstance(vals, list) and vals:
            return golden.parse_expected_values("; ".join(str(v) for v in vals))
    except ValueError:
        return []
    return _facts(str(expected.get("expected") or ""))


#: A record code written in an expected answer: WO-B-301-4533, B-301-LIFT-4471, LOLER-B-301-4471.
_CODE = re.compile(r"\b[A-Z][A-Z0-9]*(?:-[A-Z0-9]+){2,}\b")


def _facts(sentence: str) -> list[dict[str, Any]]:
    """The checkable facts of an expected answer written as a sentence: its record codes and its dates (4 Nov 2026:
    R35-1's sentence became one item no answer could contain, and a fixed answer read "still failing"). A count of
    days is left out: it changes every day."""
    items: list[dict[str, Any]] = []
    for code in dict.fromkeys(_CODE.findall(sentence)):
        items.append({"raw": code, "kind": "code", "value": code})
    for d, _a, _b in golden.dates_in(sentence):
        if not any(i["kind"] == "date" and i["value"] == d.isoformat() for i in items):
            items.append({"raw": d.isoformat(), "kind": "date", "value": d.isoformat()})
    return items


def _verdict(before: str | None, after: str | None, *, answered: bool = True) -> str:
    """`answered` False: the rerun saved no answer (it ran out of time or failed), which says nothing about the
    correction - "no answer", never "still failing" (6 Nov 2026)."""
    if after is None:
        return "not_rerun"
    if not answered:
        return "no_answer"
    if after == "all_found":
        return "passing" if before == "all_found" else "fixed"
    return "regressed" if before == "all_found" else "still_failing"


async def compare(m: labelling.Member, code: str) -> dict[str, Any]:
    """Each question of a regression run: the answer people marked, the newest answer, and whether each carries the
    expected values; with what people say of the newest answer for the run's failure mode."""
    labelling.require_active(m)
    async with db.lab_engine().connect() as conn:
        run = await runs._run_row(conn, m, code)
        reg = await _regression_row(conn, run["id"])
        if reg is None:
            raise Invalid("not_regression", "Only a regression run is compared from here.")
        qs = [dict(r) for r in (await conn.execute(text(
            "SELECT id::text AS id, seq_no, question, scenario_ref FROM tracelab.lab_questions "
            "WHERE run_id = CAST(:r AS uuid) ORDER BY seq_no"), {"r": run["id"]})).mappings().all()]
        spec = await approved_spec(conn)
        codes = rule_codes(spec)
        expected = {q["id"]: await _current_expected(conn, q["id"]) for q in qs}
        links = {q["id"]: await question_turns(conn, q["id"]) for q in qs}
        cells = (await conn.execute(text("""
            SELECT DISTINCT ON (c.question_id, coalesce(c.author_id::text, c.author_email))
                   c.question_id::text AS question_id, c.value
              FROM tracelab.lab_label_cells c JOIN tracelab.lab_questions q ON q.id = c.question_id
             WHERE q.run_id = CAST(:r AS uuid) AND c.mode_lineage_id = CAST(:lin AS uuid) AND c.source = 'manual'
             ORDER BY c.question_id, coalesce(c.author_id::text, c.author_email), c.created_at DESC, c.id DESC"""),
            {"r": run["id"], "lin": reg["mode"]})).mappings().all()
    answers = await turn_answers([t for ts in links.values() for t in ts])
    out, summary = [], {"fixed": 0, "passing": 0, "still_failing": 0, "regressed": 0, "no_answer": 0, "not_rerun": 0,
                        "no_expected": 0}
    for q in qs:
        exp = expected[q["id"]]
        items = _items(exp)
        ts = links[q["id"]]
        before_t, after_t = (ts[0] if ts else None), (ts[-1] if len(ts) > 1 else None)

        def check(t: str | None) -> dict[str, Any] | None:
            if t is None:
                return None
            a = answers.get(t) or {"answer": "", "cards": []}
            return {"turn_id": t, "answer": a["answer"][:4000],
                    "check": grading.check_values(items, a["answer"], a["cards"]) if items else None}
        before, after = check(before_t), check(after_t)
        if not items:
            verdict = "no_expected"
        else:
            verdict = _verdict((before or {}).get("check", {}).get("status") if before else None,
                               (after or {}).get("check", {}).get("status") if after else None,
                               answered=bool(after and str(after.get("answer") or "").strip()))
        summary[verdict] += 1
        said = [c["value"] for c in cells if c["question_id"] == q["id"] and c["value"]]
        out.append({"question": runs.question_code(run["run_no"], q["seq_no"]), "text": q["question"],
                    "from": q.get("scenario_ref"), "expected": exp["expected"] if exp else None,
                    "derived": derivation((exp or {}).get("evidence"), codes)["derived"] if exp else False,
                    "before": before, "after": after, "verdict": verdict,
                    "people": {"positive": said.count("positive"), "negative": said.count("negative")}})
    return {"run": runs.run_code(run["run_no"]), "mode": reg["mode"], "spec_rule": reg["spec_rule"],
            "space": reg["space"], "summary": summary, "questions": out}


# ── the loop at a glance ───────────────────────────────────────────────────────────────────────

async def overview(m: labelling.Member) -> dict[str, Any]:
    """Each confirmed failure mode and how far round the loop it is: people's positives, its rule in the approved
    specification, the corrections made for it, and its regression runs with their latest comparison."""
    labelling.require_active(m)
    where, p = runs._scope(m, "r")
    async with db.lab_engine().connect() as conn:
        live, proposed = await resolve_modes(conn)
        spec = await approved_spec(conn)
        pos = await _positives(conn)
        corr = (await conn.execute(text(
            "SELECT mode_lineage_id::text AS mode, spec_rule, count(*) AS n, max(created_at) AS last "
            "FROM tracelab.lab_corrections GROUP BY 1, 2"))).mappings().all()
        # A regression run has been asked again when one of its questions holds more than the answer it was made from.
        regs = (await conn.execute(text(
            f"SELECT g.mode_lineage_id::text AS mode, r.run_no, g.spec_rule, g.space, g.created_at, "
            "EXISTS (SELECT 1 FROM tracelab.lab_questions q JOIN tracelab.lab_question_traces t ON t.question_id = q.id "
            "WHERE q.run_id = r.id GROUP BY q.id HAVING count(*) > 1) AS rerun FROM "
            f"tracelab.lab_regression_runs g JOIN tracelab.lab_runs r ON r.id = g.run_id WHERE {where} "
            "ORDER BY g.created_at DESC"), p)).mappings().all()
    rules = {r.get("mode_lineage_id"): r for r in (spec or {}).get("rules") or []}
    out = []
    for md in live:
        lin = md["lineage_id"]
        rule = rules.get(lin)
        n_corr = sum(int(c["n"]) for c in corr if c["mode"] == lin or (rule and c["spec_rule"] == rule["code"]))
        mine = [{"run": runs.run_code(g["run_no"]), "spec_rule": g["spec_rule"], "space": g["space"],
                 "at": _iso(g["created_at"]), "rerun": bool(g["rerun"])} for g in regs if g["mode"] == lin]
        steps = {"annotated": bool(pos.get(lin)), "taxonomy": True, "spec": rule is not None,
                 "correction": n_corr > 0, "regression": bool(mine), "rerun": any(g["rerun"] for g in mine)}
        nxt = next((k for k in ("annotated", "spec", "correction", "regression", "rerun") if not steps[k]), None)
        out.append({**md, "positives": len(pos.get(lin, [])), "rule": rule, "corrections": n_corr,
                    "regression_runs": mine, "steps": steps, "next": nxt})
    return {"spec": {"version": spec["version"], "at": spec["at"]} if spec else None, "modes": out,
            "waiting": {"modes": len(proposed)}}


# ── what the browser saw ────────────────────────────────────────────────────────────────────────

async def record_frontend_event(principal: Any, body: dict[str, Any]) -> dict[str, Any]:
    """One event from the Hoistra app in a person's browser. The company and person are the signed-in caller's, never
    the body's. Over EVENTS_PER_MINUTE a minute, events are dropped: the browser never waits on this or sees an error."""
    kind = str(body.get("kind") or "")
    if kind not in EVENT_KINDS:
        raise Invalid("bad_kind", "Unknown event.")
    uid = str(principal.user_id)
    org = str(principal.organization_id) if getattr(principal, "organization_id", None) else None

    def s(key: str, most: int) -> str | None:
        v = body.get(key)
        v = str(v).strip() if v is not None else ""
        return v[:most] or None

    def n(key: str) -> int | None:
        try:
            v = int(body.get(key))
        except (TypeError, ValueError):
            return None
        return v if 0 <= v <= 86_400_000 else None

    client_at = None
    try:
        client_at = datetime.fromisoformat(str(body.get("client_at")).replace("Z", "+00:00")) if body.get("client_at") else None
    except ValueError:
        client_at = None
    now = datetime.now(timezone.utc)
    if client_at is not None and (client_at.tzinfo is None or abs(client_at - now) > timedelta(days=2)):
        client_at = None
    async with db.lab_engine().begin() as conn:
        await _limits(conn)
        recent = (await conn.execute(text(
            "SELECT count(*) FROM tracelab.lab_frontend_events WHERE user_id = CAST(:u AS uuid) "
            "AND created_at > now() - interval '1 minute'"), {"u": uid})).scalar_one()
        if int(recent) >= EVENTS_PER_MINUTE:
            return {"kept": False}
        row = (await conn.execute(text(
            "INSERT INTO tracelab.lab_frontend_events (organization_id, user_id, email, kind, turn_id, session_id, question, "
            "view, instance, ms_first_text, ms_total, error, client_at) VALUES (CAST(:org AS uuid), CAST(:u AS uuid), "
            ":email, :kind, :turn, :session, :q, :view, :inst, :first, :total, :err, :at) RETURNING id::text"),
            {"org": org, "u": uid, "email": getattr(principal, "email", None), "kind": kind, "turn": s("turn_id", 200),
             "session": s("session_id", 200), "q": s("question", 4000), "view": s("view", 60),
             "inst": s("instance", 40), "first": n("ms_first_text"), "total": n("ms_total"), "err": s("error", 500),
             "at": client_at})).scalar_one()
    return {"kept": True, "id": row}


async def browser_events(conn: Any, turn_ids: list[str]) -> list[dict[str, Any]]:
    ids = [t for t in turn_ids if t and not t.startswith("browser:")]
    own = [t.split(":", 1)[1] for t in turn_ids if t and t.startswith("browser:")]
    own = [x for x in (_uuid(o) for o in own) if x]
    if not ids and not own:
        return []
    rows = (await conn.execute(text(
        "SELECT id::text AS id, kind, turn_id, view, instance, ms_first_text, ms_total, error, client_at, created_at "
        "FROM tracelab.lab_frontend_events WHERE turn_id = ANY(:ids) OR id = ANY(CAST(:own AS uuid[])) "
        "ORDER BY created_at DESC LIMIT 50"), {"ids": ids, "own": own})).mappings().all()
    return [{"id": r["id"], "kind": r["kind"], "turn_id": r.get("turn_id"), "view": r.get("view"),
             "instance": r.get("instance"), "ms_first_text": r.get("ms_first_text"), "ms_total": r.get("ms_total"),
             "error": r.get("error"), "at": _iso(r.get("client_at") or r["created_at"])} for r in rows]


def require_wide(m: labelling.Member) -> None:
    if not (m.active and m.all_companies):
        raise Forbidden("platform_only", "Only the platform team and the consultant can do this.")
