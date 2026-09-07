"""Excel parser (python-calamine) tests — fidelity vs openpyxl + the ExcelWorkbook abstraction.

Run: python tests/test_excel_parser.py
"""
import datetime
import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pandas as pd  # noqa: E402

from excel_parser import ExcelReadError, ExcelWorkbook, _ENGINE, _detect_header_row  # noqa: E402


def check(name, cond):
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        raise AssertionError(name)


def _fixture() -> bytes:
    """Multi-sheet workbook: mixed types + a banner row on the second sheet."""
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        pd.DataFrame({
            "name": ["Alice", "Bob", None, "Zoe"],
            "age": [30, 25, 41, None],
            "rate": [12.5, 0.0, 99.99, 3.14159],
            "active": [True, False, True, None],
            "joined": [datetime.datetime(2026, 1, 15, 9, 30), datetime.date(2020, 12, 31),
                       None, datetime.datetime(2019, 6, 1)],
            "code": ["V0001", "V0002", "V0003", "V0004"],
        }).to_excel(w, sheet_name="vendors", index=False)
        # A sheet whose real header is on row 2 (a one-cell banner sits on row 0).
        banner = pd.DataFrame(
            [["FM Transportation Log — Q1", None, None],
             ["site", "asset", "status"],
             ["S1", "A1", "open"],
             ["S2", "A2", "closed"]]
        )
        banner.to_excel(w, sheet_name="log", index=False, header=False)
    return buf.getvalue()


DATA = _fixture()


def _openpyxl_records(sheet, header=0):
    return pd.read_excel(io.BytesIO(DATA), sheet_name=sheet, dtype=str, header=header,
                         engine="openpyxl").to_dict(orient="records")


check("engine is python-calamine", _ENGINE == "calamine")

wb = ExcelWorkbook(io.BytesIO(DATA))
check("sheet_names correct + ordered", wb.sheet_names == ["vendors", "log"])

# ── FIDELITY: calamine output byte-identical to openpyxl for the pipeline's read shape ──
for sheet in ("vendors", "log"):
    ca = wb.read(sheet, header=0, dtype=str).to_dict(orient="records")
    op = _openpyxl_records(sheet, header=0)
    check(f"fidelity vs openpyxl — sheet {sheet!r}", ca == op)

# strings / ints / floats / bools / dates / empties all coerce to the same str repr
vend = wb.read("vendors", header=0, dtype=str).to_dict(orient="records")
check("date cell string identical to openpyxl", vend[0]["joined"] == "2026-01-15 09:30:00")
check("bool cell -> 'True'/'False'", vend[0]["active"] == "True" and vend[1]["active"] == "False")
check("int cell -> '30'", vend[0]["age"] == "30")
check("empty cell -> NaN (float nan)", isinstance(vend[2]["name"], float))

# ── HEADER DETECTION: the 'log' sheet's real header is row 1 (banner on row 0) ──
raw = wb.read("log", header=None, dtype=str)
check("header row detected past the banner", _detect_header_row(raw) == 1)
hr = wb.header_row("log")
detected = wb.read("log", header=hr, dtype=str)
check("detected-header read yields real columns", list(detected.columns) == ["site", "asset", "status"])
check("detected-header read drops the banner row", len(detected) == 2)

# ── PREVIEW SLICE == nrows read (the ingest optimization: slice, don't re-parse) ──
full = wb.read("vendors", header=0, dtype=str)
head_slice = full.head(3).to_dict(orient="records")
nrows_read = wb.read("vendors", header=0, nrows=3, dtype=str).to_dict(orient="records")
check("head(n) slice equals a fresh nrows=n read", head_slice == nrows_read)
wb.close()

# ── ERROR HANDLING: a corrupt / non-workbook byte stream -> a clear ExcelReadError ──
try:
    ExcelWorkbook(io.BytesIO(b"this is definitely not a workbook"))
    check("corrupt file raises ExcelReadError", False)
except ExcelReadError as e:
    check("corrupt file raises ExcelReadError", True)
    check("corrupt error message is user-facing", "corrupt" in str(e).lower() or "read the excel" in str(e).lower())

print("\nALL TESTS PASSED")
