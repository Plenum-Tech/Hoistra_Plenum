"""
Centralised structlog configuration for svc-work-order-management.
Call configure_logging() once at app startup (lifespan).

Development  → coloured ConsoleRenderer  (human-readable)
Production   → JSONRenderer              (machine-parseable for log aggregators)
"""
import logging
import os
import sys

import structlog


def configure_logging() -> None:
    debug = os.getenv("DEBUG", "false").lower() == "true"
    level = logging.DEBUG if debug else logging.INFO

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=level,
    )

    shared_processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.ExceptionRenderer(),
    ]

    is_prod = os.getenv("ENVIRONMENT", "development").lower() == "production"

    structlog.configure(
        processors=shared_processors + (
            [structlog.processors.JSONRenderer()]
            if is_prod
            else [structlog.dev.ConsoleRenderer()]
        ),
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str = __name__):
    return structlog.get_logger().bind(logger=name)
