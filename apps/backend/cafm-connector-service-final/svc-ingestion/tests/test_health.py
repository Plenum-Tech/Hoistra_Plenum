"""
svc-ingestion/tests/test_health.py

Smoke test — verifies the app starts and /health responds correctly.
Does not require running infrastructure (OTel endpoint, DB, Redis).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

os.environ.setdefault("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")

_SRC = str(Path(__file__).parent.parent / "src")


def _load_ingestion_app():
    """
    Load svc-ingestion's FastAPI app without permanently modifying sys.path.

    Temporarily moves svc-ingestion/src to the front, clears the 'app'
    module cache, imports it, then restores sys.path — so svc-query tests
    that run afterwards are unaffected.
    """
    # Snapshot current path
    original_path = list(sys.path)

    # Put our src first (without removing other entries)
    sys.path = [_SRC] + [p for p in sys.path if p != _SRC]

    # Evict stale 'app' so Python re-imports from the correct src
    for k in list(sys.modules):
        if k == "app" or k.startswith("app."):
            del sys.modules[k]

    try:
        from app import app  # noqa: PLC0415
        return app
    finally:
        # Restore sys.path exactly as it was
        sys.path[:] = original_path


@pytest.mark.asyncio
async def test_health_returns_ok() -> None:
    app = _load_ingestion_app()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/health")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["service"] == "cafm-ingestion-service"


@pytest.mark.asyncio
async def test_metrics_endpoint_exists() -> None:
    app = _load_ingestion_app()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/metrics")

    assert response.status_code == 200
