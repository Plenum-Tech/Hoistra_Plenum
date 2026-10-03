"""ingest_node on the Go engine: the engine parses, the node does what it always did with the result.

The engine is faked with Python's own parse of the same bytes (its oracle runs the real one), so
the node's two branches can be compared: the same state for the same file, no rows on a Go run's
state (they are in the engine's full data set), no step pause (the run moves on by itself), and a
fallback to the Python parse — for the rest of the run — when the engine is missing or crashes.
"""
from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

import pytest

from src.engine import selection, steps, store
from src.engine.client import EngineError
from src.graph.nodes import db_writer, schema_db_writer
from src.graph.nodes import ingest_node as ing
from src.graph.nodes.column_merge import merge_duplicate_columns
from src.graph.nodes.nan_scan import scan_nan_values

MIGRATION = "3b9d6c4e-5a1f-4e2b-9c7d-8e6f5a4b3c2d"
# id and tag_number are one column (merged; id survives, being a destination column); note has a
# blank and an NA token (the NaN scan sees both).
CSV = b"id,tag_number,name,note\n1,1,Pump,x\n2,2,Fan,\n3,3,Valve,NA\n4,4,Chiller,y\n"


class FakeEngine:
    """`parse`, answered with what Python's parse, null scan and merge make of the same bytes."""

    def __init__(self, error: "EngineError | None" = None):
        self.jobs: list[dict] = []
        self.error = error

    async def __call__(self, command, job, *, workdir=None, env=None, on_event=None, timeout_s=None):
        assert command == "parse"
        self.jobs.append(job)
        if self.error:
            raise self.error
        ps = ing.parse_source_tables(Path(job["source"]).read_bytes())
        nan = scan_nan_values(ps.full_tables)
        merged, dup = merge_duplicate_columns(ps.full_tables)
        store.write_tables(Path(job["out_dir"]), merged)
        return {"detected_file_format": ps.detected_file_format, "source_delimiter": ps.source_delimiter,
                "source_encoding": job["encoding"] or "utf-8",
                "tables": [{"name": n, "rows": len(rows), "columns": list(rows[0]) if rows else [],
                            "kept_columns": list(merged[n][0]) if merged[n] else [],
                            "preview": ps.parsed_tables[n]} for n, rows in ps.full_tables.items()],
                "nan_report": nan, "duplicate_column_report": dup, "set_aside_sheets": ps.set_aside_sheets}


@pytest.fixture()
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(selection, "ENGINE_STEPS", frozenset({"parse", "outputs", "write"}))
    monkeypatch.setattr(store, "ENGINE_DIR", tmp_path / "engine")
    fake = FakeEngine()
    monkeypatch.setattr(steps, "run_engine", fake)
    rec = {"pauses": [], "logs": []}

    async def _pause(mid, step, payload):
        rec["pauses"].append(step)

    async def _progress(*a, **k):
        return None

    async def _node_log(mid, node, name, started, finished, output=None, logs=None, **kw):
        rec["logs"].append({"output": output, "logs": logs})

    async def _describe(df_head_str, column_names, client):
        return {c: f"about {c}" for c in column_names}

    def _no_db():
        raise RuntimeError("no database in this test")

    import src.db as db
    monkeypatch.setattr(db_writer, "write_step_pause", _pause)
    monkeypatch.setattr(db_writer, "update_node_progress", _progress)
    monkeypatch.setattr(schema_db_writer, "migration_append_node_log_auto", _node_log)
    monkeypatch.setattr(ing, "describe_dataset", _describe)
    monkeypatch.setattr(db, "get_async_session_factory", _no_db, raising=False)
    monkeypatch.setattr(db, "get_plenum_cafm_columns_by_table", lambda: _no_db(), raising=False)
    monkeypatch.setitem(sys.modules, "src.app", types.SimpleNamespace(get_anthropic_client=lambda: None))
    return fake, rec


def state_for(engine: str, content: bytes = CSV) -> dict:
    return {"migration_id": MIGRATION, "engine": engine, "organization_id": "", "cmms_name": "Fiix",
            "source_filename": "assets.csv", "source_file_bytes": content, "event_log": []}


def run(state: dict) -> dict:
    return asyncio.run(ing.ingest_node(state))


SAME = ("parsed_tables", "table_health", "overall_summary", "row_count", "column_count", "nan_report",
        "duplicate_column_report", "detected_file_format", "source_delimiter", "column_descriptions",
        "dataset_summary", "el_m1_passed", "current_step", "status", "error_message")


def test_a_go_parse_leaves_the_state_python_leaves(env):
    _fake, rec = env
    py = run(state_for("python"))
    go = run(state_for("go"))
    assert py.get("error_message") is None and py["duplicate_column_report"]["total_merges"] == 1
    for key in SAME:
        assert go.get(key) == py.get(key), key
    assert go["parsed_tables"]["data"][0] == {"id": "1", "name": "Pump", "note": "x"}
    assert go["source_encoding"] == "utf-8" and go["engine"] == "go"


