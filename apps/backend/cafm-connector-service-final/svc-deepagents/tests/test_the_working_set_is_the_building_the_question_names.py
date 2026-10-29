"""The thread's working set holds the building the question names, not a stale one (29 Oct 2026).

"HOW MANY FLOORS IN JASMEET BUILDING", asked after a thread about Bishopsgate Tower, left the working
set on Bishopsgate Tower: the question's building reached no tool input (answer_from_records takes the
question only), so derive() found nothing new and kept the old set — and the next "out of those"
was filtered to the wrong building. The same happened when the model passed the old building to one
tool and the new one to another (the first tool input won), when a building id from one tool was
paired with another tool's building name, and when the building's code had more than three digits
(B-090765) or no hyphen. A follow-up that names another building ("out of those, which are in
JASMEET BUILDING?") kept the old anchor too.

The building is now the one the question names: tool inputs are compared with the question by the
shared ladder (agents/name_match.py), a phrase the question calls a building is read as one, and a
name and an id are only kept together when one tool input carried both.
"""
from __future__ import annotations

from datetime import date

import pytest

from src.agents import thread_scope as ts

TODAY = date(2026, 10, 29)

Q1 = "How many work orders were raised at Bishopsgate Tower last month?"
A1 = "794 raised in September 2026; 13 open: WO-B-301-4527 and WO-B-301-4562 among them."
T1 = [{"tool": "get_cost_savings", "input": {"building_name": "Bishopsgate Tower", "building_id": "bld-301",
                                              "period": "last_month"}}]
JQ = "HOW MANY FLOORS IN JASMEET BUILDING"


def _bishopsgate():
    ws = ts.derive(None, Q1, A1, T1, TODAY)
    assert ws["building_name"] == "Bishopsgate Tower" and ws["building_code"] == "B-301"
    return ws


def _names_jasmeet(ws) -> bool:
    from src.agents import name_match
    return name_match.names_match(ws.get("building_name") or "", "JASMEET", {"building"})


def test_a_new_question_naming_a_building_replaces_the_stale_one():
    ws = ts.derive(_bishopsgate(), JQ, "JASMEET has 89 floors.",
                   [{"tool": "answer_from_records", "input": {"question": JQ}}], TODAY)
    assert ws and _names_jasmeet(ws), ws
    assert ws.get("building_id") != "bld-301" and ws.get("building_code") != "B-301"
    assert ws["work_orders"] == []


def test_the_tool_input_the_question_names_wins_over_a_stale_one():
    tools = [{"tool": "get_cost_savings", "input": {"building_name": "Bishopsgate Tower", "building_id": "bld-301"}},
             {"tool": "list_building_documents", "input": {"building_name": "JASMEET"}}]
    ws = ts.derive(None, "how many floors in jasmeet building", "89.", tools, TODAY)
    assert ws["building_name"] == "JASMEET"
    assert ws["building_id"] is None, "Bishopsgate's id was kept beside JASMEET's name"


@pytest.mark.parametrize("question,stale,named", [
    ("how many floors in tower hussain", "Bishopsgate Tower", "Hussain Tower"),
    ("how many floors in Jasmet", "Bishopsgate Tower", "JASMEET"),
    ("how many floors in RT5 Crossrail", "Bishopsgate Tower", "RT5, Crossrail"),
    ("open work orders in Building 7", "Building 70", "Building 7"),
], ids=["word order", "typo", "stray comma", "building 7 is not building 70"])
def test_the_named_building_is_found_however_it_is_typed(question, stale, named):
    tools = [{"tool": "a", "input": {"building_name": stale}}, {"tool": "b", "input": {"building_name": named}}]
    assert ts.derive(None, question, "", tools, TODAY)["building_name"] == named


def test_a_name_and_an_id_from_different_tools_are_not_paired():
    tools = [{"tool": "list_building_documents", "input": {"building_name": "JASMEET"}},
             {"tool": "get_cost_savings", "input": {"building_id": "bld-301"}}]
    ts.active_scope.set(ts.derive(None, JQ, "", tools, TODAY))
    try:
        name, bid = ts.scope_building()
        assert name == "JASMEET" and bid is None
    finally:
        ts.active_scope.set(None)


@pytest.mark.parametrize("text,code", [
    ("JASMEET (B-090765) has 89 floors.", "B-090765"),
    ("Floors at B090765?", "B090765"),
])
def test_a_building_code_of_any_length_is_kept(text, code):
    ws = ts.derive(None, text, text, [], TODAY)
    assert ws and ws["building_code"] == code


def test_a_follow_up_naming_another_building_is_not_held_to_the_old_one():
    q = "out of those, which are in JASMEET BUILDING?"
    ws = ts.derive(_bishopsgate(), q, "None of them.", [{"tool": "work_order_blockers", "input": {}}], TODAY)
    assert _names_jasmeet(ws), ws
    assert ws.get("building_id") != "bld-301" and ws.get("building_code") != "B-301"


def test_a_follow_up_naming_the_same_building_keeps_the_anchor():
    anchor = _bishopsgate()
    ws = ts.derive(anchor, "out of those, which are at Bishopsgate Tower's east wing?", "WO-B-301-4562.",
                   [{"tool": "work_order_blockers", "input": {"building_name": "Bishopsgate Tower"}}], TODAY)
    assert ws["building_name"] == "Bishopsgate Tower" and ws["building_id"] == "bld-301"
    assert ws["work_orders"] == anchor["work_orders"] and ws["turns"] == 2


def test_the_building_alone_names_no_building():
    anchor = _bishopsgate()
    ws = ts.derive(anchor, "out of those, which are in the building?", "Two.", [], TODAY)
    assert ws["building_name"] == "Bishopsgate Tower" and ws["turns"] == 2
    assert ts.derive(None, "how many floors in the building", "", [], TODAY) is None


def test_two_buildings_named_are_both_kept_and_neither_is_picked():
    tools = [{"tool": "a", "input": {"building_name": "JASMEET"}},
             {"tool": "b", "input": {"building_name": "Hussain Tower"}}]
    ws = ts.derive(None, "compare floors in JASMEET and Hussain Tower", "", tools, TODAY)
    assert ws["building_name"] is None and ws["buildings"] == ["JASMEET", "Hussain Tower"]
    assert "JASMEET" in ts.describe(ws) and "Hussain Tower" in ts.describe(ws)
    ts.active_scope.set(ws)
    try:
        assert ts.scope_building() == (None, None)
    finally:
        ts.active_scope.set(None)


def test_a_short_form_of_the_same_name_is_one_building():
    tools = [{"tool": "a", "input": {"building_name": "Bishopsgate"}},
             {"tool": "b", "input": {"building_name": "Bishopsgate Tower", "building_id": "bld-301"}}]
    ws = ts.derive(None, Q1, "", tools, TODAY)
    assert ws["building_name"] == "Bishopsgate Tower" and ws["building_id"] == "bld-301"


@pytest.mark.parametrize("question", ["What did building services cost at Kew last month?",
                                      "how many jobs for the site office at Kew?"])
def test_a_kind_word_in_fm_jargon_is_not_a_building(question):
    ws = ts.derive(None, question, "", [{"tool": "a", "input": {"building_name": "Kew"}}], TODAY)
    assert ws["building_name"] == "Kew" and "buildings" not in ws
