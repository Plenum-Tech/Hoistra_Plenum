"""A migration the worker runs reaches its file.

On 28 Sep 2026 every upload the worker picked up (migration 971c7322, Bishopsgate Tower) failed at
File Ingestion with "Type is not msgpack serializable: AsyncSession". Two things were wrong:
run_migration put a live AsyncSession in the graph input, which the checkpointer cannot store, and
the stored blob path it handed Node 1 was dropped because MigrationState did not declare it, so
Node 1 read the relative path "migrations/..." as a URL and tried to reach a host named
"migrations".
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from sqlalchemy.ext.asyncio import AsyncSession

from src import worker
from src.graph import state as state_module
from src.graph.nodes import schema_db_writer
from src.graph.state import MigrationState

STORED_PATH = "migrations/971c7322/source/983a3860_bishopsgate.xlsx"


def _graph_that_records(seen: dict):
    async def file_ingestion(state):
        seen.update(state)
        return state

    graph = StateGraph(MigrationState)
    graph.add_node("ingest_node", file_ingestion)
    graph.add_edge(START, "ingest_node")
    graph.add_edge("ingest_node", END)
    # Pauses after the first step, as the real graph does, so the checkpoint is written.
    return graph.compile(checkpointer=MemorySaver(), interrupt_after=["ingest_node"])


def test_the_stored_path_reaches_file_ingestion():
    seen: dict = {}
    graph = _graph_that_records(seen)

    asyncio.run(
        graph.ainvoke(
            {"migration_id": "m-1", "source_blob_url": STORED_PATH, "source_blob_path": STORED_PATH},
            config={"configurable": {"thread_id": "m-1"}},
        )
    )

    assert seen.get("source_blob_path") == STORED_PATH


class _Session(AsyncSession):
    """A real AsyncSession type with no database behind it."""

    async def execute(self, *args, **kwargs):
        job = SimpleNamespace(status=None, started_at=None)
        return SimpleNamespace(scalar_one_or_none=lambda: job)

    async def commit(self):
        return None


def test_a_worker_run_survives_its_first_checkpoint(monkeypatch):
    seen: dict = {}
    graph = _graph_that_records(seen)

    async def no_activity(*args, **kwargs):
        return None

    async def the_graph():
        return graph

    monkeypatch.setattr(worker, "get_async_session_factory", lambda: _Session)
    monkeypatch.setattr(worker, "get_migration_graph", the_graph)
    monkeypatch.setattr(schema_db_writer, "_sync_run_activity", no_activity)

    result = asyncio.run(
        worker.run_migration(
            {},
            migration_id="971c7322-56cd-46ad-87e8-92b558370344",
            organization_id="ad88d1ed-4376-447c-8196-e4e5b81d5af6",
            cmms_name="Custom",
            source_blob_url=STORED_PATH,
            uploaded_by="streamlit_ui",
            source_blob_path=STORED_PATH,
            source_filename="983a3860_bishopsgate.xlsx",
        )
    )

    # A step pause returns from ainvoke normally, so a paused run reports "complete"; the
    # failure this guards against came back as {"status": "failed", "error": "...AsyncSession"}.
    assert result["status"] != "failed", result
    assert seen.get("source_blob_path") == STORED_PATH


def test_the_migration_state_carries_no_live_session():
    assert "db_session" not in state_module.MigrationState.__annotations__
