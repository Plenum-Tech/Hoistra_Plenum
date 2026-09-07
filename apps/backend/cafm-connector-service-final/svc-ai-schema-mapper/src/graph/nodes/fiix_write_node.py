"""Node 3 — Fiix Data Ingestion: Write preprocessed records to plenum_cafm tables.

Strategy:
  1. For each target table, introspect information_schema.columns to get
     the exact set of columns that exist.
  2. Split each record into:
       a. known_columns  — columns that exist in the DB table
       b. extra_fields   — everything else, packed into raw_metadata JSONB
  3. Batch-upsert using:
       INSERT INTO plenum_cafm.{table} (...) VALUES (...)
       ON CONFLICT (id) DO UPDATE SET ... = EXCLUDED. ...
  4. Tables that don't yet exist in the DB are silently skipped with a warning.

Batch size: 500 records per INSERT statement to balance throughput and memory.
"""

import json
from datetime import datetime, date
from typing import Any
from uuid import UUID

from cafm_shared.logging import get_logger
from sqlalchemy import text

from ..fiix_state import FiixIngestionState

logger = get_logger(__name__)

_BATCH_SIZE = 500
_FALLBACK_SCHEMA = "plenum_cafm"   # used only if state has no target_schema

# Columns we always skip — either auto-generated or belong to a different layer
_ALWAYS_SKIP = frozenset({"created_at", "updated_at"})

# Postgres integer types: canonical CAFM tables use integer/serial PKs and FKs,
# whereas preprocess assigns deterministic UUID strings. When the target column
# is integer we must write the *original Fiix integer* (preserved on the record
# as fiix_source_id / _fk_raw_by_col) instead of the UUID — otherwise asyncpg
# raises "'str' object cannot be interpreted as an integer" and every row fails.
_INT_TYPES = frozenset({"integer", "bigint", "smallint"})
_UUID_TYPES = frozenset({"uuid"})
# Fiix returns boolean fields (bolIsSite, bolIsOnline, ...) as integer 0/1, but the
# canonical columns are `boolean` — asyncpg rejects ints for bool columns.
_BOOL_TYPES = frozenset({"boolean"})
_BOOL_TRUE = frozenset({"1", "true", "t", "yes", "y"})
_BOOL_FALSE = frozenset({"0", "false", "f", "no", "n", ""})
# Pandas often widens integer-ish Fiix code columns (barcodes, etc.) to float when
# the column has NaNs; a varchar target then rejects the float. Coerce by target type.
_TEXT_TYPES = frozenset({"character varying", "text", "character", "citext"})
_NUMERIC_TYPES = frozenset({"numeric", "double precision", "real", "decimal"})
# Date/time columns need real datetime objects — asyncpg rejects ISO strings.
_TS_TYPES = frozenset({
    "timestamp without time zone", "timestamp with time zone",
    "date", "time without time zone", "time with time zone",
})


def _to_datetime(value: Any) -> Any:
    """Coerce a pandas Timestamp / ISO string / datetime into a python datetime.
    Returns None for nulls / NaT / unparseable values."""
    if value is None:
        return None
    # pandas.Timestamp / NaT expose to_pydatetime(); NaT != NaT.
    if hasattr(value, "to_pydatetime"):
        try:
            v = value.to_pydatetime()
        except Exception:
            return None
        return None if (v != v) else v
    if isinstance(value, (datetime, date)):
        return value
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            return datetime.fromisoformat(s)
        except Exception:
            try:
                from dateutil import parser as _dtp
                return _dtp.parse(s)
            except Exception:
                return None
    return None


def _looks_intlike(value: Any) -> bool:
    if isinstance(value, bool):
        return True
    if isinstance(value, int):
        return True
    if isinstance(value, str):
        s = value.strip()
        return s.lstrip("-").isdigit()
    return False


