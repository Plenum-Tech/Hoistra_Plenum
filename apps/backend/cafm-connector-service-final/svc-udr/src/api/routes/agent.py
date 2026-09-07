from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from ...db import get_session
from ...agent.orchestrator import UDROrchestrator
from ...api.schemas.database import AgentQueryRequest, AgentQueryResponse
from ...core.logging import get_logger

router = APIRouter()
log = get_logger(__name__)


@router.post(
    "/query",
    response_model=AgentQueryResponse,
    summary="Natural-language database query",
    description=(
        "Send a natural-language request to the UDR agent. "
        "The agent will introspect the schema, run the necessary CRUD operations, "
        "and return a structured reply."
    ),
)
async def agent_query(
    body: AgentQueryRequest,
    session: AsyncSession = Depends(get_session),
) -> AgentQueryResponse:
    orchestrator = UDROrchestrator(session)
    try:
        result = await orchestrator.query(body.message)
    except Exception as exc:
        log.error("agent.query.error", exc_type=type(exc).__name__, detail=str(exc)[:300])
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "agent_error", "message": str(exc)},
        )
    return AgentQueryResponse(**result)
