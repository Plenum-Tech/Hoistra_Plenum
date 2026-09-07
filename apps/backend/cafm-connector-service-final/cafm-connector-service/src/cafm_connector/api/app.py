"""
CAFM Connector Service — FastAPI application factory.

On startup:
  1. Auto-discover and register all 12 connector plugins
  2. Run DB migrations (Alembic)
  3. Start Redis → WebSocket relay background task

Endpoints mounted at /api/v1:
  - /connectors/*
  - /imports/*

Sub-applications:
  - /table-editor  (Table Editor — browse/edit any plenum_cafm table)
    Docs → /table-editor/docs

WebSocket at:
  - /ws/imports/progress
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from cafm_connector.api.routes.connectors import router as connectors_router
from cafm_connector.api.routes.plenum_cafm import plenum_router
from cafm_connector.api.routes.plenum_cafm.table_customizer import table_editor_inner
from cafm_connector.api.websocket import start_redis_listener, websocket_import_progress
from cafm_connector.connectors.registry import ConnectorRegistry
from cafm_connector.core.config import get_settings
from cafm_connector.core.exceptions import CAFMError, JobNotFoundError
from cafm_shared.logging import configure_logging, get_logger
from cafm_shared.telemetry import configure_telemetry

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    settings = get_settings()

    # Telemetry FIRST — sets global TracerProvider + MeterProvider,
    # mounts /metrics, wires auto-instrumentation before any requests arrive.
    configure_telemetry(service_name="cafm-connector-service", app=app)

    # Logging SECOND — so add_otel_context can find the active TracerProvider.
    configure_logging(debug=settings.debug)

    # ── Startup ───────────────────────────────────────────────────
    # 1. Discover all connector plugins via entry_points
    registry = ConnectorRegistry()
    registry.discover_plugins()
    logger.info("connectors_registered", types=registry.list_registered())

    # 2. Run DB migrations
    try:
        from alembic import command
        from alembic.config import Config as AlembicConfig
        alembic_cfg = AlembicConfig("alembic.ini")
        command.upgrade(alembic_cfg, "head")
        logger.info("db_migrations_applied")
    except Exception:
        logger.warning("db_migrations_skipped_no_alembic_ini")

    # 3. Start Redis → WebSocket relay
    relay_task = asyncio.create_task(start_redis_listener())
    logger.info("app_started", env=settings.environment, port=settings.port)

    yield

    # ── Shutdown ──────────────────────────────────────────────────
    relay_task.cancel()
    try:
        await relay_task
    except asyncio.CancelledError:
        pass
    logger.info("app_stopped")


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="CAFM Connector Service",
        description=(
            "US-01 Backend — Data Import & Connector Service. "
            "Supports 12 source types: PostgreSQL, MySQL, MSSQL, MongoDB, "
            "CSV, Excel, JSON, XML, Parquet, REST, SOAP, OData."
        ),
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    # ── CORS ──────────────────────────────────────────────────────
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Error handlers ────────────────────────────────────────────
    @app.exception_handler(CAFMError)
    async def cafm_error_handler(request: Request, exc: CAFMError) -> JSONResponse:
        status_map = {
            JobNotFoundError: 404,
        }
        code = status_map.get(type(exc), 400)
        return JSONResponse(
            status_code=code,
            content={"success": False, "error": type(exc).__name__, "detail": str(exc)},
        )

    # ── Routes ────────────────────────────────────────────────────
    app.include_router(connectors_router, prefix=settings.api_prefix)
    app.include_router(plenum_router, prefix=f"{settings.api_prefix}/plenum")

    # ── Table Editor sub-app ──────────────────────────────────────
    # Local dev:  served as a standalone service on port 8005
    # Azure:      reverse proxy routes /table-editor → this mount
    app.mount("/table-editor", table_editor_inner)

    # ── Health check ──────────────────────────────────────────────
    @app.get("/health", tags=["Health"])
    async def health():
        return {"status": "ok", "service": "cafm-connector-service"}

    # ── WebSocket ─────────────────────────────────────────────────
    app.websocket("/ws/imports/progress")(websocket_import_progress)
    app.websocket("/ws/imports/{job_id}/progress")(websocket_import_progress)

    return app


app = create_app()
