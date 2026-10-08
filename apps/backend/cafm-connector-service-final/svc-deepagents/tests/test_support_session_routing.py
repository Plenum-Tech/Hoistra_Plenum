"""A support session is answered, never asked back (8 Oct 2026).

The app's Support asks the orchestrator with a page context that carries the Hoistra guide and
says, in so many words, not to ask which data area a question is about. The router reads the
question before the orchestrator does, and "Is there an API or webhooks?" - one of Support's
own suggested questions - names no register, so it was answered with "Before I read anything,
I want to be sure what you are asking: This is a general product/platform capability question
not covered by any data specialist" (7 Oct 2026, 18:12). The guide was never read.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from src.agents import agent_router

SUPPORT_JS = Path(__file__).resolve().parents[4] / "frontend" / "src" / "logic" / "support.js"
CONTEXT = (
    "This is a Hoistra support session (reference S-1A2B). The user opened Support from the "
    "Home page.\n\n- Treat each message as a request for help using Hoistra.\n\n# Hoistra guide"
)


def _model_must_not_be_asked(monkeypatch):
    # Raises before any request is built, so no test here can reach the model's API.
    def refuse():
        raise AssertionError("the router model was asked")
    monkeypatch.setattr(agent_router, "llm_routing_enabled", lambda: True)
    monkeypatch.setattr(agent_router, "_catalogue", refuse)


def test_a_support_question_goes_to_the_orchestrator_loop_without_asking_the_model(monkeypatch):
    _model_must_not_be_asked(monkeypatch)
    r = asyncio.run(agent_router.select_agent("Is there an API or webhooks?", CONTEXT))
    assert r["agent"] is None and not r.get("clarify"), "no engine, nothing asked back"
    assert r["source"] == "support"


def test_a_follow_up_in_a_support_session_is_still_support(monkeypatch):
    # A follow-up's router context is the page context with the previous turn appended.
    _model_must_not_be_asked(monkeypatch)
    ctx = CONTEXT + "\n\nPREVIOUS QUESTION: How do I add a building?"
    r = asyncio.run(agent_router.select_agent("and how do I remove one?", ctx))
    assert r["source"] == "support"


def test_any_other_context_is_routed_as_before(monkeypatch):
    monkeypatch.setattr(agent_router, "llm_routing_enabled", lambda: False)
    for ctx in (None, "", "The user is on the Compliance page.", "Not " + CONTEXT):
        r = asyncio.run(agent_router.select_agent("Which certificates have lapsed?", ctx))
        assert r["source"] == "disabled", ctx


def test_the_mark_is_the_sentence_the_app_opens_a_support_context_with():
    src = SUPPORT_JS.read_text(encoding="utf-8")
    assert "'" + agent_router.SUPPORT_SESSION_MARK + "'" in src, \
        "support.js supportContext() must open with SUPPORT_SESSION_MARK, or Support is asked back again"
