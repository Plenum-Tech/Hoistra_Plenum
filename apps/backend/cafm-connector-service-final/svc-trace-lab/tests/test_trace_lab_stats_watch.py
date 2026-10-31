"""The stats helpers Phase 7 (Watch) appends to trace_lab/stats.py: the eval-case decision, the replay bootstrap summary
and the ISO week label. 17 Oct 2026. Pure functions: no database, no model."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from trace_lab import stats

UTC = timezone.utc


def test_case_decision_repo_rule():
    ok = stats.case_decision("regression", 5, 5, None, planned=5, not_decided=0)
    assert ok == {"decision": "pass", "reason": "regression case passed 5 of 5 runs"}
    blocked = stats.case_decision("regression", 4, 5, None, planned=5, not_decided=0)
    assert blocked == {"decision": "block", "reason": "regression case failed 1 of 5 runs"}
    unsure = stats.case_decision("regression", 4, 4, None, planned=5, not_decided=1)
    assert unsure == {"decision": "incomplete", "reason": "regression case has 1 runs without a verdict of 5; "
                                                          "incomplete evidence never passes"}
    assert stats.case_decision("regression", 3, 3, None, planned=5, not_decided=0)["decision"] == "incomplete"   # n < planned
    assert stats.case_decision("regression", 0, 0, None, planned=5, not_decided=5)["decision"] == "incomplete"   # nothing ran
    assert stats.case_decision("regression", 3, 4, None, planned=5, not_decided=1)["decision"] == "block"        # a failure decides
    cap = stats.case_decision("capability", 2, 5, 0.4, planned=5, not_decided=0)
    assert cap == {"decision": "pass", "reason": "capability case passed 2 of 5, baseline 0.4, not blocking"}
    assert stats.case_decision("capability", 0, 5, 0.2)["decision"] == "pass"       # a capability case never blocks
    with pytest.raises(ValueError):
        stats.case_decision("unclassified", 5, 5)


def test_case_decision_without_a_plan_counts_undecided_runs_as_missing():
    """With no planned count the total is the decided runs plus the runs without a verdict."""
    assert stats.case_decision("regression", 3, 3) == {"decision": "pass", "reason": "regression case passed 3 of 3 runs"}
    held = stats.case_decision("regression", 3, 3, not_decided=2)
    assert held == {"decision": "incomplete", "reason": "regression case has 2 runs without a verdict of 5; "
                                                        "incomplete evidence never passes"}
    assert stats.case_decision("regression", 0, 0)["decision"] == "incomplete"      # nothing ran, nothing planned
    # more decided runs than were planned is not missing evidence
    assert stats.case_decision("regression", 6, 6, planned=5)["decision"] == "pass"
    # a failure blocks even when other runs are still undecided, and a missing run never turns a block into a pass
    assert stats.case_decision("regression", 0, 1, planned=5, not_decided=4)["decision"] == "block"


def test_case_decision_capability_never_blocks_and_names_a_missing_baseline():
    assert stats.case_decision("capability", 0, 5, 0.0) == {
        "decision": "pass", "reason": "capability case passed 0 of 5, baseline 0, not blocking"}
    assert stats.case_decision("capability", 3, 5, None)["reason"] == "capability case passed 3 of 5, baseline none, not blocking"
    assert stats.case_decision("capability", 0, 0, 0.6, planned=5, not_decided=5)["decision"] == "pass"


@pytest.mark.parametrize("kind,passes,n,not_decided", [
    ("regression", 6, 5, 0),       # more passes than runs would read as a pass
    ("regression", -1, 5, 0),
    ("regression", 0, -1, 0),
    ("regression", 5, 5, -1),
    ("capability", 6, 5, 0),
])
def test_case_decision_refuses_counts_that_cannot_be_true(kind, passes, n, not_decided):
    with pytest.raises(ValueError):
        stats.case_decision(kind, passes, n, 0.4, planned=5, not_decided=not_decided)


def test_summarize_rollouts_repo_vector():
    records = [{"passed": True, "failures": [], "steps": 2},
               {"passed": False, "failures": ["judge:a"], "steps": 4},
               {"passed": False, "failures": ["judge:a", "check:b"], "steps": 6}]
    first = stats.summarize_rollouts(records, bootstrap_iterations=100, seed=7)
    assert first == stats.summarize_rollouts(records, bootstrap_iterations=100, seed=7)   # same seed, same interval
    assert first["n"] == 3 and first["failures"] == 2
    assert first["failure_rate"] == pytest.approx(2 / 3)
    assert 0.0 <= first["ci_low"] <= 2 / 3 <= first["ci_high"] <= 1.0
    assert first["mode_counts"] == {"judge:a": 2, "check:b": 1}          # a record listing two modes counts in both
    assert first["steps"] == {"min": 2, "median": 4, "max": 6}
    assert first["wilson_high"] == stats.wilson_interval(2, 3)[1]
    assert stats.REPLAY_BOOTSTRAP_REPS == 2_000
    assert stats.summarize_rollouts([{"passed": True}])["steps"] is None
    with pytest.raises(ValueError):
        stats.summarize_rollouts([])
    with pytest.raises(ValueError):
        stats.summarize_rollouts(records, confidence=1.5)


def test_summarize_rollouts_edges():
    # the default is the replay harness size, and a different seed moves the interval but never the counts
    records = [{"passed": i % 4 != 0, "failures": ["x"] if i % 4 == 0 else [], "steps": i} for i in range(1, 21)]
    default = stats.summarize_rollouts(records)
    assert default == stats.summarize_rollouts(records, bootstrap_iterations=stats.REPLAY_BOOTSTRAP_REPS, seed=7)
    other = stats.summarize_rollouts(records, seed=8)
    assert (other["n"], other["failures"], other["failure_rate"]) == (default["n"], default["failures"], default["failure_rate"])
    assert 0.0 <= default["ci_low"] <= default["failure_rate"] <= default["ci_high"] <= 1.0
    # an even number of step counts reads the middle pair; records without steps are left out of it
    even = stats.summarize_rollouts([{"passed": True, "steps": 1}, {"passed": True, "steps": 2}, {"passed": True}],
                                    bootstrap_iterations=10)
    assert even["steps"] == {"min": 1, "median": 1.5, "max": 2}
    # a passing record's failures are not counted; failure_modes is read when failures is absent
    modes = stats.summarize_rollouts([{"passed": True, "failures": ["ignored"]},
                                      {"passed": False, "failure_modes": ["m1"]},
                                      {"passed": False}], bootstrap_iterations=10)
    assert modes["mode_counts"] == {"m1": 1} and modes["failures"] == 2
    # nothing failed: a point interval at zero
    clean = stats.summarize_rollouts([{"passed": True}] * 5, bootstrap_iterations=50)
    assert (clean["failure_rate"], clean["ci_low"], clean["ci_high"], clean["mode_counts"]) == (0.0, 0.0, 0.0, {})
    for bad in (dict(confidence=0), dict(confidence=1), dict(bootstrap_iterations=0)):
        with pytest.raises(ValueError):
            stats.summarize_rollouts(records, **bad)


def test_iso_week_label():
    assert stats.iso_week_label(datetime(2026, 10, 5, tzinfo=UTC)) == "2026-W41"
    assert stats.iso_week_label(datetime(2025, 12, 29, tzinfo=UTC)) == "2026-W01"     # the ISO year, not the calendar year
    assert stats.iso_week_label(datetime(2026, 1, 1, 9, tzinfo=UTC)) == "2026-W01"
    late = datetime(2026, 10, 11, 23, 30, tzinfo=timezone(timedelta(hours=-5)))        # Monday 04:30 UTC
    assert stats.iso_week_label(late) == "2026-W42"
    assert stats.iso_week_label(datetime(2026, 10, 12)) == "2026-W42"                  # a naive time is read as UTC


def test_iso_week_label_year_ends_and_week_53():
    assert stats.iso_week_label(datetime(2026, 12, 31, 23, 59, tzinfo=UTC)) == "2026-W53"   # 2026 has 53 ISO weeks
    assert stats.iso_week_label(datetime(2027, 1, 3, 23, 59, tzinfo=UTC)) == "2026-W53"     # Sunday of that week
    assert stats.iso_week_label(datetime(2027, 1, 4, tzinfo=UTC)) == "2027-W01"
    assert stats.iso_week_label(datetime(2026, 10, 11, 23, 59, 59, tzinfo=UTC)) == "2026-W41"   # the last second of a week
    plus = datetime(2026, 10, 12, 3, 0, tzinfo=timezone(timedelta(hours=5)))               # Sunday 22:00 UTC
    assert stats.iso_week_label(plus) == "2026-W41"
