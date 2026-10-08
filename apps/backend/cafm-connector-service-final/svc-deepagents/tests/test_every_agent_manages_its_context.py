"""Every agent the orchestrator hands work to manages its own context.

The first Skill lab Measure (6 Oct 2026) recorded 0 compactions on 6 questions: the hook sat on
the orchestrator loop and on the fixed compliance agent, but the compliance agent actually used
in production is rebuilt per question (selective skill loading), and the energy, vendor, work-order
and database-reader agents never had it. Those are the agents that read whole registers.
"""
from __future__ import annotations

import pytest

from src.agents import meta_tools
from src.agents.context_budget import COMPACT_TOOL

AGENTS = ("migration", "doc_rag", "wo_engine", "maintenance", "compliance",
          "contract_performance", "energy_intelligence", "udr")


@pytest.fixture
def built(monkeypatch):
    calls = []

    def record(llm, *, tools, prompt=None, pre_model_hook=None, **kw):
        calls.append({"tools": [getattr(t, "name", str(t)) for t in tools], "hook": pre_model_hook})
        return {"agent": len(calls) - 1}

    monkeypatch.setattr(meta_tools, "create_react_agent", record)
    runner = meta_tools._TaskRunner("sk-test", "gpt-4o-mini")
    return runner, calls


def test_every_sub_agent_gets_the_compaction_tool_and_the_budget_hook(built):
    runner, calls = built
    assert set(AGENTS) <= set(runner._agents)
    for name in AGENTS:
        c = calls[runner._agents[name]["agent"]]
        assert COMPACT_TOOL in c["tools"], name
        assert c["hook"] is not None and callable(c["hook"]), name
        assert c["tools"].count(COMPACT_TOOL) == 1, name


def test_the_compliance_agent_rebuilt_per_question_has_them_too(built):
    runner, calls = built
    runner._sub("compliance", tools=runner._compliance_tools, prompt="per-question contract")
    c = calls[-1]
    assert COMPACT_TOOL in c["tools"] and c["hook"] is not None
    assert c["tools"].count(COMPACT_TOOL) == 1


def test_no_agent_is_built_anywhere_but_through_the_helper():
    import inspect

    src = inspect.getsource(meta_tools)
    assert src.count("create_react_agent(") == 1   # the one call inside _sub
