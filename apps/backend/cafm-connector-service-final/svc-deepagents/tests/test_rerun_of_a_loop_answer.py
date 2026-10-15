"""A corrected re-run of a general-loop answer replays the loop, in the same conversation.

Seen 5 Oct 2026: "all of them" (a follow-up to "Show me everything that's at risk") went router ->
orchestrator loop -> `task` sub-agents. Its trace records the plan as one "orchestrator loop" step,
which rerun.plan_of skipped because there is no step to replay; with no engine either it returned
None, and every correction on such an answer failed with "This run recorded no route to replay."
A loop answer is now replayed through the same loop: the original question plus the corrections,
with the conversation as the server keeps it, recorded once as the re-run's own turn.
"""
from __future__ import annotations

import asyncio
import json
import uuid

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from src.agents import contract_answer, llm_cost, planner, rerun, session_workspace, thread_scope, trace
from src.agents.orchestrator import DeepAgentOrchestrator as O
from src.http_client import turn_page_engines
from src.http_client import caller_organization_id
from src.services import chat_memories, chat_threads
from src.services.principal import Principal, caller_principal

Q = "all of them"
LOOP_ANSWER = "## Work orders\n\n- **Open:** 14\n- **Overdue:** 3"
STATS = {"ok": True, "open": 14, "overdue": 3}


def _loop_turn(with_plan: bool = True):
    spans = [{"id": "root", "seq": 0, "kind": "turn", "name": "turn"},
             {"id": "r", "seq": 1, "kind": "router", "name": "router", "parent_id": "root", "output": {"model_output": {"agent": "general"}}}]
    if with_plan:
        spans.append({"id": "p", "seq": 2, "kind": "plan", "name": "plan: 1 step", "parent_id": "root",
                      "output": {"plan": {"mode": "single", "steps": [{"id": "s1", "kind": "loop", "target": "orchestrator loop"}]}}})
    spans += [{"id": "g", "seq": 3, "kind": "agent", "name": "orchestrator loop", "parent_id": "root"},
              {"id": "sa", "seq": 4, "kind": "agent", "name": "sub-agent", "parent_id": "g"},
              {"id": "m1", "seq": 5, "kind": "model", "name": "model", "parent_id": "sa"}]
    return {"turn_id": "turn-orig", "question": Q, "spans": spans}


def test_a_loop_turn_is_replayed_as_one_loop_step_with_its_question():
    for t in (_loop_turn(), _loop_turn(with_plan=False)):
        plan = rerun.plan_of(t)
        assert plan is not None and [s["kind"] for s in plan["steps"]] == ["loop"]
        assert plan["steps"][0]["target"] == "orchestrator loop" and plan["steps"][0]["ask"] == Q
        assert rerun.loop_step(plan) is plan["steps"][0]
        assert rerun.single_engine_step(plan) is None
    # a turn that shows no route at all still has nothing to replay
    assert rerun.plan_of({"question": "q", "spans": [{"id": "root", "seq": 0, "kind": "turn", "name": "turn"}]}) is None
    # the router named an engine but the dispatch sent the turn to the loop: the plan says what ran
    t = _loop_turn()
    t["spans"][1]["output"] = {"model_output": {"agent": "wo_engine", "also": ["energy_intelligence"]}}
    assert rerun.loop_step(rerun.plan_of(t)) is not None
    # no recorded plan: the router's engine still decides, before the loop span
    t["spans"] = [s for s in t["spans"] if s["kind"] != "plan"]
    assert rerun.single_engine_step(rerun.plan_of(t))["target"] == "wo_engine"
    # a recorded engine plan is that engine, never the loop
    t = _loop_turn()
    t["spans"][2]["output"] = {"plan": {"mode": "single", "steps": [{"id": "s1", "kind": "engine", "target": "compliance"}]}}
    assert rerun.single_engine_step(rerun.plan_of(t))["target"] == "compliance"


