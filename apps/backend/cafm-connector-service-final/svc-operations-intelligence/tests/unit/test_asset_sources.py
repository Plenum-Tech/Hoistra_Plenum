"""The three datasets behind an investigation's bms_trend, utility_bill and weather lines.

Each is what an endpoint returns and what the walk's one-liner is derived from, so the two can
never disagree. What these tests pin is the arithmetic and the refusals: supply meters counted
once, last year compared only when it is on record, a reading graded only against a band that was
set, degree days computed from a real archive and clipped to the day it actually holds, and a
failed read never passing for an empty one.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy.dialects import postgresql

from src.engines.energy import asset_sources as src
from src.engines.energy import open_meteo

UNBOUND = re.compile(r"(?<![:\w]):[A-Za-z_]\w*")
UTC = timezone.utc
UNTIL = datetime(2026, 9, 28, 12, tzinfo=UTC)
SINCE = UNTIL - timedelta(weeks=8)


class _Nested:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


class FakeSession:
    def __init__(self, route=None):
        self.route = route or (lambda sql, params: [])
        self.sql: list[str] = []
        self.params: list[dict] = []
        self.events: list[str] = []

    def begin_nested(self):
        return _Nested()

    async def commit(self):
        self.events.append("commit")

    async def execute(self, clause, params=None):
        compiled = str(clause.compile(dialect=postgresql.dialect()))
        self.sql.append(compiled)
        self.params.append(params or {})
        self.events.append("sql")
        out = self.route(compiled, params or {})
        if isinstance(out, Exception):
            raise out
        return _Result(out)


def _no_unbound(session):
    for sql in session.sql:
        assert not UNBOUND.findall(sql), sql


# ── BMS readings ─────────────────────────────────────────────────────────────────────

BANDS = {"fan_current": {"lo": 12.0, "hi": 17.5, "unit": "A", "note": "fan motor"},
         "co2": {"lo": 400.0, "hi": 1000.0, "unit": "ppm", "note": None}}


def _reading(t, v, at):
    return {"reading_type": t, "value": v, "unit": BANDS.get(t, {}).get("unit"), "recorded_at": at}


def _bms_route(readings, chiller=None):
    def route(sql, p):
        if "chiller_performance_readings" in sql:
            return chiller or []
        if "chiller_design_specs" in sql:
            return []
        if "asset_readings" in sql:
            return readings
        return []
    return route


class TestBmsTrend:
    async def test_readings_become_daily_buckets_graded_against_their_bands(self):
        d1, d2 = datetime(2026, 9, 26, 1, tzinfo=UTC), datetime(2026, 9, 27, 23, tzinfo=UTC)
        s = FakeSession(_bms_route([
            _reading("fan_current", 16.0, d1), _reading("fan_current", 18.0, d1 + timedelta(hours=1)),
            _reading("fan_current", 18.4, d2), _reading("co2", 880, d2),
            _reading("humidity", 61, d2)]))
        out = await src.bms_trend(s, asset_id="a1", since=SINCE, until=UNTIL, bands=BANDS)
        _no_unbound(s)
        assert out["status"] == "found" and out["kind"] == "asset_readings"
        assert out["points"] == 5 and out["reading_types"] == 3
        fan = next(t for t in out["types"] if t["reading_type"] == "fan_current")
        assert [d["day"] for d in fan["days"]] == ["2026-09-26", "2026-09-27"]
        assert fan["days"][0] == {"day": "2026-09-26", "min": 16.0, "mean": 17.0, "max": 18.0,
                                  "n": 2, "out_of_band": 1}
        assert fan["days_out_of_band"] == 2 and fan["out_of_band_points"] == 2
        assert fan["latest"] == {"value": 18.4, "at": d2.isoformat(), "state": "out_of_band"}
        # A reading with no band is ungraded, never "in band".
        hum = next(t for t in out["types"] if t["reading_type"] == "humidity")
        assert hum["latest"]["state"] == "unknown" and hum["band"] is None
        assert out["graded_types"] == 2
        assert [x["reading_type"] for x in out["out_of_band"]] == ["fan_current"]
        oob = out["out_of_band"][0]
        assert (oob["value"], oob["band_lo"], oob["band_hi"], oob["days_out_of_band"], oob["days"]) \
            == (18.4, 12.0, 17.5, 2, 2)

    async def test_a_chiller_record_is_read_as_kw_per_rt_first(self):
        chiller = [{"day": datetime(2026, 9, 27, tzinfo=UTC), "n": 96, "kw_per_rt": 0.7412,
                    "ambient_c": 31.25}]
        s = FakeSession(_bms_route([], chiller=chiller))
        out = await src.bms_trend(s, asset_id="a1", since=SINCE, until=UNTIL, bands={})
        assert out["kind"] == "chiller_performance" and out["points"] == 96
        assert out["series"] == [{"day": "2026-09-27", "kw_per_rt": 0.741, "ambient_c": 31.2, "n": 96}]

    async def test_nothing_in_either_table_is_not_found(self):
        out = await src.bms_trend(FakeSession(), asset_id="a1", since=SINCE, until=UNTIL, bands={})
        assert out["status"] == "not_found"

    async def test_a_failed_read_is_unreadable(self):
        s = FakeSession(lambda sql, p: RuntimeError("boom"))
        out = await src.bms_trend(s, asset_id="a1", since=SINCE, until=UNTIL, bands={})
        assert out["status"] == "unreadable"


# ── Utility bill (metered) ───────────────────────────────────────────────────────────

def _meter(mid, fuel, sub, tariff, period, day, kwh):
    return {"meter_id": mid, "meter_ref": mid.upper(), "fuel": fuel, "sub": sub,
            "tariff": tariff, "period": period, "day": day, "kwh": kwh}


def _bill_route(rows, first=None):
    def route(sql, p):
        if "min(r.reading_at)" in sql:
            return first or []
        if "meter_readings" in sql:
            return rows
        return []
    return route


def _days(start, n):
    return [datetime.combine(start + timedelta(days=i), datetime.min.time(), UTC) for i in range(n)]


class TestUtilityBill:
    async def test_supply_meters_only_split_by_fuel_with_weekly_buckets(self):
        now_days = _days(SINCE.date(), 56)
        ly_days = _days(src.a_year_before(SINCE).date(), 56)
        rows = ([_meter("e0", "electricity", False, 0.284, "now", d, 100.0) for d in now_days]
                + [_meter("e0", "electricity", False, 0.284, "last_year", d, 80.0) for d in ly_days]
                # A sub-meter beneath the supply: its kWh is already in the supply's.
                + [_meter("e-b", "electricity", True, 0.284, "now", d, 999.0) for d in now_days]
                + [_meter("g1", "gas", False, None, "now", d, 50.0) for d in now_days]
                + [_meter("g1", "gas", False, None, "last_year", d, 50.0) for d in ly_days])
        first = [{"meter_id": "e0", "first_at": datetime(2024, 1, 1, tzinfo=UTC)},
                 {"meter_id": "g1", "first_at": datetime(2024, 1, 1, tzinfo=UTC)}]
        s = FakeSession(_bill_route(rows, first))
        out = await src.utility_bill(s, building_id="b1", since=SINCE, until=UNTIL)
        _no_unbound(s)
        assert out["status"] == "found"
        elec = out["fuels"]["electricity"]
        assert elec["meters_basis"] == "supply"
        assert elec["now_kwh"] == 5600 and elec["last_year_kwh"] == 4480
        assert elec["change_pct"] == 25.0
        assert elec["cost_gbp"] == {"now": 1590.4, "last_year": 1272.32,
                                    "basis": "at the meter's tariff"}
        assert len(elec["weeks"]) == 8 and elec["weeks"][0]["now_kwh"] == 700
        assert elec["weeks"][0]["now_days"] == 7 and elec["weeks"][0]["last_year_days"] == 7
        gas = out["fuels"]["gas"]
        assert gas["change_pct"] == 0.0
        assert gas["cost_gbp"] is None  # no tariff on the gas meter
        assert out["total"]["now_kwh"] == 8400 and out["total"]["change_pct"] == 15.4
        assert {m["meter_ref"] for m in out["meters"]} == {"E0", "G1"}
        assert "stands in for the bill" in out["basis"]

    async def test_a_fuel_with_no_supply_meter_uses_its_sub_meters(self):
        now_days = _days(SINCE.date(), 56)
        rows = [_meter("e-b", "electricity", True, None, "now", d, 10.0) for d in now_days]
        out = await src.utility_bill(FakeSession(_bill_route(rows)), building_id="b1",
                                     since=SINCE, until=UNTIL)
        assert out["fuels"]["electricity"]["meters_basis"] == "sub-meters (no supply meter)"
        assert out["fuels"]["electricity"]["now_kwh"] == 560

    async def test_last_year_off_the_record_gives_no_change(self):
        # Bishopsgate: one day of last year's window on record against 56 this year.
        now_days = _days(SINCE.date(), 56)
        rows = ([_meter("e0", "electricity", False, None, "now", d, 100.0) for d in now_days]
                + [_meter("e0", "electricity", False, None, "last_year",
                          datetime(2025, 9, 28, tzinfo=UTC), 8383.0)])
        first = [{"meter_id": "e0", "first_at": datetime(2025, 9, 28, tzinfo=UTC)}]
        out = await src.utility_bill(FakeSession(_bill_route(rows, first)), building_id="b1",
                                     since=SINCE, until=UNTIL)
        assert out["total"]["change_pct"] is None
        assert out["total"]["comparable"] is False
        assert out["total"]["last_year_days"] == 1 and out["total"]["now_days"] == 56
        assert out["first_reading_at"].startswith("2025-09-28")

    async def test_no_consumption_is_not_found_and_a_failure_unreadable(self):
        assert (await src.utility_bill(FakeSession(), building_id="b1", since=SINCE,
                                       until=UNTIL))["status"] == "not_found"
        bad = FakeSession(lambda sql, p: RuntimeError("boom"))
        assert (await src.utility_bill(bad, building_id="b1", since=SINCE,
                                       until=UNTIL))["status"] == "unreadable"


# ── Weather (degree days) ────────────────────────────────────────────────────────────

LOCATION = [{"building_id": "b1", "name": "Bishopsgate Tower", "postcode": "EC2M 9ZZ",
             "city": "London", "country_code": "UK", "latitude": None, "longitude": None}]


def _weather_route(location=LOCATION, table=None):
    def route(sql, p):
        if "weather_degree_days" in sql:
            return table or []
        if "plenum_cafm.buildings" in sql:
            return location
        return []
    return route


def _archive(start: date, end: date, temp_of):
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    return {"daily": {"time": [d.isoformat() for d in days],
                      "temperature_2m_mean": [temp_of(d) for d in days]}}


@pytest.fixture
def meteo(monkeypatch):
    """Open-Meteo answered by a fake transport; what was asked is recorded."""
    open_meteo.clear_cache()
    asked: list[httpx.Request] = []
    state = {"temp": lambda d: 10.0, "fail": None, "geo": {"London": (51.50853, -0.12574)}}

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(request)
        if state["fail"]:
            return httpx.Response(503, json={"reason": state["fail"]})
        q = dict(request.url.params)
        if "geocoding" in request.url.host:
            hit = state["geo"].get(q.get("name"))
            return httpx.Response(200, json={"results": [
                {"name": q["name"], "latitude": hit[0], "longitude": hit[1],
                 "country_code": q.get("countryCode")}]} if hit else {})
        start, end = date.fromisoformat(q["start_date"]), date.fromisoformat(q["end_date"])
        return httpx.Response(200, json=_archive(start, end, state["temp"]))

    monkeypatch.setattr(open_meteo, "TRANSPORT", httpx.MockTransport(handler))
    yield {"asked": asked, "state": state}
    open_meteo.clear_cache()


class TestDegreeDays:
    async def test_the_postcode_falls_back_to_the_city_and_uk_is_asked_as_gb(self, meteo):
        s = FakeSession(_weather_route())
        out = await src.degree_days(s, building_id="b1", start=date(2026, 8, 3),
                                    end=date(2026, 9, 28), today=date(2026, 9, 28))
        _no_unbound(s)
        geo = [dict(r.url.params) for r in meteo["asked"] if "geocoding" in r.url.host]
        assert [g["name"] for g in geo] == ["EC2M 9ZZ", "London"]
        assert {g["countryCode"] for g in geo} == {"GB"}
        assert out["location"] == {"query": "London", "name": "London", "latitude": 51.50853,
                                   "longitude": -0.12574, "resolved_from": "city"}
        assert out["source"] == "open-meteo"

    async def test_hdd_and_cdd_at_15_5_this_period_against_the_same_dates_last_year(self, meteo):
        # 10 °C all last year, 12 °C this year: HDD 5.5 against 3.5 a day, no cooling.
        meteo["state"]["temp"] = lambda d: 12.0 if d.year == 2026 else 10.0
        out = await src.degree_days(FakeSession(_weather_route()), building_id="b1",
                                    start=date(2026, 9, 1), end=date(2026, 9, 28),
                                    today=date(2026, 9, 28))
        assert out["status"] == "found" and out["base_temp_c"] == 15.5
        # Today is partial, so the window ends yesterday: 27 days.
        assert out["window"] == {"start": "2026-09-01", "end": "2026-09-27", "days": 27}
        t = out["total"]
        assert (t["hdd"], t["last_year_hdd"], t["cdd"], t["last_year_cdd"]) == (94.5, 148.5, 0.0, 0.0)
        assert t["hdd_change_pct"] == -36.4
        assert t["cdd_change_pct"] is None  # nothing to compare against

    async def test_the_window_ends_at_the_last_day_the_archive_holds(self, meteo):
        meteo["state"]["temp"] = lambda d: None if d >= date(2026, 9, 24) else 14.0
        out = await src.degree_days(FakeSession(_weather_route()), building_id="b1",
                                    start=date(2026, 9, 1), end=date(2026, 9, 28),
                                    today=date(2026, 9, 28))
        assert out["window"]["end"] == "2026-09-23"
        assert "2026-09-23" in out["detail"]

    async def test_months_are_returned_for_the_endpoint(self, meteo):
        out = await src.degree_days(FakeSession(_weather_route()), building_id="b1",
                                    start=date(2026, 7, 1), end=date(2026, 9, 28),
                                    today=date(2026, 9, 28), monthly=True)
        assert [m["month"] for m in out["months"]] == ["2026-07", "2026-08", "2026-09"]
        assert out["months"][0]["days"] == 31 and out["months"][0]["hdd"] == 170.5

    async def test_the_platforms_own_record_is_used_first(self, meteo):
        table = [{"month": date(2026, m, 1), "hdd": 50, "cdd": 5, "base_temp_c": 15.5,
                  "station": "London City", "source": "degreedays.net"} for m in (7, 8, 9)]
        out = await src.degree_days(FakeSession(_weather_route(table=table)), building_id="b1",
                                    start=date(2026, 7, 1), end=date(2026, 9, 28),
                                    today=date(2026, 9, 28), monthly=True)
        assert out["source"] == "weather_degree_days" and out["station"] == "London City"
        assert not meteo["asked"]

    async def test_a_provider_failure_is_unreadable_not_no_degree_days(self, meteo):
        meteo["state"]["fail"] = "upstream down"
        out = await src.degree_days(FakeSession(_weather_route()), building_id="b1",
                                    start=date(2026, 9, 1), end=date(2026, 9, 28),
                                    today=date(2026, 9, 28))
        assert out["status"] == "unreadable" and "503" in out["detail"]

    async def test_no_location_on_record_is_not_found(self, meteo):
        loc = [{**LOCATION[0], "postcode": None, "city": None}]
        out = await src.degree_days(FakeSession(_weather_route(location=loc)), building_id="b1",
                                    start=date(2026, 9, 1), end=date(2026, 9, 28),
                                    today=date(2026, 9, 28))
        assert out["status"] == "not_found" and "no location" in out["detail"]
        assert not meteo["asked"]

    async def test_a_location_nobody_can_geocode_is_not_found(self, meteo):
        meteo["state"]["geo"] = {}
        out = await src.degree_days(FakeSession(_weather_route()), building_id="b1",
                                    start=date(2026, 9, 1), end=date(2026, 9, 28),
                                    today=date(2026, 9, 28))
        assert out["status"] == "not_found" and "EC2M 9ZZ" in out["detail"]

    async def test_answers_are_cached_so_a_second_read_asks_nothing(self, meteo):
        args = dict(building_id="b1", start=date(2026, 9, 1), end=date(2026, 9, 28),
                    today=date(2026, 9, 28))
        await src.degree_days(FakeSession(_weather_route()), **args)
        n = len(meteo["asked"])
        await src.degree_days(FakeSession(_weather_route()), **args)
        assert len(meteo["asked"]) == n


class TestTheCommercialKey:
    def test_without_a_key_the_free_endpoints_are_used(self, monkeypatch):
        monkeypatch.setattr(open_meteo.settings, "open_meteo_api_key", "", raising=False)
        base, extra = open_meteo._endpoint("archive")
        assert base == "https://archive-api.open-meteo.com" and extra == {}

    def test_with_a_key_the_customer_endpoints_carry_it(self, monkeypatch):
        monkeypatch.setattr(open_meteo.settings, "open_meteo_api_key", "k123", raising=False)
        base, extra = open_meteo._endpoint("archive")
        assert base == "https://customer-archive-api.open-meteo.com" and extra == {"apikey": "k123"}


# ── Review fixes, 28 Sep 2026 ────────────────────────────────────────────────────────

class TestReviewFixes:
    async def test_the_degree_day_table_is_asked_from_the_first_of_last_years_month(self, meteo):
        # Months are stored on the 1st. A mid-month lower bound dropped last year's first
        # month, so the platform's own record could never be comparable for a weeks window.
        s = FakeSession(_weather_route())
        await src.degree_days(s, building_id="b1", start=date(2026, 8, 3), end=date(2026, 9, 28),
                              today=date(2026, 9, 28))
        p = next(pp for q, pp in zip(s.sql, s.params) if "weather_degree_days" in q)
        assert p["ly_start"] == date(2025, 8, 1)

    async def test_the_platform_record_over_a_mid_month_window_is_comparable(self, meteo):
        table = [{"month": date(y, m, 1), "hdd": 40.0 + m, "cdd": 5.0, "base_temp_c": 15.5,
                  "station": "London City", "source": "degreedays.net"}
                 for y, m in [(2025, 8), (2025, 9), (2026, 8), (2026, 9)]]
        out = await src.degree_days(FakeSession(_weather_route(table=table)), building_id="b1",
                                    start=date(2026, 8, 3), end=date(2026, 9, 28),
                                    today=date(2026, 9, 28))
        assert out["source"] == "weather_degree_days" and out["total"]["comparable"] is True

    async def test_the_read_transaction_ends_before_the_weather_service_is_called(self, meteo):
        # db.py: an await inside a transaction holds its locks. The weather call can take
        # seconds, so the reads before it are committed first — the reports/cards.py rule.
        s = FakeSession(_weather_route())
        seen = []
        orig = open_meteo.TRANSPORT.handle_async_request

        async def spy(request):
            seen.append(list(s.events))
            return await orig(request)

        open_meteo.TRANSPORT.handle_async_request = spy
        await src.degree_days(s, building_id="b1", start=date(2026, 9, 1), end=date(2026, 9, 28),
                              today=date(2026, 9, 28))
        assert seen, "the weather service was not called"
        assert seen[0][-1] == "commit", seen[0]

    async def test_a_provider_failure_is_remembered_briefly_so_a_retry_does_not_wait_again(self, meteo):
        meteo["state"]["fail"] = "down"
        args = dict(building_id="b1", start=date(2026, 9, 1), end=date(2026, 9, 28), today=date(2026, 9, 28))
        await src.degree_days(FakeSession(_weather_route()), **args)
        n = len(meteo["asked"])
        out = await src.degree_days(FakeSession(_weather_route()), **args)
        assert len(meteo["asked"]) == n and out["status"] == "unreadable"

    async def test_a_slow_provider_is_unreadable_within_the_budget(self, meteo, monkeypatch):
        import asyncio

        async def slow(request):
            await asyncio.sleep(1.0)
            return httpx.Response(200, json={})

        monkeypatch.setattr(open_meteo, "TRANSPORT", httpx.MockTransport(slow))
        monkeypatch.setattr(src, "WEATHER_BUDGET_S", 0.2)
        out = await src.degree_days(FakeSession(_weather_route()), building_id="b1",
                                    start=date(2026, 9, 1), end=date(2026, 9, 28), today=date(2026, 9, 28))
        assert out["status"] == "unreadable" and "did not answer within" in out["detail"]

    def test_the_cache_is_bounded(self, monkeypatch):
        open_meteo.clear_cache()
        monkeypatch.setattr(open_meteo, "CACHE_MAX", 5)
        for i in range(20):
            open_meteo._remember(("k", i), i)
        assert len(open_meteo._CACHE) <= 5
        assert open_meteo._cached(("k", 19)) == 19
        open_meteo.clear_cache()

    def test_country_names_resolve_to_iso_for_the_geocoder(self):
        assert open_meteo.iso_country("UK") == "GB"
        assert open_meteo.iso_country("United Kingdom") == "GB"
        assert open_meteo.iso_country("England") == "GB"
        assert open_meteo.iso_country("UAE") == "AE"
        assert open_meteo.iso_country("Dubai") == "AE"
        assert open_meteo.iso_country("Narnia") is None
        assert open_meteo.iso_country(None) is None

    def test_the_api_key_never_reaches_the_request_log(self):
        # httpx logs every request URL at INFO, and the customer endpoint carries the key in
        # its query string.
        import logging
        from src.core.logging import configure_logging
        configure_logging()
        assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING

    async def test_the_newest_readings_are_kept_when_the_window_is_cut(self):
        s = FakeSession(_bms_route([]))
        await src.asset_readings_trend(s, asset_id="a1", since=SINCE, until=UNTIL, bands=BANDS)
        sql = next(q for q in s.sql if "asset_readings" in q)
        assert "ORDER BY recorded_at DESC" in sql


def _flat(mid, fuel, sub, period, start, n, kwh, skip=()):
    return [_meter(mid, fuel, sub, None, period,
                   datetime.combine(start + timedelta(days=i), datetime.min.time(), UTC), kwh)
            for i in range(n) if i not in skip]


LY_D = src.a_year_before(SINCE).date()


class TestTheBillComparesTheSameDays:
    async def test_a_gap_in_last_years_record_is_not_reported_as_a_rise(self):
        # 1,000 kWh a day in both years, with five days missing from last year's feed: the
        # raw totals say +9.8%, the same days say 0%.
        rows = (_flat("e0", "electricity", False, "now", SINCE.date(), 56, 1000.0)
                + _flat("e0", "electricity", False, "last_year", LY_D, 56, 1000.0, skip={10, 11, 12, 13, 14}))
        out = await src.utility_bill(FakeSession(_bill_route(rows)), building_id="b1", since=SINCE, until=UNTIL)
        t = out["total"]
        assert t["comparable"] is True and t["change_pct"] == 0.0
        assert t["compared_days"] == 51

    async def test_a_gap_in_this_years_feed_is_not_reported_as_a_fall(self):
        rows = (_flat("e0", "electricity", False, "now", SINCE.date(), 56, 1000.0, skip=set(range(21)))
                + _flat("e0", "electricity", False, "last_year", LY_D, 56, 1000.0))
        out = await src.utility_bill(FakeSession(_bill_route(rows)), building_id="b1", since=SINCE, until=UNTIL)
        assert out["total"]["comparable"] is False and out["total"]["change_pct"] is None

    async def test_a_fuel_metered_only_this_year_does_not_inflate_the_total(self):
        rows = (_flat("e0", "electricity", False, "now", SINCE.date(), 56, 100.0)
                + _flat("e0", "electricity", False, "last_year", LY_D, 56, 100.0)
                + _flat("g1", "gas", False, "now", SINCE.date(), 56, 50.0))
        out = await src.utility_bill(FakeSession(_bill_route(rows)), building_id="b1", since=SINCE, until=UNTIL)
        assert out["fuels"]["electricity"]["change_pct"] == 0.0
        assert out["fuels"]["gas"]["comparable"] is False
        assert out["total"]["change_pct"] is None, "the pooled total mixed in a fuel with no history"

    async def test_a_supply_meter_reading_zero_is_still_the_supply(self):
        rows = (_flat("g1", "gas", False, "now", SINCE.date(), 56, 0.0)
                + _flat("g1", "gas", False, "last_year", LY_D, 56, 30.0)
                + _flat("g-b", "gas", True, "now", SINCE.date(), 56, 5.0))
        out = await src.utility_bill(FakeSession(_bill_route(rows)), building_id="b1", since=SINCE, until=UNTIL)
        gas = out["fuels"]["gas"]
        assert gas["meters_basis"] == "supply" and gas["change_pct"] == -100.0

    async def test_a_supply_meter_with_no_reading_this_window_says_so(self):
        rows = (_flat("g1", "gas", False, "last_year", LY_D, 56, 30.0)
                + _flat("g-b", "gas", True, "now", SINCE.date(), 56, 5.0))
        out = await src.utility_bill(FakeSession(_bill_route(rows)), building_id="b1", since=SINCE, until=UNTIL)
        assert out["fuels"]["gas"]["meters_basis"] == "sub-meters (the supply meter has no reading this window)"


async def test_the_bill_counts_the_meters_every_other_building_read_counts():
    # meter_scope.counted_meters is the one predicate for which meters a building's kWh is
    # summed from, so the bill line agrees with the building's EUI and its cost card.
    from src.engines.energy.meter_scope import counted_meters
    s = FakeSession()
    await src.utility_bill(s, building_id="b1", since=SINCE, until=UNTIL)
    pred = " ".join(counted_meters("m").split())
    assert all(pred in " ".join(q.split()) for q in s.sql if "meter_readings" in q), s.sql
