"""
cafm_shared/telemetry.py

Call configure_telemetry(service_name, app) once, at the very start of the
FastAPI lifespan (before yield and before configure_logging).

This sets the global TracerProvider and MeterProvider, then applies
auto-instrumentation for FastAPI, SQLAlchemy, Redis, httpx, and asyncpg.
It also mounts /metrics on the FastAPI app for Prometheus scraping.

Service names:
    "cafm-connector-service"
    "cafm-ingestion-service"
    "cafm-query-service"
"""

from __future__ import annotations

import os

from fastapi import FastAPI
from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.exporter.prometheus import PrometheusMetricReader
from opentelemetry.instrumentation.asyncpg import AsyncPGInstrumentor
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest


def configure_telemetry(service_name: str, app: FastAPI) -> None:
    """
    Initialise OTel tracing + metrics for a single service process.

    Args:
        service_name: The OTel 'service.name' resource attribute.
        app:          The FastAPI application instance — used for auto-instrumentation
                      and to mount the /metrics Prometheus endpoint.

    Notes:
        - OTLPSpanExporter uses insecure=True (plain gRPC over Docker network).
          Change to insecure=False for TLS in production.
        - PrometheusMetricReader does NOT start its own HTTP server. Metrics are
          served via the /metrics mount on the same FastAPI port.
        - SQLAlchemyInstrumentor instruments any engine created AFTER this call.
          Always call configure_telemetry() before creating DB engines.
    """
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
    environment = os.environ.get("ENVIRONMENT", "development")
    service_version = os.environ.get("SERVICE_VERSION", "0.1.0")
    # Opt-out for environments without a collector (e.g. the single-URL local
    # compose has no Tempo). Set OTEL_SDK_DISABLED=true to skip the OTLP span
    # exporter — tracers still work as no-ops, and Prometheus metrics +
    # auto-instrumentation are unaffected. Default (unset) keeps export ON.
    otel_disabled = os.environ.get("OTEL_SDK_DISABLED", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )

    resource = Resource.create(
        {
            "service.name": service_name,
            "service.version": service_version,
            "deployment.environment": environment,
        }
    )

    # ── Tracing → Grafana Tempo ──────────────────────────────────────────
    tracer_provider = TracerProvider(resource=resource)
    if not otel_disabled:
        otlp_exporter = OTLPSpanExporter(endpoint=endpoint, insecure=True)
        tracer_provider.add_span_processor(BatchSpanProcessor(otlp_exporter))
    trace.set_tracer_provider(tracer_provider)

    # ── Metrics → Prometheus ─────────────────────────────────────────────
    prometheus_reader = PrometheusMetricReader()
    meter_provider = MeterProvider(
        resource=resource,
        metric_readers=[prometheus_reader],
    )
    metrics.set_meter_provider(meter_provider)

    # ── Auto-instrumentation ─────────────────────────────────────────────
    # Order matters: providers must be set before instrumentors are called.

    # HTTP server spans (wraps every FastAPI request)
    FastAPIInstrumentor.instrument_app(app)

    # DB query spans (instruments any engine created after this call)
    SQLAlchemyInstrumentor().instrument(enable_commenter=True, commenter_options={})

    # Redis command spans
    RedisInstrumentor().instrument()

    # Outbound HTTP spans — catches all Claude API calls (made via httpx)
    HTTPXClientInstrumentor().instrument()

    # Raw asyncpg spans — catches bulk COPY operations in CSV/Excel agents
    AsyncPGInstrumentor().instrument()

    # NOTE: /metrics endpoint is mounted by each service's create_app()
    # using prometheus_client.generate_latest() directly — see app.py.
    # This avoids issues with ASGI sub-app routing when mount() is called
    # inside the lifespan rather than at app-creation time.