def test_a_go_parse_keeps_its_rows_in_the_engine_data_set(env):
    fake, _rec = env
    go = run(state_for("go"))
    assert not go.get("full_tables")
    ref = go["engine_refs"]["full"]
    assert Path(ref["dir"]).parent == store.kind_dir(MIGRATION, "full") and Path(ref["dir"]).name == ref["version"]
    assert [r["name"] for r in store.read_tables(Path(ref["dir"]))["data"]] == ["Pump", "Fan", "Valve", "Chiller"]
    job = fake.jobs[0]
    assert job["encoding"] == "" and job["preview_rows"] == 10 and job["nan_sample_rows"] == 20
    assert job["out_dir"] == ref["dir"]
    assert "contractterms" in job["post_write_sheets"] and "asset_code" in job["known_destination_columns"]


def test_a_go_parse_writes_no_step_pause_and_logs_what_the_pause_carried(env):
    _fake, rec = env
    run(state_for("python"))
    assert rec["pauses"] == ["step_1_ingest"]
    rec["pauses"].clear()
    go = run(state_for("go"))
    assert rec["pauses"] == []
    out = rec["logs"][-1]["output"]
    assert out["engine"] == "go" and out["format"] == "csv" and out["table_health"] == go["table_health"]
    assert rec["logs"][-1]["logs"][0] == f"Parsed {go['row_count']} rows × {go['column_count']} columns (engine)"


def test_text_that_is_not_utf8_is_decoded_the_way_ingest_decodes_it(env):
    fake, _rec = env
    content = "id,name\n1,Caf\xe9 Ren\xe9e\n2,Na\xefve\n".encode("cp1252")
    py = run(state_for("python", content))
    go = run(state_for("go", content))
    assert go["parsed_tables"] == py["parsed_tables"] and go["source_encoding"] == py["source_encoding"]
    assert fake.jobs[0]["encoding"] == py["source_encoding"]


def test_an_engine_that_crashes_hands_the_run_to_python(env):
    fake, _rec = env
    fake.error = EngineError("internal", "parse exited 2: boom")
    go = run(state_for("go"))
    assert go["engine"] == "python" and go["full_tables"]["data"][0]["name"] == "Pump"
    assert "full" not in (go.get("engine_refs") or {}) and go.get("error_message") is None


def test_a_file_the_engine_cannot_read_fails_as_python_fails(env):
    fake, _rec = env
    fake.error = EngineError("parse_error", "Could not read the Excel file: Cannot detect file format")
    go = run(state_for("go"))
    assert go["error_message"] == "Could not parse file: Could not read the Excel file: Cannot detect file format"
    assert go["error_node"] == 1 and go["engine"] == "go"


def test_a_python_run_never_calls_the_engine(env):
    fake, _rec = env
    run(state_for("python"))
    assert fake.jobs == []


def test_a_parse_that_falls_back_to_python_inside_a_go_run_writes_no_step_pause(env):
    # The invocation running this node started as a Go run, which does not pause after ingest: a
    # pause written now is never taken, and the card and the deep-agent loop advance it while the
    # run is already going on — a second invocation of the same thread.
    fake, rec = env
    fake.error = EngineError("internal", "parse exited 2: boom")
    go = run(state_for("go"))
    assert go["engine"] == "python" and go.get("error_message") is None
    assert rec["pauses"] == []


@pytest.mark.skipif(not __import__("src.engine.client", fromlist=["engine_available"]).engine_available(),
                    reason="hoist-engine not built")
@pytest.mark.parametrize("label", ["bom_before_the_zip", "old_binary_xls", "opendocument_renamed"])
def test_a_workbook_that_is_not_a_zip_is_parsed_in_python(monkeypatch, tmp_path, label):
    # A run goes to the engine by its extension, confirmed by its bytes. A workbook upload whose
    # bytes are not a zip — a zip with bytes before it (a web export's BOM), or an old binary .xls
    # saved under an .xlsx name — is not something the engine reads, while Python's parse (CSV,
    # then calamine's sniffing) does: the run parses in Python and carries on as a Python run.
    import io

    import openpyxl

    monkeypatch.setattr(store, "ENGINE_DIR", tmp_path / "engine")
    wb = openpyxl.Workbook()
    wb.active.append(["code", "name"])
    wb.active.append(["A1", "Pump"])
    buf = io.BytesIO()
    wb.save(buf)
    import zipfile

    ods = io.BytesIO()
    with zipfile.ZipFile(ods, "w") as z:  # an OpenDocument sheet saved under an .xlsx name
        z.writestr("mimetype", "application/vnd.oasis.opendocument.spreadsheet")
        z.writestr("content.xml", "<office:document-content/>")
    content = {"bom_before_the_zip": b"\xef\xbb\xbf" + buf.getvalue(),
               "old_binary_xls": bytes.fromhex("D0CF11E0A1B11AE1") + b"\x00" * 600,
               "opendocument_renamed": ods.getvalue()}[label]
    state = {"migration_id": MIGRATION, "engine": "go", "source_filename": "assets.xlsx"}
    assert asyncio.run(steps.go_parse(state, content)) is None
    assert state["engine"] == "python" and "full" not in (state.get("engine_refs") or {})


def test_the_parse_job_the_node_builds_is_one_the_engine_takes(env):
    from .engine_contract import assert_the_engine_takes

    fake, _rec = env
    run(state_for("go"))
    assert_the_engine_takes("parse", fake.jobs[0])
