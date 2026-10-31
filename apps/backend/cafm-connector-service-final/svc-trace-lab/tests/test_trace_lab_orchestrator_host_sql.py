"""Approved skill overlays load read-only on a real Postgres (the scratch database): one SELECT, no DDL,
and svc-deepagents' own refresh() stays a plain SELECT once _ready is pre-set. Skips without
TRACE_LAB_TEST_DB_URL."""
from __future__ import annotations

import re

import pytest
from sqlalchemy import event, text

from tl_scratch import main_scratch, requires_scratch  # noqa: F401
from src.services import skill_overlays
from trace_lab import db, orchestrator_host

pytestmark = requires_scratch

_DDL_WORDS = re.compile(r"^\s*(create|alter|drop|insert|update|delete|truncate|grant)\b", re.I)


@pytest.fixture
def overlay_state(monkeypatch):
    monkeypatch.setattr(skill_overlays, "_ready", False)
    monkeypatch.setattr(skill_overlays, "_active", {})
    monkeypatch.setattr(skill_overlays, "_loaded_at", 0.0)
    orchestrator_host.reset_for_tests()
    yield
    orchestrator_host.reset_for_tests()


async def test_overlays_load_with_one_select_and_no_ddl(main_scratch, overlay_state):
    async with main_scratch.begin() as conn:                    # svc-deepagents' own DDL, scratch only
        for ddl in skill_overlays._DDL:
            await conn.execute(text(ddl))
        await conn.execute(text(
            "INSERT INTO plenum_cafm.skill_overlays (id, skill, doc, content, status) VALUES "
            "(gen_random_uuid(), 'query-builder', 'context-budget', 'TUNED TEXT', 'active'), "
            "(gen_random_uuid(), 'query-builder', 'context-budget', 'NOT YET', 'proposed')"))
    seen: list[str] = []
    engine = db.main_engine().sync_engine

    def record(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        assert await orchestrator_host.ensure_overlays_readonly() == 1
        assert skill_overlays._active == {("query-builder", "context-budget"): "TUNED TEXT"}
        assert skill_overlays._ready is True
        assert skill_overlays.text_for("query-builder", "context-budget") == "TUNED TEXT"
        await skill_overlays.refresh(force=True)                # svc-deepagents' own refresh, read-only engine
        assert skill_overlays._active == {("query-builder", "context-budget"): "TUNED TEXT"}
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert [s for s in seen if _DDL_WORDS.match(s)] == []
    assert sum("FROM plenum_cafm.skill_overlays" in s for s in seen) == 2


async def test_a_database_without_the_overlay_table_loads_none(main_scratch, overlay_state):
    assert await orchestrator_host.ensure_overlays_readonly() == 0
    assert skill_overlays._ready is True and skill_overlays._active == {}
    async with main_scratch.connect() as conn:
        exists = (await conn.execute(text("SELECT to_regclass('plenum_cafm.skill_overlays')"))).scalar()
    assert exists is None                                      # nothing created it
