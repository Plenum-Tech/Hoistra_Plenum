"""Node 5: Preprocess and validate — data cleaning before hierarchy detection.

Steps:
1. Dedup — drop exact-duplicate rows
2. Drop 100%-null columns — columns with zero non-null values are dropped;
   partially-null columns are kept and their nulls are filled in Step 3
3. Null handling — numeric→0, text→"", dates left as-is (NaT)
4. Date coercion — normalize to ISO 8601 (5 common formats)
5. JSON Schema validation (warnings only, no blocking)
6. FK pre-check within dataset

EL-M.5: row_count_post_dedup ≥ 80% of original
"""

import logging
import re
from datetime import datetime
from typing import Any, Optional

import pandas as pd

from ..state import MigrationState
from .ingest_node import _sanitize_records

from cafm_shared.logging import get_logger
logger = get_logger(__name__)

# Common date formats to try
DATE_FORMATS = [
    "%Y-%m-%d",  # 2025-12-31
    "%d/%m/%Y",  # 31/12/2025
    "%m/%d/%Y",  # 12/31/2025
    "%Y/%m/%d",  # 2025/12/31
    "%d-%m-%Y",  # 31-12-2025
]


async def preprocess_node(state: MigrationState) -> MigrationState:
    """
    Node 5: Clean and validate data before hierarchy detection — MULTI-TABLE.

    Each source table is cleaned independently, metrics tracked per table.

    Args:
        state: MigrationState with parsed_tables, tier1_mappings_by_table, tier2_human_decisions_by_table

    Returns:
        Updated state with cleaned_tables, row_count_post_dedup_by_table, dedup_drop_count_by_table
    """

    _node_started_at = datetime.utcnow()
    migration_id = state.get("migration_id")
    # Use full tables if available (from Node 1), otherwise fall back to parsed_tables (5-row sample)
    parsed_tables = state.get("full_tables") or state.get("parsed_tables", {})
    tier1_mappings_by_table = state.get("tier1_mappings_by_table", {})
    tier2_auto_by_table = state.get("tier2_auto_by_table", {})
    tier2_human_by_table = state.get("tier2_human_decisions_by_table", {})

    using_full_file = "full_tables" in state
    logger.info(f"[Node 5] Starting preprocessing (MULTI-TABLE): migration_id={migration_id}, using_full_file={using_full_file}")

    if not parsed_tables:
        logger.error("[Node 5] No parsed tables found")
        state["error_message"] = "No tables to preprocess"
        state["error_node"] = 5
        return state

    try:
        # ── Type-aware guard: never rename a text / categorical / date source column onto a
        # NUMERIC (or boolean / temporal) destination column (frequency='Quarterly' →
        # frequency_value INTEGER), which the DB rejects and drops every row. This is THE rename
        # point that feeds the DB write, so correcting target_field here fixes the write, the
        # CREATE-TABLE DDL and the field-mapping log in one place. On a genuine mismatch retarget
        # to a type-compatible, name-similar column on the same table (frequency → frequency_type)
        # or, if none exists, demote to a NEW column so the data is preserved. Best-effort. ──────
        try:
            from ...db import get_plenum_cafm_column_types_by_table
            from ...matchers.type_compat import choose_compatible_target

            _tg_types = await get_plenum_cafm_column_types_by_table()
            if _tg_types:
                _tg_routing = state.get("table_routing") or {}

                def _tg_samples(src_table: str, src_field: str, cap: int = 40) -> list:
                    out: list = []
                    for _r in (parsed_tables.get(src_table) or [])[:300]:
                        if isinstance(_r, dict) and src_field in _r:
                            out.append(_r.get(src_field))
                            if len(out) >= cap:
                                break
                    return out

                def _tg_snake(s: str) -> str:
                    import re as _re
                    return _re.sub(r"[^a-z0-9]+", "_", str(s or "").strip().lower()).strip("_") or "column"

                # Targets already claimed per DESTINATION table (a dest can receive several source
                # tables, e.g. work_order + workorders → work_orders), so a retarget never collides
                # with a column another mapping already lands in.
                _tg_used_by_dest: dict[str, set] = {}
                for _bucket in (tier1_mappings_by_table, tier2_auto_by_table, tier2_human_by_table):
                    for _tbl, _maps in (_bucket or {}).items():
                        _dst = str(_tg_routing.get(_tbl) or _tbl).lower()
                        for _m in _maps or []:
                            if isinstance(_m, dict) and _m.get("target_field"):
                                _tg_used_by_dest.setdefault(_dst, set()).add(str(_m["target_field"]).lower())

                _tg_fixed = 0
                for _bucket in (tier1_mappings_by_table, tier2_auto_by_table, tier2_human_by_table):
                    for _tbl, _maps in (_bucket or {}).items():
                        _dst = str(_tg_routing.get(_tbl) or _tbl).lower()
                        _dtypes = _tg_types.get(_dst)
                        if not _dtypes:
                            continue
                        for _m in _maps or []:
                            if not isinstance(_m, dict):
                                continue
                            _tf, _sf = _m.get("target_field"), _m.get("source_field")
                            if not _tf or not _sf:
                                continue
                            # Exclude every claimed target on this dest EXCEPT this mapping's own.
                            _excl = set(_tg_used_by_dest.get(_dst, set()))
                            _excl.discard(str(_tf).lower())
                            _new_t, _action = choose_compatible_target(
                                _sf, _tf, _tg_samples(_tbl, _sf), _dtypes, exclude=_excl
                            )
                            if _action == "keep":
                                continue
                            if _action == "new":
                                _new_t = _tg_snake(_sf)
                            if _new_t and _new_t != _tf:
                                _m["target_field"] = _new_t
                                _m["type_guard_retarget"] = {"from": _tf, "to": _new_t, "reason": _action}
                                # Keep the claimed-targets index in sync so the next retarget on this
                                # dest sees the new column as taken.
                                _claimed = _tg_used_by_dest.setdefault(_dst, set())
                                _claimed.discard(str(_tf).lower())
                                _claimed.add(str(_new_t).lower())
                                _tg_fixed += 1
                                logger.info(
                                    f"[Node 5] type guard: {_tbl}.{_sf} '{_tf}' → '{_new_t}' "
                                    f"({_action}) — source values don't fit the destination column type"
                                )
                if _tg_fixed:
                    logger.info(
                        f"[Node 5] Type guard retargeted {_tg_fixed} column(s) away from "
                        "incompatible destination types"
                    )
        except Exception as _tg_exc:  # pragma: no cover — additive, never fatal
            logger.warning(f"[Node 5] type-compatibility guard skipped: {_tg_exc}")

        # Build per-table mapping dicts (source_field → target_field) for column rename
        # Keyed by table_name so columns are only renamed in their own table.
        mapping_dict_by_table: dict[str, dict[str, str]] = {}
        for table_name, mappings in tier1_mappings_by_table.items():
            for m in mappings:
                src, tgt = m.get("source_field"), m.get("target_field")
                if src and tgt:
                    mapping_dict_by_table.setdefault(table_name, {})[src] = tgt
        for table_name, mappings in tier2_auto_by_table.items():
            for m in mappings:
                src, tgt = m.get("source_field"), m.get("target_field")
                if src and tgt:
                    mapping_dict_by_table.setdefault(table_name, {})[src] = tgt
        for table_name, mappings in tier2_human_by_table.items():
            for m in mappings:
                src, tgt = m.get("source_field"), m.get("target_field")
                if src and tgt:
                    mapping_dict_by_table.setdefault(table_name, {})[src] = tgt

        total_mapping_fields = sum(len(v) for v in mapping_dict_by_table.values())
        logger.info(f"[Node 5] Per-table mapping dicts: {len(mapping_dict_by_table)} tables, {total_mapping_fields} total fields")

        # Build per-table skip sets from extra_fields_config (strategy == "skip")
        extra_fields_config = state.get("extra_fields_config", [])
        skip_fields_by_table: dict[str, set] = {}
        for entry in extra_fields_config:
            if entry.get("storage_strategy") == "skip":
                src_table = entry.get("source_table", "")
                src_field = entry.get("source_field", "")
                if src_table and src_field:
                    skip_fields_by_table.setdefault(src_table, set()).add(src_field)

        if skip_fields_by_table:
            total_skips = sum(len(v) for v in skip_fields_by_table.values())
            logger.info(f"[Node 5] Skip fields: {total_skips} fields across {len(skip_fields_by_table)} tables")

        # Also rename unmapped fields the user turned into NEW COLUMNS to their chosen name
        # (extra_fields_config custom_column_name), e.g. vendors.id → asset_id. These are not in the
        # tier mappings, so without this the new-column field keeps its SOURCE name in the cleaned
        # data — the preview shows "id", and at write the asset_id column ends up empty while the
        # values stay under "id". Applies to new-table custom columns too.
        _custom_renames = 0
        for entry in extra_fields_config:
            if entry.get("storage_strategy") != "custom":
                continue
            _st = entry.get("source_table", "")
            _sf = entry.get("source_field", "")
            _cn = entry.get("custom_column_name", "")
            if _st and _sf and _cn and _sf != _cn:
                mapping_dict_by_table.setdefault(_st, {})[_sf] = _cn
                _custom_renames += 1
        if _custom_renames:
            logger.info(f"[Node 5] New-column custom renames added to rename map: {_custom_renames}")

        cleaned_tables = {}
        row_count_post_dedup_by_table = {}
        dedup_drop_count_by_table = {}
        warnings = []
        total_original_rows = 0
        total_cleaned_rows = 0

        # MULTI-TABLE: Process each source table independently
        for table_name, records in parsed_tables.items():
            if not records:
                continue

            df = pd.DataFrame(records)
            original_count = len(df)
            total_original_rows += original_count

            _tbl_started_at = datetime.utcnow()
            logger.info(f"[Node 5] ► Table '{table_name}': {original_count} rows, {len(df.columns)} columns")

            # ── Step 1: Dedup (exact-duplicate rows) ────────────────
            df_dedup = df.drop_duplicates()
            dedup_drop = len(df) - len(df_dedup)
            dedup_drop_count_by_table[table_name] = dedup_drop
            if dedup_drop > 0:
                logger.info(f"[Node 5]   Dedup: dropped {dedup_drop} duplicate rows")
                warnings.append(f"{table_name}: Dropped {dedup_drop} duplicate rows")

            # ── Step 2: Drop 100%-null columns ───────────────────────
            # A column with zero non-null values carries no mapping signal.
            # Partially-null columns are kept — nulls are filled in Step 3.
            null_only_cols = [col for col in df_dedup.columns if df_dedup[col].isna().all()]
            if null_only_cols:
                df_dedup = df_dedup.drop(columns=null_only_cols)
                logger.info(
                    f"[Node 5]   Dropped {len(null_only_cols)} fully-null columns "
                    f"from {table_name}: {null_only_cols}"
                )
                warnings.append(
                    f"{table_name}: Dropped {len(null_only_cols)} fully-null column(s): "
                    f"{null_only_cols}"
                )

            # ── Step 3: Null handling (partially-null columns kept) ──
            for col in df_dedup.columns:
                col_type = _infer_column_type(df_dedup[col])

                if col_type == "numeric":
                    # Numeric: null → 0
                    df_dedup[col] = df_dedup[col].fillna(0)
                elif col_type == "text":
                    # Text: null → ""
                    df_dedup[col] = df_dedup[col].fillna("")
                elif col_type == "date":
                    # Dates: left as-is (NaT represents null date)
                    pass

            # ── Step 4: Date coercion ────────────────────────────────
            date_columns = []
            for col in df_dedup.columns:
                # Check if column name suggests it's a date
                if any(date_hint in col.lower() for date_hint in ["date", "time", "created", "due", "completed"]):
                    if _coerce_dates(df_dedup, col):
                        date_columns.append(col)
                        logger.info(f"[Node 5] Coerced {col} to ISO 8601")

            if date_columns:
                warnings.append(f"{table_name}: Coerced {len(date_columns)} date columns to ISO 8601")

            # ── Step 5: JSON Schema validation (warnings only) ────────
            # For now, just validate that data is serializable
            for col in df_dedup.columns:
                try:
                    df_dedup[col].to_json()
                except Exception as e:
                    logger.warning(f"[Node 5] Column {col} may have serialization issues: {e}")
                    warnings.append(f"{table_name}.{col}: Potential serialization issue")

            # ── Step 6: FK pre-check ─────────────────────────────────
            # Scan for columns that look like FKs (values present in other tables)
            # This is a rough heuristic; formal FK detection happens in Node 6
            for col in df_dedup.columns:
                if any(fk_hint in col.lower() for fk_hint in ["code", "id", "num"]):
                    # This column might be a foreign key
                    logger.debug(f"[Node 5] Potential FK column detected: {col}")

            # ── Step 7: Column rename (source_field → target_field) ──────
            table_col_map = mapping_dict_by_table.get(table_name, {})
            if table_col_map:
                rename_map = {k: v for k, v in table_col_map.items() if k in df_dedup.columns}
                if rename_map:
                    df_dedup = df_dedup.rename(columns=rename_map)
                    renames_str = ", ".join(f"{k}→{v}" for k, v in list(rename_map.items())[:10])
                    suffix = f" (+{len(rename_map)-10} more)" if len(rename_map) > 10 else ""
                    logger.info(f"[Node 5]   Renamed {len(rename_map)} columns: {renames_str}{suffix}")
                unmapped = [k for k in table_col_map if k not in df_dedup.columns and k not in rename_map]
                if unmapped:
                    logger.info(f"[Node 5]   {len(unmapped)} mapped source fields not in data (already renamed or absent): {unmapped[:5]}")

            # ── Step 8: Drop "skip" fields (user-discarded unmapped fields)
            skip_fields = skip_fields_by_table.get(table_name, set())
            # skip_fields uses source column names; after rename, they stay as-is
            # (skip fields are unmapped, so they were never in rename_map)
            cols_to_drop = [c for c in skip_fields if c in df_dedup.columns]
            if cols_to_drop:
                df_dedup = df_dedup.drop(columns=cols_to_drop)
                logger.info(f"[Node 5]   Dropped {len(cols_to_drop)} skipped fields from {table_name}: {cols_to_drop}")

            cleaned_count = len(df_dedup)
            total_cleaned_rows += cleaned_count
            row_count_post_dedup_by_table[table_name] = cleaned_count

            # ── EL-M.5 Validation (per table) ────────────────────────
            dedup_ratio = cleaned_count / original_count if original_count > 0 else 1.0
            if dedup_ratio < 0.80:
                logger.warning(f"[Node 5]   Dedup ratio {dedup_ratio:.1%} < 0.80")
                warnings.append(
                    f"{table_name}: High duplication ({100 * (1 - dedup_ratio):.1f}% dropped)"
                )

            cleaned_tables[table_name] = _sanitize_records(df_dedup.to_dict(orient="records"))
            logger.info(f"[Node 5] ✓ Table {table_name}: {cleaned_count} rows after cleaning")

        # ── Overall EL-M.5 Validation ────────────────────────────────
        if total_original_rows > 0:
            overall_ratio = total_cleaned_rows / total_original_rows
            logger.info(f"[Node 5] Overall dedup ratio: {overall_ratio:.1%}")

            if overall_ratio < 0.80:
                logger.error(f"[Node 5] EL-M.5 FAILED: ratio {overall_ratio:.1%} < 0.80")
                state["error_message"] = f"Data loss during dedup: {overall_ratio:.1%} remaining"
                state["el_m5_passed"] = False
                return state

        state["el_m5_passed"] = True
        logger.info("[Node 5] EL-M.5 PASSED: dedup ratio ≥ 0.80")

        # ── Update state ───────────────────────────────────────────
        state["cleaned_tables"] = cleaned_tables

        # Keep full_tables' COLUMN keys in sync with the source→target rename applied to
        # cleaned_tables above. Node 8 (SQL artifact + IntermediateSchema) and Node 9
        # (DB write) PREFER full_tables, so if its keys stay as source names a mapping
        # like handover_date→completed_at leaves the values under a column that doesn't
        # exist on the target table — and the data is silently dropped at insert. We
        # rename the keys in place (NOT replace the dict with the deduplicated
        # cleaned_tables), so full_tables keeps its full row set for the "old + new"
        # export while the mapped data lands under the correct target columns.
        _raw_full = state.get("full_tables")
        if isinstance(_raw_full, dict):
            _renamed_full: dict = {}
            for _tbl, _records in _raw_full.items():
                _col_map = mapping_dict_by_table.get(_tbl, {})
                if _col_map and isinstance(_records, list):
                    _renamed_full[_tbl] = [
                        {_col_map.get(_k, _k): _v for _k, _v in _row.items()}
                        if isinstance(_row, dict)
                        else _row
                        for _row in _records
                    ]
                else:
                    _renamed_full[_tbl] = _records
            state["full_tables"] = _renamed_full

        state["row_count_post_dedup_by_table"] = row_count_post_dedup_by_table
        state["dedup_drop_count_by_table"] = dedup_drop_count_by_table
        state["data_quality_warnings"] = warnings

        logger.info(f"[Node 5] ═══════════════════════════════════════════")
        logger.info(f"[Node 5] Complete: {total_cleaned_rows} rows after dedup across all tables")
        logger.info(f"[Node 5] Data quality warnings: {len(warnings)}")

        state["current_step"] = 5
        state["event_log"].append(
            {
                "timestamp": datetime.utcnow().isoformat(),
                "event": "node_complete",
                "node": 5,
                "detail": f"Cleaned {total_cleaned_rows} rows, {len(warnings)} warnings",
            }
        )

        migration_id = state.get("migration_id")
        if migration_id:
            from .db_writer import update_node_progress, write_step_pause
            await update_node_progress(migration_id, "5_preprocess")

            # Build per-table preview: columns + first 3 rows
            table_previews: dict = {}
            for tbl, records in cleaned_tables.items():
                if not records:
                    table_previews[tbl] = {"columns": [], "rows": [], "total_rows": 0}
                    continue
                columns = list(records[0].keys())
                sample_rows = records[:3]
                table_previews[tbl] = {
                    "columns": columns,
                    "rows": [[str(r.get(c, "")) for c in columns] for r in sample_rows],
                    "total_rows": row_count_post_dedup_by_table.get(tbl, len(records)),
                }

            await write_step_pause(
                migration_id,
                "step_5_preprocess",
                {
                    "node": 5,
                    "label": "Preprocess & Validate",
                    "rows_cleaned": total_cleaned_rows,
                    "warnings": len(warnings),
                    "warning_messages": warnings,
                    "tables": list(cleaned_tables.keys()),
                    "table_previews": table_previews,
                },
            )
            from .schema_db_writer import migration_append_node_log_auto
            total_original = sum((state.get("row_count_post_dedup_by_table") or {}).get(t, 0) + (state.get("dedup_drop_count_by_table") or {}).get(t, 0) for t in cleaned_tables)
            await migration_append_node_log_auto(
                migration_id, 6, "Preprocess & Validate", _node_started_at, datetime.utcnow(),
                output={"total_original_rows": total_original,
                        "total_cleaned_rows": total_cleaned_rows,
                        "dedup_ratio": round(total_cleaned_rows / total_original, 3) if total_original else 1.0,
                        "table_count": len(cleaned_tables),
                        "warning_count": len(warnings)},
                logs=[f"Cleaned {total_cleaned_rows} rows across {len(cleaned_tables)} tables",
                      f"Dedup: {total_original - total_cleaned_rows} duplicate rows removed",
                      f"{len(warnings)} data quality warnings",
                      f"EL-M.5: {'PASSED' if state.get('el_m5_passed') else 'FAILED'}"],
            )

        return state

    except Exception as e:
        logger.exception(f"[Node 5] Unhandled exception: {e}")
        state["error_message"] = str(e)
        state["error_node"] = 5
        state["error_timestamp"] = datetime.utcnow()
        state["status"] = "failed"
        return state


