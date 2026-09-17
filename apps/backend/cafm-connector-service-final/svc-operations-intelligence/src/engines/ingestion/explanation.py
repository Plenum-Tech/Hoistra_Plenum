"""Does the uploader's explanation account for the warning?

Somebody has been told their document does not look like it belongs to the building they
chose, and they have answered. The answer can be entirely legitimate — a new managing agent
whose name is not yet on the building, a group policy that genuinely covers this property, a
site that trades under two names — or it can be "just upload it".

Two rules shape what happens next, and they are the reason this is a small module rather
than a clever one.

**The assessment never files anything.** The most it can do is change the question from
"explain this" to "I am satisfied — shall I proceed?". A person answers that either way, so
a model that is too generous costs a confirmation click, not a wrong record.

**Silence is not agreement.** With no model configured, or a model that errors, the
explanation is recorded and the warning stands. The uploader can still proceed, but as an
override in their name — which is exactly what an unassessed explanation is.
"""
from __future__ import annotations

import json
import re
from typing import Any

from ...config import settings
from ...core.logging import get_logger

log = get_logger(__name__)

MODEL = "claude-haiku-4-5-20251001"
MAX_EXPLANATION = 4000

#: Answers that say nothing about the warning. Recognised so the obvious non-answer does not
#: need a model call, and so a model cannot be talked past one either.
_EMPTY_ANSWERS = re.compile(
    r"^\s*(?:yes|no|ok(?:ay)?|sure|fine|go ahead|proceed|do it|just do it|ignore|ignore it|"
    r"it'?s? (?:fine|correct|right)|trust me|n/?a|because|please)\s*[.!]*\s*$", re.IGNORECASE)

_PROMPT = """A person has uploaded a document and filed it against a building. An automated \
check found the problems listed below. The person has given an explanation. Your only job is \
to say whether their explanation ACCOUNTS FOR those specific problems.

Document: {document}
Building selected: {building}
Verdict: {verdict}

Findings the check raised:
{findings}

Their explanation:
\"\"\"{explanation}\"\"\"

Answer with JSON only:
{{"resolves": true|false,
  "addressed": ["check names their explanation actually accounts for"],
  "unaddressed": ["check names it does not"],
  "reason": "one or two sentences, addressed to the person, saying what their explanation \
does and does not account for"}}

Rules for your judgement:
- "resolves" is true ONLY when the explanation gives a concrete reason that fits the \
evidence — a relationship, a renaming, a group-level arrangement, a correction of the \
record. Not when it merely asserts the document is correct, instructs you to proceed, or \
ignores the finding.
- A plausible-sounding explanation that does not touch the specific mismatch is false.
- Never say the document has been filed or approved: that is somebody else's decision.
- Write "reason" in plain English, second person, no jargon and no preamble."""


def _findings_text(findings: list[dict[str, Any]]) -> str:
    lines = [f"- {f.get('check')}: {f.get('message')}"
             for f in findings if f.get("direction") == "conflicts"]
    if not lines:
        lines = [f"- {f.get('check')}: {f.get('message')}"
                 for f in findings if f.get("direction") == "unknown" and f.get("message")]
    return "\n".join(lines[:8]) or "- nothing in the document identifies this building"


def _unassessed(reason: str, method: str, findings: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "resolves": False, "addressed": [], "method": method,
        "unaddressed": [f.get("check") for f in findings if f.get("direction") == "conflicts"],
        "reason": reason,
    }


async def assess(
    *,
    explanation: str,
    findings: list[dict[str, Any]],
    building_name: str | None,
    document_name: str | None,
    verdict: str | None = None,
) -> dict[str, Any]:
    """Whether the explanation accounts for the findings. Never raises."""
    text_in = (explanation or "").strip()[:MAX_EXPLANATION]
    if not text_in:
        return _unassessed("You have not given a reason yet, so the warning stands.",
                           "empty", findings)
    if _EMPTY_ANSWERS.match(text_in) or len(text_in) < 12:
        return _unassessed(
            f"That tells me you want it filed against {building_name}, but not why the "
            f"document looks like it belongs elsewhere — so the warning stands.",
            "no_reason_given", findings)
    if not settings.anthropic_api_key:
        return _unassessed(
            "I have recorded your explanation. I cannot assess it here, so the warning "
            "stands and filing it will be recorded as an override in your name.",
            "no_model", findings)

    try:
        import anthropic

        client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
        msg = await client.messages.create(
            model=MODEL, max_tokens=500, temperature=0,
            messages=[{"role": "user", "content": _PROMPT.format(
                document=document_name or "(unnamed)", building=building_name or "(unnamed)",
                verdict=verdict or "uncertain", findings=_findings_text(findings),
                explanation=text_in)}],
        )
        raw = "".join(getattr(b, "text", "") for b in msg.content)
        body = re.search(r"\{[\s\S]*\}", raw)
        data = json.loads(body.group(0)) if body else {}
        resolves = bool(data.get("resolves"))
        reason = str(data.get("reason") or "").strip()
        if not reason:
            raise ValueError("no reason in the model's answer")
        return {
            "resolves": resolves,
            "addressed": [str(x) for x in (data.get("addressed") or [])],
            "unaddressed": [str(x) for x in (data.get("unaddressed") or [])],
            "reason": reason, "method": f"llm:{MODEL}",
        }
    except Exception as exc:  # noqa: BLE001 — an unassessed explanation is a standing warning
        log.warning("ingestion.explanation.assess_failed", error=str(exc)[:200])
        return _unassessed(
            "I have recorded your explanation but could not assess it just now, so the "
            "warning stands and filing it will be recorded as an override in your name.",
            "error", findings)
