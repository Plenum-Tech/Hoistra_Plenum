"""ARQ background worker for svc-AI-Schema-Mapper.

Two tasks:
  run_migration    — start a fresh migration run
  resume_migration — resume a graph that was paused at a HITL gate

Interrupt handling:
  When the LangGraph hits an interrupt() call, ainvoke() raises
  GraphInterrupt (langgraph.errors.GraphInterrupt).  The worker catches it,
  writes the gate payload to migration_jobs.pending_gate_payload, sets
  status = "awaiting_review", and exits cleanly.

  When the frontend POSTs decisions via /api/migration/{id}/approve, the
  endpoint enqueues resume_migration.  That task calls ainvoke() again with
  Command(resume=decisions) using the same thread_id so the PostgresSaver
  checkpointer restores the graph from where it paused.
"""

import os
from datetime import datetime
from uuid import UUID

from cafm_shared.logging import get_logger

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from arq.connections import RedisSettings as ArqRedisSettings

from .config import get_settings
from .db import get_async_session_factory
from .models.migration import MigrationJob, FiixIngestionJob
from .graph.migration_graph import get_migration_graph
from .graph.state import MigrationState
from .graph.nodes.db_writer import update_node_progress, write_error

logger = get_logger(__name__)

# Read at module load so WorkerSettings.redis_settings is a plain class attribute
_redis_dsn = os.environ.get("REDIS_URL", "redis://localhost:6379")


class WorkerSettings:
    """ARQ worker configuration.

    functions is populated after all @async_task definitions below so that
    the references are valid at module load time.
    """

    redis_settings = ArqRedisSettings.from_dsn(_redis_dsn)
    functions: list = []          # filled in at bottom of module
    allow_abort_jobs = True
    job_timeout = 3600            # 1 hour max per migration


# ── Helper ────────────────────────────────────────────────────────────────────

async def _run_graph(
    graph,
    input_or_command,
    config: dict,
    migration_id: str,
    session_factory,
) -> dict:
    """
    Run (or resume) the graph and handle GraphInterrupt.

    Returns a dict with status = "complete" | "awaiting_review" | "failed".
    When "awaiting_review" the gate_type and payload are already written to DB
    by the gate node itself (via db_writer.write_gate_payload).
    """
    try:
        from langgraph.errors import GraphInterrupt
    except ImportError:
        # Older langgraph versions may export it differently
        try:
            from langgraph.types import GraphInterrupt
        except ImportError:
            GraphInterrupt = None

    try:
        await graph.ainvoke(input_or_command, config=config)

        # ── Graph ran to completion without interruption ──────────────
        logger.info("migration_graph_complete", migration_id=migration_id)
        return {"status": "complete"}

    except Exception as exc:
        # Detect interrupt by type name for resilience across langgraph versions
        is_interrupt = (
            (GraphInterrupt is not None and isinstance(exc, GraphInterrupt))
            or type(exc).__name__ == "GraphInterrupt"
        )

        if is_interrupt:
            gate_type = "unknown"
            try:
                interrupts = getattr(exc, "interrupts", [])
                if interrupts:
                    payload = getattr(interrupts[0], "value", {})
                    gate_type = payload.get("gate", "unknown") if isinstance(payload, dict) else "unknown"
            except Exception:
                pass

            logger.info(
                "migration_graph_paused",
                migration_id=migration_id,
                gate_type=gate_type,
            )
            return {"status": "awaiting_review", "gate_type": gate_type}

        # ── Unexpected error ──────────────────────────────────────────
        logger.error(
            "migration_graph_failed",
            migration_id=migration_id,
            error=str(exc),
            exc_info=True,
        )
        async with session_factory() as session:
            try:
                await session.execute(
                    update(MigrationJob)
                    .where(MigrationJob.id == UUID(migration_id))
                    .values(
                        status="failed",
                        error_message=str(exc)[:500],
                        error_timestamp=datetime.utcnow(),
                    )
                )
                await session.commit()
            except Exception as db_err:
                logger.error("migration_db_write_failed", migration_id=migration_id, error=str(db_err))
        # Finalise the Activity Log card to "failed". This error path never reaches the Node-10
        # emit, so without this the per-run card stays stuck at its last running/pending sync.
        try:
            from .graph.nodes.schema_db_writer import _sync_run_activity
            await _sync_run_activity(migration_id, caller="worker_error")
        except Exception as _act_err:
            logger.warning("migration_error_activity_sync_failed", migration_id=migration_id, error=str(_act_err))
        return {"status": "failed", "error": str(exc)}


