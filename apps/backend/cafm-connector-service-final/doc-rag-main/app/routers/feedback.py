"""Feedback router."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas import FeedbackRequest, FeedbackResponse
from app.services.audit_service import feedback_service

router = APIRouter(prefix="/feedback", tags=["feedback"])


@router.post("", response_model=FeedbackResponse)
def submit_feedback(req: FeedbackRequest, db: Session = Depends(get_db)):
    row = feedback_service.save(
        db=db,
        query_id=req.query_id,
        answer_id=req.answer_id,
        feedback_type=req.feedback_type,
        rating=req.rating,
        comment=req.comment,
        correction=req.correction,
    )
    return FeedbackResponse(id=row.id, status="saved")
