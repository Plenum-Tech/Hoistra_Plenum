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


# ── A Go run's ingest, preprocess and output steps have no pause after them ──────────────────
# On a Python run, a step that records an error without status="failed" (a file that cannot be
# parsed, no data rows, data lost in dedup) pauses, and the worker then fails the run with that
# error. A Go run does not pause there, so the error itself must end it, in the step's own words.

def _wrapped_nodes(monkeypatch, **stubs):
    for name, fn in stubs.items():
        monkeypatch.setattr(migration_graph, name, fn)
    wrapped = {}
    monkeypatch.setattr(migration_graph, "StateGraph", lambda _s: type("SG", (), {
        "add_node": lambda self, n, f: wrapped.__setitem__(n, f),
        "add_edge": lambda self, *a, **k: None,
        "add_conditional_edges": lambda self, *a, **k: None,
        "set_entry_point": lambda self, *a, **k: None,
        "compile": lambda self, **k: self,
    })())

    async def not_cancelled(_mid):
        return False
    monkeypatch.setattr(migration_graph, "_is_cancelled", not_cancelled)
    try:
        migration_graph.build_migration_graph(None)
    except Exception:  # noqa: BLE001 — only the registered wrappers matter here
        pass
    return wrapped


async def _unreadable(state):
    state["error_message"] = "Could not parse file: syntax error: tag not closed: `>` not found before end of input"
    state["error_node"] = 1
    return state


def test_a_go_step_that_records_an_error_ends_the_run_in_its_words(row, monkeypatch):
    wrapped = _wrapped_nodes(monkeypatch, ingest_node=_unreadable)
    with pytest.raises(migration_graph.NodeFailed, match="Could not parse file"):
        asyncio.run(wrapped["ingest_node"]({"migration_id": MIG, "engine": "go"}))
    p = row.landed[0].compile().params
    assert p["status"] == "failed" and "Could not parse file" in p["error_message"]


def test_a_python_step_that_records_an_error_still_ends_at_its_pause(row, monkeypatch):
    wrapped = _wrapped_nodes(monkeypatch, ingest_node=_unreadable)
    asyncio.run(wrapped["ingest_node"]({"migration_id": MIG, "engine": "python"}))
    assert row.landed == []


def test_an_error_already_on_the_state_does_not_end_the_next_go_step(row, monkeypatch):
    async def preprocess(state):
        return state
    wrapped = _wrapped_nodes(monkeypatch, preprocess_node=preprocess)
    asyncio.run(wrapped["preprocess_node"]({"migration_id": MIG, "engine": "go", "error_message": "set before"}))
    assert row.landed == []


def test_a_judgement_step_is_not_held_to_this(row, monkeypatch):
    async def mapper(state):
        state["error_message"] = "No parsed data from Node 1"
        return state
    wrapped = _wrapped_nodes(monkeypatch, deterministic_mapper_node=mapper)
    asyncio.run(wrapped["deterministic_mapper_node"]({"migration_id": MIG, "engine": "go"}))
    assert row.landed == []
