"""Node 6: Output Generation — Generate final JsonMapperConfig.

This final node takes the approved hierarchy and all mappings (tier1 + tier2 + approved FKs)
and generates the complete JsonMapperConfig ready for use in the data ingestion pipeline.

Output includes:
- version, source_system, canonical_fields dict, vendor_aliases dict
- Detected hierarchies
- Configuration for extra fields (raw_metadata vs custom vs skip)
- Audit information and statistics
"""

import csv
import io
import json
import logging
import re
from datetime import datetime
from typing import Dict, List, Any, Optional

from ..schema_state import SchemaMappingState, CanonicalFieldMapping

from cafm_shared.logging import get_logger
logger = get_logger(__name__)


async def schema_output_node(state: SchemaMappingState) -> SchemaMappingState:
    """
    Node 6: Generate final JsonMapperConfig from all mapping and hierarchy data.

    Takes all the approved mappings (tier1 + tier2 + flagged), approved hierarchy,
    and external schema information, and produces a complete JsonMapperConfig
    that is ready for use in the data ingestion pipeline.

    Args:
        state: SchemaMappingState with all mappings and hierarchy information

    Returns:
        Updated state with final_mapper_config and final_summary
    """

    _node_started_at = datetime.utcnow()
    schema_mapping_id = state.get("schema_mapping_id")
    external_cmms_name = state.get("external_cmms_name", "Unknown")
    external_tables = state.get("external_tables", {})

    # Collect all mappings from tiers 1 and 2
    tier1_mappings = state.get("tier1_mappings", [])
    tier2_auto_mapped = state.get("tier2_auto_mapped", [])
    tier2_flagged = state.get("tier2_flagged", [])
    tier2_unmappable = state.get("tier2_unmappable", [])

    # Hierarchy and FK data
    detected_foreign_keys = state.get("detected_foreign_keys", [])
    detected_hierarchies = state.get("detected_hierarchies", [])
    junction_tables = state.get("junction_tables", [])
    horizontal_relationships = state.get("horizontal_relationships", [])
    isolated_tables = state.get("isolated_tables", [])
    user_hierarchy_corrections = state.get("user_hierarchy_corrections", {})
    implicit_hierarchies = state.get("implicit_hierarchies", {})

    logger.info(
        f"[Node 6] Generating final output: "
        f"T1={len(tier1_mappings)}, T2_auto={len(tier2_auto_mapped)}, "
        f"T2_flagged={len(tier2_flagged)}, unmapped={len(tier2_unmappable)}"
    )

    try:
        canonical_tables_full = state.get("canonical_tables", {})
        extra_fields_config = state.get("extra_fields_config", [])

        # ── Step 1: Build table-centric mapping view ─────────────────────
        tables, new_tables, raw_metadata_fields, skipped_fields = _build_table_centric_config(
            canonical_tables_full,
            tier1_mappings,
            tier2_auto_mapped,
            tier2_flagged,
            extra_fields_config,
        )

        # ── Step 2: Compile mapping audit trail ────────────────────────
        mapping_audit = _build_mapping_audit(
            tier1_mappings, tier2_auto_mapped, tier2_flagged, tier2_unmappable
        )

        # ── Step 3: Compile hierarchy information ──────────────────────
        hierarchy_summary = _build_hierarchy_summary(
            detected_foreign_keys,
            detected_hierarchies,
            junction_tables,
            horizontal_relationships,
            isolated_tables,
            user_hierarchy_corrections,
            implicit_hierarchies,
        )

        # ── Step 4: Generate final table-centric config ────────────────
        total_mapped = len(tier1_mappings) + len(tier2_auto_mapped) + len(tier2_flagged)
        total_fields = _count_total_fields(external_tables)
        custom_ddl_count = sum(1 for e in extra_fields_config if e.get("storage_strategy") == "custom")
        new_table_count = len(new_tables)

        final_mapper_config = {
            "version": "1.0",
            "source_system": external_cmms_name,
            "generated_at": datetime.utcnow().isoformat(),
            "schema_mapping_id": str(schema_mapping_id),
            "tables": tables,
            "new_tables": new_tables,
            "raw_metadata_fields": raw_metadata_fields,
            "skipped_fields": skipped_fields,
            "hierarchy": hierarchy_summary,
            "audit": {
                "total_source_fields": total_fields,
                "tier1_mapped": len(tier1_mappings),
                "tier2_mapped": len(tier2_auto_mapped) + len(tier2_flagged),
                "unmappable": len(tier2_unmappable),
                "new_columns_added": custom_ddl_count - new_table_count,
                "new_tables_created": new_table_count,
                "raw_metadata_count": len(raw_metadata_fields),
                "skipped_count": len(skipped_fields),
                "canonical_tables_touched": len(tables),
                "mapping_coverage_pct": round(
                    min(100.0, (total_mapped / total_fields * 100)) if total_fields else 0.0, 1
                ),
                "field_confidence_stats": _compute_confidence_stats(
                    tier1_mappings, tier2_auto_mapped, tier2_flagged
                ),
                "overall_confidence": state.get("overall_mapping_confidence", 0.0),
                "hierarchy_approved": state.get("hierarchy_approved", False),
                "mapping_audit_trail": mapping_audit,
                "generated_timestamp": datetime.utcnow().isoformat(),
            },
        }

        # ── Step 6: Summary ──────────────────────────────────────────────
        summary = {
            "total_source_fields": total_fields,
            "canonical_tables_touched": len(tables),
            "new_tables_created": len(new_tables),
            "tier1_auto_mapped": len(tier1_mappings),
            "tier2_auto_mapped": len(tier2_auto_mapped),
            "tier2_flagged": len(tier2_flagged),
            "unmappable": len(tier2_unmappable),
            "mapping_coverage_pct": final_mapper_config["audit"]["mapping_coverage_pct"],
            "detected_fk_count": len(detected_foreign_keys),
            "hierarchy_count": len(detected_hierarchies),
            "max_hierarchy_depth": max((_calculate_hierarchy_depth(r) for r in detected_hierarchies), default=0),
            "junction_table_count": len(junction_tables),
            "horizontal_relationship_count": len(horizontal_relationships),
            "isolated_table_count": len(isolated_tables),
            "implicit_hierarchy_count": len(implicit_hierarchies),
        }

        logger.info(
            f"[Node 6] ✓ Final output generation complete: "
            f"{len(tables)} canonical tables touched, "
            f"{summary['mapping_coverage_pct']:.1f}% coverage"
        )

        # ── Step 6a: Generate + upload output artifacts ──────────────────
        output_json_url = ""
        output_csv_url = ""
        output_sql_url = ""
        try:
            # CSV — flat table of every field mapping with tier, confidence, rationale
            _csv_buf = io.StringIO()
            _csv_fields = ["source_table", "source_field", "target_field", "tier",
                           "confidence", "rationale", "status"]
            _writer = csv.DictWriter(_csv_buf, fieldnames=_csv_fields, extrasaction="ignore")
            _writer.writeheader()
            for row in mapping_audit:
                _writer.writerow({k: row.get(k, "") for k in _csv_fields})
            _csv_bytes = _csv_buf.getvalue().encode("utf-8")

            # JSON — full mapper config
            _json_bytes = json.dumps(final_mapper_config, indent=2, default=str).encode("utf-8")

            # SQL — DDL preview: mirrors exactly what schema_write_node will execute
            _extra_fields = state.get("extra_fields_config", [])
            _detected_fks = state.get("detected_foreign_keys", [])
            _approved_fks = [fk for fk in _detected_fks if fk.get("user_confirmed", False)]
            _existing_tables = sorted(canonical_tables_full.keys())

            _sql_lines = [
                "-- Schema Mapping DDL Preview",
                f"-- Source system: {external_cmms_name}",
                f"-- Schema mapping ID: {schema_mapping_id}",
                f"-- Generated at: {datetime.utcnow().isoformat()}Z",
                f"-- Canonical tables: {len(_existing_tables)}",
                "--",
                "-- This file mirrors the exact SQL that will be executed by Node 8.",
                "-- Replace <new_schema> with the schema name you confirm in the next step.",
                "",
                "-- ================================================================",
                "-- SECTION 1 — Create new schema",
                "-- ================================================================",
                "",
                "CREATE SCHEMA IF NOT EXISTS <new_schema>;",
                "",
                "-- ================================================================",
                f"-- SECTION 2 — Clone {len(_existing_tables)} table structures from plenum_cafm",
                "--              (INCLUDING ALL preserves indexes, constraints, defaults)",
                "-- ================================================================",
                "",
            ]
            for _tbl in _existing_tables:
                _sql_lines.append(
                    f"CREATE TABLE IF NOT EXISTS <new_schema>.{_tbl} "
                    f"(LIKE plenum_cafm.{_tbl} INCLUDING ALL);"
                )
            _sql_lines += [
                "",
                "-- ================================================================",
                f"-- SECTION 3 — Copy data from plenum_cafm into <new_schema>",
                "--              (Generated columns such as downtime_minutes are",
                "--               excluded automatically — Node 8 queries",
                "--               information_schema to build explicit column lists.)",
                "-- ================================================================",
                "",
            ]
            for _tbl in _existing_tables:
                _sql_lines.append(
                    f"INSERT INTO <new_schema>.{_tbl} "
                    f"SELECT * FROM plenum_cafm.{_tbl};  -- explicit col list at runtime"
                )
            _sql_lines += [
                "",
                "-- ================================================================",
                "-- SECTION 4 — Custom DDL (extra columns, new tables, FK constraints)",
                "--              derived from your field-mapping gate decisions",
                "-- ================================================================",
                "",
            ]
            try:
                from .schema_write_node import _build_ddl_statements
                _ddl_stmts = _build_ddl_statements(
                    _extra_fields, _approved_fks, set(_existing_tables),
                    target_schema="<new_schema>",
                )
                if _ddl_stmts:
                    for _stmt in _ddl_stmts:
                        _sql_lines.append(f"-- {_stmt['description']}")
                        _sql_lines.append(_stmt["sql"])
                        _sql_lines.append("")
                else:
                    _sql_lines.append(
                        "-- No custom DDL statements "
                        "(no extra fields or approved FK constraints)"
                    )
            except Exception as _ddl_exc:
                logger.warning(f"[Node 7] DDL preview generation failed (non-fatal): {_ddl_exc}")
                _sql_lines.append("-- Custom DDL preview unavailable")
            _sql_bytes = "\n".join(_sql_lines).encode("utf-8")

            # Browser-downloadable API paths (proxied by GET .../artifacts/{filename})
            if schema_mapping_id:
                import os
                import tempfile

                _api_base = f"/api/schema-mapping/{schema_mapping_id}/artifacts"
                output_json_url = f"{_api_base}/mapper_config.json"
                output_csv_url = f"{_api_base}/field_mappings.csv"
                output_sql_url = f"{_api_base}/schema_ddl_preview.sql"

                _artifact_dir = os.path.join(
                    tempfile.gettempdir(), f"schema_mapping_{schema_mapping_id}"
                )
                os.makedirs(_artifact_dir, exist_ok=True)
                for _fname, _data in [
                    ("mapper_config.json", _json_bytes),
                    ("field_mappings.csv", _csv_bytes),
                    ("schema_ddl_preview.sql", _sql_bytes),
                ]:
                    with open(os.path.join(_artifact_dir, _fname), "wb") as _af:
                        _af.write(_data)
                logger.info(f"[Node 7] ✓ Artifacts written to {_artifact_dir}")

            # Optional Azure Blob backup (private URLs — frontend uses API paths above)
            from ...config import get_settings as _get_artifact_settings
            _art_settings = _get_artifact_settings()
            _blob_conn = getattr(_art_settings, "azure_storage_connection_string", None)
            _blob_container = getattr(_art_settings, "azure_blob_container_name", "")
            _blob_base = f"schema-mapping/{schema_mapping_id}"

            if _blob_conn and schema_mapping_id:
                from azure.storage.blob.aio import BlobServiceClient as _BlobSvc
                async with _BlobSvc.from_connection_string(_blob_conn) as _svc:
                    for _fname, _data in [
                        ("mapper_config.json", _json_bytes),
                        ("field_mappings.csv", _csv_bytes),
                        ("schema_ddl_preview.sql", _sql_bytes),
                    ]:
                        _bpath = f"{_blob_base}/{_fname}"
                        _bc = _svc.get_blob_client(container=_blob_container, blob=_bpath)
                        await _bc.upload_blob(_data, overwrite=True)
                logger.info("[Node 7] ✓ Artifacts uploaded to Azure Blob (backup)")
            elif schema_mapping_id:
                logger.info(
                    "[Node 7] AZURE_STORAGE_CONNECTION_STRING not set — "
                    "serving artifacts from local temp + API download only"
                )

            # Persist API URLs to DB
            if schema_mapping_id and (output_json_url or output_csv_url or output_sql_url):
                from .schema_db_writer import schema_update_artifact_urls_auto
                await schema_update_artifact_urls_auto(
                    schema_mapping_id, output_json_url, output_csv_url, output_sql_url
                )
        except Exception as _art_exc:
            logger.warning(f"[Node 7] Artifact generation/upload failed (non-fatal): {_art_exc}")

        # ── Step 7: Update state ─────────────────────────────────────────
        state["final_mapping_config"] = final_mapper_config
        state["final_summary"] = summary
        state["output_json_url"] = output_json_url
        state["output_csv_url"] = output_csv_url
        state["output_sql_url"] = output_sql_url
        state["status"] = "complete"
        state["notes"] = state.get("notes", []) + [
            f"Final output generated: {len(tables)} canonical tables touched, "
            f"{summary['mapping_coverage_pct']:.1f}% coverage, "
            f"{summary['detected_fk_count']} foreign keys"
        ]

        schema_mapping_id = state.get("schema_mapping_id")
        if schema_mapping_id:
            from .schema_db_writer import schema_write_step_pause_auto
            payload = {
                "node": 7,
                "title": "Output Generation Complete",
                "canonical_tables_touched": summary["canonical_tables_touched"],
                "new_tables_created": summary["new_tables_created"],
                "total_source_fields": summary["total_source_fields"],
                "tier1_auto_mapped": summary["tier1_auto_mapped"],
                "tier2_auto_mapped": summary["tier2_auto_mapped"],
                "tier2_flagged": summary["tier2_flagged"],
                "unmappable": summary["unmappable"],
                "mapping_coverage_pct": round(summary["mapping_coverage_pct"], 1),
                "detected_fk_count": summary["detected_fk_count"],
                "max_hierarchy_depth": summary["max_hierarchy_depth"],
                "output_json_url": output_json_url,
                "output_csv_url": output_csv_url,
                "output_sql_url": output_sql_url,
            }
            await schema_write_step_pause_auto(
                schema_mapping_id, 7, "step_7_output", payload
            )
            from .schema_db_writer import schema_append_node_log_auto
            await schema_append_node_log_auto(
                schema_mapping_id, 8, "Output Generation", _node_started_at, datetime.utcnow(),
                output={"canonical_tables_touched": summary["canonical_tables_touched"],
                        "new_tables_created": summary["new_tables_created"],
                        "total_source_fields": summary["total_source_fields"],
                        "tier1_auto_mapped": summary["tier1_auto_mapped"],
                        "tier2_auto_mapped": summary["tier2_auto_mapped"],
                        "tier2_flagged": summary["tier2_flagged"],
                        "unmappable": summary["unmappable"],
                        "mapping_coverage_pct": round(summary["mapping_coverage_pct"], 1),
                        "detected_fk_count": summary["detected_fk_count"],
                        "max_hierarchy_depth": summary["max_hierarchy_depth"]},
                logs=[f"Generated table-centric mapping config for {external_cmms_name}",
                      f"{summary['canonical_tables_touched']} canonical tables touched, "
                      f"{summary['mapping_coverage_pct']:.1f}% coverage",
                      f"T1: {summary['tier1_auto_mapped']}, T2 auto: {summary['tier2_auto_mapped']}, "
                      f"T2 flagged: {summary['tier2_flagged']}, unmapped: {summary['unmappable']}",
                      f"{summary['detected_fk_count']} foreign keys, "
                      f"max hierarchy depth {summary['max_hierarchy_depth']}"],
            )

        # ── Save updated registry snapshot to DB ─────────────────────────────
        # Persists any newly learned semantic/human-approved aliases from this
        # run so next startup loads them without schema re-introspection.
        try:
            from ...services.registry_cache import save_new_version, compute_schema_hash
            from ...config import get_settings as _get_settings
            _db_url = _get_settings().db_url
            _hash = compute_schema_hash(final_mapper_config.get("tables", {}))
            _ver = await save_new_version(_db_url, final_mapper_config, _hash)
            logger.info(f"[Node 7] Registry snapshot saved as v{_ver}")
        except Exception as _reg_exc:
            logger.warning(f"[Node 7] Registry snapshot save failed (non-fatal): {_reg_exc}")

        return state

    except Exception as e:
        logger.exception(f"[Node 6] ✗ Error: {e}")
        state["status"] = "error"
        state["error_message"] = f"Output generation failed: {str(e)}"
        return state


