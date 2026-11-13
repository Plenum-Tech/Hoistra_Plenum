"""Gaps a second review of the chat's access boundary found (13 Nov 2026).

Each one was a way past the page boundary, or past the boundary of whose conversation it is:

  * the compliance answer read each certificate's linked assets, work orders, PPMs, plans and
    energy anomalies straight from the database, whatever pages the caller held;
  * a question could be asked in somebody else's recorded conversation, which handed the
    asker that conversation's history and wrote their question into it;
  * an ingest batch was readable, and cancellable, by anyone signed in;
  * a store error read as "nobody owns this conversation";
  * small things around the edges: the status poll, feedback on a hidden turn, the fallback
    history, a tool that was tagged as ingesting, three routing fall-backs, how a refusal is
    worded, the company on a resumed run, and the planner being told to use a tool it lacks.

A caller with every page, and a person in their own conversation, is answered as before.
"""
from __future__ import annotations

import json
import sys
import types
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from src.agents import agent_router, evidence, meta_tools, ontology_qa as oq, orchestrator, planner
from src.agents import page_access as PA
from src.agents import session_workspace as sw
from src.agents import spaces
from src.api.routes import ingest_batch as IB
from src.api.routes import traces as traces_routes
from src.api.routes import workflow as W
from src.http_client import caller_authorization, caller_organization_id
from src.services import chat_threads as ct
from src.services import principal as P

ORG, OTHER_ORG = str(uuid4()), str(uuid4())
CUT = datetime(2026, 11, 12, 9, 30, tzinfo=timezone.utc)
COMPLIANCE = frozenset({"compliance", "buildings"})
O = orchestrator.DeepAgentOrchestrator


def caller(role="user", org=ORG, pages=None, narrowed_at=None):
    return P.Principal(user_id=uuid4(), email="x@example.com", organization_id=org, role=role,
                       can_ingest=True, building_ids=None, pages=pages, access_narrowed_at=narrowed_at)


@pytest.fixture(autouse=True)
def _clean_context():
    tokens = [(P.caller_principal, P.caller_principal.set(None)),
              (PA.active_pages, PA.active_pages.set(PA.FROM_CALLER)),
              (caller_organization_id, caller_organization_id.set(None)),
              (caller_authorization, caller_authorization.set(None)),
              (spaces.active_space, spaces.active_space.set(None))]
    yield
    for var, tok in reversed(tokens):
        var.reset(tok)


@pytest.fixture
def owned_by(monkeypatch):
    def use(owner):
        async def _owner(_session_id):
            if isinstance(owner, Exception):
                raise owner
            return owner
        monkeypatch.setattr(ct, "session_owner", _owner)
    return use


# ── 1. what a certificate reaches is read only for the pages held ─────────────

@pytest.fixture
def reach_reads(monkeypatch):
    calls = []

    async def fake(ids, principal=None, budget=0, **kw):
        calls.append(kw)
        return {"ok": True, "certificates_reaching_something": 1, "assets": 1, "records": "{}"}
    monkeypatch.setattr(oq, "linked_for_certificates", fake)
    return calls


async def test_compliance_alone_reads_nothing_a_certificate_reaches(reach_reads):
    P.caller_principal.set(caller(pages=COMPLIANCE))
    assert await O._certificate_reach_call(None, [{"id": "c1"}], "s1") is None
    assert reach_reads == []


async def test_the_reach_read_names_only_the_kinds_of_the_pages_held(reach_reads):
    P.caller_principal.set(caller(pages=frozenset({"compliance", "ops"})))
    assert await O._certificate_reach_call(None, [{"id": "c1"}], "s1") is not None
    assert reach_reads[-1]["kinds"] == frozenset({"asset_open_work_orders", "asset_ppm_not_done",
                                                  "asset_maintenance_plans"})
    P.caller_principal.set(caller(pages=frozenset({"compliance", "assets", "energy"})))
    await O._certificate_reach_call(None, [{"id": "c1"}], "s1")
    assert reach_reads[-1]["kinds"] == frozenset({"linked_assets", "open_energy_anomalies"})


