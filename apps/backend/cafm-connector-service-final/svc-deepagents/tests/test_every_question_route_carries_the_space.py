"""Every route that answers a question records which space asked it.

Reported 2 Nov 2026: in Ops Brain, "is the all compliance certificate on bishops gate tower
building?" was answered with eight certificates by the compliance engine, which Ops Brain does
not have.

The gate was never the problem. agents/spaces.py refuses compliance in ops and the router drops
it from the catalogue; what failed is that the question arrived carrying no space at all, and
engines_for(None) means "every module", which is the right answer for a caller that genuinely has
no space and the wrong one for a caller whose space was dropped on the way.

It was dropped twice, in the same shape, at opposite ends:

    frontend    deepAgents.stream() built its socket frame without spaceField()
    backend     /run-stateful-with-files took no space Form field and set none

The test that was supposed to cover this asserted the READER -- that the contextvar exists and
the router consults it. A reader with no writer passes that every time. So this one is about the
writers, and it fails when a new question route is added without one.
"""
from __future__ import annotations

import inspect
import re

import pytest

from src.api.routes import workflow as wf

SRC = inspect.getsource(wf)

#: Routes that take a person's question and answer it. Each must record the space.
QUESTION_ROUTES = [
    "run_workflow",
    "run_stateful_workflow",
    "run_stateful_workflow_with_files",
    "ws_workflow",
]


def _body(name: str) -> str:
    m = re.search(r"\nasync def " + re.escape(name) + r"\(", SRC)
    assert m, name + " is gone; this test needs re-aiming"
    rest = SRC[m.end():]
    nxt = re.search(r"\n(?:@router|async def |def )", rest)
    return rest[:nxt.start()] if nxt else rest


def test_the_routes_this_guards_all_exist():
    """A renamed route would otherwise silently drop out of the list below."""
    for name in QUESTION_ROUTES:
        assert hasattr(wf, name), name


@pytest.mark.parametrize("name", QUESTION_ROUTES)
def test_a_route_that_answers_a_question_records_the_space(name):
    body = _body(name)
    assert "spaces.active_space.set(" in body, (
        "%s answers a question and never sets the active space, so agents/spaces.py sees None, "
        "engines_for(None) allows every engine, and a question asked in Ops Brain can be "
        "answered by the compliance engine" % name)


@pytest.mark.parametrize("name", QUESTION_ROUTES)
def test_the_space_it_records_is_the_one_the_caller_sent(name):
    """Setting a constant, or the space of whoever asked last, would pass the test above."""
    body = _body(name)
    line = next((l for l in body.split("\n") if "spaces.active_space.set(" in l), None)
    assert line, name
    # To the end of the call, not to the first ")" -- body.get("space") has one of its own.
    arg = line.split("spaces.active_space.set(", 1)[1].rstrip().rstrip(")")
    arg = arg.strip() + ")" if arg.count("(") > arg.count(")") else arg.strip()
    assert arg in ('space', 'body.get("space")', 'getattr(body, "space", None)'), (
        "%s sets the active space from %r, which is not what the caller sent" % (name, arg))


def test_the_form_route_declares_a_space_field():
    """A Form route reads nothing it has not declared, so the field and the set go together."""
    body = _body("run_stateful_workflow_with_files")
    assert "space: str | None = Form(" in body


def test_the_json_routes_declare_it_on_the_model():
    assert re.search(r"\n    space: str \| None = Field\(", SRC), (
        "the request model has no space field, so body.space is always None")


# -- what None has to keep meaning -----------------------------------------------------------

def test_no_space_still_means_every_module():
    """The platform console, a cron and an API caller have no space and must lose nothing."""
    from src.agents.spaces import allows, engines_for
    from src.agents.planner import ENGINES
    assert engines_for(None) is None
    for engine in ENGINES:
        assert allows(None, engine) is True


def test_an_empty_string_is_not_a_space():
    """A form field that arrives empty must not become a space nothing matches."""
    from src.agents.spaces import engines_for
    assert engines_for("") is None
    assert engines_for("   ") is None


# -- the reported question, end to end through the gate ----------------------------------------

def test_the_reported_question_is_refused_in_ops_brain():
    from src.agents.phase2_intents import resolve_phase2_engine
    from src.agents.spaces import allows, refusal

    q = "is the all compliance certificate on bishops gate tower building ?"
    engine = resolve_phase2_engine(user_message=q, context_note=None)
    assert engine == "compliance", "routing is right; it is the space that has to refuse it"
    assert allows("ops", engine) is False
    said = refusal("ops", engine)
    assert "Ops Brain" in said and "Compliance Engine" in said


def test_the_same_question_is_answered_in_the_compliance_engine():
    from src.agents.spaces import allows
    assert allows("comp", "compliance") is True