# ────────────────────────────────────────────────────────────────────────────
# Helper Functions
# ────────────────────────────────────────────────────────────────────────────


def _build_table_centric_config(
    canonical_tables: dict,
    tier1_mappings: List[CanonicalFieldMapping],
    tier2_auto_mapped: List[CanonicalFieldMapping],
    tier2_flagged: List[CanonicalFieldMapping],
    extra_fields_config: List[Dict],
) -> tuple:
    """
    Build table-centric artifact: organise everything by target canonical table.

    Returns: (tables, new_tables, raw_metadata_fields, skipped_fields)

    tables          — {canonical_table: {source_tables, mapped_fields, new_columns}}
    new_tables      — {new_table_name: {source_table, primary_key, columns}}
    raw_metadata_fields — [{source_table, source_field}]
    skipped_fields  — [{source_table, source_field}]
    """
    # Build reverse lookup: canonical field name → canonical table name
    canonical_field_to_table: Dict[str, str] = {}
    for tbl_name, tbl_info in canonical_tables.items():
        cols = tbl_info.get("columns", []) if isinstance(tbl_info, dict) else []
        for col in cols:
            field_name = col.get("field_name") if isinstance(col, dict) else str(col)
            if field_name:
                canonical_field_to_table[field_name] = tbl_name

    # Seed tables dict — only canonical tables that actually receive mappings will survive
    tables: Dict[str, Any] = {}

    def _get_or_create_table(tbl_name: str) -> dict:
        if tbl_name not in tables:
            tables[tbl_name] = {"source_tables": [], "mapped_fields": [], "new_columns": []}
        return tables[tbl_name]

    # Process T1 / T2 / flagged mappings
    for m in (tier1_mappings + tier2_auto_mapped + tier2_flagged):
        target_field = m.get("target_field")
        source_table = m.get("source_table", "unknown")
        canon_tbl = canonical_field_to_table.get(target_field or "")
        if not canon_tbl:
            continue
        entry = _get_or_create_table(canon_tbl)
        if source_table not in entry["source_tables"]:
            entry["source_tables"].append(source_table)
        entry["mapped_fields"].append({
            "source_table": source_table,
            "source_field": m.get("source_field"),
            "target_field": target_field,
            "tier": m.get("tier", "T1"),
            "confidence": round(float(m.get("confidence") or 0.0), 4),
            "rationale": m.get("rationale", ""),
        })

    # Process extra_fields_config (custom DDL, raw_metadata, skip)
    new_tables: Dict[str, Any] = {}
    raw_metadata_fields: List[Dict] = []
    skipped_fields: List[Dict] = []

    for entry in extra_fields_config:
        strategy = entry.get("storage_strategy")
        source_table = entry.get("source_table", "unknown")
        source_field = entry.get("source_field", "")

        if strategy == "custom":
            target_table = entry.get("target_table", "")
            is_new = bool(entry.get("is_new_table"))
            col_def = {
                "source_table": source_table,
                "source_field": source_field,
                "column_name": entry.get("custom_column_name", source_field),
                "data_type": entry.get("data_type", "VARCHAR(255)"),
                "nullable": entry.get("nullable", True),
            }
            if is_new:
                if target_table not in new_tables:
                    new_tables[target_table] = {
                        "source_table": source_table,
                        "primary_key": entry.get("new_table_pk", "id"),
                        "columns": [],
                    }
                new_tables[target_table]["columns"].append(col_def)
            else:
                tbl_entry = _get_or_create_table(target_table)
                if source_table not in tbl_entry["source_tables"]:
                    tbl_entry["source_tables"].append(source_table)
                tbl_entry["new_columns"].append(col_def)

        elif strategy == "raw_metadata":
            raw_metadata_fields.append({"source_table": source_table, "source_field": source_field})

        elif strategy == "skip":
            skipped_fields.append({"source_table": source_table, "source_field": source_field})

    # Drop canonical tables with no mappings and no new columns (untouched by this run)
    tables = {k: v for k, v in tables.items() if v["mapped_fields"] or v["new_columns"]}

    return tables, new_tables, raw_metadata_fields, skipped_fields


