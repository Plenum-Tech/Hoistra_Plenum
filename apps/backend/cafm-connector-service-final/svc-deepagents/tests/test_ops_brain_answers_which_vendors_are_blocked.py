"""Ops Brain answers "which vendors are blocked" from the vendor record (2 Nov 2026).

Ops Brain's own Vendors page shows "5 vendors blocked", but its chat refused "Which vendors are
blocked?": the keyword table reads "blocked" as compliance, which Ops Brain does not have. Hussain
chose to let it answer - from the vendor record only (name, block state, the accreditation the block
is for), never certificate, expiry or document details, which stay compliance's.
"""
from __future__ import annotations

import asyncio

import pytest

from src.agents import agent_router
from src.agents import contract_performance_agent as cp


class Reached(Exception):
    pass


@pytest.fixture
def no_model(monkeypatch):
    def reached(*a, **k):
        raise Reached()

    monkeypatch.setattr(agent_router, "llm_routing_enabled", lambda: True)
    monkeypatch.setattr(agent_router, "_catalogue", reached)


@pytest.mark.parametrize("q", ["Which vendors are blocked?", "is ProudCastle Fire blocked as a vendor?",
                               "List the blocked contractors"])
def test_ops_brain_sends_a_blocked_vendor_question_to_the_vendors_engine(no_model, q):
    out = asyncio.run(agent_router.select_agent(q, space="ops"))
    assert out["agent"] == "contract_performance" and not out.get("out_of_module"), out


@pytest.mark.parametrize("q", ["Which vendors have lapsed accreditations?", "Which certificates expire next month?",
                               "Which vendors are blocked and which certificates lapsed?"])
def test_certificate_questions_are_still_compliance_and_refused_in_ops_brain(no_model, q):
    out = asyncio.run(agent_router.select_agent(q, space="ops"))
    assert out.get("out_of_module") and out["wanted"] == "compliance", out


def test_a_work_order_question_about_vendor_blocks_stays_with_work_orders(no_model):
    with pytest.raises(Reached):  # wo_engine is Ops Brain's: it goes on to routing, not to vendors
        out = asyncio.run(agent_router.select_agent("Which work orders are blocked by vendors?", space="ops"))
        assert out["agent"] != "contract_performance"


def test_a_space_with_compliance_routes_as_before(no_model):
    with pytest.raises(Reached):
        asyncio.run(agent_router.select_agent("Which vendors are blocked?", space="full"))


def test_the_vendors_engine_has_the_tool():
    from src.agents.orchestrator import PHASE2_ENGINE_TOOLS

    names = [getattr(t, "name", "") for t in PHASE2_ENGINE_TOOLS["contract_performance"]]
    assert "list_blocked_vendors" in names


def test_the_tool_reads_only_the_vendor_record_of_the_callers_company(monkeypatch):
    from src import database
    from src.http_client import caller_organization_id

    seen = []

    class _R:
        def mappings(self):
            return self

        def all(self):
            return [{"vendor_name": "Pennard Fire Services", "blocked_accreditation_type": "BAFE_SP203_1"},
                    {"vendor_name": "ProudCastle Fire", "blocked_accreditation_type": None}]

    class _S:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def execute(self, sql, params=None):
            seen.append((str(sql), dict(params or {})))
            return _R()

    monkeypatch.setattr(database, "AsyncSessionLocal", lambda: _S())
    tok = caller_organization_id.set("ad88d1ed-4376-447c-8196-e4e5b81d5af6")
    try:
        out = asyncio.run(getattr(cp.list_blocked_vendors, "coroutine", cp.list_blocked_vendors)())
    finally:
        caller_organization_id.reset(tok)
    assert out["ok"] and out["count"] == 2
    assert out["blocked"][0] == {"vendor": "Pennard Fire Services", "blocked_for": "BAFE_SP203_1"}
    sqls = " ".join(s for s, _ in seen)
    assert "SET TRANSACTION READ ONLY" in sqls
    query, params = next((q, p) for q, p in seen if "FROM plenum_cafm.vendors v" in q and "compliance_certificates" not in q)
    assert "organization_id::text = :org" in query and params["org"] == "ad88d1ed-4376-447c-8196-e4e5b81d5af6"
    assert "compliance_certificates" not in query and "expiry" not in query


def test_without_a_company_it_reads_nothing(monkeypatch):
    from src import database

    def refuse():
        raise AssertionError("read without a company")

    monkeypatch.setattr(database, "AsyncSessionLocal", refuse)
    out = asyncio.run(getattr(cp.list_blocked_vendors, "coroutine", cp.list_blocked_vendors)())
    assert out["ok"] is False


# ── at one building (3 Nov 2026) ─────────────────────────────────────────────────────────
# "how many open work orders and blocked vendors does Bishopsgate Tower have?" - the blocked vendors
# that work there: a blocked vendor with a work order at the building, from the same vendor record.

def _fake_session(monkeypatch, rows_by_marker):
    from src import database

    seen = []

    class _R:
        def __init__(self, rows):
            self.rows = rows

        def mappings(self):
            return self

        def all(self):
            return self.rows

    class _S:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def execute(self, sql, params=None):
            s = str(sql)
            seen.append((s, dict(params or {})))
            for marker, rows in rows_by_marker:
                if marker in s:
                    return _R(rows)
            return _R([])

    monkeypatch.setattr(database, "AsyncSessionLocal", lambda: _S())
    return seen


