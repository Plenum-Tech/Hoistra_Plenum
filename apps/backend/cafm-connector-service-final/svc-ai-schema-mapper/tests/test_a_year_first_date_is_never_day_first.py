"""A year-first date is never read day-first.

On 28 Sep 2026 Bishopsgate Tower's workbook migrated its inspection dates as
"2026-07-10 00:00:00" (how an Excel date reaches the preprocessor). No DATE_FORMATS entry has a
time part, so the fallback ran pandas with dayfirst=True, which read year-DAY-month: 10 Jul
became 7 Oct, 5 Apr became 4 May, and 16 Sep became NaT - and four of five surviving cleared
the 80 % bar, so the swap was committed. Loaded by path so no service import chain is needed.
"""
import importlib.util
import os
import sys
import types

import pandas as pd

_HERE = os.path.dirname(__file__)


def _load():
    # preprocess_node imports the graph state and logging; stub what the pure function does not use.
    path = os.path.join(_HERE, "..", "src", "graph", "nodes", "preprocess_node.py")
    src = open(path, encoding="utf-8").read()
    start = src.index("DATE_FORMATS = [")
    end = src.index("async def preprocess_node")
    fn_start = src.index("def _coerce_dates(")
    fn_end = src.index("\n\n\n", fn_start) if "\n\n\n" in src[fn_start:] else len(src)
    mod = types.ModuleType("pp_dates")
    mod.__dict__.update(pd=pd, Optional=__import__("typing").Optional,
                        logger=types.SimpleNamespace(debug=lambda *a, **k: None))
    exec(src[start:end] + "\n" + src[fn_start:fn_end], mod.__dict__)
    return mod


pp = _load()


def coerce(values):
    df = pd.DataFrame({"inspection_date": values})
    assert pp._coerce_dates(df, "inspection_date")
    return [None if pd.isna(v) else v.date().isoformat() for v in df["inspection_date"]]


def test_excel_dates_with_a_time_part_keep_their_month():
    got = coerce(["2026-04-05 00:00:00", "2026-07-10 00:00:00", "2026-09-16 00:00:00",
                  "2026-07-10 00:00:00", "2026-04-05 00:00:00"])
    assert got == ["2026-04-05", "2026-07-10", "2026-09-16", "2026-07-10", "2026-04-05"]


def test_iso_timestamps_with_a_T_parse_too():
    assert coerce(["2026-09-03T10:15", "2026-09-13T08:00:00"]) == ["2026-09-03", "2026-09-13"]


def test_a_day_first_column_is_still_read_day_first():
    assert coerce(["05/04/2026", "16/09/2026", "10/07/2026"]) == ["2026-04-05", "2026-09-16", "2026-07-10"]