def _build_mapping_audit(
    tier1_mappings: List[CanonicalFieldMapping],
    tier2_auto_mapped: List[CanonicalFieldMapping],
    tier2_flagged: List[CanonicalFieldMapping],
    tier2_unmappable: List,
) -> List[Dict]:
    """
    Build audit trail of all field mappings.

    Returns: List of mapping decisions with confidence and rationale.
    """
    audit_trail = []

    # Tier 1 mappings
    for mapping in tier1_mappings:
        audit_trail.append({
            "source_field": mapping.get("source_field"),
            "target_field": mapping.get("target_field"),
            "tier": mapping.get("tier", "T1"),
            "confidence": mapping.get("confidence", 0.0),
            "rationale": mapping.get("rationale", ""),
            "status": "mapped",
        })

    # Tier 2 auto-mapped
    for mapping in tier2_auto_mapped:
        audit_trail.append({
            "source_field": mapping.get("source_field"),
            "target_field": mapping.get("target_field"),
            "tier": mapping.get("tier", "T2_semantic"),
            "confidence": mapping.get("confidence", 0.0),
            "rationale": mapping.get("rationale", ""),
            "status": "auto_mapped",
        })

    # Tier 2 flagged
    for mapping in tier2_flagged:
        audit_trail.append({
            "source_field": mapping.get("source_field"),
            "target_field": mapping.get("target_field"),
            "tier": mapping.get("tier", "T2_semantic"),
            "confidence": mapping.get("confidence", 0.0),
            "rationale": mapping.get("rationale", ""),
            "status": "flagged_for_review",
            "suggestions": mapping.get("suggestions", []),
        })

    # Unmappable fields
    for field_info in tier2_unmappable:
        audit_trail.append({
            "source_field": field_info.get("field_name"),
            "target_field": None,
            "tier": "T2_unmappable",
            "confidence": 0.0,
            "rationale": "Could not map with sufficient confidence",
            "status": "unmappable",
        })

    return audit_trail