async def _run_schema_graph(
    graph,
    input_or_command,
    config: dict,
    schema_mapping_id: str,
    session_factory,
) -> dict:
    """
    Run (or resume) the schema mapping graph and handle GraphInterrupt.

    Mirrors _run_graph but writes errors to SchemaMappingJob instead of MigrationJob.
    """
    from .models.migration import SchemaMappingJob

    try:
        from langgraph.errors import GraphInterrupt
    except ImportError:
        try:
            from langgraph.types import GraphInterrupt
        except ImportError:
            GraphInterrupt = None

    try:
        await graph.ainvoke(input_or_command, config=config)
        logger.info("schema_graph_complete", schema_mapping_id=schema_mapping_id)
        return {"status": "complete"}

    except Exception as exc:
        is_interrupt = (
            (GraphInterrupt is not None and isinstance(exc, GraphInterrupt))
            or type(exc).__name__ == "GraphInterrupt"
        )

        if is_interrupt:
            gate_type = "unknown"
            try:
                interrupts = getattr(exc, "interrupts", [])
                if interrupts:
                    payload = getattr(interrupts[0], "value", {})
                    gate_type = payload.get("gate", "unknown") if isinstance(payload, dict) else "unknown"
            except Exception:
                pass

            is_step_pause = gate_type in ("unknown", None) or gate_type == "unknown"

            if is_step_pause:
                logger.info(
                    "schema_graph_step_paused",
                    schema_mapping_id=schema_mapping_id,
                )
                return {"status": "step_paused"}
            else:
                logger.info(
                    "schema_graph_paused",
                    schema_mapping_id=schema_mapping_id,
                    gate_type=gate_type,
                )
                return {"status": "awaiting_review", "gate_type": gate_type}

        logger.error(
            "schema_graph_failed",
            schema_mapping_id=schema_mapping_id,
            error=str(exc),
            exc_info=True,
        )
        async with session_factory() as session:
            try:
                await session.execute(
                    update(SchemaMappingJob)
                    .where(SchemaMappingJob.id == UUID(schema_mapping_id))
                    .values(
                        status="error",
                        error_message=str(exc)[:500],
                        error_timestamp=datetime.utcnow(),
                    )
                )
                await session.commit()
            except Exception as db_err:
                logger.error("schema_db_write_failed", schema_mapping_id=schema_mapping_id, error=str(db_err))
        return {"status": "failed", "error": str(exc)}


# ── Tasks ─────────────────────────────────────────────────────────────────────

async def run_migration(
    ctx,
    migration_id: str,
    organization_id: str,
    cmms_name: str,
    source_blob_url: str,
    uploaded_by: str,
    json_mapper: dict = None,
    source_blob_path: str = None,
    source_filename: str = None,
) -> dict:
    """
    Start a fresh migration run through the 9-node pipeline.

    Creates the persistent db_session, builds initial state, and calls ainvoke().
    If the graph pauses at a gate the task exits with status="awaiting_review".
    """
    session_factory = get_async_session_factory()

    logger.info(
        "run_migration_start",
        migration_id=migration_id,
        cmms_name=cmms_name,
        organization_id=organization_id,
        uploaded_by=uploaded_by,
    )

    # ── Mark as running ───────────────────────────────────────────────
    async with session_factory() as session:
        result = await session.execute(
            select(MigrationJob).where(MigrationJob.id == UUID(migration_id))
        )
        job = result.scalar_one_or_none()
        if not job:
            logger.error("run_migration_job_not_found", migration_id=migration_id)
            return {"status": "failed", "error": "Job not found"}
        job.status = "running"
        job.started_at = datetime.utcnow()
        await session.commit()

    # Create the Activity Log card immediately at run start. Previously the card only appeared
    # once the first node completed, so a run that errored before Node 1 produced NO card at all.
    try:
        from .graph.nodes.schema_db_writer import _sync_run_activity
        await _sync_run_activity(migration_id, caller="run_start")
    except Exception as _act_err:
        logger.warning("run_start_activity_sync_failed", migration_id=migration_id, error=str(_act_err))

    graph = await get_migration_graph()  # async factory — MUST be awaited (else `graph` is a coroutine)

    config = {
        "configurable": {"thread_id": migration_id},
        "run_name": f"migration:{migration_id}",
        "tags": [f"cmms:{cmms_name}", f"org:{organization_id}", "service:schema-mapper"],
        "metadata": {
            "migration_id": migration_id,
            "cmms_name": cmms_name,
            "organization_id": organization_id,
        },
    }

    # ── Build persistent session for nodes that write to DB ──────────
    async with session_factory() as db_session:
        initial_state: MigrationState = {
            "migration_id": migration_id,
            "organization_id": organization_id,
            "cmms_name": cmms_name,
            "source_system": cmms_name,
            "source_blob_url": source_blob_url,
            # source_blob_path lets Node 1 re-pull the (combined) upload the inline start path
            # persisted to Blob — it's checked BEFORE source_blob_url (ingest_node.py:121).
            "source_blob_path": source_blob_path,
            "source_filename": source_filename or cmms_name,
            "uploaded_by": uploaded_by,
            "upload_timestamp": datetime.utcnow(),
            "current_step": 0,
            "status": "running",
            "checkpoint_count": 0,
            "event_log": [],
            "tier1_mapped_count": 0,
            "tier2_human_count": 0,
            "overall_confidence": 0.0,
            "db_session": db_session,   # ← passed to every node for DB writes
        }

        if json_mapper:
            initial_state["json_mapper"] = json_mapper

        result = await _run_graph(graph, initial_state, config, migration_id, session_factory)

    return result


