"""
svc-ingestion/src/worker.py

ARQ worker for svc-ingestion.
Picks up extraction jobs enqueued by Stage 1 (ingest.py).

Phase 1: extract_document is a stub — logs and updates status to 'extracting'.
Phase 2: each agent (PDF, Excel, Word, CSV, XML/JSON) will be wired in here.

Run with:
    arq worker.WorkerSettings
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import anthropic
import redis.asyncio as aioredis
from arq import cron
from arq.connections import RedisSettings
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from cafm_shared.logging import configure_logging, get_logger
from config import get_settings
from models.ingestion import IngestionDocument

logger = get_logger(__name__)


# ── Extraction job (stub — agents added in Phase 2) ───────────────────────────


async def extract_document(ctx: dict, ingestion_id: str) -> dict:
    """
    Stage 2 entry point — dispatches to the correct agent based on source_type.

    Phase 1: stub — marks the document as 'extracting' and logs.
    Phase 2: routes to pdf_agent, excel_agent, word_agent, csv_agent, xml_json_agent.
    """
    db_factory: async_sessionmaker[AsyncSession] = ctx["db_factory"]
    logger.info("extract_document_started", ingestion_id=ingestion_id)

    async with db_factory() as db:
        result = await db.execute(
            select(IngestionDocument).where(
                IngestionDocument.id == uuid.UUID(ingestion_id)
            )
        )
        doc: IngestionDocument | None = result.scalar_one_or_none()

        if doc is None:
            logger.error("extract_document_not_found", ingestion_id=ingestion_id)
            return {"status": "failed", "error": "Document not found"}

        # Update status → extracting
        doc.status = "extracting"
        await db.commit()

        logger.info(
            "extract_document_stub",
            ingestion_id=ingestion_id,
            source_type=doc.source_type,
            agent_id=doc.agent_id,
            note="Phase 2 agent logic not yet implemented",
        )

    return {"status": "extracting", "ingestion_id": ingestion_id}


# ── Worker startup / shutdown ─────────────────────────────────────────────────


async def weekly_prompt_refinement(ctx: dict) -> dict:
    """
    Task 3.5 — Weekly prompt refinement cron job.

    Runs every Sunday at 00:00 UTC.
    Aggregates correction patterns from the past 7 days, generates prompt
    improvement suggestions via Haiku, and creates A/B tests for approved ones.
    """
    from shared.prompt_refinement import run_weekly_refinement

    db_factory: async_sessionmaker[AsyncSession] = ctx["db_factory"]
    claude_client: anthropic.AsyncAnthropic = ctx["claude_client"]

    logger.info("weekly_prompt_refinement_started")
    async with db_factory() as session:
        result = await run_weekly_refinement(session=session, client=claude_client)

    logger.info(
        "weekly_prompt_refinement_complete",
        patterns_found=result.patterns_found,
        ab_tests_created=result.ab_tests_created,
        errors=len(result.errors),
    )
    return {
        "patterns_found": result.patterns_found,
        "suggestions_generated": result.suggestions_generated,
        "ab_tests_created": result.ab_tests_created,
        "errors": result.errors,
    }


async def startup(ctx: dict) -> None:
    settings = get_settings()
    configure_logging(debug=settings.debug)

    engine = create_async_engine(
        settings.db_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_pre_ping=True,
    )
    ctx["db_factory"] = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    ctx["claude_client"] = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    ctx["settings"] = settings
    logger.info("ingestion_worker_started", env=settings.environment)


async def shutdown(ctx: dict) -> None:
    logger.info("ingestion_worker_stopped")


# ── Worker settings ───────────────────────────────────────────────────────────


class WorkerSettings:
    functions = [extract_document, weekly_prompt_refinement]
    cron_jobs = [
        # Task 3.5 — weekly prompt refinement: every Sunday at 00:00 UTC
        cron(weekly_prompt_refinement, weekday=6, hour=0, minute=0),
    ]
    on_startup = startup
    on_shutdown = shutdown
    queue_name = "cafm:ingestion:queue"
    max_jobs = 10
    job_timeout = 300  # 5 minutes per extraction job

    @classmethod
    def _redis_settings(cls) -> RedisSettings:
        settings = get_settings()
        # Parse redis://host:port/db
        url = settings.redis_url
        if url.startswith("redis://"):
            url = url[len("redis://"):]
        host_port, *_ = url.split("/")
        host, port_str = (host_port.split(":") + ["6379"])[:2]
        return RedisSettings(host=host, port=int(port_str))

    redis_settings = property(lambda self: self._redis_settings())
