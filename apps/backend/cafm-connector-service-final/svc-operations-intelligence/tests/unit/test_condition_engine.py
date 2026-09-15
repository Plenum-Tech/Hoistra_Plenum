"""The condition engine's rule: two signals in, a band and a reason out.

The page states the rule in words — section over reference *and* an anomaly on the asset is a
Threat, one alone is a Watch — and these pin that wording to behaviour. The cases that matter
most are the edges: exactly on a threshold, an anomaly with no age recorded, and a section
that was never metered, where the honest answer is that one of the two signals could not be
read at all rather than that it came back clean.
"""
from __future__ import annotations

import pytest

from src.engines.energy.condition_engine import (
    BAND_IN_CONTROL,
    BAND_THREAT,
    BAND_WATCH,
    DEFAULT_ANOMALY_WEEKS,
    DEFAULT_SECTION_OVER_PCT,
    METHOD,
    R_ANOMALY_HERE,
    R_ANOMALY_PERSISTENT,
    R_ANOMALY_UNDER,
    R_SECTION_OVER,
    R_SHARES_LOAD,
    _tally,
    classify,
)

RULE = {"section_over_pct": DEFAULT_SECTION_OVER_PCT,
        "persistent_weeks": DEFAULT_ANOMALY_WEEKS}


def band(dev, anomalies=0, weeks=None, **rule):
    return classify(section_deviation_pct=dev, anomalies_open=anomalies,
                    anomaly_weeks=weeks, **{**RULE, **rule})


class TestTheRuleThePageStates:
    def test_both_signals_is_a_threat(self):
        r = band(19.0, anomalies=1, weeks=1.3)
        assert r["band"] == BAND_THREAT
        assert set(r["reasons"]) == {R_SECTION_OVER, R_ANOMALY_HERE}
        assert "Both signals agree" in r["explanation"]

    def test_a_threat_does_not_require_the_anomaly_to_be_persistent(self):
        # CHILLER-101 on the page is a Threat with a 1.3-week anomaly, under the 3-week bar.
        assert band(19.0, anomalies=1, weeks=1.3)["band"] == BAND_THREAT

    def test_section_over_with_nothing_attributed_here_is_a_watch(self):
        r = band(28.0, anomalies=0)
        assert r["band"] == BAND_WATCH
        assert R_SHARES_LOAD in r["reasons"]
        assert "shares the load" in r["explanation"]

    def test_a_persistent_anomaly_on_a_clean_section_is_a_watch(self):
        r = band(2.0, anomalies=1, weeks=5.0)
        assert r["band"] == BAND_WATCH
        assert R_ANOMALY_PERSISTENT in r["reasons"]
        assert R_SHARES_LOAD not in r["reasons"]

    def test_an_anomaly_under_the_bar_on_a_clean_section_is_in_control_but_counted(self):
        r = band(2.0, anomalies=1, weeks=0.5)
        assert r["band"] == BAND_IN_CONTROL
        assert R_ANOMALY_UNDER in r["reasons"]
        # "found something too small to act on" is not the same as "found nothing"
        assert R_ANOMALY_HERE in r["reasons"]

    def test_neither_signal_is_in_control(self):
        r = band(2.0, anomalies=0)
        assert r["band"] == BAND_IN_CONTROL
        assert r["reasons"] == []

    def test_a_section_under_reference_is_never_over(self):
        assert band(-7.0, anomalies=0)["band"] == BAND_IN_CONTROL
        assert band(-7.0, anomalies=1, weeks=0.2)["band"] == BAND_IN_CONTROL


class TestThresholdEdges:
    def test_exactly_on_the_section_threshold_is_not_over(self):
        # "over reference by more than 10%" — 10.0 itself is not more than 10.
        assert band(10.0, anomalies=1)["section_over_reference"] is False
        assert band(10.01, anomalies=1)["section_over_reference"] is True

    def test_exactly_on_the_persistence_threshold_counts_as_persistent(self):
        # "persistent for 3 weeks or more" — 3.0 is included.
        assert band(0.0, anomalies=1, weeks=3.0)["anomaly_persistent"] is True
        assert band(0.0, anomalies=1, weeks=2.99)["anomaly_persistent"] is False

    def test_moving_the_stepper_moves_the_band(self):
        assert band(12.0, anomalies=0, section_over_pct=10.0)["band"] == BAND_WATCH
        assert band(12.0, anomalies=0, section_over_pct=30.0)["band"] == BAND_IN_CONTROL

    def test_the_persistence_stepper_moves_a_watch_into_control(self):
        assert band(0.0, anomalies=1, weeks=4.0, persistent_weeks=3.0)["band"] == BAND_WATCH
        assert band(0.0, anomalies=1, weeks=4.0,
                    persistent_weeks=8.0)["band"] == BAND_IN_CONTROL

    def test_the_explanation_quotes_the_threshold_in_force(self):
        assert "8-week threshold" in band(0.0, anomalies=1, weeks=9.0,
                                          persistent_weeks=8.0)["explanation"]