def test_the_turn_from_the_report_replays_as_its_loop_with_the_routers_decision():
    # agent_trace_spans for turn-f26f8c54367d4c60 (5 Oct 2026), the run that said "This run
    # recorded no route to replay": the live router stores its decision as the span output itself.
    t = {"turn_id": "turn-f26f8c54367d4c60", "question": "all of them", "spans": [
        {"id": "a", "seq": 0, "kind": "turn", "name": "turn", "parent_id": None},
        {"id": "b", "seq": 2, "kind": "router", "name": "router", "parent_id": "a",
         "output": {"also": [], "agent": "clarify", "reason": "The referent of 'all of them' is unclear without the preceding question context."}},
        {"id": "c", "seq": 2, "kind": "plan", "name": "plan: 1 step", "parent_id": "a",
         "output": {"plan": {"goal": None, "mode": "single", "steps": [{"id": "s1", "ask": None, "why": "unclear referent", "kind": "loop", "target": "orchestrator loop"}]}}},
        {"id": "d", "seq": 4, "kind": "agent", "name": "sub-agent", "parent_id": "a"}]}
    assert [(s["kind"], s["ask"]) for s in rerun.plan_of(t)["steps"]] == [("loop", "all of them")]
    assert rerun.routing_of(t)["agent"] == "clarify"
    # the same decision nested under model_output, as other recorders write it, reads the same
    t["spans"][1]["output"] = {"model_output": t["spans"][1]["output"]}
    assert rerun.routing_of(t)["agent"] == "clarify"
    # and a top-level engine decision with no recorded plan still replays that engine
    t["spans"] = [t["spans"][0], {**t["spans"][1], "output": {"agent": "compliance", "also": []}}]
    assert rerun.single_engine_step(rerun.plan_of(t))["target"] == "compliance"


def test_a_correction_on_any_loop_span_lands_on_the_loop_step():
    t = _loop_turn()
    plan = rerun.plan_of(t)
    new, notes, replan = rerun.apply_corrections(t, plan, [{"span_id": "m1", "mode": "model", "text": "The work order service is back; read it."}])
    assert "Correction (apply exactly): The work order service is back; read it." in new["steps"][0]["ask"]
    assert new["steps"][0]["ask"].startswith(Q) and not replan and notes == ["s1: The work order service is back; read it."]
    # a route correction sends the re-run to that engine instead
    new, _, _ = rerun.apply_corrections(t, plan, [{"span_id": "sa", "mode": "route", "route": "wo_engine", "text": ""}])
    assert rerun.single_engine_step(new)["target"] == "wo_engine" and rerun.loop_step(new) is None


class _Graph:
    """The deep agent's graph, stubbed: records what it was asked and answers like the loop."""

    def __init__(self, raises: bool = False):
        self.calls: list[tuple[dict, dict]] = []
        self.closed_for: list[frozenset] = []
        self.raises = raises

    async def ainvoke(self, input_, config):
        self.calls.append((input_, config))
        self.closed_for.append(turn_page_engines.get())
        if self.raises:
            raise RuntimeError("model unavailable")
        return {"messages": [*input_["messages"],
                             AIMessage(content="", tool_calls=[
                                 {"name": "task", "args": {"agent": "wo_engine", "description": "all of them"}, "id": "c0"},
                                 {"name": "get_work_order_status_track", "args": {}, "id": "c1"}]),
                             ToolMessage(content="Open 14, overdue 3.", tool_call_id="c0", name="task"),
                             ToolMessage(content=json.dumps(STATS), tool_call_id="c1", name="get_work_order_status_track"),
                             AIMessage(content=LOOP_ANSWER)]}


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("ACTIVITY_LOG_ENABLED", "false")
    monkeypatch.setattr(trace, "_ready", True)
    writes, answers, questions, executed, scoped = [], [], [], [], []

    async def fake_write(t, answer, tool_calls, summary):
        writes.append((t, answer, tool_calls))

    async def fake_record_answer(session_id, answer, *, tools=None, citations=None):
        answers.append((answer, [x.get("tool") if isinstance(x, dict) else x for x in (tools or [])]))

    async def fake_record_question(session_id, question, **k):
        questions.append(question)

    async def no_recall(*a, **k):
        return []

    async def conversation(session_id):
        return "Earlier: Show me everything that's at risk -> 9 vendors blocked, 16 recommendations open."

    async def working_set(session_id):
        scoped.append(session_id)
        return {"building_name": "Bishopsgate Tower", "building_code": "B-301"}

    async def no_compose(*, question, answer, tool_calls, **k):
        return answer, tool_calls, {"skipped": "no rows fetched"}

    async def must_not_execute(*a, **k):
        executed.append(a)
        raise AssertionError("a loop re-run must not replay step by step")

    monkeypatch.setattr(trace, "_write", fake_write)
    monkeypatch.setattr(chat_threads, "record_answer", fake_record_answer)
    monkeypatch.setattr(chat_threads, "record_question", fake_record_question)
    monkeypatch.setattr(chat_threads, "conversation_context", conversation)
    monkeypatch.setattr(chat_threads, "working_set", working_set)
    monkeypatch.setattr(chat_memories, "recall", no_recall)
    monkeypatch.setattr(contract_answer, "compose", no_compose)
    monkeypatch.setattr(planner, "execute", must_not_execute)
    p = Principal(user_id=uuid.uuid4(), email="fm@example.com", organization_id=uuid.uuid4(), role="admin", can_ingest=True, building_ids=None)
    caller_principal.set(p)
    caller_organization_id.set(str(p.organization_id))
    trace._turn.set(None)
    llm_cost._ledger.set(None)
    thread_scope.active_scope.set(None)
    o = O.__new__(O)
    o._has_hitl = False
    o._agent = _Graph()
    sid = "sess-" + uuid.uuid4().hex[:8]
    yield {"o": o, "sid": sid, "writes": writes, "answers": answers, "questions": questions, "executed": executed, "scoped": scoped}
    session_workspace._SESSION_STATE.pop(sid, None)


