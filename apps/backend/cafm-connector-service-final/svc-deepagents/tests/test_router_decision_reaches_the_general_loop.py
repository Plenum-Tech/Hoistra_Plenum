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

    @pytest.mark.parametrize("agent", ["udr", "doc_rag", "migration"])
    def test_a_non_engine_agent_is_carried_with_the_instruction_to_delegate(self, agent):
        note = O._routing_note({"agent": agent, "reason": "because"})
        assert f"`{agent}`" in note
        assert f'task("{agent}"' in note
        assert "because" in note
        assert "select_skill" in note  # told not to re-route

    def test_an_engine_is_carried_only_when_the_caller_asks_for_it(self):
        """wo_engine became an engine on 17 Sep. Its note is None by default — the caller
        short-circuits to it — and written only when _decide_dispatch sends the turn the long
        way (an action verb, or a second domain) and says so."""
        assert O._routing_note({"agent": "wo_engine", "reason": "r"}) is None
        note = O._routing_note({"agent": "wo_engine", "reason": "r"}, carry_engine=True)
        assert 'task("wo_engine"' in note

    def test_also_agents_are_named_so_a_cross_domain_ask_fans_out(self):
        note = O._routing_note({"agent": "udr", "also": ["compliance"], "reason": ""})
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


# ── where a turn runs: one engine, or the orchestrator fanning out ─────────────────────

class TestWhereATurnRuns:
    """Three things the user asked for on 17 Sep, in their words: a Maintenance-page question
    "should route to maintenance page related question" — directly, like compliance and
    energy do; an asset-condition question "should go and get the assets details and energy"
    both; and "the orchestrator provides the whole summary according to the user query"."""

    def test_a_maintenance_question_runs_on_the_maintenance_engine_directly(self):
        engine, note = O._decide_dispatch(
            {"agent": "wo_engine", "also": [], "reason": "r"}, "which ppm contracts are behind plan?")
        assert engine == "wo_engine" and note is None

    @pytest.mark.parametrize("q", [
        "raise a work order for the chiller at kingsway",
        "approve WO-4512",
        "close the boiler job",
        "assign meridian heating to the gas work",
    ])
    def test_a_maintenance_action_goes_the_long_way_so_the_intake_keeps_its_gates(self, q):
        engine, note = O._decide_dispatch({"agent": "wo_engine", "also": []}, q)
        assert engine is None
        assert 'task("wo_engine"' in note

    def test_awaiting_approval_is_a_question_not_an_approval(self):
        assert O._maintenance_read_question("which work orders are awaiting approval?")
        assert not O._maintenance_read_question("approve the pending work orders")

    def test_a_two_domain_question_fans_out_through_the_orchestrator(self):
        engine, note = O._decide_dispatch(
            {"agent": "energy_intelligence", "also": ["wo_engine"], "reason": "condition"},
            "which assets are in the worst condition?")
        assert engine is None
        assert 'task("energy_intelligence"' in note and "`wo_engine`" in note
        assert "same turn" in note

    def test_compliance_keeps_its_pipeline_even_when_a_second_domain_is_named(self):
        engine, note = O._decide_dispatch(
            {"agent": "compliance", "also": ["wo_engine"]},
            "which buildings have lapsed certificates and open urgent work orders?")
        assert engine == "compliance" and note is None

    def test_a_single_domain_engine_question_still_short_circuits(self):
        engine, note = O._decide_dispatch({"agent": "energy_intelligence", "also": []}, "eui by building")
        assert engine == "energy_intelligence" and note is None

    def test_no_decision_means_the_general_loop_with_nothing_carried(self):
        assert O._decide_dispatch({"agent": None}, "hello") == (None, None)


class TestTheMaintenanceEngineHoldsNoWriteTool:

    def test_the_engine_tool_list_is_read_only(self):
        from src.agents.orchestrator import PHASE2_ENGINE_TOOLS
        names = {t.name for t in PHASE2_ENGINE_TOOLS["wo_engine"]}
        for forbidden in ("create_work_order", "approve_work_order", "close_work_order",
                          "transition_work_order", "update_work_order",
                          "prepare_intelligent_work_order", "confirm_intelligent_work_order_creation",
                          "respond_to_approval_step", "send_approval_request_email"):
            assert forbidden not in names, forbidden

    def test_the_engine_holds_the_four_maintenance_page_tools(self):
        from src.agents.orchestrator import PHASE2_ENGINE_TOOLS
        names = {t.name for t in PHASE2_ENGINE_TOOLS["wo_engine"]}
        assert {"list_maintenance_decisions", "get_inspection_intelligence",
                "get_ppm_contracts", "get_maintenance_overview"} <= names

    def test_the_general_loops_sub_agent_holds_them_too(self):
        """This is the one that was broken: the runner listed its tools by hand and the four
        were never added, so routing to wo_engine reached an agent that could not answer."""
        from src.agents.wo_engine_agent import WO_ENGINE_SUBAGENT_TOOLS
        names = {t.name for t in WO_ENGINE_SUBAGENT_TOOLS}
        assert {"list_maintenance_decisions", "get_inspection_intelligence",
                "get_ppm_contracts", "get_maintenance_overview", "create_work_order"} <= names

    def test_wo_engine_is_an_engine_the_router_can_short_circuit_to(self):
        from src.agents.agent_router import as_phase2_engine
        assert as_phase2_engine("wo_engine") == "wo_engine"


class TestTheOrchestratorsOwnPanel:

    def test_a_fanned_out_turn_names_the_agents_it_delegated_to(self):
        calls = [
            {"tool": "task", "input": {"agent": "energy_intelligence", "prompt": "…"}, "output": "a"},
            {"tool": "task", "input": {"agent": "wo_engine", "prompt": "…"}, "output": "b"},
        ]
        from src.agents import llm_cost
        llm_cost.begin_turn("t-panel")
        panel = O._general_loop_panel("which assets are in the worst condition?", calls)
        assert panel is not None and panel["tool"] == O.PIPELINE_PANEL_TOOL
        agents = next(s for s in panel["output"]["steps"] if s["stage"] == "agents")
        assert "energy_intelligence" in agents["label"] and "wo_engine" in agents["label"]
        assert panel["output"]["engine"] == "orchestrator"
