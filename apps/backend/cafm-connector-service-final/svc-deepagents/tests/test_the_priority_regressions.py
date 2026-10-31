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

import uuid

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


def test_a_name_shared_by_two_buildings_returns_both():
    """Already true, so a guard: "Bishopsgate" scores 0.970 against Tower and House alike, and
    FUZZY_TIE keeps both. Clarification has the candidates it needs."""
    assert {k for k, _ in pick_closest("Bishopsgate", AMBIGUOUS)} == {1, 2}


def test_a_split_compound_is_ambiguous_rather_than_unmatched():
    """The real fault behind the reported one. "Bishop Gate" matches NOTHING today -- 0.714
    against the two Bishopsgate buildings, under the bar -- so there is no silent pick, there is
    silence. Once a split compound is read as the same name it ties across both, and the tie
    logic above asks."""
    got = pick_closest("Bishop Gate", AMBIGUOUS)
    assert {k for k, _ in got} == {1, 2}, (
        "got %r: a person who types the building the way people say it should reach both "
        "Bishopsgate buildings and be asked which" % (got,))


def test_a_clear_winner_is_still_picked_alone():
    """"Bishop" is a whole word of Bishop Street Depot (0.970) and only a fragment of the others
    (0.685). That is a winner, not an ambiguity, and must not become a question."""
    assert pick_closest("Bishop", AMBIGUOUS) == [(3, "Bishop Street Depot")]


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

# The seam these needed does exist and is pure: services/principal.py reads the caller from a
# contextvar and narrows every building-keyed read to the buildings that caller is allocated.
# building_clause gives the SQL, restrict_rows applies the same boundary to rows that came back
# unfiltered. Neither needs a database, so both halves of the feedback can be asserted here.

def _as(building_ids):
    """Become a caller allocated to these buildings. None is an admin, () is allocated nothing."""
    from src.services.principal import Principal, caller_principal
    p = Principal(user_id=uuid.uuid4(), email="t@example.test", organization_id=uuid.uuid4(),
                  role="user", can_ingest=False,
                  building_ids=None if building_ids is None else tuple(building_ids))
    return caller_principal.set(p)


MINE = uuid.UUID("11111111-1111-4111-8111-111111111111")
THEIRS = uuid.UUID("22222222-2222-4222-8222-222222222222")


class TestOneInstanceNeverSeesAnothersRows:
    def test_a_row_from_another_instance_is_dropped(self):
        from src.services.principal import caller_principal, restrict_rows
        token = _as([MINE])
        try:
            rows = [{"building_id": str(MINE), "name": "mine"},
                    {"building_id": str(THEIRS), "name": "theirs"}]
            assert restrict_rows(rows, "building_id") == [{"building_id": str(MINE), "name": "mine"}]
        finally:
            caller_principal.reset(token)

    def test_a_row_with_no_building_is_dropped_too(self):
        """Absent is not the same as allowed; this is how a cross-instance row slips through."""
        from src.services.principal import caller_principal, restrict_rows
        token = _as([MINE])
        try:
            assert restrict_rows([{"building_id": None}, {"other": 1}], "building_id") == []
        finally:
            caller_principal.reset(token)

    def test_the_sql_narrows_to_the_callers_buildings(self):
        from src.services.principal import building_clause, caller_principal
        token = _as([MINE])
        try:
            sql, params = building_clause("b.building_id")
            assert "b.building_id = ANY" in sql
            assert params["scope_buildings"] == [str(MINE)]
        finally:
            caller_principal.reset(token)

    def test_a_caller_allocated_to_nothing_matches_no_row(self):
        """The failure this exists to remove: an empty clause matches EVERY row."""
        from src.services.principal import building_clause, caller_principal, restrict_rows
        token = _as([])
        try:
            sql, params = building_clause("b.building_id")
            assert sql.strip() == "AND FALSE" and params == {}
            assert restrict_rows([{"building_id": str(MINE)}], "building_id") == []
        finally:
            caller_principal.reset(token)

    def test_an_admin_is_not_narrowed(self):
        from src.services.principal import building_clause, caller_principal, restrict_rows
        token = _as(None)
        try:
            assert building_clause("b.building_id") == ("", {})
            rows = [{"building_id": str(MINE)}, {"building_id": str(THEIRS)}]
            assert restrict_rows(rows, "building_id") == rows
        finally:
            caller_principal.reset(token)

    def test_with_no_caller_at_all_a_background_job_is_not_broken(self):
        from src.services.principal import building_clause
        assert building_clause("b.building_id") == ("", {})


class TestAnInstanceWithoutComplianceReturnsNoComplianceResults:
    """The module half, which the space gate answers: an Ops Brain question never reaches the
    compliance engine at all, so there is nothing to filter afterwards."""

    def test_the_compliance_engine_is_unreachable_from_ops_brain(self):
        from src.agents.spaces import allows, refusal
        assert allows("ops", "compliance") is False
        assert "not part of Ops Brain" in refusal("ops", "compliance")

    def test_the_router_never_offers_it_there(self):
        from src.agents.agent_router import _catalogue
        _, known = _catalogue("ops")
        assert "compliance" not in known

    def test_a_space_that_has_compliance_still_answers(self):
        from src.agents.spaces import allows
        assert allows("comp", "compliance") is True


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
