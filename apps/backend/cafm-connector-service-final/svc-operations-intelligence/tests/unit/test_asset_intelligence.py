"""Asset intelligence — value at risk, the failure assessment, and the scope clause.

The arithmetic and the honesty are both under test here. An asset missing one of the three
inputs must come back as not computable rather than as zero, and the failure assessment must
keep saying it is not a fitted model no matter what is fed to it.
"""
from __future__ import annotations

from datetime import date, datetime
from uuid import uuid4

import pytest

from src.engines.energy.asset_intelligence import (
    METHOD,
    WEIGHT_AGE,
    WEIGHT_ANOMALY,
    WEIGHT_BAND,
    WEIGHT_CONDITION,
    _scope,
    assess_failure,
    value_at_risk,
)

TODAY = date(2026, 9, 15)


def var(**kw):
    """value_at_risk with a full set of plausible inputs, overridden per test."""
    args = {
        "replacement_value": 10_000.0, "design_life_years": 20.0,
        "installation_date": date(2016, 9, 15), "wear_coefficient": 1.0,
        "deviation_pct": None, "today": TODAY,
    }
    args.update(kw)
    return value_at_risk(**args)


class TestValueAtRiskNeedsAllThreeInputs:
    """A missing input is reported, never treated as a zero."""

    @pytest.mark.parametrize("missing", ["replacement_value", "design_life_years",
                                         "installation_date"])
    def test_absent_input_is_not_computable(self, missing: str):
        r = var(**{missing: None})
        assert r["value_at_risk"] is None
        assert r["basis"] == (
            "not computable: needs replacement value, design life and an install date")

    def test_zero_design_life_is_not_computable_rather_than_dividing_by_zero(self):
        assert var(design_life_years=0)["value_at_risk"] is None

    def test_every_input_comes_back_beside_the_answer(self):
        r = var(deviation_pct=30.0)
        for k in ("replacement_value", "design_life_years", "age_years",
                  "wear_coefficient", "deviation_pct"):
            assert r[k] is not None, k


class TestValueAtRiskArithmetic:
    def test_half_way_through_design_life_is_worth_half(self):
        r = var()                                   # 10 years of a 20 year life
        assert r["age_years"] == pytest.approx(10.0, abs=0.01)
        assert r["design_life_used_pct"] == pytest.approx(50.0, abs=0.1)
        assert r["straight_line_value"] == pytest.approx(5000.0, abs=5.0)

    def test_no_deviation_means_nothing_at_risk(self):
        r = var(deviation_pct=None)
        assert r["value_at_risk"] == 0.0
        assert r["adjusted_value"] == r["straight_line_value"]

    def test_deviation_ages_it_faster_and_that_gap_is_the_loss(self):
        r = var(deviation_pct=50.0)                  # wear 1.0 -> effective age x1.5
        assert r["adjusted_value"] < r["straight_line_value"]
        assert r["value_at_risk"] == pytest.approx(
            r["straight_line_value"] - r["adjusted_value"], abs=0.01)
        # 10 years x 1.5 = 15 of 20 used, so a quarter of the value is left.
        assert r["adjusted_value"] == pytest.approx(2500.0, abs=5.0)

    def test_a_gentler_asset_loses_less_to_the_same_deviation(self):
        hard = var(deviation_pct=50.0, wear_coefficient=1.0)["value_at_risk"]
        soft = var(deviation_pct=50.0, wear_coefficient=0.3)["value_at_risk"]
        assert soft < hard

    def test_running_under_reference_is_not_treated_as_a_credit(self):
        assert var(deviation_pct=-40.0)["value_at_risk"] == 0.0

    def test_value_never_goes_below_zero(self):
        r = var(deviation_pct=400.0)
        assert r["adjusted_value"] == 0.0
        assert r["remaining_life_months"] == 0.0
        assert r["value_at_risk"] == r["straight_line_value"]

    def test_past_design_life_is_worth_nothing_not_a_negative(self):
        r = var(installation_date=date(1990, 1, 1))
        assert r["straight_line_value"] == 0.0
        assert r["design_life_used_pct"] == 100.0

    def test_a_timestamp_install_date_is_accepted_like_a_date(self):
        assert var(installation_date=datetime(2016, 9, 15, 11, 30))["age_years"] == \
            pytest.approx(var()["age_years"], abs=0.01)

    def test_basis_states_the_arithmetic(self):
        b = var(deviation_pct=50.0)["basis"]
        assert "straight line over 20 years" in b
        assert "wear factor of 1.5" in b


