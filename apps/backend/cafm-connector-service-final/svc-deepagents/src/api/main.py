"""
BE4 — FastAPI application for svc-deepagents.
Lifespan manages the singleton DeepAgentOrchestrator and DB session factory.

Startup sequence:
  1. Configure structlog (console or JSON based on DEBUG)
  2. Initialise DB session factory (UDR + Compliance tools)
  3. Initialise Postgres HITL checkpointer (if HITL_ENABLED=true)
  4. Build the singleton DeepAgentOrchestrator
  5. Serve on PORT (default 8008)
"""
import os
import time
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import structlog
import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from ..agents.orchestrator import DeepAgentOrchestrator
from ..config import settings
from ..database import init_session_factory
from ..limiter import limiter
from .routes.documents import router as documents_router
from .routes.health import router as health_router
from .routes.ingest_batch import router as ingest_batch_router
from .routes.migration import router as migration_router
from .routes.workflow import router as workflow_router

log = structlog.get_logger(__name__)


def _configure_logging() -> None:
    import logging

    debug = settings.debug
    level = logging.DEBUG if debug else logging.INFO

    shared_processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.ExceptionRenderer(),
    ]

    renderer = structlog.dev.ConsoleRenderer(colors=True) if debug else structlog.processors.JSONRenderer()

    structlog.configure(
        processors=shared_processors + [renderer],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    logging.basicConfig(format="%(message)s", level=level)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    _configure_logging()

    log.info(
        "svc-deepagents.startup",
        model=settings.openai_model,
        port=settings.port,
        hitl_enabled=settings.hitl_enabled,
    )

    # LangSmith tracing — set env vars so LangChain auto-instruments all calls
    if settings.langsmith_tracing and settings.langsmith_api_key:
        os.environ["LANGSMITH_TRACING"] = "true"
        os.environ["LANGSMITH_API_KEY"] = settings.langsmith_api_key
        os.environ["LANGSMITH_PROJECT"] = settings.langsmith_project
        log.info("svc-deepagents.langsmith.enabled", project=settings.langsmith_project)
    else:
        log.info("svc-deepagents.langsmith.disabled")

    # Initialise DB session factory for tools that need direct DB access
    init_session_factory()
    log.info("svc-deepagents.db.ready")

    try:
        from ..services.ingest_batch_service import init_ingest_batch_tables

        await init_ingest_batch_tables()
    except Exception as exc:
        log.warning("svc-deepagents.ingest_batch_tables.failed", error=str(exc)[:300])

    # Initialise HITL Postgres checkpointer (optional)
    checkpointer = None
    _checkpointer_pool = None

    if settings.hitl_enabled:
        try:
            from ..hitl.checkpointer import create_checkpointer
            checkpointer, _checkpointer_pool = await create_checkpointer(settings.db_url)
            log.info("svc-deepagents.hitl.ready")
        except Exception as exc:
            log.warning(
                "svc-deepagents.hitl.init_failed",
                error=str(exc),
                detail="HITL disabled — continuing without checkpointer",
            )
            checkpointer = None
            _checkpointer_pool = None

    # Build the singleton orchestrator
    app.state.orchestrator = DeepAgentOrchestrator(
        openai_api_key=settings.openai_api_key,
        model=settings.openai_model,
        checkpointer=checkpointer,
    )
    log.info("svc-deepagents.orchestrator.ready")

    yield

    # Shutdown — close the checkpointer pool if open
    if _checkpointer_pool is not None:
        try:
            await _checkpointer_pool.close()
            log.info("svc-deepagents.hitl.pool.closed")
        except Exception as exc:
            log.warning("svc-deepagents.hitl.pool.close_error", error=str(exc))

    log.info("svc-deepagents.shutdown")


app = FastAPI(
    title="Plenum CAFM — DeepAgents Orchestration Service",
    description=(
        "Main orchestration layer for the Plenum CAFM platform. "
        "Routes natural language requests across 38 CAFM tools in 6 agent domains. "
        "Supports HITL (human-in-the-loop) approval gates for migration and rollback operations."
    ),
    version="1.1.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:3001", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_timing(request: Request, call_next) -> Response:
    start = time.perf_counter()
    response: Response = await call_next(request)
    elapsed_ms = round((time.perf_counter() - start) * 1000)
    log.info(
        "http.request",
        method=request.method,
        path=request.url.path,
        status=response.status_code,
        elapsed_ms=elapsed_ms,
    )
    return response


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    log.error(
        "http.unhandled_error",
        path=request.url.path,
        exc_type=type(exc).__name__,
        detail=str(exc)[:300],
        exc_info=True,
    )
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


# Routers
app.include_router(health_router)
app.include_router(workflow_router)
app.include_router(ingest_batch_router)
app.include_router(migration_router)
app.include_router(documents_router)


if __name__ == "__main__":
    uvicorn.run(
        "src.api.main:app",
        host="0.0.0.0",
        port=settings.port,
        reload=settings.debug,
        log_config=None,
    )
