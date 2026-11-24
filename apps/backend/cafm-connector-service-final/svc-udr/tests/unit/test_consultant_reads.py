"""A consultant's chat reads every register of the buildings they hold (24 Nov 2026).

A consultant is a plain user (platform role ``user``) whose data is every page
(svc-operations-intelligence engines/auth/pages.py), allocated to some buildings. The chat's
database tools read through this service with the consultant's own token, and every table and
catalogue route here was administrator-only — so "Aire Mill Court vendors" was answered from
two links and a refusal ("you do not have access … ask an administrator"), while the vendors
were there on its work orders, assets and visits.

Pinned here:
  * a consultant reads tables and the catalogue, scoped to their company and buildings;
  * a consultant still cannot write, run caller-supplied SQL or drive the agent;
  * a table with no company or building column is not readable by anyone but an admin, since
    no predicate can narrow it;
  * the catalogue's sample rows and sample values are an admin's only — they were taken from
    every company's rows when the catalogue was built;
  * a reading or a maintenance plan with no building of its own is reached through its meter
    or asset, the same way in this service as in the scoped views.
Any other plain user is exactly as before.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("DB_URL", "postgresql+asyncpg://u:p@localhost/d")
logging.disable(logging.CRITICAL)

from src.app import app  # noqa: E402
from src.services import catalog as cat  # noqa: E402
from src.services import principal as P  # noqa: E402
from src.services import scope  # noqa: E402
from src.services.database_service import DatabaseService  # noqa: E402

ORG = UUID("11111111-1111-1111-1111-111111111111")
B1 = UUID("33333333-3333-3333-3333-333333333333")


def _principal(role: str = "user", access_role: str | None = None,
               building_ids: tuple[UUID, ...] | None = (B1,)) -> P.Principal:
    return P.Principal(user_id=uuid4(), email="c@example.com", organization_id=ORG, role=role,
                       building_ids=building_ids, access_role=access_role)


CONSULTANT = dict(role="user", access_role="consultant")


@pytest.fixture
def client() -> TestClient:
    yield TestClient(app)
    app.dependency_overrides.clear()


class _Rows:
    def __iter__(self):
        return iter(())

    def fetchall(self):
        return []

    def all(self):
        return []

    def first(self):
        return None

    def fetchone(self):
        return (1,)  # the table exists

    def scalar(self):
        return 0

    def mappings(self):
        return self

    @property
    def rowcount(self):
        return 0


class _Session:
    async def execute(self, *a, **k):
        return _Rows()

    async def scalar(self, *a, **k):
        return 0

    async def commit(self):
        pass


def _as(principal: P.Principal) -> None:
    async def _me():
        return principal

    from src.db import get_session

    async def _session():
        yield _Session()

    app.dependency_overrides[P.current_principal] = _me
    app.dependency_overrides[get_session] = _session


def _code(response) -> str:
    body = response.json()
    errors = body.get("errors") or []
    if errors:
        return errors[0].get("code", "")
    detail = body.get("detail")
    return detail.get("reason", "") if isinstance(detail, dict) else ""


# ── who a consultant is ───────────────────────────────────────────────────────

def test_the_access_role_is_read_from_the_identity_service():
    payload = {"user": {"id": str(uuid4()), "email": "a@b.c", "organization_id": str(ORG),
                        "platform_role": "user", "building_ids": [str(B1)],
                        "access_role": "consultant", "pages": None}}
    p = P._from_me(payload)
    assert p.access_role == "consultant" and p.is_consultant and not p.is_admin
    plain = P._from_me({"user": {**payload["user"], "access_role": None}})
    assert plain.access_role is None and not plain.is_consultant


def test_an_admin_with_a_stale_access_role_is_still_an_admin():
    assert _principal(role="admin", access_role="consultant").is_admin


# ── the read routes ───────────────────────────────────────────────────────────

READS = [
    ("GET", "/api/tables/"),
    ("GET", "/api/tables/work_orders/schema"),
    ("GET", "/api/tables/work_orders/records"),
    ("POST", "/api/tables/work_orders/records/search"),
    ("GET", "/api/tables/work_orders/records/1"),
]


@pytest.mark.parametrize("method, path", READS)
def test_a_consultant_is_past_the_gate_on_every_read(client, method, path):
    _as(_principal(**CONSULTANT))
    resp = client.request(method, path, json={"search_term": "lift"} if method == "POST" else None)
    assert _code(resp) != "admin_required", f"{method} {path}: {resp.status_code} {resp.text[:200]}"


@pytest.mark.parametrize("method, path", READS)
def test_any_other_plain_user_is_still_refused_every_read(client, method, path):
    _as(_principal(role="user", access_role=None))
    resp = client.request(method, path, json={"search_term": "lift"} if method == "POST" else None)
    assert resp.status_code == 403 and _code(resp) == "admin_required"


@pytest.mark.parametrize("method, path, body", [
    ("POST", "/api/tables/work_orders/records", {"data": {"title": "x"}}),
    ("PATCH", "/api/tables/work_orders/records/1", {"data": {"title": "x"}}),
    ("DELETE", "/api/tables/work_orders/records/1", None),
    ("POST", "/api/tables/query/select", {"sql": "SELECT 1"}),
    ("POST", "/api/agent/query", {"message": "hi"}),
    ("POST", "/api/catalog/rebuild", {}),
])
def test_a_consultant_cannot_write_run_sql_or_drive_the_agent(client, method, path, body):
    """Caller-supplied SELECT runs against plenum_scoped, where 91 views over tables with no
    company or building column are unfiltered — so it stays an administrator's."""
    _as(_principal(**CONSULTANT))
    resp = client.request(method, path, json=body)
    assert resp.status_code == 403, f"{method} {path}: {resp.status_code}"
    assert _code(resp) in ("admin_required", "superadmin_required")


