"""The PPM contract state rule, and how a report is said to corroborate an anomaly.

The state rule is pinned against the eight contracts printed on the page, because a banding
rule that does not reproduce the rows it was written from is not the rule.

Corroboration is pinned on the thing that makes it defensible rather than a guess: a report
only corroborates an anomaly it *predates*, and the words the match was made on come back with
it. A report written after detection explains nothing — it is the same observation, not
independent support for it.
"""
from __future__ import annotations

import pytest

from src.services.inspection_intelligence import (
    MIN_SHARED_TERMS,
    POOR_GRADE,
    STOPWORDS,
    _unanswerable,
    _words,
)
from src.services.maintenance import (
    BEHIND_COMPLETION_PCT,
    BEHIND_MISSED,
    PPM_STATE_BEHIND,
    PPM_STATE_TO_PLAN,
    PPM_STATE_WATCH,
    ppm_state,
)


def state(done, plan, missed, late):
    return ppm_state(missed=missed, late=late, completion_pct=round(done / plan * 100, 1))


class TestTheStateRuleReproducesThePage:
    """Every contract row on the PPM health table, with the state the page gives it."""

    @pytest.mark.parametrize("contract,done,plan,missed,late,expected", [
        ("Heating and gas · UK", 12, 18, 4, 2, PPM_STATE_BEHIND),
        ("Electrical · UK", 12, 14, 1, 1, PPM_STATE_WATCH),
        ("Mechanical PPM · Bishopsgate", 29, 32, 1, 2, PPM_STATE_WATCH),
        ("Facilities · Northgate Mall", 11, 12, 1, 0, PPM_STATE_WATCH),
        ("HVAC · Marina Heights", 15, 16, 0, 1, PPM_STATE_WATCH),
        ("Fire systems · UK", 19, 20, 0, 1, PPM_STATE_WATCH),
        ("Lifts · portfolio UK", 24, 24, 0, 0, PPM_STATE_TO_PLAN),
        ("M&E · Raffles Link", 12, 12, 0, 0, PPM_STATE_TO_PLAN),
    ])
    def test_row(self, contract, done, plan, missed, late, expected):
        assert state(done, plan, missed, late) == expected, contract


class TestTheStateRuleItself:
    def test_a_complete_contract_with_nothing_wrong_is_to_plan(self):
        assert state(24, 24, 0, 0) == PPM_STATE_TO_PLAN

    def test_one_missed_visit_is_enough_to_watch(self):
        assert state(23, 24, 1, 0) == PPM_STATE_WATCH

    def test_a_late_visit_alone_is_a_watch_not_behind(self):
        # A visit that happened late still happened.
        assert state(24, 24, 0, 1) == PPM_STATE_WATCH

    def test_missed_visits_outweigh_lateness(self):
        assert state(21, 24, BEHIND_MISSED, 0) == PPM_STATE_BEHIND
        assert state(24, 24, 0, 20) == PPM_STATE_WATCH

    def test_deep_under_completion_is_behind_however_few_are_missed(self):
        assert state(7, 10, 0, 0) == PPM_STATE_BEHIND        # 70%, under the bar

    def test_exactly_on_the_completion_bar_is_not_behind(self):
        assert ppm_state(missed=0, late=0,
                         completion_pct=BEHIND_COMPLETION_PCT) == PPM_STATE_WATCH

    def test_a_contract_with_no_completion_figure_is_not_called_behind(self):
        # Unknown is not the same as bad; with nothing missed or late there is no evidence.
        assert ppm_state(missed=0, late=0, completion_pct=None) == PPM_STATE_TO_PLAN

    def test_every_state_is_one_of_the_three_the_page_prints(self):
        seen = {state(d, 24, m, l)
                for d in (0, 12, 24) for m in (0, 1, 5) for l in (0, 1)}
        assert seen <= {PPM_STATE_BEHIND, PPM_STATE_WATCH, PPM_STATE_TO_PLAN}


class TestWhatACorroborationIsMadeOn:
    def test_common_words_cannot_carry_a_match(self):
        assert _words("The unit was observed during the inspection") == set()

    def test_subject_words_survive(self):
        assert "compressor" in _words("Compressor 2 contactor replaced")
        assert "contactor" in _words("Compressor 2 contactor replaced")

    def test_matching_ignores_case_and_punctuation(self):
        assert _words("Refrigerant charge 8% low.") == _words("refrigerant CHARGE low")

    def test_short_tokens_and_numbers_are_not_subject_words(self):
        # "8" and "L3" say nothing about what went wrong.
        assert _words("8 L3 ok") == set()

    def test_a_shared_subject_is_detectable(self):
        report = _words("Condenser coils fouled, refrigerant charge low")
        anomaly = _words("refrigerant loss on chiller condenser")
        assert len(report & anomaly) >= MIN_SHARED_TERMS

    def test_unrelated_text_does_not_reach_the_threshold(self):
        report = _words("Lighting ballast replaced in the car park")
        anomaly = _words("refrigerant loss on chiller condenser")
        assert len(report & anomaly) < MIN_SHARED_TERMS

    def test_the_stoplist_holds_no_subject_words(self):
        for kept in ("compressor", "refrigerant", "condenser", "bearing", "leak"):
            assert kept not in STOPWORDS


class TestAnUnanswerableQuestionIsNotAZero:
    def test_it_says_so_and_returns_no_count(self):
        r = _unanswerable("inspections here records no observations")
        assert r["answerable"] is False
        assert r["count"] is None          # not 0 — 0 means "we checked and found none"
        assert r["reason"]

    def test_poor_is_the_grade_the_page_names(self):
        assert POOR_GRADE == 4
