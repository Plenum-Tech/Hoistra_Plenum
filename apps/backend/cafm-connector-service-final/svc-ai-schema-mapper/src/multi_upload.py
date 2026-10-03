"""A multi-file upload becomes one workbook (POST /api/migration/start-with-upload-multi).

Each CSV/TSV becomes one sheet, named after its file; each workbook's worksheets one sheet each
(a lone generic Sheet1 is named after its file). The run then ingests that combined workbook like
any single upload, and it is the durable source a re-run reads.

On a Go build the engine reads the workbooks and writes the combined workbook
(`hoist-engine combine`); a CSV/TSV is still read here with pandas, exactly as before (its python
engine sniffs a .csv's delimiter from the first line), and handed over as a frame file.
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import re
import shutil
from pathlib import Path

import pandas as pd

from .engine.client import EngineError, run_engine
from .engine.progress import EngineProgress
from .excel_parser import ExcelWorkbook
from .graph.nodes.ingest_node import _sanitize_column_names

logger = logging.getLogger(__name__)

COMBINE_TIMEOUT_S = 1800.0
_ZIP = b"PK\x03\x04"


class CombineError(Exception):
    """An upload could not be read; the message names the file."""


def _ext(filename: str) -> str:
    return ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""


def sheet_namer():
    """_safe_sheet: a sheet name Excel accepts, unique (case-insensitively) across the upload."""
    used_sheets: set[str] = set()

    def _safe_sheet(base: str) -> str:
        invalid = set(r":\/?*[]")
        cleaned = "".join("_" if c in invalid else c for c in base).strip() or "sheet"
        cleaned = cleaned[:31]
        name = cleaned
        i = 2
        while name.lower() in used_sheets:
            suffix = f"_{i}"
            name = cleaned[: 31 - len(suffix)] + suffix
            i += 1
        used_sheets.add(name.lower())
        return name

    return _safe_sheet


def read_delimited(filename: str, data: bytes) -> pd.DataFrame:
    """A CSV/TSV as the combine reads it: pandas' python engine (a .csv's delimiter sniffed),
    every cell a string, names sanitised, NaN as ""."""
    sep = "\t" if _ext(filename) == ".tsv" else None
    df = pd.read_csv(io.BytesIO(data), dtype=str, sep=sep, engine="python")
    df = _sanitize_column_names(df)
    return df.fillna("")


def combine_in_python(raw_files: list[tuple[str, bytes]]) -> tuple[bytes, list[str]]:
    """The combined workbook and its sheet names (pandas + calamine read, openpyxl write)."""
    _safe_sheet = sheet_namer()
    sheets: dict[str, pd.DataFrame] = {}
    for filename, data in raw_files:
        ext = _ext(filename)
        stem = Path(filename).stem
        try:
            if ext in {".csv", ".tsv"}:
                sheets[_safe_sheet(stem)] = read_delimited(filename, data)
            else:
                wb = ExcelWorkbook(io.BytesIO(data))  # calamine, workbook opened once
                sheet_names = wb.sheet_names
                for sh in sheet_names:
                    # Skip banner / title rows so source column names survive
                    # instead of degrading to "Unnamed: N" placeholders.
                    header_row = wb.header_row(sh)
                    df = wb.read(sh, header=header_row, dtype=str)
                    df = _sanitize_column_names(df)
                    # Source table = the Excel SHEET name; fall back to the file name
                    # only for a lone generic sheet (Sheet1).
                    sheet_label = (str(sh) or "").strip()
                    is_generic = len(sheet_names) == 1 and re.match(
                        r"^sheet\s*\d*$", sheet_label, re.IGNORECASE
                    )
                    base = stem if is_generic else (sheet_label or stem)
                    sheets[_safe_sheet(base)] = df.fillna("")
                wb.close()
        except Exception as exc:
            raise CombineError(f"Could not parse '{filename}': {exc}") from exc
    if not sheets:
        raise CombineError("No parseable structured data found in uploads")
    combined = io.BytesIO()
    with pd.ExcelWriter(combined, engine="openpyxl") as writer:
        for name, df in sheets.items():
            df.to_excel(writer, sheet_name=name, index=False)
    return combined.getvalue(), list(sheets.keys())


def _engine_ready() -> bool:
    from .engine.client import engine_available
    from .engine.selection import ENGINE_GO, configured_engine

    return configured_engine() == ENGINE_GO and engine_available()


def engine_can_combine(raw_files: list[tuple[str, bytes]]) -> bool:
    """Every upload is a CSV/TSV or a real (zip) .xlsx/.xlsm, and the engine is on."""
    for filename, data in raw_files:
        ext = _ext(filename)
        if ext not in {".csv", ".tsv", ".xlsx", ".xlsm"}:
            return False
        if ext in {".xlsx", ".xlsm"} and not data.startswith(_ZIP):
            return False
    return _engine_ready()


def write_frame(df: pd.DataFrame, path: Path) -> None:
    """A frame for the engine: JSON lines, the column names then each row."""
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps([str(c) for c in df.columns]) + "\n")
        for row in df.itertuples(index=False, name=None):
            f.write(json.dumps([None if v is None else str(v) for v in row]) + "\n")


def _engine_inputs(raw_files: list[tuple[str, bytes]], work: Path) -> list[dict]:
    files = []
    for i, (filename, data) in enumerate(raw_files):
        ext = _ext(filename)
        if ext in {".csv", ".tsv"}:
            try:
                df = read_delimited(filename, data)
            except Exception as exc:  # the engine reports it in upload order
                files.append({"path": "", "name": filename, "kind": "error", "error": str(exc)})
                continue
            p = work / f"in_{i:04d}.jsonl"
            write_frame(df, p)
            files.append({"path": str(p), "name": filename, "kind": "frame"})
        else:
            p = work / f"in_{i:04d}{ext}"
            p.write_bytes(data)
            files.append({"path": str(p), "name": filename, "kind": "workbook"})
    return files


async def combine_with_engine(raw_files: list[tuple[str, bytes]], migration_id: str) -> "tuple[bytes, list[str]] | None":
    """The combined workbook from `hoist-engine combine`. Raises CombineError where Python's
    combine fails, with its words; None when the engine is missing or crashed (combine in
    Python then)."""
    from .engine import store

    work = store.kind_dir(migration_id, "combine")
    work.mkdir(parents=True, exist_ok=True)
    try:
        files = await asyncio.to_thread(_engine_inputs, raw_files, work)
        out = work / "combined.xlsx"
        try:
            res = await run_engine("combine", {"files": files, "out_path": str(out)}, workdir=work,
                                   timeout_s=COMBINE_TIMEOUT_S,
                                   on_event=EngineProgress(migration_id, "combine").on_event)
        except EngineError as exc:
            if exc.code in ("parse_error", "data_error"):
                raise CombineError(exc.message) from exc
            logger.warning("[start-with-upload-multi] hoist-engine combine failed (%s: %s); combining in Python",
                           exc.code, exc.message)
            return None
        return out.read_bytes(), list(res.get("sheets") or [])
    finally:
        shutil.rmtree(work, ignore_errors=True)
