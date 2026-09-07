"""
Dependency injection helpers for the Plenum-CAFM CRUD routes.

Provides:
  - get_plenum_db  → async SQLAlchemy session for the Plenum-CAFM schema
  - PLENUM_DB_URL  → read from PLENUM_DB_URL env var (falls back to the
                     main DB_URL so single-instance setups work out of the box)
"""

from __future__ import annotations

import os
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Allow a separate DB URL for the Plenum schema, defaulting to the main one
_PLENUM_DB_URL = os.environ.get(
    "PLENUM_DB_URL",
    os.environ.get("DB_URL", ""),
)

_engine = None
_factory = None


def _get_factory() -> async_sessionmaker:
    global _engine, _factory
    if _factory is None:
        pool_size = int(os.environ.get("DB_POOL_SIZE", "10"))
        _engine = create_async_engine(_PLENUM_DB_URL, pool_size=pool_size, pool_pre_ping=True)
        _factory = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)
    return _factory


async def get_plenum_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency — yields an async session for the Plenum-CAFM schema."""
    factory = _get_factory()
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
