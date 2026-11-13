"""One flow for every question (12 Nov 2026): the orchestrator plans, the sub-agent reads and drafts,
and the answer comes back to the orchestrator, which writes the short answer the question asked for.

Read from the first 54 traced turns after Workstream A went live:
  * 26 answers - every compliance one - were written by the compliance analyst and sent as they stood.
    The compliance and contract analysts were exempt from the final step. Their text is now its draft;
    the card keeps its data (KPIs, certificates, actions) and the chat shows one text;
  * 13 of those had no plan at all (the compliance path that answers before the router), and a
    keyword-routed turn had none either. Every path now records the orchestrator's plan, once;
  * "is a work order booked for that inspection?" was taken as a request to raise a work order, so it
    skipped the router and the planner.
"""
from __future__ import annotations

import asyncio
import inspect
import uuid

import pytest

from src.agents import planner, trace
from src.agents.session_workspace import (ROUTE_GENERAL, ROUTE_WO_INTAKE, classify_route_intent,
                                          default_session_state)
from src.http_client import caller_organization_id
from src.services.principal import Principal, caller_principal

ROWS = [{"id": "c-1", "certificate_type_code": "EICR", "building_name": "Kingsway House", "status": "Lapsed",
         "expiry_date": "2026-08-01", "days_to_expiry": -103, "cert_scope": "building"},
        {"id": "c-2", "certificate_type_code": "GAS_SAFETY", "building_name": "Bishopsgate Tower", "status": "In Date",
         "expiry_date": "2027-03-01", "days_to_expiry": 109, "cert_scope": "building"}]
DRAFT = ("**Kingsway House** — one certificate has lapsed\n- The EICR expired on 1 Aug 2026\n- It is 103 days overdue\n\n"
         "One certificate has lapsed across the portfolio, which leaves Kingsway House exposed until it is renewed.")
SHORT = "**1 certificate has lapsed:** the EICR at Kingsway House, expired 2026-08-01."


def _card():
    return {"narrative": "One certificate has lapsed across the portfolio.",
            "groups": [{"owner": "Kingsway House", "headline": "one certificate has lapsed", "points": ["The EICR expired"]}],
            "sections": [{"id": "q1", "question": "which lapsed", "narrative": "The EICR at Kingsway House."}],
            "kpis": [{"label": "Lapsed", "value": 1}],
            "certificates": [ROWS[0]], "actions": [{"title": "Renew the EICR"}]}


def _calls(card):
    return [{"tool": "list_building_certificates", "input": {}, "output": {"certificates": ROWS}},
            {"tool": "compliance_response", "input": {}, "output": card}]


@pytest.fixture
def orch(monkeypatch):
    from src.agents import orchestrator as orch_mod

    p = Principal(user_id=uuid.uuid4(), email="fm@example.com", organization_id=uuid.uuid4(), role="admin",
                  can_ingest=True, building_ids=None)
    tp, to = caller_principal.set(p), caller_organization_id.set(str(p.organization_id))
    trace._turn.set(None)
    O = orch_mod.DeepAgentOrchestrator
    o = O.__new__(O)
    seen: list[tuple[str, str]] = []
    reply = {"text": SHORT, "raises": False}

    async def llm(system, user, role, on_delta=None):
        seen.append((role, user))
        if reply["raises"]:
            raise RuntimeError("the model is not answering")
        return reply["text"]

    o._planner_llm = llm
    yield {"o": o, "seen": seen, "reply": reply, "mod": orch_mod}
    caller_principal.reset(tp)
    caller_organization_id.reset(to)
    trace._turn.set(None)


# -- the analyst's answer comes back ------------------------------------------------------------

def test_the_analysts_text_is_the_draft_and_the_orchestrator_writes_the_answer(orch):
    card = _card()
    answer, source = asyncio.run(orch["o"]._answer_over_analyst(
        "Which certificates have lapsed?", DRAFT, _calls(card), facts="by status: Lapsed 1, In Date 1", analyst="compliance"))
    assert (answer, source) == (SHORT, "synthesis")
    role, prompt = orch["seen"][0]
    assert role == "final_answer"
    assert "DRAFT (not evidence)" in prompt and "which leaves Kingsway House exposed" in prompt


