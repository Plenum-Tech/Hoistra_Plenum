"""A plan's calls: checked before they run, linked by their values, held to one scope, retried only as
they were (Workstream A2, 11 Nov 2026).

These are the acceptance tests of each A2 area:
  * multi-read planning - arguments are checked against the tool's schema when the plan is made, and a
    question across three areas is planned as three or more calls;
  * dependency order - steps of one batch run at once (their spans overlap) and what runs inside each
    hangs under THAT step, even two steps on the same engine;
  * context passing - a later call's input names the earlier output each value came from;
  * scope - one scope for the plan; a call outside the caller's access, or off the conversation's
    building or period, is not run;
  * retries - only a temporary error is retried, with the same request, each attempt a span; a refusal
    or a validation error never is.
"""
from __future__ import annotations

import asyncio
import uuid

import httpx
import pytest

from src import http_client
from src.agents import evidence as ev
from src.agents import plan_scope, planner, thread_scope, trace
from src.http_client import caller_organization_id
from src.services.principal import Principal, caller_principal

SCHEMA = {"building_name": {"type": "string"}, "vendor_ids": {"type": "array", "items": {"type": "string"}},
          "period": {"type": "string", "enum": ["last_month", "this_month", "this_year", "last_90_days"]},
          "limit": {"type": "integer"}}
TOOLS = {"list_blocked_vendors": "blocked vendors", "work_order_blockers": "blockers", "get_cost_savings": "savings"}
SCHEMAS = {name: SCHEMA for name in TOOLS}


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
    if hasattr(trace, "_current_step"):
        trace._current_step.set(None)
    thread_scope.active_scope.set(None)
    trace.on_activity({"agent": "orchestrator", "stage": "turn", "direction": "input", "summary": "q",
                       "payload": {"message": "q"}})
    yield trace.current()
    # The caller is a context variable: left set, it leaks into every later test in the run.
    caller_principal.reset(tp)
    caller_organization_id.reset(to)
    trace._turn.set(None)


# -- multi-read planning ------------------------------------------------------------------------

@pytest.mark.parametrize("args,problem", [
    ({"building": "Bishopsgate"}, "takes no argument building"),
    ({"limit": "ten"}, "limit must be integer"),
    ({"period": "last_week"}, "period must be one of"),
])
def test_a_steps_arguments_are_checked_against_the_tools_schema(args, problem):
    plan, why = planner.validate({"steps": [{"id": "s1", "kind": "tool", "target": "get_cost_savings", "ask": "x",
                                             "args": args}]}, TOOLS, SCHEMAS)
    assert plan is None and problem in why


def test_right_arguments_and_one_value_for_a_list_pass():
    plan, why = planner.validate({"steps": [{"id": "s1", "kind": "tool", "target": "get_cost_savings", "ask": "x",
                                             "args": {"building_name": "B", "vendor_ids": "V-1", "period": "last_month"}}]},
                                 TOOLS, SCHEMAS)
    assert why is None and plan["steps"][0]["args"]["period"] == "last_month"


def test_a_value_from_an_earlier_step_makes_the_step_depend_on_it():
    plan, why = planner.validate({"steps": [
        {"id": "s1", "kind": "tool", "target": "list_blocked_vendors", "ask": "blocked vendors", "args": {}},
        {"id": "s2", "kind": "tool", "target": "work_order_blockers", "ask": "their work orders",
         "args": {"vendor_ids": "$s1.vendor_id"}}]}, TOOLS, SCHEMAS)
    assert why is None and plan["steps"][1]["depends_on"] == ["s1"]
    bad, why = planner.validate({"steps": [
        {"id": "s1", "kind": "tool", "target": "work_order_blockers", "ask": "x", "args": {"vendor_ids": "$s2.vendor_id"}}]},
        TOOLS, SCHEMAS)
    assert bad is None and "not an earlier step" in why


