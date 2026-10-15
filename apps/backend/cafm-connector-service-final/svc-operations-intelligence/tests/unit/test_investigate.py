"""Investigating an asset: what the evidence rules fire on, and what they refuse to claim.

The rules are the whole thing. A source that returned nothing has to reach the evidence as a
finding rather than vanish, a cause inferred from a trend must not be dressed as a measurement,
and no proposal may be written. Those three are what these tests pin.
"""
from __future__ import annotations

import inspect

import pytest

from src.engines.energy import investigate as inv
from src.engines.energy.investigate import (
    AMBIENT_MATCH_C,
    CONF_MEASURED_GAP,
    CONF_RECORD_ABSENT,
    CONF_TREND_TO_CAUSE,
    KW_PER_RT_DRIFT,
    KW_PER_RT_TOLERANCE,
    METHOD,
    _actions,
    _conclusion,
    _evidence,
)

ASSET = {"asset_id": "a1", "asset_name": "Chiller plant CH-2",
         "building_id": "b1", "building": "Marina Heights"}


def walk(**over):
    """A walk where everything was found and nothing is wrong, overridden per test."""
    base = {
        "bms_trend": {"source": "bms_trend", "status": "found", "badge": "9,800 points",
                      "kw_per_rt": 0.68, "design_kw_per_rt": 0.68, "over_design": False,
                      "kw_per_rt_drift": None, "drift_at_matched_ambient": True,
                      "early_kw_per_rt": 0.68, "late_kw_per_rt": 0.68,
                      "ambient_early_c": 34.0, "ambient_late_c": 34.5,
                      "condenser_approach": None},
        "meter_reading": {"source": "meter_reading", "status": "found", "badge": "sub-metered"},
        "utility_bill": {"source": "utility_bill", "status": "partial", "badge": "+0%",
                         "change_pct": 0.0},
        "weather": {"source": "weather", "status": "found", "badge": "flat", "flat": True},
        "work_order": {"source": "work_order", "status": "found", "badge": "2 on record",
                       "closed_without_report": 0, "latest": {}},
        "document": {"source": "document", "status": "found", "badge": "3 on file"},
    }
    for k, v in over.items():
        base[k] = {**base[k], **v}
    return base


def kinds(ev):
    return {e["kind"] for e in ev}


class TestAMeasuredGapAgainstDesign:
    def test_running_over_design_with_the_weather_flat_is_a_measured_gap(self):
        ev = _evidence(walk(bms_trend={"kw_per_rt": 0.81, "over_design": True}), ASSET)
        gap = next(e for e in ev if e["kind"] == "measured gap")
        assert gap["confidence"] == CONF_MEASURED_GAP
        assert "0.81" in gap["statement"] and "0.68" in gap["statement"]
        assert "not weather" in gap["statement"]
        assert set(gap["sources"]) == {"bms_trend", "weather"}

    def test_without_flat_degree_days_the_weather_is_not_ruled_out(self):
        # The same reading supports a weaker claim when the confounder is still live.
        ev = _evidence(walk(bms_trend={"kw_per_rt": 0.81, "over_design": True},
                            weather={"flat": False}), ASSET)
        gap = next(e for e in ev if e["kind"] == "measured gap")
        assert gap["confidence"] < CONF_MEASURED_GAP
        assert "not ruled out" in gap["statement"]
        assert gap["sources"] == ["bms_trend"]

    def test_running_at_design_says_nothing(self):
        assert "measured gap" not in kinds(_evidence(walk(), ASSET))


