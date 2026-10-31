"""The orchestrator's planner: when a question is planned, what a plan may contain, how it runs
(parallel where independent), what the answer may state, and that every stage is a span."""
from __future__ import annotations

import asyncio
import json
import uuid

import pytest

from src.agents import planner, trace
from src.http_client import caller_organization_id
from src.services.principal import Principal, caller_principal

TOOLS = {"answer_from_records": "records", "work_order_blockers": "blocked by vendor", "get_cost_savings": "savings"}


def test_a_question_that_asks_for_more_than_one_thing_is_planned():
    assert planner.is_multi_part("How many work orders were raised at Bishopsgate Tower last month, and how many of them are closed?")
    assert planner.is_multi_part("Which of those are blocked by vendor compliance and what would it cost to fix them?")
    assert planner.is_multi_part("Compare Apex Mechanical and Talvern Lifts on SLA breaches this quarter")
    assert planner.is_multi_part("Which certificates expire this month? Which vendors do they belong to?")
    assert not planner.is_multi_part("How many boilers at B-301?")
    assert not planner.is_multi_part("Show me the lapsed accreditations at Bishopsgate Tower")
    assert not planner.is_multi_part("hi")


def test_a_plan_is_validated_against_the_catalogue():
    good = {"goal": "g", "steps": [
        {"id": "s1", "kind": "engine", "target": "wo_engine", "ask": "Count work orders at B-301 for Sep 2026 by status", "depends_on": [], "why": "count"},
        {"id": "s2", "kind": "tool", "target": "work_order_blockers", "ask": "the open ones from s1", "depends_on": ["s1", "nope"], "why": "blockers"}],
        "answer_shape": "table"}
    plan, why = planner.validate(good, TOOLS)
    assert why is None and plan["mode"] == "multi" and plan["steps"][1]["depends_on"] == ["s1"]   # unknown dep dropped
    assert planner.validate({"steps": [{"id": "s1", "kind": "engine", "target": "payroll", "ask": "x"}]}, TOOLS)[1].startswith("step s1: unknown engine")
    assert planner.validate({"steps": [{"id": "s1", "kind": "tool", "target": "drop_tables", "ask": "x"}]}, TOOLS)[1].startswith("step s1: unknown tool")
    assert planner.validate({"steps": []}, TOOLS)[1].startswith("plan has 0 steps")
    assert planner.validate({"steps": [{"id": "s1", "kind": "engine", "target": "wo_engine", "ask": ""}]}, TOOLS)[1] == "step s1: no ask"
    assert planner.validate("not json", TOOLS)[1] == "plan is not an object with steps"
    one = planner.one_step_plan("energy_intelligence", "energy question")
    assert one["mode"] == "single" and one["steps"][0]["target"] == "energy_intelligence"


def test_make_plan_parses_the_models_json_and_falls_back_on_garbage():
    async def good_llm(system, user, role):
        assert role == "plan" and "CATALOGUE" in user and "WORKING SET" in user
        return 'Here it is:\n{"goal": "g", "steps": [{"id": "s1", "kind": "tool", "target": "get_cost_savings", "ask": "savings at B-301"}], "answer_shape": "list"}'

    plan, why = asyncio.run(planner.make_plan("q", context="WORKING SET (hard filter): building B-301", tools=TOOLS, llm=good_llm))
    assert why is None and plan["steps"][0]["target"] == "get_cost_savings"

    async def bad_llm(system, user, role):
        return "I cannot plan this."

    assert asyncio.run(planner.make_plan("q", context="", tools=TOOLS, llm=bad_llm)) == (None, "plan is not an object with steps")

    async def boom(system, user, role):
        raise RuntimeError("rate limited")

    assert asyncio.run(planner.make_plan("q", context="", tools=TOOLS, llm=boom))[1].startswith("planner call failed")


@pytest.fixture
def traced(monkeypatch):
    monkeypatch.setattr(trace, "_ready", True)
    writes = []

    async def fake_write(t, answer, tool_calls, summary):
        writes.append(t)

    monkeypatch.setattr(trace, "_write", fake_write)
    p = Principal(user_id=uuid.uuid4(), email="fm@example.com", organization_id=uuid.uuid4(), role="admin", can_ingest=True, building_ids=None)
    caller_principal.set(p)
    caller_organization_id.set(str(p.organization_id))
    trace._turn.set(None)
    return writes


