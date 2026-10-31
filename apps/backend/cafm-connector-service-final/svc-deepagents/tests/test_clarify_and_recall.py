"""A router that could not place the question is asked, not overruled; and what the thread
remembers reaches the sub-agent that writes the SQL.

Measured 3 Oct 2026 (turn-d67c0f243fde442c): "which assets are required for repurchase?" - the
router returned clarify ("asset replacement, repair cost, or inventory reorder"), the general loop
ran anyway, keyword-matched "which assets" to udr, and answered from an empty energy table after
50 s, 15 tool calls and $0.22. The company's one teaching never reached that sub-agent.
"""
from __future__ import annotations

from src.agents import meta_tools
from src.agents.orchestrator import DeepAgentOrchestrator as O
from src.services import chat_memories as cm


def test_a_clarify_decision_becomes_one_question_with_the_readings_the_router_saw():
    routing = {"agent": None, "also": [], "source": "llm", "clarify": True,
               "reason": "The phrase 'required for repurchase' is unclear and could mean asset replacement, repair cost, or inventory reorder."}
    out = O._clarify_reply(routing, "which assets are required for repurchase?")
    assert out and out.startswith("Before I read anything")
    assert "asset replacement, repair cost, or inventory reorder" in out
    assert "parts stock" in out and "all of them" in out


def test_nothing_is_asked_when_the_router_placed_the_question_or_had_no_reason():
    assert O._clarify_reply({"agent": "compliance", "source": "llm", "reason": "certificates"}, "which certificates expire?") is None
    assert O._clarify_reply({"agent": None, "source": "llm", "clarify": True, "reason": ""}, "which assets are required for repurchase?") is None
    assert O._clarify_reply({"agent": None, "source": "llm", "clarify": True, "reason": "unclear"}, "yes") is None
    assert O._clarify_reply({"agent": None, "source": "keyword", "reason": "llm routing disabled"}, "which assets are required for repurchase?") is None
    assert O._clarify_reply(None, "anything at all here") is None


def test_the_turn_recall_block_is_put_in_front_of_the_sub_agent_prompt_once():
    tok = cm.turn_recall.set("## What I remember\n- repurchase means parts below reorder level (correction, company-wide)")
    try:
        p = meta_tools.with_turn_recall("# User request\nwhich assets are required for repurchase?")
        assert p.startswith("## What I remember") and p.endswith("repurchase?")
        assert meta_tools.with_turn_recall(p) == p
    finally:
        cm.turn_recall.reset(tok)
    assert meta_tools.with_turn_recall("plain") == "plain"
