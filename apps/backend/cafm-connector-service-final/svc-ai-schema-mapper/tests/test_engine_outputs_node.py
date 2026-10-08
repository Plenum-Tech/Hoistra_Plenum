"""output_generator_node on the Go engine: the engine writes the files, the node uploads them.

The engine is faked (its oracle runs the real one). These pin what the node does around it: the
job it sends, the files it uploads and how, the state keys it sets (and the bulk ones it no longer
sets), the entity counts the write gate reads, and that a Go run writes no step pause.
"""
from __future__ import annotations

import asyncio
import gzip
import json
from pathlib import Path

import pytest

from src.engine import selection, steps, store
from src.graph import migration_graph
from src.graph.nodes import db_writer, schema_db_writer
from src.graph.nodes import output_generator_node as og

MIGRATION = "7a3c2b10-1d4e-4f5a-8b6c-9d0e1f2a3b4c"
RESULT_TABLES = [{"name": "Vendors", "rows": 2, "columns": ["vendor_code", "vendor_name"]},
                 {"name": "Suppliers", "rows": 1, "columns": ["vendor_code"]},
                 {"name": "Assets", "rows": 3, "columns": ["asset_code"]},
                 {"name": "Empty", "rows": 0, "columns": []}]


class FakeEngine:
    def __init__(self):
        self.jobs: list[dict] = []

    async def __call__(self, command, job, *, workdir=None, env=None, on_event=None, timeout_s=None):
        assert command == "outputs"
        self.jobs.append(job)
        out = Path(job["out_dir"])
        out.mkdir(parents=True, exist_ok=True)
        files = []
        for name, local, text in (("output.json", "output.json.gz", '{"tables": {}}'),
                                  ("output.sql", "output.sql.gz", "BEGIN;\nCOMMIT;"),
                                  ("table_vendors.csv", "table_0000.csv.gz", "vendor_code\nV1\n")):
            (out / local).write_bytes(gzip.compress(text.encode()))
            files.append({"name": name, "path": str(out / local), "gzip": True, "raw_bytes": len(text)})
        (out / "output.xlsx").write_bytes(b"PK-not-really")
        files.append({"name": "output.xlsx", "path": str(out / "output.xlsx"), "gzip": False, "raw_bytes": 13})
        if on_event:
            await on_event({"type": "progress", "stage": "outputs", "table": "output.json", "done": 1, "total": 4})
        return {"files": files, "records_tables": RESULT_TABLES,
                "routed": [{"dest": "vendors", "rows": 3}, {"dest": "assets", "rows": 3}, {"dest": "Empty", "rows": 0}],
                "xlsx_skipped_reason": ""}


@pytest.fixture()
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(selection, "ENGINE_STEPS", frozenset({"write", "outputs"}))
    monkeypatch.setattr(store, "ENGINE_DIR", tmp_path / "engine")
    for kind in ("full", "cleaned"):
        d = tmp_path / "engine" / MIGRATION / kind
        d.mkdir(parents=True)
        (d / "manifest.json").write_text(json.dumps({"version": 1, "tables": []}))
    fake = FakeEngine()
    monkeypatch.setattr(steps, "run_engine", fake)
    rec = {"uploads": None, "pause": 0, "progress": [], "node_log": 0, "pct": []}

    async def _upload(migration_id, artefacts, log, **_kw):
        rec["uploads"] = artefacts
        return {name: f"https://blob/{name}" for name in artefacts}, len(artefacts)

    async def _pause(*a, **k):
        rec["pause"] += 1

    async def _progress(mid, step, **kw):
        rec["progress"].append((step, kw))

    async def _node_log(*a, **k):
        rec["node_log"] += 1

    async def _pct(mid, pct):
        rec["pct"].append(pct)

    async def _no_types():
        return {}

    import src.db as db
    monkeypatch.setattr(og, "upload_all", _upload)
    monkeypatch.setattr(db_writer, "write_step_pause", _pause)
    monkeypatch.setattr(db_writer, "update_node_progress", _progress)
    monkeypatch.setattr(db_writer, "report_progress_pct", _pct)
    monkeypatch.setattr(schema_db_writer, "migration_append_node_log_auto", _node_log)
    monkeypatch.setattr(db, "get_plenum_cafm_column_types_by_table", _no_types)
    return fake, rec


def go_state(**over) -> dict:
    ref = {"dir": "", "version": "v1", "blobs": {}, "complete": True}
    state = {
        "migration_id": MIGRATION, "engine": "go", "cmms_name": "Fiix", "source_filename": "b101.xlsx",
        "organization_id": "11111111-1111-4111-8111-111111111111", "overall_confidence": 0.91,
        "engine_refs": {"full": {**ref, "kind": "full"}, "cleaned": {**ref, "kind": "cleaned"}},
        "table_routing": {"Vendors": "vendors", "Suppliers": "vendors", "Assets": "assets"},
        "confirmed_hierarchies": [{"source_table": "Assets", "target_table": "Vendors", "relationship_type": "FK",
                                   "source_column": "vendor_code", "target_column": "vendor_code", "confidence": 0.9}],
        "column_intelligence": {"shared_attribute_tables": [{"ddl_block": "CREATE TABLE x (id int);"}, {"name": "no block"}]},
        "event_log": [],
    }
    state.update(over)
    return state


def run(state):
    return asyncio.run(og.output_generator_node(state))


