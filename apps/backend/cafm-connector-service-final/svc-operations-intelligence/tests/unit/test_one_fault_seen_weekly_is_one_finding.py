"""A standing fault seen in fifty windows is one finding, not fifty.

The historical sweep steps a 35-day window forward 7 days at a time, so consecutive windows
share 28 days of readings and a fault that stands all year is detected in roughly fifty of
them. The dedup guard existed for exactly this, but when the run was dated it added
`date(window_end) == as_of` to the key — which guarantees a new row every window, because no
two windows end on the same day.

Measured on hoistra_test on 23 Sep 2026: Harbour Point held 48 schedule_mismatch rows, every
one carrying the identical 400.0%, marching weekly from 2025-10-27 to 2026-09-21. Each was
annualised in full, so the register summed them to GBP576,399 for a fault costing about
GBP20,000 a year. The building reported GBP1,393,173 of open anomalies against a GBP972,000
annual energy bill.

Identity is now window overlap, which also expresses the thing the old guard's own comment
wanted and could not say: the same rule firing in March and again in July looked at disjoint
months, so it stays two events.
"""
from __future__ import annotations

import ast
import os

from src.engines.energy import anomalies


class TestWhatMakesTwoDetectionsOneFinding:
    @staticmethod
    def _src():
        return open(anomalies.__file__, encoding="utf-8").read()

    def test_identity_is_window_overlap(self):
        src = self._src()
        assert "_new_window_start = _window_end - timedelta(days=35)" in src
        assert "EnergyAnomaly.detected_at) >= _new_window_start" in src

    def test_the_run_date_is_no_longer_part_of_the_key(self):
        """The whole bug: keying on the window's end date makes every window its own finding."""
        assert "func.date(EnergyAnomaly.window_end) == _aware(as_of).date()" not in self._src()

    def test_the_percentage_is_no_longer_part_of_the_key(self):
        """A drift that deepens weekly reports a new percentage every window and escaped it."""
        assert "EnergyAnomaly.metric_pct == Decimal(str(hit[\"metric_pct\"]))" not in self._src()

    def test_a_settled_finding_is_never_continued(self):
        """Acting on a finding closes it; a later sighting is then genuinely new."""
        assert "EnergyAnomaly.status.notin_(_SETTLED_STATUSES)" in self._src()

    def test_a_continuation_updates_rather_than_inserts(self):
        src = self._src()
        assert "_observe_again(existing, hit," in src
        assert "def _observe_again(" in src


class TestWhatASecondSightingDoesToTheFinding:
    """_observe_again is pure enough to drive with a stand-in row."""

    class Row:
        def __init__(self, **kw):
            self.window_start = None
            self.window_end = None
            self.metric_pct = 0
            self.excess_kwh = None
            self.annualised_excess_kwh = None
            self.financial_gbp = None
            self.currency = "GBP"
            self.tariff_used = None
            self.detail_json = {}
            for k, v in kw.items():
                setattr(self, k, v)

    @staticmethod
    def _at(day):
        import datetime as dt
        return dt.datetime(2026, 1, day, tzinfo=dt.timezone.utc)

    def _fold(self, row, *, pct, gbp, end_day, start_day):
        anomalies._observe_again(
            row, {"metric_pct": pct, "financial_gbp": gbp, "excess_kwh": 10.0,
                  "annualised_excess_kwh": 100.0, "currency": "GBP", "detail": {}, "impact": None},
            window_end=self._at(end_day), window_start=self._at(start_day),
            tariff=0.21, currency="GBP")
        return row

    def test_the_money_becomes_the_newest_estimate_not_the_sum(self):
        """It is a rate — kWh per year at the observed deviation. Rates over overlapping
        windows cannot be added, which is the whole defect."""
        row = self.Row(window_end=self._at(10), metric_pct=400, financial_gbp=12979)
        self._fold(row, pct=400, gbp=19860, end_day=17, start_day=2)
        assert float(row.financial_gbp) == 19860

    def test_the_window_grows_to_cover_every_sighting(self):
        row = self.Row(window_start=self._at(5), window_end=self._at(10))
        self._fold(row, pct=400, gbp=100, end_day=17, start_day=2)
        assert row.window_start == self._at(2), "back to the earliest evidence"
        assert row.window_end == self._at(17), "forward to the latest"

    def test_the_peak_is_kept_so_a_worse_past_still_says_so(self):
        row = self.Row(window_end=self._at(10), metric_pct=400, financial_gbp=90000)
        self._fold(row, pct=120, gbp=1000, end_day=17, start_day=2)
        rec = row.detail_json["recurrence"]
        assert rec["peak_financial_gbp"] == 90000
        assert rec["peak_metric_pct"] == 400
        assert float(row.financial_gbp) == 1000, "current is current; the peak sits beside it"

    def test_the_sightings_are_counted(self):
        row = self.Row(window_end=self._at(10), financial_gbp=100)
        self._fold(row, pct=1, gbp=1, end_day=17, start_day=2)
        assert row.detail_json["recurrence"]["observations"] == 2
        self._fold(row, pct=1, gbp=1, end_day=24, start_day=9)
        assert row.detail_json["recurrence"]["observations"] == 3
