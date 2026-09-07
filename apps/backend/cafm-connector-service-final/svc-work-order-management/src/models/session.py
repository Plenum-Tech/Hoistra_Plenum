import uuid
from sqlalchemy import Column, String, DateTime, func
from sqlalchemy.dialects.postgresql import JSONB
from .base import Base


class WOChatSession(Base):
    """Persists GPT conversation history + gathered WO context per session."""
    __tablename__ = "wo_chat_sessions"
    __table_args__ = {"schema": "plenum_cafm"}

    session_id    = Column(String(100), primary_key=True, default=lambda: str(uuid.uuid4()))
    messages      = Column(JSONB, nullable=False, server_default="[]")
    context       = Column(JSONB, nullable=False, server_default="{}")
    source        = Column(String(30), server_default="chat")   # chat | email | ppm
    created_at    = Column(DateTime(timezone=True), server_default=func.now())
    last_activity = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
