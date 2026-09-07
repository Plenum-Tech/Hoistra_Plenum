"""Node 1: Ingest and configure — file parsing and detection.

Handles:
1. Download blob from Azure
2. Detect encoding (chardet)
3. Detect delimiter (CSV analysis)
4. Parse CSV/Excel into pandas DataFrames
5. Generate dataset summary
6. EL-M.1 validation: row_count > 0, column_count > 0
"""

import asyncio
import io
import logging
from datetime import datetime
from uuid import UUID

import chardet
import pandas as pd
from anthropic import AsyncAnthropic
from azure.storage.blob.aio import BlobClient
from sqlalchemy.ext.asyncio import AsyncSession

from ...excel_parser import ExcelWorkbook
from ...udr import profiling
from ...matchers import describe_dataset
from ...services.mapping_service import MappingService
from ..state import MigrationState
from ..event_enrich import append_event

from cafm_shared.logging import get_logger
logger = get_logger(__name__)


def _sanitize_column_names(df: pd.DataFrame) -> pd.DataFrame:
    """
    Replace pandas' Unnamed: N fallback column labels with positional col_N
    placeholders. The original source has no header at that position; keeping
    the pandas-internal name leaks an implementation detail into the schema
    mapper and produces meaningless "Unnamed: N" rows in the field-mapping UI.
    """
    if df is None or df.empty:
        return df
    new_cols: list[str] = []
    for idx, raw in enumerate(df.columns):
        s = str(raw).strip()
        if not s or s.lower().startswith("unnamed:") or s.lower() == "nan":
            new_cols.append(f"col_{idx + 1}")
        else:
            new_cols.append(s)
    df = df.copy()
    df.columns = new_cols
    return df


def _sanitize_records(records: list) -> list:
    """
    Convert all pandas/numpy non-JSON-serializable values in a list of record
    dicts to plain Python types safe for LangGraph msgpack checkpointing.

    Handles:
      - pd.NaT          → None
      - float NaN       → None
      - pd.Timestamp    → ISO-8601 string
      - numpy scalars   → native int / float via .item()
    """
    def _clean(v):
        # NA check: covers NaT, float NaN, None
        try:
            if pd.isna(v):
                return None
        except (TypeError, ValueError):
            pass  # pd.isna raises for non-scalar types

        if isinstance(v, pd.Timestamp):
            return v.isoformat()

        # numpy scalar → native Python type
        if hasattr(v, "item"):
            try:
                return v.item()
            except Exception:
                return str(v)

        return v

    return [{k: _clean(v) for k, v in row.items()} for row in records]


