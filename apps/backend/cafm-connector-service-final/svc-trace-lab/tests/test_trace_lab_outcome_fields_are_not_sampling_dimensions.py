"""A batch may be split by channel, routing, the asker's role or page - never by an outcome (a rating,
ok, a verdict, a label, a judge result), because a split chosen by outcome would bias what people read
(spec "Open coding ..." 3.3)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from trace_lab import sampling
from trace_lab.review_errors import Invalid

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("dim", ["feedback_rating", "ok", "verdict", "label", "failure_present", None])
def test_outcome_fields_are_refused(dim):
    with pytest.raises(Invalid) as e:
        sampling.check_request({"plan_step": "dimension", "strategy": "dimension", "k": 30, "dimension": dim,
                                "since": NOW - timedelta(days=7), "until": NOW}, now=NOW)
    assert e.value.reason == "dimension_not_allowed"


@pytest.mark.parametrize("dim", sampling.DIMENSIONS)
def test_the_four_structural_dimensions_are_allowed(dim):
    sampling.check_request({"plan_step": "dimension", "strategy": "dimension", "k": 30, "dimension": dim,
                            "since": NOW - timedelta(days=7), "until": NOW}, now=NOW)
    assert sampling.DIMENSIONS == ("channel", "route_target", "asker_role", "page")
