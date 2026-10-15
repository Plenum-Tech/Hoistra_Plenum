"""A general question is asked back with the six areas; a reply continues the conversation.

From one conversation on 7 Oct 2026:
* "what is pending today's approval" should ask which area - compliance, contracts, invoices,
  assets, energy or maintenance - instead of guessing;
* "vendor name - LPS, email - aasim@lps-me.com", the answer to the assistant's own question, was
  routed alone and asked back as unclear, twice;
* "all of them" after a clarifying question got a promise ("I'll break down...") and no data;
* "for each one of these assets" was planned with no idea which assets were just listed.
"""
from __future__ import annotations

import asyncio

import pytest

from src.agents import domain_chooser as dc


@pytest.mark.parametrize("q", [
    "what is pending today's approval", "What needs my attention today?", "Any overdue items?",
    "what is outstanding", "give me a summary of the issues", "what decisions are due",
])
def test_a_general_question_with_no_area_is_asked_back(q):
    assert dc.is_general(q)


@pytest.mark.parametrize("q", [
    "which compliance certificates are pending renewal", "what invoices are pending approval",
    "which work orders are overdue", "energy anomalies at risk this month", "which vendors are blocked",
    "what is the hoist score of each building",
])
def test_a_question_that_names_its_area_is_not(q):
    assert not dc.is_general(q)


def test_a_question_asked_on_a_page_that_names_the_area_is_not_asked_back():
    assert not dc.is_general("what is pending today", page_context="Page: Maintenance - work orders and PPM")


def test_the_choice_offers_the_six_areas_and_all_of_them_each_asking_the_narrowed_question():
    cards = dc.choices("what is pending today's approval")
    assert [c["title"] for c in cards] == ["Compliance", "Contracts", "Invoices", "Assets", "Energy",
                                          "Maintenance", "All of these"]
    assert cards[5]["action"] == {"kind": "ask", "text": "what is pending today's approval - maintenance only?"}
    assert "across compliance, contracts, invoices, assets, energy and maintenance" in cards[6]["action"]["text"]


@pytest.mark.parametrize("reply, picked", [
    ("maintenance", ["maintenance"]), ("compliance and energy", ["compliance", "energy"]),
    ("all of them", ["all"]), ("everything", ["all"]), ("invoices please", ["invoice"]),
    ("the boiler on level 3 is leaking badly and needs someone today", None),
])
def test_a_typed_reply_picks_the_areas(reply, picked):
    assert dc.pick_domains(reply) == picked


@pytest.mark.parametrize("answer", [
    "I need the assigned vendor email.\nReply with the vendor name and email address you want to use, and I will prepare it.",
    "Which building do you mean?",
    "Please confirm the date before I raise it.",
])
def test_an_answer_that_asks_the_user_for_something_is_recognised(answer):
    assert dc.asks_back(answer)


def test_an_answer_that_states_facts_does_not_ask_back():
    assert not dc.asks_back("Two fire certificates have lapsed: FRA Bishopsgate and EICR B-301.")


# ── the orchestrator's step, with the previous turn stood in ──────────────────────────────

def _orch():
    import src.agents.orchestrator as O
    cls = next(v for v in vars(O).values() if isinstance(v, type) and hasattr(v, "_conversation_step"))
    return cls.__new__(cls), O


def _step(monkeypatch, message, *, prev=None, state=None, page=None):
    orch, O = _orch()

    async def last_turn(sid):
        return prev
    monkeypatch.setattr(O.chat_threads, "last_turn", last_turn)
    return asyncio.run(orch._conversation_step("s-1", message, page, state if state is not None else {}))


def test_the_vendor_details_after_the_assistant_asked_for_them_continue_the_conversation(monkeypatch):
    prev = {"question": "work order requests on email to vendors",
            "answer": "Proposed email ... Reply with the vendor name and email address you want to use, and I will prepare it."}
    out = _step(monkeypatch, "vendor name - LPS, email - aasim@lps-me.com", prev=prev)
    assert out["kind"] == "continue"
    assert "PREVIOUS ANSWER" in out["context"] and "Reply with the vendor name" in out["context"]


def test_a_new_question_after_the_assistant_asked_something_is_still_routed(monkeypatch):
    prev = {"question": "x", "answer": "Which building do you mean?"}
    assert _step(monkeypatch, "What is the EUI of Bishopsgate Tower?", prev=prev)["kind"] == "route"


def test_a_general_question_is_answered_with_the_choice(monkeypatch):
    assert _step(monkeypatch, "what is pending today's approval")["kind"] == "choose"


def test_a_typed_choice_answers_the_question_that_was_asked_back(monkeypatch):
    state = {"pending_domain_question": "what is pending today's approval"}
    out = _step(monkeypatch, "all of them", state=state)
    assert out["kind"] == "rewritten"
    assert out["message"].startswith("what is pending today's approval - across compliance")
    assert "pending_domain_question" not in state            # answered once


def test_a_follow_up_carries_the_previous_answer_to_the_agents(monkeypatch):
    prev = {"question": "building-wise assets at risk with anomalies",
            "answer": "| Bishopsgate Tower | CHILLER-101 | Threat ... |"}
    out = _step(monkeypatch, "for each one of these assets give me a work order action", prev=prev)
    assert out["kind"] == "followup"
    assert "CHILLER-101" in out["context"]
    assert "PREVIOUS QUESTION" in out["router_context"]


