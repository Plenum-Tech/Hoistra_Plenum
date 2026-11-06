"""A planned step that fails says so in the log, a bad argument does not sink it, the plan stays
inside the person's space, and an untraced figure never reaches the answer as a note (3 Nov 2026).

"How many floors does Bishopsgate Tower have, and how many open work orders and blocked vendors
does it have?" in the Assets Operating System came back with "Blocked vendors: unavailable - the
vendor-blocker check failed". Nothing in the log said why: a step that raised was turned into
"FAILED: ..." for the writer and never logged. One way a step raises with no trace is the planner
handing a tool an argument of the wrong type (`work_orders: "open"`), which the tool's schema
refuses before its body runs. The planner had no tool that answers "blocked vendors at a building"
(list_blocked_vendors read the whole company), and its catalogue offered every engine and tool,
compliance included, whatever space the question was asked in.

The planned answer's figure check appended "_Check before quoting: 3951.2, 4317.6, 282.4 could
not be traced to the step results." to what the person read - an internal check, worded for us.
"""
from __future__ import annotations

import asyncio

import pytest

from src.agents import planner, spaces
import src.agents.orchestrator as O


def _orch():
    return O.DeepAgentOrchestrator.__new__(O.DeepAgentOrchestrator)


# ── the planner stays inside the space ───────────────────────────────────────────────────

def _in_space(space, fn):
    tok = spaces.active_space.set(space)
    try:
        return fn()
    finally:
        spaces.active_space.reset(tok)


def test_the_assets_space_plans_without_compliance():
    tools = _in_space("assets", lambda: _orch()._planner_tools())
    engines = _in_space("assets", planner.planning_engines)
    assert "compliance" not in engines and "wo_engine" in engines
    assert "list_building_certificates" not in tools and "list_vendor_accreditations" not in tools
    # tools shared with an engine the space has stay
    assert "work_order_blockers" in tools and "list_blocked_vendors" in tools and "answer_from_records" in tools


def test_a_plan_naming_an_engine_the_space_lacks_is_refused():
    tools = {"answer_from_records": "x"}
    plan = {"steps": [{"id": "s1", "kind": "engine", "target": "compliance", "ask": "lapsed certificates at Bishopsgate"}]}
    ok, why = _in_space("assets", lambda: planner.validate(plan, tools))
    assert ok is None and "compliance" in why


def test_the_full_platform_and_no_space_plan_with_everything():
    for space in ("full", None):
        tools = _in_space(space, lambda: _orch()._planner_tools())
        assert "list_building_certificates" in tools
        assert "compliance" in _in_space(space, planner.planning_engines)


def test_a_compliance_tool_is_not_run_from_a_space_without_compliance():
    with pytest.raises(ValueError):
        _in_space("assets", lambda: asyncio.run(_orch()._run_planned_tool("list_building_certificates", {})))


# ── blocked vendors have a tool of their own ─────────────────────────────────────────────

def test_the_planner_is_told_blocked_vendors_are_list_blocked_vendors():
    assert "list_blocked_vendors" in planner.PLAN_PROMPT
    assert "list_blocked_vendors" in planner.TOOL_NOTES["work_order_blockers"]


# ── a bad argument does not sink the step ────────────────────────────────────────────────

def test_a_wrongly_typed_argument_is_coerced_or_dropped(monkeypatch):
    from src.agents import thread_scope

    seen = []

    async def fake_body(**kw):
        seen.append(kw)
        return {"ok": True}

    monkeypatch.setattr(thread_scope.work_order_blockers, "coroutine", fake_body)
    out = asyncio.run(_orch()._run_planned_tool(
        "work_order_blockers", {"building_name": "Bishopsgate Tower", "work_orders": "WO-1", "status": "open",
                                "period": {"label": "this month"}}))
    assert out == {"ok": True}
    assert seen[-1]["work_orders"] == ["WO-1"], "a single code becomes a one-item list"
    assert "status" not in seen[-1], "an argument the tool does not take is dropped"
    assert "period" not in seen[-1], "a value of the wrong shape is dropped, not fatal"
    assert seen[-1]["building_name"] == "Bishopsgate Tower"


def test_a_failed_step_is_logged_with_its_tool_and_error(monkeypatch):
    logged = []
    monkeypatch.setattr(planner.log, "warning", lambda ev, **kw: logged.append((ev, kw)))

    async def run_tool(name, args):
        raise RuntimeError("the read broke")

    async def run_engine(*a):
        raise AssertionError("no engine step")

    plan = {"steps": [{"id": "s2", "kind": "tool", "target": "work_order_blockers", "ask": "blocked vendors",
                       "args": {"building_name": "Bishopsgate Tower"}, "depends_on": [], "why": ""}]}
    res = asyncio.run(planner.execute(plan, run_engine=run_engine, run_tool=run_tool))
    assert res["s2"]["ok"] is False
    ev = [kw for e, kw in logged if e == "planner.step_failed"]
    assert ev and ev[0]["target"] == "work_order_blockers" and "the read broke" in ev[0]["error"]


def test_a_tool_that_answers_with_an_error_is_logged_too(monkeypatch):
    logged = []
    monkeypatch.setattr(planner.log, "warning", lambda ev, **kw: logged.append((ev, kw)))

    async def run_tool(name, args):
        return {"ok": False, "error": "No company to read vendors for."}

    async def run_engine(*a):
        raise AssertionError("no engine step")

    plan = {"steps": [{"id": "s2", "kind": "tool", "target": "list_blocked_vendors", "ask": "blocked vendors",
                       "args": {}, "depends_on": [], "why": ""}]}
    asyncio.run(planner.execute(plan, run_engine=run_engine, run_tool=run_tool))
    ev = [kw for e, kw in logged if e == "planner.step_answered_error"]
    assert ev and ev[0]["target"] == "list_blocked_vendors" and "No company" in ev[0]["error"]