async def test_every_page_reads_what_it_always_read(reach_reads):
    P.caller_principal.set(caller())
    assert await O._certificate_reach_call(None, [{"id": "c1"}], "s1") is not None
    assert reach_reads == [{}]                     # called exactly as before: no kinds argument


def test_the_plan_includes_only_the_kinds_asked_for():
    from test_ontology_qa import _cert_onto

    o = _cert_onto()
    oq.add_certificate_links(o)
    plan = oq.normalise_plan({"question_type": "list", "focus": {"concept": "ComplianceCertificate"}})
    added = oq.ensure_compliance_includes(o, plan, kinds=frozenset({"open_energy_anomalies"}))
    assert added == ["open_energy_anomalies"]
    plan = oq.normalise_plan({"question_type": "list", "focus": {"concept": "ComplianceCertificate"}})
    assert {"linked_assets", "asset_open_work_orders", "open_energy_anomalies"} <= set(
        oq.ensure_compliance_includes(o, plan))


# ── 2 and 7. a question is asked only in the asker's own conversation ─────────

class FakeOrchestrator:
    def __init__(self):
        self.asked = []

    async def run_stateful(self, user_message, session_id, extra_context=None, **kw):
        self.asked.append(session_id)
        return {"session_id": session_id, "answer": "ok", "tool_calls": [], "success": True, "error": None}

    async def run(self, user_message, session_id=None, extra_context=None):
        self.asked.append(session_id)
        return {"session_id": session_id, "answer": "ok", "tool_calls": [], "success": True, "error": None}

    async def stream(self, user_message, session_id, extra_context=None):
        self.asked.append(session_id)
        yield {"type": "workflow_completed", "answer": "ok", "session_id": session_id}


@pytest.fixture
def app(monkeypatch):
    async def _usage(**kw):
        return 1.0
    monkeypatch.setattr(W.usage_events, "record_usage", _usage)
    fastapi_app = FastAPI()
    fastapi_app.state.limiter = W.limiter
    fastapi_app.include_router(W.router)
    orch = FakeOrchestrator()
    fastapi_app.state.orchestrator = orch
    fastapi_app.dependency_overrides[W.get_orchestrator] = lambda: orch

    def as_(who):
        fastapi_app.dependency_overrides[P.current_principal] = lambda: who

        async def _resolve(_authorization):
            return who
        monkeypatch.setattr(W, "resolve_principal", _resolve)
        return TestClient(fastapi_app), orch
    return as_


@pytest.mark.parametrize("path", ["/api/workflow/run-stateful", "/api/workflow/run"])
def test_a_question_in_somebody_elses_conversation_is_refused(app, owned_by, path):
    owned_by({"user_id": str(uuid4()), "organization_id": ORG})
    client, orch = app(caller())
    r = client.post(path, json={"message": "what is overdue?", "session_id": "s1"})
    assert r.status_code == 404 and orch.asked == []


@pytest.mark.parametrize("path", ["/api/workflow/run-stateful", "/api/workflow/run"])
def test_a_question_in_ones_own_or_a_new_conversation_runs(app, owned_by, path):
    me = caller()
    owned_by({"user_id": str(me.user_id), "organization_id": ORG})
    client, orch = app(me)
    assert client.post(path, json={"message": "what is overdue?", "session_id": "s1"}).status_code == 200
    owned_by(None)                                 # a report card's new session, a new chat
    assert client.post(path, json={"message": "q", "session_id": "report-1a2b3c4d-1763000000"}).status_code == 200
    assert orch.asked == ["s1", "report-1a2b3c4d-1763000000"]


def test_a_conversation_whose_owner_cannot_be_read_is_not_answered(app, owned_by):
    owned_by(ct.SessionStoreUnavailable("down"))
    client, orch = app(caller())
    r = client.post("/api/workflow/run-stateful", json={"message": "q", "session_id": "s1"})
    assert r.status_code == 503 and orch.asked == []


def test_a_file_question_in_somebody_elses_conversation_is_refused(app, owned_by):
    owned_by({"user_id": str(uuid4()), "organization_id": ORG})
    client, orch = app(caller())
    r = client.post("/api/workflow/run-stateful-with-files", data={"message": "q", "session_id": "s1"})
    assert r.status_code == 404 and orch.asked == []


