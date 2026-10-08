"""A support turn reaches the general loop and its guide, whatever the question names (8 Oct 2026).

The router already answers a support session with `source: "support"` (test_support_session_routing),
but the orchestrator runs several shortcuts before and after the router, keyed on the question's
words alone: the migration chooser took "How do I bring in data from my CAFM?", the work-order
prompt took "How do I raise a work order?" ("This looks like a work request..."), the compliance
preflight answered "Which certificates have lapsed and which vendors are blocked?" from the live
register, the planner split it into steps, and on the REST path a second classifier re-routed what
the router had left to the loop. Support asks how to USE Hoistra; the guide in its context answers.
"""
from __future__ import annotations

import asyncio

import pytest

from src.agents import activity_log, agent_router
from src.agents import orchestrator as orch
from src.agents.orchestrator import DeepAgentOrchestrator as O

CONTEXT = (
    agent_router.SUPPORT_SESSION_MARK + " (reference S-1A2B). The user opened Support from the Home page.\n\n"
    "- Treat each message as a request for help using Hoistra.\n\n# Hoistra guide\n..."
)
QUESTIONS = [
    "How do I bring in data from my CAFM?",
    "I want to migrate my data",
    "How do I upload a certificate for a building?",
    "How do I raise a work order?",
    "The boiler is down, how do I log it?",
    "Which certificates have lapsed and which vendors are blocked?",
    "What do you remember about us?",
    "Is there an API or webhooks?",
]


class ReachedLoop(Exception):
    pass


def _never(name):
    async def refuse(*a, **k):
        raise AssertionError(name + " ran for a support turn")
    return refuse


@pytest.fixture
def o(monkeypatch):
    async def noop(*a, **k):
        return None
    monkeypatch.setattr(orch.chat_threads, "record_question", noop)
    monkeypatch.setattr(orch.skill_overlays, "refresh", noop)
    monkeypatch.setattr(orch, "record_conversation_turn", lambda *a, **k: None)
    monkeypatch.setattr(orch, "set_session_context", lambda *a, **k: None)
    monkeypatch.setattr(orch, "get_session_state", lambda sid: {})
    monkeypatch.setattr(activity_log, "fire", lambda **kw: None)
    monkeypatch.setattr(activity_log, "set_current_session", lambda *a, **k: None)
    monkeypatch.setattr(activity_log, "ensure_turn", lambda *a, **k: None)
    # The router must not reach a model either: a support session never asks it.
    monkeypatch.setattr(agent_router, "llm_routing_enabled", lambda: True)
    monkeypatch.setattr(agent_router, "_catalogue", lambda: (_ for _ in ()).throw(AssertionError("router model asked")))
    inst = O.__new__(O)
    inst._config = lambda thread_id: {}
    inst._stateful_preflight_shortcut = _never("the preflight shortcut")
    inst._llm_classify_engine = _never("the second classifier")
    inst._maybe_compliance_posture_shortcut = _never("the compliance posture shortcut")
    inst._invoke_phase2_engine = _never("a phase-2 engine")
    inst._prepare_from_implicit_work_request = _never("work-order intake")
    monkeypatch.setattr(orch.planner, "make_plan", _never("the planner"))
    inst.reached = []

    async def build(sid, message, ctx):
        inst.reached.append((message, ctx))
        return {"messages": []}
    inst._build_stateful_input = build
    return inst


@pytest.mark.parametrize("q", QUESTIONS)
def test_rest_support_turn_reaches_the_general_loop_with_the_guide(o, q):
    async def invoke(input_, session_id, thread_id, routing_note=None):
        return {"session_id": session_id, "answer": "from the guide", "tool_calls": [], "success": True}
    o._invoke = invoke
    out = asyncio.run(O._run_stateful_inner(o, user_message=q, session_id="sup-1", extra_context=CONTEXT))
    assert out["answer"] == "from the guide", out.get("answer")
    assert o.reached and o.reached[0][1].startswith(agent_router.SUPPORT_SESSION_MARK), "the guide travels with the turn"


@pytest.mark.parametrize("q", QUESTIONS)
def test_streamed_support_turn_reaches_the_general_loop_with_the_guide(o, q):
    async def build(sid, message, ctx):
        o.reached.append((message, ctx))
        raise ReachedLoop()
    o._build_stateful_input = build

    async def drive():
        events = []
        with pytest.raises(ReachedLoop):
            async for ev in O.stream(o, q, "sup-2", CONTEXT):
                events.append(ev)
        return events
    events = asyncio.run(drive())
    assert not any(ev.get("type") == "workflow_completed" for ev in events), "answered before the loop"
    assert o.reached[0][1].startswith(agent_router.SUPPORT_SESSION_MARK)


def test_any_other_turn_still_meets_the_shortcuts(o):
    # The same question outside Support is still offered the migration methods.
    o._invoke = _never("the loop")
    out = asyncio.run(O._run_stateful_inner(o, user_message="I want to migrate my data", session_id="s-3",
                                            extra_context="The user is on the Home page."))
    assert out.get("choices"), "the migration chooser still answers outside Support"
