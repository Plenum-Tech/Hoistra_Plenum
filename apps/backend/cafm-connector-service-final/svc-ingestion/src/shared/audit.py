"""
svc-ingestion/src/shared/audit.py

Task 1.6 — Audit receipt generation.

Writes a row to ingestion_audit_log for every pipeline event.
Called by each stage and by review queue decisions.

Every ingestion and query produces a replayable audit receipt — required for UAE
compliance audits. Receipts are never deleted.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from opentelemetry import trace
from opentelemetry.trace import StatusCode
from sqlalchemy.ext.asyncio import AsyncSession

from cafm_shared.logging import get_logger
from models.ingestion import IngestionAuditLog
from shared.intermediate_schema import IntermediateSchema

logger = get_logger(__name__)
tracer = trace.get_tracer(__name__)

# Valid event types — used as constants to avoid typos across callers
EVENT_STAGE1_INGEST = "stage1_ingest"
EVENT_STAGE2_EXTRACT = "stage2_extract"
EVENT_STAGE3_EVAL = "stage3_eval"
EVENT_STAGE4_UNIFY = "stage4_unify"
EVENT_REVIEW_DECISION = "review_decision"
EVENT_RE_EXTRACT = "re_extract"
EVENT_REJECTED = "rejected"


async def write_audit_event(
    *,
    ingestion_id: uuid.UUID,
    event_type: str,
    db: AsyncSession,
    model_used: str | None = None,
    prompt_version: str | None = None,
    eval_score: float | None = None,
    rules_violations: list[str] | None = None,
    reviewer_id: uuid.UUID | None = None,
    decision: str | None = None,
    corrected_json: dict | None = None,
) -> IngestionAuditLog:
    """
    Write a single audit event to ingestion_audit_log.

    Args:
        ingestion_id:      The document being audited.
        event_type:        One of the EVENT_* constants defined above.
        db:                Active AsyncSession — caller commits.
        model_used:        Claude model used in this event (if any).
        prompt_version:    Prompt template version used (if any).
        eval_score:        LLM-as-judge score for this event (0.0–1.0).
        rules_violations:  List of rule violation strings from the rule engine.
        reviewer_id:       UUID of the human reviewer (review_decision events only).
        decision:          accept | correct | reject (review_decision events only).
        corrected_json:    Corrected entity JSON (correct decisions only).

    Returns:
        The persisted IngestionAuditLog row (flushed but not committed).
    """
    with tracer.start_as_current_span("ingestion.audit.write") as span:
        span.set_attribute("cafm.ingestion_id", str(ingestion_id))
        span.set_attribute("cafm.audit_event_type", event_type)

        try:
            log = IngestionAuditLog(
                id=uuid.uuid4(),
                ingestion_id=ingestion_id,
                event_type=event_type,
                model_used=model_used,
                prompt_version=prompt_version,
                eval_score=round(eval_score, 3) if eval_score is not None else None,
                rules_violations=(
                    {"violations": rules_violations} if rules_violations else None
                ),
                reviewer_id=reviewer_id,
                decision=decision,
                corrected_json=corrected_json,
                timestamp=datetime.now(timezone.utc),
            )
            db.add(log)
            await db.flush()

            logger.info(
                "audit_event_written",
                ingestion_id=str(ingestion_id),
                event_type=event_type,
                model_used=model_used,
                eval_score=eval_score,
                decision=decision,
            )

            span.set_status(StatusCode.OK)
            return log

        except Exception as exc:
            span.record_exception(exc)
            span.set_status(StatusCode.ERROR, str(exc))
            logger.error(
                "audit_event_failed",
                ingestion_id=str(ingestion_id),
                event_type=event_type,
                error=str(exc),
            )
            raise


async def write_pipeline_receipt(
    *,
    schema: IntermediateSchema,
    db: AsyncSession,
) -> IngestionAuditLog:
    """
    Convenience wrapper — writes the final Stage 4 audit receipt.

    Called after unify() completes successfully. Captures the full
    extraction audit trail: model, prompt version, eval score, cost, violations.
    """
    violations = schema.confidence.rules_violations if schema.confidence else []
    return await write_audit_event(
        ingestion_id=schema.ingestion_id,
        event_type=EVENT_STAGE4_UNIFY,
        db=db,
        model_used=schema.model_used.value,
        prompt_version=schema.audit.prompt_version,
        eval_score=schema.confidence.eval_score,
        rules_violations=violations if violations else None,
    )


async def write_review_decision(
    *,
    ingestion_id: uuid.UUID,
    reviewer_id: uuid.UUID,
    decision: str,
    corrected_json: dict | None = None,
    db: AsyncSession,
) -> IngestionAuditLog:
    """
    Convenience wrapper — writes a HITL review decision audit event.

    Called by the review queue API when a reviewer accepts, corrects, or rejects.
    """
    return await write_audit_event(
        ingestion_id=ingestion_id,
        event_type=EVENT_REVIEW_DECISION,
        db=db,
        reviewer_id=reviewer_id,
        decision=decision,
        corrected_json=corrected_json,
    )
