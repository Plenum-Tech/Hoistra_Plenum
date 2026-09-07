"""First data-quality step — scan every column of every parsed table for null/NaN cells.

This runs as the FIRST sub-step of Node 1 (ingest), immediately after the source
file has been parsed into ``full_tables``. It does not mutate any data — the actual
null filling happens later in Node 5 (preprocess). Its job is purely to REPORT:

  1. The null/NaN-processing outcome is surfaced in the Activity Log (via the node's
     log lines + ``output.nan_report``) — see ``summarize_nan_report``.
  2. The rows that carry null/NaN values (and are cleaned during preprocessing) are
     surfaced in the central Query Space (via the Node-1 step-pause payload's
     ``nan_report.tables[*].sample_rows``).

A missing cell reaches this scan as ``None`` (a sanitized NaN/NaT/null, plus the
default NA tokens pandas maps to NaN on read), a raw float ``nan``, or an empty /
whitespace-only string (blank Excel cells via calamine). ``_is_null_or_nan``
covers all three.
"""

from __future__ import annotations

from typing import Any

# Cap the affected-row sample we carry into the step payload so a table with
# hundreds of thousands of NaN rows can't bloat the checkpoint / status response.
DEFAULT_ROW_SAMPLE_LIMIT = 20
# Cap the number of tables we emit per-table detail for in the activity-log summary.
_SUMMARY_TABLE_LIMIT = 8
_SUMMARY_COLS_PER_TABLE = 6


def _is_null_or_nan(value: Any) -> bool:
    """True if a cell is null / NaN / empty.

    Covers every way a missing value reaches this scan:
      * ``None``  — a sanitized NaN/NaT/null source cell (``pd.isna(v) -> None``),
        and the default NA tokens (``NULL``, ``N/A``, ``NaN`` …) that pandas maps
        to NaN on read even with ``dtype=str``.
      * float ``nan`` — a raw pandas NaN (a float not equal to itself), defensive
        in case an unsanitized frame is ever passed.
      * empty / whitespace-only string — a blank cell that the Excel (calamine)
        path returns as ``""`` rather than ``None``.
    """
    if value is None:
        return True
    if isinstance(value, float) and value != value:  # float('nan')
        return True
    if isinstance(value, str) and value.strip() == "":
        return True
    return False


def scan_nan_values(
    tables: dict[str, Any],
    *,
    row_sample_limit: int = DEFAULT_ROW_SAMPLE_LIMIT,
) -> dict[str, Any]:
    """Scan parsed table records for NaN cells and build a structured report.

    Args:
        tables: ``{table_name: [ {col: value, ...}, ... ]}`` as produced by the
            ingest node (``state['full_tables']``).
        row_sample_limit: max affected rows to retain per table for display.

    Returns:
        ``nan_report`` dict::

            {
              "total_nan_cells": int,
              "total_rows_with_nan": int,
              "total_rows": int,
              "columns_with_nan": int,       # distinct (table, column) pairs
              "tables": {
                 table_name: {
                    "row_count": int,
                    "rows_with_nan": int,
                    "nan_cells": int,
                    "columns": { col: nan_count, ... },   # only columns WITH nan
                    "sample_rows": [
                       {"row_index": int, "nan_columns": [col, ...], "values": {col: val, ...}},
                       ...
                    ],
                 }, ...
              },
            }
    """
    report_tables: dict[str, Any] = {}
    total_nan_cells = 0
    total_rows_with_nan = 0
    total_rows = 0
    columns_with_nan = 0

    for table_name, records in (tables or {}).items():
        if not isinstance(records, list):
            continue

        col_nan_counts: dict[str, int] = {}
        rows_with_nan = 0
        nan_cells = 0
        sample_rows: list[dict[str, Any]] = []

        for row_index, row in enumerate(records):
            if not isinstance(row, dict):
                continue
            nan_cols_in_row = [col for col, val in row.items() if _is_null_or_nan(val)]
            if not nan_cols_in_row:
                continue
            rows_with_nan += 1
            nan_cells += len(nan_cols_in_row)
            for col in nan_cols_in_row:
                col_nan_counts[col] = col_nan_counts.get(col, 0) + 1
            if len(sample_rows) < row_sample_limit:
                sample_rows.append(
                    {
                        "row_index": row_index,  # 0-based index into the source table
                        "nan_columns": nan_cols_in_row,
                        # Full row so the Query Space can show it in context. NaN cells
                        # stay None; the UI renders them as "—" and labels them cleaned.
                        "values": dict(row),
                    }
                )

        total_rows += len(records)
        if nan_cells == 0:
            # Still record the table (row_count) so the UI can say "0 NaN" per table.
            report_tables[table_name] = {
                "row_count": len(records),
                "rows_with_nan": 0,
                "nan_cells": 0,
                "columns": {},
                "sample_rows": [],
            }
            continue

        total_nan_cells += nan_cells
        total_rows_with_nan += rows_with_nan
        columns_with_nan += len(col_nan_counts)
        report_tables[table_name] = {
            "row_count": len(records),
            "rows_with_nan": rows_with_nan,
            "nan_cells": nan_cells,
            # Sort columns by NaN count desc so the worst offenders lead.
            "columns": dict(
                sorted(col_nan_counts.items(), key=lambda kv: kv[1], reverse=True)
            ),
            "sample_rows": sample_rows,
        }

    return {
        "total_nan_cells": total_nan_cells,
        "total_rows_with_nan": total_rows_with_nan,
        "total_rows": total_rows,
        "columns_with_nan": columns_with_nan,
        "tables": report_tables,
    }


def summarize_nan_report(report: dict[str, Any]) -> list[str]:
    """One-or-more activity-log lines describing the null/NaN-processing outcome."""
    if not isinstance(report, dict):
        return ["Null/NaN scan: no report available"]

    total_cells = int(report.get("total_nan_cells", 0) or 0)
    total_rows_with_nan = int(report.get("total_rows_with_nan", 0) or 0)
    cols_with_nan = int(report.get("columns_with_nan", 0) or 0)
    tables = report.get("tables", {}) or {}

    if total_cells == 0:
        return [
            f"Null/NaN scan: no null/NaN values found across {len(tables)} table(s)",
        ]

    lines = [
        f"Null/NaN scan: found {total_cells:,} null/NaN value(s) in {cols_with_nan} column(s) "
        f"across {len(tables)} table(s); {total_rows_with_nan:,} row(s) affected "
        f"(cleaned during preprocessing)"
    ]
    shown = 0
    for table_name, t in tables.items():
        if not isinstance(t, dict) or int(t.get("nan_cells", 0) or 0) == 0:
            continue
        if shown >= _SUMMARY_TABLE_LIMIT:
            lines.append("  … (more tables with NaN not shown)")
            break
        cols = t.get("columns", {}) or {}
        col_bits = ", ".join(
            f"{c} ({n})" for c, n in list(cols.items())[:_SUMMARY_COLS_PER_TABLE]
        )
        if len(cols) > _SUMMARY_COLS_PER_TABLE:
            col_bits += f", +{len(cols) - _SUMMARY_COLS_PER_TABLE} more"
        lines.append(
            f"  {table_name}: {int(t.get('rows_with_nan', 0)):,} row(s) — {col_bits}"
        )
        shown += 1
    return lines