async def resume_migration(
    ctx,
    migration_id: str,
    gate_type: str,
    decisions: dict,
) -> dict:
    """
    Resume a migration that was paused at a HITL gate.

    Called by the /approve endpoint after the frontend submits decisions.
    Restores graph state from the PostgresSaver checkpointer and continues.

    Args:
        migration_id:  Migration UUID string.
        gate_type:     Which gate is being resumed ("pre_semantic" | "field_mapping" |
                       "hierarchy" | "write").
        decisions:     The frontend's decisions — exact shape depends on the gate:
                         pre_semantic:  {table: [{source_field, decision}]}
                         field_mapping: {table: [{action, source_field, target_field}]}
                         hierarchy:     [{action, relationship_id, ...}]
                         write:         {action: "confirm" | "reject"}
    """
    session_factory = get_async_session_factory()

    logger.info(
        "resume_migration_start",
        migration_id=migration_id,
        gate_type=gate_type,
        decisions_keys=list(decisions.keys()) if isinstance(decisions, dict) else type(decisions).__name__,
    )

    # ── Mark as running again ─────────────────────────────────────────
    async with session_factory() as session:
        result = await session.execute(
            select(MigrationJob).where(MigrationJob.id == UUID(migration_id))
        )
        job = result.scalar_one_or_none()
        if not job:
            logger.error("resume_migration_job_not_found", migration_id=migration_id, gate_type=gate_type)
            return {"status": "failed", "error": "Job not found"}
        if job.status not in ["awaiting_review", "running"]:
            logger.error(
                "resume_migration_not_resumable",
                migration_id=migration_id,
                gate_type=gate_type,
                current_status=job.status,
            )
            return {"status": "failed", "error": f"Job not resumable: {job.status}"}
        # Keep pending_gate fields — the gate node's clear_gate_payload() will
        # wipe them once the graph actually resumes past the interrupt.
        job.status = "running"
        await session.commit()

    graph = await get_migration_graph()  # async factory — MUST be awaited (else `graph` is a coroutine)

    config = {
        "configurable": {"thread_id": migration_id},
        "run_name": f"migration:{migration_id}:resume:{gate_type}",
        "tags": [f"gate:{gate_type}", "service:schema-mapper"],
        "metadata": {"migration_id": migration_id, "gate_type": gate_type},
    }

    try:
        from langgraph.types import Command
    except ImportError:
        logger.error("[resume_migration] langgraph.types.Command not available")
        return {"status": "failed", "error": "LangGraph Command not available"}

    # The graph restores state from PostgresSaver using thread_id.
    # Command(resume=decisions) is passed to the interrupted interrupt() call.
    async with session_factory() as db_session:
        if gate_type == "ddl_retry":
            # DDL retry: Node 9 ran to completion (with failure — status="ddl_failed").
            # The graph is NOT paused at an interrupt(); we need to re-run Node 9 with
            # corrected extra_fields_config injected into state.
            # Use Command with update only (no resume value needed).
            corrected_config = decisions.get("extra_fields_config", [])
            resume_command = Command(
                resume=None,
                update={
                    "db_session": db_session,
                    "extra_fields_config": corrected_config,
                    "status": "running",
                    "error_message": None,
                },
            )
        else:
            # Inject db_session into state so resumed nodes can still write to DB.
            resume_command = Command(
                resume=decisions,
                update={"db_session": db_session},
            )

        result = await _run_graph(
            graph, resume_command, config, migration_id, session_factory
        )

    return result


