"""FastAPI dependency injection — DB sessions, auth, service factory."""

from __future__ import annotations

from typing import Annotated, AsyncGenerator

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from cafm_connector.api.security import Principal, current_principal
from cafm_connector.core.config import Settings, get_settings
from cafm_connector.secrets.backend import get_secrets_backend
from cafm_connector.services.connector_service import ConnectorService

# ── Database ──────────────────────────────────────────────────────────

_engine = None
_session_factory = None


def get_engine(settings: Settings):
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            settings.db_url,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            echo=settings.debug,
            # Health-check pooled connections + retire after 30 min so an idle-closed cloud-DB
            # connection is transparently reconnected instead of failing with "connection is closed".
            pool_pre_ping=True,
            pool_recycle=1800,
        )
    return _engine


def get_session_factory(settings: Settings = Depends(get_settings)):
    global _session_factory
    if _session_factory is None:
        engine = get_engine(settings)
        _session_factory = async_sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )
    return _session_factory


async def get_db_session(
    factory=Depends(get_session_factory),
) -> AsyncGenerator[AsyncSession, None]:
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


# ── Auth ──────────────────────────────────────────────────────────────

# Identity lives in cafm_connector.api.security, which asks operations-intelligence who the
# caller is. What used to be here was a stub returning ``TokenPayload(sub="anonymous")`` with
# the real check commented out inside a string literal, so every connector route — the ones
# that hold the credentials for a customer's source systems — was open to anyone who could
# reach the port.


class TokenPayload:
    """The caller, in the shape connectors.py already expects."""

    def __init__(self, sub: str, roles: list[str], principal: "Principal | None" = None) -> None:
        self.sub = sub
        self.roles = roles
        self.principal = principal

    @property
    def organization_id(self):
        return self.principal.organization_id if self.principal else None


async def get_current_user(
    principal: Principal = Depends(current_principal),
) -> TokenPayload:
    """The signed-in caller, or 401. Never anonymous."""
    return TokenPayload(sub=str(principal.user_id), roles=[principal.role], principal=principal)


# ── Service factory ───────────────────────────────────────────────────

async def get_service(
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> ConnectorService:
    secrets = get_secrets_backend(settings)
    return ConnectorService(session=session, secrets=secrets, settings=settings)
