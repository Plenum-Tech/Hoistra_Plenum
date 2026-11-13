"""The model is never handed a tool it may not use, and a tool refuses to run if it is.

Two layers, both pinned here. Every place a tool list is bound gives a caller held to some
pages a list with only their tools. And every tool carries a guard of its own, so a path that
reaches a tool some other way — a plan step, a shortcut — is refused by the tool itself before
it reads anything. A caller with every page gets the agents and the tools that were always there.
"""
from __future__ import annotations

import importlib
import inspect
import pkgutil
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from langchain_core.tools import BaseTool, tool

import src.agents as agents_pkg
from src.agents import meta_tools, orchestrator, page_access as PA, udr_agent, wo_engine_agent
from src.agents.context_budget import compact_context
from src.services import principal as P

COMPLIANCE = frozenset({"compliance", "buildings"})


@pytest.fixture(autouse=True)
def _no_pages_between_tests():
    token = PA.active_pages.set(None)
    yield
    PA.active_pages.reset(token)


def every_tool() -> dict[str, BaseTool]:
    found: dict[str, BaseTool] = {}
    for info in pkgutil.iter_modules(agents_pkg.__path__):
        module = importlib.import_module("src.agents." + info.name)
        for _name, value in vars(module).items():
            if isinstance(value, BaseTool):
                found[value.name] = value
    return found


TOOLS = every_tool()


# ── the registry is complete ──────────────────────────────────────────────────

def test_there_are_tools_to_tag():
    assert len(TOOLS) > 150


def test_every_tool_is_tagged():
    untagged = sorted(n for n, t in TOOLS.items() if PA.tool_tags(t) is None)
    assert not untagged, (
        "These tools have no owner in agents/page_access.py. Give each the page whose register "
        f"it reads (by its module, or by name): {untagged}")


def test_every_tag_is_a_page_or_a_known_kind():
    known = set(PA.PAGES) | {PA.SHARED, PA.INGEST, PA.ANY}
    for name, t in TOOLS.items():
        assert set(PA.tool_tags(t)) <= known, (name, PA.tool_tags(t))


def test_every_bound_tool_is_one_the_registry_saw():
    bound = [*orchestrator.ALL_TOOLS, compact_context,
             *[t for tools in orchestrator.PHASE2_ENGINE_TOOLS.values() for t in tools],
             *wo_engine_agent.WO_ENGINE_SUBAGENT_TOOLS, *wo_engine_agent.MAINTENANCE_READ_TOOLS]
    assert not sorted({t.name for t in bound} - set(TOOLS))


# ── the pages come from the caller ────────────────────────────────────────────

def caller(pages=None):
    return P.Principal(user_id=uuid4(), email="x@example.com", organization_id=None, role="user",
                       can_ingest=False, building_ids=None, pages=pages)


def test_the_pages_are_read_from_the_signed_in_caller():
    PA.active_pages.set(PA.FROM_CALLER)
    token = P.caller_principal.set(caller(COMPLIANCE))
    try:
        assert PA.current() == COMPLIANCE
        P.caller_principal.set(caller(None))
        assert PA.current() is None
    finally:
        P.caller_principal.reset(token)


def test_a_request_with_no_caller_is_not_held_to_pages():
    PA.active_pages.set(PA.FROM_CALLER)
    token = P.caller_principal.set(None)
    try:
        assert PA.current() is None
    finally:
        P.caller_principal.reset(token)


# ── a tool guards itself ──────────────────────────────────────────────────────

def test_every_bound_tool_carries_the_guard():
    bound = [*orchestrator.ALL_TOOLS,
             *[t for tools in orchestrator.PHASE2_ENGINE_TOOLS.values() for t in tools],
             *wo_engine_agent.WO_ENGINE_SUBAGENT_TOOLS, *wo_engine_agent.MAINTENANCE_READ_TOOLS]
    bare = sorted({t.name for t in bound if not PA.is_guarded(t)})
    assert not bare, f"tools bound without the page guard: {bare}"


