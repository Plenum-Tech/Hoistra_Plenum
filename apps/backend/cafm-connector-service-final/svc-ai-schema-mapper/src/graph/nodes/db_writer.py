"""Shared DB write utilities for all migration graph nodes.

Every node calls these helpers so the frontend can track live progress by
polling GET /api/migration/{id}/status.

Each function opens its own fresh session — nodes must NOT pass a session
through LangGraph state because AsyncSession cannot survive checkpoint
serialization by the PostgresSaver.

Pattern per regular node:
    migration_id = state.get("migration_id")
    if migration_id:
        from .db_writer import update_node_progress
        await update_node_progress(migration_id, "2_deterministic_mapping")

Pattern per gate node (before interrupt, then after resume):
    migration_id = state.get("migration_id")
    if migration_id:
        from .db_writer import write_gate_payload
        await write_gate_payload(migration_id, "pre_semantic", payload)
    decisions = interrupt(payload)
    if migration_id:
        from .db_writer import clear_gate_payload
        await clear_gate_payload(migration_id)

Two kinds of write live here. Progress beats (update_node_progress, report_progress_pct) are
cosmetic: one attempt, a warning on failure. The writes that decide whether the run can carry on
— the step pause and the gate payload — must land: they are retried, and when the database stays
away they raise StatusWriteFailed so the worker marks the run failed and it can be retried. On
30 Sep 2026 one refused connection to Azure Postgres ("[Errno 111] Connect call failed", one
second before the next write succeeded) was logged as non-fatal after file ingestion; the graph
then parked itself waiting for /advance while the job still said "running" with no gate — a
state no button recovers, and the card polled "running" for half an hour.
"""

import asyncio
import logging
from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.migration import MigrationJob

from cafm_shared.logging import get_logger
logger = get_logger(__name__)


class StatusWriteFailed(RuntimeError):
    """A write that decides whether the run can continue could not be made to land."""


# Waits between attempts (~15 s in all): long enough for a dropped connection or a busy
# Azure endpoint to come back, short enough that a truly absent database fails the run
# before anyone wonders why it is still "running".
_RETRY_DELAYS_S: tuple[float, ...] = (0.5, 1.0, 2.0, 4.0, 8.0)


def _get_session_factory():
    """Get the app-level session factory (lazy import to avoid circular imports)."""
    from ...db import get_async_session_factory
    return get_async_session_factory()


async def _write_with_retries(label: str, migration_id: str, statement) -> None:
    """Execute one UPDATE on a fresh session, retrying over _RETRY_DELAYS_S.

    Raises StatusWriteFailed, carrying the last error, when every attempt fails.
    """
    last: Optional[Exception] = None
    attempt = 0
    for attempt, delay in enumerate((0.0,) + tuple(_RETRY_DELAYS_S), start=1):
        if delay:
            await asyncio.sleep(delay)
        try:
            session_factory = _get_session_factory()
            async with session_factory() as session:
                await session.execute(statement)
                await session.commit()
            if attempt > 1:
                logger.info(
                    f"[db_writer] {label} landed on attempt {attempt} migration={migration_id}"
                )
            return
        except Exception as e:  # any failure here is a reason to try again
            last = e
            logger.warning(
                f"[db_writer] {label} attempt {attempt} failed migration={migration_id}: {e}"
            )
    raise StatusWriteFailed(
        f"{label} could not be written for migration {migration_id} "
        f"after {attempt} attempts: {last}"
    ) from last


