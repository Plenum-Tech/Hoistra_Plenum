"""
cafm_shared/logging.py

Structured logging with OTel trace_id / span_id injection.

Every log line produced after configure_logging() is called will include
trace_id and span_id fields when an active OTel span exists. This enables
log-to-trace correlation in Grafana (click a log line → jump to the trace).

Usage:
    from cafm_shared.logging import configure_logging, get_logger

    # In FastAPI lifespan (AFTER configure_telemetry):
    configure_logging(debug=settings.debug)

    # In any module:
    logger = get_logger(__name__)
    logger.info("event_name", key="value", other_key=123)
"""

from __future__ import annotations

import logging
from typing import Any

import structlog
from opentelemetry import trace


def add_otel_context(
    logger: Any,  # noqa: ANN401
    method: str,
    event_dict: dict[str, Any],
) -> dict[str, Any]:
    """
    structlog processor that injects the active OTel trace_id and span_id
    into every log record.

    When no span is active (e.g. during startup before the first request),
    the keys are omitted so log lines outside a trace context are not
    cluttered with zero values.
    """
    span = trace.get_current_span()
    ctx = span.get_span_context()
    if ctx.is_valid:
        event_dict["trace_id"] = format(ctx.trace_id, "032x")
        event_dict["span_id"] = format(ctx.span_id, "016x")
        event_dict["trace_sampled"] = ctx.trace_flags.sampled
    return event_dict


def configure_logging(debug: bool = False) -> None:
    """
    Configure structlog for the calling process.

    Call this AFTER configure_telemetry() in the FastAPI lifespan so that
    the OTel global TracerProvider is already set when add_otel_context runs.

    Args:
        debug: If True, use human-readable ConsoleRenderer.
               If False (production), use machine-readable JSONRenderer.
    """
    log_level = logging.DEBUG if debug else logging.INFO

    logging.basicConfig(
        format="%(message)s",
        level=log_level,
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            add_otel_context,  # inject trace_id / span_id from active OTel span
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            (
                structlog.dev.ConsoleRenderer()
                if debug
                else structlog.processors.JSONRenderer()
            ),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a bound structlog logger for the given module name."""
    return structlog.get_logger(name)
