"""
svc-ingestion/src/shared/confidence_router.py

Confidence router — reads eval_score from EL-2.3 result and routes
the intermediate schema to the correct downstream handler.

Routes:
  ACCEPT      → Layer 3 schema mapper (auto-accept path)
  REVIEW_QUEUE → review_queue with pre-populated extracted values
  RE_EXTRACT  → re-extract queue with correction context (max 3 attempts)

Called by every ingestion agent after eval_layer.el_2_3_llm_judge().
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from cafm_shared.logging import get_logger
from shared.eval_layer import EL23Result, RouteDecision
from shared.intermediate_schema import IntermediateSchema

logger = get_logger(__name__)


@dataclass
class RouterOutcome:
    """Outcome from the confidence router."""

    route: RouteDecision
    schema: IntermediateSchema | None       # present on ACCEPT
    review_payload: dict[str, Any] | None   # present on REVIEW_QUEUE
    retry_context: str = ""                 # present on RE_EXTRACT
    eval_score: float = 0.0
    message: str = ""


def route(
    schema: IntermediateSchema,
    el23: EL23Result,
) -> RouterOutcome:
    """
    Route the schema based on EL-2.3 result.

    Returns a RouterOutcome describing what to do next.
    """
    score = el23.eval_score
    decision = el23.route

    if decision == RouteDecision.ACCEPT:
        logger.info(
            "confidence_router_accept",
            ingestion_id=str(schema.ingestion_id),
            eval_score=score,
        )
        return RouterOutcome(
            route=RouteDecision.ACCEPT,
            schema=schema,
            review_payload=None,
            eval_score=score,
            message=f"Auto-accepted: eval_score={score:.3f} ≥ 0.85",
        )

    elif decision == RouteDecision.REVIEW_QUEUE:
        payload = _build_review_payload(schema, el23)
        logger.info(
            "confidence_router_review_queue",
            ingestion_id=str(schema.ingestion_id),
            eval_score=score,
            contradictions=el23.contradictions,
        )
        return RouterOutcome(
            route=RouteDecision.REVIEW_QUEUE,
            schema=None,
            review_payload=payload,
            eval_score=score,
            message=(
                f"Queued for human review: eval_score={score:.3f} "
                f"(0.60–0.84 range). Contradictions: {el23.contradictions}"
            ),
        )

    else:  # RE_EXTRACT
        retry_context = _build_retry_context(el23)
        logger.warning(
            "confidence_router_re_extract",
            ingestion_id=str(schema.ingestion_id),
            eval_score=score,
            contradictions=el23.contradictions,
            rules_violations=el23.rules_violations,
        )
        return RouterOutcome(
            route=RouteDecision.RE_EXTRACT,
            schema=None,
            review_payload=None,
            retry_context=retry_context,
            eval_score=score,
            message=(
                f"Re-extract required: eval_score={score:.3f} < 0.60. "
                f"Issues: {el23.contradictions + el23.rules_violations}"
            ),
        )


def _build_review_payload(
    schema: IntermediateSchema,
    el23: EL23Result,
) -> dict[str, Any]:
    """Build the review_queue payload dict with extracted values pre-populated."""
    return {
        "ingestion_id": str(schema.ingestion_id),
        "source_type": schema.source_type.value,
        "agent_id": schema.agent_id.value,
        "source_filename": schema.source_filename,
        "eval_score": el23.eval_score,
        "contradictions": el23.contradictions,
        "rules_violations": el23.rules_violations,
        "extracted_entities": schema.entities.model_dump(),
        "confidence": schema.confidence.model_dump(),
        "review_type": "eval_score_review",
        "flag": "schema_low_confidence",
    }


def _build_retry_context(el23: EL23Result) -> str:
    """Build retry context appended to prompt on re-extraction."""
    issues: list[str] = []

    if el23.contradictions:
        issues.append(
            f"Contradictions found: {'; '.join(el23.contradictions)}"
        )
    if el23.rules_violations:
        issues.append(
            f"Rule violations: {'; '.join(el23.rules_violations)}"
        )
    if not issues:
        issues.append(f"Low extraction quality score: {el23.eval_score:.3f}")

    context = (
        "CORRECTION CONTEXT (from previous extraction attempt):\n"
        + "\n".join(f"- {issue}" for issue in issues)
        + "\n\nPlease re-extract the data, paying close attention to the above issues."
    )
    return context
