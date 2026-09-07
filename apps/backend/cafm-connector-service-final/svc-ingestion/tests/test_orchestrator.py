"""
tests/test_orchestrator.py

Tests for Phase 5, Tasks 5.1–5.5:
  - CMSDecision + SingleOrchestratorRun contracts (Task 5.1)
  - EL-6.BOUND validation (Task 5.1)
  - EL-6.VOTE majority vote (Task 5.1)
  - EL-6.CONSTRAIN safety gates (Task 5.1)
  - Intent classifier logic (Task 5.2)
  - Tier 1 SQL safety check (Task 5.3)
  - Tier 1 SQL generation helpers (Task 5.3)

Run: pytest tests/test_orchestrator.py -v
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from analysis.action_schema import CMSDecision, SingleOrchestratorRun
from analysis.orchestrator import (
    _el6_bound,
    _el6_vote,
    _el6_constrain,
    _build_orchestrator_prompt,
)
from data_agents.base_data_agent import AgentResult, SingleRunResult


# ── Helpers ───────────────────────────────────────────────────────────────────


def make_agent_result(
    domain: str = "asset",
    status: str = "operational",
    confidence: float = 0.90,
    requires_human_review: bool = False,
    hard_rules_fired: list[str] | None = None,
) -> AgentResult:
    return AgentResult(
        agent_id=f"{domain}-agent",
        domain=domain,  # type: ignore[arg-type]
        status=status,
        confidence=confidence,
        reasoning="Test reasoning for the agent result.",
        runs=[],
        runs_agreed=3,
        hard_rules_fired=hard_rules_fired or [],
        requires_human_review=requires_human_review,
        audit_id=uuid.uuid4(),
    )


def make_all_agent_results(
    requires_human_review: bool = False,
) -> dict[str, AgentResult]:
    return {
        "asset": make_agent_result("asset", "operational", 0.92, requires_human_review),
        "wo": make_agent_result("wo", "monitor", 0.88, requires_human_review),
        "pm": make_agent_result("pm", "ok", 0.85, requires_human_review),
        "parts": make_agent_result("parts", "low", 0.80, requires_human_review),
        "inspection": make_agent_result("inspection", "Low", 0.90, requires_human_review),
    }


def make_orch_run(
    run_number: int = 1,
    action: str = "no_action",
    priority: str = "low",
    confidence: float = 0.90,
    reasoning: str = "No action needed.",
    contributing_agents: list[str] | None = None,
    valid: bool = True,
    failure_reason: str = "",
) -> SingleOrchestratorRun:
    return SingleOrchestratorRun(
        run_number=run_number,
        action=action,
        priority=priority,
        confidence=confidence,
        reasoning=reasoning,
        contributing_agents=contributing_agents or [],
        valid=valid,
        failure_reason=failure_reason,
    )


# ════════════════════════════════════════════════════════════════════════════
# CMSDecision + SingleOrchestratorRun contracts
# ════════════════════════════════════════════════════════════════════════════


class TestSingleOrchestratorRun:
    def test_basic_creation(self):
        r = make_orch_run()
        assert r.action == "no_action"
        assert r.valid is True

    def test_confidence_rounded(self):
        r = SingleOrchestratorRun(
            run_number=1, action="no_action", priority="low",
            confidence=0.91234, reasoning="test", valid=True,
        )
        assert r.confidence == 0.912

    def test_confidence_bounds(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            SingleOrchestratorRun(
                run_number=1, action="no_action", priority="low",
                confidence=1.5, reasoning="test", valid=True,
            )

    def test_reasoning_truncated_at_60_words(self):
        long = " ".join([f"word{i}" for i in range(70)])
        r = SingleOrchestratorRun(
            run_number=1, action="no_action", priority="low",
            confidence=0.9, reasoning=long, valid=True,
        )
        assert r.reasoning.endswith("…")
        assert len(r.reasoning.split()) <= 61

    def test_invalid_run_stores_failure_reason(self):
        r = make_orch_run(valid=False, failure_reason="json parse error")
        assert r.valid is False
        assert r.failure_reason == "json parse error"


class TestCMSDecision:
    def test_basic_creation(self):
        d = CMSDecision(
            action="no_action",
            asset_code="MOB-AHU-001",
            priority="low",
            confidence=0.92,
            reasoning="All systems operational.",
            runs_agreed=3,
        )
        assert d.action == "no_action"
        assert d.safety_passed is True

    def test_audit_id_auto_generated(self):
        d = CMSDecision(
            action="create_wo", asset_code="MOB-001", priority="high",
            confidence=0.88, reasoning="WO needed.", runs_agreed=2,
        )
        assert isinstance(d.audit_id, uuid.UUID)

    def test_confidence_rounded(self):
        d = CMSDecision(
            action="no_action", asset_code="MOB-001", priority="low",
            confidence=0.91234, reasoning="ok.", runs_agreed=3,
        )
        assert d.confidence == 0.912

    def test_all_valid_actions(self):
        for action in ["create_wo", "order_part", "alert_critical", "no_action", "human_review"]:
            d = CMSDecision(
                action=action, asset_code="A", priority="low",
                confidence=0.9, reasoning="test.", runs_agreed=3,
            )
            assert d.action == action

    def test_all_valid_priorities(self):
        for priority in ["low", "medium", "high", "critical"]:
            d = CMSDecision(
                action="no_action", asset_code="A", priority=priority,
                confidence=0.9, reasoning="test.", runs_agreed=3,
            )
            assert d.priority == priority

    def test_invalid_action_rejected(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            CMSDecision(
                action="invalid_action",  # type: ignore[arg-type]
                asset_code="A", priority="low", confidence=0.9,
                reasoning="test.", runs_agreed=3,
            )

    def test_reasoning_truncated(self):
        long = " ".join([f"word{i}" for i in range(70)])
        d = CMSDecision(
            action="no_action", asset_code="A", priority="low",
            confidence=0.9, reasoning=long, runs_agreed=3,
        )
        assert d.reasoning.endswith("…")

    def test_hard_rules_and_agents_default_empty(self):
        d = CMSDecision(
            action="no_action", asset_code="A", priority="low",
            confidence=0.9, reasoning="test.", runs_agreed=3,
        )
        assert d.hard_rules_fired == []
        assert d.contributing_agents == []


# ════════════════════════════════════════════════════════════════════════════
# EL-6.BOUND
# ════════════════════════════════════════════════════════════════════════════


class TestEL6Bound:
    def test_all_5_present_passes(self):
        results = make_all_agent_results()
        passed, reason = _el6_bound(results)
        assert passed is True
        assert reason == ""

    def test_missing_domain_fails(self):
        results = make_all_agent_results()
        del results["inspection"]
        passed, reason = _el6_bound(results)
        assert passed is False
        assert "inspection" in reason

    def test_multiple_missing_domains_fails(self):
        results = make_all_agent_results()
        del results["asset"]
        del results["wo"]
        passed, reason = _el6_bound(results)
        assert passed is False

    def test_requires_human_review_fails_bound(self):
        results = make_all_agent_results()
        results["parts"] = make_agent_result("parts", "critical", 0.85, requires_human_review=True)
        passed, reason = _el6_bound(results)
        assert passed is False
        assert "parts" in reason

    def test_multiple_human_review_flags(self):
        results = {
            domain: make_agent_result(domain, requires_human_review=True)
            for domain in ["asset", "wo", "pm", "parts", "inspection"]
        }
        passed, reason = _el6_bound(results)
        assert passed is False

    def test_invalid_confidence_fails(self):
        results = make_all_agent_results()
        # Bypass pydantic by directly setting an invalid value
        results["asset"] = AgentResult(
            agent_id="asset-agent", domain="asset", status="critical",
            confidence=0.99, reasoning="test.", runs=[], runs_agreed=3,
        )
        # Manually set to invalid range for test (mock scenario)
        results["asset"].__dict__["confidence"] = 1.5
        passed, reason = _el6_bound(results)
        assert passed is False

    def test_invalid_audit_id_type_fails(self):
        results = make_all_agent_results()
        # Set audit_id to non-UUID
        results["wo"].__dict__["audit_id"] = "not-a-uuid"
        passed, reason = _el6_bound(results)
        assert passed is False

    def test_empty_results_fails(self):
        passed, reason = _el6_bound({})
        assert passed is False


# ════════════════════════════════════════════════════════════════════════════
# EL-6.VOTE
# ════════════════════════════════════════════════════════════════════════════


class TestEL6Vote:
    def test_unanimous_3_0(self):
        runs = [
            make_orch_run(1, "create_wo", "high", 0.91),
            make_orch_run(2, "create_wo", "high", 0.89),
            make_orch_run(3, "create_wo", "high", 0.93),
        ]
        winner, agreed = _el6_vote(runs, "MOB-001")
        assert winner is not None
        assert winner.action == "create_wo"
        assert agreed == 3

    def test_majority_2_1(self):
        runs = [
            make_orch_run(1, "order_part", "medium", 0.88),
            make_orch_run(2, "order_part", "medium", 0.85),
            make_orch_run(3, "no_action", "low", 0.70),
        ]
        winner, agreed = _el6_vote(runs, "MOB-001")
        assert winner is not None
        assert winner.action == "order_part"
        assert agreed == 2

    def test_3_way_split_returns_none(self):
        runs = [
            make_orch_run(1, "create_wo", "high", 0.88),
            make_orch_run(2, "order_part", "medium", 0.85),
            make_orch_run(3, "no_action", "low", 0.80),
        ]
        winner, agreed = _el6_vote(runs, "MOB-001")
        assert winner is None
        assert agreed == 0

    def test_tiebreak_highest_confidence_wins(self):
        runs = [
            make_orch_run(1, "create_wo", "high", 0.85),
            make_orch_run(2, "create_wo", "high", 0.95),  # winner
            make_orch_run(3, "no_action", "low", 0.80),
        ]
        winner, agreed = _el6_vote(runs, "MOB-001")
        assert winner is not None
        assert winner.confidence == 0.95

    def test_invalid_runs_excluded(self):
        runs = [
            make_orch_run(1, "alert_critical", "critical", 0.92, valid=True),
            make_orch_run(2, "no_action", "low", 0.0, valid=False),
            make_orch_run(3, "alert_critical", "critical", 0.90, valid=True),
        ]
        winner, agreed = _el6_vote(runs, "MOB-001")
        assert winner is not None
        assert winner.action == "alert_critical"
        assert agreed == 2

    def test_fewer_than_2_valid_returns_none(self):
        runs = [
            make_orch_run(1, "create_wo", "high", 0.88, valid=False),
            make_orch_run(2, "create_wo", "high", 0.90, valid=False),
            make_orch_run(3, "create_wo", "high", 0.85, valid=True),
        ]
        winner, agreed = _el6_vote(runs, "MOB-001")
        assert winner is None
        assert agreed == 0

    def test_empty_runs_returns_none(self):
        winner, agreed = _el6_vote([], "MOB-001")
        assert winner is None
        assert agreed == 0


# ════════════════════════════════════════════════════════════════════════════
# EL-6.CONSTRAIN
# ════════════════════════════════════════════════════════════════════════════


class TestEL6Constrain:
    def test_normal_action_passes(self):
        winner = make_orch_run(1, "no_action", "low", 0.90)
        results = make_all_agent_results()
        action, priority, rules, safety = _el6_constrain(winner, results)
        assert action == "no_action"
        assert safety is True

    def test_alert_critical_always_fails_safety(self):
        winner = make_orch_run(1, "alert_critical", "critical", 0.95)
        results = make_all_agent_results()
        action, priority, rules, safety = _el6_constrain(winner, results)
        assert action == "alert_critical"
        assert safety is False

    def test_create_wo_passes_safety(self):
        winner = make_orch_run(1, "create_wo", "high", 0.90)
        results = make_all_agent_results()
        action, priority, rules, safety = _el6_constrain(winner, results)
        assert action == "create_wo"
        assert safety is True

    def test_hard_rules_collected_from_all_agents(self):
        winner = make_orch_run(1, "create_wo", "high", 0.90)
        results = {
            "asset": make_agent_result("asset", hard_rules_fired=["open_wo_highest_priority_critical"]),
            "wo": make_agent_result("wo", hard_rules_fired=["highest_priority_overdue_7_days"]),
            "pm": make_agent_result("pm"),
            "parts": make_agent_result("parts", hard_rules_fired=["zero_stock_critical"]),
            "inspection": make_agent_result("inspection"),
        }
        action, priority, rules, safety = _el6_constrain(winner, results)
        assert any("open_wo_highest_priority_critical" in r for r in rules)
        assert any("highest_priority_overdue_7_days" in r for r in rules)
        assert any("zero_stock_critical" in r for r in rules)

    def test_hard_rules_prefixed_with_domain(self):
        winner = make_orch_run(1, "order_part", "high", 0.88)
        results = {
            "parts": make_agent_result("parts", hard_rules_fired=["zero_stock_critical"]),
            **{d: make_agent_result(d) for d in ["asset", "wo", "pm", "inspection"]},
        }
        action, priority, rules, safety = _el6_constrain(winner, results)
        assert "parts:zero_stock_critical" in rules

    def test_no_hard_rules_returns_empty_list(self):
        winner = make_orch_run(1, "no_action", "low", 0.90)
        results = make_all_agent_results()
        action, priority, rules, safety = _el6_constrain(winner, results)
        assert rules == []

    def test_order_part_passes_safety(self):
        winner = make_orch_run(1, "order_part", "medium", 0.87)
        results = make_all_agent_results()
        action, priority, rules, safety = _el6_constrain(winner, results)
        assert action == "order_part"
        assert safety is True

    def test_priority_preserved_from_winner(self):
        winner = make_orch_run(1, "create_wo", "critical", 0.92)
        results = make_all_agent_results()
        action, priority, rules, safety = _el6_constrain(winner, results)
        assert priority == "critical"


# ════════════════════════════════════════════════════════════════════════════
# Orchestrator prompt builder
# ════════════════════════════════════════════════════════════════════════════


class TestBuildOrchestratorPrompt:
    def test_includes_asset_code(self):
        results = make_all_agent_results()
        prompt = _build_orchestrator_prompt("MOB-AHU-001", results)
        assert "MOB-AHU-001" in prompt

    def test_includes_all_domain_summaries(self):
        results = make_all_agent_results()
        prompt = _build_orchestrator_prompt("MOB-001", results)
        for domain in ["ASSET", "WO", "PM", "PARTS", "INSPECTION"]:
            assert domain in prompt

    def test_includes_status_and_confidence(self):
        results = make_all_agent_results()
        prompt = _build_orchestrator_prompt("MOB-001", results)
        assert "operational" in prompt
        assert "0.9" in prompt

    def test_includes_hard_rules_when_present(self):
        results = {
            "asset": make_agent_result("asset", hard_rules_fired=["my_critical_rule"]),
            **{d: make_agent_result(d) for d in ["wo", "pm", "parts", "inspection"]},
        }
        prompt = _build_orchestrator_prompt("MOB-001", results)
        assert "my_critical_rule" in prompt


# ════════════════════════════════════════════════════════════════════════════
# Intent classifier — logic tests (no real API calls)
# ════════════════════════════════════════════════════════════════════════════

# Import from svc-query
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "svc-query", "src"))

try:
    from intent_classifier import (
        ClassificationResult,
        _VALID_INTENTS,
        _CONFIDENCE_THRESHOLD,
        IntentType,
    )
    _INTENT_CLASSIFIER_AVAILABLE = True
except ImportError:
    _INTENT_CLASSIFIER_AVAILABLE = False


@pytest.mark.skipif(not _INTENT_CLASSIFIER_AVAILABLE, reason="svc-query not on path")
class TestIntentClassifier:
    def test_valid_intents_set(self):
        assert "tier1_structured" in _VALID_INTENTS
        assert "tier2_document" in _VALID_INTENTS
        assert "tier3_manual" in _VALID_INTENTS
        assert "document_generate" in _VALID_INTENTS
        assert "template_fill" in _VALID_INTENTS
        assert len(_VALID_INTENTS) == 5

    def test_confidence_threshold_is_80_percent(self):
        assert _CONFIDENCE_THRESHOLD == 0.80

    def test_classification_result_needs_clarification_below_threshold(self):
        result = ClassificationResult(
            intent="tier1_structured",
            confidence=0.75,
            needs_clarification=True,
            clarifying_question="Can you be more specific?",
        )
        assert result.needs_clarification is True
        assert result.clarifying_question is not None

    def test_classification_result_no_clarification_above_threshold(self):
        result = ClassificationResult(
            intent="document_generate",
            confidence=0.90,
            needs_clarification=False,
        )
        assert result.needs_clarification is False
        assert result.clarifying_question is None

    def test_classification_result_document_type_field(self):
        result = ClassificationResult(
            intent="document_generate",
            confidence=0.92,
            document_type="pm_schedule",
            needs_clarification=False,
        )
        assert result.document_type == "pm_schedule"

    @pytest.mark.asyncio
    async def test_classify_intent_api_error_returns_fallback(self):
        """API error should return safe fallback, not raise."""
        import anthropic as _anthropic
        from intent_classifier import classify_intent

        mock_client = AsyncMock()
        mock_client.messages.create.side_effect = _anthropic.APIError(
            message="test error", request=MagicMock(), body={}
        )

        result = await classify_intent("Which assets have open WOs?", mock_client)
        assert result.intent == "tier1_structured"  # safe default
        assert result.needs_clarification is True

    @pytest.mark.asyncio
    async def test_classify_intent_invalid_response_returns_fallback(self):
        """Invalid JSON response should return safe fallback."""
        from intent_classifier import classify_intent

        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="not valid json")]
        mock_response.usage.input_tokens = 10
        mock_response.usage.output_tokens = 5
        mock_client.messages.create.return_value = mock_response

        result = await classify_intent("test query", mock_client)
        assert result.intent == "tier1_structured"
        assert result.needs_clarification is True


# ════════════════════════════════════════════════════════════════════════════
# Tier 1 — SQL safety check
# ════════════════════════════════════════════════════════════════════════════

try:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "svc-query", "src"))
    from tiers.structured_query import _is_safe_select
    _TIER1_AVAILABLE = True
except ImportError:
    _TIER1_AVAILABLE = False


@pytest.mark.skipif(not _TIER1_AVAILABLE, reason="svc-query not on path")
class TestTier1SQLSafety:
    def test_select_is_safe(self):
        assert _is_safe_select("SELECT * FROM plenum_cafm.assets")

    def test_with_cte_is_safe(self):
        assert _is_safe_select(
            "WITH open_wos AS (SELECT * FROM plenum_cafm.work_orders WHERE status='Open') "
            "SELECT * FROM open_wos"
        )

    def test_insert_rejected(self):
        assert not _is_safe_select("INSERT INTO plenum_cafm.assets VALUES (1)")

    def test_update_rejected(self):
        assert not _is_safe_select("UPDATE plenum_cafm.assets SET status='inactive'")

    def test_delete_rejected(self):
        assert not _is_safe_select("DELETE FROM plenum_cafm.assets")

    def test_drop_rejected(self):
        assert not _is_safe_select("DROP TABLE plenum_cafm.assets")

    def test_truncate_rejected(self):
        assert not _is_safe_select("TRUNCATE plenum_cafm.assets")

    def test_alter_rejected(self):
        assert not _is_safe_select("ALTER TABLE plenum_cafm.assets ADD COLUMN x INT")

    def test_empty_sql_rejected(self):
        assert not _is_safe_select("")

    def test_select_with_embedded_delete_rejected(self):
        assert not _is_safe_select(
            "SELECT * FROM t; DELETE FROM plenum_cafm.assets"
        )

    def test_lowercase_select_is_safe(self):
        assert _is_safe_select("select asset_code from plenum_cafm.assets limit 10")

    def test_mixed_case_insert_rejected(self):
        assert not _is_safe_select("Insert Into plenum_cafm.assets VALUES (1)")