async def update_node_progress(
    migration_id: str,
    step_name: str,
    status: str = "running",
    **extra_fields: Any,
) -> None:
    """
    Write node completion to migration_jobs.

    Args:
        migration_id:  Migration UUID string.
        step_name:     Human-readable step label, e.g. "2_deterministic_mapping".
                       Frontend displays this as the current pipeline stage.
        status:        Job status string (default "running").
        **extra_fields: Any additional MigrationJob columns to update
                        (e.g. t1_mapped_count=42).
    """
    try:
        session_factory = _get_session_factory()
        async with session_factory() as session:
            values: dict[str, Any] = {
                "current_step": step_name,
                "status": status,
                **extra_fields,
            }
            await session.execute(
                update(MigrationJob)
                .where(MigrationJob.id == UUID(migration_id))
                .values(**values)
            )
            await session.commit()
        logger.debug(f"[db_writer] step={step_name} status={status} migration={migration_id}")
    except Exception as e:
        logger.warning(f"[db_writer] update_node_progress failed (non-fatal): {e}")


async def report_progress_pct(migration_id: str, pct: float) -> None:
    """Write ONLY progress_pct — the heartbeat of a long step (graph/progress_beat.py).

    Status, step and gate are left alone: a beat that landed after the step's own pause must
    not turn "paused" back into "running". Non-fatal, like every progress beat here.
    """
    try:
        session_factory = _get_session_factory()
        async with session_factory() as session:
            await session.execute(
                update(MigrationJob)
                .where(MigrationJob.id == UUID(migration_id))
                .values(progress_pct=float(pct))
            )
            await session.commit()
    except Exception as e:
        logger.warning(f"[db_writer] report_progress_pct failed (non-fatal): {e}")


async def write_gate_payload(
    migration_id: str,
    gate_type: str,
    payload: dict[str, Any],
) -> None:
    """
    Write HITL gate payload to DB and flip status to 'awaiting_review'.

    Called by gate nodes BEFORE interrupt() so the frontend can read the
    payload via GET /status → pending_gate_payload and render the review UI.

    gate_type values:
        "pre_semantic"   — pre-semantic deterministic review gate
        "field_mapping"  — Node 4 GATE 1 (semantic flagged review)
        "hierarchy"      — Node 7 GATE 2 (hierarchy confirmation)
        "write"          — Node 9 GATE 3 (final approval)

    Must land: without it the graph waits for an answer nobody can give.
    Raises StatusWriteFailed when the database stays away.
    """
    await _write_with_retries(
        f"write_gate_payload({gate_type})",
        migration_id,
        update(MigrationJob)
        .where(MigrationJob.id == UUID(migration_id))
        .values(
            status="awaiting_review",
            pending_gate_type=gate_type,
            pending_gate_payload=payload,
        ),
    )
    logger.info(
        f"[db_writer] Gate payload written: gate={gate_type} migration={migration_id}"
    )
    # Feature 4 — sync the durable per-run Activity entry AT THE PAUSE. The node-log
    # chokepoint only fires on node completion (post-resume), so without this the run
    # entry wouldn't show this gate's Mapping Decisions or flip to pending_human_input
    # while the user is reviewing. Non-fatal. NOTE: on RESUME, LangGraph re-executes the
    # gate node from the top, so this runs AGAIN (status→awaiting_review) right before
    # clear_gate_payload flips it back to running — watch for this write/clear pair in logs.
    try:
        from .schema_db_writer import _sync_run_activity

        synced = await _sync_run_activity(migration_id, caller="write_gate_payload")
        logger.info(
            f"[db_writer] write_gate_payload run-activity sync → status={synced} "
            f"gate={gate_type} migration={migration_id}"
        )
    except Exception as _sync_err:
        logger.warning(
            f"[db_writer] write_gate_payload run-activity sync FAILED "
            f"migration={migration_id}: {_sync_err}",
            exc_info=True,
        )


