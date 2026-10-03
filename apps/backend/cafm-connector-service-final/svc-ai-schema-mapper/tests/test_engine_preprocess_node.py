"""preprocess_node on the Go engine: the engine cleans, the node does what it always did around it.

The engine is faked with Python's own cleaning of the same tables (its oracle runs the real one),
so the node's two branches can be compared: the same counts, warnings and EL-M.5 for the same
input; no rows on a Go run's state (the cleaned and renamed full data sets are the engine's); no
step pause, the node log carrying what the pause carried; the type guard sampling the same rows;
the confirmed keys renamed for the link statistics; and the hierarchy model given those links.
"""
from __future__ import annotations

import ast
import asyncio
import copy
import re
from pathlib import Path

import pytest

from src.engine import selection, steps, store
from src.graph import migration_graph
from src.graph.nodes import db_writer, schema_db_writer
from src.graph.nodes import preprocess_node as pp

MIGRATION = "8d7c6b5a-4e3f-4a2b-9c1d-0e9f8a7b6c5d"
FULL = {
    "Assets": [{"Tag": "A1", "Freq": "Quarterly", "Installed": "31/12/2025", "Junk": "j", "Gone": None},
               {"Tag": "A1", "Freq": "Quarterly", "Installed": "31/12/2025", "Junk": "j", "Gone": None},
               {"Tag": "A2", "Freq": None, "Installed": "01/02/2026", "Junk": "k", "Gone": None},
               {"Tag": "A3", "Freq": "Monthly", "Installed": None, "Junk": "l", "Gone": None}],
    "Sites": [{"Code": "S1", "Name": "Harbour"}, {"Code": "S2", "Name": None}],
    "Empty": [],
}


class FakeEngine:
    """`preprocess`, answered with preprocess_tables' cleaning of the same tables, reported as the
    engine reports it."""

    def __init__(self):
        self.jobs: list[dict] = []
        self.env: dict = {}

    async def __call__(self, command, job, *, workdir=None, env=None, on_event=None, timeout_s=None):
        assert command == "preprocess"
        self.jobs.append(job)
        self.env = dict(env or {})
        tables = store.read_tables(Path(job["full_dir"]))
        rename = job["rename_by_table"]
        skips = {t: set(v) for t, v in job["skip_by_table"].items()}
        r = pp.preprocess_tables(tables, rename, skips)
        store.write_tables(Path(job["cleaned_dir"]), r.cleaned_tables)
        store.write_tables(Path(job["renamed_full_dir"]), r.renamed_full)
        store.write_tables(Path(job["scratch_dir"]) / "deferred", {})
        report = []
        for t in r.cleaned_tables:
            nulls, dates = [], 0
            for w in r.warnings:
                if w.startswith(f"{t}: Dropped ") and "fully-null" in w:
                    nulls = ast.literal_eval(w.split(": ", 2)[2])
                if m := re.match(rf"{re.escape(t)}: Coerced (\d+) date columns", w):
                    dates = int(m.group(1))
            out = r.row_count_post_dedup_by_table[t]
            report.append({"name": t, "rows_in": out + r.dedup_drop_count_by_table[t], "rows_out": out,
                           "dedup_dropped": r.dedup_drop_count_by_table[t], "null_columns_dropped": nulls,
                           "date_columns": ["d"] * dates, "renamed": 0, "skipped": []})
        return {"tables": report, "python_date_columns": [],
                "prewrite": [{"dest": "assets", "sources": ["Assets"], "rows": 3, "exists": True,
                              "invalid_values": [], "required_missing": []}],
                "links": [{"table": "Assets", "column": "asset_code", "references": "Sites.site_code",
                           "containment": 0.5}]}


