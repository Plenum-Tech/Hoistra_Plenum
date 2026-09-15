"""The market-profile table: each market's own benchmark rule, applied to what is in scope.

This table was a frontend constant. A constant cannot know how many buildings sit in a
market, what tariff its meters actually contracted, or what the portfolio's use mix does
to a use-class benchmark — so every cell now says what kind of fact it is, and the
reference EUI is each market's rule run over the buildings actually there.
"""
import inspect

import pytest

from src.engines.energy import market_profiles as mp
from src.engines.energy import us_ratings
from src.engines.energy.ratings_position import BCA_OFFICE_REFERENCE_KWH_M2


def b(**kw):
    return {"building_type": None, "gfa_sqm": None, "eui_kwh_per_m2": None,
            "benchmark_kwh_per_m2": None, **kw}


class TestTheUkRuleIsTm46WeightedByWhatIsThere:
    def test_weights_by_floor_area_not_by_count(self):
        # one big office and one small retail unit: the office must dominate
        out = mp.weighted_tm46([b(building_type="office", gfa_sqm=38000),
                                b(building_type="retail", gfa_sqm=1000)])
        assert out["buildings"] == 2
        office = 95 + 117
        retail = 280
        expected = (office * 38000 + retail * 1000) / 39000
        assert abs(out["value"] - round(expected)) <= 1
        assert set(out["by_type"]) == {"office", "retail"}

    def test_an_untyped_building_is_counted_out_and_said(self):
        out = mp.weighted_tm46([b(building_type="office", gfa_sqm=1000), b(building_type=None)])
        assert out["buildings"] == 1, "a building with no mappable type cannot weight anything"

    def test_no_typed_building_means_no_figure(self):
        assert mp.weighted_tm46([b(), b(building_type="spaceport")]) is None

    def test_missing_area_falls_back_to_equal_weight(self):
        out = mp.weighted_tm46([b(building_type="office"), b(building_type="retail")])
        assert out["value"] == round(((95 + 117) + 280) / 2)


class TestTheUaeRuleIsThePortfolioMedian:
    def test_needs_the_same_minimum_the_row_benchmark_needs(self):
        assert mp.rolling_portfolio_median([b(eui_kwh_per_m2=200)]) is None
        out = mp.rolling_portfolio_median([b(eui_kwh_per_m2=200), b(eui_kwh_per_m2=260), b(eui_kwh_per_m2=300)])
        assert out == {"value": 260, "buildings": 3}

    def test_buildings_without_a_reading_do_not_count(self):
        out = mp.rolling_portfolio_median([b(eui_kwh_per_m2=200), b(eui_kwh_per_m2=300), b(), b()])
        assert out["buildings"] == 2


class TestTheUsRuleIsLl97ByOccupancyGroup:
    def test_the_cap_follows_each_buildings_use(self):
        out = mp.ll97_reference([b(building_type="retail", gfa_sqm=1000)], year=2026)
        assert out["period"] == "2024-2029"
        assert abs(out["cap_tco2e_per_ft2"] - us_ratings.LL97_LIMITS["2024-2029"]["M"]) < 1e-9
        assert out["groups"] == {"M": 1}
        assert out["energy_star_target"] == us_ratings.CERTIFICATION_SCORE

    def test_area_weighting_across_groups(self):
        lim = us_ratings.LL97_LIMITS["2024-2029"]
        out = mp.ll97_reference([b(building_type="office", gfa_sqm=3000),
                                 b(building_type="hotel", gfa_sqm=1000)], year=2026)
        expected = (lim["B"] * 3000 + lim["R-1"] * 1000) / 4000
        assert abs(out["cap_tco2e_per_ft2"] - expected) < 1e-9

    def test_the_period_moves_with_the_year(self):
        assert mp.ll97_reference([b(building_type="office")], year=2031)["period"] == "2030-2034"

    def test_no_us_building_or_no_period_means_the_rule_not_a_number(self):
        assert mp.ll97_reference([], year=2026) is None
        assert mp.ll97_reference([b(building_type="office")], year=2040) is None


class TestTheSgRuleIsBcaWithPerBuildingOverride:
    def test_a_recorded_benchmark_beats_the_office_median(self):
        out = mp.bca_reference([b(benchmark_kwh_per_m2=150, gfa_sqm=1000)])
        assert out == {"value": 150, "buildings": 1, "recorded": 1}

    def test_the_median_stands_in_where_none_is_recorded(self):
        out = mp.bca_reference([b(gfa_sqm=1000), b(benchmark_kwh_per_m2=150, gfa_sqm=1000)])
        assert out["recorded"] == 1
        assert out["value"] == round((BCA_OFFICE_REFERENCE_KWH_M2 + 150) / 2)

    def test_no_sg_building_states_the_reference_with_nothing_behind_it(self):
        out = mp.bca_reference([])
        assert out["buildings"] == 0 and out["value"] == BCA_OFFICE_REFERENCE_KWH_M2


class TestATariffReadsTheWayItsMarketQuotesIt:
    @pytest.mark.parametrize("value,currency,text", [
        (0.284, "GBP", "28.4p/kWh"), (0.22, "USD", "$0.22/kWh"),
        (0.445, "AED", "44.5 fils/kWh"), (0.30, "SGD", "S$0.30/kWh"),
    ])
    def test_each_market(self, value, currency, text):
        assert mp.per_kwh(value, currency) == text

    def test_an_unknown_currency_is_printed_not_dropped(self):
        assert mp.per_kwh(0.5, "MXN") == "0.500 MXN/kWh"


class TestTheReferencePackIsHonest:
    def test_every_market_has_every_attribute_or_a_derivation(self):
        ref = mp.load_profiles()
        keys = {a[2] for a in ref["attributes"]}
        for cc, m in ref["markets"].items():
            for k in keys - {"ref", "elec", "cur"}:
                assert k in m, f"{cc} lacks {k}"
            assert "reference_eui" in m and "elec" in m and "cur" in m

    def test_every_published_tariff_carries_a_date_and_a_band(self):
        # A tariff is a band now, not a point: a commercial rate is a contract range, and the
        # single figure that used to sit here sat above the published ceiling in three of the
        # four markets.
        for cc, m in mp.load_profiles()["markets"].items():
            assert m["elec"].get("as_of"), f"{cc} tariff has no as-of date"
            assert m["elec"].get("label"), f"{cc} tariff has nothing to show a reader"
            band = (m.get("tariff") or {}).get("electricity") or {}
            assert band.get("unit"), f"{cc} electricity band has no unit"
            assert band.get("low") is not None and band.get("high") is not None,                 f"{cc} electricity has no priceable band"
            assert band["low"] <= band["high"], f"{cc} band is inverted"

    def test_a_fuel_nobody_can_price_says_so_rather_than_costing_nothing(self):
        # LPG in the UAE is sold by weight, so there is no per-kWh rate for it at all.
        ae_gas = mp.load_profiles()["markets"]["AE"]["tariff"]["gas"]
        assert ae_gas.get("unpriceable") is True
        assert ae_gas.get("low") is None

    def test_the_order_is_the_pages_order(self):
        assert mp.MARKET_ORDER == ("UK", "US", "AE", "SG")


class TestTheRouteIsScoped:
    def test_market_profiles_runs_through_scope_and_building_ids_for(self):
        from src.api.routes import energy

        src = inspect.getsource(energy.market_profiles)
        assert "Depends(scope)" in src
        assert "building_ids_for(session, s, building_id)" in src, (
            "a caller must only ever see the markets of buildings they may see")