class TestFailureAssessmentSaysWhatItIs:
    """The point of points 8 and 9: a stated rule, never a model wearing a model's clothes."""

    def test_it_never_claims_to_be_fitted(self):
        for kw in ({"condition_score": None, "open_anomalies": 0, "anomaly_weeks": None,
                    "design_life_used_pct": None, "readings_out_of_band": 0,
                    "readings_total": 0},
                   {"condition_score": 5, "open_anomalies": 9, "anomaly_weeks": 52.0,
                    "design_life_used_pct": 100.0, "readings_out_of_band": 8,
                    "readings_total": 8}):
            r = assess_failure(**kw)
            assert r["is_fitted_model"] is False
            assert r["accuracy"] is None and r["precision"] is None and r["recall"] is None
            assert "invented rather than measured" in r["why_no_metrics"]
            assert r["method"] == METHOD

    def test_no_signals_means_no_probability_and_no_drivers(self):
        r = assess_failure(condition_score=None, open_anomalies=0, anomaly_weeks=None,
                           design_life_used_pct=None, readings_out_of_band=0,
                           readings_total=0)
        assert r["probability"] == 0.0
        assert r["drivers"] == []

    def test_every_signal_at_its_worst_caps_at_one(self):
        r = assess_failure(condition_score=5, open_anomalies=10, anomaly_weeks=52.0,
                           design_life_used_pct=100.0, readings_out_of_band=8,
                           readings_total=8)
        assert r["probability"] == 1.0
        assert {d["signal"] for d in r["drivers"]} == {
            "condition grade", "open energy anomalies", "design life used",
            "readings outside band"}

    def test_each_signal_stays_inside_its_own_weight(self):
        for kw, weight in (
            ({"condition_score": 5}, WEIGHT_CONDITION),
            ({"open_anomalies": 10, "anomaly_weeks": 52.0}, WEIGHT_ANOMALY),
            ({"design_life_used_pct": 100.0}, WEIGHT_AGE),
            ({"readings_out_of_band": 8, "readings_total": 8}, WEIGHT_BAND),
        ):
            args = {"condition_score": None, "open_anomalies": 0, "anomaly_weeks": None,
                    "design_life_used_pct": None, "readings_out_of_band": 0,
                    "readings_total": 0}
            args.update(kw)
            assert assess_failure(**args)["probability"] == pytest.approx(weight, abs=1e-4)

    def test_a_new_asset_in_good_condition_contributes_nothing(self):
        r = assess_failure(condition_score=1, open_anomalies=0, anomaly_weeks=None,
                           design_life_used_pct=10.0, readings_out_of_band=0,
                           readings_total=6)
        assert r["probability"] == 0.0
        # The signals are still reported, at zero, so the reader sees they were looked at.
        assert len(r["drivers"]) == 3

    def test_age_starts_counting_past_sixty_per_cent(self):
        below = assess_failure(condition_score=None, open_anomalies=0, anomaly_weeks=None,
                               design_life_used_pct=60.0, readings_out_of_band=0,
                               readings_total=0)["probability"]
        above = assess_failure(condition_score=None, open_anomalies=0, anomaly_weeks=None,
                               design_life_used_pct=80.0, readings_out_of_band=0,
                               readings_total=0)["probability"]
        assert below == 0.0
        assert above == pytest.approx(WEIGHT_AGE * 0.5, abs=1e-4)

    def test_a_persistent_finding_counts_for_more_than_a_fresh_one(self):
        fresh = assess_failure(condition_score=None, open_anomalies=2, anomaly_weeks=0.5,
                               design_life_used_pct=None, readings_out_of_band=0,
                               readings_total=0)["probability"]
        old = assess_failure(condition_score=None, open_anomalies=2, anomaly_weeks=8.0,
                             design_life_used_pct=None, readings_out_of_band=0,
                             readings_total=0)["probability"]
        assert old > fresh

    def test_band_driver_reports_the_count_it_graded(self):
        r = assess_failure(condition_score=None, open_anomalies=0, anomaly_weeks=None,
                           design_life_used_pct=None, readings_out_of_band=2,
                           readings_total=8)
        driver = next(d for d in r["drivers"] if d["signal"] == "readings outside band")
        assert driver["value"] == "2 of 8"


class TestScopeClause:
    """Building scope is the whole point: no allocation must mean no rows, never all rows."""

    def test_none_is_unrestricted(self):
        assert _scope(None, "a.building_id") == ("", {})

    def test_an_empty_allocation_matches_no_row(self):
        clause, params = _scope([], "a.building_id")
        assert clause == " AND FALSE"
        assert params == {}

    def test_ids_are_bound_never_interpolated(self):
        ids = [uuid4(), uuid4()]
        clause, params = _scope(ids, "a.building_id")
        assert clause == " AND a.building_id = ANY(CAST(:scope_b AS uuid[]))"
        assert params["scope_b"] == [str(i) for i in ids]
        assert str(ids[0]) not in clause
