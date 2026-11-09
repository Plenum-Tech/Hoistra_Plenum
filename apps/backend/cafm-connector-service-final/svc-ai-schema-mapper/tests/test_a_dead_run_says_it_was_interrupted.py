"""A run that has stopped says so, instead of reading "running" for ever.

6 Nov 2026: run bce6b4d2 read "running" for 37 days -- its first step finished two seconds in on
30 Sep and nothing ran the next. The rule now (services/interrupted_runs.py): still 'running', and
nothing written to its row for RUN_INTERRUPTED_AFTER_MINUTES, means failed as interrupted. What
matters as much is what is NOT touched -- a busy run, a run waiting at a gate, a run already
finished -- and that nothing is judged without the activity stamp in place.

Real Postgres only: a trigger and a time window cannot be faked usefully. Set SM_TEST_DB_URL to a
LOCAL database with plenum_cafm.migration_jobs (a pg_dump --schema-only of hoistra_test works). The
tests write rows there and remove them; they refuse a non-local host.
"""
from __future__ import annotations

import os
import uuid
from urllib.parse import urlparse

import pytest
from sqlalchemy import text

from src import schema_patches as sp
from src.services import interrupted_runs as ir

URL = os.environ.get("SM_TEST_DB_URL", "")
pytestmark = pytest.mark.skipif(not URL, reason="SM_TEST_DB_URL is not set")

ORG = str(uuid.uuid4())
COUNTS = ("t1_mapped_count", "t2_auto_count", "t2_human_count", "t2_multi_merge_count",
          "unmapped_count", "total_fields", "total_records", "orphan_count", "cycle_count")


@pytest.fixture
async def engine():
    host = (urlparse(URL.replace("+asyncpg", "")).hostname or "").lower()
    if host not in ("localhost", "127.0.0.1", "::1"):
        pytest.fail(f"SM_TEST_DB_URL must be local; refusing {host!r}")
    from sqlalchemy.ext.asyncio import create_async_engine
    eng = create_async_engine(URL)
    async with eng.begin() as conn:
        await conn.execute(text(
            "ALTER TABLE plenum_cafm.migration_jobs ADD COLUMN IF NOT EXISTS last_activity_at TIMESTAMPTZ"))
    assert await sp.ensure_activity_is_stamped(eng) in ("applied", "already_applied")
    yield eng
    async with eng.begin() as conn:
        await conn.execute(text("DELETE FROM plenum_cafm.migration_jobs WHERE organization_id = CAST(:o AS uuid)"),
                           {"o": ORG})
    await eng.dispose()


async def _run(engine, *, status="running", step="1_ingest", silent_minutes=0) -> str:
    """A run, then its last activity backdated -- with triggers off for THIS session only, since
    the stamp would otherwise set it to now."""
    rid = str(uuid.uuid4())
    async with engine.begin() as conn:
        await conn.execute(text(
            "INSERT INTO plenum_cafm.migration_jobs (id, organization_id, cmms_name, source_filename,"
            " status, current_step, progress_pct, started_at, " + ", ".join(COUNTS) + ")"
            " VALUES (CAST(:i AS uuid), CAST(:o AS uuid), 'Custom', 'x.csv', :s, :st, 0,"
            " now() - interval '2 days', " + ", ".join("0" for _ in COUNTS) + ")"),
            {"i": rid, "o": ORG, "s": status, "st": step})
    if silent_minutes:
        async with engine.begin() as conn:
            await conn.execute(text("SET LOCAL session_replication_role = replica"))
            await conn.execute(text(
                "UPDATE plenum_cafm.migration_jobs SET last_activity_at = now() - make_interval(mins => :m)"
                " WHERE id = CAST(:i AS uuid)"), {"m": silent_minutes, "i": rid})
    return rid


async def _row(engine, rid):
    async with engine.connect() as conn:
        return (await conn.execute(text(
            "SELECT status, error_message, last_activity_at, completed_at FROM plenum_cafm.migration_jobs"
            " WHERE id = CAST(:i AS uuid)"), {"i": rid})).mappings().first()


# -- the stamp ----------------------------------------------------------------------------------------

