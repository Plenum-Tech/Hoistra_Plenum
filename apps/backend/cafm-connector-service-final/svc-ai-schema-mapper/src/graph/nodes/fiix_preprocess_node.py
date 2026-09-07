"""Node 2 — Fiix Data Ingestion: Preprocess fetched records.

Reuses the helper functions from preprocess_node.py (the migration pipeline's
Node 5) for dedup, null handling, and date coercion.

Additional steps specific to Fiix data:
      1. Field rename via context-aware Fiix→plenum mappings (native names preserved in raw_metadata)
  2. Deterministic UUID assignment for every record's "id" field
  3. FK field resolution: int*ID integer values → deterministic UUIDs
  4. Object-level dedup + null fill + date coerce (from preprocess_node helpers)
  5. Route preprocessed records by TARGET table (OBJECT_TABLE_MAP)

State consumed:  fetched_objects, fetch_stats
State produced:  preprocessed_tables, preprocess_stats, preprocess_warnings,
                 total_records_preprocessed
"""

from datetime import datetime
from typing import Any

import pandas as pd

from cafm_shared.logging import get_logger

from ..fiix_state import FiixIngestionState
from ...connectors.fiix_data_connector import (
    OBJECT_TABLE_MAP,
    FK_OBJECT_MAP,
    fiix_uuid,
)
from ...connectors.fiix_plenum_mappings import resolve_plenum_column
from .preprocess_node import _infer_column_type, _coerce_dates

logger = get_logger(__name__)


async def fiix_preprocess_node(state: FiixIngestionState) -> FiixIngestionState:
    """
    Node 2: Rename fields, assign UUIDs, resolve FK IDs, dedup, fill nulls.

    Each Fiix object is processed independently; the results are grouped by
    TARGET plenum_cafm table (not by Fiix object name) so that multiple Fiix
    objects that map to the same table are merged correctly.
    """
    ingestion_id = state.get("ingestion_id", "unknown")
    logger.info(f"[FiixPreprocess] Node 2 start — ingestion_id={ingestion_id}")

    state["status"] = "preprocessing"
    state["current_node"] = 2

    fetched_objects: dict[str, list[dict]] = state.get("fetched_objects", {})
    if not fetched_objects:
        logger.warning("[FiixPreprocess] No fetched objects — skipping")
        state["preprocessed_tables"] = {}
        state["preprocess_stats"] = {}
        state["preprocess_warnings"] = []
        state["total_records_preprocessed"] = 0
        return state

    # Accumulate records per target table (multiple objects can share a table)
    table_buckets: dict[str, list[dict]] = {}
    warnings: list[str] = []
    stats: dict[str, Any] = {}

    for obj_name, records in fetched_objects.items():
        if not records:
            continue

        target_table = OBJECT_TABLE_MAP.get(obj_name)
        if not target_table:
            logger.debug(f"[FiixPreprocess] No table mapping for {obj_name} — skipping")
            continue

        logger.info(f"[FiixPreprocess] Processing {obj_name} → {target_table}: {len(records)} records")

        processed = _process_object(obj_name, records, warnings)

        table_buckets.setdefault(target_table, []).extend(processed)
        stats[obj_name] = {
            "source_count": len(records),
            "output_count": len(processed),
            "target_table": target_table,
        }

    # Per-table dedup + null fill + date coerce using existing helpers
    preprocessed_tables: dict[str, list[dict]] = {}
    total = 0

    for table_name, records in table_buckets.items():
        cleaned = _table_clean(table_name, records, warnings)
        preprocessed_tables[table_name] = cleaned
        total += len(cleaned)
        logger.info(f"[FiixPreprocess]   ✓ {table_name}: {len(cleaned)} records after cleaning")

    state["preprocessed_tables"] = preprocessed_tables
    state["preprocess_stats"] = stats
    state["preprocess_warnings"] = warnings
    state["total_records_preprocessed"] = total
    state["notes"] = state.get("notes", []) + [
        f"Preprocessed {total} records across {len(preprocessed_tables)} tables, "
        f"{len(warnings)} warnings"
    ]

    logger.info(
        f"[FiixPreprocess] ✓ Complete — {total} records in "
        f"{len(preprocessed_tables)} tables, {len(warnings)} warnings"
    )

    await _write_progress(state, total, len(warnings))
    return state


# ── Per-object processing ─────────────────────────────────────────────────────

