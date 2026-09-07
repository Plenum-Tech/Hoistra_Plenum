"""Excel reading for the migration pipeline — a thin, fast abstraction over python-calamine.

WHY: openpyxl is pure-Python and is the dominant CPU/memory cost when ingesting large workbooks.
`python-calamine` is a Rust-based reader (typically several× faster and far lower memory) that
pandas 2.2+ exposes as an engine, so we get the speed while pandas still produces the EXACT same
DataFrames. Verified byte-identical to the openpyxl output (`dtype=str` → `to_dict`) for strings,
ints, floats, bools, dates, and empty cells — so no downstream mapping logic changes.

The rest of the pipeline consumes THIS module (``ExcelWorkbook``) instead of pandas' Excel API or
openpyxl directly, so the reader lives in one place:

    ExcelWorkbook            — read-once workbook (opens the file a single time)
    ├── sheet_names          — MetadataExtractor: sheet enumeration
    ├── header_row(sheet)    — banner/title-row detection per sheet
    └── read(sheet, …)       — SheetReader/RowIterator: typed DataFrame (CellNormalizer = pandas dtype)

Writing .xlsx is unaffected — openpyxl is still used for export (output.xlsx / combined.xlsx); this
module is the READ path only.
"""
from __future__ import annotations

import time
from typing import Any

import pandas as pd

try:  # the shared structured logger in the running service …
    from cafm_shared.logging import get_logger

    logger = get_logger(__name__)
except Exception:  # … but this low-level reader stays importable/testable without shared-lib
    import logging

    logger = logging.getLogger(__name__)

# pandas engine id for python-calamine (reads .xlsx / .xlsm / .xltx / .xls / .xlsb / .ods). Pinned,
# with a graceful fallback to pandas' default engine so ingestion still works if the dependency is
# ever unavailable — the output is identical either way, only the speed differs.
_CALAMINE = "calamine"
try:  # presence check only — the actual reader is used via pandas' engine=
    import python_calamine  # noqa: F401

    _ENGINE: str | None = _CALAMINE
except Exception:  # pragma: no cover - defensive
    logger.warning("python-calamine not installed — Excel reads fall back to pandas' default engine")
    _ENGINE = None


class ExcelReadError(ValueError):
    """An Excel workbook could not be read (corrupt / password-protected / unsupported format)."""


def _friendly_error(exc: Exception) -> str:
    m = str(exc).lower()
    if "password" in m or "encrypt" in m:
        return "This Excel file is password-protected. Remove the password and upload it again."
    if "not a zip" in m or "badzip" in m or "corrupt" in m or "invalid" in m:
        return "This Excel file appears to be corrupted or is not a valid workbook."
    return f"Could not read the Excel file: {exc}"


def _detect_header_row(raw: pd.DataFrame, max_scan: int = 10) -> int:
    """0-indexed row that holds the real column headers.

    Real exports often open with a title/banner row (only the first cell populated, sometimes merged
    across the width), which would otherwise make every later cell ``Unnamed: N``. Scan the first
    ``max_scan`` rows and return the FIRST whose non-null count is at least half the widest seen —
    skipping banner rows (≈1 non-null cell) and stopping at the first full header strip. Falls back
    to row 0. (Operates on an already-read raw DataFrame so the sheet isn't parsed twice.)
    """
    if raw is None or raw.empty:
        return 0
    counts = [int(raw.iloc[i].notna().sum()) for i in range(min(len(raw), max_scan))]
    if not counts:
        return 0
    widest = max(counts)
    if widest <= 1:
        return 0  # nothing helpful — let pandas' default behaviour run
    threshold = max(2, widest // 2)
    for i, c in enumerate(counts):
        if c >= threshold:
            return i
    return 0


class ExcelWorkbook:
    """Read-once wrapper around a workbook.

    Opens the file a single time (``pd.ExcelFile`` with the calamine engine loads the whole workbook
    once); every ``read`` / ``header_row`` call reuses that loaded workbook rather than re-parsing —
    so a multi-sheet file is parsed once, not once per sheet. Use as a context manager to release it.
    """

    def __init__(self, source: Any):
        self._t0 = time.perf_counter()
        try:
            self._xls = pd.ExcelFile(source, engine=_ENGINE) if _ENGINE else pd.ExcelFile(source)
        except Exception as exc:  # corrupt / encrypted / unsupported → a clear, user-facing error
            raise ExcelReadError(_friendly_error(exc)) from exc
        logger.debug(
            "[excel] workbook opened via %s in %.0fms (%d sheet(s))",
            _ENGINE or "pandas-default",
            (time.perf_counter() - self._t0) * 1000,
            len(self._xls.sheet_names),
        )

    @property
    def sheet_names(self) -> list[str]:
        return list(self._xls.sheet_names)

    def header_row(self, sheet: str, max_scan: int = 10) -> int:
        try:
            raw = self._xls.parse(sheet, header=None, nrows=max_scan, dtype=str)
        except Exception:
            return 0
        return _detect_header_row(raw, max_scan)

    def read(
        self,
        sheet: str,
        *,
        header: int | None = 0,
        nrows: int | None = None,
        dtype: Any = str,
    ) -> pd.DataFrame:
        """Parse one sheet into a DataFrame (reuses the already-loaded workbook).

        ``dtype=str`` matches the pipeline's normalization (every cell → string) and is what the
        fidelity check verifies identical to openpyxl. Benchmarks emit at DEBUG level only.
        """
        t = time.perf_counter()
        df = self._xls.parse(sheet, header=header, nrows=nrows, dtype=dtype)
        dt = time.perf_counter() - t
        rows = len(df)
        logger.debug(
            "[excel] sheet %r parsed: %d rows × %d cols in %.0fms (%s rows/s)",
            sheet, rows, len(df.columns), dt * 1000,
            f"{rows / dt:,.0f}" if dt > 0 else "—",
        )
        return df

    def close(self) -> None:
        try:
            self._xls.close()
        except Exception:  # pragma: no cover - best effort
            pass

    def __enter__(self) -> "ExcelWorkbook":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
