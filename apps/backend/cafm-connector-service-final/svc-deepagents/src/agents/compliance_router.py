"""Pick the compliance skill documents one question needs, and compose them.

The sub-agent used to carry its whole contract on every call — 21,894 characters of
query-builder discipline, tool routing and data layer, whether the question was "how many
types are in the pack" or a portfolio audit. That costs tokens and latency on every turn,
and it buries the three paragraphs that matter under twenty that do not.

Selection is made by the model reading the question, because that is what the question
requires: "which certificates must an owner hold" and "which of our certificates lapsed"
share almost every word and need different documents. A keyword table cannot tell them
apart — the existing one already fails on exactly that pair.

Three guards keep a wrong or unavailable decision from becoming a broken answer:

* a core set is always loaded, so the agent never runs without its vocabulary, its tables
  or its prohibitions, whatever the router says;
* a named document that does not exist is dropped and logged, never silently skipped into
  an empty contract;
* any failure — no key, a bad response, a timeout — falls back to loading everything, which
  is the behaviour the agent has today. Degrading to "slower and complete" is the only
  acceptable direction for a contract.
"""
from __future__ import annotations

import json
import os
import time

import structlog

from ..config import settings
from .skills import prompt_doc, skills_dir
from . import llm_cost
from . import activity_log

log = structlog.get_logger(__name__)

#: Loaded for every compliance question regardless of what the router says. `core.md` is the
#: single compressed document that replaced vocabulary + tables + tool-selection + never and
#: the recipe index: the words, the tools, the name-matching rules, the data underneath, the
#: question-shape index and the prohibitions, deduplicated (11K characters where the four
#: separate files came to 12.7K plus 5.9K of recipes). The four originals stay on disk because
#: the analyst and reviewer still read vocabulary and tables directly.
CORE_DOCS: tuple[str, ...] = ("core",)

#: Selectable by the router, with the question shapes each one serves.
TOPIC_DOCS: dict[str, str] = {
    "answering": "How to shape the answer: one fact, which-one, particular rows, or portfolio. Load for every question that produces prose.",
    "recipes": "Worked question-to-rows recipes: named company, counts, status filters, a trade at a site.",
    "renewals": "Expiry, renewal chasing, remediation, blocked vendors, and what the PM should do next.",
    "taxonomy": "What the LAW requires rather than what is on file — the country pack as the answer.",
    "cross-domain": "The question also touches work orders, vendor performance, sites or energy.",
}

_ENV_FLAG = "COMPLIANCE_SELECTIVE_SKILLS"


def selective_loading_enabled() -> bool:
    """Feature flag. Off means load everything, which is today's behaviour."""
    return str(os.getenv(_ENV_FLAG, "")).strip().lower() in {"1", "true", "yes", "on"}


#: Files in the directory that are not part of the sub-agent's contract. `review` and
#: `scope-eval` are the reviewer's prompts; `domain_compliance_knowledge` is read by the
#: analyst and reviewer, not the fetcher; `tool-routing` is the pre-split original that
#: vocabulary, tool-selection and renewals were cut from; and vocabulary, tables,
#: tool-selection and never are now folded into `core` — loading any of them alongside it
#: would say the same thing twice. Excluding them here also keeps them out of the
#: load-everything fallback.
NOT_AGENT_DOCS: frozenset[str] = frozenset(
    {
        "SKILL",
        "review",
        "scope-eval",
        "tool-routing",
        "domain_compliance_knowledge",
        "vocabulary",
        "tables",
        "tool-selection",
        "never",
    }
)


_PATH_FLAG = "COMPLIANCE_SKILL_PATH"


def compliance_skill_path_enabled() -> bool:
    """Route compliance through the sub-agent and its skills instead of the preflight.

    Off is today's behaviour: the deterministic preflight answers compliance questions
    before the orchestrator model runs. On, it stands down, the orchestrator routes, and the
    compliance sub-agent answers from its own skill files. Kept as a flag because the two
    paths produce different answers and the only honest way to choose is to run both against
    the same questions.
    """
    return str(os.getenv(_PATH_FLAG, "")).strip().lower() in {"1", "true", "yes", "on"}


def available_docs() -> set[str]:
    """Which compliance documents actually exist on disk and belong to the agent."""
    return {
        p.stem
        for p in (skills_dir() / "compliance").glob("*.md")
        if p.stem not in NOT_AGENT_DOCS
    }


_ROUTER_PROMPT = (
    "You choose which instruction documents a compliance agent needs to answer one "
    "question. You are not answering the question.\n\n"
    "Read the question and return the documents whose content the answer will actually "
    "depend on. Choosing too few leaves the agent without something it needs; choosing "
    "everything defeats the point. Two or three is usual.\n\n"
    "Judge by what the question MEANS, not by the words in it. \"Which certificates must an "
    "owner hold\" and \"which of our certificates have lapsed\" share nearly every word and "
    "need different documents: the first is answered from the statutory pack, the second "
    "from the register.\n\n"
    "Return JSON only: {\"docs\": [\"name\", …], \"reason\": \"one short sentence\"}."
)

