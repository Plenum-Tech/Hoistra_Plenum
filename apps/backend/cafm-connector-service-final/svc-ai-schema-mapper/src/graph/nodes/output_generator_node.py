"""Node 8: Output generator — generate all export formats and upload to Azure Blob.

Produces all output artefacts from clean, hierarchy-resolved data:
1. Nested JSON (sites > locations > assets > work_orders > tasks)
2. Flat CSV exports (per table)
3. SQL INSERT statements (in FK-dependency order)
4. PDF migration summary report
5. Mapping flow document (PDF/Word)
6. IntermediateSchema (for svc-ingestion handoff)
7. Upload all to Azure Blob with signed URLs

Dual logging: execution_logs for Streamlit display + logger for Docker monitoring

EL-M.8: IntermediateSchema Pydantic validates
"""

import io
import json
import logging
from datetime import datetime
from typing import Any, Optional
from uuid import UUID

import chardet
import pandas as pd
from azure.storage.blob.aio import BlobClient, BlobServiceClient

from ...excel_parser import ExcelWorkbook
from ...export import (
    build_nested_json,
    export_to_csv,
    export_to_sql,
    generate_pdf_report,
    build_intermediate_schema,
)
from ..state import MigrationState

from cafm_shared.logging import get_logger
logger = get_logger(__name__)


def _normalize_hierarchy_edges(confirmed: Any) -> list[dict]:
    """Defensively extract FK edges (child→parent) from confirmed_hierarchies,
    tolerating the several shapes the pipeline may produce."""
    items: list[Any] = []
    if isinstance(confirmed, list):
        items = confirmed
    elif isinstance(confirmed, dict):
        for k, v in confirmed.items():
            if isinstance(v, dict):
                vv = dict(v)
                vv.setdefault("child", vv.get("child_table") or vv.get("child") or k)
                items.append(vv)
            else:
                items.append({"child": k, "parent": v})
    edges: list[dict] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        child = (
            it.get("child_table") or it.get("child") or it.get("source_table")
            or it.get("source") or it.get("from_table")
        )
        parent = (
            it.get("parent_table") or it.get("parent") or it.get("target_table")
            or it.get("target") or it.get("to_table")
        )
        fk = it.get("fk_column") or it.get("fk") or it.get("foreign_key") or it.get("column") or ""
        pk = it.get("parent_pk") or it.get("pk") or it.get("primary_key") or ""
        if child and parent:
            edges.append({"child": str(child), "parent": str(parent), "fk": str(fk), "pk": str(pk)})
    return edges


def build_structure_markdown(
    *,
    cmms_name: str,
    records_tables: dict,
    confirmed_hierarchies: Any,
    containment_hierarchy: dict,
) -> str:
    """Render the recommended finalized DB structure as markdown (Feature 4a)."""
    lines: list[str] = []
    lines.append(f"# Recommended Finalized DB Structure — {cmms_name or 'UDR'}")
    lines.append("")
    lines.append(f"_Generated at UDR completion · {datetime.utcnow().isoformat()}Z_")
    lines.append("")
    lines.append("## Canonical hierarchy")
    lines.append("")
    lines.append("Organization → Location → Site → Asset → Work Order → Resource → Vendor")
    lines.append("")

    tables = sorted((records_tables or {}).keys())
    lines.append(f"## Tables ({len(tables)})")
    lines.append("")
    for t in tables:
        recs = records_tables.get(t) or []
        cols = list(recs[0].keys()) if recs and isinstance(recs[0], dict) else []
        lines.append(f"### {t}")
        lines.append(f"- Rows: {len(recs)}")
        if cols:
            lines.append(f"- Columns ({len(cols)}): {', '.join(str(c) for c in cols)}")
        lines.append("")

    edges = _normalize_hierarchy_edges(confirmed_hierarchies)
    if edges:
        lines.append("## Relationships (foreign keys)")
        lines.append("")
        lines.append("| Child table | FK column | → | Parent table | Parent PK |")
        lines.append("|---|---|---|---|---|")
        for e in edges:
            lines.append(f"| {e['child']} | {e['fk']} | → | {e['parent']} | {e['pk']} |")
        lines.append("")

    if isinstance(containment_hierarchy, dict) and containment_hierarchy:
        lines.append("## Containment hierarchy")
        lines.append("")
        for parent, children in containment_hierarchy.items():
            child_list = children if isinstance(children, list) else [children]
            lines.append(f"- **{parent}**")
            for c in child_list:
                lines.append(f"  - {c}")
        lines.append("")

    return "\n".join(lines)


