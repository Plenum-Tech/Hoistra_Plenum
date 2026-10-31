"""trace_lab.runner, deciding about one answered item without a database: infrastructure versus answers,
the stored outcome, which item runs next (G0 addendum 2.5)."""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from trace_lab import runner
from trace_lab.capture import CapturedTurn


def _ct(**over) -> CapturedTurn:
    base = dict(answer="759 work orders.", error=None, turn_id="turn-g1", spans=[], ledger=[], usd=0.01, ms=10,
                refused=[], cards=[], ended_by="workflow_completed")
    base.update(over)
    return CapturedTurn(**base)


@pytest.mark.parametrize("over,expected", [
    ({}, None),
    ({"ended_by": "timeout", "error": "timed out after 240 s"}, "infra"),
    ({"upstream_failures": [{"service": "udr", "status": 503}]}, "infra"),
    ({"upstream_failures": [{"service": "oi", "status": 401}]}, "infra"),
    ({"upstream_failures": [{"service": "udr", "status": None, "error": "ConnectError"}]}, "infra"),
    ({"upstream_failures": [{"service": "udr", "status": 500}]}, None),          # a 500 is an answer
    ({"tool_errors": [{"tool": "wo_list", "status_code": 429, "error": "busy"}]}, "infra"),
    ({"tool_errors": [{"tool": "wo_list", "status_code": None, "error": "Service 'udr' circuit open"}]}, "infra"),
    ({"tool_errors": [{"tool": "x", "status_code": 403, "error": "Not your company."}]}, None),
    ({"ended_by": "error", "error": "The model provider is overloaded"}, "infra"),
    ({"ended_by": "exception", "error": "RuntimeError: connection reset by peer"}, "infra"),
    ({"ended_by": "error", "error": "GraphRecursionError: needed too many tool steps"}, "chat_error"),
    ({"ended_by": "exception", "error": "ValueError: bad state"}, "chat_error"),
    ({"ended_by": "none", "error": "the chat ended without an answer"}, "chat_error"),
    ({"ended_by": "gate_interrupt"}, "gate"),
])
def test_infrastructure_versus_answers(over, expected):
    assert runner.classify(_ct(**over)) == expected


def test_infra_reasons_name_what_failed():
    assert "udr GET /api/tables" in runner.infra_reason(_ct(upstream_failures=[
        {"service": "udr", "method": "GET", "path": "/api/tables", "status": 503}]))
    assert runner.infra_reason(_ct()) is None


@dataclass
class G:
    outcome: str
    usd: float = 0.0


@pytest.mark.parametrize("grade,captured,expected", [
    (G("pass"), [_ct()], "pass"),
    (G("fail"), [_ct()], "fail"),
    (G("fail"), [_ct(refused=[{"method": "PATCH"}])], "degraded"),         # a refusal is never "wrong"
    (G("pass"), [_ct(), _ct(refused=[{"method": "POST"}])], "degraded"),
    (G("unsure"), [_ct(ended_by="gate_interrupt", answer="")], "degraded"),
    (G("fail"), [_ct(ended_by="error", error="needed too many tool steps", refused=[{}])], "fail"),
    (G("grading_pending"), [_ct()], "pending"),
    (G("needs_person"), [_ct()], "unsure"),
    (G("context_unavailable"), [_ct()], "degraded"),
    (G("stale_reference"), [_ct()], "stale_reference"),
    (G("something new"), [_ct()], "unsure"),
    ({"outcome": "pass"}, [_ct()], "pass"),
])
def test_the_stored_outcome(grade, captured, expected):
    assert runner.final_outcome(grade, captured) == expected


def test_items_run_rep_major_and_retry_with_the_next_attempt():
    keys = ["a", "b"]
    assert runner.next_item(keys, 2, set(), {}) == ("a", 1, 1)
    assert runner.next_item(keys, 2, {("a", 1)}, {}) == ("b", 1, 1)
    assert runner.next_item(keys, 2, {("a", 1), ("b", 1)}, {"a#2": 2}) == ("a", 2, 3)
    assert runner.next_item(keys, 2, {(k, r) for k in keys for r in (1, 2)}, {}) is None


def test_grades_become_json_whatever_their_shape():
    from collections import namedtuple

    NT = namedtuple("NT", "outcome usd")
    assert runner.grade_dict(G("pass", 0.02)) == {"outcome": "pass", "usd": 0.02}
    assert runner.grade_dict(NT("fail", 0.01)) == {"outcome": "fail", "usd": 0.01}
    assert runner.grade_dict({"outcome": "pass", "when": object()})["outcome"] == "pass"
    assert runner.grade_dict(None) == {}
