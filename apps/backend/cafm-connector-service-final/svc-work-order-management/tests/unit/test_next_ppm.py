"""Next PPM date per asset: the frequency table, the calendar arithmetic, the labelling.

The thing under test is mostly honesty. A next date that came from somebody's booking and a
next date this service worked out from a cadence are different claims, and an asset with no
schedule at all must come back as null with a reason rather than as a plausible-looking date.

The calendar arithmetic gets its own attention because it is where a projection goes quietly
wrong: a quarterly visit last done on 31 January is due on 30 April, and a naive month add
either overflows or silently lands in May.
"""
from __future__ import annotations

from datetime import date, datetime

import pytest

from src.services.maintenance import (
    FREQUENCY_MONTHS,
    PLANNED_KINDS,
    _add_months,
    _as_date,
    _freq_months,
    _ppm_summary,
)


class TestFrequencyIsMatchedNotGuessed:
    """Both databases spell the same cadence differently; none of them may be guessed at."""

    @pytest.mark.parametrize("spelling,months", [
        ("monthly", 1), ("Monthly", 1), ("MONTHLY", 1),
        ("Quarterly", 3), ("quarterly", 3), ("three-monthly", 3),
        ("Six-monthly", 6), ("six monthly", 6), ("Biannual", 6), ("semi-annual", 6),
        ("Annual", 12), ("yearly", 12), ("annually", 12),
        ("Weekly", 0.25), ("fortnightly", 0.5), ("bi-weekly", 0.5),
        ("Biennial", 24), ("five-yearly", 60),
    ])
    def test_the_spellings_both_databases_actually_use(self, spelling, months):
        assert _freq_months(spelling) == months

    @pytest.mark.parametrize("junk", ["", "   ", "as required", "on failure", "ad hoc",
                                      "when needed", "PPM", "12", None, "every so often"])
    def test_an_unrecognised_frequency_is_none_never_a_guess(self, junk):
        # A wrong interval produces a confident wrong date, which is worse than no date.
        assert _freq_months(junk) is None

    def test_punctuation_and_spacing_do_not_change_the_answer(self):
        for v in ("six-monthly", "six monthly", "SixMonthly", "Six_Monthly", " six-monthly "):
            assert _freq_months(v) == 6

    def test_every_table_entry_is_a_positive_number_of_months(self):
        assert all(m > 0 for m in FREQUENCY_MONTHS.values())

    def test_planned_kinds_are_lowercase_so_matching_holds(self):
        assert all(k == k.lower() for k in PLANNED_KINDS)


class TestCalendarArithmetic:
    """Where a projection goes quietly wrong."""

    def test_a_quarterly_visit_from_the_end_of_january_lands_on_the_end_of_april(self):
        assert _add_months(date(2026, 1, 31), 3) == date(2026, 4, 30)

    def test_it_does_not_overflow_into_the_next_month(self):
        assert _add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
        assert _add_months(date(2024, 1, 31), 1) == date(2024, 2, 29)   # leap year

    def test_it_crosses_the_year_boundary(self):
        assert _add_months(date(2026, 11, 15), 3) == date(2027, 2, 15)
        assert _add_months(date(2026, 6, 1), 12) == date(2027, 6, 1)
        assert _add_months(date(2026, 6, 1), 24) == date(2028, 6, 1)

    def test_a_mid_month_date_keeps_its_day(self):
        assert _add_months(date(2026, 3, 12), 6) == date(2026, 9, 12)

    def test_sub_month_cadences_are_added_as_days(self):
        assert _add_months(date(2026, 3, 12), 0.25) == date(2026, 3, 19)   # weekly
        assert _add_months(date(2026, 3, 12), 0.5) == date(2026, 3, 26)    # fortnightly

    def test_the_result_is_always_after_the_input(self):
        start = date(2026, 1, 31)
        for m in FREQUENCY_MONTHS.values():
            assert _add_months(start, m) > start


class TestDateFromEitherDatabase:
    """scheduled_date is a date on one database and a varchar on the other."""

    def test_a_text_date_is_read(self):
        assert _as_date("2024-04-08") == date(2024, 4, 8)

    def test_a_text_timestamp_is_read_down_to_the_date(self):
        assert _as_date("2024-04-08T13:45:00Z") == date(2024, 4, 8)

    def test_a_real_date_passes_through(self):
        assert _as_date(date(2024, 4, 8)) == date(2024, 4, 8)

    def test_a_datetime_becomes_its_date(self):
        assert _as_date(datetime(2024, 4, 8, 13, 45)) == date(2024, 4, 8)

    @pytest.mark.parametrize("junk", [None, "", "not a date", "TBC", "0000-00-00"])
    def test_unparseable_text_is_none_rather_than_an_exception(self, junk):
        assert _as_date(junk) is None


def row(confidence, days=None, overdue=False, superseded=False):
    return {"confidence": confidence, "days_until": days, "overdue": overdue,
            "superseded_by_completion": superseded}


class TestSummaryCountsWhatThePageLeadsWith:
    def test_an_empty_portfolio_says_nothing_is_scheduled(self):
        s = _ppm_summary([], 0)
        assert s["scheduled_anywhere"] is False
        assert s["booked"] == s["projected"] == s["unknown"] == 0

    def test_a_portfolio_with_no_schedule_loaded_is_all_unknown(self):
        s = _ppm_summary([row("unknown") for _ in range(54)], 54)
        assert s == {"assets": 54, "booked": 0, "projected": 0, "unknown": 54,
                     "overdue": 0, "due_next_30_days": 0, "overdue_superseded": 0,
                     "scheduled_anywhere": False}

    def test_projected_dates_alone_do_not_count_as_scheduled(self):
        # Nobody has booked anything; the dates are this service's arithmetic.
        s = _ppm_summary([row("projected", days=10) for _ in range(3)], 3)
        assert s["projected"] == 3
        assert s["scheduled_anywhere"] is False

    def test_one_booking_makes_the_portfolio_scheduled(self):
        s = _ppm_summary([row("booked", days=5), row("unknown")], 2)
        assert s["scheduled_anywhere"] is True

    def test_due_in_thirty_days_excludes_the_overdue_and_the_distant(self):
        rows = [row("booked", days=-1), row("booked", days=0), row("booked", days=30),
                row("booked", days=31), row("unknown")]
        assert _ppm_summary(rows, 5)["due_next_30_days"] == 2

    def test_stale_bookings_are_counted_apart_from_the_overdue_total(self):
        rows = [row("booked", days=-219, overdue=True, superseded=True),
                row("projected", days=-10, overdue=True)]
        s = _ppm_summary(rows, 2)
        assert s["overdue"] == 2
        assert s["overdue_superseded"] == 1   # so the page can say 1 of the 2 is stale data