def test_the_socket_refuses_somebody_elses_conversation(app, owned_by):
    owned_by({"user_id": str(uuid4()), "organization_id": OTHER_ORG})
    client, orch = app(caller())
    with pytest.raises(WebSocketDisconnect) as e:
        with client.websocket_connect("/api/workflow/ws/s1", subprotocols=["hoistra.auth.bearer", "tok"]) as ws:
            ws.receive_text()
    assert e.value.code == 1008 and orch.asked == []


def test_the_socket_answers_in_ones_own_conversation(app, owned_by):
    me = caller()
    owned_by({"user_id": str(me.user_id), "organization_id": ORG})
    client, orch = app(me)
    with client.websocket_connect("/api/workflow/ws/s1", subprotocols=["hoistra.auth.bearer", "tok"]) as ws:
        ws.send_text(json.dumps({"message": "q"}))
        assert json.loads(ws.receive_text())["type"] == "workflow_completed"
    assert orch.asked == ["s1"]


async def test_a_rerun_into_somebody_elses_conversation_is_refused(owned_by, monkeypatch):
    async def _turn(_p, _org, _tid):
        return {"turn_id": "t1", "started_at": datetime.now(timezone.utc).isoformat(), "question": "q"}
    monkeypatch.setattr(traces_routes.trace, "get_turn", _turn)
    owned_by({"user_id": str(uuid4()), "organization_id": ORG})
    body = traces_routes.RerunBody(session_id="s1", corrections=[traces_routes.Correction(text="x")])
    with pytest.raises(HTTPException) as e:
        await traces_routes.rerun_turn("t1", body, SimpleNamespace(headers={}), None, principal=caller(),
                                       orchestrator=SimpleNamespace())
    assert e.value.status_code == 404


async def test_a_store_error_is_not_read_as_no_record(monkeypatch):
    class Boom:
        def connect(self):
            raise RuntimeError("connection refused")
    monkeypatch.setattr(ct, "_ready", True)
    monkeypatch.setattr("src.database._get_engine", lambda: Boom())
    with pytest.raises(ct.SessionStoreUnavailable):
        await ct.session_owner("s1")
    with pytest.raises(HTTPException) as e:
        await W._session_guard("s1", caller(), unknown_ok=True)
    assert e.value.status_code == 503


# ── 3. an ingest batch is its uploader's company's ────────────────────────────

@pytest.fixture
def batches(monkeypatch):
    store = {"b1": {"batch_id": "b1", "session_id": "s1", "organization_id": OTHER_ORG},
             "b2": {"batch_id": "b2", "session_id": "s1", "organization_id": ORG}}
    cancelled = []

    async def _get(bid):
        return store.get(bid)

    async def _list(sid, limit=10):
        return [b for b in store.values() if b["session_id"] == sid]

    async def _cancel(bid):
        return store.get(bid)
    monkeypatch.setattr(IB.batch_svc, "get_ingest_batch", _get)
    monkeypatch.setattr(IB.batch_svc, "list_ingest_batches_for_session", _list)
    monkeypatch.setattr(IB.batch_svc, "cancel_ingest_batch", _cancel)
    monkeypatch.setattr(IB, "mark_batch_cancelled", cancelled.append)
    return cancelled


async def test_another_companys_batch_is_not_found(batches, owned_by):
    owned_by(None)
    for call in (lambda: IB.get_batch("b1", principal=caller()), lambda: IB.cancel_batch("b1", principal=caller())):
        with pytest.raises(HTTPException) as e:
            await call()
        assert e.value.status_code == 404
    assert batches == []
    assert (await IB.get_batch("b2", principal=caller()))["batch_id"] == "b2"
    assert (await IB.get_batch("b1", principal=caller(role="superadmin", org=None)))["batch_id"] == "b1"


async def test_a_sessions_batches_are_its_owners(batches, owned_by):
    owned_by({"user_id": str(uuid4()), "organization_id": ORG})
    with pytest.raises(HTTPException):
        await IB.list_batches(session_id="s1", limit=10, principal=caller())
    me = caller()
    owned_by({"user_id": str(me.user_id), "organization_id": ORG})
    out = await IB.list_batches(session_id="s1", limit=10, principal=me)
    assert [b["batch_id"] for b in out["batches"]] == ["b2"]


