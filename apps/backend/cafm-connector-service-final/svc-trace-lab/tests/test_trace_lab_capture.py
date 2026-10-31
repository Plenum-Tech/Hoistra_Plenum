"""trace_lab.capture.run_message: one golden message through orchestrator.stream(), captured in memory by
svc-deepagents' own trace hooks on a Turn Trace Lab pre-set (G0 addendum 2.5). The orchestrator is a
recorded stand-in that calls the real hooks; nothing is written, no model, network or database."""
from __future__ import annotations

import asyncio
import inspect

import pytest

from golden_fakes import BEARER, LOOP_MODEL, ORG, OTHER_ORG, Msg, RecordedOrchestrator, principal
from src import database
from src.agents import activity_log, llm_cost, replay_guard, skill_lab, trace
from src.agents.session_workspace import workflow_stream_completion_payload
from src.http_client import caller_authorization, caller_organization_id
from src.services.principal import caller_principal
from trace_lab import capture, normalize, proxy

SID = "golden-1a2b3c4d5e6f-21-r1"
Q = "How many work orders were raised in August 2026?"


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setenv("ACTIVITY_LOG_ENABLED", "false")
    monkeypatch.setattr(trace, "_ready", False)
    touched: list[str] = []

    def no_engine():
        touched.append("engine")
        raise AssertionError("capture touched the database engine")

    monkeypatch.setattr(database, "_get_engine", no_engine)
    proxy.reset_for_tests()
    yield touched
    proxy.reset_for_tests()


async def _ask(orch, *, who=None, org=ORG, page="The user is on the Work orders page.", question=Q):
    return await capture.run_message(orch, question=question, session_id=SID, page_context=page,
                                     principal=who or principal(), organization_id=org, authorization=BEARER)


async def test_a_completed_message_is_captured_whole(_quiet):
    card = {"kpis": [{"label": "Work orders", "value": 759}]}
    orch = RecordedOrchestrator([Msg(cards=[card])])
    got = await _ask(orch)
    await asyncio.sleep(0)                                     # let fire()'s record tasks finish
    assert got.answer.startswith("There were 759") and got.error is None and got.ended_by == "workflow_completed"
    assert got.turn_id.startswith("turn-g") and len(got.turn_id) == 22 and got.session_id == SID
    assert got.cards == [card] and got.refused == [] and got.ms >= 0
    kinds = [s["kind"] for s in got.spans]
    assert kinds[0] == "turn" and {"plan", "tool", "db", "llm"} <= set(kinds)
    tool = next(s for s in got.spans if s["kind"] == "tool")
    assert tool["name"] == "udr_query_table" and tool["output"] == {"rows": [{"n": 759}]}
    db_span = next(s for s in got.spans if s["kind"] == "db")
    assert db_span["parent_id"] == tool["id"]
    t = got.turn
    assert (t["turn_id"], t["session_id"], t["organization_id"]) == (got.turn_id, SID, ORG)
    assert t["question"] == Q and t["answer"] == got.answer and t["ok"] is True and t["route"] == "single:udr"
    assert LOOP_MODEL in t["models"] and "udr_query_table" in t["tools"] and t["email"] == "admin@example.com"
    assert _quiet == []                                        # nothing reached the database


async def test_both_ledgers_and_the_loop_model_are_counted():
    got = await _ask(RecordedOrchestrator([Msg()]))
    roles = [e["role"] for e in got.ledger]
    assert roles == ["router", "evaluator"]                    # the caller's ledger, then stream()'s own
    ledger_usd = sum(e["usd"] for e in got.ledger)
    loop = next(s for s in got.spans if s["kind"] == "llm" and s["model"] == LOOP_MODEL)
    assert loop["cost_usd"] > 0
    assert got.usd == pytest.approx(max(ledger_usd, sum(s["cost_usd"] or 0 for s in got.spans
                                                        if s["kind"] in ("llm", "router"))), abs=1e-6)
    assert got.usd > ledger_usd                                # the loop's own call is not in any ledger


@pytest.mark.parametrize("who,org,acting", [(principal("admin"), ORG, None),
                                            (principal("superadmin", None), OTHER_ORG, OTHER_ORG)])
async def test_the_chat_sees_the_starter_and_nothing_leaks_out(who, org, acting):
    orch = RecordedOrchestrator([Msg()])
    got = await _ask(orch, who=who, org=org)
    (seen,) = orch.seen
    assert seen["principal"] is who and seen["auth"] == BEARER and seen["org"] == acting
    assert seen["replay"] is False and seen["turn"] == got.turn_id
    assert isinstance(seen["trace_turn"], trace.Turn) and seen["trace_turn"].turn_id == got.turn_id
    assert seen["context"] == "The user is on the Work orders page." and seen["message"] == Q
    assert caller_principal.get() is None and caller_authorization.get() is None
    assert caller_organization_id.get() is None and trace.current() is None and llm_cost.current() is None
    assert activity_log.current_turn() is None and replay_guard.is_replay() is False
    assert got.turn["organization_id"] == org


