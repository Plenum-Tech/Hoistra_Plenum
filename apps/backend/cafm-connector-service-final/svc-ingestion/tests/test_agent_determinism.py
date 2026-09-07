"""
tests/test_agent_determinism.py

Tests for Phase 4:
  - AgentResult / SingleRunResult contracts (Task 4.1)
  - AgentDeterminismCycle._bound() (EL-5.BOUND)
  - AgentDeterminismCycle._majority_vote() (EL-5.VOTE)
  - AgentDeterminismCycle._evaluate_rule_conditions() (EL-5.CONSTRAIN)
  - AgentDeterminismCycle._row_matches() (condition evaluation)
  - BoundValidationError
  - YAML rule loading
  - Asset / WO / PM / Parts / Inspection bound schemas

Run: pytest tests/test_agent_determinism.py -v
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

# ── Imports under test ────────────────────────────────────────────────────────

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from data_agents.base_data_agent import AgentResult, SingleRunResult
from shared.agent_determinism import AgentDeterminismCycle, BoundValidationError

# Bound schemas from each agent
from data_agents.asset_agent import AssetBoundRow
from data_agents.wo_agent import WOBoundRow
from data_agents.pm_agent import PMBoundRow
from data_agents.parts_agent import PartsBoundRow
from data_agents.inspection_agent import InspectionBoundRow


# ── Helpers ───────────────────────────────────────────────────────────────────


def make_cycle(
    allowed_statuses: list[str] | None = None,
    confidence_threshold: float = 0.80,
    rules_yaml: str | None = None,
    tmp_path: Path | None = None,
) -> AgentDeterminismCycle:
    """Build a minimal cycle for unit tests (no real model calls needed)."""
    statuses = allowed_statuses or ["operational", "at_risk", "critical"]

    # Write a minimal YAML rules file if requested
    if rules_yaml and tmp_path:
        rules_path = tmp_path / "rules.yaml"
        rules_path.write_text(rules_yaml)
    else:
        rules_path = Path("/nonexistent/rules.yaml")  # will return [] gracefully

    from pydantic import BaseModel as _BaseModel

    class _MinimalSchema(_BaseModel):
        value: str = ""

    return AgentDeterminismCycle(
        allowed_statuses=statuses,
        confidence_threshold=confidence_threshold,
        model="claude-haiku-4-5",
        system_prompt="Test system prompt.",
        rules_yaml_path=rules_path,
        bound_schema=_MinimalSchema,
        vote_field="status",
    )


def make_run(
    run_number: int = 1,
    status: str = "operational",
    confidence: float = 0.9,
    reasoning: str = "Asset is operational.",
    valid: bool = True,
    failure_reason: str = "",
) -> SingleRunResult:
    return SingleRunResult(
        run_number=run_number,
        status=status,
        confidence=confidence,
        reasoning=reasoning,
        raw_response="{}",
        valid=valid,
        failure_reason=failure_reason,
    )


# ════════════════════════════════════════════════════════════════════════════
# SingleRunResult
# ════════════════════════════════════════════════════════════════════════════


class TestSingleRunResult:
    def test_basic_creation(self):
        r = make_run()
        assert r.run_number == 1
        assert r.status == "operational"
        assert r.valid is True

    def test_confidence_rounded_to_3_decimals(self):
        r = SingleRunResult(
            run_number=1, status="ok", confidence=0.123456789,
            reasoning="test", valid=True
        )
        assert r.confidence == 0.123

    def test_confidence_bounds(self):
        with pytest.raises(ValidationError):
            SingleRunResult(run_number=1, status="ok", confidence=1.5, reasoning="x", valid=True)
        with pytest.raises(ValidationError):
            SingleRunResult(run_number=1, status="ok", confidence=-0.1, reasoning="x", valid=True)

    def test_reasoning_truncated_at_60_words(self):
        long = " ".join([f"word{i}" for i in range(70)])
        r = SingleRunResult(run_number=1, status="ok", confidence=0.9, reasoning=long, valid=True)
        assert len(r.reasoning.split()) <= 61  # 60 words + "…"
        assert r.reasoning.endswith("…")

    def test_reasoning_under_60_words_unchanged(self):
        short = "The asset is operational."
        r = SingleRunResult(run_number=1, status="ok", confidence=0.9, reasoning=short, valid=True)
        assert r.reasoning == short

    def test_invalid_run_has_failure_reason(self):
        r = make_run(valid=False, failure_reason="json parse error")
        assert r.valid is False
        assert r.failure_reason == "json parse error"

    def test_defaults(self):
        r = SingleRunResult(
            run_number=2, status="at_risk", confidence=0.75, reasoning="needs attention", valid=True
        )
        assert r.raw_response == ""
        assert r.failure_reason == ""


# ════════════════════════════════════════════════════════════════════════════
# AgentResult
# ════════════════════════════════════════════════════════════════════════════


class TestAgentResult:
    def test_basic_creation(self):
        result = AgentResult(
            agent_id="asset-agent",
            domain="asset",
            status="operational",
            confidence=0.92,
            reasoning="All clear.",
            runs=[make_run()],
            runs_agreed=3,
        )
        assert result.agent_id == "asset-agent"
        assert result.domain == "asset"
        assert result.requires_human_review is False

    def test_audit_id_auto_generated(self):
        r = AgentResult(
            agent_id="wo-agent", domain="wo", status="escalate",
            confidence=0.88, reasoning="Escalate.", runs=[], runs_agreed=2,
        )
        assert isinstance(r.audit_id, uuid.UUID)

    def test_confidence_rounded(self):
        r = AgentResult(
            agent_id="pm-agent", domain="pm", status="overdue",
            confidence=0.876543, reasoning="Overdue.", runs=[], runs_agreed=3,
        )
        assert r.confidence == 0.877

    def test_confidence_bounds(self):
        with pytest.raises(ValidationError):
            AgentResult(
                agent_id="x", domain="asset", status="ok",
                confidence=1.5, reasoning="x", runs=[], runs_agreed=0,
            )

    def test_domain_literal_validation(self):
        with pytest.raises(ValidationError):
            AgentResult(
                agent_id="x", domain="invalid_domain", status="ok",  # type: ignore[arg-type]
                confidence=0.8, reasoning="x", runs=[], runs_agreed=0,
            )

    def test_reasoning_truncated(self):
        long = " ".join([f"word{i}" for i in range(70)])
        r = AgentResult(
            agent_id="x", domain="parts", status="critical",
            confidence=0.9, reasoning=long, runs=[], runs_agreed=3,
        )
        assert r.reasoning.endswith("…")

    def test_requires_human_review_default_false(self):
        r = AgentResult(
            agent_id="x", domain="inspection", status="Low",
            confidence=0.9, reasoning="ok.", runs=[], runs_agreed=3,
        )
        assert r.requires_human_review is False

    def test_hard_rules_fired_default_empty(self):
        r = AgentResult(
            agent_id="x", domain="asset", status="ok",
            confidence=0.9, reasoning="ok.", runs=[], runs_agreed=3,
        )
        assert r.hard_rules_fired == []

    def test_all_domains_valid(self):
        for domain in ["asset", "wo", "pm", "parts", "inspection"]:
            r = AgentResult(
                agent_id="x", domain=domain, status="ok",  # type: ignore[arg-type]
                confidence=0.8, reasoning="ok.", runs=[], runs_agreed=2,
            )
            assert r.domain == domain


# ════════════════════════════════════════════════════════════════════════════
# EL-5.BOUND
# ════════════════════════════════════════════════════════════════════════════


class TestEL5Bound:
    def test_valid_rows_pass(self, tmp_path):
        from pydantic import BaseModel as BM

        class Schema(BM):
            name: str

        cycle = AgentDeterminismCycle(
            allowed_statuses=["ok"],
            confidence_threshold=0.8,
            model="claude-haiku-4-5",
            system_prompt="x",
            rules_yaml_path=tmp_path / "r.yaml",
            bound_schema=Schema,
        )
        rows = [{"name": "foo"}, {"name": "bar"}]
        validated = cycle._bound(rows, "test-agent")
        assert len(validated) == 2

    def test_invalid_row_raises_bound_error(self, tmp_path):
        from pydantic import BaseModel as BM

        class Schema(BM):
            count: int

        cycle = AgentDeterminismCycle(
            allowed_statuses=["ok"],
            confidence_threshold=0.8,
            model="claude-haiku-4-5",
            system_prompt="x",
            rules_yaml_path=tmp_path / "r.yaml",
            bound_schema=Schema,
        )
        rows = [{"count": "not_an_int"}]
        with pytest.raises(BoundValidationError) as exc_info:
            cycle._bound(rows, "test-agent")
        assert len(exc_info.value.rejected_rows) == 1

    def test_partial_failure_raises_for_any_invalid(self, tmp_path):
        from pydantic import BaseModel as BM

        class Schema(BM):
            value: int

        cycle = AgentDeterminismCycle(
            allowed_statuses=["ok"],
            confidence_threshold=0.8,
            model="claude-haiku-4-5",
            system_prompt="x",
            rules_yaml_path=tmp_path / "r.yaml",
            bound_schema=Schema,
        )
        rows = [{"value": 1}, {"value": "bad"}, {"value": 3}]
        with pytest.raises(BoundValidationError) as exc_info:
            cycle._bound(rows, "test-agent")
        assert len(exc_info.value.rejected_rows) == 1

    def test_empty_rows_pass(self, tmp_path):
        from pydantic import BaseModel as BM

        class Schema(BM):
            x: str = "default"

        cycle = AgentDeterminismCycle(
            allowed_statuses=["ok"],
            confidence_threshold=0.8,
            model="claude-haiku-4-5",
            system_prompt="x",
            rules_yaml_path=tmp_path / "r.yaml",
            bound_schema=Schema,
        )
        result = cycle._bound([], "test-agent")
        assert result == []


# ════════════════════════════════════════════════════════════════════════════
# EL-5.VOTE
# ════════════════════════════════════════════════════════════════════════════


class TestEL5Vote:
    def test_unanimous_3_way_agreement(self, tmp_path):
        cycle = make_cycle(tmp_path=tmp_path)
        runs = [
            make_run(1, "operational", 0.9),
            make_run(2, "operational", 0.92),
            make_run(3, "operational", 0.88),
        ]
        winner, agreed = cycle._majority_vote(runs, "test")
        assert winner is not None
        assert winner.status == "operational"
        assert agreed == 3

    def test_2_1_majority(self, tmp_path):
        cycle = make_cycle(tmp_path=tmp_path)
        runs = [
            make_run(1, "at_risk", 0.85),
            make_run(2, "at_risk", 0.80),
            make_run(3, "operational", 0.75),
        ]
        winner, agreed = cycle._majority_vote(runs, "test")
        assert winner is not None
        assert winner.status == "at_risk"
        assert agreed == 2

    def test_3_way_split_returns_none(self, tmp_path):
        cycle = make_cycle(tmp_path=tmp_path)
        runs = [
            make_run(1, "operational", 0.8),
            make_run(2, "at_risk", 0.8),
            make_run(3, "critical", 0.8),
        ]
        winner, agreed = cycle._majority_vote(runs, "test")
        assert winner is None
        assert agreed == 0

    def test_tiebreak_picks_highest_confidence(self, tmp_path):
        cycle = make_cycle(tmp_path=tmp_path)
        runs = [
            make_run(1, "at_risk", 0.75),
            make_run(2, "at_risk", 0.91),  # highest confidence winner
            make_run(3, "operational", 0.85),
        ]
        winner, agreed = cycle._majority_vote(runs, "test")
        assert winner is not None
        assert winner.confidence == 0.91

    def test_only_valid_runs_counted(self, tmp_path):
        cycle = make_cycle(tmp_path=tmp_path)
        runs = [
            make_run(1, "critical", 0.9, valid=True),
            make_run(2, "operational", 0.0, valid=False),  # invalid — excluded
            make_run(3, "critical", 0.85, valid=True),
        ]
        winner, agreed = cycle._majority_vote(runs, "test")
        assert winner is not None
        assert winner.status == "critical"
        assert agreed == 2

    def test_fewer_than_2_valid_returns_none(self, tmp_path):
        cycle = make_cycle(tmp_path=tmp_path)
        runs = [
            make_run(1, "operational", 0.9, valid=False),
            make_run(2, "operational", 0.0, valid=False),
            make_run(3, "operational", 0.85, valid=True),
        ]
        winner, agreed = cycle._majority_vote(runs, "test")
        assert winner is None
        assert agreed == 0

    def test_empty_runs_returns_none(self, tmp_path):
        cycle = make_cycle(tmp_path=tmp_path)
        winner, agreed = cycle._majority_vote([], "test")
        assert winner is None
        assert agreed == 0


# ════════════════════════════════════════════════════════════════════════════
# EL-5.CONSTRAIN — row_matches + rule evaluation
# ════════════════════════════════════════════════════════════════════════════


class TestRowMatches:
    def test_eq_operator(self):
        row = {"status": "Open", "priority": "Highest"}
        assert AgentDeterminismCycle._row_matches(row, "status", "eq", "Open")
        assert not AgentDeterminismCycle._row_matches(row, "status", "eq", "Closed")

    def test_gte_operator(self):
        row = {"age_days": 10}
        assert AgentDeterminismCycle._row_matches(row, "age_days", "gte", 7)
        assert AgentDeterminismCycle._row_matches(row, "age_days", "gte", 10)
        assert not AgentDeterminismCycle._row_matches(row, "age_days", "gte", 11)

    def test_lte_operator(self):
        row = {"stock": 5}
        assert AgentDeterminismCycle._row_matches(row, "stock", "lte", 10)
        assert not AgentDeterminismCycle._row_matches(row, "stock", "lte", 4)

    def test_gt_operator(self):
        row = {"count": 3}
        assert AgentDeterminismCycle._row_matches(row, "count", "gt", 2)
        assert not AgentDeterminismCycle._row_matches(row, "count", "gt", 3)

    def test_lt_operator(self):
        row = {"count": 2}
        assert AgentDeterminismCycle._row_matches(row, "count", "lt", 3)
        assert not AgentDeterminismCycle._row_matches(row, "count", "lt", 2)

    def test_neq_operator(self):
        row = {"status": "Open"}
        assert AgentDeterminismCycle._row_matches(row, "status", "neq", "Closed")
        assert not AgentDeterminismCycle._row_matches(row, "status", "neq", "Open")

    def test_in_operator(self):
        row = {"priority": "Highest"}
        assert AgentDeterminismCycle._row_matches(row, "priority", "in", ["Highest", "High"])
        assert not AgentDeterminismCycle._row_matches(row, "priority", "in", ["Low", "Lowest"])

    def test_not_in_operator(self):
        row = {"status": "Closed"}
        assert AgentDeterminismCycle._row_matches(row, "status", "not_in", ["Open"])
        assert not AgentDeterminismCycle._row_matches(row, "status", "not_in", ["Closed"])

    def test_is_null_operator(self):
        row = {"asset_code": None}
        assert AgentDeterminismCycle._row_matches(row, "asset_code", "is_null", None)
        row2 = {"asset_code": "MOB-001"}
        assert not AgentDeterminismCycle._row_matches(row2, "asset_code", "is_null", None)

    def test_is_not_null_operator(self):
        row = {"asset_code": "MOB-001"}
        assert AgentDeterminismCycle._row_matches(row, "asset_code", "is_not_null", None)
        row2 = {"asset_code": None}
        assert not AgentDeterminismCycle._row_matches(row2, "asset_code", "is_not_null", None)

    def test_missing_field_returns_false(self):
        row = {"other_field": "value"}
        assert not AgentDeterminismCycle._row_matches(row, "missing_field", "eq", "x")

    def test_nested_field_dot_notation(self):
        row = {"asset": {"priority": "Highest"}}
        assert AgentDeterminismCycle._row_matches(row, "asset.priority", "eq", "Highest")
        assert not AgentDeterminismCycle._row_matches(row, "asset.priority", "eq", "Low")

    def test_invalid_numeric_comparison_returns_false(self):
        row = {"value": "not_a_number"}
        assert not AgentDeterminismCycle._row_matches(row, "value", "gte", 5)


class TestRuleConditionEvaluation:
    def _make_cycle_with_rules(self, rules_yaml: str, tmp_path: Path) -> AgentDeterminismCycle:
        path = tmp_path / "test_rules.yaml"
        path.write_text(rules_yaml)
        from pydantic import BaseModel as BM

        class Schema(BM):
            x: str = ""

        return AgentDeterminismCycle(
            allowed_statuses=["ok", "critical"],
            confidence_threshold=0.8,
            model="claude-haiku-4-5",
            system_prompt="x",
            rules_yaml_path=path,
            bound_schema=Schema,
        )

    def test_any_row_where_fires(self, tmp_path):
        rules_yaml = """
