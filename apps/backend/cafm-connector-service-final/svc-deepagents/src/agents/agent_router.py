"""Pick the agent for a question by reading it — before any keyword table gets a say.

The orchestrator asks two keyword layers first and its own model last. "What accreditations
must a contractor hold to do regulated work in the UK?" matched both tables at once —
`contractor` in the contract-performance list, `accreditation` in the compliance list — and a
tie-break sent a statutory-pack question to the contract agent. The model that could have
read the sentence was third in line and never reached.

This puts a reading model first. Its catalogue is the `description` of every routable
SKILL.md, so the routing table and the agent's contract still come from one file, and adding
a skill adds a route with no code change.

Same three guards as the skill router, because a routing miss must not become a broken turn:
the chosen name is validated against the agents that exist; an unknown name is logged and
falls back; any failure falls back to today's keyword table, so the worst case is the current
behaviour rather than a new one.
"""
from __future__ import annotations

import json
import os
import time

import structlog

from ..config import settings
from .phase2_intents import Phase2AgentId, resolve_phase2_engine
from .skills import routable_skills
from . import llm_cost

log = structlog.get_logger(__name__)

_FLAG = "ORCHESTRATOR_LLM_ROUTING"

#: Agents that answer a turn by themselves through _invoke_phase2_engine. Any other agent the
#: router names is handed to the orchestrator loop, which already knows how to task() it.
PHASE2_ENGINES: frozenset[str] = frozenset(
    {"compliance", "contract_performance", "energy_intelligence"}
)


def llm_routing_enabled() -> bool:
    """Feature flag. Off means the keyword tables route, which is today's behaviour."""
    return str(os.getenv(_FLAG, "")).strip().lower() in {"1", "true", "yes", "on"}


_PROMPT = (
    "You route one question to the agent that should answer it. You are not answering it.\n\n"
    "Read the question for what it is ASKING, not for the words it contains. A question "
    "mentioning a contractor is about contract performance only if it asks about that "
    "contractor's work, SLAs, invoices or scorecard; if it asks what a contractor must HOLD "
    "to do regulated work, it is a statutory compliance question. Decide from meaning.\n\n"
    "Choose exactly one agent from the catalogue. If the question plainly spans two domains, "
    "name the one that owns the answer's core and list the other under also. If nothing "
    "fits, return clarify.\n\n"
    "Return JSON only: {\"agent\": \"<id or clarify>\", \"also\": [ids], "
    "\"reason\": \"one short sentence\"}."
)

_SCHEMA = {
    "type": "object",
    "properties": {
        "agent": {"type": "string"},
        "also": {"type": "array", "items": {"type": "string"}},
        "reason": {"type": "string"},
    },
    "required": ["agent", "also", "reason"],
    "additionalProperties": False,
}


def _catalogue() -> tuple[str, set[str]]:
    skills = routable_skills()
    lines = [f"- {s.agent}: {s.description}" for s in skills]
    return "\n".join(lines), {s.agent for s in skills}


async def select_agent(question: str, context_note: str | None = None) -> dict:
    """Decide which agent owns this question.

    Returns ``{"agent": str | None, "also": [...], "reason": str, "source": str}`` where
    ``source`` is ``"llm"``, ``"keyword"`` (fell back) or ``"disabled"``. ``agent`` is None
    when nothing should short-circuit and the orchestrator loop should run as it does today.
    """
    if not llm_routing_enabled():
        eng = resolve_phase2_engine(user_message=question, context_note=context_note)
        return {"agent": eng, "also": [], "reason": "llm routing disabled", "source": "disabled"}

    api_key = (getattr(settings, "anthropic_api_key", "") or "").strip()
    catalogue, known = _catalogue()
    if not api_key or not known:
        eng = resolve_phase2_engine(user_message=question, context_note=context_note)
        return {"agent": eng, "also": [], "reason": "router unavailable", "source": "keyword"}

    model = (getattr(settings, "compliance_summary_model", "") or "claude-opus-5").strip()
    try:
        import anthropic

        client = anthropic.AsyncAnthropic(api_key=api_key)
        _t0 = time.perf_counter()
        message = await client.messages.create(
            model=model,
            max_tokens=400,
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": _SCHEMA}},
            system=_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"AGENTS:\n{catalogue}\n\n"
                        + (f"CONTEXT:\n{context_note.strip()[:600]}\n\n" if context_note else "")
                        + f"QUESTION:\n{(question or '').strip()[:1000]}"
                    ),
                }
            ],
        )
        raw = next(
            (b.text for b in (message.content or []) if getattr(b, "type", "") == "text"), ""
        )
        chosen = json.loads(raw)
        llm_cost.record(
            "agent_router",
            model,
            llm_cost.usage_from_anthropic(message.usage),
            (time.perf_counter() - _t0) * 1000,
            effort="low",
            agent=str(chosen.get("agent") or ""),
            reason=str(chosen.get("reason") or "")[:200],
        )
    except Exception as exc:  # noqa: BLE001 — routing must never break the turn
        log.warning("agent_router.failed", error=str(exc)[:200])
        eng = resolve_phase2_engine(user_message=question, context_note=context_note)
        return {"agent": eng, "also": [], "reason": f"router failed: {type(exc).__name__}", "source": "keyword"}

    agent = str(chosen.get("agent") or "").strip()
    also = [str(a).strip() for a in (chosen.get("also") or []) if str(a).strip() in known]
    reason = str(chosen.get("reason") or "")[:200]

    if agent == "clarify":
        # The model could not place it. That is an answer, not a failure — the orchestrator
        # loop asks the user rather than a keyword table guessing on their behalf.
        log.info("agent_router.clarify", reason=reason)
        return {"agent": None, "also": also, "reason": reason, "source": "llm"}
    if agent not in known:
        log.warning("agent_router.unknown_agent", named=agent, known=sorted(known))
        eng = resolve_phase2_engine(user_message=question, context_note=context_note)
        return {"agent": eng, "also": [], "reason": f"unknown agent {agent!r}", "source": "keyword"}

    log.info("agent_router.selected", agent=agent, also=also, reason=reason)
    return {"agent": agent, "also": also, "reason": reason, "source": "llm"}


def as_phase2_engine(agent: str | None) -> Phase2AgentId | None:
    """The engine id when the chosen agent answers turns by itself, else None."""
    return agent if agent in PHASE2_ENGINES else None  # type: ignore[return-value]
