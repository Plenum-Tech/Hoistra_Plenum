"""Helper service for tracking job progress in database.

Used by both 9-node migration pipeline and 6-node schema mapping pipeline
to update progress after each node completes.
"""

import logging
from datetime import datetime
from uuid import UUID
from typing import Optional, Dict, Any, List

from sqlalchemy import update, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.migration import MigrationJob, SchemaMappingJob, SchemaMappingFieldMapping
from ..graph.event_enrich import enrich_node_entry  # F4-2 — trigger/outcome/actor enrichment

from cafm_shared.logging import get_logger
logger = get_logger(__name__)


async def update_migration_job_progress(
    session: AsyncSession,
    migration_id: UUID,
    status: str,
    current_step: Optional[str] = None,
    progress_pct: Optional[float] = None,
    t1_count: Optional[int] = None,
    t2_auto_count: Optional[int] = None,
    t2_human_count: Optional[int] = None,
    unmapped_count: Optional[int] = None,
    error_message: Optional[str] = None,
) -> None:
    """
    Update a MigrationJob record after node completion.

    Used by 9-node migration pipeline.

    Args:
        session: AsyncSession
        migration_id: UUID of migration job
        status: Current status (e.g., "running", "awaiting_review", "complete", "error")
        current_step: Name of current node (e.g., "deterministic_mapper")
        progress_pct: Progress percentage (0-100)
        t1_count: Tier 1 mapped count
        t2_auto_count: Tier 2 auto-mapped count
        t2_human_count: Tier 2 flagged for human review count
        unmapped_count: Unmapped field count
        error_message: If status is "error", error message
    """
    try:
        update_data = {
            "status": status,
            "last_updated_at": datetime.utcnow(),
        }

        if current_step:
            update_data["current_step"] = current_step
        if progress_pct is not None:
            update_data["progress_pct"] = progress_pct
        if t1_count is not None:
            update_data["t1_mapped_count"] = t1_count
        if t2_auto_count is not None:
            update_data["t2_auto_count"] = t2_auto_count
        if t2_human_count is not None:
            update_data["t2_human_count"] = t2_human_count
        if unmapped_count is not None:
            update_data["unmapped_count"] = unmapped_count
        if error_message:
            update_data["error_message"] = error_message
        if status == "complete":
            update_data["completed_at"] = datetime.utcnow()

        stmt = update(MigrationJob).where(MigrationJob.id == migration_id).values(**update_data)
        await session.execute(stmt)
        await session.commit()

        logger.info(f"Updated MigrationJob {migration_id}: {status} ({progress_pct}%)")

    except Exception as e:
        logger.error(f"Failed to update MigrationJob {migration_id}: {e}")
        await session.rollback()
        raise


async def update_schema_mapping_job_progress(
    session: AsyncSession,
    schema_mapping_id: UUID,
    status: str,
    current_node: Optional[int] = None,
    progress_pct: Optional[float] = None,
    total_tables: Optional[int] = None,
    total_fields: Optional[int] = None,
    tier1_mapped: Optional[int] = None,
    tier2_auto_mapped: Optional[int] = None,
    tier2_flagged: Optional[int] = None,
    unmapped: Optional[int] = None,
    detected_fk_count: Optional[int] = None,
    hierarchy_depth: Optional[int] = None,
    implicit_hierarchy_count: Optional[int] = None,
    final_mapping_config: Optional[Dict[str, Any]] = None,
    final_summary: Optional[Dict[str, Any]] = None,
    mapping_coverage_pct: Optional[float] = None,
    node_state_json: Optional[Dict[str, Any]] = None,
    error_message: Optional[str] = None,
) -> None:
    """
    Update a SchemaMappingJob record after node completion.

    Used by 6-node schema mapping pipeline.

    Args:
        session: AsyncSession
        schema_mapping_id: UUID of schema mapping job
        status: Current status (ingest|deterministic|semantic|hierarchy|verify|output|complete|error)
        current_node: Current node number (1-6)
        progress_pct: Progress percentage (0-100)
        total_tables: Total tables in schema
        total_fields: Total fields across all tables
        tier1_mapped: Tier 1 mapped count
        tier2_auto_mapped: Tier 2 auto-mapped count
        tier2_flagged: Tier 2 flagged for review count
        unmapped: Unmapped field count
        detected_fk_count: Number of FKs detected
        hierarchy_depth: Maximum hierarchy tree depth
        implicit_hierarchy_count: Count of implicit hierarchies
        final_mapping_config: Complete output config (set when status="complete")
        final_summary: Summary stats (set when status="complete")
        mapping_coverage_pct: Percentage of fields mapped
        node_state_json: Full SchemaMappingState for checkpointing
        error_message: If status is "error", error message
    """
    try:
        update_data = {
            "status": status,
            "last_updated_at": datetime.utcnow(),
        }

        if current_node is not None:
            update_data["current_node"] = current_node
        if progress_pct is not None:
            update_data["progress_pct"] = progress_pct
        if total_tables is not None:
            update_data["total_tables"] = total_tables
        if total_fields is not None:
            update_data["total_fields"] = total_fields
        if tier1_mapped is not None:
            update_data["tier1_mapped"] = tier1_mapped
        if tier2_auto_mapped is not None:
            update_data["tier2_auto_mapped"] = tier2_auto_mapped
        if tier2_flagged is not None:
            update_data["tier2_flagged"] = tier2_flagged
        if unmapped is not None:
            update_data["unmapped"] = unmapped
        if detected_fk_count is not None:
            update_data["detected_fk_count"] = detected_fk_count
        if hierarchy_depth is not None:
            update_data["hierarchy_depth"] = hierarchy_depth
        if implicit_hierarchy_count is not None:
            update_data["implicit_hierarchy_count"] = implicit_hierarchy_count
        if final_mapping_config:
            update_data["final_mapping_config"] = final_mapping_config
        if final_summary:
            update_data["final_summary"] = final_summary
        if mapping_coverage_pct is not None:
            update_data["mapping_coverage_pct"] = mapping_coverage_pct
        if node_state_json:
            update_data["node_state_json"] = node_state_json
        if error_message:
            update_data["error_message"] = error_message
        if status == "complete":
            update_data["completed_at"] = datetime.utcnow()

        stmt = update(SchemaMappingJob).where(SchemaMappingJob.id == schema_mapping_id).values(**update_data)
        await session.execute(stmt)
        await session.commit()

        logger.info(f"Updated SchemaMappingJob {schema_mapping_id}: {status} (Node {current_node}, {progress_pct}%)")

    except Exception as e:
        logger.error(f"Failed to update SchemaMappingJob {schema_mapping_id}: {e}")
        await session.rollback()
        raise


