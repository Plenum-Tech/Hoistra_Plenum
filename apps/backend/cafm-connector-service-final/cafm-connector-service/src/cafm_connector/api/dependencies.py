"""FastAPI dependency injection — DB sessions, auth, service factory."""

from __future__ import annotations

from typing import Annotated, AsyncGenerator

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

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


class TokenPayload:
    def __init__(self, sub: str, roles: list[str]) -> None:
        self.sub   = sub
        self.roles = roles


async def get_current_user() -> TokenPayload:
    return TokenPayload(sub="anonymous", roles=[])
'''
bearer_scheme = HTTPBearer()


class TokenPayload:
    def __init__(self, sub: str, roles: list[str]) -> None:
        self.sub   = sub
        self.roles = roles


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    settings: Settings = Depends(get_settings),
) -> TokenPayload:
    token = credentials.credentials
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        sub: str = payload.get("sub", "")
        roles: list[str] = payload.get("roles", [])
        return TokenPayload(sub=sub, roles=roles)
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

'''
# ── Service factory ───────────────────────────────────────────────────

async def get_service(
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> ConnectorService:
    secrets = get_secrets_backend(settings)
    return ConnectorService(session=session, secrets=secrets, settings=settings)