def _run(env, turn, corrections):
    async def go():
        out = await env["o"].rerun_turn(turn, corrections, session_id=env["sid"])
        await asyncio.sleep(0)       # let record_answer_soon's task run
        return out
    return asyncio.run(go())


def _assistant_turns(sid):
    return [t["content"] for t in session_workspace.get_session_state(sid).get("conversation_turns") or [] if t.get("role") == "assistant"]


def _human_text(input_):
    return next(m.content for m in input_["messages"] if isinstance(m, HumanMessage))


def test_a_rerun_of_a_loop_answer_asks_the_loop_the_original_question_with_the_correction(env):
    out = _run(env, _loop_turn(), [{"span_id": "m1", "mode": "model", "text": "The work order service is back; read it."}])
    assert out["ok"] is True, out
    assert len(env["o"]._agent.calls) == 1 and not env["executed"]
    input_, config = env["o"]._agent.calls[0]
    asked = _human_text(input_)
    # the original question, the correction, and the conversation it was a follow-up in
    assert "**Current user message:**\nall of them" in asked
    assert "Correction (apply exactly): The work order service is back; read it." in asked
    assert "Show me everything that's at risk" in asked
    assert "B-301" in asked and env["scoped"] == [env["sid"]]   # the thread's working set, as a follow-up
    assert config["configurable"]["thread_id"] == env["sid"]    # the same conversation, not a fresh one
    # the loop's own answer and calls come back to the chat unchanged
    assert out["answer"] == LOOP_ANSWER
    assert any(c.get("tool") == "get_work_order_status_track" and c.get("output") for c in out["tool_calls"])
    assert out["plan"]["steps"][0]["target"] == "orchestrator loop"
    # recorded once, as the re-run's turn: one question, the answer written with the chat's tools
    assert len(env["questions"]) == 1 and env["questions"][0].startswith("Re-run with corrections (")
    assert env["answers"] and all(a == LOOP_ANSWER for a, _ in env["answers"])
    tools = [c.get("tool") for c in out["tool_calls"]]
    assert all(t == tools for _, t in env["answers"])
    assert len(env["writes"]) == 1 and env["writes"][0][1] == LOOP_ANSWER
    assert _assistant_turns(env["sid"]) == [LOOP_ANSWER]
    # the run panel a routed loop answer draws: the sub-agents it delegated to
    panel = next(c for c in out["tool_calls"] if c.get("tool") == "compliance_pipeline")
    assert any("wo_engine" in str(st.get("label")) for st in panel["output"]["steps"])


def test_the_routers_recorded_decision_is_replayed_to_the_loop(env):
    t = _loop_turn()
    t["spans"][1]["output"] = {"model_output": {"agent": "wo_engine", "also": ["energy_intelligence"], "reason": "two registers"}}
    out = _run(env, t, [{"span_id": "m1", "mode": "model", "text": "Read the work orders."}])
    assert out["ok"] is True
    asked = _human_text(env["o"]._agent.calls[0][0])
    assert 'Call task("wo_engine"' in asked and "`energy_intelligence`" in asked
    # page engines: the database catalogue is closed while the loop runs, as on the original turn
    assert env["o"]._agent.closed_for == [frozenset({"wo_engine", "energy_intelligence"})]


def test_a_correction_that_widens_the_scope_is_not_held_to_the_followups_working_set(env):
    # the reader's correction is the newer instruction: "across the estate" drops the follow-up filter
    out = _run(env, _loop_turn(), [{"span_id": "m1", "mode": "model", "text": "Count across the estate, not one building."}])
    assert out["ok"] is True and env["scoped"] == []
    assert "across the estate" in _human_text(env["o"]._agent.calls[0][0])


def test_a_failing_loop_rerun_says_so_and_records_no_answer(env):
    env["o"]._agent = _Graph(raises=True)
    out = _run(env, _loop_turn(), [{"span_id": "m1", "mode": "model", "text": "Read the work orders."}])
    assert out["ok"] is False and "did not finish" in out["error"] and "model unavailable" in out["error"]
    assert env["answers"] == [] and not env["executed"]
