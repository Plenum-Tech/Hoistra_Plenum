"""An answer the general loop wrote from its sub-agents renders as cards, like every other answer.

Seen 5 Oct 2026: "Show me everything that's at risk" went router -> orchestrator loop -> `task`
to energy_intelligence and compliance. The energy sub-agent called get_asset_condition_summary,
but the turn's own tool list held only `task`, so no card builder ever saw the data and the
answer reached the chat as a markdown table and numbered lists. The trace recorded the
sub-agent's tool output, but cut to its bound; the card tools' outputs are now kept whole on the
turn so the loop's answer is carded from what its sub-agents actually read.
"""
from __future__ import annotations

import json

import pytest

from src.agents import llm_cost, trace
from src.agents.orchestrator import DeepAgentOrchestrator as O
from src.http_client import caller_organization_id

from test_planner_cards import CONDITION


@pytest.fixture
def turn(monkeypatch):
    monkeypatch.setattr(trace, "_ready", True)
    caller_organization_id.set(None)
    trace._turn.set(None)
    llm_cost._ledger.set(None)
    return trace.begin("turn-cards", "s1")


def test_a_sub_agents_card_tool_output_is_kept_whole(turn):
    big = dict(CONDITION, sections=CONDITION["sections"] * 200)        # well past the trace bound
    assert len(json.dumps(big)) > trace.BOUND
    trace.on_tool_open("r1", "get_asset_condition_summary", {"building_id": None}, group="energy_intelligence")
    trace.on_tool_close("r1", json.dumps(big))
    trace.on_tool_open("r2", "list_building_certificates", {}, group="compliance")   # not a card tool
    trace.on_tool_close("r2", {"rows": []})
    kept = trace.card_tool_outputs()
    assert [k["tool"] for k in kept] == ["get_asset_condition_summary"]
    assert kept[0]["output"]["summary"]["threat"] == 6 and len(kept[0]["output"]["sections"]) == 600
    # the span itself stays bounded, as before
    span = next(s for s in turn.spans if s.name == "get_asset_condition_summary")
    assert "_truncated" in span.output


def test_the_loops_answer_is_carded_from_its_sub_agents_reads(turn):
    trace.on_tool_open("r1", "get_asset_condition_summary", {}, group="energy_intelligence")
    trace.on_tool_close("r1", CONDITION)
    calls = [{"tool": "task", "input": {"agent": "energy_intelligence"}, "output": "Six assets are a threat."}]
    cards = O._loop_cards("Show me everything that's at risk", calls, "Six assets are a threat, all at Bishopsgate Tower.")
    assert [c["tool"] for c in cards] == ["compliance_response"]
    assert {k["label"] for k in cards[0]["output"]["kpis"]} >= {"Threat", "Watch", "In control"}


def test_no_card_tool_read_means_no_cards(turn):
    assert O._loop_cards("q", [{"tool": "task", "input": {}, "output": "x"}], "An answer with no data tools.") == []


def test_an_answer_that_already_has_its_dashboard_is_left_alone(turn):
    trace.on_tool_open("r1", "get_asset_condition_summary", {}, group="energy_intelligence")
    trace.on_tool_close("r1", CONDITION)
    calls = [{"tool": "compliance_response", "input": {}, "output": {"kpis": [{"count": 1}]}}]
    assert O._loop_cards("q", calls, "Six assets are a threat today.") == []



def test_two_sub_agents_reading_one_tool_differently_are_not_merged_into_one_tile(turn):
    other = dict(CONDITION, summary=dict(CONDITION["summary"], threat=1))
    trace.on_tool_open("r1", "get_asset_condition_summary", {}, group="energy_intelligence")
    trace.on_tool_close("r1", CONDITION)
    trace.on_tool_open("r2", "get_asset_condition_summary", {}, group="energy_intelligence")
    trace.on_tool_close("r2", other)
    assert O._loop_cards("q", [{"tool": "task", "input": {}, "output": "x"}], "Two buildings, two answers.") == []


def test_the_turns_domain_comes_from_the_last_tool_that_did_work():
    from src.agents.session_workspace import last_working_tool
    calls = [{"tool": "replacement_candidates"}, {"tool": "compliance_pipeline"}, {"tool": "compliance_response"}]
    assert last_working_tool(calls) == "replacement_candidates"
    assert last_working_tool([{"tool": "planner"}]) == "" and last_working_tool([]) == ""