def _infer_column_type(series: pd.Series) -> str:
    """
    Infer column type: numeric, date, text.

    Args:
        series: pandas Series

    Returns:
        Type string: 'numeric', 'date', or 'text'
    """

    # Remove nulls for analysis
    non_null = series.dropna()

    if len(non_null) == 0:
        return "text"  # Default for all-null columns

    # Try numeric
    try:
        pd.to_numeric(non_null)
        return "numeric"
    except (ValueError, TypeError):
        pass

    # Try date
    if _contains_dates(non_null):
        return "date"

    return "text"


def _contains_dates(series: pd.Series) -> bool:
    """Check if series contains date-like values."""

    sample = series.head(10).astype(str)

    date_pattern = r"^\d{1,4}[-/]\d{1,2}[-/]\d{1,4}$"

    date_matches = sum(1 for val in sample if re.match(date_pattern, val.strip()))

    return date_matches >= len(sample) * 0.5  # At least 50% match


def _coerce_dates(df: pd.DataFrame, col_name: str) -> bool:
    """
    Coerce a column to datetime (ISO 8601) WITHOUT destroying the data.

    Every parse attempt reads the ORIGINAL values and the column is only
    overwritten once a format actually parses them. The previous version
    reassigned ``df[col_name]`` inside the format loop, so the first
    non-matching format turned every value into ``NaT`` (``errors="coerce"``)
    and all later formats — plus the fallback — then parsed an all-``NaT``
    column. Any date not in the first ``%Y-%m-%d`` format (e.g. UAE-style
    ``DD/MM/YYYY``) was permanently nulled while the function still reported
    success, so it surfaced downstream as ``None`` (``handover_date`` bug).

    Args:
        df: pandas DataFrame (mutated in-place only on success)
        col_name: Column name

    Returns:
        True if the column was coerced, False if it was left untouched.
    """

    original = df[col_name]

    # Already datetime-typed (e.g. Excel cells read as datetime) — keep as-is;
    # _sanitize_records serialises Timestamps to ISO 8601 downstream.
    if pd.api.types.is_datetime64_any_dtype(original):
        return True

    non_null_count = int(original.notna().sum())
    if non_null_count == 0:
        return False  # nothing to coerce — never overwrite an all-null column

    best_parsed: Optional[pd.Series] = None
    best_count = 0

    try:
        # Deterministic pass — always parse from `original`, never from a prior
        # (possibly all-NaT) attempt. Commit the first format that parses every
        # non-null value cleanly.
        for fmt in DATE_FORMATS:
            try:
                parsed = pd.to_datetime(original, format=fmt, errors="coerce")
            except Exception:
                continue
            count = int(parsed.notna().sum())
            if count >= non_null_count:
                df[col_name] = parsed
                return True
            if count > best_count:
                best_parsed, best_count = parsed, count

        # Fallback — let pandas infer the format from the original values
        # (day-first to match the DD/MM/YYYY preference in DATE_FORMATS).
        try:
            parsed = pd.to_datetime(original, errors="coerce", dayfirst=True)
            count = int(parsed.notna().sum())
            if count > best_count:
                best_parsed, best_count = parsed, count
        except Exception:
            pass

        # Only commit a partial parse if it preserves the vast majority of the
        # values; otherwise leave the column untouched so a column the heuristic
        # mis-flagged as a date (e.g. "date_code") is never silently nulled.
        if best_parsed is not None and best_count >= non_null_count * 0.8:
            df[col_name] = best_parsed
            return True
        return False

    except Exception as e:
        logger.debug(f"Failed to coerce {col_name} to datetime: {e}")
        return False