class TestDriftIsOnlyClaimedAtMatchedAmbient:
    def test_a_drift_at_comparable_ambient_names_a_cause(self):
        ev = _evidence(walk(bms_trend={
            "kw_per_rt_drift": 0.09, "drift_at_matched_ambient": True,
            "early_kw_per_rt": 0.72, "late_kw_per_rt": 0.81,
            "ambient_early_c": 34.0, "ambient_late_c": 34.5}), ASSET)
        d = next(e for e in ev if e["kind"] == "trend to cause")
        assert d["confidence"] == CONF_TREND_TO_CAUSE
        assert "fouling" in d["statement"]
        assert "34.0" in d["statement"] and "34.5" in d["statement"]

    def test_a_drift_the_walk_refused_to_match_is_not_claimed(self):
        # A hotter second half explains a worse second half without anything being wrong.
        ev = _evidence(walk(bms_trend={"kw_per_rt_drift": None,
                                       "drift_at_matched_ambient": False}), ASSET)
        assert "trend to cause" not in kinds(ev)

    def test_a_drift_under_the_bar_is_not_named(self):
        ev = _evidence(walk(bms_trend={"kw_per_rt_drift": KW_PER_RT_DRIFT / 2,
                                       "drift_at_matched_ambient": True}), ASSET)
        assert "trend to cause" not in kinds(ev)


class TestWhatIsNotThereIsEvidence:
    def test_a_closed_visit_with_no_report_and_no_log_is_a_full_confidence_finding(self):
        ev = _evidence(walk(
            work_order={"closed_without_report": 1,
                        "latest": {"title": "cooling tower PPM", "closed_at": "2026-07-03"}},
            document={"status": "not_found", "badge": "not found"}), ASSET)
        m = next(e for e in ev if e["kind"] == "missing record")
        # Nothing is inferred from an absent record, so nothing is discounted for it.
        assert m["confidence"] == CONF_RECORD_ABSENT == 1.0
        assert "cooling tower PPM" in m["statement"] and "2026-07-03" in m["statement"]
        assert set(m["sources"]) == {"work_order", "document"}

    def test_an_unreadable_signal_is_reported_rather_than_skipped(self):
        # The approach columns are empty on both databases. Saying so is the finding.
        ev = _evidence(walk(bms_trend={
            "condenser_approach": "not on record — the columns are empty here"}), ASSET)
        m = next(e for e in ev if e["kind"] == "missing reading")
        assert m["confidence"] == CONF_RECORD_ABSENT
        assert "cannot be read" in m["statement"]

    def test_a_complete_record_produces_no_missing_finding(self):
        assert "missing record" not in kinds(_evidence(walk(), ASSET))


class TestATotalThatCannotAttribute:
    def test_consumption_up_confirms_the_cost_and_says_it_cannot_attribute_it(self):
        ev = _evidence(walk(utility_bill={"change_pct": 12.0}), ASSET)
        t = next(e for e in ev if e["kind"] == "corroborating total")
        assert "+12%" in t["statement"]
        assert "cannot attribute" in t["statement"]

    def test_consumption_down_is_not_offered_as_evidence_of_a_problem(self):
        ev = _evidence(walk(utility_bill={"change_pct": -8.0}), ASSET)
        assert "corroborating total" not in kinds(ev)


class TestTheConclusionSaysWhatItRestsOn:
    def test_a_missing_record_plus_a_trend_names_the_cause_and_demands_confirmation(self):
        w = walk(bms_trend={"kw_per_rt": 0.81, "over_design": True, "kw_per_rt_drift": 0.09,
                            "drift_at_matched_ambient": True,
                            "early_kw_per_rt": 0.72, "late_kw_per_rt": 0.81},
                 work_order={"closed_without_report": 1, "latest": {"title": "tower PPM"}},
                 document={"status": "not_found"})
        ev = _evidence(w, ASSET)
        c = _conclusion(ev, w, ASSET, {"annual_cost": 22700.0, "weeks": 12.0,
                                       "currency": "AED"})
        assert "water treatment" in c["cause"]
        assert c["rests_on"] == "inference"
        assert c["confirmation_required"] is True
        assert "before anything is claimed" in c["caveat"]
        assert c["cost_annualised"] == 22700.0
        assert c["cost_to_date"] and c["cost_to_date"] < c["cost_annualised"]

    def test_no_evidence_is_said_plainly_rather_than_guessed_at(self):
        c = _conclusion([], walk(), ASSET, {})
        assert c["cause"] is None
        assert c["rests_on"] == "no evidence"
        assert "not on record" in c["statement"]

    def test_cost_to_date_is_the_annual_figure_scaled_by_how_long_it_has_run(self):
        c = _conclusion([{"kind": "missing record"}], walk(), ASSET,
                        {"annual_cost": 5200.0, "weeks": 26.0})
        assert c["cost_to_date"] == pytest.approx(2600.0, abs=1.0)

    def test_a_run_longer_than_a_year_does_not_exceed_the_annual_figure(self):
        c = _conclusion([{"kind": "missing record"}], walk(), ASSET,
                        {"annual_cost": 5200.0, "weeks": 104.0})
        assert c["cost_to_date"] == 5200.0


