"""write_node on the Go engine: a plan before the write gate, one apply after it.

The engine itself is faked here (its own tests and the parity suite run the real one); these pin
what the node does around it — what the gate shows, that confirming writes once and records the
field mappings once, that a resume does not plan again, and what each engine failure becomes.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from langgraph.errors import GraphInterrupt

from src.engine import selection, steps, store
from src.engine.client import EngineError
from src.graph import migration_graph
from src.graph.nodes import db_writer
from src.graph.nodes import write_node as wn

MIGRATION = "5d1c2a90-0c4e-4d7e-9a35-2a8f3b1f2e11"
ORG = "11111111-1111-4111-8111-111111111111"
PLAN = {"tables": [{"source": "Vendors", "dest": "vendors", "rows": 2, "already_present": 1}],
        "ddl_statements": 0, "input_hash": "x"}
APPLIED = {"rows_inserted": 1, "tables_written": 1, "rows_skipped": 0, "rows_merged": 0,
           "buildings_linked": 0, "meters_linked": 0, "meters_created": 0, "meters_matched": 0,
           "meters_unlinked": [], "references": {}, "row_errors": [], "tables": []}


class FakeEngine:
    def __init__(self, apply_error: "EngineError | None" = None):
        self.calls: list[tuple[str, dict, dict]] = []
        self.apply_error = apply_error

    async def __call__(self, command, job, *, workdir=None, env=None, on_event=None, timeout_s=None):
        assert command == "write"
        self.calls.append((job["mode"], job, dict(env or {})))
        if job["mode"] == "plan":
            Path(job["out_dir"]).mkdir(parents=True, exist_ok=True)
            (Path(job["out_dir"]) / "plan.json").write_text(json.dumps(PLAN))
            return {"plan": PLAN}
        if self.apply_error:
            raise self.apply_error
        if on_event:
            await on_event({"type": "progress", "stage": "insert", "table": "vendors", "done": 2, "total": 2})
            await on_event({"type": "log", "level": "info", "message": "[Node 9] done"})
        return APPLIED

    def modes(self) -> list[str]:
        return [m for m, _j, _e in self.calls]


class Gate:
    """interrupt(): pauses the first time (GraphInterrupt), answers on the resume."""

    def __init__(self, answer: dict):
        self.answer, self.payloads, self.resumed = answer, [], False

    def __call__(self, payload):
        self.payloads.append(payload)
        if not self.resumed:
            raise GraphInterrupt(())
        return self.answer


@pytest.fixture()
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(selection, "ENGINE_STEPS", frozenset({"write"}))
    monkeypatch.setattr(store, "ENGINE_DIR", tmp_path / "engine")
    cleaned = tmp_path / "engine" / MIGRATION / "cleaned"
    cleaned.mkdir(parents=True)
    (cleaned / "manifest.json").write_text(json.dumps({"version": 1, "tables": []}))
    fake = FakeEngine()
    monkeypatch.setattr(steps, "run_engine", fake)
    monkeypatch.setattr(steps, "engine_db_env", lambda: {"HOIST_ENGINE_DSN": "postgres://parity"})
    recorded = {"gate": [], "cleared": 0, "errors": [], "mappings": 0, "finished": 0, "pct": []}

    async def _gate_payload(mid, gate, payload):
        recorded["gate"].append((gate, payload))

    async def _clear(mid):
        recorded["cleared"] += 1

    async def _error(mid, msg, error_node=None, status="failed"):
        recorded["errors"].append((msg, status))

    async def _pct(mid, pct):
        recorded["pct"].append(pct)

    async def _mappings(state):
        recorded["mappings"] += 1

    async def _finish(state):
        recorded["finished"] += 1
        state["status"] = "complete"
        return state

    monkeypatch.setattr(db_writer, "write_gate_payload", _gate_payload)
    monkeypatch.setattr(db_writer, "clear_gate_payload", _clear)
    monkeypatch.setattr(db_writer, "write_error", _error)
    monkeypatch.setattr(db_writer, "report_progress_pct", _pct)
    monkeypatch.setattr(wn, "_persist_field_mappings", _mappings)
    monkeypatch.setattr(wn, "_finish_successful_write", _finish)
    return fake, recorded


def go_state(**over) -> dict:
    state = {
        "migration_id": MIGRATION, "organization_id": ORG, "engine": "go",
        "engine_refs": {"cleaned": {"kind": "cleaned", "dir": "", "version": "v1", "blobs": {}, "complete": True}},
        "table_routing": {"Vendors": "vendors"},
        "confirmed_hierarchies": [{"source_table": "Vendors", "target_table": "Sites", "confidence": 0.9}],
        "intermediate_schema": {"source_type": "excel", "source_filename": "b101.xlsx",
                                "confidence": {"eval_score": 0.93}, "entities": {"vendors": [{}, {}]}},
        "extra_fields_config": [], "event_log": [], "building_id": None,
    }
    state.update(over)
    return state


def run(coro):
    return asyncio.run(coro)


def test_the_write_gate_shows_todays_summary_and_the_plan(env):
    fake, rec = env
    gate = Gate({"confirmed": True})
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(steps, "interrupt", gate)
        with pytest.raises(GraphInterrupt):
            run(wn.write_node(go_state()))
    assert fake.modes() == ["plan"]
    payload = gate.payloads[0]
    assert payload["summary"] == {"source_type": "excel", "source_filename": "b101.xlsx",
                                  "overall_confidence": 0.93, "entity_counts": {"vendors": 2},
                                  "total_entities": 2}
    assert payload["plan"] == PLAN and payload["migration_id"] == MIGRATION and payload["instructions"]
    assert rec["gate"] == [("write", payload)]
    assert rec["mappings"] == 0  # nothing is recorded before the person confirms


def test_the_job_carries_what_the_writer_needs(env):
    fake, _rec = env
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(steps, "interrupt", Gate({"confirmed": True}))
        with pytest.raises(GraphInterrupt):
            run(wn.write_node(go_state(building_id="b-1", column_dest_overrides={"Vendors": {"Colour": "__new__"}})))
    _mode, job, env_ = fake.calls[0]
    assert job["organization_id"] == ORG and job["default_building_id"] == "b-1"
    assert job["table_routing"] == {"Vendors": "vendors"}
    assert job["confirmed_hierarchies"] == [{"source_table": "Vendors", "target_table": "Sites"}]
    assert job["approved_new_columns"] == {"vendors": ["colour"]}
    assert job["ddl_statements"] == [] and job["column_renames"] == []
    assert job["rules"]["write_chunk"] == 500 and job["schema"] == "plenum_cafm"
    assert job["cleaned_dir"].endswith(f"{MIGRATION}/cleaned")
    assert env_ == {"HOIST_ENGINE_DSN": "postgres://parity"}
    assert "HOIST_ENGINE_DSN" not in json.dumps(job)


def test_confirming_applies_once_and_records_the_mappings_once(env):
    fake, rec = env
    gate = Gate({"confirmed": True})
    state = go_state()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(steps, "interrupt", gate)
        with pytest.raises(GraphInterrupt):
            run(wn.write_node(state))
        gate.resumed = True
        out = run(wn.write_node(state))
    assert fake.modes() == ["plan", "apply"]  # the resume reuses the plan
    assert rec["mappings"] == 1 and rec["finished"] == 1 and rec["cleared"] == 1
    assert out["handoff_status"] == "applied_sql_aligned"
    assert out["svc_ingestion_response"] == {"status": "applied_sql_aligned", **APPLIED}
    assert out["status"] == "complete"
    assert rec["pct"] and all(90.0 <= p <= 99.0 for p in rec["pct"])  # the engine drives the 90 → 99 beat


def test_a_changed_job_plans_again(env):
    fake, _rec = env
    gate = Gate({"confirmed": True})
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(steps, "interrupt", gate)
        for routing in ({"Vendors": "vendors"}, {"Vendors": "suppliers"}):
            with pytest.raises(GraphInterrupt):
                run(wn.write_node(go_state(table_routing=routing)))
    assert fake.modes() == ["plan", "plan"]


def test_a_rejected_write_writes_nothing(env):
    fake, rec = env
    gate = Gate({"confirmed": False})
    state = go_state()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(steps, "interrupt", gate)
        with pytest.raises(GraphInterrupt):
            run(wn.write_node(state))
        gate.resumed = True
        out = run(wn.write_node(state))
    assert fake.modes() == ["plan"] and rec["mappings"] == 0
    assert out["handoff_status"] == "rejected" and out["error_message"] == "Customer rejected handoff at GATE 3"


def _resume(env, error) -> dict:
    fake, _rec = env
    fake.apply_error = error
    gate = Gate({"confirmed": True})
    gate.resumed = True
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(steps, "interrupt", gate)
        return run(wn.write_node(go_state()))


def test_a_lost_connection_says_nothing_partial_was_kept(env):
    out = _resume(env, EngineError("connection_lost", "the database connection closed part way through the write: EOF"))
    assert out["error_message"].startswith(
        "The database connection closed part way through the write, so the run stopped. Nothing partial was "
        "kept. This is the connection, not the file — try the same upload again. (")
    assert out["error_node"] == 9 and out["el_m9_passed"] is False


def test_a_failed_ddl_statement_marks_the_run_ddl_failed(env):
    _fake, rec = env
    msg = "DDL execution failed at statement 2/2: 'second'. Database error: boom. All 1 previously executed statements were rolled back."
    out = _resume(env, EngineError("ddl_failed", msg))
    assert out["status"] == "ddl_failed" and out["error_message"] == msg and out["error_node"] == 9
    assert rec["errors"] == [(msg, "ddl_failed")]


def test_any_other_engine_failure_is_a_failed_write(env):
    out = _resume(env, EngineError("db_error", "relation does not exist"))
    assert out["error_message"] == "Schema-aligned write failed: relation does not exist"
    assert out["el_m9_passed"] is False


def test_a_python_run_never_calls_the_engine(env):
    fake, _rec = env
    out = run(wn.write_node(go_state(engine="python", intermediate_schema=None)))
    assert fake.calls == [] and out["error_message"] == "Missing IntermediateSchema from Node 8"


def test_a_go_write_does_not_load_the_rows_into_python():
    assert migration_graph.bulk_io_for("write_node", {"engine": "go"}, frozenset({"write"})) == (
        ["intermediate_schema"], [])
    assert migration_graph.bulk_io_for("write_node", {"engine": "python"}, frozenset({"write"})) == (
        ["cleaned_tables", "intermediate_schema", "output_sql_script"], [])
    assert migration_graph.bulk_io_for("write_node", {"engine": "go"}, frozenset()) == (
        ["cleaned_tables", "intermediate_schema", "output_sql_script"], [])


@pytest.mark.parametrize("password", ["ab#cdefgh", "ab?cd=1", "p/ss", "50%off", "c:lon"])
def test_the_engine_dsn_carries_every_password_the_writer_accepts(monkeypatch, password):
    # SQLAlchemy (the Python writer's parser) reads these passwords raw in DB_URL. The engine's DSN
    # must name the same host, port, database, user and password — a password cut at '#' or '?'
    # sends the engine nowhere and puts the start of the password in its error message.
    from types import SimpleNamespace
    from urllib.parse import unquote, urlparse

    from src import config

    url = f"postgresql+asyncpg://admin:{password}@srv.postgres.database.azure.com:5432/plenum_db"
    monkeypatch.setattr(config, "get_settings", lambda: SimpleNamespace(db_url=url))
    p = urlparse(steps.engine_db_dsn())
    assert (p.scheme, p.hostname, p.port, p.path) == ("postgresql", "srv.postgres.database.azure.com", 5432, "/plenum_db")
    assert (unquote(p.username), unquote(p.password)) == ("admin", password)
    assert "sslmode=verify-full" in p.query


def test_the_write_jobs_the_node_builds_are_ones_the_engine_takes(env):
    from .engine_contract import assert_the_engine_takes

    fake, _rec = env
    state = go_state(building_id="b-1", column_dest_overrides={"Vendors": {"Colour": "__new__"}})
    gate = Gate({"confirmed": True})
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(steps, "interrupt", gate)
        with pytest.raises(GraphInterrupt):
            run(wn.write_node(state))
        gate.resumed = True  # the resume: the gate answers, the engine applies
        run(wn.write_node(state))
    assert fake.modes() == ["plan", "apply"]
    for _mode, job, _env in fake.calls:
        assert_the_engine_takes("write", job)


def test_a_failed_write_plan_is_reported_and_nothing_is_written(env, monkeypatch):
    from src import worker

    fake, _rec = env

    async def broken(command, job, **k):
        fake.calls.append((job["mode"], job, {}))
        raise EngineError("db_error", "relation plenum_cafm.vendors does not exist")
    monkeypatch.setattr(steps, "run_engine", broken)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(steps, "interrupt", Gate({"confirmed": True}))
        out = run(wn.write_node(go_state()))
    assert fake.modes() == ["plan"] and out["error_node"] == 9 and out["el_m9_passed"] is False
    assert worker._error_in_final_state(out) == "Schema-aligned write failed: relation plenum_cafm.vendors does not exist"


def test_a_write_whose_cleaned_files_are_gone_fails_with_the_step_to_rerun(env, monkeypatch):
    fake, _rec = env
    monkeypatch.setattr(store, "_blob_conf", lambda: ("", ""))
    state = go_state()
    state["engine_refs"]["cleaned"] = {"kind": "cleaned", "dir": "/nowhere/cleaned/0123456789ab",
                                       "version": "0123456789ab", "blobs": {}, "complete": False}
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(steps, "interrupt", Gate({"confirmed": True}))
        out = run(wn.write_node(state))
    assert fake.modes() == [] and out["status"] == "failed"
    assert "Re-run the migration from Preprocess" in out["error_message"]
