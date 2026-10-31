"""A graph run that returns normally but whose final state carries an error is marked failed on
the job — never left "running" with nothing to resume it.

Two nodes report failure without raising and without status="failed": write_node on a rejected
final gate sets error_message and returns; human_review_node on an invalid decision sets
error_message / error_node and returns. The app's inline runners already check the returned
state for that; the ARQ worker's _run_graph returned "complete" and left the row as it was.
"""
from __future__ import annotations

import asyncio

import pytest

from src import worker
from src.graph.nodes import db_writer

MIG = "0b498dfd-7411-4a19-ba7d-71a5264bf250"


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


class _Graph:
    def __init__(self, final_state) -> None:
        self.final_state = final_state

    async def ainvoke(self, _input, config=None):
        return self.final_state


@pytest.fixture
def row(monkeypatch):
    factory = _Factory()
    monkeypatch.setattr(db_writer, "_get_session_factory", lambda: factory)
    monkeypatch.setattr(db_writer, "_RETRY_DELAYS_S", (0, 0, 0, 0, 0))

    async def _noop(*_a, **_k):
        return "failed"

    from src.graph.nodes import schema_db_writer

    monkeypatch.setattr(schema_db_writer, "_sync_run_activity", _noop)
    return factory


def test_a_rejected_write_marks_the_run_failed(row):
    graph = _Graph({"migration_id": MIG, "status": "running", "error_message": "Customer rejected handoff at Gate 3", "handoff_status": "rejected"})

    out = asyncio.run(worker._run_graph(graph, {}, {}, MIG, row))

    assert out["status"] == "failed"
    assert "rejected" in out["error"]
    p = row.landed[-1].compile().params
    assert p["status"] == "failed"
    assert "rejected" in p["error_message"]


def test_an_invalid_gate_decision_marks_the_run_failed_at_its_node(row):
    graph = _Graph({"migration_id": MIG, "status": "running", "error_message": "Invalid decision for Assets.Install Dt", "error_node": 4})

    out = asyncio.run(worker._run_graph(graph, {}, {}, MIG, row))

    assert out["status"] == "failed"
    p = row.landed[-1].compile().params
    assert p["status"] == "failed"
    assert p["current_step"] == "error_node_4"


def test_a_run_that_finished_cleanly_is_left_alone(row):
    graph = _Graph({"migration_id": MIG, "status": "complete", "error_message": None})

    out = asyncio.run(worker._run_graph(graph, {}, {}, MIG, row))

    assert out["status"] == "complete"
    assert row.landed == []


def test_a_paused_run_with_an_old_error_message_is_not_failed(row):
    # A step pause or a review gate is a healthy stop; an error_message left over from an
    # earlier, recovered problem must not end the run.
    for status in ("step_paused", "awaiting_review", "complete"):
        graph = _Graph({"migration_id": MIG, "status": status, "error_message": "old"})
        out = asyncio.run(worker._run_graph(graph, {}, {}, MIG, row))
        assert out["status"] == "complete"
    assert row.landed == []


def test_a_ddl_rollback_keeps_its_own_status_so_retry_ddl_still_works(row):
    # write_node rolls a failed DDL back, writes status="ddl_failed" (with the failing SQL)
    # itself and ends the graph. /retry-ddl accepts only that status, so the worker must not
    # rewrite the row as "failed" (review, 1 Oct 2026).
    graph = _Graph({"migration_id": MIG, "status": "ddl_failed", "error_node": 9,
                    "error_message": "DDL failed: relation \"trade\" already exists"})

    out = asyncio.run(worker._run_graph(graph, {}, {}, MIG, row))

    assert out["status"] == "ddl_failed"
    assert row.landed == [], "the row write_node made is the one that stands"