# ── the figure check never reaches the answer ────────────────────────────────────────────

def test_an_untraced_figure_is_rewritten_once_and_never_shown_as_a_note(monkeypatch):
    drafts = iter(["Pennard's total is **3951.2**.", "Pennard's latest scorecard is **71.2**."])
    asked = []

    async def synth(question, plan, results, llm):
        asked.append(question)
        return next(drafts)

    monkeypatch.setattr(planner, "synthesise", synth)
    monkeypatch.setattr(planner, "verify_claims", lambda *a: [])
    results = {"s1": {"output": {"scorecard": 71.2}, "ok": True, "tool_calls": [], "error": None}}
    answer, check = asyncio.run(_orch()._write_planned_answer("Pennard's scorecard?", {"steps": []}, results))
    assert answer == "Pennard's latest scorecard is **71.2**."
    assert len(asked) == 2 and "3951.2" in asked[1], "the rewrite is told which figure did not trace"
    assert check["ok"]


def test_a_figure_still_untraced_after_the_rewrite_stays_out_of_the_text(monkeypatch):
    async def synth(question, plan, results, llm):
        return "The three totals are **3951.2**, **4317.6** and **282.4**."

    monkeypatch.setattr(planner, "synthesise", synth)
    monkeypatch.setattr(planner, "verify_claims", lambda *a: [])
    results = {"s1": {"output": {"scorecard": 71.2}, "ok": True, "tool_calls": [], "error": None}}
    answer, check = asyncio.run(_orch()._write_planned_answer("totals?", {"steps": []}, results))
    assert "Check before quoting" not in answer and "could not be traced" not in answer
    assert not check["ok"] and "3951.2" in check["unmatched"], "the check is kept for the trace"


# ── an engine step that reads nothing is asked again, then counted as failed ─────────────
# 3 Nov 2026, Compliance Engine: "Which buildings have lapsed certificates and overdue work
# orders?" - the contract_performance step wrote 98 tokens ("Plan: identify the live work-order
# and building fields, validate which due-date/status combination defines overdue...") and
# stopped without calling a tool. The planner took that text as the step's answer, and the
# reply said the overdue work orders "could not be accessed".

def _engine_plan(depends_on=()):
    return {"steps": [{"id": "s2", "kind": "engine", "target": "contract_performance",
                       "ask": "Which buildings have overdue work orders?", "args": {},
                       "depends_on": list(depends_on), "why": ""}]}


async def _no_tool(*a):
    raise AssertionError("no tool step")


def test_an_engine_step_that_read_no_records_is_asked_again(monkeypatch):
    asks = []

    async def run_engine(engine, ask, on_ev):
        asks.append(ask)
        if len(asks) == 1:
            return "Plan: identify the live work-order and building fields.", []
        return "Bishopsgate Tower: 11 overdue.", [{"tool": "work_order_blockers", "output": {"ok": True}}]

    res = asyncio.run(planner.execute(_engine_plan(), run_engine=run_engine, run_tool=_no_tool))
    assert len(asks) == 2 and planner.READ_THE_RECORDS in asks[1]
    assert res["s2"]["ok"] is True and res["s2"]["output"] == "Bishopsgate Tower: 11 overdue."
    assert res["s2"]["tool_calls"]


def test_an_engine_step_that_still_read_nothing_is_a_failed_step(monkeypatch):
    logged = []
    monkeypatch.setattr(planner.log, "warning", lambda ev, **kw: logged.append((ev, kw)))

    async def run_engine(engine, ask, on_ev):
        return "Plan: identify the live work-order fields.", []

    res = asyncio.run(planner.execute(_engine_plan(), run_engine=run_engine, run_tool=_no_tool))
    assert res["s2"]["ok"] is False and "no records" in res["s2"]["error"]
    assert any(e == "planner.step_read_nothing" for e, _ in logged)


def test_an_engine_step_working_from_earlier_results_may_answer_without_a_tool():
    calls = []

    async def run_engine(engine, ask, on_ev):
        calls.append(ask)
        if "earlier steps" in ask:
            return "Bishopsgate Tower is in both.", []
        return "x", [{"tool": "t", "output": {}}]

    plan = {"steps": [{"id": "s1", "kind": "engine", "target": "compliance", "ask": "lapsed", "args": {},
                       "depends_on": [], "why": ""},
                      {"id": "s2", "kind": "engine", "target": "contract_performance", "ask": "both",
                       "args": {}, "depends_on": ["s1"], "why": ""}]}
    res = asyncio.run(planner.execute(plan, run_engine=run_engine, run_tool=_no_tool))
    assert res["s2"]["ok"] is True and len(calls) == 2


def test_the_figure_rewrite_is_not_kept_when_it_brings_back_a_contradiction(monkeypatch):
    # Pre-push review (3 Nov 2026): the figure rewrite started again from the question, not from
    # the draft the claim check had corrected, and was kept without the claim check.
    drafts = iter(["Pennard is blocked; total **3951.2**.", "Pennard is not blocked; scorecard **71.2**."])

    async def synth(question, plan, results, llm):
        return next(drafts)

    monkeypatch.setattr(planner, "synthesise", synth)
    monkeypatch.setattr(planner, "verify_claims",
                        lambda answer, q, r: ["Pennard is blocked"] if "not blocked" in answer else [])
    results = {"s1": {"output": {"scorecard": 71.2, "blocked": True}, "ok": True, "tool_calls": [], "error": None}}
    answer, _ = asyncio.run(_orch()._write_planned_answer("Pennard?", {"steps": []}, results))
    assert answer == "Pennard is blocked; total **3951.2**."
