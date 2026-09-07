"""Which agent a question routes to, and what each agent is told once it gets there.

The orchestrator picks a skill from the user's own words, routes to that agent, and writes the
summary itself. Two things have to hold for that to work: the routing has to be deterministic
(the same question cannot land on Compliance one turn and the WO dashboard the next), and a
question that spans two domains has to surface BOTH — answering half of it silently is worse
than refusing it.
"""
from __future__ import annotations

import pytest

from src.agents.skills import (
    FALLBACK_AGENT,
    agent_system_prompt,
    load_skills,
    reload_skills,
    route,
    routable_skills,
    select_skills,
    skill_for_agent,
    skill_index_markdown,
)

#: Every agent registered in meta_tools must own exactly one skill.
AGENTS = {
    "migration",
    "doc_rag",
    "wo_engine",
    "compliance",
    "contract_performance",
    "energy_intelligence",
    "udr",
}


class TestRegistry:
    def test_every_agent_owns_a_skill(self):
        assert {s.agent for s in routable_skills()} == AGENTS

    def test_the_shared_skill_is_never_a_route_target(self):
        # query-builder is prepended to every agent; it is not a destination.
        shared = [s for s in load_skills() if s.shared]
        assert len(shared) == 1
        assert shared[0].agent not in AGENTS

    def test_every_skill_carries_triggers_and_a_description(self):
        for skill in routable_skills():
            assert skill.triggers, f"{skill.slug} has no triggers — it can never be routed to"
            assert skill.description, f"{skill.slug} has no description"
            assert len(skill.body) > 500, f"{skill.slug} body looks truncated"

    def test_the_index_lists_every_routable_skill(self):
        index = skill_index_markdown()
        for skill in routable_skills():
            assert skill.agent in index

    def test_reload_picks_up_the_same_set(self):
        before = {s.slug for s in load_skills()}
        assert {s.slug for s in reload_skills()} == before


@pytest.mark.parametrize(
    "question,expected",
    [
        ("which building certificates are lapsed at Tower A?", "compliance"),
        ("is AIB Solutions accreditation still valid", "compliance"),
        ("what is the status of WO-2024-031", "wo_engine"),
        ("the chiller in block C is leaking, can someone look at it", "wo_engine"),
        ("which PPM schedules are overdue", "wo_engine"),
        ("how is Gough and Kelly performing against their SLA", "contract_performance"),
        ("check this invoice for overcharging", "contract_performance"),
        ("what is our EUI against the TM46 benchmark", "energy_intelligence"),
        ("show me the energy anomalies for last month", "energy_intelligence"),
        ("what does the O&M manual say about belt torque", "doc_rag"),
        ("import this Maximo CSV export and map the fields", "migration"),
        ("connect Fiix and sync the assets", "migration"),
        ("show me all spare parts below reorder level", "udr"),
        ("how many assets do we have per site", "udr"),
    ],
)
def test_questions_route_to_their_owner(question, expected):
    assert route(question)["primary_agent"] == expected


class TestCrossDomain:
    """A question spanning two domains must name both, so the orchestrator fans out."""

    def test_certificates_and_work_orders_names_both(self):
        decision = route(
            "which buildings have lapsed certificates and open urgent work orders?"
        )
        assert decision["primary_agent"] == "compliance"
        assert "wo_engine" in [a["agent"] for a in decision["also_relevant"]]

    def test_scorecards_and_blocked_vendors_names_both(self):
        agents = {
            decision["primary_agent"],
            *[a["agent"] for a in decision["also_relevant"]],
        } if (decision := route(
            "compare vendor scorecards against their blocked accreditations"
        )) else set()
        assert {"compliance", "contract_performance"} <= agents

    def test_next_step_lists_every_agent_to_spawn(self):
        decision = route("lapsed certificates and open work orders")
        agents = [decision["primary_agent"]] + [
            a["agent"] for a in decision["also_relevant"]
        ]
        assert len(set(agents)) == len(agents), "an agent must not be spawned twice"