def make_tool(module="energy_intelligence_agent"):
    calls = []

    async def list_things(limit: int = 5) -> dict:
        """List things."""
        calls.append(limit)
        return {"ok": True, "rows": [1, 2, 3]}
    list_things.__module__ = "src.agents." + module
    return tool(list_things), calls


async def test_a_guarded_tool_refuses_a_caller_without_its_page():
    t, calls = make_tool()
    PA.guard_tools([t])
    PA.active_pages.set(COMPLIANCE)
    out = await t.ainvoke({"limit": 2})
    assert calls == []                                    # the register was never read
    assert out["reason"] == "page_not_allowed"
    assert out["error"] == "That is outside the pages you have access to."


async def test_a_guarded_tool_runs_for_a_holder_and_for_every_page():
    t, calls = make_tool()
    PA.guard_tools([t])
    PA.active_pages.set(frozenset({"energy"}))
    assert (await t.ainvoke({"limit": 2}))["rows"] == [1, 2, 3]
    PA.active_pages.set(None)
    assert (await t.ainvoke({"limit": 3}))["rows"] == [1, 2, 3]
    assert calls == [2, 3]


async def test_guarding_twice_wraps_once():
    t, calls = make_tool()
    PA.guard_tools([t])
    once = t.coroutine
    PA.guard_tools([t])
    assert t.coroutine is once
    await t.ainvoke({})
    assert calls == [5]


def test_a_guarded_tool_keeps_its_owner_and_its_arguments():
    t, _ = make_tool()
    before = inspect.signature(t.coroutine)
    PA.guard_tools([t])
    assert PA.tool_tags(t) == ("energy",)
    assert inspect.signature(t.coroutine) == before
    assert t.args_schema.model_json_schema()["properties"].keys() == {"limit"}


def test_a_sync_tool_is_guarded_too():
    def lookup(key: str = "a") -> str:
        """Look something up."""
        return "found " + key
    lookup.__module__ = "src.agents.contract_performance_agent"
    t = tool(lookup)
    PA.guard_tools([t])
    PA.active_pages.set(COMPLIANCE)
    assert t.invoke({"key": "b"})["reason"] == "page_not_allowed"
    PA.active_pages.set(None)
    assert t.invoke({"key": "b"}) == "found b"