async def test_every_write_stamps_the_run(engine):
    rid = await _run(engine)
    first = (await _row(engine, rid))["last_activity_at"]
    assert first is not None, "an insert is activity"
    async with engine.begin() as conn:     # what the progress beat writes: progress_pct alone
        await conn.execute(text("UPDATE plenum_cafm.migration_jobs SET progress_pct = 42 "
                                "WHERE id = CAST(:i AS uuid)"), {"i": rid})
    assert (await _row(engine, rid))["last_activity_at"] > first


async def test_installing_twice_takes_no_lock(engine):
    assert await sp.ensure_activity_is_stamped(engine) == "already_applied"


# -- what is marked, and what is not ------------------------------------------------------------------

async def test_a_silent_running_run_is_marked_interrupted(engine):
    rid = await _run(engine, step="5_output", silent_minutes=40)
    assert rid in await ir.mark_interrupted(engine, minutes=30)
    row = await _row(engine, rid)
    assert row["status"] == "failed" and row["completed_at"] is not None
    assert row["error_message"].startswith("Interrupted: nothing was heard from this run after ")
    assert "on step 5_output" in row["error_message"] and "re-run it from that step" in row["error_message"]


async def test_a_run_that_wrote_recently_is_left_running(engine):
    rid = await _run(engine, silent_minutes=10)
    assert rid not in await ir.mark_interrupted(engine, minutes=30)
    assert (await _row(engine, rid))["status"] == "running"


async def test_a_progress_beat_keeps_a_long_step_alive(engine):
    """The output step once spent 13 minutes uploading; it beats progress_pct as it goes. A beat
    after a long silence must reset the clock."""
    rid = await _run(engine, step="5_output", silent_minutes=40)
    async with engine.begin() as conn:
        await conn.execute(text("UPDATE plenum_cafm.migration_jobs SET progress_pct = 83 "
                                "WHERE id = CAST(:i AS uuid)"), {"i": rid})
    assert rid not in await ir.mark_interrupted(engine, minutes=30)
    assert (await _row(engine, rid))["status"] == "running"


@pytest.mark.parametrize("status", ["awaiting_review", "step_paused", "complete", "cancelled", "failed"])
async def test_a_run_that_is_not_running_is_never_touched(engine, status):
    """Waiting at a gate or between steps is waiting for a person, not a failure, however long;
    a finished run is finished."""
    rid = await _run(engine, status=status, silent_minutes=60 * 24 * 30)
    before = await _row(engine, rid)
    assert rid not in await ir.mark_interrupted(engine, minutes=30)
    after = await _row(engine, rid)
    assert (after["status"], after["error_message"]) == (before["status"], before["error_message"])


# -- nothing is judged without the stamp ----------------------------------------------------------------

async def test_without_the_trigger_nothing_is_marked(engine):
    """Judged from started_at alone, a busy run and a dead one look the same."""
    rid = await _run(engine, silent_minutes=40)
    async with engine.begin() as conn:
        await conn.execute(text(f"DROP TRIGGER {sp.ACTIVITY_TRIGGER} ON plenum_cafm.migration_jobs"))
    try:
        assert await ir.mark_interrupted(engine, minutes=30) == []
        assert (await _row(engine, rid))["status"] == "running"
    finally:
        assert await sp.ensure_activity_is_stamped(engine) == "applied"


async def test_a_run_already_going_when_the_stamp_arrives_gets_a_fresh_clock(engine):
    """Installed while a run is mid-way, the run has no last activity yet. It must not be judged
    from started_at -- two days ago -- and called dead at the first round."""
    async with engine.begin() as conn:
        await conn.execute(text(f"DROP TRIGGER {sp.ACTIVITY_TRIGGER} ON plenum_cafm.migration_jobs"))
    rid = await _run(engine)                       # inserted with no trigger: no stamp
    assert (await _row(engine, rid))["last_activity_at"] is None
    assert await sp.ensure_activity_is_stamped(engine) == "applied"
    assert (await _row(engine, rid))["last_activity_at"] is not None
    assert rid not in await ir.mark_interrupted(engine, minutes=30)


# -- the default, from the data -------------------------------------------------------------------------

def test_the_default_outlasts_the_slowest_step_ever_recorded():
    """24.6 minutes: the longest any step has taken across every run in hoistra_test (Output
    Generation, 6 Nov 2026). The threshold has to outlast it even if that step wrote nothing."""
    assert ir.AFTER_MINUTES > 24.6
