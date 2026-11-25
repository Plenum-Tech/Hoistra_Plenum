"""The planner asks for statutory decisions by filter, and a building-narrowed list is not called
unfiltered (25 Nov 2026).

list_maintenance_decisions now takes `statutory` (its own test file:
test_maintenance_decision_filters_narrow.py). The plan prompt still told the planner that
statutory means "source Compliance", a filter the route reads differently - so the plan asked for
the wrong thing. And verify_claims' "its filter did nothing" check compared count with total; on a
list narrowed to one building the route's total is that building's own, so a correct answer quoting
it was sent back for a rewrite.
"""
from __future__ import annotations

from src.agents import planner

Q = "Which maintenance decisions are blocked at Bishopsgate Tower?"
ANSWER = "12 decisions at Bishopsgate Tower are blocked."


def _results(args: dict) -> dict:
    out = {"count": 12, "total": 12, "items": [{"state": "Blocked"}] * 12}
    return {"s1": {"ok": True, "output": out,
                   "tool_calls": [{"tool": "list_maintenance_decisions", "input": args, "output": out}]}}


def test_the_planner_is_told_statutory_is_a_filter_of_its_own():
    assert '"statutory": true' in planner.PLAN_PROMPT
    assert "statutory = source Compliance" not in planner.PLAN_PROMPT


def test_a_list_narrowed_to_one_building_is_not_called_unfiltered():
    found = planner.verify_claims(ANSWER, Q, _results({"building_id": "b-1"}))
    assert not [c for c in found if "filter did nothing" in c]


def test_a_list_that_was_not_narrowed_still_is():
    found = planner.verify_claims(ANSWER, Q, _results({}))
    assert [c for c in found if "filter did nothing" in c]
