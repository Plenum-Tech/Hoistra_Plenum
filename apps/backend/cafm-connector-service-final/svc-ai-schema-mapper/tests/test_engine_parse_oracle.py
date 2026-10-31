"""`hoist-engine parse` reads every fixture as ingest's Python parse reads it.

Python: ingest_node.parse_source_tables → the NaN scan → the duplicate-column merge (the node's
own steps). Engine: `hoist-engine parse` on the same file. Compared: the format, the delimiter (for
delimited text — a workbook has none), the preview rows and every row of every table (key order
included), the NaN report and the duplicate-column report; a file Python cannot read must fail in
the engine too.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.engine import client

from .engine_oracle.compare_parse import differences, engine_parse, python_parse
from .engine_oracle.parse_fixtures import write_fixtures

pytestmark = pytest.mark.skipif(not client.engine_available(),
                                reason="hoist-engine not built (engine/scripts/dev.sh build-linux)")

_FIXTURES = Path("/tmp/hoist-parse-fixtures")
NAMES = [p.name for p in write_fixtures(_FIXTURES)] if client.engine_available() else []


@pytest.mark.parametrize("name", NAMES)
def test_the_engine_parses_it_as_python_does(name, tmp_path):
    path = _FIXTURES / name
    py = python_parse(path)
    go = engine_parse(path, tmp_path)
    if not py["ok"]:
        assert not go["ok"] and go.get("code") in ("parse_error", "data_error"), go
        return
    assert go["ok"], go
    assert differences(py, go, csv=py["detected_file_format"] == "csv") == []


# A damaged export Python refuses, the engine refuses in Python's words (the part the cut lands in
# decides them): never a partial table, and never a different message for the same file.
DAMAGED = [n for n in NAMES if n.startswith("damaged_")]


@pytest.mark.parametrize("name", DAMAGED)
def test_a_damaged_workbook_is_refused_in_pythons_words(name, tmp_path):
    path = _FIXTURES / name
    py = python_parse(path)
    go = engine_parse(path, tmp_path)
    assert not py["ok"], "the fixture was meant to be one Python refuses"
    assert not go["ok"] and go.get("code") == "parse_error", go
    assert "Could not parse file: " + go["error"] == py["error"]