def test_the_plan_runs_independent_steps_together_and_dependent_ones_after_with_their_inputs(traced):
    plan, _ = planner.validate({"goal": "g", "steps": [
        {"id": "s1", "kind": "engine", "target": "wo_engine", "ask": "count at B-301", "depends_on": []},
        {"id": "s2", "kind": "tool", "target": "get_cost_savings", "ask": "savings at B-301", "depends_on": []},
        {"id": "s3", "kind": "tool", "target": "work_order_blockers", "ask": "blockers for the open ones", "depends_on": ["s1"]}],
        "answer_shape": "x"}, TOOLS)
    order, events = [], []

    async def run_engine(engine, ask, on_ev):
        order.append(("engine", engine))
        await asyncio.sleep(0.02)
        return "794 raised, 781 completed, 13 open", [{"tool": "answer_from_records", "input": {}, "output": {"total": 794}}]

    async def run_tool(name, args):
        order.append(("tool", name, args))
        if name == "work_order_blockers":
            assert "794 raised" in args["question"] and "Scope (hard filter): building B-301" in args["question"]
        return {"ok": True, "blocked": 2}

    async def on_event(ev):
        events.append(ev["type"])

    async def run():
        trace.on_activity({"agent": "orchestrator", "stage": "turn", "direction": "input", "summary": "q", "payload": {"message": "q"}})
        trace.on_plan(plan, source="planner")
        results = await planner.execute(plan, run_engine=run_engine, run_tool=run_tool, scope_hint="building B-301", on_event=on_event)
        t = trace.current()
        trace.on_activity({"agent": "orchestrator", "stage": "turn", "direction": "output", "summary": "ok", "payload": {"answer": "x"}})
        await asyncio.sleep(0)
        return results, t

    results, t = asyncio.run(run())
    # s1 and s2 start together (s2's tool call lands before s1's slow engine returns); s3 only after s1.
    assert order[0][0] == "engine" and order[1][1] == "get_cost_savings" and order[2][1] == "work_order_blockers"
    assert results["s3"]["ok"] and results["s1"]["tool_calls"][0]["tool"] == "answer_from_records"
    assert "agent_switch" in events and "tool_started" in events and "tool_completed" in events
    plan_span = next(s for s in t.spans if s.kind == "plan")
    steps = [s for s in t.spans if s.kind == "step"]
    assert plan_span.name == "plan: 3 steps" and plan_span.output["plan"]["mode"] == "multi"
    assert [s.name for s in steps] == ["s1: wo_engine", "s2: get_cost_savings", "s3: work_order_blockers"]
    assert all(s.parent_id == plan_span.id and not s.open for s in steps)
    assert steps[2].input["depends_on"] == ["s1"] and steps[2].output["output"] == {"ok": True, "blocked": 2}
    assert t.route == "multi:wo_engine,get_cost_savings,work_order_blockers"


def test_a_failed_step_is_a_fact_for_the_synthesis_not_the_end_of_the_turn(traced):
    plan, _ = planner.validate({"steps": [{"id": "s1", "kind": "tool", "target": "get_cost_savings", "ask": "x"}]}, TOOLS)

    async def run_tool(name, args):
        raise RuntimeError("ops-intel timeout")

    async def run_engine(*a):
        return "", []

    async def run():
        trace.on_activity({"agent": "orchestrator", "stage": "turn", "direction": "input", "summary": "q", "payload": {"message": "q"}})
        return await planner.execute(plan, run_engine=run_engine, run_tool=run_tool), trace.current()

    results, t = asyncio.run(run())
    assert not results["s1"]["ok"] and results["s1"]["error"] == "ops-intel timeout"
    step = next(s for s in t.spans if s.kind == "step")
    assert not step.ok and step.error == "ops-intel timeout"


def test_verify_traces_every_figure_to_a_step_result():
    results = {"s1": {"output": "794 raised in September 2026; 781 completed; 13 open", "ok": True},
               "s2": {"output": {"blocked": 2, "cost_usd": 1409.2}, "ok": True}}
    good = planner.verify("794 work orders were raised, 781 are closed and 13 open; 2 are blocked; £1,409.20 is at stake.", results)
    assert good["ok"] and good["figures_checked"] == 4   # "2 are blocked" is a single digit: prose, not checked
    bad = planner.verify("794 raised; 42 of them are overdue and £9,999 is recoverable.", results)
    assert not bad["ok"] and bad["unmatched"] == ["42", "£9,999"]
    # years and single digits are prose
    assert planner.verify("In 2026, 3 things happened.", results)["figures_checked"] == 0


def test_the_synthesis_prompt_carries_question_plan_shape_and_results():
    plan, _ = planner.validate({"goal": "g", "steps": [{"id": "s1", "kind": "tool", "target": "get_cost_savings", "ask": "x"}], "answer_shape": "two tables"}, TOOLS)
    seen = {}

    async def llm(system, user, role):
        seen.update({"role": role, "user": user})
        return "Answer."

    out = asyncio.run(planner.synthesise("Which save cost?", plan, {"s1": {"output": {"open_jobs": 3}, "ok": True, "error": None}}, llm))
    assert out == "Answer." and seen["role"] == "synthesise"
    assert "Which save cost?" in seen["user"] and "two tables" in seen["user"] and '"open_jobs": 3' in seen["user"] and "s1: get_cost_savings" in seen["user"]


def test_a_tool_step_with_args_calls_the_tool_with_them_and_the_prompt_prefers_tools():
    assert "An engine step costs 5-10x a tool step" in planner.PLAN_PROMPT
    plan, _ = planner.validate({"steps": [{"id": "s1", "kind": "tool", "target": "get_cost_savings", "ask": "savings at B-301 last month",
                                            "args": {"building_name": "Bishopsgate Tower", "period": "last_month"}}]}, TOOLS)
    seen = {}

    async def run_tool(name, args):
        seen[name] = args
        return {"ok": True}

    async def run_engine(*a):
        return "", []

    asyncio.run(planner.execute(plan, run_engine=run_engine, run_tool=run_tool))
    assert seen == {"get_cost_savings": {"building_name": "Bishopsgate Tower", "period": "last_month"}}