class TestNothingIsWritten:
    def test_the_module_contains_no_write_at_all(self):
        src = inspect.getsource(inv).lower()
        for forbidden in ("insert into", "update plenum_cafm", "delete from",
                          "session.commit", "session.add"):
            assert forbidden not in src, f"investigate performs a write: {forbidden}"

    def test_every_action_is_a_proposal_naming_where_it_would_go(self):
        w = walk(bms_trend={"kw_per_rt": 0.81, "over_design": True, "kw_per_rt_drift": 0.09,
                            "drift_at_matched_ambient": True,
                            "early_kw_per_rt": 0.72, "late_kw_per_rt": 0.81},
                 work_order={"closed_without_report": 1,
                             "latest": {"vendor": "Gulf Cooling", "title": "tower PPM"}},
                 document={"status": "not_found"})
        actions = _actions(_evidence(w, ASSET), w, ASSET)
        assert actions, "an asset with a cause and a missing record should propose something"
        ids = {a["id"] for a in actions}
        assert {"raise_work_order", "request_records"} <= ids
        for a in actions:
            assert a["label"] and a["detail"]
            # Either it names the endpoint that would carry it out, or it says plainly that
            # this platform does not carry it out at all.
            assert a.get("endpoint") or a.get("note")

    def test_the_raise_proposal_carries_the_body_it_would_be_raised_with(self):
        w = walk(bms_trend={"kw_per_rt": 0.81, "over_design": True})
        a = next(x for x in _actions(_evidence(w, ASSET), w, ASSET)
                 if x["id"] == "raise_work_order")
        assert a["endpoint"] == "POST /api/work-orders/"
        assert a["body"]["asset"] == ASSET["asset_name"]
        assert a["body"]["building_id"] == ASSET["building_id"]

    def test_a_bms_change_says_this_platform_does_not_make_it(self):
        w = walk(bms_trend={"over_design": True})
        a = next(x for x in _actions(_evidence(w, ASSET), w, ASSET) if x["id"] == "resequence")
        assert a["endpoint"] is None
        assert "does not make it" in a["note"]

    def test_nothing_is_proposed_when_nothing_was_found(self):
        assert _actions([], walk(), ASSET) == []


class TestTheRuleIsNamed:
    def test_method_identifies_the_rule_set(self):
        assert METHOD == "rule:asset-investigation/v1"

    def test_the_thresholds_are_stated_constants(self):
        assert KW_PER_RT_TOLERANCE > 0
        assert KW_PER_RT_DRIFT > 0
        assert AMBIENT_MATCH_C > 0

    def test_an_absent_record_is_never_discounted_below_a_measurement(self):
        assert CONF_RECORD_ABSENT >= CONF_MEASURED_GAP > CONF_TREND_TO_CAUSE


# ── Readings outside their bands (28 Sep 2026) ───────────────────────────────────────
# AHU-3 read "6 out of band" in the walk while its conclusion spoke only of a missing report.
# A reading against the limit set for it is a measurement with nothing inferred, so it is a
# finding at full confidence, it reaches the conclusion, and it proposes an inspection.

AHU = {"asset_id": "a3", "asset_name": "AHU-3", "building_id": "b1", "building": "Bishopsgate Tower"}
OOB = [
    {"reading_type": "fan_current", "value": 18.4, "unit": "A", "band_lo": 12.0, "band_hi": 17.5,
     "days_out_of_band": 2, "days": 2},
    {"reading_type": "fan_vibration", "value": 4.6, "unit": "mm/s", "band_lo": 0.0, "band_hi": 4.5,
     "days_out_of_band": 1, "days": 2},
]


def bms_readings(**over):
    return {"bms_trend": {"source": "bms_trend", "status": "found", "badge": "192 points · 2 out of band",
                          "points": 192, "graded_types": 8, "out_of_band": OOB, **over}}


