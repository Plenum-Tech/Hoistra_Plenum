"""What the annotations add up to, written as a document a person can carry to the agents.

A reviewer's note on a step is the most specific thing anybody ever says about why an answer was
wrong -- "there needs to be a check at the orchestrator for access/restrictions based on the user
role" is worth more than any score. It sits in Trace Lab and reaches nothing.

This gathers the three things reviewers leave behind and drafts them into markdown:

  step labels and notes   what went wrong, on which step, in the reviewer's words
  expected answers        what the answer should have been -- a person stating the target
  gate failures           a synthetic question outside its instance that was answered anyway;
                          machine-judged, so it needs no reviewer and is never ambiguous

What it does NOT do is apply anything. The documents the agents read live in plenum_cafm
(services/skill_overlays.py), which this service may never write (G0): Trace Lab reads that
database and keeps its own. So the output is a draft with its evidence attached, for a person to
read and apply through the Skill lab. That is also the right shape regardless of the rule -- an
annotation is one reviewer's opinion, and a loop that taught the agents directly from opinions
would drift with whoever reviewed last.

The draft is assembled, not written by a model. Every line in it traces to a row somebody wrote,
and a model asked to "summarise the lessons" would produce something fluent that nobody said.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

import structlog
from sqlalchemy import text

from . import db

log = structlog.get_logger(__name__)

#: How many of each kind to carry into one draft. A document nobody reads teaches nobody.
MAX_PER_SOURCE = 50

#: The skill documents a lesson can be filed against, by the step it was left on. A note on the
#: orchestrator step is about routing; one on a model step inside an engine is about that engine's
#: instructions. Anything else is filed as general so it is never silently dropped.
_STEP_TO_SKILL = {
    "orchestrator": "orchestrator routing",
    "router": "orchestrator routing",
    "model": "the engine's own instructions",
    "tool": "tool choice",
    "code": "the deterministic step",
    "output": "how the answer is written",
}


def _skill_for(step_kind: str | None) -> str:
    return _STEP_TO_SKILL.get(str(step_kind or "").lower(), "general")


#: The real skill folders in svc-deepagents, by the engine a step ran in. The reviewer picks the
#: document in the end -- only they know what it already says -- but a draft that does not even
#: name a candidate leaves them hunting through nine folders for the one the note is about.
_ENGINE_TO_SKILL = {
    "wo_engine": "wo-engine",
    "compliance": "compliance",
    "contract_performance": "contract-performance",
    "energy_intelligence": "energy-intelligence",
    "udr": "udr",
    "doc_rag": "doc-rag",
    "migration": "migration",
}

#: Which document inside that folder a note of this kind is usually about.
_KIND_TO_DOC = {
    "tool": "tool-selection.md",
    "output": "answering.md",
    "model": "answering.md",
}

#: Notes that are not about one engine. Routing and the way an answer is written are shared, and
#: query-builder is where the shared instructions live (fm-lens is read by every answer writer).
_SHARED = ("query-builder", "SKILL.md")


def suggest(step_kind: str | None, step_title: str | None) -> tuple[str, str]:
    """A candidate (skill, doc) for a note. A SUGGESTION, and labelled as one everywhere it is
    shown: filing a lesson against the wrong document teaches the wrong agent."""
    title = str(step_title or "").strip().lower().replace("-", "_").replace(" ", "_")
    skill = None
    for engine, folder in _ENGINE_TO_SKILL.items():
        if engine in title:
            skill = folder
            break
    if skill is None:
        return _SHARED
    return skill, _KIND_TO_DOC.get(str(step_kind or "").lower(), "SKILL.md")


def by_skill(found: dict[str, Any]) -> list[dict[str, Any]]:
    """The notes grouped by the document each one is probably about, with its own markdown.

    One document at a time is how a skill document is actually edited, and how the Skill lab takes
    a proposal: one (skill, doc) and the text it should have. A single draft of everything reads
    well and cannot be applied.
    """
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for n in found.get("notes") or []:
        groups.setdefault(suggest(n.get("step_kind"), n.get("step_title")), []).append(n)
    out = []
    for (skill, doc_name), rows in sorted(groups.items()):
        lines = [f"## What reviewers said about {skill}/{doc_name}", ""]
        for n in rows:
            who = n.get("reviewer_email") or n.get("reviewer_role") or "a reviewer"
            lines += [f"- _{n.get('label') or 'noted'}_ on **{n.get('step_title') or n.get('step_kind')}** - {who}",
                      f"  - > {_one_line(n['note'], 400)}"]
            if n.get("question"):
                lines += [f"  - Asked: {_one_line(n['question'])}"]
            lines += [f"  - Turn: `{n['turn_id']}`", ""]
        out.append({"skill": skill, "doc": doc_name, "notes": len(rows),
                    "suggested": True, "markdown": "\n".join(lines).rstrip() + "\n"})
    return out


async def gather(org: str, *, run_code: str | None = None) -> dict[str, Any]:
    """Everything reviewers have left that a draft can be built from, newest first.

    `run_code` narrows all three sources to one run, not just the expected answers. A draft headed
    "R32" that carried every note the company has ever written would be read as what R32 showed.
    A note reaches its run through the turn its step belongs to: step_labels knows the turn,
    lab_question_traces knows which question that turn answered, and the question knows its run.
    """
    rn = _run_no(run_code) if run_code else None
    in_run = (
        " AND turn_id IN (SELECT t.turn_id FROM tracelab.lab_question_traces t"
        "   JOIN tracelab.lab_questions q ON q.id = t.question_id"
        "   JOIN tracelab.lab_runs r ON r.id = q.run_id WHERE r.run_no = :rn)")
    async with db.lab_engine().connect() as conn:
        notes = [dict(r) for r in (await conn.execute(text(
            "SELECT turn_id, step_kind, step_title, question, label, note, reviewer_email,"
            " reviewer_role, created_at"
            " FROM tracelab.step_labels"
            " WHERE organization_id = CAST(:org AS uuid) AND NOT tombstone AND superseded_by IS NULL"
            "   AND note IS NOT NULL"
            + (in_run if rn is not None else "") +
            " ORDER BY created_at DESC LIMIT :lim"),
            {"org": org, "lim": MAX_PER_SOURCE,
             **({"rn": rn} if rn is not None else {})})).mappings().all()]

        expected = [dict(r) for r in (await conn.execute(text(
            "SELECT q.question, e.expected, e.author_email, e.created_at, r.title AS run_title"
            " FROM tracelab.lab_expected_answers e"
            " JOIN tracelab.lab_questions q ON q.id = e.question_id"
            " JOIN tracelab.lab_runs r ON r.id = q.run_id"
            " WHERE q.organization_id = CAST(:org AS uuid)"
            + (" AND r.run_no = :rn" if run_code else "") +
            " ORDER BY e.created_at DESC LIMIT :lim"),
            {"org": org, "lim": MAX_PER_SOURCE,
             **({"rn": _run_no(run_code)} if run_code else {})})).mappings().all()]

        # The gate verdicts live in the 0012 tables, which a person applies and which are not
        # applied everywhere yet. Asking for them unconditionally made the whole draft 500 where
        # they are absent -- the notes and expected answers, which need nothing from 0012, were
        # lost with them. Missing means "no gate failures to report", not "no draft".
        has_scenarios = (await conn.execute(text(
            "SELECT to_regclass('tracelab.scenario_results')"))).scalar() is not None
        gates = [dict(r) for r in (await conn.execute(text(
            "SELECT s.question, s.engine, s.answer, r.space, s.scenario_id, s.asked_at"
            " FROM tracelab.scenario_results s"
            " JOIN tracelab.scenario_runs r ON r.id = s.run_id"
            " WHERE s.organization_id = CAST(:org AS uuid) AND s.gate_ok IS FALSE"
            + (" AND s.turn_id IN (SELECT t.turn_id FROM tracelab.lab_question_traces t"
               "   JOIN tracelab.lab_questions q ON q.id = t.question_id"
               "   JOIN tracelab.lab_runs r2 ON r2.id = q.run_id WHERE r2.run_no = :rn)"
               if rn is not None else "") +
            " ORDER BY s.asked_at DESC LIMIT :lim"),
            {"org": org, "lim": MAX_PER_SOURCE,
             **({"rn": rn} if rn is not None else {})})).mappings().all()] if has_scenarios else []

    return {"notes": notes, "expected": expected, "gates": gates, "run_code": run_code,
            # False means gate failures could not be looked for, which is not the same as there
            # being none. Said in the reply rather than left to be inferred from a zero.
            "gates_available": has_scenarios,
            "counts": {"notes": len(notes), "expected": len(expected), "gates": len(gates)}}


def _run_no(code: str | None) -> int:
    m = re.match(r"^\s*R(\d+)\s*$", str(code or ""), re.I)
    return int(m.group(1)) if m else -1


def draft(found: dict[str, Any], *, org_name: str | None = None) -> str:
    """The lessons as markdown, each with the evidence that justifies it.

    Assembled from the rows, not written by a model: a reader has to be able to follow every line
    back to somebody who typed it, and decide for themselves whether the agents should be taught
    it. Nothing here is phrased as an instruction to a model -- that is the person's job when they
    put it into a skill document, because only they know what the document already says.
    """
    notes, expected, gates = found["notes"], found["expected"], found["gates"]
    when = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out: list[str] = [
        f"# What the reviews say{(' · ' + org_name) if org_name else ''}",
        "",
        f"Drafted {when} from Trace Lab. Every line below traces to a row somebody wrote; nothing "
        "is summarised by a model. Read it, decide what the agents should actually be taught, and "
        "put that into the skill document yourself — this file is evidence, not an instruction.",
        "",
        f"- {len(notes)} step notes",
        f"- {len(expected)} expected answers",
        f"- {len(gates)} questions answered in an instance that should have refused them",
        "",
    ]

    if gates:
        out += ["## The instance answered something it does not have", "",
                "Machine-judged and unambiguous: the question was generated OUTSIDE the instance, "
                "so the instance should have refused it. An answer here means the gate did not "
                "hold, whatever the answer said.", ""]
        for g in gates:
            out += [f"- **{g['scenario_id']}** · asked in `{g['space']}` · routed to `{g['engine']}`",
                    f"  - Question: {_one_line(g['question'])}",
                    f"  - It answered: {_one_line(g['answer'], 240)}", ""]

    if notes:
        out += ["## What reviewers said went wrong", ""]
        for skill in sorted({_skill_for(n["step_kind"]) for n in notes}):
            rows = [n for n in notes if _skill_for(n["step_kind"]) == skill]
            out += [f"### {skill}", ""]
            for n in rows:
                who = n.get("reviewer_email") or n.get("reviewer_role") or "a reviewer"
                verdict = n.get("label") or "noted"
                out += [f"- _{verdict}_ on **{n.get('step_title') or n.get('step_kind')}** — {who}",
                        f"  - > {_one_line(n['note'], 400)}"]
                if n.get("question"):
                    out += [f"  - Asked: {_one_line(n['question'])}"]
                out += [f"  - Turn: `{n['turn_id']}`", ""]

    if expected:
        out += ["## What the answer should have been", "",
                "A person stating the target, which is the strongest signal here: it says what "
                "right looks like rather than describing a fault.", ""]
        for e in expected:
            out += [f"- **{_one_line(e['question'])}**",
                    f"  - Should be: {_one_line(e['expected'], 600)}",
                    f"  - {e.get('author_email') or 'a reviewer'}"
                    f"{(' · ' + e['run_title']) if e.get('run_title') else ''}", ""]

    if not (notes or expected or gates):
        out += ["Nothing to draft yet: no notes, no expected answers and no gate failures. "
                "Annotate a run first.", ""]
    return "\n".join(out).rstrip() + "\n"


def _one_line(value: Any, limit: int = 300) -> str:
    """A cell as one line of markdown: no newlines to break a list, no pipes to break a table."""
    s = " ".join(str(value or "").split()).replace("|", "\\|")
    return s if len(s) <= limit else s[: limit - 1] + "…"
