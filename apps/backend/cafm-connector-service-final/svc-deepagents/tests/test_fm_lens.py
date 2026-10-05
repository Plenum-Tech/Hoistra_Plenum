"""Every answer weighs what it found as a facilities manager would.

Until 5 Oct 2026 the facilities manager's order of what matters (risk to life, statutory exposure,
service to occupants, money) lived in two places: the record engine's answer rules and the
compliance tiers. A question routed anywhere else (assets through the database reader, energy,
vendors, documents, a multi-part plan) got facts with nothing weighing them. The lens is now one
file, skills/query-builder/fm-lens.md, and these pin that every answer writer reads it.
"""
from __future__ import annotations

import asyncio

from src.agents import planner, skills, system_prompt
from src.agents.ontology_qa import ANSWER_RULES

LENS = skills.fm_lens()


def test_the_lens_is_loaded_and_states_the_order_of_what_matters():
    assert LENS, "skills/query-builder/fm-lens.md did not load"
    order = [LENS.index(t) for t in ("Risk to life", "Statutory and legal exposure", "Service to occupants", "Money")]
    assert order == sorted(order)


def test_the_lens_sizes_the_answer_so_a_lookup_gets_no_action_list():
    # An earlier "always include next steps" put a renewal paragraph under one-line answers.
    assert "A lookup" in LENS and "No action list under a one-line answer" in LENS
    assert "A lookup has no such section" in LENS


def test_the_lens_names_every_area_the_user_asked_about():
    for area in ("**Assets:**", "**Energy:**", "**Work orders:**", "**Vendors:**", "**Documents:**", "**Compliance:**"):
        assert area in LENS


def test_every_agent_that_answers_carries_the_lens_and_migration_does_not():
    for agent in ("udr", "energy_intelligence", "wo_engine", "contract_performance", "doc_rag", "compliance"):
        assert LENS in (skills.agent_system_prompt(agent) or ""), agent
    assert LENS not in (skills.agent_system_prompt("migration") or "")


def test_the_lens_follows_the_shared_discipline_and_precedes_the_agents_own_skill():
    prompt = skills.agent_system_prompt("energy_intelligence")
    shared = skills.shared_skill().body
    own = skills.skill_for_agent("energy_intelligence").body
    assert prompt.index(shared) < prompt.index(LENS) < prompt.index(own)


def test_the_orchestrator_answers_with_the_lens_and_serves_all_four_markets():
    prompt = system_prompt.build_system_prompt()
    assert LENS in prompt
    assert "UK," in prompt and "Singapore" in prompt


def test_the_planners_writer_reads_the_lens():
    seen = {}

    async def llm(system, prompt, stage):
        seen["prompt"] = prompt
        return "answer"

    plan = {"steps": [{"id": "1", "target": "wo_engine", "ask": "open jobs", "depends_on": []}], "answer_shape": "table"}
    results = {"1": {"ok": True, "output": {"rows": []}, "error": None, "tool_calls": [], "ms": 1}}
    asyncio.run(planner.synthesise("what needs my attention?", plan, results, llm))
    assert LENS in seen["prompt"] and "What to do" in seen["prompt"]


def test_the_record_engine_orders_findings_the_same_way():
    assert "risk to life, then statutory exposure, then service to occupants, then money" in ANSWER_RULES


def test_a_missing_lens_file_leaves_answers_running(monkeypatch, tmp_path):
    monkeypatch.setenv("SKILLS_DIR", str(tmp_path))
    skills.fm_lens.cache_clear()
    skills.prompt_doc.cache_clear()
    try:
        assert skills.fm_lens() == ""
    finally:
        monkeypatch.delenv("SKILLS_DIR")
        skills.fm_lens.cache_clear()
        skills.prompt_doc.cache_clear()
