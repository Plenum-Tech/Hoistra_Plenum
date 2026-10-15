"""A user's buildings bound every query, and a selected building narrows them further.

The boundary used to be applied after the fact on three routes and nowhere in the engines:
a certificate list fetched 200 rows and then threw away the ones that were not the caller's;
a contract list did the same; scorecards, insights, criticalities and coverage did not do it
at all. These pin the rule as it stands now — expressed once, in SQL, from the ids the route
hands the engine — and the two account states an administrator can put a person in.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from src.api.routes import admin as admin_routes
from src.api.routes import auth as auth_routes
from src.app import app
from src.db import get_session
from src.engines.auth import access
from src.engines.auth.tokens import Principal
from src.models.contract_performance import VendorMonthlyScorecard

ORG = UUID("11111111-1111-5111-8111-111111111111")
MINE, ALSO_MINE, THEIRS = uuid4(), uuid4(), uuid4()


def principal(role="user", buildings=None, selected=None, user_id=None):
    return Principal(user_id=user_id or uuid4(), email="x@example.com", organization_id=ORG,
                     session_id=None, issued_at=datetime.now(timezone.utc),
                     password_changed_at=0, role=role, can_ingest=False,
                     building_ids=buildings, selected_building_id=selected)


# ── the selected building ─────────────────────────────────────────────────────

def test_a_selected_building_narrows_a_user_to_that_one():
    s = access.scope_for(principal(buildings=(MINE, ALSO_MINE), selected=MINE))
    assert s.building_ids == (MINE,)


def test_a_selection_outside_the_allocation_is_ignored_not_honoured():
    s = access.scope_for(principal(buildings=(MINE,), selected=THEIRS))
    assert s.building_ids == (MINE,)


def test_an_admin_selection_narrows_from_everything_to_one():
    s = access.scope_for(principal(role="admin", buildings=None, selected=MINE))
    assert s.building_ids == (MINE,)


def test_no_selection_leaves_the_allocation_alone():
    assert access.scope_for(principal(buildings=(MINE, ALSO_MINE))).building_ids == (MINE, ALSO_MINE)
    assert access.scope_for(principal(role="admin")).building_ids is None


# ── the predicates the engines apply ──────────────────────────────────────────

def test_building_predicate_is_the_same_rule_as_building_filter():
    assert access.building_predicate(None, "i.building_id") == ("", {})
    assert access.building_predicate((), "i.building_id") == (" AND FALSE", {})
    sql, params = access.building_predicate((MINE,), "i.building_id")
    assert sql == " AND i.building_id = ANY(CAST(:scope_building_ids AS uuid[]))"
    assert params == {"scope_building_ids": [str(MINE)]}


def test_vendor_predicate_derives_vendors_from_certificates_work_orders_and_invoices():
    sql, params = access.vendor_predicate((MINE,), "vendor_id")
    assert sql.startswith(" AND vendor_id::text IN (SELECT v.vendor_id::text FROM (")
    for table in ("compliance_certificates", "work_orders", "invoices"):
        assert f"plenum_cafm.{table}" in sql
    assert params == {"scope_building_ids": [str(MINE)]}
    assert access.vendor_predicate((), "vendor_id") == (" AND FALSE", {})
    assert access.vendor_predicate(None, "vendor_id") == ("", {})


def test_asset_and_document_predicates_go_through_their_own_link():
    sql, _ = access.asset_predicate((MINE,), "asset_id")
    assert "asset_id::text IN (SELECT a.id::text FROM plenum_cafm.assets a WHERE a.building_id" in sql
    sql, _ = access.document_predicate((MINE,), "document_id")
    assert "document_id IN (SELECT d.document_id FROM plenum_cafm.documents d WHERE d.building_id" in sql


def test_a_predicate_refuses_a_column_it_did_not_choose():
    with pytest.raises(ValueError):
        access.vendor_predicate((MINE,), "vendor_id; DROP TABLE users")


def test_orm_where_binds_the_predicate_into_a_select():
    q = access.orm_where(select(VendorMonthlyScorecard), *access.vendor_predicate((MINE,), "vendor_id"))
    compiled = q.compile(dialect=postgresql.dialect())
    assert "vendor_id::text IN (SELECT v.vendor_id::text" in str(compiled)
    assert compiled.params["scope_building_ids"] == [str(MINE)]
    # Nothing to narrow: the select is handed back untouched.
    assert access.orm_where(q, "", {}) is q


def test_orm_where_with_nothing_allocated_matches_no_row():
    q = access.orm_where(select(VendorMonthlyScorecard), " AND FALSE", {})
    assert "WHERE FALSE" in str(q.compile(dialect=postgresql.dialect())).replace("\n", " ")


# ── the routes hand their buildings to the engines ────────────────────────────

class Exploding:
    async def execute(self, *a, **k):
        raise AssertionError("route reached the database")
    async def commit(self): pass


async def _exploding():
    yield Exploding()


@pytest.fixture
def client():
    app.dependency_overrides[get_session] = _exploding
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def as_(p):
    async def _dep():
        return p
    app.dependency_overrides[auth_routes.current_principal] = _dep


@pytest.mark.parametrize("path,module,fn,key", [
    ("/api/compliance/certificates", "cert_svc", "list_certificates", "certificates"),
    ("/api/contract-performance/contracts", "params_svc", "list_contract_parameters", "parameters"),
    ("/api/contract-performance/scorecards", "score_svc", "list_scorecards", "scorecards"),
    ("/api/contract-performance/asset-criticality", "params_svc", "list_asset_criticalities", "items"),
    ("/api/contract-performance/invoices", "invoice_svc", "list_invoices", "invoices"),
])
def test_each_list_route_passes_the_callers_buildings_to_its_engine(client, monkeypatch, path, module, fn, key):
    from src.api.routes import compliance as c_routes
    from src.api.routes import contract_performance as cp_routes
    routes = c_routes if path.startswith("/api/compliance") else cp_routes
    seen = {}

    async def fake(session, **kw):
        seen.update(kw)
        return []
    monkeypatch.setattr(getattr(routes, module), fn, fake)
    as_(principal(buildings=(MINE, ALSO_MINE), selected=MINE))
    r = client.get(path)
    assert r.status_code == 200, (path, r.text[:200])
    assert seen["building_ids"] == (MINE,), path
    assert r.json()[key] == []


def test_an_admin_passes_no_boundary(client, monkeypatch):
    from src.api.routes import contract_performance as cp_routes
    seen = {}

    async def fake(session, **kw):
        seen.update(kw); return []
    monkeypatch.setattr(cp_routes.score_svc, "list_scorecards", fake)
    as_(principal(role="admin"))
    assert client.get("/api/contract-performance/scorecards").status_code == 200
    assert seen["building_ids"] is None


def test_insights_and_coverage_take_the_boundary_too(client, monkeypatch):
    from src.api.routes import compliance as c_routes
    from src.api.routes import contract_performance as cp_routes
    seen = {}

    async def fake_insights(session, **kw):
        seen["insights"] = kw["building_ids"]
        return {"ok": True, "cost_variance_pct": None, "labour_variance_pct": None,
                "matched_flagged_trend": [], "contributing_wo_ids": [],
                "period": {"from_date": None, "to_date": None}, "work_orders_considered": 0}

    async def fake_cov(session, **kw):
        seen["coverage"] = kw["building_ids"]; return {"ok": True, "buildings": []}
    monkeypatch.setattr(cp_routes.insights_svc, "compute_insights", fake_insights)
    monkeypatch.setattr(c_routes.coverage_svc, "building_coverage", fake_cov)
    as_(principal(buildings=(MINE,)))
    assert client.get("/api/contract-performance/insights").status_code == 200
    assert client.get("/api/compliance/coverage/buildings").status_code == 200
    assert seen == {"insights": (MINE,), "coverage": (MINE,)}


# ── choosing a building ───────────────────────────────────────────────────────

class Quiet:
    """A session that answers every query with nothing — for a route that reads
    conveniences (building names, sessions) around the fields under test."""
    class _R:
        def mappings(self): return self
        def scalars(self): return self
        def all(self): return []
        def first(self): return None
        def one_or_none(self): return None
        def scalar(self): return None
    async def execute(self, *a, **k): return self._R()
    async def commit(self): pass


async def _quiet():
    yield Quiet()


def test_me_reports_the_selected_building(client, monkeypatch):
    app.dependency_overrides[get_session] = _quiet

    async def find(session, email):
        return {"id": str(uuid4()), "email": email, "status": "active"}
    monkeypatch.setattr(auth_routes.acc, "find_by_email", find)
    monkeypatch.setattr(auth_routes.acc, "public_user", lambda row: dict(row))
    as_(principal(buildings=(MINE,), selected=MINE))
    r = client.get("/api/auth/me")
    assert r.status_code == 200, r.text[:200]
    assert r.json()["user"]["selected_building_id"] == str(MINE)
    assert r.json()["user"]["building_ids"] == [str(MINE)]


def test_a_user_cannot_select_a_building_they_are_not_allocated(client):
    as_(principal(buildings=(MINE,)))
    r = client.patch("/api/auth/me/selected-building", json={"building_id": str(THEIRS)})
    assert r.status_code == 403
    assert r.json()["detail"]["reason"] == "building_not_allocated"


def test_a_user_allocated_to_nothing_cannot_select_anything(client):
    as_(principal(buildings=()))
    r = client.patch("/api/auth/me/selected-building", json={"building_id": str(MINE)})
    assert r.status_code == 403


# ── inactive and deleted ──────────────────────────────────────────────────────

def test_status_words_normalise_to_the_two_settable_states():
    assert admin_routes._normalise_status("Active") == "active"
    assert admin_routes._normalise_status("inactive") == "inactive"
    for old in ("suspended", "disabled", "deactivated"):
        assert admin_routes._normalise_status(old) == "inactive"


def test_deleting_by_status_is_refused_and_points_at_the_route():
    with pytest.raises(HTTPException) as e:
        admin_routes._normalise_status("deleted")
    assert e.value.status_code == 400 and e.value.detail["reason"] == "use_delete"
    with pytest.raises(HTTPException) as e:
        admin_routes._normalise_status("banana")
    assert e.value.status_code == 422 and e.value.detail["reason"] == "bad_status"


@pytest.mark.parametrize("method,suffix", [
    ("POST", "/deactivate"), ("POST", "/reactivate"), ("DELETE", ""),
])
def test_only_an_administrator_changes_an_account_state(client, method, suffix):
    as_(principal(buildings=(MINE,)))
    r = client.request(method, f"/api/admin/users/{uuid4()}{suffix}")
    assert r.status_code == 403, (method, suffix, r.status_code)


@pytest.mark.parametrize("method,suffix", [("POST", "/deactivate"), ("DELETE", "")])
def test_an_administrator_cannot_deactivate_or_delete_themselves(client, method, suffix):
    me = uuid4()
    as_(principal(role="admin", user_id=me))
    r = client.request(method, f"/api/admin/users/{me}{suffix}")
    assert r.status_code == 400
    err = r.json()["detail"]["error"]
    assert "yourself" in err or "your own" in err


def test_the_sign_in_gate_refuses_the_new_states():
    from src.engines.auth import accounts
    assert "inactive" not in accounts.LIVE_STATUSES and "deleted" not in accounts.LIVE_STATUSES
