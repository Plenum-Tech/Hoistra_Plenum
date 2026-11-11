"""What a tool result is, and how a failure reaches the user (Workstream A3, 11 Nov 2026).

Before: a tool answered with rows, `[]`, or `{"error": <the service's raw body, up to 2,000 chars>,
"status_code": 403}`; a tool that returned {ok: false} was stored as a successful step; the writer
saw one flat list; a failed lookup could be written up as "there are none"; and the exception's own
text reached the chat. These pin the six states, the failure record in business words (on every
agent that calls a service), the SUCCEEDED / FAILED / CONFLICTS view the writers read, the wording
checks, and the engine-error path end to end.
"""
from __future__ import annotations

import asyncio
import importlib
import json
import uuid

import httpx
import pytest

from src.agents import evidence as ev
from src.agents import final_answer as fa
from src.agents import planner, trace

SECRET = 'SECRET-INTERNAL {"detail": "relation plenum_cafm.users does not exist", "trace": "Traceback (most recent call last)"}'


def _http_error(code: int, body: str = SECRET) -> httpx.HTTPStatusError:
    req = httpx.Request("GET", "http://svc.internal/api/x")
    return httpx.HTTPStatusError("boom", request=req, response=httpx.Response(code, text=body, request=req))


# -- the six states -----------------------------------------------------------------------------

@pytest.mark.parametrize("output,status", [
    ({"rows": [{"code": "WO-1", "status": "open"}], "count": 1}, ev.OK),
    ([], ev.ZERO),
    ({"rows": [], "count": 0}, ev.ZERO),
    ({"found": False, "answer_hint": "no building called that"}, ev.ZERO),
    ({"still_loading": True}, ev.UNAVAILABLE),
    ({"ok": False, "error": "timed out after 30s"}, ev.FAILED),
    ({"error": "x", "status_code": 403}, ev.DENIED),
    ({"error": "x", "status_code": 503}, ev.UNAVAILABLE),
    ('{"error": "OpenAI 429"}', ev.FAILED),
    ("Error: boom", ev.FAILED),
])
def test_every_result_has_one_state(output, status):
    assert ev.classify("t", output)["status"] == status


def test_a_field_the_records_never_fill_is_not_recorded_not_zero():
    rows = {"rows": [{"code": "A-1", "replacement_cost": None}, {"code": "A-2", "replacement_cost": ""}]}
    env = ev.classify("list_assets", rows, question="what is the replacement cost of these assets?")
    assert env["status"] == ev.NOT_RECORDED and env["ok"] is True
    assert "replacement" in env["message"] and "not recorded" in env["message"]


def test_a_failure_carries_a_record_for_the_writer():
    env = ev.classify("t", {"error": "x", "status_code": 403})
    assert set(env) >= {"status", "category", "user_actionable", "message", "next_step"}
    assert env["user_actionable"] is True and "administrator" in env["next_step"]


# -- the record in business words, on every agent that calls a service ---------------------------

AGENTS = ["wo_engine_agent", "compliance_engine_agent", "contract_performance_agent", "doc_rag_agent",
          "energy_intelligence_agent", "fiix_agent", "migration_agent", "schema_mapper_agent", "udr_agent"]


@pytest.mark.parametrize("module", AGENTS)
@pytest.mark.parametrize("code,status", [(401, ev.DENIED), (403, ev.DENIED), (500, ev.FAILED), (503, ev.UNAVAILABLE)])
def test_no_agent_hands_the_model_a_services_raw_reply(module, code, status):
    """It handed over up to 2,000 characters of the body: SQL errors, traces, internal names."""
    mod = importlib.import_module(f"src.agents.{module}")
    out = mod._err(_http_error(code), "op")
    text = json.dumps(out)
    assert "SECRET" not in text and "plenum_cafm" not in text and "Traceback" not in text
    assert out["status"] == status and out["error"] == out["message"]


@pytest.mark.parametrize("module", AGENTS)
def test_no_agent_hands_the_model_an_exceptions_text(module):
    mod = importlib.import_module(f"src.agents.{module}")
    out = mod._err(RuntimeError("asyncpg.exceptions.UndefinedTableError: relation x does not exist"), "op")
    assert "asyncpg" not in json.dumps(out) and out["status"] == ev.FAILED


def test_a_plain_validation_message_is_kept_and_a_raw_one_is_not():
    kept = ev.from_status_code(422, detail='{"detail": "start_date must be before end_date"}')
    assert kept["detail"] == "start_date must be before end_date"
    assert "detail" not in ev.from_status_code(422, detail=SECRET)