class TestReadingsOutsideTheirBands:
    def test_an_out_of_band_reading_is_a_full_confidence_finding(self):
        from src.engines.energy.investigate import CONF_READING_OUT_OF_BAND
        ev = _evidence(walk(**bms_readings()), AHU)
        f = next(e for e in ev if e["kind"] == "out of band")
        assert f["confidence"] == CONF_READING_OUT_OF_BAND == 1.0
        assert f["sources"] == ["bms_trend"]
        assert "2 of 8" in f["statement"]
        assert "fan current 18.4 A (band 12–17.5, out of band on 2 of 2 days)" in f["statement"]
        assert "fan vibration 4.6 mm/s (band 0–4.5, out of band on 1 of 2 days)" in f["statement"]

    def test_readings_in_band_say_nothing(self):
        assert "out of band" not in kinds(_evidence(walk(**bms_readings(out_of_band=[])), AHU))

    def test_with_a_missing_report_the_conclusion_names_both(self):
        w = walk(**bms_readings(),
                 work_order={"closed_without_report": 1, "latest": {"title": "supply fan PPM"}},
                 document={"status": "not_found"})
        c = _conclusion(_evidence(w, AHU), w, AHU, {})
        assert c["cause"] == ("fan current and fan vibration are outside their bands, and the "
                              "last visit closed without a report.")
        assert c["rests_on"] == "record"
        assert c["confirmation_required"] is True

    def test_alone_the_conclusion_is_the_symptom_not_a_cause(self):
        w = walk(**bms_readings())
        c = _conclusion(_evidence(w, AHU), w, AHU, {})
        assert c["cause"] == "fan current and fan vibration are outside their bands."
        assert c["confirmation_required"] is True
        assert "symptom, not the cause" in c["caveat"]

    def test_a_chiller_measured_gap_keeps_its_precedence(self):
        w = walk(bms_trend={"kw_per_rt": 0.81, "over_design": True, "out_of_band": OOB})
        c = _conclusion(_evidence(w, ASSET), w, ASSET, {})
        assert "design efficiency" in c["cause"]

    def test_it_proposes_an_inspection_the_dock_turns_into_a_draft(self):
        w = walk(**bms_readings(), work_order={"latest": {"vendor": "Apex Mechanical"}})
        a = next(x for x in _actions(_evidence(w, AHU), w, AHU) if x["id"] == "inspect_out_of_band")
        assert a["endpoint"] == "POST /api/work-orders/"
        assert a["body"]["request_type"] == "inspection"
        assert "fan current 18.4 A (band 12–17.5" in a["body"]["issue_description"]
        assert "Apex Mechanical" in a["detail"]


class TestReviewFixes:
    def test_the_draft_is_described_as_going_to_the_vendor_it_is_addressed_to(self):
        # The dock addresses every draft to the asset's registered vendor; the action text used
        # to name the latest work order's vendor instead, so it could say "draft to X" and
        # open a draft to Y.
        asset = {**AHU, "vendor": "Apex Mechanical"}
        w = walk(**bms_readings(),
                 work_order={"closed_without_report": 1,
                             "latest": {"vendor": "Mitie", "title": "supply fan PPM"}},
                 document={"status": "not_found"})
        acts = {a["id"]: a for a in _actions(_evidence(w, asset), w, asset)}
        assert "Apex Mechanical" in acts["request_records"]["detail"]
        assert "Apex Mechanical" in acts["inspect_out_of_band"]["detail"]
        assert "carried out by Mitie" in acts["request_records"]["body"]["issue_description"]

    def test_readings_are_written_out_in_full_not_in_scientific_notation(self):
        from src.engines.energy.investigate import _reading_phrase
        p = _reading_phrase({"reading_type": "energy_kwh", "value": 1234567.0, "unit": "kWh",
                             "band_lo": None, "band_hi": 1000000.0, "days_out_of_band": 3, "days": 3})
        assert p == "energy kwh 1,234,567 kWh (limit 1,000,000, out of band on 3 of 3 days)"
        assert "fan current 18.4 A (band 12–17.5" in _reading_phrase(OOB[0])
