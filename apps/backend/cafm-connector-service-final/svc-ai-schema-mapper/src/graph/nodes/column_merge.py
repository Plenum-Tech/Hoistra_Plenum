"""Second data-quality step — merge duplicate columns inside a table.

Runs in Node 1 immediately after the null/NaN scan, on ``state['full_tables']``. A source
sheet often records the same value under two headers (``works.tagnum`` and ``works.id``
both holding ``A-001…A-010``). Left alone these travel through the whole pipeline as two
independent columns: they group against each other, compete for a canonical name, and land
as two identical destination columns.

Merging here — before table mapping — means every later step (unique-table identification,
PK/FK classification, canonical naming, destination mapping, the write) sees ONE column.

The check is strict identity, not similarity: every row must agree (case- and
whitespace-insensitively) with at least ``_MIN_REDUNDANT_ROWS`` rows of real evidence.
Columns that merely look alike are left for the B17.1/B19.1 grouping stage to reason about.
"""

from __future__ import annotations

import logging
from typing import Any

from ...udr.primitives import redundant_column_groups

logger = logging.getLogger(__name__)


def _ordered_columns(records: list[dict]) -> list[str]:
    """Column names in source order — first appearance wins, unioned across rows."""
    cols: dict[str, None] = {}
    for rec in records:
        if isinstance(rec, dict):
            for key in rec:
                cols.setdefault(key, None)
    return list(cols)


def _representative(group: list[str], order: dict[str, int]) -> str:
    """Pick the surviving name: the longest (most specific) one.

    ``tagnum`` beats ``id`` — a descriptive domain name carries more meaning than a generic
    key. Ties break on source order so the result is deterministic.
    """
    return min(group, key=lambda c: (-len(c), order.get(c, 0)))


def merge_duplicate_columns(
    tables: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Collapse row-for-row identical columns within each table.

    Args:
        tables: ``{table_name: [ {col: value, ...}, ... ]}`` (``state['full_tables']``).

    Returns:
        ``(merged_tables, report)``. ``merged_tables`` is a new dict with the redundant keys
        removed from every row; tables with nothing to merge are passed through by reference.
        ``report`` is::

            {
              "total_merges": int,          # number of merge groups
              "total_columns_dropped": int,
              "tables": {
                 table_name: [
                   {"kept": str, "dropped": [str, ...], "members": [str, ...],
                    "match_pct": 100, "row_count": int},
                   ...
                 ], ...
              },
            }
    """
    merged: dict[str, Any] = {}
    report_tables: dict[str, list[dict]] = {}
    total_merges = 0
    total_dropped = 0

    for table_name, records in (tables or {}).items():
        if not isinstance(records, list) or not records:
            merged[table_name] = records
            continue

        cols = _ordered_columns(records)
        groups = redundant_column_groups(records, cols)
        if not groups:
            merged[table_name] = records
            continue

        order = {c: i for i, c in enumerate(cols)}
        drop: set[str] = set()
        entries: list[dict] = []
        for group in groups:
            kept = _representative(group, order)
            dropped = [c for c in group if c != kept]
            drop.update(dropped)
            entries.append({
                "kept": kept,
                "dropped": dropped,
                "members": list(group),
                "match_pct": 100,
                "row_count": len(records),
            })
            logger.info(
                "[Node 1] Duplicate columns in %s: %s — values identical in all %d row(s); "
                "keeping '%s', dropping %s",
                table_name, " == ".join(group), len(records), kept, ", ".join(dropped),
            )

        merged[table_name] = [
            {k: v for k, v in rec.items() if k not in drop} if isinstance(rec, dict) else rec
            for rec in records
        ]
        report_tables[table_name] = entries
        total_merges += len(entries)
        total_dropped += len(drop)

    return merged, {
        "total_merges": total_merges,
        "total_columns_dropped": total_dropped,
        "tables": report_tables,
    }


def apply_merge_decisions(tables: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
    """Replay an existing merge report onto another view of the same data.

    The sampled ``parsed_tables`` must lose exactly the columns the full-data merge dropped.
    Re-detecting on the sample could disagree with the full pass (fewer rows means weaker
    evidence, and a different representative), so the decisions are replayed rather than
    recomputed.
    """
    drops = {
        table_name: {c for entry in entries for c in entry["dropped"]}
        for table_name, entries in (report.get("tables") or {}).items()
    }
    if not drops:
        return tables
    out: dict[str, Any] = {}
    for table_name, records in (tables or {}).items():
        drop = drops.get(table_name)
        if not drop or not isinstance(records, list):
            out[table_name] = records
            continue
        out[table_name] = [
            {k: v for k, v in rec.items() if k not in drop} if isinstance(rec, dict) else rec
            for rec in records
        ]
    return out


def summarize_duplicate_column_report(report: dict[str, Any]) -> list[str]:
    """Human-readable node-log lines for the Activity Log."""
    if not report or not report.get("total_merges"):
        return ["Duplicate-column merge: no identical columns found"]
    lines = [
        f"Duplicate-column merge: {report['total_merges']} merge(s), "
        f"{report['total_columns_dropped']} redundant column(s) removed"
    ]
    for table_name, entries in (report.get("tables") or {}).items():
        for entry in entries:
            lines.append(
                # ASCII only — these lines go to the Windows console logger (cp1252).
                f"  {table_name}: {' == '.join(entry['members'])} -> kept '{entry['kept']}' "
                f"(100% identical across {entry['row_count']} rows)"
            )
    return lines