rules:
  - name: test_rule
    conditions:
      - type: any_row_where
        field: status
        operator: eq
        value: "Open"
    action:
      set_status: critical
"""
        cycle = self._make_cycle_with_rules(rules_yaml, tmp_path)
        rows = [{"status": "Open"}, {"status": "Closed"}]
        assert cycle._evaluate_rule_conditions(
            cycle._load_rules()[0]["conditions"], rows
        )

    def test_any_row_where_no_match(self, tmp_path):
        rules_yaml = """
rules:
  - name: test_rule
    conditions:
      - type: any_row_where
        field: status
        operator: eq
        value: "Open"
    action:
      set_status: critical
"""
        cycle = self._make_cycle_with_rules(rules_yaml, tmp_path)
        rows = [{"status": "Closed"}, {"status": "Resolved"}]
        assert not cycle._evaluate_rule_conditions(
            cycle._load_rules()[0]["conditions"], rows
        )

    def test_count_where_gte_meets_threshold(self, tmp_path):
        rules_yaml = """
rules:
  - name: test_count_rule
    conditions:
      - type: count_where_gte
        field: priority
        operator: eq
        value: "Highest"
        threshold: 3
    action:
      set_status: critical
"""
        cycle = self._make_cycle_with_rules(rules_yaml, tmp_path)
        rows = [
            {"priority": "Highest"},
            {"priority": "Highest"},
            {"priority": "Highest"},
            {"priority": "High"},
        ]
        assert cycle._evaluate_rule_conditions(
            cycle._load_rules()[0]["conditions"], rows
        )

    def test_count_where_gte_below_threshold(self, tmp_path):
        rules_yaml = """
