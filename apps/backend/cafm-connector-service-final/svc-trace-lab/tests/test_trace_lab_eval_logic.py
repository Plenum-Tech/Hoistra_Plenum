"""The logic level (11 Nov 2026, task plan B3): "correct plan, call order, scope kept, retries follow the rules,
context passed correctly", checked automatically by comparing a trace's steps with the scenario's expected outcome.

Each check answers pass, fail, not_observable or not_applicable. The last two matter as much as the first two:
today's orchestrator records no structured plan, no retry attempts and no evidence state, so a check that cannot see
what it needs must say so rather than fail the trace or crash. A trace fails the level only when a check found
something wrong."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from trace_lab import eval_logic

T0 = datetime(2026, 11, 11, 9, 0, 0, tzinfo=timezone.utc)


def _at(seconds: float | None) -> str | None:
    return None if seconds is None else (T0 + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")


def node(id_: str, type_: str, name: str, *, agent: str | None = None, parent: str | None = "root",
         start: float | None = 0, end: float | None = 1, input=None, output=None, failed: bool = False,  # noqa: A002
         error: str | None = None, metadata: dict | None = None) -> dict:
    n = {"id": id_, "parent_id": parent, "depth": 0 if parent is None else 1, "seq": 0, "kind": type_, "type": type_,
         "name": name, "agent": agent, "model": None, "started_at": _at(start), "ended_at": _at(end),
         "latency_ms": None, "cost_usd": None, "input_tokens": None, "output_tokens": None, "cache_read_tokens": None,
         "ok": not failed, "error": error, "level": "ERROR" if failed else "DEFAULT", "input": input, "output": output,
         "filled": {}, "truncated": False}
    if metadata:
        n["metadata"] = metadata
    return n


ROOT = node("root", "agent", "chat", parent=None, start=0, end=30)


def call(cid: str, domain: str | None = None, tool: str | None = None, deps: tuple[str, ...] = ()) -> dict:
    return {"call_id": cid, "domain": domain, "tool": tool, "params": {}, "depends_on": list(deps)}


def expected(*plan: dict, state: str | None = None, failed: tuple[str, ...] = ()) -> dict:
    return {"plan": list(plan), "failure_handling": {"state": state, "failed": list(failed)} if state else None,
            "answer_format": "success", "must_include": [], "must_not_include": [], "answer": None}


def results(r) -> dict[str, str]:
    return {c.key: c.result for c in r.checks}


def test_every_check_is_reported_in_a_fixed_order():
    r = eval_logic.evaluate(expected(call("c1", "compliance")), {}, [ROOT], trace_status="ok")
    assert tuple(c.key for c in r.checks) == eval_logic.CHECKS == (
        "completed", "plan_coverage", "call_order", "parallel", "scope", "retries", "context_passing", "evidence_state")
    assert eval_logic.EVALUATOR == "logic-1"


def test_todays_trace_without_plan_retry_or_status_passes_on_what_can_be_seen():
    nodes = [ROOT, node("a", "agent", "compliance", agent="compliance"),
             node("t1", "tool", "list_certificates", agent="compliance", parent="a", start=1, end=3,
                  input={"within_days": 30}, output={"rows": [{"id": "CERT-9001"}]})]
    r = eval_logic.evaluate(expected(call("c1", "compliance")), {}, nodes, trace_status="ok")
    got = results(r)
    assert got["completed"] == "pass" and got["plan_coverage"] == "pass"
    assert got["retries"] == "not_applicable" and got["evidence_state"] == "not_applicable"
    assert got["call_order"] == "not_applicable" and got["scope"] == "not_applicable"
    assert r.verdict == "pass" and r.value == 1.0


def test_a_trace_with_no_recorded_steps_is_not_observable_rather_than_wrong():
    r = eval_logic.evaluate(expected(call("c1", "compliance")), {}, [ROOT], trace_status="ok")
    assert results(r)["plan_coverage"] == "not_observable"
    assert r.verdict == "not_evaluated" and r.value is None, "only that it ended could be seen: that is no verdict"


def test_a_trace_of_which_only_its_ending_can_be_seen_is_not_evaluated_but_one_that_failed_still_fails():
    assert eval_logic.evaluate({}, None, [], trace_status="ok").verdict == "not_evaluated"
    assert eval_logic.evaluate({}, None, [], trace_status="timeout").verdict == "fail"


def test_a_scope_check_whose_violations_are_not_a_list_still_fails_the_scope_and_never_raises():
    for violations, shown in (({"vendor": "V-9"}, "V-9"), (7, "7"), ("vendor V-9", "vendor V-9"), (None, "no reason")):
        nodes = [ROOT, node("sc", "span", "scope_check", output={"ok": False, "violations": violations})]
        check = next(c for c in eval_logic.evaluate(expected(call("c1", "wo_engine")), {}, nodes,
                                                    trace_status="ok").checks if c.key == "scope")
        assert check.result == "fail" and shown in check.note, (violations, check.note)


def test_a_trace_that_did_not_complete_fails_whatever_else_holds():
    nodes = [ROOT, node("t1", "tool", "list_certificates", agent="compliance")]
    r = eval_logic.evaluate(expected(call("c1", "compliance")), {}, nodes, trace_status="timeout")
    assert results(r)["completed"] == "fail" and r.verdict == "fail"
    assert "timeout" in next(c.note for c in r.checks if c.key == "completed")


def test_a_missing_area_fails_plan_coverage_and_is_named():
    nodes = [ROOT, node("t1", "tool", "list_work_orders", agent="wo_engine")]
    r = eval_logic.evaluate(expected(call("c1", "wo_engine"), call("c2", "compliance")), {}, nodes, trace_status="ok")
    check = next(c for c in r.checks if c.key == "plan_coverage")
    assert check.result == "fail" and "c2" in check.note and "compliance" in check.note
    assert r.verdict == "fail" and r.value == 0.5


def test_a_named_tool_must_be_the_tool_called_and_an_agents_own_steps_count_for_its_area():
    nodes = [ROOT, node("t1", "tool", "list_vendors", agent="compliance")]
    by_tool = eval_logic.evaluate(expected(call("c1", "compliance", "list_certificates")), {}, nodes, trace_status="ok")
    assert results(by_tool)["plan_coverage"] == "fail"
    stage_only = [ROOT, node("s1", "span", "fetch", agent="compliance")]
    by_area = eval_logic.evaluate(expected(call("c1", "compliance")), {}, stage_only, trace_status="ok")
    assert results(by_area)["plan_coverage"] == "pass"


def test_another_name_for_the_same_area_still_matches():
    nodes = [ROOT, node("t1", "tool", "list_work_orders", agent="maintenance")]
    r = eval_logic.evaluate(expected(call("c1", "wo_engine")), {}, nodes, trace_status="ok")
    assert results(r)["plan_coverage"] == "pass"


def test_a_structured_plan_is_checked_too_when_the_trace_has_one():
    plan_span = node("p", "span", "plan", output={"calls": [{"call_id": "1", "tool": "list_work_orders", "params": {},
                                                             "depends_on": []}]})
    nodes = [ROOT, plan_span, node("t1", "tool", "list_work_orders", agent="wo_engine"),
             node("t2", "tool", "list_certificates", agent="compliance")]
    exp = expected(call("c1", "wo_engine", "list_work_orders"), call("c2", "compliance", "list_certificates"))
    check = next(c for c in eval_logic.evaluate(exp, {}, nodes, trace_status="ok").checks if c.key == "plan_coverage")
    assert check.result == "fail" and "planned" in check.note and "c2" in check.note
    assert eval_logic.planned_calls(nodes)[0]["tool"] == "list_work_orders"
    assert eval_logic.planned_calls([ROOT]) is None


def test_a_dependency_is_kept_when_the_second_call_starts_after_the_first_ends():
    exp = expected(call("c1", "wo_engine"), call("c2", "compliance", deps=("c1",)))
    kept = [ROOT, node("t1", "tool", "a", agent="wo_engine", start=1, end=3),
            node("t2", "tool", "b", agent="compliance", start=3, end=5)]
    broken = [ROOT, node("t1", "tool", "a", agent="wo_engine", start=1, end=4),
              node("t2", "tool", "b", agent="compliance", start=2, end=5)]
    assert results(eval_logic.evaluate(exp, {}, kept, trace_status="ok"))["call_order"] == "pass"
    bad = next(c for c in eval_logic.evaluate(exp, {}, broken, trace_status="ok").checks if c.key == "call_order")
    assert bad.result == "fail" and "c2" in bad.note and "c1" in bad.note


def test_call_order_without_times_cannot_be_seen():
    exp = expected(call("c1", "wo_engine"), call("c2", "compliance", deps=("c1",)))
    nodes = [ROOT, node("t1", "tool", "a", agent="wo_engine", start=None, end=None),
             node("t2", "tool", "b", agent="compliance", start=None, end=None)]
    assert results(eval_logic.evaluate(exp, {}, nodes, trace_status="ok"))["call_order"] == "not_observable"


def test_independent_calls_must_overlap_only_when_the_scenario_tests_dependency_order():
    exp = expected(call("c1", "wo_engine"), call("c2", "compliance"))
    together = [ROOT, node("t1", "tool", "a", agent="wo_engine", start=1, end=4),
                node("t2", "tool", "b", agent="compliance", start=2, end=5)]
    in_turn = [ROOT, node("t1", "tool", "a", agent="wo_engine", start=1, end=2),
               node("t2", "tool", "b", agent="compliance", start=3, end=4)]
    tested = {"sections": ["dependency_order"]}
    assert results(eval_logic.evaluate(exp, {}, together, trace_status="ok", **tested))["parallel"] == "pass"
    assert results(eval_logic.evaluate(exp, {}, in_turn, trace_status="ok", **tested))["parallel"] == "fail"
    assert results(eval_logic.evaluate(exp, {}, in_turn, trace_status="ok"))["parallel"] == "not_applicable"
    chained = expected(call("c1", "wo_engine"), call("c2", "compliance", deps=("c1",)))
    assert results(eval_logic.evaluate(chained, {}, in_turn, trace_status="ok", **tested))["parallel"] == "not_applicable"


def test_a_building_outside_the_scope_fails_and_names_the_call_and_the_value():
    scope = {"buildings": ["B-301"], "vendors": [], "period": {"from": "2026-10-01", "to": "2026-10-31"}}
    inside = [ROOT, node("t1", "tool", "list_work_orders", agent="wo_engine",
                         input={"filters": {"building_id": "b-301", "date_from": "2026-10-01", "date_to": "2026-10-31"}})]
    outside = [ROOT, node("t1", "tool", "list_work_orders", agent="wo_engine", input={"building_ids": ["B-301", "B-999"]})]
    late = [ROOT, node("t1", "tool", "list_work_orders", agent="wo_engine", input={"building": "B-301", "to": "2026-11-30"})]
    exp = expected(call("c1", "wo_engine"))
    assert results(eval_logic.evaluate(exp, scope, inside, trace_status="ok"))["scope"] == "pass"
    bad = next(c for c in eval_logic.evaluate(exp, scope, outside, trace_status="ok").checks if c.key == "scope")
    assert bad.result == "fail" and "B-999" in bad.note and "list_work_orders" in bad.note
    wide = next(c for c in eval_logic.evaluate(exp, scope, late, trace_status="ok").checks if c.key == "scope")
    assert wide.result == "fail" and "2026-11-30" in wide.note


def test_scope_with_nothing_to_compare_is_said_plainly():
    scope = {"buildings": ["B-301"]}
    nodes = [ROOT, node("t1", "tool", "list_work_orders", agent="wo_engine", input={"status": "open"})]
    exp = expected(call("c1", "wo_engine"))
    assert results(eval_logic.evaluate(exp, scope, nodes, trace_status="ok"))["scope"] == "not_observable"
    assert results(eval_logic.evaluate(exp, {}, nodes, trace_status="ok"))["scope"] == "not_applicable"


def test_a_scope_check_span_from_the_orchestrator_decides_when_there_is_one():
    scope = {"buildings": ["B-301"]}
    said_no = [ROOT, node("sc", "span", "scope_check", output={"ok": False, "violations": ["vendor V-9 is outside the scope"]}),
               node("t1", "tool", "list_work_orders", agent="wo_engine", input={"building": "B-301"})]
    bad = next(c for c in eval_logic.evaluate(expected(call("c1", "wo_engine")), scope, said_no, trace_status="ok").checks
               if c.key == "scope")
    assert bad.result == "fail" and "V-9" in bad.note


def _retry(id_: str, parent: str, params: dict, reason: str = "timeout") -> dict:
    return node(id_, "span", "retry_attempt", parent=parent, input=params, metadata={"reason": reason})


def test_a_retry_after_a_temporary_error_with_the_same_parameters_passes():
    params = {"building": "B-301"}
    nodes = [ROOT, node("t1", "tool", "list_work_orders", agent="wo_engine", input=params, failed=True,
                        error="upstream timeout"), _retry("r1", "t1", params)]
    assert results(eval_logic.evaluate(expected(call("c1", "wo_engine")), {}, nodes, trace_status="ok"))["retries"] == "pass"


def test_a_retry_with_changed_parameters_fails():
    nodes = [ROOT, node("t1", "tool", "list_work_orders", agent="wo_engine", input={"building": "B-301"}, failed=True,
                        error="HTTP 503"), _retry("r1", "t1", {"building": "B-302"})]
    bad = next(c for c in eval_logic.evaluate(expected(call("c1", "wo_engine")), {}, nodes, trace_status="ok").checks
               if c.key == "retries")
    assert bad.result == "fail" and "parameters" in bad.note


@pytest.mark.parametrize("error, want", [("HTTP 403 Forbidden", "fail"), ("401 unauthorized", "fail"),
                                         ("422 validation error", "fail"), ("HTTP 504 gateway timeout", "pass"),
                                         ("rate limited (429)", "pass"), ("connection reset", "pass")])
def test_only_temporary_errors_may_be_retried(error, want):
    params = {"building": "B-301"}
    nodes = [ROOT, node("t1", "tool", "list_work_orders", agent="wo_engine", input=params, failed=True, error=error),
             _retry("r1", "t1", params, reason=error)]
    assert results(eval_logic.evaluate(expected(call("c1", "wo_engine")), {}, nodes, trace_status="ok"))["retries"] == want


def test_more_retries_than_the_limit_fail():
    params = {"q": 1}
    nodes = [ROOT, node("t1", "tool", "x", agent="wo_engine", input=params, failed=True, error="timeout"),
             *[_retry(f"r{i}", "t1", params) for i in range(eval_logic.RETRY_MAX + 1)]]
    bad = next(c for c in eval_logic.evaluate(expected(call("c1", "wo_engine")), {}, nodes, trace_status="ok").checks
               if c.key == "retries")
    assert bad.result == "fail" and str(eval_logic.RETRY_MAX) in bad.note


def test_the_same_call_made_again_after_it_failed_is_seen_as_a_retry_without_a_retry_span():
    params = {"building": "B-301"}
    nodes = [ROOT, node("t1", "tool", "list_work_orders", agent="wo_engine", input=params, failed=True,
                        error="HTTP 403 Forbidden", start=1, end=2),
             node("t2", "tool", "list_work_orders", agent="wo_engine", input=params, start=3, end=4)]
    assert results(eval_logic.evaluate(expected(call("c1", "wo_engine")), {}, nodes, trace_status="ok"))["retries"] == "fail"


def test_a_failed_call_that_was_not_retried_breaks_no_retry_rule():
    nodes = [ROOT, node("t1", "tool", "x", agent="wo_engine", input={"q": 1}, failed=True, error="HTTP 403")]
    assert results(eval_logic.evaluate(expected(call("c1", "wo_engine")), {}, nodes, trace_status="ok"))["retries"] == "not_applicable"


def test_a_value_from_the_first_calls_output_in_the_second_calls_input_is_context_passed():
    exp = expected(call("c1", "wo_engine"), call("c2", "compliance", deps=("c1",)))
    passed = [ROOT, node("t1", "tool", "a", agent="wo_engine", start=1, end=2, output={"rows": [{"vendor_id": "V-1042"}]}),
              node("t2", "tool", "b", agent="compliance", start=3, end=4, input={"vendor_ids": ["V-1042"]})]
    lost = [ROOT, node("t1", "tool", "a", agent="wo_engine", start=1, end=2, output={"rows": [{"vendor_id": "V-1042"}]}),
            node("t2", "tool", "b", agent="compliance", start=3, end=4, input={"status": "expired"})]
    blind = [ROOT, node("t1", "tool", "a", agent="wo_engine", start=1, end=2, output=None),
             node("t2", "tool", "b", agent="compliance", start=3, end=4, input={"status": "expired"})]
    assert results(eval_logic.evaluate(exp, {}, passed, trace_status="ok"))["context_passing"] == "pass"
    bad = next(c for c in eval_logic.evaluate(exp, {}, lost, trace_status="ok").checks if c.key == "context_passing")
    assert bad.result == "fail" and "c1" in bad.note and "c2" in bad.note
    assert results(eval_logic.evaluate(exp, {}, blind, trace_status="ok"))["context_passing"] == "not_observable"


def test_a_context_passing_span_from_the_orchestrator_is_taken_as_the_link():
    exp = expected(call("c1", "wo_engine"), call("c2", "compliance", deps=("c1",)))
    nodes = [ROOT, node("t1", "tool", "a", agent="wo_engine", start=1, end=2, output={"n": 3}),
             node("cp", "span", "context_passing", metadata={"from_call": "1", "field": "vendor_id", "to_call": "2",
                                                             "param": "vendor_ids"}),
             node("t2", "tool", "b", agent="compliance", start=3, end=4, input={"status": "expired"})]
    assert results(eval_logic.evaluate(exp, {}, nodes, trace_status="ok"))["context_passing"] == "pass"


def test_the_evidence_state_a_tool_reports_is_compared_with_the_one_expected():
    exp = expected(call("c1", "compliance"), state="zero_results")
    said = lambda status: [ROOT, node("t1", "tool", "list_certificates", agent="compliance",  # noqa: E731
                                      output={"status": status, "rows": []})]
    assert results(eval_logic.evaluate(exp, {}, said("zero_results"), trace_status="ok"))["evidence_state"] == "pass"
    bad = next(c for c in eval_logic.evaluate(exp, {}, said("not_recorded"), trace_status="ok").checks
               if c.key == "evidence_state")
    assert bad.result == "fail" and "not_recorded" in bad.note
    silent = [ROOT, node("t1", "tool", "list_certificates", agent="compliance", output={"rows": []})]
    assert results(eval_logic.evaluate(exp, {}, silent, trace_status="ok"))["evidence_state"] == "not_observable"
    other = expected(call("c1", "compliance"), state="partial_failure")
    assert results(eval_logic.evaluate(other, {}, silent, trace_status="ok"))["evidence_state"] == "not_applicable"


def test_payloads_of_any_shape_never_raise():
    odd = [ROOT, node("t1", "tool", "x", agent="wo_engine", input="a plain string", output=[1, 2, {"a": None}],
                      start=None, end=None),
           node("t2", "tool", "y", agent=None, parent="nowhere", input=None, output="text"),
           {"id": "bare", "type": "tool", "name": "z", "level": "DEFAULT"}]
    exp = expected(call("c1", "wo_engine"), call("c2", "compliance", deps=("c1",)))
    r = eval_logic.evaluate(exp, {"buildings": ["B-1"], "period": {"from": "not a date"}}, odd, trace_status="ok",
                            sections=["dependency_order"])
    assert len(r.checks) == len(eval_logic.CHECKS) and r.verdict in ("pass", "fail")
    assert eval_logic.evaluate({}, None, [], trace_status="ok").verdict == "not_evaluated"
    assert eval_logic.evaluate(None, None, None, trace_status="error").verdict == "fail"


def test_the_result_is_written_out_for_the_score_row():
    nodes = [ROOT, node("t1", "tool", "list_work_orders", agent="wo_engine")]
    r = eval_logic.evaluate(expected(call("c1", "wo_engine"), call("c2", "compliance")), {}, nodes, trace_status="ok")
    out = r.as_details()
    assert out["evaluator"] == "logic-1" and len(out["checks"]) == 8
    assert out["checks"][1] == {"key": "plan_coverage", "result": "fail", "note": out["checks"][1]["note"]}
    assert out["counts"] == {"pass": 1, "fail": 1, "not_observable": 0, "not_applicable": 6}
