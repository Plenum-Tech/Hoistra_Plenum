"""Every engine shows which agent answered, what it did, and what it cost.

The panel existed only for compliance: the whole block sat behind `engine == "compliance"`, so
an energy turn measured itself exactly as carefully — the ledger starts in the shared
_invoke_phase2_engine, above the branch — and then displayed none of it. An energy answer showed
a bare tool list and no routing, no timings and no cost.
"""
from src.agents import llm_cost
from src.agents.orchestrator import DeepAgentOrchestrator as O


def energy_turn():
    llm_cost.begin_turn("test-session")
    llm_cost.record("agent_router", "claude-sonnet-5",
                    {"input_tokens": 900, "output_tokens": 40}, 2500.0,
                    agent="energy_intelligence", reason="Asks about consumption anomalies.")
    llm_cost.record("sub_agent", "gpt-5.6-terra",
                    {"input_tokens": 6000, "output_tokens": 500}, 5300.0, cache_hit=True)
    return [
        {"tool": "list_energy_anomalies", "output": {"anomalies": [{}] * 9}},
        {"tool": "summarise_anomalies", "output": {"groups": [{}] * 8}},
        {"tool": "list_energy_buildings", "output": {"buildings": [{}] * 10}},
        {"tool": "phase2_engine:energy_intelligence", "output": {}},
    ]


class TestTheRoutingStep:

    def test_it_names_the_agent_that_answered(self):
        steps = O._early_pipeline_steps(energy_turn())
        route = next(s for s in steps if s["stage"] == "route")
        assert "energy_intelligence" in route["label"]

    def test_it_does_not_say_compliance_on_an_energy_turn(self):
        """The fallback label was the literal string "compliance"."""
        steps = O._early_pipeline_steps(energy_turn())
        assert "compliance" not in next(s for s in steps if s["stage"] == "route")["label"]

    def test_it_carries_the_cost_and_the_model(self):
        route = next(s for s in O._early_pipeline_steps(energy_turn()) if s["stage"] == "route")
        assert route["usd"] > 0 and route["model"] == "claude-sonnet-5"
        assert route["ms"] == 2500.0

    def test_it_says_why_it_routed_there(self):
        route = next(s for s in O._early_pipeline_steps(energy_turn()) if s["stage"] == "route")
        assert "consumption anomalies" in route["detail"]


class TestTheFetchStep:

    def test_it_counts_rows_energy_actually_returns(self):
        """The keys were compliance-only — certificates, buildings, vendors, types — so an
        energy turn that fetched nine anomalies and eight groups reported 0 rows."""
        data = next(s for s in O._early_pipeline_steps(energy_turn()) if s["stage"] == "data")
        assert "27 rows" in data["label"]          # 9 anomalies + 8 groups + 10 buildings

    def test_it_lists_the_tools_the_agent_used(self):
        data = next(s for s in O._early_pipeline_steps(energy_turn()) if s["stage"] == "data")
        for tool in ("list_energy_anomalies", "summarise_anomalies", "list_energy_buildings"):
            assert tool in data["detail"]

    def test_the_engine_wrapper_is_not_listed_as_a_tool(self):
        """phase2_engine:… is the engine call itself, not something it fetched."""
        data = next(s for s in O._early_pipeline_steps(energy_turn()) if s["stage"] == "data")
        assert "phase2_engine" not in data["detail"]
        assert "3 tools" in data["label"]

    def test_it_carries_the_cache_hit(self):
        data = next(s for s in O._early_pipeline_steps(energy_turn()) if s["stage"] == "data")
        assert data.get("cache_hit") is True


class TestWhatIsDeliberatelyAbsent:
    """Only the steps that really happened. The analyst, the grounding check and the reviewer
    are compliance stages; labelling work that did not occur would be a lie with a cost figure
    attached."""

    def test_energy_gets_two_steps_not_six(self):
        steps = O._early_pipeline_steps(energy_turn())
        assert {s["stage"] for s in steps} == {"route", "data"}

    def test_there_is_no_document_step_without_a_doc_router(self):
        """Energy loads its references statically and has no doc router to record."""
        assert not any(s["stage"] == "docs" for s in O._early_pipeline_steps(energy_turn()))


class TestTheCostSummary:

    def test_it_breaks_down_by_role(self):
        tool_calls = energy_turn()
        O._early_pipeline_steps(tool_calls)
        summary = llm_cost.current().log_summary("q")
        assert summary["by_role"]["agent_router"] > 0
        assert summary["by_role"]["sub_agent"] > 0
        assert summary["calls"] == 2


class TestAnAssetInvestigationIsCountedToo:
    """investigate_asset returns what it WALKED (sources) and what it FOUND (evidence), not a
    row list. Without those keys the panel said "1 tool, 0 rows" about a call that examined six
    sources and produced two pieces of evidence — the same shape of miss as the compliance-only
    keys, one tool shape later."""

    @staticmethod
    def asset_turn():
        llm_cost.begin_turn("asset-test")
        llm_cost.record("agent_router", "claude-sonnet-5",
                        {"input_tokens": 900, "output_tokens": 40}, 2400.0,
                        agent="energy_intelligence", reason="Asks why one asset costs what it does.")
        llm_cost.record("sub_agent", "gpt-5.6-terra",
                        {"input_tokens": 7000, "output_tokens": 600}, 6100.0, cache_hit=True)
        return [
            {"tool": "investigate_asset",
             "output": {"sources": [{}] * 6, "evidence": [{}] * 2, "actions": [{}] * 1}},
            {"tool": "get_asset_intelligence", "output": {"readings": [{}] * 4}},
            {"tool": "phase2_engine:energy_intelligence", "output": {}},
        ]

    def test_sources_and_evidence_are_counted(self):
        data = next(s for s in O._early_pipeline_steps(self.asset_turn()) if s["stage"] == "data")
        assert "12 rows" in data["label"]        # 6 sources + 2 evidence + 4 readings

    def test_actions_are_reported_but_not_as_rows(self):
        """Actions are shown — a person wants to know how many decisions are waiting — but in
        their own clause. An action is something the tool PROPOSES (expedite this order, claim
        this credit), not a record it fetched, so folding it into the row count would mean
        "16 rows" included four things nobody retrieved."""
        from src.agents.orchestrator import DeepAgentOrchestrator
        assert "actions" not in DeepAgentOrchestrator._ROW_KEYS
        data = next(s for s in O._early_pipeline_steps(self.asset_turn()) if s["stage"] == "data")
        assert "12 rows" in data["label"]
        assert "1 action ready" in data["label"]
        assert data["actions_ready"] == 1

    def test_a_turn_with_no_actions_says_nothing_about_them(self):
        """No empty clause. "0 actions ready" on every energy answer is noise."""
        llm_cost.begin_turn("no-actions")
        llm_cost.record("sub_agent", "gpt-5.6-terra",
                        {"input_tokens": 100, "output_tokens": 10}, 1000.0)
        steps = O._early_pipeline_steps(
            [{"tool": "list_energy_anomalies", "output": {"anomalies": [{}] * 9}}])
        data = next(s for s in steps if s["stage"] == "data")
        assert "action" not in data["label"]
        assert data["actions_ready"] == 0

    def test_an_asset_question_still_names_the_engine_that_took_it(self):
        route = next(s for s in O._early_pipeline_steps(self.asset_turn()) if s["stage"] == "route")
        assert "energy_intelligence" in route["label"]