rules:
  - name: test_count_rule
    conditions:
      - type: count_where_gte
        field: priority
        operator: eq
        value: "Highest"
        threshold: 4
    action:
      set_status: critical
"""
        cycle = self._make_cycle_with_rules(rules_yaml, tmp_path)
        rows = [{"priority": "Highest"}, {"priority": "Highest"}, {"priority": "High"}]
        assert not cycle._evaluate_rule_conditions(
            cycle._load_rules()[0]["conditions"], rows
        )

    def test_field_contains_match(self, tmp_path):
        rules_yaml = """
rules:
  - name: keyword_rule
    conditions:
      - type: field_contains
        field: observations
        operator: eq
        value: "corrective"
    action:
      set_status: critical
      requires_human_review: true
"""
        cycle = self._make_cycle_with_rules(rules_yaml, tmp_path)
        rows = [{"observations": "Immediate corrective action required."}]
        assert cycle._evaluate_rule_conditions(
            cycle._load_rules()[0]["conditions"], rows
        )

    def test_field_contains_case_insensitive(self, tmp_path):
        rules_yaml = """
rules:
  - name: keyword_rule
    conditions:
      - type: field_contains
        field: observations
        operator: eq
        value: "CRITICAL"
    action:
      set_status: critical
"""
        cycle = self._make_cycle_with_rules(rules_yaml, tmp_path)
        rows = [{"observations": "This is a critical safety issue."}]
        assert cycle._evaluate_rule_conditions(
            cycle._load_rules()[0]["conditions"], rows
        )

    def test_multiple_conditions_all_must_match(self, tmp_path):
        rules_yaml = """