def test_a_flow_that_explains_its_own_refusals_keeps_the_explanation():
    out = ev.http_failure(_http_error(409, '{"detail": "Gate already answered"}'), service="migration", op="gate",
                          keep_detail=True)
    assert out["detail"] == "Gate already answered"
    out = ev.http_failure(_http_error(409, '{"detail": "Gate already answered"}'), service="wo", op="gate")
    assert "detail" not in out


@pytest.mark.parametrize("exc,phrase", [
    (TimeoutError("ReadTimeout: timed out"), "took too long"),
    (RuntimeError("Not authorised: token expired"), "sign-in has expired"),
    (RuntimeError("relation plenum_cafm.users does not exist"), "lookup failed"),
])
def test_a_whole_turn_failing_is_said_in_plain_words(exc, phrase):
    said = ev.user_message(exc)
    assert phrase in said and "plenum_cafm" not in said and "Error" not in said


# -- what the writers read ----------------------------------------------------------------------

def test_the_writer_reads_succeeded_and_failed_apart_with_each_failures_question():
    results = {"s1": {"ok": True, "status": ev.OK, "output": {"rows": [{"code": "B-1", "kwh": 10}]}},
               "s2": {"ok": False, "status": ev.DENIED, "failure": ev.from_status_code(403), "error": "x"}}
    text, _ = ev.render(results, asks={"s1": "energy at B-1", "s2": "their vendors' accreditations"})
    ok_part, failed_part = text.split("\nFAILED\n")
    assert '[s1] for "energy at B-1" - OK' in ok_part
    assert '[s2] for "their vendors\' accreditations" - DENIED: You do not have access' in failed_part
    assert "Next step for the user: Ask an administrator" in failed_part


def test_disagreeing_sources_are_named_before_the_writer_starts():
    results = {"s1": {"ok": True, "tool": "energy_page", "output": {"rows": [{"building_code": "B-1", "kwh": 41250}]}},
               "s2": {"ok": True, "tool": "meter_reads", "output": {"rows": [{"building_code": "B-1", "kwh": 39800}]}}}
    text, found = ev.render(results)
    assert found and found[0]["values"] == [41250, 39800]
    assert "CONFLICTS" in text and "B-1 has kwh = 41250 in [s1] but 39800 in [s2]" in text


def test_two_pages_of_one_register_are_not_a_conflict():
    results = {"s1": {"ok": True, "tool": "meter_reads", "output": {"rows": [{"building_code": "B-1", "kwh": 1}]}},
               "s2": {"ok": True, "tool": "meter_reads", "output": {"rows": [{"building_code": "B-1", "kwh": 2}]}}}
    assert ev.conflicts(results) == []


def test_a_conflict_given_one_sided_is_caught():
    found = [{"record": "B-1", "field": "kwh", "values": [41250, 39800], "sources": ["s1", "s2"], "sentence": ""}]
    assert ev.conflict_mismatches("B-1 used 41,250 kWh.", found)
    assert not ev.conflict_mismatches("B-1: 41,250 kWh on the energy page, 39,800 kWh from the meter reads.", found)


# -- the wording checks -------------------------------------------------------------------------

def test_a_failed_lookup_is_never_written_as_none_found():
    failed = {"t1": {"ok": False, "status": ev.FAILED, "failure": ev.from_status_code(504)}}
    assert ev.state_mismatches("There are no overdue work orders at Bishopsgate.", failed)
    assert not ev.state_mismatches("The work-order lookup took too long; ask again in a moment.", failed)


def test_a_refusal_must_be_said_as_one():
    denied = {"t1": {"ok": False, "status": ev.DENIED, "failure": ev.from_status_code(403)}}
    assert ev.state_mismatches("The information could not be read.", denied)
    assert not ev.state_mismatches("You do not have access to this; ask an administrator.", denied)


def test_technical_text_in_an_answer_is_caught_but_plain_english_is_not():
    assert ev.state_mismatches("The lookup failed with HTTPStatusError 403 Forbidden.", {})
    assert not ev.state_mismatches("An exception was raised on the lift inspection.", {})


# -- the final answer step ----------------------------------------------------------------------

def _llm(*replies):
    seen, queue = [], list(replies)

    async def llm(system, user, role):
        seen.append(user)
        r = queue.pop(0) if queue else replies[-1]
        if isinstance(r, Exception):
            raise r
        return r

    llm.seen = seen
    return llm


def test_an_answer_that_calls_a_failed_lookup_none_is_rewritten():
    results, _ = fa.gather([{"tool": "list_work_orders", "output": {"ok": False, "error": "timed out"}}])
    llm = _llm("There are no overdue work orders.", "The work-order lookup took too long; ask again in a moment.")
    answer, report = asyncio.run(fa.write("overdue work orders?", "", results=results, llm=llm))
    assert answer.startswith("The work-order lookup took too long")
    assert report["outcome"] == "failure" and report["rewrites"] == 1


