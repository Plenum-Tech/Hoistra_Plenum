"""A work-order question about one building reads that building, and says what its dates cover.

2 Nov 2026, "how work-order deviations at Bishopsgate Tower this mont" (checked read-only in the
activity log): the model called list_maintenance_decisions with {"state": "Deviation", "group_by":
"building"} and no building, so the tool read the whole portfolio - "Decisions owed 38" (25 to raise,
6 deviating, 5 awaiting approval, 2 blocked) appeared under a Bishopsgate answer. The thread's
building only reached the tools on a follow-up, never from the question itself. And the answer called
the six open deviations "this month's" though the list has no date filter and they fell due 29 Sep -
3 Oct.
"""
from __future__ import annotations

import asyncio
import inspect
from datetime import date

import pytest

from src.agents import thread_scope as ts
from src.agents import wo_engine_agent as wo
from src.agents.orchestrator import DeepAgentOrchestrator as O

LIST = getattr(wo.list_maintenance_decisions, "coroutine", wo.list_maintenance_decisions)


def _scope(q, register):
    o = O.__new__(O)
    asked = []

    async def known(self, spec):
        phrase = spec["filters"][0]["value"]
        asked.append(phrase)
        return [n for n in register if n.lower().replace(" ", "") in phrase.lower().replace(" ", "")
                or phrase.lower().replace(" ", "") in n.lower().replace(" ", "")]

    o._known_names = known.__get__(o)
    return asyncio.run(o._question_scope(q)), asked


REGISTER = ["Bishopsgate Tower", "Manchester Town Hall", "sams tower"]


def test_the_building_a_question_names_scopes_its_tools():
    s, asked = _scope("how work-order deviations at Bishopsgate Tower this mont", REGISTER)
    assert s["building_name"] == "Bishopsgate Tower" and asked == ["Bishopsgate Tower"]
    assert s["period"]["key"] == "this_month"
    assert ts.place_candidates("how work-order deviations at Bishopsgate Tower this mont") == ["Bishopsgate Tower"]


def test_no_building_or_several_set_no_building():
    s, _ = _scope("Which work orders breached SLA this week?", REGISTER)
    assert not (s or {}).get("building_name")
    s, _ = _scope("Compare deviations at Bishopsgate Tower and at Manchester Town Hall", REGISTER)
    assert not (s or {}).get("building_name")
    s, _ = _scope("open work orders for Apex Mechanical", REGISTER)  # a vendor, not on the register
    assert not (s or {}).get("building_name")


def test_period_words_survive_a_typo():
    t = date(2026, 11, 2)
    assert ts.parse_period("deviations this mont", today=t)["key"] == "this_month"
    assert ts.parse_period("what lapsed last mont?", today=t)["key"] == "last_month"
    assert ts.parse_period("deviations in this monthly report", today=t) is None  # "monthly" is not a period


def test_every_turn_sets_its_scope_before_any_route_runs():
    # 15:04 the same question still read every building: the chat ran the work-order engine
    # (_invoke_phase2_engine), which never built the stateful input where the scope was set.
    for fn in (O._stream_turn, O._run_stateful_inner):
        src = inspect.getsource(fn)
        assert ("await self._set_turn_scope(sid, user_message)" in src
                or "await self._set_turn_scope(session_id, user_message)" in src), fn.__name__
    assert "await self._set_turn_scope(session_id, user_message, keep=True)" in inspect.getsource(O._invoke_phase2_engine)


def test_the_turn_scope_is_the_threads_on_a_follow_up_and_the_questions_otherwise(monkeypatch):
    from src.services import chat_threads

    async def working_set(sid):
        return {"building_name": "Manchester Town Hall"}

    monkeypatch.setattr(chat_threads, "working_set", working_set)
    o = O.__new__(O)

    async def q_scope(self, question):
        return {"building_name": "Bishopsgate Tower"}

    o._question_scope = q_scope.__get__(o)

    async def run(question, keep=False, preset=None):
        ts.active_scope.set(preset)
        await o._set_turn_scope("s1", question, keep=keep)
        return ts.active_scope.get()

    assert asyncio.run(run("work orders at Bishopsgate Tower"))["building_name"] == "Bishopsgate Tower"
    assert asyncio.run(run("and which of those are overdue?"))["building_name"] == "Manchester Town Hall"
    # an engine dispatched inside a turn keeps the scope the turn set
    assert asyncio.run(run("x", keep=True, preset={"building_name": "RT5, Crossrail"}))["building_name"] == "RT5, Crossrail"


def _call(monkeypatch, building_id, decisions):
    sent = {}

    class _Resp:
        def json(self):
            return {"ok": True, "total": len(decisions), "by_state": {"Deviation": len(decisions)}, "decisions": decisions}

    async def request(method, base, path, **kw):
        sent.update(kw.get("params") or {})
        return _Resp()

    async def building(bid, *, use_thread=False):
        return building_id, None

    monkeypatch.setattr(wo, "_request", request)
    monkeypatch.setattr(wo, "_maintenance_building", building)
    monkeypatch.setattr(wo, "_today", lambda: date(2026, 11, 2))
    return asyncio.run(LIST(state="Deviation")), sent


def test_the_decisions_say_they_are_not_filtered_by_date_and_count_what_falls_when(monkeypatch):
    rows = [{"state": "Deviation", "due": "2026-09-29T09:00:00"}, {"state": "Deviation", "due": "2026-10-03"},
            {"state": "Deviation", "due": "2026-11-20"}, {"state": "Deviation", "due": None}]
    out, sent = _call(monkeypatch, "b-301", rows)
    note = out["dates_note"]
    assert "not filtered by date" in note
    assert "1 due in November 2026" in note and "2 due before November 2026" in note
    assert "1 with no due date" in note
    assert out["scope"] == "one building" and sent["building_id"] == "b-301"


def test_without_a_building_the_result_says_it_is_every_building(monkeypatch):
    out, sent = _call(monkeypatch, None, [])
    assert out["scope"] == "every building in the company" and "building_id" not in sent
    assert "not filtered by date" in out["dates_note"]


# Pre-push review (3 Nov 2026): a question that compares a period with another ("this month vs all
# time") must not make "this month" every tool's default - the "all time" read would get it too.
@pytest.mark.parametrize("q", [
    "Open work orders this month vs all time",
    "How many work orders this month compared with last month?",
    "Work orders raised this month versus overall",
    "Open work orders at Bishopsgate this month and in total",
])
def test_a_question_comparing_periods_sets_no_default_period(q):
    from src.agents import thread_scope

    assert "period" not in (thread_scope.question_scope(q) or {})


def test_a_question_with_one_period_still_sets_it():
    from src.agents import thread_scope

    assert (thread_scope.question_scope("Open work orders this month") or {}).get("period")