rules:
  - name: multi_cond_rule
    conditions:
      - type: any_row_where
        field: priority
        operator: eq
        value: "Highest"
      - type: any_row_where
        field: age_days
        operator: gte
        value: 7
    action:
      set_status: critical
"""
        cycle = self._make_cycle_with_rules(rules_yaml, tmp_path)

        # Both conditions met
        rows_both = [{"priority": "Highest", "age_days": 10}]
        assert cycle._evaluate_rule_conditions(
            cycle._load_rules()[0]["conditions"], rows_both
        )

        # Only priority matches, age_days doesn't
        rows_partial = [{"priority": "Highest", "age_days": 2}]
        assert not cycle._evaluate_rule_conditions(
            cycle._load_rules()[0]["conditions"], rows_partial
        )

    def test_missing_rules_file_returns_empty(self, tmp_path):
        cycle = make_cycle(tmp_path=tmp_path)
        cycle.rules_yaml_path = Path("/nonexistent/path.yaml")
        cycle._rules = None  # reset cache
        rules = cycle._load_rules()
        assert rules == []

    @pytest.mark.asyncio
    async def test_apply_hard_rules_fires_and_overrides(self, tmp_path):
        rules_yaml = """
rules:
  - name: zero_stock_critical
    conditions:
      - type: any_row_where
        field: stock_on_hand
        operator: eq
        value: 0
    action:
      set_status: critical
