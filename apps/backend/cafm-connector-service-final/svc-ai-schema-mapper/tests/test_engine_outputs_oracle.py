"""`hoist-engine outputs` writes the output step's files exactly as the Python step writes them.

The Python side is the output step's own code (output_generator_node's builders and the export
package); the engine gets the same tables through the Arrow bridge. CSV, SQL and JSON are compared
byte for byte after gunzip; the workbook is read back from both sides and compared cell by cell.
"""
from __future__ import annotations

import asyncio
import gzip
import io
import json
from pathlib import Path

import pandas as pd
import pytest

from src.engine import client, store
from src.excel_parser import ExcelWorkbook
from src.export import build_nested_json
from src.graph.nodes import output_generator_node as og

pytestmark = pytest.mark.skipif(not client.engine_available(),
                                reason="hoist-engine not built (engine/scripts/dev.sh build-linux)")

GENERATED_AT = "2026-10-01T12:00:00.000000Z"
TRICKY = ['plain', 'comma, inside', 'quote " inside', 'new\nline', 'carriage\rreturn', 'both\r\nends', '',
          ' spaced ', 'emoji 🏗️ and 𝔘𝔫𝔦', 'é', '=SUM(1,2)', '=', '#N/A', "it's", 'tab\tin', 'back\\slash',
          ' line sep', 'zero-width​', None]


def scenario() -> dict:
    sites = [{"site_code": "S1", "site_name": "Harbour Point"}, {"site_code": "S2", "site_name": "Quay ✓"}]
    locations = [{"loc_code": "L1", "site_code": "s1", "name": "Plant room"},
                 {"loc_code": "L2", "site_code": "S1", "name": None},
                 {"loc_code": "L3", "site_code": "S9", "name": "Orphan"}]
    tricky = [{"label": f"v{i}", "value": v, "qty": 0 if i % 2 else str(i), "always_zero": 0}
              for i, v in enumerate(TRICKY)]
    return {
        "cleaned": {
            "sites": sites,
            "locations": locations,
            "Tricky Values": tricky,
            "WorkOrders": [{"wo": "W1", "Desc": "a,b", "hours": 0}, {"wo": "W2", "Desc": "c", "hours": 0}],
            "WorkOrders_2": [{"wo": "W3", "notes": 'say "hi"'}],
            "single": [{"only": ""}, {"only": "x"}, {"only": None}],
            "1 bad name": [{"Col With Space": "x", "UPPER": "y", "ok_col": "z"}],
        },
        "full": {
            "Tricky Values": tricky + [{"label": "extra", "value": "only in full", "qty": "9", "always_zero": 0}],
            "A sheet name that is longer than thirty-one characters": [{"a": "1"}],
            "A sheet name that is longer than thirty-one characters, again": [{"a": "2"}, {"a": None}],
            "gaps": [{"x": None, "y": None}, {"x": "1", "y": None}, {"x": None, "y": None}],
            "Empty": [],
        },
        "routing": {"WorkOrders": "work_orders", "WorkOrders_2": "work_orders", "single": "singles"},
        "hierarchies": [
            {"source_table": "locations", "target_table": "sites", "relationship_type": "CONTAINMENT",
             "source_column": "site_code", "target_column": "site_code"},
            {"source_table": "work_orders", "target_table": "singles", "relationship_type": "FK",
             "source_column": "wo", "target_column": "only"},
        ],
        "blocks": ["CREATE TABLE IF NOT EXISTS plenum_cafm.trade (id uuid);", "INSERT INTO plenum_cafm.trade VALUES (1);"],
    }


def python_outputs(sc: dict) -> dict:
    """What output_generator_node builds, with its own functions."""
    records = dict(sc["cleaned"])
    df_tables = {}
    for name, rows in sc["full"].items():
        records[name] = rows
        df_tables[name] = pd.DataFrame(rows)
    routed = og.routed_records(records, sc["routing"])
    nested = build_nested_json(records, {}, sc["hierarchies"])
    files = {"output.json": og.output_json_for(nested, records, GENERATED_AT),
             "output.sql": og.sql_script_for(routed, sc["hierarchies"], sc["blocks"])}
    for name, text in og.csv_exports_for(routed).items():
        files[f"table_{name}.csv"] = text
    xlsx, why = og.excel_bytes_for(df_tables, records)
    return {"files": files, "xlsx": xlsx, "xlsx_error": why, "records": records, "routed": routed}