# ── tables that nothing can narrow ────────────────────────────────────────────

def _svc(principal: P.Principal, columns: frozenset[str]) -> DatabaseService:
    async def _cols(_session, _table):
        return columns

    svc = DatabaseService(_Session(), principal)
    svc._scope_columns = _cols  # type: ignore[attr-defined]
    return svc


def test_a_consultant_cannot_read_a_table_with_no_company_or_building_column(monkeypatch):
    async def _cols(_session, _table):
        return frozenset()

    monkeypatch.setattr(scope, "scope_columns", _cols)
    svc = DatabaseService(_Session(), _principal(**CONSULTANT))
    for call in (lambda: svc.read_records(table="organizations"),
                 lambda: svc.get_record("organizations", "1"),
                 lambda: svc.search_records(table="organizations", search_term="acme", search_columns=["name"])):
        with pytest.raises(Exception) as exc:
            asyncio.run(call())
        assert getattr(exc.value, "reason", "") == "not_scoped_for_reader", repr(exc.value)
        assert getattr(exc.value, "status_code", 0) == 403


def test_an_admin_still_reads_a_table_with_no_scope_column(monkeypatch):
    async def _cols(_session, _table):
        return frozenset()

    monkeypatch.setattr(scope, "scope_columns", _cols)
    svc = DatabaseService(_Session(), _principal(role="admin", building_ids=None))
    try:
        asyncio.run(svc.read_records(table="organizations"))
    except Exception as exc:  # anything but the reader refusal is the fake session's doing
        assert getattr(exc, "reason", "") != "not_scoped_for_reader"


def test_a_consultant_reads_a_table_that_is_scoped(monkeypatch):
    async def _cols(_session, _table):
        return frozenset({scope.ORG_COLUMN, scope.BUILDING_COLUMN})

    monkeypatch.setattr(scope, "scope_columns", _cols)
    svc = DatabaseService(_Session(), _principal(**CONSULTANT))
    try:
        asyncio.run(svc.read_records(table="work_orders"))
    except Exception as exc:
        assert getattr(exc, "reason", "") != "not_scoped_for_reader"


# ── the catalogue ─────────────────────────────────────────────────────────────

_CARD = {"table": "work_orders", "purpose": "Jobs.", "answers": ["Which jobs are open?"],
         "columns": [{"name": "title", "type": "text", "sample_values": ["Lift service — Acme Ltd"]}],
         "sample_rows": [{"title": "Lift service — Acme Ltd"}], "links_out": [], "links_in": []}


@pytest.fixture
def card(monkeypatch):
    async def _card(_session, table):
        return {**_CARD, "columns": [dict(c) for c in _CARD["columns"]],
                "sample_rows": list(_CARD["sample_rows"])} if table == "work_orders" else None

    async def _search(_session, q, k=8, domain=None):
        return {"ok": True, "method": "text", "question": q,
                "tables": [{"table": "work_orders", "purpose": "Jobs.", "columns": ["title"]}]}

    async def _summary(_session):
        return {"ok": True, "domains": [], "total": 0}

    monkeypatch.setattr(cat, "card", _card)
    monkeypatch.setattr(cat, "search", _search)
    monkeypatch.setattr(cat, "summary", _summary)


def test_a_consultant_reads_the_catalogue_without_any_sample(client, card):
    _as(_principal(**CONSULTANT))
    resp = client.get("/api/catalog/work_orders")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["purpose"] == "Jobs." and body["columns"][0]["name"] == "title"
    assert "sample_rows" not in body
    assert all("sample_values" not in c for c in body["columns"])
    assert "Acme" not in resp.text
    assert client.get("/api/catalog/search", params={"q": "open jobs"}).status_code == 200
    assert client.get("/api/catalog").status_code == 200


def test_an_admin_still_sees_the_samples(client, card):
    _as(_principal(role="admin", building_ids=None))
    body = client.get("/api/catalog/work_orders").json()
    assert body["sample_rows"] and body["columns"][0]["sample_values"]


def test_any_other_plain_user_is_still_refused_the_catalogue(client, card):
    _as(_principal(role="user", access_role=None))
    resp = client.get("/api/catalog/work_orders")
    assert resp.status_code == 403 and _code(resp) == "admin_required"