async def run_schema_mapping(
    ctx,
    schema_mapping_id: str,
    organization_id: str,
    external_cmms_name: str,
    external_schema_source: str,
    external_schema_format: str,
    created_by: str,
    schema_content: str = None,
    db_url: str = None,
) -> dict:
    """
    Start a fresh schema mapping run through the 9-node pipeline.

    Creates the persistent db_session, builds initial state, and calls ainvoke().
    If the graph pauses at a gate the task exits with status="awaiting_review".
    """
    from datetime import datetime
    from .graph.schema_mapping_graph import build_schema_mapping_graph
    from .graph.schema_state import SchemaMappingState
    from .models.migration import SchemaMappingJob

    session_factory = get_async_session_factory()

    logger.info(
        f"[run_schema_mapping] Starting schema_mapping={schema_mapping_id} "
        f"cmms={external_cmms_name} org={organization_id}"
    )

    # ── Mark as running ───────────────────────────────────────────────
    async with session_factory() as session:
        result = await session.execute(
            select(SchemaMappingJob).where(SchemaMappingJob.id == UUID(schema_mapping_id))
        )
        job = result.scalar_one_or_none()
        if not job:
            logger.error(f"[run_schema_mapping] Job not found: {schema_mapping_id}")
            return {"status": "failed", "error": "Job not found"}
        job.status = "running"
        job.started_at = datetime.utcnow()
        await session.commit()

    # ── Build and run graph ───────────────────────────────────────────
    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        from .config import get_settings
        settings = get_settings()
        checkpointer = AsyncPostgresSaver.from_conn_string(settings.database_url)
        graph = build_schema_mapping_graph(checkpointer=checkpointer)
    except Exception:
        graph = build_schema_mapping_graph(checkpointer=None)

    config = {
        "configurable": {"thread_id": schema_mapping_id},
        "run_name": f"schema_mapping:{schema_mapping_id}",
        "tags": [f"cmms:{external_cmms_name}", f"org:{organization_id}", "service:schema-mapper"],
        "metadata": {
            "schema_mapping_id": schema_mapping_id,
            "external_cmms_name": external_cmms_name,
            "organization_id": organization_id,
        },
    }

    async with session_factory() as db_session:
        initial_state: SchemaMappingState = {
            "schema_mapping_id": schema_mapping_id,
            "organization_id": organization_id,
            "external_cmms_name": external_cmms_name,
            "external_schema_source": external_schema_source,
            "external_schema_format": external_schema_format,
            "created_by": created_by,
            "created_at": datetime.utcnow(),
            "processing_started_at": datetime.utcnow(),
            "status": "running",
            "notes": [],
            "langsmith_run_ids": [],
            "db_session": db_session,
        }
        if schema_content:
            initial_state["schema_content"] = schema_content
        if db_url:
            initial_state["db_url"] = db_url

        result = await _run_schema_graph(graph, initial_state, config, schema_mapping_id, session_factory)

    return result


