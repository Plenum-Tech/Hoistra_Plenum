"""Every entry point answers a question with the same orchestration (2 Nov 2026).

Feedback: "Frontend, backend batch runs, and other entry points must use the same orchestration logic,
routing rules, tools, and instance context." The chat streamed through _stream_turn (compliance
shortcut, router, planner, the thread's building carried into a follow-up); /run-stateful (report
cards, Hoist Crons, API callers) ran a separate 280-line path, _run_stateful_inner; and /run was a
bare agent loop with no shortcut, router, planner or space gate at all. A question now runs
_stream_turn wherever it comes from, and a REST caller gets the turn's completed event as its result
- the answer as text rather than OpenUI, the one thing that differs by caller. Calls that carry
files or an ingestion's pipeline keep their own path.
"""
from __future__ import annotations

import asyncio
import inspect

import pytest

from src.agents import activity_log
from src.agents import orchestrator as orch

O = orch.DeepAgentOrchestrator


@pytest.fixture
def turn(monkeypatch):
    """_stream_turn replaced by `events`; returns (events, calls)."""
    events: list[dict] = []
    calls: list[tuple] = []

    async def fake(self, user_message, session_id=None, extra_context=None, chat=True):
        calls.append((user_message, session_id, extra_context, chat))
        for e in events:
            yield e

    async def old_path(self, **kw):
        raise AssertionError("a plain question took the old REST path")

    monkeypatch.setattr(O, "_stream_turn", fake)
    monkeypatch.setattr(O, "_run_stateful_inner", old_path)
    monkeypatch.setattr(activity_log, "fire", lambda **k: None)
    monkeypatch.setattr(activity_log, "set_current_session", lambda *a, **k: None)
    monkeypatch.setattr(activity_log, "ensure_turn", lambda *a, **k: None)
    return events, calls


DONE = {"type": "workflow_completed", "answer": "Two buildings have overdue certificates.", "session_id": "s1",
        "turn_id": "turn-1", "tool_calls": [{"tool": "list_building_certificates", "output": {}}],
        "workspace_status": {}, "ingested_schema_mapping_ids": []}


def test_a_rest_question_runs_the_chats_pipeline_and_returns_its_answer(turn):
    events, calls = turn
    events += [{"type": "reasoning", "label": "Plan"}, dict(DONE)]
    res = asyncio.run(O.run_stateful(object.__new__(O), "Which buildings have overdue certificates?", "s1",
                                     extra_context="From a report card"))
    assert calls == [("Which buildings have overdue certificates?", "s1", "From a report card", False)]
    assert res["answer"] == "Two buildings have overdue certificates."
    assert res["success"] is True and res["error"] is None
    assert res["interrupted"] is False and res["interrupt_payload"] is None
    assert res["session_id"] == "s1" and res["turn_id"] == "turn-1"
    assert res["tool_calls"] == DONE["tool_calls"]


def test_run_is_the_same_pipeline_too(turn):
    events, calls = turn
    events.append(dict(DONE))
    res = asyncio.run(O.run(object.__new__(O), "Which buildings have overdue certificates?", "s9"))
    assert calls[0][0] == "Which buildings have overdue certificates?" and calls[0][3] is False
    assert res["answer"] == DONE["answer"] and res["success"] is True
    # with no session it still answers, on a fresh one
    events.clear()
    events.append(dict(DONE))
    res = asyncio.run(O.run(object.__new__(O), "Which vendors are blocked?"))
    assert calls[-1][1] and res["success"] is True


def test_a_gate_or_an_error_comes_back_as_the_rest_result_says(turn):
    events, _ = turn
    events.append({"type": "gate_interrupt", "session_id": "s1", "payload": {"gate": "map_fields", "message": "Confirm the mapping"}})
    res = asyncio.run(O.run_stateful(object.__new__(O), "ingest this", "s1"))
    assert res["interrupted"] is True and res["interrupt_payload"]["gate"] == "map_fields"
    assert res["success"] is True
    events.clear()
    events.append({"type": "error", "session_id": "s1", "error": "boom"})
    res = asyncio.run(O.run_stateful(object.__new__(O), "q", "s1"))
    assert res["success"] is False and res["error"] == "boom" and res["answer"] == ""
    events.clear()
    res = asyncio.run(O.run_stateful(object.__new__(O), "q", "s1"))
    assert res["success"] is False and "no answer" in res["error"]


