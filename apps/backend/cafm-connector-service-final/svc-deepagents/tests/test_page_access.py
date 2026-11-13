"""The chat answers only from the pages the caller holds.

An administrator may give a user an access role and tick the pages they may open. The pages
come from the identity service with the caller — never from the browser — and decide which
engines a question may be routed to and which tools the model is handed. A caller with every
page is not affected at all.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.agents import agent_router, page_access as PA, planner, spaces

ALL_PAGES = frozenset({"buildings", "compliance", "energy", "vendors", "ops", "assets"})
COMPLIANCE = frozenset({"compliance", "buildings"})


@pytest.fixture(autouse=True)
def _no_pages_between_tests():
    token = PA.active_pages.set(None)
    space = spaces.active_space.set(None)
    yield
    PA.active_pages.reset(token)
    spaces.active_space.reset(space)


def held(pages):
    PA.active_pages.set(pages)


# ── where the pages come from ─────────────────────────────────────────────────

def test_no_pages_is_every_page():
    assert PA.current() is None
    assert all(PA.allows_engine(e) for e in PA.ENGINE_PAGE)


def test_the_pages_are_taken_from_the_caller():
    PA.set_for(SimpleNamespace(pages=COMPLIANCE))
    assert PA.current() == COMPLIANCE
    PA.set_for(SimpleNamespace(pages=None))
    assert PA.current() is None
    PA.set_for(SimpleNamespace())                 # a caller object from before the field
    assert PA.current() is None
    PA.set_for(None)
    assert PA.current() is None


def test_the_engines_are_the_ones_the_spaces_know():
    assert PA.ENGINE_PAGE == spaces._ENGINE_REPORT
    assert set(PA.ENGINE_PAGE.values()) <= ALL_PAGES


# ── engines ───────────────────────────────────────────────────────────────────

def test_an_engine_outside_the_pages_is_not_allowed():
    held(COMPLIANCE)
    assert PA.allows_engine("compliance")
    assert not PA.allows_engine("energy_intelligence")
    assert not PA.allows_engine("contract_performance")
    assert not PA.allows_engine("wo_engine")
    assert PA.allows_engine("migration") and PA.allows_engine(None)   # not a domain engine


def test_the_refusal_is_plain_and_names_nothing_internal():
    held(COMPLIANCE)
    said = PA.refusal("energy_intelligence")
    assert said == "That question is about energy, which is outside the pages you have access to."
    assert PA.refusal("compliance") is None
    for engine in PA.ENGINE_PAGE:
        text = (PA.refusal(engine) or "").lower()
        for internal in ("engine", "_", "svc", "plenum", "space"):
            assert internal not in text, (engine, text)


def test_browser_space_cannot_widen_pages():
    held(frozenset({"compliance"}))
    assert not spaces.allows("full", "energy_intelligence")
    assert not spaces.allows(None, "energy_intelligence")
    assert not spaces.allows("a space nobody has heard of", "wo_engine")
    assert spaces.allows("full", "compliance")


def test_a_space_still_narrows_inside_the_pages():
    held(frozenset({"compliance", "ops"}))
    assert spaces.allows("ops", "wo_engine")
    assert not spaces.allows("ops", "compliance")          # Ops Brain has no compliance module


def test_the_space_rules_are_unchanged_for_a_caller_with_every_page():
    assert spaces.allows("full", "energy_intelligence")
    assert not spaces.allows("ops", "compliance")
    said = spaces.refusal("ops", "compliance")
    assert said.startswith("That question is about compliance, which is not part of Ops Brain.")
    assert spaces.engines_for("full") == frozenset(PA.ENGINE_PAGE)
    assert spaces.engines_for(None) is None


def test_engines_for_follows_the_pages():
    held(COMPLIANCE)
    assert spaces.engines_for(None) == frozenset({"compliance"})
    assert spaces.engines_for("full") == frozenset({"compliance"})
    assert spaces.engines_for("ops") == frozenset()        # ops ∩ {compliance}
    held(frozenset())
    assert spaces.engines_for(None) == frozenset()


def test_the_pages_sentence_is_used_when_the_pages_are_what_refuse():
    held(COMPLIANCE)
    assert spaces.refusal("full", "energy_intelligence") == PA.refusal("energy_intelligence")
    assert spaces.refusal(None, "wo_engine") == PA.refusal("wo_engine")
    assert spaces.refusal("full", "compliance") is None


def test_the_planner_offers_only_held_engines():
    held(COMPLIANCE)
    assert set(planner.planning_engines()) == {"compliance"}
    held(None)
    assert set(planner.planning_engines()) == set(planner.ENGINES)


def test_engine_outside_pages_is_out_of_module_with_plain_refusal():
    held(COMPLIANCE)
    gated = agent_router._out_of_module("full", "energy_intelligence", "llm")
    assert gated["agent"] is None and gated["wanted"] == "energy_intelligence"
    assert gated["out_of_module"] == PA.refusal("energy_intelligence")
    assert agent_router._out_of_module("full", "compliance", "llm") is None


def test_the_router_catalogue_lists_only_held_agents():
    held(COMPLIANCE)
    _text, agents = agent_router._catalogue("full")
    assert "compliance" in agents
    assert not ({"energy_intelligence", "contract_performance", "wo_engine"} & agents)


# ── tools ─────────────────────────────────────────────────────────────────────

def tool(name, module):
    def fn():
        return None
    fn.__module__ = "src.agents." + module
    return SimpleNamespace(name=name, func=fn, coroutine=None)


def atool(name, module):
    async def fn():
        return None
    fn.__module__ = "src.agents." + module
    return SimpleNamespace(name=name, func=None, coroutine=fn)


@pytest.mark.parametrize("name,module,tags", [
    ("list_building_certificates", "compliance_engine_agent", ("compliance",)),
    ("generate_compliance_report", "compliance_agent", ("compliance",)),
    ("list_vendor_scorecards", "contract_performance_agent", ("vendors",)),
    ("propose_asset_criticality", "contract_performance_agent", ("vendors", "assets")),
    ("list_energy_anomalies", "energy_intelligence_agent", ("energy",)),
    ("get_asset_intelligence", "energy_intelligence_agent", ("assets",)),
    ("list_energy_buildings", "energy_intelligence_agent", ("shared",)),
    ("get_hoist_score", "energy_intelligence_agent", ("shared",)),
    ("upsert_building_energy_profile", "energy_intelligence_agent", ("buildings", "energy")),
    ("get_building_cost_drivers", "energy_intelligence_agent", ("buildings", "assets")),
    ("list_work_orders", "wo_engine_agent", ("ops",)),
    ("search_assets", "wo_engine_agent", ("assets",)),
    ("search_locations", "wo_engine_agent", ("assets", "ops")),
    ("find_asset", "udr_agent", ("assets",)),
    ("query_table", "udr_agent", ("any",)),
    ("udr_execute_select", "udr_agent", ("any",)),
    ("answer_with_graph_context", "udr_hybrid_tools", ("any",)),
    ("answer_from_records", "ontology_qa", ("any",)),
    ("query_docs", "doc_rag_agent", ("any",)),
    ("index_document", "doc_rag_agent", ("ingest",)),
    ("search_documents", "document_search_tool", ("shared",)),
    ("task", "meta_tools", ("shared",)),
    ("compact_context", "context_budget", ("shared",)),
    ("replacement_candidates", "thread_scope", ("assets",)),
    ("work_order_blockers", "thread_scope", ("ops",)),
    ("get_cost_savings", "cost_savings_agent", ("ops", "energy", "assets", "vendors")),
    ("start_migration", "migration_agent", ("ingest",)),
    ("something", "a_module_nobody_tagged", None),
])
def test_tool_tags(name, module, tags):
    assert PA.tool_tags(atool(name, module)) == tags
    assert PA.tool_tags(tool(name, module)) == tags


def test_unrestricted_filter_is_identity():
    tools = [tool("query_table", "udr_agent"), tool("x", "a_module_nobody_tagged")]
    assert PA.filter_tools(tools) is tools


def test_compliance_officer_gets_no_energy_or_vendor_tool():
    held(COMPLIANCE)
    tools = [atool("list_building_certificates", "compliance_engine_agent"),
             atool("list_energy_anomalies", "energy_intelligence_agent"),
             atool("list_vendor_scorecards", "contract_performance_agent"),
             atool("list_work_orders", "wo_engine_agent"),
             atool("get_asset_intelligence", "energy_intelligence_agent"),
             atool("list_energy_buildings", "energy_intelligence_agent"),
             atool("search_documents", "document_search_tool"),
             atool("query_table", "udr_agent"),
             atool("query_docs", "doc_rag_agent"),
             atool("index_document", "doc_rag_agent"),
             atool("task", "meta_tools"),
             atool("upsert_building_energy_profile", "energy_intelligence_agent"),
             atool("something", "a_module_nobody_tagged")]
    kept = [t.name for t in PA.filter_tools(tools)]
    assert kept == ["list_building_certificates", "list_energy_buildings", "search_documents",
                    "index_document", "task", "upsert_building_energy_profile"]


def test_a_tool_that_draws_on_several_registers_needs_all_of_them():
    savings = atool("get_cost_savings", "cost_savings_agent")
    held(frozenset({"ops", "energy", "assets"}))
    assert not PA.tool_allowed(savings)
    held(frozenset({"ops", "energy", "assets", "vendors"}))
    assert PA.tool_allowed(savings)


def test_tools_that_read_any_register_are_withheld_even_from_a_holder_of_every_page():
    # Held to pages at all, a caller is kept off the tools that can read anything: the table
    # tools name their own table and the document index has no notion of who is asking.
    held(ALL_PAGES)
    assert not PA.tool_allowed(atool("query_table", "udr_agent"))
    assert not PA.tool_allowed(atool("query_docs", "doc_rag_agent"))
    assert PA.tool_allowed(atool("list_energy_anomalies", "energy_intelligence_agent"))
    held(None)
    assert PA.tool_allowed(atool("query_table", "udr_agent"))


def test_the_pages_key_is_stable_for_caching():
    assert PA.pages_key(None) is None
    assert PA.pages_key(frozenset({"energy", "assets"})) == ("assets", "energy")
    assert PA.pages_key(frozenset()) == ()


# ── documents ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("doc_type,page", [
    ("compliance_certificate", "compliance"), ("certificate", "compliance"),
    ("contract", "vendors"), ("invoice", "vendors"), ("inspection_report", "ops"),
    ("warranty", "assets"), ("om_manual", "assets"), ("utility_bill", "energy"),
    ("drawing", "buildings"), (None, "buildings"), ("Other", "buildings"),
])
def test_document_page(doc_type, page):
    assert PA.document_page(doc_type) == page


def test_document_allowed_follows_the_pages():
    assert PA.document_allowed("contract")                     # no pages: everything
    held(COMPLIANCE)
    assert PA.document_allowed("certificate") and PA.document_allowed("drawing")
    assert not PA.document_allowed("contract")