async def resume_schema_mapping(
    ctx,
    schema_mapping_id: str,
    gate_type: str,
    decisions: dict,
) -> dict:
    """
    Resume a schema mapping that was paused at a HITL gate.

    Called by the /api/schema-mapping/{id}/approve endpoint after the frontend
    submits decisions. Restores graph state from the PostgresSaver checkpointer.

    Args:
        schema_mapping_id:  SchemaMappingJob UUID string.
        gate_type:          Which gate is being resumed ("field_mapping" | "hierarchy").
        decisions:          The frontend's decisions — shape varies by gate_type.
    """
    from .models.migration import SchemaMappingJob

    session_factory = get_async_session_factory()

    logger.info(
        f"[resume_schema_mapping] Resuming schema_mapping={schema_mapping_id} gate={gate_type}"
    )

    # ── Mark as running again ─────────────────────────────────────────
    async with session_factory() as session:
        result = await session.execute(
            select(SchemaMappingJob).where(SchemaMappingJob.id == UUID(schema_mapping_id))
        )
        job = result.scalar_one_or_none()
        if not job:
            logger.error(f"[resume_schema_mapping] Job not found: {schema_mapping_id}")
            return {"status": "failed", "error": "Job not found"}
        if job.status not in ["awaiting_review", "running", "step_paused"]:
            logger.error(
                f"[resume_schema_mapping] Job not resumable: status={job.status}"
            )
            return {"status": "failed", "error": f"Job not resumable: {job.status}"}
        job.status = "running"
        await session.commit()

    # ── Build graph with checkpointer ─────────────────────────────────
    from .graph.schema_mapping_graph import build_schema_mapping_graph
    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        from .config import get_settings
        settings = get_settings()
        checkpointer = AsyncPostgresSaver.from_conn_string(settings.database_url)
        graph = build_schema_mapping_graph(checkpointer=checkpointer)
    except Exception:
        graph = build_schema_mapping_graph(checkpointer=None)

    config = {
        "configurable": {"thread_id": schema_mapping_id},
        "run_name": f"schema_mapping:{schema_mapping_id}:resume:{gate_type}",
        "tags": [f"gate:{gate_type}", "service:schema-mapper"],
        "metadata": {"schema_mapping_id": schema_mapping_id, "gate_type": gate_type},
    }

    try:
        from langgraph.types import Command
    except ImportError:
        logger.error("[resume_schema_mapping] langgraph.types.Command not available")
        return {"status": "failed", "error": "LangGraph Command not available"}

    async with session_factory() as db_session:
        if gate_type == "ddl_retry":
            # DDL retry: the graph is NOT paused at an interrupt() — Node 8 ran
            # to completion (with failure).  We need to re-run the graph with the
            # corrected extra_fields_config injected into state.
            # Use Command with update only (no resume value needed).
            corrected_config = decisions.get("extra_fields_config", [])
            resume_command = Command(
                resume=None,
                update={
                    "db_session": db_session,
                    "extra_fields_config": corrected_config,
                    "status": "running",
                    "error_message": None,
                },
            )
        else:
            resume_command = Command(
                resume=decisions,
                update={"db_session": db_session},
            )

        result = await _run_schema_graph(
            graph, resume_command, config, schema_mapping_id, session_factory
        )

    return result


async def run_fiix_data_ingestion(
    ctx,
    ingestion_id: str,
    organization_id: str,
    created_by: str,
    schema_mapping_id: str = None,
    target_schema: str = None,
) -> dict:
    """
    Start a full Fiix data ingestion run through the 3-node pipeline:
      fiix_fetch_node → fiix_preprocess_node → fiix_write_node

    Credentials are read from FIIX_* environment variables (set in config).
    The ingestion is fully automated (no HITL gates).

    Args:
        ingestion_id:       FiixIngestionJob UUID string.
        organization_id:    Organization UUID string.
        created_by:         User ID who triggered the run.
        schema_mapping_id:  SchemaMappingJob UUID.  Used to resolve the target
                            PostgreSQL schema (SchemaMappingJob.new_schema_name).
        target_schema:      Explicit target schema. Takes precedence over the
                            schema_mapping_id lookup. The schema-mapping auto-trigger
                            passes the freshly created schema here so we never race
                            the DB write and fall back to the live plenum_cafm tables.
    """
    from datetime import datetime
    from .graph.fiix_ingestion_graph import build_fiix_ingestion_graph
    from .graph.fiix_state import FiixIngestionState
    from .models.migration import FiixIngestionJob, SchemaMappingJob

    session_factory = get_async_session_factory()

    logger.info(
        f"[run_fiix_data_ingestion] Starting ingestion_id={ingestion_id} "
        f"org={organization_id} schema_mapping_id={schema_mapping_id} "
        f"target_schema={target_schema} by={created_by}"
    )

    # ── Resolve target schema ─────────────────────────────────────────────────
    # Explicit override wins (avoids racing the SchemaMappingJob.new_schema_name
    # write); otherwise resolve from the schema mapper output.
    if not target_schema and schema_mapping_id:
        async with session_factory() as session:
            result = await session.execute(
                select(SchemaMappingJob).where(
                    SchemaMappingJob.id == UUID(schema_mapping_id)
                )
            )
            sm_job = result.scalar_one_or_none()
            if sm_job and sm_job.new_schema_name:
                target_schema = sm_job.new_schema_name
                logger.info(
                    f"[run_fiix_data_ingestion] Target schema resolved: {target_schema}"
                )
            else:
                logger.warning(
                    f"[run_fiix_data_ingestion] SchemaMappingJob {schema_mapping_id} "
                    "has no new_schema_name — will fall back to plenum_cafm"
                )

    # ── Mark job as running ───────────────────────────────────────────────────
    async with session_factory() as session:
        result = await session.execute(
            select(FiixIngestionJob).where(FiixIngestionJob.id == UUID(ingestion_id))
        )
        job = result.scalar_one_or_none()
        if not job:
            logger.error(f"[run_fiix_data_ingestion] Job not found: {ingestion_id}")
            return {"status": "failed", "error": "Job not found"}
        job.status = "fetching"
        job.started_at = datetime.utcnow()
        await session.commit()

    # ── Build graph ───────────────────────────────────────────────────────────
    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        settings = get_settings()
        checkpointer = AsyncPostgresSaver.from_conn_string(settings.database_url)
        graph = build_fiix_ingestion_graph(checkpointer=checkpointer)
    except Exception:
        graph = build_fiix_ingestion_graph(checkpointer=None)

    config = {
        "configurable": {"thread_id": ingestion_id},
        "run_name": f"fiix_ingestion:{ingestion_id}",
        "tags": [f"org:{organization_id}", "service:fiix-ingestion"],
        "metadata": {
            "ingestion_id": ingestion_id,
            "organization_id": organization_id,
            "schema_mapping_id": schema_mapping_id,
            "target_schema": target_schema,
        },
    }

    async with session_factory() as db_session:
        initial_state: FiixIngestionState = {
            "ingestion_id": ingestion_id,
            "organization_id": organization_id,
            "created_by": created_by,
            "created_at": datetime.utcnow(),
            "started_at": datetime.utcnow(),
            "status": "fetching",
            "current_node": 1,
            "notes": [],
            "db_session": db_session,
            "schema_mapping_id": schema_mapping_id,
            "target_schema": target_schema or "plenum_cafm",
        }

        try:
            await graph.ainvoke(initial_state, config=config)
            logger.info(f"[run_fiix_data_ingestion] Complete: ingestion_id={ingestion_id}")
            return {"status": "complete", "target_schema": target_schema}

        except Exception as exc:
            logger.error(
                f"[run_fiix_data_ingestion] Failed: ingestion_id={ingestion_id} error={exc}",
                exc_info=True,
            )
            async with session_factory() as err_session:
                try:
                    await err_session.execute(
                        update(FiixIngestionJob)
                        .where(FiixIngestionJob.id == UUID(ingestion_id))
                        .values(
                            status="failed",
                            error_message=str(exc)[:500],
                        )
                    )
                    await err_session.commit()
                except Exception as db_err:
                    logger.error(f"[run_fiix_data_ingestion] DB error write failed: {db_err}")
            return {"status": "failed", "error": str(exc)}