# ── 4. the status poll reads the caller's own thread ──────────────────────────

async def test_status_reads_the_thread_the_caller_now_runs_on(owned_by):
    me = caller(pages=COMPLIANCE, narrowed_at=CUT)
    owned_by({"user_id": str(me.user_id), "organization_id": ORG})
    seen = {}

    class Orch:
        async def get_thread_state(self, sid):
            seen["thread"] = PA.thread_key(sid)
            return None
    await W.get_workflow_status("s1", principal=me, orchestrator=Orch())
    assert seen["thread"] == "s1@" + str(int(CUT.timestamp()))


# ── 5. feedback on a hidden turn ──────────────────────────────────────────────

async def test_feedback_on_a_turn_from_before_the_cut_is_refused(monkeypatch):
    gets, sets = [], []

    async def _turn(_p, _org, tid):
        gets.append(tid)
        return {"turn_id": tid, "started_at": (CUT - timedelta(days=1)).isoformat(), "question": "q"}

    async def _set(*a):
        sets.append(a)
        return True
    monkeypatch.setattr(traces_routes.trace, "get_turn", _turn)
    monkeypatch.setattr(traces_routes.trace, "set_feedback", _set)
    with pytest.raises(HTTPException) as e:
        await traces_routes.set_feedback("t1", traces_routes.Feedback(rating="down", comment="wrong"), None,
                                         principal=caller(pages=COMPLIANCE, narrowed_at=CUT))
    assert e.value.status_code == 404 and sets == []
    gets.clear()
    out = await traces_routes.set_feedback("t1", traces_routes.Feedback(rating="up"), None, principal=caller())
    assert out["ok"] and len(sets) == 1 and gets == []    # everyone else: no extra read


# ── 6. the fallback history after a store error ───────────────────────────────

def test_the_fallback_history_is_not_given_to_a_narrowed_caller():
    sid = "fallback-" + str(uuid4())
    sw.record_conversation_turn(sid, "user", "how much energy did B-101 waste?")
    sw.record_conversation_turn(sid, "assistant", "GBP 7,605 a year")
    P.caller_principal.set(caller())
    assert "7,605" in sw.build_session_runtime_context(sid, None)
    P.caller_principal.set(caller(pages=COMPLIANCE, narrowed_at=CUT))
    assert "7,605" not in sw.build_session_runtime_context(sid, None)
    assert "kept" in sw.build_session_runtime_context(sid, "kept")      # the store's own read stands


# ── 8. a tool that lists certificates is Compliance's; an engine outside the pages is not run ─

def test_the_membership_list_is_compliances():
    assert PA.tool_tags(meta_tools_tool("manage_vector_membership")) == ("compliance",)


def meta_tools_tool(name):
    from src.agents import compliance_engine_agent
    return getattr(compliance_engine_agent, name)


def test_an_engine_whose_page_is_not_held_is_not_run_on_its_shared_tools():
    from src.agents.context_budget import compact_context
    from src.agents.document_search_tool import search_documents

    runner = meta_tools._TaskRunner.__new__(meta_tools._TaskRunner)
    runner._page_runners = {}
    runner._sub = lambda name, *, tools, prompt: SimpleNamespace(name=name, tools=[t.name for t in tools])
    runner._specs = {"compliance": ([search_documents, compact_context], "p"),
                     "maintenance": ([search_documents, compact_context], "p"),
                     "doc_rag": ([search_documents, compact_context], "p")}
    PA.active_pages.set(frozenset({"buildings"}))
    assert runner._runner_for_pages("compliance") is None
    assert runner._runner_for_pages("maintenance") is None
    assert runner._runner_for_pages("doc_rag") is not None        # not an engine: its tools decide
    PA.active_pages.set(COMPLIANCE)
    assert runner._runner_for_pages("compliance").tools == ["search_documents"]


# ── 9. the routing fall-backs refuse what the pages and the space refuse ───────

