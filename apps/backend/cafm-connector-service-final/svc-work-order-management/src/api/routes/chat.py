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
from ...services.principal import Principal, current_principal

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


# ── Whose conversation it is ─────────────────────────────────────────────────────

def may_read(principal: Principal, owner: Optional[Dict[str, Any]]) -> bool:
    """Whether this caller may read or continue a conversation recorded as ``owner``'s.

    Its owner may, and an administrator of the owner's company; a superadmin may read any. A
    conversation from before owners were recorded belongs to nobody, so only a superadmin sees
    it: it answered any session id to anybody, and nobody's is not everybody's.
    """
    role = (principal.role or "").strip().lower()
    if role == "superadmin":
        return True
    if not owner:
        return False
    if str(owner.get("user_id")) == str(principal.user_id):
        return True
    return (role == "admin" and principal.organization_id is not None
            and str(owner.get("organization_id")) == str(principal.organization_id))


async def _own_session(session: AsyncSession, principal: Principal,
                       session_id: Optional[str]) -> Optional[str]:
    """The session id to continue: the one asked for when the caller may, otherwise None, so a
    new conversation starts rather than another person's being read into this one."""
    if not session_id:
        return None
    exists, owner = await SessionStore(session).owner(session_id)
    if exists and may_read(principal, owner):
        return session_id
    return None


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
    principal: Principal = Depends(current_principal),
):
    agent = WOOrchestrator(session, principal=principal)
    result = await agent.chat(
        message=req.message,
        session_id=await _own_session(session, principal, req.session_id),
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
    principal: Principal = Depends(current_principal),
):
    agent = WOOrchestrator(session, principal=principal)
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
    principal: Principal = Depends(current_principal),
):
    agent = WOOrchestrator(session, principal=principal)
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
    principal: Principal = Depends(current_principal),
):
    store = SessionStore(session)
    exists, owner = await store.owner(session_id)
    # Another person's conversation answers exactly as one that does not exist.
    if not exists or not may_read(principal, owner):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "session_not_found", "message": f"Session '{session_id}' not found"},
        )
    agent = WOOrchestrator(session, principal=principal)
    messages = await agent.get_history(session_id)
    return {"session_id": session_id, "messages": messages, "count": len(messages)}