_ROUTER_SCHEMA = {
    "type": "object",
    "properties": {
        "docs": {"type": "array", "items": {"type": "string"}},
        "reason": {"type": "string"},
    },
    "required": ["docs", "reason"],
    "additionalProperties": False,
}


async def select_docs(question: str) -> dict:
    """Which topic documents this question needs, decided by reading it.

    Returns ``{"docs": [...], "reason": str, "source": "router"|"fallback"|"disabled"}``.
    ``docs`` never includes the core set — the composer adds that unconditionally.
    """
    have = available_docs()
    everything = sorted(have - set(CORE_DOCS))

    if not selective_loading_enabled():
        return {"docs": everything, "reason": "selective loading disabled", "source": "disabled"}

    api_key = (getattr(settings, "anthropic_api_key", "") or "").strip()
    if not api_key:
        return {"docs": everything, "reason": "no key for the router", "source": "fallback"}

    catalogue = "\n".join(
        f"- {name}: {why}" for name, why in TOPIC_DOCS.items() if name in have
    )
    model = (getattr(settings, "compliance_summary_model", "") or "claude-opus-5").strip()
    try:
        import anthropic

        client = anthropic.AsyncAnthropic(api_key=api_key)
        _t0 = time.perf_counter()
        message = await client.messages.create(
            model=model,
            max_tokens=500,
            output_config={
                "effort": "low",
                "format": {"type": "json_schema", "schema": _ROUTER_SCHEMA},
            },
            system=_ROUTER_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"AVAILABLE DOCUMENTS:\n{catalogue}\n\n"
                        f"QUESTION:\n{(question or '').strip()[:1000]}"
                    ),
                }
            ],
        )
        raw = next(
            (b.text for b in (message.content or []) if getattr(b, "type", "") == "text"), ""
        )
        chosen = json.loads(raw)
        llm_cost.record(
            "doc_router",
            model,
            llm_cost.usage_from_anthropic(message.usage),
            (time.perf_counter() - _t0) * 1000,
            effort="low",
            docs=[str(d) for d in (chosen.get("docs") or [])],
            reason=str(chosen.get("reason") or "")[:200],
        )
    except Exception as exc:  # noqa: BLE001 — routing must never break the turn
        log.warning("compliance.router.failed", error=str(exc)[:200])
        activity_log.fire(
            agent="compliance_router", stage="router", direction="error",
            summary=str(exc)[:300], ok=False, error=str(exc), model=model,
            payload={"question": (question or "")[:1000], "fallback_docs": everything},
        )
        return {"docs": everything, "reason": f"router failed: {type(exc).__name__}", "source": "fallback"}

    named = [str(d).strip() for d in (chosen.get("docs") or []) if str(d).strip()]
    # A name the router invented is a routing miss, not a reason to ship an empty contract.
    kept = [d for d in named if d in have and d not in CORE_DOCS]
    dropped = [d for d in named if d not in have]
    if dropped:
        log.warning("compliance.router.unknown_docs", dropped=dropped, kept=kept)
    result = (
        {"docs": everything, "reason": "router named nothing that exists", "source": "fallback"}
        if not kept
        else {"docs": kept, "reason": str(chosen.get("reason") or "")[:200], "source": "router"}
    )
    activity_log.fire(
        agent="compliance_router", stage="router", direction="output",
        summary=f"{result['source']}: {', '.join(result['docs'])}"[:300], model=model,
        latency_ms=(time.perf_counter() - _t0) * 1000,
        input_tokens=getattr(message.usage, "input_tokens", None),
        output_tokens=getattr(message.usage, "output_tokens", None),
        payload={"question": (question or "")[:1000], "chosen": chosen, "dropped": dropped,
                 "result": result},
    )
    return result


def compose(docs: list[str]) -> tuple[str, list[str]]:
    """The sub-agent's contract: the core set, then the selected topics, in a stable order.

    Returns the composed text and the document names actually included, so the trace can
    say what the agent was told rather than what it was meant to be told.
    """
    have = available_docs()
    order = [d for d in CORE_DOCS if d in have] + [
        d for d in docs if d in have and d not in CORE_DOCS
    ]
    parts: list[str] = []
    used: list[str] = []
    for name in order:
        try:
            parts.append(prompt_doc("compliance", name))
            used.append(name)
        except RuntimeError as exc:  # a listed file that will not read
            log.warning("compliance.router.unreadable", doc=name, error=str(exc)[:160])
    return "\n\n---\n\n".join(parts), used
