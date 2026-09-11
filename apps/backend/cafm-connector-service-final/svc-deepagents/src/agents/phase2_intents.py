"""Phase 2 Orchestrator intent keywords — single source of truth (PRD v1.2).

Tool agents registered with the Orchestrator:

| Tool Agent (PRD)              | Code agent id           | Feature |
|-------------------------------|-------------------------|---------|
| compliance-engine             | compliance              | A       |
| contract-performance-engine   | contract_performance    | B       |
| energy-intelligence-engine    | energy_intelligence     | C       |

Intent keyword lists below must stay byte-for-byte aligned with the PRD table.
"""
from __future__ import annotations

from typing import Literal

Phase2AgentId = Literal["compliance", "contract_performance", "energy_intelligence"]

# PRD names ↔ code agent ids
PHASE2_AGENT_REGISTRY: dict[str, Phase2AgentId] = {
    "compliance-engine": "compliance",
    "contract-performance-engine": "contract_performance",
    "energy-intelligence-engine": "energy_intelligence",
}

# Exact PRD intent keyword lists (order preserved for prompt display)
COMPLIANCE_INTENT_KEYWORDS: tuple[str, ...] = (
    "certificate",
    "cert",
    "certs",
    "expiry",
    "cert due",
    "inspection",
    "LOLER",
    "EICR",
    "gas safety",
    "compliance",
    "accreditation",
    "Gas Safe",
    "NICEIC",
    "lapsed",
    "renewal",
    "statutory",
    "fire risk",
    "vendor cert",
    "building cert",
    "blocked",
    "vendor accreditation",
    "not on record",
    "remedial",
    "insurance risk",
    "expired",
)

CONTRACT_PERFORMANCE_INTENT_KEYWORDS: tuple[str, ...] = (
    "SLA",
    "contractor",
    "performance",
    "KPI",
    "PPM completion rate",
    "vendor score",
    "first fix",
    "recall",
    "invoice",
    "overrun",
    "contract breach",
)

ENERGY_INTELLIGENCE_INTENT_KEYWORDS: tuple[str, ...] = (
    "energy",
    "meter",
    "consumption",
    "kWh",
    "spike",
    "anomaly",
    "EUI",
    "NABERS",
    "carbon",
    "EPC",
    "smart meter",
    "utility",
    "electricity",
    "benchmark",
)

PHASE2_INTENT_BY_AGENT: dict[Phase2AgentId, tuple[str, ...]] = {
    "compliance": COMPLIANCE_INTENT_KEYWORDS,
    "contract_performance": CONTRACT_PERFORMANCE_INTENT_KEYWORDS,
    "energy_intelligence": ENERGY_INTELLIGENCE_INTENT_KEYWORDS,
}


def format_keyword_list(keywords: tuple[str, ...]) -> str:
    return ", ".join(keywords)


def format_phase2_intent_table() -> str:
    """Markdown table for Orchestrator system prompt routing."""
    rows = [
        (
            "`compliance`",
            "A",
            format_keyword_list(COMPLIANCE_INTENT_KEYWORDS),
        ),
        (
            "`contract_performance`",
            "B",
            format_keyword_list(CONTRACT_PERFORMANCE_INTENT_KEYWORDS),
        ),
        (
            "`energy_intelligence`",
            "C",
            format_keyword_list(ENERGY_INTELLIGENCE_INTENT_KEYWORDS),
        ),
    ]
    lines = [
        "| Agent | Feature | Intent keywords |",
        "|-------|---------|-----------------|",
    ]
    for agent, feature, kws in rows:
        lines.append(f"| {agent} | {feature} | {kws} |")
    return "\n".join(lines)


def _normalize(text: str) -> str:
    return " ".join((text or "").lower().split())