def _call(**kw):
    from src.http_client import caller_organization_id

    tok = caller_organization_id.set("ad88d1ed-4376-447c-8196-e4e5b81d5af6")
    try:
        return asyncio.run(getattr(cp.list_blocked_vendors, "coroutine", cp.list_blocked_vendors)(**kw))
    finally:
        caller_organization_id.reset(tok)


def test_at_a_building_it_names_the_blocked_vendors_that_work_there(monkeypatch):
    seen = _fake_session(monkeypatch, [
        ("SELECT DISTINCT name", [{"n": "Bishopsgate Tower"}, {"n": "Manchester Town Hall"}]),
        ("plenum_cafm.work_orders w", [
            {"vendor_name": "Pennard Fire Services", "blocked_for": "BAFE_SP203_1", "work_orders": 12, "open_work_orders": 2}]),
        ("FROM plenum_cafm.vendors v", [{"vendor_name": "Pennard Fire Services", "blocked_accreditation_type": "BAFE_SP203_1"},
                                         {"vendor_name": "Northgate", "blocked_accreditation_type": None}]),
    ])
    out = _call(building_name="bishopsgate")
    assert out["ok"] and out["building"] == "Bishopsgate Tower"
    assert out["count"] == 1 and out["blocked"][0]["vendor"] == "Pennard Fire Services"
    assert out["blocked"][0]["open_work_orders"] == 2 and out["blocked"][0]["blocked_for"] == "BAFE_SP203_1"
    assert out["blocked_company_wide"] == 2
    q, params = next((s, p) for s, p in seen if "plenum_cafm.work_orders w" in s)
    assert params["bname"] == "Bishopsgate Tower" and params["org"] == "ad88d1ed-4376-447c-8196-e4e5b81d5af6"
    assert "w.organization_id::text = :org" in q and "b.organization_id::text = :org" in q
    assert all("SET TRANSACTION READ ONLY" in s or s.lstrip().upper().startswith("SELECT") for s, _ in seen)


def test_a_building_it_cannot_find_is_said_not_guessed(monkeypatch):
    _fake_session(monkeypatch, [("SELECT DISTINCT name", [{"n": "Bishopsgate Tower"}])])
    out = _call(building_name="Kew House")
    assert out["ok"] is False and "Kew House" in out["error"]


# ── pre-push review (3 Nov 2026) ─────────────────────────────────────────────────────────
# vendors.id is varchar on production (legacy ids) and uuid on the test database, work_orders
# vendor ids are uuid: `w.vendor_id = v.id` raised "operator does not exist" on one of them. Two
# vendor rows with one name each matched every work order by name and counted it twice. And a
# user restricted to some buildings saw every blocked vendor in the company.

def _restricted(building_ids):
    from src.services import principal as P

    class _P:
        organization_id = "ad88d1ed-4376-447c-8196-e4e5b81d5af6"

    p = _P()
    p.building_ids = building_ids
    return P.caller_principal.set(p)


def test_vendor_ids_are_compared_as_text_and_each_work_order_counted_once(monkeypatch):
    seen = _fake_session(monkeypatch, [
        ("SELECT DISTINCT name", [{"n": "Bishopsgate Tower"}]),
        ("plenum_cafm.work_orders", [{"vendor_name": "Pennard Fire Services", "blocked_for": "BAFE_SP203_1",
                                      "work_orders": 1, "open_work_orders": 1}]),
    ])
    out = _call(building_name="Bishopsgate Tower")
    assert out["ok"]
    q = next(s for s, _ in seen if "plenum_cafm.work_orders" in s)
    assert "w.vendor_id = v.id" not in q and "w.vendor_id::text = v.id::text" in q
    assert "count(DISTINCT w.id)" in q
    assert "JOIN plenum_cafm.buildings" not in q, "a building named twice would double every order"


def test_a_restricted_user_sees_only_the_vendors_on_their_buildings(monkeypatch):
    from src.services import principal as P

    seen = _fake_session(monkeypatch, [("FROM plenum_cafm.vendors v", [])])
    tok = _restricted(["11111111-1111-1111-1111-111111111111"])
    try:
        _call()
        _call(building_name="Bishopsgate Tower")
    finally:
        P.caller_principal.reset(tok)
    vendor_reads = [(s, p) for s, p in seen if "FROM plenum_cafm.vendors v" in s]
    assert vendor_reads and all("scope_" in s for s, _ in vendor_reads), vendor_reads
    building_lookup = [(s, p) for s, p in seen if "SELECT DISTINCT name" in s]
    assert building_lookup and all("building_id = ANY" in s for s, _ in building_lookup)



def test_a_space_without_compliance_never_reads_certificates(monkeypatch):
    """S-5's at-risk list is certificate detail: Ops Brain and Assets do not show it (module separation, 2 Nov 2026)."""
    from src.agents import spaces

    seen = _fake_session(monkeypatch, [("FROM plenum_cafm.vendors v", [
        {"vendor_name": "Pennard Fire Services", "blocked_accreditation_type": "BAFE_SP203_1"}])])
    tok = spaces.active_space.set("ops")
    try:
        out = _call()
    finally:
        spaces.active_space.reset(tok)
    assert out["ok"] and out["at_risk"] == []
    assert not any("compliance_certificates" in s for s, _ in seen)
