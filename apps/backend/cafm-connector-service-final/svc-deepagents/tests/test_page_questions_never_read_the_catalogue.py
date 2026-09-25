"""A page question is answered from the page's API, never from the database catalogue.

The catalogue (find_tables / table_card / udr_*) exists so a question no page owns — spare
parts, purchase orders, who may see which building — gets the right table. Compliance, vendor,
energy, asset and maintenance questions must keep reading the page APIs, so the chat agrees
with the screen. Three layers make that so, and each is pinned here:

1. the four page engines' tool lists hold no UDR tool, so a direct dispatch cannot reach it;
2. a turn the router gave to page engines but that runs in the general loop (fan-out, or an
   action verb) claims the turn: `turn_page_engines` is set, and every svc-udr call refuses;
3. the routing note tells the general loop the same thing in words.

A decision that names only `udr` leaves the catalogue open — "spare parts below reorder level
and which assets' repairs need them" is one cross-table question, not a page question. A
decision that names page engines AND `udr` (assets to investigate, plus parts to reorder) opens
the catalogue inside task("udr") only: the general loop may not re-count a page engine's figures
from tables. Measured 17 Sep 2026 at 15:40, with the catalogue simply open on such a turn, it ran
four SELECTs of its own, counted 6 where the Assets page engine says 17, and the judge withheld a
correct answer over the conflict. Over 26 questions: 0 page questions reached `udr`, 6/6 general did.
"""
import pytest

import src.http_client as hc
from src.agents.orchestrator import PHASE2_ENGINE_TOOLS, DeepAgentOrchestrator as O
from src.agents.udr_agent import get_schema
from src.agents.wo_engine_agent import WO_ENGINE_SUBAGENT_TOOLS

UDR_TOOLS = {"find_tables", "table_card", "get_schema", "query_table", "udr_list_tables", "udr_describe_table",
             "udr_read_records", "udr_get_record", "udr_search_records", "udr_execute_select",
             "udr_create_record", "udr_update_record", "udr_delete_record"}


@pytest.fixture(autouse=True)
def _open_catalogue():
    token = hc.turn_page_engines.set(frozenset())
    via = hc.turn_catalogue_via_task.set(False)
    yield
    hc.turn_catalogue_via_task.reset(via)
    hc.turn_page_engines.reset(token)


class TestTheEnginesCannotReachTheCatalogue:

    @pytest.mark.parametrize("engine", sorted(PHASE2_ENGINE_TOOLS))
    def test_a_direct_dispatch_holds_no_udr_tool(self, engine):
        names = {t.name for t in PHASE2_ENGINE_TOOLS[engine]}
        assert not (names & UDR_TOOLS), f"{engine} can reach {sorted(names & UDR_TOOLS)}"

    def test_the_maintenance_sub_agent_holds_none_either(self):
        assert not ({t.name for t in WO_ENGINE_SUBAGENT_TOOLS} & UDR_TOOLS)