def _adapt_value_for_column(
    col: str,
    value: Any,
    col_type: str,
    record: dict[str, Any],
) -> tuple[Any, bool]:
    """
    Adapt a preprocessed value to the target column's Postgres type.

    Returns (adapted_value, fits) where fits=False means the value cannot be
    written to this column and should be diverted to raw_metadata instead.

    The important case: an *integer* target column that received a UUID string.
    Preprocess keeps the original Fiix integer, so we substitute it:
      - `id`        -> record["fiix_source_id"]   (the Fiix integer id)
      - FK columns  -> record["_fk_raw_by_col"][col]  (the raw int*ID value)
    UUID columns keep the UUID string; everything else passes through.
    """
    if col_type in _INT_TYPES:
        if col == "id":
            fi = record.get("fiix_source_id")
            if fi is not None and _looks_intlike(fi):
                return int(fi), True
        raw_by_col = record.get("_fk_raw_by_col") or {}
        if col in raw_by_col and raw_by_col[col] is not None and _looks_intlike(raw_by_col[col]):
            return int(raw_by_col[col]), True
        if value is None:
            return None, True
        if _looks_intlike(value) and not isinstance(value, bool):
            return int(value), True
        # A UUID/text value can't go into an integer column — divert it.
        return None, False
    if col_type in _TS_TYPES:
        dt = _to_datetime(value)
        if col_type == "date" and isinstance(dt, datetime):
            return dt.date(), True
        return dt, True
    if col_type in _BOOL_TYPES:
        if value is None or isinstance(value, bool):
            return value, True
        if isinstance(value, (int, float)):
            return bool(value), True
        if isinstance(value, str):
            v = value.strip().lower()
            if v in _BOOL_TRUE:
                return True, True
            if v in _BOOL_FALSE:
                return False, True
            return None, True
        return bool(value), True
    if col_type in _TEXT_TYPES:
        if value is None or isinstance(value, str):
            return value, True
        # 12690323.0 -> "12690323" (don't carry the spurious .0 from float widening)
        if isinstance(value, float) and value.is_integer():
            return str(int(value)), True
        return str(value), True
    if col_type in _NUMERIC_TYPES:
        if value is None or (isinstance(value, (int, float)) and not isinstance(value, bool)):
            return value, True
        if isinstance(value, str):
            s = value.strip().replace(",", "")
            try:
                return float(s), True
            except ValueError:
                return None, True
        return value, True
    return value, True


async def fiix_write_node(state: FiixIngestionState) -> FiixIngestionState:
    """
    Node 3: Bulk-upsert all preprocessed Fiix records into plenum_cafm tables.

    Uses the AsyncSession from state["db_session"] which is injected by the
    worker before the graph runs.
    """
    ingestion_id = state.get("ingestion_id", "unknown")
    logger.info(f"[FiixWrite] Node 3 start — ingestion_id={ingestion_id}")

    state["status"] = "writing"
    state["current_node"] = 3

    # Resolve the target schema — must come from the schema mapper output
    target_schema: str = state.get("target_schema") or _FALLBACK_SCHEMA
    if not state.get("target_schema"):
        logger.warning(
            f"[FiixWrite] No target_schema in state — falling back to '{_FALLBACK_SCHEMA}'. "
            "Pass schema_mapping_id when starting ingestion to write to the correct schema."
        )
    logger.info(f"[FiixWrite] Writing to schema: {target_schema}")

    db_session = state.get("db_session")
    if not db_session:
        state["error_message"] = "No db_session in state — cannot write"
        state["error_node"] = 3
        state["status"] = "failed"
        return state

    preprocessed_tables: dict[str, list[dict]] = state.get("preprocessed_tables", {})
    if not preprocessed_tables:
        logger.warning("[FiixWrite] No preprocessed tables — nothing to write")
        state["write_results"] = {}
        state["total_records_written"] = 0
        state["write_errors"] = []
        state["status"] = "complete"
        state["completed_at"] = datetime.utcnow()
        return state

    # ── Introspect available tables in the target schema ─────────────────────
    existing_tables = await _get_existing_tables(db_session, target_schema)
    logger.info(f"[FiixWrite] Found {len(existing_tables)} tables in {target_schema} schema")

    write_results: dict[str, dict[str, int]] = {}
    write_errors: list[str] = []
    total_written = 0

    for table_name, records in preprocessed_tables.items():
        if not records:
            continue

        if table_name not in existing_tables:
            msg = f"Table {target_schema}.{table_name} not found in DB — skipping"
            logger.warning(f"[FiixWrite] {msg}")
            write_errors.append(msg)
            continue

        table_columns = existing_tables[table_name]
        logger.info(
            f"[FiixWrite] Writing {len(records)} records → {target_schema}.{table_name} "
            f"({len(table_columns)} columns)"
        )

        inserted, skipped, errors = await _upsert_table(
            db_session, target_schema, table_name, table_columns, records
        )

        write_results[table_name] = {
            "inserted": inserted,
            "skipped": skipped,
            "errors": errors,
        }
        total_written += inserted
        if errors:
            write_errors.append(f"{table_name}: {errors} row error(s)")

        logger.info(
            f"[FiixWrite]   ✓ {table_name}: {inserted} inserted, "
            f"{skipped} skipped, {errors} errors"
        )

    state["write_results"] = write_results
    state["total_records_written"] = total_written
    state["write_errors"] = write_errors
    state["status"] = "complete"
    state["completed_at"] = datetime.utcnow()
    state["notes"] = state.get("notes", []) + [
        f"Wrote {total_written} records across {len(write_results)} tables"
    ]

    logger.info(
        f"[FiixWrite] ✓ Complete — {total_written} total records written, "
        f"{len(write_errors)} table-level errors"
    )

    await _write_final_status(state, total_written)
    return state