async def cleanup_expired_migrations(ctx) -> dict:
    """Periodic cleanup: archive migrations older than 7 days."""
    logger.info("[cleanup] Running cleanup_expired_migrations")
    # TODO: implement if needed
    return {"status": "ok"}


async def on_startup(ctx: dict) -> None:
    """
    ARQ worker startup hook — runs once before any tasks are processed.

    Initializes the canonical field embeddings cache so that Node 3
    (semantic_mapper_node) has a populated cache to match against.
    The FastAPI lifespan does the same for the API process, but the
    ARQ worker is a separate process and never runs that lifespan.
    """
    settings = get_settings()

    if not settings.openai_api_key:
        logger.warning("[worker] openai_api_key missing — canonical embeddings will not be initialized")
        return

    try:
        from openai import AsyncOpenAI
        from .embeddings import initialize_canonical_embeddings
        from .services.registry_cache import load_or_build

        _oai = AsyncOpenAI(api_key=settings.openai_api_key)
        _config = await load_or_build(settings.db_url)
        canonical_fields = _config.get("canonical_fields", {})

        if not canonical_fields:
            logger.warning(
                "[worker] DB registry has 0 canonical fields — "
                "falling back to hardcoded canonical field definitions"
            )
            from .graph.nodes.semantic_mapper import _HARDCODED_CANONICAL_FIELDS
            canonical_fields = _HARDCODED_CANONICAL_FIELDS

        await initialize_canonical_embeddings(_oai, canonical_fields)
        logger.info(
            f"[worker] Canonical embeddings initialized: {len(canonical_fields)} fields"
        )

    except Exception as exc:
        logger.error(f"[worker] Failed to initialize canonical embeddings at startup: {exc}")


# Populate WorkerSettings.functions now that all @async_task functions are defined.
# ARQ requires actual Function objects (the result of @async_task), not string names.
WorkerSettings.on_startup = on_startup
WorkerSettings.functions = [
    run_migration,
    resume_migration,
    run_schema_mapping,
    resume_schema_mapping,
    run_fiix_data_ingestion,
    cleanup_expired_migrations,
]