def test_the_counted_figures_and_the_cited_rows_lead_the_evidence(orch):
    asyncio.run(orch["o"]._answer_over_analyst(
        "Which certificates have lapsed?", DRAFT, _calls(_card()), facts="by status: Lapsed 1, In Date 1", analyst="compliance"))
    prompt = orch["seen"][0][1]
    results = prompt.split("TOOL RESULTS", 1)[1]
    assert "by status: Lapsed 1, In Date 1" in results
    assert results.index("counted_facts") < results.index("certificates_cited") < results.index("list_building_certificates")


def test_the_card_keeps_its_data_and_shows_one_text(orch):
    card = _card()
    answer, _ = asyncio.run(orch["o"]._answer_over_analyst("Which certificates have lapsed?", DRAFT, _calls(card), analyst="compliance"))
    assert card["narrative"] == planner._lead(answer)
    assert card["groups"] == [] and card["sections"] == []
    assert card["kpis"] == [{"label": "Lapsed", "value": 1}] and card["certificates"] == [ROWS[0]]
    assert card["actions"] == [{"title": "Renew the EICR"}]


def test_what_was_painted_while_the_analyst_wrote_is_replaced(orch):
    zones: list[tuple[str, object]] = []

    async def on_zone(key, value):
        zones.append((key, value))

    card = _card()
    asyncio.run(orch["o"]._answer_over_analyst("Which certificates have lapsed?", DRAFT, _calls(card), on_zone=on_zone,
                                               analyst="compliance"))
    assert dict(zones) == {"narrative": card["narrative"], "groups": [], "sections": []}


def test_when_the_final_step_cannot_write_the_analysts_answer_stands(orch):
    """It is what the user got before, and it is checked against the rows in code."""
    orch["reply"]["raises"] = True
    card = _card()
    before = {k: (list(v) if isinstance(v, list) else v) for k, v in card.items()}
    answer, source = asyncio.run(orch["o"]._answer_over_analyst("Which certificates have lapsed?", DRAFT, _calls(card),
                                                                analyst="compliance"))
    assert source is None and answer == DRAFT.strip()
    assert card == before


def test_nothing_to_rewrite_is_left_alone(orch):
    assert asyncio.run(orch["o"]._answer_over_analyst("q", "  ", [], analyst="contract")) == ("", None)
    assert orch["seen"] == []


def test_a_contract_card_is_treated_the_same(orch):
    card = {"narrative": "Apex Mechanical scored 61 in October.", "groups": [{"owner": "Apex", "headline": "below 80", "points": []}],
            "kpis": [{"label": "Score", "value": 61}]}
    orch["reply"]["text"] = "**Apex Mechanical scored 61 in October 2026**, below the 80 threshold."
    calls = [{"tool": "list_vendor_scorecards", "input": {}, "output": {"scorecards": [
        {"vendor_name": "Apex Mechanical", "score_month": "2026-10", "overall_score": 61}], "threshold": 80}},
        {"tool": "compliance_response", "input": {}, "output": card}]
    answer, source = asyncio.run(orch["o"]._answer_over_analyst("How is Apex performing?", card["narrative"], calls, analyst="contract"))
    assert source == "synthesis" and "61" in answer
    assert card["groups"] == [] and card["kpis"] == [{"label": "Score", "value": 61}]


def test_every_analyst_exit_goes_back_through_the_orchestrator():
    """The four places an analyst's text used to leave from: the compliance path before the router,
    the compliance engine, and the contract analyst on the engine and on the loop."""
    import src.agents.orchestrator as O

    src = inspect.getsource(O)
    assert src.count("await self._answer_over_analyst(") == 4
    assert src.count('analyst="compliance"') == 2 and src.count('analyst="contract"') == 2


