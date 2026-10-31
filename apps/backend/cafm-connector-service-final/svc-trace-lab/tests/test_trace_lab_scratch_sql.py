"""The scratch helpers themselves, on a throwaway Postgres: the URL guard, the main tables created by
svc-deepagents' own ensure functions, and the read-only main engine that refuses every write.
Skips without TRACE_LAB_TEST_DB_URL (except the guard test, which needs no database)."""
from __future__ import annotations

import pytest
from sqlalchemy import text

import tl_scratch
from tl_scratch import lab_scratch, main_scratch, requires_scratch  # noqa: F401
from trace_lab import db

MAIN_TABLES = {"agent_trace_turns", "agent_trace_spans", "chat_threads", "chat_turns", "agent_activity_log"}


@pytest.mark.parametrize("url", [
    "postgresql+asyncpg://u:p@db.example.postgres.database.azure.com/tracelab_scratch",
    "postgresql+asyncpg://postgres:scratch@127.0.0.1:55432/hoist_tracelab",
])
def test_the_guard_fails_rather_than_skips_on_any_other_database(url):
    with pytest.raises(pytest.fail.Exception, match="tracelab_scratch"):
        tl_scratch.assert_scratch(url)


@requires_scratch
async def test_main_scratch_creates_the_application_tables_through_svc_deepagents(main_scratch):
    async with main_scratch.connect() as conn:
        rows = (await conn.execute(text(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'plenum_cafm'"))).all()
    assert MAIN_TABLES <= {r[0] for r in rows}
    from src.agents import trace
    from src.services import chat_threads

    assert trace.ready() is False and chat_threads._ready is False   # the fixture left no store "ready"


@requires_scratch
async def test_the_main_engine_is_read_only_on_a_real_postgres(main_scratch):
    from src import database

    engine = db.main_engine()
    assert database._get_engine() is engine and engine is not main_scratch
    async with engine.connect() as conn:
        assert (await conn.execute(text("SHOW default_transaction_read_only"))).scalar() == "on"
        assert (await conn.execute(text("SHOW application_name"))).scalar() == "trace-lab"
        assert (await conn.execute(text("SELECT count(*) FROM plenum_cafm.agent_trace_turns"))).scalar() == 0
    for stmt in ("INSERT INTO plenum_cafm.chat_threads (id) VALUES ('x')",
                 "CREATE TABLE plenum_cafm.not_allowed (id integer)",
                 "DELETE FROM plenum_cafm.agent_trace_spans"):
        with pytest.raises(Exception, match="read-only transaction"):
            async with engine.begin() as conn:
                await conn.exec_driver_sql(stmt)


@requires_scratch
async def test_seeded_rows_are_readable_through_the_main_engine(main_scratch):
    async with main_scratch.begin() as conn:
        await conn.execute(text(
            "INSERT INTO plenum_cafm.agent_trace_turns (turn_id, session_id, question, started_at)"
            " VALUES ('turn-1', 'sess-1', 'How many open work orders?', now())"))
    async with db.main_engine().connect() as conn:
        assert (await conn.execute(text("SELECT question FROM plenum_cafm.agent_trace_turns"))).scalar() == \
            "How many open work orders?"


@requires_scratch
async def test_both_fixtures_share_one_scratch_database(lab_scratch, main_scratch):
    assert db.ready() and db.lab_engine() is lab_scratch
    async with lab_scratch.connect() as conn:
        assert (await conn.execute(text("SELECT count(*) FROM tracelab.jobs"))).scalar() == 0
    async with db.main_engine().connect() as conn:
        assert (await conn.execute(text("SELECT count(*) FROM plenum_cafm.agent_activity_log"))).scalar() == 0