def engine_outputs(sc: dict, tmp_path: Path) -> dict:
    full, cleaned, out = tmp_path / "full", tmp_path / "cleaned", tmp_path / "out"
    store.write_tables(full, sc["full"])
    store.write_tables(cleaned, sc["cleaned"])
    job = {"full_dir": str(full), "cleaned_dir": str(cleaned), "out_dir": str(out), "schema": "plenum_cafm",
           "table_routing": sc["routing"], "confirmed_hierarchies": sc["hierarchies"],
           "lookup_ddl_blocks": sc["blocks"], "generated_at": GENERATED_AT}
    return asyncio.run(client.run_engine("outputs", job, workdir=tmp_path))


def read_book(data: bytes) -> dict:
    wb = ExcelWorkbook(io.BytesIO(data))
    try:
        return {name: wb.read(name, header=None, dtype=str).fillna("<empty>").values.tolist()
                for name in wb.sheet_names}
    finally:
        wb.close()


def test_every_output_file_reads_as_the_python_step_writes_it(tmp_path):
    sc = scenario()
    py = python_outputs(sc)
    res = engine_outputs(sc, tmp_path)
    by_name = {f["name"]: f for f in res["files"]}
    assert set(by_name) == set(py["files"]) | {"output.xlsx"}
    for name, text in py["files"].items():
        f = by_name[name]
        assert f["gzip"] is True and f["raw_bytes"] == len(text.encode("utf-8")), name
        got = gzip.decompress(Path(f["path"]).read_bytes()).decode("utf-8")
        assert got == text, f"{name} differs"
    assert py["xlsx"] is not None, py["xlsx_error"]
    assert read_book(Path(by_name["output.xlsx"]["path"]).read_bytes()) == read_book(py["xlsx"])
    assert res["records_tables"] == [{"name": n, "rows": len(r), "columns": list(r[0].keys()) if r else []}
                                     for n, r in py["records"].items()]
    assert res["routed"] == [{"dest": d, "rows": len(r)} for d, r in py["routed"].items()]
    assert res["xlsx_skipped_reason"] == ""


def test_a_value_excel_cannot_hold_means_no_workbook(tmp_path):
    sc = scenario()
    sc["full"] = {"Bad": [{"a": "bell \x07 char"}]}
    py = python_outputs(sc)
    assert py["xlsx"] is None and "cannot be used in worksheets" in py["xlsx_error"]
    res = engine_outputs(sc, tmp_path)
    assert "output.xlsx" not in {f["name"] for f in res["files"]}
    assert "cannot be used in worksheets" in res["xlsx_skipped_reason"]


def test_a_sheet_title_excel_rejects_means_no_workbook(tmp_path):
    sc = scenario()
    sc["full"] = {"Q1/Q2 [draft]": [{"a": "1"}]}
    py = python_outputs(sc)
    assert py["xlsx"] is None and "Invalid character" in py["xlsx_error"]
    res = engine_outputs(sc, tmp_path)
    assert "output.xlsx" not in {f["name"] for f in res["files"]}
    assert res["xlsx_skipped_reason"] == py["xlsx_error"]


def test_a_character_xml_cannot_carry_means_no_workbook(tmp_path):
    sc = scenario()
    sc["full"] = {"Odd": [{"a": "non-character \ufffe here"}]}
    py = python_outputs(sc)
    assert py["xlsx"] is None, "openpyxl kept a character XML cannot carry"
    res = engine_outputs(sc, tmp_path)
    assert "output.xlsx" not in {f["name"] for f in res["files"]}
    assert res["xlsx_skipped_reason"] == py["xlsx_error"]
