"""
conftest.py — pytest configuration for svc-ingestion tests.

Adds src/ to sys.path so all test modules can import from agents/, shared/, etc.
Adds shared-lib/ so cafm_shared is importable without pip install.
Sets up OTel no-op tracer to prevent span errors in unit tests.
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent

# svc-ingestion src
sys.path.insert(0, str(Path(__file__).parent / "src"))
# shared-lib (cafm_shared package — bypass Python version check)
sys.path.insert(0, str(REPO_ROOT / "shared-lib"))
# cafm-connector-service src (for models, exceptions, etc.)
sys.path.insert(0, str(REPO_ROOT / "cafm-connector-service" / "src"))

# ── Stub out OTel so unit tests don't need a live Tempo endpoint ──────────────
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry import trace

trace.set_tracer_provider(TracerProvider())

# ── Stub out structlog so tests don't need full logging config ────────────────
import structlog

structlog.configure(
    processors=[structlog.dev.ConsoleRenderer()],
    logger_factory=structlog.PrintLoggerFactory(),
)