# ── Table introspection ───────────────────────────────────────────────────────

async def _get_existing_tables(db_session, schema: str) -> dict[str, dict[str, str]]:
    """
    Return {table_name: {column_name: data_type}} for every table in the schema.
    The data_type is needed so the writer can match integer columns to Fiix
    integer ids and UUID columns to deterministic UUIDs (see _adapt_value_for_column).
    Uses information_schema so we never guess at column existence.
    """
    result = await db_session.execute(
        text(
            """
            SELECT table_name, column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = :schema
            ORDER BY table_name, ordinal_position
            """
        ),
        {"schema": schema},
    )
    tables: dict[str, dict[str, str]] = {}
    for row in result.fetchall():
        tables.setdefault(row[0], {})[row[1]] = row[2]
    return tables


# ── Batch upsert ──────────────────────────────────────────────────────────────

async def _upsert_table(
    db_session,
    schema: str,
    table_name: str,
    table_columns: dict[str, str],
    records: list[dict],
) -> tuple[int, int, int]:
    """
    Upsert records into a single plenum_cafm table in batches of _BATCH_SIZE.

    `table_columns` maps column_name -> postgres data_type (from information_schema).

    Returns (inserted, skipped, errors).
    INSERT ... ON CONFLICT (id) DO UPDATE — re-runs are safe and update stale data.
    """
    inserted = skipped = errors = 0

    # Determine which columns from the records actually exist in the DB table
    # (minus always-skip set)
    writable_columns: list[str] = [
        c for c in table_columns
        if c not in _ALWAYS_SKIP and c != "raw_metadata"
    ]
    column_types: dict[str, str] = {c: table_columns[c] for c in writable_columns}

    has_raw_metadata = "raw_metadata" in table_columns

    for batch_start in range(0, len(records), _BATCH_SIZE):
        batch = records[batch_start : batch_start + _BATCH_SIZE]

        for record in batch:
            try:
                row_data, extra = _split_record(record, writable_columns, column_types)

                # Pack leftover fields into raw_metadata JSONB if the column exists
                if has_raw_metadata:
                    existing_meta = record.get("raw_metadata") or {}
                    if isinstance(existing_meta, str):
                        try:
                            existing_meta = json.loads(existing_meta)
                        except Exception:
                            existing_meta = {}
                    merged_meta = {**existing_meta, **extra}
                    # default=str so pandas Timestamps / datetimes / UUIDs in the
                    # leftover Fiix fields don't blow up json.dumps.
                    row_data["raw_metadata"] = (
                        json.dumps(merged_meta, default=str) if merged_meta else None
                    )

                if not row_data.get("id"):
                    skipped += 1
                    continue

                cols = list(row_data.keys())
                values = [_coerce(v) for v in row_data.values()]

                placeholders = ", ".join(f":{c}" for c in cols)
                col_list = ", ".join(f'"{c}"' for c in cols)
                update_cols = [c for c in cols if c != "id"]
                update_set = ", ".join(f'"{c}" = EXCLUDED."{c}"' for c in update_cols)

                conflict_clause = (
                    f"DO UPDATE SET {update_set}" if update_set else "DO NOTHING"
                )
                sql = text(
                    f'INSERT INTO {schema}."{table_name}" ({col_list}) '
                    f"VALUES ({placeholders}) "
                    f"ON CONFLICT (id) {conflict_clause}"
                )

                # SAVEPOINT per row: a single bad row (NOT NULL / FK / unique
                # violation) rolls back only itself instead of aborting the whole
                # batch transaction and poisoning every subsequent row.
                async with db_session.begin_nested():
                    await db_session.execute(sql, dict(zip(cols, values)))
                inserted += 1

            except Exception as exc:
                logger.warning(
                    f"[FiixWrite] Row error in {table_name}: {exc} — "
                    f"record id={record.get('id', '?')}"
                )
                errors += 1

        # Commit each batch
        try:
            await db_session.commit()
        except Exception as exc:
            logger.error(f"[FiixWrite] Batch commit failed for {table_name}: {exc}")
            await db_session.rollback()
            errors += len(batch)
            inserted -= len(batch)

    return inserted, skipped, errors


