import uuid
from datetime import datetime
from typing import Any, Dict, List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.session import WOChatSession
from ..core.logging import get_logger

log = get_logger(__name__)


class SessionStore:
    """DB-backed conversation session store. Each session persists messages as JSONB."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, source: str = "chat") -> str:
        session_id = str(uuid.uuid4())
        ws = WOChatSession(session_id=session_id, messages=[], context={}, source=source)
        self.session.add(ws)
        await self.session.commit()
        log.info("session.created", session_id=session_id, source=source)
        return session_id

    async def exists(self, session_id: str) -> bool:
        result = await self.session.execute(
            select(WOChatSession.session_id).where(WOChatSession.session_id == session_id)
        )
        return result.scalar_one_or_none() is not None

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
