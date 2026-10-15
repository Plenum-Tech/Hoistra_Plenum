"""Pricing energy per market: the bands, the conversions, and the sums that must not happen.

Three claims are under test, and all three are about refusing to invent a number.

A commercial tariff is a **band**. Pricing at a midpoint would be wrong by up to a quarter
either way and would look like a measurement, so everything returns a low and a high.

The markets do not share a currency, and no exchange rate is held here. A portfolio spanning
four of them therefore has four totals and no single one — adding them at a made-up rate is
the one outcome worse than not answering.

Some fuel has no per-kWh rate at all. LPG in the UAE is sold by weight. That is unpriceable,
which is a different fact from free.
"""
from __future__ import annotations

import pytest

from src.engines.energy.pricing import (
    assess_statutory,
    conversions,
    gas_m3_to_kwh,
    price_by_market,
    price_kwh,
    statutory_rule,
    tariff_for,
    to_kwh,
)


class TestTheConversionsFromTheSpecification:
    def test_square_feet_to_square_metres(self):
        assert conversions()["area_sqft_to_sqm"] == 0.092903
        assert round(1000 * conversions()["area_sqft_to_sqm"], 3) == 92.903

    def test_therms_to_kwh(self):
        assert to_kwh(1, "therms") == pytest.approx(29.3001)
        assert to_kwh(100, "therm") == pytest.approx(2930.01)

    def test_refrigeration_ton_hours_to_kwh(self):
        assert to_kwh(1, "RTh") == pytest.approx(3.51685)
        assert to_kwh(100, "rth") == pytest.approx(351.685)

    def test_kwh_passes_through(self):
        assert to_kwh(42.0, "kWh") == 42.0

    @pytest.mark.parametrize("unit", ["barrels", "gallons", "Mlb", "", "widgets"])
    def test_a_unit_with_no_published_factor_is_none_not_a_guess(self, unit):
        # A converted figure that used an assumed factor is indistinguishable from a measured
        # one once it is a number.
        assert to_kwh(100, unit) is None

    def test_no_consumption_converts_to_nothing(self):
        assert to_kwh(None, "therms") is None


class TestUkGasVolumeToEnergy:
    def test_it_follows_the_published_formula(self):
        r = gas_m3_to_kwh(1000, 39.5)
        assert r["kwh"] == pytest.approx(1000 * 1.02264 * 39.5 / 3.6, rel=1e-6)
        assert r["volume_correction_factor"] == 1.02264

    def test_a_metered_calorific_value_is_used_and_marked_as_real(self):
        r = gas_m3_to_kwh(1000, 39.2)
        assert r["calorific_value"] == 39.2
        assert r["calorific_value_is_default"] is False
        assert "fallback" not in r["basis"]

    def test_falling_back_to_the_default_says_so(self):
        # The calorific value is published per settlement period; the default is an estimate.
        r = gas_m3_to_kwh(1000)
        assert r["calorific_value_is_default"] is True
        assert "fallback" in r["basis"]

    def test_no_volume_is_not_zero_energy(self):
        assert gas_m3_to_kwh(None)["kwh"] is None


class TestTariffsAreBands:
    @pytest.mark.parametrize("market,low,high,currency", [
        ("UK", 0.210, 0.260, "GBP"),
        ("AE", 0.265, 0.395, "AED"),
        ("SG", 0.25, 0.28, "SGD"),
    ])
    def test_the_specification_band_is_what_is_on_file(self, market, low, high, currency):
        t = tariff_for(market)
        assert t["low"] == pytest.approx(low)
        assert t["high"] == pytest.approx(high)
        assert t["unit"].startswith(currency)

    def test_pricing_returns_a_range_not_a_point(self):
        r = price_kwh("UK", 100_000)
        assert r["priceable"] is True
        assert r["low"] == pytest.approx(21_000)
        assert r["high"] == pytest.approx(26_000)
        assert r["currency"] == "GBP"

    def test_the_uae_band_includes_the_fuel_surcharge(self):
        # 20–33 fils slab plus ~6.5 fils surcharge.
        t = tariff_for("AE")
        c = t["components"]
        assert c["slab_low"] + c["fuel_surcharge"] == pytest.approx(t["low"])
        assert c["slab_high"] + c["fuel_surcharge"] == pytest.approx(t["high"])

    def test_a_flat_rate_prices_to_the_same_low_and_high(self):
        r = price_kwh("US", 100_000)
        assert r["low"] == r["high"] == pytest.approx(22_000)

    def test_the_us_rate_says_what_it_leaves_out(self):
        # Demand charges are per kW, not per kWh, so they are not in an energy-only price.
        r = price_kwh("US", 1_000)
        assert "demand" in (r.get("excludes") or "").lower()


