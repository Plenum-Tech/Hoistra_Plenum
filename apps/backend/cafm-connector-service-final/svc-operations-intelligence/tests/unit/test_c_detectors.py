"""The ten rules on the energy page's card, each fired and each held quiet.

Every detector is exercised twice: on a synthetic feed built to contain exactly the fault
the rule describes, and on the same feed without it. The quiet case matters as much as the
loud one — a detector that fires on a healthy office is worse than none, because it buries
the real anomaly under noise.
"""
from __future__ import annotations

import math
import random
from datetime import date, datetime, timedelta, timezone

from src.engines.energy import detectors as D

STEP = timedelta(minutes=30)
END = datetime(2024, 3, 28, 23, 30, tzinfo=timezone.utc)  # a Thursday
TARIFF = 0.28


def office(
    end: datetime = END,
    *,
    days: int = 35,
    base: float = 16.0,
    peak: float = 50.0,
    start_hour: float = 7.0,
    end_hour: float = 19.0,
    seed: int = 1,
    tweak=None,
) -> list[tuple[datetime, float]]:
    """Half-hourly office: overnight base, working-hours peak, weekends at base. ``tweak``
    is an optional f(t, value) -> value applied last, for building a fault into the feed."""
    rng = random.Random(seed)
    out = []
    t = end - timedelta(days=days) + STEP
    while t <= end:
        hour = t.hour + t.minute / 60
        v = peak if (start_hour <= hour < end_hour and t.weekday() < 5) else base
        v *= (1 + 0.04 * math.sin(hour / 24 * 2 * math.pi)) * rng.uniform(0.97, 1.03)
        if tweak:
            v = tweak(t, v)
        out.append((t, v))
        t += STEP
    return out


def keys_ok(hit):
    for k in ("anomaly_type", "metric_pct", "excess_kwh", "annualised_excess_kwh",
              "financial_gbp", "tariff_gbp_per_kwh", "detail"):
        assert k in hit, k
    assert hit["financial_gbp"] >= 0


# ── nonocc ──────────────────────────────────────────────────────────────────────────

class TestNonOccupancy:
    def test_quiet_on_a_healthy_office(self):
        # 16/50 = 32%: above the card's 30% but exactly the building's own norm, so no news.
        assert D.detect_nonocc_spike(office(), tariff=TARIFF) is None

    def test_fires_when_plant_is_left_running_overnight_this_week(self):
        def left_on(t, v):
            return v * 2.2 if (t > END - timedelta(days=7) and not D.DEFAULT_OCCUPANCY.occupied(t)) else v
        hit = D.detect_nonocc_spike(office(tweak=left_on), tariff=TARIFF)
        assert hit and hit["anomaly_type"] == "nonocc_spike"
        keys_ok(hit)
        assert hit["metric_pct"] > 60
        assert hit["detail"]["baseline_ratio_pct"] < hit["metric_pct"]
        assert hit["financial_gbp"] > 0

    def test_fires_on_the_card_rule_alone_when_there_is_no_history(self):
        # Only the current week exists: nothing to be "novel" against, so 30% is the rule.
        hit = D.detect_nonocc_spike(office(days=7, base=20, peak=50), tariff=TARIFF)
        assert hit and hit["metric_pct"] > 30


# ── schedule ────────────────────────────────────────────────────────────────────────

class TestSchedule:
    def test_quiet_when_plant_follows_the_calendar(self):
        assert D.detect_schedule_mismatch(office(), tariff=TARIFF) is None

    def test_fires_when_plant_starts_two_hours_early_every_day(self):
        hit = D.detect_schedule_mismatch(office(start_hour=5.0), tariff=TARIFF)
        assert hit and hit["anomaly_type"] == "schedule_mismatch"
        keys_ok(hit)
        assert hit["detail"]["median_start_early_min"] >= 100
        assert hit["financial_gbp"] > 0

    def test_fires_when_plant_runs_late(self):
        hit = D.detect_schedule_mismatch(office(end_hour=21.0), tariff=TARIFF)
        assert hit and hit["detail"]["median_stop_late_min"] >= 100

    def test_forty_minutes_is_within_tolerance(self):
        assert D.detect_schedule_mismatch(office(start_hour=6.5), tariff=TARIFF) is None


