"""A role space answers from its own modules and says so when a question is outside them.

Reported 30 Oct: "Each module should separate the data specific to it. For example you cannot
have compliance certificates shown in the OpsBrain. It is not part of the module."

It could not work. The spaces are declared in the FRONTEND (logic/instances.js) and the active
one never reached the backend; grepping agent_router.py and orchestrator.py for a module concept
found nothing at all. So a question asked in Ops Brain was routed across every engine, and
compliance answered because nothing told it not to.

The space's `reports` list is the module -- that is what the person is looking at. Ops Brain also
names compliance in its VALUE ledger, but that is a headline figure on the home page, not a
report anybody can open, and a figure in a card is not permission to read the records behind it.
"""
from __future__ import annotations

import pytest

from src.agents.planner import ENGINES
from src.agents.spaces import (SPACE_REPORTS, allows, engines_for, refusal, space_with)


# -- what each space may use ---------------------------------------------------------------

def test_ops_brain_has_work_orders_and_vendors_and_not_compliance():
    assert engines_for("ops") == {"wo_engine", "contract_performance"}


def test_the_compliance_engine_has_compliance_and_vendors_and_not_work_orders():
    assert engines_for("comp") == {"compliance", "contract_performance"}


def test_the_assets_operating_system_has_energy_work_orders_and_vendors():
    assert engines_for("assets") == {"energy_intelligence", "wo_engine", "contract_performance"}


def test_the_full_platform_has_every_engine():
    assert engines_for("full") == set(ENGINES)


def test_every_space_maps_onto_engines_that_exist():
    """A space naming an engine the planner has never heard of would be unroutable."""
    for key in SPACE_REPORTS:
        assert (engines_for(key) or set()) <= set(ENGINES), key


# -- an unknown space loses nothing ---------------------------------------------------------

@pytest.mark.parametrize("space", [None, "", "   ", "something_new"])
def test_no_space_or_an_unknown_one_allows_every_engine(space):
    assert engines_for(space) is None
    for engine in ENGINES:
        assert allows(space, engine) is True


# -- the reported case ----------------------------------------------------------------------

def test_compliance_is_refused_in_ops_brain():
    assert allows("ops", "compliance") is False
    said = refusal("ops", "compliance")
    assert said and "compliance" in said and "Ops Brain" in said


def test_the_refusal_says_where_the_answer_lives():
    said = refusal("ops", "compliance")
    assert "Compliance Engine" in said, (
        "exactly one space has compliance, so the person should be told which")


def test_a_refusal_never_offers_to_answer_anyway():
    said = refusal("ops", "compliance").lower()
    assert "anyway" not in said and "but i can" not in said


def test_work_orders_are_refused_in_the_compliance_engine():
    assert allows("comp", "wo_engine") is False
    assert "Compliance Engine" in refusal("comp", "wo_engine")


def test_energy_is_refused_in_ops_brain_and_points_at_the_assets_space():
    assert allows("ops", "energy_intelligence") is False
    assert "Assets Operating System" in refusal("ops", "energy_intelligence")


# -- what each space still answers ------------------------------------------------------------

@pytest.mark.parametrize("space,engine", [
    ("ops", "wo_engine"), ("ops", "contract_performance"),
    ("comp", "compliance"), ("comp", "contract_performance"),
    ("assets", "energy_intelligence"), ("assets", "wo_engine"),
    ("assets", "contract_performance"),
])
def test_a_space_answers_from_its_own_modules(space, engine):
    assert allows(space, engine) is True
    assert refusal(space, engine) is None


@pytest.mark.parametrize("engine", list(ENGINES))
def test_the_full_platform_refuses_nothing(engine):
    assert allows("full", engine) is True and refusal("full", engine) is None


# -- what is not a module ---------------------------------------------------------------------

@pytest.mark.parametrize("engine", ["migration", "udr", "doc_rag", "orchestrator loop", None])
def test_ingesting_migrating_and_reading_documents_are_not_gated(engine):
    """Every space ingests its own files. A module is a data domain, not a verb."""
    for space in SPACE_REPORTS:
        assert allows(space, engine) is True
        assert refusal(space, engine) is None


# -- where to send someone ----------------------------------------------------------------------

def test_an_engine_only_one_space_has_names_that_space():
    assert space_with("compliance") == "comp"
    assert space_with("energy_intelligence") == "assets"


def test_an_engine_several_spaces_have_names_none():
    """Suggesting one of three is worse than suggesting none."""
    assert space_with("contract_performance") is None
    assert space_with("wo_engine") is None


def test_something_that_is_not_a_domain_engine_names_no_space():
    assert space_with("migration") is None
    assert space_with(None) is None


# -- the router honours the space ---------------------------------------------------------