def test_the_compliance_engines_answer_is_written_by_the_orchestrator(orch, monkeypatch):
    from src.agents import llm_cost
    from src.services import chat_memories

    monkeypatch.setenv("ACTIVITY_LOG_ENABLED", "false")
    llm_cost._ledger.set(None)
    o, mod = orch["o"], orch["mod"]

    async def no_recall(*a, **k):
        return []

    async def fake_engine(engine, prompt, on_event=None):
        return "The EICR at Kingsway House lapsed.", [
            {"tool": "list_building_certificates", "input": {}, "output": {"certificates": ROWS}}]

    async def composed(self, question, calls, session_id, answer, on_zone=None):
        return _card(), []

    async def set_scope(*a, **k):
        return None

    monkeypatch.setattr(chat_memories, "recall", no_recall)
    monkeypatch.setattr(mod, "run_phase2_engine_verbose", fake_engine)
    monkeypatch.setattr(type(o), "_compose_structured_compliance", composed)
    o._set_turn_scope = set_scope
    out = asyncio.run(o._invoke_phase2_engine(engine="compliance", user_message="Which certificates have lapsed?",
                                              session_id="s-" + uuid.uuid4().hex[:6], extra_context=None,
                                              on_zone=None, attach_route=False))
    assert out["answer"] == SHORT and out["answer_source"] == "synthesis"
    card = next(tc["output"] for tc in out["tool_calls"] if tc["tool"] == "compliance_response")
    assert card["groups"] == [] and card["kpis"] and card["certificates"]
    assert [r for r, _ in orch["seen"]] == ["final_answer"]          # written once, not twice


# -- every turn records the orchestrator's plan, once --------------------------------------------

@pytest.fixture
def traced(monkeypatch):
    monkeypatch.setattr(trace, "_ready", True)

    async def fake_write(t, answer, tool_calls, summary):
        return None

    monkeypatch.setattr(trace, "_write", fake_write)
    p = Principal(user_id=uuid.uuid4(), email="fm@example.com", organization_id=uuid.uuid4(), role="admin",
                  can_ingest=True, building_ids=None)
    tp, to = caller_principal.set(p), caller_organization_id.set(str(p.organization_id))
    trace._turn.set(None)
    trace.on_activity({"agent": "orchestrator", "stage": "turn", "direction": "input", "summary": "q",
                       "payload": {"message": "q"}})
    yield trace.current()
    caller_principal.reset(tp)
    caller_organization_id.reset(to)
    trace._turn.set(None)


def test_a_turn_knows_whether_it_has_planned(traced):
    assert not trace.has_plan()
    trace.on_plan(planner.one_step_plan("compliance", "a certificate question", source="keyword"), source="keyword")
    assert trace.has_plan()
    assert traced.route == "single:compliance"


def test_no_turn_means_no_plan():
    trace._turn.set(None)
    assert not trace.has_plan()


def test_the_compliance_path_before_the_router_records_the_plan_once():
    import src.agents.orchestrator as O

    body = inspect.getsource(O.DeepAgentOrchestrator._maybe_compliance_posture_shortcut)
    assert "if not trace.has_plan():" in body
    assert body.index("trace.on_plan(planner.one_step_plan(") < body.index("await self._plan_compliance_query(")


def test_a_keyword_route_records_its_plan_on_both_entry_points():
    import src.agents.orchestrator as O

    src = inspect.getsource(O)
    assert src.count('f"a {route_intent} request: handled by the orchestrator\'s own loop"') == 2


# -- a question about work orders is a question ---------------------------------------------------

@pytest.mark.parametrize("question", [
    "For each statutory inspection due in the next 60 days, is a work order booked for that inspection?",
    "Do any of them have open work orders?",
    "Is a work order raised for any of them?",
    "Who is the vendor on each work order?",
    "When is the work order for lift 2 due?",
    "List all open work orders by building.",
])
def test_a_question_about_work_orders_reaches_the_router(question):
    assert classify_route_intent(" ".join(question.lower().split()), default_session_state()) == ROUTE_GENERAL


