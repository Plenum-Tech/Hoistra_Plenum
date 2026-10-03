"""The thread's working set (memory phase C-lite): what a follow-up means, and how it binds the tools.

Taken from the 2 Oct 2026 thread where "out of those, which breach SLA?" answered for the whole
estate after three follow-ups had held the scope."""
from __future__ import annotations

import asyncio
from datetime import date

from src.agents import thread_scope as ts

TODAY = date(2026, 10, 2)


def test_a_follow_up_is_recognised_and_a_new_scope_is_not():
    assert ts.is_followup("out those which work orders we can't proceed bcoz of vendor compliance?")
    assert ts.is_followup("Which of these save cost, with a corrective action plan?")
    assert ts.is_followup("and the SLA breaches among them?")
    assert not ts.is_followup("How many work orders were raised at Bishopsgate Tower last month?")
    assert not ts.is_followup("out of those, now across all buildings, which are overdue?")


def test_periods_are_parsed_to_bounds_with_the_end_exclusive():
    assert ts.parse_period("raised last month", TODAY) == {"from": "2026-09-01", "to": "2026-10-01",
                                                           "label": "September 2026", "key": "last_month"}
    assert ts.parse_period("in Sept 2026", TODAY)["from"] == "2026-09-01"
    assert ts.parse_period("in December 2025", TODAY) == {"from": "2025-12-01", "to": "2026-01-01",
                                                          "label": "December 2025", "key": None}
    assert ts.parse_period("this month", TODAY)["key"] == "this_month"
    assert ts.parse_period("the last 90 days", TODAY)["key"] == "last_90_days"
    assert ts.parse_period("which are overdue", TODAY) is None


Q1 = "How many work orders were raised at Bishopsgate Tower last month, and how many of them are closed?"
A1 = "794 raised in September 2026; 781 completed, 13 open: WO-B-301-4527, WO-B-301-4562 and WO-B-301-4568 among them."
T1 = [{"tool": "answer_from_records", "input": {"question": Q1}},
      {"tool": "get_cost_savings", "input": {"building_name": "Bishopsgate Tower", "period": "last_month"}}]


def test_the_anchor_question_sets_the_working_set_and_follow_ups_keep_it():
    ws = ts.derive(None, Q1, A1, T1, TODAY)
    assert ws["building_name"] == "Bishopsgate Tower" and ws["building_code"] == "B-301"
    assert ws["period"]["label"] == "September 2026"
    assert ws["work_orders"] == ["WO-B-301-4527", "WO-B-301-4562", "WO-B-301-4568"]
    # "out of those": the anchor stays; what this answer named is recorded beside it.
    ws2 = ts.derive(ws, "out of those which are blocked by vendor compliance?",
                    "Two: WO-B-301-4562 and WO-B-301-4568.", [{"tool": "work_order_blockers", "input": {}}], TODAY)
    assert ws2["building_name"] == "Bishopsgate Tower" and ws2["work_orders"] == ws["work_orders"]
    assert ws2["last_mentioned"] == ["WO-B-301-4562", "WO-B-301-4568"] and ws2["turns"] == 2
    # A question with its own scope replaces it.
    ws3 = ts.derive(ws2, "What is open at Harbour Point this month?", "WO-B-101-31 is open.",
                    [{"tool": "answer_from_records", "input": {"building_name": "Harbour Point"}}], TODAY)
    assert ws3["building_name"] == "Harbour Point" and ws3["work_orders"] == ["WO-B-101-31"] and ws3["turns"] == 1
    # Small talk with no scope of its own leaves the working set as it was.
    assert ts.derive(ws3, "thanks", "You're welcome.", [], TODAY) == ws3


def test_the_model_is_told_the_set_as_a_hard_filter():
    block = ts.scope_block(ts.derive(None, Q1, A1, T1, TODAY))
    assert block.startswith("## Working set (hard filter")
    assert "building Bishopsgate Tower (B-301)" in block and "period September 2026 (2026-09-01 to 2026-10-01" in block
    assert "WO-B-301-4527" in block and "never widen" in block
    assert ts.scope_block(None) == ""


def test_tools_called_without_a_building_take_it_from_the_active_scope(monkeypatch):
    from src.agents import cost_savings_agent as cs

    ws = ts.derive(None, Q1, A1, T1, TODAY)
    seen = {}

    async def resolve(name, bid):
        seen["resolved"] = (name, bid)
        return "bld-301", None

    class _R:
        def json(self):
            return {"ok": True, "ledger": {}}

    async def req(method, base, path, **kw):
        seen["path"], seen["params"] = path, kw["params"]
        return _R()

    monkeypatch.setattr("src.agents.energy_intelligence_agent._resolve_building", resolve)
    monkeypatch.setattr(cs, "_request", req)
    monkeypatch.setattr(ts, "_request", req)

    async def run():
        ts.active_scope.set(ws)
        out = await cs.get_cost_savings.ainvoke({})
        assert seen["params"] == {"period": "last_month", "building_id": "bld-301"} and out["building"] == "Bishopsgate Tower"
        out = await ts.work_order_blockers.ainvoke({})
        assert seen["path"] == "/api/work-orders/blockers"
        assert seen["params"]["building_id"] == "bld-301"
        assert seen["params"]["period_from"] == "2026-09-01" and seen["params"]["period_to"] == "2026-10-01"
        assert seen["params"]["wo_code"] == ws["work_orders"]
        assert out["scope"]["period"] == "September 2026"
        # No scope (a question with its own words): nothing is defaulted.
        ts.active_scope.set(None)
        seen.clear()
        await cs.get_cost_savings.ainvoke({})
        assert seen["params"] == {"period": "last_month"}
        assert ts.scope_hint() == ""
        ts.active_scope.set(ws)
        assert ts.scope_hint() == " (at Bishopsgate Tower in September 2026)"

    asyncio.run(run())
