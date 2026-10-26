"""Real-SQL test helper for Trace Lab. A plain module, not conftest.py.

Works only against a throwaway Postgres named by TRACE_LAB_TEST_DB_URL: the database must be
tracelab_scratch on localhost, 127.0.0.1 or host.docker.internal. Anything else FAILS the run (it
never skips), because these helpers drop schemas. Without the variable every test that uses them
is skipped.

    from tl_scratch import lab_scratch, main_scratch, requires_scratch
    pytestmark = requires_scratch

lab_scratch   a fresh `tracelab` schema with every migration applied through
              migrate.apply_pending (the production path); db.lab_engine() and db.STATE point at
              it. Yields the (writable) lab engine.
main_scratch  a fresh `plenum_cafm` schema in the same scratch database, its trace, thread and
              activity tables created by svc-deepagents' OWN ensure functions run against the
              scratch engine only; then db.install_main_engine() installs the READ-ONLY main
              engine on it (so src.database._get_engine() is read-only, as in production).
              Yields a WRITABLE engine for seeding rows; code under test reads through
              db.main_engine().
"""
from __future__ import annotations

import os

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from trace_lab import db, migrate
from trace_lab.settings import Settings

SCRATCH_URL = os.environ.get("TRACE_LAB_TEST_DB_URL", "")

requires_scratch = pytest.mark.skipif(
    not SCRATCH_URL, reason="TRACE_LAB_TEST_DB_URL is not set: real-SQL tests need a throwaway Postgres")


def assert_scratch(url: str = SCRATCH_URL) -> None:
    if not migrate.scratch_url_ok(url):
        pytest.fail("TRACE_LAB_TEST_DB_URL must name tracelab_scratch on localhost, 127.0.0.1 "
                    f"or host.docker.internal; got {url.split('@')[-1]!r}")


def new_engine() -> AsyncEngine:
    """A writable scratch engine. NullPool: no pooled connection outlives the test's event loop."""
    assert_scratch()
    return create_async_engine(SCRATCH_URL, poolclass=NullPool)


async def fresh_lab_schema(monkeypatch, *, apply: bool = True) -> AsyncEngine:
    """Drop schema tracelab, apply every migration (unless apply=False) and point db at it."""
    if not SCRATCH_URL:
        pytest.skip("TRACE_LAB_TEST_DB_URL is not set")
    engine = new_engine()
    async with engine.begin() as conn:
        await conn.exec_driver_sql(f"DROP SCHEMA IF EXISTS {migrate.SCHEMA} CASCADE")
    monkeypatch.setattr(db, "_lab", engine)
    monkeypatch.setattr(db, "STATE", None)
    if apply:
        st = await migrate.apply_pending(engine, applied_by="pytest")
        assert st.status == "ready", st.as_dict()
        monkeypatch.setattr(db, "STATE", st)
    return engine


@pytest.fixture
async def lab_scratch(monkeypatch):
    engine = await fresh_lab_schema(monkeypatch)
    yield engine
    await engine.dispose()


async def _create_main_tables(monkeypatch, engine: AsyncEngine) -> None:
    """svc-deepagents' own DDL, run against the scratch engine ONLY (never its real engine)."""
    from src import database
    from src.agents import activity_log, trace
    from src.services import chat_threads

    assert engine.url.database == "tracelab_scratch", engine.url
    assert (engine.url.host or "") in migrate.SCRATCH_HOSTS, engine.url
    with monkeypatch.context() as m:
        m.setattr(database, "_get_engine", lambda: engine)
        m.setattr(trace, "_ready", False)
        m.setattr(chat_threads, "_ready", False)
        m.setenv("ACTIVITY_LOG_ENABLED", "true")
        assert await chat_threads.ensure_tables(), "chat_threads could not be created on the scratch database"
        assert await activity_log.ensure_table(), "agent_activity_log could not be created on the scratch database"
        assert await trace.ensure_tables(), "agent_trace_* could not be created on the scratch database"


@pytest.fixture
async def main_scratch(monkeypatch):
    if not SCRATCH_URL:
        pytest.skip("TRACE_LAB_TEST_DB_URL is not set")
    from src import database

    writable = new_engine()
    async with writable.begin() as conn:
        await conn.exec_driver_sql("DROP SCHEMA IF EXISTS plenum_cafm CASCADE")
    await _create_main_tables(monkeypatch, writable)
    monkeypatch.setattr(database, "_engine", None)
    monkeypatch.setattr(database, "_session_factory", None)
    monkeypatch.setattr(database, "AsyncSessionLocal", None)
    monkeypatch.setattr(db, "_main", None)
    monkeypatch.setattr(db, "_settings", None)
    read_only = db.install_main_engine(Settings(main_db_url=SCRATCH_URL))
    try:
        yield writable
    finally:
        await read_only.dispose()
        await writable.dispose()
        db.reset_for_tests()
