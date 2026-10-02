"""Hoist Traces: a turn's span tree is built from the rows the activity log already writes, the
cost ledger's dollars land on the right span, the totals match what the chat shows, and the
reads are scoped."""
from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import pytest

from src.agents import llm_cost, trace
from src.http_client import caller_organization_id
from src.services.principal import Principal, caller_principal


def _principal(role="admin", org=None):
    return Principal(user_id=uuid.uuid4(), email="fm@example.com", organization_id=org or uuid.uuid4(), role=role,
                     can_ingest=True, building_ids=None)


@pytest.fixture
def ready(monkeypatch):
    monkeypatch.setattr(trace, "_ready", True)
    writes = []

    async def fake_write(t, answer, tool_calls, summary):
        writes.append((t, answer, tool_calls, summary))

    monkeypatch.setattr(trace, "_write", fake_write)
    p = _principal()
    caller_principal.set(p)
    caller_organization_id.set(str(p.organization_id))
    trace._turn.set(None)
    llm_cost._ledger.set(None)
    return writes


def _turn_input(q="How many boilers at B-301?"):
    trace.on_activity({"agent": "orchestrator", "stage": "turn", "direction": "input", "turn_id": "turn-1", "session_id": "s1",
                       "summary": q, "payload": {"message": q, "mode": "stream"}})


def test_a_compliance_turn_becomes_a_tree_with_cost_on_the_right_spans(ready):
    async def run():
        llm_cost.begin_turn("s1")
        _turn_input("Which certificates expire this month?")
        # the router, as two activity rows and one ledger entry
        trace.on_activity({"agent": "agent_router", "stage": "router", "direction": "input", "summary": "route", "payload": {"user_message": "x"}})
        trace.on_activity({"agent": "agent_router", "stage": "router", "direction": "output", "summary": "compliance", "model": "claude-haiku-4-5",
                           "latency_ms": 420, "input_tokens": 900, "output_tokens": 20, "payload": {"model_output": {"agent": "compliance"}}})
        llm_cost.record("agent_router", "claude-haiku-4-5", {"input_tokens": 900, "output_tokens": 20, "cache_read": 0}, 420)
        # two compliance stages under one agent group
        trace.on_activity({"agent": "compliance", "stage": "fetch", "direction": "input", "summary": "fetch", "payload": {"params": {"building": "B-301"}}})
        trace.on_activity({"agent": "compliance", "stage": "fetch", "direction": "output", "summary": "12 rows", "latency_ms": 310, "payload": {"rows": 12}})
        trace.on_activity({"agent": "compliance", "stage": "analyst", "direction": "input", "summary": "analyse", "model": "claude-sonnet-5",
                           "payload": {"system_prompt": "You are...", "user_message": "12 rows"}})
        trace.on_activity({"agent": "compliance", "stage": "analyst", "direction": "output", "summary": "Two expire", "model": "claude-sonnet-5",
                           "latency_ms": 5200, "input_tokens": 30000, "output_tokens": 600, "payload": {"model_output": "Two expire."}})
        llm_cost.record("analyst", "claude-sonnet-5", {"input_tokens": 30000, "output_tokens": 600, "cache_read": 25000}, 5200)
        # a tool pair
        trace.on_activity({"agent": "tool:compliance", "stage": "tool", "direction": "input", "summary": "list_vendor_accreditations",
                           "payload": {"tool": "list_vendor_accreditations", "input": {"status": "lapsed"}}})
        trace.on_activity({"agent": "tool:compliance", "stage": "tool", "direction": "output", "summary": "list_vendor_accreditations",
                           "payload": {"tool": "list_vendor_accreditations", "output": {"count": 4}}})
        t = trace.current()
        trace.on_activity({"agent": "orchestrator", "stage": "turn", "direction": "output", "summary": "Two expire", "latency_ms": 7000,
                           "payload": {"answer": "Two certificates expire this month.", "tool_calls": [{"tool": "x"}]}})
        await asyncio.sleep(0)
        return t

    t = asyncio.run(run())
    assert len(ready) == 1 and ready[0][1] == "Two certificates expire this month."
    kinds = [(s.kind, s.name, s.agent) for s in t.spans]
    assert kinds[0] == ("turn", "turn", "orchestrator")
    assert ("router", "router", "agent_router") in kinds and ("agent", "compliance", "compliance") in kinds
    fetch = next(s for s in t.spans if s.name == "fetch")
    analyst = next(s for s in t.spans if s.name == "analyst")
    group = next(s for s in t.spans if s.kind == "agent" and s.name == "compliance")
    assert fetch.parent_id == group.id and analyst.parent_id == group.id and group.parent_id == t.root.id
    assert fetch.kind == "stage" and fetch.latency_ms == 310 and fetch.cost_usd is None
    assert analyst.kind == "llm" and analyst.model == "claude-sonnet-5" and analyst.cache_read == 25000
    assert analyst.cost_usd == llm_cost.price_usd("claude-sonnet-5", {"input_tokens": 30000, "output_tokens": 600, "cache_read": 25000})
    router = next(s for s in t.spans if s.kind == "router")
    assert router.cost_usd is not None and router.input_tokens == 900
    assert router.parent_id == t.root.id and not any(s.kind == "agent" and s.name == "agent_router" for s in t.spans)
    tool = next(s for s in t.spans if s.kind == "tool")
    assert tool.name == "list_vendor_accreditations" and not tool.open and tool.output == {"count": 4} and tool.parent_id == t.root.id
    assert t.root.ok and t.root.latency_ms == 7000 and t.question == "Which certificates expire this month?"
    assert t.organization_id and t.user_id and t.email == "fm@example.com"
    tot = trace.totals(t, ready[0][3])
    assert tot["llm_calls"] == 2 and tot["tool_calls"] == 1 and tot["cache_read_tokens"] == 25000
    assert tot["cost_usd"] == round(ready[0][3]["usd"], 6) and tot["models"] == ["claude-haiku-4-5", "claude-sonnet-5"]
    assert tot["tools"] == ["list_vendor_accreditations"]


