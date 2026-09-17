"""The orchestrator can ask which table is for what, before it names one.

svc-udr now keeps a catalogue — one row per plenum_cafm table with a written purpose, the
questions it answers, keys, links (declared and by-name) and sample values, embedded for
search by meaning. The UDR agent gets two tools for it and both skills tell it to use them
before get_schema, which returns 221 names and nothing about meaning.
"""
from src.agents.orchestrator import ALL_TOOLS, _TOOL_DOMAIN
from src.agents.skills import agent_system_prompt, prompt_doc

NAMES = {t.name for t in ALL_TOOLS}


class TestTheToolsExist:

    def test_find_tables_and_table_card_are_orchestrator_tools(self):
        assert {"find_tables", "table_card"} <= NAMES

    def test_they_belong_to_the_udr_domain(self):
        assert _TOOL_DOMAIN["find_tables"] == "udr" and _TOOL_DOMAIN["table_card"] == "udr"

    def test_find_tables_says_to_pass_the_question_as_asked(self):
        doc = " ".join(next(t for t in ALL_TOOLS if t.name == "find_tables").description.lower().split())
        assert "as asked" in doc and "before naming a table" in doc


class TestTheSkillsTeachThem:

    def test_the_udr_skill_puts_the_catalogue_before_get_schema(self):
        prompt = agent_system_prompt("udr") or ""
        assert "find_tables" in prompt and "table_card" in prompt
        assert prompt.index("find_tables(question)") < prompt.index("`get_schema()` | Every table")

    def test_the_shared_query_discipline_says_so_for_every_agent(self):
        shared = prompt_doc("query-builder", "SKILL") or agent_system_prompt("udr") or ""
        assert "find_tables(question)" in shared

    def test_by_name_links_are_called_out_as_unenforced(self):
        prompt = agent_system_prompt("udr") or ""
        assert "naming convention" in prompt and "230" in prompt
