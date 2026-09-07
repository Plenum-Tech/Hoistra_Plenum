"""
Shared fixtures for all tests.

Uses an in-memory SQLite database so no live PostgreSQL is needed.
The plenum_cafm schema is stripped via schema_translate_map so SQLite
can handle the ORM models unchanged.
"""
import os

# Must be set before any src import so that create_async_engine() at module
# level in src/db.py receives a parseable URL (asyncpg is never called in tests).
os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://localhost/test_placeholder"
)

import pytest
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import StaticPool

from src.app import app
from src.db import get_session
from src.models.base import Base

_SQLITE_URL = "sqlite+aiosqlite://"


@pytest.fixture
async def db_engine():
    """Fresh in-memory SQLite engine per test with schema translation."""
    engine = create_async_engine(
        _SQLITE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        execution_options={"schema_translate_map": {"plenum_cafm": None}},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def http_client(db_engine):
    """
    HTTPX AsyncClient wired to the FastAPI app with the test database.

    Overrides get_session to return sessions from the test engine, and
    patches init_db so the lifespan does not attempt a real DB connection.
    """
    factory = async_sessionmaker(db_engine, expire_on_commit=False)

    async def _override_session():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = _override_session

    with patch("src.app.init_db", new_callable=AsyncMock):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            yield ac

    app.dependency_overrides.clear()