async def clear_gate_payload(
    migration_id: str,
) -> None:
    """
    Clear gate payload and flip status back to 'running' after graph resumes.

    Called by gate nodes immediately after interrupt() returns with decisions.

    Retried, but never fatal: by now the answer has been applied and the graph is moving on,
    so failing the run here would lose that answer. The next status write repairs the row.
    """
    try:
        await _write_with_retries(
            "clear_gate_payload",
            migration_id,
            update(MigrationJob)
            .where(MigrationJob.id == UUID(migration_id))
            .values(
                status="running",
                pending_gate_type=None,
                pending_gate_payload=None,
            ),
        )
    except StatusWriteFailed as e:
        logger.error(f"[db_writer] {e} — the row still shows the answered gate until the next write")
        return
    logger.info(
        f"[db_writer] clear_gate_payload committed status=running, pending_gate cleared "
        f"migration={migration_id} — now syncing run-activity to running"
    )
    # Feature 4 — sync the run entry back to "running" the moment the gate resolves, so
    # the audit row shows pending_human_input → running crisply (not only on the next
    # node completion). Non-fatal, but log loudly: this IS the running transition, so a
    # silent failure here is exactly the "stuck at pending" symptom.
    try:
        from .schema_db_writer import _sync_run_activity

        synced = await _sync_run_activity(migration_id, caller="clear_gate_payload")
        logger.info(
            f"[db_writer] clear_gate_payload run-activity sync → status={synced} "
            f"migration={migration_id}"
        )
    except Exception as _sync_err:
        logger.warning(
            f"[db_writer] clear_gate_payload run-activity sync FAILED (row stays pending!) "
            f"migration={migration_id}: {_sync_err}",
            exc_info=True,
        )


async def write_step_pause(
    migration_id: str,
    step_key: str,
    summary: dict[str, Any],
) -> None:
    """
    Write step summary to DB and flip status to 'step_paused'.

    Called by non-gate nodes at the end of their execution.  The LangGraph
    interrupt_after mechanism then pauses the graph until the user clicks
    "Next Node" in the UI (which POSTs to /api/migration/{id}/advance).

    step_key format:  "step_1_ingest", "step_2_deterministic_mapping", …
    summary:          node-specific dict displayed in the Streamlit step card.

    Must land: /advance refuses anything that is not step_paused, so a run whose pause was
    never recorded cannot be continued. Raises StatusWriteFailed when the database stays away.
    """
    await _write_with_retries(
        f"write_step_pause({step_key})",
        migration_id,
        update(MigrationJob)
        .where(MigrationJob.id == UUID(migration_id))
        .values(
            status="step_paused",
            pending_gate_type=step_key,
            pending_gate_payload=summary,
        ),
    )
    logger.info(
        f"[db_writer] Step pause written: step={step_key} migration={migration_id}"
    )
    # Feature 4 — sync the durable per-run Activity entry at the step pause too (progressive
    # status while the user reviews a node's output). Non-fatal.
    try:
        from .schema_db_writer import _sync_run_activity

        synced = await _sync_run_activity(migration_id, caller="write_step_pause")
        logger.info(
            f"[db_writer] write_step_pause run-activity sync → status={synced} "
            f"step={step_key} migration={migration_id}"
        )
    except Exception as _sync_err:
        logger.warning(
            f"[db_writer] write_step_pause run-activity sync FAILED "
            f"migration={migration_id}: {_sync_err}",
            exc_info=True,
        )


async def write_error(
    migration_id: str,
    error_message: str,
    error_node: Optional[int] = None,
    status: str = "failed",
) -> None:
    """Write error state to migration_jobs.

    Args:
        status: "failed" for general failures, "ddl_failed" for DDL rollbacks
                (DDL rollbacks expose the failing SQL to the frontend so the
                user can correct their field definitions and re-submit via /retry-ddl).

    Retried: a failure that never reaches the row looks exactly like a run still working.
    """
    values: dict[str, Any] = {
        "status": status,
        "error_message": error_message[:2000],
        "error_timestamp": datetime.utcnow(),
    }
    if error_node is not None:
        values["current_step"] = f"error_node_{error_node}"
    try:
        await _write_with_retries(
            f"write_error({status})",
            migration_id,
            update(MigrationJob)
            .where(MigrationJob.id == UUID(migration_id))
            .values(**values),
        )
    except StatusWriteFailed as e:
        logger.error(f"[db_writer] {e}")