@pytest.fixture()
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(selection, "ENGINE_STEPS", frozenset({"parse", "preprocess", "outputs", "write"}))
    monkeypatch.setattr(store, "ENGINE_DIR", tmp_path / "engine")
    fake = FakeEngine()
    monkeypatch.setattr(steps, "run_engine", fake)
    monkeypatch.setattr(steps, "engine_db_env", lambda: {"HOIST_ENGINE_DSN": "postgres://read-only"})
    rec = {"pauses": [], "logs": [], "samples": []}

    async def _pause(mid, step, payload):
        rec["pauses"].append((step, payload))

    async def _progress(*a, **k):
        return None

    async def _node_log(mid, node, name, started, finished, output=None, logs=None, **kw):
        rec["logs"].append({"output": output, "logs": logs})

    async def _types():
        return {"assets": {"frequency_value": "integer", "frequency_type": "text", "asset_code": "text",
                           "install_date": "date"}}

    import src.db as db
    from src.matchers import type_compat

    real_choose = type_compat.choose_compatible_target

    def _choose(sf, tf, samples, dtypes, exclude=None):
        rec["samples"].append((sf, tf, list(samples)))
        return real_choose(sf, tf, samples, dtypes, exclude=exclude)

    monkeypatch.setattr(type_compat, "choose_compatible_target", _choose)
    monkeypatch.setattr(db_writer, "write_step_pause", _pause)
    monkeypatch.setattr(db_writer, "update_node_progress", _progress)
    monkeypatch.setattr(schema_db_writer, "migration_append_node_log_auto", _node_log)
    monkeypatch.setattr(db, "get_plenum_cafm_column_types_by_table", _types)
    return fake, rec


def state_for(engine: str, full: dict | None = None) -> dict:
    full = copy.deepcopy(FULL if full is None else full)
    st = {
        "migration_id": MIGRATION, "engine": engine, "event_log": [],
        "table_routing": {"Assets": "assets", "Sites": "sites"},
        "tier1_mappings_by_table": {"Assets": [
            {"source_field": "Tag", "target_field": "asset_code"},
            {"source_field": "Freq", "target_field": "frequency_value"},  # the type guard retargets it
            {"source_field": "Installed", "target_field": "install_date"},
        ], "Sites": [{"source_field": "Code", "target_field": "site_code"}]},
        "extra_fields_config": [{"source_table": "Assets", "source_field": "Junk", "storage_strategy": "skip"}],
        "pk_confirmed_by_table": {"Assets": ["Tag"], "Sites": ["Code"]},
    }
    if engine == "go":
        ref = asyncio.run(store.save_tables({"migration_id": MIGRATION}, "full", full))
        st["engine_refs"] = {"full": ref}
    else:
        st["full_tables"] = full
    return st


def run(state: dict) -> dict:
    return asyncio.run(pp.preprocess_node(state))


SAME = ("row_count_post_dedup_by_table", "dedup_drop_count_by_table", "data_quality_warnings", "el_m5_passed",
        "current_step", "error_message", "tier1_mappings_by_table")


def test_a_go_preprocess_leaves_the_state_python_leaves(env):
    py = run(state_for("python"))
    go = run(state_for("go"))
    assert py.get("error_message") is None and py["data_quality_warnings"]
    for key in SAME:
        assert go.get(key) == py.get(key), key


def test_its_rows_are_the_engines_data_sets(env):
    fake, _rec = env
    py = run(state_for("python"))
    st = state_for("go")
    parse_dir = Path(st["engine_refs"]["full"]["dir"])
    go = run(st)
    assert go["cleaned_tables"] == {} and go["full_tables"] == {}
    refs = go["engine_refs"]
    for ref, kind in ((refs["cleaned"], "cleaned"), (refs["full"], "renamed_full")):
        assert Path(ref["dir"]).parent == store.kind_dir(MIGRATION, kind) and Path(ref["dir"]).name == ref["version"]
    assert store.read_tables(Path(refs["cleaned"]["dir"])) == py["cleaned_tables"]
    assert store.read_tables(Path(refs["full"]["dir"])) == {t: r or [] for t, r in py["full_tables"].items()}
    # the parse's own full data set is left as it was, for a re-run of this step
    assert Path(fake.jobs[0]["full_dir"]) == parse_dir
    assert store.read_tables(parse_dir)["Assets"][0]["Tag"] == "A1"