# ── Helpers ───────────────────────────────────────────────────────────────────

def _split_record(
    record: dict[str, Any],
    writable_columns: list[str],
    column_types: dict[str, str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """
    Split a record into (known_columns_dict, extra_fields_dict).

    known_columns_dict  → written to table columns (type-adapted to the column)
    extra_fields_dict   → packed into raw_metadata JSONB

    `column_types` maps writable column -> postgres data_type so integer columns
    receive the original Fiix integer instead of a UUID string.
    """
    column_types = column_types or {}
    known: dict[str, Any] = {}
    extra: dict[str, Any] = {}
    # Internal preprocess bookkeeping keys never written as columns directly.
    _internal = ("raw_metadata", "source_system", "fiix_source_id", "_raw_fk", "_fk_raw_by_col")

    for k, v in record.items():
        if k in _internal:
            continue  # handled separately / not a column
        if k in writable_columns:
            adapted, fits = _adapt_value_for_column(k, v, column_types.get(k, ""), record)
            if fits:
                known[k] = adapted
            else:
                extra[k] = v  # divert mismatched value to raw_metadata
        else:
            extra[k] = v

    # `id` may be the deterministic UUID; for integer PK columns swap in the Fiix int.
    if "id" in writable_columns and "id" not in known:
        adapted, fits = _adapt_value_for_column("id", record.get("id"), column_types.get("id", ""), record)
        if fits and adapted is not None:
            known["id"] = adapted

    # Always carry source_system if column exists
    if "source_system" in writable_columns:
        known["source_system"] = record.get("source_system", "fiix")

    # Carry fiix_source_id if column exists (for traceability)
    if "fiix_source_id" in writable_columns:
        known["fiix_source_id"] = record.get("fiix_source_id")

    return known, extra


def _coerce(value: Any) -> Any:
    """Coerce Python values to types safe for asyncpg / SQLAlchemy."""
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    # pandas Timestamp → native datetime (asyncpg binds datetime, NOT ISO strings).
    if hasattr(value, "to_pydatetime") and not isinstance(value, datetime):
        try:
            v = value.to_pydatetime()
            return None if (v != v) else v
        except Exception:
            return None
    if isinstance(value, (datetime, date)):
        return value  # pass through — asyncpg handles datetime/date natively
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, float) and (value != value):  # NaN
        return None
    return value


# ── Final status update ───────────────────────────────────────────────────────

async def _write_final_status(state: FiixIngestionState, total_written: int) -> None:
    db_session = state.get("db_session")
    ingestion_id = state.get("ingestion_id")
    if not db_session or not ingestion_id:
        return
    try:
        from sqlalchemy import update as sa_update
        from ...models.migration import FiixIngestionJob
        from uuid import UUID as _UUID

        await db_session.execute(
            sa_update(FiixIngestionJob)
            .where(FiixIngestionJob.id == _UUID(ingestion_id))
            .values(
                status="complete",
                current_step="3_write_complete",
                total_records_written=total_written,
                write_results=state.get("write_results", {}),
                write_errors=state.get("write_errors", []),
                progress_pct=100.0,
                completed_at=datetime.utcnow(),
            )
        )
        await db_session.commit()
    except Exception as exc:
        logger.warning(f"[FiixWrite] Final status write failed (non-fatal): {exc}")