"""
        path = tmp_path / "rules.yaml"
        path.write_text(rules_yaml)
        from pydantic import BaseModel as BM

        class Schema(BM):
            x: str = ""

        cycle = AgentDeterminismCycle(
            allowed_statuses=["ok", "severe", "critical"],
            confidence_threshold=0.78,
            model="claude-haiku-4-5",
            system_prompt="x",
            rules_yaml_path=path,
            bound_schema=Schema,
        )
        rows = [{"stock_on_hand": 0, "part_code": "MOTOR-8HP"}]
        fired, final_status, requires_review = await cycle._apply_hard_rules(
            rows, "severe", "parts-agent"
        )
        assert "zero_stock_critical" in fired
        assert final_status == "critical"

    @pytest.mark.asyncio
    async def test_apply_hard_rules_requires_human_review(self, tmp_path):
        rules_yaml = """
rules:
  - name: high_risk_review
    conditions:
      - type: any_row_where
        field: risk_level
        operator: eq
        value: "High"
    action:
      set_status: High
      requires_human_review: true
"""
        path = tmp_path / "rules.yaml"
        path.write_text(rules_yaml)
        from pydantic import BaseModel as BM

        class Schema(BM):
            x: str = ""

        cycle = AgentDeterminismCycle(
            allowed_statuses=["High", "Medium", "Low"],
            confidence_threshold=0.85,
            model="claude-sonnet-4-6",
            system_prompt="x",
            rules_yaml_path=path,
            bound_schema=Schema,
        )
        rows = [{"risk_level": "High", "observations": "Erosion found."}]
        fired, final_status, requires_review = await cycle._apply_hard_rules(
            rows, "Medium", "inspection-agent"
        )
        assert "high_risk_review" in fired
        assert final_status == "High"
        assert requires_review is True

    @pytest.mark.asyncio
    async def test_apply_hard_rules_no_match_preserves_status(self, tmp_path):
        rules_yaml = """
