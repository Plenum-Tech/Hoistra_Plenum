"""A migration the worker runs keeps the building the uploader selected.

Both upload doors (start-with-upload and start-with-upload-multi) take the building the uploader
had selected and put it in the inline run's state, where write_node hands it on as
default_building_id. A half-hourly meter export names an MPAN and no site, so that building is the
only thing its meter can be placed against. The ARQ enqueue in both doors left it out, and
worker.run_migration had no parameter to take it, so on 28 Sep 2026 (once the worker got past its
first checkpoint) the same file placed every reading inline and skipped every reading through the
worker.

Nothing has to be stored for a resume: MigrationState declares building_id, so it goes into the
checkpoint with the rest of the state and a resume after a gate reads it back from there.
"""
from __future__ import annotations

import asyncio
import io
import sys
from types import SimpleNamespace

# Several files in this folder stand in a bare cafm_shared (get_logger only) when it is not yet
# imported, and they are collected before this one. src.app needs the real package
# (configure_logging, telemetry), so drop the stand-in and let the import below load it.
if not hasattr(sys.modules.get("cafm_shared.logging"), "configure_logging"):
    for _name in [n for n in sys.modules if n == "cafm_shared" or n.startswith("cafm_shared.")]:
        del sys.modules[_name]

import arq
from fastapi import UploadFile
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from sqlalchemy.ext.asyncio import AsyncSession

from src import app as app_module
from src import migration_runs, worker
from src.graph.nodes import schema_db_writer
from src.graph.state import MigrationState

ORG = "ad88d1ed-4376-447c-8196-e4e5b81d5af6"
BUILDING = "5b0c6f0e-1f7e-4b43-9d0b-2a3c6d1e9f10"
MIGRATION = "971c7322-56cd-46ad-87e8-92b558370344"
STORED_PATH = f"migrations/{MIGRATION}/source/b101_half_hourly.csv"
READINGS = b"MPAN,Date,00:30,01:00\n1012345678901,2025-04-01,1.2,1.1\n"


# ── The upload doors hand the worker the building ──────────────────────────────


class _Queue:
    """Stands in for the ARQ pool: records what would have been enqueued."""

    def __init__(self):
        self.jobs: list[tuple[str, dict]] = []

    async def enqueue_job(self, name, **kwargs):
        self.jobs.append((name, kwargs))

    async def aclose(self):
        return None


class _RequestSession:
    """The request's session: the job row is added and committed, and goes nowhere."""

    def add(self, obj):
        return None

    async def commit(self):
        return None

    async def rollback(self):
        return None


