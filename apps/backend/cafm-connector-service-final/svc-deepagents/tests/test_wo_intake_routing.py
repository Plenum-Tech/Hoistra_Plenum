"""Mentioning a work order is not asking for one to be raised.

`classify_route_intent` fires ROUTE_WO_INTAKE on the bare noun, and a core route short-circuits
phase-2 engine selection entirely — so a question ABOUT work orders never reaches the engine
that owns the answer, and comes back from whatever the general agent could find.

Measured 16 Sep 2026 on the live stack: "How many work orders were scored for SafeLift, and
what's the average?" was classified as work-order intake ("create/triage a maintenance work
order"), answered from vendor scorecard rows, and reported "7 work orders" that were in fact
7 monthly scorecards for a different vendor.
"""
from __future__ import annotations

import pytest

from src.agents.session_workspace import (
    ROUTE_GENERAL,
    ROUTE_WO_INTAKE,
    classify_route_intent,
)

#: Questions ABOUT work orders. Every one is a read; none asks for anything to be created.
READS = [
    "How many work orders were scored for SafeLift, and what's the average?",
    "Which work orders are still open?",
    "What is the average work order cost this month?",
    "Show me the work orders for Gough and Kelly",
    "List work orders raised last week",
    "Compare work order volume between vendors",
]

#: Questions asking for one to be RAISED. These must keep the intake route.
INTAKE = [
    "raise a work order for the broken chiller",
    "create a work order for AHU-004",
    "the lift is stuck, please log a work order",
    "create wo for the leaking pipe",
]


@pytest.mark.parametrize("q", READS)
def test_a_question_about_work_orders_is_not_a_request_to_raise_one(q):
    assert classify_route_intent(q.lower(), {}) == ROUTE_GENERAL, (
        "an intake route short-circuits engine selection, so this never reaches the "
        "engine that owns the answer"
    )


@pytest.mark.parametrize("q", INTAKE)
def test_asking_for_a_work_order_still_reaches_intake(q):
    assert classify_route_intent(q.lower(), {}) == ROUTE_WO_INTAKE