def test_a_call_with_files_or_an_ingestion_pipeline_keeps_its_own_path(monkeypatch):
    seen = []

    async def old_path(self, **kw):
        seen.append(kw)
        return {"session_id": kw["session_id"], "answer": "ok", "tool_calls": [], "success": True}

    async def no_stream(*a, **k):
        raise AssertionError("a file call was streamed")
        yield  # pragma: no cover

    monkeypatch.setattr(O, "_run_stateful_inner", old_path)
    monkeypatch.setattr(O, "_stream_turn", no_stream)
    monkeypatch.setattr(activity_log, "fire", lambda **k: None)
    monkeypatch.setattr(activity_log, "set_current_session", lambda *a, **k: None)
    monkeypatch.setattr(activity_log, "ensure_turn", lambda *a, **k: None)
    for extra in ({"filenames": ["a.xlsx"]}, {"preferred_engine": "compliance"},
                  {"pipeline_tool_calls": [{"tool": "x"}]}, {"detected_engines": ["energy_intelligence"]}):
        res = asyncio.run(O.run_stateful(object.__new__(O), "q", "s1", **extra))
        assert res["answer"] == "ok"
    assert len(seen) == 4


def test_only_the_chat_is_taught_openui():
    sig = inspect.signature(O._stream_turn)
    assert "chat" in sig.parameters and sig.parameters["chat"].default is True
    assert "openui_view.CHAT_TURN.set(chat)" in inspect.getsource(O._stream_turn)
    assert "self._stream_turn(user_message, session_id, extra_context)" in inspect.getsource(O.stream)


# ── the instance gate holds before the compliance shortcut ──────────────────────────────────────
# 2 Nov 2026, in Ops Brain (space "ops": buildings, ops, vendors - no compliance), "Which
# certificates expire next month?" was answered from the certificate register: the shortcut runs
# before routing, and only the router applied the space.

def test_the_compliance_shortcut_stands_down_outside_a_space_that_has_compliance(monkeypatch):
    from src.agents import spaces

    async def boom(*a, **k):
        raise AssertionError("the certificate register was read in a space without compliance")

    from src import database

    monkeypatch.setattr(O, "_fetch_compliance_table_direct", boom)
    monkeypatch.setattr(O, "_register_via_engine_tools", boom, raising=False)
    monkeypatch.setattr(O, "_claude_analyse_compliance", boom, raising=False)
    monkeypatch.setattr(database, "AsyncSessionLocal", boom)
    monkeypatch.setattr(activity_log, "fire", lambda **k: None)
    tok = spaces.active_space.set("ops")
    try:
        out = asyncio.run(O._maybe_compliance_posture_shortcut(object.__new__(O), "Which certificates expire next month?", "s1"))
    finally:
        spaces.active_space.reset(tok)
    assert out is None
    src = inspect.getsource(O._maybe_compliance_posture_shortcut)
    assert 'spaces.allows(spaces.current(), "compliance")' in src


def test_a_question_that_names_a_module_the_space_lacks_is_refused_before_any_model(monkeypatch):
    # 15:2x, Ops Brain: the router's model chose the general loop for "Which certificates expire
    # next month?", the loop read the register through the generic table tools, and the space
    # gate - applied only to a named engine - passed it.
    from src.agents import agent_router, spaces

    def no_model(*a, **k):
        raise AssertionError("the router's model was asked")

    monkeypatch.setattr(agent_router, "llm_routing_enabled", lambda: True)
    monkeypatch.setattr(agent_router, "_catalogue", no_model)
    out = asyncio.run(agent_router.select_agent("Which certificates expire next month?", space="ops"))
    assert out["agent"] is None and out["out_of_module"] and out["wanted"] == "compliance"
    # a question this space does own still goes on to routing (reaching the catalogue proves it,
    # before any model is called)
    class Reached(Exception):
        pass

    def reached(*a, **k):
        raise Reached()

    monkeypatch.setattr(agent_router, "_catalogue", reached)
    with pytest.raises(Reached):
        asyncio.run(agent_router.select_agent("What are the work order deviations?", space="ops"))
    # and a space that has compliance is not refused
    assert spaces.allows("full", "compliance") and spaces.allows("comp", "compliance")