rules:
  - name: only_fires_on_open
    conditions:
      - type: any_row_where
        field: status
        operator: eq
        value: "Open"
    action:
      set_status: escalate
"""
        path = tmp_path / "rules.yaml"
        path.write_text(rules_yaml)
        from pydantic import BaseModel as BM

        class Schema(BM):
            x: str = ""

        cycle = AgentDeterminismCycle(
            allowed_statuses=["escalate", "monitor", "routine"],
            confidence_threshold=0.82,
            model="claude-haiku-4-5",
            system_prompt="x",
            rules_yaml_path=path,
            bound_schema=Schema,
        )
        rows = [{"status": "Closed"}]
        fired, final_status, requires_review = await cycle._apply_hard_rules(
            rows, "routine", "wo-agent"
        )
        assert fired == []
        assert final_status == "routine"
        assert requires_review is False


# ════════════════════════════════════════════════════════════════════════════
# Bound schemas — per-agent validation
# ════════════════════════════════════════════════════════════════════════════


class TestAssetBoundRow:
    def test_valid_row(self):
        row = AssetBoundRow(asset_code="MOB-AHU-001", category="Air Handler", location_code="L1")
        assert row.asset_code == "MOB-AHU-001"

    def test_empty_asset_code_rejected(self):
        with pytest.raises(ValidationError):
            AssetBoundRow(asset_code="", category="Air Handler")

    def test_whitespace_asset_code_rejected(self):
        with pytest.raises(ValidationError):
            AssetBoundRow(asset_code="   ", category="Air Handler")

    def test_optional_fields_allowed(self):
        row = AssetBoundRow(asset_code="MOB-001")
        assert row.category is None
        assert row.location_code is None


class TestWOBoundRow:
    def test_valid_row(self):
        row = WOBoundRow(wo_code="WO-001", priority="Highest", status="Open")
        assert row.priority == "Highest"

    def test_invalid_priority_rejected(self):
        with pytest.raises(ValidationError):
            WOBoundRow(wo_code="WO-001", priority="URGENT", status="Open")

    def test_invalid_status_rejected(self):
        with pytest.raises(ValidationError):
            WOBoundRow(wo_code="WO-001", priority="High", status="Pending")

    def test_empty_wo_code_rejected(self):
        with pytest.raises(ValidationError):
            WOBoundRow(wo_code="", priority="High", status="Open")

    def test_all_valid_priorities(self):
        for p in ["Highest", "High", "Medium", "Low", "Lowest"]:
            row = WOBoundRow(wo_code="WO-1", priority=p, status="Open")
            assert row.priority == p

    def test_all_valid_statuses(self):
        for s in ["Open", "Closed"]:
            row = WOBoundRow(wo_code="WO-1", priority="High", status=s)
            assert row.status == s


class TestPMBoundRow:
    def test_valid_time_based(self):
        row = PMBoundRow(sm_code="PM-001", trigger_type="t", schedule_interval=3, asset_code="A1")
        assert row.trigger_type == "t"

    def test_valid_meter_based(self):
        row = PMBoundRow(sm_code="PM-002", trigger_type="m", schedule_interval=1000, asset_code="A2")
        assert row.trigger_type == "m"

    def test_invalid_trigger_type_rejected(self):
        with pytest.raises(ValidationError):
            PMBoundRow(sm_code="PM-1", trigger_type="x", schedule_interval=3, asset_code="A1")

    def test_zero_interval_rejected(self):
        with pytest.raises(ValidationError):
            PMBoundRow(sm_code="PM-1", trigger_type="t", schedule_interval=0, asset_code="A1")

    def test_negative_interval_rejected(self):
        with pytest.raises(ValidationError):
            PMBoundRow(sm_code="PM-1", trigger_type="t", schedule_interval=-1, asset_code="A1")


class TestPartsBoundRow:
    def test_valid_row(self):
        row = PartsBoundRow(part_code="MOTOR-8HP", stock_on_hand=0, minimum_allowed_stock=2)
        assert row.stock_on_hand == 0

    def test_negative_stock_rejected(self):
        with pytest.raises(ValidationError):
            PartsBoundRow(part_code="X", stock_on_hand=-1, minimum_allowed_stock=5)

    def test_zero_minimum_rejected(self):
        with pytest.raises(ValidationError):
            PartsBoundRow(part_code="X", stock_on_hand=1, minimum_allowed_stock=0)

    def test_negative_minimum_rejected(self):
        with pytest.raises(ValidationError):
            PartsBoundRow(part_code="X", stock_on_hand=1, minimum_allowed_stock=-1)

    def test_empty_part_code_rejected(self):
        with pytest.raises(ValidationError):
            PartsBoundRow(part_code="", stock_on_hand=1, minimum_allowed_stock=5)

    def test_zero_stock_is_valid_bound_row(self):
        # Zero stock is valid input — the hard rule handles the classification
        row = PartsBoundRow(part_code="MOTOR-8HP", stock_on_hand=0, minimum_allowed_stock=2)
        assert row.stock_on_hand == 0


class TestInspectionBoundRow:
    def test_valid_row(self):
        from datetime import date
        row = InspectionBoundRow(
            id=uuid.uuid4(),
            section="B",
            inspection_date=date(2025, 11, 15),
        )
        assert row.section == "B"

    def test_invalid_section_rejected(self):
        with pytest.raises(ValidationError):
            InspectionBoundRow(id=uuid.uuid4(), section="Z")

    def test_future_date_rejected(self):
        from datetime import date, timedelta
        with pytest.raises(ValidationError):
            InspectionBoundRow(
                id=uuid.uuid4(),
                section="A",
                inspection_date=date.today() + timedelta(days=1),
            )

    def test_today_date_allowed(self):
        from datetime import date
        row = InspectionBoundRow(id=uuid.uuid4(), section="C", inspection_date=date.today())
        assert row.inspection_date == date.today()

    def test_all_valid_sections(self):
        for s in ["A", "B", "C", "D", "E", "F", "G"]:
            row = InspectionBoundRow(id=uuid.uuid4(), section=s)
            assert row.section == s


# ════════════════════════════════════════════════════════════════════════════
# YAML rules files — load + validate structure
# ════════════════════════════════════════════════════════════════════════════


class TestYAMLRulesFiles:
    """Verify that each agent's YAML rules file exists and has valid structure."""

    _RULES_BASE = (
        Path(__file__).parent.parent / "src" / "data_agents" / "rules"
    )

    def _load(self, filename: str) -> list[dict]:
        import yaml
        path = self._RULES_BASE / filename
        assert path.exists(), f"Rules file not found: {path}"
        with open(path) as f:
            data = yaml.safe_load(f)
        assert "rules" in data, f"'rules' key missing in {filename}"
        return data["rules"]

    def test_asset_rules_loadable(self):
        rules = self._load("asset_rules.yaml")
        assert len(rules) >= 1
        for r in rules:
            assert "name" in r
            assert "conditions" in r
            assert "action" in r

    def test_wo_rules_loadable(self):
        rules = self._load("wo_rules.yaml")
        assert len(rules) >= 1

    def test_pm_rules_loadable(self):
        rules = self._load("pm_rules.yaml")
        assert len(rules) >= 1

    def test_parts_rules_loadable(self):
        rules = self._load("parts_rules.yaml")
        assert len(rules) >= 1

    def test_inspection_rules_loadable(self):
        rules = self._load("inspection_rules.yaml")
        assert len(rules) >= 1

    def test_asset_rules_has_critical_rule(self):
        rules = self._load("asset_rules.yaml")
        names = [r["name"] for r in rules]
        assert "open_wo_highest_priority_critical" in names

    def test_wo_rules_has_escalate_rule(self):
        rules = self._load("wo_rules.yaml")
        names = [r["name"] for r in rules]
        assert "highest_priority_overdue_7_days" in names

    def test_pm_rules_has_date_math_rule(self):
        rules = self._load("pm_rules.yaml")
        names = [r["name"] for r in rules]
        assert "time_based_pm_overdue" in names

    def test_parts_rules_has_zero_stock_rule(self):
        rules = self._load("parts_rules.yaml")
        names = [r["name"] for r in rules]
        assert "zero_stock_critical" in names

    def test_inspection_rules_has_high_risk_review(self):
        rules = self._load("inspection_rules.yaml")
        names = [r["name"] for r in rules]
        assert "high_risk_always_human_review" in names

    def test_wo_rules_four_highest_requires_review(self):
        rules = self._load("wo_rules.yaml")
        review_rule = next(
            (r for r in rules if r["name"] == "four_or_more_highest_same_asset"), None
        )
        assert review_rule is not None
        assert review_rule["action"].get("requires_human_review") is True

    def test_inspection_high_risk_requires_review(self):
        rules = self._load("inspection_rules.yaml")
        high_rule = next(
            (r for r in rules if r["name"] == "high_risk_always_human_review"), None
        )
        assert high_rule is not None
        assert high_rule["action"].get("requires_human_review") is True


