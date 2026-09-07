"""
tests/test_confidence_router.py

Unit tests for shared/confidence_router.py — Task 3.4 (router component).

Covers:
  - route() ACCEPT path: schema returned, review_payload None
  - route() REVIEW_QUEUE path: schema None, payload built with correct keys
  - route() RE_EXTRACT path: schema None, retry_context built with issues
  - _build_review_payload(): contains ingestion_id, eval_score, entities, flag
  - _build_retry_context(): contains contradiction + rules info
  - RouterOutcome: correct fields on each path
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from shared.confidence_router import (
    RouterOutcome,
    _build_retry_context,
    _build_review_payload,
    route,
)
from shared.eval_layer import EL23Result, RouteDecision
from shared.intermediate_schema import IntermediateSchema


# ===========================================================================
# Helpers
# ===========================================================================

def _make_schema(eval_score: float = 0.0) -> IntermediateSchema:
    from uuid import UUID
    return IntermediateSchema(
        ingestion_id=uuid4(),
        source_type="pdf",
        agent_id="pdf-agent",
        source_filename="report.pdf",
        source_blob_url="https://example.com/report.pdf",
        extracted_at="2025-11-15T10:30:00Z",
        extraction_method="claude-vision",
        model_used="claude-sonnet-4-6",
        entities={},
        confidence={
            "overall": "high",
            "per_field": {},
            "eval_score": eval_score,
            "rules_passed": True,
            "rules_violations": [],
        },
        audit={"passes": 1, "tokens_in": 1000, "tokens_out": 200, "cost_usd": 0.005},
    )


def _make_el23(
    score: float = 0.90,
    route_decision: RouteDecision = RouteDecision.ACCEPT,
    contradictions: list | None = None,
    rules_violations: list | None = None,
) -> EL23Result:
    return EL23Result(
        eval_score=score,
        contradictions=contradictions or [],
        rules_violations=rules_violations or [],
        rules_passed=not rules_violations,
        route=route_decision,
    )


# ===========================================================================
# route() — ACCEPT path
# ===========================================================================


class TestRouteAccept:
    def test_accept_returns_schema(self):
        schema = _make_schema()
        el23 = _make_el23(score=0.92, route_decision=RouteDecision.ACCEPT)
        outcome = route(schema, el23)

        assert outcome.route == RouteDecision.ACCEPT
        assert outcome.schema is not None
        assert outcome.review_payload is None
        assert outcome.retry_context == ""

    def test_accept_eval_score_in_outcome(self):
        schema = _make_schema()
        el23 = _make_el23(score=0.88, route_decision=RouteDecision.ACCEPT)
        outcome = route(schema, el23)
        assert outcome.eval_score == pytest.approx(0.88)

    def test_accept_message_mentions_score(self):
        schema = _make_schema()
        el23 = _make_el23(score=0.91, route_decision=RouteDecision.ACCEPT)
        outcome = route(schema, el23)
        assert "0.91" in outcome.message or "accept" in outcome.message.lower()


# ===========================================================================
# route() — REVIEW_QUEUE path
# ===========================================================================


class TestRouteReviewQueue:
    def test_review_queue_schema_is_none(self):
        schema = _make_schema()
        el23 = _make_el23(score=0.72, route_decision=RouteDecision.REVIEW_QUEUE)
        outcome = route(schema, el23)

        assert outcome.route == RouteDecision.REVIEW_QUEUE
        assert outcome.schema is None

    def test_review_queue_payload_built(self):
        schema = _make_schema()
        el23 = _make_el23(
            score=0.72,
            route_decision=RouteDecision.REVIEW_QUEUE,
            contradictions=["Normal + Critical"],
        )
        outcome = route(schema, el23)

        assert outcome.review_payload is not None
        assert "ingestion_id" in outcome.review_payload
        assert "eval_score" in outcome.review_payload
        assert "contradictions" in outcome.review_payload

    def test_review_queue_payload_has_correct_ingestion_id(self):
        schema = _make_schema()
        el23 = _make_el23(score=0.75, route_decision=RouteDecision.REVIEW_QUEUE)
        outcome = route(schema, el23)
        assert outcome.review_payload["ingestion_id"] == str(schema.ingestion_id)

    def test_review_queue_payload_has_entities(self):
        schema = _make_schema()
        el23 = _make_el23(score=0.65, route_decision=RouteDecision.REVIEW_QUEUE)
        outcome = route(schema, el23)
        assert "extracted_entities" in outcome.review_payload

    def test_review_queue_payload_has_flag(self):
        schema = _make_schema()
        el23 = _make_el23(score=0.70, route_decision=RouteDecision.REVIEW_QUEUE)
        outcome = route(schema, el23)
        assert "flag" in outcome.review_payload

    def test_review_queue_message_mentions_range(self):
        schema = _make_schema()
        el23 = _make_el23(score=0.80, route_decision=RouteDecision.REVIEW_QUEUE)
        outcome = route(schema, el23)
        assert "review" in outcome.message.lower()

    def test_review_queue_contradictions_in_payload(self):
        schema = _make_schema()
        el23 = _make_el23(
            score=0.78,
            route_decision=RouteDecision.REVIEW_QUEUE,
            contradictions=["issue A", "issue B"],
        )
        outcome = route(schema, el23)
        assert outcome.review_payload["contradictions"] == ["issue A", "issue B"]


# ===========================================================================
# route() — RE_EXTRACT path
# ===========================================================================


class TestRouteReExtract:
    def test_re_extract_schema_is_none(self):
        schema = _make_schema()
        el23 = _make_el23(score=0.40, route_decision=RouteDecision.RE_EXTRACT)
        outcome = route(schema, el23)

        assert outcome.route == RouteDecision.RE_EXTRACT
        assert outcome.schema is None

    def test_re_extract_retry_context_built(self):
        schema = _make_schema()
        el23 = _make_el23(score=0.35, route_decision=RouteDecision.RE_EXTRACT)
        outcome = route(schema, el23)
        assert outcome.retry_context != ""
        assert "CORRECTION CONTEXT" in outcome.retry_context

    def test_re_extract_review_payload_is_none(self):
        schema = _make_schema()
        el23 = _make_el23(score=0.20, route_decision=RouteDecision.RE_EXTRACT)
        outcome = route(schema, el23)
        assert outcome.review_payload is None

    def test_re_extract_contradictions_in_context(self):
        schema = _make_schema()
        el23 = _make_el23(
            score=0.45,
            route_decision=RouteDecision.RE_EXTRACT,
            contradictions=["Normal + Critical severity"],
        )
        outcome = route(schema, el23)
        assert "Normal + Critical severity" in outcome.retry_context

    def test_re_extract_rules_violations_in_context(self):
        schema = _make_schema()
        el23 = _make_el23(
            score=0.50,
            route_decision=RouteDecision.RE_EXTRACT,
            rules_violations=["rule_001 fired"],
        )
        outcome = route(schema, el23)
        assert "rule_001 fired" in outcome.retry_context

    def test_re_extract_fallback_message_when_no_issues(self):
        """When contradictions and rules_violations are empty, uses score as fallback."""
        schema = _make_schema()
        el23 = _make_el23(score=0.30, route_decision=RouteDecision.RE_EXTRACT)
        outcome = route(schema, el23)
        assert "0.30" in outcome.retry_context or "0.3" in outcome.retry_context


# ===========================================================================
# _build_review_payload()
# ===========================================================================


class TestBuildReviewPayload:
    def test_all_required_keys_present(self):
        schema = _make_schema()
        el23 = _make_el23(score=0.70)
        payload = _build_review_payload(schema, el23)

        required = {
            "ingestion_id", "source_type", "agent_id", "source_filename",
            "eval_score", "contradictions", "rules_violations",
            "extracted_entities", "confidence", "review_type", "flag",
        }
        assert required.issubset(payload.keys())

    def test_eval_score_matches(self):
        schema = _make_schema()
        el23 = _make_el23(score=0.77)
        payload = _build_review_payload(schema, el23)
        assert payload["eval_score"] == pytest.approx(0.77)

    def test_source_type_is_string(self):
        schema = _make_schema()
        el23 = _make_el23(score=0.75)
        payload = _build_review_payload(schema, el23)
        assert isinstance(payload["source_type"], str)


# ===========================================================================
# _build_retry_context()
# ===========================================================================


class TestBuildRetryContext:
    def test_contains_correction_context_header(self):
        el23 = _make_el23(score=0.40, contradictions=["c1"])
        ctx = _build_retry_context(el23)
        assert "CORRECTION CONTEXT" in ctx

    def test_lists_contradictions(self):
        el23 = _make_el23(score=0.40, contradictions=["contradiction one", "contradiction two"])
        ctx = _build_retry_context(el23)
        assert "contradiction one" in ctx
        assert "contradiction two" in ctx

    def test_lists_rules_violations(self):
        el23 = _make_el23(score=0.40, rules_violations=["rule_x fired"])
        ctx = _build_retry_context(el23)
        assert "rule_x fired" in ctx

    def test_fallback_when_no_issues(self):
        el23 = _make_el23(score=0.35)
        ctx = _build_retry_context(el23)
        assert len(ctx) > 20  # Some content generated

    def test_ends_with_re_extract_instruction(self):
        el23 = _make_el23(score=0.40, contradictions=["c1"])
        ctx = _build_retry_context(el23)
        assert "re-extract" in ctx.lower() or "re-analyse" in ctx.lower() or "re-" in ctx.lower()