def _build_hierarchy_summary(
    detected_foreign_keys: List,
    detected_hierarchies: List,
    junction_tables: List,
    horizontal_relationships: List,
    isolated_tables: List,
    user_hierarchy_corrections: dict,
    implicit_hierarchies: dict,
) -> Dict:
    """Build the full schema graph summary for the output artifact."""
    max_depth = max((_calculate_hierarchy_depth(r) for r in detected_hierarchies), default=0)
    return {
        "detected_foreign_keys": [
            {
                "source_table": fk.get("source_table"),
                "source_column": fk.get("source_column"),
                "target_table": fk.get("target_table"),
                "target_column": fk.get("target_column"),
                "relationship_type": fk.get("relationship_type"),
                "confidence": fk.get("confidence"),
                "canonical_backed": fk.get("canonical_backed", False),
                "canonical_target_table": fk.get("canonical_target_table"),
                "user_confirmed": fk.get("user_confirmed", False),
            }
            for fk in detected_foreign_keys
        ],
        "hierarchy_forest": [_serialize_hierarchy_tree(r) for r in detected_hierarchies],
        "junction_tables": [
            {
                "table_name": jt.get("table_name"),
                "left_table": jt.get("left_table"),
                "left_fk_column": jt.get("left_fk_column"),
                "right_table": jt.get("right_table"),
                "right_fk_column": jt.get("right_fk_column"),
                "confidence": jt.get("confidence"),
            }
            for jt in junction_tables
        ],
        "horizontal_relationships": [
            {
                "source_table": hr.get("source_table"),
                "target_table": hr.get("target_table"),
                "relationship_type": hr.get("relationship_type"),
                "via_table": hr.get("via_table"),
                "shared_parent": hr.get("shared_parent"),
                "confidence": hr.get("confidence"),
            }
            for hr in horizontal_relationships
        ],
        "isolated_tables": isolated_tables,
        "implicit_hierarchies": implicit_hierarchies,
        "user_corrections_applied": bool(user_hierarchy_corrections),
        "summary": {
            "hierarchy_count": len(detected_hierarchies),
            "max_depth": max_depth,
            "junction_table_count": len(junction_tables),
            "horizontal_relationship_count": len(horizontal_relationships),
            "isolated_table_count": len(isolated_tables),
        },
    }


