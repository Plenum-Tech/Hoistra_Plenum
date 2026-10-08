"""POST /api/workflow/investigation-summary: signed in like /chat, and a pass-through to the
summariser with the service's own key — it reads no database and records no chat turn."""
from __future__ import annotations

import pytest

try:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from src.api.routes import workflow
    from src.limiter import limiter
    from src.services.principal import current_principal
except Exception as _exc:  # noqa: BLE001 — a bare dev environment cannot import the router
    workflow = None
    _import_error = _exc


pytestmark = pytest.mark.skipif(workflow is None, reason="the workflow router could not be imported here")


def _app():
    app = FastAPI()
    app.state.limiter = limiter
    app.include_router(workflow.router)
    return app


def test_it_needs_a_signed_in_caller():
    r = TestClient(_app()).post("/api/workflow/investigation-summary", json={"asset": {}, "sources": {}})
    assert r.status_code in (401, 403)


def test_it_hands_the_page_data_to_the_summariser_with_the_services_key(monkeypatch):
    from src.agents import investigation_summary as isum

    seen = {}

    async def _summarise(**kw):
        seen.update(kw)
        return {"ok": True, "lines": [{"source": "weather", "text": "t"}], "overall": "", "dropped": [],
                "meta": {"model": kw["model"]}}

    monkeypatch.setattr(isum, "summarise", _summarise)
    monkeypatch.setattr(workflow.settings, "anthropic_api_key", "sk-test", raising=False)
    app = _app()
    app.dependency_overrides[current_principal] = lambda: object()
    body = {"asset": {"name": "Boiler 1"}, "sources": {"weather": {"status": "found"}}}
    r = TestClient(app).post("/api/workflow/investigation-summary", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    assert seen["asset"] == {"name": "Boiler 1"} and seen["sources"] == body["sources"]
    assert seen["api_key"] == "sk-test" and seen["model"] == isum.DEFAULT_MODEL
