"""An answer takes its form, answers every part, and a follow-up keeps the conversation's records (A4).

Workstream A4, 11 Nov 2026. The specification - the four forms and one worked example of each - is
skills/query-builder/answer-spec.md. These are its eval scenarios: each example is referenced by id,
its form is what the scenario's lookups call for, and its answer passes the same checks the writers
are held to. Then the checks themselves (each catches what it should and passes what it should), the
parts of a question, and continuity: the records an answer showed are kept, taken from the rows, and
a follow-up reads inside them without asking again.
"""
from __future__ import annotations

import asyncio

import pytest

from src.agents import answer_spec as spec
from src.agents import evidence as ev
from src.agents import final_answer as fa
from src.agents import planner, thread_scope

OK = {"ok": True, "status": ev.OK, "output": {"rows": [{"code": "x"}]}}
ZERO = {"ok": True, "status": ev.ZERO, "output": []}
NOT_RECORDED = {"ok": True, "status": ev.NOT_RECORDED, "output": {"rows": [{"floor_area": None}]}}
TIMEOUT = {"ok": False, "status": ev.FAILED, "failure": ev.from_status_code(504)}
DOWN = {"ok": False, "status": ev.UNAVAILABLE, "failure": ev.from_status_code(503)}
REFUSED = {"ok": False, "status": ev.DENIED, "failure": ev.from_status_code(403)}

#: The eval scenarios: each names the example in the spec it is held to, and the lookups behind it.
SCENARIOS = [
    {"example": "E-1", "results": {"s1": OK}},
    {"example": "E-2", "results": {"s1": ZERO}},
    {"example": "E-3", "results": {"s1": OK, "s2": TIMEOUT}},
    {"example": "E-4", "results": {"s1": REFUSED}},
    {"example": "E-5", "results": {"s1": DOWN}},
    {"example": "E-6", "results": {"s1": OK, "s2": OK, "s3": NOT_RECORDED}},
]


def _example(eid: str) -> dict:
    return next(e for e in spec.examples() if e["id"] == eid)


def test_the_spec_has_an_example_of_every_form():
    assert {e["form"] for e in spec.examples()} == set(spec.FORMS)
    assert {s["example"] for s in SCENARIOS} == {e["id"] for e in spec.examples()}


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s["example"])
def test_each_scenario_calls_for_its_examples_form(scenario):
    assert spec.form_for(scenario["results"]) == _example(scenario["example"])["form"]


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s["example"])
def test_each_example_passes_the_checks_the_writers_are_held_to(scenario):
    e = _example(scenario["example"])
    assert e["answer"] and e["question"]
    assert spec.checks(e["answer"], e["form"], spec.parts_of(e["question"])) == []
    assert ev.state_mismatches(e["answer"], scenario["results"]) == []


def test_both_writers_are_given_the_forms_and_the_examples():
    block = spec.prompt_block(spec.PARTIAL, ["energy at Kingsway", "its blocked vendors"])
    assert "ANSWER FORM: F-PARTIAL" in block and "1. energy at Kingsway" in block
    assert "### E-3 · F-PARTIAL" in block and "### F-DENIAL" in block
    assert "{forms}" in fa.FINAL_PROMPT and "{forms}" in planner.SYNTH_PROMPT


# -- each check catches what it should ----------------------------------------------------------

def test_a_partial_answer_must_name_what_it_could_not_answer():
    assert spec.form_mismatches("Kingsway House used 41,250 kWh.", spec.PARTIAL)
    assert not spec.form_mismatches("Kingsway House used 41,250 kWh.\n**Not answered:** its vendors - the lookup "
                                    "took too long. Ask again in a moment.", spec.PARTIAL)


def test_a_denial_must_say_so_and_give_the_next_step():
    assert spec.form_mismatches("The invoices could not be shown.", spec.DENIAL)
    assert spec.form_mismatches("You do not have access to these invoices.", spec.DENIAL)
    assert not spec.form_mismatches("You do not have access to these invoices.\n**Next step:** ask an administrator.",
                                    spec.DENIAL)


def test_a_failure_gives_the_next_step_and_no_table_of_figures():
    assert spec.form_mismatches("The compliance position could not be read.", spec.FAILURE)
    tabled = "Could not be read.\n**Next step:** ask again.\n| a | b |\n|---|---|\n| 1 | 2 |"
    assert any("table" in m for m in spec.form_mismatches(tabled, spec.FAILURE))


@pytest.mark.parametrize("opening", ["Here is the answer:", "Sure - here are the figures.", "Based on the records,"])
def test_every_answer_leads_with_the_answer(opening):
    assert spec.form_mismatches(opening + "\n3 work orders are overdue.", spec.SUCCESS)


# -- the parts of a question --------------------------------------------------------------------

@pytest.mark.parametrize("question,n", [
    ("How many work orders are overdue at Bishopsgate Tower?", 1),
    ("How much energy did Kingsway House use last month, and which of its vendors are blocked?", 2),
    ("Which assets failed most? Which of them are under warranty?", 2),
    ("List the overdue PPMs; then show the vendors who own them", 2),
])
def test_a_question_is_split_into_the_parts_that_each_need_an_answer(question, n):
    assert len(spec.split_question(question)) == n


