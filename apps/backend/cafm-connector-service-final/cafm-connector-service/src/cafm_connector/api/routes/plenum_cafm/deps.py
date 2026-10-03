"""
Dependency injection helpers for the Plenum-CAFM CRUD routes.

Provides:
  - get_plenum_db  → async SQLAlchemy session, bound to the signed-in caller
  - PLENUM_DB_URL  → read from PLENUM_DB_URL env var (falls back to the
                     main DB_URL so single-instance setups work out of the box)

Every route in this package depends on ``get_plenum_db`` and nothing else, which is why the
caller is resolved here rather than route by route. Asking for the session now means asking
for a signed-in caller: a request with no usable bearer token is refused before any route
body runs, and the session that is handed over answers only for that caller's company (see
``cafm_connector.api.security``). One hundred and thirty-five routes, one place that decides.

Nothing else may build a session from this module in a request path. ``unscoped_session`` is
provided for startup, migrations and workers, where there is no caller to scope to, and it
is named so that its use in a route reads as the mistake it would be.
"""

from __future__ import annotations

import os
from typing import AsyncGenerator

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from cafm_connector.api.security import Principal, current_principal, guard

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


async def unscoped_session() -> AsyncGenerator[AsyncSession, None]:
    """A session with no caller bound to it — startup, migrations and workers only.

    Every row in the database is reachable through this. It exists because a migration has
    no signed-in user, not because a route might find it convenient.
    """
    factory = _get_factory()
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def get_plenum_db(
    principal: Principal = Depends(current_principal),
) -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency — an async session that answers only for the signed-in caller.

    The 401 comes from ``current_principal`` before the generator body runs, so a route
    cannot be reached without a token even if it never mentions one.
    """
    factory = _get_factory()
    async with factory() as session:
        guard(session, principal)
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
