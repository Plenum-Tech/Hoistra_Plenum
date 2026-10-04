"""A correction that re-ran a turn, or a thumbs-down with a reason, becomes a teaching by itself.

Channel 5 of the orchestrator's feedback (4 Oct 2026). Until now a reader had to choose "Save as
teaching" for a correction to outlive the turn; "Re-run the steps" fixed one answer and taught
nothing, and a thumbs-down with a comment went to the trace alone. Now:

    re-run with corrections   -> one teaching per correction, worded for the next similar question
    thumbs-down + a comment   -> one teaching from the comment

Teachings are company-wide corrections (services/chat_memories.py: the sharing rule), recalled on
similar questions and carried into every sub-agent's prompt (turn_recall). Pure builders here,
one async writer; nothing raised into the route - a teaching that fails to save never fails the
re-run that earned it.
"""
from __future__ import annotations

import re
from typing import Any

import structlog

log = structlog.get_logger(__name__)

#: A comment this short says nothing a future question can use ("wrong", "no", "bad").
MIN_COMMENT = 12
_NOISE = re.compile(r"^(wrong|no|bad|incorrect|not right|nope|rubbish|useless)[.!\s]*$", re.I)


def _q(question: str) -> str:
    q = " ".join((question or "").split()).strip().rstrip("?")
    return q[:140] + ("…" if len(q) > 140 else "")


def teaching_from_correction(question: str, c: dict[str, Any]) -> str | None:
    """The sentence a correction becomes. None when the correction carries nothing to remember."""
    parts: list[str] = []
    text = " ".join(str(c.get("text") or "").split())
    if text:
        parts.append(text.rstrip("."))
    route = str(c.get("route") or "").strip()
    if c.get("mode") == "route" and route:
        parts.append("route it to the " + route + (" planner" if route == "planner" else " engine"))
    ex = [str(x).strip() for x in (c.get("exclude") or []) if str(x).strip()]
    if ex:
        parts.append("exclude status " + ", ".join(ex))
    if c.get("period"):
        parts.append("use the period " + str(c["period"]).replace("_", " "))
    if c.get("field"):
        parts.append("date by " + str(c["field"]))
    args = c.get("args") if isinstance(c.get("args"), dict) else None
    if args and c.get("mode") == "tool":
        parts.append("call it with " + ", ".join(f"{k}={v}" for k, v in list(args.items())[:4]))
    body = "; ".join(dict.fromkeys(p for p in parts if p))
    if len(body) < 8:
        return None
    lead = f'For questions like "{_q(question)}": ' if question else ""
    return (lead + body + ".")[:600]


def teaching_from_feedback(question: str, comment: str | None) -> str | None:
    """A thumbs-down with a reason. None for a bare rating or a one-word verdict."""
    c = " ".join(str(comment or "").split())
    if len(c) < MIN_COMMENT or _NOISE.match(c):
        return None
    lead = f'When asked "{_q(question)}" the answer was marked wrong: ' if question else "An answer was marked wrong: "
    return (lead + c.rstrip(".") + ".")[:600]


async def teach(texts: list[str], *, principal, org: str | None, source_thread: str | None = None,
                source_turn: int | None = None, subject: str | None = None) -> list[str]:
    """Store the teachings that are not personal; returns those stored (or refreshed)."""
    from ..services import chat_memories

    kept = [t for t in texts if t and not chat_memories.looks_personal(t)]
    if not kept:
        return []
    try:
        await chat_memories.store([{"kind": "correction", "text": t, "subject": subject} for t in kept],
                                  principal=principal, org=org, source_thread=source_thread, source_turn=source_turn)
    except Exception as exc:  # noqa: BLE001 - a teaching that fails to save never fails the turn
        log.warning("auto_teach.failed", error=str(exc)[:200])
        return []
    return kept


def subject_of(turn: dict[str, Any] | None) -> str | None:
    """The building a turn was about, if its question or working set names one (B-301 style)."""
    q = str((turn or {}).get("question") or "")
    m = re.search(r"\b(B-\d{3})\b", q)
    return m.group(1) if m else None