def _llm(*replies):
    seen = []
    queue = list(replies)

    async def llm(system, user, role):
        seen.append(user)
        return queue.pop(0)

    llm.seen = seen
    return llm


ONE_STEP = '{"steps": [{"id": "s1", "kind": "tool", "target": "get_cost_savings", "ask": "everything", "args": {}}]}'
THREE_STEPS = ('{"steps": [{"id": "s1", "kind": "engine", "target": "energy_intelligence", "ask": "energy at B"},'
               '{"id": "s2", "kind": "engine", "target": "contract_performance", "ask": "vendors at B"},'
               '{"id": "s3", "kind": "engine", "target": "compliance", "ask": "certificates at B"}]}')


def test_a_question_across_three_areas_is_planned_as_three_calls_or_more():
    q = "What did Bishopsgate Tower use in energy last month, which of its vendors missed SLA, and which certificates lapsed?"
    llm = _llm(ONE_STEP, THREE_STEPS)
    plan, why = asyncio.run(planner.make_plan(q, context="", tools=TOOLS, llm=llm, schemas=SCHEMAS))
    assert why is None and len(plan["steps"]) >= 3
    assert "THE QUESTION COVERS 3 AREAS" in llm.seen[1]
    assert set(plan["areas"]) == {"energy_intelligence", "contract_performance", "compliance"}


def test_a_refused_plan_gets_one_corrected_attempt_and_no_more():
    bad = '{"steps": [{"id": "s1", "kind": "tool", "target": "get_cost_savings", "ask": "x", "args": {"building": "B"}}]}'
    good = '{"steps": [{"id": "s1", "kind": "tool", "target": "get_cost_savings", "ask": "x", "args": {"building_name": "B"}}]}'
    llm = _llm(bad, good)
    plan, why = asyncio.run(planner.make_plan("savings at B?", context="", tools=TOOLS, llm=llm, schemas=SCHEMAS))
    assert plan is not None and "YOUR PREVIOUS PLAN WAS REFUSED" in llm.seen[1] and "takes no argument building" in llm.seen[1]
    llm = _llm(bad, bad)
    plan, why = asyncio.run(planner.make_plan("savings at B?", context="", tools=TOOLS, llm=llm, schemas=SCHEMAS))
    assert plan is None and len(llm.seen) == 2


# -- dependency order ---------------------------------------------------------------------------

def _plan(*steps):
    return planner.validate({"steps": list(steps)}, TOOLS)[0]


def _descends(t, span, ancestor_id):
    by_id = {s.id: s for s in t.spans}
    while span is not None and span.parent_id:
        if span.parent_id == ancestor_id:
            return True
        span = by_id.get(span.parent_id)
    return False


def test_parallel_steps_overlap_and_each_keeps_its_own_calls(traced):
    """Two wo_engine steps at once used to share one engine span; the second step's tool calls
    landed under the first."""
    plan = _plan({"id": "s1", "kind": "engine", "target": "wo_engine", "ask": "overdue at A"},
                 {"id": "s2", "kind": "engine", "target": "wo_engine", "ask": "overdue at B"})

    async def run_engine(engine, ask, on_ev):
        rid = "run-" + ask[-1]
        trace.on_tool_open(rid, "list_work_orders", {"building": ask[-1]}, group=engine)
        await asyncio.sleep(0.05)   # both steps are inside their tool at once
        trace.on_tool_close(rid, {"rows": [{"code": "WO-" + ask[-1]}]})
        return "ok", [{"tool": "list_work_orders", "output": {"rows": [{"code": "WO-" + ask[-1]}]}}]

    async def no_tool(*a):
        raise AssertionError

    asyncio.run(planner.execute(plan, run_engine=run_engine, run_tool=no_tool))
    t = traced
    steps = {s.name.split(":")[0]: s for s in t.spans if s.kind == "step"}
    tools = [s for s in t.spans if s.kind == "tool" and s.name == "list_work_orders"]
    assert len(tools) == 2
    for tool in tools:
        owner = "s1" if tool.input["building"] == "A" else "s2"
        other = "s2" if owner == "s1" else "s1"
        assert _descends(t, tool, steps[owner].id) and not _descends(t, tool, steps[other].id)
    a, b = steps["s1"], steps["s2"]
    assert a.started_at < b.ended_at and b.started_at < a.ended_at   # the spans overlap in time


