"""Node 7.5: Artifacts Review Gate — Review generated artifacts and set new schema name.

Pauses after schema_output_node so the user can:
  1. Download and inspect the generated JSON config, CSV mappings, and SQL DDL preview.
  2. Enter a custom name for the new PostgreSQL schema before it is created.

Resumes with Command(resume={"new_schema_name": "user_chosen_name"}).
The sanitized name is stored in state["new_schema_name"] which schema_write_node reads.
"""

import re
from datetime import datetime

from langgraph.types import interrupt

from ..schema_state import SchemaMappingState
from .schema_db_writer import schema_write_gate_payload_auto, schema_clear_gate_payload_auto

from cafm_shared.logging import get_logger
logger = get_logger(__name__)


async def schema_artifacts_review_node(state: SchemaMappingState) -> SchemaMappingState:
    """
    Node 7.5: Artifacts Review Gate.

    Pauses execution so the user can review the generated artifacts and set
    a custom PostgreSQL schema name before the write node creates it.

    Resume payload expected from frontend:
        {"new_schema_name": "my_custom_schema_name"}
    """
    _node_started_at = datetime.utcnow()
    schema_mapping_id = state.get("schema_mapping_id")
    external_cmms_name = state.get("external_cmms_name", "")
    summary = state.get("final_summary") or {}

    # Suggest a default name so the UI can pre-fill the input
    _slug = re.sub(r"[^a-z0-9]+", "_", external_cmms_name.lower()).strip("_")[:30]
    _ts = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    suggested_schema_name = f"plenum_cafm_{_slug}_{_ts}"[:63]

    payload = {
        "gate": "artifacts_review",
        "schema_mapping_id": schema_mapping_id,
        "suggested_schema_name": suggested_schema_name,
        "external_cmms_name": external_cmms_name,
        "output_json_url": state.get("output_json_url") or "",
        "output_csv_url": state.get("output_csv_url") or "",
        "output_sql_url": state.get("output_sql_url") or "",
        "summary": {
            "canonical_fields_count": summary.get("canonical_fields_count", 0),
            "total_source_fields": summary.get("total_source_fields", 0),
            "tier1_auto_mapped": summary.get("tier1_auto_mapped", 0),
            "tier2_auto_mapped": summary.get("tier2_auto_mapped", 0),
            "tier2_flagged": summary.get("tier2_flagged", 0),
            "unmappable": summary.get("unmappable", 0),
            "mapping_coverage_pct": round(float(summary.get("mapping_coverage_pct") or 0), 1),
            "detected_fk_count": summary.get("detected_fk_count", 0),
            "max_hierarchy_depth": summary.get("max_hierarchy_depth", 0),
            "junction_table_count": summary.get("junction_table_count", 0),
        },
        "instructions": (
            "Review the generated artifacts below. "
            "Enter a name for the new PostgreSQL schema, then click Confirm to write to the database."
        ),
        "action_required": "Set schema name and confirm to proceed with database write.",
    }

    logger.info(
        f"[Node 9] Artifacts review gate — schema_mapping={schema_mapping_id} "
        f"suggested_name={suggested_schema_name}"
    )

    # Write gate payload — frontend polls /status and sees awaiting_review + artifacts_review
    if schema_mapping_id:
        await schema_write_gate_payload_auto(schema_mapping_id, "artifacts_review", payload)

    # Pause for user input
    decisions = interrupt(payload)

    # Resume — clear gate payload and read user-provided schema name
    if schema_mapping_id:
        await schema_clear_gate_payload_auto(schema_mapping_id)

    user_schema_name = ""
    if isinstance(decisions, dict):
        user_schema_name = (decisions.get("new_schema_name") or "").strip()

    if user_schema_name:
        # Sanitize: lowercase, replace non-alphanumeric with underscores, max 63 chars
        safe = re.sub(r"[^a-z0-9_]", "_", user_schema_name.lower()).strip("_")[:63]
        if not safe:
            safe = suggested_schema_name
        state["new_schema_name"] = safe
        logger.info(
            f"[Node 9] Schema name set by user: '{user_schema_name}' → sanitized: '{safe}'"
        )
    else:
        # Fall back to the suggested name generated above
        state["new_schema_name"] = suggested_schema_name
        logger.info(
            f"[Node 9] No schema name provided — using suggested: '{suggested_schema_name}'"
        )

    final_name = state.get("new_schema_name", suggested_schema_name)
    if schema_mapping_id:
        try:
            from .schema_db_writer import schema_append_node_log_auto
            await schema_append_node_log_auto(
                schema_mapping_id, 9, "Artifacts Review Gate", _node_started_at, datetime.utcnow(),
                output={"new_schema_name": final_name, "external_cmms_name": external_cmms_name},
                logs=[
                    f"[Node 9] Artifacts review gate completed",
                    f"[Node 9] Schema name confirmed: '{final_name}'",
                ],
            )
        except Exception as _log_exc:
            logger.warning(f"[Node 9] Failed to write node log (non-fatal): {_log_exc}")

    return state
