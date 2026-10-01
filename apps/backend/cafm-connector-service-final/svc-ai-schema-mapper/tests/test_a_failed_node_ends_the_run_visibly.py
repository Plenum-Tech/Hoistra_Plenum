"""A node that reports failure in its state ends the run as failed — recorded on the job row and
raised — instead of parking at its pause with the job still "running".

Every step node (ingest, deterministic, semantic, preprocess, hierarchy, output) catches its own
exceptions and returns state with status="failed" rather than raising. Nothing downstream read
that: LangGraph went on to the node's interrupt_after pause, the worker logged the graph as
complete, and the job row kept whatever status it last had — "running", with no gate, which
nothing can resume. The graph's node wrapper now writes the failure to the row and raises, so
the worker ends the run as failed and the card offers a retry from that step.
"""
from __future__ import annotations

import asyncio

import pytest

from src.graph import migration_graph
from src.graph.nodes import db_writer

MIG = "bce6b4d2-f338-4753-95a2-6e3bbf296d01"


class _Factory:
    def __init__(self) -> None:
        self.landed: list = []

    def __call__(self):
        return _Session(self)


class _Session:
    def __init__(self, factory: _Factory) -> None:
        self.factory = factory

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, stmt):
        self.factory.landed.append(stmt)

    async def commit(self):
        pass


@pytest.fixture
def row(monkeypatch):
    factory = _Factory()
    monkeypatch.setattr(db_writer, "_get_session_factory", lambda: factory)
    monkeypatch.setattr(db_writer, "_RETRY_DELAYS_S", (0, 0, 0, 0, 0))
    return factory


def test_a_failed_node_state_is_written_to_the_job_and_stops_the_run(row):
    out = {
        "migration_id": MIG,
        "status": "failed",
        "error_message": "write_step_pause(step_1_ingest) could not be written for migration",
        "error_node": 1,
    }

    with pytest.raises(migration_graph.NodeFailed) as err:
        asyncio.run(migration_graph.raise_if_node_failed("ingest_node", out))

    assert "ingest_node" in str(err.value)
    assert "step_1_ingest" in str(err.value)
    assert len(row.landed) == 1
    p = row.landed[0].compile().params
    assert p["status"] == "failed"
    assert "step_1_ingest" in p["error_message"]
    assert p["current_step"] == "error_node_1"


def test_a_healthy_node_state_passes_through_untouched(row):
    out = {"migration_id": MIG, "status": "running", "row_count": 12}

    asyncio.run(migration_graph.raise_if_node_failed("ingest_node", out))

    assert row.landed == []


def test_a_failed_node_without_a_migration_id_still_stops_the_run(row):
    with pytest.raises(migration_graph.NodeFailed):
        asyncio.run(migration_graph.raise_if_node_failed("hierarchy_node", {"status": "failed"}))

    assert row.landed == []


def test_a_non_dict_result_is_left_alone(row):
    asyncio.run(migration_graph.raise_if_node_failed("ingest_node", None))
    assert row.landed == []