# -- context passing ----------------------------------------------------------------------------

def test_a_later_calls_input_names_the_earlier_output_each_value_came_from(traced):
    plan, _ = planner.validate({"steps": [
        {"id": "s1", "kind": "tool", "target": "list_blocked_vendors", "ask": "blocked vendors", "args": {"building_name": "B"}},
        {"id": "s2", "kind": "tool", "target": "work_order_blockers", "ask": "their work orders",
         "args": {"vendor_ids": "$s1.vendor_id"}}]}, TOOLS, SCHEMAS)
    calls = []

    async def run_tool(name, args):
        calls.append((name, args))
        if name == "list_blocked_vendors":
            return {"rows": [{"vendor_id": "V-1", "name": "Acme"}, {"vendor_id": "V-2", "name": "Zed"}]}
        return {"rows": [{"code": "WO-9", "vendor_id": "V-1"}]}

    asyncio.run(planner.execute(plan, run_engine=None, run_tool=run_tool))
    assert calls[1] == ("work_order_blockers", {"vendor_ids": ["V-1", "V-2"]})
    s2 = next(s for s in traced.spans if s.kind == "step" and s.name.startswith("s2"))
    assert s2.input["bindings"] == [{"param": "vendor_ids", "from_step": "s1", "field": "vendor_id", "count": 2,
                                     "values": ["V-1", "V-2"]}]


def test_nothing_to_pass_is_an_answer_and_a_failed_source_is_a_failure():
    out, b, rec = planner.bind({"vendor_ids": "$s1.vendor_id"}, {"s1": {"ok": True, "output": {"rows": []}}})
    assert rec["status"] == ev.ZERO and rec["ok"] is True
    out, b, rec = planner.bind({"vendor_ids": "$s1.vendor_id"}, {"s1": {"ok": False, "output": None}})
    assert rec["status"] == ev.FAILED


# -- one scope for the plan ---------------------------------------------------------------------

def _scope(**kw):
    base = dict(organization_id="org-1", role="manager",
                allowed=[{"id": "b1", "name": "Bishopsgate Tower", "building_code": "B-301"}])
    base.update(kw)
    return plan_scope.PlanScope(**base)


@pytest.mark.parametrize("args,status,rule", [
    ({"organization_id": "org-2"}, ev.DENIED, "company"),
    ({"building_id": "b9"}, ev.DENIED, "buildings_allowed"),
    ({"building_name": "Riverside Court"}, ev.DENIED, "buildings_allowed"),
])
def test_a_call_outside_the_callers_access_is_refused(args, status, rule):
    rec = _scope().check("get_cost_savings", args)
    assert rec["status"] == status and rec["scope_rule"] == rule


def test_a_call_inside_the_callers_access_passes():
    assert _scope().check("get_cost_savings", {"building_name": "Bishopsgate Tower", "building_id": "b1"}) is None
    assert _scope().check("get_cost_savings", {"building_name": "B-301"}) is None


def test_a_call_off_the_conversations_building_or_period_is_not_run():
    s = _scope(allowed=None, building={"building_name": "Bishopsgate Tower"},
               period={"key": "last_month", "label": "October 2026", "from": "2026-10-01", "to": "2026-11-01"})
    assert s.check("get_cost_savings", {"building_name": "Kingsway House"})["scope_rule"] == "conversation_building"
    assert s.check("get_cost_savings", {"period": "this_year"})["scope_rule"] == "conversation_period"
    assert s.check("get_cost_savings", {"building_name": "Bishopsgate Tower", "period": "last_month"}) is None


