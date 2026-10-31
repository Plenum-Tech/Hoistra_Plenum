"""GET /api/trace-lab/status: readers only, always 200, never names a table. No database: the
schema state is set directly and migrate.verify is replaced where a re-check would run."""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from src.services import principal as principal_module
from src.services.principal import Principal
from trace_lab import app as app_module
from trace_lab import db, jobs, migrate, routes_status
from trace_lab.settings import Settings

ORG = "00000000-0000-0000-0000-0000000000a1"
OTHER = "00000000-0000-0000-0000-0000000000b2"


def _p(role: str, org: str | None = ORG) -> Principal:
    return Principal(user_id=uuid.uuid4(), email="fm@example.com",
                     organization_id=uuid.UUID(org) if org else None, role=role,
                     can_ingest=True, building_ids=None)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    db.reset_for_tests()

    async def no_sweep(stale_after_s=120):
        return []

    monkeypatch.setattr(jobs, "mark_interrupted", no_sweep)
    monkeypatch.setattr(db, "lab_engine", lambda: object())
    yield
    db.reset_for_tests()


def _client(role: str | None = "admin", org: str | None = ORG) -> TestClient:
    app = app_module.create_app(Settings())
    if role:
        app.dependency_overrides[principal_module.current_principal] = lambda: _p(role, org)
    return TestClient(app)   # not entered: the lifespan does not run


def _verify_returns(monkeypatch, status: str):
    calls: list[int] = []

    async def fake(engine, *, directory=None):
        calls.append(1)
        down = status != "ready"
        return migrate.SchemaState(status, 2, 1 if down else 2, pending=[2] if down else [],
                                   problems=["jobs: missing column x"] if down else [])

    monkeypatch.setattr(migrate, "verify", fake)
    return calls


def test_the_status_route_is_registered_under_the_prefix():
    assert routes_status.router in app_module.ROUTERS
    assert _client(role=None).get("/api/trace-lab/status").status_code == 401   # there, and needs a caller
    assert _client(role=None).get("/status").status_code == 404


def test_status_needs_a_signed_in_reader():
    assert _client(role=None).get("/api/trace-lab/status").status_code == 401
    r = _client(role="user").get("/api/trace-lab/status")
    assert r.status_code == 403 and r.json()["detail"]["reason"] == "reviewers_only"
    r = _client(role="admin").get("/api/trace-lab/status", params={"organization_id": OTHER})
    assert r.status_code == 403 and r.json()["detail"]["reason"] == "bad_company"


def test_status_answers_200_and_names_no_table_while_down(monkeypatch):
    _verify_returns(monkeypatch, "behind")
    r = _client().get("/api/trace-lab/status")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["available"] is False and body["status"] == "behind"
    assert (body["expected_version"], body["applied_version"]) == (2, 1) and body["checked_at"].endswith("Z")
    assert "jobs" not in r.text and "tracelab" not in r.text and "problems" not in body


def test_status_reports_available_when_ready(monkeypatch):
    _verify_returns(monkeypatch, "ready")
    body = _client(role="superadmin", org=None).get("/api/trace-lab/status").json()
    assert body["available"] is True and body["status"] == "ready" and body["applied_version"] == 2


def test_a_superadmin_may_ask_for_a_company(monkeypatch):
    _verify_returns(monkeypatch, "ready")
    r = _client(role="superadmin", org=None).get("/api/trace-lab/status", params={"organization_id": OTHER})
    assert r.status_code == 200 and r.json()["available"] is True


def test_a_fresh_state_is_not_checked_again(monkeypatch):
    calls = _verify_returns(monkeypatch, "behind")
    db.set_state(migrate.SchemaState("behind", 2, 1, pending=[2]))   # checked just now
    assert _client().get("/api/trace-lab/status").json()["status"] == "behind"
    assert calls == []


def test_a_disabled_service_reports_unreadable_without_a_check(monkeypatch):
    calls = _verify_returns(monkeypatch, "ready")
    db.disable("Trace Lab is turned off (TRACE_LAB_ENABLED is not 1)")
    body = _client().get("/api/trace-lab/status").json()
    assert body["available"] is False and body["status"] == "unreadable" and calls == []
    assert "TRACE_LAB_ENABLED" not in str(body)