def test_the_domain_choice_keeps_the_question_and_carries_the_cards(monkeypatch):
    orch, O = _orch()
    monkeypatch.setattr(O.trace, "on_plan", lambda *a, **k: None)
    state = {}
    result = orch._domain_choice("s-1", "what is pending today's approval", state)
    assert state["pending_domain_question"] == "what is pending today's approval"
    assert len(result["choices"]) == 7 and "Which area do you mean" in result["answer"]


# ── 7 Oct 2026, second conversation: the at-risk asset list and what followed it ──────────

_AT_RISK = ("| Bishopsgate Tower | Boiler 1 — central plant | B-301-BOILER-01 | threat | £24,938.50 |\n"
            "| Bishopsgate Tower | CHILLER-101 | B-301-CHILLER-101 | threat | £114,716.69 |")


@pytest.mark.parametrize("message", [
    "Okay, go ahead and plan emails step by step for me for each one of the assets.",
    "You just gave me all the assets at risk on a building-wise level - plan a work order for each",
    "use the previous table and raise one per row",
])
def test_a_message_that_leans_on_the_last_answer_without_saying_these_is_a_follow_up(monkeypatch, message):
    out = _step(monkeypatch, message, prev={"question": "assets at risk by building", "answer": _AT_RISK})
    assert out["kind"] == "followup"
    assert "B-301-CHILLER-101" in out["previous_answer"]


def test_a_fresh_question_does_not_lean_on_the_last_answer():
    assert not dc.refers_back("Which vendors are blocked right now?")


def test_the_previous_answer_reaches_every_planned_step():
    from src.agents import planner
    asks = []

    async def run_engine(engine, ask, on_ev):
        asks.append(ask)
        return "ok", []

    async def run_tool(name, args):
        asks.append(args.get("question"))
        return {"rows": []}
    plan = {"steps": [
        {"id": "s1", "kind": "engine", "target": "wo_engine", "ask": "plan a work order per asset",
         "depends_on": [], "why": ""},
        {"id": "s2", "kind": "tool", "target": "answer_from_records", "args": {"question": "open WOs"},
         "ask": "open WOs for these assets", "depends_on": [], "why": ""}]}
    asyncio.run(planner.execute(plan, run_engine=run_engine, run_tool=run_tool, given=_AT_RISK))
    assert len(asks) == 2 and all("B-301-BOILER-01" in a for a in asks)


def test_the_answer_writer_is_given_the_previous_answer_as_a_result(monkeypatch):
    orch, O = _orch()
    seen = {}

    async def execute(plan, **kw):
        seen["given"] = kw.get("given")
        return {"s1": {"output": "no open work orders", "tool_calls": [], "ok": True, "error": None}}

    async def write(question, plan, results):
        seen["results"] = results
        return "answer", {"ok": True}

    async def close(*a, **k):
        return None
    monkeypatch.setattr(O.planner, "execute", execute)
    monkeypatch.setattr(O.planner, "describe", lambda plan: "plan")
    orch._write_planned_answer = write
    orch._planned_cards = lambda *a: []
    orch._close_streamed_turn = close
    orch._run_planned_tool = None
    orch._replan_after_gates = None
    orch._check_step = None
    monkeypatch.setattr(O, "attach_route_to_result", lambda r, *a, **k: r)
    monkeypatch.setattr(O, "workflow_stream_completion_payload", lambda *a, **k: {"done": True})

    async def drain():
        async for _ in orch._stream_planned_turn("s-1", "a work order for each", {"steps": [{"target": "wo_engine"}]},
                                                 0.0, previous=_AT_RISK):
            pass
    asyncio.run(drain())
    assert "B-301-BOILER-01" in seen["given"]
    assert "CHILLER-101" in seen["results"]["previous_answer"]["output"]


# ── when a turn asks back, and how ─────────────────────────────────────────────────────────

_NO_AGENT = {"clarify": True, "agent": None,
             "reason": "No agent in the catalogue drafts or sends emails; need clarification on what action/tool should handle this"}


def test_a_follow_up_the_router_cannot_place_is_answered_not_asked_back():
    orch, _ = _orch()
    msg = "Okay, go ahead and plan emails step by step for me for each one of the assets."
    assert orch._ask_back("followup", _NO_AGENT, msg) == (None, None)


def test_a_question_that_names_its_area_gets_the_routers_one_line_question_not_the_cards():
    orch, _ = _orch()
    routing = {"clarify": True, "reason": "could mean asset replacement, repair cost or parts reorder"}
    how, line = orch._ask_back("route", routing, "which assets are required for repurchase?")
    assert how == "text" and "replacement" in line


def test_a_question_that_names_no_area_and_cannot_be_placed_gets_the_cards():
    orch, _ = _orch()
    how, _ = orch._ask_back("route", {"clarify": True, "reason": "too broad to place anywhere"},
                            "what should I look at first this morning?")
    assert how == "cards"


def test_a_general_question_always_gets_the_cards_and_a_placed_one_never_asks():
    orch, _ = _orch()
    assert orch._ask_back("choose", None, "what is pending today's approval")[0] == "cards"
    assert orch._ask_back("route", {"agent": "compliance"}, "which certificates lapsed?") == (None, None)
