"""A corrected re-run of an engine answer comes back the way the answer first did: as the dashboard.

Seen 5 Oct 2026: "Which vendors are blocked right now?" answered live through the compliance engine
(_invoke_phase2_engine -> the analyst -> compliance_response) and rendered as KPI tiles, vendor cards
and a certificates table. "Re-run the steps" replayed the recorded one-step plan through
planner.execute with a bare run_phase2_engine_verbose, which never runs the analyst, so the
corrected answer came back as a markdown table. A re-run of a one-step engine plan - which is
what every direct engine answer is recorded as - now replays that engine's own path, without the
engine path's closing thread write (the re-run records its answer once, at the end).
"""
from __future__ import annotations

import asyncio
import json
import uuid

import pytest

from src.agents import llm_cost, planner, rerun, session_workspace, trace
from src.agents import orchestrator as orch_mod
from src.agents.orchestrator import DeepAgentOrchestrator as O
from src.http_client import caller_organization_id
from src.services import chat_memories, chat_threads
from src.services.principal import Principal, caller_principal

Q = "Which vendors are blocked right now?"
ROWS = {"ok": True, "certificates": [{"vendor": "Ostley Power Services", "status": "Lapsed"}]}
STRUCTURED = {"narrative": "Nine vendors are blocked; three carry life-safety work.", "kpis": [{"count": 9, "label": "Vendors blocked"}],
              "groups": [{"owner": "Vendor - Ostley Power Services", "points": ["NICEIC lapsed"]}], "actions": [], "sections": [],
              "insights": [], "certificates": [], "pending": [], "offers": []}


def _turn(spans=None):
    return {"turn_id": "turn-orig", "question": Q, "spans": spans or [
        {"seq": 0, "kind": "turn", "name": "turn"},
        {"seq": 1, "kind": "router", "name": "router", "output": {"model_output": {"agent": "compliance"}}}]}


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("ACTIVITY_LOG_ENABLED", "false")
    monkeypatch.setattr(trace, "_ready", True)
    writes, answers, engine_calls, composed = [], [], [], []

    async def fake_write(t, answer, tool_calls, summary):
        writes.append((t, answer, tool_calls))

    async def fake_record_answer(session_id, answer, *, tools=None, citations=None):
        answers.append((answer, [t.get("tool") if isinstance(t, dict) else t for t in (tools or [])]))

    async def fake_record_question(*a, **k):
        return None

    async def no_recall(*a, **k):
        return []

    monkeypatch.setattr(trace, "_write", fake_write)
    monkeypatch.setattr(chat_threads, "record_answer", fake_record_answer)
    monkeypatch.setattr(chat_threads, "record_question", fake_record_question)
    monkeypatch.setattr(chat_memories, "recall", no_recall)
    p = Principal(user_id=uuid.uuid4(), email="fm@example.com", organization_id=uuid.uuid4(), role="admin", can_ingest=True, building_ids=None)
    caller_principal.set(p)
    caller_organization_id.set(str(p.organization_id))
    trace._turn.set(None)
    llm_cost._ledger.set(None)

    state = {"engine_answer": "Nine vendors are blocked.", "engine_raises": False, "structured": STRUCTURED}

    async def fake_engine(engine, prompt, on_event=None):
        engine_calls.append((engine, prompt))
        if state["engine_raises"]:
            raise RuntimeError("engine down")
        return state["engine_answer"], [{"tool": "list_vendor_accreditations", "input": {"risk_filter": "blocked"}, "output": ROWS}]

    monkeypatch.setattr(orch_mod, "run_phase2_engine_verbose", fake_engine)

    o = O.__new__(O)

    async def compose(user_message, inner_tool_calls, session_id, answer, on_zone=None):
        composed.append(user_message)
        return state["structured"], ([{"stage": "analyse", "label": "analyst", "detail": ""}] if state["structured"] else [])

    async def no_review(*a, **k):
        return None

    async def llm(system, user, role):
        return "Planner text: nine vendors are blocked." if role == "synthesise" else "{}"

    async def ok_check(step, result):
        return True, ""

    async def no_replan(*a, **k):
        return None

    o._compose_structured_compliance = compose
    o._review_compliance_answer = no_review
    o._planner_llm = llm
    o._check_step = ok_check
    o._replan_after_gates = no_replan
    sid = "sess-" + uuid.uuid4().hex[:8]
    yield {"o": o, "sid": sid, "writes": writes, "answers": answers, "engine_calls": engine_calls, "composed": composed, "state": state}
    session_workspace._SESSION_STATE.pop(sid, None)


def _run(env, turn, corrections):
    async def go():
        out = await env["o"].rerun_turn(turn, corrections, session_id=env["sid"])
        await asyncio.sleep(0)       # let record_answer_soon's task run
        return out
    return asyncio.run(go())


def _assistant_turns(sid):
    return [t["content"] for t in session_workspace.get_session_state(sid).get("conversation_turns") or [] if t.get("role") == "assistant"]


def test_a_rerun_of_an_engine_answer_comes_back_as_that_engines_dashboard(env):
    out = _run(env, _turn(), [{"span_id": None, "mode": "model", "text": "eeee"}])
    assert out["ok"] is True
    calls = out["tool_calls"]
    responses = [c for c in calls if c.get("tool") == "compliance_response"]
    assert len(responses) == 1 and responses[0]["output"] == STRUCTURED          # the analyst's dashboard, once
    first_pipe = next(c for c in calls if c.get("tool") == "compliance_pipeline")
    assert any(s.get("stage") == "analyse" for s in first_pipe["output"]["steps"])   # the engine's own run panel
    assert not any(c.get("tool") == "planner" for c in calls)
    # the correction reached the engine and the analyst
    assert len(env["engine_calls"]) == 1 and "Correction (apply exactly): eeee" in env["engine_calls"][0][1]
    assert "Correction (apply exactly): eeee" in env["composed"][0]
    # the answer text is the one the cards were written from
    assert out["answer"] == O._compliance_plain_text(STRUCTURED) or out["answer"] == STRUCTURED["narrative"]
    # recorded once as the re-run's answer: thread, conversation and trace all hold the final answer
    assert env["answers"] and all(a == out["answer"] for a, _ in env["answers"])
    assert all("compliance_response" in tools for _, tools in env["answers"])
    assert _assistant_turns(env["sid"]) == [out["answer"]]
    assert len(env["writes"]) == 1 and env["writes"][0][1] == out["answer"]


