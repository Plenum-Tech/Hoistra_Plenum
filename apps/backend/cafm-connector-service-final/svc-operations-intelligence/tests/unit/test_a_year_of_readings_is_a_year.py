"""A year of readings annualises as a year, and a gap costs what the building pays for it.

Two faults found together on hoistra_test on 23 Sep 2026, both on the same card.

The window was measured as ``len(months_seen)`` — the distinct calendar months the readings
fall in. Half-hourly data running 22 Sep 2025 to 21 Sep 2026 is 364 days, but it touches
thirteen calendar months: eleven whole ones and two partial Septembers that together make one.
So a full year was annualised by 12/13 and every derived EUI came out 7.7% low. The ratio was
exact in the stored data: Harbour Point's 241.17 was held as 222.47, Ashgrove's 173.20 as
159.87.

7.7% off an EUI is far more than 7.7% off the card, because the card prices the GAP above a
reference. Harbour Point's gap to TM46's 212 fell from 29.2 to 10.5 kWh/m², and its cost above
benchmark from about GBP117k to GBP42k — 64% of the number gone.

The second fault priced that excess at the market reference band's low end while the page
priced the same kWh at the contracted rate, so one excess carried two costs: GBP31,222 stored
against GBP42k displayed. A contracted rate is on record per supply in
``energy_meters.tariff_gbp_per_kwh``; that is what the building pays, and a whole-building gap
is priced at the blend of it, weighted by what the building actually burns.
"""
from __future__ import annotations

import pytest

from src.engines.energy import benchmarks as bench
from src.engines.energy import us_ratings


class TestAYearIsAYear:
    """``annualise`` scales by elapsed metered time, and takes it fractionally."""

    def test_a_full_year_is_not_scaled(self):
        assert bench.annualise(3_422_410, 12.0) == pytest.approx(3_422_410)

    def test_364_days_of_half_hourly_data_reads_as_a_year_not_13_months(self):
        # 17,520 half-hours, as consumption_for_building now measures it.
        months = 17_520 * 30 / us_ratings.MINUTES_PER_MONTH
        assert months == pytest.approx(11.99, abs=0.01)
        out = bench.derive_eui(total_kwh=3_422_410, months=months, gfa_m2=14_200)
        assert out["eui_kwh_per_m2"] == pytest.approx(241.2, abs=0.5)

    def test_the_old_calendar_month_count_is_what_understated_it(self):
        """The regression, stated as the arithmetic that produced the wrong figure."""
        was = bench.derive_eui(total_kwh=3_422_410, months=13, gfa_m2=14_200)
        assert was["eui_kwh_per_m2"] == pytest.approx(222.47, abs=0.01)
        now = bench.derive_eui(total_kwh=3_422_410, months=11.99, gfa_m2=14_200)
        assert now["eui_kwh_per_m2"] > was["eui_kwh_per_m2"]
        assert was["eui_kwh_per_m2"] / now["eui_kwh_per_m2"] == pytest.approx(12 / 13, abs=0.01)

    def test_a_half_year_still_doubles(self):
        """The property the month count existed for: unmetered time is not counted as zero."""
        assert bench.annualise(1_000_000, 6.0) == pytest.approx(2_000_000)

    def test_months_are_reported_whole_and_annualised_fractional(self):
        out = bench.derive_eui(total_kwh=1_000_000, months=11.99, gfa_m2=10_000)
        assert out["months_of_data"] == 12, "a whole number for display and for the column"
        assert out["months_measured"] == 11.99, "the fraction that was actually divided by"

    def test_a_month_is_the_mean_gregorian_month(self):
        assert us_ratings.MINUTES_PER_MONTH == pytest.approx(43_829.06, abs=0.5)

    def test_nothing_is_divided_by_zero_months(self):
        assert bench.annualise(500.0, 0) > 0


class TestTheWindowIsMeasuredNotCounted:
    """The measurement lives in the SQL and in how its rows are folded up."""

    def test_the_query_asks_for_the_time_each_reading_covers(self):
        src = open(us_ratings.__file__, encoding="utf-8").read()
        assert "sum(coalesce(r.period_minutes, 30))::float AS covered_minutes" in src
        assert "months=len(months_seen)" not in src, "the calendar-month count is gone"

    def test_two_meters_reading_the_same_year_cover_one_year(self):
        src = open(us_ratings.__file__, encoding="utf-8").read()
        assert "max(minutes_by_meter.values())" in src, "per meter then the longest, never the sum"

    def test_a_window_with_no_usable_period_falls_back_rather_than_annualising_by_zero(self):
        src = open(us_ratings.__file__, encoding="utf-8").read()
        assert "coverage = float(len(months_seen))" in src


