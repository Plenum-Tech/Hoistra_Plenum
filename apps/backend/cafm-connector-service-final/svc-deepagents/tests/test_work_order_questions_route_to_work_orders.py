"""A question about work orders goes to the work-order engine, however it is written (2 Nov 2026).

Feedback: "a request for work-order deviations must use the work-order/operations model and tools -
not compliance tools". Checked offline with the model's choice forced to "compliance":
- "Show work-order deviations at Bishopsgate Tower this month" (the feedback's own wording, with a
  hyphen) matched no keyword, so whatever the model said stood;
- "Show work order WO00000359 status and SLA" named work orders AND an SLA, so no engine was certain
  and the model's "compliance" stood, though the question names one work order by its code;
- "Which work orders are linked to lapsed certificates?" was answered by the compliance shortcut,
  which runs before routing, from the certificate register alone.
"""
from __future__ import annotations

import asyncio

import pytest

from src.agents.agent_router import agent_for_question
from src.agents.orchestrator import DeepAgentOrchestrator as O
from src.agents.phase2_intents import certain_engine, match_phase2_agent


@pytest.mark.parametrize("q", [
    "Show work-order deviations at Bishopsgate Tower this month",
    "show work_order deviations",
    "Which Work-Orders breached SLA?",
    "What are the work order deviations?",
    "Show PPM deviations by building",
])
def test_work_orders_however_written_are_the_work_order_engines(q):
    assert certain_engine(q) == "wo_engine", q
    assert agent_for_question(q, "compliance") == "wo_engine", q
    assert match_phase2_agent(q) == "wo_engine", q


@pytest.mark.parametrize("q", [
    "Show work order WO00000359 status and SLA",
    "What is the SLA on WO-1184?",
    "status of wo 00000359",
])
def test_a_work_order_named_by_its_code_is_the_work_order_engines(q):
    assert certain_engine(q) == "wo_engine", q
    assert agent_for_question(q, "compliance") == "wo_engine", q


@pytest.mark.parametrize("q,engine", [
    ("Which certificates expire next month?", "compliance"),
    ("Which vendors are blocked?", "compliance"),
    ("Which certificates at B-301 have lapsed?", "compliance"),
    ("What is the energy use at Bishopsgate Tower?", "energy_intelligence"),
])
def test_other_questions_keep_their_engine(q, engine):
    assert certain_engine(q) == engine, q


@pytest.fixture(autouse=True)
def no_database_or_services(monkeypatch):
    """Nothing here may reach a database or a service: the test container's DB_URL is a live one."""
    from src import database
    from src.agents import activity_log

    def refuse(*a, **k):
        raise AssertionError("a routing test tried to reach the database")

    monkeypatch.setattr(database, "AsyncSessionLocal", refuse)
    monkeypatch.setattr(database, "_get_session_factory", refuse, raising=False)
    monkeypatch.setattr(activity_log, "fire", lambda **k: None)


def _shortcut(q):
    return asyncio.run(O._maybe_compliance_posture_shortcut(object.__new__(O), q, "s-test"))


@pytest.mark.parametrize("q", [
    "Which work orders are linked to lapsed certificates?",
    "Show work-order deviations at Bishopsgate Tower this month",
    "Are any PPMs overdue on assets with expired certificates?",
    "Is WO00000359 covered by a valid certificate?",
])
def test_the_compliance_shortcut_stands_down_for_a_question_about_work_orders(q, monkeypatch):
    import src.agents.compliance_engine_agent as cea

    async def boom(*a, **k):
        raise AssertionError("the certificate register was read for a work-order question")

    monkeypatch.setattr(cea, "get_compliance_saved_space_summary", boom, raising=False)
    monkeypatch.setattr(O, "_fetch_compliance_table_direct", boom)
    assert _shortcut(q) is None


def test_the_compliance_shortcut_still_takes_a_certificate_question():
    from src.agents.orchestrator import _NAMES_WORK_ORDERS

    for q in ("which certificates expire next month?", "fire alarm servicing certificate lapsed at bishopsgate?",
              "which buildings have overdue certificates?"):
        assert not _NAMES_WORK_ORDERS.search(q), q
