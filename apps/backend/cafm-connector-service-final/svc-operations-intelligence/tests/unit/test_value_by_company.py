"""Each company's platform value, for the Super Admin console's company list.

Hussain, 29 Sep 2026: in place of the credits beside each company name, the sum of that
company's Platform value DETECTED column — the Home page's ledger total, the same figure the
company sees on its own Home page. It is read through the same engine (read_value_summary) with
every one of the company's buildings in scope, so the two can never disagree. A company whose
ledger cannot be read says so (None), never £0.
"""
from __future__ import annotations

from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from src.engines import value_ledger

A = "11111111-1111-1111-1111-111111111111"
B = "22222222-2222-2222-2222-222222222222"
C = "33333333-3333-3333-3333-333333333333"


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return list(self._rows)


class FakeDB:
    async def execute(self, stmt, params=None):
        assert "FROM plenum_cafm.organizations" in str(stmt)
        return _Result([{"id": A, "name": "Plenum Technologies"}, {"id": B, "name": "Northbridge"},
                        {"id": C, "name": "Broken Ltd"}])


@pytest.fixture
def ledgers(monkeypatch):
    seen = []

    async def read(session, *, organization_id, building_ids=None, year=None):
        seen.append((str(organization_id), building_ids, year))
        if str(organization_id) == C:
            raise RuntimeError("relation does not exist")
        total = {A: 129028.0, B: None}[str(organization_id)]
        return {"ok": True, "year": 2026, "currency": "GBP",
                "ledger": {"total_detected": total, "counted_modules": 0 if total is None else 4}}

    monkeypatch.setattr(value_ledger, "read_value_summary", read)
    return seen


async def test_each_company_carries_its_own_ledger_total(ledgers):
    out = await value_ledger.value_by_company(FakeDB(), year=2026)
    by = {r["organization_id"]: r for r in out["companies"]}
    assert by[A]["total_detected"] == 129028.0 and by[A]["counted_modules"] == 4
    assert out["currency"] == "GBP" and out["year"] == 2026


async def test_every_company_is_read_with_all_its_buildings_in_scope(ledgers):
    await value_ledger.value_by_company(FakeDB(), year=2026)
    assert [(o, b) for o, b, _y in ledgers] == [(A, None), (B, None), (C, None)]


async def test_a_ledger_with_nothing_counted_is_none_not_zero(ledgers):
    out = await value_ledger.value_by_company(FakeDB(), year=2026)
    by = {r["organization_id"]: r for r in out["companies"]}
    assert by[B]["total_detected"] is None


async def test_a_company_whose_ledger_cannot_be_read_says_so_and_the_rest_still_show(ledgers):
    out = await value_ledger.value_by_company(FakeDB(), year=2026)
    by = {r["organization_id"]: r for r in out["companies"]}
    assert by[C]["total_detected"] is None and by[C]["error"]
    assert by[A]["total_detected"] == 129028.0


# ── the route ───────────────────────────────────────────────────────────────────────────


def test_the_value_route_is_super_admin_only_and_returns_the_engines_answer(monkeypatch):
    from datetime import datetime, timezone

    from src.api.routes import auth as auth_routes
    from src.api.routes import superadmin as sa_routes
    from src.app import app
    from src.db import get_session
    from src.engines.auth.tokens import Principal

    async def _vbc(session, *, year=None):
        return {"ok": True, "year": 2026, "currency": "GBP",
                "companies": [{"organization_id": A, "name": "P", "total_detected": 1.0,
                               "counted_modules": 1, "error": None}]}

    async def _s():
        yield object()

    def as_(role):
        async def _p():
            return Principal(user_id=UUID(int=1), email="x@example.com", organization_id=UUID(A),
                             session_id=None, issued_at=datetime.now(timezone.utc),
                             password_changed_at=0, role=role, can_ingest=True, building_ids=None)
        app.dependency_overrides[auth_routes.current_principal] = _p

    monkeypatch.setattr(sa_routes.value_ledger, "value_by_company", _vbc)
    app.dependency_overrides[get_session] = _s
    try:
        c = TestClient(app)
        as_("admin")
        assert c.get("/api/superadmin/value").status_code == 403
        as_("superadmin")
        r = c.get("/api/superadmin/value")
        assert r.status_code == 200 and r.json()["companies"][0]["total_detected"] == 1.0
    finally:
        app.dependency_overrides.clear()



def test_a_priced_module_that_could_not_be_read_says_so():
    assert value_ledger._energy_module({"error": "timeout"}, [])["unreadable"] is True
    assert value_ledger._assets_module({"error": "timeout"}, [])["unreadable"] is True
    half = value_ledger._vendors_module({"error": "timeout"}, {"detected": 5, "alerts": 1}, [], {})
    assert half["counted"] is True and half["partial"] is True
    assert "unreadable" not in value_ledger._maintenance_module({"closed": 3}), "never priced is not unreadable"


async def test_a_total_missing_a_module_is_marked_partial(monkeypatch):
    async def read(session, *, organization_id, building_ids=None, year=None):
        return {"ok": True, "ledger": {"total_detected": 28.0, "counted_modules": 1, "modules": [
            {"key": "energy", "name": "Energy", "counted": False, "unreadable": True},
            {"key": "vendors", "name": "Vendors", "counted": True, "detected": 28.0},
            {"key": "maintenance", "name": "Maintenance", "counted": False}]}}

    monkeypatch.setattr(value_ledger, "read_value_summary", read)
    out = await value_ledger.value_by_company(FakeDB(), year=2026)
    first = out["companies"][0]
    assert first["partial"] is True and first["unreadable"] == ["Energy"]
    assert first["total_detected"] == 28.0