def test_the_general_loops_model_runs_come_from_the_stream_events(ready):
    msg = SimpleNamespace(usage_metadata={"input_tokens": 5000, "output_tokens": 120, "input_token_details": {"cache_read": 4000}},
                          response_metadata={"model_name": "gpt-4o-mini"}, tool_calls=[{"name": "answer_from_records", "args": {"question": "q"}}],
                          content="")

    async def run():
        _turn_input()
        trace.on_model_start("run-1", "gpt-4o-mini", 2)
        trace.on_model_end("run-1", msg)
        trace.on_model_start("run-2", "gpt-4o-mini", 4)   # a sub-agent's call
        trace.on_model_end("run-2", SimpleNamespace(usage_metadata={}, response_metadata={}, tool_calls=[], content="done"))
        t = trace.current()
        trace.on_activity({"agent": "orchestrator", "stage": "turn", "direction": "output", "summary": "Three.", "payload": {"answer": "Three."}})
        await asyncio.sleep(0)
        return t

    t = asyncio.run(run())
    loop = next(s for s in t.spans if s.kind == "agent" and s.name == "orchestrator loop")
    sub = next(s for s in t.spans if s.kind == "agent" and s.name == "sub-agent")
    m1 = next(s for s in t.spans if s.kind == "llm" and s.parent_id == loop.id)
    assert m1.input_tokens == 1000 and m1.cache_read == 4000 and m1.output_tokens == 120 and m1.model == "gpt-4o-mini"
    assert m1.cost_usd == llm_cost.price_usd("gpt-4o-mini", {"input_tokens": 1000, "output_tokens": 120, "cache_read": 4000})
    assert m1.output["tool_calls"] == [{"name": "answer_from_records", "args": {"question": "q"}}]
    assert any(s.parent_id == sub.id for s in t.spans)
    assert trace.totals(t, None)["llm_calls"] == 2


def test_an_errored_turn_closes_everything_and_marks_the_root(ready):
    async def run():
        _turn_input()
        trace.on_activity({"agent": "compliance", "stage": "plan", "direction": "input", "summary": "plan", "payload": {}})
        t = trace.current()
        trace.on_activity({"agent": "orchestrator", "stage": "turn", "direction": "error", "summary": "boom", "ok": False, "error": "boom",
                           "latency_ms": 100})
        await asyncio.sleep(0)
        return t

    t = asyncio.run(run())
    assert not t.root.ok and t.root.error == "boom" and all(not s.open for s in t.spans)
    # Rows after the flush are ignored, and nothing raises without a turn.
    trace.on_activity({"agent": "compliance", "stage": "x", "direction": "output", "summary": "late"})
    trace._turn.set(None)
    trace.on_llm_cost({"role": "x", "model": "m", "usd": 0.1})


def test_payloads_are_bounded():
    big = "x" * (trace.BOUND + 500)
    out = trace._bound(big)
    assert out.endswith("[500 more chars]") and len(out) < trace.BOUND + 40
    d = trace._bound({"system_prompt": big, "small": 1})
    assert d["small"] == 1 and "more chars" in d["system_prompt"]
    assert trace._bound(None) is None and trace._bound({"a": 1}) == {"a": 1}


def test_reads_are_scoped_to_the_company_and_to_the_person_unless_admin():
    p = _principal(role="user")
    where, params = trace._scope_sql(p, str(p.organization_id))
    assert "organization_id = CAST(:org AS uuid)" in where and "user_id = CAST(:uid AS uuid)" in where
    a = _principal(role="admin")
    where, _ = trace._scope_sql(a, str(a.organization_id))
    assert "user_id" not in where
    sa = _principal(role="superadmin")
    assert trace._scope_sql(sa, None)[0] == "TRUE"           # acting for no one: everything
    assert trace._scope_sql(a, None)[0].startswith("FALSE")  # an admin with no company sees nothing


