"""The frontend and a batch run orchestrate a question the same way.

"Frontend, backend batch runs, and other entry points must use the same orchestration logic,
routing rules, tools, and instance context. Equivalent queries against the same instance should
produce consistent results." (30 Oct 2026 feedback, item 5)

Two entry points answer a question. _stream_turn serves the websocket the frontend talks to;
_run_stateful_inner serves POST /run-stateful -- the report cards, Hoist Crons, API callers, and
the Skill lab, which replays recorded questions through it to judge the answers.

They had diverged in both directions, each holding a decision the other did not:

    batch only     fell back to _llm_classify_engine when keyword routing found nothing
    stream only    planned a multi-part question, and recorded the plan on the trace

So the same question could be answered by one engine in the browser and another from a cron, and
a Skill lab replay of a planned question judged an UNPLANNED run against the planned one it was
replaying. Both decisions now live in one place each -- _choose_engine and _run_planned_turn --
and these tests fail if either path grows a second copy.

This is structural: it reads the source rather than running a model. It cannot tell a correct
decision from a careless one, but it can tell one decision from two, and two is the bug.
"""
from __future__ import annotations

import inspect
import re

from src.agents import orchestrator as orch

SRC = inspect.getsource(orch)


def _body(name: str) -> str:
    """The source of one method, to the start of the next one."""
    m = re.search(r"\n    (?:async )?def " + re.escape(name) + r"\(", SRC)
    assert m, name + " is gone; this test needs re-aiming"
    rest = SRC[m.end():]
    nxt = re.search(r"\n    (?:async )?def ", rest)
    return rest[:nxt.start()] if nxt else rest


STREAM = _body("_stream_turn")
BATCH = _body("_run_stateful_inner")
BOTH = (("stream", STREAM), ("batch", BATCH))


# -- the routing decision ------------------------------------------------------------------

def test_both_ask_the_same_router_the_same_way():
    call = 'select_agent(user_message, step["router_context"])'
    for name, body in BOTH:
        assert call in body, (
            "%s no longer routes by the shared call, so the same question can reach different "
            "engines depending on where it was asked" % name)


def test_both_choose_the_engine_through_one_function():
    for name, body in BOTH:
        assert "await self._choose_engine(" in body, name


def test_neither_path_decides_the_dispatch_for_itself():
    """_decide_dispatch is _choose_engine's to call. A path calling it directly again is a
    second decision, which is how the classifier fallback came to live in only one of them."""
    for name, body in BOTH:
        assert "_decide_dispatch(" not in body, (
            "%s calls _decide_dispatch directly instead of _choose_engine" % name)


def test_the_intent_classifier_is_reached_from_exactly_one_place():
    calls = [ln for ln in SRC.split("\n") if "_llm_classify_engine(" in ln and "def " not in ln]
    assert len(calls) == 1, (
        "the LLM intent fallback is called from %d places: %r. One of them will be forgotten the "
        "next time the decision changes, and the entry points diverge again" % (len(calls), calls))
    assert "_llm_classify_engine" in _body("_choose_engine")


def test_neither_carries_its_own_keyword_table():
    for name, body in BOTH:
        assert "INTENT_KEYWORDS" not in body, name


# -- the planner ---------------------------------------------------------------------------

def test_both_plan_a_question_that_needs_decomposition():
    for name, body in BOTH:
        assert "planner.is_multi_part(user_message)" in body, (
            "%s answers a multi-part question without planning it, so it gives a different "
            "answer from the other entry point for the same question" % name)
        assert "planner.make_plan(" in body, name


def test_both_build_the_planner_context_from_one_helper():
    for name, body in BOTH:
        assert "self._plan_context(step, extra_context)" in body, name
        assert "WORKING SET (hard filter)" not in body, (
            "%s composes the planner context itself; two copies drift" % name)


def test_both_run_the_plan_through_the_same_core():
    """The streamer wraps the core rather than owning a copy of it."""
    assert "self._run_planned_turn(" in BATCH
    assert "self._stream_planned_turn(" in STREAM
    assert "self._run_planned_turn(" in _body("_stream_planned_turn")


def test_the_planned_turn_does_not_close_its_own_turn():
    """The two paths close differently -- a streamed turn writes its own closing row, a REST turn
    is closed by run_stateful -- so the shared core must leave that to the caller. A core that
    closed for itself would fire a second closing row on every batch run."""
    core = _body("_run_planned_turn")
    assert "_close_streamed_turn" not in core
    assert "_close_streamed_turn" in _body("_stream_planned_turn")


def test_the_core_runs_silently_when_nobody_is_watching():
    """A batch caller passes no event sink; the same work has to happen anyway."""
    core = _body("_run_planned_turn")
    assert "on_event: Any = None" in core
    assert "if on_event is not None" in core


# -- what both record ----------------------------------------------------------------------

def test_both_record_the_route_they_took():
    """Consistency is only checkable afterwards if both say what they did."""
    for name, body in BOTH:
        assert "trace.on_plan" in body, name


def test_both_record_a_one_step_plan_when_they_do_not_plan():
    """A batch run used to reach Hoist Traces with no plan at all, so nothing said why it went
    where it went -- the same turn from the browser carried one."""
    for name, body in BOTH:
        assert "planner.one_step_plan(" in body and 'source="router"' in body, name


# -- the instance context ---------------------------------------------------------------------

def test_the_space_reaches_both_without_either_passing_it():
    """Module scope is a contextvar set where the request arrives, so neither entry point can
    forget to pass it -- the failure a parameter would invite."""
    from src.agents import spaces
    assert hasattr(spaces, "active_space")
    router = inspect.getsource(__import__("src.agents.agent_router", fromlist=["x"]))
    assert "spaces.current()" in router
