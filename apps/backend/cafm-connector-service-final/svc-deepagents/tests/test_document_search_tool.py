"""Documents in the chat: one general tool, held by every engine, and the plan that decides when to use it."""
from __future__ import annotations

import asyncio

from src.agents import document_search_tool as dst
from src.agents.skills import shared_skill


def test_every_engine_holds_the_document_search():
    from src.agents.compliance_engine_agent import COMPLIANCE_ENGINE_TOOLS
    from src.agents.contract_performance_agent import CONTRACT_PERFORMANCE_TOOLS
    from src.agents.energy_intelligence_agent import ENERGY_INTELLIGENCE_TOOLS
    from src.agents.wo_engine_agent import MAINTENANCE_READ_TOOLS
    for tools in (COMPLIANCE_ENGINE_TOOLS, CONTRACT_PERFORMANCE_TOOLS, ENERGY_INTELLIGENCE_TOOLS, MAINTENANCE_READ_TOOLS):
        assert dst.search_documents in tools


def test_a_search_must_name_what_the_documents_are_about():
    out = asyncio.run(dst.search_documents.ainvoke({"question": "what is the service credit?"}))
    assert out["ok"] is False and "linked to" in out["error"]


def test_the_shared_plan_sorts_facts_into_records_and_documents():
    body = shared_skill().body
    assert "Records or documents" in body and "search_documents" in body
    assert "not_indexed" in body and "never search the whole corpus" in body