async def test_a_router_that_failed_does_not_route_past_the_pages(monkeypatch):
    class Boom:
        def __init__(self, **kw):
            raise RuntimeError("router down")
    monkeypatch.setitem(sys.modules, "anthropic", types.SimpleNamespace(AsyncAnthropic=Boom))
    monkeypatch.setattr(agent_router, "llm_routing_enabled", lambda: True)
    monkeypatch.setattr(agent_router.settings, "anthropic_api_key", "k", raising=False)
    monkeypatch.setattr(agent_router, "certain_engine", lambda q: None)
    monkeypatch.setattr(agent_router, "resolve_phase2_engine", lambda **kw: "energy_intelligence")
    P.caller_principal.set(caller(pages=COMPLIANCE))
    out = await agent_router.select_agent("how are we doing this month?")
    assert out.get("out_of_module") and out["agent"] is None
    P.caller_principal.set(caller())
    out = await agent_router.select_agent("how are we doing this month?")
    assert out["agent"] == "energy_intelligence" and not out.get("out_of_module")


async def test_an_engine_the_pages_refuse_is_never_dispatched(monkeypatch):
    ran = []

    async def _run(engine, prompt, on_event=None):
        ran.append(engine)
        raise RuntimeError("stop here")
    monkeypatch.setattr(orchestrator, "run_phase2_engine_verbose", _run)
    orch = O.__new__(O)

    async def _scope(*a, **kw):
        return None
    orch._set_turn_scope = _scope
    P.caller_principal.set(caller(pages=COMPLIANCE))
    out = await orch._invoke_phase2_engine(engine="energy_intelligence", user_message="worst meter?",
                                           session_id="s1", extra_context=None, attach_route=False)
    assert ran == [] and "outside the pages you have access to" in out["answer"]
    spaces.active_space.set("ops")                     # the space refuses too, for everyone
    P.caller_principal.set(caller())
    out = await orch._invoke_phase2_engine(engine="compliance", user_message="lapsed?", session_id="s1",
                                           extra_context=None, attach_route=False)
    assert ran == [] and out["answer"]
    spaces.active_space.set(None)
    await orch._invoke_phase2_engine(engine="compliance", user_message="lapsed?", session_id="s1",
                                     extra_context=None, attach_route=False)
    assert ran == ["compliance"]                        # no space, every page: dispatched as before


# ── 10. a refusal is said as one ──────────────────────────────────────────────

def test_a_page_refusal_is_classified_as_outside_the_pages():
    for out in (PA.refusal_payload(), json.dumps({"error": PA.OUTSIDE_PAGES})):
        r = evidence.classify("list_energy_anomalies", out)
        assert r["status"] == evidence.DENIED and r["category"] == "outside_pages"
        assert "outside the pages" in r["message"]


# ── 11. a resumed run reads one company ───────────────────────────────────────

async def test_a_resumed_run_reads_the_callers_company(owned_by, monkeypatch):
    seen = {}

    class Orch:
        async def resume(self, session_id, decision):
            seen["org"] = caller_organization_id.get()
            return {"session_id": session_id, "answer": "ok", "tool_calls": [], "success": True}
    monkeypatch.setattr(W, "_to_response", lambda r: r)
    owned_by(None)
    # As /run and /run-stateful set it: a plain caller reads their own company from the token
    # (None here), a superadmin who names none reads their own - not every company.
    for who, want in ((caller(), None), (caller(role="superadmin"), ORG)):
        caller_organization_id.set("stale")
        await W.resume_workflow("s1", W.ResumeRequest(decision={}), SimpleNamespace(headers={}),
                                principal=who, orchestrator=Orch())
        assert seen["org"] == want


# ── 12. the planner is not told to use a tool it does not have ────────────────

async def test_the_planner_is_told_when_the_record_tool_is_not_there():
    prompts = []

    async def llm(system, user, role):
        prompts.append(user)
        return "{}"
    await planner.make_plan("which certificates lapsed and which work orders are open?", context="",
                            tools={"list_work_orders": "list_work_orders: open jobs"}, llm=llm, data=False)
    assert "answer_from_records is not available" in prompts[0]
    prompts.clear()
    await planner.make_plan("which certificates lapsed and which work orders are open?", context="",
                            tools={planner.RECORD_TOOL: "answer_from_records: records"}, llm=llm, data=False)
    assert "answer_from_records is not available" not in prompts[0]
