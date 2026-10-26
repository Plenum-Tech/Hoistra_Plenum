"""A sampling window must lie inside span retention, so every sampled answer still has its steps; the
batch kinds that need failure modes wait for M4 (spec "Open coding ..." 3.1, 10.3)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from trace_lab import sampling
from trace_lab.review_errors import Invalid

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)


def req(**kw):
    base = {"plan_step": "initial_uniform", "strategy": "uniform", "k": 15, "seed": 7, "channel": "chat",
            "since": NOW - timedelta(days=7), "until": NOW, "source": "bow"}
    base.update(kw)
    return base


def test_a_window_older_than_retention_is_refused(monkeypatch):
    monkeypatch.delenv("TRACE_RETENTION_DAYS", raising=False)
    sampling.check_request(req(since=NOW - timedelta(days=89)), now=NOW)
    with pytest.raises(Invalid) as e:
        sampling.check_request(req(since=NOW - timedelta(days=91)), now=NOW)
    assert e.value.reason == "window_outside_retention" and "90 days" in e.value.error


def test_the_retention_setting_is_respected(monkeypatch):
    monkeypatch.setenv("TRACE_RETENTION_DAYS", "30")
    with pytest.raises(Invalid) as e:
        sampling.check_request(req(since=NOW - timedelta(days=31)), now=NOW)
    assert e.value.reason == "window_outside_retention"


def test_a_window_must_end_after_it_starts_and_carry_a_time_zone():
    with pytest.raises(Invalid) as e:
        sampling.check_request(req(until=NOW - timedelta(days=8)), now=NOW)
    assert e.value.reason == "bad_window"
    with pytest.raises(Invalid):
        sampling.check_request(req(since=datetime(2026, 10, 10), until=datetime(2026, 10, 11)), now=NOW)


@pytest.mark.parametrize("extra", [{"mode_id": "m1"}, {"source": "depth_scan"}, {"proposal_run_id": "r1"}])
def test_options_that_name_no_real_failure_mode_or_depth_scan_are_refused(extra):
    """Failure modes exist now (Phase 5): the interim refusal `needs_failure_modes` is gone, and an option is refused
    only when it names nothing real (test_trace_lab_progress_m4 pins the accepted forms)."""
    with pytest.raises(Invalid) as e:
        sampling.check_request(req(strategy="enrich", plan_step="depth_search", **extra), now=NOW)
    assert e.value.reason == "bad_seed_source"


def test_golden_runs_are_never_a_sampling_channel():
    with pytest.raises(Invalid) as e:
        sampling.check_request(req(channel="golden"), now=NOW)
    assert e.value.reason == "bad_channel"


def test_a_monitor_flagged_request_names_between_1_and_200_answers(monkeypatch):
    monkeypatch.delenv("TRACE_RETENTION_DAYS", raising=False)
    flagged = {"strategy": "monitor_flagged", "seed": 7, "since": NOW - timedelta(days=7), "until": NOW,
               "params": {"monitor_batch_id": "batch-1"}, "items": [{"turn_id": "turn-1", "session_id": None, "reason": "flagged"}]}
    sampling.check_request(flagged, now=NOW)                                  # no plan_step or k needed: nothing is drawn
    too_many = [{"turn_id": f"turn-{i}"} for i in range(sampling.RECORDED_MAX + 1)]
    for items in (None, [], too_many, [{"turn_id": ""}], [{"reason": "no id"}], ["turn-1"]):
        with pytest.raises(Invalid) as e:
            sampling.check_request({**flagged, "items": items}, now=NOW)
        assert e.value.reason == "bad_items"
    with pytest.raises(Invalid) as e:
        sampling.check_request({**flagged, "since": NOW - timedelta(days=91)}, now=NOW)
    assert e.value.reason == "window_outside_retention"
    assert sampling.RECORDED_STRATEGIES == ("monitor_flagged",) and "monitor_flagged" not in sampling.STRATEGIES
