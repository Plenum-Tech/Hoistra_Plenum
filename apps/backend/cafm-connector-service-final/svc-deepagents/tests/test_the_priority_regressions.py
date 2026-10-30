"""The six priority regressions from the 30 Oct development feedback, as executable specs.

Written BEFORE any behaviour is changed, so each one says which reported fault is real and which
is already handled. Four neighbouring tests already exist and all 99 of their cases pass, which
is why the reported faults needed pinning somewhere new rather than assumed broken.

Reproduced here, from the feedback's own examples:

    rung("Bishop Gate Tower", "Bishopsgate Tower")  -> None, at similarity 0.94
    pick_closest("Bishop", [Bishopsgate Tower, Bishopsgate House, Bishop Street Depot])
        -> [(3, "Bishop Street Depot")]       one silent pick, and the least likely of the three

NOT reproduced, and therefore guarded rather than fixed: work-order questions are not taken by
the compliance shortcut, and the deterministic router sends them to wo_engine. The misrouting
the feedback describes can only come from the LLM router, whose answer nothing checks — which is
the gap the third test in that group pins.
"""
from __future__ import annotations

import pytest

from src.agents.agent_router import resolve_phase2_engine
from src.agents.name_match import pick_closest, rung, similarity
from src.agents.orchestrator import DeepAgentOrchestrator as O

KIND = ("tower", "building", "house", "depot")
STORED = "Bishopsgate Tower"


# -- 1. Building-name variations return the intended matching records --------------------

@pytest.mark.parametrize("query", [
    "Bishopsgate Tower",        # exact
    "bishopsgate",              # the distinctive word alone
    "Bishops Gate Tower",       # the apostrophe spelled out
    "Bishopgate",               # a letter dropped
    "Bishop Gate Tower",        # the compound split -- reported
    "Bishop Gate",              # split, and the kind word dropped -- reported
    "Bishop's Gate",            # split, with the apostrophe -- reported
])
def test_a_building_is_found_however_its_name_is_split_or_punctuated(query):
    assert rung(query, STORED, KIND) is not None, (
        "%r does not match %r (similarity %.2f) -- a person typing it this way finds nothing"
        % (query, STORED, similarity(query, STORED)))


@pytest.mark.parametrize("query,stored", [
    ("Bishop Gate Tower", "Bishopsgate Tower"),
    ("Bishops gate house", "Bishopsgate House"),
])
def test_a_split_compound_scores_as_the_same_name(query, stored):
    assert similarity(query, stored) >= 0.80, (
        "a split compound is the same name typed differently, not a different building")


def test_a_different_building_is_still_a_different_building():
    """The widening must not make every Bishop- name the same place."""
    assert rung("Bishop Street Depot", STORED, KIND) is None or \
        similarity("Bishop Street Depot", STORED) < 0.80


# -- 2. Ambiguous building names trigger clarification ----------------------------------

AMBIGUOUS = [(1, "Bishopsgate Tower"), (2, "Bishopsgate House"), (3, "Bishop Street Depot")]


def test_an_ambiguous_name_returns_every_plausible_building():
    """"Bishop" could be any of the three. Returning one is a silent guess."""
    got = pick_closest("Bishop", AMBIGUOUS)
    assert len(got) != 1, (
        "picked %r silently out of %d plausible buildings -- the feedback asks for a question "
        "back, which needs the candidates, not a winner" % (got, len(AMBIGUOUS)))


def test_an_ambiguous_name_does_not_silently_pick_the_least_likely():
    got = pick_closest("Bishop", AMBIGUOUS)
    if len(got) == 1:
        assert got[0][0] in (1, 2), (
            "of three plausible buildings it chose %r, which shares only the weakest word with "
            "the query" % (got[0][1],))


def test_an_unambiguous_name_still_resolves_to_one_building():
    assert pick_closest("Bishopsgate Tower", AMBIGUOUS) == [(1, "Bishopsgate Tower")]


# -- 3. Work-order questions never invoke compliance tooling ----------------------------

WORK_ORDER_QUESTIONS = [
    "what are the work order deviations",
    "show me work order deviations this month",
    "which work orders deviated from the plan",
    "work order deviations by building",
    "which work orders breached sla",
    "list overdue work orders",
    "show the ppm deviations",
]


@pytest.mark.parametrize("question", WORK_ORDER_QUESTIONS)
def test_the_compliance_shortcut_leaves_a_work_order_question_alone(question):
    """A guard, not a fix: this already holds, and it is how the 29 Oct fault behaved."""
    assert O._is_compliance_data_question(question) is False


@pytest.mark.parametrize("question", WORK_ORDER_QUESTIONS)
def test_the_deterministic_router_sends_it_to_the_work_order_engine(question):
    assert resolve_phase2_engine(user_message=question, context_note=None) == "wo_engine"


@pytest.mark.parametrize("question", WORK_ORDER_QUESTIONS)
def test_a_work_order_question_can_never_end_up_on_compliance(question):
    """The gap.

    select_agent returns the LLM's agent as given, so when the model answers "compliance" for a
    work-order question, nothing disagrees. The deterministic resolver is certain about these
    questions; its verdict should veto a contradicting one.
    """
    from src.agents import agent_router

    guard = getattr(agent_router, "agent_for_question", None)
    if guard is None:
        pytest.fail(
            "nothing checks the router's choice: select_agent returns the LLM's agent as given, "
            "so a work-order question answered 'compliance' is routed to compliance. The fix is "
            "a guard that prefers the deterministic engine when it is certain and the LLM "
            "disagrees; this test should then call it.")
    assert guard(question, llm_choice="compliance") == "wo_engine"


# -- 4. An instance without compliance data never returns compliance results ------------

@pytest.mark.skip(reason=(
    "Needs a seam callable without a live instance. The scoping lives in svc-deepagents "
    "principal/scope and is applied per tool against a real database, so 'this instance has no "
    "compliance module, therefore no compliance tool may run' has nowhere to be asserted. Wire "
    "a module-availability check the router consults, then this asserts: given an instance with "
    "no compliance records, a compliance question returns that it has no compliance module and "
    "no compliance tool is invoked."))
def test_an_instance_without_compliance_returns_no_compliance_results():
    raise AssertionError("unwired")


@pytest.mark.skip(reason=(
    "Same seam, stronger half of the feedback: a row belonging to another instance never "
    "appears, whatever the question. Needs two seeded instances and the scoped read path under "
    "test; today it can only be checked against the live database."))
def test_one_instance_never_sees_another_instances_rows():
    raise AssertionError("unwired")


# -- 5. Frontend and backend runs share one orchestration -------------------------------

@pytest.mark.skip(reason=(
    "Needs both entry points to expose their routing decision without running a model. The "
    "frontend path goes through the streaming orchestrator, the batch path through its own "
    "caller; comparing them means capturing {agent, tools, instance} from each for the same "
    "question. Add that capture, then this asserts the two are equal across a fixed set."))
def test_the_same_question_routes_the_same_way_from_either_entry_point():
    raise AssertionError("unwired")


# -- 6. New traces sync without erasing review data -------------------------------------

@pytest.mark.skip(reason=(
    "There is no sync to test: no svc-trace-lab package exists, and the lab runs as its own "
    "process against hoist_tracelab. The spec to hold it to: a sync that ingests new turns "
    "leaves every existing annotation, label and taxonomy decision untouched, and orders newest "
    "first. Worth writing against the sync as it is built, not after."))
def test_a_trace_sync_adds_new_turns_and_keeps_existing_annotations():
    raise AssertionError("unwired")
