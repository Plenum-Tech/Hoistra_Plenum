"""The work-order status shortcut answers only about a work order the question or this session names (29 Oct 2026).

The shortcut answers "status / progress / completed ..." questions about a work order without the
agents. It took any message with "work order" and a status word in it, and with no 14-digit WO id in
the message it used this session's last work order - or, with none, the last work order ANY session
of ANY company had touched in this process. So "how many work orders were completed at JASMEET?"
was answered with one unrelated work order's status, and "what is the status of WO-1042?" (the
register's wo_code, not the 14-digit id) with whichever work order the session had open.

Now: a 14-digit id in the message is that work order; two ids, a code the id pattern does not know,
or any word that names something else (a building, a count, a description) stand down to the agents;
a plain follow-up ("what's the status of the work order?") is about this session's work order only;
another session's work order is never used.
"""
from __future__ import annotations

import asyncio

import pytest

from src.agents import wo_engine_agent as wea
from src.agents.orchestrator import DeepAgentOrchestrator as O

MINE = "WO-20261029101500000001"
OTHER_COMPANYS = "WO-20261029093000000777"


class _Track:
    def __init__(self):
        self.calls = []

    async def ainvoke(self, args):
        self.calls.append(args["work_order_id"])
        return {"found": True, "work_order_id": args["work_order_id"],
                "formatted_summary": "Status of " + args["work_order_id"]}


@pytest.fixture
def track(monkeypatch):
    t = _Track()
    monkeypatch.setattr(wea, "get_work_order_status_track", t)
    monkeypatch.setattr(wea, "_LAST_WORK_ORDER_ID", OTHER_COMPANYS)   # another session, another company
    monkeypatch.setattr(wea, "_SESSION_WORK_ORDER_MAP", {})
    return t


def _ask(msg, session="s-mine"):
    return asyncio.run(O.__new__(O)._maybe_status_query_shortcut(msg, session))


class TestItStandsDown:
    @pytest.mark.parametrize("msg", [
        "how many work orders were completed at JASMEET?",
        "how many work orders are in progress at Jasmeet Building",
        "list the work orders on hold",
        "which work orders are pending approval?",
        "what is the status of the boiler work order",
        "JASMEET work order status",
    ])
    def test_a_question_about_other_work_orders_goes_to_the_agents(self, track, msg, monkeypatch):
        monkeypatch.setattr(wea, "_SESSION_WORK_ORDER_MAP", {"s-mine": MINE})
        assert _ask(msg) is None
        assert track.calls == []

    @pytest.mark.parametrize("msg", [
        "what is the status of WO-1042?",
        "status of work order 1042",
        "track wo 1042",
    ])
    def test_a_work_order_code_the_id_pattern_does_not_know_goes_to_the_agents(self, track, msg, monkeypatch):
        # WO-1042 is the register's wo_code; answering with the session's work order named the
        # wrong one.
        monkeypatch.setattr(wea, "_SESSION_WORK_ORDER_MAP", {"s-mine": MINE})
        assert _ask(msg) is None
        assert track.calls == []

    def test_two_work_orders_are_compared_by_the_agents(self, track):
        assert _ask(f"status of {MINE} and {OTHER_COMPANYS}") is None
        assert track.calls == []

    def test_another_sessions_work_order_is_never_used(self, track):
        # Nothing in this session: the last work order of the process belongs to someone else.
        assert _ask("what's the status of the work order?") is None
        assert track.calls == []


class TestItAnswers:
    def test_the_work_order_the_question_names(self, track):
        out = _ask(f"what is the status of {MINE.lower()}?")
        assert track.calls == [MINE] and out["answer"] == "Status of " + MINE

    @pytest.mark.parametrize("msg", [
        "what's the status of the work order?",
        "work order status",
        "who approved the work order?",
        "is the work order completed yet?",
        "where is my work order in the approval chain?",
    ])
    def test_a_follow_up_about_this_sessions_work_order(self, track, msg, monkeypatch):
        monkeypatch.setattr(wea, "_SESSION_WORK_ORDER_MAP", {"s-mine": MINE, "s-other": OTHER_COMPANYS})
        out = _ask(msg)
        assert track.calls == [MINE] and out["success"] is True
