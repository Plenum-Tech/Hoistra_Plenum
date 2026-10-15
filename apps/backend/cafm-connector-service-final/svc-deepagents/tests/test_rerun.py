"""Re-running a recorded turn with corrections: the plan it replays, where each correction lands,
and that the route needs a signed-in caller."""
from __future__ import annotations

import json
import uuid

from src.agents import rerun


def _turn_multi():
    return {"turn_id": "t1", "question": "How many raised and which save cost?", "spans": [
        {"id": "root", "seq": 0, "kind": "turn", "name": "turn"},
        {"id": "p", "seq": 1, "kind": "plan", "name": "plan: 2 steps", "parent_id": "root", "output": json.dumps({"plan": {"mode": "multi", "goal": "g", "answer_shape": "two tables", "steps": [
            {"id": "s1", "kind": "tool", "target": "answer_from_records", "ask": "count raised at B-301 in Sep 2026", "depends_on": []},
            {"id": "s2", "kind": "tool", "target": "get_cost_savings", "ask": "savings at B-301", "args": {"building_name": "Bishopsgate Tower", "period": "last_month"}, "depends_on": ["s1"]}]}})},
        {"id": "st1", "seq": 2, "kind": "step", "name": "s1: answer_from_records", "parent_id": "p"},
        {"id": "tool1", "seq": 3, "kind": "tool", "name": "answer_from_records", "parent_id": "st1"},
        {"id": "q1", "seq": 4, "kind": "db", "name": "aggregate", "parent_id": "tool1"},
        {"id": "st2", "seq": 5, "kind": "step", "name": "s2: get_cost_savings", "parent_id": "p"},
        {"id": "tool2", "seq": 6, "kind": "tool", "name": "get_cost_savings", "parent_id": "st2"},
    ]}


def test_a_planned_turn_replays_its_own_steps_and_a_routed_turn_becomes_one_engine_step():
    plan = rerun.plan_of(_turn_multi())
    assert plan["mode"] == "multi" and [s["id"] for s in plan["steps"]] == ["s1", "s2"]
    assert plan["steps"][1]["args"] == {"building_name": "Bishopsgate Tower", "period": "last_month"}
    routed = {"turn_id": "t2", "question": "expired certificates by vendor", "spans": [
        {"id": "root", "seq": 0, "kind": "turn", "name": "turn"},
        {"id": "r", "seq": 1, "kind": "router", "name": "router", "parent_id": "root", "output": {"model_output": {"agent": "compliance"}}},
        {"id": "p", "seq": 2, "kind": "plan", "name": "plan: 1 step", "parent_id": "root", "output": {"plan": {"mode": "single", "steps": [{"id": "s1", "kind": "engine", "target": "compliance", "ask": None}]}}}]}
    one = rerun.plan_of(routed)
    assert one["mode"] == "single" and one["steps"][0]["target"] == "compliance" and one["steps"][0]["ask"] == "expired certificates by vendor"
    assert rerun.plan_of({"question": "q", "spans": [{"id": "root", "seq": 0, "kind": "turn", "name": "turn"}]}) is None
    # a general-loop plan ("orchestrator loop") replays the loop - the plan is what ran, even when an
    # engine ran inside it (5 Oct 2026; it used to fall to that engine when the loop had no replay)
    loop = {"question": "q", "spans": [{"id": "root", "seq": 0, "kind": "turn", "name": "turn"},
                                      {"id": "p", "seq": 1, "kind": "plan", "name": "plan: 1 step", "parent_id": "root", "output": {"plan": {"mode": "single", "steps": [{"id": "s1", "kind": "loop", "target": "orchestrator loop"}]}}},
                                      {"id": "g", "seq": 2, "kind": "agent", "name": "wo_engine", "parent_id": "root"}]}
    assert rerun.loop_step(rerun.plan_of(loop))["target"] == "orchestrator loop"