def _process_object(
    obj_name: str,
    records: list[dict],
    warnings: list[str],
) -> list[dict]:
    """
    For one Fiix object:
      1. Assign deterministic UUID as "id"
      2. Rename fields via resolve_plenum_column (per Fiix object)
      3. Resolve int*ID FK fields → deterministic UUIDs
      4. Tag source system metadata
    """
    processed = []

    for raw in records:
        fiix_id = raw.get("id")
        if fiix_id is None:
            warnings.append(f"{obj_name}: record missing 'id' field — skipped")
            continue

        row: dict[str, Any] = {}

        # 1. Assign deterministic UUID
        row["id"] = fiix_uuid(obj_name, fiix_id)

        # 2. Rename + resolve fields
        for field_name, value in raw.items():
            if field_name == "id":
                # Store Fiix integer ID separately for reference
                row["fiix_source_id"] = fiix_id
                continue

            # FK resolution: int*ID → deterministic UUID of referenced object
            if field_name in FK_OBJECT_MAP and value is not None:
                ref_obj = FK_OBJECT_MAP[field_name]
                fk_uuid = fiix_uuid(ref_obj, value)
                # Use the canonical renamed field name for the FK column
                plenum_col = resolve_plenum_column(obj_name, field_name) or field_name
                row[plenum_col] = fk_uuid
                # Keep the raw Fiix integer (by Fiix field AND by plenum column) so the
                # write node can use it when the target FK column is an integer type.
                row.setdefault("_raw_fk", {})[field_name] = value
                row.setdefault("_fk_raw_by_col", {})[plenum_col] = value
                continue

            # Regular field: rename to plenum column when mapped; else keep Fiix name
            plenum_col = resolve_plenum_column(obj_name, field_name)
            row[plenum_col or field_name] = value

        # 3. Tag with source system
        row["source_system"] = "fiix"

        processed.append(row)

    return processed


# ── Table-level cleaning (reuses migration preprocess helpers) ────────────────

def _table_clean(
    table_name: str,
    records: list[dict],
    warnings: list[str],
) -> list[dict]:
    """
    Apply dedup, null fill, and date coercion to a table's records.

    Uses pandas for dedup and type inference — same logic as preprocess_node.py
    Node 5 in the migration pipeline.
    """
    if not records:
        return []

    df = pd.DataFrame(records)
    original_count = len(df)

    # 1. Dedup on scalar columns only — skip any column containing dicts/lists
    #    (pandas cannot hash these for deduplication).
    #    Since IDs are already deterministic UUIDs from fiix_uuid(), dedup on
    #    scalar fields is just a safety net for truly identical rows.
    def _col_is_hashable(series: pd.Series) -> bool:
        return not series.dropna().apply(lambda v: isinstance(v, (dict, list))).any()

    dedup_cols = [c for c in df.columns if c != "id" and _col_is_hashable(df[c])]
    if dedup_cols:
        df = df.drop_duplicates(subset=dedup_cols)

    dedup_dropped = original_count - len(df)
    if dedup_dropped:
        warnings.append(f"{table_name}: dropped {dedup_dropped} duplicate rows")

    # 2. Drop fully-null columns (except 'id')
    null_only = [c for c in df.columns if c != "id" and df[c].isna().all()]
    if null_only:
        df = df.drop(columns=null_only)

    # 3. Null fill: numeric→0, text→""
    for col in df.columns:
        if col in ("id", "fiix_source_id", "source_system"):
            continue
        try:
            col_type = _infer_column_type(df[col])
            if col_type == "numeric":
                df[col] = df[col].fillna(0)
            elif col_type == "text":
                df[col] = df[col].fillna("")
        except Exception:
            pass

    # 4. Date coercion on columns with date-like names
    date_hints = ["date", "time", "created", "completed", "received", "submitted", "updated"]
    for col in df.columns:
        if any(h in col.lower() for h in date_hints):
            try:
                _coerce_dates(df, col)
            except Exception:
                pass

    # 5. Collect raw FK data into raw_metadata JSONB
    raw_fk_col = "_raw_fk"
    if raw_fk_col in df.columns:
        # Move _raw_fk dict into raw_metadata
        if "raw_metadata" in df.columns:
            df["raw_metadata"] = df.apply(
                lambda r: _merge_dicts(r.get("raw_metadata"), r.get(raw_fk_col)), axis=1
            )
        else:
            df["raw_metadata"] = df[raw_fk_col]
        df = df.drop(columns=[raw_fk_col])

    # Convert NaT → None so records are JSON-serialisable
    df = df.where(pd.notnull(df), None)

    return df.to_dict(orient="records")


def _merge_dicts(a: Any, b: Any) -> dict:
    """Merge two values (each may be dict or None) into one dict."""
    result = {}
    if isinstance(a, dict):
        result.update(a)
    if isinstance(b, dict):
        result.update(b)
    return result or None


# ── DB progress write ─────────────────────────────────────────────────────────

async def _write_progress(state: FiixIngestionState, total: int, warnings: int) -> None:
    db_session = state.get("db_session")
    ingestion_id = state.get("ingestion_id")
    if not db_session or not ingestion_id:
        return
    try:
        from sqlalchemy import update as sa_update
        from ...models.migration import FiixIngestionJob
        from uuid import UUID

        await db_session.execute(
            sa_update(FiixIngestionJob)
            .where(FiixIngestionJob.id == UUID(ingestion_id))
            .values(
                status="writing",
                current_step="2_preprocess_complete",
                total_records_preprocessed=total,
                progress_pct=66.0,
            )
        )
        await db_session.commit()
    except Exception as exc:
        logger.warning(f"[FiixPreprocess] DB progress write failed (non-fatal): {exc}")