class TestWhatAGapCosts:
    """A whole-building gap is priced at the blend of what the building is contracted at."""

    ELEC_AND_GAS = {"electricity": 0.21, "gas": 0.062}

    def test_one_fuel_prices_at_its_own_rate(self):
        out = bench.blended_tariff({"electricity": 0.21}, {"electricity": 1_000_000})
        assert out["value"] == pytest.approx(0.21)
        assert out["basis"] == "contracted"

    def test_two_fuels_are_weighted_by_what_is_actually_burned(self):
        """Half gas at 6.2p is not priced at the electricity rate — that trebles the cost."""
        out = bench.blended_tariff(self.ELEC_AND_GAS,
                                   {"electricity": 1_710_852, "gas": 1_711_558})
        assert out["value"] == pytest.approx(0.136, abs=0.001)
        assert out["basis"] == "contracted_blended"
        assert out["covered_pct"] == 100.0

    def test_a_fuel_with_no_contracted_rate_is_left_out_rather_than_priced_at_zero(self):
        out = bench.blended_tariff({"electricity": 0.21},
                                   {"electricity": 750_000, "gas": 250_000})
        assert out["value"] == pytest.approx(0.21)
        assert out["covered_pct"] == 75.0, "the page can say the blend is partial"

    def test_no_meter_rate_at_all_prices_nothing(self):
        assert bench.blended_tariff({}, {"electricity": 100})["value"] is None
        assert bench.blended_tariff(None, {"electricity": 100})["value"] is None

    def test_the_headline_cost_uses_the_contracted_rate(self):
        out = bench.price_excess(eui=241.17, bench=212.0, gfa_m2=14_200, cc="UK",
                                 contracted={"value": 0.284, "basis": "contracted"})
        assert out["excess_kwh"] == pytest.approx(414_214, abs=200)
        assert out["cost"] == pytest.approx(117_636, abs=100)
        assert out["tariff"] == 0.284
        assert out["tariff_source"] == "contracted"

    def test_without_a_contracted_rate_it_falls_back_to_the_market_band(self):
        out = bench.price_excess(eui=241.17, bench=212.0, gfa_m2=14_200, cc="UK")
        assert out["tariff_source"] == "market_band_low"
        assert out["cost"] == out["cost_low"], "the conservative end, and it says so"
        assert "market tariff band" in out["cost_basis"]

    def test_the_band_stays_beside_it_as_the_range(self):
        out = bench.price_excess(eui=241.17, bench=212.0, gfa_m2=14_200, cc="UK",
                                 contracted={"value": 0.284, "basis": "contracted"})
        assert out["cost_low"] is not None and out["cost_high"] is not None
        assert out["cost_low"] < out["cost_high"]

    def test_a_building_under_its_reference_costs_nothing_whatever_the_rate(self):
        """Ashgrove. max(0, negative) is zero, and no tariff can make it otherwise."""
        out = bench.price_excess(eui=173.20, bench=185.0, gfa_m2=8_600, cc="UK",
                                 contracted={"value": 0.284, "basis": "contracted"})
        assert out["excess_kwh"] == 0
        assert out["cost"] == 0
        assert out["deviation_pct"] < 0


class TestARecomputedWindowReachesThePage:
    """Fixing the arithmetic changes nothing if the corrected figure is never stored.

    The persist step skipped a building whose latest snapshot already ended on the window's
    end date. That was there to stop a daily run piling up identical rows, but it also meant
    both fixes above recomputed today's window, found today's row already written, and left
    the wrong number in place — the engine reported 241.22 while the page kept showing 222.47.

    A snapshot is the current answer for its window, not the first answer anyone computed for
    it. So a window already written is updated, and only an unchanged one is left alone.
    """

    @staticmethod
    def _src():
        return open(bench.__file__, encoding="utf-8").read()

    def test_an_existing_window_is_updated_rather_than_skipped(self):
        src = self._src()
        assert "_snapshot_for_window(" in src
        assert "if await _latest_snapshot_end(session, bid, fuel=" not in src, \
            "the skip-if-already-written guard is what hid the corrected figure"

    def test_an_unchanged_window_is_still_left_alone(self):
        """The property the skip existed for: a daily run must not churn identical rows."""
        assert "if all(getattr(existing, k) == v for k, v in values.items()):" in self._src()

    def test_the_lookup_is_keyed_on_the_window_and_the_fuel(self):
        src = self._src()
        block = src.split("async def _snapshot_for_window", 1)[1].split("\n\n\n", 1)[0]
        for key in ("EuiSnapshot.building_id", "EuiSnapshot.meter_type", "EuiSnapshot.period_end"):
            assert key in block, f"{key} must be part of what identifies a window"