# ── baseload ────────────────────────────────────────────────────────────────────────

class TestBaseload:
    def test_quiet_when_the_floor_is_steady(self):
        assert D.detect_baseload_creep(office(days=42), tariff=TARIFF) is None

    @staticmethod
    def ramp(t) -> float:
        # +6% per ISO week over the last three COMPLETE weeks (END is a Thursday, so its own
        # week is partial and the detector rightly ignores it).
        last_complete = (END - timedelta(days=END.weekday() + 1)).isocalendar()[1]
        back = last_complete - t.isocalendar()[1]
        return 1 + 0.06 * max(0, 3 - back) if back >= 0 else 1.0

    def test_fires_when_the_overnight_floor_climbs_three_weeks_running(self):
        def creep(t, v):
            if not D.DEFAULT_OCCUPANCY.occupied(t) or t.weekday() >= 5:
                return v * self.ramp(t)
            return v
        hit = D.detect_baseload_creep(office(days=42, tweak=creep), tariff=TARIFF)
        assert hit and hit["anomaly_type"] == "baseload_creep"
        keys_ok(hit)
        assert all(r >= D.BASELOAD_RISE_PCT for r in hit["detail"]["weekly_rise_pct"][-3:])
        assert abs(hit["detail"]["daytime_move_pct"]) <= D.DAYTIME_FLAT_PCT

    def test_whole_building_getting_busier_is_not_creep(self):
        # Daytime rising with the floor: baseline_drift's job, not this rule's.
        def busier(t, v):
            return v * self.ramp(t)
        assert D.detect_baseload_creep(office(days=42, tweak=busier), tariff=TARIFF) is None


# ── peak ────────────────────────────────────────────────────────────────────────────

class TestPeak:
    def test_no_limit_means_no_judgement(self):
        assert D.detect_peak_excursion(office(), tariff=TARIFF) is None

    def test_quiet_under_capacity(self):
        # 50 kWh per half hour = 100 kW average; capacity 120 kW
        assert D.detect_peak_excursion(office(), tariff=TARIFF, capacity_kw=120) is None

    def test_fires_over_capacity_and_reports_when(self):
        def spike(t, v):
            return v * 1.6 if t.date() == (END - timedelta(days=2)).date() and t.hour == 14 else v
        hit = D.detect_peak_excursion(office(tweak=spike), tariff=TARIFF, capacity_kw=120)
        assert hit and hit["anomaly_type"] == "peak_excursion"
        keys_ok(hit)
        assert hit["detail"]["peak_kw"] > 120
        assert hit["detail"]["limit_basis"] == "agreed_capacity"
        assert "2024-03-26" in hit["detail"]["peak_at"]

    def test_prior_year_max_is_the_fallback_limit(self):
        hit = D.detect_peak_excursion(office(), tariff=TARIFF, prior_year_max_kw=90)
        assert hit and hit["detail"]["limit_basis"] == "prior_year_max"


# ── dataq ───────────────────────────────────────────────────────────────────────────

class TestDataQuality:
    def test_quiet_on_a_clean_feed(self):
        assert D.detect_data_quality(office(), tariff=TARIFF) is None

    def test_a_gap_is_counted_and_never_priced(self):
        pts = [(t, v) for t, v in office() if not (END - timedelta(days=3) < t < END - timedelta(days=2))]
        hit = D.detect_data_quality(pts, tariff=TARIFF)
        assert hit and hit["anomaly_type"] == "data_quality"
        assert hit["financial_gbp"] == 0.0 and hit["excess_kwh"] == 0.0
        assert hit["detail"]["missing_intervals"] >= 46
        assert hit["detail"]["longest_gap_hours"] >= 23

    def test_a_flatline_is_a_stuck_meter(self):
        def stuck(t, v):
            return 12.345 if END - timedelta(days=2) < t < END - timedelta(days=1) else v
        hit = D.detect_data_quality(office(tweak=stuck), tariff=TARIFF)
        assert hit and hit["detail"]["flatline_runs"] >= 1

    def test_estimated_reads_count_through_the_flags(self):
        pts = office()
        flags = [(t, "estimated" if i % 10 == 0 else "actual") for i, (t, _) in enumerate(pts)]
        hit = D.detect_data_quality(pts, tariff=TARIFF, flags=flags)
        assert hit and hit["detail"]["estimated_reads"] > 0 and hit["metric_pct"] >= 9

    def test_a_negative_read_always_fires(self):
        pts = office()
        pts[100] = (pts[100][0], -3.0)
        hit = D.detect_data_quality(pts, tariff=TARIFF)
        assert hit and hit["detail"]["negative_reads"] == 1


