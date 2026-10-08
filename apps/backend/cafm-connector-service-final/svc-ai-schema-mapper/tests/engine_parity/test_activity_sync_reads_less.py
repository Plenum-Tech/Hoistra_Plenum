"""The run-activity sync builds the same entry from the few log fields it reads (parity DB).

_sync_run_activity runs ~40 times a run and loaded the whole job row each time — node_logs is ~2 MB
by the late gates, each node's full log lines included — to read a node's id, status, times and a
handful of output numbers. Postgres now hands it just those; the entry it builds is the same.
"""
import copy
import uuid
from datetime import datetime

import pytest

from tests.engine_parity.conftest import DSN, reset_parity_run

pytestmark = pytest.mark.skipif(not DSN, reason="parity database not available (HOIST_PARITY=1)")

BIG = ["[Node] a long log line " + "x" * 400] * 50
NODE_LOGS = [
    {"node_id": 1, "node_name": "File Ingestion", "status": "complete", "started_at": "2026-10-02T09:00:00",
     "completed_at": "2026-10-02T09:00:40", "duration_ms": 40000, "logs": BIG,
     "output": {"parsed_tables": {"Sites": 1, "Assets": 56}, "table_names": ["Sites", "Assets"], "row_count": 280796,
                "column_intelligence": {"huge": BIG}}},
    {"node_id": 2, "node_name": "Deterministic", "status": "complete", "started_at": "2026-10-02T09:01:00",
     "completed_at": "2026-10-02T09:01:05", "duration_ms": 5000, "logs": BIG,
     "output": {"overall_confidence": 0.93, "cafm_table_matches": {"Sites": "sites", "Assets": "assets"}, "noise": BIG}},
    "not an entry",
    {"node_id": 6, "node_name": "Preprocess", "status": "complete", "started_at": "2026-10-02T09:05:00",
     "completed_at": "2026-10-02T09:05:30", "duration_ms": 30000, "logs": BIG,
     "output": {"total_cleaned_rows": 280790, "total_original_rows": 280796, "warning_count": 3, "samples": BIG}},
    {"node_id": 7, "node_name": "Hierarchy", "status": "complete", "started_at": "2026-10-02T09:06:00",
     "completed_at": "2026-10-02T09:06:20", "duration_ms": 20000, "output": {"hierarchy_count": 25}},
    {"node_id": 9, "node_name": "Output Generation", "status": "complete", "started_at": "2026-10-02T09:07:00",
     "completed_at": "2026-10-02T09:07:50", "duration_ms": 50000, "logs": BIG,
     "output": {"artifacts_uploaded": 22, "table_count": 17, "json_url": "https://x"}},
    {"node_id": 3, "node_name": "odd", "status": "running", "output": None},
    {"node_id": 4, "node_name": "odd2", "status": "complete", "output": ["not", "a", "dict"]},
]


async def _job():
    from sqlalchemy import insert

    from src.db import get_async_engine
    from src.models.migration import MigrationJob

    await reset_parity_run()
    engine = get_async_engine()
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: MigrationJob.__table__.create(c, checkfirst=True))
    mid = uuid.UUID("451be644-3158-46dc-a6e3-a18e46c161b3")
    async with engine.begin() as conn:
        await conn.execute(insert(MigrationJob).values(
            id=mid, organization_id=uuid.UUID("11111111-1111-4111-8111-111111111111"), cmms_name="Custom",
            source_filename="f.xlsx", status="awaiting_review", started_at=datetime(2026, 10, 2, 9, 0),
            current_step="8_output_generation", pending_gate_type="write",
            pending_gate_payload={"suggested_target_by_table": {"Sites": "sites"}}, node_logs=NODE_LOGS))
    return str(mid)


async def _entry(monkeypatch, full: bool):
    from src.graph.nodes import schema_db_writer as w
    from src.udr import activity_persist as ap

    seen = {}

    async def no_state(**k):
        return {}

    async def capture(entry, **k):
        seen["entry"] = copy.deepcopy(entry)
    monkeypatch.setattr(ap, "get_run_entry_state", no_state)
    monkeypatch.setattr(ap, "upsert_run_activity", capture)
    if full:  # today: the whole node_logs array
        async def everything(s, mid):
            return copy.deepcopy(NODE_LOGS)
        monkeypatch.setattr(w, "_node_logs_for_activity", everything)
    mid = await _job()
    status = await w._sync_run_activity(mid, caller="test")
    return status, seen["entry"]


async def test_the_entry_is_the_same_from_the_trimmed_logs(parity_db, monkeypatch):
    full = await _entry(monkeypatch, True)
    monkeypatch.undo()
    trimmed = await _entry(monkeypatch, False)
    assert _without_clock(trimmed) == _without_clock(full)


def _without_clock(v, now_prefix=None):
    """The entry minus the moments the sync stamps with the wall clock (utcnow), which differ
    between any two syncs; every other value must match."""
    import re

    if isinstance(v, dict):
        return {k: _without_clock(x) for k, x in v.items()
                if not (k in ("at", "updated_at", "generated_at") and isinstance(x, str)
                        and re.match(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d+", x))}
    if isinstance(v, (list, tuple)):
        return [_without_clock(x) for x in v]
    return v


async def test_the_logs_it_reads_are_small(parity_db):
    import json

    from src.db import get_async_session_factory
    from src.graph.nodes.schema_db_writer import _node_logs_for_activity

    mid = await _job()
    async with get_async_session_factory()() as s:
        got = await _node_logs_for_activity(s, uuid.UUID(mid))
    assert len(json.dumps(got)) * 20 < len(json.dumps(NODE_LOGS))
    assert [e.get("node_id") if isinstance(e, dict) else e for e in got] == [1, 2, "not an entry", 6, 7, 9, 3, 4]
    assert got[0]["output"] == {"parsed_tables": {"Sites": 1, "Assets": 56}, "table_names": ["Sites", "Assets"]}
    assert got[6]["output"] is None and got[7]["output"] == ["not", "a", "dict"]
