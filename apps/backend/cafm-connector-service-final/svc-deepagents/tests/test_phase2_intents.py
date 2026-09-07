"""Phase 2 intent keywords stay aligned with PRD tool-agent table."""
from src.agents.phase2_intents import (
    COMPLIANCE_INTENT_KEYWORDS,
    CONTRACT_PERFORMANCE_INTENT_KEYWORDS,
    ENERGY_INTELLIGENCE_INTENT_KEYWORDS,
    PHASE2_AGENT_REGISTRY,
    format_keyword_list,
    match_phase2_agent,
)
from src.agents.system_prompt import build_system_prompt

# Exact PRD lists — fail if anyone trims/reorders without updating this lock
_PRD_COMPLIANCE = (
    "certificate, expiry, cert due, inspection, LOLER, EICR, gas safety, compliance, "
    "accreditation, Gas Safe, NICEIC, lapsed, renewal, statutory, fire risk, vendor cert, "
    "building cert, blocked, vendor accreditation, not on record, remedial, insurance risk, "
    "expired"
)
_PRD_CONTRACT = (
    "SLA, contractor, performance, KPI, PPM completion rate, vendor score, first fix, "
    "recall, invoice, overrun, contract breach"
)
_PRD_ENERGY = (
    "energy, meter, consumption, kWh, spike, anomaly, EUI, NABERS, carbon, EPC, "
    "smart meter, utility, electricity, benchmark"
)


def test_prd_keyword_lists_exact():
    assert format_keyword_list(COMPLIANCE_INTENT_KEYWORDS) == _PRD_COMPLIANCE
    assert format_keyword_list(CONTRACT_PERFORMANCE_INTENT_KEYWORDS) == _PRD_CONTRACT
    assert format_keyword_list(ENERGY_INTELLIGENCE_INTENT_KEYWORDS) == _PRD_ENERGY


def test_agent_registry_names():
    assert PHASE2_AGENT_REGISTRY["compliance-engine"] == "compliance"
    assert PHASE2_AGENT_REGISTRY["contract-performance-engine"] == "contract_performance"
    assert PHASE2_AGENT_REGISTRY["energy-intelligence-engine"] == "energy_intelligence"


def test_system_prompt_injects_all_prd_keywords():
    prompt = build_system_prompt()
    assert "@@PHASE2_INTENT_TABLE@@" not in prompt
    assert "@@COMPLIANCE_INTENT_KEYWORDS@@" not in prompt
    for kw in COMPLIANCE_INTENT_KEYWORDS:
        assert kw in prompt, f"missing compliance keyword: {kw}"
    for kw in CONTRACT_PERFORMANCE_INTENT_KEYWORDS:
        assert kw in prompt, f"missing contract keyword: {kw}"
    for kw in ENERGY_INTELLIGENCE_INTENT_KEYWORDS:
        assert kw in prompt, f"missing energy keyword: {kw}"
    assert 'task("compliance"' in prompt or "task(\"compliance\"" in prompt
    assert "contract_performance" in prompt
    assert "energy_intelligence" in prompt


def test_match_phase2_agent_examples():
    assert match_phase2_agent("Show LOLER and EICR certificates due for renewal") == "compliance"
    assert match_phase2_agent("Vendor score and first fix SLA KPI") == "contract_performance"
    assert match_phase2_agent("EUI and NABERS benchmark for electricity meter") == "energy_intelligence"
    assert match_phase2_agent("Which vendors are blocked?") == "compliance"
    assert match_phase2_agent("how many have an insurance risk flag") == "compliance"
    assert match_phase2_agent("hello") is None


def test_resolve_phase2_engine_from_extraction():
    from src.agents.phase2_intents import resolve_phase2_engine

    assert (
        resolve_phase2_engine(
            user_message="please process",
            detected_engines=["compliance"],
        )
        == "compliance"
    )
    assert (
        resolve_phase2_engine(
            user_message="thanks",
            tool_calls=[{"tool": "extract_and_verify_invoice", "input": {}, "output": {}}],
        )
        == "contract_performance"
    )
    assert (
        resolve_phase2_engine(
            user_message="check certificates",
            filenames=["LOLER_cert_MOB.pdf"],
        )
        == "compliance"
    )
    assert resolve_phase2_engine(user_message="hello") is None