# ── tou ─────────────────────────────────────────────────────────────────────────────

class TestTimeOfUse:
    def test_quiet_when_the_peak_band_carries_its_share(self):
        assert D.detect_tou_misalignment(office(), tariff=TARIFF) is None

    def test_fires_when_discretionary_load_sits_in_the_red_band(self):
        def red(t, v):
            return v * 2.0 if (t.weekday() < 5 and 16 <= t.hour < 19) else v
        hit = D.detect_tou_misalignment(office(tweak=red), tariff=TARIFF)
        assert hit and hit["anomaly_type"] == "tou_misalignment"
        keys_ok(hit)
        assert hit["detail"]["premium_basis"] == "default_premium_half_of_tariff"

    def test_band_rates_price_the_shift_when_known(self):
        def red(t, v):
            return v * 2.0 if (t.weekday() < 5 and 16 <= t.hour < 19) else v
        bands = [D.TariffBand("red", 16, 19, True, rate=0.45)]
        hit = D.detect_tou_misalignment(office(tweak=red), tariff=TARIFF, bands=bands, offpeak_rate=0.20)
        assert hit and hit["detail"]["premium_basis"] == "band_rates"
        assert abs(hit["detail"]["premium_per_kwh"] - 0.25) < 1e-6


# ── weather ─────────────────────────────────────────────────────────────────────────

def months(n: int, *, start: date = date(2022, 1, 1)):
    out = []
    y, m = start.year, start.month
    for _ in range(n):
        out.append(date(y, m, 1))
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def weather_series(n=24, *, base=20000.0, per_hdd=40.0, per_cdd=25.0, drift_from: int | None = None, drift_kwh=6000.0, seed=3):
    rng = random.Random(seed)
    dd, kwh = [], []
    for i, d in enumerate(months(n)):
        hdd = max(0.0, 300 * math.cos((d.month - 1) / 12 * 2 * math.pi) + 300)
        # not a pure mirror of HDD — clipped at zero and phase-shifted, as a real cooling
        # season is — so the two regressors are correlated but not collinear
        cdd = max(0.0, 200 * -math.cos((d.month - 2) / 12 * 2 * math.pi) - 40) + rng.uniform(0, 15)
        dd.append((d, hdd, cdd))
        v = base + per_hdd * hdd + per_cdd * cdd + rng.gauss(0, 400)
        if drift_from is not None and i >= drift_from:
            v += drift_kwh
        kwh.append((d, v))
    return kwh, dd


class TestWeather:
    def test_quiet_when_weather_explains_the_bill(self):
        kwh, dd = weather_series()
        assert D.detect_weather_residual(kwh, dd, tariff=TARIFF) is None

    def test_fires_when_consumption_runs_above_the_weather_model(self):
        kwh, dd = weather_series(drift_from=18)
        hit = D.detect_weather_residual(kwh, dd, tariff=TARIFF)
        assert hit and hit["anomaly_type"] == "weather_residual"
        keys_ok(hit)
        assert hit["metric_pct"] > 100
        assert len(hit["detail"]["run_months"]) >= 3
        assert hit["detail"]["model"] == "hdd+cdd"

    def test_needs_a_year_of_months(self):
        kwh, dd = weather_series(n=8)
        assert D.detect_weather_residual(kwh, dd, tariff=TARIFF) is None


# ── fight ───────────────────────────────────────────────────────────────────────────

