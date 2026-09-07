"""
Conversational Work Order Interface — POST /api/chat

Three entry points share the same GPT orchestrator:
  POST /api/chat/          — direct user chat (primary interface)
  POST /api/chat/email     — email webhook ingest
  POST /api/chat/ppm       — PPM schedule trigger
  GET  /api/chat/{id}/history — conversation history
"""
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ...db import get_session
from ...agent.orchestrator import WOOrchestrator
from ...agent.session_store import SessionStore
from ...core.logging import get_logger

log = get_logger(__name__)
router = APIRouter()


# ── Request / response models ─────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None


class EmailIngestRequest(BaseModel):
    subject: Optional[str] = None
    body: Optional[str] = None
    sender_name: Optional[str] = None
    sender_email: Optional[str] = None
    asset: Optional[str] = None
    location: Optional[str] = None


class PPMTriggerRequest(BaseModel):
    schedule_id: str
    asset_id: str
    asset_name: str
    description: str
    maintenance_type: Optional[str] = None
    next_due_date: Optional[str] = None
    frequency: Optional[str] = None


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    work_order: Optional[Dict[str, Any]] = None


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post(
    "/",
    response_model=ChatResponse,
    summary="Send a message to the WO assistant",
    description=(
        "Primary conversational endpoint. Omit session_id to start a new conversation. "
        "Include the returned session_id in subsequent messages to continue."
    ),
)
async def chat(
    req: ChatRequest,
    session: AsyncSession = Depends(get_session),
):
    agent = WOOrchestrator(session)
    result = await agent.chat(
        message=req.message,
        session_id=req.session_id,
        source="chat",
    )
    log.info(
        "chat.response",
        session_id=result["session_id"],
        has_work_order=result.get("work_order") is not None,
    )
    return result


@router.post(
    "/email",
    response_model=ChatResponse,
    summary="Ingest an email and start a WO conversation",
    description=(
        "Accepts a parsed email payload from the Outlook connector or any webhook. "
        "The agent extracts the issue, looks up the asset, and either creates a WO "
        "automatically or asks clarifying questions."
    ),
)
async def ingest_email(
    req: EmailIngestRequest,
    session: AsyncSession = Depends(get_session),
):
    agent = WOOrchestrator(session)
    result = await agent.start_from_email(req.model_dump())
    log.info("chat.email_ingest", session_id=result["session_id"])
    return result


@router.post(
    "/ppm",
    response_model=ChatResponse,
    summary="Trigger a PPM work order via the agent",
    description=(
        "Called by the PPM scheduler when a maintenance schedule is due. "
        "The agent looks up the asset, runs scheduling and resource tools, "
        "and creates the work order with minimal conversation."
    ),
)
async def trigger_ppm(
    req: PPMTriggerRequest,
    session: AsyncSession = Depends(get_session),
):
    agent = WOOrchestrator(session)
    result = await agent.start_from_ppm(req.model_dump())
    log.info("chat.ppm_trigger", session_id=result["session_id"], schedule_id=req.schedule_id)
    return result


@router.get(
    "/{session_id}/history",
    summary="Get conversation history",
    description="Returns all user and assistant messages for a session (system prompt excluded).",
)
async def get_history(
    session_id: str,
    session: AsyncSession = Depends(get_session),
):
    store = SessionStore(session)
    if not await store.exists(session_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "session_not_found", "message": f"Session '{session_id}' not found"},
        )
    agent = WOOrchestrator(session)
    messages = await agent.get_history(session_id)
    return {"session_id": session_id, "messages": messages, "count": len(messages)}
