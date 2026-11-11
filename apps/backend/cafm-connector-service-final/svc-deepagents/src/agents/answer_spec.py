"""The answer forms, the parts of a question, and the checks that hold an answer to both (Workstream A4).

The specification itself - the four forms and one worked example of each - is
skills/query-builder/answer-spec.md, read through the skill overlays like the FM lens, so it can be
changed in the one place instructions live. This module chooses the form from what the lookups did,
splits a question into the parts that must each be answered, and checks an answer against both:

  * form: a partial answer names what it could not answer; a denial says so and gives the next step;
    a failure gives the next step and no table of figures; every answer leads with the answer;
  * completeness: every part of the question is either answered or named as not answered.

Each finding is a sentence a rewrite can act on (agents/final_answer.py, the planner's writer).
"""
from __future__ import annotations

import re
from typing import Any

import structlog

from . import evidence as ev

log = structlog.get_logger(__name__)

SUCCESS, PARTIAL, DENIAL, FAILURE = "F-SUCCESS", "F-PARTIAL", "F-DENIAL", "F-FAILURE"
FORMS = (SUCCESS, PARTIAL, DENIAL, FAILURE)
MAX_PARTS = 6


def text() -> str:
    """The whole specification, as the agents should read it now (an approved change, else the file)."""
    from .skills import prompt_doc

    try:
        return prompt_doc("query-builder", "answer-spec")
    except RuntimeError as exc:  # a missing spec costs the forms, never the answer
        log.warning("answer_spec.missing", error=str(exc)[:200])
        return ""


def forms_and_examples() -> str:
    """The part the writers read: the four forms and the examples."""
    t = text()
    i = t.find("## Answer forms")
    return t[i:].strip() if i >= 0 else ""


def form_for(results: dict[str, dict[str, Any]]) -> str:
    """The form an answer takes, from what its lookups did. A turn with no lookup is a success."""
    rs = [r for k, r in results.items() if k not in ("previous_answer", "__plan__") and isinstance(r, dict)]
    failed = [r for r in rs if not r.get("ok")]
    if not failed:
        return SUCCESS
    if len(failed) < len(rs):
        return PARTIAL
    if all((r.get("status") or (r.get("failure") or {}).get("status")) == ev.DENIED for r in failed):
        return DENIAL
    return FAILURE


# -- the parts of a question --------------------------------------------------------------------

_ASK = r"(?:what|which|how|who|when|where|why|list|show|give|tell|is|are|was|were|does|do|did|can|could|has|have|compare|break)"
_SPLIT = re.compile(r"\?\s*|;\s*|\.\s+(?=[A-Z])|\s+(?:and then|then|also|as well as)\s+|\s+and\s+(?=" + _ASK + r"\b)", re.I)


def split_question(question: str) -> list[str]:
    """The parts of a question that each need an answer: "How much energy did X use, and which of its
    vendors are blocked?" -> two. A question with one ask is one part."""
    q = " ".join((question or "").split())
    parts = [p.strip(" ,.") for p in _SPLIT.split(q) if p and p.strip(" ,.")]
    parts = [p for p in parts if len(p.split()) >= 3]
    return parts[:MAX_PARTS] or ([q] if q else [])


def parts_of(question: str, plan: dict[str, Any] | None = None) -> list[str]:
    """The sub-questions to check off: a plan's step asks when there is a plan, else the question's own
    parts."""
    steps = [str(s.get("ask") or "").strip() for s in (plan or {}).get("steps") or [] if isinstance(s, dict)]
    steps = [s for s in steps if s]
    return steps[:MAX_PARTS] if len(steps) > 1 else split_question(question)


# -- the checks ---------------------------------------------------------------------------------

_GENERIC = frozenset("""
what which how who when where why list show give tell does did have has were was are the and for with from
that this those these them their there they many much any some all each every one ones also then about into
over under per our your please could would should can will just only more most less than last next this
month year week today day days number count total amount show me us its it is be been being do of in on at
to by as or an a than between across within whole time
""".split())


def _terms(text: str) -> list[str]:
    words = re.findall(r"[A-Za-z][A-Za-z0-9-]{2,}|\b[A-Z]{1,3}-?\d{2,}\b|\bWO-[A-Z0-9-]+\b", text or "")
    return [w for w in words if w.lower() not in _GENERIC]