def test_a_correction_lands_on_the_step_its_span_belongs_to():
    t = _turn_multi()
    plan = rerun.plan_of(t)
    assert rerun.step_for_span(t, "q1", plan) == "s1"        # the aggregate query -> through its tool -> step s1
    assert rerun.step_for_span(t, "tool2", plan) == "s2"
    assert rerun.step_for_span(t, "nope", plan) == "s1"      # unknown span: the first step
    new, notes, replan = rerun.apply_corrections(t, plan, [
        {"span_id": "q1", "mode": "query", "text": 'Exclude work orders with status "Cancelled" from "raised".'},
        {"span_id": "tool2", "mode": "tool", "text": "Use this month.", "args": {"period": "this_month"}},
    ])
    assert 'Correction (apply exactly): Exclude work orders with status "Cancelled"' in new["steps"][0]["ask"]
    assert new["steps"][1]["args"] == {"building_name": "Bishopsgate Tower", "period": "this_month"}
    assert not replan and notes[0].startswith("s1: Exclude") and any(n.startswith("s2: args") for n in notes)
    # the original plan is untouched
    assert "Correction" not in plan["steps"][0]["ask"]


def test_a_route_correction_changes_the_steps_engine_and_a_plan_correction_asks_for_a_replan():
    t = _turn_multi()
    plan = rerun.plan_of(t)
    new, notes, replan = rerun.apply_corrections(t, plan, [{"span_id": "st1", "mode": "route", "route": "compliance", "text": "Route questions like this to the compliance engine."}])
    assert new["steps"][0]["kind"] == "engine" and new["steps"][0]["target"] == "compliance" and new["steps"][0]["args"] is None
    assert not replan and notes == ["s1: route to compliance"]
    new, notes, replan = rerun.apply_corrections(t, plan, [{"span_id": "p", "mode": "plan", "text": "Use one tool step, not two."}])
    assert replan and notes[0].startswith("plan again")
    assert rerun.rerun_question(t, notes).startswith("Re-run with corrections (plan again: Use one tool step")


def test_the_rerun_route_needs_a_signed_in_caller_and_a_correction():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from src.agents import trace
    from src.api.deps import get_orchestrator
    from src.api.routes import traces as routes
    from src.services.principal import Principal, current_principal

    app = FastAPI()
    app.include_router(routes.router)
    c = TestClient(app)
    assert c.post("/api/traces/turns/t1/rerun", json={"session_id": "s", "corrections": []}).status_code in (401, 403)
    p = Principal(user_id=uuid.uuid4(), email="fm@example.com", organization_id=uuid.uuid4(), role="admin", can_ingest=True, building_ids=None)
    app.dependency_overrides[current_principal] = lambda: p

    class _Orch:
        async def rerun_turn(self, turn, corrections, *, session_id):
            return {"ok": True, "turn_id": "turn-new", "rerun_of": turn["turn_id"], "answer": "791 raised.", "tool_calls": [], "applied": ["s1: x"]}

    app.dependency_overrides[get_orchestrator] = lambda: _Orch()

    async def fake_get(principal, org, turn_id):
        return _turn_multi() if turn_id == "t1" else None

    import pytest
    mp = pytest.MonkeyPatch()
    mp.setattr(trace, "get_turn", fake_get)
    try:
        assert c.post("/api/traces/turns/t1/rerun", json={"session_id": "s", "corrections": []}).status_code == 422
        r = c.post("/api/traces/turns/t1/rerun", json={"session_id": "s", "corrections": [{"span_id": "q1", "mode": "query", "text": "Exclude Cancelled."}]})
        assert r.status_code == 200 and r.json()["turn_id"] == "turn-new" and r.json()["rerun_of"] == "t1"
        assert c.post("/api/traces/turns/nope/rerun", json={"session_id": "s", "corrections": [{"mode": "suggestion", "text": "x"}]}).status_code == 404
    finally:
        mp.undo()