async def append_node_log(
    session: AsyncSession,
    schema_mapping_id: UUID,
    node_id: int,
    node_name: str,
    started_at: datetime,
    completed_at: datetime,
    output: Dict[str, Any],
    logs: List[str],
    *,
    trigger: str = "query",
    outcome: Optional[str] = None,
    actor: str = "system",
    status: str = "complete",
) -> None:
    """
    Append a completed-node entry to SchemaMappingJob.node_logs.

    Reads current node_logs array, appends the new entry, then writes it back.
    Called by each schema mapper node after it finishes. The entry is enriched with the
    Activity-Log dimensions (trigger / one-line outcome / status / actor) — F4-2.
    """
    try:
        result = await session.execute(
            select(SchemaMappingJob.node_logs).where(SchemaMappingJob.id == schema_mapping_id)
        )
        row = result.one_or_none()
        existing: list = (row[0] or []) if row else []

        duration_ms = int((completed_at - started_at).total_seconds() * 1000)
        entry = {
            "node_id": node_id,
            "node_name": node_name,
            "status": status,
            "started_at": started_at.isoformat(),
            "completed_at": completed_at.isoformat(),
            "duration_ms": duration_ms,
            "output": output,
            "logs": logs,
        }
        enrich_node_entry(entry, node_name=node_name, trigger=trigger, outcome=outcome, actor=actor)
        existing.append(entry)

        await session.execute(
            update(SchemaMappingJob)
            .where(SchemaMappingJob.id == schema_mapping_id)
            .values(node_logs=existing)
        )
        await session.commit()
        logger.debug(f"[append_node_log] node={node_id} schema_mapping={schema_mapping_id}")
    except Exception as e:
        logger.warning(f"[append_node_log] failed (non-fatal): {e}")
        try:
            await session.rollback()
        except Exception:
            pass


async def append_migration_node_log(
    session: AsyncSession,
    migration_id: UUID,
    node_id: int,
    node_name: str,
    started_at: datetime,
    completed_at: datetime,
    output: Dict[str, Any],
    logs: List[str],
    *,
    trigger: str = "query",
    outcome: Optional[str] = None,
    actor: str = "system",
    status: str = "complete",
) -> None:
    """
    Append a completed-node entry to MigrationJob.node_logs.

    Called by each ingestor node after it finishes. The entry is enriched with the
    Activity-Log dimensions (trigger / one-line outcome / status / actor) — F4-2.
    """
    try:
        result = await session.execute(
            select(MigrationJob.node_logs).where(MigrationJob.id == migration_id)
        )
        row = result.one_or_none()
        existing: list = (row[0] or []) if row else []

        duration_ms = int((completed_at - started_at).total_seconds() * 1000)
        entry = {
            "node_id": node_id,
            "node_name": node_name,
            "status": status,
            "started_at": started_at.isoformat(),
            "completed_at": completed_at.isoformat(),
            "duration_ms": duration_ms,
            "output": output,
            "logs": logs,
        }
        enrich_node_entry(entry, node_name=node_name, trigger=trigger, outcome=outcome, actor=actor)
        existing.append(entry)

        await session.execute(
            update(MigrationJob)
            .where(MigrationJob.id == migration_id)
            .values(node_logs=existing)
        )
        await session.commit()
        logger.debug(f"[append_migration_node_log] node={node_id} migration={migration_id}")
    except Exception as e:
        logger.warning(f"[append_migration_node_log] failed (non-fatal): {e}")
        try:
            await session.rollback()
        except Exception:
            pass


async def log_field_mapping(
    session: AsyncSession,
    schema_mapping_id: UUID,
    source_field: str,
    source_table: str,
    target_field: str,
    confidence: float,
    tier: str,
    rationale: str,
) -> None:
    """
    Log a field mapping to the audit trail.

    Args:
        session: AsyncSession
        schema_mapping_id: UUID of schema mapping job
        source_field: Source field name
        source_table: Source table name
        target_field: Target canonical field name
        confidence: Confidence score (0-1)
        tier: Mapping tier (T1_exact, T1_alias, T1_regex, T2_semantic, unmapped)
        rationale: Why this mapping was chosen
    """
    try:
        mapping = SchemaMappingFieldMapping(
            schema_mapping_id=schema_mapping_id,
            source_field=source_field,
            source_table=source_table,
            target_field=target_field,
            confidence=confidence,
            tier=tier,
            rationale=rationale,
        )
        session.add(mapping)
        await session.commit()

        logger.debug(f"Logged mapping: {source_field} → {target_field} ({tier}, {confidence:.2f})")

    except Exception as e:
        logger.error(f"Failed to log field mapping: {e}")
        await session.rollback()
        raise