def _stem(w: str) -> str:
    w = w.lower()
    for suffix in ("ings", "ing", "ies", "es", "s", "ed"):
        if len(w) > len(suffix) + 3 and w.endswith(suffix):
            return w[: -len(suffix)]
    return w


def uncovered(answer: str, parts: list[str]) -> list[str]:
    """The parts the answer does not speak to: none of a part's own words (a name, a code, the thing
    asked about) appears in the answer. One part is the whole question and is not checked."""
    if len(parts) < 2:
        return []
    a = (answer or "").lower()
    out = []
    for p in parts:
        terms = {_stem(t) for t in _terms(p)}
        if terms and not any(t in a for t in terms):
            out.append(p)
    return out


_NOT_ANSWERED = re.compile(r"\bnot answered\b|\bcould not (?:be )?(?:read|answer|found|checked)\b|\bcannot be (?:read|answered)\b", re.I)
_NEXT_STEP = re.compile(r"\bnext step\b|\bask again\b|\btry again\b|\bsign in again\b|\badministrator\b", re.I)
_ACCESS = re.compile(r"\b(access|permission|allowed|sign(?:ed)? ?in)\b", re.I)
_TABLE = re.compile(r"^\s*\|?\s*:?-{3,}", re.M)
_PREAMBLE = re.compile(r"^\s*(?:#+\s*)?(?:sure|certainly|of course|here(?:'s| is| are)|below (?:is|are)|based on|i (?:have|found|checked|looked))\b", re.I)


def form_mismatches(answer: str, form: str) -> list[str]:
    """Where the answer does not take its form."""
    a = answer or ""
    first = next((line for line in a.splitlines() if line.strip()), "")
    out = []
    if _PREAMBLE.search(first):
        out.append("Lead with the direct answer - no preamble such as 'Here is' or 'Based on'.")
    if form == PARTIAL and not _NOT_ANSWERED.search(a):
        out.append("Some parts could not be read: add '**Not answered:**' naming each such part, why, and its next step.")
    if form == DENIAL and not (_ACCESS.search(a) and _NEXT_STEP.search(a)):
        out.append("A lookup was refused: say the user does not have access, and give '**Next step:**' "
                   "(ask an administrator of your company, or sign in again).")
    if form == FAILURE:
        if not _NEXT_STEP.search(a):
            out.append("Nothing could be read: say what could not be done and give '**Next step:**'.")
        if _TABLE.search(a):
            out.append("Nothing could be read, so the answer must not show a table of figures.")
    return out


def checks(answer: str, form: str, parts: list[str]) -> list[str]:
    """Every form and completeness finding, as sentences for a rewrite."""
    out = form_mismatches(answer, form)
    missing = uncovered(answer, parts)
    if missing:
        out.append("The answer does not address: " + "; ".join(f'"{m}"' for m in missing)
                   + ". Answer each, or put it under '**Not answered:**' with why.")
    return out


def prompt_block(form: str, parts: list[str]) -> str:
    """What the writer is told about form and parts, with the forms and examples from the spec."""
    lines = [f"ANSWER FORM: {form} (from the forms below; the examples show each one)."]
    if len(parts) > 1:
        lines.append("PARTS OF THE QUESTION - answer every one, or put it under **Not answered:** with why:")
        lines += [f"{i}. {p}" for i, p in enumerate(parts, 1)]
    spec = forms_and_examples()
    if spec:
        lines.append(spec)
    return "\n".join(lines)


_EXAMPLE = re.compile(r"^### (E-\d+) · (F-[A-Z]+)[^\n]*\n(.*?)(?=^### |\Z)", re.M | re.S)


def examples() -> list[dict[str, str]]:
    """The worked examples in the spec, as the eval scenarios reference them: {id, form, question,
    results, answer} (tests/test_answers_take_their_form.py)."""
    out = []
    for m in _EXAMPLE.finditer(text()):
        body = m.group(3)
        q = re.search(r"^Question:\s*(.+)$", body, re.M)
        r = re.search(r"^Results:\s*(.+)$", body, re.M)
        a = body.split("Answer:", 1)[1] if "Answer:" in body else ""
        answer = "\n".join(line[4:] if line.startswith("    ") else line for line in a.strip("\n").splitlines()).strip()
        out.append({"id": m.group(1), "form": m.group(2), "question": q.group(1).strip() if q else "",
                    "results": r.group(1).strip() if r else "", "answer": answer})
    return out