def test_the_plan_runs_under_one_scope_and_a_refused_call_never_goes_out(traced):
    plan, _ = planner.validate({"steps": [
        {"id": "s1", "kind": "tool", "target": "get_cost_savings", "ask": "savings at Riverside",
         "args": {"building_name": "Riverside Court"}}]}, TOOLS, SCHEMAS)
    calls = []

    async def run_tool(name, args):
        calls.append(name)
        return {"rows": []}

    res = asyncio.run(planner.execute(plan, run_engine=None, run_tool=run_tool, scope=_scope()))
    assert calls == [] and res["s1"]["ok"] is False and res["s1"]["status"] == ev.DENIED
    step = next(s for s in traced.spans if s.kind == "step")
    assert step.input["scope"]["buildings_allowed"] == 1


def test_the_scope_is_built_from_the_caller_and_the_conversation():
    bid = uuid.uuid4()
    p = Principal(user_id=uuid.uuid4(), email="m@example.com", organization_id=uuid.uuid4(), role="manager",
                  can_ingest=False, building_ids=(bid,), buildings=({"id": str(bid), "name": "Bishopsgate Tower"},))
    tp, to = caller_principal.set(p), caller_organization_id.set(str(p.organization_id))
    thread_scope.active_scope.set({"building_name": "Bishopsgate Tower", "period": {"key": "last_month", "label": "x"}})
    try:
        s = plan_scope.build()
    finally:
        caller_principal.reset(tp)
        caller_organization_id.reset(to)
    assert s.organization_id == str(p.organization_id) and s.allowed[0]["name"] == "Bishopsgate Tower"
    assert s.building == {"building_name": "Bishopsgate Tower"} and s.period["key"] == "last_month"
    thread_scope.active_scope.set(None)


# -- retries ------------------------------------------------------------------------------------

@pytest.mark.parametrize("code,retried", [(500, True), (503, True), (429, True), (401, False), (403, False),
                                          (404, False), (422, False)])
def test_only_a_temporary_error_is_retried(code, retried):
    req = httpx.Request("GET", "http://x/y")
    exc = httpx.HTTPStatusError("e", request=req, response=httpx.Response(code, request=req))
    assert http_client._is_transient(exc) is retried


def _serve(monkeypatch, codes):
    seen = []
    queue = list(codes)
    real = httpx.AsyncClient

    def handler(request):
        seen.append((request.method, str(request.url), request.content))
        return httpx.Response(queue.pop(0) if queue else 200, json={"ok": True})

    monkeypatch.setattr(http_client.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(http_client, "wait_exponential", lambda **kw: (lambda state: 0))
    return seen


def test_a_retry_repeats_the_same_request_and_each_attempt_is_a_span(traced, monkeypatch):
    seen = _serve(monkeypatch, [503, 503, 200])
    r = asyncio.run(http_client.request("POST", "http://svc", "/api/x", service="retry-" + uuid.uuid4().hex[:6],
                                        json={"building_name": "B"}))
    assert r.status_code == 200 and len(seen) == 3 and len(set(seen)) == 1   # three identical requests
    retries = [s for s in traced.spans if s.name.startswith("retry ")]
    assert [s.name.split(":")[0] for s in retries] == ["retry 1 of 3", "retry 2 of 3"]
    assert all(s.error == "HTTP 503" and s.input["same_request"] for s in retries)


@pytest.mark.parametrize("code", [401, 403, 422])
def test_a_refusal_or_a_validation_error_is_never_retried(traced, monkeypatch, code):
    seen = _serve(monkeypatch, [code])
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(http_client.request("GET", "http://svc", "/api/x", service="noretry-" + uuid.uuid4().hex[:6]))
    assert len(seen) == 1 and not [s for s in traced.spans if s.name.startswith("retry ")]