def test_the_engine_writes_the_files_and_the_node_uploads_them(env):
    fake, rec = env
    out = run(go_state())
    job = fake.jobs[0]
    assert job["table_routing"] == {"Vendors": "vendors", "Suppliers": "vendors", "Assets": "assets"}
    assert job["confirmed_hierarchies"] == [{"source_table": "Assets", "target_table": "Vendors",
                                             "relationship_type": "FK", "source_column": "vendor_code",
                                             "target_column": "vendor_code"}]
    assert job["lookup_ddl_blocks"] == ["CREATE TABLE x (id int);"]
    assert job["full_dir"].endswith(f"{MIGRATION}/full") and job["cleaned_dir"].endswith(f"{MIGRATION}/cleaned")
    assert job["generated_at"].endswith("Z") and job["schema"] == "plenum_cafm"

    up = rec["uploads"]
    assert list(up) == ["output.json", "output.sql", "structure.md", "migration_report.pdf", "output.xlsx",
                        "table_vendors.csv"]
    assert isinstance(up["output.json"], steps.EngineFile) and up["output.json"].raw_len == len('{"tables": {}}')
    assert isinstance(up["output.xlsx"], steps.EngineFile)
    assert "### Vendors\n- Rows: 2\n- Columns (2): vendor_code, vendor_name" in up["structure.md"]
    assert isinstance(up["migration_report.pdf"], (bytes, bytearray))

    assert out["output_json_url"] == "https://blob/output.json"
    assert out["output_csv_url"] == "https://blob/output.xlsx"
    assert out["output_sql_url"] == "https://blob/output.sql"
    assert out["migration_report_url"] == "https://blob/migration_report.pdf"
    assert out["output_structure_md_url"] == "https://blob/structure.md"
    assert out["exported_artefacts"]["by_type"] == {"json": 1, "csv": 1, "sql": 1, "pdf": 1}
    assert out["exported_artefacts"]["total_count"] == 6
    assert out["current_step"] == 8 and out["el_m8_passed"] is True and out["execution_logs"]
    assert "output_sql_script" not in out
    assert all(not rows for rows in out["intermediate_schema"]["entities"].values())
    assert out["intermediate_schema"]["source_filename"] == "b101.xlsx"
    assert out["engine_reports"]["outputs"]["records_tables"] == RESULT_TABLES
    assert out["engine_reports"]["entity_counts"] == {"vendors": 3, "assets": 3}

    assert rec["pause"] == 0  # the proxy runs a Go run past this step
    assert [s for s, _kw in rec["progress"]] == ["8_output_generation"] and rec["node_log"] == 1
    assert out["event_log"][-1]["event"] == "node_complete"


def test_an_engine_file_is_sent_as_it_is_with_the_settings_its_name_gets(tmp_path):
    p = tmp_path / "x.gz"
    p.write_bytes(gzip.compress(b"a,b\n"))
    data, settings, raw = og.encode_artefact("table_x.csv", steps.EngineFile(str(p), 4))
    assert data == p.read_bytes() and raw == 4
    assert settings.content_encoding == "gzip" and settings.content_type == "text/csv; charset=utf-8"
    assert settings.content_disposition == 'attachment; filename="table_x.csv"'
    x = tmp_path / "w.xlsx"
    x.write_bytes(b"PK")
    data, settings, raw = og.encode_artefact("output.xlsx", steps.EngineFile(str(x), 2))
    assert data == b"PK" and settings is None and raw == 2


def test_a_python_run_never_calls_the_engine(env):
    fake, rec = env
    out = run(go_state(engine="python", cleaned_tables={"Vendors": [{"vendor_code": "V1"}]}, full_tables=None))
    assert fake.jobs == [] and out.get("el_m8_passed") is True and rec["pause"] == 1


def test_a_go_output_step_does_not_load_the_rows_into_python():
    steps_ = frozenset({"write", "outputs"})
    assert migration_graph.bulk_io_for("output_generator_node", {"engine": "go"}, steps_) == (
        [], ["intermediate_schema", "output_sql_script"])
    assert migration_graph.bulk_io_for("output_generator_node", {"engine": "python"}, steps_) == (
        ["full_tables", "cleaned_tables"], ["intermediate_schema", "output_sql_script"])


def test_the_outputs_job_the_node_builds_is_one_the_engine_takes(env):
    from .engine_contract import assert_the_engine_takes

    fake, _rec = env
    run(go_state())
    assert_the_engine_takes("outputs", fake.jobs[0])


def test_an_engine_failure_in_outputs_ends_the_run_visibly(env, monkeypatch):
    from src.engine.client import EngineError
    from src.graph import migration_graph

    async def broken(*a, **k):
        raise EngineError("internal", "outputs exited 2: boom")

    async def _no_write(*a, **k):
        return None
    monkeypatch.setattr(steps, "run_engine", broken)
    monkeypatch.setattr(db_writer, "write_error", _no_write)
    out = run(go_state())
    assert out["status"] == "failed" and "boom" in out["error_message"]
    with pytest.raises(migration_graph.NodeFailed):
        asyncio.run(migration_graph.raise_if_node_failed("output_generator_node", out))


def test_outputs_whose_engine_files_are_gone_fail_with_the_step_to_rerun(env, monkeypatch, tmp_path):
    import shutil

    from src.engine.store import EngineStoreMissing

    shutil.rmtree(tmp_path / "engine" / MIGRATION)
    monkeypatch.setattr(store, "_blob_conf", lambda: ("", ""))
    with pytest.raises(EngineStoreMissing, match="Re-run the migration from"):
        run(go_state())
