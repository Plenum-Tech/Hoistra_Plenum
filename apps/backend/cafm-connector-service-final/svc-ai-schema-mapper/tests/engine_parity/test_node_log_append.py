"""A node's log entry goes to the job row by itself.

Every node completion read the job's whole node_logs array and wrote it back with one entry more:
on a 2.8 Mbit/s uplink that was ~5.7 s per node by the end of a run (2 Oct 2026), and two writers
at once lost one entry. One UPDATE … || appends the entry where it lies. Parity database only.
"""
import asyncio
import json
import uuid
from datetime import datetime, timedelta

import pytest

from tests.engine_parity.conftest import DSN, reset_parity_run

pytestmark = pytest.mark.skipif(not DSN, reason="parity database not available (HOIST_PARITY=1)")


async def _job_with_logs(n_old: int) -> tuple[uuid.UUID, list]:
    from sqlalchemy import insert

    from src.db import get_async_engine
    from src.models.migration import MigrationJob

    await reset_parity_run()
    engine = get_async_engine()
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: MigrationJob.__table__.create(c, checkfirst=True))
    old = [{"node_id": i, "node_name": f"n{i}", "logs": ["x" * 2000] * 20, "output": {"k": i}} for i in range(n_old)]
    mid = uuid.uuid4()
    async with engine.begin() as conn:
        await conn.execute(insert(MigrationJob).values(
            id=mid, organization_id=uuid.UUID("11111111-1111-4111-8111-111111111111"), cmms_name="Custom",
            source_filename="f.xlsx", status="running", started_at=datetime(2026, 10, 2, 9, 0), node_logs=old))
    return mid, old


async def _append(mid, node_id, logs):
    from src.db import get_async_session_factory
    from src.services.job_progress import append_migration_node_log

    t0 = datetime(2026, 10, 2, 9, 0, 0)
    async with get_async_session_factory()() as s:
        await append_migration_node_log(s, mid, node_id, f"node {node_id}", t0, t0 + timedelta(seconds=2),
                                        {"rows": node_id}, logs)


async def _logs(mid) -> list:
    import asyncpg

    c = await asyncpg.connect(DSN)
    try:
        return json.loads(await c.fetchval("SELECT node_logs::text FROM plenum_cafm.migration_jobs WHERE id = $1", mid))
    finally:
        await c.close()


async def test_an_entry_is_appended_without_sending_the_others_again(parity_db, monkeypatch):
    from sqlalchemy import event

    from src.db import get_async_engine

    mid, old = await _job_with_logs(8)
    sent = []

    def _count(conn, cursor, statement, params, context, executemany):
        sent.append(len(str(params)))
    event.listen(get_async_engine().sync_engine, "before_cursor_execute", _count)
    try:
        await _append(mid, 9, ["[Node 9] wrote 3 rows"])
    finally:
        event.remove(get_async_engine().sync_engine, "before_cursor_execute", _count)
    got = await _logs(mid)
    assert got[:8] == old
    new = got[8]
    assert (new["node_id"], new["node_name"], new["logs"], new["output"]) == (9, "node 9", ["[Node 9] wrote 3 rows"], {"rows": 9})
    assert new["started_at"] == "2026-10-02T09:00:00" and new["duration_ms"] == 2000
    assert "trigger" in new and "status" in new                    # the Activity-Log fields, as before
    assert max(sent) < 10_000, f"the earlier entries ({len(json.dumps(old)):,} bytes) went up again"


async def test_two_nodes_finishing_at_once_both_keep_their_entry(parity_db):
    mid, _ = await _job_with_logs(1)
    await asyncio.gather(*(_append(mid, 20 + i, [f"line {i}"]) for i in range(6)))
    got = await _logs(mid)
    assert sorted(e["node_id"] for e in got[1:]) == [20, 21, 22, 23, 24, 25]


async def test_a_job_row_with_no_logs_yet_starts_its_list(parity_db):
    import asyncpg

    mid, _ = await _job_with_logs(0)
    c = await asyncpg.connect(DSN)
    try:
        await c.execute("UPDATE plenum_cafm.migration_jobs SET node_logs = NULL WHERE id = $1", mid)
    finally:
        await c.close()
    await _append(mid, 1, ["a"])
    got = await _logs(mid)
    assert [e["node_id"] for e in got] == [1]


async def test_a_json_null_list_starts_a_new_one_as_before(parity_db):
    # The read-and-rewrite turned a stored JSON null into [entry] ((row[0] or [])); appending to it
    # must not give [null, entry], which the status endpoint cannot read.
    import asyncpg

    mid, _ = await _job_with_logs(0)
    c = await asyncpg.connect(DSN)
    try:
        await c.execute("UPDATE plenum_cafm.migration_jobs SET node_logs = 'null'::jsonb WHERE id = $1", mid)
    finally:
        await c.close()
    await _append(mid, 1, ["a"])
    assert [e["node_id"] for e in await _logs(mid)] == [1]
