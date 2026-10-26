"""Evaluation spend without a database: UTC calendar months, and GET /api/trace-lab/spend for superadmins only (any
superadmin: the Platform cost page is platform-wide), 503 while Trace Lab is not ready, registered in trace_lab.app."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from src.services.principal import Principal, current_principal
from trace_lab import db, spend

ORG = "11111111-1111-4111-8111-111111111111"


def who(role="superadmin", org=None):
    return Principal(user_id=uuid.uuid4(), email=f"{role}@example.com", organization_id=uuid.UUID(org) if org else None,
                     role=role, can_ingest=True, building_ids=None)


def client(principal, *, ready=True):
    async def fake_ready():
        if not ready:
            raise HTTPException(status_code=503, detail={"reason": "trace_lab_unavailable", "error": "Not ready."})

    app = FastAPI()
    app.include_router(spend.router, prefix="/api/trace-lab")
    app.dependency_overrides[db.require_ready] = fake_ready
    if principal is not None:
        app.dependency_overrides[current_principal] = lambda: principal
    return TestClient(app)


def test_utc_calendar_months():
    label, start, end = spend.month_bounds("2026-12")
    assert (label, start, end) == ("2026-12", datetime(2026, 12, 1, tzinfo=timezone.utc),
                                   datetime(2027, 1, 1, tzinfo=timezone.utc))
    assert spend.month_bounds("2026-02")[2] == datetime(2026, 3, 1, tzinfo=timezone.utc)
    assert spend.month_bounds(None, now=datetime(2026, 10, 15, 23, 0, tzinfo=timezone.utc))[0] == "2026-10"
    for bad in ("2026-13", "2026-1", "October", "2026-10-01", "26-10"):
        with pytest.raises(ValueError):
            spend.month_bounds(bad)


def test_superadmins_only_and_the_company_is_ignored(monkeypatch):
    seen = []

    async def fake(month=None):
        seen.append(month)
        return {"month": month or "2026-10", "total_usd": 1.25, "by_company": [], "by_kind": {}, "unmeasured": []}

    monkeypatch.setattr(spend, "month_spend", fake)
    assert client(None).get("/api/trace-lab/spend").status_code == 401
    for role, org in (("user", ORG), ("admin", ORG)):
        r = client(who(role, org)).get("/api/trace-lab/spend")
        assert r.status_code == 403 and r.json()["detail"]["reason"] == "platform_only"
    r = client(who()).get("/api/trace-lab/spend", params={"month": "2026-09", "organization_id": ORG})
    assert r.status_code == 200 and r.json() == {"ok": True, "month": "2026-09", "total_usd": 1.25, "by_company": [],
                                                 "by_kind": {}, "unmeasured": []}
    assert client(who("superadmin", ORG)).get("/api/trace-lab/spend").status_code == 200   # acting for a company
    assert seen == ["2026-09", None]


def test_a_bad_month_is_400_and_not_ready_is_503_only_for_superadmins(monkeypatch):
    async def real_parse(month=None):
        spend.month_bounds(month)
        return {}

    monkeypatch.setattr(spend, "month_spend", real_parse)
    r = client(who()).get("/api/trace-lab/spend", params={"month": "2026-13"})
    assert r.status_code == 400 and r.json()["detail"]["reason"] == "bad_month"
    assert client(who(), ready=False).get("/api/trace-lab/spend").status_code == 503
    assert client(who("admin", ORG), ready=False).get("/api/trace-lab/spend").status_code == 403


def test_the_spend_router_is_registered_in_the_trace_lab_app():
    from trace_lab import app as app_module

    assert spend.router in app_module.ROUTERS
    c = TestClient(app_module.app)                # no "with": the lifespan (engines, schema check) never runs
    assert c.get("/api/trace-lab/spend").status_code == 401   # served: no bearer
