"""Node 6: Verify Hierarchy — HITL gate for hierarchy approval/correction.

This is an INTERRUPT node:
1. Build hierarchy_review_payload (what user sees)
2. Write gate payload to DB so frontend can render the review UI
3. Call interrupt() to pause execution
4. User submits corrections via /api/schema-mapping/{id}/approve
5. Resume with user decisions via Command(resume=decisions)
6. Apply corrections to state and proceed to Node 7 (output)
"""

import logging
from datetime import datetime

from langgraph.types import interrupt

from ..schema_state import (
    ForeignKeyDetection,
    HorizontalRelationship,
    JunctionTable,
    SchemaMappingState,
)

from cafm_shared.logging import get_logger
logger = get_logger(__name__)


async def schema_verify_hierarchy_node(state: SchemaMappingState) -> SchemaMappingState:
    """
    Node 6: Verify and allow user to approve/correct detected hierarchies.

    This is a HITL gate — pauses execution via interrupt() until the user
    submits decisions through /api/schema-mapping/{id}/approve.
    """

    schema_mapping_id = state.get("schema_mapping_id")
    detected_fks = state.get("detected_foreign_keys", [])
    detected_hierarchies = state.get("detected_hierarchies", [])
    junction_tables = state.get("junction_tables", [])
    horizontal_relationships = state.get("horizontal_relationships", [])
    isolated_tables = state.get("isolated_tables", [])
    implicit_hierarchies = state.get("implicit_hierarchies", {})
    db_session = state.get("db_session")

    logger.info(
        f"[Node 6] HITL Gate: schema_mapping_id={schema_mapping_id}, "
        f"{len(detected_fks)} FKs, {len(detected_hierarchies)} hierarchies, "
        f"{len(junction_tables)} junction tables, "
        f"{len(horizontal_relationships)} horizontal, {len(isolated_tables)} isolated"
    )

    try:
        # ── Step 1: Build review payload ──────────────────────────────
        review_payload = _build_hierarchy_review_payload(
            detected_fks,
            detected_hierarchies,
            junction_tables,
            horizontal_relationships,
            isolated_tables,
            implicit_hierarchies,
            schema_mapping_id,
        )

        state["hierarchy_review_payload"] = review_payload

        # ── Step 2: Write step-pause to DB so frontend can render ────
        # Uses auto-session (no db_session in graph state)
        if schema_mapping_id:
            from .schema_db_writer import schema_write_step_pause_auto
            await schema_write_step_pause_auto(
                schema_mapping_id, 6, "step_6_verify_hierarchy", review_payload
            )

        logger.info(
            f"[Node 6] Interrupting for hierarchy approval "
            f"(schema_mapping_id={schema_mapping_id})"
        )

        _gate_started_at = datetime.utcnow()

        # ── Step 3: Pause for user input ──────────────────────────────
        # Resumed by advance endpoint with Command(resume={"approved_foreign_keys": [], ...})
        user_corrections = interrupt(review_payload)

        # ── Step 5: Apply user corrections ───────────────────────────
        approved_fks = user_corrections.get("approved_foreign_keys", [])
        rejected_fks = user_corrections.get("rejected_foreign_keys", [])
        hierarchy_corrections = user_corrections.get("hierarchy_corrections")
        reviewer_notes = user_corrections.get("reviewer_notes", "")

        logger.info(
            f"[Node 6] Resumed: {len(approved_fks)} approved, "
            f"{len(rejected_fks)} rejected FKs"
        )

        # Remove rejected FKs
        if rejected_fks:
            rejected_fk_set = {
                (fk["source_table"], fk["source_column"], fk["target_table"])
                for fk in rejected_fks
            }
            detected_fks = [
                fk
                for fk in detected_fks
                if (fk["source_table"], fk["source_column"], fk["target_table"])
                not in rejected_fk_set
            ]
            logger.info(f"[Node 6] Removed {len(rejected_fks)} rejected FKs")

        # Mark approved FKs as user_confirmed
        for fk in detected_fks:
            fk_key = (fk["source_table"], fk["source_column"], fk["target_table"])
            for approved in approved_fks:
                approved_key = (
                    approved["source_table"],
                    approved["source_column"],
                    approved["target_table"],
                )
                if fk_key == approved_key:
                    fk["user_confirmed"] = True
                    fk["confirmed_at"] = datetime.utcnow()

        if hierarchy_corrections:
            state["hierarchy_user_modified"] = True

        state["detected_foreign_keys"] = detected_fks
        state["user_hierarchy_corrections"] = user_corrections
        state["hierarchy_approved"] = True
        state["hierarchy_approved_at"] = datetime.utcnow()
        state["status"] = "output"
        state["notes"] = state.get("notes", []) + [
            f"User hierarchy verification: {len(approved_fks)} approved, "
            f"{len(rejected_fks)} rejected. {reviewer_notes or 'No notes'}"
        ]

        logger.info(f"[Node 6] ✓ Hierarchy verification approved")

        # ── Step 6: Record node completion ────────────────────────────
        if db_session and schema_mapping_id:
            from .schema_db_writer import schema_update_node_progress
            await schema_update_node_progress(
                db_session, schema_mapping_id, 6,
                progress_pct=80.0,
            )

        if schema_mapping_id:
            from .schema_db_writer import schema_append_node_log_auto
            await schema_append_node_log_auto(
                schema_mapping_id, 7, "Gate 3: Hierarchy Verification", _gate_started_at, datetime.utcnow(),
                output={"approved_fks": len(approved_fks), "rejected_fks": len(rejected_fks),
                        "hierarchy_corrections": bool(hierarchy_corrections),
                        "reviewer_notes": reviewer_notes},
                logs=[f"Gate opened for hierarchy review",
                      f"{len(approved_fks)} FKs approved by user",
                      f"{len(rejected_fks)} FKs rejected",
                      f"Hierarchy corrections applied: {bool(hierarchy_corrections)}",
                      f"Notes: {reviewer_notes or 'None'}"],
            )

        return state

    except Exception as e:
        logger.exception(f"[Node 6] ✗ Error: {e}")
        state["status"] = "error"
        state["error_message"] = f"Hierarchy verification failed: {str(e)}"
        return state


