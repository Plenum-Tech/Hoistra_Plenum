"""`hoist-engine preprocess` cleans every corpus table as Node 5's Python cleaning does.

Python: preprocess_node.preprocess_tables. Engine: steps.preprocess_on_engine (what the node's Go
branch runs) through the Arrow bridge. Compared: every cleaned row with its types (the numeric
fill's 0 is an int, a coerced date an ISO string), every renamed full row, the per-table counts,
the totals and every warning, word for word.
"""
from __future__ import annotations

import pytest

from src.engine import client

from .engine_oracle.compare_preprocess import differences, engine_preprocess, python_preprocess
from .engine_oracle.preprocess_corpus import cases

pytestmark = pytest.mark.skipif(not client.engine_available(),
                                reason="hoist-engine not built (engine/scripts/dev.sh build-linux)")

CASES = cases()


@pytest.mark.parametrize("case", CASES, ids=[c[0] for c in CASES])
def test_the_engine_cleans_it_as_python_does(case, tmp_path):
    _name, tables, mapping, skips = case
    py = python_preprocess(tables, mapping, skips)
    go = engine_preprocess(tables, mapping, skips, tmp_path)
    assert differences(py, go) == []