class TestASignalThatCouldNotBeRead:
    """An unmetered section is not a clean section."""

    def test_no_deviation_is_not_treated_as_within_reference(self):
        r = band(None, anomalies=0)
        assert r["section_measured"] is False
        assert r["section_over_reference"] is False
        assert "not metered" in r["explanation"]

    def test_an_unmetered_section_still_bands_on_the_anomaly_alone(self):
        assert band(None, anomalies=1, weeks=6.0)["band"] == BAND_WATCH
        assert band(None, anomalies=1, weeks=0.4)["band"] == BAND_IN_CONTROL

    def test_an_anomaly_with_no_age_recorded_is_not_assumed_persistent(self):
        r = band(0.0, anomalies=2, weeks=None)
        assert r["anomaly_persistent"] is False
        assert r["band"] == BAND_IN_CONTROL
        assert R_ANOMALY_UNDER in r["reasons"]


class TestEveryVerdictCanBeArguedWith:
    @pytest.mark.parametrize("dev,anom,wks", [
        (19.0, 1, 1.3), (28.0, 0, None), (2.0, 1, 5.0), (2.0, 1, 0.5), (2.0, 0, None),
        (None, 0, None), (None, 3, 9.0),
    ])
    def test_the_inputs_come_back_beside_the_answer(self, dev, anom, wks):
        r = band(dev, anomalies=anom, weeks=wks)
        assert r["section_deviation_pct"] == dev
        assert r["anomalies_open"] == anom
        assert r["anomaly_weeks"] == wks
        assert r["explanation"]
        assert r["band"] in (BAND_THREAT, BAND_WATCH, BAND_IN_CONTROL)

    def test_the_rule_is_named(self):
        assert METHOD == "rule:section-over-reference+anomaly-attributed/v1"


def asset(b, reasons=(), measured=True):
    return {"band": b, "reasons": list(reasons), "section_measured": measured}


class TestTheKpiCards:
    def test_the_three_bands_account_for_every_asset(self):
        rows = [asset(BAND_THREAT), asset(BAND_WATCH), asset(BAND_IN_CONTROL)] * 4
        t = _tally(rows)
        assert t["threat"] + t["watch"] + t["in_control"] == t["assets"] == 12

    def test_the_watch_card_splits_into_the_two_reasons_it_prints(self):
        rows = [asset(BAND_WATCH, [R_SECTION_OVER, R_SHARES_LOAD]) for _ in range(6)] + \
               [asset(BAND_WATCH, [R_ANOMALY_HERE, R_ANOMALY_PERSISTENT])]
        t = _tally(rows)
        # "7 — 6 shared section · 1 persistent anomaly"
        assert (t["watch"], t["watch_shares_section"], t["watch_persistent_anomaly"]) == (7, 6, 1)

    def test_the_in_control_card_counts_those_with_an_anomaly_under_the_bar(self):
        rows = [asset(BAND_IN_CONTROL, [R_ANOMALY_HERE, R_ANOMALY_UNDER]) for _ in range(3)] + \
               [asset(BAND_IN_CONTROL) for _ in range(5)]
        t = _tally(rows)
        # "8 of 19 — 3 with an anomaly under threshold"
        assert (t["in_control"], t["in_control_anomaly_under_threshold"]) == (8, 3)

    def test_assets_banded_on_one_signal_are_counted_so_the_page_can_say_so(self):
        rows = [asset(BAND_IN_CONTROL, measured=False) for _ in range(35)] + \
               [asset(BAND_IN_CONTROL) for _ in range(19)]
        assert _tally(rows)["section_not_measured"] == 35

    def test_an_empty_portfolio_tallies_to_zero_rather_than_failing(self):
        t = _tally([])
        assert t["assets"] == 0 and t["threat"] == 0 and t["section_not_measured"] == 0
