"""Audit logging + feedback persistence services."""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.core.logger import logger
from app.db.models import RagAnswer, RagFeedback, RagQuery


class AuditService:
    def log_query(
        self,
        db: Session,
        query_text: str,
        query_type: str,
        filters: dict | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> RagQuery:
        row = RagQuery(
            query_text=query_text,
            query_type=query_type,
            filters=filters,
            user_id=user_id,
            session_id=session_id,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        logger.info("Audit: query logged | id={} | type={}", row.id, query_type)
        return row

    def log_answer(
        self,
        db: Session,
        query_id: str,
        answer_text: str,
        citations: list[dict[str, Any]],
        highlights: list[dict[str, Any]],
        model_name: str,
        prompt_tokens: int,
        completion_tokens: int,
        latency_ms: int,
        confidence: float,
        answer_json: dict | None = None,
    ) -> RagAnswer:
        row = RagAnswer(
            query_id=query_id,
            answer_text=answer_text,
            answer_json=answer_json,
            citations=citations,
            highlights=highlights,
            model_name=model_name,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=latency_ms,
            confidence=confidence,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        logger.info(
            "Audit: answer logged | id={} | model={} | latency={}ms | conf={:.3f}",
            row.id, model_name, latency_ms, confidence,
        )
        return row


class FeedbackService:
    def save(
        self,
        db: Session,
        query_id: str,
        answer_id: str,
        feedback_type: str,
        rating: int | None = None,
        comment: str | None = None,
        correction: dict | None = None,
    ) -> RagFeedback:
        row = RagFeedback(
            query_id=query_id,
            answer_id=answer_id,
            feedback_type=feedback_type,
            rating=rating,
            comment=comment,
            correction=correction,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        logger.info("Feedback saved | id={} | type={} | rating={}",
                    row.id, feedback_type, rating)
        return row


audit_service = AuditService()
feedback_service = FeedbackService()