class TestClaimingTheTurn:

    def test_an_action_verb_turn_for_wo_engine_closes_the_catalogue(self):
        assert O._claim_turn_for_page_engines({"agent": "wo_engine"}) == {"wo_engine"}
        assert hc.turn_page_engines.get() == {"wo_engine"}

    def test_a_fanned_out_condition_question_names_both_engines(self):
        got = O._claim_turn_for_page_engines({"agent": "energy_intelligence", "also": ["wo_engine"]})
        assert got == {"energy_intelligence", "wo_engine"}

    def test_a_udr_question_leaves_it_open(self):
        assert O._claim_turn_for_page_engines({"agent": "udr"}) == frozenset()
        assert hc.turn_page_engines.get() == frozenset()
        assert hc.catalogue_closed() is None

    def test_a_page_question_with_a_database_half_opens_it_inside_task_udr_only(self):
        """The router said the database half is real — so task("udr") may read; the general
        loop, which would otherwise re-count the page engine's figures from tables, may not."""
        assert O._claim_turn_for_page_engines({"agent": "wo_engine", "also": ["udr"]}) == {"wo_engine"}
        assert hc.turn_catalogue_via_task.get() is True
        assert hc.catalogue_closed() == {"wo_engine"}, "the general loop is closed"
        token = hc.active_subagent.set("udr")
        try:
            assert hc.catalogue_closed() is None, "task(\"udr\") is open"
        finally:
            hc.active_subagent.reset(token)
        token = hc.active_subagent.set("energy_intelligence")
        try:
            assert hc.catalogue_closed() == {"wo_engine"}, "another sub-agent is not"
        finally:
            hc.active_subagent.reset(token)

    def test_task_udr_on_a_turn_with_no_database_half_is_still_closed(self):
        O._claim_turn_for_page_engines({"agent": "wo_engine"})
        token = hc.active_subagent.set("udr")
        try:
            assert hc.catalogue_closed() == {"wo_engine"}
        finally:
            hc.active_subagent.reset(token)

    def test_agents_that_are_not_page_engines_claim_nothing(self):
        assert O._claim_turn_for_page_engines({"agent": "doc_rag"}) == frozenset()
        assert O._claim_turn_for_page_engines(None) == frozenset()

    def test_a_new_turn_replaces_the_last_claim(self):
        O._claim_turn_for_page_engines({"agent": "compliance"})
        O._claim_turn_for_page_engines({"agent": "udr"})
        assert hc.turn_page_engines.get() == frozenset()


class TestTheDoorRefuses:

    @pytest.fixture
    def stub(self, monkeypatch):
        seen = []

        class _Resp:
            def raise_for_status(self): return None
            def json(self): return {}

        class _Client:
            def __init__(self, *a, **k): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def request(self, method, path, **kw):
                seen.append(path); return _Resp()

        monkeypatch.setattr(hc.httpx, "AsyncClient", _Client)
        return seen

    @pytest.mark.asyncio
    async def test_a_udr_read_on_a_claimed_turn_never_leaves_the_process(self, stub):
        hc.turn_page_engines.set(frozenset({"wo_engine"}))
        with pytest.raises(hc.PageEngineOwnsThisTurn) as e:
            await hc.request("GET", "http://udr", "/api/catalog/search", service="udr", params={"q": "x"})
        assert "wo_engine" in str(e.value) and 'task("<engine>")' in str(e.value)
        assert stub == []

    @pytest.mark.asyncio
    async def test_the_page_apis_stay_open_on_the_same_turn(self, stub):
        hc.turn_page_engines.set(frozenset({"wo_engine"}))
        await hc.request("GET", "http://wo", "/api/maintenance/decisions", service="wo_management")
        assert stub == ["/api/maintenance/decisions"]

    @pytest.mark.asyncio
    async def test_an_open_turn_reads_the_catalogue(self, stub):
        await hc.request("GET", "http://udr", "/api/catalog/search", service="udr", params={"q": "x"})
        assert stub == ["/api/catalog/search"]

    @pytest.mark.asyncio
    async def test_get_schema_refuses_without_touching_the_database(self):
        """The one UDR tool that reads the database itself, not through svc-udr."""
        hc.turn_page_engines.set(frozenset({"energy_intelligence", "wo_engine"}))
        out = await get_schema.ainvoke({})
        assert "error" in out and "energy_intelligence, wo_engine" in str(out["error"])


class TestTheNoteSaysSo:

    def test_a_carried_page_engine_is_told_the_catalogue_is_closed(self):
        note = O._routing_note({"agent": "wo_engine", "reason": "raise a work order"}, carry_engine=True)
        assert "source of record" in note and "find_tables" in note and "will refuse" in note

    def test_a_udr_decision_is_not(self):
        note = O._routing_note({"agent": "udr", "reason": "cross-table"})
        assert note and "source of record" not in note

    def test_a_page_question_with_a_udr_half_is_told_the_database_half_runs_in_task_udr(self):
        note = O._routing_note({"agent": "wo_engine", "also": ["udr"]}, carry_engine=True)
        assert 'task("udr") ONLY' in note and "page engine's figure stands" in note and "`udr`" in note
