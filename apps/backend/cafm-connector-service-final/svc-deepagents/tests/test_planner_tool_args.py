"""The planner is told what arguments a tool takes, and a filter that did nothing is fixed in args.

7 Oct 2026: "a building-wise breakdown of all the assets at risk, with the detected anomaly for
each". The router chose energy_intelligence and the planner chose list_asset_conditions - both
right - but the step's args were empty: the planner's line for the tool was 160 characters of
description and no parameter names, so "Threat band" went into the ask, where no tool reads it.
All 61 assets came back; the re-plan then swapped the right tool for a wider record search.
"""
from __future__ import annotations

import pytest

from src.agents import planner, skills


def test_a_tool_line_names_its_arguments():
    from src.agents.energy_intelligence_agent import list_asset_conditions as t
    line = planner.catalogue_line(t.name, t.description, t.args)
    assert line.endswith("Args: band, building_id, min_deviation_pct, limit.")


def test_question_is_not_listed_as_a_filter():
    line = planner.catalogue_line("answer_from_records", "Answer a question about records.",
                                  {"question": {}, "limit": {}})
    assert line.endswith("Args: limit.")


def test_the_planner_catalogue_carries_the_args():
    import src.agents.orchestrator as O
    cls = next(v for v in vars(O).values() if isinstance(v, type) and hasattr(v, "_planner_tools"))
    tools = cls._planner_tools(cls.__new__(cls))
    assert "Args: band," in tools["list_asset_conditions"]


def test_a_filter_that_did_nothing_tells_the_replan_to_keep_the_tool_and_set_args():
    step = {"ask": "List every asset in the Threat energy band", "target": "list_asset_conditions"}
    out = {"output": {"count": 61, "total": 61, "assets": [{"asset_code": "A"}, {"asset_code": "B"}]}}
    res = {"ok": True, "output": out["output"]}
    f = [x for x in planner.inspect_step(step, res) if x["kind"] == "filter_did_nothing"]
    assert f and "Keep this tool" in f[0]["detail"] and "`args`" in f[0]["detail"]


@pytest.mark.parametrize("q", [
    "give me a building wise breakdown of all the assets at risk in each of the building along with the detected anamoly for each one",
    "which assets are at risk at Bishopsgate",
])
def test_at_risk_asset_questions_route_to_energy(q):
    skills.reload_skills()
    assert skills.route(q)["primary_agent"] == "energy_intelligence"


@pytest.mark.parametrize("q", ["list all assets in B-301", "how many assets do we have"])
def test_a_plain_register_question_still_goes_to_the_register(q):
    skills.reload_skills()
    assert skills.route(q)["primary_agent"] == "udr"