def _serialize_hierarchy_tree(node) -> Dict:
    """Recursively serialize hierarchy tree for output."""
    if not node:
        return {}

    children = node.get("children", [])
    return {
        "table_name": node.get("table_name"),
        "primary_key": node.get("primary_key_field"),
        "level": node.get("level", 0),
        "children_count": len(children),
        "children": [_serialize_hierarchy_tree(child) for child in children],
    }


def _compute_confidence_stats(
    tier1_mappings: List[CanonicalFieldMapping],
    tier2_auto_mapped: List[CanonicalFieldMapping],
    tier2_flagged: List[CanonicalFieldMapping],
) -> Dict:
    """
    Compute confidence statistics across all mapping tiers.

    Returns: Dictionary with min, max, avg confidence per tier.
    """
    def _stats_for_tier(mappings):
        confidences = [m.get("confidence", 0.0) for m in mappings]
        if not confidences:
            return {"count": 0, "min": 0.0, "max": 0.0, "avg": 0.0}
        return {
            "count": len(confidences),
            "min": min(confidences),
            "max": max(confidences),
            "avg": sum(confidences) / len(confidences),
        }

    return {
        "tier1_deterministic": _stats_for_tier(tier1_mappings),
        "tier2_semantic_auto": _stats_for_tier(tier2_auto_mapped),
        "tier2_semantic_flagged": _stats_for_tier(tier2_flagged),
    }


def _calculate_mapping_coverage(canonical_count: int, mapped_count: int) -> float:
    """Calculate percentage coverage of mapped fields."""
    if canonical_count == 0:
        return 0.0
    return min(100.0, (mapped_count / canonical_count) * 100.0)


def _count_total_fields(external_tables: dict) -> int:
    """Count total fields across all external tables."""
    return sum(len(table_info.get("columns", [])) for table_info in external_tables.values())


def _calculate_hierarchy_depth(node, depth: int = 0) -> int:
    """Calculate maximum depth of hierarchy tree."""
    if not node:
        return 0

    children = node.get("children", [])
    if not children:
        return depth + 1

    return max(_calculate_hierarchy_depth(child, depth + 1) for child in children)