class TestTheRouterIsGatedByTheSpace:
    def _catalogue(self, space):
        from src.agents.agent_router import _catalogue
        return _catalogue(space)

    def test_ops_brain_is_not_offered_the_compliance_agent(self):
        text, known = self._catalogue("ops")
        assert "compliance" not in known, (
            "the model cannot choose what it is not shown; leaving compliance in the catalogue "
            "invites exactly the answer that should not happen")
        # Its NAME must not be offered. The word itself legitimately appears inside other
        # agents' descriptions ("blocked by a vendor's compliance"), which is prose, not a choice.
        assert "- compliance:" not in text

    def test_ops_brain_is_still_offered_its_own_agents(self):
        _, known = self._catalogue("ops")
        assert {"wo_engine", "contract_performance"} <= known

    def test_the_compliance_engine_is_not_offered_work_orders(self):
        _, known = self._catalogue("comp")
        assert "wo_engine" not in known and "compliance" in known

    def test_a_space_that_is_not_known_keeps_every_agent(self):
        _, every = self._catalogue(None)
        _, same = self._catalogue("something_new")
        assert every == same and len(every) > 4

    def test_ingestion_and_migration_survive_every_space(self):
        for space in ("ops", "comp", "assets", "full"):
            _, known = self._catalogue(space)
            assert "migration" in known and "udr" in known, space

    def test_the_refusal_the_router_returns_names_the_space_and_the_domain(self):
        from src.agents.agent_router import _out_of_module
        got = _out_of_module("ops", "compliance", "llm")
        assert got["agent"] is None, "nothing may run"
        assert got["wanted"] == "compliance"
        assert "Ops Brain" in got["out_of_module"]
        assert "Compliance Engine" in got["out_of_module"]

    def test_an_allowed_engine_is_not_refused(self):
        from src.agents.agent_router import _out_of_module
        assert _out_of_module("ops", "wo_engine", "llm") is None
        assert _out_of_module(None, "compliance", "llm") is None

    def test_select_agent_takes_a_space(self):
        import inspect
        from src.agents.agent_router import select_agent
        assert "space" in inspect.signature(select_agent).parameters, (
            "the caller has nothing to pass the active space in, so the gate never fires")


# -- every space answers the questions it offers -------------------------------------------

class TestASpaceNeverRefusesItsOwnChips:
    """Found by checking the four role spaces after the gate went in (1 Nov 2026).

    Ops Brain offers "Which inspection recommendations were never actioned?" on its own home
    page, and it routed to the compliance engine, which Ops Brain does not have. The gate turned
    a routing quirk into a dead button: a chip the space suggests, refused by the space.

    "inspection" is a compliance keyword, rightly -- a LOLER or EICR inspection is a statutory
    duty. An inspection REPORT's open recommendations are work to be done, which is what the Ops
    Brain home card counts, so those are phrases that outscore the bare word.
    """

    #: The pinned chips and suggested asks, from logic/instances.js.
    OWN_QUESTIONS = {
        "ops": ["What needs my approval today?", "Which vendor is costing me money?",
                "Which inspection recommendations were never actioned?",
                "Is planned maintenance running to plan?",
                "What is open across my buildings this week?", "What documents have I ingested?",
                "Which assets are unaccounted for?", "Is each vendor inside its SLA?",
                "What service credit can I claim?"],
        "comp": ["Which buildings are out of compliance today?",
                 "Which vendors are not eligible to work here?",
                 "What certificates lapse in the next 30 days?",
                 "Which certificates failed authenticity checks?",
                 "What documents evidence each certificate?",
                 "Which approvals expire this month?", "Who can replace a blocked vendor?"],
        "assets": ["Which assets are losing value?", "What is energy telling me about asset health?",
                   "Which assets should I remediate first?", "What will break in the next 90 days?"],
        "full": ["Which buildings put me at risk this month?", "What needs my approval today?",
                 "Which vendors are blocked right now?"],
    }

    @pytest.mark.parametrize("space,question", [
        (s, q) for s, qs in OWN_QUESTIONS.items() for q in qs
    ])
    def test_a_space_answers_the_question_it_suggests(self, space, question):
        from src.agents.phase2_intents import resolve_phase2_engine
        engine = resolve_phase2_engine(user_message=question, context_note=None)
        assert allows(space, engine), (
            "%s suggests %r on its own page and then refuses it: the router sends it to %s, "
            "which %s does not have" % (space, question, engine, space))

    def test_an_inspection_report_recommendation_is_maintenance(self):
        from src.agents.phase2_intents import resolve_phase2_engine as r
        assert r(user_message="Which inspection recommendations were never actioned?",
                 context_note=None) == "wo_engine"

    @pytest.mark.parametrize("question", [
        "When is the next LOLER inspection due?",
        "Show the EICR inspection certificate",
        "Which certificates lapse in the next 30 days?",
    ])
    def test_a_statutory_inspection_is_still_compliance(self, question):
        from src.agents.phase2_intents import resolve_phase2_engine as r
        assert r(user_message=question, context_note=None) == "compliance"