def test_a_plans_steps_are_the_parts_to_check_off():
    plan = {"steps": [{"ask": "energy at Kingsway last month"}, {"ask": "Kingsway's blocked vendors"}]}
    assert spec.parts_of("anything", plan) == ["energy at Kingsway last month", "Kingsway's blocked vendors"]


def test_a_part_the_answer_never_speaks_to_is_found():
    parts = spec.split_question("How much energy did Kingsway House use last month, and which of its vendors are blocked?")
    assert spec.uncovered("Kingsway House used 41,250 kWh in October.", parts) == [parts[1]]
    assert spec.uncovered("Kingsway House used 41,250 kWh. No vendors are blocked.", parts) == []


def test_the_final_writer_rewrites_an_answer_that_leaves_a_part_out():
    results, _ = fa.gather([{"tool": "energy", "output": {"rows": [{"building": "Kingsway House", "kwh": 41250}]}},
                            {"tool": "vendors", "output": []}])
    replies = iter(["Kingsway House used 41,250 kWh.", "Kingsway House used 41,250 kWh. No vendors are blocked there."])
    seen = []

    async def llm(system, user, role):
        seen.append(user)
        return next(replies)

    q = "How much energy did Kingsway House use last month, and which of its vendors are blocked?"
    answer, report = asyncio.run(fa.write(q, "", results=results, llm=llm))
    assert answer.endswith("No vendors are blocked there.") and report["rewrites"] == 1
    assert "does not address" in seen[1] and report["parts"] == spec.split_question(q)
    assert "PARTS OF THE QUESTION" in seen[0]


# -- continuity ---------------------------------------------------------------------------------

@pytest.mark.parametrize("question,expected", [
    ("show me those", True),
    ("what about the vendors?", True),
    ("And for Kingsway House?", True),
    ("for each of them, what does it cost?", True),
    ("how many buildings do we have?", False),
    ("what about all buildings?", False),
])
def test_a_follow_up_is_known_without_a_pronoun(question, expected):
    assert thread_scope.continues(question) is expected


ROWS = {"rows": [{"vendor_name": "Acme Lifts", "vendor_code": "V-12", "vendor_id": "u1"},
                 {"vendor_name": "Zed Electrical", "vendor_code": "V-40", "vendor_id": "u2"},
                 {"asset_code": "AHU-03", "asset_name": "Air handler 3", "asset_id": "a3"}]}


def test_the_records_kept_are_the_ones_a_tool_returned_and_the_answer_showed():
    shown = thread_scope.entities([{"tool": "list_blocked_vendors", "output": ROWS}],
                                  "Acme Lifts is blocked; AHU-03 is waiting on it. Ghost Ltd is fine.")
    assert shown["vendors"] == [{"name": "Acme Lifts", "code": "V-12", "id": "u1"}]
    assert shown["assets"] == [{"name": "Air handler 3", "code": "AHU-03", "id": "a3"}]
    assert all(e["name"] != "Ghost Ltd" for e in shown["vendors"])  # named in the answer, not in any row


def test_the_working_set_keeps_them_and_a_follow_up_reads_inside_it():
    ws = thread_scope.derive(None, "which vendors are blocked at Bishopsgate Tower?", "Acme Lifts is blocked.",
                             [{"tool": "list_blocked_vendors", "input": {"building_name": "Bishopsgate Tower"}, "output": ROWS}])
    assert ws["entities"]["vendors"][0]["name"] == "Acme Lifts"
    assert "vendors the last answer showed: Acme Lifts" in thread_scope.describe(ws)
    after = thread_scope.derive(ws, "what about their open work orders?", "Acme Lifts has 2 open work orders.", [])
    assert after["building_name"] == "Bishopsgate Tower" and after["entities"]["vendors"][0]["name"] == "Acme Lifts"


def test_a_follow_up_that_names_another_building_moves_to_it_and_keeps_the_period():
    ws = {"building_name": "Bishopsgate Tower", "building_code": "B-301",
          "period": {"label": "October 2026", "from": "2026-10-01", "to": "2026-11-01"},
          "entities": {"vendors": [{"name": "Acme Lifts"}]}, "work_orders": ["WO-1"]}
    moved = thread_scope.carry(ws, "And for Kingsway House?", {"building_name": "Kingsway House", "building_id": "b2"})
    assert moved["building_name"] == "Kingsway House" and moved["period"]["label"] == "October 2026"
    assert "entities" not in moved and "work_orders" not in moved
    assert thread_scope.carry(ws, "what about the vendors?", None) is ws


def test_the_orchestrator_applies_the_working_set_to_a_follow_up_without_a_pronoun(monkeypatch):
    from src.agents import orchestrator as orch_mod
    from src.services import chat_threads

    ws = {"building_name": "Bishopsgate Tower", "entities": {"vendors": [{"name": "Acme Lifts"}]}}

    async def working_set(sid):
        return ws

    monkeypatch.setattr(chat_threads, "working_set", working_set)
    O = orch_mod.DeepAgentOrchestrator
    o = O.__new__(O)

    async def no_own_scope(question):
        return None

    o._question_scope = no_own_scope
    thread_scope.active_scope.set(None)

    async def go():
        await o._set_turn_scope("s1", "what about their open work orders?")
        return thread_scope.active_scope.get()

    assert asyncio.run(go()) == ws
