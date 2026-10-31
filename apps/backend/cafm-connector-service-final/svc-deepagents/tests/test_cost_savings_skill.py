"""Cost saving in the chat: the tool is held where the questions land, the skills require the section."""
from __future__ import annotations

from src.agents import cost_savings_agent as cs
from src.agents import ontology_qa as oq
from src.agents.skills import skill_for_agent


def test_the_cost_saving_read_is_held_by_the_maintenance_and_energy_engines():
    from src.agents.energy_intelligence_agent import ENERGY_INTELLIGENCE_TOOLS
    from src.agents.wo_engine_agent import MAINTENANCE_READ_TOOLS
    assert cs.get_cost_savings in MAINTENANCE_READ_TOOLS and cs.get_cost_savings in ENERGY_INTELLIGENCE_TOOLS


def test_a_period_is_one_the_service_takes():
    assert cs._period(None) == "last_month" and cs._period("YTD") == "this_year"
    assert cs._period("last quarter") == "last_90_days" and cs._period("nonsense") == "last_month"


def test_cost_saving_questions_route_to_the_work_order_engine_and_every_answer_ends_with_options():
    wo = skill_for_agent("wo_engine")
    assert "COST SAVING" in wo.description and "cost saving" in wo.triggers and "cost-savings" in wo.references
    assert "Cost-saving options" in wo.body
    assert "Cost-saving options" in skill_for_agent("energy_intelligence").body
    assert "Cost-saving options" in oq.ANSWER_RULES and "Cost-saving options" in oq.PENDING_RULES
    assert "Corrective action plan" in cs.ANSWER_RULES and "never add them up" in cs.ANSWER_RULES