def test_when_everything_failed_and_the_writer_failed_the_user_gets_the_failures_own_words():
    results, _ = fa.gather([{"tool": "list_work_orders", "output": {"error": "x", "status_code": 403}}])
    answer, report = asyncio.run(fa.write("overdue work orders?", "", results=results, llm=_llm(RuntimeError("down"))))
    assert answer.startswith("You do not have access") and "administrator" in answer
    assert report["source"] == "unwritten"


# -- the planner --------------------------------------------------------------------------------

TOOLS = {"get_cost_savings": "cost savings"}


def test_a_tool_step_that_answered_with_an_error_is_a_failed_step():
    """It was stored as an OK result and the writer read the error as evidence (planner.py:560)."""
    plan, _ = planner.validate({"steps": [{"id": "s1", "kind": "tool", "target": "get_cost_savings", "ask": "x"}]}, TOOLS)

    async def run_tool(name, args):
        return {"ok": False, "error": "timed out"}

    async def run_engine(*a):
        return "", []

    trace._turn.set(None)
    results = asyncio.run(planner.execute(plan, run_engine=run_engine, run_tool=run_tool))
    assert results["s1"]["ok"] is False and results["s1"]["status"] == ev.FAILED
    assert results["s1"]["error"] == "The lookup failed." or "took too long" in results["s1"]["error"]


def test_an_engine_step_that_answered_with_a_bare_json_error_is_a_failed_step():
    plan, _ = planner.validate({"steps": [{"id": "s1", "kind": "engine", "target": "wo_engine", "ask": "x"}]}, TOOLS)

    async def run_engine(engine, ask, on_event):
        return '{"error": "OpenAI 429 rate limit"}', [{"tool": "list_work_orders", "output": []}]

    trace._turn.set(None)
    results = asyncio.run(planner.execute(plan, run_engine=run_engine, run_tool=None))
    assert results["s1"]["ok"] is False and "OpenAI" not in results["s1"]["error"]


# -- the engine path, end to end ----------------------------------------------------------------

@pytest.fixture
def engine(monkeypatch):
    from src.agents import llm_cost, orchestrator as orch_mod
    from src.http_client import caller_organization_id
    from src.services import chat_memories
    from src.services.principal import Principal, caller_principal

    monkeypatch.setenv("ACTIVITY_LOG_ENABLED", "false")
    p = Principal(user_id=uuid.uuid4(), email="fm@example.com", organization_id=uuid.uuid4(), role="admin",
                  can_ingest=True, building_ids=None)
    caller_principal.set(p)
    caller_organization_id.set(str(p.organization_id))
    trace._turn.set(None)
    llm_cost._ledger.set(None)

    async def no_recall(*a, **k):
        return []

    monkeypatch.setattr(chat_memories, "recall", no_recall)
    state: dict = {}

    async def fake_engine(engine, prompt, on_event=None):
        if state.get("raise"):
            raise state["raise"]
        return state["answer"], []

    monkeypatch.setattr(orch_mod, "run_phase2_engine_verbose", fake_engine)
    O = orch_mod.DeepAgentOrchestrator
    o = O.__new__(O)

    async def llm(system, user, role, on_delta=None):
        raise AssertionError("the final writer must not be asked to write over a failed engine")

    async def set_scope(*a, **k):
        return None

    o._planner_llm = llm
    o._set_turn_scope = set_scope

    def run():
        return asyncio.run(o._invoke_phase2_engine(engine="energy_intelligence", user_message="q",
                                                   session_id="s-" + uuid.uuid4().hex[:6], extra_context=None,
                                                   attach_route=False))
    return state, run


def test_an_engine_that_answered_with_a_json_error_shows_plain_words_and_fails(engine):
    state, run = engine
    state["answer"] = '{"error": "Error code: 401 - invalid_api_key sk-proj-abc"}'
    out = run()
    assert out["success"] is False and out["answer_source"] == "engine_error"
    assert "{" not in out["answer"] and "sk-proj" not in out["answer"] and "401" not in out["answer"]


def test_an_engine_that_raised_shows_plain_words(engine):
    state, run = engine
    state["raise"] = RuntimeError("asyncpg.exceptions.UndefinedTableError: relation plenum_cafm.x does not exist")
    out = run()
    assert out["success"] is False and out["answer"] and "asyncpg" not in out["answer"]
    assert "plenum_cafm" not in out["error"]
