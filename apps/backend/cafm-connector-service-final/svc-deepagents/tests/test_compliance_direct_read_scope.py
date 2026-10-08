"""The direct compliance register read is scoped to the caller's company (7 Oct 2026).

_fetch_compliance_table_direct reads plenum_cafm.compliance_certificates straight from
Postgres, and had no company filter: anyone not narrowed to buildings - every admin and
superadmin - was handed every company's certificates. It went unnoticed only because on
hoistra_test the read failed on every question (a join compared a uuid id with text) and the
HTTP fallback, which operations-intelligence scopes, answered instead. Both are fixed together:
fixing the join alone would have switched the leak on.

No database is touched: the session factory is replaced and every statement is recorded.
"""
from __future__ import annotations

import asyncio
import re
from uuid import UUID

import pytest

from src import database
from src.agents import activity_log
from src.agents.orchestrator import DeepAgentOrchestrator as O
from src.http_client import caller_organization_id
from src.services.principal import Principal, caller_principal

ACTING = "ad88d1ed-4376-447c-8196-e4e5b81d5af6"
HOME = "00000000-0000-0000-0000-000000000001"
COLS = {"organization_id", "org_id", "raw_metadata", "vendor_id", "status", "expiry_date"}


class _Result:
    def mappings(self):
        return self

    def all(self):
        return []


class _Session:
    def __init__(self, seen):
        self.seen = seen

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, sql, params=None):
        self.seen.append((str(sql), dict(params or {})))
        return _Result()


class _Seen(list):
    """The statements sent, plus the register columns the read is told exist."""

    cols: dict


def _admin(org=HOME, role="admin", buildings=None):
    return Principal(user_id=UUID(int=7), email="a@x", organization_id=UUID(org) if org else None,
                     role=role, can_ingest=True, building_ids=buildings)


@pytest.fixture
def seen(monkeypatch):
    seen = _Seen()
    monkeypatch.setattr(database, "AsyncSessionLocal", lambda: _Session(seen))
    monkeypatch.setattr(activity_log, "fire", lambda **k: None)
    cols = {"value": set(COLS)}

    async def allow(self):
        return set(cols["value"])

    monkeypatch.setattr(O, "_compliance_column_allowlist", allow)
    seen.cols = cols
    return seen


def _read(principal, acting=None):
    async def go():
        tp = caller_principal.set(principal)
        to = caller_organization_id.set(acting)
        try:
            return await O.__new__(O)._fetch_compliance_table_direct(limit=10)
        finally:
            caller_principal.reset(tp)
            caller_organization_id.reset(to)

    return asyncio.run(go())


class TestTheCompany:
    def test_an_admin_reads_only_their_own_company(self, seen):
        _read(_admin())
        sql, params = seen[-1]
        assert "c.organization_id::text = :scope_org" in sql
        assert "c.org_id::text = :scope_org" in sql
        assert params["scope_org"] == HOME

    def test_a_superadmin_viewing_a_company_reads_that_company(self, seen):
        _read(_admin(role="superadmin"), acting=ACTING)
        assert seen[-1][1]["scope_org"] == ACTING

    def test_a_building_bound_user_is_held_to_their_company_too(self, seen):
        # Their vendor accreditations name no building, so the building rule alone let in
        # every company's.
        _read(_admin(role="user", buildings=(UUID(int=1),)))
        sql, params = seen[-1]
        assert ":scope_org" in sql and params["scope_org"] == HOME
        assert "scope_buildings" in params

    def test_the_company_is_compared_in_the_form_postgres_prints_a_uuid(self, seen):
        # uuid::text is lowercase; an id named in capitals would match nothing, silently.
        _read(_admin(role="superadmin"), acting=ACTING.upper())
        assert seen[-1][1]["scope_org"] == ACTING

    def test_a_company_that_is_not_an_id_reads_nothing(self, seen):
        with pytest.raises(RuntimeError):
            _read(_admin(role="superadmin"), acting="None")
        assert seen == []

    def test_with_no_company_nothing_is_read(self, seen):
        with pytest.raises(RuntimeError):
            _read(None)
        assert seen == [], "the scoped fallback answers instead"

    def test_a_register_with_no_company_column_is_not_read(self, seen):
        seen.cols["value"] = COLS - {"organization_id", "org_id"}
        with pytest.raises(RuntimeError):
            _read(_admin())
        assert seen == []

    def test_only_the_company_columns_that_exist_are_named(self, seen):
        seen.cols["value"] = COLS - {"org_id"}
        _read(_admin())
        sql = seen[-1][0]
        assert "c.organization_id::text = :scope_org" in sql and "org_id::text" not in sql


class TestOpenWorkOrders:
    """The open work orders a compliance plan can ask for alongside the register (needs:
    work_orders) were read with the building rule only - every company's for an admin."""

    def _cols(self, monkeypatch, cols):
        from src.services import principal as pr

        async def table_columns(session, table):
            assert table == "work_orders"
            return frozenset(cols)

        monkeypatch.setattr(pr, "table_columns", table_columns)

    def _run(self, principal, acting=None):
        async def go():
            tp = caller_principal.set(principal)
            to = caller_organization_id.set(acting)
            try:
                return await O.__new__(O)._fetch_open_work_orders()
            finally:
                caller_principal.reset(tp)
                caller_organization_id.reset(to)

        return asyncio.run(go())

    def test_an_admin_reads_only_their_companys(self, seen, monkeypatch):
        self._cols(monkeypatch, {"organization_id", "status", "building_id"})
        self._run(_admin(role="superadmin"), acting=ACTING)
        sql, params = seen[-1]
        assert "organization_id::text = :scope_org" in sql and params["scope_org"] == ACTING

    def test_without_a_company_column_they_are_not_read(self, seen, monkeypatch):
        self._cols(monkeypatch, {"status", "building_id"})
        with pytest.raises(RuntimeError):
            self._run(_admin())
        assert not any("FROM plenum_cafm.work_orders" in s for s, _ in seen)

    def test_without_a_company_they_are_not_read(self, seen, monkeypatch):
        self._cols(monkeypatch, {"organization_id"})
        with pytest.raises(RuntimeError):
            self._run(None)
        assert seen == []


class TestTheJoins:
    def test_every_join_compares_as_text_whatever_the_id_types(self, seen):
        # vendors.id is uuid on hoistra_test and varchar on production; a join written for
        # one fails on the other, and that failure is what hid the missing company filter.
        _read(_admin())
        sql = seen[-1][0]
        joins = re.findall(r"LEFT JOIN plenum_cafm\.(\w+)\s+\w+ ON (.+)", sql)
        assert {t for t, _ in joins} == {"vendors", "sites", "assets", "locations", "ingestion_documents"}
        for table, cond in joins:
            left, right = (s.strip() for s in cond.split("=", 1))
            assert left.endswith("::text"), f"{table}: {cond}"
            assert right.endswith("::text") or right.startswith("COALESCE("), f"{table}: {cond}"


class TestWhatThePageHides:
    def test_fixtures_superseded_and_archived_rows_are_left_out(self, seen):
        # The same rows operations-intelligence hides from the compliance page, so the chat
        # counts what the page counts.
        _read(_admin())
        sql = seen[-1][0]
        for key in ("a1_test_fixture", "superseded_duplicate", "archived"):
            assert f"c.raw_metadata->>'{key}'" in sql
