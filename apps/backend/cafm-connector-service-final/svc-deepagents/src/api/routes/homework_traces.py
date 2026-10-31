"""Principal-owned execution metadata export for the isolated Homework profile."""
from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy.exc import SQLAlchemyError

from ...services.trace_hw1.service import TraceService

router = APIRouter(prefix="/api/homework/traces", tags=["Isolated execution traces"])


@router.get("")
async def export(request: Request, conversation_id: str | None = Query(default=None, max_length=160),
                 limit: int = Query(default=50, ge=1, le=100)):
    try:
        return await TraceService().export(request.state.test_principal, conversation_id, limit)
    except SQLAlchemyError:
        raise HTTPException(503, "Trace store unavailable; no export claimed") from None