# ════════════════════════════════════════════════════════════════════════════
# BoundValidationError
# ════════════════════════════════════════════════════════════════════════════


class TestBoundValidationError:
    def test_stores_rejected_rows(self):
        bad_rows = [{"row": {"x": "bad"}, "error": "field missing"}]
        exc = BoundValidationError("1 row(s) failed", bad_rows)
        assert len(exc.rejected_rows) == 1
        assert "1 row(s) failed" in str(exc)

    def test_is_exception(self):
        exc = BoundValidationError("fail", [])
        assert isinstance(exc, Exception)


# ════════════════════════════════════════════════════════════════════════════
# AgentDeterminismCycle — _build_user_prompt
# ════════════════════════════════════════════════════════════════════════════


class TestBuildUserPrompt:
    def test_includes_rows_and_question(self, tmp_path):
        cycle = make_cycle(tmp_path=tmp_path)
        rows = [{"asset_code": "MOB-001", "category": "Chiller"}]
        context = {"question": "What is the status?"}
        prompt = cycle._build_user_prompt(rows, context)
        assert "MOB-001" in prompt
        assert "What is the status?" in prompt
        assert "operational" in prompt  # allowed statuses listed

    def test_default_question_used_if_missing(self, tmp_path):
        cycle = make_cycle(tmp_path=tmp_path)
        prompt = cycle._build_user_prompt([], {})
        assert "Analyse this data" in prompt
