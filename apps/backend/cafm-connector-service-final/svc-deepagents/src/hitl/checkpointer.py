"""
Postgres-backed LangGraph checkpointer for stateful HITL workflows.

Converts the asyncpg DB_URL format used by the rest of the service into
the psycopg3 format required by AsyncPostgresSaver, then creates and
setup()s the checkpointer.

Usage (FastAPI lifespan):
    saver, pool = await create_checkpointer(settings.db_url)
    # ... use saver ...
    await pool.close()
"""
from __future__ import annotations

import re

import structlog
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg_pool import AsyncConnectionPool

log = structlog.get_logger(__name__)

_ASYNCPG_PREFIX = re.compile(r"^postgresql\+asyncpg://")


def _to_psycopg_url(asyncpg_url: str) -> str:
    """Convert 'postgresql+asyncpg://...' to 'postgresql://...' for psycopg3."""
    return _ASYNCPG_PREFIX.sub("postgresql://", asyncpg_url)


async def create_checkpointer(db_url: str) -> tuple[AsyncPostgresSaver, AsyncConnectionPool]:
    """
    Open a psycopg3 connection pool and create the Postgres checkpointer.

    Returns (checkpointer, pool). The caller must close the pool on shutdown:
        await pool.close()

    Calls checkpointer.setup() to create the langgraph_checkpoints table
    if it doesn't exist yet.
    """
    conn_string = _to_psycopg_url(db_url)
    pool = AsyncConnectionPool(
        conninfo=conn_string,
        max_size=4,
        kwargs={"autocommit": True, "prepare_threshold": 0},
        open=False,
    )
    await pool.open()

    saver = AsyncPostgresSaver(pool)
    await saver.setup()

    host = conn_string.split("@")[-1].split("/")[0] if "@" in conn_string else "db"
    log.info("hitl.checkpointer.ready", host=host)

    return saver, pool