async def test_query_table_refuses_a_caller_held_to_pages(monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("the table was read")
    monkeypatch.setattr(udr_agent.database, "AsyncSessionLocal", boom)
    PA.active_pages.set(frozenset(PA.PAGES))              # even with every page ticked
    out = await udr_agent.query_table.ainvoke({"table_name": "compliance_certificates"})
    assert out["reason"] == "page_not_allowed"


# ── the main loop's agent ─────────────────────────────────────────────────────

def bare_orchestrator(monkeypatch):
    orch = orchestrator.DeepAgentOrchestrator.__new__(orchestrator.DeepAgentOrchestrator)
    orch._agent = object()
    orch._page_agents = {}
    built = []

    def _build(tools):
        built.append([t.name for t in tools])
        return SimpleNamespace(tools=built[-1])
    monkeypatch.setattr(orch, "_build_agent", _build, raising=False)
    return orch, built


def test_a_caller_with_every_page_gets_the_agent_built_at_start(monkeypatch):
    orch, built = bare_orchestrator(monkeypatch)
    assert orch._agent_for_caller() is orch._agent
    assert built == []


def test_main_agent_is_built_per_page_set_and_cached(monkeypatch):
    orch, built = bare_orchestrator(monkeypatch)
    PA.active_pages.set(COMPLIANCE)
    first = orch._agent_for_caller()
    assert orch._agent_for_caller() is first and len(built) == 1
    names = set(first.tools)
    assert "compact_context" in names and "task" in names
    # The loop's list holds the work-order tools and the table tools; neither is this caller's.
    assert {"list_work_orders", "query_table"} <= {t.name for t in orchestrator.ALL_TOOLS}
    assert not names & {"list_work_orders", "create_work_order", "query_table",
                        "udr_execute_select", "query_docs", "search_assets"}
    PA.active_pages.set(frozenset({"ops"}))
    other = orch._agent_for_caller()
    assert other is not first and len(built) == 2
    assert "list_work_orders" in other.tools and "query_table" not in other.tools
    assert "search_assets" not in other.tools              # the assets page's, kept in this list


def test_the_engine_tool_lists_follow_the_pages():
    PA.active_pages.set(frozenset({"energy"}))
    lists = dict(orchestrator.DeepAgentOrchestrator._space_engine_tools())
    assert set(lists) == {"energy_intelligence"}
    names = {t.name for t in lists["energy_intelligence"]}
    assert "list_energy_anomalies" in names
    assert "get_asset_intelligence" not in names          # the assets page's, kept in this list
    PA.active_pages.set(None)
    full = dict(orchestrator.DeepAgentOrchestrator._space_engine_tools())
    assert full["energy_intelligence"] is orchestrator.PHASE2_ENGINE_TOOLS["energy_intelligence"]


# ── sub-agents ────────────────────────────────────────────────────────────────

def bare_runner():
    runner = meta_tools._TaskRunner.__new__(meta_tools._TaskRunner)
    runner._page_runners = {}
    built = []

    def _sub(name, *, tools, prompt):
        built.append((name, [t.name for t in tools]))
        return SimpleNamespace(name=name, tools=built[-1][1])
    runner._sub = _sub
    cert = TOOLS["list_building_certificates"]
    anomalies = TOOLS["list_energy_anomalies"]
    runner._specs = {"compliance": ([cert, compact_context], "p"),
                     "energy_intelligence": ([anomalies, compact_context], "p"),
                     "udr": ([TOOLS["query_table"], TOOLS["udr_execute_select"], compact_context], "p")}
    return runner, built


def test_a_sub_agent_is_rebuilt_with_only_the_callers_tools():
    runner, built = bare_runner()
    PA.active_pages.set(COMPLIANCE)
    sub = runner._runner_for_pages("compliance")
    assert sub.tools == ["list_building_certificates"]
    assert runner._runner_for_pages("compliance") is sub and len(built) == 1


def test_a_sub_agent_with_nothing_left_is_not_run():
    runner, built = bare_runner()
    PA.active_pages.set(COMPLIANCE)
    assert runner._runner_for_pages("energy_intelligence") is None
    assert runner._runner_for_pages("udr") is None
    assert runner._runner_for_pages("an agent nobody registered") is None
    assert built == []


async def test_running_a_sub_agent_outside_the_pages_answers_with_the_refusal():
    runner, _ = bare_runner()
    runner._agents = {"energy_intelligence": SimpleNamespace()}
    PA.active_pages.set(COMPLIANCE)
    answer, calls = await runner._run_verbose("energy_intelligence", "what is my worst meter?")
    assert "outside the pages you have access to" in answer and calls == []


# ── what the model is told ────────────────────────────────────────────────────

def test_the_model_is_told_which_pages_it_may_answer_from():
    assert PA.prompt_note() is None
    PA.active_pages.set(COMPLIANCE)
    note = PA.prompt_note()
    assert "Buildings" in note and "Compliance" in note
    assert "Energy" in note.split("Everything else")[1]    # named as out of reach
    for internal in ("svc", "plenum", "_engine", "page_access"):
        assert internal not in note


def test_an_engine_named_by_the_client_is_still_held_to_the_pages():
    # The request may name an engine outright; that must not be a way round the pages.
    source = inspect.getsource(orchestrator.DeepAgentOrchestrator)
    named = source.index("engine = None if support else preferred_engine")
    assert "page_access.refusal(engine)" in source[named:named + 900]
