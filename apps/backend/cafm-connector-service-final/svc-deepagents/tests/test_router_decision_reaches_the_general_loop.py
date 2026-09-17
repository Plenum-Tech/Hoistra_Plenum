"""The router's decision reaches the agent that acts on it — for every agent, not three.

Measured 17 Sep 2026 with production routing (ORCHESTRATOR_LLM_ROUTING=1) over sixteen
questions from the five pages:

    compliance   3/3 → compliance
    vendor       3/3 → contract_performance
    energy       3/3 → energy_intelligence
    assets       0/3 → udr, udr, none
    maintenance  1/4 → compliance, contract_performance, udr, wo_engine

Two faults, both here. First, the router's catalogue is each skill's `description`, and the
energy description never said "asset", "condition" or "investigate"; the wo-engine description
never said "decision", "statutory", "PPM" or "inspection report". The tools existed and were
invisible to the thing that routes to them. With the descriptions extended, the same sixteen
questions routed 16/16.

Second, the orchestrator used the decision only to pick a phase-2 engine. A decision of
wo_engine or udr was computed, logged, and thrown away — the general agent started cold, and a
second classifier was then free to read "statutory" as compliance. "Which decisions are
statutory?" is printed on the Maintenance page and was answered as a certificate question.
"""
from pathlib import Path

import pytest

from src.agents.orchestrator import DeepAgentOrchestrator as O
from src.agents.skills import skill_for_agent

FRONTEND = Path(__file__).resolve().parents[4] / "frontend" / "src" / "logic" / "complianceLive.js"


# ── the catalogue names what the tools can do ──────────────────────────────────────────

class TestTheRouterCanSeeTheAssetAndMaintenanceTools:

    def test_energy_describes_asset_intelligence(self):
        d = skill_for_agent("energy_intelligence").description.lower()
        for word in ("asset", "investigat", "condition", "chiller", "hvac"):
            assert word in d, f"energy description never mentions {word!r}"

    def test_wo_engine_describes_the_maintenance_decisions_page(self):
        d = skill_for_agent("wo_engine").description.lower()
        for word in ("decision", "statutory", "ppm", "inspection", "warranty", "blocked"):
            assert word in d, f"wo-engine description never mentions {word!r}"

    def test_the_maintenance_chips_match_wo_engine_triggers_by_keyword_too(self):
        """The general agent's own select_skill routes by trigger, not by description. Both
        routers must agree, or the LLM sends it to wo_engine and the keyword table sends it
        back."""
        from src.agents.skills import route
        for q in ("which decisions are statutory?",
                  "which ppm contracts are behind plan?",
                  "which inspection recommendations were never converted to orders?",
                  "what needs my decision today?"):
            assert route(q)["primary_agent"] == "wo_engine", q

    def test_asset_investigation_matches_energy_triggers_by_keyword(self):
        from src.agents.skills import route
        for q in ("which assets need to be investigated?",
                  "which chillers are in the worst condition?"):
            assert route(q)["primary_agent"] == "energy_intelligence", q

    def test_a_plain_asset_count_still_falls_to_udr(self):
        """Extending energy's triggers must not make it swallow the register. "how many assets"
        is a UDR question; a bare `asset` trigger on energy would have taken it."""
        from src.agents.skills import route
        assert route("how many assets do we have and where are they?")["primary_agent"] == "udr"


# ── the decision is carried, not dropped ───────────────────────────────────────────────

class TestTheRoutingNote:

    def test_a_phase2_engine_needs_no_note_because_the_caller_acted_on_it(self):
        for agent in ("compliance", "contract_performance", "energy_intelligence"):
            assert O._routing_note({"agent": agent, "reason": "r"}) is None

    def test_no_decision_no_note(self):
        assert O._routing_note(None) is None
        assert O._routing_note({}) is None
        assert O._routing_note({"agent": None}) is None

    def test_an_unknown_agent_is_not_carried(self):
        """A model can name an agent that does not exist. Telling the general loop to task() it
        would fail on the first call."""
        assert O._routing_note({"agent": "billing", "reason": "invoices"}) is None

    @pytest.mark.parametrize("agent", ["wo_engine", "udr", "doc_rag", "migration"])
    def test_a_non_engine_agent_is_carried_with_the_instruction_to_delegate(self, agent):
        note = O._routing_note({"agent": agent, "reason": "because"})
        assert f"`{agent}`" in note
        assert f'task("{agent}"' in note
        assert "because" in note
        assert "select_skill" in note  # told not to re-route

    def test_also_agents_are_named_so_a_cross_domain_ask_fans_out(self):
        note = O._routing_note({"agent": "wo_engine", "also": ["compliance"], "reason": ""})
        assert "`compliance`" in note
        assert "same turn" in note

    def test_the_note_is_appended_to_context_not_in_place_of_it(self):
        assert O._with_routing_note("role: admin", "NOTE") == "role: admin\n\nNOTE"
        assert O._with_routing_note(None, "NOTE") == "NOTE"
        assert O._with_routing_note("role: admin", None) == "role: admin"
        assert O._with_routing_note(None, None) is None


# ── the run panel is drawn under the name the interface reads ──────────────────────────

class TestThePanelNameIsTheInterfacesContract:

    def test_every_engine_emits_its_panel_under_the_name_the_renderer_matches(self):
        assert O.PIPELINE_PANEL_TOOL == "compliance_pipeline"

    @pytest.mark.skipif(not FRONTEND.exists(), reason="frontend not checked out beside backend")
    def test_the_renderer_really_does_match_that_name_literally(self):
        """If someone renames the frontend match, this is the test that says the backend has to
        follow. `energy_pipeline` was emitted for weeks with nothing on the other end."""
        js = FRONTEND.read_text(encoding="utf-8")
        assert f'find("{O.PIPELINE_PANEL_TOOL}")' in js
