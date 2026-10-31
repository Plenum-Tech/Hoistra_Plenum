"""The AI proposal spend caps (Phase 5): the per-run and monthly limits come from the environment and fall back to
their defaults when unset, unreadable or not positive; the month's proposal spend counts only the three proposal job
kinds. The refusal classes the proposal routes raise are Phase 4's review_errors, pinned here as Phase 5 uses them.
The month-spend query is in test_trace_lab_proposal_caps_sql.py (needs a scratch database)."""
from __future__ import annotations

import inspect

import pytest

from trace_lab import jobs, review_errors, spend


def test_conflict_carries_reason_error_and_extra_fields():
    e = review_errors.Conflict("mode_not_live", "This failure mode was merged.", live_mode_id="m-2")
    assert (e.reason, e.error, e.extra) == ("mode_not_live", "This failure mode was merged.", {"live_mode_id": "m-2"})


def test_forbidden_carries_reason_and_error():
    e = review_errors.Forbidden("platform_only", "Only the platform team decides grouping proposals.")
    assert (e.reason, e.error) == ("platform_only", "Only the platform team decides grouping proposals.")


def test_the_proposal_kinds_are_exactly_the_proposal_jobs():
    assert spend.PROPOSAL_JOB_KINDS == ("proposal_grouping", "proposal_depth_scan", "proposal_cells")
    assert set(spend.PROPOSAL_JOB_KINDS) == {k for k in jobs.KINDS if k.startswith("proposal_")}
    assert not set(spend.PROPOSAL_JOB_KINDS) & set(spend.JOB_KINDS)     # golden spend and proposal spend stay apart


def test_caps_default_and_read_the_environment(monkeypatch):
    monkeypatch.delenv("TRACE_LAB_PROPOSAL_RUN_MAX_USD", raising=False)
    monkeypatch.delenv("TRACE_LAB_PROPOSALS_MONTHLY_USD", raising=False)
    assert spend.proposal_run_max() == 25.0
    assert spend.proposals_month_cap() == 100.0
    monkeypatch.setenv("TRACE_LAB_PROPOSAL_RUN_MAX_USD", "7.5")
    monkeypatch.setenv("TRACE_LAB_PROPOSALS_MONTHLY_USD", "not a number")
    assert spend.proposal_run_max() == 7.5
    assert spend.proposals_month_cap() == 100.0


@pytest.mark.parametrize("raw", ["", "   ", "0", "-3", "nan", "inf", "-inf", "1e999", "abc", "5 dollars"])
def test_an_unusable_cap_falls_back_to_its_default(monkeypatch, raw):
    monkeypatch.setenv("TRACE_LAB_PROPOSAL_RUN_MAX_USD", raw)
    monkeypatch.setenv("TRACE_LAB_PROPOSALS_MONTHLY_USD", raw)
    assert spend.proposal_run_max() == 25.0
    assert spend.proposals_month_cap() == 100.0


def test_a_cap_with_spaces_around_it_is_read(monkeypatch):
    monkeypatch.setenv("TRACE_LAB_PROPOSAL_RUN_MAX_USD", " 12.25 ")
    assert spend.proposal_run_max() == 12.25


def test_the_month_spend_reader_takes_an_optional_connection():
    sig = inspect.signature(spend.proposals_month_spent)
    assert list(sig.parameters) == ["conn"] and sig.parameters["conn"].default is None
    assert inspect.iscoroutinefunction(spend.proposals_month_spent)