def trend(zone: str, *, minutes: int, both_for: int, step: int = 5):
    out = []
    t0 = END - timedelta(minutes=minutes)
    for i in range(minutes // step):
        t = t0 + timedelta(minutes=i * step)
        fighting = i * step < both_for
        out.append((t, zone, 60.0 if fighting else 40.0, 55.0 if fighting else 0.0))
    return out


class TestFight:
    def test_quiet_when_only_one_side_calls(self):
        assert D.detect_simultaneous_heating_cooling(trend("L3-E", minutes=240, both_for=0), tariff=TARIFF) is None

    def test_twenty_minutes_is_a_normal_changeover(self):
        assert D.detect_simultaneous_heating_cooling(trend("L3-E", minutes=240, both_for=20), tariff=TARIFF) is None

    def test_fires_after_thirty_minutes_and_is_unpriced_without_zone_kw(self):
        hit = D.detect_simultaneous_heating_cooling(trend("L3-E", minutes=240, both_for=60), tariff=TARIFF)
        assert hit and hit["anomaly_type"] == "simultaneous_heating_cooling"
        keys_ok(hit)
        assert hit["detail"]["zones_affected"] == ["L3-E"]
        assert hit["financial_gbp"] == 0 and hit["detail"]["priced"] is False

    def test_priced_when_the_zone_draw_is_known(self):
        hit = D.detect_simultaneous_heating_cooling(trend("L3-E", minutes=240, both_for=60), tariff=TARIFF, zone_kw=8.0)
        assert hit and hit["financial_gbp"] > 0


# ── regress ─────────────────────────────────────────────────────────────────────────

class TestRegression:
    CLOSED = END - timedelta(days=21)

    def test_quiet_while_the_fix_holds(self):
        def fixed(t, v):
            return v * 0.8 if t >= self.CLOSED else v
        hit = D.detect_post_works_regression(office(days=42, tweak=fixed), tariff=TARIFF,
                                             closed_work_orders=[(self.CLOSED, "WO-1042")])
        assert hit is None

    def test_fires_when_load_is_back_where_it_was(self):
        def back(t, v):
            if self.CLOSED <= t < self.CLOSED + timedelta(days=10):
                return v * 0.8
            return v
        hit = D.detect_post_works_regression(office(days=42, tweak=back), tariff=TARIFF,
                                             closed_work_orders=[(self.CLOSED, "WO-1042")])
        assert hit and hit["anomaly_type"] == "post_works_regression"
        keys_ok(hit)
        assert hit["detail"]["work_order"] == "WO-1042"
        assert hit["metric_pct"] >= 95
        assert hit["financial_gbp"] > 0

    def test_a_fix_that_never_showed_cannot_regress(self):
        hit = D.detect_post_works_regression(office(days=42), tariff=TARIFF,
                                             closed_work_orders=[(self.CLOSED, "WO-1042")])
        assert hit is None


# ── cop ─────────────────────────────────────────────────────────────────────────────

def chiller(*, kw_per_rt: float, n=96, rt=300.0, ambient=35.0):
    t0 = END - timedelta(hours=n / 4)
    return [(t0 + timedelta(minutes=15 * i), kw_per_rt * rt, rt, ambient) for i in range(n)]


class TestChiller:
    def test_quiet_at_design(self):
        assert D.detect_chiller_efficiency(chiller(kw_per_rt=0.70), tariff=TARIFF, design_kw_per_rt=0.68) is None

    def test_fires_fifteen_percent_over_design(self):
        hit = D.detect_chiller_efficiency(chiller(kw_per_rt=0.81), tariff=TARIFF, design_kw_per_rt=0.68)
        assert hit and hit["anomaly_type"] == "chiller_efficiency"
        keys_ok(hit)
        assert abs(hit["detail"]["actual_kw_per_rt"] - 0.81) < 1e-6
        assert hit["financial_gbp"] > 0

    def test_part_load_samples_are_dropped_when_capacity_is_known(self):
        samples = chiller(kw_per_rt=0.95, rt=80.0) + chiller(kw_per_rt=0.70, rt=300.0)
        hit = D.detect_chiller_efficiency(samples, tariff=TARIFF, design_kw_per_rt=0.68, design_capacity_rt=500.0)
        assert hit is None  # the inefficient samples were all below 40% load

    def test_ambient_is_matched_when_a_design_ambient_is_given(self):
        hot = chiller(kw_per_rt=0.90, ambient=46.0)      # unmatched ambient, dropped
        matched = chiller(kw_per_rt=0.70, ambient=35.0)
        hit = D.detect_chiller_efficiency(hot + matched, tariff=TARIFF, design_kw_per_rt=0.68, design_ambient_c=35.0)
        assert hit is None


def test_every_card_rule_has_a_detector_name():
    for rid in ("nonocc", "spike", "drift", "schedule", "baseload", "calendar", "weather",
                "peak", "fight", "cop", "regress", "dataq", "tou"):
        assert rid in D.RULE_IDS
    assert len(D.RULE_IDS) == 13


class TestOverlappingTrendExports:
    """Two BMS exports of one zone must not cancel each other out — at any phase.

    The live scan found nothing on a zone that was fighting for seventy minutes, because the
    table held two exports covering different hours. Any rule that first estimates one
    cadence for the zone and then asks whether two samples are "contiguous" breaks here: at
    one phase the estimate is five minutes, at another it is three, and the run is chopped
    below the threshold either way. A run is now simply consecutive samples that all say
    both are calling, ended by the first that says otherwise — phase and cadence do not
    enter into it.
    """

    @staticmethod
    def series(n, fighting, *, start_ago=0, step=5, heat=65.0, cool=58.0, zone="L3-East"):
        return [(END - timedelta(minutes=start_ago + step * i), zone, heat,
                 cool if i < fighting else 0.0) for i in range(n)]

    def test_one_export_fires(self):
        hit = D.detect_simultaneous_heating_cooling(self.series(60, 14), tariff=TARIFF)
        assert hit and max(r["minutes"] for r in hit["detail"]["runs"]) >= 60

    def test_a_second_export_at_another_phase_does_not_hide_it(self):
        both = self.series(60, 14) + self.series(48, 12, start_ago=172)
        hit = D.detect_simultaneous_heating_cooling(sorted(both, key=lambda x: x[0]), tariff=TARIFF)
        assert hit, "an unrelated export must not suppress a genuine run"
        assert max(r["minutes"] for r in hit["detail"]["runs"]) >= 60

    def test_exports_that_disagree_raise_nothing(self):
        # One says the zone was fighting, the other says it was not, over the same stretch.
        # An alarm is worth having only where the trends agree.
        clash = [(END - timedelta(minutes=5 * i), "L3-East", 65.0, 58.0) for i in range(14)] +                 [(END - timedelta(minutes=5 * i + 2), "L3-East", 65.0, 0.0) for i in range(14)]
        assert D.detect_simultaneous_heating_cooling(sorted(clash, key=lambda x: x[0]),
                                                     tariff=TARIFF) is None

    def test_the_same_instant_reported_twice_takes_the_stronger_signal(self):
        pair = [(END, "L3-East", 65.0, 0.0), (END, "L3-East", 0.0, 58.0),
                (END - timedelta(minutes=5), "L3-East", 65.0, 58.0),
                (END - timedelta(minutes=10), "L3-East", 65.0, 58.0),
                (END - timedelta(minutes=15), "L3-East", 65.0, 58.0),
                (END - timedelta(minutes=20), "L3-East", 65.0, 58.0),
                (END - timedelta(minutes=25), "L3-East", 65.0, 58.0),
                (END - timedelta(minutes=30), "L3-East", 65.0, 58.0),
                (END - timedelta(minutes=35), "L3-East", 65.0, 0.0)]
        hit = D.detect_simultaneous_heating_cooling(sorted(pair, key=lambda x: x[0]), tariff=TARIFF)
        assert hit and hit["detail"]["runs"][0]["minutes"] >= 30

    def test_a_sparse_trend_does_not_inherit_the_hour_between_its_rows(self):
        # Hourly rows: two fighting rows are 60 minutes apart, and one sample stands for at
        # most fifteen minutes — so the run is measured, not assumed.
        hourly = [(END - timedelta(hours=i), "L3-East", 65.0, 58.0 if i < 2 else 0.0)
                  for i in range(10)]
        hit = D.detect_simultaneous_heating_cooling(sorted(hourly, key=lambda x: x[0]), tariff=TARIFF)
        assert hit and hit["detail"]["runs"][0]["minutes"] == 75.0

    def test_a_second_export_cannot_invent_a_fight(self):
        quiet = self.series(60, 0) + self.series(48, 0, start_ago=172)
        assert D.detect_simultaneous_heating_cooling(quiet, tariff=TARIFF) is None

    def test_the_threshold_still_holds(self):
        assert D.detect_simultaneous_heating_cooling(self.series(60, 4), tariff=TARIFF) is None