def test_a_failed_engine_falls_back_to_the_step_by_step_replay(env):
    env["state"]["engine_raises"] = True
    out = _run(env, _turn(), [{"span_id": None, "mode": "model", "text": "eeee"}])
    assert out["ok"] is True
    assert any(c.get("tool") == "planner" for c in out["tool_calls"])
    assert env["composed"] == []
    assert all(a == out["answer"] for a, _ in env["answers"])


def test_an_engine_with_no_dashboard_keeps_its_own_answer_and_panel(env):
    env["state"]["structured"] = None
    out = _run(env, _turn(), [{"span_id": None, "mode": "model", "text": "eeee"}])
    assert out["ok"] is True and out["answer"] == "Nine vendors are blocked."
    assert not any(c.get("tool") == "planner" for c in out["tool_calls"])
    assert all(a == out["answer"] for a, _ in env["answers"])
    assert _assistant_turns(env["sid"]) == [out["answer"]]


def test_a_multi_step_plan_still_replays_step_by_step(env):
    plan_span = {"seq": 1, "kind": "plan", "name": "plan", "output": {"plan": {"mode": "multi", "goal": "g", "steps": [
        {"id": "s1", "kind": "engine", "target": "compliance", "ask": "blocked vendors", "depends_on": []},
        {"id": "s2", "kind": "engine", "target": "wo_engine", "ask": "their open work orders", "depends_on": []}]}}}
    out = _run(env, _turn([{"seq": 0, "kind": "turn", "name": "turn"}, plan_span]), [{"span_id": None, "mode": "suggestion", "text": "x"}])
    assert out["ok"] is True
    assert any(c.get("tool") == "planner" for c in out["tool_calls"])
    assert env["composed"] == [] and len(env["engine_calls"]) == 2


def test_only_a_one_step_engine_plan_takes_the_engine_path():
    one = {"steps": [{"id": "s1", "kind": "engine", "target": "compliance", "ask": "q"}]}
    assert rerun.single_engine_step(one)["target"] == "compliance"
    assert rerun.single_engine_step({"steps": [{"id": "s1", "kind": "tool", "target": "work_order_blockers", "ask": "q"}]}) is None
    assert rerun.single_engine_step({"steps": [{"id": "s1", "kind": "engine", "target": "nope", "ask": "q"}]}) is None
    assert rerun.single_engine_step({"steps": one["steps"] * 2}) is None
    assert set(planner.ENGINES) <= set(orch_mod.PHASE2_ENGINE_TOOLS)


def test_an_engine_that_answers_with_a_bare_error_falls_back_too(env):
    # meta_tools.run_verbose swallows engine exceptions and returns json.dumps({"error": ...}) as the answer
    env["state"]["engine_answer"] = json.dumps({"error": "OpenAI 429"})
    env["state"]["structured"] = None
    out = _run(env, _turn(), [{"span_id": None, "mode": "model", "text": "eeee"}])
    assert any(c.get("tool") == "planner" for c in out["tool_calls"])
    assert any("the engine path failed" in n for n in out["applied"])


def test_what_counts_as_an_engine_error():
    assert rerun.is_engine_error("") and rerun.is_engine_error('{"error": "x"}')
    assert not rerun.is_engine_error("Nine vendors are blocked.")
    assert not rerun.is_engine_error('{"vendors": 9, "rows": [], "note": "", "x": 1}')



def test_a_rerun_of_a_wo_engine_answer_sends_the_chat_the_cards_the_thread_records(env, monkeypatch):
    # The engine's data tool is a card tool; attach_route_to_result builds the cards. The chat must
    # get them too, not only the thread (review finding, 5 Oct 2026).
    from test_planner_cards import CONDITION

    async def wo_engine(engine, prompt, on_event=None):
        env["engine_calls"].append((engine, prompt))
        return "Six assets are a threat today at Bishopsgate Tower.", [{"tool": "get_asset_condition_summary", "input": {}, "output": CONDITION}]

    monkeypatch.setattr(orch_mod, "run_phase2_engine_verbose", wo_engine)
    turn = _turn([{"seq": 0, "kind": "turn", "name": "turn"},
                  {"seq": 1, "kind": "router", "name": "router", "output": {"model_output": {"agent": "energy_intelligence"}}}])
    out = _run(env, turn, [{"span_id": None, "mode": "model", "text": "eeee"}])
    names = [c.get("tool") for c in out["tool_calls"]]
    assert "compliance_response" in names
    assert all("compliance_response" in tools for _, tools in env["answers"])


def test_a_failing_analyst_falls_back_instead_of_failing_the_rerun(env):
    async def boom(*a, **k):
        raise RuntimeError("analyst refused")
    env["o"]._compose_structured_compliance = boom
    out = _run(env, _turn(), [{"span_id": None, "mode": "model", "text": "eeee"}])
    assert out["ok"] is True
    assert any(c.get("tool") == "planner" for c in out["tool_calls"])
    assert any("the engine path failed" in n for n in out["applied"])
    assert env["answers"] and all(a == out["answer"] for a, _ in env["answers"])
