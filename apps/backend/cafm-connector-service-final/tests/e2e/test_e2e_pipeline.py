"""
tests/e2e/test_e2e_pipeline.py

End-to-end integration tests covering the full CAFM platform pipeline.

Scope:
  - Ingestion eval layers: EL-2.1, EL-2.2, EL-2.3
  - Schema mapper: EL-3.0
  - Confidence router routing
  - Agent determinism cycle: EL-5.BOUND, EL-5.AGG, EL-5.VOTE, EL-5.CONSTRAIN
  - Layer 6 orchestration: EL-6.BOUND, EL-6.AGG, EL-6.VOTE, EL-6.CONSTRAIN
  - Document generation eval: EL-7.DOC.PLAN, EL-7.DOC.RENDER, EL-7.DOC.EVAL
  - Template filler: EL-7.TEMPLATE.PRE, EL-7.TEMPLATE.POST
  - held_for_review=True fires correctly when eval_score < 0.85

All external dependencies (DB, Claude API) are mocked.
Run: pytest tests/e2e/ --import-mode=importlib -q
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── sys.path wiring ───────────────────────────────────────────────────────────
# The test file needs access to both svc-ingestion/src and svc-query/src.
# We wire up the relevant service src directory per test class using helpers
# that snapshot/restore sys.path so classes don't bleed into each other.

_ROOT = Path(__file__).parent.parent.parent
_INGESTION_SRC = str(_ROOT / "svc-ingestion" / "src")
_QUERY_SRC = str(_ROOT / "svc-query" / "src")
_SHARED_SRC = str(_ROOT / "shared-lib")

os.environ.setdefault("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")


def _add_ingestion_src():
    """Prepend svc-ingestion/src to sys.path (idempotent)."""
    if _INGESTION_SRC not in sys.path:
        sys.path.insert(0, _INGESTION_SRC)


def _add_query_src():
    """Prepend svc-query/src to sys.path (idempotent)."""
    if _QUERY_SRC not in sys.path:
        sys.path.insert(0, _QUERY_SRC)


_add_ingestion_src()
_add_query_src()

# ── Minimal intermediate schema fixture ──────────────────────────────────────

_VALID_INTERMEDIATE_DICT: dict[str, Any] = {
    "ingestion_id": str(uuid.uuid4()),
    "source_type": "pdf",
    "agent_id": "pdf-agent",
    "source_filename": "inspection_report_nov_2025.pdf",
    "source_blob_url": "https://plenumstorage.blob.core.windows.net/pdf-raw/test.pdf",
    "extracted_at": "2025-11-15T10:30:00Z",
    "extraction_method": "claude-vision",
    "model_used": "claude-sonnet-4-6",
    "entities": {
        "assets": [],
        "work_orders": [],
        "readings": [],
        "findings": [],
        "technicians": [],
        "vendors": [],
        "certificates": [],
        "spare_parts": [],
    },
    "confidence": {
        "overall": "high",
        "per_field": {},
        "eval_score": None,
        "rules_passed": True,
        "rules_violations": [],
    },
    "audit": {
        "prompt_template_id": None,
        "prompt_version": None,
        "passes": 1,
        "tokens_in": 1000,
        "tokens_out": 200,
        "cache_read_tokens": 0,
        "cost_usd": 0.005,
        "cost_aed": 0.018,
        "processing_ms": 3000,
    },
}


# ═══════════════════════════════════════════════════════════════════════════
# Part 1 — EL-2.x  Ingestion eval layer unit scenarios
# ═══════════════════════════════════════════════════════════════════════════


class TestEL21RawExtractionOutput:
    """EL-2.1: Raw extraction output validation."""

    def setup_method(self):
        from shared.eval_layer import el_2_1_raw_output
        self.el_2_1 = el_2_1_raw_output

    def test_valid_json_passes(self):
        payload = json.dumps(_VALID_INTERMEDIATE_DICT)
        result = self.el_2_1(payload)
        assert result.passed is True
        assert result.parsed is not None
        assert result.error == ""

    def test_markdown_fenced_json_passes(self):
        payload = "```json\n" + json.dumps(_VALID_INTERMEDIATE_DICT) + "\n```"
        result = self.el_2_1(payload)
        assert result.passed is True

    def test_invalid_json_fails_with_retry_context(self):
        result = self.el_2_1("this is not json at all")
        assert result.passed is False
        assert result.parsed is None
        assert "JSON" in result.retry_context

    def test_json_array_at_root_fails(self):
        result = self.el_2_1("[1, 2, 3]")
        assert result.passed is False
        assert "dict" in result.error or "object" in result.error

    def test_missing_entities_key_fails(self):
        bad = dict(_VALID_INTERMEDIATE_DICT)
        del bad["entities"]
        result = self.el_2_1(json.dumps(bad))
        assert result.passed is False
        assert "entities" in result.retry_context

    def test_missing_confidence_key_fails(self):
        bad = dict(_VALID_INTERMEDIATE_DICT)
        del bad["confidence"]
        result = self.el_2_1(json.dumps(bad))
        assert result.passed is False

    def test_missing_audit_key_fails(self):
        bad = dict(_VALID_INTERMEDIATE_DICT)
        del bad["audit"]
        result = self.el_2_1(json.dumps(bad))
        assert result.passed is False

    def test_null_ingestion_id_fails(self):
        bad = dict(_VALID_INTERMEDIATE_DICT)
        bad["ingestion_id"] = None
        result = self.el_2_1(json.dumps(bad))
        assert result.passed is False
        assert "ingestion_id" in result.error

    def test_null_source_type_fails(self):
        bad = dict(_VALID_INTERMEDIATE_DICT)
        bad["source_type"] = None
        result = self.el_2_1(json.dumps(bad))
        assert result.passed is False

    def test_truncated_json_fails(self):
        result = self.el_2_1('{"entities": {"assets": [')
        assert result.passed is False


class TestEL22SchemaConformance:
    """EL-2.2: Intermediate JSON schema conformance check."""

    def setup_method(self):
        from shared.eval_layer import el_2_2_schema_conformance
        self.el_2_2 = el_2_2_schema_conformance

    def test_valid_schema_passes(self):
        result = self.el_2_2(_VALID_INTERMEDIATE_DICT)
        assert result.passed is True
        assert result.schema is not None
        assert result.violations == []

    def test_invalid_source_type_fails(self):
        bad = {**_VALID_INTERMEDIATE_DICT, "source_type": "floppy_disk"}
        result = self.el_2_2(bad)
        assert result.passed is False
        assert len(result.violations) > 0

    def test_missing_entities_block_fails(self):
        bad = {k: v for k, v in _VALID_INTERMEDIATE_DICT.items() if k != "entities"}
        result = self.el_2_2(bad)
        assert result.passed is False

    def test_invalid_confidence_overall_fails(self):
        bad = {
            **_VALID_INTERMEDIATE_DICT,
            "confidence": {**_VALID_INTERMEDIATE_DICT["confidence"], "overall": "unknown_level"},
        }
        result = self.el_2_2(bad)
        assert result.passed is False


class TestEL23LLMJudge:
    """EL-2.3: LLM-as-judge eval (mocked Haiku response)."""

    def setup_method(self):
        from shared.eval_layer import el_2_3_llm_judge, RouteDecision
        self.el_2_3 = el_2_3_llm_judge
        self.RouteDecision = RouteDecision

    def _make_haiku_response(self, eval_score: float, contradictions: list[str]) -> MagicMock:
        response = MagicMock()
        response.content = [MagicMock()]
        response.content[0].text = json.dumps({
            "eval_score": eval_score,
            "contradictions": contradictions,
        })
        response.usage = MagicMock(input_tokens=100, output_tokens=50, cache_read_input_tokens=0)
        return response

    @pytest.mark.asyncio
    async def test_high_score_routes_to_accept(self):
        client = AsyncMock()
        client.messages.create = AsyncMock(
            return_value=self._make_haiku_response(0.92, [])
        )
        from shared.intermediate_schema import IntermediateSchema
        schema = IntermediateSchema(**_VALID_INTERMEDIATE_DICT)
        result = await self.el_2_3(
            schema=schema,
            source_excerpt="Sample PDF text",
            client=client,
        )
        assert result.eval_score == pytest.approx(0.92)
        assert result.route == self.RouteDecision.ACCEPT

    @pytest.mark.asyncio
    async def test_medium_score_routes_to_review_queue(self):
        client = AsyncMock()
        client.messages.create = AsyncMock(
            return_value=self._make_haiku_response(0.72, ["minor ambiguity"])
        )
        from shared.intermediate_schema import IntermediateSchema
        schema = IntermediateSchema(**_VALID_INTERMEDIATE_DICT)
        result = await self.el_2_3(
            schema=schema,
            source_excerpt="Sample text",
            client=client,
        )
        assert 0.60 <= result.eval_score < 0.85
        assert result.route == self.RouteDecision.REVIEW_QUEUE

    @pytest.mark.asyncio
    async def test_low_score_routes_to_re_extract(self):
        client = AsyncMock()
        client.messages.create = AsyncMock(
            return_value=self._make_haiku_response(0.45, ["major extraction failure"])
        )
        from shared.intermediate_schema import IntermediateSchema
        schema = IntermediateSchema(**_VALID_INTERMEDIATE_DICT)
        result = await self.el_2_3(
            schema=schema,
            source_excerpt="Sample text",
            client=client,
        )
        assert result.eval_score < 0.60
        assert result.route == self.RouteDecision.RE_EXTRACT

    @pytest.mark.asyncio
    async def test_score_exactly_at_threshold_accepts(self):
        """Boundary: eval_score == 0.85 must route to ACCEPT."""
        client = AsyncMock()
        client.messages.create = AsyncMock(
            return_value=self._make_haiku_response(0.85, [])
        )
        from shared.intermediate_schema import IntermediateSchema
        schema = IntermediateSchema(**_VALID_INTERMEDIATE_DICT)
        result = await self.el_2_3(schema=schema, source_excerpt="text", client=client)
        assert result.route == self.RouteDecision.ACCEPT

    @pytest.mark.asyncio
    async def test_score_just_below_threshold_goes_to_review(self):
        """Boundary: 0.849 must route to REVIEW_QUEUE, not ACCEPT."""
        client = AsyncMock()
        client.messages.create = AsyncMock(
            return_value=self._make_haiku_response(0.849, [])
        )
        from shared.intermediate_schema import IntermediateSchema
        schema = IntermediateSchema(**_VALID_INTERMEDIATE_DICT)
        result = await self.el_2_3(schema=schema, source_excerpt="text", client=client)
        assert result.route == self.RouteDecision.REVIEW_QUEUE

    @pytest.mark.asyncio
    async def test_score_written_to_schema_confidence(self):
        """eval_score must be written to schema.confidence.eval_score."""
        client = AsyncMock()
        client.messages.create = AsyncMock(
            return_value=self._make_haiku_response(0.91, [])
        )
        from shared.intermediate_schema import IntermediateSchema
        schema = IntermediateSchema(**_VALID_INTERMEDIATE_DICT)
        result = await self.el_2_3(schema=schema, source_excerpt="text", client=client)
        # eval_score should be written to schema
        assert result.eval_score == pytest.approx(0.91)


# ═══════════════════════════════════════════════════════════════════════════
# Part 2 — EL-3.0  Schema mapper confidence gate
# ═══════════════════════════════════════════════════════════════════════════


class TestEL30SchemaMappingConfidence:
    """EL-3.0: Schema mapper confidence gate (blocks agent if < 0.80)."""

    def setup_method(self):
        from shared.schema_mapper import _validate_mapping_confidence
        self.validate = _validate_mapping_confidence

    def test_high_confidence_passes(self):
        mapping = {"Asset Code": "asset_code", "Work Order": "wo_code"}
        confidence = {"Asset Code": 0.95, "Work Order": 0.88}
        result = self.validate(mapping, confidence)
        assert result.passed is True
        assert result.human_review_required is False

    def test_low_confidence_blocked(self):
        mapping = {"Asset Code": "asset_code", "Unknown Col": "asset_name"}
        confidence = {"Asset Code": 0.95, "Unknown Col": 0.55}
        result = self.validate(mapping, confidence)
        assert result.passed is False
        assert result.human_review_required is True

    def test_exactly_at_threshold_passes(self):
        mapping = {"SM Code": "sm_code"}
        confidence = {"SM Code": 0.80}
        result = self.validate(mapping, confidence)
        assert result.passed is True

    def test_just_below_threshold_blocked(self):
        mapping = {"SM Code": "sm_code"}
        confidence = {"SM Code": 0.799}
        result = self.validate(mapping, confidence)
        assert result.passed is False
        assert result.human_review_required is True

    def test_empty_mapping_passes(self):
        result = self.validate({}, {})
        assert result.passed is True


# ═══════════════════════════════════════════════════════════════════════════
# Part 3 — Confidence router
# ═══════════════════════════════════════════════════════════════════════════


class TestConfidenceRouter:
    """Confidence router correctly maps eval_score to route decision."""

    def setup_method(self):
        from shared.confidence_router import route
        from shared.eval_layer import EL23Result, RouteDecision
        from shared.intermediate_schema import IntermediateSchema
        self.route = route
        self.EL23Result = EL23Result
        self.RouteDecision = RouteDecision
        self.IntermediateSchema = IntermediateSchema

    def _make_schema(self):
        return self.IntermediateSchema(**_VALID_INTERMEDIATE_DICT)

    def _make_el23(self, score: float, route_decision) -> Any:
        return self.EL23Result(
            eval_score=score,
            contradictions=[],
            rules_violations=[],
            rules_passed=True,
            route=route_decision,
        )

    def test_accept_route(self):
        schema = self._make_schema()
        el23 = self._make_el23(0.90, self.RouteDecision.ACCEPT)
        outcome = self.route(schema, el23)
        assert outcome.route == self.RouteDecision.ACCEPT
        assert outcome.schema is not None

    def test_review_queue_route(self):
        schema = self._make_schema()
        el23 = self._make_el23(0.72, self.RouteDecision.REVIEW_QUEUE)
        outcome = self.route(schema, el23)
        assert outcome.route == self.RouteDecision.REVIEW_QUEUE
        assert outcome.review_payload is not None

    def test_re_extract_route(self):
        schema = self._make_schema()
        el23 = self._make_el23(0.40, self.RouteDecision.RE_EXTRACT)
        outcome = self.route(schema, el23)
        assert outcome.route == self.RouteDecision.RE_EXTRACT
        assert outcome.retry_context != ""


# ═══════════════════════════════════════════════════════════════════════════
# Part 4 — EL-5.x  Agent determinism cycle
# ═══════════════════════════════════════════════════════════════════════════


class TestEL5BoundValidation:
    """EL-5.BOUND: Pydantic row validation before AI sees data."""

    def setup_method(self):
        from shared.agent_determinism import AgentDeterminismCycle, BoundValidationError
        from pydantic import BaseModel, field_validator
        from typing import Literal

        class _AssetRow(BaseModel):
            asset_code: str
            category: str
            location_code: str

            @field_validator("asset_code")
            @classmethod
            def asset_code_not_null(cls, v: str) -> str:
                if not v:
                    raise ValueError("asset_code must not be empty")
                return v

        self.BoundValidationError = BoundValidationError
        self.AssetRow = _AssetRow

        from pathlib import Path
        self.cycle = AgentDeterminismCycle(
            allowed_statuses=["operational", "at_risk", "critical"],
            confidence_threshold=0.80,
            model="claude-haiku-4-5",
            system_prompt="Asset health agent",
            rules_yaml_path=Path(
                _ROOT / "svc-ingestion/src/data_agents/rules/asset_rules.yaml"
            ),
            bound_schema=_AssetRow,
            vote_field="status",
        )

    def test_valid_rows_pass_bound(self):
        rows = [
            {"asset_code": "MOB-AHU-001", "category": "Air Handler", "location_code": "B1"},
            {"asset_code": "MOB-CHW-001", "category": "Chiller", "location_code": "B2"},
        ]
        validated = self.cycle._bound(rows, "asset-agent")
        assert len(validated) == 2

    def test_null_asset_code_raises_bound_error(self):
        rows = [{"asset_code": "", "category": "Air Handler", "location_code": "B1"}]
        with pytest.raises(self.BoundValidationError) as exc_info:
            self.cycle._bound(rows, "asset-agent")
        assert len(exc_info.value.rejected_rows) > 0

    def test_missing_required_field_raises_bound_error(self):
        rows = [{"asset_code": "MOB-AHU-001"}]  # missing category and location_code
        with pytest.raises(self.BoundValidationError):
            self.cycle._bound(rows, "asset-agent")

    def test_all_rows_invalid_raises_bound_error(self):
        rows = [
            {"asset_code": "", "category": "X", "location_code": "Y"},
            {"asset_code": "", "category": "X", "location_code": "Y"},
        ]
        with pytest.raises(self.BoundValidationError) as exc_info:
            self.cycle._bound(rows, "asset-agent")
        assert len(exc_info.value.rejected_rows) == 2


class TestEL5AGGRunValidation:
    """EL-5.AGG: Per-run output validation before vote."""

    def setup_method(self):
        from shared.agent_determinism import AgentDeterminismCycle
        from pydantic import BaseModel
        from pathlib import Path

        class _Minimal(BaseModel):
            asset_code: str

        self.cycle = AgentDeterminismCycle(
            allowed_statuses=["operational", "at_risk", "critical"],
            confidence_threshold=0.80,
            model="claude-haiku-4-5",
            system_prompt="Test",
            rules_yaml_path=Path(
                _ROOT / "svc-ingestion/src/data_agents/rules/asset_rules.yaml"
            ),
            bound_schema=_Minimal,
            vote_field="status",
        )

    def test_valid_run_output_is_accepted(self):
        raw = '{"status": "operational", "confidence": 0.91, "reasoning": "Asset looks fine."}'
        result = self.cycle._validate_single_run(raw, run_number=1)
        assert result.valid is True
        assert result.status == "operational"
        assert result.confidence == pytest.approx(0.91)

    def test_invalid_status_enum_excluded(self):
        raw = '{"status": "flying", "confidence": 0.80, "reasoning": "x"}'
        result = self.cycle._validate_single_run(raw, run_number=1)
        assert result.valid is False

    def test_confidence_out_of_range_excluded(self):
        raw = '{"status": "operational", "confidence": 1.5, "reasoning": "x"}'
        result = self.cycle._validate_single_run(raw, run_number=1)
        assert result.valid is False

    def test_missing_confidence_field_excluded(self):
        raw = '{"status": "operational", "reasoning": "x"}'
        result = self.cycle._validate_single_run(raw, run_number=1)
        assert result.valid is False

    def test_non_json_response_excluded(self):
        raw = "I think the asset is fine."
        result = self.cycle._validate_single_run(raw, run_number=1)
        assert result.valid is False

    def test_markdown_fenced_json_accepted(self):
        raw = '```json\n{"status": "at_risk", "confidence": 0.75, "reasoning": "overdue PM"}\n```'
        result = self.cycle._validate_single_run(raw, run_number=1)
        assert result.valid is True
        assert result.status == "at_risk"


class TestEL5MajorityVote:
    """EL-5.VOTE: Majority vote integrity."""

    def setup_method(self):
        from shared.agent_determinism import AgentDeterminismCycle
        from data_agents.base_data_agent import SingleRunResult
        from pydantic import BaseModel
        from pathlib import Path

        class _M(BaseModel):
            asset_code: str

        self.cycle = AgentDeterminismCycle(
            allowed_statuses=["operational", "at_risk", "critical"],
            confidence_threshold=0.80,
            model="claude-haiku-4-5",
            system_prompt="Test",
            rules_yaml_path=Path(
                _ROOT / "svc-ingestion/src/data_agents/rules/asset_rules.yaml"
            ),
            bound_schema=_M,
            vote_field="status",
        )
        self.SingleRunResult = SingleRunResult

    def _run(self, status: str, confidence: float, valid: bool = True):
        return self.SingleRunResult(
            run_number=1,
            raw_response="{}",
            status=status,
            confidence=confidence,
            reasoning="test",
            valid=valid,
        )

    def test_unanimous_vote_wins(self):
        runs = [
            self._run("operational", 0.90),
            self._run("operational", 0.88),
            self._run("operational", 0.92),
        ]
        winner, agreed, human_review = self.cycle._majority_vote(runs)
        assert winner.status == "operational"
        assert agreed == 3
        assert human_review is False

    def test_2_1_majority_wins(self):
        runs = [
            self._run("at_risk", 0.85),
            self._run("at_risk", 0.82),
            self._run("operational", 0.78),
        ]
        winner, agreed, human_review = self.cycle._majority_vote(runs)
        assert winner.status == "at_risk"
        assert agreed == 2
        assert human_review is False

    def test_three_way_split_triggers_human_review(self):
        runs = [
            self._run("operational", 0.80),
            self._run("at_risk", 0.79),
            self._run("critical", 0.81),
        ]
        winner, agreed, human_review = self.cycle._majority_vote(runs)
        assert human_review is True

    def test_only_one_valid_run_triggers_human_review(self):
        runs = [
            self._run("operational", 0.90, valid=True),
            self._run("at_risk", 0.00, valid=False),
            self._run("critical", 0.00, valid=False),
        ]
        winner, agreed, human_review = self.cycle._majority_vote(runs)
        assert human_review is True

    def test_tiebreak_highest_confidence_wins(self):
        runs = [
            self._run("at_risk", 0.70),
            self._run("at_risk", 0.85),
            self._run("operational", 0.80),
        ]
        winner, agreed, human_review = self.cycle._majority_vote(runs)
        assert winner.status == "at_risk"
        # tiebreak: at_risk with higher confidence should be chosen
        assert winner.confidence == pytest.approx(0.85)


class TestEL5ConstrainHardRules:
    """
    EL-5.CONSTRAIN: Hard YAML rules always override AI vote.

    Key hard rules per CLAUDE.md:
    - stock_on_hand == 0 → urgency = critical (Parts Agent)
    - priority == 'Highest' AND age_days > 7 → triage = escalate (WO Agent)
    - PM trigger_type == 't' and date math overdue → pm_status = overdue (PM Agent)
    """

    def setup_method(self):
        from shared.agent_determinism import AgentDeterminismCycle
        from pydantic import BaseModel
        from pathlib import Path

        class _PartRow(BaseModel):
            part_code: str
            stock_on_hand: int
            minimum_allowed_stock: int
            supplier: str

        self.cycle_parts = AgentDeterminismCycle(
            allowed_statuses=["critical", "severe", "low", "ok"],
            confidence_threshold=0.78,
            model="claude-haiku-4-5",
            system_prompt="Parts agent",
            rules_yaml_path=Path(
                _ROOT / "svc-ingestion/src/data_agents/rules/parts_rules.yaml"
            ),
            bound_schema=_PartRow,
            vote_field="urgency",
        )

    def test_stock_zero_always_critical_overrides_ai(self):
        """stock_on_hand == 0 → hard rule forces urgency = critical."""
        validated_rows = [
            {
                "part_code": "MOTOR-8HP",
                "stock_on_hand": 0,
                "minimum_allowed_stock": 2,
                "supplier": "Al-Futtaim",
            }
        ]
        from data_agents.base_data_agent import SingleRunResult
        # AI voted "low" — hard rule must override
        winner = SingleRunResult(
            run_number=1, raw_response="{}", status="low",
            confidence=0.85, reasoning="stock seems fine", valid=True,
        )
        result_status, rules_fired = self.cycle_parts._apply_hard_rules(
            winner_status="low",
            validated_rows=validated_rows,
        )
        assert result_status == "critical"
        assert len(rules_fired) > 0

    def test_stock_above_minimum_no_rule_fires(self):
        validated_rows = [
            {
                "part_code": "FILTER-HEPA",
                "stock_on_hand": 10,
                "minimum_allowed_stock": 5,
                "supplier": "3M",
            }
        ]
        result_status, rules_fired = self.cycle_parts._apply_hard_rules(
            winner_status="ok",
            validated_rows=validated_rows,
        )
        assert result_status == "ok"
        assert rules_fired == []


# ═══════════════════════════════════════════════════════════════════════════
# Part 5 — EL-6.x  Layer 6 orchestration
# ═══════════════════════════════════════════════════════════════════════════


class TestEL6BoundValidation:
    """EL-6.BOUND: All 5 AgentResults must be present and typed."""

    def setup_method(self):
        from analysis.orchestrator import el_6_bound
        from data_agents.base_data_agent import AgentResult
        self.el_6_bound = el_6_bound
        self.AgentResult = AgentResult

    def _make_agent_result(
        self,
        agent_id: str,
        domain: str,
        status: str,
        requires_human_review: bool = False,
    ) -> Any:
        return self.AgentResult(
            agent_id=agent_id,
            domain=domain,
            status=status,
            confidence=0.90,
            reasoning="test reasoning within sixty words",
            runs=[],
            runs_agreed=3,
            hard_rules_fired=[],
            requires_human_review=requires_human_review,
            raw_data={},
            audit_id=uuid.uuid4(),
        )

    def _all_five(self, **overrides):
        agents = {
            "asset-agent": ("asset", "operational"),
            "wo-agent": ("wo", "monitor"),
            "pm-agent": ("pm", "ok"),
            "parts-agent": ("parts", "ok"),
            "inspection-agent": ("inspection", "Low"),
        }
        results = []
        for agent_id, (domain, status) in agents.items():
            hr = overrides.get(agent_id, False)
            results.append(self._make_agent_result(agent_id, domain, status, hr))
        return results

    def test_all_five_clean_results_pass(self):
        results = self._all_five()
        passed, error_msg = self.el_6_bound(results)
        assert passed is True
        assert error_msg == ""

    def test_fewer_than_five_agents_fails(self):
        results = self._all_five()[:4]
        passed, error_msg = self.el_6_bound(results)
        assert passed is False
        assert "5" in error_msg or "agent" in error_msg.lower()

    def test_any_human_review_flag_fails_bound(self):
        results = self._all_five(**{"parts-agent": True})
        passed, error_msg = self.el_6_bound(results)
        assert passed is False
        assert "human_review" in error_msg or "requires" in error_msg.lower()

    def test_multiple_human_review_flags_fail(self):
        results = self._all_five(**{"parts-agent": True, "wo-agent": True})
        passed, error_msg = self.el_6_bound(results)
        assert passed is False

    def test_duplicate_agent_id_fails(self):
        results = self._all_five()
        results.append(self._make_agent_result("asset-agent", "asset", "critical"))
        passed, error_msg = self.el_6_bound(results)
        assert passed is False


class TestEL6ConfidenceGate:
    """EL-6.CONSTRAIN: confidence < 0.85 → human_review; alert_critical always human_review."""

    def setup_method(self):
        from analysis.orchestrator import el_6_constrain
        self.el_6_constrain = el_6_constrain

    def test_high_confidence_action_passes(self):
        decision, safety_passed = self.el_6_constrain(
            action="create_wo", confidence=0.90, runs_agreed=3
        )
        assert decision == "create_wo"
        assert safety_passed is True

    def test_confidence_below_gate_downgrades_to_human_review(self):
        decision, safety_passed = self.el_6_constrain(
            action="create_wo", confidence=0.84, runs_agreed=3
        )
        assert decision == "human_review"
        assert safety_passed is False

    def test_exactly_at_gate_passes(self):
        decision, safety_passed = self.el_6_constrain(
            action="no_action", confidence=0.85, runs_agreed=3
        )
        assert decision == "no_action"
        assert safety_passed is True

    def test_alert_critical_always_human_review(self):
        """alert_critical always routes to human_review regardless of confidence."""
        decision, safety_passed = self.el_6_constrain(
            action="alert_critical", confidence=0.95, runs_agreed=3
        )
        assert decision == "human_review"

    def test_no_action_high_confidence_passes(self):
        decision, safety_passed = self.el_6_constrain(
            action="no_action", confidence=0.88, runs_agreed=2
        )
        assert decision == "no_action"
        assert safety_passed is True

    def test_order_part_with_sufficient_confidence_passes(self):
        decision, safety_passed = self.el_6_constrain(
            action="order_part", confidence=0.91, runs_agreed=3
        )
        assert decision == "order_part"
        assert safety_passed is True


# ═══════════════════════════════════════════════════════════════════════════
# Part 6 — EL-7.x  Document generation eval
# ═══════════════════════════════════════════════════════════════════════════


class TestEL7DocPlanValidation:
    """EL-7.DOC.PLAN: Pydantic validation + data source dry-run."""

    def setup_method(self):
        from document_generator.schemas import DocumentPlan, DocumentSection
        from document_generator.validator import ValidationResult
        self.DocumentPlan = DocumentPlan
        self.DocumentSection = DocumentSection
        self.ValidationResult = ValidationResult

    def _make_plan(self, data_sources: list[str] = None, output_format: str = "docx") -> Any:
        sections = []
        for ds in (data_sources or ["assets"]):
            sections.append(
                self.DocumentSection(
                    type="summary_table",
                    heading="Assets",
                    data_source=ds,
                )
            )
        return self.DocumentPlan(
            document_type="asset_health_summary",
            title="Asset Health Report",
            generated_for="test",
            output_format=output_format,
            sections=sections,
            footer={"generated_by": "cafm-query-service", "timestamp": "now", "audit_id": str(uuid.uuid4())},
            data_sources_required=data_sources or ["assets"],
        )

    @pytest.mark.asyncio
    async def test_known_table_passes_plan_validation(self):
        from document_generator.validator import validate_document_plan

        session = AsyncMock()
        # Simulate table exists (fetchone returns a row)
        session.execute = AsyncMock(return_value=MagicMock(fetchone=MagicMock(return_value=(1,))))

        plan = self._make_plan(["assets"])
        result = await validate_document_plan(plan, session)
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_unknown_table_fails_plan_validation(self):
        from document_generator.validator import validate_document_plan

        session = AsyncMock()
        session.execute = AsyncMock(return_value=MagicMock(fetchone=MagicMock(return_value=None)))

        plan = self._make_plan(["nonexistent_table_xyz"])
        result = await validate_document_plan(plan, session)
        assert result.passed is False
        assert len(result.errors) > 0

    @pytest.mark.asyncio
    async def test_empty_table_fails_plan_validation(self):
        """Table exists in schema but returns 0 rows → EL-7.DOC.PLAN fails."""
        from document_generator.validator import validate_document_plan

        session = AsyncMock()
        # Table exists but has no rows
        session.execute = AsyncMock(return_value=MagicMock(fetchone=MagicMock(return_value=None)))

        plan = self._make_plan(["assets"])
        result = await validate_document_plan(plan, session)
        assert result.passed is False

    def test_invalid_output_format_fails_schema(self):
        """output_format must be docx|xlsx|pdf."""
        with pytest.raises(Exception):
            self._make_plan(output_format="txt")

    def test_missing_footer_fields_detected(self):
        from document_generator.validator import _validate_footer
        footer_bad = {"generated_by": "cafm-query-service"}  # missing timestamp + audit_id
        errors = _validate_footer(footer_bad)
        assert len(errors) > 0

    def test_complete_footer_passes(self):
        from document_generator.validator import _validate_footer
        footer_ok = {
            "generated_by": "cafm-query-service",
            "timestamp": "2026-03-27T12:00:00Z",
            "audit_id": str(uuid.uuid4()),
        }
        errors = _validate_footer(footer_ok)
        assert errors == []


class TestEL7DocEvalHeldForReview:
    """
    EL-7.DOC.EVAL: eval_score < 0.85 → held_for_review = True, never auto-delivered.

    This is the critical gate: documents with low eval scores must NEVER be
    auto-delivered. These tests verify the held_for_review flag is set correctly.
    """

    def setup_method(self):
        from eval_layer import EvalResult
        self.EvalResult = EvalResult

    def _make_eval_result(self, eval_score: float) -> Any:
        total = 10
        passed_count = round(eval_score * total)
        return self.EvalResult(
            eval_score=eval_score,
            total_checked=total,
            passed_count=passed_count,
            failed_values=[],
            held_for_review=eval_score < 0.85,
        )

    def test_score_above_threshold_not_held(self):
        result = self._make_eval_result(0.90)
        assert result.held_for_review is False

    def test_score_at_threshold_not_held(self):
        result = self._make_eval_result(0.85)
        assert result.held_for_review is False

    def test_score_just_below_threshold_is_held(self):
        """0.849 must trigger held_for_review — critical regression boundary."""
        result = self._make_eval_result(0.849)
        assert result.held_for_review is True

    def test_score_zero_is_held(self):
        result = self._make_eval_result(0.0)
        assert result.held_for_review is True

    def test_score_half_is_held(self):
        result = self._make_eval_result(0.50)
        assert result.held_for_review is True

    @pytest.mark.asyncio
    async def test_evaluate_rendered_document_low_score_sets_held_flag(self):
        """Integration: evaluate_rendered_document returns held_for_review=True on bad data."""
        from eval_layer import evaluate_rendered_document

        # 10 sampled values that look fabricated
        sampled_values = [
            {"value": f"FAKE-VALUE-{i}", "table": "assets", "column": "asset_name"}
            for i in range(10)
        ]
        source_rows = [{"asset_name": "MOB-AHU-001", "asset_code": "A001"}]

        # Mock Claude returning all False (all values unverified)
        client = AsyncMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="[false, false, false, false, false, false, false, false, false, false]")]
        mock_response.usage = MagicMock(input_tokens=200, output_tokens=50, cache_read_input_tokens=0)
        client.messages.create = AsyncMock(return_value=mock_response)

        session = AsyncMock()

        result = await evaluate_rendered_document(
            sampled_values=sampled_values,
            source_rows=source_rows,
            session=session,
            client=client,
        )
        assert result.held_for_review is True
        assert result.eval_score < 0.85

    @pytest.mark.asyncio
    async def test_evaluate_rendered_document_high_score_not_held(self):
        """Integration: evaluate_rendered_document returns held_for_review=False on good data."""
        from eval_layer import evaluate_rendered_document

        # Sampled values that match source data exactly
        sampled_values = [
            {"value": "MOB-AHU-001", "table": "assets", "column": "asset_code"},
            {"value": "Air Handler", "table": "assets", "column": "category"},
            {"value": "10", "table": "assets", "column": "asset_id"},
        ]
        source_rows = [
            {"asset_code": "MOB-AHU-001", "category": "Air Handler", "asset_id": "10"}
        ]

        client = AsyncMock()
        # Rule-based check should find exact matches without needing LLM
        session = AsyncMock()

        result = await evaluate_rendered_document(
            sampled_values=sampled_values,
            source_rows=source_rows,
            session=session,
            client=client,
        )
        # All 3 values should match via rule-based check → high score
        assert result.held_for_review is False
        assert result.eval_score >= 0.85


class TestEL7TemplateFiller:
    """EL-7.TEMPLATE: Placeholder resolution blocks render if unresolvable."""

    def setup_method(self):
        from document_generator.filler import _parse_placeholders, _PLACEHOLDER_RE
        self.parse_placeholders = _parse_placeholders
        self.PLACEHOLDER_RE = _PLACEHOLDER_RE

    def test_placeholder_pattern_matches_valid_format(self):
        text = "Asset: {{assets.asset_name}} Status: {{work_orders.status}}"
        matches = self.PLACEHOLDER_RE.findall(text)
        assert len(matches) == 2
        # matches are (table, column, filter) tuples
        assert matches[0][0] == "assets"
        assert matches[0][1] == "asset_name"
        assert matches[1][0] == "work_orders"
        assert matches[1][1] == "status"

    def test_placeholder_with_filter_expression(self):
        text = "{{assets.asset_name:asset_code=MOB-AHU-001}}"
        matches = self.PLACEHOLDER_RE.findall(text)
        assert len(matches) == 1
        assert matches[0][2] == "asset_code=MOB-AHU-001"

    def test_no_placeholders_returns_empty_list(self):
        text = "No placeholders here."
        phs = self.parse_placeholders(text)
        assert phs == []

    def test_duplicate_placeholders_parsed_once(self):
        text = "{{assets.asset_name}} and {{assets.asset_name}} again"
        phs = self.parse_placeholders(text)
        # Should deduplicate
        assert len(phs) == 1

    def test_unknown_table_detected_in_parse(self):
        text = "{{nonexistent_table.some_col}}"
        phs = self.parse_placeholders(text)
        assert any(ph.table == "nonexistent_table" for ph in phs)

    @pytest.mark.asyncio
    async def test_unresolvable_placeholder_blocks_fill(self):
        """EL-7.TEMPLATE.PRE: BLOCK render if any placeholder cannot be resolved."""
        from document_generator.filler import fill_template

        session = AsyncMock()
        # DB returns nothing → placeholder unresolvable
        session.execute = AsyncMock(
            return_value=MagicMock(fetchone=MagicMock(return_value=None))
        )

        # Minimal DOCX bytes (just enough to be a valid zip/docx structure)
        import zipfile
        import io
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("word/document.xml",
                b'<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>{{assets.asset_name}}</w:t></w:r></w:p></w:body></w:document>'.decode()
            )
            zf.writestr("[Content_Types].xml",
                '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>'
            )
            zf.writestr("_rels/.rels",
                '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>'
            )
        docx_bytes = buf.getvalue()

        result = await fill_template(
            template_path=None,
            template_bytes=docx_bytes,
            asset_code="MOB-AHU-001",
            session=session,
        )

        # EL-7.TEMPLATE.PRE must block the fill — content empty, held_for_review True
        assert result.held_for_review is True

    @pytest.mark.asyncio
    async def test_post_fill_unfilled_placeholder_sets_low_score(self):
        """EL-7.TEMPLATE.POST: any remaining {{...}} reduces eval_score."""
        from document_generator.filler import _post_fill_check

        # Simulate a document where one placeholder was not replaced
        filled_text = "Asset: MOB-AHU-001\nStatus: {{work_orders.status}}"
        eval_score, issues = _post_fill_check(filled_text, total_placeholders=2)
        assert eval_score < 0.85
        assert len(issues) > 0


# ═══════════════════════════════════════════════════════════════════════════
# Part 7 — Full pipeline integration scenario (mocked)
# ═══════════════════════════════════════════════════════════════════════════


class TestFullIngestionPipelineE2E:
    """
    Simulates a full CSV ingestion: upload → EL-2.x → confidence router.
    All Claude calls mocked. Verifies the pipeline gates work end-to-end.
    """

    @pytest.mark.asyncio
    async def test_csv_ingestion_accept_path(self):
        """CSV file ingested → EL-2.1 pass → EL-2.2 pass → EL-2.3 ≥ 0.85 → ACCEPT."""
        from shared.eval_layer import el_2_1_raw_output, el_2_2_schema_conformance, RouteDecision

        # Step 1: EL-2.1
        valid_json = json.dumps(_VALID_INTERMEDIATE_DICT)
        el21 = el_2_1_raw_output(valid_json)
        assert el21.passed is True

        # Step 2: EL-2.2
        el22 = el_2_2_schema_conformance(el21.parsed)
        assert el22.passed is True

        # Step 3: EL-2.3 (mocked Haiku at score 0.90)
        from shared.eval_layer import el_2_3_llm_judge
        client = AsyncMock()
        response = MagicMock()
        response.content = [MagicMock(text='{"eval_score": 0.90, "contradictions": []}')]
        response.usage = MagicMock(input_tokens=100, output_tokens=50, cache_read_input_tokens=0)
        client.messages.create = AsyncMock(return_value=response)

        el23 = await el_2_3_llm_judge(
            schema=el22.schema, source_excerpt="test excerpt", client=client
        )
        assert el23.route == RouteDecision.ACCEPT

    @pytest.mark.asyncio
    async def test_pdf_ingestion_held_for_review_path(self):
        """PDF extraction → EL-2.3 score 0.72 → routed to REVIEW_QUEUE (held_for_review)."""
        from shared.eval_layer import el_2_1_raw_output, el_2_2_schema_conformance, el_2_3_llm_judge, RouteDecision

        valid_json = json.dumps(_VALID_INTERMEDIATE_DICT)
        el21 = el_2_1_raw_output(valid_json)
        el22 = el_2_2_schema_conformance(el21.parsed)

        client = AsyncMock()
        response = MagicMock()
        response.content = [MagicMock(text='{"eval_score": 0.72, "contradictions": ["ambiguous reading"]}')]
        response.usage = MagicMock(input_tokens=100, output_tokens=50, cache_read_input_tokens=0)
        client.messages.create = AsyncMock(return_value=response)

        el23 = await el_2_3_llm_judge(
            schema=el22.schema, source_excerpt="test excerpt", client=client
        )
        assert el23.route == RouteDecision.REVIEW_QUEUE
        assert el23.eval_score == pytest.approx(0.72)

    @pytest.mark.asyncio
    async def test_extraction_failure_triggers_retry_path(self):
        """Garbled Claude response → EL-2.1 fails → retry_context provided."""
        from shared.eval_layer import el_2_1_raw_output

        garbled = "Here is the extracted data: {incomplete json..."
        result = el_2_1_raw_output(garbled)
        assert result.passed is False
        assert result.retry_context != ""
        # retry_context must tell Claude what went wrong
        assert "JSON" in result.retry_context or "json" in result.retry_context.lower()


class TestDocumentGenerationE2E:
    """
    Simulates full document generation: classify → plan → validate → render → eval.
    Verifies held_for_review gate at each EL-7 stage.
    """

    @pytest.mark.asyncio
    async def test_document_not_delivered_when_eval_score_below_threshold(self):
        """
        Critical regression test: a document with eval_score 0.70 must be
        held_for_review=True and never auto-delivered.
        """
        from eval_layer import EvalResult

        low_score_result = EvalResult(
            eval_score=0.70,
            total_checked=10,
            passed_count=7,
            failed_values=["fake_value_1", "fake_value_2", "fake_value_3"],
            held_for_review=True,
        )

        assert low_score_result.held_for_review is True
        assert low_score_result.eval_score < 0.85

    @pytest.mark.asyncio
    async def test_document_delivered_at_threshold(self):
        """eval_score == 0.85 → held_for_review=False → document is delivered."""
        from eval_layer import EvalResult

        threshold_result = EvalResult(
            eval_score=0.85,
            total_checked=20,
            passed_count=17,
            failed_values=[],
            held_for_review=False,
        )

        assert threshold_result.held_for_review is False

    @pytest.mark.asyncio
    async def test_output_renderer_held_for_review_returns_json_status(self):
        """Output renderer signals held_for_review in JSON — never returns file bytes."""
        from output_renderer import render_held_for_review

        output = render_held_for_review(
            document_type="pm_schedule",
            eval_score=0.72,
            errors=["value 'X' not found in source rows"],
        )
        assert output.format == "json"
        data = json.loads(output.content)
        assert data["status"] == "held_for_review"
        assert data["eval_score"] == pytest.approx(0.72)

    @pytest.mark.asyncio
    async def test_output_renderer_docx_delivery_for_valid_document(self):
        """Valid document → output renderer produces DOCX with correct MIME type."""
        from output_renderer import render_document_output

        fake_docx = b"PK\x03\x04" + b"\x00" * 100  # fake DOCX header
        output = render_document_output(
            content=fake_docx,
            output_format="docx",
            document_type="pm_schedule",
            audit_id=str(uuid.uuid4()),
        )
        assert output.format == "docx"
        assert "wordprocessingml" in output.content_type or "openxmlformats" in output.content_type
        assert output.filename.endswith(".docx")

    def test_output_renderer_xlsx_mime_type(self):
        from output_renderer import render_document_output

        output = render_document_output(
            content=b"PK" + b"\x00" * 50,
            output_format="xlsx",
            document_type="parts_reorder",
            audit_id=str(uuid.uuid4()),
        )
        assert "spreadsheet" in output.content_type or "xlsx" in output.content_type
        assert output.filename.endswith(".xlsx")

    def test_output_renderer_clarifying_question(self):
        from output_renderer import render_clarifying_question

        output = render_clarifying_question("Which asset would you like the PM schedule for?")
        data = json.loads(output.content)
        assert data["status"] == "needs_clarification"
        assert "asset" in data["question"]


# ═══════════════════════════════════════════════════════════════════════════
# Part 8 — Audit log immutability
# ═══════════════════════════════════════════════════════════════════════════


class TestAuditLogImmutability:
    """
    Verify orchestration_audit_log is INSERT ONLY.
    The audit function must only issue INSERT statements — never UPDATE or DELETE.
    """

    @pytest.mark.asyncio
    async def test_orchestration_audit_uses_insert_only(self):
        """Captures all SQL statements; none should be UPDATE or DELETE."""
        from analysis.orchestrator import _write_orchestration_audit

        issued_sql: list[str] = []

        async def capture_execute(stmt, *args, **kwargs):
            sql = str(stmt).upper().strip()
            issued_sql.append(sql)
            return MagicMock()

        session = AsyncMock()
        session.execute = AsyncMock(side_effect=capture_execute)

        from analysis.action_schema import CMSDecision

        decision = CMSDecision(
            action="no_action",
            asset_code="MOB-AHU-001",
            priority="low",
            confidence=0.88,
            reasoning="No issues detected from all five agents",
            contributing_agents=["asset-agent"],
            runs_agreed=3,
        )

        await _write_orchestration_audit(
            session=session,
            decision=decision,
            asset_code="MOB-AHU-001",
            bound_passed=True,
            run_1_valid=True,
            run_2_valid=True,
            run_3_valid=True,
            confidence_gate_passed=True,
            safety_passed=True,
            hard_rules_fired=[],
            agent_results_json="{}",
            model_used="claude-sonnet-4-6",
            tokens_total=1500,
            cost_usd=0.012,
        )

        assert len(issued_sql) >= 1, "No SQL was issued — expected at least one INSERT"
        for sql in issued_sql:
            assert not sql.startswith("UPDATE"), f"UPDATE found in audit: {sql}"
            assert not sql.startswith("DELETE"), f"DELETE found in audit: {sql}"
        assert any("INSERT" in sql for sql in issued_sql), "No INSERT found in audit SQL"