@pytest.mark.parametrize("request_", [
    "raise a work order for the lift in core B",
    "the lift is broken, create a work order",
    "can you create a work order for the boiler?",
    "do we need to raise a work order for the leak?",
    "please log a work order: AHU is noisy",
    "work order for the leaking tap on level 3",
])
def test_a_request_for_a_work_order_is_still_one(request_):
    assert classify_route_intent(" ".join(request_.lower().split()), default_session_state()) == ROUTE_WO_INTAKE


def test_the_path_before_the_router_never_reads_an_earlier_turns_answer_as_fact(orch):
    """The previous answer is set when a turn is routed. The compliance path that answers before
    that would otherwise carry whatever an earlier turn on the same connection left behind."""
    mod = orch["mod"]
    token = mod._turn_previous.set("STALE: 99 vendors were blocked last week.")
    try:
        asyncio.run(orch["o"]._answer_over_analyst("Which certificates have lapsed?", DRAFT, _calls(_card()),
                                                   analyst="compliance", carries_previous=False))
        assert "STALE" not in orch["seen"][-1][1]
        assert mod._turn_previous.get() == "STALE: 99 vendors were blocked last week."     # put back as it was
        asyncio.run(orch["o"]._answer_over_analyst("Which of those are at Kingsway House?", DRAFT, _calls(_card()),
                                                   analyst="compliance"))
        assert "STALE" in orch["seen"][-1][1]                # a routed turn's own previous answer is a fact
    finally:
        mod._turn_previous.reset(token)


# -- the text does not repeat what the cards show -------------------------------------------------

def test_in_the_chat_the_writer_is_told_what_the_cards_already_show(orch):
    """turn-548da79bee2642eb (12 Nov 2026): the answer listed the four lapsed certificates as a table,
    above cards that drew the same four as a bar chart and a table - and the chat drew a second bar
    chart from the answer's own table."""
    async def on_zone(key, value):
        return None

    card = _card()
    card["overdue"] = {"buildings": [{"label": "Kingsway House · EICR", "days": 103}]}
    asyncio.run(orch["o"]._answer_over_analyst("Which certificates have lapsed?", DRAFT, _calls(card), on_zone=on_zone,
                                               analyst="compliance"))
    prompt = orch["seen"][-1][1]
    assert "SHOWN UNDER YOUR ANSWER" in prompt
    assert "a table of every certificate in scope" in prompt and "a bar chart of the days each item is overdue" in prompt
    assert "the headline counts as tiles" in prompt and "the recommended actions as cards" in prompt
    assert "no table and no list of those rows" in prompt
    assert prompt.index("SHOWN UNDER YOUR ANSWER") < prompt.index("DRAFT (not evidence)")


def test_only_the_zones_the_card_has_are_named(orch):
    O = type(orch["o"])
    assert O._card_shows({"kpis": [{"label": "Lapsed", "count": 1}], "certificates": [], "actions": None}) == \
        "- the headline counts as tiles"
    assert O._card_shows(None) == "" and O._card_shows({}) == ""


def test_a_caller_with_no_cards_gets_the_whole_answer_in_the_text(orch):
    """REST - a scheduled question, an emailed answer, the scenario runs - draws no cards."""
    asyncio.run(orch["o"]._answer_over_analyst("Which certificates have lapsed?", DRAFT, _calls(_card()), analyst="compliance"))
    assert "SHOWN UNDER YOUR ANSWER" not in orch["seen"][-1][1]


def test_an_answer_with_no_cards_is_written_as_before():
    from src.agents import final_answer as fa

    assert "SHOWN UNDER YOUR ANSWER" not in fa._prompt("q", "draft", [], {})
    assert "SHOWN UNDER YOUR ANSWER" in fa._prompt("q", "draft", [], {}, shown="- the headline counts as tiles")
