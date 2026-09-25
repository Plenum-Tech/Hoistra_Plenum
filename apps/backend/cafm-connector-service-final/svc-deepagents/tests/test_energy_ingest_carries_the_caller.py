"""A meter file uploaded in the chat is ingested as the person who uploaded it.

Measured 22 Sep 2026. Someone attached two half-hourly CSVs and both came back
"Energy ingest failed ... Client error '401 Unauthorized' for url
http://svc-operations-intelligence:8009/api/energy/readings/ingest/csv", while every other
call in the same turn succeeded — /api/ingestion/validate 201, /api/ingestion/extraction-plan
200. The difference was not the route. It was that this one posted with a bare
httpx.AsyncClient and so sent no Authorization header at all, the only ingestion path in the
service that did not go through the shared client which forwards the caller's own token.

operations-intelligence requires a caller on every route and scopes what it returns to that
caller's company and buildings, so a request without one is refused — correctly.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import src.http_client as hc
from src.agents import energy_single_door as esd

CSV = "reading_at,consumption_kwh,period_minutes,mpan\n2025-09-01T00:00:00Z,12.5,30,NB-B-101-E0\n"
TOKEN = "Bearer test-caller-token"


@pytest.fixture()
def meter_file(tmp_path: Path) -> str:
    p = tmp_path / "harbour_point_electricity_halfhourly.csv"
    p.write_text(CSV, encoding="utf-8")
    return str(p)


@pytest.fixture()
def captured(monkeypatch):
    """Record what actually goes on the wire, without one leaving."""
    seen: dict = {}

    class _Resp:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"inserted": 1, "gaps_flagged": [], "created_meters": [], "meters": []}

    class _Client:
        def __init__(self, *a, **k):
            seen["client_kwargs"] = k

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def request(self, method, url, **kwargs):
            seen["method"] = method
            seen["url"] = str(url)
            seen["headers"] = {k.title(): v for k, v in (kwargs.get("headers") or {}).items()}
            seen["files"] = kwargs.get("files")
            seen["data"] = kwargs.get("data")
            return _Resp()

    monkeypatch.setattr(hc.httpx, "AsyncClient", _Client)
    return seen


@pytest.mark.asyncio
async def test_the_upload_is_sent_with_the_callers_own_bearer(meter_file, captured):
    token = hc.caller_authorization.set(TOKEN)
    try:
        out = await esd.route_energy_upload(
            file_path=meter_file, user_query="ingest these meter readings"
        )
    finally:
        hc.caller_authorization.reset(token)

    assert out is not None and out.get("kind") == "meter_readings"
    assert captured["headers"].get("Authorization") == TOKEN, (
        "without this the service answers 401 and the readings are never stored"
    )
    assert captured["url"].endswith("/api/energy/readings/ingest/csv")
    assert captured["files"], "the CSV itself still has to travel"


@pytest.mark.asyncio
async def test_a_write_is_never_retried_into_a_duplicate(meter_file, captured):
    """Readings are written by this call. A retry after a partial write is how the same
    half-hour lands twice, and a duplicate reading is invisible in a way a failure is not."""
    hc.caller_authorization.set(TOKEN)
    calls = {"n": 0}
    real_request = hc.request

    async def counting(method, base_url, path, **kwargs):
        calls["n"] += 1
        assert kwargs.get("max_attempts") == 1, (
            "an ingest must not be retried: max_attempts=%r" % kwargs.get("max_attempts")
        )
        return await real_request(method, base_url, path, **kwargs)

    import src.agents.energy_single_door as mod

    mod._request = counting
    try:
        await mod.route_energy_upload(file_path=meter_file, user_query="ingest these readings")
    finally:
        mod._request = real_request
    assert calls["n"] == 1
