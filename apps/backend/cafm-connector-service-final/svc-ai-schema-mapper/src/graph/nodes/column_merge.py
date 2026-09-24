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

try:  # the mapper's own picture of the destination; absent only in stripped-down test rigs
    from ...matchers.plenum_cafm_schema import TABLES as _PLENUM_TABLES
except Exception:  # noqa: BLE001
    _PLENUM_TABLES = {}

#: Every column name that exists on some plenum_cafm table. A source column whose name is one of
#: these is a destination fact in its own right, and two such names are two facts — whatever the
#: values in one particular file happen to do.
#: Tables the generated catalogue does not carry: plenum_cafm.csv predates the energy graph, so
#: energy_meters, building_sections, meter_readings and floors are not in TABLES at all, and
#: none of their columns counted as a destination. A sub-meter sheet says is_sub_meter=true and
#: active=true on every row, and a section per floor is named as the floor — so is_sub_meter
#: was merged into active and floor_name into name, two facts read as one column. These are
#: the live columns of those tables (information_schema, hoistra_test, 24 Sep 2026).
PLATFORM_TABLES: dict[str, list[str]] = {
    "energy_meters": [
        "id", "organization_id", "building_id", "asset_id", "meter_type", "mpan", "mprn",
        "dcc_device_id", "tariff_gbp_per_kwh", "carbon_kg_per_kwh", "is_sub_meter",
        "asset_type_benchmark_kwh", "active", "raw_metadata", "section_id", "description",
        "site_ref", "site_id", "building_code", "meter_ref",
    ],
    "building_sections": [
        "section_id", "organization_id", "building_id", "floor_id", "name", "section_type",
        "gross_area_m2", "reference_eui_kwh_m2", "reference_source", "floor_name",
        "building_code",
    ],
    "meter_readings": [
        "id", "organization_id", "meter_id", "asset_id", "reading_at", "period_minutes",
        "consumption_kwh", "source", "quality_flag", "building_code", "meter_type", "meter_ref",
    ],
    "floors": ["floor_id", "building_id", "level", "name", "gross_area_sqft"],
}

KNOWN_DESTINATION_COLUMNS: frozenset[str] = frozenset(
    str(c).lower()
    for cols in list(_PLENUM_TABLES.values()) + list(PLATFORM_TABLES.values())
    for c in (cols or [])
)


def _distinct_facts(group: list[str]) -> list[str]:
    """The members of a duplicate group that are real destination columns."""
    return [c for c in group if str(c).strip().lower() in KNOWN_DESTINATION_COLUMNS]

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
            facts = _distinct_facts(group)
            if len(facts) >= 2:
                # Identical values, but two or more of these names are real columns on the
                # destination. expiry_date and next_due_date coincide on most certificates;
                # they are still the date a certificate lapses and the date the next
                # inspection is owed, and dropping either loses a fact the page reads.
                logger.info(
                    "[Node 1] Duplicate values in %s: %s — identical in all %d row(s) but %s "
                    "are distinct destination columns; kept all of them",
                    table_name, " == ".join(group), len(records), ", ".join(facts),
                )
                entries.append({
                    "kept": list(group), "dropped": [], "members": list(group),
                    "match_pct": 100, "row_count": len(records),
                    "declined": "distinct destination columns",
                })
                continue
            # One member is a real destination column and the rest are spellings of it: the
            # destination name survives, because it is the one the mapper matches exactly and
            # the others would each cost a mapping step to arrive at the same place. Only when
            # none of them is a known column does the longest-name rule decide.
            kept = facts[0] if len(facts) == 1 else _representative(group, order)
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