class _Writes:
    """The app's session factory for the multi door's source-path update: goes nowhere."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, *args, **kwargs):
        return None

    async def commit(self):
        return None


def _door(path: str):
    return next(
        r.endpoint for r in app_module.app.routes
        if getattr(r, "path", None) == path and "POST" in (getattr(r, "methods", None) or ())
    )


def _with_a_queue(monkeypatch) -> _Queue:
    queue = _Queue()

    async def create_pool(*args, **kwargs):
        return queue

    async def stored(migration_id, filename, data, *, subdir="source"):
        return f"migrations/{migration_id}/{subdir}/{filename}"

    async def no_activity(*args, **kwargs):
        return None

    settings = SimpleNamespace(redis_url="redis://queue:6379", max_file_size_mb=10)
    monkeypatch.setattr(arq, "create_pool", create_pool)
    monkeypatch.setattr(app_module, "get_settings", lambda: settings)
    monkeypatch.setattr(app_module, "_store_migration_source", stored)
    monkeypatch.setattr(app_module, "get_migration_graph_instance", lambda: object())
    monkeypatch.setattr(app_module, "get_session_factory", lambda: _Writes)
    monkeypatch.setattr(schema_db_writer, "_sync_run_activity", no_activity)
    return queue


def _enqueued_run(queue: _Queue) -> dict:
    runs = [kwargs for name, kwargs in queue.jobs if name == "run_migration"]
    assert len(runs) == 1, queue.jobs
    return runs[0]


def test_the_single_file_door_hands_the_worker_its_building(monkeypatch):
    queue = _with_a_queue(monkeypatch)
    door = _door("/api/migration/start-with-upload")

    asyncio.run(
        door(
            file=UploadFile(file=io.BytesIO(READINGS), filename="b101_half_hourly.csv"),
            cmms_name="Custom",
            organization_id=ORG,
            building_id=BUILDING,
            session=_RequestSession(),
        )
    )

    assert _enqueued_run(queue).get("building_id") == BUILDING


def test_the_multi_file_door_hands_the_worker_its_building(monkeypatch):
    queue = _with_a_queue(monkeypatch)
    door = _door("/api/migration/start-with-upload-multi")

    async def upload_and_wait():
        started = await door(
            files=[UploadFile(file=io.BytesIO(READINGS), filename="b101_half_hourly.csv")],
            cmms_name="Custom",
            organization_id=ORG,
            building_id=BUILDING,
            session=_RequestSession(),
        )
        # The multi door combines the files and enqueues from a background task.
        assert await migration_runs.await_migration_run(started.migration_id, timeout=60)

    asyncio.run(upload_and_wait())

    assert _enqueued_run(queue).get("building_id") == BUILDING


# ── The worker puts it in the graph, and a resume reads it back ────────────────


class _WorkerSession(AsyncSession):
    """A real AsyncSession type with no database behind it."""

    async def execute(self, *args, **kwargs):
        job = SimpleNamespace(status="awaiting_review", started_at=None)
        return SimpleNamespace(scalar_one_or_none=lambda: job, first=lambda: (job.status,))

    async def commit(self):
        return None


def _graph_with_a_gate(at_ingest: dict, at_write: dict):
    """Ingest, a gate that pauses the run, then the write step, as the real graph orders them."""

    async def file_ingestion(state):
        at_ingest.update(state)
        return {"current_step": 1}

    async def field_mapping_gate(state):
        interrupt({"gate": "field_mapping"})
        return {"current_step": 2}

    async def write_to_plenum(state):
        at_write.update(state)
        return {"current_step": 3}

    graph = StateGraph(MigrationState)
    graph.add_node("ingest_node", file_ingestion)
    graph.add_node("field_mapping_gate", field_mapping_gate)
    graph.add_node("write_node", write_to_plenum)
    graph.add_edge(START, "ingest_node")
    graph.add_edge("ingest_node", "field_mapping_gate")
    graph.add_edge("field_mapping_gate", "write_node")
    graph.add_edge("write_node", END)
    # MemorySaver serialises every checkpoint the way PostgresSaver does, so a key that does not
    # survive the round trip is missing on resume here too.
    return graph.compile(checkpointer=MemorySaver())


def _worker_with(monkeypatch, graph):
    async def no_activity(*args, **kwargs):
        return None

    async def the_graph():
        return graph

    monkeypatch.setattr(worker, "get_async_session_factory", lambda: _WorkerSession)
    monkeypatch.setattr(worker, "get_migration_graph", the_graph)
    monkeypatch.setattr(schema_db_writer, "_sync_run_activity", no_activity)


def _start(**extra) -> dict:
    return asyncio.run(
        worker.run_migration(
            {},
            migration_id=MIGRATION,
            organization_id=ORG,
            cmms_name="Custom",
            source_blob_url=STORED_PATH,
            uploaded_by="streamlit_ui",
            source_blob_path=STORED_PATH,
            source_filename="b101_half_hourly.csv",
            **extra,
        )
    )


def test_a_worker_run_hands_the_building_to_the_graph(monkeypatch):
    at_ingest: dict = {}
    _worker_with(monkeypatch, _graph_with_a_gate(at_ingest, {}))

    result = _start(building_id=BUILDING)

    assert result["status"] != "failed", result
    assert at_ingest.get("building_id") == BUILDING


def test_the_building_is_still_there_after_a_gate(monkeypatch):
    at_write: dict = {}
    _worker_with(monkeypatch, _graph_with_a_gate({}, at_write))

    _start(building_id=BUILDING)
    assert at_write == {}, "the run should be paused at the gate, short of the write step"

    decisions = {"b101_half_hourly": [{"action": "approve", "source_field": "MPAN", "target_field": "mpan"}]}
    resumed = asyncio.run(
        worker.resume_migration({}, migration_id=MIGRATION, gate_type="field_mapping", decisions=decisions)
    )

    assert resumed["status"] != "failed", resumed
    assert at_write.get("building_id") == BUILDING


def test_a_job_queued_without_a_building_still_runs(monkeypatch):
    """Jobs enqueued before this change carry no building_id; they run as they did."""
    at_ingest: dict = {}
    _worker_with(monkeypatch, _graph_with_a_gate(at_ingest, {}))

    result = _start()

    assert result["status"] != "failed", result
    assert at_ingest.get("building_id") is None
