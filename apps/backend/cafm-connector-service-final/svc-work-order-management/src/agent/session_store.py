import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.session import WOChatSession
from ..core.logging import get_logger

log = get_logger(__name__)


class SessionStore:
    """DB-backed conversation session store. Each session persists messages as JSONB."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, source: str = "chat", owner: Any = None) -> str:
        """A new conversation, recorded as ``owner``'s (the signed-in caller) in ``context``.

        Kept in the JSONB context rather than a column of its own: no schema change, and a
        conversation from before owners were recorded simply has none (api/routes/chat.py
        shows it to a superadmin only)."""
        session_id = str(uuid.uuid4())
        context: Dict[str, Any] = {}
        if owner is not None:
            context["owner"] = {
                "user_id": str(owner.user_id),
                "organization_id": str(owner.organization_id) if owner.organization_id else None,
            }
        ws = WOChatSession(session_id=session_id, messages=[], context=context, source=source)
        self.session.add(ws)
        await self.session.commit()
        log.info("session.created", session_id=session_id, source=source)
        return session_id

    async def exists(self, session_id: str) -> bool:
        result = await self.session.execute(
            select(WOChatSession.session_id).where(WOChatSession.session_id == session_id)
        )
        return result.scalar_one_or_none() is not None

    async def owner(self, session_id: str) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """(exists, owner) for one conversation; owner is None when none was recorded."""
        result = await self.session.execute(
            select(WOChatSession.context).where(WOChatSession.session_id == session_id)
        )
        row = result.first()
        if row is None:
            return False, None
        ctx = row[0] or {}
        owner = ctx.get("owner") if isinstance(ctx, dict) else None
        return True, owner if isinstance(owner, dict) else None

    async def load_messages(self, session_id: str) -> List[Dict[str, Any]]:
        result = await self.session.execute(
            select(WOChatSession).where(WOChatSession.session_id == session_id)
        )
        row = result.scalar_one_or_none()
        return row.messages or [] if row else []

    async def save_messages(self, session_id: str, messages: List[Dict[str, Any]]) -> None:
        result = await self.session.execute(
            select(WOChatSession).where(WOChatSession.session_id == session_id)
        )
        row = result.scalar_one_or_none()
        if row:
            row.messages = messages
            row.last_activity = datetime.utcnow()
            await self.session.commit()