def test_the_job_carries_the_plan_python_built(env):
    fake, _rec = env
    run(state_for("go"))
    job = fake.jobs[0]
    assert job["rename_by_table"]["Assets"]["Tag"] == "asset_code"
    assert job["rename_by_table"]["Assets"]["Freq"] != "frequency_value"  # the type guard's retarget
    assert job["skip_by_table"] == {"Assets": ["Junk"]}
    assert job["keys_by_table"] == {"Assets": ["asset_code"], "Sites": ["site_code"]}
    assert job["table_routing"] == {"Assets": "assets", "Sites": "sites"} and job["schema"] == "plenum_cafm"
    assert job["max_link_pairs"] == 200 and "organization_id" in job["system_supplied_columns"]
    assert fake.env == {"HOIST_ENGINE_DSN": "postgres://read-only"}


def test_the_type_guard_samples_the_same_rows(env):
    _fake, rec = env
    run(state_for("python"))
    py_samples, rec["samples"] = rec["samples"], []
    run(state_for("go"))
    assert rec["samples"] == py_samples and py_samples


def test_no_step_pause_and_the_node_log_carries_the_pause(env):
    _fake, rec = env
    run(state_for("python"))
    (step, payload), = rec["pauses"]
    assert step == "step_5_preprocess"
    rec["pauses"].clear()
    go = run(state_for("go"))
    assert rec["pauses"] == []
    out = rec["logs"][-1]["output"]
    assert out["engine"] == "go" and out["prewrite"][0]["dest"] == "assets"
    for key in ("rows_cleaned", "warnings", "warning_messages", "tables", "table_previews"):
        assert out[key] == payload[key], key
    assert rec["logs"][-1]["logs"][0].endswith("(engine)")
    assert go["engine_reports"]["preprocess"]["links"][0]["references"] == "Sites.site_code"


def test_el_m5_fails_as_python_fails(env):
    dupes = {"T": [{"a": "1"}] * 8 + [{"a": "2"}, {"a": "3"}]}
    py = run(state_for("python", dupes))
    go = run(state_for("go", dupes))
    assert py["el_m5_passed"] is False and go["el_m5_passed"] is False
    assert go["error_message"] == py["error_message"] == "Data loss during dedup: 30.0% remaining"


def test_a_go_preprocess_does_not_load_the_rows_into_python():
    steps_ = frozenset({"preprocess"})
    assert migration_graph.bulk_io_for("preprocess_node", {"engine": "go"}, steps_) == ([], [])
    assert migration_graph.bulk_io_for("preprocess_node", {"engine": "python"}, steps_) == (
        ["full_tables"], ["full_tables", "cleaned_tables"])


def test_the_hierarchy_model_is_given_the_measured_links(monkeypatch):
    from src.graph.nodes import hierarchy_node as hn

    seen = {}

    async def _infer(schema_summary, log):
        seen.update(schema_summary)
        raise RuntimeError("stop after the prompt")

    monkeypatch.setattr(hn, "_claude_infer_hierarchies", _infer)
    state = {"migration_id": None, "event_log": [],
             "cleaned_tables": {"Assets": [{"asset_code": "A1", "site_code": "S1"}], "Sites": [{"site_code": "S1"}]},
             "engine_reports": {"preprocess": {"links": [
                 {"table": "Assets", "column": "site_code", "references": "Sites.site_code", "containment": 0.6667}]}}}
    asyncio.run(hn.hierarchy_node(state))
    assert seen["Assets"]["measured_links"] == [{"column": "site_code", "references": "Sites.site_code",
                                                 "containment": 0.667}]
    assert "measured_links" not in seen["Sites"]


def test_the_preprocess_job_the_node_builds_is_one_the_engine_takes(env):
    from .engine_contract import assert_the_engine_takes

    fake, _rec = env
    run(state_for("go"))
    assert_the_engine_takes("preprocess", fake.jobs[0])


def test_an_engine_failure_in_preprocess_ends_the_run_visibly(env, monkeypatch):
    from src.engine.client import EngineError
    from src.graph import migration_graph

    async def broken(*a, **k):
        raise EngineError("internal", "preprocess exited 2: boom")

    async def _no_write(*a, **k):
        return None
    monkeypatch.setattr(steps, "run_engine", broken)
    monkeypatch.setattr(db_writer, "write_error", _no_write)
    out = run(state_for("go"))
    assert out["status"] == "failed" and "boom" in out["error_message"]
    with pytest.raises(migration_graph.NodeFailed):
        asyncio.run(migration_graph.raise_if_node_failed("preprocess_node", out))
