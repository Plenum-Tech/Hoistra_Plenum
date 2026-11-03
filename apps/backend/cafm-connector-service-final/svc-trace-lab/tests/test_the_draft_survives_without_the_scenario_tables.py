"""The learning draft against a database where 0012 was never applied.

0012 is applied by a person and is not applied everywhere. The gate verdicts live in its tables;
the step notes and expected answers need nothing from it. Asking for all three unconditionally
made the WHOLE draft fail where 0012 is missing, so annotating a run taught nobody anything until
a DBA ran a migration that has nothing to do with notes.

Proved by dropping the tables rather than by reading the source: the guard can stay in the file
and the query still break, and a test that only greps for `to_regclass` would not notice.

A FILE OF ITS OWN, deliberately. Every real-SQL test here drops the `tracelab` schema and applies
every migration, and the fixture does not survive many of those in one file -- a sixth test in
test_a_saved_batch_is_reviewable.py left a connection idle in transaction and two DROP SCHEMA
statements queued behind it for fifteen minutes. One test, one clean schema.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from tl_scratch import lab_scratch, requires_scratch  # noqa: F401
from trace_lab import db

pytestmark = requires_scratch

ORG = "11111111-1111-4111-8111-111111111111"


@pytest.mark.asyncio
async def test_the_draft_is_still_built_when_the_scenario_tables_are_absent(lab_scratch):
    from trace_lab import learning

    async with db.lab_engine().begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS tracelab.scenario_results CASCADE"))
        await conn.execute(text("DROP TABLE IF EXISTS tracelab.scenario_runs CASCADE"))

    found = await learning.gather(ORG)

    assert found["gates_available"] is False, (
        "a zero here reads as 'no gate failures' when the truth is 'could not look'")
    assert found["gates"] == []
    # The point: the notes and the expected answers are still gathered, and it still drafts.
    assert found["counts"]["notes"] == 0 and found["counts"]["expected"] == 0
    assert "What the reviews say" in learning.draft(found)
