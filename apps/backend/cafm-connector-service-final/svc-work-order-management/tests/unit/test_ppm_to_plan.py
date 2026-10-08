"""PPM to plan compares like with like: visits done against visits due, per asset, so far.

Until 6 Oct 2026 the card read "196 of 125 visits" (156.8%) at Bishopsgate. Done counted one row
per asset per visit, year to date; the plan was each contract's visits_per_year, a whole-year
count of visits to the site. A quarterly contract over 21 fan coil units books 63 visits by
October against a "plan" of 4. Call-out contracts (visits_per_year = 0) fell back to what was
booked and counted as planned maintenance.
"""
from __future__ import annotations

from src.services import maintenance as mx

OCT_6 = 279 / 365  # share of 2026 gone by 6 October


def row(**kw):
    base = {"contract": "C", "planned": 0, "due": 0, "done": 0, "done_due": None, "missed": 0,
            "late": 0, "deferred": 0, "reports": 0, "buildings": 1, "next_due": None,
            "visits_per_year": None}
    base.update(kw)
    return base


def test_a_quarterly_contract_over_21_units_is_measured_against_its_63_visits_due_not_4():
    r = mx.ppm_contract_row(row(contract="Terminal units PPM", visits_per_year=4, planned=63,
                                due=63, done=63, done_due=63), elapsed=OCT_6)
    assert r["visits_to_plan"] == {"done": 63, "plan": 63, "plan_is_committed": False,
                                   "basis": "visits due so far"}
    assert r["completion_pct"] == 100.0 and r["state"] == mx.PPM_STATE_TO_PLAN
    # The per-asset count is well over the contract's yearly count: no false alarm.
    assert r["booking"]["short_by"] == 0


def test_visits_still_to_come_are_not_counted_against_the_contract():
    r = mx.ppm_contract_row(row(visits_per_year=12, planned=11, due=10, done=9, done_due=9,
                                missed=1), elapsed=OCT_6)
    assert r["visits_to_plan"]["plan"] == 10 and r["completion_pct"] == 90.0


def test_a_monthly_contract_with_three_visits_booked_by_october_is_flagged_under_booked():
    r = mx.ppm_contract_row(row(contract="Water hygiene monitoring", visits_per_year=12,
                                planned=3, due=3, done=3, done_due=3), elapsed=OCT_6)
    assert r["completion_pct"] == 100.0  # every visit booked was done...
    assert r["booking"]["expected_by_now"] == 9.2 and r["booking"]["short_by"] == 6
    assert r["state"] == mx.PPM_STATE_WATCH  # ...but too few were booked
    assert r["note"] == "3 visits booked by now; the contract's 12 a year implies about 9.2"


def test_a_call_out_contract_is_kept_for_reference_and_marked_as_such():
    r = mx.ppm_contract_row(row(contract="Lift call-out", visits_per_year=0, planned=3, due=3,
                                done=3, done_due=3), elapsed=OCT_6)
    assert r["kind"] == "call-out" and r["booking"] is None


def test_a_visit_done_ahead_of_its_date_is_not_counted_against_today():
    r = mx.ppm_contract_row(row(visits_per_year=4, planned=4, due=3, done=4, done_due=3),
                            elapsed=OCT_6)
    assert (r["done"], r["done_ahead"], r["completion_pct"]) == (3, 1, 100.0)


def test_outside_a_year_to_date_window_there_is_no_booking_check():
    r = mx.ppm_contract_row(row(visits_per_year=12, planned=3, due=3, done=3, done_due=3),
                            elapsed=None)
    assert r["booking"]["expected_by_now"] is None and r["booking"]["short_by"] == 0