class TestMatchingRules:
    def test_plural_still_matches_a_singular_trigger(self):
        # "open work orders" must hit the "work order" trigger.
        assert route("list the open work orders")["primary_agent"] == "wo_engine"

    def test_a_longer_phrase_outweighs_a_generic_word(self):
        # "due" alone is a WO word; "due for renewal" is a compliance phrase.
        assert route("certificates due for renewal")["primary_agent"] == "compliance"

    def test_a_generic_opener_does_not_steal_a_specialist_question(self):
        # "show me" belongs to UDR, but the question is about certificates.
        assert route("show me the lapsed certificates")["primary_agent"] == "compliance"

    def test_whole_words_only(self):
        # "gas" must not fire on "gasket", and the fallback is fine here.
        assert route("replace the gasket on pump 3")["primary_agent"] != "energy_intelligence"

    def test_gas_safety_is_compliance_not_energy(self):
        assert route("gas safety certificates expiring this month")["primary_agent"] == (
            "compliance"
        )

    def test_gas_consumption_is_energy_not_compliance(self):
        assert route("gas consumption against benchmark")["primary_agent"] == (
            "energy_intelligence"
        )

    def test_routing_is_deterministic(self):
        question = "which vendors are blocked and what are their scorecards"
        assert [route(question) for _ in range(5)].count(route(question)) == 5


class TestFallback:
    def test_an_unmatched_question_falls_back_and_asks_first(self):
        decision = route("hello, can you help me")
        assert decision["primary_agent"] == FALLBACK_AGENT
        assert decision["confidence"] == "fallback"
        assert decision["clarify_first"] is True

    def test_a_matched_question_never_asks_first(self):
        assert route("list open work orders")["clarify_first"] is False

    def test_empty_input_does_not_raise(self):
        assert route("")["primary_agent"] == FALLBACK_AGENT

    def test_udr_never_outranks_a_specialist_that_scored(self):
        # Both score; UDR is the fallback tier, so the specialist leads.
        matches = select_skills("show me every lapsed certificate in the database")
        assert matches[0].skill.agent == "compliance"


class TestAgentPrompts:
    def test_each_agent_gets_the_shared_discipline_plus_its_own_skill(self):
        for agent in AGENTS:
            prompt = agent_system_prompt(agent)
            assert prompt, f"{agent} has no system prompt"
            assert "RESOLVE" in prompt, f"{agent} is missing the shared query loop"
            assert skill_for_agent(agent).body[:200] in prompt

    def test_an_existing_contract_is_kept_and_ordered_before_the_skill(self):
        # Compliance already had a tuned tool-routing contract; the skill adds the data
        # layer beneath it and must not displace it.
        prompt = agent_system_prompt("compliance", extra="EXISTING CONTRACT TEXT")
        assert "EXISTING CONTRACT TEXT" in prompt
        assert prompt.index("EXISTING CONTRACT TEXT") < prompt.index(
            skill_for_agent("compliance").body[:80]
        )

    def test_an_unknown_agent_gets_the_shared_skill_not_a_crash(self):
        prompt = agent_system_prompt("no_such_agent")
        assert prompt and "RESOLVE" in prompt

    def test_every_engine_prompt_carries_its_no_work_order_rule(self):
        # A/B/C engines are approvals and dashboards only — the rule has to reach the agent.
        for agent in ("compliance", "contract_performance", "energy_intelligence"):
            assert "work order" in agent_system_prompt(agent).lower()


class TestSchemaGuardrails:
    """The live schema does not match the ORM. Every skill has to say so."""

    def test_the_shared_skill_warns_about_live_schema_drift(self):
        shared = next(s for s in load_skills() if s.shared)
        assert "get_schema()" in shared.body
        assert "varchar(50)" in shared.body

    def test_the_shared_skill_carries_the_join_key_map(self):
        shared = next(s for s in load_skills() if s.shared)
        for key in ("asset_id", "vendor_id", "work_order_id", "meter_id", "document_id"):
            assert key in shared.body

    def test_the_site_key_trap_is_documented_where_it_bites(self):
        # site_id is UUID in the Phase 2 tables but varchar in the live sites table — the
        # reason per-site compliance coverage once collapsed into one bucket.
        for agent in ("compliance", "energy_intelligence"):
            assert "site_ref" in agent_system_prompt(agent) or "varchar" in (
                agent_system_prompt(agent)
            )
