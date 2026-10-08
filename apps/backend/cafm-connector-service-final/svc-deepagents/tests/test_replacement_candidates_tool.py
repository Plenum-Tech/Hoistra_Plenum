"""The repurchase question reaches one deterministic read, through every router there is."""
from __future__ import annotations

from src.agents import planner
from src.agents.skills import route, skill_for_agent


def test_the_tool_sits_with_the_maintenance_reads_and_the_planner_names_it():
    from src.agents.wo_engine_agent import MAINTENANCE_READ_TOOLS, WO_ENGINE_SUBAGENT_TOOLS
    names = {getattr(t, "name", "") for t in MAINTENANCE_READ_TOOLS}
    assert "replacement_candidates" in names and "work_order_blockers" in names
    assert "replacement_candidates" in {getattr(t, "name", "") for t in WO_ENGINE_SUBAGENT_TOOLS}
    assert "replacement_candidates" in planner.PLAN_PROMPT and "repurchase" in planner.PLAN_PROMPT


def test_repurchase_reorder_and_end_of_life_route_by_keyword_to_wo_engine():
    for q in ("which assets are required for repurchase?", "which assets are at end of life?", "what should we restock?"):
        assert route(q)["primary_agent"] == "wo_engine", q
    # "reorder" and "replacement value" stay udr's words (test_skill_routing); both sub-agents hold the tool
    for q in ("what parts do we need to reorder?", "which assets are past their design life and what is the replacement value"):
        assert route(q)["primary_agent"] in ("wo_engine", "udr"), q
    d = skill_for_agent("wo_engine").description.lower()
    for word in ("repurchase", "reorder level", "end of life", "replacement_candidates"):
        assert word in d, word


def test_the_tool_describes_both_readings_and_names_the_false_register():
    from src.agents.thread_scope import replacement_candidates as t
    doc = (t.description or "").lower()
    assert "parts_to_reorder" in doc and "end_of_life" in doc and "energy_recommendations is not one" in doc
