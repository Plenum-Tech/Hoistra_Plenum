"""spend.proposals_month_spent on a throwaway Postgres (tracelab_scratch): only the three proposal job kinds count, only
jobs created in the current UTC month, and it reads through the caller's connection or the lab engine. Skips without
TRACE_LAB_TEST_DB_URL."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from tl_scratch import lab_scratch, requires_scratch  # noqa: F401
from trace_lab import spend

pytestmark = requires_scratch


async def _job(conn, kind: str, usd: float, when: str = "now()") -> None:
    await conn.execute(text(
        "INSERT INTO tracelab.jobs (id, kind, status, scope_key, usd_cap, usd_spent, created_at) "
        f"VALUES (CAST(:id AS uuid), :k, 'done', :sk, 10, :u, {when})"),
        {"id": str(uuid.uuid4()), "k": kind, "sk": "test:" + str(uuid.uuid4()), "u": usd})


async def test_month_spend_counts_only_this_months_proposal_jobs(lab_scratch):
    async with lab_scratch.begin() as conn:
        await _job(conn, "proposal_cells", 1.5)
        await _job(conn, "golden_run", 3.0)                                       # another kind
        await _job(conn, "proposal_grouping", 2.0, "now() - interval '70 days'")  # another month
        await _job(conn, "proposal_depth_scan", 0.25)
    async with lab_scratch.connect() as conn:
        assert await spend.proposals_month_spent(conn) == pytest.approx(1.75)    # the caller's connection


async def test_month_spend_uses_the_lab_engine_when_no_connection_is_given(lab_scratch):
    assert await spend.proposals_month_spent() == 0.0
    async with lab_scratch.begin() as conn:
        await _job(conn, "proposal_grouping", 4.0)
    assert await spend.proposals_month_spent() == pytest.approx(4.0)


async def test_the_month_starts_at_midnight_utc_on_the_first(lab_scratch):
    async with lab_scratch.begin() as conn:
        await _job(conn, "proposal_cells", 1.0, "date_trunc('month', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'")
        await _job(conn, "proposal_cells", 2.0,
                   "date_trunc('month', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC' - interval '1 second'")
    assert await spend.proposals_month_spent() == pytest.approx(1.0)
