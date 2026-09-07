"""Node 8: Output generation — generate IntermediateSchema and export files (MULTI-TABLE).

Steps:
1. Build IntermediateSchema (Pydantic model) from cleaned tables + mappings
2. Generate output files per table: JSON, CSV, SQL via proper export modules
3. Upload all artefacts to Azure Blob
4. Prepare for handoff to svc-ingestion

EL-M.8: IntermediateSchema Pydantic validates before storing.
"""

import json
import logging
import time
from datetime import datetime
from uuid import uuid4

from azure.storage.blob import BlobClient
from ..state import MigrationState
from ...export import (
    json_builder,
    csv_exporter,
    sql_exporter,
    report_generator,
    intermediate_schema_builder,
)

from cafm_shared.logging import get_logger
logger = get_logger(__name__)


async def output_generator_node(state: MigrationState) -> MigrationState:
    """
    Node 8: Generate outputs and build IntermediateSchema — MULTI-TABLE.

    Processes cleaned tables + mappings to create canonical output format
    for handoff to svc-ingestion.

    Args:
        state: MigrationState with cleaned_tables, tier1_mappings_by_table, etc.

    Returns:
        Updated state with intermediate_schema, output_json_url, output_csv_url, output_sql_url
    """

    migration_id = state.get("migration_id")
    cleaned_tables = state.get("cleaned_tables", {})
    tier1_by_table = state.get("tier1_mappings_by_table", {})
    tier2_auto_by_table = state.get("tier2_auto_by_table", {})
    tier2_human_by_table = state.get("tier2_human_decisions_by_table", {})
    confirmed_hierarchies = state.get("confirmed_hierarchies", [])
    containment_hierarchy_by_table = state.get("containment_hierarchy_by_table", {})

    logger.info(f"[Node 8] Starting output generation: migration_id={migration_id}")

    start_time = state.get("_start_time", time.time())

    try:
        # Build combined mapping dict (canonical source → target)
        mapping_dict = {}
        for mappings in tier1_by_table.values():
            for m in mappings:
                mapping_dict[m.get("source_field")] = m.get("target_field")

        for mappings in tier2_auto_by_table.values():
            for m in mappings:
                mapping_dict[m.get("source_field")] = m.get("target_field")

        for mappings in tier2_human_by_table.values():
            for m in mappings:
                mapping_dict[m.get("source_field")] = m.get("target_field")

        logger.info(f"[Node 8] Combined mapping: {len(mapping_dict)} fields")

        # ── Step 1: Build canonical tables (rename columns) ────────────────
        canonical_tables = {}
        for table_name, records in cleaned_tables.items():
            if not records:
                logger.info(f"[Node 8] Skipping empty table: {table_name}")
                continue

            canonical_records = []
            for record in records:
                canonical_record = {}
                for source_field, value in record.items():
                    target_field = mapping_dict.get(source_field, source_field)
                    canonical_record[target_field] = value
                canonical_records.append(canonical_record)

            canonical_tables[table_name] = canonical_records
            logger.info(f"[Node 8] ► Renamed columns for {table_name}: {len(canonical_records)} rows")

        # ── Step 2: Generate output files using proper export modules ─────
        logger.info(f"[Node 8] Generating output files via export modules...")

        # Merge containment_hierarchy_by_table into single dict for json_builder
        merged_containment_hierarchy = {}
        for table_name, hierarchy in containment_hierarchy_by_table.items():
            merged_containment_hierarchy[table_name] = hierarchy

        # JSON: nested structure (sites → locations → assets → WOs)
        nested_json = json_builder.build_nested_json(
            cleaned_tables=canonical_tables,
            containment_hierarchy=merged_containment_hierarchy,
            confirmed_hierarchies=confirmed_hierarchies
        )
        json_data = json.dumps(nested_json, indent=2, default=str)
        logger.info(f"[Node 8]   JSON: nested structure built")

        # CSV: flat export (one file per source table)
        csv_exports = csv_exporter.export_to_csv(canonical_tables)
        # Combine all CSVs into single file
        combined_csv_data = ""
        for table_name, csv_content in csv_exports.items():
            combined_csv_data += f"-- Table: {table_name}\n"
            combined_csv_data += csv_content
            combined_csv_data += "\n"
        logger.info(f"[Node 8]   CSV: {len(csv_exports)} tables exported")

        # SQL: parameterised INSERT statements in FK-dependency order
        sql_statements = sql_exporter.export_to_sql(
            cleaned_tables=canonical_tables,
            confirmed_hierarchies=confirmed_hierarchies
        )
        logger.info(f"[Node 8]   SQL: parameterised inserts generated")

        # Compute stats for PDF report
        tier1_count = sum(len(m) for m in tier1_by_table.values())
        tier2_auto_count = sum(len(m) for m in tier2_auto_by_table.values())
        tier2_human_count = sum(len(m) for m in tier2_human_by_table.values())

        # Flatten tier2_unmappable_by_table
        tier2_unmappable = []
        if state.get("tier2_unmappable_by_table"):
            for fields in state.get("tier2_unmappable_by_table", {}).values():
                tier2_unmappable.extend(fields)

        # Flatten all mappings for report
        all_tier1_mappings = []
        for mappings in tier1_by_table.values():
            all_tier1_mappings.extend(mappings)

        all_tier2_auto_mappings = []
        for mappings in tier2_auto_by_table.values():
            all_tier2_auto_mappings.extend(mappings)

        all_tier2_human_decisions = []
        for mappings in tier2_human_by_table.values():
            all_tier2_human_decisions.extend(mappings)

        # PDF: summary report
        pdf_data = report_generator.generate_pdf_report(
            migration_id=migration_id,
            cmms_name=state.get("cmms_name", "Unknown"),
            tier1_count=tier1_count,
            tier2_auto_count=tier2_auto_count,
            tier2_human_count=tier2_human_count,
            tier2_unmappable=tier2_unmappable,
            overall_confidence=state.get("overall_confidence", 0.5),
            data_quality_warnings=state.get("data_quality_warnings", []),
            tier1_mappings=all_tier1_mappings,
            tier2_auto_mappings=all_tier2_auto_mappings,
            tier2_human_decisions=all_tier2_human_decisions,
            confirmed_hierarchies=confirmed_hierarchies,
            hierarchy_cycles=state.get("hierarchy_cycles", [])
        )
        logger.info(f"[Node 8]   PDF: report generated")

        # ── Step 3: Build IntermediateSchema ────────────────────────────────
        logger.info(f"[Node 8] Building IntermediateSchema...")

        intermediate_schema_obj = intermediate_schema_builder.build_intermediate_schema(
            migration_id=migration_id,
            cmms_name=state.get("cmms_name", "Unknown"),
            source_blob_url=state.get("source_blob_url", ""),
            cleaned_tables=canonical_tables,
            tier1_mappings=all_tier1_mappings,
            tier2_auto_mappings=all_tier2_auto_mappings,
            tier2_human_decisions=all_tier2_human_decisions,
            overall_confidence=state.get("overall_confidence", 0.5),
            confirmed_hierarchies=confirmed_hierarchies
        )

        # Convert IntermediateSchema object to dict
        intermediate_schema = intermediate_schema_obj.dict()

        logger.info(f"[Node 8] ═══════════════════════════════════════════")
        logger.info(f"[Node 8] IntermediateSchema built:")
        if intermediate_schema.get("entities"):
            for entity_type, records in intermediate_schema["entities"].items():
                if records:
                    logger.info(f"[Node 8]   {entity_type}: {len(records)} entities")

        # ── EL-M.8 Validation ────────────────────────────────────────────
        required_keys = ["ingestion_id", "source_type", "entities", "confidence"]
        for key in required_keys:
            if key not in intermediate_schema:
                logger.error(f"[Node 8] EL-M.8 FAILED: Missing key {key}")
                state["error_message"] = f"Missing required IntermediateSchema key: {key}"
                state["error_node"] = 8
                state["el_m8_passed"] = False
                return state

        # Validate entities structure
        if not isinstance(intermediate_schema.get("entities"), dict):
            logger.error(f"[Node 8] EL-M.8 FAILED: entities must be dict")
            state["error_message"] = "IntermediateSchema entities must be a dictionary"
            state["error_node"] = 8
            state["el_m8_passed"] = False
            return state

        state["el_m8_passed"] = True
        logger.info("[Node 8] EL-M.8 PASSED: IntermediateSchema valid")

        # ── Step 4: Upload all artefacts to Azure Blob ────────────────────
        logger.info(f"[Node 8] Uploading artefacts to Azure Blob...")

        try:
            from ...config import settings

            # Upload nested JSON
            json_blob_name = f"migrations/{migration_id}/output.json"
            _upload_to_blob(
                blob_name=json_blob_name,
                data=json_data,
                content_type="application/json",
                connection_string=settings.azure_storage_connection_string,
                container_name=settings.azure_blob_container_name
            )
            output_json_url = f"blob://{settings.azure_blob_container_name}/{json_blob_name}"
            logger.info(f"[Node 8]   Uploaded: {json_blob_name}")

            # Upload flat CSV (combined from all tables)
            csv_blob_name = f"migrations/{migration_id}/output.csv"
            _upload_to_blob(
                blob_name=csv_blob_name,
                data=combined_csv_data,
                content_type="text/csv",
                connection_string=settings.azure_storage_connection_string,
                container_name=settings.azure_blob_container_name
            )
            output_csv_url = f"blob://{settings.azure_blob_container_name}/{csv_blob_name}"
            logger.info(f"[Node 8]   Uploaded: {csv_blob_name}")

            # Upload SQL statements
            sql_blob_name = f"migrations/{migration_id}/INSERT.sql"
            _upload_to_blob(
                blob_name=sql_blob_name,
                data=sql_statements,
                content_type="text/plain",
                connection_string=settings.azure_storage_connection_string,
                container_name=settings.azure_blob_container_name
            )
            output_sql_url = f"blob://{settings.azure_blob_container_name}/{sql_blob_name}"
            logger.info(f"[Node 8]   Uploaded: {sql_blob_name}")

            # Upload PDF report
            pdf_blob_name = f"migrations/{migration_id}/report.pdf"
            _upload_to_blob(
                blob_name=pdf_blob_name,
                data=pdf_data,
                content_type="application/pdf",
                connection_string=settings.azure_storage_connection_string,
                container_name=settings.azure_blob_container_name
            )
            migration_report_url = f"blob://{settings.azure_blob_container_name}/{pdf_blob_name}"
            logger.info(f"[Node 8]   Uploaded: {pdf_blob_name}")

        except Exception as e:
            logger.exception(f"[Node 8] Blob upload failed: {e}")
            state["error_message"] = f"Blob upload failed: {str(e)}"
            state["error_node"] = 8
            state["el_m8_passed"] = False
            return state

        # ── Store in state ──────────────────────────────────────────────
        state["intermediate_schema"] = intermediate_schema
        state["output_json_url"] = output_json_url
        state["output_csv_url"] = output_csv_url
        state["output_sql_url"] = output_sql_url
        state["migration_report_url"] = migration_report_url

        state["current_step"] = 8
        state["event_log"].append({
            "timestamp": datetime.utcnow().isoformat(),
            "event": "node_complete",
            "node": 8,
            "detail": f"Generated IntermediateSchema with {sum(len(v) for v in intermediate_schema.get('entities', {}).values())} entities"
        })

        logger.info(f"[Node 8] Complete: IntermediateSchema ready for svc-ingestion")
        return state

    except Exception as e:
        logger.exception(f"[Node 8] Unhandled exception: {e}")
        state["error_message"] = str(e)
        state["error_node"] = 8
        state["error_timestamp"] = datetime.utcnow()
        state["status"] = "failed"
        return state


def _upload_to_blob(
    blob_name: str,
    data: str | bytes,
    content_type: str,
    connection_string: str,
    container_name: str
) -> None:
    """Upload file to Azure Blob Storage.

    Args:
        blob_name: Path in blob (e.g., migrations/{id}/output.json)
        data: File contents (string or bytes)
        content_type: MIME type
        connection_string: Azure connection string
        container_name: Blob container name
    """
    try:
        blob_client = BlobClient.from_connection_string(
            conn_str=connection_string,
            container_name=container_name,
            blob_name=blob_name
        )

        # Convert string to bytes if needed
        if isinstance(data, str):
            data = data.encode('utf-8')

        blob_client.upload_blob(data, overwrite=True)
        logger.debug(f"[Node 8] Uploaded blob: {blob_name}")

    except Exception as e:
        logger.exception(f"[Node 8] Failed to upload blob {blob_name}: {e}")
        raise
