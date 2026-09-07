"""
svc-ingestion/src/shared/db.py

Async SQLAlchemy session factory for svc-ingestion.
Used by Stage 1 (ingest), Stage 4 (unifier), and any future pipeline stage.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from config import get_settings


@lru_cache(maxsize=1)
def _engine():
    settings = get_settings()
    return create_async_engine(
        settings.db_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_pre_ping=True,
        echo=False,
    )


@lru_cache(maxsize=1)
def _session_factory() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        _engine(),
        class_=AsyncSession,
        expire_on_commit=False,
    )


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency — yields a scoped AsyncSession."""
    async with _session_factory()() as session:
        yield session


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Returns the session factory for use outside FastAPI DI (e.g. ARQ worker)."""
    return _session_factory()
