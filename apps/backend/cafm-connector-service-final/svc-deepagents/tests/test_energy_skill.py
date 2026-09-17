"""The energy skill must describe the tools that exist, and the traps that are real.

The Energy page renders four markets, a per-building list benchmarked against each building's
own pack, and an anomaly detail view. The agent had no tool for market-profiles, the buildings
list, cost-drivers, the anomaly rollup, statutory duties or tariffs — so it could not answer the
questions printed as chips on that very page while a person was looking at the answer.

A skill that names a tool which does not exist is worse than no skill: the model plans a call
that cannot be made and reports an outage. That is what the first test guards.
"""
import re
from pathlib import Path

import pytest

SKILL = (Path(__file__).resolve().parents[1]
         / "skills" / "energy-intelligence" / "SKILL.md").read_text(encoding="utf-8")


def tool_names() -> set[str]:
    from src.agents.energy_intelligence_agent import ENERGY_INTELLIGENCE_TOOLS
    return {t.name for t in ENERGY_INTELLIGENCE_TOOLS}


class TestTheSkillOnlyNamesRealTools:

    def test_every_backticked_call_resolves_to_a_tool(self):
        """Names in backticks that look like calls must be tools the agent actually has."""
        mentioned = set(re.findall(r"`([a-z][a-z0-9_]{4,})\(", SKILL))
        known = tool_names()
        # Named as cross-domain hops. Each is asserted to be a REAL tool on its own agent
        # below, so widening this set cannot be used to wave through a typo.
        cross_domain = {"find_location", "find_asset",
                        "udr_read_records", "udr_execute_select"}
        missing = mentioned - known - cross_domain
        assert not missing, f"skill names tools that do not exist: {sorted(missing)}"

    @pytest.mark.parametrize("name", [
        "get_market_profiles", "list_energy_buildings", "get_building_cost_drivers",
        "get_anomaly_rollup", "get_statutory_duties", "get_energy_tariffs",
    ])
    def test_the_new_tools_are_registered(self, name):
        assert name in tool_names()

    def test_no_tool_is_registered_twice(self):
        from src.agents.energy_intelligence_agent import ENERGY_INTELLIGENCE_TOOLS
        names = [t.name for t in ENERGY_INTELLIGENCE_TOOLS]
        assert len(names) == len(set(names))


class TestTheThreeQuestionShapes:
    """Country, building, anomaly — each has to route somewhere different."""

    def test_a_country_question_routes_to_market_profiles(self):
        assert "get_market_profiles" in SKILL

    def test_a_building_question_routes_to_the_buildings_list(self):
        assert "list_energy_buildings" in SKILL
        assert "get_building_cost_drivers" in SKILL

    def test_an_anomaly_total_routes_to_the_rollup(self):
        assert "get_anomaly_rollup" in SKILL


class TestTheTrapsAreWrittenDown:

    def test_it_forbids_adding_across_markets(self):
        low = SKILL.lower()
        assert "never add figures across markets" in low or "not be added together" in low

    def test_it_carries_the_reference_with_every_eui(self):
        """214 against a reference of 215 is AT reference; 198 against 172 is 15% over. Raw
        EUIs side by side reverse the finding — the worked example is in the skill for that."""
        assert "ref 172" in SKILL and "ref 215" in SKILL

    def test_it_forbids_summing_the_detectors(self):
        assert "never a sum" in SKILL.lower()

    def test_it_requires_the_basis_of_a_statutory_tile(self):
        low = SKILL.lower()
        assert "certificate" in low and "inferred" in low

    def test_it_says_building_level_metering_infers_rather_than_attributes(self):
        low = SKILL.lower()
        assert "inferred" in low and "building-level" in low

    def test_it_still_forbids_creating_work_orders(self):
        assert "never create or dispatch a work order" in SKILL.lower()


class TestFrontMatter:

    def test_it_declares_the_agent_it_belongs_to(self):
        assert "agent: energy_intelligence" in SKILL

    def test_the_new_question_shapes_are_triggerable(self):
        for trigger in ("market profile", "which market", "mees", "baseline drift"):
            assert trigger in SKILL.lower(), trigger


class TestCrossDomainToolsAreRealToo:
    """The allow-list above exempts tools belonging to OTHER agents. That exemption is only
    safe while each one actually exists — otherwise the list becomes a place to hide a typo,
    and the skill sends the model after a tool nobody has."""

    def test_the_udr_tools_the_skill_points_at_exist(self):
        from src.agents.udr_agent import udr_execute_select, udr_read_records
        assert udr_read_records.name == "udr_read_records"
        assert udr_execute_select.name == "udr_execute_select"

    def test_the_skill_says_they_are_already_scoped(self):
        """A reader who thinks UDR is a way around the company filter will use it to "check"
        a figure and get the same rows, or reach for it believing it sees more."""
        low = SKILL.lower()
        assert "scoped to the caller" in low


class TestCompoundQuestionsAreChained:
    """Both observed failures answered after ONE tool call. The recursion limit is 60, so
    nothing was cutting the agent off — the skill read as a routing table, one question to one
    tool, and that is what it did. "Why is this building over reference and how much can I act
    on" is six reads, and answering it from the building list alone is thin, confidently."""

    def test_the_skill_says_a_compound_question_takes_several_calls(self):
        assert "A compound question takes several calls" in SKILL

    def test_it_forbids_stopping_at_the_first_result(self):
        assert "Do not stop at the first tool that returns something" in SKILL

    def test_the_worked_chain_names_every_step(self):
        """Six reads. If one is dropped from the skill the chain silently shortens."""
        for call in ("list_energy_buildings", "list_energy_anomalies",
                     "get_operating_hours", "udr_read_records"):
            assert call in SKILL, call

    def test_an_empty_read_is_evidence_not_a_dead_end(self):
        """assumed.known false, provenance.measured false, an unmetered asset — each is a fact
        to report, not a step to omit quietly."""
        assert "evidence, not a dead end" in SKILL

    def test_the_decomposition_is_stated(self):
        low = SKILL.lower()
        assert "actionable now" in low and "structural" in low


class TestTheConclusionMustFollowTheEvidence:
    """A worked answer that shows its reads and then contradicts them is worse than a short
    one: it looks audited. Observed in a proposed transcript — "occupancy: as assumed" in the
    evidence, "the building keeps longer hours than its pack assumes" in the conclusion, and
    "re-benchmark with actual hours" offered as an action on the strength of it."""

    def test_it_forbids_contradicting_the_evidence(self):
        assert "must not contradict the evidence rows" in SKILL

    def test_it_forbids_an_action_with_nothing_in_it(self):
        assert "Do not propose an action with nothing in it" in SKILL


class TestTheAssetInvestigation:
    """A compound question about ONE asset is one call. The engine already walked six sources
    and reported the empty ones; deep-agents simply had no tool for it."""

    def test_the_tools_are_registered(self):
        assert "investigate_asset" in tool_names()
        assert "get_asset_intelligence" in tool_names()

    def test_the_skill_requires_reporting_the_missing_sources(self):
        assert "sources_missing" in SKILL
        assert "finding, not a gap in the search" in SKILL

    def test_it_forbids_narrating_telemetry_that_does_not_exist(self):
        """chiller_performance_readings and bms_trends carry asset links that are not in the
        register, so a COP trend is invention however plausible it reads."""
        low = SKILL.lower()
        assert "cop fell" in low and "not available for any asset today" in low

    def test_it_says_a_missing_record_is_a_record_finding(self):
        assert "not as a plant diagnosis" in SKILL

    def test_the_failure_assessment_is_not_sold_as_a_model(self):
        assert "not a fitted model" in SKILL
