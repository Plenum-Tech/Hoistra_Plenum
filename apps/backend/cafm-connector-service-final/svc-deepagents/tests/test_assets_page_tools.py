"""The Assets page can be asked its own questions, and the agent can reach the answer.

Measured 17 Sep 2026. "Which assets have never been scored?" is a chip printed on the Assets
page. Asked in the chat, the router sent it to UDR as "a straightforward cross-table lookup";
the UDR sub-agent wrote a LEFT JOIN against a table that does not exist, got 0 rows, and
answered "cannot determine". An earlier attempt on the energy engine guessed asset codes
(AST-GEN-501, A-001 → 404) because the only asset tool took an id.

The page reads GET /api/energy/condition/{summary,assets,rules} for its bands and the register
for health_score. Now so does the agent.
"""
from src.agents.energy_intelligence_agent import ENERGY_INTELLIGENCE_TOOLS
from src.agents.skills import agent_system_prompt, skill_for_agent

TOOLS = {t.name: t for t in ENERGY_INTELLIGENCE_TOOLS}


class TestTheToolsExist:

    def test_the_four_assets_page_tools_are_on_the_energy_engine(self):
        assert {"get_asset_condition_summary", "list_asset_conditions",
                "get_asset_condition_rules", "list_unscored_assets"} <= set(TOOLS)

    def test_never_scored_is_a_register_read_not_a_guess(self):
        doc = " ".join(TOOLS["list_unscored_assets"].description.lower().split())
        assert "health_score" in doc and "never been scored" in doc
        assert "not evidence of health" in doc

    def test_none_unscored_still_says_what_is_there(self):
        """The chat answered "0 of 53 are unscored" and stopped. A person asked the same thing
        would add how the scores spread and which assets sit nearest the bottom — which is what
        the reader asks next. The tool now returns both, and the docstring says to use them."""
        doc = " ".join(TOOLS["list_unscored_assets"].description.lower().split())
        for key in ("score_bands", "score_range", "lowest_scored"):
            assert key in doc, key
        assert "seeded" in doc  # a register scored 78-100 throughout is that shape

    def test_the_band_tool_names_the_pages_chips(self):
        doc = TOOLS["list_asset_conditions"].description.lower()
        for band in ("threat", "watch", "in_control"):
            assert band in doc


class TestTheSkillTeachesThem:

    def test_the_assets_reference_doc_maps_each_question_to_a_tool(self):
        prompt = agent_system_prompt("energy_intelligence") or ""
        for name in ("get_asset_condition_summary", "list_asset_conditions",
                     "list_unscored_assets", "get_asset_condition_rules"):
            assert name in prompt, f"{name} is not taught anywhere the agent reads"

    def test_the_description_claims_the_assets_page(self):
        d = skill_for_agent("energy_intelligence").description.lower()
        assert "assets page" in d and "never been scored" in d