# ────────────────────────────────────────────────────────────────────────────
# Helper Functions
# ────────────────────────────────────────────────────────────────────────────


def _build_hierarchy_review_payload(
    detected_fks: list[ForeignKeyDetection],
    detected_hierarchies: list,
    junction_tables: list[JunctionTable],
    horizontal_relationships: list[HorizontalRelationship],
    isolated_tables: list[str],
    implicit_hierarchies: dict,
    schema_mapping_id: str,
) -> dict:
    """Build the full graph review payload shown to the user."""

    max_depth = max((_calculate_tree_depth(r) for r in detected_hierarchies), default=0)
    canonical_backed = sum(1 for fk in detected_fks if fk.get("canonical_backed"))
    low_confidence = [fk for fk in detected_fks if fk.get("confidence", 0) < 0.9]

    payload = {
        "schema_mapping_id": schema_mapping_id,
        "timestamp": datetime.utcnow().isoformat(),
        # All FK relationships (parent-child direction)
        "detected_foreign_keys": _serialize_fks(detected_fks),
        # Forest — one entry per independent hierarchy tree
        "hierarchy_forest": [_serialize_hierarchy_tree(r) for r in detected_hierarchies],
        # Many-to-many bridge tables
        "junction_tables": [
            {
                "table_name": jt.get("table_name"),
                "left_table": jt.get("left_table"),
                "left_fk_column": jt.get("left_fk_column"),
                "right_table": jt.get("right_table"),
                "right_fk_column": jt.get("right_fk_column"),
                "confidence": jt.get("confidence"),
                "reasoning": jt.get("reasoning"),
            }
            for jt in junction_tables
        ],
        # Peer / lateral relationships at the same hierarchy level
        "horizontal_relationships": [
            {
                "source_table": hr.get("source_table"),
                "target_table": hr.get("target_table"),
                "relationship_type": hr.get("relationship_type"),
                "via_table": hr.get("via_table"),
                "shared_parent": hr.get("shared_parent"),
                "source_fk_column": hr.get("source_fk_column"),
                "confidence": hr.get("confidence"),
                "reasoning": hr.get("reasoning"),
            }
            for hr in horizontal_relationships
        ],
        # Tables with no FK connections
        "isolated_tables": isolated_tables,
        "implicit_hierarchies": implicit_hierarchies,
        "summary": {
            "total_fks": len(detected_fks),
            "canonical_backed_fks": canonical_backed,
            "hierarchy_count": len(detected_hierarchies),
            "hierarchy_roots": [r.get("table_name") for r in detected_hierarchies],
            "max_hierarchy_depth": max_depth,
            "junction_table_count": len(junction_tables),
            "horizontal_relationship_count": len(horizontal_relationships),
            "isolated_table_count": len(isolated_tables),
            "total_implicit": len(implicit_hierarchies),
        },
        "action_required": "review_fks" if low_confidence else "approve_all",
        "instructions": (
            "Review the detected schema graph: vertical parent-child hierarchies, "
            "many-to-many junction tables, and horizontal peer relationships. "
            "Approve relationships that are correct or reject incorrect ones. "
            "You may also correct table placements within the hierarchy."
        ),
    }

    return payload


def _serialize_fks(detected_fks: list[ForeignKeyDetection]) -> list[dict]:
    return [
        {
            "source_table": fk.get("source_table"),
            "source_column": fk.get("source_column"),
            "target_table": fk.get("target_table"),
            "target_column": fk.get("target_column"),
            "relationship_type": fk.get("relationship_type"),
            "confidence": fk.get("confidence"),
            "reasoning": fk.get("reasoning"),
            "canonical_backed": fk.get("canonical_backed", False),
            "canonical_target_table": fk.get("canonical_target_table"),
            "user_confirmed": fk.get("user_confirmed", False),
        }
        for fk in detected_fks
    ]


def _serialize_hierarchy_tree(node) -> dict:
    if not node:
        return {}
    children = node.get("children", [])
    result = {
        "table_name": node.get("table_name"),
        "primary_key": node.get("primary_key_field"),
        "parent_fk_field": node.get("parent_fk_field"),
        "level": node.get("level", 0),
        "canonical_table": node.get("canonical_table"),
        "self_referential_column": node.get("self_referential_column"),
        "children_count": len(children),
        "children": [_serialize_hierarchy_tree(child) for child in children],
    }
    return {k: v for k, v in result.items() if v is not None}


def _calculate_tree_depth(node) -> int:
    if not node:
        return 0
    children = node.get("children", [])
    if not children:
        return 1
    return 1 + max(_calculate_tree_depth(child) for child in children)