async def output_generator_node(state: MigrationState) -> MigrationState:
    """
    Node 8: Generate all output formats and upload to Azure Blob.

    This node processes the FULL file (not just 5-row sample from Node 1).
    Downloads source file from Azure Blob to ensure complete data for exports.

    Generates:
    - Nested JSON hierarchy
    - Per-table CSV exports
    - SQL INSERT statements (in FK-dependency order)
    - PDF migration summary report
    - Mapping flow document (PDF/Word)
    - IntermediateSchema (Pydantic model for svc-ingestion)

    All artefacts uploaded to Azure Blob with signed URLs returned.

    Args:
        state: MigrationState with cleaned_tables, mappings, hierarchies

    Returns:
        Updated state with all output URLs, IntermediateSchema, execution_logs
    """

    _node_started_at = datetime.utcnow()
    migration_id = state.get("migration_id")
    cmms_name = state.get("cmms_name", "Unknown")
    organization_id = state.get("organization_id")
    source_blob_url = state.get("source_blob_url")
    source_filename = state.get("source_filename", "unknown")
    cleaned_tables = state.get("cleaned_tables", {})
    confirmed_hierarchies = state.get("confirmed_hierarchies", [])
    tier1_mappings = state.get("tier1_mappings", [])
    tier2_auto_mappings = state.get("tier2_auto_accepted", [])
    tier2_human_decisions = state.get("human_approved_mappings", [])

    # ── Apply B14.1 column-mapping gate overrides (HITL per-column decisions) ──────
    # {source_table: {source_field: "<dest_col>" | "__new__"}} — a dest column RE-TARGETS the
    # source column's destination; "__new__" makes it a NEW column (not merged) by targeting its
    # own snake_cased name. Best-effort + defensive: never break the write; match on source_table
    # when the mapping carries it, else on source_field alone.
    _col_overrides = state.get("column_dest_overrides") or {}
    if _col_overrides:
        def _snake(s: str) -> str:
            import re as _re
            return _re.sub(r"[^a-z0-9]+", "_", str(s or "").strip().lower()).strip("_")

        # Flatten to {source_field_lower: target} per table, plus a table-agnostic fallback.
        _by_tbl = {
            str(_t).lower(): {str(_c).lower(): _v for _c, _v in _cols.items()}
            for _t, _cols in _col_overrides.items() if isinstance(_cols, dict)
        }
        _any = {c: v for _cols in _by_tbl.values() for c, v in _cols.items()}

        def _apply(mlist):
            _changed = 0
            for _m in (mlist or []):
                if not isinstance(_m, dict):
                    continue
                _sf = str(_m.get("source_field") or "").lower()
                if not _sf:
                    continue
                _st = str(_m.get("source_table") or "").lower()
                _tgt = (_by_tbl.get(_st, {}).get(_sf)) if _st else None
                if _tgt is None:
                    _tgt = _any.get(_sf)
                if not _tgt:
                    continue
                _new_target = _snake(_m.get("source_field")) if _tgt == "__new__" else _tgt
                if _new_target and _new_target != _m.get("target_field"):
                    _m["target_field"] = _new_target
                    _m["column_mapping_override"] = True
                    _changed += 1
            return _changed

        _n = _apply(tier1_mappings) + _apply(tier2_auto_mappings) + _apply(tier2_human_decisions)
        if _n:
            logger.info(f"[Node 8] Applied {_n} B14.1 column-mapping override(s) to final mappings")
    overall_confidence = state.get("overall_confidence", 0.0)
    tier2_unmappable = state.get("tier2_unmappable", [])
    data_quality_warnings = state.get("data_quality_warnings", [])
    hierarchy_cycles = state.get("hierarchy_cycles", [])
    orphaned_records = state.get("orphaned_records", {})

    # ── Set up dual logging (Streamlit + Docker) ──────────────────
    execution_logs = []

    def log(msg: str):
        """Helper to append logs and also log to docker"""
        execution_logs.append(f"[Node 8] {msg}")
        logger.info(f"[Node 8] {msg}")

    # ── Type-aware guard (runs AFTER user B14.1 overrides so an explicit choice wins) ──────
    # Never write a text / categorical / date source column into a NUMERIC (or boolean /
    # temporal) destination column — the DB rejects it and drops every row. Pre-flight each
    # final mapping against the destination column's real type using a sample of the source
    # values; on a genuine mismatch retarget to a type-compatible, name-similar column on the
    # same table (frequency → frequency_type) or, if none exists, demote to a NEW column so the
    # data is preserved. Additive + best-effort — never fails the write.
    try:
        from ...db import get_plenum_cafm_column_types_by_table
        from ...matchers.type_compat import choose_compatible_target

        _dest_types_all = await get_plenum_cafm_column_types_by_table()
        if _dest_types_all:
            _tg_routing = state.get("table_routing") or {}
            _tg_rows = state.get("full_tables") or cleaned_tables or {}

            def _tg_samples(src_table: str, src_field: str, cap: int = 40) -> list:
                out: list = []
                for _r in (_tg_rows.get(src_table) or [])[:300]:
                    if isinstance(_r, dict) and src_field in _r:
                        out.append(_r.get(src_field))
                        if len(out) >= cap:
                            break
                return out

            def _tg_snake(s: str) -> str:
                import re as _re
                return _re.sub(r"[^a-z0-9]+", "_", str(s or "").strip().lower()).strip("_") or "column"

            # Targets already claimed per destination table, so a retarget never collides with a
            # column another mapping already lands in (mirrors the preprocess-node guard).
            _tg_used_by_dest: dict[str, set] = {}
            for _mlist in (tier1_mappings, tier2_auto_mappings, tier2_human_decisions):
                for _m in (_mlist or []):
                    if isinstance(_m, dict) and _m.get("target_field"):
                        _st = str(_m.get("source_table") or "")
                        _dst = str(_tg_routing.get(_st) or _st).lower()
                        _tg_used_by_dest.setdefault(_dst, set()).add(str(_m["target_field"]).lower())

            def _tg_guard(mlist) -> int:
                _fixed = 0
                for _m in (mlist or []):
                    if not isinstance(_m, dict):
                        continue
                    _tf = _m.get("target_field")
                    _sf = _m.get("source_field")
                    if not _tf or not _sf:
                        continue
                    _st = str(_m.get("source_table") or "")
                    _dst = str(_tg_routing.get(_st) or _st).lower()
                    _dtypes = _dest_types_all.get(_dst)
                    if not _dtypes:
                        continue
                    _excl = set(_tg_used_by_dest.get(_dst, set()))
                    _excl.discard(str(_tf).lower())
                    _new_t, _action = choose_compatible_target(
                        _sf, _tf, _tg_samples(_st, _sf), _dtypes, exclude=_excl
                    )
                    if _action == "keep":
                        continue
                    if _action == "new":
                        _new_t = _tg_snake(_sf)
                    if _new_t and _new_t != _tf:
                        _m["target_field"] = _new_t
                        _m["type_guard_retarget"] = {"from": _tf, "to": _new_t, "reason": _action}
                        _claimed = _tg_used_by_dest.setdefault(_dst, set())
                        _claimed.discard(str(_tf).lower())
                        _claimed.add(str(_new_t).lower())
                        _fixed += 1
                        log(
                            f"type guard: {_st or '?'}.{_sf} '{_tf}' → '{_new_t}' "
                            f"({_action}) — source values don't fit the destination column type"
                        )
                return _fixed

            _tg_total = (
                _tg_guard(tier1_mappings)
                + _tg_guard(tier2_auto_mappings)
                + _tg_guard(tier2_human_decisions)
            )
            if _tg_total:
                log(f"Type guard retargeted {_tg_total} column(s) away from incompatible destination types")
    except Exception as _tg_exc:  # pragma: no cover — additive, never fatal
        logger.warning(f"[Node 8] type-compatibility guard skipped: {_tg_exc}")

    log(f"Starting output generation for migration {migration_id}")

    if not cleaned_tables:
        log("❌ No cleaned tables found")
        state["error_message"] = "No data for output generation"
        state["error_node"] = 8
        state["execution_logs"] = execution_logs
        return state

    try:
        # ── CRITICAL: Get FULL file data for complete export ──────────────
        # `cleaned_tables` (from state) is always list-of-dicts — never DataFrames.
        # We build a SEPARATE `df_tables` dict of DataFrames for CSV generation only.
        # DataFrames MUST NOT be written back to state (LangGraph checkpointer
        # cannot serialize them to msgpack).

        state_full_tables = state.get("full_tables")
        is_direct_upload = (not source_blob_url) or source_blob_url == "direct_upload"

        # records_tables: list-of-dicts — used for SQL, JSON builder, IntermediateSchema
        # df_tables:      DataFrames    — used ONLY for CSV generation (never touches state)
        records_tables: dict = dict(cleaned_tables)  # shallow copy — keeps list-of-dicts
        df_tables: dict = {}

        if state_full_tables:
            # Fast path: Node 1 stored full file as list-of-dicts in state
            log(f"Using full_tables from state ({len(state_full_tables)} table(s))")
            for tbl_name, records in state_full_tables.items():
                if isinstance(records, list):
                    records_tables[tbl_name] = records
                    df_tables[tbl_name] = pd.DataFrame(records)
                elif isinstance(records, pd.DataFrame):
                    records_tables[tbl_name] = records.to_dict(orient="records")
                    df_tables[tbl_name] = records
            total_rows = sum(len(v) for v in records_tables.values() if hasattr(v, "__len__"))
            log(f"Full data loaded from state: {total_rows} total rows")

        elif is_direct_upload:
            # Direct upload but full_tables not in state — use cleaned_tables
            log("⚠️  Direct upload: full_tables not in state, using cleaned_tables for export")
            for tbl_name, records in records_tables.items():
                if isinstance(records, list):
                    df_tables[tbl_name] = pd.DataFrame(records)
                elif isinstance(records, pd.DataFrame):
                    # Should not happen, but handle defensively
                    df_tables[tbl_name] = records
                    records_tables[tbl_name] = records.to_dict(orient="records")

        else:
            # Download from Azure Blob
            log("Downloading FULL source file from Blob for complete export...")
            try:
                async with BlobClient.from_blob_url(source_blob_url) as blob_client:
                    file_bytes_dl = await blob_client.download_blob()
                    file_content = await file_bytes_dl.readall()
                log(f"Downloaded FULL file: {len(file_content):,} bytes")

                detected = chardet.detect(file_content)
                encoding = detected.get("encoding", "utf-8") or "utf-8"
                file_str = file_content.decode(encoding, errors="replace")
                sample = file_str[:4096]
                delimiter = "," if "," in sample else "\t"

                try:
                    df_full = pd.read_csv(io.StringIO(file_str), delimiter=delimiter, dtype=str)
                    records_tables["data"] = df_full.to_dict(orient="records")
                    df_tables["data"] = df_full
                    log(f"Parsed FULL CSV: {len(df_full):,} rows × {len(df_full.columns)} columns")
                except Exception as csv_err:
                    log(f"CSV parse failed: {csv_err}; trying Excel...")
                    wb = ExcelWorkbook(io.BytesIO(file_content))  # calamine, workbook opened once
                    for sheet_name in wb.sheet_names:
                        df_full = wb.read(sheet_name, dtype=str)
                        records_tables[sheet_name] = df_full.to_dict(orient="records")
                        df_tables[sheet_name] = df_full
                        log(f"Parsed FULL Excel sheet '{sheet_name}': {len(df_full):,} rows")
                    wb.close()

            except Exception as e:
                log(f"⚠️  Failed to download full file from Blob: {e} — using cleaned_tables")
                for tbl_name, records in records_tables.items():
                    if isinstance(records, list):
                        df_tables[tbl_name] = pd.DataFrame(records)

        # ── Step 1: Build nested JSON (uses records, not DataFrames) ─────────
        log(f"Building nested JSON hierarchy...")
        containment_hierarchy = state.get("containment_hierarchy", {})

        try:
            nested_json = build_nested_json(records_tables, containment_hierarchy, confirmed_hierarchies)
            total_entities = sum(len(v) if isinstance(v, list) else 1 for v in nested_json.get("entities", {}).values())
            log(f"✅ Nested JSON built: {total_entities} entities")
        except Exception as e:
            log(f"❌ Failed to build nested JSON: {e}")
            state["error_message"] = f"Nested JSON generation failed: {str(e)}"
            state["error_node"] = 8
            state["execution_logs"] = execution_logs
            return state

        # Route each SOURCE table to its DESTINATION and UNION co-routed / duplicate source tables
        # into ONE table. Used for CSV + SQL so both emit a SINGLE plenum_cafm.work_orders (both
        # files' rows) instead of source-named splits. The intermediate/nested JSON already merges by
        # routed entity (build_intermediate_schema extends records per entity_type), so it is left on
        # the source-keyed set. Union keeps every row; a shared PK dedupes at insert.
        _routing = state.get("table_routing") or {}
        routed_records_tables: dict = {}
        for _src, _rows in records_tables.items():
            _dest = _routing.get(_src) or _src
            if _dest in routed_records_tables:
                routed_records_tables[_dest].extend(list(_rows or []))
                log(f"  Merged '{_src}' → '{_dest}' ({len(_rows or [])} rows, union of duplicate/co-routed tables)")
            else:
                routed_records_tables[_dest] = list(_rows or [])

        # ── Step 2: Export to CSV — one file per DESTINATION table (duplicates merged) ──
        log(f"Exporting {len(routed_records_tables)} destination table(s) to CSV...")
        csv_exports = {}
        total_csv_rows = 0

        try:
            for table_name, records in routed_records_tables.items():
                if records:
                    df_tmp = pd.DataFrame(records)
                    csv_exports[table_name] = df_tmp.to_csv(index=False)
                    total_csv_rows += len(df_tmp)
                    log(f"  Exported '{table_name}': {len(df_tmp):,} rows")
            log(f"✅ CSV export complete: {total_csv_rows:,} total rows across {len(csv_exports)} file(s)")
        except Exception as e:
            log(f"❌ CSV export failed: {e}")
            state["error_message"] = f"CSV export failed: {str(e)}"
            state["error_node"] = 8
            state["execution_logs"] = execution_logs
            return state

        # ── Step 3: Export to SQL (uses records_tables — list-of-dicts) ──────
        log("Generating SQL INSERT statements (FK-dependency order)...")

        try:
            # Uses the routed+union set built above, so work_order + workorders emit a SINGLE
            # plenum_cafm.work_orders insert (both files' rows) rather than two source-named tables.
            sql_script = export_to_sql(routed_records_tables, confirmed_hierarchies)
            # B22.1 — append the shared-attribute lookup tables (attribute → new PK table) + FK
            # rewrites to the output SQL. Each block is idempotent (CREATE IF NOT EXISTS, INSERT ON
            # CONFLICT DO NOTHING) and its FK ALTERs are wrapped in error-swallowing DO blocks, so it
            # is safe to apply. Sourced from the column-intelligence report already in state.
            _lookup_tables = ((state.get("column_intelligence") or {}).get("shared_attribute_tables") or [])
            _ddl_blocks = [t.get("ddl_block") for t in _lookup_tables if isinstance(t, dict) and t.get("ddl_block")]
            if _ddl_blocks:
                sql_script = (
                    sql_script
                    + "\n\n-- ============================================================\n"
                    + f"-- SECTION: Shared-attribute lookup tables ({len(_ddl_blocks)}) + FK rewrites (B22.1)\n"
                    + "-- Attribute promoted to a lookup PK; source columns rewritten as FKs.\n"
                    + "-- ============================================================\n\n"
                    + "\n\n".join(_ddl_blocks)
                    + "\n"
                )
                log(f"✅ Appended {len(_ddl_blocks)} shared-attribute lookup table(s) to SQL output")
            sql_lines = sql_script.count('\n')
            log(f"✅ SQL script generated: {len(sql_script):,} bytes, {sql_lines:,} lines")
        except Exception as e:
            log(f"❌ SQL export failed: {e}")
            state["error_message"] = f"SQL export failed: {str(e)}"
            state["error_node"] = 8
            state["execution_logs"] = execution_logs
            return state

        # ── Step 4: Generate PDF migration summary report ────────────
        log("Generating PDF migration summary report...")

        try:
            pdf_bytes = generate_pdf_report(
                migration_id=str(migration_id),
                cmms_name=cmms_name,
                t1_count=len(tier1_mappings),
                t2_auto_count=len(tier2_auto_mappings),
                t2_human_count=len(tier2_human_decisions),
                t2_unmappable=tier2_unmappable,
                overall_confidence=overall_confidence,
                data_quality_warnings=data_quality_warnings,
                tier1_mappings=tier1_mappings,
                tier2_auto_mappings=tier2_auto_mappings,
                tier2_human_decisions=tier2_human_decisions,
                confirmed_hierarchies=confirmed_hierarchies,
                hierarchy_cycles=hierarchy_cycles,
                orphaned_records=orphaned_records,
            )
            log(f"✅ PDF report generated: {len(pdf_bytes):,} bytes")
        except Exception as e:
            log(f"❌ PDF report generation failed: {e}")
            # PDF generation is not critical; continue with warning
            pdf_bytes = None
            log("⚠️  Continuing without PDF report...")

        # ── Step 5: Build IntermediateSchema ─────────────────────────
        log("Building IntermediateSchema for svc-ingestion handoff...")

        try:
            intermediate_schema = build_intermediate_schema(
                migration_id=str(migration_id),
                cmms_name=cmms_name,
                source_filename=source_filename,
                source_blob_url=source_blob_url,
                cleaned_tables=records_tables,
                tier1_mappings=tier1_mappings,
                tier2_auto_mappings=tier2_auto_mappings,
                tier2_human_decisions=tier2_human_decisions,
                overall_confidence=overall_confidence,
                confirmed_hierarchies=confirmed_hierarchies,
                table_routing=state.get("table_routing"),
                new_tables=state.get("new_tables"),
                detected_file_format=state.get("detected_file_format"),
            )
            log(f"✅ IntermediateSchema built")
        except Exception as e:
            log(f"❌ IntermediateSchema build failed: {e}")
            state["error_message"] = f"IntermediateSchema build failed: {str(e)}"
            state["error_node"] = 8
            state["execution_logs"] = execution_logs
            return state

        # ── Step 6: EL-M.8 Validation: Validate IntermediateSchema ───
        log("Running EL-M.8 validation...")

        try:
            schema_dict = intermediate_schema.dict()

            # Validate required fields
            required_fields = [
                "ingestion_id",
                "source_type",
                "agent_id",
                "entities",
                "confidence",
                "audit",
            ]

            missing_fields = []
            for field in required_fields:
                if field not in schema_dict:
                    missing_fields.append(field)

            if missing_fields:
                raise ValueError(f"Missing required fields: {', '.join(missing_fields)}")

            # Validate entity structure
            entities = schema_dict.get("entities", {})
            if not isinstance(entities, dict):
                raise ValueError(f"entities must be dict, got {type(entities)}")

            # Validate confidence structure
            confidence = schema_dict.get("confidence", {})
            if not isinstance(confidence, dict):
                raise ValueError(f"confidence must be dict, got {type(confidence)}")

            if "overall" not in confidence:
                raise ValueError("confidence.overall is required")

            state["el_m8_passed"] = True
            log(f"✅ EL-M.8 PASSED: IntermediateSchema validates")

        except Exception as e:
            log(f"❌ EL-M.8 FAILED: Schema validation error: {e}")
            state["error_message"] = f"IntermediateSchema validation failed: {str(e)}"
            state["error_node"] = 8
            state["el_m8_passed"] = False
            state["execution_logs"] = execution_logs
            return state

        # ── Step 7: Upload to Azure Blob ─────────────────────────────
        log("Uploading artefacts to Azure Blob...")

        from ...config import get_settings as _get_settings
        _settings = _get_settings()
        blob_connection_string = _settings.azure_storage_connection_string
        blob_container = _settings.azure_blob_container_name
        blob_base_path = f"migrations/{migration_id}"

        # Keep backward-compatible nested output while also including full per-table
        # records so downloads reflect all flow tables, not only "sites" hierarchy.
        output_json_payload = {
            "nested_hierarchy": nested_json,
            "tables": records_tables,
            "table_count": len(records_tables),
            "tables_included": sorted(records_tables.keys()),
            "generated_at": datetime.utcnow().isoformat() + "Z",
        }

        # Build Excel workbook: one sheet per table
        excel_bytes: bytes | None = None
        try:
            import openpyxl
            wb = openpyxl.Workbook()
            wb.remove(wb.active)  # remove default empty sheet
            for table_name, df in df_tables.items():
                if not isinstance(df, pd.DataFrame) or df.empty:
                    continue
                sheet_name = table_name[:31]  # Excel sheet name limit
                ws = wb.create_sheet(title=sheet_name)
                ws.append(list(df.columns))
                for row in df.itertuples(index=False, name=None):
                    ws.append(list(row))
            if not wb.sheetnames and records_tables:
                for table_name, records in records_tables.items():
                    if not records:
                        continue
                    df_tmp = pd.DataFrame(records)
                    sheet_name = table_name[:31]
                    ws = wb.create_sheet(title=sheet_name)
                    ws.append(list(df_tmp.columns))
                    for row in df_tmp.itertuples(index=False, name=None):
                        ws.append(list(row))
            buf = io.BytesIO()
            wb.save(buf)
            excel_bytes = buf.getvalue()
            log(f"✅ Excel workbook built: {len(wb.sheetnames)} sheet(s), {len(excel_bytes):,} bytes")
        except Exception as e:
            log(f"❌ Excel workbook generation failed: {e}")

        # Recommended finalized DB structure (Feature 4a) — a human-readable
        # Locations→Sites→Assets→… summary derived from the confirmed hierarchy
        # + final tables/columns, surfaced as a download at UDR completion.
        try:
            structure_md = build_structure_markdown(
                cmms_name=cmms_name,
                records_tables=records_tables,
                confirmed_hierarchies=confirmed_hierarchies,
                containment_hierarchy=state.get("containment_hierarchy", {}),
            )
        except Exception as e:
            log(f"⚠️  Structure markdown generation failed: {e}")
            structure_md = None

        artefacts: dict[str, bytes | str] = {
            "output.json": json.dumps(output_json_payload, indent=2),
            "output.sql": sql_script,
        }
        if structure_md:
            artefacts["structure.md"] = structure_md
        if pdf_bytes:
            artefacts["migration_report.pdf"] = pdf_bytes
        if excel_bytes:
            artefacts["output.xlsx"] = excel_bytes
        for table_name, csv_content in csv_exports.items():
            artefacts[f"table_{table_name}.csv"] = csv_content

        uploaded_count = 0
        urls_generated: dict[str, str] = {}

        if not blob_connection_string:
            log("⚠️  AZURE_STORAGE_CONNECTION_STRING not set — skipping blob upload, URLs will be empty")
        else:
            async with BlobServiceClient.from_connection_string(blob_connection_string) as svc:
                for filename, content in artefacts.items():
                    blob_path = f"{blob_base_path}/{filename}"
                    try:
                        blob_client = svc.get_blob_client(container=blob_container, blob=blob_path)
                        data: bytes = content.encode("utf-8") if isinstance(content, str) else content
                        await blob_client.upload_blob(data, overwrite=True)
                        urls_generated[filename] = blob_client.url
                        uploaded_count += 1
                        log(f"  ✅ Uploaded: {filename} ({len(data):,} bytes) → {blob_client.url}")
                    except Exception as e:
                        log(f"  ⚠️  Failed to upload {filename}: {e}")

        log(f"✅ Uploaded {uploaded_count}/{len(artefacts)} artefacts to Blob")

        # ── Step 8: Update state with output URLs ────────────────────
        state["intermediate_schema"] = schema_dict
        state["output_json_url"] = urls_generated.get("output.json", "")
        state["output_csv_url"] = urls_generated.get("output.xlsx", "")
        state["output_sql_url"] = urls_generated.get("output.sql", "")
        state["output_sql_script"] = sql_script
        state["migration_report_url"] = urls_generated.get("migration_report.pdf", "")
        state["output_structure_md_url"] = urls_generated.get("structure.md", "")

        # Track all artefact metadata
        state["exported_artefacts"] = {
            "total_count": uploaded_count,
            "by_type": {
                "json": 1,
                "csv": len(csv_exports),
                "sql": 1,
                "pdf": 1 if pdf_bytes else 0,
            },
            "total_size_bytes": sum(len(str(c)) for c in artefacts.values()),
            "urls": urls_generated,
        }

        log(
            f"✅ Output generation complete: "
            f"1 JSON + {len(csv_exports)} CSV + 1 SQL + 1 PDF + IntermediateSchema"
        )

        state["current_step"] = 8
        state["execution_logs"] = execution_logs

        if "event_log" in state and isinstance(state["event_log"], list):
            state["event_log"].append(
                {
                    "timestamp": datetime.utcnow().isoformat(),
                    "event": "node_complete",
                    "node": 8,
                    "detail": f"All outputs generated and uploaded ({uploaded_count} artefacts)",
                }
            )

        migration_id = state.get("migration_id")
        if migration_id:
            from .db_writer import update_node_progress, write_step_pause
            await update_node_progress(
                migration_id, "8_output_generation",
                output_json_url=state.get("output_json_url"),
                output_csv_url=state.get("output_csv_url"),
                output_sql_url=state.get("output_sql_url"),
                migration_report_url=state.get("migration_report_url"),
                output_structure_md_url=state.get("output_structure_md_url"),
            )
            await write_step_pause(
                migration_id,
                "step_8_output_generation",
                {
                    "node": 8,
                    "label": "Output Generation",
                    "tables": len(records_tables),
                    "formats": ["json", "csv", "sql", "pdf"],
                    "artifacts_uploaded": uploaded_count,
                    # CAFM-013 — the outputs are COMBINED across all tables (1 JSON + N CSV + 1 SQL +
                    # 1 PDF), not 3-per-table. Surface the per-type breakdown so the UI can label the
                    # count unambiguously instead of reading as a miscount.
                    "artifacts_by_type": state["exported_artefacts"]["by_type"],
                    "json_url": state.get("output_json_url"),
                    "csv_url": state.get("output_csv_url"),
                    "sql_url": state.get("output_sql_url"),
                    "report_url": state.get("migration_report_url"),
                },
            )
            from .schema_db_writer import migration_append_node_log_auto
            await migration_append_node_log_auto(
                migration_id, 9, "Output Generation", _node_started_at, datetime.utcnow(),
                output={"table_count": len(records_tables),
                        "artifacts_uploaded": uploaded_count,
                        "formats": ["json", "csv", "sql", "pdf"],
                        "json_url": state.get("output_json_url"),
                        "csv_url": state.get("output_csv_url"),
                        "sql_url": state.get("output_sql_url"),
                        "report_url": state.get("migration_report_url")},
                logs=[f"Generated outputs for {len(records_tables)} tables",
                      f"Formats: JSON, CSV, SQL, PDF",
                      f"{uploaded_count} artifacts uploaded to Azure Blob",
                      f"EL-M.8: {'PASSED' if state.get('el_m8_passed') else 'FAILED'}"],
            )

        return state

    except Exception as e:
        log(f"❌ ERROR: {str(e)}")
        logger.exception(f"[Node 8] Unhandled exception: {e}")
        state["error_message"] = str(e)
        state["error_node"] = 8
        state["error_timestamp"] = datetime.utcnow()
        state["status"] = "failed"
        state["execution_logs"] = execution_logs
        return state