async def test_refusals_and_upstream_failures_come_back_with_the_answer():
    got = await _ask(RecordedOrchestrator([Msg(refuse=True, upstream_status=503)]))
    (refusal,) = got.refused
    assert refusal["method"] == "PATCH" and refusal["case"] == f"{SID}:{got.turn_id}"
    assert [u["status"] for u in got.upstream_failures] == [503]
    assert proxy.refusals.active() is None and proxy.refusals.current() == []


async def test_an_error_event_and_a_gate_are_results():
    err = await _ask(RecordedOrchestrator([Msg(kind="error", error="needed too many tool steps")]))
    assert (err.ended_by, err.error, err.answer) == ("error", "needed too many tool steps", "")
    assert err.turn["ok"] is False
    gate = await _ask(RecordedOrchestrator([Msg(kind="gate")]))
    assert gate.ended_by == "gate_interrupt" and gate.error is None and gate.answer == ""


async def test_a_raising_chat_is_a_result_and_the_refusal_log_is_closed():
    proxy.refusals.reset()
    got = await _ask(RecordedOrchestrator([Msg(kind="raise", error="connection reset by peer")]))
    assert got.ended_by == "exception" and "RuntimeError: connection reset by peer" in got.error
    assert got.spans[0]["kind"] == "turn" and got.spans[0]["ok"] is False and got.turn["ok"] is False
    assert proxy.refusals.active() is None


async def test_a_message_that_runs_out_of_time_is_a_timeout(monkeypatch):
    monkeypatch.setattr(capture, "MESSAGE_TIMEOUT_S", 0.05)
    got = await _ask(RecordedOrchestrator([Msg(kind="hang")]))
    assert got.ended_by == "timeout" and "timed out" in got.error and proxy.refusals.active() is None


async def test_tool_errors_are_kept_for_the_runner():
    got = await _ask(RecordedOrchestrator([Msg(tools=[("wo_list", {"error": "Sign in again.", "status_code": 401}),
                                                      ("wo_get", '{"error": "busy", "status_code": 429}'),
                                                      ("udr_query_table", {"rows": []})])]))
    assert [(e["tool"], e["status_code"]) for e in got.tool_errors] == [("wo_list", 401), ("wo_get", 429)]


async def test_the_captured_rows_normalize_like_a_stored_turn():
    got = await _ask(RecordedOrchestrator([Msg()]))
    record = normalize.normalize_turn(got.turn, got.spans, meta={
        "channel": "golden", "channel_source": "recorded", "asker_question": Q, "golden_run_id": "r1",
        "golden_case_key": "21", "rep": 1})
    assert record["id"] == got.turn_id and record["output"] == got.answer
    assert record["messages"][0]["role"] == "user" and record["messages"][-1]["role"] == "assistant"
    assert any(m["role"] == "tool_call" and m["name"] == "udr_query_table" for m in record["messages"])
    assert record["meta"]["golden"] == {"run_id": "r1", "case_key": "21", "rep": 1}


def test_cards_are_kept_whole_and_cut_past_64_kb():
    small, big = {"kpis": [1]}, {"rows": ["x" * 70_000]}
    calls = [{"tool": "compliance_response", "output": small}, {"tool": "compliance_pipeline", "output": small},
             {"tool": "compliance_response", "output": big}, {"tool": "compliance_response", "output": "text"}]
    assert capture.cards_of(calls) == ([small], True)


def test_acting_org_follows_the_chat_route():
    assert capture.acting_org_for(principal("admin"), ORG) is None
    assert capture.acting_org_for(principal("superadmin", None), OTHER_ORG) == OTHER_ORG
    assert capture.acting_org_for(principal("superadmin", None), None) is None


def test_the_private_touch_points_still_hold():
    """capture relies on these svc-deepagents behaviours; a change there must fail here first."""
    replay_src = inspect.getsource(skill_lab.replay)
    for var in ("caller_principal", "caller_organization_id", "caller_authorization"):
        assert f"({var}, {var}.set(" in replay_src
    fire_src = inspect.getsource(activity_log.fire)
    assert fire_src.index("_trace.on_activity(kwargs)") < fire_src.index("loop.create_task(record(**kwargs))")
    assert "if not enabled():" in inspect.getsource(activity_log.record)
    assert "if t is None or t.flushed:" in inspect.getsource(trace.on_activity)
    assert inspect.getsource(trace._write).split("\n")[1].strip() == "if not _ready:"
    assert isinstance(trace._turn.get(None), (type(None), trace.Turn)) and trace._turn.name == "hoist_trace_turn"
    assert list(inspect.signature(trace.Turn.__init__).parameters) == ["self", "turn_id", "session_id"]
    from src.agents.orchestrator import DeepAgentOrchestrator
    stream_src = inspect.getsource(DeepAgentOrchestrator.stream)
    assert "activity_log.ensure_turn()" in stream_src and "llm_cost.begin_turn(sid)" in stream_src
    assert list(inspect.signature(DeepAgentOrchestrator.stream).parameters) == [
        "self", "user_message", "session_id", "extra_context"]


def test_workflow_completed_carries_the_turn_the_caller_started():
    async def go():
        activity_log.start_turn("turn-gabc")
        return workflow_stream_completion_payload(SID, answer="a", tool_calls=[])
    ev = asyncio.run(go())
    assert (ev["type"], ev["answer"], ev["turn_id"], ev["session_id"]) == ("workflow_completed", "a", "turn-gabc", SID)
