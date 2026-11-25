"""list_maintenance_decisions narrows by what the plan asks, or says it cannot (25 Nov 2026).

A daily planned run failed its verify step every day from 16 to 23 Nov:

    The answer quotes 49 as the narrowed count, but step s1 returned every row (49 of 49): its
    filter did nothing.
    The answer calls 'decision'/'statutory' unavailable, but step s1 returned 46 rows with
    state/trigger / statutory populated.

Step s1 was list_maintenance_decisions. "Which decisions are statutory" had no argument to go in
(the route marks every row `statutory` but filters only on state and source), and the planner's
schema check refuses an argument a tool does not take, so the step listed everything. And the
route compares `state` and `source` exactly: "blocked" was a 422, "compliance" an empty list.

The fake route below filters as svc-work-order-management's /api/maintenance/decisions does.
"""
from __future__ import annotations

import httpx
import pytest

from src.agents import planner
from src.agents import wo_engine_agent as wo

STATES = ("Blocked", "Deviation", "Awaiting approval", "To raise")

DECISIONS = [
    {"work_order": "WO-1", "state": "Blocked", "source": "Compliance", "statutory": True,
     "building": "Bishopsgate Tower", "vendor": "Apex", "estimated_cost": 100.0, "due": "2026-11-01"},
    {"work_order": "WO-2", "state": "Blocked", "source": "Vendors", "statutory": False,
     "building": "Bishopsgate Tower", "vendor": "Brite", "estimated_cost": 50.0, "due": "2026-11-02"},
    {"work_order": "WO-3", "state": "Deviation", "source": "Maintenance", "statutory": True,
     "building": "Ashgrove Court", "vendor": "Apex", "estimated_cost": None, "due": "2026-10-20"},
    {"work_order": None, "state": "To raise", "source": "Energy", "statutory": False,
     "building": "Ashgrove Court", "vendor": None, "estimated_cost": 900.0, "due": None},
    {"work_order": "WO-5", "state": "Awaiting approval", "source": "Compliance", "statutory": True,
     "building": "Ashgrove Court", "vendor": "Apex", "estimated_cost": 20.0, "due": "2026-12-01"},
    {"work_order": "WO-6", "state": "Awaiting approval", "source": "Assets", "statutory": False,
     "building": "Bishopsgate Tower", "vendor": "Brite", "estimated_cost": 10.0, "due": "2026-12-03"},
]


class _Resp:
    def __init__(self, body):
        self._body = body

    def json(self):
        return self._body


class _Route:
    def __init__(self):
        self.calls: list[dict] = []

    async def __call__(self, method, base, path, **kwargs):
        assert path == "/api/maintenance/decisions", path
        params = dict(kwargs.get("params") or {})
        self.calls.append(params)
        state, source = params.get("state"), params.get("source")
        if state is not None and state not in STATES:      # Query(pattern=...) on the route
            req = httpx.Request("GET", "http://wo" + path)
            raise httpx.HTTPStatusError("422", request=req, response=httpx.Response(422, request=req))
        kept = [d for d in DECISIONS
                if (state is None or d["state"] == state) and (source is None or d["source"] == source)]
        page = [dict(d) for d in kept[: int(params.get("limit") or 200)]]
        return _Resp({
            "ok": True, "count": len(page), "matched": len(kept), "total": len(DECISIONS),
            "total_is_capped": False, "filtered": state is not None or source is not None,
            "by_state": {s: sum(1 for d in DECISIONS if d["state"] == s) for s in STATES},
            "by_source": {},
            "group_by": params.get("group_by"), "groups": None, "decisions": page,
        })


@pytest.fixture
def route(monkeypatch):
    r = _Route()
    monkeypatch.setattr(wo, "_request", r)
    return r


async def _list(**args):
    return await wo.list_maintenance_decisions.ainvoke(args)


async def test_statutory_lists_only_the_statutory_decisions(route):
    out = await _list(statutory=True)
    assert [d["work_order"] for d in out["decisions"]] == ["WO-1", "WO-3", "WO-5"]
    assert out["count"] == 3 and out["matched"] == 3 and out["total"] == 6
    assert out["filtered"] is True


async def test_not_statutory_lists_the_others(route):
    out = await _list(statutory=False)
    assert [d["work_order"] for d in out["decisions"]] == ["WO-2", None, "WO-6"]


async def test_the_planner_may_pass_statutory(route):
    # planner.validate refuses an argument a tool does not take; this is the check it runs.
    assert planner.arg_problems({"statutory": True}, wo.list_maintenance_decisions.args) == []


async def test_statutory_and_a_state_narrow_by_both(route):
    out = await _list(statutory=True, state="Blocked")
    assert [d["work_order"] for d in out["decisions"]] == ["WO-1"]
    assert route.calls[-1]["state"] == "Blocked"


async def test_statutory_reads_past_a_small_limit_before_narrowing(route):
    # The route marks only the page it returns; narrowing a 2-row page would miss WO-5.
    out = await _list(statutory=True, limit=2)
    assert route.calls[-1]["limit"] >= len(DECISIONS)
    assert out["matched"] == 3 and [d["work_order"] for d in out["decisions"]] == ["WO-1", "WO-3"]


@pytest.mark.parametrize("typed,canon", [
    ("blocked", "Blocked"), ("BLOCKED", "Blocked"), ("deviations", "Deviation"),
    ("awaiting_approval", "Awaiting approval"), ("to raise", "To raise"),
])
async def test_a_state_however_written_narrows(route, typed, canon):
    out = await _list(state=typed)
    assert route.calls[-1]["state"] == canon
    assert out["decisions"] and all(d["state"] == canon for d in out["decisions"])


@pytest.mark.parametrize("typed,canon", [
    ("compliance", "Compliance"), ("vendor", "Vendors"), ("ASSETS", "Assets"),
])
async def test_a_source_however_written_narrows_instead_of_emptying(route, typed, canon):
    out = await _list(source=typed)
    assert route.calls[-1]["source"] == canon
    assert out["decisions"] and all(d["source"] == canon for d in out["decisions"])


async def test_a_state_that_is_none_of_the_four_is_refused_by_name(route):
    out = await _list(state="urgent")
    assert route.calls == []
    assert out.get("ok") is False
    assert all(s in out["error"] for s in STATES)


async def test_group_by_with_statutory_groups_only_the_statutory_rows(route):
    out = await _list(statutory=True, group_by="building")
    assert {g["key"]: g["count"] for g in out["groups"]} == {"Ashgrove Court": 2, "Bishopsgate Tower": 1}


async def test_the_verify_step_no_longer_flags_a_statutory_answer(route):
    # The 16-23 Nov failure: a narrowing question, and a step that returned every row.
    out = await _list(statutory=True)
    results = {"s1": {"ok": True, "output": out}}
    answer = f"{out['matched']} statutory decisions are open."
    assert planner.verify_claims(answer, "Which statutory decisions are open?", results) == []
