"""Work-order SLA facts are worked out from the record (agents/wo_sla.py, 2 Nov 2026).

"Please check work order WO-B-301-4564 at Bishopsgate Tower ... did it meet its response SLA, and how
has Kestamere Fire Protection been performing at this building overall?" came back "met" (attended
before sla_due_at - the COMPLETION due time) for an order still open 34 days past it, and with only
Kestamere's vendor-wide scorecard though its 325 orders at the building carry per-order scores.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from src.agents import wo_sla

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)
UTC = timezone.utc


def test_an_open_order_past_its_due_time_is_overdue_whatever_its_response():
    row = {"wo_code": "WO-B-301-4564", "status": "In Progress", "priority": "P2",
           "reported_at": datetime(2026, 9, 27, 9, 0, tzinfo=UTC), "attended_at": datetime(2026, 9, 27, 12, 0, tzinfo=UTC),
           "sla_due_at": datetime(2026, 9, 29, 9, 0), "completed_at": None, "sla_response_met": None}
    j = wo_sla.judge(row, NOW)
    assert j["completion"]["met"] is False and j["completion"]["overdue_days"] == 34 and j["completion"]["still_open"]
    assert j["response"]["minutes_to_respond"] == 180
    assert j["response"]["met"] is None and "not scored" in j["response"]["basis"]
    assert "never the response target" in j["note"]


def test_a_scored_order_carries_its_response_result_and_its_completion():
    row = {"wo_code": "WO-B-301-9730", "status": "Completed", "reported_at": datetime(2026, 4, 1, 9, 50, tzinfo=UTC),
           "attended_at": datetime(2026, 4, 1, 13, 0, tzinfo=UTC), "sla_due_at": datetime(2026, 4, 1, 13, 50),
           "completed_at": datetime(2026, 4, 1, 13, 25), "sla_response_met": False}
    j = wo_sla.judge(row, NOW)
    assert j["response"]["met"] is False and j["completion"]["met"] is True


def test_a_vendors_record_at_a_building_is_counted_order_by_order():
    rows = [
        {"wo_code": "A", "status": "Completed", "sla_due_at": datetime(2026, 4, 2), "completed_at": datetime(2026, 4, 1),
         "sla_response_met": True, "first_fix": True},
        {"wo_code": "B", "status": "Completed", "sla_due_at": datetime(2026, 4, 2), "completed_at": datetime(2026, 4, 3),
         "sla_response_met": False, "recall": True},
        {"wo_code": "C", "status": "In Progress", "sla_due_at": datetime(2026, 9, 29), "completed_at": None,
         "sla_response_met": None},
    ]
    out = wo_sla.summarise("Kestamere Fire Protection", "Bishopsgate Tower", rows, NOW)
    assert out["work_orders"] == 3 and out["by_status"] == {"Completed": 2, "In Progress": 1}
    assert out["response_sla"] == {"met": 1, "breached": 1, "not_scored": 1, "met_rate_pct": 50.0}
    assert out["completion_sla"]["met"] == 1 and out["completion_sla"]["breached"] == 1
    assert out["completion_sla"]["open_overdue"] == 1 and out["open_overdue"] == ["C"]
    assert out["first_fix"] == 1 and out["recalls"] == 1
    assert "Do not use a vendor-wide scorecard" in out["answer_rules"]


class _Fake:
    def __init__(self, answers):
        self.answers, self.seen = answers, []

    def __call__(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, sql, params=None):
        sql = str(sql)
        self.seen.append((sql, dict(params or {})))
        rows = []
        for key, value in self.answers:
            if key in sql:
                rows = value
                break

        class _R:
            def mappings(self_inner):
                return self_inner

            def all(self_inner):
                return rows
        return _R()


def _company(monkeypatch, fake):
    from src import database
    from src.http_client import caller_organization_id

    monkeypatch.setattr(database, "AsyncSessionLocal", fake)
    return caller_organization_id.set("ad88d1ed-4376-447c-8196-e4e5b81d5af6")


def test_the_vendor_read_matches_names_and_reads_only_the_callers_company(monkeypatch):
    from src.http_client import caller_organization_id

    fake = _Fake([("FROM plenum_cafm.vendors t WHERE", [{"n": "Kestamere Fire Protection"}, {"n": "Pennard Fire Services"}]),
                  ("FROM plenum_cafm.buildings t WHERE", [{"n": "Bishopsgate Tower"}, {"n": "Manchester Town Hall"}]),
                  ("FROM plenum_cafm.work_orders w", [{"wo_code": "X", "status": "Completed", "sla_response_met": True}])])
    tok = _company(monkeypatch, fake)
    try:
        out = asyncio.run(wo_sla.vendor_at_building("kestamere", "Bishopsgate"))
    finally:
        caller_organization_id.reset(tok)
    assert out["ok"] and out["vendor"] == "Kestamere Fire Protection" and out["building"] == "Bishopsgate Tower"
    assert out["work_orders"] == 1
    sqls = [s for s, _ in fake.seen]
    assert any("SET TRANSACTION READ ONLY" in s for s in sqls)
    wo_sql, params = next((s, p) for s, p in fake.seen if "FROM plenum_cafm.work_orders w" in s)
    assert "w.organization_id::text = :org" in wo_sql and params["org"] == "ad88d1ed-4376-447c-8196-e4e5b81d5af6"
    assert "vendor_wo_scores" in wo_sql
    # Pre-push review (3 Nov 2026): vendors.id is varchar on production, uuid on the test database;
    # and a vendor (or building) named twice must not count each work order twice.
    assert "w.vendor_id = v.id" not in wo_sql and "w.vendor_id::text = v.id::text" in wo_sql
    assert "JOIN plenum_cafm.vendors" not in wo_sql and "JOIN plenum_cafm.buildings" not in wo_sql


def test_an_unmatched_vendor_or_building_is_said_not_guessed(monkeypatch):
    from src.http_client import caller_organization_id

    fake = _Fake([("FROM plenum_cafm.vendors t WHERE", [{"n": "Kestamere Fire Protection"}]),
                  ("FROM plenum_cafm.buildings t WHERE", [{"n": "Bishopsgate Tower"}])])
    tok = _company(monkeypatch, fake)
    try:
        out = asyncio.run(wo_sla.vendor_at_building("Acme Lifts", "Bishopsgate Tower"))
    finally:
        caller_organization_id.reset(tok)
    assert out["ok"] is False and out["found"] is False and "Acme Lifts" in out["error"]
    assert not any("FROM plenum_cafm.work_orders" in s for s, _ in fake.seen)


def test_both_engines_hold_the_tool_and_the_status_track_carries_the_check():
    import inspect

    from src.agents import wo_engine_agent as wo
    from src.agents.orchestrator import PHASE2_ENGINE_TOOLS

    for engine in ("wo_engine", "contract_performance"):
        assert "vendor_work_orders_at_building" in [getattr(t, "name", "") for t in PHASE2_ENGINE_TOOLS[engine]], engine
    track_src = inspect.getsource(getattr(wo.get_work_order_status_track, "coroutine", wo.get_work_order_status_track))
    assert 'track["sla_check"] = check' in track_src
