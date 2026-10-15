"""Findings on one meter are not added, and the page is told what adding would have given.

The Energy page summed every open anomaly under a building. On plenum_agent that produced
£667,375 for Bishopsgate Tower — from four rules on ONE electricity meter, each pricing the
whole excess it could see — on a building reading 205 kWh/m2 against a reference of 212. Three
per cent under its own benchmark and two gigawatt-hours of waste, on the same line of the same
screen.

The rules are lenses, not faults. A weekend is an unoccupied hour, so the weekend rule's kWh
are already inside the non-occupancy rule's. Adding them counts the same energy twice.
"""

from src.engines.energy.anomaly_rollup import CONTAINED_IN, combine


def f(rule, amount, kwh=0.0, label=None, currency="GBP"):
    return {"rule": rule, "label": label or rule, "reason": "-", "amount": amount,
            "kwh": kwh, "currency": currency}


# The exact meter from the screenshot: SYN-C9C5E21D at Bishopsgate Tower.
BISHOPSGATE_METER = [
    f("post_works_regression", 321310.0, 1131373.0),
    f("nonocc_spike", 159484.0, 561563.0),
    f("weather_residual", 54421.0, 191622.0),
    f("weekend_spike", 46494.0, 163710.0),
    f("simultaneous_heating_cooling", 0.0, 0.0),
]


class TestTheHeadlineIsNotTheSum:

    def test_the_largest_single_finding_is_the_number(self):
        out = combine(BISHOPSGATE_METER)
        assert out["headline"]["rule"] == "post_works_regression"
        assert out["headline"]["amount"] == 321310.0
        assert out["headline"]["kwh"] == 1131373.0

    def test_what_adding_would_have_given_is_still_reported(self):
        """A figure that drops with no explanation is its own kind of dishonesty."""
        out = combine(BISHOPSGATE_METER)
        assert out["if_added"] == 581709.0
        assert out["kwh_if_added"] == 2048268.0

    def test_the_sum_is_not_silently_adopted_as_the_headline(self):
        out = combine(BISHOPSGATE_METER)
        assert out["headline"]["amount"] < out["if_added"]


class TestWhatIsProvablyCountedTwice:

    def test_a_weekend_is_an_unoccupied_hour_so_one_rule_sits_inside_the_other(self):
        out = combine(BISHOPSGATE_METER)
        assert [c["rule"] for c in out["contained"]] == ["weekend_spike"]
        assert out["contained"][0]["inside"] == "nonocc_spike"
        assert out["double_counted_at_least"] == 46494.0

    def test_containment_only_counts_when_both_rules_actually_fired(self):
        """The weekend rule alone overlaps nothing — there is no larger rule to sit inside."""
        out = combine([f("weekend_spike", 46494.0, 163710.0)])
        assert out["contained"] == []
        assert out["double_counted_at_least"] == 0.0
        assert out["headline"]["rule"] == "weekend_spike"

    def test_rules_that_are_not_definitionally_contained_are_only_flagged_as_maybe(self):
        """Overstating what is known is the same failure as overstating the money.

        Weather residual and post-works regression can cover the same hours, but neither
        contains the other by construction, so neither is claimed as double counted.
        """
        out = combine(BISHOPSGATE_METER)
        assert set(out["may_overlap"]) == {"nonocc_spike", "weather_residual"}
        assert "weather_residual" not in [c["rule"] for c in out["contained"]]

    def test_the_containment_table_holds_only_relationships_true_by_construction(self):
        assert CONTAINED_IN == {"weekend_spike": "nonocc_spike"}


class TestFindingsWithNoMoneyOnThem:

    def test_an_unpriced_rule_is_listed_but_cannot_be_the_headline(self):
        """Simultaneous heating and cooling is measured in hours; peak excursion in kW."""
        out = combine(BISHOPSGATE_METER)
        assert out["priced_rules"] == 4
        assert out["unpriced_rules"] == 1
        assert out["headline"]["rule"] != "simultaneous_heating_cooling"

    def test_a_meter_with_nothing_priced_has_no_headline_rather_than_a_zero(self):
        """Zero reads as "we checked and it costs nothing"; None reads as "not priced"."""
        out = combine([f("simultaneous_heating_cooling", 0.0), f("peak_excursion", 0.0)])
        assert out["headline"] is None
        assert out["if_added"] == 0.0
        assert out["unpriced_rules"] == 2

    def test_no_findings_at_all_is_not_an_error(self):
        out = combine([])
        assert out["headline"] is None
        assert out["if_added"] == 0.0
        assert out["contained"] == []


class TestTheArithmeticHoldsForTheRealCase:

    def test_the_building_total_on_screen_is_reproduced_as_if_added(self):
        """Bishopsgate's three meters, as the rollup groups them.

        £667,375 is what the page showed. It has to appear somewhere in the answer, labelled
        as what it is, or the fix looks like a number quietly going missing.
        """
        m1 = combine(BISHOPSGATE_METER)
        m2 = combine([f("weather_residual", 85667.0, 301646.0),
                      f("simultaneous_heating_cooling", 0.0)])
        m3 = combine([f("peak_excursion", 0.0), f("simultaneous_heating_cooling", 0.0)])
        assert round(m1["if_added"] + m2["if_added"] + m3["if_added"], 2) == 667376.0
        # and the headline the building would carry
        heads = [m["headline"]["amount"] for m in (m1, m2, m3) if m["headline"]]
        assert max(heads) == 321310.0