# ── a row whose building is its parent's ──────────────────────────────────────

def test_a_reading_with_no_building_is_reached_through_its_meter():
    frag, params = scope.predicate_for(frozenset({scope.ORG_COLUMN, scope.BUILDING_COLUMN}),
                                       _principal(**CONSULTANT), table="meter_readings")
    assert '"building_id" IS NULL' in frag
    assert '"meter_id" IN (SELECT p.id FROM' in frag and '"energy_meters" p' in frag
    assert str(B1) not in frag and str(B1) in params.values()


def test_a_plan_with_no_building_is_reached_through_its_asset():
    frag, _ = scope.predicate_for(frozenset({scope.ORG_COLUMN, scope.BUILDING_COLUMN}),
                                  _principal(**CONSULTANT), table="maintenance_plans")
    assert '"asset_id" IN (SELECT p.id FROM' in frag and '"assets" p' in frag


def test_every_building_needs_no_parent_route():
    frag, _ = scope.predicate_for(frozenset({scope.ORG_COLUMN, scope.BUILDING_COLUMN}),
                                  _principal(**CONSULTANT, building_ids=None), table="meter_readings")
    assert "meter_id" not in frag and "building_id" not in frag


def test_other_tables_keep_the_plain_building_rule():
    frag, _ = scope.predicate_for(frozenset({scope.ORG_COLUMN, scope.BUILDING_COLUMN}),
                                  _principal(**CONSULTANT), table="work_orders")
    assert "IS NULL" not in frag and "SELECT" not in frag


def test_the_scoped_views_reach_the_same_parents():
    """Two routes, one rule: if the views and this service disagree, the same question
    answers differently through the chat's scoped reads and through the table routes."""
    import re as _re

    sql = Path(__file__).resolve().parents[3] / (
        "svc-operations-intelligence/migrations/udr_scoped_views.sql")
    if not sql.exists():
        pytest.skip(f"migration not found at {sql}")
    text_ = sql.read_text(encoding="utf-8")
    for table, (col, parent) in scope.BUILDING_VIA_PARENT.items():
        assert _re.search(rf"'{table}'\s*,\s*'{col}'\s*,\s*'{parent}'", text_), (
            f"udr_scoped_views.sql does not route {table}.{col} through {parent}")


# ── rows with no building of their own (24 Nov 2026) ──────────────────────────
# Planned visits, inspections, asset readings, invoice lines... carry a company and no building,
# so a consultant read every building's: the chat answered about Bishopsgate's planned visits.

def test_a_planned_visit_is_narrowed_through_its_asset():
    frag, params = scope.predicate_for(frozenset({scope.ORG_COLUMN}), _principal(**CONSULTANT), table="ppm_visits")
    assert '"asset_id"::text IN (SELECT p.id::text FROM' in frag and '"assets" p' in frag
    assert '"organization_id"::text = :scope_org' in frag
    assert str(B1) in params.values() and str(B1) not in frag


def test_every_building_needs_no_parent_route_for_them_either():
    frag, _ = scope.predicate_for(frozenset({scope.ORG_COLUMN}), _principal(**CONSULTANT, building_ids=None),
                                  table="ppm_visits")
    assert "asset_id" not in frag


def test_an_invoice_verification_is_narrowed_through_its_document():
    frag, _ = scope.predicate_for(frozenset({scope.ORG_COLUMN}), _principal(**CONSULTANT),
                                  table="invoice_verifications")
    assert '"document_id"::text IN (SELECT p.document_id::text FROM' in frag


@pytest.mark.parametrize("table", ["users", "chat_turns", "agent_trace_turns", "ops_audit_log",
                                   "approvals_queue_items", "auth_sessions_x", "platform_usage_events"])
def test_internal_tables_are_an_admins_only(monkeypatch, table):
    async def _cols(_session, _table):
        return frozenset({scope.ORG_COLUMN, scope.BUILDING_COLUMN})

    monkeypatch.setattr(scope, "scope_columns", _cols)
    svc = DatabaseService(_Session(), _principal(**CONSULTANT))
    with pytest.raises(Exception) as exc:
        asyncio.run(svc.read_records(table=table))
    assert getattr(exc.value, "reason", "") == "not_scoped_for_reader"


def test_the_scoped_views_narrow_the_same_building_less_tables():
    import re as _re

    sql = Path(__file__).resolve().parents[3] / (
        "svc-operations-intelligence/migrations/udr_scoped_views.sql")
    if not sql.exists():
        pytest.skip(f"migration not found at {sql}")
    text_ = sql.read_text(encoding="utf-8")
    for table, (col, parent, key) in scope.BUILDING_ONLY_VIA_PARENT.items():
        assert _re.search(rf"'{table}'\s*,\s*'{col}'\s*,\s*'{parent}'\s*,\s*'{key}'", text_), (
            f"udr_scoped_views.sql does not narrow {table}.{col} through {parent}.{key}")