class TestWhatCannotBePriced:
    def test_uae_gas_is_unpriceable_rather_than_free(self):
        r = price_kwh("AE", 100_000, "gas")
        assert r["priceable"] is False
        assert r["low"] is None and r["high"] is None      # not 0.0
        assert "weight" in r["basis"]

    def test_a_market_with_no_tariff_on_file_says_so(self):
        r = price_kwh("ZZ", 1_000)
        assert r["priceable"] is False
        assert "no tariff on file" in r["basis"]

    def test_no_consumption_is_not_priced_as_zero(self):
        assert price_kwh("UK", None)["low"] is None


class TestCurrenciesAreNotAdded:
    def test_one_currency_is_arithmetic_and_is_totalled(self):
        r = price_by_market({"UK": 50_000})
        assert r["total"] == {"low": pytest.approx(10_500), "high": pytest.approx(13_000)}
        assert r["total_currency"] == "GBP"

    def test_a_mixed_portfolio_has_no_single_total(self):
        r = price_by_market({"UK": 50_000, "AE": 30_000, "SG": 20_000})
        assert r["total"] is None
        assert set(r["currencies"]) == {"GBP", "AED", "SGD"}
        assert "no exchange rate" in r["note"]

    def test_each_market_still_gets_its_own_figure(self):
        r = price_by_market({"UK": 50_000, "AE": 30_000})
        by = {m["market"]: m for m in r["markets"]}
        assert by["UK"]["currency"] == "GBP" and by["UK"]["priceable"]
        assert by["AE"]["currency"] == "AED" and by["AE"]["priceable"]

    def test_markets_are_ranked_by_what_they_cost(self):
        r = price_by_market({"UK": 1_000, "AE": 900_000})
        assert r["markets"][0]["market"] == "AE"

    def test_an_unpriceable_market_is_listed_with_its_reason(self):
        r = price_by_market({"ZZ": 1_000, "UK": 1_000})
        assert [u["market"] for u in r["not_priceable"]] == ["ZZ"]
        assert r["priced"] == 1


class TestTheStatutoryRuleIsCountrySpecific:
    """One number for all four markets would flatten the difference the page exists to show."""

    def test_each_market_carries_its_own_rule(self):
        assert statutory_rule("UK")["rule"] == "eui_over_reference"
        assert statutory_rule("US")["rule"] == "carbon_cap"
        assert statutory_rule("AE")["rule"] == "eui_over_reference"
        assert statutory_rule("SG")["rule"] == "eui_over_reference"

    @pytest.mark.parametrize("market,reference", [
        ("UK", 190.0), ("AE", 228.0), ("SG", 192.0)])
    def test_the_reference_is_the_one_the_specification_gives(self, market, reference):
        assert statutory_rule(market)["reference_eui_kwh_m2"] == reference

    def test_the_uk_reference_is_its_two_halves(self):
        b = statutory_rule("UK")["breakdown"]
        assert b["heating"] + b["electricity"] == 190.0

    @pytest.mark.parametrize("market,eui,over", [
        ("UK", 214.0, True), ("UK", 150.0, False),
        ("AE", 245.6, True), ("AE", 200.0, False),
        ("SG", 200.0, True), ("SG", 180.0, False)])
    def test_intensity_is_tested_against_that_market_only(self, market, eui, over):
        r = assess_statutory(market, eui_kwh_m2=eui)
        assert r["assessable"] is True
        assert r["over"] is over

    def test_the_same_intensity_passes_in_one_market_and_fails_in_another(self):
        # 200 kWh/m2/yr is over Singapore's 192 and under the UAE's 228.
        assert assess_statutory("SG", eui_kwh_m2=200.0)["over"] is True
        assert assess_statutory("AE", eui_kwh_m2=200.0)["over"] is False

    def test_new_york_is_carbon_not_intensity_and_carries_a_fine(self):
        r = assess_statutory("US", tco2e=1200.0, carbon_cap_tco2e=1000.0)
        assert r["over"] is True
        assert r["penalty"] == pytest.approx((1200 - 1000) * 268)
        assert r["currency"] == "USD"

    def test_under_the_cap_there_is_no_fine(self):
        r = assess_statutory("US", tco2e=900.0, carbon_cap_tco2e=1000.0)
        assert r["over"] is False
        assert r["penalty"] == 0

    def test_an_intensity_rule_cannot_be_answered_without_an_intensity(self):
        r = assess_statutory("UK")
        assert r["assessable"] is False
        assert "needs a measured intensity" in r["basis"]

    def test_the_carbon_rule_needs_both_the_figure_and_the_cap(self):
        assert assess_statutory("US", tco2e=1200.0)["assessable"] is False
        assert assess_statutory("US", carbon_cap_tco2e=1000.0)["assessable"] is False

    def test_a_market_with_no_rule_says_so(self):
        assert assess_statutory("ZZ", eui_kwh_m2=200.0)["assessable"] is False
