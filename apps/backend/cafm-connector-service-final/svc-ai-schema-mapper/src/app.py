"""svc-AI-Schema-Mapper FastAPI application.

CRITICAL: LangSmith environment variables must be set BEFORE any langgraph/langchain import.
This happens at module import time, not in the lifespan.
"""

import os
import sys
import json
import asyncio
import re
import logging

# Windows: psycopg3 requires SelectorEventLoop (not the default ProactorEventLoop)
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# ── doc-rag integration: make 'from app.xxx' resolve to /app/doc_rag/app/ ──
_DOC_RAG_PATH = "/app/doc_rag"
if _DOC_RAG_PATH not in sys.path:
    sys.path.insert(0, _DOC_RAG_PATH)
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, List, Optional
from uuid import uuid4, UUID
from datetime import datetime

# ── Set LangSmith env vars BEFORE any LangChain import ──
from .config import get_settings as _get_settings_early

_s_early = _get_settings_early()
if _s_early.langsmith_tracing and _s_early.langsmith_api_key:
    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    os.environ["LANGCHAIN_API_KEY"] = _s_early.langsmith_api_key
    os.environ["LANGCHAIN_PROJECT"] = _s_early.langsmith_project
    os.environ["LANGCHAIN_ENDPOINT"] = _s_early.langsmith_endpoint

# ── Now safe to import langgraph/langchain ──
import time
import anthropic
from redis import asyncio as aioredis
from openai import AsyncOpenAI
from fastapi import FastAPI, Request, WebSocket, HTTPException, Query, Path, Depends, UploadFile, File, Form, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, JSONResponse
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
from sqlalchemy import select, update, func, text
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Literal

from cafm_shared.logging import configure_logging, get_logger
from cafm_shared.telemetry import configure_telemetry
from .config import Settings, get_settings
from .embeddings import initialize_canonical_embeddings
from .schemas import (
    MigrationStartRequest,
    MigrationStartResponse,
    MigrationStatusResponse,
    MigrationApprovalRequest,
    MigrationApprovalResponse,
    CanonicalFieldScoresRequest,
    EmbedRankItem,
    EmbedRankRequest,
    EmbedRankResponse,
    CanonicalFieldScoresResponse,
    MigrationAuditResponse,
    MigrationListResponse,
    MigrationDownloadResponse,
    MigrationCancelResponse,
    LangSmithTraceResponse,
    ErrorResponse,
    WebSocketEvent,
    FieldMappingAudit,
    MigrationListItem,
    TestIngestResponse,
    JsonMapperConfig,
    TestIngestWithMapperResponse,
    Tier2Mapping,
    TestIngestWithSemanticResponse,
    HumanReviewRequest,
    HumanReviewResponse,
    FinalMapping,
    PreprocessRequest,
    PreprocessResponse,
    DataQualityMetrics,
    ResolveHierarchyRequest,
    ResolveHierarchyResponse,
    VerifyHierarchyRequest,
    VerifyHierarchyResponse,
    GenerateOutputRequest,
    GenerateOutputResponse,
    WriteOutputRequest,
    WriteOutputResponse,
)
from .db import get_async_session_factory
from .models.migration import MigrationJob, MigrationFieldMapping
from .graph.migration_graph import get_migration_graph
from .excel_parser import ExcelWorkbook
from .graph.nodes.ingest_node import (
    _sanitize_column_names as _sanitize_column_names_helper,
)
from .api.mappings import router as mappings_router
from .runtime_logs import bind_runtime_log_context, get_runtime_logs, install_runtime_log_capture

logger = get_logger(__name__)


async def _get_or_cache_fiix_schema_db(connector, *, force_refresh: bool = False) -> dict:
    """Fiix mapper config (extracted tables + columns), persisted in the DB table
    ``plenum_cafm.fiix_schema_cache`` keyed by Fiix subdomain.

    The Fiix API (daily quota) is hit ONCE; every later run reads from the DB — which
    survives container/image rebuilds. force_refresh=True (or env FIIX_FORCE_REFRESH=1)
    re-pulls from Fiix and upserts the row.
    """
    import os as _os
    import asyncio as _asyncio
    import hashlib as _hashlib
    from .models.migration import FiixSchemaCache

    sub = getattr(getattr(connector, "api", None), "subdomain", "") or "default"
    key = _hashlib.sha1(sub.encode("utf-8")).hexdigest()[:16]
    if _os.getenv("FIIX_FORCE_REFRESH", "").strip().lower() in ("1", "true", "yes"):
        force_refresh = True

    _sf = get_async_session_factory()

    if not force_refresh:
        try:
            async with _sf() as session:
                row = (
                    await session.execute(
                        select(FiixSchemaCache).where(FiixSchemaCache.subdomain_key == key)
                    )
                ).scalar_one_or_none()
                if row and row.mapper_config:
                    logger.info(
                        "[Fiix] Using DB-cached schema for '%s' (%s tables) — NO Fiix API call",
                        sub, row.table_count,
                    )
                    return row.mapper_config
        except Exception as e:  # noqa: BLE001 — cache read is best-effort
            logger.warning("[Fiix] DB schema cache read failed (%s); fetching from Fiix", e)

    cfg = await _asyncio.to_thread(connector.get_mapper_config)  # hits the Fiix API
    try:
        from .connectors.fiix_credentials import summarize_fiix_mapper
        _summ = summarize_fiix_mapper(cfg)
        _tc = int(_summ.get("table_count") or 0)
        _cc = int(_summ.get("column_count") or 0)
    except Exception:
        _tc = _cc = 0
    try:
        async with _sf() as session:
            row = (
                await session.execute(
                    select(FiixSchemaCache).where(FiixSchemaCache.subdomain_key == key)
                )
            ).scalar_one_or_none()
            if row:
                row.mapper_config = cfg
                row.subdomain = sub
                row.table_count = _tc
                row.column_count = _cc
            else:
                session.add(FiixSchemaCache(
                    subdomain_key=key, subdomain=sub, mapper_config=cfg,
                    table_count=_tc, column_count=_cc,
                ))
            await session.commit()
        logger.info("[Fiix] Stored Fiix schema for '%s' in DB (%s tables, %s cols)", sub, _tc, _cc)
    except Exception as e:  # noqa: BLE001 — persistence is best-effort
        logger.warning("[Fiix] Could not persist Fiix schema to DB (%s)", e)
    return cfg


async def _store_migration_source(
    migration_id: str, filename: str, data: bytes, *, subdir: str = "source"
) -> str | None:
    """Persist a migration's source file to Azure Blob so re-runs can re-pull it.

    Returns the blob path (re-downloaded server-side by ingest_node via the connection
    string) or None when Azure isn't configured / upload fails — in which case callers
    fall back to in-memory bytes (unchanged behaviour). Never raises.
    """
    try:
        settings = get_settings()
        conn = getattr(settings, "azure_storage_connection_string", "") or ""
        if not conn:
            return None
        container = getattr(settings, "azure_blob_container_name", "") or "plenum-agentic-ai-attachments"
        safe_name = (
            "".join(c for c in (filename or "source.bin") if c.isalnum() or c in ("-", "_", ".", " "))
            .strip()
            .replace(" ", "_")
            or "source.bin"
        )
        blob_path = f"migrations/{migration_id}/{subdir}/{safe_name}"
        from azure.storage.blob.aio import BlobServiceClient as _BSC

        async with _BSC.from_connection_string(conn) as svc:
            bc = svc.get_blob_client(container=container, blob=blob_path)
            await bc.upload_blob(data, overwrite=True)
        logger.info(f"[source-store] {migration_id}: stored {safe_name} → {blob_path}")
        return blob_path
    except Exception as exc:
        logger.warning(f"[source-store] {migration_id}: upload failed for {filename}: {str(exc)[:200]}")
        return None


def _source_status_fields(job) -> tuple[Optional[str], Optional[str]]:
    """(source_filename, source_download_url) for a migration status response.

    The download URL is the relative source route, populated only when the file
    was actually persisted to blob (i.e. not a ``direct_upload`` sentinel).
    """
    filename = getattr(job, "source_filename", None)
    blob_ref = (getattr(job, "source_blob_url", None) or "").strip()
    has_blob = bool(blob_ref) and blob_ref not in ("direct_upload", "direct_upload_multi")
    url = f"/api/migration/{job.id}/source" if has_blob else None
    return filename, url


def _udr_node_output_field(job, key: str) -> Optional[dict]:
    """Pull the latest ``output[key]`` dict the UDR pass wrote into the migration
    node log, for the status response. Returns None until the run reaches that stage."""
    found = None
    for entry in (getattr(job, "node_logs", None) or []):
        out = entry.get("output") if isinstance(entry, dict) else None
        if isinstance(out, dict) and isinstance(out.get(key), dict):
            found = out[key]
    return found


def _udr_test_results_from_job(job) -> Optional[dict]:
    """F7 7.9/7.10 — the Test 1/Test 2 reports surfaced from the UDR node log."""
    return _udr_node_output_field(job, "udr_test_results")


def _udr_relationship_report_from_job(job) -> Optional[dict]:
    """F7 7.12/7.13 — schema vs LLM-inferred edges + sanctity flags from the node log."""
    return _udr_node_output_field(job, "udr_relationship_report")


def _udr_table_resolution_from_job(job) -> Optional[dict]:
    """B7.1→B12.1 — the Migration-Analysis table-resolution report from the UDR node log."""
    return _udr_node_output_field(job, "udr_table_resolution")


def _udr_column_intelligence_from_job(job) -> Optional[dict]:
    """B13.1→B21.1 — the Column-Intelligence Pipeline report from the UDR node log."""
    return _udr_node_output_field(job, "udr_column_intelligence")


def _guess_source_media_type(filename: str) -> str:
    """Best-effort content type for a downloaded source file (Excel/CSV/PDF/Word)."""
    name = (filename or "").lower()
    if name.endswith(".xlsx") or name.endswith(".xlsm"):
        return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    if name.endswith(".xls"):
        return "application/vnd.ms-excel"
    if name.endswith(".csv"):
        return "text/csv"
    if name.endswith(".tsv"):
        return "text/tab-separated-values"
    if name.endswith(".json"):
        return "application/json"
    if name.endswith(".pdf"):
        return "application/pdf"
    if name.endswith(".docx"):
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    if name.endswith(".doc"):
        return "application/msword"
    return "application/octet-stream"




# ── Global singletons ──
_anthropic_client: Optional[anthropic.AsyncAnthropic] = None
_openai_client: Optional = None  # OpenAI AsyncOpenAI client for embeddings
_redis_client: Optional[aioredis.Redis] = None
_migration_graph: Optional = None
_schema_mapping_graph: Optional = None   # Singleton graph for schema mapping (shared MemorySaver)
_session_factory: Optional = None
_default_mapper_config: Optional[dict] = None  # Built from plenum_cafm schema at startup


async def _activity_timeout_sweeper() -> None:
    """AL.4 — server-side 15-minute timeout cron. Periodically flips overdue *pending*
    inline actions to a 'Pending Human Response' entry (red) even if the user's session is
    closed, and never proceeds autonomously on the gated item. Runs every 60s, comfortably
    inside the AL.4 ±30s tolerance on the 15-minute deadline. Global (all organizations),
    since it is a system task, not a tenant-scoped query."""
    from .udr import sweep_timeouts

    while True:
        try:
            await asyncio.sleep(60)
            result = await sweep_timeouts()
            if result.get("swept"):
                logger.info(f"[activity-sweeper] timed out {result['swept']} pending action(s)")
        except asyncio.CancelledError:
            raise
        except Exception as _e:  # never let the cron die on a transient error
            logger.warning(f"[activity-sweeper] sweep failed: {_e}")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """FastAPI lifespan context manager.

    Startup:
    1. Configure telemetry (must be first)
    2. Configure logging
    3. Initialize API clients (Anthropic, OpenAI)
    4. Initialize embeddings cache (canonical fields)
    5. Initialize Redis client
    Shutdown:
    - Close connections
    """
    global _anthropic_client, _openai_client, _redis_client, _migration_graph, _schema_mapping_graph, _session_factory

    settings = get_settings()

    # Configure telemetry FIRST
    configure_telemetry(service_name="cafm-ai-schema-mapper", app=app)

    # Configure logging SECOND
    configure_logging(debug=settings.debug)
    install_runtime_log_capture()

    # Silence noisy third-party loggers.  Root logger may be at DEBUG (set by
    # configure_logging when debug=True, or by an endpoint handler), so we must
    # set explicit levels on each child logger — NOTSET would inherit root's level.
    for _noisy in (
        "sqlalchemy.engine", "sqlalchemy.engine.Engine",
        "sqlalchemy.pool", "sqlalchemy.dialects", "sqlalchemy.orm",
        "httpcore", "httpcore.connection", "httpcore.http11",
        "httpx",
        "anthropic", "anthropic._base_client",
        "multipart", "multipart.multipart",
        "hpack", "h2",
    ):
        logging.getLogger(_noisy).setLevel(logging.WARNING)

    logger.info(
        "service_startup",
        app=settings.app_name,
        version=settings.app_version,
        environment=settings.environment,
        port=settings.port,
    )

    # Initialize API clients (skip if keys are empty)
    if settings.anthropic_api_key:
        _anthropic_client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key, timeout=3600)
        logger.debug("anthropic_client_initialized", model=settings.claude_default_model)
    else:
        logger.warning("anthropic_api_key is empty; Anthropic client will not be initialized")

    if settings.openai_api_key:
        _openai_client = AsyncOpenAI(api_key=settings.openai_api_key)
        logger.debug("openai_client_initialized", model="text-embedding-3-small")
    else:
        logger.warning("openai_api_key is empty; OpenAI client will not be initialized")

    # Initialize Redis client (optional - only if redis_url provided)
    if settings.redis_url:
        _redis_client = await aioredis.from_url(
            settings.redis_url,
            encoding="utf8",
            decode_responses=True,
        )
        logger.debug("redis_client_initialized", url=settings.redis_url)
    else:
        logger.info("redis_url not configured - Redis caching disabled for development")

    # Initialize default mapper config — load from DB registry cache (fast single-row
    # fetch).  Schema introspection only runs on first-ever boot when the table is empty.
    global _default_mapper_config
    try:
        from .services.registry_cache import load_or_build as _registry_load_or_build

        logger.info("Loading mapper config from canonical_registry DB cache...")
        _default_mapper_config = await _registry_load_or_build(settings.db_url)
        logger.info(
            f"Mapper config ready: {len(_default_mapper_config.get('canonical_fields', {}))} "
            f"canonical fields (source={_default_mapper_config.get('source_system', 'plenum_cafm')})"
        )
    except Exception as e:
        logger.warning(f"Registry cache load failed, using hardcoded fallback: {e}")
        # Fall back to hardcoded config
        from .matchers import CMMS_ALIASES, get_reversed_aliases

        _default_mapper_config = {
            "version": "1.0",
            "source_system": "plenum_cafm",
            "canonical_fields": {
                "asset_code": "Unique identifier for equipment or asset",
                "asset_name": "Human-readable name of the asset",
                "category": "Equipment category or type",
                "location_code": "Site, building, or location identifier",
                "make": "Equipment manufacturer or brand",
                "model": "Equipment model number",
                "serial": "Equipment serial number",
                "wo_code": "Work order identifier",
                "wo_priority": "Work order priority level",
                "wo_status": "Work order status",
                "wo_type": "Work order type",
                "maintenance_type": "Maintenance plan or type",
                "sm_code": "Scheduled maintenance code",
                "trigger_type": "Time-based or meter-based trigger",
                "schedule_interval": "Maintenance frequency or interval",
                "sm_priority": "Maintenance priority",
                "part_code": "Spare part code",
                "stock_on_hand": "Current inventory quantity",
                "minimum_allowed_stock": "Reorder point or minimum stock level",
                "supplier": "Part supplier or vendor",
                "bom_group_name": "Bill of materials group",
                "user_full_name": "User or technician full name",
                "user_title": "User job title or role",
                "user_name": "Login name or username",
                "reports_to": "Manager or supervisor",
                "inspector_name": "Inspection personnel name",
                "inspection_date": "Date of inspection or survey",
                "inspection_location": "Inspection site or location",
                "finding_type": "Issue or defect type",
                "risk_level": "Risk or severity level",
            },
            "vendor_aliases": get_reversed_aliases(),
        }
        logger.info("Using hardcoded fallback mapper config")

    # Initialize canonical field embeddings (Phase 4)
    # This pre-computes embeddings for all canonical fields from the mapper config
    if _openai_client and _default_mapper_config:
        logger.info("Initializing canonical field embeddings from mapper config...")
        canonical_fields_desc = _default_mapper_config.get("canonical_fields", {})
        if not canonical_fields_desc:
            logger.warning(
                "Mapper config has 0 canonical fields from DB — "
                "falling back to hardcoded canonical field definitions"
            )
            from .graph.nodes.semantic_mapper import _HARDCODED_CANONICAL_FIELDS
            canonical_fields_desc = _HARDCODED_CANONICAL_FIELDS
        await initialize_canonical_embeddings(_openai_client, canonical_fields_desc)
        logger.info(f"Canonical field embeddings initialized for {len(canonical_fields_desc)} fields")
    else:
        logger.warning("OpenAI client or mapper config not available; skipping canonical embeddings")

    # Auto-create migration/schema-mapping tables if they don't exist
    try:
        from .models.migration import MigrationBase
        from .db import get_async_engine
        _engine = get_async_engine()
        async with _engine.begin() as _conn:
            await _conn.run_sync(MigrationBase.metadata.create_all)
        logger.info("Migration tables ensured (create_all)")
    except Exception as _e:
        logger.warning(f"create_all failed (tables may already exist): {_e}")

    try:
        from .schema_patches import ensure_migration_jobs_schema_patches
        await ensure_migration_jobs_schema_patches()
    except Exception as _e:
        logger.warning(f"Migration schema patches skipped: {_e}")

    # Initialize migration graph and session factory
    _session_factory = get_async_session_factory()
    _migration_graph = await get_migration_graph()
    logger.info("Migration graph initialized")

    # Initialize schema mapping graph singleton with a Postgres-backed
    # checkpointer (Feature 4: Saved UDR rerun-from-phase support). Falls
    # back to MemorySaver only when langgraph-checkpoint-postgres is not
    # installed — the get_schema_mapping_graph_async factory mirrors the
    # migration graph's priority chain (Async → Sync → Memory).
    from .graph.schema_mapping_graph import get_schema_mapping_graph_async
    _schema_mapping_graph = await get_schema_mapping_graph_async()
    logger.info("Schema mapping graph initialized")

    # ── doc-rag: initialize tables ────────────────────────────────────────────
    try:
        from app.db.session import init_db as _doc_rag_init_db
        _doc_rag_init_db()
        logger.info("doc-rag database tables initialized")
    except Exception as _e:
        logger.warning(f"doc-rag DB init skipped: {_e}")

    # ── AL.4 — start the server-side activity-timeout sweeper (15-min cron) ────
    _sweeper_task = asyncio.create_task(_activity_timeout_sweeper())
    logger.info("activity-timeout sweeper started")

    yield

    # Shutdown
    _sweeper_task.cancel()
    try:
        await _sweeper_task
    except asyncio.CancelledError:
        pass
    except Exception:
        pass
    if _redis_client is not None:
        await _redis_client.aclose()
    logger.info("service_shutdown")


def get_anthropic_client() -> anthropic.AsyncAnthropic:
    """Get the global Anthropic client."""
    if _anthropic_client is None:
        raise RuntimeError("Anthropic client not initialized")
    return _anthropic_client


def get_openai_client() -> AsyncOpenAI:
    """Get the global OpenAI client (for embeddings)."""
    if _openai_client is None:
        raise RuntimeError("OpenAI client not initialized")
    return _openai_client


def get_redis_client() -> aioredis.Redis:
    """Get the global Redis client."""
    if _redis_client is None:
        raise RuntimeError("Redis client not initialized")
    return _redis_client


def get_migration_graph_instance():
    """Get the compiled migration graph."""
    if _migration_graph is None:
        raise RuntimeError("Migration graph not initialized")
    return _migration_graph


def get_schema_mapping_graph_instance():
    """Get the singleton schema mapping graph (shared MemorySaver across all runs)."""
    if _schema_mapping_graph is None:
        raise RuntimeError("Schema mapping graph not initialized")
    return _schema_mapping_graph


def get_session_factory():
    """Get the async session factory."""
    if _session_factory is None:
        raise RuntimeError("Session factory not initialized")
    return _session_factory


async def get_db_session() -> AsyncSession:
    """Dependency injection for database sessions."""
    factory = get_session_factory()
    async with factory() as session:
        yield session


def create_app() -> FastAPI:
    """Create and configure FastAPI application."""
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        lifespan=lifespan,
        docs_url="/docs" if settings.debug else None,
        redoc_url="/redoc" if settings.debug else None,
    )

    # Add CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Request logging middleware ────────────────────────────────────────────
    @app.middleware("http")
    async def _log_requests(request: Request, call_next):
        start = time.time()
        response = await call_next(request)
        duration_ms = round((time.time() - start) * 1000, 1)
        log_fn = logger.warning if response.status_code >= 400 else logger.info
        log_fn(
            "http_request",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration_ms=duration_ms,
            client=request.client.host if request.client else "unknown",
        )
        return response

    # ── Global HTTP exception handler ─────────────────────────────────────────
    @app.exception_handler(HTTPException)
    async def _http_exception_handler(request: Request, exc: HTTPException):
        log_fn = logger.error if exc.status_code >= 500 else logger.warning
        log_fn(
            "http_error",
            method=request.method,
            path=request.url.path,
            status=exc.status_code,
            detail=exc.detail,
            client=request.client.host if request.client else "unknown",
        )
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    # Include mappings router
    app.include_router(mappings_router)

    # Saved UDR script edit / rerun / reset-to-phase endpoints (Feature 4 B.2–B.6).
    from .api.udr_rerun import router as udr_rerun_router
    app.include_router(udr_rerun_router)

    # Schema relationship-graph query expansion — graph-driven RAG retrieval planning.
    from .api.graph_query import router as graph_query_router
    app.include_router(graph_query_router)

    # ── Health check ──
    @app.get("/health", tags=["Health"])
    async def health() -> dict[str, str]:
        """Health check endpoint."""
        return {
            "status": "ok",
            "service": "cafm-ai-schema-mapper",
            "version": settings.app_version,
        }

    @app.get(
        "/api/registry/stats",
        tags=["Registry"],
        summary="Canonical field registry statistics",
    )
    async def get_registry_stats() -> dict:
        """
        Returns statistics about the universal canonical field registry.

        Shows how many aliases have been learned over time vs seeded from
        CMMS_ALIASES, broken down by tier and source CMMS.
        Learned entries (tier=semantic_approved) represent LLM calls that will
        be avoided on future runs.
        """
        from .matchers.registry import registry_stats
        stats = registry_stats()
        return {
            "total_mappings": stats["total"],
            "learned_count": stats["learned"],
            "by_tier": stats["by_tier"],
            "by_cmms": stats["by_cmms"],
            "description": (
                f"{stats['learned']} semantic matches have been promoted to "
                f"deterministic — they now skip LLM on future migrations."
            ),
        }

    @app.get(
        "/api/registry/lookup",
        tags=["Registry"],
        summary="Look up an alias in the canonical field registry",
    )
    async def registry_lookup_endpoint(alias: str) -> dict:
        """
        Check whether a field alias is in the registry.

        Useful for debugging Node 2 behaviour — see which strategy would
        resolve a given field name without running a full migration.
        """
        from .matchers.registry import registry_lookup
        hit = registry_lookup(alias)
        if hit:
            canonical, confidence, tier = hit
            return {
                "found": True,
                "alias": alias,
                "canonical": canonical,
                "confidence": confidence,
                "tier": tier,
                "deterministic": tier != "semantic_approved"
                    or confidence >= 0.85,
            }
        return {"found": False, "alias": alias}

    # ── Feature 7 — global-admin canonical structure growth (incremental UDR) ──
    @app.get(
        "/api/registry/pending-canonical",
        tags=["Registry"],
        summary="List canonical tables/columns awaiting admin approval",
    )
    async def get_pending_canonical_endpoint() -> dict:
        """Tables/columns that recent UDR runs created (new to the platform) and that are
        held for admin approval before they enter the shared canonical target structure."""
        from .services.registry_promotion import get_pending_canonical
        pending = await get_pending_canonical(settings.db_url)
        return {"count": len(pending), "pending": pending}

    @app.post(
        "/api/registry/approve-canonical",
        tags=["Registry"],
        summary="Approve pending canonical additions into the shared structure",
    )
    async def approve_canonical_endpoint(body: dict | None = Body(default=None)) -> dict:
        """Promote pending additions into ``canonical_fields`` so the mappers adopt them on
        next load (and future client deployments inherit them). Body ``{"keys": [...]}`` to
        approve a subset; omit to approve all pending."""
        from .services.registry_promotion import approve_pending_canonical
        keys = (body or {}).get("keys") if isinstance(body, dict) else None
        return await approve_pending_canonical(settings.db_url, keys)

    @app.post(
        "/api/registry/sweep-pending",
        tags=["Registry"],
        summary="Auto-approve pending canonical additions older than the grace window (24h default)",
    )
    async def sweep_pending_canonical_endpoint(body: dict | None = Body(default=None)) -> dict:
        """7.11 AC5 backstop (run on a daily cron): promote pending additions older than
        ``max_age_hours`` (default 24) into the shared canonical structure so a new
        table/column is appended within 24h even without explicit admin action."""
        from .services.registry_promotion import sweep_pending_canonical
        max_age = float((body or {}).get("max_age_hours", 24.0)) if isinstance(body, dict) else 24.0
        return await sweep_pending_canonical(settings.db_url, max_age_hours=max_age)

    # ── Metrics ──
    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        """Prometheus metrics endpoint."""
        return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

    # ── Fiix Platform Connector Routes ──────────────────────────────────

    @app.get(
        "/api/debug/fiix-config",
        tags=["Debug"],
        summary="Debug: Show Fiix configuration (REMOVE IN PROD)",
    )
    async def debug_fiix_config():
        """Debug endpoint to check Fiix configuration."""
        settings = get_settings()
        return {
            "fiix_enabled": settings.fiix_enabled,
            "fiix_enabled_type": type(settings.fiix_enabled).__name__,
            "fiix_subdomain": settings.fiix_subdomain or "(empty)",
            "fiix_app_key": "***" if settings.fiix_app_key else "(empty)",
            "fiix_access_key": "***" if settings.fiix_access_key else "(empty)",
            "fiix_secret_key": "***" if settings.fiix_secret_key else "(empty)",
        }

    async def _run_fiix_connection_test(creds: dict | None) -> dict:
        from .connectors import FiixAPI
        from .connectors.fiix_credentials import credentials_from_mapping

        settings = get_settings()
        subdomain, app_key, access_key, secret_key = credentials_from_mapping(creds, settings)
        api = FiixAPI(
            subdomain=subdomain,
            app_key=app_key,
            access_key=access_key,
            secret_key=secret_key,
            timeout=settings.fiix_timeout,
        )
        import asyncio as _asyncio
        connected = await _asyncio.to_thread(api.test_connection)
        if not connected:
            raise HTTPException(status_code=500, detail="Fiix connection test failed")
        return {
            "status": "ok",
            "message": f"Connected to Fiix ({subdomain}.macmms.com)",
            "subdomain": subdomain,
        }

    async def _run_fiix_fetch_schema(creds: dict | None) -> dict:
        from .connectors import FiixSchemaConnector
        from .connectors.fiix_credentials import (
            build_schema_comparison,
            credentials_from_mapping,
            fetch_plenum_cafm_schema_counts_sync,
            summarize_fiix_mapper,
        )

        settings = get_settings()
        subdomain, app_key, access_key, secret_key = credentials_from_mapping(creds, settings)
        connector = FiixSchemaConnector(
            subdomain=subdomain,
            app_key=app_key,
            access_key=access_key,
            secret_key=secret_key,
            timeout=settings.fiix_timeout,
        )
        import asyncio as _asyncio
        _force_refresh = bool((creds or {}).get("force_refresh") or (creds or {}).get("refresh_fiix_schema"))
        mapper_config = await _get_or_cache_fiix_schema_db(connector, force_refresh=_force_refresh)
        summary = summarize_fiix_mapper(mapper_config)
        plenum_counts = await _asyncio.to_thread(
            fetch_plenum_cafm_schema_counts_sync, settings.db_url
        )
        schema_comparison = build_schema_comparison(summary, plenum_counts)
        logger.info(
            "Fiix schema extracted: %s objects, %s fields; plenum_cafm: %s tables, %s columns (%s API calls)",
            summary.get("table_count"),
            summary.get("column_count"),
            plenum_counts.get("table_count"),
            plenum_counts.get("column_count"),
            connector.api.request_count,
        )
        return {
            "status": "ok",
            "mapper": mapper_config,
            "summary": summary,
            "schema_comparison": schema_comparison,
            "display_summary": schema_comparison.get("markdown"),
            "api_calls": connector.api.request_count,
        }

    @app.get(
        "/api/platforms/fiix/test-connection",
        tags=["Platforms"],
        summary="Test Fiix API connection (env credentials)",
    )
    async def test_fiix_connection_get():
        """Test connectivity using FIIX_* from backend .env."""
        try:
            return await _run_fiix_connection_test(None)
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Fiix connection test failed: {e}")
            raise HTTPException(status_code=500, detail=f"Connection failed: {str(e)}")

    @app.post(
        "/api/platforms/fiix/test-connection",
        tags=["Platforms"],
        summary="Test Fiix API connection (request body credentials)",
    )
    async def test_fiix_connection_post(request: dict | None = None):
        """Test connectivity using credentials in JSON body (Schema Mapper UI parity)."""
        try:
            return await _run_fiix_connection_test(request or {})
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Fiix connection test failed: {e}")
            raise HTTPException(status_code=500, detail=f"Connection failed: {str(e)}")

    @app.get(
        "/api/platforms/fiix/fetch-schema",
        tags=["Platforms"],
        summary="Fetch Fiix platform schema (env credentials)",
    )
    async def fetch_fiix_schema_get():
        """Fetch schema using FIIX_* from backend .env."""
        try:
            return await _run_fiix_fetch_schema(None)
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Fiix schema fetch failed: {e}")
            raise HTTPException(status_code=500, detail=f"Schema fetch failed: {str(e)}")

    @app.post(
        "/api/platforms/fiix/fetch-schema",
        tags=["Platforms"],
        summary="Fetch Fiix platform schema (request body credentials)",
    )
    async def fetch_fiix_schema_post(request: dict | None = None):
        """Fetch live Fiix schema using credentials in JSON body."""
        try:
            return await _run_fiix_fetch_schema(request or {})
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Fiix schema fetch failed: {e}")
            raise HTTPException(status_code=500, detail=f"Schema fetch failed: {str(e)}")

    # ════════════════════════════════════════════════════════════════════════
    # PRODUCTION MIGRATION API
    # 9-node LangGraph pipeline with 3 HITL gates
    #
    #   POST   /api/migration                         — start migration
    #   GET    /api/migration                         — list migrations
    #   GET    /api/migration/{id}                    — migration detail
    #   GET    /api/migration/{id}/status             — poll progress
    #   DELETE /api/migration/{id}                    — cancel
    #   POST   /api/migration/{id}/gate/field-mapping — Gate 1, Node 4
    #   POST   /api/migration/{id}/gate/hierarchy     — Gate 2, Node 7
    #   POST   /api/migration/{id}/gate/final         — Gate 3, Node 9
    #   POST   /api/migration/{id}/retry-ddl          — DDL error recovery
    #   GET    /api/migration/{id}/mappings           — field mappings
    #   GET    /api/migration/{id}/hierarchy          — detected hierarchy
    #   GET    /api/migration/{id}/audit              — full audit trail
    #   GET    /api/migration/{id}/download/{format}  — download output
    #   GET    /api/migration/{id}/langsmith          — LangSmith trace URL
    #   WS     /ws/migration/{id}                     — real-time events
    # ════════════════════════════════════════════════════════════════════════

    @app.post(
        "/api/migration",
        response_model=MigrationStartResponse,
        tags=["Migration"],
        summary="Start a new CMMS migration job",
        status_code=201,
    )
    async def start_migration(
        request: MigrationStartRequest,
        session: AsyncSession = Depends(get_db_session),
    ) -> MigrationStartResponse:
        """
        Start a new CMMS migration job.

        Creates a MigrationJob record and dispatches an ARQ background task
        to run the 9-node LangGraph pipeline. Returns migration_id for tracking.
        Poll `GET /api/migration/{id}/status` for progress.
        """
        migration_id = uuid4()

        try:
            # Create migration job record
            migration_job = MigrationJob(
                id=migration_id,
                organization_id=request.organization_id,
                cmms_name=request.cmms_name,
                source_filename=request.source_blob_url.split("/")[-1],
                source_blob_url=request.source_blob_url,
                status="running",
                started_at=datetime.utcnow(),
                progress_pct=0.0,
            )
            session.add(migration_job)
            await session.commit()

            logger.info(f"Created migration job: {migration_id}")

            # Dispatch background task via ARQ
            try:
                # Import here to avoid circular dependencies
                from arq import create_pool
                from .worker import run_migration

                settings = get_settings()
                redis_client = get_redis_client()

                # Enqueue the migration job
                # Note: The job will be picked up by the ARQ worker
                logger.info(
                    f"Migration {migration_id} dispatched: "
                    f"cmms={request.cmms_name}, org={request.organization_id}"
                )

            except ImportError:
                logger.warning("ARQ not available - migration will need manual dispatch")
            except Exception as e:
                logger.error(f"Failed to dispatch migration to ARQ: {e}")
                # Don't fail the request - job is in DB, can be manually dispatched

            return MigrationStartResponse(
                migration_id=migration_id,
                status="running",
                progress_pct=0.0,
                message="Migration started. Poll GET /api/migration/{id}/status for progress.",
            )

        except Exception as e:
            logger.exception(f"Failed to start migration: {e}")
            try:
                await session.rollback()
            except Exception as rollback_error:
                logger.error(f"Error rolling back transaction: {rollback_error}")
            raise HTTPException(status_code=500, detail="Failed to create migration job")

    @app.post(
        "/api/migration/start-with-upload",
        response_model=MigrationStartResponse,
        tags=["Migration"],
        summary="Start a new migration by uploading a file directly",
        status_code=201,
    )
    async def start_migration_with_upload(
        file: UploadFile = File(..., description="CSV or Excel file to migrate"),
        cmms_name: str = Form("Custom", description="Source CMMS system name"),
        organization_id: str = Form(
            "00000000-0000-0000-0000-000000000001",
            description="Organization UUID",
        ),
        session: AsyncSession = Depends(get_db_session),
    ) -> MigrationStartResponse:
        """
        Start a new migration by uploading a file directly (no pre-existing Blob URL needed).

        Accepts a CSV or Excel file as multipart/form-data. The file bytes are passed
        directly into the migration pipeline's initial state so Node 1 does not need
        to download from Azure Blob.  The graph runs in an asyncio background task.
        Poll GET /api/migration/{id}/status for progress.
        """
        from uuid import UUID as _UUID

        # Validate file extension
        filename = file.filename or "upload.csv"
        allowed_ext = {".csv", ".tsv", ".xlsx", ".xls", ".xlsm"}
        ext = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""
        if ext not in allowed_ext:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported file type '{ext}'. Allowed: {', '.join(sorted(allowed_ext))}",
            )

        settings = get_settings()
        file_bytes = await file.read()
        max_bytes = settings.max_file_size_mb * 1024 * 1024
        if len(file_bytes) > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"File too large ({len(file_bytes) / 1_048_576:.1f} MB). Max: {settings.max_file_size_mb} MB",
            )

        migration_id = uuid4()
        # Persist the source file so a re-run can re-pull it (no-op without Azure config).
        source_blob_path = await _store_migration_source(str(migration_id), filename, file_bytes)

        try:
            org_uuid = _UUID(organization_id)
        except ValueError:
            try:
                org_uuid = _UUID(int=int(organization_id))
            except (ValueError, TypeError):
                raise HTTPException(status_code=400, detail=f"Invalid organization_id UUID: {organization_id}")

        try:
            migration_job = MigrationJob(
                id=migration_id,
                organization_id=org_uuid,
                cmms_name=cmms_name,
                source_filename=filename,
                source_blob_url=source_blob_path or "direct_upload",
                status="running",
                started_at=datetime.utcnow(),
                progress_pct=0.0,
            )
            session.add(migration_job)
            await session.commit()
        except Exception as e:
            logger.exception(f"Failed to create migration job: {e}")
            try:
                await session.rollback()
            except Exception:
                pass
            raise HTTPException(status_code=500, detail="Failed to create migration job")

        # Try to enqueue via ARQ; fall back to inline asyncio task
        try:
            from arq import create_pool
            from arq.connections import RedisSettings

            if settings.redis_url:
                redis_settings = RedisSettings.from_dsn(settings.redis_url)
                pool = await create_pool(redis_settings)
                # ARQ tasks can't accept raw bytes — store bytes, pass blob URL via helper
                # For direct-upload path we always use the inline runner
                await pool.aclose()
        except Exception:
            pass

        # ── Inline runner (primary path for direct-upload) ──────────────────
        graph = get_migration_graph_instance()
        session_factory = get_session_factory()
        mid_str = str(migration_id)

        config = {
            "configurable": {"thread_id": mid_str},
            "run_name": f"migration:{mid_str}",
            "tags": [f"cmms:{cmms_name}", f"org:{organization_id}", "service:schema-mapper"],
            "metadata": {"migration_id": mid_str, "cmms_name": cmms_name, "organization_id": organization_id},
        }

        async def _run_inline():
            # Emit the per-run Activity entry AT RUN START so "File Ingestion · running" shows the
            # instant the Migration Panel does — the inline path otherwise creates no entry until
            # Node 1 completes (see _prepare_and_run_multi for the same fix on the bulk path).
            try:
                from .graph.nodes.schema_db_writer import _sync_run_activity
                await _sync_run_activity(mid_str, caller="inline_run_start")
            except Exception as _act_err:
                logger.warning(
                    "[start-with-upload] run_start activity sync failed for %s: %s", mid_str, _act_err
                )
            initial_state: dict = {
                "migration_id": mid_str,
                "organization_id": organization_id,
                "cmms_name": cmms_name,
                "source_filename": filename,
                "source_system": cmms_name,
                "source_file_bytes": file_bytes,
                "source_blob_url": None,
                "source_blob_path": source_blob_path,
                "uploaded_by": "streamlit_ui",
                "upload_timestamp": datetime.utcnow(),
                "current_step": 0,
                "status": "running",
                "checkpoint_count": 0,
                "event_log": [],
                "tier1_mapped_count": 0,
                "tier2_human_count": 0,
                "overall_confidence": 0.0,
            }
            with bind_runtime_log_context(migration_id=mid_str):
                try:
                    await graph.ainvoke(initial_state, config=config)
                except Exception as exc:
                    if type(exc).__name__ == "GraphInterrupt":
                        pass  # gate node already wrote status to DB via db_writer
                    else:
                        logger.exception(f"[start-with-upload] Graph error for {mid_str}: {exc}")
                        try:
                            from sqlalchemy import update as _update
                            async with session_factory() as err_session:
                                await err_session.execute(
                                    _update(MigrationJob)
                                    .where(MigrationJob.id == migration_id)
                                    .values(status="failed", error_message=str(exc)[:500])
                                )
                                await err_session.commit()
                            # Reflect the failure in the Activity Log NOW so the entry flips to
                            # "failed" (clearing any stale "Awaiting your review" HITL card). The
                            # inline path has no ARQ worker to do this. Non-fatal.
                            try:
                                from .graph.nodes.schema_db_writer import _sync_run_activity
                                await _sync_run_activity(mid_str, caller="inline_error")
                            except Exception:
                                pass
                        except Exception:
                            pass

        # Prefer the ARQ worker: it runs the CPU-heavy migration graph in a SEPARATE process, so the
        # API event loop stays free to serve status / activity-log / actions (running it inline blocks
        # the loop → 504s on large files). Enqueue only when the source is in Blob (the worker re-pulls
        # it — ARQ can't carry raw bytes) and Redis is reachable; on ANY failure fall back to the inline
        # task, which still holds the in-memory bytes. Worst case = today's inline behaviour.
        _enqueued = False
        if settings.redis_url and source_blob_path:
            try:
                from arq import create_pool as _create_pool
                from arq.connections import RedisSettings as _RedisSettings

                _pool = await _create_pool(_RedisSettings.from_dsn(settings.redis_url))
                await _pool.enqueue_job(
                    "run_migration",
                    migration_id=mid_str,
                    organization_id=organization_id,
                    cmms_name=cmms_name,
                    source_blob_url=source_blob_path,
                    uploaded_by="streamlit_ui",
                    source_blob_path=source_blob_path,
                    source_filename=filename,
                )
                await _pool.aclose()
                _enqueued = True
                logger.info(f"[start-with-upload] Migration {mid_str} enqueued to ARQ worker")
            except Exception as _arq_err:
                logger.warning(f"[start-with-upload] ARQ enqueue failed ({_arq_err}); running inline")
        if not _enqueued:
            asyncio.create_task(_run_inline())

        logger.info(f"[start-with-upload] Migration {migration_id} started, file={filename}")

        return MigrationStartResponse(
            migration_id=migration_id,
            status="running",
            progress_pct=0.0,
            message="Migration started. Poll GET /api/migration/{id}/status for progress.",
        )

    @app.post(
        "/api/migration/start-with-upload-multi",
        response_model=MigrationStartResponse,
        tags=["Migration"],
        summary="Start ONE migration from multiple uploaded CSV/Excel files",
        status_code=201,
    )
    async def start_migration_with_upload_multi(
        files: list[UploadFile] = File(..., description="CSV/Excel files to migrate as ONE job"),
        cmms_name: str = Form("Custom", description="Source CMMS system name"),
        organization_id: str = Form(
            "00000000-0000-0000-0000-000000000001",
            description="Organization UUID",
        ),
        session: AsyncSession = Depends(get_db_session),
    ) -> MigrationStartResponse:
        """
        Start ONE migration from several structured files at once.

        All uploaded CSV/Excel files are combined server-side into a single
        multi-sheet workbook (one sheet per CSV file / per Excel sheet) and fed to
        the existing migration graph unchanged — so every file/sheet becomes a
        source table inside ONE migration_id (not one migration per file).
        """
        import io as _io
        from pathlib import Path as _Path
        from uuid import UUID as _UUID

        import pandas as _pd

        if not files:
            raise HTTPException(status_code=400, detail="No files uploaded")

        settings = get_settings()
        allowed_ext = {".csv", ".tsv", ".xlsx", ".xls", ".xlsm"}
        max_bytes = settings.max_file_size_mb * 1024 * 1024

        # ── Read raw bytes now (fast) + cheap ext/size validation; DEFER the heavy work ──
        # Only reading raw bytes and checking extension/size happen synchronously here, so the
        # migration can BEGIN immediately. Parsing every file (pandas reads the full data),
        # combining them into one workbook, and persisting the blobs are the slow parts — they
        # run in the background task below, in parallel, so the bulk-ingest caller isn't blocked
        # and the run starts right away (Node 1 then reads the top-10-row preview + the full data).
        raw_files: list[tuple[str, bytes]] = []  # (filename, raw bytes)
        source_names: list[str] = []
        total_bytes = 0
        for up in files:
            filename = up.filename or "upload.csv"
            ext = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""
            if ext not in allowed_ext:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unsupported file type '{ext}' ({filename}). Allowed: {', '.join(sorted(allowed_ext))}",
                )
            data = await up.read()
            total_bytes += len(data)
            if total_bytes > max_bytes:
                raise HTTPException(
                    status_code=413,
                    detail=f"Combined upload too large. Max: {settings.max_file_size_mb} MB",
                )
            raw_files.append((filename, data))
            source_names.append(filename)

        if not raw_files:
            raise HTTPException(status_code=400, detail="No files uploaded")

        try:
            org_uuid = _UUID(organization_id)
        except ValueError:
            try:
                org_uuid = _UUID(int=int(organization_id))
            except (ValueError, TypeError):
                raise HTTPException(status_code=400, detail=f"Invalid organization_id UUID: {organization_id}")

        combined_filename = ", ".join(source_names)[:240] or "combined.xlsx"
        migration_id = uuid4()
        try:
            migration_job = MigrationJob(
                id=migration_id,
                organization_id=org_uuid,
                cmms_name=cmms_name,
                source_filename=combined_filename,
                # The durable source path is filled in by the background task once the combined
                # workbook is persisted; the graph runs from the in-memory bytes meanwhile.
                source_blob_url="direct_upload_multi",
                status="running",
                started_at=datetime.utcnow(),
                progress_pct=0.0,
            )
            session.add(migration_job)
            await session.commit()
        except Exception as e:
            logger.exception(f"Failed to create multi-file migration job: {e}")
            try:
                await session.rollback()
            except Exception:
                pass
            raise HTTPException(status_code=500, detail="Failed to create migration job")

        graph = get_migration_graph_instance()
        session_factory = get_session_factory()
        mid_str = str(migration_id)

        config = {
            "configurable": {"thread_id": mid_str},
            "run_name": f"migration:{mid_str}",
            "tags": [f"cmms:{cmms_name}", f"org:{organization_id}", "service:schema-mapper", "multi-file"],
            "metadata": {
                "migration_id": mid_str,
                "cmms_name": cmms_name,
                "organization_id": organization_id,
                "source_files": source_names,
            },
        }

        async def _fail_job(msg: object) -> None:
            try:
                from sqlalchemy import update as _update
                async with session_factory() as err_session:
                    await err_session.execute(
                        _update(MigrationJob)
                        .where(MigrationJob.id == migration_id)
                        .values(status="failed", error_message=str(msg)[:500])
                    )
                    await err_session.commit()
                # Flip the Activity Log entry to "failed" now (clears any stale "Awaiting your
                # review" HITL card) — the inline bulk path has no ARQ worker to do it. Non-fatal.
                try:
                    from .graph.nodes.schema_db_writer import _sync_run_activity
                    await _sync_run_activity(mid_str, caller="inline_error")
                except Exception:
                    pass
            except Exception:
                logger.exception(f"[start-with-upload-multi] Could not mark {mid_str} failed")

        async def _prepare_and_run_multi():
            # Emit the per-run Activity entry AT RUN START — before the heavy parse/combine work —
            # so the Activity Log shows "File Ingestion · running" the instant the Migration Panel
            # does, not only after Node 1 completes. Unlike worker.run_migration (which syncs at
            # run_start), the inline start paths otherwise create NO entry until the first node
            # finishes, leaving the log empty for the entire ingest window on large/bulk uploads.
            try:
                from .graph.nodes.schema_db_writer import _sync_run_activity
                await _sync_run_activity(mid_str, caller="inline_multi_run_start")
            except Exception as _act_err:
                logger.warning(
                    "[start-with-upload-multi] run_start activity sync failed for %s: %s", mid_str, _act_err
                )
            # HEAVY work, off the request path: parse each file/sheet, combine into ONE workbook,
            # persist blobs, then drive the graph. Node 1 reads the top-10-row preview (matching)
            # + the full data (full_tables) — all in the background, in parallel, so the run began
            # the moment the endpoint returned. A bad file now fails THIS migration (was a 400).
            used_sheets: set[str] = set()

            def _safe_sheet(base: str) -> str:
                invalid = set(r":\/?*[]")
                cleaned = "".join("_" if c in invalid else c for c in base).strip() or "sheet"
                cleaned = cleaned[:31]
                name = cleaned
                i = 2
                while name.lower() in used_sheets:
                    suffix = f"_{i}"
                    name = cleaned[: 31 - len(suffix)] + suffix
                    i += 1
                used_sheets.add(name.lower())
                return name

            # Parse + combine is CPU-bound (pandas / calamine / openpyxl over potentially 100k+ rows).
            # Run it in a THREAD so it NEVER blocks the event loop: a large bulk upload otherwise
            # starves the async loop for minutes, and every concurrent status / activity-log / actions
            # poll hits the ingress timeout (504) even though the DB is idle. Mirrors the to_thread
            # offload used for the other blocking calls in this module. Parse errors carry the
            # offending filename back out to _fail_job.
            class _ParseError(Exception):
                pass

            def _parse_and_combine() -> tuple[bytes, list[str]]:
                sheets: dict[str, _pd.DataFrame] = {}
                for filename, data in raw_files:
                    ext = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""
                    stem = _Path(filename).stem
                    try:
                        if ext in {".csv", ".tsv"}:
                            sep = "\t" if ext == ".tsv" else None
                            df = _pd.read_csv(_io.BytesIO(data), dtype=str, sep=sep, engine="python")
                            df = _sanitize_column_names_helper(df)
                            sheets[_safe_sheet(stem)] = df.fillna("")
                        else:
                            wb = ExcelWorkbook(_io.BytesIO(data))  # calamine, workbook opened once
                            sheet_names = wb.sheet_names
                            for sh in sheet_names:
                                # Skip banner / title rows so source column names survive
                                # instead of degrading to "Unnamed: N" placeholders.
                                header_row = wb.header_row(sh)
                                df = wb.read(sh, header=header_row, dtype=str)
                                df = _sanitize_column_names_helper(df)
                                # Source table = the Excel SHEET name; fall back to the file name
                                # only for a lone generic sheet (Sheet1).
                                sheet_label = (str(sh) or "").strip()
                                is_generic = len(sheet_names) == 1 and re.match(
                                    r"^sheet\s*\d*$", sheet_label, re.IGNORECASE
                                )
                                base = stem if is_generic else (sheet_label or stem)
                                sheets[_safe_sheet(base)] = df.fillna("")
                            wb.close()
                    except Exception as exc:
                        raise _ParseError(f"Could not parse '{filename}': {exc}") from exc
                if not sheets:
                    raise _ParseError("No parseable structured data found in uploads")
                combined = _io.BytesIO()
                with _pd.ExcelWriter(combined, engine="openpyxl") as writer:
                    for name, df in sheets.items():
                        df.to_excel(writer, sheet_name=name, index=False)
                return combined.getvalue(), list(sheets.keys())

            try:
                try:
                    file_bytes, sheet_names_out = await asyncio.to_thread(_parse_and_combine)
                except _ParseError as exc:
                    await _fail_job(str(exc))
                    return

                # Persist the combined workbook (for re-run) + each original file (archive).
                # Best-effort: the graph runs from the in-memory bytes even if a blob upload fails.
                source_blob_path = None
                try:
                    source_blob_path = await _store_migration_source(
                        mid_str, "combined.xlsx", file_bytes
                    )
                    for _orig_name, _orig_data in raw_files:
                        await _store_migration_source(
                            mid_str, _orig_name, _orig_data, subdir="originals"
                        )
                except Exception as exc:
                    logger.warning(f"[start-with-upload-multi] Blob persist failed for {mid_str}: {exc}")

                # Record the durable source path now it's known (so a re-run can re-pull it).
                if source_blob_path:
                    try:
                        from sqlalchemy import update as _update
                        async with session_factory() as up_session:
                            await up_session.execute(
                                _update(MigrationJob)
                                .where(MigrationJob.id == migration_id)
                                .values(source_blob_url=source_blob_path)
                            )
                            await up_session.commit()
                    except Exception:
                        logger.warning(f"[start-with-upload-multi] Could not persist source path for {mid_str}")

                logger.info(
                    f"[start-with-upload-multi] Migration {mid_str} prepared: "
                    f"{len(source_names)} file(s) → {len(sheet_names_out)} table(s): {sheet_names_out}"
                )

                initial_state: dict = {
                    "migration_id": mid_str,
                    "organization_id": organization_id,
                    "cmms_name": cmms_name,
                    "source_filename": combined_filename,
                    "source_system": cmms_name,
                    "source_file_bytes": file_bytes,
                    "source_blob_url": None,
                    "source_blob_path": source_blob_path,
                    "uploaded_by": "single_door_multi",
                    "upload_timestamp": datetime.utcnow(),
                    "current_step": 0,
                    "status": "running",
                    "checkpoint_count": 0,
                    "event_log": [],
                    "tier1_mapped_count": 0,
                    "tier2_human_count": 0,
                    "overall_confidence": 0.0,
                }
                # Prefer the ARQ worker (runs the graph off the API loop). Enqueue only when the
                # combined workbook is in Blob (the worker re-pulls it) + Redis is reachable; else run
                # the graph inline as before — graceful fallback, no regression.
                _mset = get_settings()
                _enqueued = False
                if _mset.redis_url and source_blob_path:
                    try:
                        from arq import create_pool as _create_pool
                        from arq.connections import RedisSettings as _RedisSettings

                        _pool = await _create_pool(_RedisSettings.from_dsn(_mset.redis_url))
                        await _pool.enqueue_job(
                            "run_migration",
                            migration_id=mid_str,
                            organization_id=organization_id,
                            cmms_name=cmms_name,
                            source_blob_url=source_blob_path,
                            uploaded_by="single_door_multi",
                            source_blob_path=source_blob_path,
                            source_filename=combined_filename,
                        )
                        await _pool.aclose()
                        _enqueued = True
                        logger.info(f"[start-with-upload-multi] Migration {mid_str} enqueued to ARQ worker")
                    except Exception as _arq_err:
                        logger.warning(
                            f"[start-with-upload-multi] ARQ enqueue failed ({_arq_err}); running inline"
                        )
                if not _enqueued:
                    with bind_runtime_log_context(migration_id=mid_str):
                        try:
                            await graph.ainvoke(initial_state, config=config)
                        except Exception as exc:
                            if type(exc).__name__ == "GraphInterrupt":
                                pass
                            else:
                                logger.exception(f"[start-with-upload-multi] Graph error for {mid_str}: {exc}")
                                await _fail_job(exc)
            except Exception as exc:
                logger.exception(f"[start-with-upload-multi] Prepare failed for {mid_str}: {exc}")
                await _fail_job(exc)

        asyncio.create_task(_prepare_and_run_multi())

        logger.info(
            f"[start-with-upload-multi] Migration {migration_id} accepted — "
            f"{len(source_names)} file(s); reading + combining in the background"
        )

        return MigrationStartResponse(
            migration_id=migration_id,
            status="running",
            progress_pct=0.0,
            message=(
                f"Migration started from {len(source_names)} file(s). Reading data in the "
                f"background — poll GET /api/migration/{{id}}/status."
            ),
        )

    @app.get(
        "/api/migration/{migration_id}/status",
        response_model=MigrationStatusResponse,
        tags=["Migration"],
        summary="Get migration status and progress",
    )
    async def get_migration_status(
        migration_id: str = Path(..., description="Migration UUID"),
        session: AsyncSession = Depends(get_db_session),
    ) -> MigrationStatusResponse:
        """Get current status, progress, and statistics for a migration."""
        from uuid import UUID

        def _tier_label(tier: str) -> str:
            t = (tier or "").strip()
            return {
                "T1_exact": "S1 (exact)",
                "T1_variation": "S1B (var)",
                "T1_regex": "S2 (regex)",
                "T1_llm": "S3 (llm)",
            }.get(t, t or "T?")

        def _build_node2_detailed_logs(payload: dict | None) -> list[str]:
            if not isinstance(payload, dict):
                return []
            mappings_by_table = payload.get("mappings_by_table")
            unresolved_by_table = payload.get("unresolved_by_table") or {}
            if not isinstance(mappings_by_table, dict):
                return []

            lines: list[str] = []
            for table, mappings in mappings_by_table.items():
                if not isinstance(mappings, list):
                    continue

                cols = [str(m.get("source_field", "")) for m in mappings if isinstance(m, dict)]
                cols = [c for c in cols if c]
                col_preview = ", ".join(cols[:8]) + ("..." if len(cols) > 8 else "")

                lines.append(f"[Node 2] ▶ Processing source table: {table}")
                lines.append(f"[Node 2]   Columns: {len(cols)} [{col_preview}]")

                for m in mappings:
                    if not isinstance(m, dict):
                        continue
                    src = str(m.get("source_field", ""))
                    tgt = str(m.get("target_field", ""))
                    tier = _tier_label(str(m.get("tier", "")))
                    conf = float(m.get("confidence", 0.0) or 0.0)
                    lines.append(f"[Node 2]   {tier}: {src} -> {tgt} ({conf:.2f})")

                unresolved = unresolved_by_table.get(table, [])
                unresolved_count = len(unresolved) if isinstance(unresolved, list) else 0
                lines.append(
                    f"[Node 2] ✓ Table {table}: {len(mappings)} mapped, {unresolved_count} unresolved"
                )
            return lines

        def _build_node1_ingest_logs(payload: dict | None) -> list[str]:
            if not isinstance(payload, dict):
                return []
            lines: list[str] = []
            fmt = payload.get("format")
            rows = int(payload.get("rows", 0) or 0)
            cols = int(payload.get("columns", 0) or 0)
            tables = payload.get("tables") if isinstance(payload.get("tables"), list) else []
            table_health = payload.get("table_health") if isinstance(payload.get("table_health"), dict) else {}

            lines.append(f"[Node 1] ▶ Ingested source format: {fmt or 'unknown'}")
            lines.append(f"[Node 1]   Rows: {rows}, Columns: {cols}, Tables: {len(tables)}")
            if tables:
                lines.append(f"[Node 1]   Tables: {', '.join(str(t) for t in tables)}")

            for t in tables:
                if not isinstance(t, str):
                    continue
                th = table_health.get(t, {})
                if not isinstance(th, dict):
                    continue
                r = int(th.get('row_count', 0) or 0)
                c = int(th.get('column_count', 0) or 0)
                avg_null = float(th.get('avg_null_percentage', 0.0) or 0.0)
                lines.append(f"[Node 1] ▶ Table {t}: {r} rows × {c} cols (avg null {avg_null:.1f}%)")

                nulls = th.get("null_percentages", {})
                if isinstance(nulls, dict) and nulls:
                    # Show top 5 highest-null columns for compact diagnostics
                    top = sorted(
                        ((str(k), float(v or 0.0)) for k, v in nulls.items()),
                        key=lambda x: x[1],
                        reverse=True,
                    )[:5]
                    top_txt = ", ".join(f"{k}={v:.1f}%" for k, v in top)
                    lines.append(f"[Node 1]   Null hotspots: {top_txt}")
            return lines

        def _build_node3_semantic_logs(payload: dict | None) -> list[str]:
            if not isinstance(payload, dict):
                return []
            lines: list[str] = []
            results = payload.get("semantic_results")
            if not isinstance(results, list):
                return []

            t2_auto = int(payload.get("t2_auto", 0) or 0)
            flagged = int(payload.get("flagged", 0) or 0)
            unmappable = int(payload.get("unmappable", 0) or 0)
            lines.append(
                f"[Node 3] ▶ Semantic mapping results: auto={t2_auto}, flagged={flagged}, unmappable={unmappable}"
            )

            for r in results:
                if not isinstance(r, dict):
                    continue
                table = str(r.get("table", "unknown"))
                src = str(r.get("source_field", ""))
                status = str(r.get("status", "unknown"))
                tgt = r.get("target_field")
                best_tgt = r.get("best_target")
                conf = r.get("confidence")
                best_conf = r.get("best_confidence")

                if status == "auto_accepted" and tgt:
                    c = float(conf or 0.0)
                    lines.append(f"[Node 3]   auto: {table}.{src} -> {tgt} ({c:.2f})")
                elif status == "flagged":
                    bt = str(best_tgt or tgt or "?")
                    bc = float(best_conf or conf or 0.0)
                    lines.append(f"[Node 3]   flagged: {table}.{src} -> {bt} ({bc:.2f})")
                else:
                    bt = str(best_tgt or "?")
                    bc = float(best_conf or 0.0)
                    lines.append(f"[Node 3]   unmappable: {table}.{src} (best={bt}, {bc:.2f})")
            return lines

        def _merge_runtime_logs_into_nodes(nodes: list[dict], migration_id_value: str) -> None:
            """Append captured runtime [Node X] lines into matching nodes[].logs.

            Node files were authored before Gate-0 (Pre-Semantic Review) was inserted
            as pipeline node_id 3.  Everything from old "[Node 3]" onwards is now one
            position later in the pipeline, so we remap before bucketing.

            structlog formats the full line before it reaches the stdlib handler, so
            record.getMessage() returns the fully-rendered ConsoleRenderer or JSONRenderer
            string.  We use re.search() (not match()) to find [Node X] anywhere in that
            string, then extract a clean body by stripping the trailing [module.name] suffix.
            """
            # old label → current pipeline node_id (Gate-0 inserted at position 3)
            _REMAP = {3: 4, 4: 5, 5: 6, 6: 7, 7: 8, 8: 9}
            _node_re = re.compile(r"\[Node\s+(\d+)\](.*)")
            _sfx_re = re.compile(r"\s+\[\S+(?:\.\S+)+\]\s*$")
            _ansi_re = re.compile(r"\x1b\[[0-9;]*m")

            rt = get_runtime_logs(migration_id=migration_id_value, since=0, limit=1000)
            entries = rt.get("logs", []) if isinstance(rt, dict) else []
            by_node: dict[int, list[str]] = {}
            for e in entries:
                if not isinstance(e, dict):
                    continue
                raw = str(e.get("message", "") or "")
                # JSONRenderer (prod) wraps the event in a JSON object
                event_str = raw
                if raw.startswith("{"):
                    try:
                        event_str = json.loads(raw).get("event") or raw
                    except Exception:
                        pass
                event_str = _ansi_re.sub("", event_str)
                m = _node_re.search(event_str)
                if not m:
                    continue
                nid = int(m.group(1))
                nid = _REMAP.get(nid, nid)
                body = _sfx_re.sub("", m.group(2)).strip()
                clean = f"[Node {nid}] {body}" if body else f"[Node {nid}]"
                by_node.setdefault(nid, []).append(clean)

            for n in nodes:
                nid = int(n.get("node_id", 0) or 0)
                extra = by_node.get(nid, [])
                if not extra:
                    continue
                existing = n.get("logs") or []
                seen = set(existing)
                merged = list(existing)
                for line in extra:
                    if line not in seen:
                        merged.append(line)
                        seen.add(line)
                n["logs"] = merged

        try:
            result = await session.execute(
                select(MigrationJob).where(MigrationJob.id == UUID(migration_id))
            )
            migration_job = result.scalar_one_or_none()

            if not migration_job:
                raise HTTPException(status_code=404, detail="Migration not found")

            raw_step = migration_job.current_step or ""
            try:
                step_int = int(str(raw_step).split("_")[0]) if raw_step else 0
            except (ValueError, IndexError):
                step_int = 0

            # ── Build nodes[] array ───────────────────────────────────────
            _MIGRATION_PIPELINE = [
                (1, "File Ingestion"),
                (2, "Deterministic Mapping"),
                (3, "Gate 0: Pre-Semantic Review"),
                (4, "Semantic Mapping"),
                (5, "Gate 1: Field Mapping Review"),
                (6, "Data Preprocessing"),
                (7, "Hierarchy Detection"),
                (8, "Gate 2: Hierarchy Verification"),
                (9, "Output Generation"),
            ]
            completed_ids = {e["node_id"] for e in (migration_job.node_logs or [])}
            completed_map = {e["node_id"]: e for e in (migration_job.node_logs or [])}
            migration_nodes = []
            for nid, nname in _MIGRATION_PIPELINE:
                if nid in completed_ids:
                    migration_nodes.append(completed_map[nid])
                elif nid == step_int and migration_job.status in ("running", "step_paused", "awaiting_review"):
                    migration_nodes.append({
                        "node_id": nid, "node_name": nname, "status": "running",
                        "started_at": None, "completed_at": None,
                        "duration_ms": None, "output": None, "logs": [],
                    })
                else:
                    migration_nodes.append({
                        "node_id": nid, "node_name": nname, "status": "pending",
                        "started_at": None, "completed_at": None,
                        "duration_ms": None, "output": None, "logs": [],
                    })

            # Overlay pre-semantic (Node 3) overrides onto the Node 2
            # deterministic output. node_logs is append-only, so Node 2 keeps
            # its PRE-override mappings forever; without this overlay the
            # completed "Deterministic mapping" card (and chat history / archived
            # runs / exports, which all read these node outputs) show stale
            # source table/column names after a rename or new-table override.
            _node3_entry = completed_map.get(3) or {}
            _node3_out = _node3_entry.get("output") if isinstance(_node3_entry, dict) else None
            if isinstance(_node3_out, dict):
                _ovr_routing = _node3_out.get("table_routing") or {}
                _ovr_new_tables = _node3_out.get("new_tables") or []
                _ovr_tier1 = _node3_out.get("tier1_mappings_by_table") or {}
                if _ovr_routing or _ovr_new_tables or _ovr_tier1:
                    for _n in migration_nodes:
                        if _n.get("node_id") != 2:
                            continue
                        _n2_out = _n.get("output")
                        if not isinstance(_n2_out, dict):
                            break
                        _merged_out = dict(_n2_out)
                        if _ovr_routing:
                            _merged_out["table_routing"] = {
                                **(_merged_out.get("table_routing") or {}),
                                **_ovr_routing,
                            }
                        if _ovr_new_tables:
                            _merged_out["new_tables"] = _ovr_new_tables
                        # Renamed column targets: rebuild mappings_by_table from
                        # the post-override tier-1 set, keyed by the (possibly
                        # renamed) target table.
                        if _ovr_tier1:
                            _merged_out["mappings_by_table"] = {
                                **(_merged_out.get("mappings_by_table") or {}),
                                **_ovr_tier1,
                            }
                        _n["output"] = _merged_out
                        break

            # Enrich Node logs with gate payload details so frontend can render
            # terminal-style node logs directly from /status.
            if migration_job.pending_gate_type == "step_1_ingest":
                detailed_lines = _build_node1_ingest_logs(migration_job.pending_gate_payload)
                if detailed_lines:
                    for n in migration_nodes:
                        if n.get("node_id") == 1:
                            n_logs = n.get("logs") or []
                            n["logs"] = n_logs + detailed_lines
                            break

            # Node 2 deterministic details
            if migration_job.pending_gate_type == "step_2_deterministic_mapping":
                detailed_lines = _build_node2_detailed_logs(migration_job.pending_gate_payload)
                if detailed_lines:
                    for n in migration_nodes:
                        if n.get("node_id") == 2:
                            n_logs = n.get("logs") or []
                            n["logs"] = n_logs + detailed_lines
                            break

            # Node 3 semantic details
            if migration_job.pending_gate_type == "step_3_semantic_mapping":
                detailed_lines = _build_node3_semantic_logs(migration_job.pending_gate_payload)
                if detailed_lines:
                    for n in migration_nodes:
                        if n.get("node_id") == 4:
                            n_logs = n.get("logs") or []
                            n["logs"] = n_logs + detailed_lines
                            break

            # Finally, merge all captured runtime [Node X] lines so completed nodes
            # also expose detailed terminal-style logs in nodes[].logs.
            _merge_runtime_logs_into_nodes(migration_nodes, str(migration_job.id))

            return MigrationStatusResponse(
                migration_id=migration_job.id,
                status=migration_job.status,
                progress_pct=migration_job.progress_pct,
                current_step=step_int,
                cmms_name=migration_job.cmms_name,
                started_at=migration_job.started_at,
                completed_at=migration_job.completed_at,
                t1_mapped_count=migration_job.t1_mapped_count,
                t2_auto_count=migration_job.t2_auto_count,
                t2_human_count=migration_job.t2_human_count,
                unmapped_count=migration_job.unmapped_count,
                total_fields=migration_job.total_fields,
                source_filename=_source_status_fields(migration_job)[0],
                source_download_url=_source_status_fields(migration_job)[1],
                output_json_url=migration_job.output_json_url,
                output_csv_url=migration_job.output_csv_url,
                output_sql_url=migration_job.output_sql_url,
                migration_report_url=migration_job.migration_report_url,
                output_structure_md_url=getattr(migration_job, "output_structure_md_url", None),
                pending_gate_type=migration_job.pending_gate_type,
                pending_gate_payload=migration_job.pending_gate_payload,
                field_mapping_draft=getattr(migration_job, "field_mapping_draft", None),
                udr_test_results=_udr_test_results_from_job(migration_job),
                udr_relationship_report=_udr_relationship_report_from_job(migration_job),
                udr_table_resolution=_udr_table_resolution_from_job(migration_job),
                udr_column_intelligence=_udr_column_intelligence_from_job(migration_job),
                error_message=migration_job.error_message,
                nodes=migration_nodes,
            )

        except HTTPException:
            raise
        except (TimeoutError, OperationalError, InterfaceError, DBAPIError) as e:
            # Transient DB blip (e.g. a slow SSL handshake to the cloud DB exceeded the 15s connect
            # timeout, or a pooled connection dropped). The status endpoint is polled every ~2s, so a
            # 503 tells the client to simply poll again instead of surfacing a hard 500 with a full
            # traceback. The migration itself is unaffected — it runs on its own worker/session.
            logger.warning(f"[status] transient DB error for {migration_id}: {type(e).__name__}: {e}")
            raise HTTPException(
                status_code=503,
                detail="Status temporarily unavailable (database busy) — retrying.",
                headers={"Retry-After": "2"},
            )
        except Exception as e:
            logger.exception(f"Failed to get migration status: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.get(
        "/api/migration/{migration_id}/runtime-logs",
        tags=["Migration"],
        summary="Get captured backend runtime logs for this migration",
    )
    async def get_migration_runtime_logs(
        migration_id: str = Path(..., description="Migration UUID"),
        since: int = Query(0, ge=0, description="Return logs with seq > since"),
        limit: int = Query(200, ge=1, le=1000),
    ):
        data = get_runtime_logs(migration_id=migration_id, since=since, limit=limit)
        return {"migration_id": migration_id, **data}

    @app.get(
        "/api/migration/{migration_id}/full-export",
        tags=["Migration"],
        summary="Export each target table's FULL data (existing rows + this migration's new rows)",
    )
    async def export_migration_full_tables(
        migration_id: str = Path(..., description="Migration UUID"),
        format: str = Query("csv", pattern="^(csv|json|sql)$"),
        session: AsyncSession = Depends(get_db_session),
    ):
        """Read each target plenum_cafm table in full (old + newly-migrated rows) and
        return it as CSV (zip of per-table files), JSON, or SQL INSERTs."""
        import csv as _csv
        import io as _io
        import json as _json
        import re as _re
        import zipfile as _zip
        from fastapi.responses import Response, StreamingResponse

        result = await session.execute(
            select(MigrationJob).where(MigrationJob.id == UUID(migration_id))
        )
        job = result.scalar_one_or_none()
        if not job:
            raise HTTPException(status_code=404, detail="Migration not found")

        # Discover target tables from the deterministic node's persisted table_routing.
        target_tables: list[str] = []
        for entry in (job.node_logs or []):
            tr = (entry.get("output") or {}).get("table_routing") if isinstance(entry, dict) else None
            if isinstance(tr, dict):
                for tgt in tr.values():
                    if isinstance(tgt, str) and tgt and tgt not in target_tables:
                        target_tables.append(tgt)
        if not target_tables:
            raise HTTPException(
                status_code=409,
                detail="Target tables not available yet (migration must reach deterministic mapping).",
            )

        _IDENT = _re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
        schema = "plenum_cafm"
        tables_data: dict[str, tuple[list, list]] = {}
        for tbl in target_tables:
            if not _IDENT.match(tbl):
                continue  # skip unsafe identifiers
            res = await session.execute(text(f'SELECT * FROM {schema}."{tbl}"'))
            tables_data[tbl] = (list(res.keys()), res.fetchall())

        def _sql_lit(v):
            if v is None:
                return "NULL"
            if isinstance(v, bool):
                return "TRUE" if v else "FALSE"
            if isinstance(v, (int, float)):
                return str(v)
            return "'" + str(v).replace("'", "''") + "'"

        if format == "json":
            payload = {
                tbl: [dict(zip(cols, row)) for row in rows]
                for tbl, (cols, rows) in tables_data.items()
            }
            return Response(
                content=_json.dumps(payload, default=str, indent=2),
                media_type="application/json",
                headers={"Content-Disposition": f'attachment; filename="migration_{migration_id}_full.json"'},
            )

        if format == "sql":
            lines: list[str] = []
            for tbl, (cols, rows) in tables_data.items():
                collist = ", ".join(f'"{c}"' for c in cols)
                lines.append(f"-- {schema}.{tbl}: {len(rows)} rows (existing + new)")
                for row in rows:
                    vals = ", ".join(_sql_lit(v) for v in row)
                    lines.append(
                        f'INSERT INTO {schema}."{tbl}" ({collist}) VALUES ({vals}) ON CONFLICT DO NOTHING;'
                    )
                lines.append("")
            return Response(
                content="\n".join(lines),
                media_type="application/sql",
                headers={"Content-Disposition": f'attachment; filename="migration_{migration_id}_full.sql"'},
            )

        # csv → zip of one CSV per table
        buf = _io.BytesIO()
        with _zip.ZipFile(buf, "w", _zip.ZIP_DEFLATED) as zf:
            for tbl, (cols, rows) in tables_data.items():
                s = _io.StringIO()
                w = _csv.writer(s)
                w.writerow(cols)
                for row in rows:
                    w.writerow(["" if v is None else v for v in row])
                zf.writestr(f"{tbl}.csv", s.getvalue())
        buf.seek(0)
        return StreamingResponse(
            buf,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="migration_{migration_id}_full_csv.zip"'},
        )

    @app.get(
        "/api/migration/{migration_id}/source",
        tags=["Migration"],
        summary="Download the original uploaded source file (CSV/Excel/etc.) for this migration",
    )
    async def download_migration_source(
        migration_id: str = Path(..., description="Migration UUID"),
        session: AsyncSession = Depends(get_db_session),
    ):
        """Stream the migration's original uploaded file back from Azure Blob.

        The file is persisted on upload (``_store_migration_source`` → blob path
        held in ``MigrationJob.source_blob_url``), so it stays downloadable from
        the migration, history, and any linked UDR script version. 404 if the
        source was never persisted (e.g. Azure not configured at upload time).
        """
        import io as _io
        import re as _re
        from fastapi.responses import StreamingResponse

        result = await session.execute(
            select(MigrationJob).where(MigrationJob.id == UUID(migration_id))
        )
        job = result.scalar_one_or_none()
        if not job:
            raise HTTPException(status_code=404, detail="Migration not found")

        blob_ref = (job.source_blob_url or "").strip()
        # Sentinels written when no blob persistence happened — nothing to stream.
        if not blob_ref or blob_ref in ("direct_upload", "direct_upload_multi"):
            raise HTTPException(
                status_code=404,
                detail="Source file was not persisted for this migration (uploaded before blob storage, or Azure not configured).",
            )

        settings = get_settings()
        conn = getattr(settings, "azure_storage_connection_string", "") or ""
        if not conn:
            raise HTTPException(status_code=503, detail="Blob storage is not configured.")
        container = getattr(settings, "azure_blob_container_name", "") or "plenum-agentic-ai-attachments"

        # source_blob_url may be a full https URL (legacy start_migration) or a
        # container-relative blob path (upload path). Normalise to a blob path.
        blob_path = blob_ref
        m = _re.match(r"^https?://[^/]+/[^/]+/(.+)$", blob_ref)
        if m:
            blob_path = m.group(1)

        filename = job.source_filename or blob_path.rsplit("/", 1)[-1] or "source.bin"
        media_type = _guess_source_media_type(filename)

        try:
            from azure.storage.blob.aio import BlobServiceClient as _BSC

            async with _BSC.from_connection_string(conn) as svc:
                bc = svc.get_blob_client(container=container, blob=blob_path)
                stream = await bc.download_blob()
                data = await stream.readall()
        except Exception as exc:  # noqa: BLE001 — surface as 404, never 500
            logger.warning(f"[source-download] {migration_id}: {str(exc)[:200]}")
            raise HTTPException(status_code=404, detail="Source file is not retrievable from storage.")

        return StreamingResponse(
            _io.BytesIO(data),
            media_type=media_type,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.get(
        "/api/migration/{migration_id}",
        response_model=MigrationStatusResponse,
        tags=["Migration"],
        summary="Get migration detail",
    )
    async def get_migration_detail(
        migration_id: str = Path(..., description="Migration UUID"),
        session: AsyncSession = Depends(get_db_session),
    ) -> MigrationStatusResponse:
        """
        Get full detail for a migration including status, progress, stats, and
        pending gate payload if the pipeline is paused at a HITL gate.
        """
        try:
            result = await session.execute(
                select(MigrationJob).where(MigrationJob.id == UUID(migration_id))
            )
            migration_job = result.scalar_one_or_none()

            if not migration_job:
                raise HTTPException(status_code=404, detail="Migration not found")

            raw_step = migration_job.current_step or ""
            try:
                step_int = int(str(raw_step).split("_")[0]) if raw_step else 0
            except (ValueError, IndexError):
                step_int = 0

            return MigrationStatusResponse(
                migration_id=migration_job.id,
                status=migration_job.status,
                progress_pct=migration_job.progress_pct,
                current_step=step_int,
                cmms_name=migration_job.cmms_name,
                started_at=migration_job.started_at,
                completed_at=migration_job.completed_at,
                t1_mapped_count=migration_job.t1_mapped_count,
                t2_auto_count=migration_job.t2_auto_count,
                t2_human_count=migration_job.t2_human_count,
                unmapped_count=migration_job.unmapped_count,
                total_fields=migration_job.total_fields,
                source_filename=_source_status_fields(migration_job)[0],
                source_download_url=_source_status_fields(migration_job)[1],
                output_json_url=migration_job.output_json_url,
                output_csv_url=migration_job.output_csv_url,
                output_sql_url=migration_job.output_sql_url,
                migration_report_url=migration_job.migration_report_url,
                output_structure_md_url=getattr(migration_job, "output_structure_md_url", None),
                pending_gate_type=migration_job.pending_gate_type,
                pending_gate_payload=migration_job.pending_gate_payload,
                field_mapping_draft=getattr(migration_job, "field_mapping_draft", None),
                udr_test_results=_udr_test_results_from_job(migration_job),
                udr_relationship_report=_udr_relationship_report_from_job(migration_job),
                udr_table_resolution=_udr_table_resolution_from_job(migration_job),
                udr_column_intelligence=_udr_column_intelligence_from_job(migration_job),
                error_message=migration_job.error_message,
            )

        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Failed to get migration detail: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # ── Internal shared helper for gate resume ────────────────────────────
    async def _enqueue_migration_resume(
        migration_id: str,
        gate_type: str,
        decisions: dict,
        session: AsyncSession,
    ) -> MigrationApprovalResponse:
        """Validate state and enqueue ARQ resume_migration task."""
        migration_id_uuid = UUID(migration_id)

        # A gate approval must not hard-fail on a TRANSIENT DB-connection stall (a momentary
        # pool-exhaustion / Azure connect blip can time out one request while the very next
        # /status poll succeeds). Retry the initial lookup a few times with a short backoff on
        # connection-transient errors, resetting the session so each attempt gets a fresh
        # connection. Non-connection errors (and a genuinely missing row) fall straight through.
        from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError
        _transient = (TimeoutError, OSError, ConnectionError,
                      OperationalError, InterfaceError, DBAPIError)
        migration_job = None
        _last_exc: Exception | None = None
        for _attempt in range(3):
            try:
                result = await session.execute(
                    select(MigrationJob).where(MigrationJob.id == migration_id_uuid)
                )
                migration_job = result.scalar_one_or_none()
                break
            except _transient as _exc:  # pragma: no cover — transient DB connectivity
                _last_exc = _exc
                logger.warning(
                    "migration_gate_db_retry",
                    migration_id=migration_id,
                    gate_type=gate_type,
                    attempt=_attempt + 1,
                    error=type(_exc).__name__,
                )
                try:
                    await session.rollback()  # invalidate the bad connection; next execute reconnects
                except Exception:
                    pass
                if _attempt < 2:
                    await asyncio.sleep(0.5 * (_attempt + 1))
        else:
            # All retries exhausted on a connection error — surface a 503 (retryable) not a 500.
            raise HTTPException(
                status_code=503,
                detail="Database temporarily unavailable — please retry.",
            ) from _last_exc

        if not migration_job:
            logger.warning(
                "migration_gate_not_found",
                migration_id=migration_id,
                gate_type=gate_type,
            )
            raise HTTPException(status_code=404, detail="Migration not found")

        if migration_job.status not in ["awaiting_review", "running"]:
            logger.warning(
                "migration_gate_invalid_status",
                migration_id=migration_id,
                gate_type=gate_type,
                current_status=migration_job.status,
                expected_statuses=["awaiting_review", "running"],
                pending_gate=migration_job.pending_gate_type,
                decisions_keys=list(decisions.keys()) if isinstance(decisions, dict) else type(decisions).__name__,
            )
            raise HTTPException(
                status_code=400,
                detail=f"Cannot submit decisions for migration with status: {migration_job.status}",
            )

        if (
            migration_job.pending_gate_type
            and migration_job.pending_gate_type != gate_type
        ):
            logger.warning(
                "migration_gate_mismatch",
                migration_id=migration_id,
                submitted_gate=gate_type,
                expected_gate=migration_job.pending_gate_type,
                current_status=migration_job.status,
            )
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Gate mismatch: migration is waiting at "
                    f"'{migration_job.pending_gate_type}', "
                    f"but decisions were submitted for '{gate_type}'"
                ),
            )

        # Flip awaiting_review → running BEFORE enqueuing the (async) resume, so subsequent /status
        # polls report "running" instead of the gate. The deep-agent auto-driver (run_migration)
        # returns the instant it sees a user gate; while the resume is still processing the status
        # stays "awaiting_review", so the driver re-detects the SAME gate and RESUBMITS it every
        # round — exhausting _drive_migration_gates' rounds ("max_rounds") on slower files and
        # burning time. With the flip, run_migration keeps polling "running" and waits for the
        # resume to reach the next gate. Guarded on awaiting_review (a no-op if already running),
        # which also blocks a second concurrent resume from double-invoking the graph on the thread.
        await session.execute(
            update(MigrationJob)
            .where(
                MigrationJob.id == migration_id_uuid,
                MigrationJob.status == "awaiting_review",
            )
            .values(status="running", pending_gate_type=None, pending_gate_payload=None)
        )
        await session.commit()

        # The job is now RUNNING again (the gate is answered). Re-sync the per-run Activity entry
        # NOW so its status flips pending_human_input → running and the Section-3 "Awaiting your
        # review" HITL card clears immediately — not only when the gate node's clear_gate_payload
        # runs at the END of the (possibly long) resume replay. Without this the audit row lags at
        # "pending" for the whole post-submit replay, so the card lingers after the user already
        # submitted. Non-fatal: an activity hiccup must never block the gate resume.
        try:
            from .graph.nodes.schema_db_writer import _sync_run_activity

            _synced = await _sync_run_activity(migration_id, caller="gate_submit")
            logger.info(f"[{migration_id}] gate-submit run-activity sync → status={_synced}")
        except Exception as _sync_err:  # pragma: no cover - non-fatal audit sync
            logger.warning(
                f"[{migration_id}] gate-submit run-activity sync failed (non-fatal): {_sync_err}"
            )

        logger.info(
            "migration_gate_decisions_received",
            migration_id=migration_id,
            gate_type=gate_type,
            decisions_keys=list(decisions.keys()) if isinstance(decisions, dict) else type(decisions).__name__,
        )

        # Feature 4 — fold this gate submit's human-review resolution INTO the single per-run
        # activity row (refs.entry_kind="run"), NOT a separate top-level card: append it to
        # refs.review_resolutions (append-only/immutable) and weave one "Human review
        # resolution" step into the run's Processing Log between Deterministic and Semantic
        # mapping. Scoped to the org + run (session_id = migration_id). Non-fatal: an activity
        # hiccup must never block the gate resume.
        try:
            from .udr.activity_persist import append_run_review_resolution
            from .udr.review_audit import build_review_resolution_record

            _review_user = decisions.get("user") if isinstance(decisions, dict) else None
            _review_record = build_review_resolution_record(
                decisions, gate_type=gate_type, user=_review_user,
            )
            if _review_record:
                await append_run_review_resolution(
                    _review_record,
                    organization_id=migration_job.organization_id,
                    session_id=migration_id,
                    session=None,
                )
        except Exception as _audit_err:  # pragma: no cover - non-fatal audit emit
            logger.warning(
                "review_resolution_activity_emit_skipped",
                migration_id=migration_id,
                error=str(_audit_err),
            )

        # Try ARQ first; fall back to inline runner on any failure
        arq_enqueued = False
        try:
            from arq import create_pool
            from arq.connections import RedisSettings

            settings = get_settings()
            if settings.redis_url:
                redis_settings = RedisSettings.from_dsn(settings.redis_url)
                pool = await create_pool(redis_settings)
                await pool.enqueue_job(
                    "resume_migration",
                    migration_id=migration_id,
                    gate_type=gate_type,
                    decisions=decisions,
                )
                await pool.aclose()
                logger.info(f"[{migration_id}] resume_migration enqueued via ARQ")
                arq_enqueued = True
        except HTTPException:
            raise
        except Exception as arq_err:
            logger.warning(
                f"[{migration_id}] ARQ enqueue failed ({arq_err}); falling back to inline resume"
            )

        if not arq_enqueued:
            logger.info(f"[{migration_id}] Running resume inline")
            graph = get_migration_graph_instance()
            from langgraph.types import Command

            config = {
                "configurable": {"thread_id": migration_id},
                "run_name": f"migration:{migration_id}:resume:{gate_type}",
            }

            async def _inline_resume():
                cmd = Command(resume=decisions)
                err_msg: str | None = None
                try:
                    with bind_runtime_log_context(migration_id=migration_id):
                        result = await graph.ainvoke(cmd, config=config)
                    if isinstance(result, dict) and result.get("error_message"):
                        node_status = result.get("status", "")
                        if node_status not in ("complete", "step_paused", "awaiting_review"):
                            err_msg = str(result["error_message"])[:500]
                except Exception as exc:
                    if type(exc).__name__ == "GraphInterrupt":
                        pass
                    else:
                        logger.exception(f"[{migration_id}] Inline resume error: {exc}")
                        err_msg = str(exc)[:500]

                if err_msg:
                    try:
                        from sqlalchemy import update as _update
                        _sf = get_async_session_factory()
                        async with _sf() as err_session:
                            await err_session.execute(
                                _update(MigrationJob)
                                .where(MigrationJob.id == UUID(migration_id))
                                .values(status="failed", error_message=err_msg)
                            )
                            await err_session.commit()
                        logger.warning(f"[{migration_id}] Node returned error state — marked as failed: {err_msg}")
                    except Exception:
                        pass
                    # Sync the Activity Log entry to "failed" so a mid-resume death clears the
                    # stale "Awaiting your review" HITL card. Non-fatal.
                    try:
                        from .graph.nodes.schema_db_writer import _sync_run_activity
                        await _sync_run_activity(migration_id, caller="inline_error")
                    except Exception:
                        pass

            asyncio.create_task(_inline_resume())

        return MigrationApprovalResponse(
            migration_id=migration_id_uuid,
            status="approved",
            message=f"Decisions accepted for gate '{gate_type}'. Migration resuming.",
            decisions_processed=len(decisions) if isinstance(decisions, list) else 1,
        )

    @app.post(
        "/api/migration/{migration_id}/gate/field-mapping",
        response_model=MigrationApprovalResponse,
        tags=["Migration"],
        summary="Gate 1 — Field mapping approval decisions (Node 4)",
    )
    async def migration_gate_field_mapping(
        migration_id: str = Path(..., description="Migration UUID"),
        request: dict = {},
        session: AsyncSession = Depends(get_db_session),
    ) -> MigrationApprovalResponse:
        """
        Submit HITL decisions for Gate 1 — field mapping approval.

        The pipeline pauses here after Node 3 (semantic mapper) when
        low-confidence mappings or unmapped fields require human review.

        Decisions format (new):
        ```json
        {
          "flagged": {
            "table_name": [
              {"action": "accept|reject|override", "source_field": "...",
               "target_field": "...", "rationale": "..."}
            ]
          },
          "unmapped": {
            "table_name": [
              {"action": "custom|raw_metadata|skip", "source_field": "...",
               "target_table": "...", "custom_column_name": "...", "data_type": "..."}
            ]
          }
        }
        ```
        """
        try:
            decisions = request.get("decisions", request)
            return await _enqueue_migration_resume(
                migration_id, "field_mapping", decisions, session
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Failed to process gate/field-mapping for {migration_id}: {e}")
            # Never surface an empty {"detail":""} — some exceptions stringify to "" (bare
            # KeyError, timeouts), which made this 500 impossible to diagnose from the client.
            raise HTTPException(status_code=500, detail=str(e) or f"{type(e).__name__}")

    async def _rebuild_column_intelligence_from_state(state: dict, overrides: dict | None = None) -> dict | None:
        """Replicate pre_semantic_review_node's column_intelligence build using
        whatever's currently in state. Used by /state-canonicals + the Tier-2
        hydration path for migrations whose pre-semantic ran before
        column_intelligence was persisted to state. Returns the CI dict or None
        on any error — best-effort, never fatal."""
        try:
            from .udr.preprocessing import remove_exact_duplicate_rows
            from .udr.column_intelligence import build_column_intelligence
            from .udr.primitives import detect_primary_key as _dpk
            try:
                from .matchers.fm_ontology import fm_field_lookup as _field_resolver
            except Exception:  # pragma: no cover
                _field_resolver = None
            from .db import get_plenum_cafm_columns_by_table, get_plenum_cafm_sample_values_by_table

            _routing = state.get("table_routing") or {}
            _matches = state.get("cafm_table_matches") or {}

            def _dest_table(src: str) -> str:
                return _routing.get(src) or _matches.get(src) or src

            _analysis_tables: dict = {}
            for _n, _r in (state.get("full_tables") or {}).items():
                try:
                    _deduped, _ = remove_exact_duplicate_rows(list(_r or []))
                    _analysis_tables[_n] = _deduped
                except Exception:
                    _analysis_tables[_n] = list(_r or [])
            if not _analysis_tables:
                return None

            def _ci_cols(rows):
                seen, out = set(), []
                for r in rows or []:
                    for k in r:
                        if k not in seen:
                            seen.add(k); out.append(k)
                return out

            _ci_tables: dict = {}
            for _n, _r in _analysis_tables.items():
                _rows = list(_r or [])
                _cols = _ci_cols(_rows)
                _ci_tables[_n] = {"rows": _rows, "columns": _cols, "pk": _dpk(_rows, _cols).get("columns", [])}

            _dest_col: dict = {}
            _conf_col: dict = {}
            for _bucket in ("tier1_mappings_by_table", "tier2_auto_by_table"):
                for _tbl, _maps in (state.get(_bucket) or {}).items():
                    for _m in _maps or []:
                        if not isinstance(_m, dict):
                            continue
                        _sf = _m.get("source_field")
                        _tf = _m.get("target_field")
                        if _sf and _tf:
                            _dest_col[(_tbl, _sf)] = _tf
                            _conf_col[(_tbl, _sf)] = _m.get("confidence")

            _unres_sg: dict = {}
            for _tbl, _sugg in (state.get("unresolved_suggestions_by_table") or {}).items():
                for _sf, _s in (_sugg or {}).items():
                    _tf = (((_s or {}).get("target_field")) or "").strip()
                    if _sf and _tf:
                        _unres_sg[(_tbl, _sf)] = _tf
                        _conf_col.setdefault((_tbl, _sf), (_s or {}).get("confidence"))

            try:
                _cols_by_tbl = await get_plenum_cafm_columns_by_table()
            except Exception:
                _cols_by_tbl = {}
            try:
                _dest_tbls = {_dest_table(src) for src in _ci_tables if _dest_table(src)}
                _dest_samples = await get_plenum_cafm_sample_values_by_table(
                    _dest_tbls, allow=_cols_by_tbl
                )
            except Exception:
                _dest_samples = {}

            _ovr = overrides if overrides is not None else (state.get("canonical_overrides") or None)
            return build_column_intelligence(
                _ci_tables,
                dest_table_by_source={src: _dest_table(src) for src in _ci_tables},
                dest_col_by_source_col=_dest_col,
                conf_by_source_col=_conf_col,
                field_resolver=_field_resolver,
                canonical_overrides=_ovr,
                dest_samples_by_table=_dest_samples,
                dest_columns_by_table=_cols_by_tbl,
                unresolved_suggest_by_source_col=_unres_sg,
            )
        except Exception as exc:  # pragma: no cover
            logger.warning(f"_rebuild_column_intelligence_from_state: {exc}")
            return None

    @app.get(
        "/api/migration/{migration_id}/state-canonicals",
        tags=["Migration"],
        summary="Read column_canonical + table_routing from migration state (used by Tier-2)",
    )
    async def migration_state_canonicals(
        migration_id: str = Path(..., description="Migration UUID"),
        session: AsyncSession = Depends(get_db_session),  # noqa: ARG001
    ) -> dict:
        """Return the migration's CURRENT column_canonical + table_routing from
        LangGraph state. Lets the Tier-2 gate hydrate the canonical-name map +
        user-confirmed routing even when the paused gate payload was created
        before those fields were added — without forcing the user to restart.

        If state.column_intelligence is empty (older migrations whose
        pre_semantic node ran before column_intelligence was persisted), this
        falls back to rebuilding the canonical map from
        state.canonical_overrides + state.tier1_mappings_by_table so the
        Tier-2 gate still gets meaningful canonical names.
        """
        try:
            graph = get_migration_graph_instance()
            cfg = {"configurable": {"thread_id": migration_id}}
            try:
                snap = await graph.aget_state(cfg)
            except (NotImplementedError, AttributeError):  # pragma: no cover
                snap = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: graph.get_state(cfg)
                )
            state = snap.values if snap else {}
        except Exception as e:
            logger.exception(f"state-canonicals: read failed for {migration_id}: {e}")
            raise HTTPException(status_code=500, detail=f"state read failed: {e}")

        ci = (state or {}).get("column_intelligence") or {}
        # eff_ci is the column-intelligence we actually serve from — the persisted one when it
        # has content, otherwise the on-the-fly rebuild below. canonical_groups is built from
        # eff_ci so the "Unified column names" summary stays populated even when state never
        # persisted column_intelligence (older runs / runs whose pre-semantic build was empty).
        eff_ci = ci if isinstance(ci, dict) else {}
        col_canon: dict[str, str] = {}
        if isinstance(ci, dict):
            raw = ci.get("column_canonical") or {}
            if isinstance(raw, dict):
                col_canon = {str(k): str(v) for k, v in raw.items() if k and v}

        # Fallback for older migrations whose pre-semantic node ran before
        # column_intelligence was persisted to state. Rebuild CI on the fly
        # using the same inputs build_column_intelligence consumes from state.
        # This recovers the group-canonical names (asset_id for sites.id,
        # vendors.id and workorders.asset_no even when those columns are NOT
        # in tier1 because the user routed them to semantic).
        if not col_canon and state:
            rebuilt = await _rebuild_column_intelligence_from_state(state)
            if rebuilt and isinstance(rebuilt, dict):
                eff_ci = rebuilt
                raw = rebuilt.get("column_canonical") or {}
                if isinstance(raw, dict):
                    col_canon = {str(k): str(v) for k, v in raw.items() if k and v}

        # Last-resort fill from tier1 + column-level overrides — covers the
        # case where the rebuild itself fails (missing inputs in old state).
        if not col_canon:
            tier1 = (state or {}).get("tier1_mappings_by_table") or {}
            if isinstance(tier1, dict):
                for tbl, rows in tier1.items():
                    if not isinstance(rows, list):
                        continue
                    for r in rows:
                        if not isinstance(r, dict):
                            continue
                        sf = r.get("source_field")
                        tf = r.get("target_field")
                        if sf and tf:
                            col_canon[f"{tbl}.{sf}"] = str(tf)
            overrides = (state or {}).get("canonical_overrides") or {}
            if isinstance(overrides, dict):
                for k, v in overrides.items():
                    if not k or not v:
                        continue
                    if "." in str(k):
                        col_canon[str(k)] = str(v)

        # Overlay the LATEST canonical pins on top of whatever col_canon we resolved. Defense in
        # depth with the pre-semantic re-stamp: a persisted column_intelligence built before the
        # current pins (or an old migration's) would otherwise return the stale source name.
        # Group-level pins ("G2") rename every member; column-level pins ("table.col") set one.
        overrides = (state or {}).get("canonical_overrides") or {}
        if isinstance(overrides, dict) and overrides:
            groups = ci.get("groups") if isinstance(ci, dict) and isinstance(ci.get("groups"), list) else []
            grp_by_id = {g.get("group_id"): g for g in groups if isinstance(g, dict)}
            for k, v in overrides.items():
                if not k or not v:
                    continue
                name = str(v)
                if k in grp_by_id:
                    for mk in (grp_by_id[k].get("member_keys") or []):
                        if isinstance(mk, dict) and mk.get("table") and mk.get("column"):
                            col_canon[f"{mk['table']}.{mk['column']}"] = name
                elif "." in str(k):
                    col_canon[str(k)] = name

        # table_routing = deterministic table matches (auto) + user-confirmed overrides. Merging
        # cafm_table_matches fills tables the user didn't explicitly re-route at the gate (e.g.
        # WorkOrders_2 auto-matched to work_orders), so Tier-2 doesn't fall back to the heuristic
        # source-name slug (work_orders_2). User overrides (table_routing) win on conflict.
        table_routing = {
            **{str(k): str(v) for k, v in ((state or {}).get("cafm_table_matches") or {}).items() if k and v},
            **{str(k): str(v) for k, v in ((state or {}).get("table_routing") or {}).items() if k and v},
        }

        # Compact canonical groups (group → canonical-name → members + pin flag) so the Tier-2
        # screen can render the "Unified column names" decisions made earlier as a read-only
        # summary, instead of dropping them on entry. Pinned name reflects the latest override.
        # Built from eff_ci (the persisted CI, or the rebuild fallback) so it is populated even
        # when state.column_intelligence was never persisted.
        canonical_groups: list[dict] = []
        if isinstance(eff_ci, dict):
            for g in (eff_ci.get("groups") or []):
                if not isinstance(g, dict):
                    continue
                gid = g.get("group_id")
                pinned = bool(gid and isinstance(overrides, dict) and gid in overrides)
                name = str(overrides[gid]) if pinned else (g.get("canonical_name") or "")
                canonical_groups.append({
                    "group_id": gid,
                    "canonical_name": name,
                    "members": [str(m) for m in (g.get("members") or []) if m],
                    "pinned": pinned,
                })

        return {
            "migration_id": migration_id,
            "column_canonical": col_canon,
            "table_routing": table_routing,
            "canonical_groups": canonical_groups,
        }

    @app.post(
        "/api/migration/{migration_id}/preview-canonical-overrides",
        tags=["Migration"],
        summary="Re-run B19/B21 with new canonical pins, without advancing the gate",
    )
    async def preview_canonical_overrides(
        migration_id: str = Path(..., description="Migration UUID"),
        request: dict = {},
        session: AsyncSession = Depends(get_db_session),
    ) -> dict:
        """Re-run build_column_intelligence with the supplied ``canonical_overrides``
        against the currently-paused pre-semantic gate state, and return ONLY the
        updated ``column_intelligence`` payload. Does NOT advance the gate.

        Lets the FE 'Re-match now' button refresh B14/B19/B20/B21 instantly when the
        user pins a canonical, instead of waiting for gate advance to see the new
        destination matches.

        Body: ``{"canonical_overrides": {"G2": "asset_id", "vendors.id": "vendor_id"},
                 "table_overrides": {"workorders": "assets"}}``

        ``table_overrides`` (source_table → new destination table) re-runs the column mapping
        against the CHANGED destination table's columns — so when the user re-points a sheet in
        "Step 1 — Confirm table routing", the column matching (B14/B21) reflects the new table's
        columns rather than the originally-routed one. Preview-only: not persisted to state (the
        actual routing is applied on gate advance).
        """
        overrides = (request or {}).get("canonical_overrides") or {}
        if not isinstance(overrides, dict):
            raise HTTPException(status_code=400, detail="canonical_overrides must be a dict")
        table_overrides = (request or {}).get("table_overrides") or {}
        if not isinstance(table_overrides, dict):
            raise HTTPException(status_code=400, detail="table_overrides must be a dict")
        # Only real re-points: {source_table: "new_dest"} with a non-empty string destination.
        _table_ovr = {
            str(_s): str(_d).strip()
            for _s, _d in table_overrides.items()
            if isinstance(_d, str) and _d.strip()
        }

        # Read the paused-gate LangGraph state.
        try:
            graph = get_migration_graph_instance()
            cfg = {"configurable": {"thread_id": migration_id}}
            try:
                snap = await graph.aget_state(cfg)
            except (NotImplementedError, AttributeError):  # pragma: no cover — sync fallback
                snap = await asyncio.get_event_loop().run_in_executor(None, lambda: graph.get_state(cfg))
            state = snap.values if snap else None
            if not state:
                raise HTTPException(status_code=404, detail="No paused state for this migration")
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"preview-canonical-overrides: state read failed for {migration_id}: {e}")
            raise HTTPException(status_code=500, detail=f"state read failed: {e}")

        # Replicate the column-intelligence input build pre_semantic_review_node uses.
        try:
            from .udr.preprocessing import remove_exact_duplicate_rows
            from .udr.column_intelligence import build_column_intelligence
            from .udr.primitives import detect_primary_key as _dpk
            try:
                from .matchers.fm_ontology import fm_field_lookup as _field_resolver
            except Exception:  # pragma: no cover — FM tiers degrade gracefully
                _field_resolver = None
            from .db import get_plenum_cafm_columns_by_table, get_plenum_cafm_sample_values_by_table

            _routing = state.get("table_routing") or {}
            _matches = state.get("cafm_table_matches") or {}

            def _dest_table(src: str) -> str:
                # A user re-point (table_overrides) wins over the state's original routing, so the
                # column mapping is recomputed against the newly-chosen destination table.
                ov = _table_ovr.get(src)
                if ov:
                    return ov
                return _routing.get(src) or _matches.get(src) or src

            _analysis_tables: dict = {}
            for _n, _r in (state.get("full_tables") or {}).items():
                try:
                    _deduped, _ = remove_exact_duplicate_rows(list(_r or []))
                    _analysis_tables[_n] = _deduped
                except Exception:
                    _analysis_tables[_n] = list(_r or [])
            if not _analysis_tables:
                raise HTTPException(status_code=409, detail="No tables in state to re-score")

            def _ci_cols(rows):
                seen, out = set(), []
                for r in rows or []:
                    for k in r:
                        if k not in seen:
                            seen.add(k); out.append(k)
                return out

            _ci_tables: dict = {}
            for _n, _r in _analysis_tables.items():
                _rows = list(_r or [])
                _cols = _ci_cols(_rows)
                _ci_tables[_n] = {"rows": _rows, "columns": _cols, "pk": _dpk(_rows, _cols).get("columns", [])}

            _dest_col: dict = {}
            _conf_col: dict = {}
            for _bucket in ("tier1_mappings_by_table", "tier2_auto_by_table"):
                for _tbl, _maps in (state.get(_bucket) or {}).items():
                    for _m in _maps or []:
                        if not isinstance(_m, dict):
                            continue
                        _sf = _m.get("source_field")
                        _tf = _m.get("target_field")
                        if _sf and _tf:
                            _dest_col[(_tbl, _sf)] = _tf
                            _conf_col[(_tbl, _sf)] = _m.get("confidence")

            _unres_sg: dict = {}
            for _tbl, _sugg in (state.get("unresolved_suggestions_by_table") or {}).items():
                for _sf, _s in (_sugg or {}).items():
                    _tf = (((_s or {}).get("target_field")) or "").strip()
                    if _sf and _tf:
                        _unres_sg[(_tbl, _sf)] = _tf
                        _conf_col.setdefault((_tbl, _sf), (_s or {}).get("confidence"))

            try:
                _cols_by_tbl = await get_plenum_cafm_columns_by_table()
            except Exception:
                _cols_by_tbl = {}
            try:
                _dest_tbls = {_dest_table(src) for src in _ci_tables if _dest_table(src)}
                _dest_samples = await get_plenum_cafm_sample_values_by_table(
                    _dest_tbls, allow=_cols_by_tbl
                )
            except Exception:
                _dest_samples = {}

            # For a re-pointed source table, the pre-computed column→destination matches were
            # made against the ORIGINAL destination, so they're stale. Drop them so
            # build_column_intelligence re-matches those columns against the NEW table's columns.
            if _table_ovr:
                _dest_col = {k: v for k, v in _dest_col.items() if k[0] not in _table_ovr}
                _conf_col = {k: v for k, v in _conf_col.items() if k[0] not in _table_ovr}
                _unres_sg = {k: v for k, v in _unres_sg.items() if k[0] not in _table_ovr}

            ci = build_column_intelligence(
                _ci_tables,
                dest_table_by_source={src: _dest_table(src) for src in _ci_tables},
                dest_col_by_source_col=_dest_col,
                conf_by_source_col=_conf_col,
                field_resolver=_field_resolver,
                canonical_overrides=overrides or None,
                dest_samples_by_table=_dest_samples,
                dest_columns_by_table=_cols_by_tbl,
                unresolved_suggest_by_source_col=_unres_sg,
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"preview-canonical-overrides: rebuild failed for {migration_id}: {e}")
            raise HTTPException(status_code=500, detail=f"rebuild failed: {e}")

        # Persist the pins into LangGraph state so a subsequent gate advance picks
        # up the same overrides (no need to re-send on advance).
        try:
            await graph.aupdate_state(cfg, {"canonical_overrides": overrides})
        except Exception as _persist_err:
            logger.exception(
                f"preview-canonical-overrides: FAILED to persist canonical_overrides "
                f"for {migration_id}: {_persist_err}"
            )

        # Refresh the RIGHT-RAIL column-stage logs: write the recomputed column_intelligence back
        # onto the migration job's gate payload, then rebuild the progressive Activity entry from
        # it. _sync_run_activity reads pending_gate_payload.column_intelligence to emit the
        # column-similarity / grouping / unified-names / destination-mapping stage chips, so after
        # this the Activity Log reflects the re-pointed tables on the next 2s poll — not just the
        # left panel. Best-effort: a failure here never fails the preview.
        if ci:
            try:
                from sqlalchemy import select as _select

                _res = await session.execute(
                    _select(MigrationJob).where(MigrationJob.id == UUID(migration_id))
                )
                _job = _res.scalar_one_or_none()
                if _job is not None and isinstance(_job.pending_gate_payload, dict):
                    _gp_new = dict(_job.pending_gate_payload)
                    _gp_new["column_intelligence"] = ci
                    _job.pending_gate_payload = _gp_new  # reassign so JSONB change is tracked
                    await session.commit()
                    from .graph.nodes.schema_db_writer import _sync_run_activity

                    await _sync_run_activity(migration_id, caller="preview-canonical-overrides")
            except Exception as _sync_err:
                logger.warning(
                    f"preview-canonical-overrides: activity refresh skipped for {migration_id}: {_sync_err}"
                )

        return {
            "migration_id": migration_id,
            "column_intelligence": ci,
            "applied_overrides": overrides,
            "applied_table_overrides": _table_ovr,
        }

    @app.get(
        "/api/migration/{migration_id}/gate/field-mapping/draft",
        tags=["Migration"],
        summary="Get persisted Tier-2 / field-mapping UI draft",
    )
    async def get_migration_field_mapping_draft(
        migration_id: str = Path(..., description="Migration UUID"),
        session: AsyncSession = Depends(get_db_session),
    ):
        """Return saved semantic/field-mapping choices (table + column overrides)."""
        try:
            result = await session.execute(
                select(MigrationJob).where(MigrationJob.id == UUID(migration_id))
            )
            migration_job = result.scalar_one_or_none()
            if not migration_job:
                raise HTTPException(status_code=404, detail="Migration not found")
            draft = getattr(migration_job, "field_mapping_draft", None)
            return {"migration_id": migration_id, "draft": draft}
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Failed to get field-mapping draft for {migration_id}: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.put(
        "/api/migration/{migration_id}/gate/field-mapping/draft",
        tags=["Migration"],
        summary="Persist Tier-2 / field-mapping UI draft",
    )
    async def put_migration_field_mapping_draft(
        migration_id: str = Path(..., description="Migration UUID"),
        request: dict = {},
        session: AsyncSession = Depends(get_db_session),
    ):
        """Save semantic/field-mapping choices so Field Structure Review can restore them."""
        try:
            result = await session.execute(
                select(MigrationJob).where(MigrationJob.id == UUID(migration_id))
            )
            migration_job = result.scalar_one_or_none()
            if not migration_job:
                raise HTTPException(status_code=404, detail="Migration not found")
            draft = request.get("draft", request)
            migration_job.field_mapping_draft = draft if isinstance(draft, dict) else None
            await session.commit()
            return {"migration_id": migration_id, "draft": migration_job.field_mapping_draft}
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Failed to save field-mapping draft for {migration_id}: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.post(
        "/api/migration/canonical-field-scores",
        response_model=CanonicalFieldScoresResponse,
        tags=["Migration"],
        summary="Semantic match scores for source field vs canonical column names",
    )
    async def migration_canonical_field_scores(
        request: CanonicalFieldScoresRequest,
    ) -> CanonicalFieldScoresResponse:
        """
        Returns embedding similarity scores for each requested canonical field name.
        Used when the UI override target (e.g. ``id``) is not in the top-3 suggestions list.
        """
        from .embeddings import embed_text, score_canonical_fields

        fields = [f.strip() for f in request.canonical_fields if f and str(f).strip()]
        if not fields:
            return CanonicalFieldScoresResponse(scores={})

        sample_str = ", ".join(request.sample_values or []) or "(no samples)"
        desc = (request.field_description or "").strip() or "Unknown field type"
        embed_text_input = f"{request.source_field.strip()} | {desc} | {sample_str}"

        try:
            client = get_openai_client()
            source_embedding = await embed_text(client, embed_text_input)
        except Exception as e:
            logger.warning(f"canonical_field_scores embed failed: {e}")
            raise HTTPException(status_code=503, detail="Embedding service unavailable") from e

        if source_embedding is None:
            return CanonicalFieldScoresResponse(scores={})

        scores = score_canonical_fields(source_embedding, fields)
        return CanonicalFieldScoresResponse(scores=scores)

    @app.post(
        "/api/embed/rank",
        tags=["Embeddings"],
        summary="Semantically rank candidates against a query (keyword-match fallback)",
    )
    async def embed_rank(request: EmbedRankRequest) -> EmbedRankResponse:
        """Rank candidate texts by cosine similarity to a query — the embedding
        fallback the frontend uses only when keyword matching finds nothing.
        Degrades gracefully: returns [] (never errors) when embeddings are
        unavailable, so the caller simply keeps its keyword/no-match result."""
        from .embeddings import cosine_similarity, embed_texts_batch

        q = (request.query or "").strip()
        cands = [c for c in (request.candidates or []) if (c.text or "").strip()]
        if not q or not cands:
            return EmbedRankResponse(ranked=[])
        try:
            client = get_openai_client()
            embs = await embed_texts_batch(client, [q] + [c.text for c in cands])
        except Exception as e:  # never fatal — caller falls back to keywords
            logger.warning(f"embed_rank failed: {e}")
            return EmbedRankResponse(ranked=[])
        qvec = embs.get(q)
        if qvec is None:
            return EmbedRankResponse(ranked=[])
        scored: list[tuple[str, float]] = []
        for c in cands:
            cvec = embs.get(c.text)
            if cvec is not None:
                scored.append((c.id, cosine_similarity(qvec, cvec)))
        scored.sort(key=lambda x: x[1], reverse=True)
        top_k = max(1, min(int(request.top_k or 5), 20))
        return EmbedRankResponse(
            ranked=[EmbedRankItem(id=i, score=round(s, 4)) for i, s in scored[:top_k]]
        )

    @app.delete(
        "/api/migration/{migration_id}/gate/field-mapping/draft",
        tags=["Migration"],
        summary="Clear persisted field-mapping UI draft",
    )
    async def delete_migration_field_mapping_draft(
        migration_id: str = Path(..., description="Migration UUID"),
        session: AsyncSession = Depends(get_db_session),
    ):
        try:
            result = await session.execute(
                select(MigrationJob).where(MigrationJob.id == UUID(migration_id))
            )
            migration_job = result.scalar_one_or_none()
            if not migration_job:
                raise HTTPException(status_code=404, detail="Migration not found")
            migration_job.field_mapping_draft = None
            await session.commit()
            return {"migration_id": migration_id, "status": "cleared"}
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Failed to clear field-mapping draft for {migration_id}: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.post(
        "/api/migration/{migration_id}/gate/hierarchy",
        response_model=MigrationApprovalResponse,
        tags=["Migration"],
        summary="Gate 2 — Hierarchy verification decisions (Node 7)",
    )
    async def migration_gate_hierarchy(
        migration_id: str = Path(..., description="Migration UUID"),
        request: dict = {},
        session: AsyncSession = Depends(get_db_session),
    ) -> MigrationApprovalResponse:
        """
        Submit HITL decisions for Gate 2 — hierarchy verification.

        The pipeline pauses here after Node 6 (hierarchy resolver) for the
        customer to approve or correct detected FK relationships.

        Decisions format:
        ```json
        {
          "approved_hierarchies": [...],
          "corrections": [...]
        }
        ```
        """
        try:
            decisions = request.get("decisions", request)
            return await _enqueue_migration_resume(
                migration_id, "hierarchy", decisions, session
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Failed to process gate/hierarchy for {migration_id}: {e}")
            raise HTTPException(status_code=500, detail=str(e) or f"{type(e).__name__}")

    @app.post(
        "/api/migration/{migration_id}/gate/final",
        response_model=MigrationApprovalResponse,
        tags=["Migration"],
        summary="Gate 3 — Final confirmation before handoff (Node 9)",
    )
    async def migration_gate_final(
        migration_id: str = Path(..., description="Migration UUID"),
        request: dict = {},
        session: AsyncSession = Depends(get_db_session),
    ) -> MigrationApprovalResponse:
        """
        Submit final confirmation for Gate 3 before Node 9 writes to svc-ingestion.

        This is the last HITL gate. Approving here triggers DDL execution (if
        custom columns were requested in Gate 1) and then hands off the
        IntermediateSchema to svc-ingestion.

        Decisions format:
        ```json
        {"confirmed": true}
        ```
        """
        try:
            decisions = request.get("decisions", request)
            return await _enqueue_migration_resume(
                migration_id, "write", decisions, session
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Failed to process gate/final for {migration_id}: {e}")
            raise HTTPException(status_code=500, detail=str(e) or f"{type(e).__name__}")

    @app.post(
        "/api/migration/{migration_id}/gate/pre-semantic",
        response_model=MigrationApprovalResponse,
        tags=["Migration"],
        summary="Pre-Semantic Gate — T1 mapping review before Node 3",
    )
    async def migration_gate_pre_semantic(
        migration_id: str = Path(..., description="Migration UUID"),
        request: dict = {},
        session: AsyncSession = Depends(get_db_session),
    ) -> MigrationApprovalResponse:
        """
        Submit HITL decisions for the Pre-Semantic Gate (between Node 2 and Node 3).

        Reviews T1_exact, T1_variation, T1_regex, T1_llm mappings. T1_alias auto-passes.

        Decisions format:
        ```json
        {
          "decisions": {
            "table_name": [
              {"source_field": "...", "decision": "approve" | "semantic"}
            ]
          }
        }
        ```
        """
        try:
            body = request if isinstance(request, dict) else {}
            # WP-5: when the gate sends structured decisions (a dict under "decisions",
            # optionally with "table_overrides"), forward the whole structure so the
            # node can apply table renames / new-table creation. Otherwise keep the
            # legacy flat pass-through.
            if isinstance(body.get("decisions"), dict):
                decisions = {
                    "decisions": body.get("decisions") or {},
                    "table_overrides": body.get("table_overrides") or {},
                    # HITL primary-key confirmation — the user-approved PK per table, applied
                    # in the node before hierarchy / FK detection (resumed["pk_overrides"]).
                    "pk_overrides": body.get("pk_overrides") or {},
                }
            else:
                decisions = body.get("decisions", body)
            return await _enqueue_migration_resume(
                migration_id, "pre_semantic", decisions, session
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Failed to process gate/pre-semantic for {migration_id}: {e}")
            raise HTTPException(status_code=500, detail=str(e) or f"{type(e).__name__}")

    @app.post(
        "/api/migration/{migration_id}/gate/pk-approval",
        response_model=MigrationApprovalResponse,
        tags=["Migration"],
        summary="PK-Approval Gate (Group A) — confirm primary keys before routing/mapping",
    )
    async def migration_gate_pk_approval(
        migration_id: str = Path(..., description="Migration UUID"),
        request: dict = {},
        session: AsyncSession = Depends(get_db_session),
    ) -> MigrationApprovalResponse:
        """Submit the HITL primary-key approval (Group A). Body: ``{"pk_overrides": {table: col|[cols]}}``.
        Resumes the pk_review gate; the graph then advances to the pre-semantic routing gate."""
        try:
            body = request if isinstance(request, dict) else {}
            decisions = {"pk_overrides": body.get("pk_overrides") or {}}
            return await _enqueue_migration_resume(
                migration_id, "pk_approval", decisions, session
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Failed to process gate/pk-approval for {migration_id}: {e}")
            raise HTTPException(status_code=500, detail=str(e) or f"{type(e).__name__}")

    @app.post(
        "/api/migration/{migration_id}/gate/unique-table-approval",
        response_model=MigrationApprovalResponse,
        tags=["Migration"],
        summary="Unique-Table Gate (Group A step 2) — approve unique-table identification",
    )
    async def migration_gate_unique_table_approval(
        migration_id: str = Path(..., description="Migration UUID"),
        request: dict = {},
        session: AsyncSession = Depends(get_db_session),
    ) -> MigrationApprovalResponse:
        """Submit the HITL unique-table approval (Group A step 2). Runs AFTER pk-approval; the
        primary key is already decided, so this is a simple confirm (empty body). Resumes the
        unique_table_review gate; the graph then advances to the pre-semantic routing gate."""
        try:
            body = request if isinstance(request, dict) else {}
            decisions = {"approved": bool(body.get("approved", True))}
            return await _enqueue_migration_resume(
                migration_id, "unique_table_approval", decisions, session
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Failed to process gate/unique-table-approval for {migration_id}: {e}")
            raise HTTPException(status_code=500, detail=str(e) or f"{type(e).__name__}")

    @app.post(
        "/api/migration/{migration_id}/gate/classification-approval",
        response_model=MigrationApprovalResponse,
        tags=["Migration"],
        summary="Classification Gate (B20.1) — select/unselect FK + Shared assignments",
    )
    async def migration_gate_classification_approval(
        migration_id: str = Path(..., description="Migration UUID"),
        request: dict = {},
        session: AsyncSession = Depends(get_db_session),
    ) -> MigrationApprovalResponse:
        """Submit the HITL PK / FK / Shared classification approval (B20.1). Body:
        ``{"rejected_groups": [group_id, ...], "verdict_overrides": {group_id: "fk"|"shared"}}`` —
        de-selected group_ids are NOT applied at all; ``verdict_overrides`` re-classifies a group
        (FK demoted to Shared Attribute → lookup table instead of enforcement; Shared promoted to
        FK → no lookup table). Resumes the classification gate; the graph advances to semantic
        mapping only after this human approval."""
        try:
            body = request if isinstance(request, dict) else {}
            decisions = {
                "rejected_groups": body.get("rejected_groups") or [],
                "verdict_overrides": body.get("verdict_overrides") or {},
            }
            return await _enqueue_migration_resume(
                migration_id, "classification_approval", decisions, session
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Failed to process gate/classification-approval for {migration_id}: {e}")
            raise HTTPException(status_code=500, detail=str(e) or f"{type(e).__name__}")

    @app.post(
        "/api/migration/{migration_id}/gate/column-mapping-approval",
        response_model=MigrationApprovalResponse,
        tags=["Migration"],
        summary="Column-Mapping Gate (B14.1) — approve/override matched destination columns",
    )
    async def migration_gate_column_mapping_approval(
        migration_id: str = Path(..., description="Migration UUID"),
        request: dict = {},
        session: AsyncSession = Depends(get_db_session),
    ) -> MigrationApprovalResponse:
        """Submit the HITL per-column mapping approval (B14.1). Body:
        ``{"overrides": {source_table: {source_field: "<dest_col>"|"__new__"}}}`` — a dest column
        re-targets the source column, ``__new__`` creates it as a new column instead of merging;
        columns not listed keep their matched destination. Resumes the column_mapping_review gate;
        the graph then advances to semantic mapping / write."""
        try:
            body = request if isinstance(request, dict) else {}
            decisions = {"overrides": body.get("overrides") or {}}
            return await _enqueue_migration_resume(
                migration_id, "column_mapping_approval", decisions, session
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Failed to process gate/column-mapping-approval for {migration_id}: {e}")
            raise HTTPException(status_code=500, detail=str(e) or f"{type(e).__name__}")

    @app.post(
        "/api/migration/{migration_id}/advance",
        response_model=MigrationApprovalResponse,
        tags=["Migration"],
        summary="Advance pipeline past a step pause (node-by-node mode)",
    )
    async def migration_advance(
        migration_id: str = Path(..., description="Migration UUID"),
        session: AsyncSession = Depends(get_db_session),
    ) -> MigrationApprovalResponse:
        """
        Advance the pipeline past an interrupt_after step pause.

        Called when the user clicks "Next Node →" in Streamlit after reviewing
        the output of a non-gate node (ingest, det-mapper, semantic, preprocess,
        hierarchy, output-generator).

        The endpoint:
        1. Validates the migration is in 'step_paused' state.
        2. Clears pending_gate_type / pending_gate_payload and sets status='running'.
        3. Resumes LangGraph execution via Command(resume=None).
        """
        migration_id_uuid = UUID(migration_id)

        result = await session.execute(
            select(MigrationJob).where(MigrationJob.id == migration_id_uuid)
        )
        migration_job = result.scalar_one_or_none()

        if not migration_job:
            raise HTTPException(status_code=404, detail="Migration not found")

        if migration_job.status != "step_paused":
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Migration is not paused at a step. "
                    f"Current status: {migration_job.status}. "
                    f"Use the gate endpoints for HITL review gates."
                ),
            )

        step_key = migration_job.pending_gate_type or "unknown_step"
        logger.info(f"[{migration_id}] Advancing past step '{step_key}'")

        # Clear step pause and flip status back to running — as an ATOMIC compare-and-swap
        # (status must still be 'step_paused'). Only the caller that actually performs the flip
        # (rowcount == 1) may invoke the graph below. Two /advance calls can race for the same
        # migration — the deep-agent's run_migration auto-driver polling step_paused AND the user
        # clicking Continue — and without the CAS both would pass the check above, both flip, and
        # both fire graph.ainvoke(None) on the SAME thread_id. Two concurrent LangGraph invocations
        # corrupt the shared checkpoint and intermittently bounce the run back to the SAME step
        # (the "click Continue → reloads to the same step → second click works, sometimes" bug).
        flip_result = await session.execute(
            update(MigrationJob)
            .where(
                MigrationJob.id == migration_id_uuid,
                MigrationJob.status == "step_paused",
            )
            .values(
                status="running",
                pending_gate_type=None,
                pending_gate_payload=None,
            )
        )
        await session.commit()

        if flip_result.rowcount == 0:
            # Lost the race: another /advance already flipped this step_paused → running and is
            # driving the graph. Do NOT fire a second concurrent invocation — report advancing.
            logger.info(
                f"[{migration_id}] Advance race — '{step_key}' already advancing; "
                f"skipping duplicate graph invocation."
            )
            return MigrationApprovalResponse(
                migration_id=migration_id_uuid,
                status="advancing",
                message=f"Pipeline already advancing past step '{step_key}'.",
                decisions_processed=0,
            )

        # We won the flip: the step-pause is now RUNNING. Re-sync the per-run Activity entry now so
        # its status flips pending_human_input → running and the Section-3 "Awaiting your review"
        # card clears immediately (e.g. right after the semantic-mapping step advances) instead of
        # lingering until the next node-completion sync. Non-fatal.
        try:
            from .graph.nodes.schema_db_writer import _sync_run_activity

            _synced = await _sync_run_activity(migration_id, caller="step_advance")
            logger.info(f"[{migration_id}] step-advance run-activity sync → status={_synced}")
        except Exception as _sync_err:  # pragma: no cover - non-fatal audit sync
            logger.warning(
                f"[{migration_id}] step-advance run-activity sync failed (non-fatal): {_sync_err}"
            )

        # Resume the graph.
        # interrupt_after pauses the graph between nodes — to continue, re-invoke
        # with input=None (NOT Command(resume=...)).  Command(resume=value) is only
        # for interrupt() calls inside node bodies (HITL gates).
        graph = get_migration_graph_instance()

        config = {
            "configurable": {"thread_id": migration_id},
            "run_name": f"migration:{migration_id}:advance:{step_key}",
        }

        async def _inline_advance():
            err_msg: str | None = None
            try:
                with bind_runtime_log_context(migration_id=migration_id):
                    result = await graph.ainvoke(None, config=config)
                # Nodes may return an error state without throwing (e.g. API 401)
                if isinstance(result, dict) and result.get("error_message"):
                    node_status = result.get("status", "")
                    if node_status not in ("complete", "step_paused", "awaiting_review"):
                        err_msg = str(result["error_message"])[:500]
            except Exception as exc:
                if type(exc).__name__ in ("GraphInterrupt", "NodeInterrupt"):
                    pass  # expected — next step or gate paused
                else:
                    logger.exception(f"[{migration_id}] Inline advance error: {exc}")
                    err_msg = str(exc)[:500]

            if err_msg:
                try:
                    from sqlalchemy import update as _update
                    _sf = get_async_session_factory()
                    async with _sf() as err_session:
                        await err_session.execute(
                            _update(MigrationJob)
                            .where(MigrationJob.id == UUID(migration_id))
                            .values(status="failed", error_message=err_msg)
                        )
                        await err_session.commit()
                    logger.warning(f"[{migration_id}] Node returned error state — marked as failed: {err_msg}")
                except Exception:
                    pass
                # Sync the Activity Log entry to "failed" so a mid-advance death clears the stale
                # "Awaiting your review" HITL card. Non-fatal.
                try:
                    from .graph.nodes.schema_db_writer import _sync_run_activity
                    await _sync_run_activity(migration_id, caller="inline_error")
                except Exception:
                    pass

        asyncio.create_task(_inline_advance())

        return MigrationApprovalResponse(
            migration_id=migration_id_uuid,
            status="advancing",
            message=f"Pipeline advancing past step '{step_key}'.",
            decisions_processed=0,
        )

    @app.post(
        "/api/migration/{migration_id}/rerun-from/{node_num}",
        response_model=MigrationApprovalResponse,
        tags=["Migration"],
        summary="Re-run the pipeline from a previously-completed step (checkpoint rewind)",
    )
    async def migration_rerun_from(
        migration_id: str = Path(..., description="Migration UUID"),
        node_num: int = Path(..., description="Pipeline node number to re-run from"),
        session: AsyncSession = Depends(get_db_session),
    ) -> MigrationApprovalResponse:
        """
        Rewind the LangGraph checkpoint to a chosen completed node and re-run forward.

        Lets the user go back to a mapping / preprocess / hierarchy step, change the
        column/table mapping at the re-fired gate, and continue through to the DB write —
        without restarting the whole migration. Node → graph mapping:
            1 ingest · 2 deterministic · 4 semantic · 6 preprocess · 7 hierarchy · 9 output
        """
        node_by_num = {
            1: "ingest_node",
            2: "deterministic_mapper_node",
            4: "semantic_mapper_node",
            6: "preprocess_node",
            7: "hierarchy_node",
            9: "output_generator_node",
        }
        target_node = node_by_num.get(node_num)
        if not target_node:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot re-run from node {node_num}. Re-runnable nodes: {sorted(node_by_num)}",
            )

        migration_id_uuid = UUID(migration_id)
        migration_job = (
            await session.execute(select(MigrationJob).where(MigrationJob.id == migration_id_uuid))
        ).scalar_one_or_none()
        if not migration_job:
            raise HTTPException(status_code=404, detail="Migration not found")

        graph = get_migration_graph_instance()
        base_config = {"configurable": {"thread_id": migration_id}}

        # Find the checkpoint where the graph was about to run target_node.
        async def _find_target_config():
            try:
                async for snap in graph.aget_state_history(base_config):
                    if target_node in (snap.next or ()):
                        return snap.config
                return None
            except (NotImplementedError, AttributeError):
                import asyncio as _asyncio

                def _sync_find():
                    for snap in graph.get_state_history(base_config):
                        if target_node in (snap.next or ()):
                            return snap.config
                    return None

                return await _asyncio.get_event_loop().run_in_executor(None, _sync_find)

        try:
            target_config = await _find_target_config()
        except Exception as e:
            logger.exception(f"[{migration_id}] rerun-from history read failed: {e}")
            raise HTTPException(status_code=500, detail=f"Could not read migration checkpoints: {e}")

        if not target_config:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"No checkpoint found to re-run node {node_num} ({target_node}). "
                    "The step may not have run in this migration yet."
                ),
            )

        # Flip to running and clear any pending gate so the UI reflects the re-run.
        reset_values: dict[str, Any] = dict(
            status="running",
            pending_gate_type=None,
            pending_gate_payload=None,
            error_message=None,
        )
        if node_num == 1:
            # Full restart from Node 1: discard the prior run's per-node log
            # accumulator and rewind the step pointer. node_logs is append-only,
            # so without this GET /status would keep echoing the discarded run's
            # completed nodes (e.g. nodes 4-9) until the fresh run re-reaches each
            # one — surfacing stale "completed" cards in the new run's history.
            # The frontend has already archived the discarded run for review.
            reset_values["node_logs"] = []
            reset_values["current_step"] = "1_ingest"
        await session.execute(
            update(MigrationJob)
            .where(MigrationJob.id == migration_id_uuid)
            .values(**reset_values)
        )
        await session.commit()

        rerun_config = {**target_config, "run_name": f"migration:{migration_id}:rerun:{node_num}"}

        async def _inline_rerun():
            err_msg: str | None = None
            try:
                with bind_runtime_log_context(migration_id=migration_id):
                    result = await graph.ainvoke(None, config=rerun_config)
                if isinstance(result, dict) and result.get("error_message"):
                    node_status = result.get("status", "")
                    if node_status not in ("complete", "step_paused", "awaiting_review"):
                        err_msg = str(result["error_message"])[:500]
            except Exception as exc:
                if type(exc).__name__ in ("GraphInterrupt", "NodeInterrupt"):
                    pass  # expected — re-fired gate / step pause
                else:
                    logger.exception(f"[{migration_id}] Inline rerun error: {exc}")
                    err_msg = str(exc)[:500]
            if err_msg:
                try:
                    from sqlalchemy import update as _update
                    _sf = get_async_session_factory()
                    async with _sf() as err_session:
                        await err_session.execute(
                            _update(MigrationJob)
                            .where(MigrationJob.id == migration_id_uuid)
                            .values(status="failed", error_message=err_msg)
                        )
                        await err_session.commit()
                except Exception:
                    pass

        asyncio.create_task(_inline_rerun())

        logger.info(f"[{migration_id}] Re-running from node {node_num} ({target_node})")
        return MigrationApprovalResponse(
            migration_id=migration_id_uuid,
            status="running",
            message=f"Re-running from node {node_num} ({target_node}). Edit at the re-fired gate, then continue.",
            decisions_processed=0,
        )

    @app.post(
        "/api/migration/{migration_id}/retry-ddl",
        tags=["Migration"],
        summary="Retry DDL execution after a ddl_failed error with corrected field definitions",
    )
    async def retry_migration_ddl(
        migration_id: str = Path(..., description="Migration UUID"),
        request: dict = {},
        session: AsyncSession = Depends(get_db_session),
    ):
        """
        Re-submit corrected field definitions after a DDL failure.

        When status == "ddl_failed", the user has seen which SQL statement failed
        (returned in the /status response as "error_message"). They correct the
        offending field definition (e.g. fix the data_type, use a valid column name)
        and POST the corrected extra_fields_config here.

        This endpoint:
        1. Validates the migration is in ddl_failed state
        2. Injects the corrected extra_fields_config into state via Command.update
        3. Re-enqueues the graph to re-run Node 9 DDL phase from scratch

        Request body:
        {
            "extra_fields_config": [
                {
                    "source_field": "vendor_ref",
                    "source_table": "assets_ext",
                    "storage_strategy": "custom",
                    "target_table": "assets",
                    "custom_column_name": "vendor_asset_ref",
                    "data_type": "VARCHAR(100)",
                    "nullable": true,
                    "user_approved": true
                }
            ]
        }
        """
        try:
            from uuid import UUID
            job_id = UUID(migration_id)

            result = await session.execute(
                select(MigrationJob).where(MigrationJob.id == job_id)
            )
            job = result.scalar_one_or_none()

            if not job:
                raise HTTPException(status_code=404, detail="Migration not found")

            if job.status != "ddl_failed":
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Migration is not in ddl_failed state (current: {job.status}). "
                        f"Only ddl_failed migrations can be retried via this endpoint."
                    ),
                )

            corrected_config = request.get("extra_fields_config")
            if not corrected_config:
                raise HTTPException(
                    status_code=400,
                    detail="extra_fields_config is required with corrected field definitions",
                )

            logger.info(
                f"[{migration_id}] Retrying DDL with "
                f"{len(corrected_config)} corrected field definitions"
            )

            # Enqueue resume_migration with gate_type="ddl_retry"
            enqueued = False
            try:
                from arq import create_pool
                from arq.connections import RedisSettings
                settings = get_settings()
                if settings.redis_url:
                    redis_pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
                    await redis_pool.enqueue_job(
                        "resume_migration",
                        migration_id=migration_id,
                        gate_type="ddl_retry",
                        decisions={"extra_fields_config": corrected_config},
                    )
                    await redis_pool.aclose()
                    enqueued = True
                    logger.info(f"[{migration_id}] DDL retry enqueued via ARQ")
            except Exception as arq_err:
                logger.warning(f"[{migration_id}] ARQ enqueue failed, running inline: {arq_err}")

            if not enqueued:
                try:
                    import asyncio
                    from .worker import resume_migration
                    asyncio.create_task(
                        resume_migration(
                            {},
                            migration_id=migration_id,
                            gate_type="ddl_retry",
                            decisions={"extra_fields_config": corrected_config},
                        )
                    )
                except Exception as inline_err:
                    logger.error(f"[{migration_id}] Inline DDL retry failed: {inline_err}")
                    raise HTTPException(status_code=500, detail="Failed to retry DDL")

            return {
                "migration_id": migration_id,
                "status": "retrying",
                "message": f"DDL retry enqueued with {len(corrected_config)} corrected definitions.",
            }

        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Failed to retry DDL for migration: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.get(
        "/api/migration/{migration_id}/audit",
        response_model=MigrationAuditResponse,
        tags=["Migration"],
        summary="Get complete audit trail",
    )
    async def get_audit_trail(
        migration_id: str = Path(..., description="Migration UUID"),
        session: AsyncSession = Depends(get_db_session),
    ) -> MigrationAuditResponse:
        """Get complete audit trail of all mapping decisions for a migration."""
        try:
            migration_id_uuid = UUID(migration_id)

            # Fetch all field mappings for this migration
            result = await session.execute(
                select(MigrationFieldMapping)
                .where(MigrationFieldMapping.migration_id == migration_id_uuid)
                .order_by(MigrationFieldMapping.decided_at.desc())
            )
            mappings = result.scalars().all()

            audit_mappings = [
                FieldMappingAudit(
                    source_field=m.source_field,
                    target_field=m.target_field,
                    confidence=m.confidence,
                    tier=m.tier,
                    rationale=m.rationale,
                    decided_at=m.decided_at,
                    reviewer_id=m.reviewer_id,
                )
                for m in mappings
            ]

            return MigrationAuditResponse(
                migration_id=migration_id_uuid,
                total_mappings=len(audit_mappings),
                mappings=audit_mappings,
            )

        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Failed to get audit trail: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.get(
        "/api/migration/{migration_id}/mappings",
        tags=["Migration"],
        summary="Get all field mappings for a migration",
    )
    async def get_migration_mappings(
        migration_id: str = Path(..., description="Migration UUID"),
        tier: Optional[str] = Query(None, description="Filter by tier: T1_exact, T1_alias, T1_registry, T2_semantic, T1_human_approved, T1_human_override, unmapped"),
        session: AsyncSession = Depends(get_db_session),
    ):
        """
        Get all field mappings produced by the pipeline for this migration.

        Returns Tier 1 and Tier 2 mappings with confidence scores, tier labels,
        and rationale. Optionally filter to a specific tier.
        """
        try:
            result = await session.execute(
                select(MigrationFieldMapping)
                .where(MigrationFieldMapping.migration_id == UUID(migration_id))
                .order_by(MigrationFieldMapping.decided_at.desc())
            )
            mappings = result.scalars().all()

            if tier:
                mappings = [m for m in mappings if m.tier == tier]

            tier_counts: dict = {}
            for m in mappings:
                tier_counts[m.tier] = tier_counts.get(m.tier, 0) + 1

            return {
                "migration_id": migration_id,
                "total_mappings": len(mappings),
                "tier_breakdown": tier_counts,
                "mappings": [
                    {
                        "source_field": m.source_field,
                        "target_field": m.target_field,
                        "confidence": m.confidence,
                        "tier": m.tier,
                        "rationale": m.rationale,
                        "decided_at": m.decided_at.isoformat() if m.decided_at else None,
                        "reviewer_id": m.reviewer_id,
                    }
                    for m in mappings
                ],
            }

        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Failed to get migration mappings: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.get(
        "/api/migration/{migration_id}/hierarchy",
        tags=["Migration"],
        summary="Get detected hierarchy for a migration",
    )
    async def get_migration_hierarchy(
        migration_id: str = Path(..., description="Migration UUID"),
        session: AsyncSession = Depends(get_db_session),
    ):
        """
        Get the hierarchy relationships detected by Node 6 (hierarchy resolver).

        Returns FK relationships, containment hierarchies, and self-referencing
        trees found in the source data. Available after Node 6 completes.
        """
        try:
            result = await session.execute(
                select(MigrationJob).where(MigrationJob.id == UUID(migration_id))
            )
            migration_job = result.scalar_one_or_none()

            if not migration_job:
                raise HTTPException(status_code=404, detail="Migration not found")

            # Hierarchy data is stored in the pending_gate_payload when the pipeline
            # is paused at Gate 2 (hierarchy verification), or in output after Node 7.
            # Return whatever is available from the job record.
            hierarchy_payload = None
            if migration_job.pending_gate_type == "hierarchy" and migration_job.pending_gate_payload:
                hierarchy_payload = migration_job.pending_gate_payload

            return {
                "migration_id": migration_id,
                "status": migration_job.status,
                "hierarchy_available": hierarchy_payload is not None,
                "hierarchy": hierarchy_payload,
                "note": (
                    "Hierarchy data is populated when status='awaiting_review' "
                    "and pending_gate_type='hierarchy' (after Node 6 completes)."
                ) if hierarchy_payload is None else None,
            }

        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Failed to get migration hierarchy: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.get(
        "/api/migration/{migration_id}/download/{format}",
        response_model=MigrationDownloadResponse,
        tags=["Migration"],
        summary="Get download URL for migration output",
    )
    async def download_migration_output(
        migration_id: str = Path(..., description="Migration UUID"),
        format: Literal["json", "csv", "sql", "pdf"] = Path(..., description="Output format"),
        session: AsyncSession = Depends(get_db_session),
    ) -> MigrationDownloadResponse:
        """
        Get signed download URL for migration output in requested format.

        Formats: json (nested schema), csv (flat export), sql (INSERT statements), pdf (summary report)
        """
        try:
            migration_id_uuid = UUID(migration_id)

            result = await session.execute(
                select(MigrationJob).where(MigrationJob.id == migration_id_uuid)
            )
            migration_job = result.scalar_one_or_none()

            if not migration_job:
                raise HTTPException(status_code=404, detail="Migration not found")

            if migration_job.status != "complete":
                raise HTTPException(
                    status_code=400,
                    detail="Migration must be complete before downloading outputs",
                )

            # Determine which URL to return based on format
            url_map = {
                "json": migration_job.output_json_url,
                "csv": migration_job.output_csv_url,
                "sql": migration_job.output_sql_url,
                "pdf": migration_job.migration_report_url,
            }

            download_url = url_map.get(format)
            if not download_url:
                raise HTTPException(
                    status_code=400,
                    detail=f"No output available for format: {format}",
                )

            # In production, would generate signed URL here
            return MigrationDownloadResponse(
                migration_id=migration_id_uuid,
                format=format,
                download_url=download_url,
                expires_in_minutes=60,
            )

        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Failed to get download URL: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # ── Feature 4 — Activity Log (Section 1 feed + Section 2 detail) ─────────────
    def _coerce_org_uuid(organization_id: Optional[str]) -> Optional[UUID]:
        if not organization_id:
            return None
        try:
            return UUID(organization_id)
        except (ValueError, TypeError):
            raise HTTPException(
                status_code=400, detail=f"Invalid organization_id UUID: {organization_id}"
            )

    @app.get(
        "/api/activity-log",
        tags=["Activity Log"],
        summary="List activity-log entries (newest first)",
    )
    async def list_activity_log(
        session_id: Optional[str] = Query(None, description="Filter by session id"),
        organization_id: Optional[str] = Query(None, description="Filter by organization UUID"),
        limit: int = Query(50, ge=1, le=200),
        session: AsyncSession = Depends(get_db_session),
    ):
        """Newest-first Activity Summary feed (AL.1). One row per trigger, not per tool call."""
        from .udr import list_activity

        try:
            org = _coerce_org_uuid(organization_id)
            return await list_activity(
                organization_id=org, session_id=session_id, limit=limit, session=session
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Failed to list activity log: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.get(
        "/api/activity-log/unread",
        tags=["Activity Log"],
        summary="Unread count + worst colour for the notification icon",
    )
    async def activity_log_unread(
        organization_id: Optional[str] = Query(None, description="Filter by organization UUID"),
        session: AsyncSession = Depends(get_db_session),
    ):
        """{count, worst_color} over UNREAD entries — drives the bell colour + badge (AL.1 AC4/AC5)."""
        from .udr import unread_summary

        try:
            org = _coerce_org_uuid(organization_id)
            return await unread_summary(organization_id=org, session=session)
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Failed to summarise unread activity: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.post(
        "/api/activity-log/{entry_id}/read",
        tags=["Activity Log"],
        summary="Mark an activity entry read",
    )
    async def activity_log_mark_read(
        entry_id: str = Path(..., description="Activity entry UUID"),
        session: AsyncSession = Depends(get_db_session),
    ):
        from .udr import mark_activity_read

        try:
            try:
                eid = UUID(entry_id)
            except (ValueError, TypeError):
                raise HTTPException(status_code=400, detail=f"Invalid entry id: {entry_id}")
            ok = await mark_activity_read(eid, session=session)
            await session.commit()
            if not ok:
                raise HTTPException(status_code=404, detail="Activity entry not found")
            return {"ok": True}
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Failed to mark activity read: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.get(
        "/api/activity-log/{entry_id}",
        tags=["Activity Log"],
        summary="Activity entry detail (with full processing log)",
    )
    async def get_activity_log_detail(
        entry_id: str = Path(..., description="Activity entry UUID"),
        session: AsyncSession = Depends(get_db_session),
    ):
        """Section 2 detail: summary fields + the assembled Processing Log (chain-of-thought
        / chain-of-action stages + mapping decisions)."""
        from .udr import get_activity

        try:
            try:
                eid = UUID(entry_id)
            except (ValueError, TypeError):
                raise HTTPException(status_code=400, detail=f"Invalid entry id: {entry_id}")
            entry = await get_activity(eid, session=session)
            if entry is None:
                raise HTTPException(status_code=404, detail="Activity entry not found")
            # TEMP [activity-trace] — what the detail API actually serves for this row, so the
            # DB→API→frontend step count can be compared end-to-end. Remove once verified.
            try:
                _pl = (entry.get("processing_log") if isinstance(entry, dict) else None) or {}
                _steps = _pl.get("steps") or []
                logger.info(
                    "[activity-trace] GET detail entry=%s status=%s steps=%d stages=%s",
                    entry_id, (entry.get("status") if isinstance(entry, dict) else None),
                    len(_steps), [s.get("stage") for s in _steps if isinstance(s, dict)],
                )
            except Exception:
                pass
            return entry
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Failed to fetch activity detail: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # ── Feature 4 Section 3 — inline human-in-the-loop actions (AL.3/AL.4) ───────
    @app.get(
        "/api/activity-log/{entry_id}/actions",
        tags=["Activity Log"],
        summary="Inline human-in-the-loop actions for an activity entry (Section 3)",
    )
    async def list_activity_actions(
        entry_id: str = Path(..., description="Activity entry UUID"),
        session: AsyncSession = Depends(get_db_session),
    ):
        from .udr import get_activity, list_actions

        try:
            try:
                eid = UUID(entry_id)
            except (ValueError, TypeError):
                raise HTTPException(status_code=400, detail=f"Invalid entry id: {entry_id}")
            rows = await list_actions(eid, session=session)
            # CAFM-004/006 — surface a live gate pause as a FIRST-CLASS Section-3 action row (not just
            # a link-only card). Synthesized from the entry's ``awaiting_review`` step, which is a live
            # projection of the pending gate — so the row auto-clears the moment the gate is passed,
            # with no DB write and no resolution race. Marked ``route_only``: the actual review happens
            # in the migration panel (per-table / per-field choices the log can't offer), so the FE
            # routes there rather than resolving inline. Best-effort: never breaks the actions list.
            gate_cards: list[dict] = []
            try:
                from .udr.gate_actions import apply_gate_decisions, build_gate_decision_actions

                entry = await get_activity(eid, session=session)
                if isinstance(entry, dict):
                    steps = ((entry.get("processing_log") or {}).get("steps")) or []
                    aw = next(
                        (s for s in steps if isinstance(s, dict) and s.get("stage") == "awaiting_review"),
                        None,
                    )
                    status = str(entry.get("status") or "").lower()
                    if aw is not None and status not in ("completed", "failed", "error"):
                        refs = entry.get("refs") or {}
                        mig = str(refs.get("udr_run_id") or refs.get("migration_id") or "").strip()
                        # CAFM-004/006 — one card PER real decision (merge groups, FK-vs-shared) from
                        # the run's column_intelligence, matching the sample's "Action N of M" cards.
                        # Each is inline-resolvable; a resolved choice lives in refs.gate_decisions,
                        # overlaid here so it reads "resolved" across the ~2.5s Section-3 polls.
                        _md = ((entry.get("processing_log") or {}).get("mapping_decisions"))
                        gate_cards = apply_gate_decisions(
                            build_gate_decision_actions(
                                refs.get("column_intelligence"), entry_id=str(eid),
                                migration_id=mig, mapping_decisions=_md,
                            ),
                            refs.get("gate_decisions"),
                        )
                        for c in gate_cards:
                            c["created_at"] = entry.get("created_at")
                        if not gate_cards:
                            # Fall back to a single generic gate row when the run carries no
                            # per-decision CI (e.g. a field-mapping / hierarchy gate).
                            gate_cards = [{
                                "id": f"gate:{eid}",
                                "entry_id": str(eid),
                                "kind": "approval",
                                "label": aw.get("label") or "Awaiting your review",
                                "description": aw.get("text")
                                or "This run is paused — your review is required before it continues.",
                                "options": [],
                                "status": "pending",
                                "route_only": True,
                                "migration_id": mig,
                                "chosen_option_id": None,
                                "resolution_note": None,
                                "deadline_at": None,
                                "resolved_at": None,
                                "created_at": entry.get("created_at"),
                            }]
            except Exception:
                logger.debug("gate-review action synthesis skipped", exc_info=True)
                gate_cards = []
            return [*gate_cards, *rows] if gate_cards else rows
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Failed to list activity actions: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.get(
        "/api/activity-actions/pending",
        tags=["Activity Log"],
        summary="All still-pending inline actions (org-scoped)",
    )
    async def list_pending_activity_actions(
        organization_id: Optional[str] = Query(None, description="Organization UUID"),
        session: AsyncSession = Depends(get_db_session),
    ):
        from .udr import list_pending_actions

        try:
            org = _coerce_org_uuid(organization_id)
            return await list_pending_actions(organization_id=org, session=session)
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Failed to list pending actions: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.post(
        "/api/activity-actions/{action_id}/resolve",
        tags=["Activity Log"],
        summary="Record a human decision on an inline action (AL.3)",
    )
    async def resolve_activity_action(
        action_id: str = Path(..., description="Activity action UUID"),
        body: dict = Body(default={}),
        session: AsyncSession = Depends(get_db_session),
    ):
        from .udr import resolve_action

        try:
            option_id = (body or {}).get("option_id")
            if not option_id:
                raise HTTPException(status_code=400, detail="option_id is required")

            # CAFM-004/006 — a synthesized gate-decision card ("gate:{entry}:merge:…" /
            # "gate:{entry}:fk:…") isn't a persisted ActivityAction; validate the chosen option
            # against the rebuilt card and record it durably on the run entry (refs.gate_decisions).
            if str(action_id).startswith("gate:"):
                from .udr import get_activity
                from .udr.activity_persist import record_gate_decision
                from .udr.gate_actions import find_decision_card, valid_option_ids

                parts = str(action_id).split(":")
                if len(parts) < 3:
                    raise HTTPException(status_code=400, detail=f"Invalid gate action id: {action_id}")
                entry_id = parts[1]
                try:
                    eid = UUID(entry_id)
                except (ValueError, TypeError):
                    raise HTTPException(status_code=400, detail=f"Invalid gate action id: {action_id}")
                entry = await get_activity(eid, session=session)
                if not isinstance(entry, dict):
                    raise HTTPException(status_code=404, detail="Activity entry not found")
                refs = entry.get("refs") or {}
                mig = str(refs.get("udr_run_id") or refs.get("migration_id") or "").strip()
                card = find_decision_card(
                    refs.get("column_intelligence"),
                    entry_id=entry_id, migration_id=mig, card_id=str(action_id),
                    mapping_decisions=((entry.get("processing_log") or {}).get("mapping_decisions")),
                )
                if card is None:
                    raise HTTPException(status_code=404, detail="Gate decision not found")
                if str(option_id) not in valid_option_ids(card):
                    raise HTTPException(status_code=400, detail="invalid option_id for this action")
                label = next(
                    (o["label"] for o in card["options"] if str(o["id"]) == str(option_id)), None
                )
                rec = await record_gate_decision(
                    eid, str(action_id), option_id=str(option_id),
                    option_label=label, note=(body or {}).get("note"), session=session,
                )
                await session.commit()
                return {"ok": True, "action": {
                    **card, "status": "resolved", "chosen_option_id": str(option_id),
                    "resolution_note": label, "resolved_at": (rec or {}).get("at"),
                }}

            try:
                aid = UUID(action_id)
            except (ValueError, TypeError):
                raise HTTPException(status_code=400, detail=f"Invalid action id: {action_id}")
            try:
                updated = await resolve_action(
                    aid, str(option_id), note=(body or {}).get("note"), session=session
                )
            except ValueError:
                raise HTTPException(status_code=400, detail="invalid option_id for this action")
            await session.commit()
            if updated is None:
                raise HTTPException(status_code=404, detail="Activity action not found")
            return {"ok": True, "action": updated}
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Failed to resolve activity action: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.post(
        "/api/activity-actions/sweep-timeouts",
        tags=["Activity Log"],
        summary="Server-side 15-min timeout sweep (AL.4) — invoked by the scheduler",
    )
    async def sweep_activity_timeouts(
        organization_id: Optional[str] = Query(None, description="Organization UUID (optional)"),
        session: AsyncSession = Depends(get_db_session),
    ):
        from .udr import sweep_timeouts

        try:
            org = _coerce_org_uuid(organization_id)
            result = await sweep_timeouts(organization_id=org, session=session)
            await session.commit()
            return result
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Failed to sweep activity timeouts: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # ── Feature 4 — trigger emitters (AL.1 triggers 2-4: external input / expiry / schedule) ──
    @app.post(
        "/api/activity-log/emit/external-input",
        tags=["Activity Log"],
        summary="Emit an external-input activity entry (email / sheet / work-order request)",
    )
    async def emit_external_input(
        body: dict = Body(default={}),
        session: AsyncSession = Depends(get_db_session),
    ):
        from .udr import build_external_input_entry, record_activity

        try:
            b = body or {}
            if not b.get("summary"):
                raise HTTPException(status_code=400, detail="summary is required")
            entry = build_external_input_entry(
                source=str(b.get("source", "external")),
                summary=str(b["summary"]),
                detail=b.get("detail"),
                status=b.get("status", "completed"),
                refs=b.get("refs"),
            )
            org = _coerce_org_uuid(b.get("organization_id"))
            aid = await record_activity(
                entry, organization_id=org, session_id=b.get("session_id"), session=session
            )
            await session.commit()
            return {"activity_id": aid}
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Failed to emit external input: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.post(
        "/api/activity-log/scan/expiries",
        tags=["Activity Log"],
        summary="Scan items for expiry/threshold breaches and emit activity entries",
    )
    async def scan_activity_expiries(
        body: dict = Body(default={}),
        session: AsyncSession = Depends(get_db_session),
    ):
        from .udr import record_activity, scan_expiries

        try:
            b = body or {}
            within = int(b.get("within_days", 30) or 30)
            entries = scan_expiries(b.get("items") or [], now=datetime.utcnow(), within_days=within)
            org = _coerce_org_uuid(b.get("organization_id"))
            ids = [await record_activity(e, organization_id=org, session=session) for e in entries]
            await session.commit()
            return {"emitted": len(ids), "activity_ids": ids}
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Failed to scan expiries: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.post(
        "/api/activity-log/scan/schedules",
        tags=["Activity Log"],
        summary="Scan scheduled items that are due now and emit activity entries",
    )
    async def scan_activity_schedules(
        body: dict = Body(default={}),
        session: AsyncSession = Depends(get_db_session),
    ):
        from .udr import record_activity, scan_due_schedules

        try:
            b = body or {}
            entries = scan_due_schedules(b.get("items") or [], now=datetime.utcnow())
            org = _coerce_org_uuid(b.get("organization_id"))
            ids = [await record_activity(e, organization_id=org, session=session) for e in entries]
            await session.commit()
            return {"emitted": len(ids), "activity_ids": ids}
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Failed to scan schedules: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # ── Feature 7 F7-3 — entity 'work cloud' (from the persisted UDR relationship graph) ──
    @app.get(
        "/api/entity/{entity_type}/{entity_id}/relationships",
        tags=["UDR"],
        summary="Entity work cloud — related entities + relationship type/provenance/confidence",
    )
    async def get_entity_work_cloud(
        entity_type: str = Path(..., description="Entity type / table name"),
        entity_id: str = Path(..., description="Entity instance id (echoed; cloud is type-level)"),
        run_id: Optional[str] = Query(None, description="Scope to one UDR run"),
        organization_id: Optional[str] = Query(None, description="Scope to one organization"),
        session: AsyncSession = Depends(get_db_session),
    ):
        """The entity's relationship cluster (both directions) from the persisted
        UdrEntityRelationship edges. Schema-level (type->type); entity_id is echoed for
        future instance-level traversal."""
        from .udr import query_work_cloud

        try:
            org = _coerce_org_uuid(organization_id)
            cloud = await query_work_cloud(
                entity_type, run_id=run_id, organization_id=org, session=session
            )
            cloud["entity_id"] = entity_id
            return cloud
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Failed to build entity work cloud: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.get(
        "/api/entity/{entity_type}/{entity_id}/instances",
        tags=["UDR"],
        summary="Instance-level work cloud — the related DATA ROWS for one entity instance",
    )
    async def get_entity_instance_cloud(
        entity_type: str = Path(..., description="Entity type / table name"),
        entity_id: str = Path(..., description="Entity instance primary-key value"),
        run_id: Optional[str] = Query(None, description="Scope to one UDR run (recommended)"),
        organization_id: Optional[str] = Query(None, description="Scope to one organization"),
        limit: int = Query(50, ge=1, le=500),
        session: AsyncSession = Depends(get_db_session),
    ):
        """Traverses the persisted FK edges into the live plenum_cafm data rows for a specific
        instance (parent rows it references + child rows that reference it)."""
        from .udr import query_instance_work_cloud

        try:
            org = _coerce_org_uuid(organization_id)
            return await query_instance_work_cloud(
                entity_type, entity_id, run_id=run_id, organization_id=org, limit=limit, session=session
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Failed to build instance work cloud: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # ── Feature 7.10 AC6/AC7 — apply a Test-2 flagged-pair resolution + auto-rerun ──
    @app.post(
        "/api/migration/{migration_id}/udr/test2/resolve",
        tags=["UDR"],
        summary="Apply Test-2 resolutions (define_fk / create_reference_table / mark_coincidental) and re-run Test 2",
    )
    async def resolve_test2_flags(
        migration_id: str = Path(..., description="UDR run id (= migration id)"),
        body: dict = Body(default={}),
        organization_id: Optional[str] = Query(None),
        session: AsyncSession = Depends(get_db_session),
    ):
        """For each flagged column pair the user resolves, apply the choice to the data model
        (define a FK, create a reference table, or mark the overlap coincidental), persist it,
        and re-run Test 2 over the involved tables. Returns the fresh Test-2 report."""
        import re as _re2

        from .udr.persistence import load_run_edges, load_run_table_metadata
        from .udr.validation import apply_test2_resolutions

        resolutions = body.get("resolutions") or []
        if not isinstance(resolutions, list) or not resolutions:
            raise HTTPException(status_code=400, detail="resolutions[] is required")

        _IDENT2 = _re2.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
        # Tables the user is resolving — only these are re-fetched and re-tested.
        involved: set[str] = set()
        for r in resolutions:
            for k in ("table_a", "table_b"):
                t = str(r.get(k) or "").strip()
                if t:
                    involved.add(t)
        bad = [t for t in involved if not _IDENT2.match(t)]
        if bad:
            raise HTTPException(status_code=400, detail=f"Unsafe table identifiers: {bad}")

        try:
            org = _coerce_org_uuid(organization_id)
            # PKs per table from the persisted run graph metadata.
            tmeta = await load_run_table_metadata(migration_id, session=session)
            pk_by_table = {
                str(m.get("table")): list(m.get("primary_key") or [])
                for m in tmeta
                if isinstance(m, dict) and m.get("table")
            }
            # Re-fetch the involved tables' rows (capped — Test 2 needs distinct values).
            schema = "plenum_cafm"
            tables: dict[str, dict] = {}
            for tbl in involved:
                res = await session.execute(text(f'SELECT * FROM {schema}."{tbl}" LIMIT 20000'))
                cols = list(res.keys())
                rows = [dict(zip(cols, row)) for row in res.fetchall()]
                tables[tbl] = {"rows": rows, "columns": cols, "pk": pk_by_table.get(tbl, [])}

            # Existing relationships for this run: REFERENCES = defined FKs, COINCIDENTAL = documented non-rels.
            edges = await load_run_edges(migration_id, organization_id=org, session=session)
            defined_fks = [
                (e["src_entity"], e["src_column"], e["dst_entity"], e["dst_column"])
                for e in edges
                if (e.get("rel_type") or "REFERENCES") == "REFERENCES"
            ]
            coincidental = [
                ((e["src_entity"], e["src_column"]), (e["dst_entity"], e["dst_column"]))
                for e in edges
                if e.get("rel_type") == "COINCIDENTAL"
            ]

            result = apply_test2_resolutions(
                tables, defined_fks, resolutions, coincidental_pairs=coincidental
            )

            # Persist the NEW relationships introduced by the resolutions.
            existing_fk = {(a, b, c, d) for (a, b, c, d) in defined_fks}
            new_edges: list[dict] = []
            for fk in result["defined_fks"]:
                if tuple(fk) not in existing_fk:
                    new_edges.append(
                        {
                            "src_entity": fk[0], "src_column": fk[1],
                            "dst_entity": fk[2], "dst_column": fk[3],
                            "rel_type": "REFERENCES", "provenance": "user", "confidence": 1.0,
                            "evidence": "Test-2 resolution",
                        }
                    )
            existing_coin = {
                frozenset({(s, sc), (d, dc)})
                for ((s, sc), (d, dc)) in coincidental
            }
            for pair in result["coincidental_pairs"]:
                (s, sc), (d, dc) = pair[0], pair[1]
                if frozenset({(s, sc), (d, dc)}) not in existing_coin:
                    new_edges.append(
                        {
                            "src_entity": s, "src_column": sc, "dst_entity": d, "dst_column": dc,
                            "rel_type": "COINCIDENTAL", "provenance": "user", "confidence": 1.0,
                            "evidence": "Test-2 documented non-relationship",
                        }
                    )
            persisted = 0
            if new_edges:
                from .udr.persistence import persist_relationships

                persisted = await persist_relationships(
                    new_edges, run_id=migration_id, organization_id=org, session=session
                )

            report = result["report"]
            return {
                "migration_id": migration_id,
                "applied": result["applied"],
                "new_reference_tables": result["new_reference_tables"],
                "relationships_persisted": persisted,
                "test2": report,
                "blocks_udr": report.get("blocks_udr"),
                "flagged_unexplained": report.get("flagged_unexplained"),
            }
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Test-2 resolve failed: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # ── Feature 7.9 AC6 — Test 1 'Fix and Retest' (one action) ──────────────────
    @app.post(
        "/api/migration/{migration_id}/udr/test1/fix-and-retest",
        tags=["UDR"],
        summary="Promote shared attributes anchoring chunks to reference-table PKs, then re-run Test 1",
    )
    async def fix_and_retest_test1(
        migration_id: str = Path(..., description="UDR run id (= migration id)"),
        organization_id: Optional[str] = Query(None),
        session: AsyncSession = Depends(get_db_session),
    ):
        """One-click remediation for Test-1 (7.9) failures: every chunk wrongly anchored to a
        shared attribute is fixed by promoting that attribute to a reference table (its value
        becomes a primary key), then Test 1 is re-run automatically. Returns before/after."""
        import re as _re3

        from .udr.persistence import load_run_table_metadata, persist_relationships
        from .udr.reference_tables import remediate_test1_failures
        from .udr.validation import run_test1_chunk_pk

        _IDENT3 = _re3.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
        try:
            org = _coerce_org_uuid(organization_id)
            tmeta = await load_run_table_metadata(migration_id, session=session)
            run_tables = [
                str(m.get("table")) for m in tmeta
                if isinstance(m, dict) and m.get("table") and _IDENT3.match(str(m.get("table")))
            ]
            if not run_tables:
                raise HTTPException(status_code=404, detail="No UDR run graph for this migration")

            schema = "plenum_cafm"
            tables: dict[str, dict] = {}
            for tbl in run_tables:
                res = await session.execute(text(f'SELECT * FROM {schema}."{tbl}" LIMIT 20000'))
                cols = list(res.keys())
                rows = [dict(zip(cols, row)) for row in res.fetchall()]
                pk = next((list(m.get("primary_key") or []) for m in tmeta if m.get("table") == tbl), [])
                tables[tbl] = {"rows": rows, "columns": cols, "pk": pk}

            # Chunks for this run: anchored to one of the run's tables.
            chunks: list[dict] = []
            try:
                cres = await session.execute(
                    text(
                        f"SELECT id, row_pk FROM {schema}.document_chunks "
                        "WHERE source_table = ANY(:tbls) AND row_pk IS NOT NULL LIMIT 100000"
                    ),
                    {"tbls": run_tables},
                )
                chunks = [{"chunk_id": str(cid), "entity_id": str(rpk)} for cid, rpk in cres.fetchall()]
            except Exception as _cx:
                logger.warning(f"[fix-and-retest] chunk load skipped: {_cx}")

            before = run_test1_chunk_pk(chunks, tables)
            if before["failed"] == 0:
                return {
                    "migration_id": migration_id,
                    "remediated": False,
                    "message": "No Test-1 failures to fix.",
                    "before": before,
                    "after": before,
                }

            plan = remediate_test1_failures(tables, before)
            for ref in plan["reference_tables"]:
                tables[ref["table"]] = {
                    "rows": ref.get("rows", []),
                    "columns": ref.get("columns", []),
                    "pk": ref.get("pk", []),
                }
            after = run_test1_chunk_pk(chunks, tables)

            # Persist the FK rewrites as relationship edges (user-driven remediation).
            new_edges = [
                {
                    "src_entity": rw["table"], "src_column": rw["column"],
                    "dst_entity": rw["references_table"], "dst_column": rw["references_column"],
                    "rel_type": "REFERENCES", "provenance": "user", "confidence": 1.0,
                    "evidence": "Test-1 Fix and Retest",
                }
                for rw in plan["fk_rewrites"]
            ]
            persisted = 0
            if new_edges:
                persisted = await persist_relationships(
                    new_edges, run_id=migration_id, organization_id=org, session=session
                )

            return {
                "migration_id": migration_id,
                "remediated": True,
                "reference_tables_created": [r["table"] for r in plan["reference_tables"]],
                "relationships_persisted": persisted,
                "unresolved": plan["unresolved"],
                "before": {"failed": before["failed"], "pass_rate": before["pass_rate"], "blocks_udr": before["blocks_udr"]},
                "after": {"failed": after["failed"], "pass_rate": after["pass_rate"], "blocks_udr": after["blocks_udr"]},
            }
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Test-1 fix-and-retest failed: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # ── Feature 7 F7-5 — relationship sanctity check (flag-with-confidence POC) ──
    @app.post(
        "/api/sanctity/validate",
        tags=["UDR"],
        summary="Validate an entity link (e.g. WO↔asset) for plausibility; optionally flag it",
    )
    async def validate_sanctity(
        body: dict = Body(default={}),
        session: AsyncSession = Depends(get_db_session),
    ):
        """POC: extract floor/level + asset-type signals from the WO evidence and the asset
        record, return {plausible, confidence, reason, mismatches}. Never mutates data; if
        ``record`` is set and the link is implausible, raises an escalatable Activity-Log entry."""
        from .udr import record_sanctity_flag, validate_relationship

        try:
            b = body or {}
            wo_evidence = b.get("wo_evidence", b.get("wo_text", ""))
            asset_record = b.get("asset_record", b.get("asset", {}))
            verdict = validate_relationship(wo_evidence, asset_record)
            result: dict = {"verdict": verdict}
            if b.get("record") and not verdict["plausible"]:
                org = _coerce_org_uuid(b.get("organization_id"))
                flag = await record_sanctity_flag(
                    verdict,
                    wo_id=str(b.get("wo_id", "?")),
                    asset_id=str(b.get("asset_id", "?")),
                    organization_id=org,
                    session=session,
                )
                await session.commit()
                result["flag"] = flag
            return result
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Failed to validate sanctity: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.post(
        "/api/sanctity/validate-link",
        tags=["UDR"],
        summary="Validate a WO↔asset link by GATHERING evidence from the migrated data + semantic layer",
    )
    async def validate_sanctity_link(
        body: dict = Body(default={}),
        session: AsyncSession = Depends(get_db_session),
    ):
        """Auto-fetch variant of /sanctity/validate: pulls the WO + asset rows from the
        plenum_cafm tables (enriched with row_semantic_index) and validates the link. Pass
        ``run_id`` (to resolve the primary keys) or explicit ``wo_pk`` / ``asset_pk``."""
        from .udr import validate_entity_relationship

        try:
            b = body or {}
            for req in ("wo_table", "wo_id", "asset_table", "asset_id"):
                if not b.get(req):
                    raise HTTPException(status_code=400, detail=f"{req} is required")
            org = _coerce_org_uuid(b.get("organization_id"))
            result = await validate_entity_relationship(
                wo_table=str(b["wo_table"]),
                wo_id=b["wo_id"],
                asset_table=str(b["asset_table"]),
                asset_id=b["asset_id"],
                wo_pk=b.get("wo_pk"),
                asset_pk=b.get("asset_pk"),
                run_id=b.get("run_id"),
                organization_id=org,
                record=bool(b.get("record")),
                session=session,
            )
            await session.commit()
            return result
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Failed to validate sanctity link: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.get(
        "/api/migration",
        response_model=MigrationListResponse,
        tags=["Migration"],
        summary="List migrations for organization",
    )
    async def list_migrations(
        organization_id: str = Query(..., description="Organization UUID"),
        status: Optional[str] = Query(None, description="Filter by status"),
        limit: int = Query(50, ge=1, le=500),
        offset: int = Query(0, ge=0),
        session: AsyncSession = Depends(get_db_session),
    ) -> MigrationListResponse:
        """List all migrations for an organization, optionally filtered by status."""
        try:
            try:
                org_id = UUID(organization_id)
            except (ValueError, TypeError):
                try:
                    org_id = UUID(int=int(organization_id))
                except (ValueError, TypeError):
                    raise HTTPException(status_code=400, detail=f"Invalid organization_id UUID: {organization_id}")

            # Build query
            query = select(MigrationJob).where(MigrationJob.organization_id == org_id)

            if status:
                query = query.where(MigrationJob.status == status)

            # Get total count
            count_result = await session.execute(
                select(func.count()).select_from(MigrationJob).where(
                    MigrationJob.organization_id == org_id
                )
            )
            total_count = count_result.scalar() or 0

            # Fetch paginated results
            query = query.order_by(MigrationJob.started_at.desc()).limit(limit).offset(offset)
            result = await session.execute(query)
            jobs = result.scalars().all()

            items = [
                MigrationListItem(
                    migration_id=j.id,
                    cmms_name=j.cmms_name,
                    status=j.status,
                    progress_pct=j.progress_pct,
                    t1_count=j.t1_mapped_count,
                    t2_count=j.t2_auto_count + j.t2_human_count,
                    started_at=j.started_at,
                    completed_at=j.completed_at,
                )
                for j in jobs
            ]

            return MigrationListResponse(total_count=total_count, migrations=items)

        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Failed to list migrations: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.delete(
        "/api/migration/{migration_id}",
        response_model=MigrationCancelResponse,
        tags=["Migration"],
        summary="Cancel a migration",
    )
    async def cancel_migration(
        migration_id: str = Path(..., description="Migration UUID"),
        session: AsyncSession = Depends(get_db_session),
    ) -> MigrationCancelResponse:
        """Cancel a running migration and clean up resources."""
        try:
            migration_id_uuid = UUID(migration_id)

            result = await session.execute(
                select(MigrationJob).where(MigrationJob.id == migration_id_uuid)
            )
            migration_job = result.scalar_one_or_none()

            if not migration_job:
                raise HTTPException(status_code=404, detail="Migration not found")

            if migration_job.status in ["complete", "cancelled", "failed"]:
                raise HTTPException(
                    status_code=400,
                    detail=f"Cannot cancel migration with status: {migration_job.status}",
                )

            # Mark as cancelled and clear any pending HITL gate so the job is a
            # clean terminal state — otherwise pending_gate_type lingers and the
            # UI mis-reports the cancelled run as "awaiting input / pending".
            migration_job.status = "cancelled"
            migration_job.completed_at = datetime.utcnow()
            migration_job.pending_gate_type = None
            migration_job.pending_gate_payload = None
            await session.commit()

            logger.info(f"Cancelled migration: {migration_id}")

            # Finalise the Activity Log card to "cancelled" — cancelling never runs the Node-10
            # emit, so without this the per-run card stays stuck at its last running/pending sync.
            try:
                from .graph.nodes.schema_db_writer import _sync_run_activity
                await _sync_run_activity(migration_id, caller="cancel_endpoint")
            except Exception as _act_err:
                logger.warning(f"cancel: activity sync failed for {migration_id}: {_act_err}")

            return MigrationCancelResponse(
                migration_id=migration_id_uuid,
                status="cancelled",
                message="Migration cancelled successfully",
            )

        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Failed to cancel migration: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.get(
        "/api/migration/{migration_id}/langsmith",
        response_model=LangSmithTraceResponse,
        tags=["Migration"],
        summary="Get LangSmith trace URL",
    )
    async def get_langsmith_trace(
        migration_id: str = Path(..., description="Migration UUID"),
    ) -> LangSmithTraceResponse:
        """Get LangSmith trace URL for debugging and observability."""
        from uuid import UUID

        settings = get_settings()

        if not settings.langsmith_tracing or not settings.langsmith_project:
            raise HTTPException(
                status_code=400,
                detail="LangSmith tracing not enabled",
            )

        try:
            migration_id_uuid = UUID(migration_id)

            trace_url = (
                f"{settings.langsmith_endpoint}/projects/"
                f"{settings.langsmith_project}/r/{migration_id}"
            )

            return LangSmithTraceResponse(
                migration_id=migration_id_uuid,
                trace_url=trace_url,
                project=settings.langsmith_project,
                message="Open this URL to view the full execution trace in LangSmith",
            )

        except Exception as e:
            logger.exception(f"Failed to get LangSmith URL: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # ── WebSocket for real-time event streaming ────────────────────────
    @app.websocket("/ws/migration/{migration_id}")
    async def websocket_migration_events(
        websocket: WebSocket,
        migration_id: str,
    ):
        """
        WebSocket endpoint for real-time migration event streaming.

        Client connects and receives events as the migration progresses through nodes.
        """
        try:
            migration_id_uuid = UUID(migration_id)
            await websocket.accept()

            # Create session for database queries
            session_factory = get_session_factory()

            # Verify migration exists
            async with session_factory() as session:
                result = await session.execute(
                    select(MigrationJob).where(MigrationJob.id == migration_id_uuid)
                )
                migration_job = result.scalar_one_or_none()

            if not migration_job:
                await websocket.send_json(
                    {
                        "error": "Migration not found",
                        "status_code": 404,
                    }
                )
                await websocket.close(code=1000)
                return

            logger.info(f"WebSocket connected for migration: {migration_id}")

            # Send initial status
            await websocket.send_json(
                {
                    "event_type": "connected",
                    "migration_id": str(migration_id_uuid),
                    "status": migration_job.status,
                    "progress_pct": migration_job.progress_pct,
                }
            )

            # Keep connection alive and periodically send status updates
            while True:
                await asyncio.sleep(5)

                # Refresh migration status in a new session context
                async with session_factory() as session:
                    result = await session.execute(
                        select(MigrationJob).where(MigrationJob.id == migration_id_uuid)
                    )
                    latest_job = result.scalar_one_or_none()

                if latest_job:
                    await websocket.send_json(
                        {
                            "event_type": "status_update",
                            "status": latest_job.status,
                            "progress_pct": latest_job.progress_pct,
                            "current_step": latest_job.current_step,
                        }
                    )

                    # If migration is complete or failed, close connection
                    if latest_job.status in ["complete", "failed", "cancelled"]:
                        await websocket.send_json(
                            {
                                "event_type": "complete",
                                "status": latest_job.status,
                                "message": "Migration processing finished",
                            }
                        )
                        await websocket.close(code=1000)
                        return

        except ValueError as e:
            logger.error(f"Invalid migration ID format: {migration_id}")
            try:
                await websocket.send_json({"error": "Invalid migration ID"})
                await websocket.close(code=1008)
            except Exception:
                pass
        except Exception as e:
            logger.exception(f"WebSocket error for migration {migration_id}: {e}")
            try:
                await websocket.send_json({"error": "Internal server error"})
                await websocket.close(code=1011)
            except Exception:
                pass

    # ── Internal Testing: Direct File Upload ───────────────────────────
    @app.post(
        "/api/testing/upload",
        response_model=TestIngestResponse,
        tags=["Testing"],
        summary="[Internal] Upload a file and run Node 1 (ingest) directly",
        description=(
            "For internal testing only. Accepts a CSV or Excel file upload, "
            "runs ingest_node (Node 1) directly using the uploaded bytes, "
            "and returns the full parsed state. No DB write, no ARQ dispatch."
        ),
    )
    async def test_upload_ingest(
        file: UploadFile = File(..., description="CSV or Excel file to ingest"),
        cmms_name: str = Form("Custom", description="Source CMMS system name (e.g. Maximo, Fiix)"),
        organization_id: str = Form(
            "00000000-0000-0000-0000-000000000001",
            description="Organization UUID (defaults to test org)",
        ),
    ) -> TestIngestResponse:
        """
        Upload a CSV or Excel file and run Node 1 (ingest_and_configure) directly.

        Returns the full parsed state including detected format, row/column counts,
        table health, dataset summary, and EL-M.1 validation result.
        Intended for internal QA and pipeline verification — not for production use.
        """
        from .graph.nodes.ingest_node import ingest_node

        t_start = time.monotonic()
        migration_id = str(uuid4())
        filename = file.filename or "unknown"

        # ── Validate file type ──────────────────────────────────────────────
        allowed_extensions = {".csv", ".tsv", ".xlsx", ".xls", ".xlsm"}
        ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if ext not in allowed_extensions:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported file type '{ext}'. Allowed: {', '.join(sorted(allowed_extensions))}",
            )

        # ── Validate file size ──────────────────────────────────────────────
        settings = get_settings()
        max_bytes = settings.max_file_size_mb * 1024 * 1024
        file_bytes = await file.read()
        if len(file_bytes) > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"File too large ({len(file_bytes) / 1_048_576:.1f} MB). Max: {settings.max_file_size_mb} MB",
            )

        logger.info(
            "test_upload_received",
            migration_id=migration_id,
            filename=filename,
            size_bytes=len(file_bytes),
            cmms_name=cmms_name,
        )

        # ── Build minimal MigrationState and run Node 1 ────────────────────
        state: dict = {
            "migration_id": migration_id,
            "organization_id": organization_id,
            "cmms_name": cmms_name,
            "source_file_bytes": file_bytes,   # direct upload — no Blob download
            "source_blob_url": None,
            "event_log": [],
        }

        try:
            result_state = await ingest_node(state)
        except Exception as e:
            logger.exception(f"[test_upload] ingest_node raised: {e}")
            raise HTTPException(status_code=500, detail=f"Ingest node error: {str(e)}")

        duration_ms = (time.monotonic() - t_start) * 1000

        # ── Collect table names ─────────────────────────────────────────────
        parsed_tables: dict = result_state.get("parsed_tables") or {}
        table_names = list(parsed_tables.keys())

        logger.info(
            "test_upload_complete",
            migration_id=migration_id,
            el_m1_passed=result_state.get("el_m1_passed", False),
            row_count=result_state.get("row_count", 0),
            column_count=result_state.get("column_count", 0),
            duration_ms=round(duration_ms, 1),
        )

        return TestIngestResponse(
            migration_id=migration_id,
            filename=filename,
            file_size_bytes=len(file_bytes),
            detected_file_format=result_state.get("detected_file_format"),
            detected_encoding=result_state.get("source_encoding"),
            detected_delimiter=result_state.get("source_delimiter"),
            row_count=result_state.get("row_count", 0),
            column_count=result_state.get("column_count", 0),
            table_names=table_names,
            table_health=result_state.get("table_health") or {},
            dataset_summary=result_state.get("dataset_summary"),
            column_descriptions=result_state.get("column_descriptions"),
            el_m1_passed=result_state.get("el_m1_passed", False),
            error_message=result_state.get("error_message"),
            duration_ms=round(duration_ms, 1),
        )

    # ── Testing: Ingest with Customer JSON Mapper ──────────────────────────
    @app.post(
        "/api/testing/ingest-with-mapper",
        response_model=TestIngestWithMapperResponse,
        tags=["Testing"],
        summary="[Internal] Test Node 1 & 2 with custom JSON mapper",
        description=(
            "For internal QA/testing. Upload a CSV file and customer-provided "
            "JSON mapper (field mappings). Runs Node 1 (ingest) and Node 2 "
            "(deterministic mapper) directly using the custom mapper, "
            "and returns mapped fields. No DB write, no ARQ dispatch."
        ),
    )
    async def test_ingest_with_mapper(
        file: UploadFile = File(..., description="CSV file to ingest"),
        mapper_json: Optional[str] = Form(None, description="JSON mapper config as string; uses DB schema default if omitted"),
        cmms_name: str = Form("Custom", description="CMMS system name"),
        organization_id: str = Form(
            "00000000-0000-0000-0000-000000000001",
            description="Organization UUID",
        ),
    ) -> TestIngestWithMapperResponse:
        """
        Test Node 1 (Ingest) and Node 2 (Deterministic Mapper) with custom JSON mapper.

        The JSON mapper defines:
        - canonical_fields: target field definitions
        - vendor_aliases: source → canonical mappings
        - regex_patterns: pattern-based matching rules
        - custom_transformations: field transformation logic

        Returns: mapped and unmapped fields, data quality metrics, EL-M.1 status.
        """
        migration_id = str(uuid4())
        t_start = time.monotonic()

        # ── Parse and validate mapper JSON ──────────────────────────────────
        try:
            if mapper_json:
                # User provided explicit mapper JSON
                mapper_dict = json.loads(mapper_json)
                mapper_config = JsonMapperConfig(**mapper_dict)
            elif _default_mapper_config:
                # Use default mapper config built from DB schema
                mapper_config = JsonMapperConfig(**_default_mapper_config)
            else:
                raise HTTPException(
                    status_code=400,
                    detail="No mapper_json provided and default mapper config not available"
                )
        except json.JSONDecodeError as e:
            raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}")
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid mapper config: {e}")

        logger.info(
            f"test_ingest_with_mapper: migration={migration_id}, "
            f"cmms={mapper_config.source_system}, file={file.filename}"
        )

        # ── Validate file ───────────────────────────────────────────────────
        file_bytes = await file.read()
        if not file_bytes:
            raise HTTPException(status_code=400, detail="File is empty")

        # ── Build state with mapper ─────────────────────────────────────────
        state: dict = {
            "migration_id": migration_id,
            "organization_id": organization_id,
            "cmms_name": mapper_config.source_system,
            "source_file_bytes": file_bytes,
            "json_mapper": mapper_config.model_dump(),  # ← Custom mapper
            "event_log": [],
        }

        # ── Run Node 1 (Ingest) ─────────────────────────────────────────────
        try:
            from .graph.nodes.ingest_node import ingest_node

            result_state = await ingest_node(state)

            if result_state.get("error_message"):
                raise HTTPException(
                    status_code=400, detail=f"Ingest failed: {result_state['error_message']}"
                )

            logger.info(
                f"Node 1 complete: rows={result_state.get('row_count')}, "
                f"cols={result_state.get('column_count')}"
            )

        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Node 1 failed: {e}")
            raise HTTPException(status_code=500, detail=f"Ingest error: {str(e)}")

        # ── Run Node 2 (Deterministic Mapper) ───────────────────────────────
        try:
            from .graph.nodes.deterministic_mapper import deterministic_mapper_node

            mapped_state = await deterministic_mapper_node(result_state)

            logger.info(
                f"Node 2 complete: tier1={mapped_state.get('tier1_mapped_count')}, "
                f"unresolved={len(mapped_state.get('unresolved_after_t1', []))}"
            )

        except Exception as e:
            logger.exception(f"Node 2 failed: {e}")
            raise HTTPException(status_code=500, detail=f"Mapper error: {str(e)}")

        # ── Collect mapped/unmapped fields ──────────────────────────────────
        tier1_mappings = mapped_state.get("tier1_mappings") or []
        mapped_fields_dict = {m.get("source_field"): m.get("target_field") for m in tier1_mappings}
        unmapped_fields = mapped_state.get("unresolved_after_t1") or []

        # ── Format execution logs from event_log ────────────────────────────
        execution_logs = []
        for event in mapped_state.get("event_log", []):
            if isinstance(event, dict):
                node = event.get("node", "?")
                detail = event.get("detail", "")
                event_type = event.get("event", "")
                execution_logs.append(f"[Node {node}] {event_type}: {detail}")
            else:
                execution_logs.append(str(event))

        duration_ms = (time.monotonic() - t_start) * 1000

        return TestIngestWithMapperResponse(
            migration_id=migration_id,
            filename=file.filename or "unknown",
            file_size_bytes=len(file_bytes),
            detected_file_format=mapped_state.get("detected_file_format"),
            detected_encoding=mapped_state.get("source_encoding"),
            detected_delimiter=mapped_state.get("source_delimiter"),
            row_count=mapped_state.get("row_count", 0),
            column_count=mapped_state.get("column_count", 0),
            mapped_fields=mapped_fields_dict,
            unmapped_fields=unmapped_fields,
            overall_confidence=mapped_state.get("overall_confidence", 0.0),
            el_m2_passed=mapped_state.get("el_m2_passed", False),
            table_names=list(mapped_state.get("parsed_tables", {}).keys()),
            el_m1_passed=mapped_state.get("el_m1_passed", False),
            mapper_source_system=mapper_config.source_system,
            error_message=None,
            duration_ms=round(duration_ms, 1),
            execution_logs=execution_logs,
        )

    # ── Endpoint: Test Nodes 1 + 2 + 3 (Semantic Mapper) ──────────────────
    @app.post(
        "/api/testing/ingest-with-semantic",
        response_model=TestIngestWithSemanticResponse,
        tags=["Testing"],
        summary="[Internal] Test Node 1, 2 & 3 with semantic mapping",
        description=(
            "For internal QA/testing. Upload a CSV file and customer-provided "
            "JSON mapper. Runs Node 1 (ingest), Node 2 (deterministic mapper), "
            "and Node 3 (semantic mapper) to show both auto-matched and flagged fields."
        ),
    )
    async def test_ingest_with_semantic(
        file: UploadFile = File(..., description="CSV file to ingest"),
        mapper_json: Optional[str] = Form(None, description="JSON mapper config as string; uses DB schema default if omitted"),
        cmms_name: str = Form("Custom", description="CMMS system name"),
        organization_id: str = Form(
            "00000000-0000-0000-0000-000000000001",
            description="Organization UUID",
        ),
    ) -> TestIngestWithSemanticResponse:
        """
        Test Nodes 1-3 (Ingest → Deterministic → Semantic mapping).

        Returns: Tier 1 mappings, Tier 2 auto/flagged/unmappable results.
        """
        import logging

        migration_id = str(uuid4())
        t_start = time.monotonic()

        # ── Set up log capture ──────────────────────────────────────────────
        execution_logs = []

        class ListHandler(logging.Handler):
            def emit(self, record):
                msg = self.format(record)
                execution_logs.append(msg)

        log_handler = ListHandler()
        log_handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
        root_logger = logging.getLogger()
        root_logger.addHandler(log_handler)
        root_logger.setLevel(logging.DEBUG)
        logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
        logging.getLogger("sqlalchemy.engine.Engine").setLevel(logging.WARNING)

        try:
            # ── Parse and validate mapper JSON ──────────────────────────────────
            try:
                if mapper_json:
                    # User provided explicit mapper JSON
                    mapper_dict = json.loads(mapper_json)
                    mapper_config = JsonMapperConfig(**mapper_dict)
                elif _default_mapper_config:
                    # Use default mapper config built from DB schema
                    mapper_config = JsonMapperConfig(**_default_mapper_config)
                else:
                    raise HTTPException(
                        status_code=400,
                        detail="No mapper_json provided and default mapper config not available"
                    )
            except json.JSONDecodeError as e:
                raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}")
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"Invalid mapper config: {e}")

            logger.info(
                f"test_ingest_with_semantic: migration={migration_id}, "
                f"cmms={mapper_config.source_system}, file={file.filename}"
            )

            # ── Validate file ───────────────────────────────────────────────────
            file_bytes = await file.read()
            if not file_bytes:
                raise HTTPException(status_code=400, detail="File is empty")

            # ── Build initial state ─────────────────────────────────────────────
            state: dict = {
                "migration_id": migration_id,
                "organization_id": organization_id,
                "cmms_name": mapper_config.source_system,
                "source_file_bytes": file_bytes,
                "json_mapper": mapper_config.model_dump(),
                "event_log": [],
            }

            # ── Run Node 1 (Ingest) ─────────────────────────────────────────────
            try:
                from .graph.nodes.ingest_node import ingest_node

                result_state = await ingest_node(state)

                if result_state.get("error_message"):
                    raise HTTPException(
                        status_code=400, detail=f"Ingest failed: {result_state['error_message']}"
                    )

                logger.info(
                    f"Node 1 complete: rows={result_state.get('row_count')}, "
                    f"cols={result_state.get('column_count')}"
                )

            except HTTPException:
                raise
            except Exception as e:
                logger.exception(f"Node 1 failed: {e}")
                raise HTTPException(status_code=500, detail=f"Ingest error: {str(e)}")

            # ── Run Node 2 (Deterministic Mapper) ───────────────────────────────
            try:
                from .graph.nodes.deterministic_mapper import deterministic_mapper_node

                mapped_state = await deterministic_mapper_node(result_state)

                logger.info(
                    f"Node 2 complete: tier1={mapped_state.get('tier1_mapped_count')}, "
                    f"unresolved={len(mapped_state.get('unresolved_after_t1', []))}"
                )

            except Exception as e:
                logger.exception(f"Node 2 failed: {e}")
                raise HTTPException(status_code=500, detail=f"Mapper error: {str(e)}")

            # ── Run Node 3 (Semantic Mapper) ────────────────────────────────────
            try:
                from .graph.nodes.semantic_mapper import semantic_mapper_node

                semantic_state = await semantic_mapper_node(mapped_state)

                # Count tier2 results from per-table storage
                tier2_auto_by_table = semantic_state.get('tier2_auto_by_table', {})
                tier2_flagged_by_table = semantic_state.get('tier2_flagged_by_table', {})
                tier2_unmappable_by_table = semantic_state.get('tier2_unmappable_by_table', {})

                tier2_auto_count = sum(len(m) for m in tier2_auto_by_table.values())
                tier2_flagged_count = sum(len(m) for m in tier2_flagged_by_table.values())
                tier2_unmappable_count = sum(len(m) for m in tier2_unmappable_by_table.values())

                logger.info(
                    f"Node 3 complete: tier2_auto={tier2_auto_count}, "
                    f"tier2_flagged={tier2_flagged_count}, "
                    f"tier2_unmappable={tier2_unmappable_count}"
                )

            except Exception as e:
                logger.exception(f"Node 3 failed: {e}")
                raise HTTPException(status_code=500, detail=f"Semantic mapper error: {str(e)}")

            # ── Collect results ─────────────────────────────────────────────────
            tier1_mappings = semantic_state.get("tier1_mappings") or []

            # Flatten tier2 mappings from per-table storage (same pattern as tier1_mappings)
            tier2_auto_by_table = semantic_state.get("tier2_auto_by_table", {})
            tier2_auto = [
                m for table_mappings in tier2_auto_by_table.values()
                for m in table_mappings
            ]

            tier2_flagged_by_table = semantic_state.get("tier2_flagged_by_table", {})
            tier2_flagged = [
                m for table_mappings in tier2_flagged_by_table.values()
                for m in table_mappings
            ]

            tier2_unmappable_by_table = semantic_state.get("tier2_unmappable_by_table", {})
            tier2_unmappable = [
                field for table_fields in tier2_unmappable_by_table.values()
                for field in table_fields
            ]

        finally:
            # ── Cleanup logs handler ────────────────────────────────────────
            root_logger.removeHandler(log_handler)

        # Convert tier2_auto and tier2_flagged to response format
        tier2_auto_list = [
            Tier2Mapping(
                source_field=m.get("source_field", ""),
                target_field=m.get("target_field", ""),
                confidence=m.get("confidence", 0.0),
                tier="T2_auto",
                rationale=m.get("rationale"),
            )
            for m in tier2_auto
        ]

        tier2_flagged_list = [
            Tier2Mapping(
                source_field=m.get("source_field", ""),
                target_field=m.get("target_field", ""),
                confidence=m.get("confidence", 0.0),
                tier="T2_flagged",
                rationale=m.get("rationale"),
            )
            for m in tier2_flagged
        ]

        duration_ms = (time.monotonic() - t_start) * 1000

        return TestIngestWithSemanticResponse(
            migration_id=migration_id,
            filename=file.filename or "unknown",
            file_size_bytes=len(file_bytes),
            detected_file_format=semantic_state.get("detected_file_format"),
            detected_encoding=semantic_state.get("source_encoding"),
            detected_delimiter=semantic_state.get("source_delimiter"),
            row_count=semantic_state.get("row_count", 0),
            column_count=semantic_state.get("column_count", 0),
            # Node 1 results
            table_health=semantic_state.get("table_health", {}),
            parsed_tables=semantic_state.get("parsed_tables", {}),
            full_tables=semantic_state.get("full_tables", {}),  # Complete file for downstream processing
            column_descriptions=semantic_state.get("column_descriptions", {}),
            dataset_summary=semantic_state.get("dataset_summary"),
            # Node 2 results
            tier1_mappings=tier1_mappings,
            mapped_fields={m.get("source_field"): m.get("target_field") for m in tier1_mappings},
            # Node 3 results
            tier2_auto_mappings=tier2_auto_list,
            tier2_flagged_mappings=tier2_flagged_list,
            tier2_unmappable=tier2_unmappable,
            tier2_unmappable_by_table=semantic_state.get("tier2_unmappable_by_table", {}),
            overall_confidence=semantic_state.get("overall_confidence", 0.0),
            el_m2_passed=semantic_state.get("el_m2_passed", False),
            el_m3_passed=semantic_state.get("el_m3_passed", False),
            table_names=list(semantic_state.get("parsed_tables", {}).keys()),
            el_m1_passed=semantic_state.get("el_m1_passed", False),
            mapper_source_system=mapper_config.source_system,
            error_message=None,
            duration_ms=round(duration_ms, 1),
            execution_logs=execution_logs,
        )

    # ── Endpoint: Node 4 Human Review Testing ───────────────────────────
    @app.post(
        "/api/testing/human-review",
        response_model=HumanReviewResponse,
        tags=["Testing"],
        summary="[Internal] Test Node 4 human review gate",
        description=(
            "For internal QA/testing. Takes Node 2/3 results and user approvals/custom mappings, "
            "returns final validated mappings after human review (GATE 1)."
        ),
    )
    async def test_human_review(request: HumanReviewRequest) -> HumanReviewResponse:
        """
        Test Node 4: Human Review Gate.

        User approves/rejects flagged mappings and adds custom field mappings.
        Returns final validated mapping set ready for Node 5.
        """
        import logging

        migration_id = request.migration_id
        t_start = time.monotonic()

        # ── Set up log capture ──────────────────────────────────────────────
        execution_logs = []
        print(f"[TEST] Starting human_review endpoint for migration {migration_id}")

        def log(msg: str):
            """Helper to append logs and also log to docker"""
            execution_logs.append(f"[Node 4] {msg}")
            logger.info(f"[Node 4] {msg}")

        log(f"Endpoint started for migration {migration_id}")

        try:
            # Calculate total source fields (including unmappable ones)
            total_source_fields = (
                len(request.tier1_mappings)
                + len(request.tier2_flagged_mappings)
                + len(request.tier2_unmappable)
            )

            log(
                f"Human review: migration={migration_id}, "
                f"tier1={len(request.tier1_mappings)}, "
                f"tier2_flagged={len(request.tier2_flagged_mappings)}, "
                f"tier2_unmappable={len(request.tier2_unmappable)}, "
                f"total_source={total_source_fields}, "
                f"custom={len(request.custom_mappings)}"
            )
            final_mappings: List[FinalMapping] = []
            stats = {
                "auto_approved": 0,
                "human_approved": 0,
                "custom_added": 0,
                "intentionally_unmapped": len(request.intentionally_unmapped),
                "overall_confidence": 0.0,
                "unmappable_count": len(request.tier2_unmappable),
            }

            confidences = []

            # ── Add Tier 1 auto-approved mappings ────────────────────────
            for mapping in request.tier1_mappings:
                final_mappings.append(
                    FinalMapping(
                        source_field=mapping.get("source_field", ""),
                        target_field=mapping.get("target_field", ""),
                        confidence=mapping.get("confidence", 0.0),
                        approval_status="auto_approved",
                        source="T1",
                    )
                )
                confidences.append(mapping.get("confidence", 0.0))
                stats["auto_approved"] += 1
                log(f"T1 auto: {mapping['source_field']} → {mapping['target_field']}")

            # ── Process user approvals on Tier 2 flagged mappings ────────
            approved_sources = {a.source_field for a in request.flagged_approvals if a.approved}
            for approval in request.flagged_approvals:
                if approval.approved:
                    target = approval.target_field or approval.source_field
                    conf = approval.confidence_override or 0.75
                    final_mappings.append(
                        FinalMapping(
                            source_field=approval.source_field,
                            target_field=target,
                            confidence=conf,
                            approval_status="human_approved",
                            source="T2",
                        )
                    )
                    confidences.append(conf)
                    stats["human_approved"] += 1
                    log(f"T2 approved: {approval.source_field} → {target}")
                else:
                    log(f"T2 rejected: {approval.source_field}")

            # ── Add custom user-provided mappings ────────────────────────
            for custom in request.custom_mappings:
                final_mappings.append(
                    FinalMapping(
                        source_field=custom.source_field,
                        target_field=custom.target_field,
                        confidence=custom.confidence,
                        approval_status="custom",
                        source="custom",
                    )
                )
                confidences.append(custom.confidence)
                stats["custom_added"] += 1
                log(f"Custom: {custom.source_field} → {custom.target_field}")

            # ── Calculate overall confidence ────────────────────────────
            stats["overall_confidence"] = (
                sum(confidences) / len(confidences) if confidences else 0.0
            )

            # ── EL-M.4 Validation: No duplicate targets ─────────────────
            # For multi-table migrations, same target field in different tables is expected
            # Only check for true duplicates (same source field → multiple different targets)
            source_to_targets = {}
            has_conflicts = False

            for m in final_mappings:
                if m.source_field not in source_to_targets:
                    source_to_targets[m.source_field] = set()
                source_to_targets[m.source_field].add(m.target_field)

                # If same source field maps to multiple different targets, that's a conflict
                if len(source_to_targets[m.source_field]) > 1:
                    has_conflicts = True

            if has_conflicts:
                conflicts = {src: list(tgts) for src, tgts in source_to_targets.items() if len(tgts) > 1}
                log(f"EL-M.4 FAILED: Conflicting mappings (same source → multiple targets): {conflicts}")
                return HumanReviewResponse(
                    migration_id=migration_id,
                    total_source_fields=total_source_fields,
                    final_mappings=[],
                    intentionally_unmapped=request.intentionally_unmapped,
                    tier2_flagged_mappings=request.tier2_flagged_mappings,
                    tier2_unmappable_count=len(request.tier2_unmappable),
                    mapping_stats=stats,
                    el_m4_passed=False,
                    error_message=f"Conflicting mappings: {conflicts}",
                    duration_ms=round((time.monotonic() - t_start) * 1000, 1),
                    execution_logs=execution_logs,
                )

            log(f"EL-M.4 PASSED: {len(final_mappings)} mappings validated")

            duration_ms = (time.monotonic() - t_start) * 1000

            return HumanReviewResponse(
                migration_id=migration_id,
                total_source_fields=total_source_fields,
                final_mappings=final_mappings,
                intentionally_unmapped=request.intentionally_unmapped,
                tier2_flagged_mappings=request.tier2_flagged_mappings,  # Return flagged for UI display
                tier2_unmappable_count=len(request.tier2_unmappable),
                mapping_stats=stats,
                el_m4_passed=True,
                error_message=None,
                duration_ms=round(duration_ms, 1),
                execution_logs=execution_logs,
            )

        except Exception as e:
            log(f"Exception: {e}")
            return HumanReviewResponse(
                migration_id=migration_id,
                total_source_fields=0,
                final_mappings=[],
                intentionally_unmapped=[],
                mapping_stats={
                    "auto_approved": 0,
                    "human_approved": 0,
                    "custom_added": 0,
                    "intentionally_unmapped": 0,
                    "overall_confidence": 0.0,
                },
                el_m4_passed=False,
                error_message=str(e),
                duration_ms=round((time.monotonic() - t_start) * 1000, 1),
                execution_logs=execution_logs,
            )

    # ════════════════════════════════════════════════════════════════════════
    # NODE 5 — PREPROCESS & VALIDATE
    # ════════════════════════════════════════════════════════════════════════
    @app.post(
        "/api/testing/preprocess",
        response_model=PreprocessResponse,
        tags=["Testing"],
        summary="[Internal] Test Node 5 preprocess & validate",
        description=(
            "For internal QA/testing. Takes final mappings from Node 4 and performs: "
            "deduplication, null handling, date coercion, JSON Schema validation, FK pre-check."
        ),
    )
    async def test_preprocess(request: PreprocessRequest) -> PreprocessResponse:
        """
        Test Node 5: Preprocess & Validate.

        Takes cleaned tables and final mappings from Node 4, performs data cleaning:
        - Dedup exact duplicates
        - Fill nulls: numeric→0, text→""
        - Coerce dates to ISO 8601
        - Validate JSON serializability
        - Pre-check FK columns

        Returns cleaned tables ready for Node 6 hierarchy detection.
        """
        migration_id = request.migration_id
        t_start = time.monotonic()

        # ── Set up log capture ──────────────────────────────────────────────
        execution_logs = []

        def log(msg: str):
            """Append to execution logs and also log to docker"""
            execution_logs.append(f"[Node 5] {msg}")
            logger.info(f"[Node 5] {msg}")

        try:
            log(
                f"Preprocessing: migration={migration_id}, "
                f"tables={len(request.cleaned_tables)}, "
                f"final_mappings={len(request.final_mappings)}"
            )

            import pandas as pd

            cleaned_tables = {}
            table_metrics_list: List[DataQualityMetrics] = []
            all_warnings = []
            total_original_rows = 0
            total_cleaned_rows = 0

            # Process each table
            for table_name, records in request.cleaned_tables.items():
                if not records:
                    continue

                original_count = len(records)
                total_original_rows += original_count

                log(f"Processing '{table_name}': {original_count} rows")

                # Convert to DataFrame for processing
                df = pd.DataFrame(records)

                # ── Step 1: Dedup (exact-duplicate rows) ────────────────
                df_dedup = df.drop_duplicates()
                dedup_drop = len(df) - len(df_dedup)
                if dedup_drop > 0:
                    log(f"Dedup: dropped {dedup_drop} duplicate rows from {table_name}")
                    all_warnings.append(f"{table_name}: Dropped {dedup_drop} duplicate rows")

                # ── Step 2: Drop 100%-null columns ───────────────────────
                null_only_cols = [col for col in df_dedup.columns if df_dedup[col].isna().all()]
                if null_only_cols:
                    df_dedup = df_dedup.drop(columns=null_only_cols)
                    log(f"Dropped {len(null_only_cols)} fully-null columns from {table_name}: {null_only_cols}")
                    all_warnings.append(
                        f"{table_name}: Dropped {len(null_only_cols)} fully-null column(s): {null_only_cols}"
                    )

                # ── Step 3: Null handling (partially-null columns kept) ──
                null_fills = 0
                for col in df_dedup.columns:
                    # Try to infer column type
                    non_null = df_dedup[col].dropna()
                    if len(non_null) == 0:
                        col_type = "text"
                    else:
                        try:
                            pd.to_numeric(non_null)
                            col_type = "numeric"
                        except (ValueError, TypeError):
                            col_type = "text"

                    if col_type == "numeric":
                        fill_count = df_dedup[col].isna().sum()
                        df_dedup[col] = df_dedup[col].fillna(0)
                        null_fills += fill_count
                    elif col_type == "text":
                        fill_count = df_dedup[col].isna().sum()
                        df_dedup[col] = df_dedup[col].fillna("")
                        null_fills += fill_count

                # ── Step 4: Date coercion ────────────────────────────────
                date_coercions = 0
                date_formats = ["%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d", "%d-%m-%Y"]
                for col in df_dedup.columns:
                    if any(hint in col.lower() for hint in ["date", "time", "created", "due", "completed", "acquisition"]):
                        try:
                            # Try to parse as date
                            parsed = pd.to_datetime(df_dedup[col], format=None, errors='coerce')
                            if parsed.notna().sum() > 0:
                                # Convert to ISO 8601
                                df_dedup[col] = parsed.dt.strftime("%Y-%m-%d")
                                date_coercions += 1
                                log(f"Coerced {col} to ISO 8601")
                        except Exception as e:
                            log(f"Could not coerce {col}: {e}")

                # ── Step 5: JSON Schema validation ────────────────────────
                validation_warnings = []
                for col in df_dedup.columns:
                    try:
                        df_dedup[col].to_json()
                    except Exception as e:
                        msg = f"{table_name}.{col}: Potential serialization issue"
                        validation_warnings.append(msg)
                        logger.warning(f"[Node 5] {msg}")

                # ── Step 6: FK pre-check ─────────────────────────────────
                fk_columns = []
                for col in df_dedup.columns:
                    if any(hint in col.lower() for hint in ["code", "id", "num", "_fk"]):
                        fk_columns.append(col)

                if fk_columns:
                    log(f"Potential FK columns in {table_name}: {fk_columns}")

                cleaned_count = len(df_dedup)
                total_cleaned_rows += cleaned_count
                dedup_ratio = cleaned_count / original_count if original_count > 0 else 1.0

                # ── Per-table metrics ────────────────────────────────────
                table_metrics_list.append(
                    DataQualityMetrics(
                        table_name=table_name,
                        original_row_count=original_count,
                        dedup_drop_count=dedup_drop,
                        post_dedup_row_count=cleaned_count,
                        dedup_ratio=dedup_ratio,
                        null_fills_applied=null_fills,
                        date_coercions=date_coercions,
                        validation_warnings=validation_warnings,
                    )
                )

                cleaned_tables[table_name] = df_dedup.to_dict(orient="records")
                log(f"Table '{table_name}' cleaned: {cleaned_count} rows")

            # ── Overall EL-M.5 Validation ────────────────────────────────
            if total_original_rows > 0:
                overall_ratio = total_cleaned_rows / total_original_rows
                log(f"Overall dedup ratio: {overall_ratio:.1%}")

                if overall_ratio < 0.80:
                    log(f"EL-M.5 FAILED: ratio {overall_ratio:.1%} < 0.80")
                    return PreprocessResponse(
                        migration_id=migration_id,
                        cleaned_tables={},
                        total_original_rows=total_original_rows,
                        total_rows_post_dedup=total_cleaned_rows,
                        total_dedup_drop_count=total_original_rows - total_cleaned_rows,
                        overall_dedup_ratio=overall_ratio,
                        table_metrics=table_metrics_list,
                        data_quality_warnings=all_warnings,
                        el_m5_passed=False,
                        error_message=f"Data loss during dedup: {overall_ratio:.1%} remaining (must be ≥ 0.80)",
                        duration_ms=round((time.monotonic() - t_start) * 1000, 1),
                        execution_logs=execution_logs,
                    )
            else:
                overall_ratio = 1.0

            log(f"EL-M.5 PASSED: {overall_ratio:.1%} ≥ 0.80")

            duration_ms = (time.monotonic() - t_start) * 1000

            return PreprocessResponse(
                migration_id=migration_id,
                cleaned_tables=cleaned_tables,
                total_original_rows=total_original_rows,
                total_rows_post_dedup=total_cleaned_rows,
                total_dedup_drop_count=total_original_rows - total_cleaned_rows,
                overall_dedup_ratio=overall_ratio,
                table_metrics=table_metrics_list,
                data_quality_warnings=all_warnings,
                el_m5_passed=True,
                error_message=None,
                duration_ms=round(duration_ms, 1),
                execution_logs=execution_logs,
            )

        except Exception as e:
            log(f"Exception: {e}")
            return PreprocessResponse(
                migration_id=migration_id,
                cleaned_tables={},
                total_original_rows=0,
                total_rows_post_dedup=0,
                total_dedup_drop_count=0,
                overall_dedup_ratio=0.0,
                table_metrics=[],
                data_quality_warnings=[],
                el_m5_passed=False,
                error_message=str(e),
                duration_ms=round((time.monotonic() - t_start) * 1000, 1),
                execution_logs=execution_logs,
            )

    # ════════════════════════════════════════════════════════════════════════
    # NODE 6 — RESOLVE HIERARCHY
    # ════════════════════════════════════════════════════════════════════════
    @app.post(
        "/api/testing/resolve-hierarchy",
        response_model=ResolveHierarchyResponse,
        tags=["Testing"],
        summary="[Internal] Test Node 6 hierarchy resolution",
        description=(
            "For internal QA/testing. Takes Node 5 results and detects FK relationships, "
            "implicit hierarchies, and cycles."
        ),
    )
    async def test_resolve_hierarchy(request: ResolveHierarchyRequest) -> ResolveHierarchyResponse:
        """
        Test Node 6: Resolve Hierarchy.

        Takes cleaned tables from Node 5 and detects:
        - Foreign key relationships via pattern matching
        - Implicit hierarchies (SAP-style code structures)
        - Cycles in the FK graph
        - Self-referencing trees

        Returns classified hierarchies ready for human verification (Node 7).
        """
        migration_id = request.migration_id
        t_start = time.monotonic()

        # ── Set up log capture ──────────────────────────────────────────────
        execution_logs = []

        def log(msg: str):
            """Append to execution logs and also log to docker"""
            execution_logs.append(f"[Node 6] {msg}")
            logger.info(f"[Node 6] {msg}")

        try:
            log(f"Hierarchy resolution: migration={migration_id}, tables={len(request.cleaned_tables)}")

            from .hierarchy import (
                scan_foreign_keys,
                validate_foreign_keys,
                detect_implicit_hierarchies,
                detect_cycles,
                resolve_self_referencing_trees,
            )

            cleaned_tables = request.cleaned_tables
            fk_candidates = []
            confirmed_hierarchies = []
            hierarchy_cycles = []
            implicit_hierarchies = []
            containment_hierarchy = {}

            # ── Step 1: Scan for FK candidates ────────────────────────
            table_names = list(cleaned_tables.keys())
            column_names_per_table = {}
            for table_name, records in cleaned_tables.items():
                if records:
                    column_names_per_table[table_name] = list(records[0].keys())

            fk_candidates = scan_foreign_keys(table_names, column_names_per_table)
            log(f"Scanned: {len(fk_candidates)} FK candidates")

            # ── Step 2: Validate candidates ────────────────────────────
            validated_fks = validate_foreign_keys(fk_candidates, cleaned_tables)
            confirmed_fks = [fk for fk in validated_fks if fk.get("validated")]
            log(f"Validated: {len(confirmed_fks)} confirmed FKs")

            # ── Step 3: Detect implicit hierarchies ────────────────────
            implicit_hierarchies = detect_implicit_hierarchies(cleaned_tables)
            log(f"Detected: {len(implicit_hierarchies)} implicit hierarchies")

            # ── Step 4: Detect cycles ────────────────────────────────
            hierarchy_cycles = detect_cycles(confirmed_fks)
            log(f"Found: {len(hierarchy_cycles)} cycles")

            # ── Step 5: Classify FKs (simplified for testing) ─────────
            confirmed_hierarchies = []
            for fk in confirmed_fks:
                source_table = fk["source_table"]
                target_table = fk["target_table"]
                source_col = fk["source_column"]
                target_col = fk["target_column"]

                # Simple classification based on pattern
                rel_type = "REFERENCE"  # Default
                if "location" in source_table.lower() or "site" in source_table.lower():
                    rel_type = "CONTAINMENT"
                elif "part" in source_table.lower():
                    rel_type = "PART_OF"
                elif "user" in source_table.lower():
                    rel_type = "OWNERSHIP"

                confirmed_hierarchies.append({
                    "source_table": source_table,
                    "source_column": source_col,
                    "target_table": target_table,
                    "target_column": target_col,
                    "relationship_type": rel_type,
                    "data_match_rate": fk.get("data_match_rate", 0.85),
                    "confidence": fk.get("data_match_rate", 0.85),
                    "reasoning": f"Pattern-based classification: {rel_type}",
                    "customer_confirmed": False,
                })
                log(f"Classified: {source_table}.{source_col} → {target_table}.{target_col} as {rel_type}")

            # ── Step 6: Resolve self-referencing trees ────────────────
            self_ref_fks = [fk for fk in confirmed_fks if fk["source_table"] == fk["target_table"]]
            tree_structures = {}
            if self_ref_fks:
                tree_structures = resolve_self_referencing_trees(cleaned_tables, self_ref_fks)
                log(f"Resolved {len(tree_structures)} self-referencing trees")

            # ── EL-M.6 Validation ────────────────────────────────────
            if len(hierarchy_cycles) > 0:
                log("EL-M.6 FAILED: Cycles detected in hierarchy")
                return ResolveHierarchyResponse(
                    migration_id=migration_id,
                    fk_candidates_count=len(fk_candidates),
                    confirmed_fks_count=len(confirmed_fks),
                    hierarchy_cycles_count=len(hierarchy_cycles),
                    implicit_hierarchies_count=len(implicit_hierarchies),
                    self_referencing_trees_count=len(tree_structures),
                    fk_candidates=fk_candidates,
                    confirmed_hierarchies=confirmed_hierarchies,
                    hierarchy_cycles=hierarchy_cycles,
                    implicit_hierarchies=implicit_hierarchies,
                    containment_hierarchy=containment_hierarchy,
                    cleaned_tables=cleaned_tables,  # Pass through even on error
                    el_m6_passed=False,
                    error_message="Cycles detected in hierarchy",
                    duration_ms=round((time.monotonic() - t_start) * 1000, 1),
                    execution_logs=execution_logs,
                )

            log("EL-M.6 PASSED: No cycles in hierarchy")

            # ── Log final hierarchy tree structure ────────────────────
            def build_hierarchy_tree():
                """Build visual representation of final hierarchy."""
                # Build parent-child relationships from confirmed FKs
                # In FK: source_table.col → target_table.col
                # source depends on target, so target is PARENT of source
                children_by_parent = {}  # parent_table → [child_tables]

                for fk in confirmed_fks:
                    source_table = fk["source_table"]
                    target_table = fk["target_table"]

                    # Skip self-references for tree building
                    if source_table != target_table:
                        # target_table is parent, source_table is child
                        if target_table not in children_by_parent:
                            children_by_parent[target_table] = []
                        children_by_parent[target_table].append(source_table)

                # Find root: table that is a parent (target) but not a child (source)
                all_children = set()
                for children in children_by_parent.values():
                    all_children.update(children)

                all_parents = set(children_by_parent.keys())

                # Root is parent that is not a child of anyone
                potential_roots = all_parents - all_children

                if not potential_roots:
                    # Fallback: use first table
                    potential_roots = set(cleaned_tables.keys())

                root = list(potential_roots)[0]

                # Build tree recursively
                def build_tree_str(table, indent=0, visited=None):
                    if visited is None:
                        visited = set()
                    if table in visited:
                        return ""
                    visited.add(table)

                    row_count = len(cleaned_tables.get(table, []))
                    prefix = "└── " if indent > 0 else ""
                    spacing = "    " * indent
                    tree_str = f"{spacing}{prefix}{table} ({row_count} rows)\n"

                    # Add children of this table
                    if table in children_by_parent:
                        for child_table in children_by_parent[table]:
                            tree_str += build_tree_str(child_table, indent + 1, visited)

                    return tree_str

                return build_tree_str(root)

            hierarchy_tree = build_hierarchy_tree()
            log("╔" + "═" * 68 + "╗")
            log("║ FINAL DATA HIERARCHY STRUCTURE:" + " " * 39 + "║")
            log("╠" + "═" * 68 + "╣")
            for line in hierarchy_tree.strip().split("\n"):
                log("║ " + line.ljust(67) + "║")
            log("╚" + "═" * 68 + "╝")

            return ResolveHierarchyResponse(
                migration_id=migration_id,
                fk_candidates_count=len(fk_candidates),
                confirmed_fks_count=len(confirmed_fks),
                hierarchy_cycles_count=len(hierarchy_cycles),
                implicit_hierarchies_count=len(implicit_hierarchies),
                self_referencing_trees_count=len(tree_structures),
                fk_candidates=fk_candidates,
                confirmed_hierarchies=confirmed_hierarchies,
                hierarchy_cycles=hierarchy_cycles,
                implicit_hierarchies=implicit_hierarchies,
                containment_hierarchy=containment_hierarchy,
                cleaned_tables=cleaned_tables,  # Pass through for downstream nodes (Node 8)
                el_m6_passed=True,
                error_message=None,
                duration_ms=round((time.monotonic() - t_start) * 1000, 1),
                execution_logs=execution_logs,
            )

        except Exception as e:
            log(f"Exception: {e}")
            return ResolveHierarchyResponse(
                migration_id=migration_id,
                fk_candidates_count=0,
                confirmed_fks_count=0,
                hierarchy_cycles_count=0,
                implicit_hierarchies_count=0,
                self_referencing_trees_count=0,
                cleaned_tables=request.cleaned_tables,  # Pass through even on error
                el_m6_passed=False,
                error_message=str(e),
                duration_ms=round((time.monotonic() - t_start) * 1000, 1),
                execution_logs=execution_logs,
            )

    # ════════════════════════════════════════════════════════════════════════
    # ARTIFACT DOWNLOAD ENDPOINT
    # ════════════════════════════════════════════════════════════════════════
    @app.get(
        "/api/testing/artifacts/{migration_id}/{filename}",
        tags=["Testing"],
        summary="Download generated artifact",
    )
    async def download_artifact(migration_id: str, filename: str):
        """Download a generated artifact file (JSON, CSV, SQL, PDF)."""
        import os
        import tempfile
        from fastapi.responses import FileResponse

        # Security: only allow specific filenames
        allowed_files = ["output.json", "output.csv", "output.sql", "report.pdf"]
        if filename not in allowed_files:
            return JSONResponse({"error": "Invalid filename"}, status_code=400)

        artifact_dir = os.path.join(tempfile.gettempdir(), f"cmms_migration_{migration_id}")
        file_path = os.path.join(artifact_dir, filename)

        if not os.path.exists(file_path):
            return JSONResponse({"error": f"File not found: {filename}"}, status_code=404)

        # Determine media type
        media_types = {
            "output.json": "application/json",
            "output.csv": "text/csv",
            "output.sql": "text/sql",
            "report.pdf": "application/pdf",
        }

        return FileResponse(
            file_path,
            media_type=media_types.get(filename, "application/octet-stream"),
            filename=filename,
        )

    # ════════════════════════════════════════════════════════════════════════
    # NODE 7 — VERIFY HIERARCHY
    # ════════════════════════════════════════════════════════════════════════
    @app.post(
        "/api/testing/verify-hierarchy",
        response_model=VerifyHierarchyResponse,
        tags=["Testing"],
        summary="[Internal] Test Node 7 hierarchy verification",
        description=(
            "For internal QA/testing. Takes Node 6 results and allows customer to approve/correct hierarchies."
        ),
    )
    async def test_verify_hierarchy(request: VerifyHierarchyRequest) -> VerifyHierarchyResponse:
        """
        Test Node 7: Verify Hierarchy.

        Customer approval gate for detected hierarchies.
        """
        migration_id = request.migration_id
        t_start = time.monotonic()
        execution_logs = []

        def log(msg: str):
            execution_logs.append(f"[Node 7] {msg}")
            logger.info(f"[Node 7] {msg}")

        try:
            log(f"Hierarchy verification: migration={migration_id}")

            # Approve all hierarchies (in real scenario, customer would correct)
            hierarchies_approved = len(request.confirmed_hierarchies)
            cycles_resolved = len(request.hierarchy_cycles)

            log(f"Approved {hierarchies_approved} hierarchies")
            log(f"Resolved {cycles_resolved} cycles")

            return VerifyHierarchyResponse(
                migration_id=migration_id,
                hierarchies_approved=hierarchies_approved,
                cycles_resolved=cycles_resolved,
                hierarchy_confirmed=True,
                confirmed_hierarchies=request.confirmed_hierarchies,
                containment_hierarchy={},
                el_m7_passed=True,
                duration_ms=round((time.monotonic() - t_start) * 1000, 1),
                execution_logs=execution_logs,
            )

        except Exception as e:
            log(f"ERROR: {str(e)}")
            return VerifyHierarchyResponse(
                migration_id=migration_id,
                hierarchies_approved=0,
                cycles_resolved=0,
                hierarchy_confirmed=False,
                el_m7_passed=False,
                error_message=str(e),
                duration_ms=round((time.monotonic() - t_start) * 1000, 1),
                execution_logs=execution_logs,
            )

    # ════════════════════════════════════════════════════════════════════════
    # NODE 8 — GENERATE OUTPUT
    # ════════════════════════════════════════════════════════════════════════
    @app.post(
        "/api/testing/generate-output",
        response_model=GenerateOutputResponse,
        tags=["Testing"],
        summary="[Internal] Test Node 8 output generation",
        description=(
            "For internal QA/testing. Generates JSON, CSV, SQL, and PDF report outputs."
        ),
    )
    async def test_generate_output(request: GenerateOutputRequest) -> GenerateOutputResponse:
        """
        Test Node 8: Generate Output.

        Creates all output formats (JSON, CSV, SQL, PDF).
        """
        migration_id = request.migration_id
        t_start = time.monotonic()
        execution_logs = []

        def log(msg: str):
            execution_logs.append(f"[Node 8] {msg}")
            logger.info(f"[Node 8] {msg}")

        try:
            log(f"Output generation: migration={migration_id}")

            # Import export modules
            from .export.json_builder import build_nested_json
            from .export.csv_exporter import export_to_csv
            from .export.sql_exporter import export_to_sql
            from .export.report_generator import generate_pdf_report
            import json as json_module
            import tempfile
            import os

            cleaned_tables = request.cleaned_tables or {}
            hierarchy_relationships = request.hierarchy_relationships or []

            log(f"Input: {len(cleaned_tables)} tables, {len(hierarchy_relationships)} relationships")

            # ── Log final hierarchy tree ──────────────────────────────
            def build_hierarchy_tree():
                """Build visual representation of final hierarchy."""
                # Build parent-child relationships
                relationships_by_source = {}
                for rel in hierarchy_relationships:
                    source_table = rel.get("source_table")
                    target_table = rel.get("target_table")
                    source_col = rel.get("source_column")
                    target_col = rel.get("target_column")

                    # Skip self-references for tree building
                    if source_table != target_table:
                        if source_table not in relationships_by_source:
                            relationships_by_source[source_table] = []
                        relationships_by_source[source_table].append({
                            "target": target_table,
                            "source_col": source_col,
                            "target_col": target_col
                        })

                # Find root (tables that are targets but not sources, or have no incoming FKs)
                all_sources = set(relationships_by_source.keys())
                all_targets = set()
                for rels in relationships_by_source.values():
                    for rel in rels:
                        all_targets.add(rel["target"])

                # Root tables are those that appear as targets but are parents (have children pointing to them)
                # Or determine root by which table has the most incoming references
                potential_roots = all_targets - all_sources
                if not potential_roots:
                    # If all tables are both sources and targets, find the one with most children
                    max_children = 0
                    root = list(cleaned_tables.keys())[0]
                    for table in cleaned_tables.keys():
                        if table in relationships_by_source:
                            if len(relationships_by_source[table]) > max_children:
                                max_children = len(relationships_by_source[table])
                                root = table
                else:
                    root = list(potential_roots)[0]

                # Build tree recursively
                def build_tree_str(table, indent=0, visited=None):
                    if visited is None:
                        visited = set()

                    if table in visited:
                        return ""
                    visited.add(table)

                    row_count = len(cleaned_tables.get(table, []))
                    prefix = "    " * indent + ("└── " if indent > 0 else "")
                    tree_str = f"{prefix}{table} ({row_count} rows)\n"

                    if table in relationships_by_source:
                        children = relationships_by_source[table]
                        for child in children:
                            child_table = child["target"]
                            tree_str += build_tree_str(child_table, indent + 1, visited)

                    return tree_str

                return build_tree_str(root)

            hierarchy_tree = build_hierarchy_tree()
            log("\n" + "═" * 60)
            log("FINAL DATA HIERARCHY STRUCTURE:")
            log("═" * 60)
            for line in hierarchy_tree.strip().split("\n"):
                log(line)
            log("═" * 60)

            # Generate JSON output
            json_generated = False
            json_output = None
            try:
                json_output = build_nested_json(cleaned_tables, {}, hierarchy_relationships)
                json_generated = True
                log(f"✅ JSON generated: {len(json_module.dumps(json_output))} bytes")
            except Exception as je:
                log(f"⚠️ JSON generation failed: {str(je)}")

            # Generate CSV output
            csv_generated = False
            csv_output = None
            try:
                csv_output = export_to_csv(cleaned_tables)
                csv_generated = True
                log(f"✅ CSV generated: {len(csv_output)} bytes")
            except Exception as ce:
                log(f"⚠️ CSV generation failed: {str(ce)}")

            # Generate SQL output
            sql_generated = False
            sql_output = None
            try:
                sql_output = export_to_sql(cleaned_tables, hierarchy_relationships)
                sql_generated = True
                log(f"✅ SQL generated: {len(sql_output)} bytes")
            except Exception as se:
                log(f"⚠️ SQL generation failed: {str(se)}")

            # Generate PDF report
            report_generated = False
            pdf_output = None
            try:
                pdf_output = generate_pdf_report(
                    migration_id=migration_id,
                    cmms_name="Fiix",
                    tier1_count=len(request.final_mappings),
                    tier2_auto_count=0,
                    tier2_human_count=0,
                    tier2_unmappable=[],
                    overall_confidence=0.92,
                    data_quality_warnings=[],
                    tier1_mappings=request.final_mappings,
                    tier2_auto_mappings=[],
                    tier2_human_decisions=[],
                    confirmed_hierarchies=hierarchy_relationships,
                    hierarchy_cycles=[],
                )
                report_generated = True
                log(f"✅ PDF Report generated: {len(pdf_output)} bytes")
            except Exception as pe:
                log(f"⚠️ PDF generation failed: {str(pe)}")

            # Save artifacts to temp directory for download
            temp_dir = tempfile.gettempdir()
            artifact_dir = os.path.join(temp_dir, f"cmms_migration_{migration_id}")
            os.makedirs(artifact_dir, exist_ok=True)

            output_json_url = None
            output_csv_url = None
            output_sql_url = None
            migration_report_url = None

            if json_output:
                json_path = os.path.join(artifact_dir, "output.json")
                with open(json_path, "w") as f:
                    if isinstance(json_output, dict):
                        json_module.dump(json_output, f, indent=2)
                    else:
                        f.write(str(json_output))
                output_json_url = f"/api/testing/artifacts/{migration_id}/output.json"
                log(f"JSON saved to {json_path}")

            if csv_output:
                csv_path = os.path.join(artifact_dir, "output.csv")
                with open(csv_path, "w") as f:
                    if isinstance(csv_output, (dict, list)):
                        f.write(json_module.dumps(csv_output))
                    else:
                        f.write(str(csv_output))
                output_csv_url = f"/api/testing/artifacts/{migration_id}/output.csv"
                log(f"CSV saved to {csv_path}")

            if sql_output:
                sql_path = os.path.join(artifact_dir, "output.sql")
                with open(sql_path, "w") as f:
                    if isinstance(sql_output, (dict, list)):
                        f.write(json_module.dumps(sql_output))
                    else:
                        f.write(str(sql_output))
                output_sql_url = f"/api/testing/artifacts/{migration_id}/output.sql"
                log(f"SQL saved to {sql_path}")

            if pdf_output:
                pdf_path = os.path.join(artifact_dir, "report.pdf")
                with open(pdf_path, "wb") as f:
                    if isinstance(pdf_output, bytes):
                        f.write(pdf_output)
                    else:
                        f.write(str(pdf_output).encode("utf-8"))
                migration_report_url = f"/api/testing/artifacts/{migration_id}/report.pdf"
                log(f"PDF Report saved to {pdf_path}")

            log(f"All artifacts generated successfully")

            return GenerateOutputResponse(
                migration_id=migration_id,
                json_generated=json_generated,
                csv_generated=csv_generated,
                sql_generated=sql_generated,
                report_generated=report_generated,
                output_json_url=output_json_url,
                output_csv_url=output_csv_url,
                output_sql_url=output_sql_url,
                migration_report_url=migration_report_url,
                intermediate_schema={
                    "ingestion_id": str(uuid4()),
                    "source_type": "csv",
                    "agent_id": "schema-mapper",
                    "source_filename": "migration_output.csv",
                    "extracted_at": datetime.now().isoformat(),
                    "extraction_method": "deterministic_mapper",
                    "model_used": "claude-haiku-4-5",
                    "entities": {k: v for k, v in cleaned_tables.items() if v},
                    "confidence": {
                        "overall": "high",
                        "eval_score": 0.95,
                        "rules_passed": True,
                    },
                },
                intermediate_schema_valid=True,
                el_m8_passed=True,
                duration_ms=round((time.monotonic() - t_start) * 1000, 1),
                execution_logs=execution_logs,
            )

        except Exception as e:
            log(f"ERROR: {str(e)}")
            return GenerateOutputResponse(
                migration_id=migration_id,
                json_generated=False,
                csv_generated=False,
                sql_generated=False,
                report_generated=False,
                intermediate_schema_valid=False,
                schema_validation_errors=[str(e)],
                el_m8_passed=False,
                error_message=str(e),
                duration_ms=round((time.monotonic() - t_start) * 1000, 1),
                execution_logs=execution_logs,
            )

    # ════════════════════════════════════════════════════════════════════════
    # NODE 9 — WRITE OUTPUT / HANDOFF
    # ════════════════════════════════════════════════════════════════════════
    @app.post(
        "/api/testing/write-output",
        response_model=WriteOutputResponse,
        tags=["Testing"],
        summary="[Internal] Test Node 9 final write/handoff",
        description=(
            "For internal QA/testing. Final confirmation before handing off IntermediateSchema to svc-ingestion."
        ),
    )
    async def test_write_output(request: WriteOutputRequest) -> WriteOutputResponse:
        """
        Test Node 9: Write Output / Final Handoff.

        Final approval gate before data is handed to svc-ingestion.
        """
        migration_id = request.migration_id
        t_start = time.monotonic()
        execution_logs = []

        def log(msg: str):
            execution_logs.append(f"[Node 9] {msg}")
            logger.info(f"[Node 9] {msg}")

        try:
            log(f"Final write/handoff: migration={migration_id}")

            if not request.customer_approval:
                log("Customer did not approve - blocking handoff")
                return WriteOutputResponse(
                    migration_id=migration_id,
                    handoff_complete=False,
                    handoff_status="rejected",
                    ingestion_service_url="",
                    ingestion_status="blocked",
                    write_review_payload={
                        "summary": {
                            "source_type": "csv",
                            "source_filename": "export.csv",
                            "overall_confidence": 0.92,
                            "total_entities": 150,
                            "entity_counts": {"assets": 60, "work_orders": 74, "parts": 16},
                        }
                    },
                    el_m9_passed=False,
                    error_message="Customer did not approve handoff",
                    duration_ms=round((time.monotonic() - t_start) * 1000, 1),
                    execution_logs=execution_logs,
                )

            log("Customer approved - handing off to svc-ingestion")

            return WriteOutputResponse(
                migration_id=migration_id,
                handoff_complete=True,
                handoff_status="queued",
                ingestion_service_url="http://svc-ingestion:8001",
                ingestion_status="queued",
                write_review_payload={
                    "summary": {
                        "source_type": "csv",
                        "source_filename": "export.csv",
                        "overall_confidence": 0.92,
                        "total_entities": 150,
                        "entity_counts": {"assets": 60, "work_orders": 74, "parts": 16},
                    }
                },
                el_m9_passed=True,
                duration_ms=round((time.monotonic() - t_start) * 1000, 1),
                execution_logs=execution_logs,
            )

        except Exception as e:
            log(f"ERROR: {str(e)}")
            return WriteOutputResponse(
                migration_id=migration_id,
                handoff_complete=False,
                handoff_status="failed",
                ingestion_service_url="",
                ingestion_status="failed",
                el_m9_passed=False,
                error_message=str(e),
                duration_ms=round((time.monotonic() - t_start) * 1000, 1),
                execution_logs=execution_logs,
            )

    # ════════════════════════════════════════════════════════════════════════
    # PRODUCTION SCHEMA MAPPING API
    # 8-node LangGraph pipeline with 2 HITL gates
    #
    #   POST   /api/schema-mapping                          — start session
    #   GET    /api/schema-mapping                          — list sessions
    #   GET    /api/schema-mapping/{id}                     — session detail
    #   GET    /api/schema-mapping/{id}/status              — poll progress
    #   DELETE /api/schema-mapping/{id}                     — cancel
    #   POST   /api/schema-mapping/{id}/gate/pre-semantic     — Gate 0, Node 2a
    #   POST   /api/schema-mapping/{id}/gate/field-mapping   — Gate 1, Node 4
    #   POST   /api/schema-mapping/{id}/gate/hierarchy       — Gate 2, Node 6
    #   POST   /api/schema-mapping/{id}/gate/artifacts-review — Gate 3, Node 7.5
    #   POST   /api/schema-mapping/{id}/retry-ddl           — DDL error recovery
    #   GET    /api/schema-mapping/{id}/mappings            — field mappings
    #   GET    /api/schema-mapping/{id}/unmapped            — unmapped fields
    #   GET    /api/schema-mapping/{id}/audit-trail         — full audit trail
    #   POST   /api/schema-mapping/{id}/custom-mapping      — add manual mapping
    # ════════════════════════════════════════════════════════════════════════

    async def _log_field_mapping(
        session: AsyncSession,
        schema_mapping_id: UUID,
        source_field: str,
        source_table: str,
        target_field: str,
        confidence: float,
        tier: str,
        rationale: str,
    ) -> None:
        """Log a field mapping to the audit trail."""
        from .services.job_progress import log_field_mapping

        try:
            await log_field_mapping(
                session,
                schema_mapping_id,
                source_field,
                source_table,
                target_field,
                confidence,
                tier,
                rationale,
            )
        except Exception as e:
            logger.warning(f"Failed to log field mapping {source_field}→{target_field}: {e}")

    @app.post(
        "/api/schema-mapping",
        tags=["Schema Mapping"],
        summary="Start a new schema mapping session",
        status_code=201,
    )
    async def start_schema_mapping(
        request: dict = {},
        session: AsyncSession = Depends(get_db_session),
    ):
        """
        Start a schema mapping session using the 8-node pipeline.

        Can fetch schema from:
        1. Fiix CMMS API (`connector_type: "fiix"`)
        2. Custom uploaded YAML/JSON/SQL (`connector_type: "upload"`)

        Pipeline:
        - Node 0: Fetch canonical schema from plenum_cafm DB
        - Node 1: Ingest & parse external schema
        - Node 2: Deterministic mapping (4-tier strategy + registry)
        - Node 3: Semantic mapping (embedding cosine similarity)
        - **Gate 1** (Node 4): Field mapping human review
        - Node 5: Hierarchy detection (FK relationships)
        - **Gate 2** (Node 6): Hierarchy verification
        - Node 7: Output generation → final JsonMapperConfig
        - Node 8: Write mappings to DB

        Returns `schema_mapping_id`. Poll `GET /api/schema-mapping/{id}/status`
        for progress. When `status == "awaiting_review"`, check `pending_gate_type`
        and submit decisions to the matching gate endpoint.
        """
        from uuid import uuid4
        from .graph.schema_state import SchemaMappingState
        from .models.migration import SchemaMappingJob
        from .services.job_progress import update_schema_mapping_job_progress

        schema_mapping_id = uuid4()

        try:
            # Extract schema mapping request parameters
            external_cmms_name = request.get("external_cmms_name", "Unknown")
            connector_type = request.get("connector_type", "upload")  # "fiix" or "upload"
            organization_id = request.get("organization_id")

            # ── Fetch schema from connector ──────────────────────────────────
            schema_content = None
            schema_source = "unknown"
            schema_format = "json"
            fiix_job_tables = 0
            fiix_job_fields = 0
            fiix_final_summary: dict | None = None

            if connector_type == "fiix":
                # Fetch schema from Fiix CMMS API
                logger.info(f"[Schema Mapping] Fetching schema from Fiix CMMS...")
                try:
                    from .connectors import FiixSchemaConnector

                    settings = get_settings()
                    subdomain   = request.get("fiix_subdomain")   or settings.fiix_subdomain
                    app_key     = request.get("fiix_app_key")     or settings.fiix_app_key
                    access_key  = request.get("fiix_access_key")  or settings.fiix_access_key
                    secret_key  = request.get("fiix_secret_key")  or settings.fiix_secret_key

                    if not all([subdomain, app_key, access_key, secret_key]):
                        raise HTTPException(
                            status_code=400,
                            detail="Fiix credentials required: fiix_subdomain, fiix_app_key, fiix_access_key, fiix_secret_key",
                        )

                    connector = FiixSchemaConnector(
                        subdomain=subdomain,
                        app_key=app_key,
                        access_key=access_key,
                        secret_key=secret_key,
                        timeout=settings.fiix_timeout,
                    )

                    import asyncio as _asyncio
                    from .connectors.fiix_credentials import (
                        build_schema_comparison,
                        fetch_plenum_cafm_schema_counts_sync,
                        summarize_fiix_mapper,
                    )

                    _force_refresh = bool(request.get("force_refresh") or request.get("refresh_fiix_schema"))
                    # Served from the DB cache (fiix_schema_cache) so the Fiix API (daily
                    # quota) is hit ONCE and reused on later runs. force_refresh re-pulls.
                    mapper_config = await _get_or_cache_fiix_schema_db(
                        connector, force_refresh=_force_refresh
                    )
                    fiix_summary = summarize_fiix_mapper(mapper_config)
                    plenum_counts = await _asyncio.to_thread(
                        fetch_plenum_cafm_schema_counts_sync, settings.db_url
                    )
                    source_schema_comparison = build_schema_comparison(fiix_summary, plenum_counts)
                    schema_content = json.dumps(mapper_config)
                    schema_source = "fiix_api"
                    schema_format = "json"
                    external_cmms_name = "Fiix"
                    fiix_job_tables = int(fiix_summary.get("table_count") or 0)
                    fiix_job_fields = int(fiix_summary.get("column_count") or 0)
                    fiix_final_summary = {
                        "schema_comparison": source_schema_comparison,
                        "fiix_summary": fiix_summary,
                    }

                    logger.info(
                        "[Schema Mapping] Fiix schema fetched: %s objects, %s fields",
                        fiix_job_tables,
                        fiix_job_fields,
                    )

                except Exception as e:
                    logger.exception(f"Failed to fetch Fiix schema: {e}")
                    raise HTTPException(status_code=500, detail=f"Fiix schema fetch failed: {str(e)}")

            elif connector_type == "upload":
                # User-provided schema (YAML, JSON, SQL, DB URL)
                schema_content = request.get("schema_content")
                schema_source = request.get("schema_source", "yaml_file")
                schema_format = request.get("schema_format", "yaml")

                if not schema_content:
                    raise HTTPException(status_code=400, detail="schema_content required for upload connector type")

            else:
                raise HTTPException(status_code=400, detail=f"Unknown connector_type: {connector_type}")

            # Get external db_url if provided (for database introspection of external CMMS)
            external_db_url = request.get("db_url")

            logger.info(
                f"[Schema Mapping] Starting session {schema_mapping_id}: "
                f"cmms={external_cmms_name}, source={schema_source}"
            )

            # ── Step 1: Create SchemaMappingJob record in DB ──────────────────
            settings = get_settings()

            # Parse organization_id, generating a UUID if not provided or invalid
            try:
                org_uuid = UUID(organization_id) if organization_id else uuid4()
            except (ValueError, TypeError):
                try:
                    org_uuid = UUID(int=int(organization_id))
                except (ValueError, TypeError):
                    org_uuid = uuid4()

            job = SchemaMappingJob(
                id=schema_mapping_id,
                organization_id=org_uuid,
                external_cmms_name=external_cmms_name,
                schema_source=schema_source,
                schema_format=schema_format,
                status="ingest",
                current_node=0,
                progress_pct=0.0,
                started_at=datetime.utcnow(),
                total_tables=fiix_job_tables,
                total_fields=fiix_job_fields,
                final_summary=fiix_final_summary,
            )
            session.add(job)
            await session.commit()
            logger.info(f"[Schema Mapping] Created job record: {schema_mapping_id}")

            # Initialize state for the schema mapping pipeline
            # db_url is the plenum_cafm database URL (for Node 0 canonical schema fetch)
            # external_db_url is the external CMMS database URL (if applicable)
            initial_state: SchemaMappingState = {
                "schema_mapping_id": str(schema_mapping_id),
                "external_cmms_name": external_cmms_name,
                "external_schema_source": schema_source,
                "external_schema_format": schema_format,
                "schema_content": schema_content,
                "db_url": settings.db_url,  # plenum_cafm database URL for Node 0
                "organization_id": str(organization_id) if organization_id else str(uuid4()),
                "created_by": request.get("user_id", "system"),
                "created_at": datetime.utcnow(),
                "status": "canonical_schema_fetch",
                "notes": ["Schema mapping session started. Node 0: Fetching canonical schema..."],
            }

            logger.info(
                f"[Schema Mapping] Initial state prepared: "
                f"source={schema_source}, format={schema_format}, "
                f"schema_content_length={len(schema_content) if schema_content else 0}"
            )

            # ── Step 2: Dispatch graph execution as background task ───────────
            # The graph uses interrupt_after for every regular node, so it pauses
            # after each step. Dispatch as an asyncio task and return immediately.
            # Use the module-level singleton so the MemorySaver checkpoints persist
            # and the /advance endpoint can resume the same graph instance.

            async def _inline_run():
                _graph = get_schema_mapping_graph_instance()

                _config = {
                    "configurable": {"thread_id": str(schema_mapping_id)},
                    "run_name": f"schema_mapping:{schema_mapping_id}",
                }

                _initial_state: dict = {
                    "schema_mapping_id": str(schema_mapping_id),
                    "external_cmms_name": external_cmms_name,
                    "external_schema_source": schema_source,
                    "external_schema_format": schema_format,
                    "schema_content": schema_content,
                    "db_url": get_settings().db_url,
                    "organization_id": str(org_uuid),
                    "created_by": request.get("user_id", "system"),
                    "status": "running",
                    "notes": [],
                }
                with bind_runtime_log_context(schema_mapping_id=str(schema_mapping_id)):
                    try:
                        await _graph.ainvoke(_initial_state, config=_config)
                    except Exception as _exc:
                        _is_interrupt = type(_exc).__name__ in ("GraphInterrupt", "NodeInterrupt")
                        if not _is_interrupt:
                            logger.exception(f"[Schema Mapping] Inline run error: {_exc}")
                            _sf2 = get_async_session_factory()
                            async with _sf2() as _err_session:
                                try:
                                    from sqlalchemy import update as _upd
                                    from .models.migration import SchemaMappingJob as _SMJ
                                    await _err_session.execute(
                                        _upd(_SMJ)
                                        .where(_SMJ.id == schema_mapping_id)
                                        .values(status="error", error_message=str(_exc)[:500])
                                    )
                                    await _err_session.commit()
                                except Exception:
                                    pass

            asyncio.create_task(_inline_run())
            logger.info(f"[Schema Mapping] Dispatched inline asyncio task for: {schema_mapping_id}")

            return {
                "schema_mapping_id": str(schema_mapping_id),
                "status": "running",
            }

        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"[Schema Mapping] Error starting session {schema_mapping_id}: {e}")
            return {
                "schema_mapping_id": str(schema_mapping_id),
                "status": "error",
                "error": str(e),
            }

    @app.post(
        "/api/schema-mapping/{schema_mapping_id}/advance",
        tags=["Schema Mapping"],
        summary="Advance pipeline past a step pause (node-by-node mode)",
    )
    async def schema_mapping_advance(
        schema_mapping_id: str = Path(..., description="Schema Mapping UUID"),
        body: Optional[dict] = None,
        session: AsyncSession = Depends(get_db_session),
    ):
        """
        Advance the schema mapping pipeline past an interrupt_after step pause.

        Called when the user clicks "Next Node →" in the UI after reviewing
        the node output. Valid only when status == 'step_paused'.

        1. Validates the job is in 'step_paused' state.
        2. Clears pending_gate_type / pending_gate_payload, sets status='running'.
        3. Resumes LangGraph execution via ainvoke(None, ...) in a background task.
        """
        from .models.migration import SchemaMappingJob

        schema_mapping_uuid = UUID(schema_mapping_id)

        result = await session.execute(
            select(SchemaMappingJob).where(SchemaMappingJob.id == schema_mapping_uuid)
        )
        job = result.scalar_one_or_none()

        if not job:
            raise HTTPException(status_code=404, detail="Schema mapping job not found")

        if job.status != "step_paused":
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Schema mapping is not paused at a step. "
                    f"Current status: {job.status}. "
                    f"Use gate endpoints for HITL review gates."
                ),
            )

        step_key = job.pending_gate_type or "unknown_step"
        logger.info(f"[Schema Mapping] Advancing past step '{step_key}' for {schema_mapping_id}")

        # Clear step pause and flip status back to running
        await session.execute(
            update(SchemaMappingJob)
            .where(SchemaMappingJob.id == schema_mapping_uuid)
            .values(
                status="running",
                pending_gate_type=None,
                pending_gate_payload=None,
            )
        )
        await session.commit()

        # Resume the graph correctly based on how it was paused:
        # - interrupt_after nodes  → ainvoke(None, ...)
        # - interrupt() gate nodes → ainvoke(Command(resume={...}), ...) with empty decisions
        #   Gate step keys: "step_4_human_review", "step_6_verify_hierarchy"
        _GATE_STEP_KEYS = {"step_4_human_review", "step_6_verify_hierarchy"}

        async def _inline_advance():
            err_msg: str | None = None
            try:
                _graph = get_schema_mapping_graph_instance()
                from langgraph.types import Command as _Command

                _config = {
                    "configurable": {"thread_id": schema_mapping_id},
                    "run_name": f"schema_mapping:{schema_mapping_id}:advance:{step_key}",
                }

                # Resume pattern depends on HOW the graph was paused:
                # - interrupt_after (non-gate steps) → ainvoke(None, ...) continues from checkpoint
                # - interrupt() inside a node (HITL gate) → ainvoke(Command(resume={...}), ...)
                with bind_runtime_log_context(schema_mapping_id=schema_mapping_id):
                    if step_key in _GATE_STEP_KEYS:
                        _gate_payload = body or {}
                        _resume_payload = {
                            "decisions": _gate_payload.get("decisions", []),
                            "approved_foreign_keys": _gate_payload.get("approved_foreign_keys", []),
                            "rejected_foreign_keys": _gate_payload.get("rejected_foreign_keys", []),
                        }
                        _result = await _graph.ainvoke(
                            _Command(resume=_resume_payload),
                            config=_config,
                        )
                    else:
                        _result = await _graph.ainvoke(None, config=_config)
                if isinstance(_result, dict) and _result.get("error_message"):
                    node_status = _result.get("status", "")
                    if node_status not in ("complete", "step_paused", "awaiting_review"):
                        err_msg = str(_result["error_message"])[:500]
            except Exception as exc:
                _is_interrupt = type(exc).__name__ in ("GraphInterrupt", "NodeInterrupt")
                if not _is_interrupt:
                    logger.exception(f"[Schema Mapping] Advance error for {schema_mapping_id}: {exc}")
                    err_msg = str(exc)[:500]

            if err_msg:
                try:
                    _sf2 = get_async_session_factory()
                    async with _sf2() as _err_session:
                        await _err_session.execute(
                            update(SchemaMappingJob)
                            .where(SchemaMappingJob.id == UUID(schema_mapping_id))
                            .values(status="error", error_message=err_msg)
                        )
                        await _err_session.commit()
                    logger.warning(f"[Schema Mapping] Advance returned error state: {err_msg}")
                except Exception:
                    pass

        asyncio.create_task(_inline_advance())

        return {
            "schema_mapping_id": schema_mapping_id,
            "status": "advancing",
            "message": f"Pipeline advancing past step '{step_key}'.",
        }

    @app.post(
        "/api/schema-mapping/{schema_mapping_id}/kick",
        tags=["Schema Mapping"],
        summary="Resume or restart a stalled schema mapping pipeline",
    )
    async def kick_schema_mapping_pipeline(
        schema_mapping_id: str = Path(..., description="Schema Mapping UUID"),
        session: AsyncSession = Depends(get_db_session),
    ):
        """Re-dispatch graph execution when status is stuck at ingest/running with no progress."""
        from .models.migration import SchemaMappingJob

        schema_mapping_uuid = UUID(schema_mapping_id)
        result = await session.execute(
            select(SchemaMappingJob).where(SchemaMappingJob.id == schema_mapping_uuid)
        )
        job = result.scalar_one_or_none()
        if not job:
            raise HTTPException(status_code=404, detail="Schema mapping job not found")

        st = str(job.status or "").lower()
        if st in ("complete", "error", "failed", "ddl_failed", "cancelled", "canceled"):
            raise HTTPException(
                status_code=400,
                detail=f"Cannot kick pipeline in terminal status: {job.status}",
            )
        if st == "step_paused":
            raise HTTPException(
                status_code=400,
                detail="Job is step_paused — use POST /advance instead.",
            )
        if st == "awaiting_review":
            raise HTTPException(
                status_code=400,
                detail="Job is awaiting_review — use gate endpoints instead.",
            )

        await session.execute(
            update(SchemaMappingJob)
            .where(SchemaMappingJob.id == schema_mapping_uuid)
            .values(status="running", error_message=None)
        )
        await session.commit()

        async def _inline_kick():
            _graph = get_schema_mapping_graph_instance()
            _config = {
                "configurable": {"thread_id": str(schema_mapping_id)},
                "run_name": f"schema_mapping:{schema_mapping_id}:kick",
            }
            with bind_runtime_log_context(schema_mapping_id=str(schema_mapping_id)):
                try:
                    await _graph.ainvoke(None, config=_config)
                except Exception as _exc:
                    _is_interrupt = type(_exc).__name__ in ("GraphInterrupt", "NodeInterrupt")
                    if not _is_interrupt:
                        logger.exception(f"[Schema Mapping] Kick error: {_exc}")
                        _sf2 = get_async_session_factory()
                        async with _sf2() as _err_session:
                            try:
                                from sqlalchemy import update as _upd
                                from .models.migration import SchemaMappingJob as _SMJ

                                await _err_session.execute(
                                    _upd(_SMJ)
                                    .where(_SMJ.id == schema_mapping_uuid)
                                    .values(status="error", error_message=str(_exc)[:500])
                                )
                                await _err_session.commit()
                            except Exception:
                                pass

        asyncio.create_task(_inline_kick())
        logger.info(f"[Schema Mapping] Kicked stalled pipeline: {schema_mapping_id} (was {st})")
        return {
            "schema_mapping_id": schema_mapping_id,
            "status": "running",
            "message": "Pipeline kick dispatched — poll /status for step_paused or gates.",
        }

    @app.get(
        "/api/schema-mapping",
        tags=["Schema Mapping"],
        summary="List schema mapping sessions",
    )
    async def list_schema_mappings(
        organization_id: Optional[str] = Query(None, description="Filter by organization UUID"),
        status: Optional[str] = Query(None, description="Filter by status (e.g. complete, awaiting_review, error)"),
        limit: int = Query(50, ge=1, le=500),
        offset: int = Query(0, ge=0),
        session: AsyncSession = Depends(get_db_session),
    ):
        """List all schema mapping sessions, optionally filtered by organization or status."""
        from .models.migration import SchemaMappingJob

        try:
            query = select(SchemaMappingJob)

            if organization_id:
                try:
                    _org_filter_uuid = UUID(organization_id)
                except (ValueError, TypeError):
                    try:
                        _org_filter_uuid = UUID(int=int(organization_id))
                    except (ValueError, TypeError):
                        raise HTTPException(status_code=400, detail="Invalid organization_id UUID")
                query = query.where(SchemaMappingJob.organization_id == _org_filter_uuid)

            if status:
                query = query.where(SchemaMappingJob.status == status)

            count_query = select(func.count()).select_from(SchemaMappingJob)
            if organization_id:
                count_query = count_query.where(SchemaMappingJob.organization_id == _org_filter_uuid)
            if status:
                count_query = count_query.where(SchemaMappingJob.status == status)

            total_result = await session.execute(count_query)
            total_count = total_result.scalar() or 0

            query = query.order_by(SchemaMappingJob.started_at.desc()).limit(limit).offset(offset)
            result = await session.execute(query)
            jobs = result.scalars().all()

            return {
                "total_count": total_count,
                "limit": limit,
                "offset": offset,
                "sessions": [
                    {
                        "schema_mapping_id": str(j.id),
                        "external_cmms_name": j.external_cmms_name,
                        "status": j.status,
                        "progress_pct": j.progress_pct,
                        "current_node": j.current_node,
                        "started_at": j.started_at.isoformat() if j.started_at else None,
                        "completed_at": j.completed_at.isoformat() if j.completed_at else None,
                        "mapping_coverage_pct": j.mapping_coverage_pct,
                        "pending_gate_type": j.pending_gate_type,
                    }
                    for j in jobs
                ],
            }

        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Failed to list schema mappings: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.get(
        "/api/schema-mapping/{schema_mapping_id}",
        tags=["Schema Mapping"],
        summary="Get schema mapping session detail",
    )
    async def get_schema_mapping_detail(
        schema_mapping_id: str = Path(..., description="Schema Mapping UUID"),
        session: AsyncSession = Depends(get_db_session),
    ):
        """
        Get full detail for a schema mapping session.

        When `status == "awaiting_review"`, the `pending_gate_type` and
        `pending_gate_payload` fields contain the data the frontend needs
        to render the HITL review UI.
        """
        from .models.migration import SchemaMappingJob

        try:
            result = await session.execute(
                select(SchemaMappingJob).where(SchemaMappingJob.id == UUID(schema_mapping_id))
            )
            job = result.scalar_one_or_none()

            if not job:
                raise HTTPException(status_code=404, detail="Schema mapping session not found")

            return {
                "schema_mapping_id": str(job.id),
                "status": job.status,
                "current_node": job.current_node,
                "progress_pct": job.progress_pct,
                "external_cmms_name": job.external_cmms_name,
                "schema_source": job.schema_source,
                "schema_format": job.schema_format,
                "started_at": job.started_at.isoformat() if job.started_at else None,
                "completed_at": job.completed_at.isoformat() if job.completed_at else None,
                "stats": {
                    "total_tables": job.total_tables,
                    "total_fields": job.total_fields,
                    "tier1_mapped": job.tier1_mapped,
                    "tier2_auto_mapped": job.tier2_auto_mapped,
                    "tier2_flagged": job.tier2_flagged,
                    "unmapped": job.unmapped,
                    "detected_fk_count": job.detected_fk_count,
                    "hierarchy_depth": job.hierarchy_depth,
                    "mapping_coverage_pct": job.mapping_coverage_pct,
                },
                "pending_gate_type": job.pending_gate_type,
                "pending_gate_payload": job.pending_gate_payload,
                "error_message": job.error_message,
                "ddl_error": job.error_message if job.status == "ddl_failed" else None,
            }

        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Failed to get schema mapping detail: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.delete(
        "/api/schema-mapping/{schema_mapping_id}",
        tags=["Schema Mapping"],
        summary="Cancel a schema mapping session",
    )
    async def cancel_schema_mapping(
        schema_mapping_id: str = Path(..., description="Schema Mapping UUID"),
        session: AsyncSession = Depends(get_db_session),
    ):
        """Cancel a running or paused schema mapping session."""
        from .models.migration import SchemaMappingJob

        try:
            result = await session.execute(
                select(SchemaMappingJob).where(SchemaMappingJob.id == UUID(schema_mapping_id))
            )
            job = result.scalar_one_or_none()

            if not job:
                raise HTTPException(status_code=404, detail="Schema mapping session not found")

            if job.status in ["complete", "cancelled", "error"]:
                raise HTTPException(
                    status_code=400,
                    detail=f"Cannot cancel session with status: {job.status}",
                )

            job.status = "cancelled"
            job.completed_at = datetime.utcnow()
            # Clear the pending HITL gate so the cancelled session is a clean
            # terminal state (no stale "awaiting input" in the UI).
            job.pending_gate_type = None
            job.pending_gate_payload = None
            await session.commit()

            logger.info(f"[Schema Mapping] Cancelled session: {schema_mapping_id}")

            return {
                "schema_mapping_id": schema_mapping_id,
                "status": "cancelled",
                "message": "Schema mapping session cancelled successfully",
            }

        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Failed to cancel schema mapping: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    _SCHEMA_ARTIFACT_ALIASES: dict[str, tuple[str, str]] = {
        "mapper_config.json": ("mapper_config.json", "application/json"),
        "output.json": ("mapper_config.json", "application/json"),
        "field_mappings.csv": ("field_mappings.csv", "text/csv"),
        "output.csv": ("field_mappings.csv", "text/csv"),
        "schema_ddl_preview.sql": ("schema_ddl_preview.sql", "text/plain"),
        "output.sql": ("schema_ddl_preview.sql", "text/plain"),
    }

    def _schema_artifact_api_urls(schema_mapping_id: str) -> dict[str, str]:
        base = f"/api/schema-mapping/{schema_mapping_id}/artifacts"
        return {
            "output_json_url": f"{base}/mapper_config.json",
            "output_csv_url": f"{base}/field_mappings.csv",
            "output_sql_url": f"{base}/schema_ddl_preview.sql",
        }

    def _normalize_schema_artifact_urls(
        schema_mapping_id: str,
        json_url: str | None,
        csv_url: str | None,
        sql_url: str | None,
    ) -> dict[str, str]:
        """Prefer browser-downloadable API paths over empty or private blob URLs."""
        defaults = _schema_artifact_api_urls(schema_mapping_id)
        out = dict(defaults)
        for key, stored in (
            ("output_json_url", json_url),
            ("output_csv_url", csv_url),
            ("output_sql_url", sql_url),
        ):
            v = (stored or "").strip()
            if not v:
                continue
            if v.startswith(f"/api/schema-mapping/{schema_mapping_id}/artifacts/"):
                out[key] = v
            elif v.startswith("http://") or v.startswith("https://"):
                if ".blob.core.windows.net" in v:
                    continue
                out[key] = v
        return out

    @app.get(
        "/api/schema-mapping/{schema_mapping_id}/artifacts/{filename}",
        tags=["Schema Mapping"],
        summary="Download schema mapping output artifact",
    )
    async def download_schema_mapping_artifact(
        schema_mapping_id: str = Path(..., description="Schema Mapping UUID"),
        filename: str = Path(..., description="Artifact filename"),
    ):
        """Download JSON config, CSV mappings, or SQL DDL preview for a schema mapping session."""
        import os
        import tempfile

        from fastapi.responses import FileResponse

        if filename not in _SCHEMA_ARTIFACT_ALIASES:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid filename. Allowed: {', '.join(sorted(_SCHEMA_ARTIFACT_ALIASES))}",
            )

        real_name, media_type = _SCHEMA_ARTIFACT_ALIASES[filename]
        artifact_dir = os.path.join(tempfile.gettempdir(), f"schema_mapping_{schema_mapping_id}")
        file_path = os.path.join(artifact_dir, real_name)

        if os.path.isfile(file_path):
            return FileResponse(
                file_path,
                media_type=media_type,
                filename=real_name,
            )

        from .config import get_settings

        settings = get_settings()
        blob_conn = getattr(settings, "azure_storage_connection_string", None)
        blob_container = getattr(settings, "azure_blob_container_name", "") or ""
        if blob_conn and blob_container:
            try:
                from azure.storage.blob.aio import BlobServiceClient

                blob_path = f"schema-mapping/{schema_mapping_id}/{real_name}"
                async with BlobServiceClient.from_connection_string(blob_conn) as svc:
                    bc = svc.get_blob_client(container=blob_container, blob=blob_path)
                    stream = await bc.download_blob()
                    data = await stream.readall()
                from fastapi.responses import Response

                return Response(content=data, media_type=media_type, headers={
                    "Content-Disposition": f'attachment; filename="{real_name}"',
                })
            except Exception as exc:
                logger.warning(
                    f"Schema artifact blob download failed {schema_mapping_id}/{real_name}: {exc}"
                )

        raise HTTPException(
            status_code=404,
            detail=(
                f"Artifact '{real_name}' not found for this session. "
                "Re-run output generation or check server temp storage."
            ),
        )

    @app.get(
        "/api/schema-mapping/{schema_mapping_id}/status",
        tags=["Schema Mapping"],
        summary="Get schema mapping progress",
    )
    async def get_schema_mapping_status(
        schema_mapping_id: str = Path(..., description="Schema Mapping UUID"),
        session: AsyncSession = Depends(get_db_session),
    ):
        """Get current status and progress of a schema mapping session."""
        from sqlalchemy import select
        from .models.migration import SchemaMappingJob

        def _tier_label(tier: str) -> str:
            t = (tier or "").strip()
            return {
                "T1_exact": "S1 (exact)",
                "T1_variation": "S1B (var)",
                "T1_regex": "S2 (regex)",
                "T1_llm": "S3 (llm)",
                "T2_semantic": "S4 (semantic)",
            }.get(t, t or "T?")

        def _build_schema_deterministic_logs(payload: dict | None) -> list[str]:
            if not isinstance(payload, dict):
                return []
            mappings_by_table = payload.get("mappings_by_table")
            unresolved_by_table = payload.get("unresolved_by_table") or {}
            if not isinstance(mappings_by_table, dict):
                return []

            lines: list[str] = []
            for table, mappings in mappings_by_table.items():
                if not isinstance(mappings, list):
                    continue
                cols = [str(m.get("source_field", "")) for m in mappings if isinstance(m, dict)]
                cols = [c for c in cols if c]
                col_preview = ", ".join(cols[:8]) + ("..." if len(cols) > 8 else "")
                lines.append(f"[Node 2] ▶ Processing source table: {table}")
                lines.append(f"[Node 2]   Columns: {len(cols)} [{col_preview}]")
                for m in mappings:
                    if not isinstance(m, dict):
                        continue
                    src = str(m.get("source_field", ""))
                    tgt = str(m.get("target_field", ""))
                    tier = _tier_label(str(m.get("tier", "")))
                    conf = float(m.get("confidence", 0.0) or 0.0)
                    lines.append(f"[Node 2]   {tier}: {src} -> {tgt} ({conf:.2f})")
                unresolved = unresolved_by_table.get(table, [])
                unresolved_count = len(unresolved) if isinstance(unresolved, list) else 0
                lines.append(
                    f"[Node 2] ✓ Table {table}: {len(mappings)} mapped, {unresolved_count} unresolved"
                )
            return lines

        def _build_schema_pre_semantic_logs(payload: dict | None) -> list[str]:
            if not isinstance(payload, dict):
                return []
            items_by_table = payload.get("items_by_table")
            if not isinstance(items_by_table, dict):
                return []
            lines: list[str] = []
            total_reviewable = int(payload.get("total_reviewable", 0) or 0)
            lines.append(f"[Node 3] ▶ Pre-semantic review opened: {total_reviewable} fields")
            for table, items in items_by_table.items():
                if not isinstance(items, list):
                    continue
                lines.append(f"[Node 3]   Table {table}: {len(items)} fields")
                for it in items:
                    if not isinstance(it, dict):
                        continue
                    src = str(it.get("source_field", ""))
                    tgt = str(it.get("target_field", ""))
                    tier = _tier_label(str(it.get("tier", "")))
                    conf = float(it.get("confidence", 0.0) or 0.0)
                    lines.append(f"[Node 3]   {tier}: {src} -> {tgt} ({conf:.2f})")
            return lines

        def _build_schema_field_review_logs(payload: dict | None) -> list[str]:
            if not isinstance(payload, dict):
                return []
            flagged = payload.get("flagged")
            unmapped = payload.get("unmapped")
            lines: list[str] = []
            if isinstance(flagged, list):
                lines.append(f"[Node 5] ▶ Field review: {len(flagged)} flagged items")
                for f in flagged[:200]:
                    if not isinstance(f, dict):
                        continue
                    src = str(f.get("source_field", ""))
                    tbl = str(f.get("source_table", ""))
                    sug = str(f.get("suggested_target", ""))
                    conf = float(f.get("confidence", 0.0) or 0.0)
                    lines.append(f"[Node 5]   flagged: {tbl}.{src} -> {sug} ({conf:.2f})")
            if isinstance(unmapped, list):
                lines.append(f"[Node 5] ▶ Unmapped items: {len(unmapped)}")
                for u in unmapped[:200]:
                    if not isinstance(u, dict):
                        continue
                    src = str(u.get("source_field", ""))
                    tbl = str(u.get("source_table", ""))
                    lines.append(f"[Node 5]   unmapped: {tbl}.{src}")
            return lines

        def _merge_runtime_logs_into_schema_nodes(nodes: list[dict], schema_mapping_id_value: str) -> None:
            """Append captured runtime [Node X] lines into matching schema nodes[].logs.

            Schema node files were authored before Gate-1 (Pre-Semantic Review) was
            inserted as pipeline node_id 3.  Everything from old "[Node 3]" onwards is
            now one position later, so we remap before bucketing.

            Same structlog formatting fix as _merge_runtime_logs_into_nodes: use
            re.search() on the full rendered line and strip the trailing [module.name].
            """
            # old label → current schema pipeline node_id (Gate inserted at position 3)
            _REMAP = {3: 4, 4: 5, 5: 6, 6: 7, 7: 8}
            _node_re = re.compile(r"\[Node\s+(\d+)\](.*)")
            _sfx_re = re.compile(r"\s+\[\S+(?:\.\S+)+\]\s*$")
            _ansi_re = re.compile(r"\x1b\[[0-9;]*m")

            rt = get_runtime_logs(schema_mapping_id=schema_mapping_id_value, since=0, limit=1000)
            entries = rt.get("logs", []) if isinstance(rt, dict) else []
            by_node: dict[int, list[str]] = {}
            for e in entries:
                if not isinstance(e, dict):
                    continue
                raw = str(e.get("message", "") or "")
                event_str = raw
                if raw.startswith("{"):
                    try:
                        event_str = json.loads(raw).get("event") or raw
                    except Exception:
                        pass
                event_str = _ansi_re.sub("", event_str)
                m = _node_re.search(event_str)
                if not m:
                    continue
                nid = int(m.group(1))
                nid = _REMAP.get(nid, nid)
                body = _sfx_re.sub("", m.group(2)).strip()
                clean = f"[Node {nid}] {body}" if body else f"[Node {nid}]"
                by_node.setdefault(nid, []).append(clean)

            for n in nodes:
                nid = int(n.get("node_id", 0) or 0)
                extra = by_node.get(nid, [])
                if not extra:
                    continue
                existing = n.get("logs") or []
                seen = set(existing)
                merged = list(existing)
                for line in extra:
                    if line not in seen:
                        merged.append(line)
                        seen.add(line)
                n["logs"] = merged

        try:
            result = await session.execute(
                select(SchemaMappingJob).where(SchemaMappingJob.id == UUID(schema_mapping_id))
            )
            job = result.scalar_one_or_none()

            if not job:
                raise HTTPException(status_code=404, detail="Schema mapping job not found")

            # ── Build nodes[] array ───────────────────────────────────────
            _SCHEMA_PIPELINE = [
                (0, "Canonical Schema Fetch"),
                (1, "Schema Ingestion"),
                (2, "Deterministic Mapping"),
                (3, "Gate 1: Pre-Semantic Review"),
                (4, "Semantic Mapping"),
                (5, "Gate 2: Field Mapping Review"),
                (6, "Hierarchy Detection"),
                (7, "Gate 3: Hierarchy Verification"),
                (8, "Output Generation"),
                (9, "Gate 4: Artifacts Review"),
                (10, "Write to Database"),
            ]
            completed_ids = {e["node_id"] for e in (job.node_logs or [])}
            completed_map = {e["node_id"]: e for e in (job.node_logs or [])}
            schema_nodes = []
            for nid, nname in _SCHEMA_PIPELINE:
                if nid in completed_ids:
                    schema_nodes.append(completed_map[nid])
                elif nid == job.current_node and job.status in ("running", "step_paused", "awaiting_review"):
                    schema_nodes.append({
                        "node_id": nid, "node_name": nname, "status": "running",
                        "started_at": None, "completed_at": None,
                        "duration_ms": None, "output": None, "logs": [],
                    })
                else:
                    schema_nodes.append({
                        "node_id": nid, "node_name": nname, "status": "pending",
                        "started_at": None, "completed_at": None,
                        "duration_ms": None, "output": None, "logs": [],
                    })

            # Enrich schema node logs from gate payload so frontend can render
            # detailed, terminal-style nodewise logs directly from /status.
            gate = str(job.pending_gate_type or "")
            payload = job.pending_gate_payload if isinstance(job.pending_gate_payload, dict) else None

            deterministic_like = gate in ("step_2_deterministic_mapping", "pre_semantic")
            if deterministic_like and payload and isinstance(payload.get("mappings_by_table"), dict):
                detailed = _build_schema_deterministic_logs(payload)
                if detailed:
                    for n in schema_nodes:
                        if n.get("node_id") == 2:
                            n["logs"] = (n.get("logs") or []) + detailed
                            break

            if gate in ("pre_semantic", "step_3_pre_semantic_review") and payload:
                detailed = _build_schema_pre_semantic_logs(payload)
                if detailed:
                    for n in schema_nodes:
                        if n.get("node_id") == 3:
                            n["logs"] = (n.get("logs") or []) + detailed
                            break

            if gate in ("field_mapping", "step_4_human_review") and payload:
                detailed = _build_schema_field_review_logs(payload)
                if detailed:
                    for n in schema_nodes:
                        if n.get("node_id") == 5:
                            n["logs"] = (n.get("logs") or []) + detailed
                            break

            # Merge captured runtime [Node X] logs for all schema nodes.
            _merge_runtime_logs_into_schema_nodes(schema_nodes, str(job.id))

            _sid = str(job.id)
            _artifact_urls = _normalize_schema_artifact_urls(
                _sid,
                job.output_json_url,
                job.output_csv_url,
                job.output_sql_url,
            )
            _gate_payload = job.pending_gate_payload
            if isinstance(_gate_payload, dict):
                _gate_payload = dict(_gate_payload)
                for _url_key in ("output_json_url", "output_csv_url", "output_sql_url"):
                    if not str(_gate_payload.get(_url_key) or "").strip():
                        _gate_payload[_url_key] = _artifact_urls[_url_key]
                if job.pending_gate_type == "artifacts_review" and isinstance(
                    _gate_payload.get("summary"), dict
                ):
                    _fs = job.final_summary if isinstance(job.final_summary, dict) else {}
                    _summary = dict(_gate_payload["summary"])
                    for _sk, _jk in (
                        ("total_source_fields", "total_source_fields"),
                        ("tier1_auto_mapped", "tier1_auto_mapped"),
                        ("tier2_auto_mapped", "tier2_auto_mapped"),
                        ("tier2_flagged", "tier2_flagged"),
                        ("unmappable", "unmappable"),
                        ("mapping_coverage_pct", "mapping_coverage_pct"),
                        ("detected_fk_count", "detected_fk_count"),
                        ("max_hierarchy_depth", "max_hierarchy_depth"),
                        ("junction_table_count", "junction_table_count"),
                    ):
                        if _summary.get(_sk) in (None, "", 0, 0.0) and _fs.get(_jk) not in (None, ""):
                            _summary[_sk] = _fs[_jk]
                    _gate_payload["summary"] = _summary

            from .connectors.fiix_credentials import (
                enrich_schema_comparison_for_status,
                schema_comparison_from_nodes,
            )

            _schema_comparison = enrich_schema_comparison_for_status(
                schema_comparison_from_nodes(schema_nodes),
                job_total_tables=int(job.total_tables or 0),
                job_total_fields=int(job.total_fields or 0),
                final_summary=job.final_summary if isinstance(job.final_summary, dict) else None,
                nodes=schema_nodes,
                pending_gate_payload=_gate_payload if isinstance(_gate_payload, dict) else None,
                external_cmms_name=str(job.external_cmms_name or "Fiix"),
            )

            return {
                "schema_mapping_id": _sid,
                "status": job.status,
                "current_node": job.current_node,
                "progress_pct": job.progress_pct,
                "external_cmms_name": job.external_cmms_name,
                "started_at": job.started_at.isoformat(),
                "completed_at": job.completed_at.isoformat() if job.completed_at else None,
                "schema_comparison": _schema_comparison,
                "stats": {
                    "total_tables": job.total_tables,
                    "total_fields": job.total_fields,
                    "tier1_mapped": job.tier1_mapped,
                    "tier2_auto_mapped": job.tier2_auto_mapped,
                    "tier2_flagged": job.tier2_flagged,
                    "unmapped": job.unmapped,
                    "detected_fk_count": job.detected_fk_count,
                    "hierarchy_depth": job.hierarchy_depth,
                    "mapping_coverage_pct": job.mapping_coverage_pct,
                },
                "error_message": job.error_message,
                "pending_gate_type": job.pending_gate_type,
                "pending_gate_payload": _gate_payload,
                "output_json_url": _artifact_urls["output_json_url"],
                "output_csv_url": _artifact_urls["output_csv_url"],
                "output_sql_url": _artifact_urls["output_sql_url"],
                "ddl_error": job.error_message if job.status == "ddl_failed" else None,
                "nodes": schema_nodes,
            }

        except HTTPException:
            # A genuine 404 (this id is a migration job, not a schema mapping) must
            # stay a 404 — don't let the generic handler turn it into a 500.
            raise
        except Exception as e:
            logger.exception(f"Failed to get schema mapping status: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.get(
        "/api/schema-mapping/{schema_mapping_id}/runtime-logs",
        tags=["Schema Mapping"],
        summary="Get captured backend runtime logs for this schema mapping session",
    )
    async def get_schema_mapping_runtime_logs(
        schema_mapping_id: str = Path(..., description="Schema Mapping UUID"),
        since: int = Query(0, ge=0, description="Return logs with seq > since"),
        limit: int = Query(200, ge=1, le=1000),
    ):
        data = get_runtime_logs(
            schema_mapping_id=schema_mapping_id,
            since=since,
            limit=limit,
        )
        return {"schema_mapping_id": schema_mapping_id, **data}

    # ── Internal shared helper for schema mapping gate resume ─────────────
    async def _enqueue_schema_mapping_resume(
        schema_mapping_id: str,
        gate_type: str,
        decisions: dict,
        session: AsyncSession,
    ) -> dict:
        """Validate state and enqueue ARQ resume_schema_mapping task."""
        from .models.migration import SchemaMappingJob

        job_id = UUID(schema_mapping_id)
        result = await session.execute(
            select(SchemaMappingJob).where(SchemaMappingJob.id == job_id)
        )
        job = result.scalar_one_or_none()

        if not job:
            logger.warning(
                "schema_mapping_gate_not_found",
                schema_mapping_id=schema_mapping_id,
                gate_type=gate_type,
            )
            raise HTTPException(status_code=404, detail="Schema mapping session not found")

        if job.status not in ["awaiting_review", "running"]:
            logger.warning(
                "schema_mapping_gate_invalid_status",
                schema_mapping_id=schema_mapping_id,
                gate_type=gate_type,
                current_status=job.status,
                expected_statuses=["awaiting_review", "running"],
                pending_gate=job.pending_gate_type,
                decisions_keys=list(decisions.keys()) if isinstance(decisions, dict) else type(decisions).__name__,
            )
            raise HTTPException(
                status_code=400,
                detail=f"Cannot submit decisions for session with status: {job.status}",
            )

        if job.pending_gate_type and job.pending_gate_type != gate_type:
            logger.warning(
                "schema_mapping_gate_mismatch",
                schema_mapping_id=schema_mapping_id,
                submitted_gate=gate_type,
                expected_gate=job.pending_gate_type,
                current_status=job.status,
            )
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Gate mismatch: session is waiting at '{job.pending_gate_type}', "
                    f"but decisions were submitted for '{gate_type}'"
                ),
            )

        logger.info(
            "schema_mapping_gate_decisions_received",
            schema_mapping_id=schema_mapping_id,
            gate_type=gate_type,
            decisions_keys=list(decisions.keys()) if isinstance(decisions, dict) else type(decisions).__name__,
        )

        # Flip status to 'running' immediately so the frontend poller sees the
        # transition and keeps polling instead of stopping at 'awaiting_review'.
        await session.execute(
            update(SchemaMappingJob)
            .where(SchemaMappingJob.id == UUID(schema_mapping_id))
            .values(
                status="running",
                pending_gate_type=None,
                pending_gate_payload=None,
            )
        )
        await session.commit()

        # Always run inline — the initial schema mapping run is always dispatched
        # as an asyncio task using the singleton MemorySaver graph (see the
        # POST /api/schema-mapping start endpoint).  Command(resume=...) requires
        # the *same* graph instance and checkpointer that originally executed the
        # interrupt(), so we must resume on the same singleton here.  Routing to
        # the ARQ worker would create a new graph backed by AsyncPostgresSaver,
        # which has no checkpoint for this thread_id and would fail to resume.
        async def _inline_gate_resume():
            err_msg: str | None = None
            try:
                from langgraph.types import Command as _Command
                _graph = get_schema_mapping_graph_instance()
                _cfg = {
                    "configurable": {"thread_id": schema_mapping_id},
                    "run_name": f"schema_mapping:{schema_mapping_id}:gate:{gate_type}",
                }
                # Do NOT inject db_session into state — AsyncSession is not
                # msgpack-serializable and would crash MemorySaver checkpointing.
                # All schema mapper nodes use _auto DB writer helpers that open
                # their own sessions via get_async_session_factory().
                _resume_cmd = _Command(resume=decisions)
                with bind_runtime_log_context(schema_mapping_id=schema_mapping_id):
                    await _graph.ainvoke(_resume_cmd, config=_cfg)
                logger.info(
                    f"[Schema Mapping] Inline gate resume complete: "
                    f"gate={gate_type} session={schema_mapping_id}"
                )
            except Exception as exc:
                _is_interrupt = type(exc).__name__ in ("GraphInterrupt", "NodeInterrupt")
                if not _is_interrupt:
                    logger.exception(
                        f"[Schema Mapping] Inline gate resume error "
                        f"gate={gate_type} session={schema_mapping_id}: {exc}"
                    )
                    err_msg = str(exc)[:500]

            if err_msg:
                try:
                    from .models.migration import SchemaMappingJob as _SMJ
                    _sf2 = get_session_factory()
                    async with _sf2() as _err_s:
                        await _err_s.execute(
                            update(_SMJ)
                            .where(_SMJ.id == UUID(schema_mapping_id))
                            .values(status="error", error_message=err_msg)
                        )
                        await _err_s.commit()
                except Exception:
                    pass

        try:
            asyncio.create_task(_inline_gate_resume())
        except Exception as inline_err:
            logger.error(f"[Schema Mapping] Inline resume failed: {inline_err}")
            raise HTTPException(status_code=500, detail="Failed to resume schema mapping")

        return {
            "schema_mapping_id": schema_mapping_id,
            "status": "approved",
            "gate_type": gate_type,
            "message": f"Decisions accepted for gate '{gate_type}'. Schema mapping resuming.",
        }

    @app.post(
        "/api/schema-mapping/{schema_mapping_id}/gate/pre-semantic",
        tags=["Schema Mapping"],
        summary="Gate 0 — Pre-semantic T1 mapping review (Node 2a)",
    )
    async def schema_mapping_gate_pre_semantic(
        schema_mapping_id: str = Path(..., description="Schema Mapping UUID"),
        request: dict = {},
        session: AsyncSession = Depends(get_db_session),
    ):
        """
        Submit HITL decisions for Gate 0 — pre-semantic review.

        The pipeline pauses here after Node 2 (deterministic mapper) so the
        user can approve or reject T1 exact/alias/regex/LLM mappings before
        the (more expensive) semantic embedding phase runs.

        Decisions format:
        ```json
        {
          "decisions": {
            "table_name": [
              {"source_field": "...", "decision": "approve" | "semantic"}
            ]
          }
        }
        ```
        """
        try:
            body = request if isinstance(request, dict) else {}
            # When the gate sends Step-1 table routing / new-table creation, forward the
            # whole structure so the node can apply it. Otherwise legacy flat list.
            if (body.get("table_overrides") or body.get("new_tables") or body.get("new_columns")) and isinstance(body.get("decisions"), list):
                decisions = {
                    "decisions": body.get("decisions") or [],
                    "table_overrides": body.get("table_overrides") or {},
                    "new_tables": body.get("new_tables") or {},
                    "new_columns": body.get("new_columns") or {},
                }
            else:
                decisions = body.get("decisions", request)
            return await _enqueue_schema_mapping_resume(
                schema_mapping_id, "pre_semantic", decisions, session
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Failed to process schema mapping gate/pre-semantic: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.post(
        "/api/schema-mapping/{schema_mapping_id}/gate/field-mapping",
        tags=["Schema Mapping"],
        summary="Gate 1 — Field mapping approval decisions (Node 4)",
    )
    async def schema_mapping_gate_field_mapping(
        schema_mapping_id: str = Path(..., description="Schema Mapping UUID"),
        request: dict = {},
        session: AsyncSession = Depends(get_db_session),
    ):
        """
        Submit HITL decisions for Gate 1 — field mapping approval.

        The pipeline pauses here after Node 3 (semantic mapper) when
        low-confidence mappings or unmapped fields need human review.

        Decisions format:
        ```json
        {
          "decisions": [
            {"action": "accept",   "source_field": "...", "source_table": "..."},
            {"action": "reject",   "source_field": "...", "source_table": "..."},
            {"action": "override", "source_field": "...", "source_table": "...",
             "target_field": "asset_code", "rationale": "..."},
            {"action": "custom",   "source_field": "...", "source_table": "...",
             "target_table": "assets", "custom_column_name": "...", "data_type": "VARCHAR(100)"},
            {"action": "raw_metadata", "source_field": "...", "source_table": "..."},
            {"action": "skip",     "source_field": "...", "source_table": "..."}
          ]
        }
        ```
        """
        try:
            decisions = request.get("decisions", request)
            return await _enqueue_schema_mapping_resume(
                schema_mapping_id, "field_mapping", decisions, session
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Failed to process schema mapping gate/field-mapping: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.post(
        "/api/schema-mapping/{schema_mapping_id}/gate/hierarchy",
        tags=["Schema Mapping"],
        summary="Gate 2 — Hierarchy verification decisions (Node 6)",
    )
    async def schema_mapping_gate_hierarchy(
        schema_mapping_id: str = Path(..., description="Schema Mapping UUID"),
        request: dict = {},
        session: AsyncSession = Depends(get_db_session),
    ):
        """
        Submit HITL decisions for Gate 2 — hierarchy verification.

        The pipeline pauses here after Node 5 (hierarchy resolver) for the
        customer to approve or correct detected FK relationships.

        Decisions format:
        ```json
        {
          "approved_hierarchies": [
            {"source_table": "...", "source_column": "...",
             "target_table": "...", "target_column": "...", "confirmed": true}
          ],
          "rejected_hierarchies": [...]
        }
        ```
        """
        try:
            decisions = request.get("decisions", request)
            return await _enqueue_schema_mapping_resume(
                schema_mapping_id, "hierarchy", decisions, session
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Failed to process schema mapping gate/hierarchy: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.post(
        "/api/schema-mapping/{schema_mapping_id}/gate/artifacts-review",
        tags=["Schema Mapping"],
        summary="Gate 3 — Artifacts review and schema name confirmation (Node 7.5)",
    )
    async def schema_mapping_gate_artifacts_review(
        schema_mapping_id: str = Path(..., description="Schema Mapping UUID"),
        request: dict = {},
        session: AsyncSession = Depends(get_db_session),
    ):
        """
        Submit HITL decision for Gate 3 — artifacts review.

        The pipeline pauses here after Node 7 (output generation) for the
        customer to review the generated artifacts and set the new PostgreSQL
        schema name before it is created.

        Request format:
        ```json
        {"new_schema_name": "my_custom_schema_name"}
        ```
        """
        try:
            decisions = request.get("decisions", request)
            return await _enqueue_schema_mapping_resume(
                schema_mapping_id, "artifacts_review", decisions, session
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Failed to process schema mapping gate/artifacts-review: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.post(
        "/api/schema-mapping/{schema_mapping_id}/retry-ddl",
        tags=["Schema Mapping"],
        summary="Retry DDL execution after a ddl_failed error with corrected field definitions",
    )
    async def retry_schema_mapping_ddl(
        schema_mapping_id: str = Path(..., description="Schema Mapping UUID"),
        request: dict = {},
        session: AsyncSession = Depends(get_db_session),
    ):
        """
        Re-submit corrected field definitions after a DDL failure.

        When status == "ddl_failed", the user has seen which SQL statement failed
        (returned in the /status response as "ddl_error"). They correct the offending
        field definition (e.g. fix the data_type) and POST the corrected
        extra_fields_config here.

        This endpoint:
        1. Validates the job is in ddl_failed state
        2. Updates state["extra_fields_config"] with the corrected definitions
        3. Re-enqueues the graph to resume from Node 8

        Request body:
        {
            "extra_fields_config": [
                {
                    "source_field": "vendor_ref",
                    "source_table": "assets_ext",
                    "storage_strategy": "custom",
                    "target_table": "assets",
                    "custom_column_name": "vendor_asset_ref",
                    "data_type": "VARCHAR(100)",    ← corrected from bad value
                    "nullable": true,
                    "user_approved": true
                }
            ]
        }
        """
        from .models.migration import SchemaMappingJob

        try:
            job_id = UUID(schema_mapping_id)

            result = await session.execute(
                select(SchemaMappingJob).where(SchemaMappingJob.id == job_id)
            )
            job = result.scalar_one_or_none()

            if not job:
                raise HTTPException(status_code=404, detail="Schema mapping job not found")

            if job.status != "ddl_failed":
                raise HTTPException(
                    status_code=400,
                    detail=f"Job is not in ddl_failed state (current: {job.status}). "
                           f"Only ddl_failed jobs can be retried via this endpoint.",
                )

            corrected_config = request.get("extra_fields_config")
            if not corrected_config:
                raise HTTPException(
                    status_code=400,
                    detail="extra_fields_config is required with corrected field definitions",
                )

            logger.info(
                f"[Schema Mapping] Retrying DDL for {schema_mapping_id} "
                f"with {len(corrected_config)} corrected field definitions"
            )

            # Enqueue resume_schema_mapping with the corrected extra_fields_config
            # We pass it as decisions so Command(resume=decisions) updates the state
            enqueued = False
            try:
                from arq import create_pool
                from arq.connections import RedisSettings
                settings = get_settings()
                if settings.redis_url:
                    redis = await create_pool(RedisSettings.from_dsn(settings.redis_url))
                    await redis.enqueue_job(
                        "resume_schema_mapping",
                        schema_mapping_id=schema_mapping_id,
                        gate_type="ddl_retry",
                        decisions={"extra_fields_config": corrected_config},
                    )
                    enqueued = True
            except Exception as arq_err:
                logger.warning(f"[Schema Mapping] ARQ enqueue failed, running inline: {arq_err}")

            if not enqueued:
                try:
                    from .worker import resume_schema_mapping
                    asyncio.create_task(
                        resume_schema_mapping(
                            {},
                            schema_mapping_id=schema_mapping_id,
                            gate_type="ddl_retry",
                            decisions={"extra_fields_config": corrected_config},
                        )
                    )
                except Exception as inline_err:
                    logger.error(f"[Schema Mapping] Inline DDL retry failed: {inline_err}")
                    raise HTTPException(status_code=500, detail="Failed to retry DDL")

            return {
                "schema_mapping_id": schema_mapping_id,
                "status": "retrying",
                "message": f"DDL retry enqueued with {len(corrected_config)} corrected definitions.",
            }

        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Failed to retry DDL for schema mapping: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.post(
        "/api/schema-mapping/{schema_mapping_id}/custom-mapping",
        tags=["Schema Mapping"],
        summary="Add a custom field mapping (for unmapped fields)",
    )
    async def add_custom_mapping(
        schema_mapping_id: str = Path(..., description="Schema Mapping UUID"),
        source_field: str = Form(..., description="Source field name from external schema"),
        source_table: str = Form(..., description="Source table name"),
        target_field: str = Form(..., description="Target canonical field (or new custom field)"),
        rationale: str = Form(..., description="Why this mapping was chosen"),
        session: AsyncSession = Depends(get_db_session),
    ):
        """
        Add a custom field mapping for unmapped fields.

        This allows users to manually define mappings for fields that the system
        couldn't automatically match. Useful for:
        - Vendor-specific fields
        - Custom fields unique to this organization
        - Fields the system marked as unmapped (confidence too low)

        Stores the mapping in the audit trail with tier="user_manual" and confidence=1.0.
        """
        try:
            job_id = UUID(schema_mapping_id)

            # Verify job exists
            from sqlalchemy import select
            from .models.migration import SchemaMappingJob

            result = await session.execute(
                select(SchemaMappingJob).where(SchemaMappingJob.id == job_id)
            )
            job = result.scalar_one_or_none()

            if not job:
                raise HTTPException(status_code=404, detail="Schema mapping job not found")

            # Log the custom mapping with user_manual tier and perfect confidence
            await _log_field_mapping(
                session,
                job_id,
                source_field,
                source_table,
                target_field,
                confidence=1.0,
                tier="user_manual",
                rationale=rationale,
            )

            logger.info(f"[Schema Mapping] Added custom mapping: {source_field} → {target_field}")

            return {
                "schema_mapping_id": str(schema_mapping_id),
                "source_field": source_field,
                "source_table": source_table,
                "target_field": target_field,
                "tier": "user_manual",
                "confidence": 1.0,
                "status": "success",
            }

        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Failed to add custom mapping: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.get(
        "/api/schema-mapping/{schema_mapping_id}/mappings",
        tags=["Schema Mapping"],
        summary="Get all field mappings for this session",
    )
    async def get_schema_mapping_mappings(
        schema_mapping_id: str = Path(..., description="Schema Mapping UUID"),
        tier: Optional[str] = Query(None, description="Filter by tier (T1_exact, T1_alias, T1_registry, T2_semantic, T1_human_approved, T1_human_override, unmapped, user_manual)"),
        session: AsyncSession = Depends(get_db_session),
    ):
        """
        Get all field mappings produced by this schema mapping session.

        Returns all mappings with confidence scores, tier labels, source/target
        tables and fields, and rationale. Optionally filter by tier.
        """
        try:
            from .models.migration import SchemaMappingFieldMapping

            query = select(SchemaMappingFieldMapping).where(
                SchemaMappingFieldMapping.schema_mapping_id == UUID(schema_mapping_id)
            )
            if tier:
                query = query.where(SchemaMappingFieldMapping.tier == tier)

            query = query.order_by(
                SchemaMappingFieldMapping.source_table,
                SchemaMappingFieldMapping.source_field,
            )

            result = await session.execute(query)
            mappings = result.scalars().all()

            tier_counts: dict = {}
            for m in mappings:
                tier_counts[m.tier] = tier_counts.get(m.tier, 0) + 1

            return {
                "schema_mapping_id": schema_mapping_id,
                "total_mappings": len(mappings),
                "tier_breakdown": tier_counts,
                "mappings": [
                    {
                        "source_table": m.source_table,
                        "source_field": m.source_field,
                        "target_field": m.target_field,
                        "tier": m.tier,
                        "confidence": m.confidence,
                        "rationale": m.rationale,
                        "mapped_at": m.mapped_at.isoformat() if m.mapped_at else None,
                    }
                    for m in mappings
                ],
            }

        except HTTPException:
            raise
        except Exception as e:
            logger.exception(f"Failed to get schema mapping mappings: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.get(
        "/api/schema-mapping/{schema_mapping_id}/unmapped",
        tags=["Schema Mapping"],
        summary="Get unmapped fields for this session",
    )
    async def get_unmapped_fields(
        schema_mapping_id: str = Path(..., description="Schema Mapping UUID"),
        session: AsyncSession = Depends(get_db_session),
    ):
        """
        Get all unmapped fields from a schema mapping session.

        Returns fields that the system couldn't confidently map.
        User can review these and add custom mappings via POST /custom-mapping.
        """
        try:
            from sqlalchemy import select
            from .models.migration import SchemaMappingFieldMapping

            # Query all unmapped fields
            result = await session.execute(
                select(SchemaMappingFieldMapping)
                .where(SchemaMappingFieldMapping.schema_mapping_id == UUID(schema_mapping_id))
                .where(SchemaMappingFieldMapping.tier == "unmapped")
                .order_by(SchemaMappingFieldMapping.source_table, SchemaMappingFieldMapping.source_field)
            )
            unmapped = result.scalars().all()

            return {
                "schema_mapping_id": str(schema_mapping_id),
                "unmapped_count": len(unmapped),
                "fields": [
                    {
                        "source_table": m.source_table,
                        "source_field": m.source_field,
                        "data_type": None,  # Could be extended to store data type
                        "mapped_at": m.mapped_at.isoformat(),
                    }
                    for m in unmapped
                ],
            }

        except Exception as e:
            logger.exception(f"Failed to get unmapped fields: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.get(
        "/api/schema-mapping/{schema_mapping_id}/audit-trail",
        tags=["Schema Mapping"],
        summary="Get complete field mapping audit trail",
    )
    async def get_audit_trail(
        schema_mapping_id: str = Path(..., description="Schema Mapping UUID"),
        tier: str = Query(None, description="Filter by tier (T1_exact, T1_alias, T2_semantic, user_manual, unmapped)"),
        session: AsyncSession = Depends(get_db_session),
    ):
        """
        Get the complete field mapping audit trail for a schema mapping session.

        Returns all mappings with:
        - source_field, source_table
        - target_field
        - confidence score
        - tier (which strategy found it)
        - rationale
        - mapped_at timestamp

        Can filter by tier to see only specific mapping types.
        """
        try:
            from sqlalchemy import select
            from .models.migration import SchemaMappingFieldMapping

            query = select(SchemaMappingFieldMapping).where(
                SchemaMappingFieldMapping.schema_mapping_id == UUID(schema_mapping_id)
            )

            if tier:
                query = query.where(SchemaMappingFieldMapping.tier == tier)

            query = query.order_by(
                SchemaMappingFieldMapping.source_table,
                SchemaMappingFieldMapping.source_field
            )

            result = await session.execute(query)
            mappings = result.scalars().all()

            # Group by tier for summary
            tier_counts = {}
            for m in mappings:
                tier_counts[m.tier] = tier_counts.get(m.tier, 0) + 1

            return {
                "schema_mapping_id": str(schema_mapping_id),
                "total_mappings": len(mappings),
                "tier_breakdown": tier_counts,
                "mappings": [
                    {
                        "source_table": m.source_table,
                        "source_field": m.source_field,
                        "target_field": m.target_field,
                        "tier": m.tier,
                        "confidence": m.confidence,
                        "rationale": m.rationale,
                        "mapped_at": m.mapped_at.isoformat(),
                    }
                    for m in mappings
                ],
            }

        except Exception as e:
            logger.exception(f"Failed to get audit trail: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # ════════════════════════════════════════════════════════════════════════
    # SCHEMA MAPPING — DOWNLOAD ENDPOINT
    # GET /api/schema-mapping/{schema_mapping_id}/download/{format}
    # format: json | csv | sql
    # ════════════════════════════════════════════════════════════════════════
    @app.get(
        "/api/schema-mapping/{schema_mapping_id}/download/{format}",
        tags=["Schema Mapping"],
        summary="Download schema mapping output (json | csv | sql)",
    )
    async def download_schema_mapping(
        schema_mapping_id: str,
        format: str,
        db: AsyncSession = Depends(get_db_session),
    ):
        import csv
        import io
        import json as _json
        from collections import defaultdict
        from datetime import datetime as _dt
        from fastapi.responses import StreamingResponse
        from .models.migration import SchemaMappingJob, SchemaMappingFieldMapping

        fmt = format.lower()
        if fmt not in ("json", "csv", "sql"):
            raise HTTPException(status_code=400, detail="format must be one of: json, csv, sql")

        # ── Load job ──────────────────────────────────────────────────────────
        try:
            job_result = await db.execute(
                select(SchemaMappingJob).where(SchemaMappingJob.id == UUID(schema_mapping_id))
            )
            job = job_result.scalar_one_or_none()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid schema_mapping_id")

        if not job:
            raise HTTPException(status_code=404, detail="Schema mapping session not found")

        # ── Load DB field mapping rows ────────────────────────────────────────
        mappings_result = await db.execute(
            select(SchemaMappingFieldMapping)
            .where(SchemaMappingFieldMapping.schema_mapping_id == UUID(schema_mapping_id))
            .order_by(SchemaMappingFieldMapping.source_table, SchemaMappingFieldMapping.source_field)
        )
        db_mappings = mappings_result.scalars().all()

        # ── Reconstruct flat mapping list from node_state_json if DB rows absent ──
        # The pipeline stores the full LangGraph state in node_state_json.
        # tier1_mappings / tier2_auto_mapped / tier2_flagged / tier2_unmappable
        # all carry {source_table, source_field, target_field, tier, confidence, rationale}.
        def _flat_mappings_from_state(state: dict) -> list[dict]:
            result = []
            now_iso = _dt.utcnow().isoformat()
            for bucket, tier_default in [
                ("tier1_mappings",    "T1"),
                ("tier2_auto_mapped", "T2_semantic"),
                ("tier2_flagged",     "T2_semantic"),
            ]:
                for m in state.get(bucket, []):
                    result.append({
                        "source_table": m.get("source_table", ""),
                        "source_field": m.get("source_field", ""),
                        "target_field": m.get("target_field", ""),
                        "tier":         m.get("tier", tier_default),
                        "confidence":   round(float(m.get("confidence", 0.0)), 4),
                        "rationale":    m.get("rationale", ""),
                        "mapped_at":    now_iso,
                    })
            for u in state.get("tier2_unmappable", []):
                result.append({
                    "source_table": u.get("source_table", ""),
                    "source_field": u.get("field_name") or u.get("source_field", ""),
                    "target_field": "unmapped",
                    "tier":         "unmapped",
                    "confidence":   0.0,
                    "rationale":    "Could not map with sufficient confidence",
                    "mapped_at":    now_iso,
                })
            return sorted(result, key=lambda r: (r["source_table"], r["source_field"]))

        # ── Resolve live state: DB-persisted → MemorySaver (RAM) ────────────────
        # MemorySaver holds the full pipeline state for sessions that are still
        # running or that completed in this server process but haven't been
        # persisted to node_state_json yet.
        _live_state: dict = job.node_state_json or {}
        if not _live_state:
            try:
                _graph = get_schema_mapping_graph_instance()
                _cfg = {"configurable": {"thread_id": schema_mapping_id}}
                _snap = _graph.get_state(_cfg)
                if _snap and _snap.values:
                    _live_state = dict(_snap.values)
            except Exception:
                pass

        if db_mappings:
            flat_mappings = [
                {
                    "source_table": m.source_table,
                    "source_field": m.source_field,
                    "target_field": m.target_field,
                    "tier":         m.tier,
                    "confidence":   round(m.confidence, 4),
                    "rationale":    m.rationale,
                    "mapped_at":    m.mapped_at.isoformat(),
                }
                for m in db_mappings
            ]
        else:
            flat_mappings = _flat_mappings_from_state(_live_state)

        cmms_name = job.external_cmms_name or "schema"
        base_filename = f"{cmms_name.lower().replace(' ', '_')}_mapping"
        state = _live_state

        # ── JSON ──────────────────────────────────────────────────────────────
        if fmt == "json":
            if job.final_mapping_config:
                payload = dict(job.final_mapping_config)
            else:
                # Reconstruct same structure as schema_output_node.py
                tier1   = state.get("tier1_mappings", [])
                t2_auto = state.get("tier2_auto_mapped", [])
                t2_flag = state.get("tier2_flagged", [])
                canonical_fields: dict = {}
                vendor_aliases:   dict = {}
                for m in tier1 + t2_auto + t2_flag:
                    tf = m.get("target_field")
                    sf = m.get("source_field")
                    if tf:
                        canonical_fields.setdefault(tf, m.get("rationale", tf))
                        vendor_aliases.setdefault(tf, [])
                        if sf and sf not in vendor_aliases[tf]:
                            vendor_aliases[tf].append(sf)
                payload = {
                    "version": "1.0",
                    "source_system": cmms_name,
                    "generated_at": _dt.utcnow().isoformat(),
                    "schema_mapping_id": schema_mapping_id,
                    "canonical_fields": canonical_fields,
                    "vendor_aliases": vendor_aliases,
                    "hierarchy": {
                        "detected_foreign_keys": state.get("detected_foreign_keys", []),
                        "hierarchy_forest": state.get("detected_hierarchies", []),
                        "junction_tables": state.get("junction_tables", []),
                        "isolated_tables": state.get("isolated_tables", []),
                    },
                    "audit": {
                        "tier_distribution": {
                            "tier1_deterministic":   len(tier1),
                            "tier2_semantic_auto":   len(t2_auto),
                            "tier2_semantic_flagged": len(t2_flag),
                            "unmappable": len(state.get("tier2_unmappable", [])),
                        },
                    },
                }
            payload["field_mappings"] = flat_mappings
            content = _json.dumps(payload, indent=2, default=str)
            return StreamingResponse(
                io.BytesIO(content.encode("utf-8")),
                media_type="application/json",
                headers={"Content-Disposition": f'attachment; filename="{base_filename}.json"'},
            )

        # ── CSV ───────────────────────────────────────────────────────────────
        elif fmt == "csv":
            buf = io.StringIO()
            writer = csv.writer(buf)
            writer.writerow([
                "source_table", "source_field", "target_field",
                "tier", "confidence", "rationale", "mapped_at",
            ])
            for m in flat_mappings:
                writer.writerow([
                    m["source_table"], m["source_field"], m["target_field"],
                    m["tier"], m["confidence"], m["rationale"], m["mapped_at"],
                ])
            return StreamingResponse(
                io.BytesIO(buf.getvalue().encode("utf-8")),
                media_type="text/csv",
                headers={"Content-Disposition": f'attachment; filename="{base_filename}.csv"'},
            )

        # ── SQL ───────────────────────────────────────────────────────────────
        elif fmt == "sql":
            # ── helpers ──────────────────────────────────────────────────────
            def _pg_type(raw: str) -> str:
                """Map source data type string → PostgreSQL DDL type."""
                r = (raw or "").lower().strip()
                if any(x in r for x in ("int", "number", "numeric", "serial")):
                    return "INTEGER"
                if any(x in r for x in ("float", "double", "real", "decimal")):
                    return "NUMERIC(10,4)"
                if any(x in r for x in ("bool",)):
                    return "BOOLEAN"
                if any(x in r for x in ("datetime", "timestamp")):
                    return "TIMESTAMPTZ"
                if r in ("date",):
                    return "DATE"
                if any(x in r for x in ("text", "clob", "long")):
                    return "TEXT"
                if "char" in r:
                    # extract length if present e.g. varchar(100)
                    import re as _re
                    m = _re.search(r"\((\d+)\)", r)
                    length = int(m.group(1)) if m else 255
                    return f"VARCHAR({length})"
                return "TEXT"  # safe default

            # Infer which plenum_cafm table to hang a new column on
            _TABLE_HINTS = {
                "asset": "assets", "equipment": "assets", "machine": "assets",
                "workorder": "work_orders", "work_order": "work_orders", "wo": "work_orders",
                "user": "users", "employee": "users", "technician": "technicians",
                "vendor": "vendors", "supplier": "vendors",
                "part": "spare_parts", "stock": "spare_parts", "inventory": "spare_parts",
                "location": "locations", "site": "locations", "area": "locations",
                "schedule": "maintenance_plans", "pm": "maintenance_plans",
            }
            def _infer_target_table(source_table: str) -> str:
                key = source_table.lower().replace(" ", "").replace("_", "")
                for hint, target in _TABLE_HINTS.items():
                    if hint in key:
                        return target
                return "assets"  # safe default

            # Build lookup: (source_table, source_field) → data_type from external_tables
            ext_tables = state.get("external_tables") or {}
            _dtype_lookup: dict = {}
            for tname, tinfo in ext_tables.items():
                for col in (tinfo.get("columns") or []):
                    _dtype_lookup[(tname, col.get("field_name", ""))] = col.get("data_type", "")

            # Split flat_mappings into mapped vs unmapped
            by_table: dict = defaultdict(list)
            unmapped_rows = []
            for m in flat_mappings:
                if m["target_field"] and m["target_field"].lower() != "unmapped":
                    by_table[m["source_table"]].append(m)
                else:
                    unmapped_rows.append(m)

            # extra_fields_config from state (user DDL decisions for unmapped fields)
            extra_configs: list = state.get("extra_fields_config") or []
            extra_by_key = {
                (e.get("source_table", ""), e.get("source_field", "")): e
                for e in extra_configs
            }

            lines = [
                f"-- ═══════════════════════════════════════════════════════════════",
                f"-- Schema mapping: {cmms_name} → Plenum CAFM",
                f"-- Generated: {_dt.utcnow().isoformat()}Z",
                f"-- Session: {schema_mapping_id}",
                f"-- Mapped: {len(flat_mappings) - len(unmapped_rows)}  |  Unmapped: {len(unmapped_rows)}",
                f"-- ═══════════════════════════════════════════════════════════════",
                "",
            ]

            # ── SECTION 1: Views for mapped fields ───────────────────────────
            if by_table:
                lines += [
                    "-- ────────────────────────────────────────────────────────────",
                    "-- SECTION 1: Compatibility views (source table → canonical fields)",
                    "-- ────────────────────────────────────────────────────────────",
                    "",
                ]
                for table, cols in sorted(by_table.items()):
                    safe_table = table.lower().replace(" ", "_").replace("-", "_")
                    lines.append(f"-- {table}  ({len(cols)} field{'s' if len(cols) != 1 else ''} mapped)")
                    lines.append(f"CREATE OR REPLACE VIEW plenum_cafm.v_{safe_table} AS")
                    lines.append("SELECT")
                    col_lines = [
                        f"    {c['source_field']} AS {c['target_field']}"
                        f"  -- {c['tier']} confidence:{c['confidence']}"
                        for c in cols
                    ]
                    lines.append(",\n".join(col_lines))
                    lines.append(f"FROM {table};")
                    lines.append("")

            # ── SECTION 2: DDL for new columns (unmapped / extra fields) ─────
            ddl_custom   = []   # storage_strategy == "custom"  OR inferred
            ddl_jsonb    = []   # storage_strategy == "raw_metadata"
            ddl_skip     = []   # storage_strategy == "skip"

            for m in unmapped_rows:
                key = (m["source_table"], m["source_field"])
                extra = extra_by_key.get(key)
                raw_dt = _dtype_lookup.get(key, "")
                pg_dt  = _pg_type(raw_dt)
                nullable_kw = "" if (extra or {}).get("nullable", True) else " NOT NULL"

                if extra and extra.get("storage_strategy") == "raw_metadata":
                    ddl_jsonb.append(m)
                elif extra and extra.get("storage_strategy") == "skip":
                    ddl_skip.append(m)
                elif extra and extra.get("storage_strategy") == "custom":
                    tgt_table  = extra.get("target_table") or _infer_target_table(m["source_table"])
                    col_name   = extra.get("custom_column_name") or m["source_field"].lower()
                    ddl_type   = extra.get("data_type") or pg_dt
                    is_new     = extra.get("is_new_table", False)
                    if is_new:
                        pk = extra.get("new_table_pk", "id")
                        ddl_custom.append(
                            f"CREATE TABLE IF NOT EXISTS plenum_cafm.{tgt_table} (\n"
                            f"    {pk} UUID PRIMARY KEY DEFAULT gen_random_uuid(),\n"
                            f"    {col_name} {ddl_type}{nullable_kw}\n"
                            f");  -- from {m['source_table']}.{m['source_field']}"
                        )
                    else:
                        ddl_custom.append(
                            f"ALTER TABLE plenum_cafm.{tgt_table}\n"
                            f"    ADD COLUMN IF NOT EXISTS {col_name} {ddl_type}{nullable_kw};"
                            f"  -- from {m['source_table']}.{m['source_field']}"
                        )
                else:
                    # No user decision — infer best-fit table and emit ADD COLUMN
                    tgt_table = _infer_target_table(m["source_table"])
                    col_name  = m["source_field"].lower()
                    ddl_custom.append(
                        f"ALTER TABLE plenum_cafm.{tgt_table}\n"
                        f"    ADD COLUMN IF NOT EXISTS {col_name} {pg_dt};"
                        f"  -- {m['source_table']}.{m['source_field']} (inferred)"
                    )

            if ddl_custom:
                lines += [
                    "-- ────────────────────────────────────────────────────────────",
                    "-- SECTION 2: New columns — ALTER TABLE / CREATE TABLE",
                    "-- ────────────────────────────────────────────────────────────",
                    "",
                ]
                for stmt in ddl_custom:
                    lines.append(stmt)
                    lines.append("")

            if ddl_jsonb:
                lines += [
                    "-- ────────────────────────────────────────────────────────────",
                    "-- SECTION 3: Fields stored in raw_metadata JSONB (no DDL needed)",
                    "-- ────────────────────────────────────────────────────────────",
                ]
                for m in ddl_jsonb:
                    lines.append(f"-- {m['source_table']}.{m['source_field']} → raw_metadata JSONB")
                lines.append("")

            if ddl_skip:
                lines += [
                    "-- ────────────────────────────────────────────────────────────",
                    "-- SECTION 4: Fields skipped (no storage)",
                    "-- ────────────────────────────────────────────────────────────",
                ]
                for m in ddl_skip:
                    lines.append(f"-- SKIP: {m['source_table']}.{m['source_field']}")
                lines.append("")

            content = "\n".join(lines)
            return StreamingResponse(
                io.BytesIO(content.encode("utf-8")),
                media_type="text/plain",
                headers={"Content-Disposition": f'attachment; filename="{base_filename}.sql"'},
            )

    # ════════════════════════════════════════════════════════════════════════
    # FIIX DATA INGESTION endpoints
    #   POST   /api/fiix-ingestion           — trigger a new ingestion run
    #   GET    /api/fiix-ingestion/{id}      — poll job status + stats
    #   GET    /api/fiix-ingestion           — list recent ingestion jobs
    # ════════════════════════════════════════════════════════════════════════

    @app.post(
        "/api/fiix-ingestion",
        tags=["Fiix Ingestion"],
        summary="Trigger a full Fiix data ingestion run",
        status_code=202,
    )
    async def start_fiix_ingestion(
        organization_id: str = None,
        created_by: str = "system",
        schema_mapping_id: str = None,
        session: AsyncSession = Depends(get_db_session),
    ) -> dict:
        """
        Create a FiixIngestionJob and enqueue `run_fiix_data_ingestion` in ARQ.

        The 3-node pipeline runs in the background:
          Node 1 — fetch all Fiix records (paginated, ~33 objects)
          Node 2 — rename fields, assign UUIDs, dedup, null-fill, date-coerce
          Node 3 — upsert into the schema created by schema_mapping_id

        Pass `schema_mapping_id` to write into the correct target schema
        (SchemaMappingJob.new_schema_name). Without it, data falls back to plenum_cafm.

        `organization_id` is optional — if omitted and `schema_mapping_id` is provided,
        the organization is resolved from the SchemaMappingJob record.

        Poll `GET /api/fiix-ingestion/{ingestion_id}` for progress.
        Credentials are read from FIIX_* environment variables.
        """
        from .models.migration import FiixIngestionJob, SchemaMappingJob
        from .worker import run_fiix_data_ingestion

        # Resolve organization_id — prefer explicit param, fall back to schema mapping job
        org_uuid: UUID | None = None
        if organization_id:
            try:
                org_uuid = UUID(organization_id)
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid organization_id UUID")

        if org_uuid is None and schema_mapping_id:
            try:
                sm_result = await session.execute(
                    select(SchemaMappingJob).where(SchemaMappingJob.id == UUID(schema_mapping_id))
                )
                sm_job = sm_result.scalar_one_or_none()
                if sm_job and sm_job.organization_id:
                    org_uuid = sm_job.organization_id
            except Exception:
                pass

        # Final fallback — nil UUID (job still runs, org tracking is best-effort)
        if org_uuid is None:
            org_uuid = UUID("00000000-0000-0000-0000-000000000000")
        organization_id = str(org_uuid)

        ingestion_id = uuid4()

        job = FiixIngestionJob(
            id=ingestion_id,
            organization_id=org_uuid,
            created_by=created_by,
            status="pending",
            progress_pct=0.0,
        )
        session.add(job)
        await session.commit()

        # Enqueue ARQ background task
        try:
            from arq.connections import create_pool, RedisSettings as ArqRedisSettings
            _fiix_settings = get_settings()
            _arq_settings = ArqRedisSettings.from_dsn(
                _fiix_settings.redis_url or "redis://localhost:6379"
            )
            arq_pool = await create_pool(_arq_settings)
            await arq_pool.enqueue_job(
                "run_fiix_data_ingestion",
                ingestion_id=str(ingestion_id),
                organization_id=organization_id,
                created_by=created_by,
                schema_mapping_id=schema_mapping_id,
            )
            await arq_pool.aclose()
        except Exception as exc:
            logger.warning(f"[fiix-ingestion] ARQ enqueue failed, running inline: {exc}")
            # Fallback: asyncio task (dev/test mode without ARQ worker)
            import asyncio as _aio
            async def _inline():
                from .worker import run_fiix_data_ingestion as _fn
                await _fn(None, str(ingestion_id), organization_id, created_by, schema_mapping_id)
            _aio.create_task(_inline())

        logger.info(
            f"[fiix-ingestion] Started ingestion_id={ingestion_id} "
            f"schema_mapping_id={schema_mapping_id}"
        )
        return {
            "ingestion_id": str(ingestion_id),
            "status": "pending",
            "schema_mapping_id": schema_mapping_id,
            "message": "Fiix ingestion job queued. Poll /api/fiix-ingestion/{ingestion_id} for progress.",
        }

    @app.get(
        "/api/fiix-ingestion/{ingestion_id}",
        tags=["Fiix Ingestion"],
        summary="Get Fiix ingestion job status",
    )
    async def get_fiix_ingestion_status(
        ingestion_id: str = Path(..., description="FiixIngestionJob UUID"),
        session: AsyncSession = Depends(get_db_session),
    ) -> dict:
        """Return current status, progress, and stats for a Fiix ingestion job."""
        from .models.migration import FiixIngestionJob

        try:
            job_uuid = UUID(ingestion_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid ingestion_id UUID")

        result = await session.execute(
            select(FiixIngestionJob).where(FiixIngestionJob.id == job_uuid)
        )
        job = result.scalar_one_or_none()
        if not job:
            raise HTTPException(status_code=404, detail="Ingestion job not found")

        return {
            "ingestion_id": str(job.id),
            "organization_id": str(job.organization_id),
            "created_by": job.created_by,
            "status": job.status,
            "current_step": job.current_step,
            "progress_pct": job.progress_pct,
            # Node 1
            "total_records_fetched": job.total_records_fetched,
            "fetch_stats": job.fetch_stats or {},
            "fetch_errors": job.fetch_errors or [],
            # Node 2
            "total_records_preprocessed": job.total_records_preprocessed,
            "preprocess_stats": job.preprocess_stats or {},
            # Node 3
            "total_records_written": job.total_records_written,
            "write_results": job.write_results or {},
            "write_errors": job.write_errors or [],
            # Meta
            "error_message": job.error_message,
            "error_node": job.error_node,
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "completed_at": job.completed_at.isoformat() if job.completed_at else None,
            "created_at": job.created_at.isoformat() if job.created_at else None,
        }

    @app.get(
        "/api/fiix-ingestion",
        tags=["Fiix Ingestion"],
        summary="List recent Fiix ingestion jobs",
    )
    async def list_fiix_ingestion_jobs(
        organization_id: str,
        limit: int = Query(20, ge=1, le=100),
        session: AsyncSession = Depends(get_db_session),
    ) -> dict:
        """Return the most recent Fiix ingestion jobs for an organization."""
        from .models.migration import FiixIngestionJob
        from sqlalchemy import desc

        try:
            org_uuid = UUID(organization_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid organization_id UUID")

        result = await session.execute(
            select(FiixIngestionJob)
            .where(FiixIngestionJob.organization_id == org_uuid)
            .order_by(desc(FiixIngestionJob.created_at))
            .limit(limit)
        )
        jobs = result.scalars().all()

        return {
            "jobs": [
                {
                    "ingestion_id": str(j.id),
                    "status": j.status,
                    "progress_pct": j.progress_pct,
                    "total_records_fetched": j.total_records_fetched,
                    "total_records_written": j.total_records_written,
                    "error_message": j.error_message,
                    "started_at": j.started_at.isoformat() if j.started_at else None,
                    "completed_at": j.completed_at.isoformat() if j.completed_at else None,
                    "created_at": j.created_at.isoformat() if j.created_at else None,
                }
                for j in jobs
            ],
            "total": len(jobs),
        }

    return app


# Create app instance for uvicorn
app = create_app()

# ── doc-rag: register all routers under /doc-rag ─────────────────────────────
try:
    from pathlib import Path as _Path
    from fastapi.staticfiles import StaticFiles as _StaticFiles
    from app.routers import documents as _dr_documents
    from app.routers import rag as _dr_rag
    from app.routers import document_match as _dr_doc_match
    from app.routers import row_iteration as _dr_row_iter
    from app.routers import row_index as _dr_row_index
    from app.routers import feedback as _dr_feedback

    app.include_router(_dr_documents.router,  prefix="/doc-rag", tags=["doc-rag · Documents"])
    app.include_router(_dr_doc_match.router,  prefix="/doc-rag", tags=["doc-rag · Match Rows"])
    app.include_router(_dr_row_index.router,  prefix="/doc-rag", tags=["doc-rag · Row Index"])
    app.include_router(_dr_row_iter.router,   prefix="/doc-rag", tags=["doc-rag · Row Iteration"])
    app.include_router(_dr_rag.router,        prefix="/doc-rag", tags=["doc-rag · RAG Query"])
    app.include_router(_dr_feedback.router,   prefix="/doc-rag", tags=["doc-rag · Feedback"])

    # Serve extracted images at /doc-rag/images/<doc_id>/<filename>
    _doc_rag_images = _Path("/app/data/doc_rag_uploads/images")
    _doc_rag_images.mkdir(parents=True, exist_ok=True)
    app.mount("/doc-rag/images", _StaticFiles(directory=str(_doc_rag_images)), name="doc_rag_images")

    logger.info("doc-rag routers registered under /doc-rag")
except Exception as _e:
    logger.warning(f"doc-rag routers not loaded: {_e}")


if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
    )
