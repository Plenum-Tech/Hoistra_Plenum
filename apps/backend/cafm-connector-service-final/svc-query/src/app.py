"""
svc-query/src/app.py

CAFM Query Service — FastAPI application factory.

Sprint 2 Phase 1: Health check + telemetry only.
Query routes (intent classifier, tier 1/2/3, output renderer) are added in Phase 4.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from cafm_shared.logging import configure_logging, get_logger
from cafm_shared.telemetry import configure_telemetry
from config import get_settings

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    settings = get_settings()

    # 1. Telemetry FIRST
    configure_telemetry(service_name="cafm-query-service", app=app)

    # 2. Logging SECOND
    configure_logging(debug=settings.debug)

    logger.info(
        "cafm_query_service_started",
        env=settings.environment,
        port=settings.port,
        otel_endpoint=settings.otel_exporter_otlp_endpoint,
    )

    yield

    logger.info("cafm_query_service_stopped")


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="CAFM Query Service",
        description=(
            "Sprint 2 — Natural language query layer over plenum_cafm. "
            "3-tier routing: Tier 1 (SQL) / Tier 2 (Blob+Claude) / Tier 3 (pgvector)."
        ),
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health", tags=["Health"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "cafm-query-service"}

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app


app = create_app()
