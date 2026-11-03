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


async def gather(org: str, *, run_code: str | None = None) -> dict[str, Any]:
    """Everything reviewers have left that a draft can be built from, newest first."""
    async with db.lab_engine().connect() as conn:
        notes = [dict(r) for r in (await conn.execute(text(
            "SELECT turn_id, step_kind, step_title, question, label, note, reviewer_email,"
            " reviewer_role, created_at"
            " FROM tracelab.step_labels"
            " WHERE organization_id = CAST(:org AS uuid) AND NOT tombstone AND superseded_by IS NULL"
            "   AND note IS NOT NULL"
            " ORDER BY created_at DESC LIMIT :lim"),
            {"org": org, "lim": MAX_PER_SOURCE})).mappings().all()]

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

        gates = [dict(r) for r in (await conn.execute(text(
            "SELECT s.question, s.engine, s.answer, r.space, s.scenario_id, s.asked_at"
            " FROM tracelab.scenario_results s"
            " JOIN tracelab.scenario_runs r ON r.id = s.run_id"
            " WHERE s.organization_id = CAST(:org AS uuid) AND s.gate_ok IS FALSE"
            " ORDER BY s.asked_at DESC LIMIT :lim"),
            {"org": org, "lim": MAX_PER_SOURCE})).mappings().all()]

    return {"notes": notes, "expected": expected, "gates": gates,
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
