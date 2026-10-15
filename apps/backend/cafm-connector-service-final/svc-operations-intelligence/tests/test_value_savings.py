"""Cost savings: the period, and the warnings that keep a figure from being quoted before it is checked.

The read itself was run against hoistra_test (Bishopsgate, September 2026: 794 jobs, £365,748
reactive (98.8 %), 9 overcharge lines £1,409.20, AHU-3 £40,151/yr at stake on WO-B-301-4527);
these hold the rules that do not need the database."""
from __future__ import annotations

from datetime import date

from src.engines import value_savings as sv


def test_the_period_defaults_to_last_month():
    a, b, label = sv.period_bounds(None, date(2026, 10, 1))
    assert (a, b, label) == (date(2026, 9, 1), date(2026, 10, 1), "September 2026")
    a, b, _ = sv.period_bounds("last month", date(2026, 1, 15))
    assert (a, b) == (date(2025, 12, 1), date(2026, 1, 1))
    assert sv.period_bounds("this_year", date(2026, 10, 1))[0] == date(2026, 1, 1)


def test_warnings_flag_unrecovered_value_an_outlier_and_an_implausible_callout_rate():
    out = {"ledger": {"modules": [{"module": "Energy", "detected": 320107.0, "saved": 0.0,
                                   "top": [{"what": "Baseline drift - Bishopsgate", "detected": 298273.0}, {"what": "x", "detected": 1090.0}]}]},
           "open_jobs": [], "repeat_failures": [{"asset": "LIFT-4471", "reactive_jobs": 76, "jobs_per_month": 76.0}]}
    w = sv.warnings(out)
    assert any("nothing saved yet" in x for x in w)
    assert any("93% of the module" in x and "baseline and tariff" in x for x in w)
    assert any("LIFT-4471" in x and "duplicate records" in x for x in w)
