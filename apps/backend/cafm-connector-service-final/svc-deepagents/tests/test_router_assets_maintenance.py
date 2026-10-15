"""Asset and maintenance questions reach an engine, and nothing else moves.

Until 5 Oct 2026 the skill triggers had no word for "maintains", "serviced", "looks after" or for
the equipment people name, so "who maintains the boiler at Bishopsgate?" matched no skill and fell
back to the database reader with a clarifying question; the keyword fallback (used when the model
router is unavailable) had no work-order list at all. 12 of the 17 chat memories no skill claimed
were facts of exactly this kind ("Apex Lifts maintains Lift Asset-4471").

Equipment words live in udr's `naming_triggers`: they rank the asset register first for "show me
the chillers", but never add it as an extra agent run on a certificate or energy question that
happens to name a lift or a boiler.
"""
from __future__ import annotations

import pytest

from src.agents import skills as S
from src.agents.phase2_intents import match_phase2_agent


@pytest.mark.parametrize("question", [
    "Who maintains the boiler at Bishopsgate?",
    "Which vendor services the lifts at Bishopsgate Tower?",
    "Who looks after AHU-3?",
    "What is the maintenance history of Boiler 1?",
    "When was the generator last serviced?",
    "Is the pump due for maintenance?",
    "Should we repair or replace the chiller?",
])
def test_who_maintains_what_goes_to_the_work_order_engine_without_asking_first(question):
    r = S.route(question)
    assert r["primary_agent"] == "wo_engine" and not r["clarify_first"]
    # And when the model router is down, the keyword fallback now picks it too.
    assert match_phase2_agent(question) == "wo_engine"


@pytest.mark.parametrize("question", [
    "Which assets are graded poor at Bishopsgate?",
    "Show me the chillers at Manchester Town Hall",
    "list the assets at Bishopsgate",
])
def test_the_asset_register_answers_asset_questions_without_asking_first(question):
    r = S.route(question)
    assert r["primary_agent"] == "udr" and r["confidence"] != "fallback" and not r["clarify_first"]


def test_a_condition_and_maintenance_question_brings_both():
    r = S.route("Which assets are in poor condition and need maintenance?")
    assert r["primary_agent"] == "wo_engine"
    assert [a["agent"] for a in r["also_relevant"]] == ["udr"]


@pytest.mark.parametrize("question, engine", [
    ("Is the lift LOLER certificate current?", "compliance"),
    ("Which fire alarm certificates are lapsed?", "compliance"),
    ("What is the gas safety certificate expiry for Boiler 1?", "compliance"),
    ("Which buildings have a lapsed fire risk assessment?", "compliance"),
    ("Which boiler is wasting the most energy?", "energy_intelligence"),
    ("What is the EUI of Bishopsgate Tower?", "energy_intelligence"),
    ("Which vendor has the worst SLA completion?", "contract_performance"),
    ("What needs my decision today?", "wo_engine"),
])
def test_specialist_questions_that_name_equipment_stay_where_they_were(question, engine):
    r = S.route(question)
    assert r["primary_agent"] == engine
    assert match_phase2_agent(question) == engine


@pytest.mark.parametrize("question", [
    "Is the lift LOLER certificate current?",
    "What is the gas safety certificate expiry for Boiler 1?",
    "Which boiler is wasting the most energy?",
])
def test_naming_a_piece_of_equipment_never_adds_the_asset_register_as_an_extra_run(question):
    assert "udr" not in [a["agent"] for a in S.route(question)["also_relevant"]]


def test_equipment_words_are_naming_triggers_not_routing_triggers():
    udr = S.skill_for_agent("udr")
    for word in ("boiler", "lift", "ahu", "chiller", "assets"):
        assert word in udr.naming_triggers and word not in udr.triggers


def test_a_question_that_names_a_certificate_and_maintenance_stays_with_compliance_in_the_fallback():
    # The work-order list loses every tie: the certificate is what the question is about.
    assert match_phase2_agent("Is the gas safety certificate for the boiler maintenance current?") == "compliance"