def test_the_routes_need_a_signed_in_caller_and_export_is_for_admins(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from src.api.routes import traces as routes
    from src.services.principal import current_principal

    app = FastAPI()
    app.include_router(routes.router)
    c = TestClient(app)
    assert c.get("/api/traces/turns").status_code in (401, 403)
    p = _principal(role="user")
    app.dependency_overrides[current_principal] = lambda: p

    async def fake_list(principal, org, **kw):
        return [{"turn_id": "t1", "question": "q"}]

    monkeypatch.setattr(trace, "list_turns", fake_list)
    monkeypatch.setattr(trace, "_ready", True)
    r = c.get("/api/traces/turns")
    assert r.status_code == 200 and r.json()["turns"][0]["turn_id"] == "t1" and r.json()["can_manage"] is False
    assert c.get("/api/traces/export").status_code == 403
    assert c.post("/api/traces/turns/t1/feedback", json={"rating": "sideways"}).status_code == 422


def test_the_sql_a_tool_ran_is_a_query_span_under_that_tool_with_its_rows(ready):
    async def run():
        _turn_input("How many open work orders at B-301?")
        trace.on_activity({"agent": "tool:wo_engine", "stage": "tool", "direction": "input", "summary": "answer_from_records",
                           "payload": {"tool": "answer_from_records", "input": {"question": "open work orders at B-301"}}})
        # the record engine's planner: a ledger entry with no activity row -> under the open tool
        llm_cost.begin_turn("s1")
        llm_cost.record("planner", "claude-sonnet-5", {"input_tokens": 13000, "output_tokens": 200, "cache_read": 0}, 2500)
        rows = [{"wo_code": f"WO-B-301-{i}", "status": "Open"} for i in range(60)]
        trace.on_sql("SELECT f.wo_code, f.status FROM plenum_scoped.work_orders f WHERE f.building_id = :b", {"b": "bld-301"}, rows, 83.6,
                     label="records: work_orders")
        trace.on_sql("SELECT count(*) FROM plenum_scoped.work_orders f WHERE f.building_id = :b", {"b": "bld-301"}, None, 12,
                     label="count", error="relation does not exist")
        trace.on_activity({"agent": "tool:wo_engine", "stage": "tool", "direction": "output", "summary": "answer_from_records",
                           "payload": {"tool": "answer_from_records", "output": {"total": 60}}})
        t = trace.current()
        trace.on_activity({"agent": "orchestrator", "stage": "turn", "direction": "output", "summary": "60", "payload": {"answer": "60 open."}})
        await asyncio.sleep(0)
        return t

    t = asyncio.run(run())
    tool = next(s for s in t.spans if s.kind == "tool")
    planner = next(s for s in t.spans if s.kind == "llm" and s.name == "planner")
    assert planner.parent_id == tool.id and planner.cost_usd is not None and not any(s.kind == "agent" for s in t.spans)
    qs = [s for s in t.spans if s.kind == "db"]
    assert len(qs) == 2 and all(q.parent_id == tool.id for q in qs)
    assert qs[0].name == "records: work_orders" and qs[0].input["sql"].startswith("SELECT f.wo_code") and qs[0].input["params"] == {"b": "bld-301"}
    assert qs[0].output["row_count"] == 60 and len(qs[0].output["rows"]) == trace.SQL_ROWS and qs[0].output["truncated"] == 10
    assert qs[0].latency_ms == 84 and qs[0].ok
    assert not qs[1].ok and qs[1].error == "relation does not exist" and qs[1].output["row_count"] is None
    # Query spans are neither model nor tool calls in the totals.
    tot = trace.totals(t, None)
    assert tot["tool_calls"] == 1 and tot["llm_calls"] == 1
    # Outside a turn, nothing raises.
    trace._turn.set(None)
    trace.on_sql("SELECT 1", {}, [], 1)


def test_what_shaped_the_turn_is_one_span_under_the_root(ready):
    async def run():
        _turn_input()
        trace.on_prompt(system_prompt="SYSTEM " * 10, user_message="out of those which are blocked?",
                        conversation="## Recent conversation\n**User:** q1", working_set={"building_name": "Bishopsgate Tower"},
                        memories="## What I remember\n- plant room on level 3", extra_context="Buildings page", scope_block="## Working set")
        trace.on_model_start("run-1", "gpt-4o-mini", 2, {"messages": 7, "last_human_message": "…", "tool_results_since_last_human": 0})
        t = trace.current()
        trace.on_activity({"agent": "orchestrator", "stage": "turn", "direction": "output", "summary": "ok", "payload": {"answer": "Two."}})
        await asyncio.sleep(0)
        return t

    t = asyncio.run(run())
    p = next(s for s in t.spans if s.name == "prompt assembled")
    assert p.parent_id == t.root.id and p.kind == "stage" and not p.open
    assert p.input["working_set"] == {"building_name": "Bishopsgate Tower"} and "plant room" in p.input["memories_recalled"]
    assert p.output["memories"] is True and p.output["system_prompt_chars"] == 70
    m = next(s for s in t.spans if s.kind == "llm")
    assert m.input["messages"] == 7
