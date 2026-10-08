"""A step pause or a gate that fails to write is retried; if the database stays away, the run fails
loudly instead of hanging.

On 30 Sep 2026 a run finished file ingestion, then the one UPDATE that records the pause was
refused by Azure Postgres ("[Errno 111] Connect call failed") one second before the next write
succeeded. db_writer logged it as non-fatal, the graph parked itself waiting for /advance, and the
job stayed `running` with no gate — a state nothing can resume. The card polled "running" for half
an hour. The status-deciding writes must land, or fail the run so it can be retried.
"""
from __future__ import annotations

import asyncio

import pytest

from src.graph.nodes import db_writer, schema_db_writer

MIG = "bce6b4d2-f338-4753-95a2-6e3bbf296d01"


class _Factory:
    """A session factory whose first `fail_first` sessions refuse the connection."""

    def __init__(self, fail_first: int) -> None:
        self.fail_first = fail_first
        self.opened = 0
        self.landed: list = []

    def __call__(self):
        self.opened += 1
        return _Session(self, self.opened <= self.fail_first)


class _Session:
    def __init__(self, factory: _Factory, fail: bool) -> None:
        self.factory = factory
        self.fail = fail

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, stmt):
        if self.fail:
            raise OSError(111, "Connect call failed ('74.243.249.171', 5432)")
        self.factory.landed.append(stmt)

    async def commit(self):
        pass


def _params(stmt) -> dict:
    return stmt.compile().params


@pytest.fixture
def quiet(monkeypatch):
    """No real sleeps, no run-activity sync (that has its own session)."""
    monkeypatch.setattr(db_writer, "_RETRY_DELAYS_S", (0, 0, 0, 0, 0), raising=False)

    async def _noop(*_a, **_k):
        return "running"

    monkeypatch.setattr(schema_db_writer, "_sync_run_activity", _noop)


def test_a_pause_lands_after_the_connection_is_refused_twice(quiet, monkeypatch):
    factory = _Factory(fail_first=2)
    monkeypatch.setattr(db_writer, "_get_session_factory", lambda: factory)

    asyncio.run(db_writer.write_step_pause(MIG, "step_1_ingest", {"node": 1}))

    assert factory.opened == 3
    assert len(factory.landed) == 1
    p = _params(factory.landed[0])
    assert p["status"] == "step_paused"
    assert p["pending_gate_type"] == "step_1_ingest"


def test_a_pause_that_cannot_be_written_fails_the_run_instead_of_hanging_it(quiet, monkeypatch):
    factory = _Factory(fail_first=99)
    monkeypatch.setattr(db_writer, "_get_session_factory", lambda: factory)

    with pytest.raises(db_writer.StatusWriteFailed) as err:
        asyncio.run(db_writer.write_step_pause(MIG, "step_1_ingest", {"node": 1}))

    assert "step_1_ingest" in str(err.value)
    assert "Connect call failed" in str(err.value)
    assert factory.landed == []
    assert factory.opened == 6


def test_a_gate_lands_after_a_dropped_connection(quiet, monkeypatch):
    factory = _Factory(fail_first=1)
    monkeypatch.setattr(db_writer, "_get_session_factory", lambda: factory)

    asyncio.run(db_writer.write_gate_payload(MIG, "pk_approval", {"gate": "pk_approval"}))

    assert factory.opened == 2
    p = _params(factory.landed[0])
    assert p["status"] == "awaiting_review"
    assert p["pending_gate_type"] == "pk_approval"


def test_a_gate_that_cannot_be_written_fails_the_run(quiet, monkeypatch):
    factory = _Factory(fail_first=99)
    monkeypatch.setattr(db_writer, "_get_session_factory", lambda: factory)

    with pytest.raises(db_writer.StatusWriteFailed) as err:
        asyncio.run(db_writer.write_gate_payload(MIG, "pk_approval", {"gate": "pk_approval"}))

    assert "pk_approval" in str(err.value)
    assert factory.landed == []


def test_clearing_a_gate_is_retried_but_never_fails_an_answered_run(quiet, monkeypatch):
    # The answer has already been applied by the time the gate is cleared; failing the run here
    # would lose it. The next status write repairs the row, so this one stays best effort.
    factory = _Factory(fail_first=99)
    monkeypatch.setattr(db_writer, "_get_session_factory", lambda: factory)

    asyncio.run(db_writer.clear_gate_payload(MIG))

    assert factory.landed == []
    assert factory.opened == 6


def test_a_progress_beat_stays_a_single_best_effort_write(quiet, monkeypatch):
    factory = _Factory(fail_first=99)
    monkeypatch.setattr(db_writer, "_get_session_factory", lambda: factory)

    asyncio.run(db_writer.update_node_progress(MIG, "1_ingest"))
    asyncio.run(db_writer.report_progress_pct(MIG, 12.5))

    assert factory.opened == 2