async def ingest_node(state: MigrationState) -> MigrationState:
    """
    Node 1: Download, parse, and analyze uploaded CMMS export.

    Args:
        state: MigrationState with source_blob_url populated

    Returns:
        Updated state with parsed_tables, row_count, column_count, table_health, column_descriptions
    """

    _node_started_at = datetime.utcnow()
    # PK + Unique-table gates now run right after this node, so a restart from Node 1 must re-open
    # them — clear their "answered" flags here (the deterministic mapper clears the DOWNSTREAM gates).
    state["pk_reviewed"] = False
    state["unique_table_reviewed"] = False
    migration_id = state.get("migration_id")
    source_blob_url = state.get("source_blob_url")
    source_blob_path = state.get("source_blob_path")
    source_file_bytes = state.get("source_file_bytes")

    if not source_file_bytes and not source_blob_url and not source_blob_path:
        logger.error("[Node 1] No source_file_bytes / source_blob_url / source_blob_path provided")
        state["error_message"] = "No file available — provide direct bytes, a Blob URL, or a stored source path"
        state["error_node"] = 1
        return state

    logger.info(f"[Node 1] Starting ingest: migration_id={migration_id}")

    try:
        # ── Step 1: Obtain file content (direct upload OR Azure Blob) ─────
        if source_file_bytes:
            # Fast path: caller already supplied raw bytes (direct upload)
            file_content: bytes = source_file_bytes
            logger.info(f"[Node 1] Using directly-uploaded file bytes ({len(file_content):,} bytes)")
        elif source_blob_path:
            # Re-run path: re-pull the persisted source from Blob, authenticated via the
            # connection string (works even on a private container).
            from ...config import get_settings as _gs
            _settings = _gs()
            conn = getattr(_settings, "azure_storage_connection_string", "") or ""
            container = getattr(_settings, "azure_blob_container_name", "") or "plenum-agentic-ai-attachments"
            if not conn:
                logger.error("[Node 1] source_blob_path set but Azure storage is not configured")
                state["error_message"] = "Source is stored in Blob but Azure storage is not configured for re-download."
                state["error_node"] = 1
                return state
            from azure.storage.blob.aio import BlobServiceClient as _BSC
            async with _BSC.from_connection_string(conn) as svc:
                bc = svc.get_blob_client(container=container, blob=source_blob_path)
                stream = await bc.download_blob()
                file_content = await stream.readall()
            logger.info(
                f"[Node 1] Re-pulled source from blob path {source_blob_path} ({len(file_content):,} bytes)"
            )
        else:
            # Fallback: download from a full Azure Blob URL (legacy / SAS)
            logger.info(f"[Node 1] Downloading file from Blob: {source_blob_url[:60]}...")
            async with BlobClient.from_blob_url(source_blob_url) as blob_client:
                file_bytes = await blob_client.download_blob()
                file_content = await file_bytes.readall()
            logger.info(f"[Node 1] Downloaded {len(file_content):,} bytes from Blob")

        state["source_file_bytes"] = file_content  # Transient; will be cleared before checkpoint

        # ── Step 2: Detect encoding ────────────────────────────────────
        detected = chardet.detect(file_content)
        encoding = detected.get("encoding", "utf-8")
        if not encoding:
            encoding = "utf-8"
        logger.info(f"[Node 1] Detected encoding: {encoding} (confidence: {detected.get('confidence', 0):.2f})")
        state["source_encoding"] = encoding

        # Decode to string
        try:
            file_str = file_content.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            logger.warning(f"[Node 1] Encoding {encoding} failed, trying utf-8")
            file_str = file_content.decode("utf-8", errors="replace")
            state["source_encoding"] = "utf-8"

        # ── Step 3: Detect file format and delimiter ───────────────────
        # Sample first 4KB for analysis
        sample = file_str[:4096]

        # Detect delimiter (CSV)
        delimiter = _detect_delimiter(sample)
        state["source_delimiter"] = delimiter
        logger.info(f"[Node 1] Detected delimiter: {repr(delimiter)}")

        # ── Step 4: Parse into pandas DataFrames ──────────────────────
        # Load ONLY first 5 rows (+ header) for analysis
        parsed_tables = {}
        total_rows = 0
        total_columns = 0
        actual_row_count = 0  # Track full file row count
        actual_col_count = 0

        # Try CSV first
        try:
            logger.info("[Node 1] Attempting CSV parse (top 10 rows only for column/table matching)...")
            # First pass: get actual row count from full file
            # Run the full-file parse OFF the event loop (asyncio.to_thread). This node otherwise
            # blocks the single API event loop while it parses 120k+ rows, which starves the status
            # polls / other requests to this service (they pend for the duration of ingestion).
            _mid = str(state.get("migration_id") or "")
            with profiling.span(_mid, "ingest.csv.read_full"):
                df_full = await asyncio.to_thread(
                    pd.read_csv,
                    io.StringIO(file_str),
                    delimiter=delimiter,
                    dtype=str,
                    nrows=None,  # Read all to get accurate count
                )
            df_full = _sanitize_column_names(df_full)
            actual_row_count = len(df_full)
            actual_col_count = len(df_full.columns)

            # Second pass: only the TOP 10 rows feed column/table matching + the panel preview.
            # The full file (df_full above → state["full_tables"]) is what gets the finalized
            # column renames and is pushed to the DB at the write phase.
            # PROFILING NOTE: this is a SECOND full parse of the same bytes (the Excel path below
            # avoids it via df_full.head(10)); the harness measures it as ingest.csv.read_preview so
            # the redundant-parse cost is visible before optimizing.
            with profiling.span(_mid, "ingest.csv.read_preview"):
                df = pd.read_csv(
                    io.StringIO(file_str),
                    delimiter=delimiter,
                    dtype=str,
                    nrows=10,  # ← TOP 10 ROWS FOR COLUMN/TABLE MATCHING + DISPLAY
                )
            df = _sanitize_column_names(df)
            parsed_tables["data"] = _sanitize_records(df.to_dict(orient="records"))

            # STORE FULL FILE separately for Node 5+ processing. Building the full-file records is
            # pure-Python over EVERY row (120k+) — the single longest CPU stretch here — so run it
            # off the event loop too, keeping the service responsive during ingestion.
            full_tables = {}
            with profiling.span(_mid, "ingest.csv.sanitize_full"):
                full_tables["data"] = await asyncio.to_thread(
                    lambda: _sanitize_records(df_full.to_dict(orient="records"))
                )
            state["full_tables"] = full_tables
            profiling.emit_report(_mid, logger)

            state["detected_file_format"] = "csv"
            logger.info(f"[Node 1] CSV parsed: {actual_row_count:,} rows × {actual_col_count} columns (analyzing first {len(df)} rows)")
        except Exception as e:
            logger.warning(f"[Node 1] CSV parse failed: {e}")
            # Try Excel
            try:
                logger.info("[Node 1] Attempting Excel parse (python-calamine) — top 10 rows for matching, full sheet stored for later...")
                # Open the workbook ONCE (calamine loads it a single time; each sheet read reuses it).
                _mid = str(state.get("migration_id") or "")
                with profiling.span(_mid, "ingest.excel.open"):
                    wb = ExcelWorkbook(io.BytesIO(file_content))
                full_tables = {}
                for sheet_name in wb.sheet_names:
                    # Detect which row is actually the header — skip banner / title rows so we don't
                    # end up with "Unnamed: N" columns spread across what was really blank padding.
                    header_row = wb.header_row(sheet_name)
                    if header_row > 0:
                        logger.info(
                            f"[Node 1] Sheet {sheet_name}: skipping {header_row} banner row(s) "
                            f"before real header at row {header_row}"
                        )

                    # Parse the FULL sheet off the event loop (see CSV note) so a large workbook
                    # doesn't starve other requests. Reuses the loaded workbook — no re-parse.
                    with profiling.span(_mid, "ingest.excel.read_sheet"):
                        df_full = await asyncio.to_thread(wb.read, sheet_name, header=header_row, dtype=str)
                    df_full = _sanitize_column_names(df_full)
                    actual_row_count = len(df_full)
                    actual_col_count = len(df_full.columns)

                    # Only the TOP 10 rows feed column/table matching + the panel preview — sliced
                    # from the full frame (no second parse). The full sheet (→ state["full_tables"])
                    # gets the finalized column renames and is pushed to the DB at the write phase.
                    df = df_full.head(10)
                    parsed_tables[sheet_name] = _sanitize_records(df.to_dict(orient="records"))

                    # STORE FULL FILE separately for Node 5+ processing (records build off-loop).
                    with profiling.span(_mid, "ingest.excel.sanitize_full"):
                        full_tables[sheet_name] = await asyncio.to_thread(
                            lambda df=df_full: _sanitize_records(df.to_dict(orient="records"))
                        )

                    logger.info(f"[Node 1] Sheet {sheet_name}: {actual_row_count:,} rows × {actual_col_count} columns (analyzing first {len(df)} rows)")

                wb.close()
                # Store full tables for later nodes
                state["full_tables"] = full_tables
                state["detected_file_format"] = "excel"
                profiling.emit_report(_mid, logger)
            except Exception as e2:
                logger.error(f"[Node 1] Excel parse failed: {e2}")
                state["error_message"] = f"Could not parse file: {str(e2)}"
                state["error_node"] = 1
                return state

        # ── Step 5: Analyze data quality (on sample only, but report full file size) ──────────────────────────────
        state["parsed_tables"] = parsed_tables

        # Calculate table health. null% is measured on the sample, but ROW/COLUMN
        # counts are each table's ACTUAL full-file counts. IMPORTANT: read every
        # table's row count from state["full_tables"], NOT the shared
        # `actual_row_count` scratch var — that var held only the LAST sheet parsed,
        # so in a multi-file / multi-sheet migration (each uploaded file becomes a
        # sheet in the combined workbook) it stamped one file's row count onto every
        # table and the dataset totals showed a single file instead of the aggregate.
        # Include ALL tables, even empty ones, so the user sees every sheet's completeness.
        full_tables = state.get("full_tables") or {}
        table_health = {}
        for table_name, records in parsed_tables.items():
            # ACTUAL full-file row count for THIS specific table/sheet.
            _full = full_tables.get(table_name)
            tbl_row_count = len(_full) if isinstance(_full, list) else 0

            if not records:
                # Empty sample → 0 columns; still report the real row count.
                table_health[table_name] = {
                    "row_count": tbl_row_count,
                    "column_count": 0,
                    "null_percentages": {},
                    "avg_null_percentage": 0.0,
                }
                continue

            df = pd.DataFrame(records)
            sample_row_count = len(df)
            col_count = len(df.columns)

            # Calculate null percentage per column (on sample only)
            null_pcts = {}
            for col in df.columns:
                null_pcts[col] = float((df[col].isna().sum() / sample_row_count) * 100)

            table_health[table_name] = {
                "row_count": tbl_row_count,  # ACTUAL full-file rows for THIS table
                "column_count": col_count,   # ACTUAL columns for THIS table
                "null_percentages": null_pcts,
                "avg_null_percentage": sum(null_pcts.values()) / len(null_pcts) if null_pcts else 0,
            }

        # ── Node 1 overall summary (WP-5: 7-node flow, Node 1) ─────────────
        # Dataset totals AGGREGATE across every source table/file: SUM of rows and
        # SUM of columns. This matches Deterministic Mapping (Node 2), which reports
        # total_columns = mapped + unresolved summed across all tables. A single-file
        # migration is unaffected (one table → the sum equals that table's counts).
        summary_tables = []
        total_rows_all = 0
        total_columns_all = 0
        for tname, th in table_health.items():
            rows = int(th.get("row_count", 0) or 0)
            cols = int(th.get("column_count", 0) or 0)
            total_rows_all += rows
            total_columns_all += cols
            summary_tables.append({
                "name": tname,
                "rows": rows,
                "columns": cols,
                "avg_null_pct": round(float(th.get("avg_null_percentage", 0.0) or 0.0), 1),
            })

        # Keep total_rows/total_columns as the AGGREGATE for the dataset-summary text
        # (Step 6) and the dataset-level fields the ingestion panel reads first.
        total_rows = total_rows_all
        total_columns = total_columns_all
        state["table_health"] = table_health
        state["row_count"] = total_rows_all
        state["column_count"] = total_columns_all

        overall_summary = {
            "table_count": len(table_health),
            "total_rows": total_rows_all,
            "total_columns": total_columns_all,
            "detected_format": state.get("detected_file_format", "unknown"),
            "tables": summary_tables,
        }
        state["overall_summary"] = overall_summary

        # ── Table-level CAFM match (Excel sheet → plenum_cafm table) ───────
        # Surfaces the source→target table comparison directly in the File
        # Ingestion card (e.g. assets→assets, sites_2→sites). Deterministic name
        # match first, then one Haiku call for the leftovers. Non-fatal: any
        # failure just yields an empty/partial map and the card shows "no match".
        cafm_table_matches: dict[str, str | None] = {}
        cafm_table_match_confidence: dict[str, float] = {}
        try:
            from ...db import get_plenum_cafm_columns_by_table
            from ...matchers import match_tables_to_cafm
            from ...app import get_anthropic_client as _get_client

            _columns_by_table = await get_plenum_cafm_columns_by_table()
            _cafm_tables = sorted(_columns_by_table.keys())
            _source_tables = {
                name: (list(records[0].keys()) if records else [])
                for name, records in parsed_tables.items()
            }
            if _cafm_tables and _source_tables:
                # Pass destination columns so match_tables_to_cafm can run the
                # deterministic column-overlap pre-check before falling back to the
                # LLM (fixes works → work_orders mis-routing on column meaning).
                cafm_table_matches, cafm_table_match_confidence = await match_tables_to_cafm(
                    _source_tables, _cafm_tables, _get_client(),
                    cafm_columns_by_table=_columns_by_table,
                )
                logger.info(
                    f"[Node 1] CAFM table matches: {cafm_table_matches} "
                    f"(confidence: {cafm_table_match_confidence})"
                )
        except Exception as e:
            logger.warning(f"[Node 1] CAFM table match skipped: {e}")
        state["cafm_table_matches"] = cafm_table_matches
        state["cafm_table_match_confidence"] = cafm_table_match_confidence
        overall_summary["cafm_table_matches"] = cafm_table_matches

        # ── Step 6: Generate dataset summary (via Haiku) ───────────────
        logger.info("[Node 1] Generating dataset description...")

        # Get first table for analysis
        first_table = next(iter(parsed_tables.values())) if parsed_tables else []
        if first_table:
            df = pd.DataFrame(first_table[:5])  # First 5 rows
            df_head_str = df.to_string()
            column_names = list(df.columns)

            # Import AsyncAnthropic here to avoid circular imports
            from ...app import get_anthropic_client

            client = get_anthropic_client()

            # Call describe_dataset (Haiku)
            column_descriptions = await describe_dataset(df_head_str, column_names, client)
            state["column_descriptions"] = column_descriptions
            logger.info(f"[Node 1] Column descriptions generated for {len(column_names)} columns")

            # Create human-readable summary
            summary = f"CMMS export: {state.get('cmms_name', 'Unknown')} system. "
            summary += f"{total_rows} total rows across {len(parsed_tables)} table(s). "
            if table_health:
                avg_health = sum(t["avg_null_percentage"] for t in table_health.values()) / len(
                    table_health
                )
                summary += f"Data quality: {100 - avg_health:.1f}% complete (avg)."
            state["dataset_summary"] = summary

        # ── Step 7: Try to auto-load stored mapping configuration ───────
        # Attempt to lookup a stored mapping based on source_system and table_name
        try:
            organization_id = state.get("organization_id")
            source_system = state.get("cmms_name", "").strip()
            table_name = next(iter(parsed_tables.keys())) if parsed_tables else "unknown"

            if organization_id and source_system:
                # Get a DB session for the lookup
                from ...db import get_async_session_factory
                session_factory = get_async_session_factory()

                async with session_factory() as session:
                    mapping_service = MappingService(session)
                    stored_mapping = await mapping_service.lookup_mapping(
                        organization_id=UUID(organization_id) if isinstance(organization_id, str) else organization_id,
                        source_system=source_system,
                        table_name=table_name,
                    )

                    if stored_mapping:
                        logger.info(
                            f"[Node 1] Auto-loaded stored mapping for {source_system}/{table_name}"
                        )
                        state["json_mapper"] = stored_mapping
                        state["mapping_source"] = "stored"
                    else:
                        logger.debug(
                            f"[Node 1] No stored mapping found for {source_system}/{table_name}"
                        )
                        state["mapping_source"] = "provided_or_default"
        except Exception as e:
            logger.warning(
                f"[Node 1] Failed to auto-load stored mapping: {str(e)}. Proceeding with provided config."
            )
            state["mapping_source"] = "provided_or_default"

        # ── EL-M.1 Validation ────────────────────────────────────────
        # Check: row_count > 0 and column_count > 0
        if state["row_count"] <= 0:
            logger.error("[Node 1] EL-M.1 FAILED: row_count == 0")
            state["error_message"] = "No data rows found in file"
            state["error_node"] = 1
            state["el_m1_passed"] = False
            return state

        if state["column_count"] <= 0:
            logger.error("[Node 1] EL-M.1 FAILED: column_count == 0")
            state["error_message"] = "No columns found in file"
            state["error_node"] = 1
            state["el_m1_passed"] = False
            return state

        state["el_m1_passed"] = True
        logger.info(
            f"[Node 1] EL-M.1 PASSED: {state['row_count']} rows × {state['column_count']} columns"
        )

        # ── First data-quality step: null/NaN scan across every column ──────
        # Detect-only (Node 5 does the actual filling). The outcome is reported in
        # the Activity Log (node log lines + output.nan_report) and the affected
        # rows are surfaced in the Query Space via the step-pause payload below.
        from .nan_scan import scan_nan_values, summarize_nan_report
        # Full-dataset cell pass — run off the event loop (like the sanitize step above)
        # so a large file doesn't block status polls / other requests during ingestion.
        nan_report = await asyncio.to_thread(scan_nan_values, state.get("full_tables") or {})
        state["nan_report"] = nan_report
        _nan_log_lines = summarize_nan_report(nan_report)
        for _ln in _nan_log_lines:
            logger.info(f"[Node 1] {_ln}")

        # ── Second data-quality step: merge duplicate columns within each table ──────
        # A sheet often records one value under two headers (works.tagnum == works.id). Merge
        # them HERE, before table mapping, so the whole pipeline downstream sees one column
        # instead of two identical copies competing for a canonical name and a destination.
        from .column_merge import (
            apply_merge_decisions,
            merge_duplicate_columns,
            summarize_duplicate_column_report,
        )
        _merged_tables, dup_col_report = await asyncio.to_thread(
            merge_duplicate_columns, state.get("full_tables") or {}
        )
        if dup_col_report.get("total_merges"):
            state["full_tables"] = _merged_tables
            # parsed_tables is the SAMPLED view of the same sheets — replay the same drops so the
            # sample and the full data don't disagree about which columns exist.
            state["parsed_tables"] = apply_merge_decisions(
                state.get("parsed_tables") or {}, dup_col_report
            )
            # Column counts are reported to the user and drive the EL-M.1 check — keep them true
            # after the merge, both per-table (table_health) and in the dataset total.
            _health = state.get("table_health") or {}
            for _tname, _entries in (dup_col_report.get("tables") or {}).items():
                _h = _health.get(_tname)
                if isinstance(_h, dict) and _h.get("column_count"):
                    _h["column_count"] = max(
                        0, int(_h["column_count"]) - sum(len(e["dropped"]) for e in _entries)
                    )
            state["table_health"] = _health
            state["column_count"] = max(
                0,
                int(state.get("column_count") or 0)
                - int(dup_col_report.get("total_columns_dropped") or 0),
            )
        state["duplicate_column_report"] = dup_col_report
        for _ln in summarize_duplicate_column_report(dup_col_report):
            logger.info(f"[Node 1] {_ln}")

        # ── Clear transient file bytes before checkpoint ───────────────
        state["source_file_bytes"] = None

        state["current_step"] = 1
        state["status"] = "running"
        _nan_cells = int(nan_report.get("total_nan_cells", 0) or 0)
        _nan_rows = int(nan_report.get("total_rows_with_nan", 0) or 0)
        _nan_outcome = (
            f"Null/NaN scan: {_nan_cells:,} value(s) in {_nan_rows:,} row(s) flagged for cleaning"
            if _nan_cells
            else "Null/NaN scan: no null/NaN values found"
        )
        append_event(
            state,
            node_id=1,
            node_name="ingest_node",
            event="node_complete",
            outcome=f"Ingested {state.get('table_count', 0)} table(s), {state['row_count']} rows",
            detail=(
                f"Parsed {state['row_count']} rows · {_nan_outcome}"
                + (
                    f" · Merged {dup_col_report['total_merges']} duplicate column group(s), "
                    f"{dup_col_report['total_columns_dropped']} column(s) removed"
                    if dup_col_report.get("total_merges")
                    else ""
                )
            ),
        )

        logger.info(f"[Node 1] Complete: {state['row_count']} rows, {state['column_count']} columns")

        migration_id = state.get("migration_id")
        if migration_id:
            from .db_writer import update_node_progress, write_step_pause
            await update_node_progress(
                migration_id, "1_ingest",
                total_fields=state.get("column_count", 0),
            )
            await write_step_pause(
                migration_id,
                "step_1_ingest",
                {
                    "node": 1,
                    "label": "Ingest & Configure",
                    "rows": state.get("row_count", 0),
                    "columns": state.get("column_count", 0),
                    "tables": list((state.get("full_tables") or {}).keys()),
                    "format": state.get("detected_file_format", "unknown"),
                    "table_health": state.get("table_health", {}),
                    "overall_summary": state.get("overall_summary", {}),
                    # Excel sheet → plenum_cafm table comparison (incl. LLM matches
                    # like sites_2 → sites) for the ingest card.
                    "cafm_table_matches": state.get("cafm_table_matches", {}),
                    # First data-quality step — NaN scan report. Drives the "NaN values
                    # cleaned" section in the Query Space ingest card (affected rows).
                    "nan_report": state.get("nan_report", {}),
                },
            )
            # Per-table source→canonical match lines for the processing log, labelled by
            # METHOD: confidence 1.0 = deterministic exact-name match; <1.0 = semantic
            # search (Haiku) over the column names; None = no canonical match.
            _tm = state.get("cafm_table_matches", {}) or {}
            _tc = state.get("cafm_table_match_confidence", {}) or {}
            _table_match_logs: list[str] = []
            for _src, _tgt in _tm.items():
                _conf = float(_tc.get(_src, 0.0) or 0.0)
                _pct = int(round(_conf * 100))
                if _tgt and _conf >= 0.999:
                    _table_match_logs.append(f"Table match (exact name): {_src} → {_tgt} ({_pct}%)")
                elif _tgt:
                    _table_match_logs.append(
                        f"Table match (semantic search): {_src} → {_tgt} ({_pct}% confidence)"
                    )
                else:
                    _table_match_logs.append(
                        f"Table match (semantic search): {_src} → no canonical match"
                    )

            # B7.1 EARLY — the "Unique table identification" cards depend only on the parsed
            # source tables, so build them here (from the small preview rows + full-file row
            # counts) and surface them on the status response under `udr_table_resolution`.
            # The left panel then shows each table card DURING the run, in step with the
            # Activity Log's "Unique table identification" entry, instead of the whole block
            # appearing only at the pre-semantic gate. The node-3 gate payload / node-11 emit
            # carry the FULL report (PK detection · mapping · final decisions), which supersedes
            # this partial (status reads the LAST node-log output.udr_table_resolution).
            _partial_table_resolution: dict = {}
            try:
                from ...udr.run_activity import build_partial_table_resolution

                _full_tables = state.get("full_tables") or {}
                _row_counts = {
                    _t: (len(_r) if isinstance(_r, list) else 0)
                    for _t, _r in _full_tables.items()
                }
                _partial_table_resolution = build_partial_table_resolution(
                    state.get("parsed_tables") or {},
                    row_counts=_row_counts,
                )
            except Exception as _e:  # pragma: no cover — additive, never fatal to ingestion
                logger.warning(f"[Node 1] partial B7.1 table cards failed: {_e}")
                _partial_table_resolution = {}

            from .schema_db_writer import migration_append_node_log_auto
            await migration_append_node_log_auto(
                migration_id, 1, "File Ingestion", _node_started_at, datetime.utcnow(),
                output={"row_count": state.get("row_count", 0),
                        "column_count": state.get("column_count", 0),
                        "table_count": len(state.get("full_tables") or {}),
                        "tables": list((state.get("full_tables") or {}).keys()),
                        "detected_format": state.get("detected_file_format", "unknown"),
                        "overall_summary": state.get("overall_summary", {}),
                        "cafm_table_matches": state.get("cafm_table_matches", {}),
                        "cafm_table_match_confidence": state.get("cafm_table_match_confidence", {}),
                        # First data-quality step — NaN scan outcome (detect-only; filled in Node 5).
                        "nan_report": state.get("nan_report", {}),
                        # Only attach when non-empty so the status field stays None until there
                        # are real cards (avoids rendering an empty B7.1 block mid-ingest).
                        **({"udr_table_resolution": _partial_table_resolution}
                           if _partial_table_resolution else {})},
                logs=[f"Parsed {state.get('row_count', 0)} rows × {state.get('column_count', 0)} columns",
                      f"Detected format: {state.get('detected_file_format', 'unknown')}",
                      f"EL-M.1: {'PASSED' if state.get('el_m1_passed') else 'FAILED'}"]
                     + _nan_log_lines
                     + _table_match_logs,
            )

        return state

    except Exception as e:
        logger.exception(f"[Node 1] Unhandled exception: {e}")
        state["error_message"] = str(e)
        state["error_node"] = 1
        state["error_timestamp"] = datetime.utcnow()
        state["status"] = "failed"
        return state


def _detect_delimiter(sample_text: str) -> str:
    """
    Detect CSV delimiter by analyzing sample text.

    Common delimiters: , \t ; |

    Returns: Most likely delimiter (default: comma)
    """
    # Count occurrences of each delimiter in first 5 lines
    lines = sample_text.split("\n")[:5]
    delimiter_counts = {",": 0, "\t": 0, ";": 0, "|": 0}

    for line in lines:
        for delim in delimiter_counts:
            delimiter_counts[delim] += line.count(delim)

    # Find delimiter with most consistent count
    # (all lines should have roughly the same count)
    if delimiter_counts[","] > 0:
        return ","
    if delimiter_counts["\t"] > 0:
        return "\t"
    if delimiter_counts[";"] > 0:
        return ";"
    if delimiter_counts["|"] > 0:
        return "|"

    # Default to comma
    return ","
