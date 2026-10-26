"""How the value check and the grader combine into an outcome, and which outcomes go to a person.
15 Oct 2026. Spec: Golden set §4.1, §4.5-§4.7, R28."""
from __future__ import annotations

import pytest

from trace_lab import grading


def _combine(det="none", verdict="pass", evaluation="objective", **kw):
    args = dict(evaluation=evaluation, det={"status": det}, grade={"verdict": verdict} if verdict else None,
                refused=False, gate=False, chat_error=False, stale=False, judgment_validated=False)
    args.update(kw)
    return grading.combine(**args)


@pytest.mark.parametrize("det,verdict,expected", [
    ("mismatch", "fail", "fail"), ("mismatch", "unsure", "fail"), ("mismatch", "pass", "unsure"),
    ("all_found", "pass", "pass"), ("all_found", "fail", "fail"), ("all_found", "unsure", "unsure"),
    ("unclear", "unsure", "unsure"), ("none", "unsure", "unsure"),
    ("unclear", "pass", "pass"), ("unclear", "fail", "fail"), ("none", "pass", "pass"), ("none", "fail", "fail"),
])
def test_the_objective_combination_table(det, verdict, expected):
    assert _combine(det, verdict) == expected


def test_a_chat_error_fails_and_a_refusal_or_gate_is_degraded():
    assert _combine(verdict=None, chat_error=True) == "fail"
    assert _combine(refused=True) == "degraded"
    assert _combine(gate=True) == "degraded"


def test_a_stale_case_that_fails_is_a_stale_reference_and_a_stale_pass_stays_pass():
    assert _combine("all_found", "fail", stale=True) == "stale_reference"
    assert _combine("all_found", "pass", stale=True) == "pass"


def test_judgment_needs_a_person_until_the_grader_is_validated():
    assert _combine(verdict="pass", evaluation="judgment") == "needs_person"
    assert _combine(verdict="fail", evaluation="judgment", judgment_validated=True) == "fail"
    assert _combine(verdict="unsure", evaluation="judgment", judgment_validated=True) == "unsure"


def test_objective_results_go_to_people_when_the_grader_is_demoted():
    assert _combine("all_found", "pass", objective_demoted=True) == "needs_person"


def test_lost_follow_up_context_is_never_pass_or_fail():
    assert _combine("all_found", "pass", context_lost=True) == "context_unavailable"
    assert _combine(verdict=None, chat_error=True, context_lost=True) == "context_unavailable"


def test_no_grade_yet_is_pending():
    assert _combine("all_found", None) == "grading_pending"


def test_spot_checks_are_deterministic_and_near_the_rate(monkeypatch):
    picks = [grading.is_spot_check("run-1", f"c{i}", 1, rate=0.10) for i in range(2000)]
    assert picks == [grading.is_spot_check("run-1", f"c{i}", 1, rate=0.10) for i in range(2000)]
    assert 140 < sum(picks) < 260
    monkeypatch.setenv("TRACE_LAB_GOLDEN_SPOTCHECK", "0")
    assert not any(grading.is_spot_check("run-1", f"c{i}", 1) for i in range(200))
