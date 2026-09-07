"""
tests/test_eval_layer.py

Unit tests for shared/eval_layer.py — Tasks 3.1 + 3.2.

Covers:
  - EL-2.1: el_2_1_raw_output() — valid JSON, missing keys, null fields, fences
  - EL-2.2: el_2_2_schema_conformance() — Pydantic validation, violations
  - EL-2.3: el_2_3_llm_judge() — mocked Haiku, score routing, rules engine
  - YAML contradiction rules: _load_contradiction_rules(), _apply_contradiction_rules()
  - RouteDecision: _route_by_score() thresholds
  - apply_eval_score_to_schema(): writes eval result back to schema
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from shared.eval_layer import (
    EL21Result,
    EL22Result,
    EL23Result,
    RouteDecision,
    _apply_contradiction_rules,
    _load_contradiction_rules,
    _parse_judge_response,
    _route_by_score,
    apply_eval_score_to_schema,
    el_2_1_raw_output,
    el_2_2_schema_conformance,
    el_2_3_llm_judge,
)


# ===========================================================================
# Helpers
# ===========================================================================

def _minimal_schema_dict(
    *,
    source_type: str = "pdf",
    agent_id: str = "pdf-agent",
    extra_entities: dict | None = None,
) -> dict[str, Any]:
    """Build a minimal valid intermediate schema dict for EL-2.2 testing."""
    entities: dict[str, Any] = extra_entities or {}
    return {
        "ingestion_id": str(uuid4()),
        "source_type": source_type,
        "agent_id": agent_id,
        "source_filename": "test.pdf",
        "source_blob_url": "https://example.com/test.pdf",
        "extracted_at": "2025-11-15T10:30:00Z",
        "extraction_method": "claude-vision",
        "model_used": "claude-sonnet-4-6",
        "entities": entities,
        "confidence": {
            "overall": "high",
            "per_field": {"asset_code": "high"},
            "eval_score": 0.0,
            "rules_passed": True,
            "rules_violations": [],
        },
        "audit": {
            "passes": 1,
            "tokens_in": 1000,
            "tokens_out": 200,
            "cost_usd": 0.005,
        },
    }


# ===========================================================================
# EL-2.1 — Raw extraction output validation
# ===========================================================================


class TestEL21RawOutput:
    def test_valid_json_passes(self):
        raw = json.dumps(
            {
                "entities": {"assets": []},
                "confidence": {"overall": "high", "per_field": {}},
                "audit": {"passes": 1},
            }
        )
        result = el_2_1_raw_output(raw)
        assert result.passed is True
        assert result.parsed is not None
        assert "entities" in result.parsed
        assert result.error == ""

    def test_invalid_json_fails(self):
        result = el_2_1_raw_output("not json at all {{{")
        assert result.passed is False
        assert result.parsed is None
        assert "JSON" in result.error
        assert "Return ONLY valid JSON" in result.retry_context

    def test_markdown_fence_stripped(self):
        raw = "```json\n{\"entities\": {}, \"confidence\": {}, \"audit\": {}}\n```"
        result = el_2_1_raw_output(raw)
        assert result.passed is True
        assert result.parsed is not None

    def test_fence_without_lang_tag_stripped(self):
        raw = "```\n{\"entities\": {}, \"confidence\": {}, \"audit\": {}}\n```"
        result = el_2_1_raw_output(raw)
        assert result.passed is True

    def test_missing_entities_key_fails(self):
        raw = json.dumps({"confidence": {}, "audit": {}})
        result = el_2_1_raw_output(raw)
        assert result.passed is False
        assert "entities" in result.error

    def test_missing_confidence_key_fails(self):
        raw = json.dumps({"entities": {}, "audit": {}})
        result = el_2_1_raw_output(raw)
        assert result.passed is False
        assert "confidence" in result.error

    def test_missing_audit_key_fails(self):
        raw = json.dumps({"entities": {}, "confidence": {}})
        result = el_2_1_raw_output(raw)
        assert result.passed is False
        assert "audit" in result.error

    def test_null_ingestion_id_fails(self):
        raw = json.dumps(
            {
                "ingestion_id": None,
                "entities": {},
                "confidence": {},
                "audit": {},
            }
        )
        result = el_2_1_raw_output(raw)
        assert result.passed is False
        assert "ingestion_id" in result.error

    def test_null_source_type_fails(self):
        raw = json.dumps(
            {
                "source_type": None,
                "entities": {},
                "confidence": {},
                "audit": {},
            }
        )
        result = el_2_1_raw_output(raw)
        assert result.passed is False
        assert "source_type" in result.error

    def test_array_root_fails(self):
        raw = json.dumps([{"entities": {}, "confidence": {}, "audit": {}}])
        result = el_2_1_raw_output(raw)
        assert result.passed is False
        assert "dict" in result.error.lower() or "object" in result.error.lower()

    def test_retry_context_present_on_failure(self):
        result = el_2_1_raw_output("{bad json}")
        assert result.retry_context != ""

    def test_all_keys_present_passes(self):
        """Missing optional keys (ingestion_id absent) should still pass EL-2.1."""
        raw = json.dumps({"entities": {}, "confidence": {}, "audit": {}})
        result = el_2_1_raw_output(raw)
        assert result.passed is True

    def test_empty_string_fails(self):
        result = el_2_1_raw_output("")
        assert result.passed is False


# ===========================================================================
# EL-2.2 — Schema conformance
# ===========================================================================


class TestEL22SchemaConformance:
    def test_valid_minimal_passes(self):
        d = _minimal_schema_dict()
        result = el_2_2_schema_conformance(d)
        assert result.passed is True
        assert result.schema is not None
        assert result.violations == []

    def test_missing_per_field_fails(self):
        d = _minimal_schema_dict()
        d["confidence"].pop("per_field")
        result = el_2_2_schema_conformance(d)
        assert result.passed is False
        assert any("per_field" in v for v in result.violations)

    def test_invalid_overall_confidence_fails(self):
        d = _minimal_schema_dict()
        d["confidence"]["overall"] = "super_high"  # invalid enum value
        result = el_2_2_schema_conformance(d)
        # Pydantic should reject invalid enum
        assert result.passed is False

    def test_invalid_asset_entity_fails(self):
        """AssetEntity requires at least one of: asset_code, serial_number, name."""
        d = _minimal_schema_dict(
            extra_entities={
                "assets": [
                    {
                        "asset_code": None,
                        "serial_number": None,
                        "name": None,
                        "category": "Air Handler",
                    }
                ]
            }
        )
        result = el_2_2_schema_conformance(d)
        assert result.passed is False

    def test_valid_asset_entity_passes(self):
        d = _minimal_schema_dict(
            extra_entities={
                "assets": [{"asset_code": "MOB-AHU-001", "category": "Air Handler"}]
            }
        )
        result = el_2_2_schema_conformance(d)
        assert result.passed is True
        assert result.schema is not None
        assert len(result.schema.entities.assets) == 1

    def test_invalid_source_type_fails(self):
        d = _minimal_schema_dict(source_type="fax_machine")
        result = el_2_2_schema_conformance(d)
        assert result.passed is False

    def test_confidence_not_dict_fails(self):
        d = _minimal_schema_dict()
        d["confidence"] = "just a string"
        result = el_2_2_schema_conformance(d)
        assert result.passed is False

    def test_violations_list_populated_on_failure(self):
        d = _minimal_schema_dict()
        d["confidence"].pop("per_field")
        d["confidence"]["overall"] = "bad_value"
        result = el_2_2_schema_conformance(d)
        assert result.passed is False
        assert len(result.violations) >= 1


# ===========================================================================
# YAML contradiction rules
# ===========================================================================


class TestContradictionRules:
    def _make_rule(self, name: str, cond_field: str, cond_val: str, contra_field: str, contra_val: str) -> dict:
        return {
            "name": name,
            "condition_field": cond_field,
            "condition_value": cond_val,
            "contradicted_by": contra_field,
            "contradicted_value": contra_val,
            "message": f"Test rule {name}",
        }

    def test_rule_fires_on_contradiction(self):
        rules = [self._make_rule("r1", "observations", "normal", "severity", "critical")]
        extracted = {
            "entities": {
                "findings": [
                    {"observations": "normal", "severity": "critical", "description": "test"}
                ]
            }
        }
        violations = _apply_contradiction_rules(extracted, rules)
        assert len(violations) == 1
        assert "r1" in violations[0]

    def test_rule_does_not_fire_when_no_match(self):
        rules = [self._make_rule("r1", "observations", "normal", "severity", "critical")]
        extracted = {
            "entities": {
                "findings": [
                    {"observations": "abnormal", "severity": "critical"}
                ]
            }
        }
        violations = _apply_contradiction_rules(extracted, rules)
        assert len(violations) == 0

    def test_rule_fires_case_insensitive(self):
        rules = [self._make_rule("r1", "observations", "normal", "severity", "critical")]
        extracted = {
            "entities": {
                "findings": [{"observations": "NORMAL", "severity": "CRITICAL"}]
            }
        }
        violations = _apply_contradiction_rules(extracted, rules)
        assert len(violations) == 1

    def test_no_rules_returns_empty(self):
        extracted = {"entities": {"findings": [{"severity": "critical"}]}}
        violations = _apply_contradiction_rules(extracted, [])
        assert violations == []

    def test_empty_entities_no_violations(self):
        rules = [self._make_rule("r1", "severity", "normal", "risk_level", "high")]
        extracted = {"entities": {}}
        violations = _apply_contradiction_rules(extracted, rules)
        assert violations == []

    def test_any_contra_value_fires_always(self):
        rules = [self._make_rule("r1", "status", "closed", "severity", "any")]
        extracted = {
            "entities": {
                "work_orders": [{"status": "closed", "severity": "medium"}]
            }
        }
        violations = _apply_contradiction_rules(extracted, rules)
        # "any" means contra_match is True as long as severity is not None
        assert len(violations) == 1

    def test_load_from_real_yaml_file(self, tmp_path):
        """Test loading a real YAML rules file."""
        yaml_content = textwrap.dedent("""
            rules:
              - name: test_rule
                condition_field: status
                condition_value: ok
                contradicted_by: risk_level
                contradicted_value: high
                message: "Test contradiction"
        """)
        rules_file = tmp_path / "rules.yaml"
        rules_file.write_text(yaml_content)
        rules = _load_contradiction_rules(rules_file)
        assert len(rules) == 1
        assert rules[0]["name"] == "test_rule"

    def test_load_nonexistent_file_returns_empty(self, tmp_path):
        rules = _load_contradiction_rules(tmp_path / "nonexistent.yaml")
        assert rules == []

    def test_multiple_rules_both_fire(self):
        rules = [
            self._make_rule("r1", "observations", "normal", "severity", "critical"),
            self._make_rule("r2", "status", "ok", "risk_level", "high"),
        ]
        extracted = {
            "entities": {
                "findings": [
                    {"observations": "normal", "severity": "critical"},
                ],
                "work_orders": [
                    {"status": "ok", "risk_level": "high"},
                ],
            }
        }
        violations = _apply_contradiction_rules(extracted, rules)
        assert len(violations) == 2


# ===========================================================================
# RouteDecision thresholds
# ===========================================================================


class TestRouteByScore:
    def test_accept_at_threshold(self):
        assert _route_by_score(0.85) == RouteDecision.ACCEPT

    def test_accept_above_threshold(self):
        assert _route_by_score(0.95) == RouteDecision.ACCEPT
        assert _route_by_score(1.0) == RouteDecision.ACCEPT

    def test_review_queue_just_below_threshold(self):
        assert _route_by_score(0.84) == RouteDecision.REVIEW_QUEUE

    def test_review_queue_at_min(self):
        assert _route_by_score(0.60) == RouteDecision.REVIEW_QUEUE

    def test_review_queue_midrange(self):
        assert _route_by_score(0.72) == RouteDecision.REVIEW_QUEUE

    def test_re_extract_just_below_review(self):
        assert _route_by_score(0.59) == RouteDecision.RE_EXTRACT

    def test_re_extract_at_zero(self):
        assert _route_by_score(0.0) == RouteDecision.RE_EXTRACT

    def test_re_extract_low(self):
        assert _route_by_score(0.30) == RouteDecision.RE_EXTRACT


# ===========================================================================
# _parse_judge_response
# ===========================================================================


class TestParseJudgeResponse:
    def test_clean_json(self):
        raw = '{"eval_score": 0.92, "contradictions": [], "reasoning": "good"}'
        result = _parse_judge_response(raw)
        assert result["eval_score"] == 0.92
        assert result["contradictions"] == []

    def test_fenced_json(self):
        raw = '```json\n{"eval_score": 0.75, "contradictions": ["c1"]}\n```'
        result = _parse_judge_response(raw)
        assert result["eval_score"] == 0.75
        assert "c1" in result["contradictions"]

    def test_json_embedded_in_text(self):
        raw = 'Some text before {"eval_score": 0.6, "contradictions": []} after'
        result = _parse_judge_response(raw)
        assert result["eval_score"] == 0.6

    def test_invalid_returns_fallback(self):
        raw = "This is definitely not json at all"
        result = _parse_judge_response(raw)
        assert result["eval_score"] == 0.5  # fallback

    def test_empty_string_returns_fallback(self):
        result = _parse_judge_response("")
        assert result["eval_score"] == 0.5


# ===========================================================================
# EL-2.3 — LLM-as-judge (mocked Haiku)
# ===========================================================================


class TestEL23LLMJudge:
    def _make_client(self, eval_score: float = 0.90, contradictions: list | None = None) -> Any:
        """Build a mock Anthropic client that returns a judge response."""
        client = AsyncMock()
        response_text = json.dumps(
            {
                "eval_score": eval_score,
                "contradictions": contradictions or [],
                "reasoning": "test reasoning",
            }
        )
        mock_content = MagicMock()
        mock_content.text = response_text
        mock_response = MagicMock()
        mock_response.content = [mock_content]
        client.messages.create = AsyncMock(return_value=mock_response)
        return client

    @pytest.mark.asyncio
    async def test_accept_route_on_high_score(self, tmp_path):
        client = self._make_client(eval_score=0.92)
        result = await el_2_3_llm_judge(
            source_excerpt="Test source",
            extracted_json={"entities": {}},
            client=client,
            rules_file=tmp_path / "no_rules.yaml",  # non-existent — no rules
        )
        assert result.eval_score == pytest.approx(0.92, abs=0.01)
        assert result.route == RouteDecision.ACCEPT
        assert result.rules_passed is True

    @pytest.mark.asyncio
    async def test_review_queue_route_on_medium_score(self, tmp_path):
        client = self._make_client(eval_score=0.72)
        result = await el_2_3_llm_judge(
            source_excerpt="Test source",
            extracted_json={"entities": {}},
            client=client,
            rules_file=tmp_path / "no_rules.yaml",
        )
        assert result.route == RouteDecision.REVIEW_QUEUE

    @pytest.mark.asyncio
    async def test_re_extract_route_on_low_score(self, tmp_path):
        client = self._make_client(eval_score=0.40)
        result = await el_2_3_llm_judge(
            source_excerpt="Test source",
            extracted_json={"entities": {}},
            client=client,
            rules_file=tmp_path / "no_rules.yaml",
        )
        assert result.route == RouteDecision.RE_EXTRACT

    @pytest.mark.asyncio
    async def test_contradictions_returned(self, tmp_path):
        client = self._make_client(eval_score=0.88, contradictions=["Normal + Critical"])
        result = await el_2_3_llm_judge(
            source_excerpt="Test source",
            extracted_json={"entities": {}},
            client=client,
            rules_file=tmp_path / "no_rules.yaml",
        )
        assert "Normal + Critical" in result.contradictions

    @pytest.mark.asyncio
    async def test_api_error_fallback(self, tmp_path):
        """On API error, falls back to score=0.5 (< 0.60 threshold → re_extract)."""
        import anthropic as _anthropic
        client = AsyncMock()
        client.messages.create = AsyncMock(side_effect=_anthropic.APIError("connection error", request=MagicMock(), body={}))
        result = await el_2_3_llm_judge(
            source_excerpt="Test source",
            extracted_json={"entities": {}},
            client=client,
            rules_file=tmp_path / "no_rules.yaml",
        )
        # Fallback score 0.5 < 0.60 → re_extract
        assert result.route == RouteDecision.RE_EXTRACT
        assert 0.0 <= result.eval_score <= 1.0

    @pytest.mark.asyncio
    async def test_yaml_rules_reduce_score(self, tmp_path):
        """YAML rule violations should reduce the eval score."""
        import textwrap
        rules_file = tmp_path / "rules.yaml"
        rules_file.write_text(textwrap.dedent("""
            rules:
              - name: normal_critical
                condition_field: observations
                condition_value: normal
                contradicted_by: severity
                contradicted_value: critical
                message: "Normal contradicts Critical"
        """))
        # High score from LLM but YAML rule fires
        client = self._make_client(eval_score=0.90)
        extracted = {
            "entities": {
                "findings": [{"observations": "normal", "severity": "critical"}]
            }
        }
        result = await el_2_3_llm_judge(
            source_excerpt="Test source",
            extracted_json=extracted,
            client=client,
            rules_file=rules_file,
        )
        # Score should be reduced by rule violation penalty
        assert result.eval_score < 0.90
        assert len(result.rules_violations) == 1
        assert result.rules_passed is False

    @pytest.mark.asyncio
    async def test_score_clamped_to_zero(self, tmp_path):
        """Even with many rule violations, score never goes below 0."""
        import textwrap
        rules_file = tmp_path / "rules.yaml"
        # 5 rules that all fire
        rule_list = "\n".join([
            f"""  - name: r{i}
    condition_field: status
    condition_value: ok
    contradicted_by: risk
    contradicted_value: high
    message: "Rule {i}" """ for i in range(5)
        ])
        rules_file.write_text(f"rules:\n{rule_list}\n")
        client = self._make_client(eval_score=0.10)
        extracted = {"entities": {"items": [{"status": "ok", "risk": "high"}]}}
        result = await el_2_3_llm_judge(
            source_excerpt="Test",
            extracted_json=extracted,
            client=client,
            rules_file=rules_file,
        )
        assert result.eval_score >= 0.0

    @pytest.mark.asyncio
    async def test_agent_id_in_result(self, tmp_path):
        client = self._make_client(eval_score=0.88)
        result = await el_2_3_llm_judge(
            source_excerpt="Test",
            extracted_json={"entities": {}},
            client=client,
            rules_file=tmp_path / "no_rules.yaml",
            agent_id="pdf-agent",
        )
        assert isinstance(result, EL23Result)
        assert result.judge_raw != ""


# ===========================================================================
# apply_eval_score_to_schema
# ===========================================================================


class TestApplyEvalScore:
    def test_writes_eval_score(self):
        from shared.intermediate_schema import IntermediateSchema
        d = _minimal_schema_dict()
        schema = IntermediateSchema.model_validate(d)

        el23 = EL23Result(
            eval_score=0.91,
            contradictions=["c1"],
            rules_violations=["r1"],
            rules_passed=False,
            route=RouteDecision.ACCEPT,
        )
        updated = apply_eval_score_to_schema(schema, el23)
        assert updated.confidence.eval_score == pytest.approx(0.91)
        assert updated.confidence.rules_passed is False
        assert "c1" in updated.confidence.rules_violations
        assert "r1" in updated.confidence.rules_violations

    def test_original_schema_unchanged(self):
        from shared.intermediate_schema import IntermediateSchema
        d = _minimal_schema_dict()
        schema = IntermediateSchema.model_validate(d)
        original_score = schema.confidence.eval_score

        el23 = EL23Result(eval_score=0.75, route=RouteDecision.REVIEW_QUEUE)
        apply_eval_score_to_schema(schema, el23)

        # Pydantic model_copy — original unchanged
        assert schema.confidence.eval_score == original_score