def match_phase2_agent(message: str) -> Phase2AgentId | None:
    """
    Return the best Phase 2 agent for a user message by keyword hit count.
    Prefer longer / multi-word phrases (scored higher). Tie-break: C < B < A
    only when counts equal — actually prefer highest score; ties return first
    of energy, contract, compliance in that check order so energy EPC can win
    over generic certificate when both fire... 

    Order of evaluation for ties: compliance, contract_performance, energy —
    but we score by number of distinct keyword hits weighted by phrase length.
    """
    msg = _normalize(message)
    if not msg:
        return None

    scores: dict[Phase2AgentId, float] = {
        "compliance": 0.0,
        "contract_performance": 0.0,
        "energy_intelligence": 0.0,
    }
    for agent, keywords in PHASE2_INTENT_BY_AGENT.items():
        for kw in keywords:
            needle = _normalize(kw)
            if needle and needle in msg:
                # Multi-word / longer phrases weigh more
                scores[agent] += 1.0 + 0.25 * max(0, len(needle.split()) - 1)

    best_agent: Phase2AgentId | None = None
    best_score = 0.0
    # Prefer more specific agents on ties: energy > contract > compliance
    # when scores equal (EPC/energy vs compliance certificate ambiguity).
    for agent in ("energy_intelligence", "contract_performance", "compliance"):
        if scores[agent] > best_score:  # type: ignore[index]
            best_score = scores[agent]  # type: ignore[index]
            best_agent = agent  # type: ignore[assignment]
    return best_agent if best_score > 0 else None


# Pipeline / tool-name signals from single-door Feature A/B extracts
_COMPLIANCE_TOOL_HINTS = frozenset(
    {
        "extract_compliance_certificate",
        "upsert_compliance_certificate",
        "run_compliance_scan",
        "list_building_certificates",
        "list_vendor_accreditations",
        "verify_accreditation_now",
        "list_compliance_approvals",
        "decide_compliance_approval",
    }
)
_CONTRACT_TOOL_HINTS = frozenset(
    {
        "extract_contract_from_document",
        "extract_and_verify_invoice",
        "ingest_contract_parameters",
        "confirm_contract_parameters",
        "score_vendor_work_orders",
        "generate_vendor_scorecard",
        "verify_vendor_invoice",
        "list_contract_approvals",
        "decide_contract_approval",
    }
)
_ENERGY_TOOL_HINTS = frozenset(
    {
        "upsert_energy_meter",
        "pull_smart_meter_readings",
        "ingest_meter_readings",
        "compute_site_eui",
        "scan_energy_anomalies",
        "compute_building_rating",
        "get_ratings_position",
        "scan_chiller_efficiency",
        "generate_monthly_energy_report",
        "list_energy_approvals",
        "decide_energy_approval",
    }
)


def engines_from_tool_calls(tool_calls: list[dict] | None) -> list[Phase2AgentId]:
    """Map single-door / prior tool names to Phase 2 engines (order preserved)."""
    found: list[Phase2AgentId] = []
    seen: set[Phase2AgentId] = set()
    for tc in tool_calls or []:
        name = str((tc or {}).get("tool") or "").strip()
        if not name:
            continue
        engine: Phase2AgentId | None = None
        if name in _COMPLIANCE_TOOL_HINTS or name.startswith("extract_compliance"):
            engine = "compliance"
        elif name in _CONTRACT_TOOL_HINTS:
            engine = "contract_performance"
        elif name in _ENERGY_TOOL_HINTS or name.startswith("energy_"):
            engine = "energy_intelligence"
        if engine and engine not in seen:
            seen.add(engine)
            found.append(engine)
    return found


def resolve_phase2_engine(
    *,
    user_message: str | None = None,
    context_note: str | None = None,
    filenames: list[str] | None = None,
    tool_calls: list[dict] | None = None,
    detected_engines: list[Phase2AgentId] | None = None,
) -> Phase2AgentId | None:
    """
    Pick exactly one Phase 2 engine from extraction outcomes + user text.

    Priority:
      1. Explicit detected_engines from single-door (Feature A/B classifiers)
      2. Tool-call hints from the pipeline (extract_compliance_*, Feature B tools)
      3. Keyword match on message + filenames + context_note

    When multiple engines appear in (1)/(2), prefer the first explicit detection.
    Returns None when no Phase 2 engine should own the turn (use core orchestrator).
    """
    if detected_engines:
        return detected_engines[0]

    from_tools = engines_from_tool_calls(tool_calls)
    if from_tools:
        return from_tools[0]

    blob_parts = [
        user_message or "",
        context_note or "",
        " ".join(filenames or []),
    ]
    return match_phase2_agent(" ".join(blob_parts))
