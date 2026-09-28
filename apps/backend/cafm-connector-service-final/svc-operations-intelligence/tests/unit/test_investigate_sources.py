"""The walk itself: each source reads what it says it reads, and says so when it cannot.

Measured on hoistra_test on 28 Sep 2026, Boiler 1 at Bishopsgate Tower read back three lines
that were each wrong in a different way:

- weather said "no degree days" on every asset, on every database. Its query never ran:
  ``:since_m::date`` is not a bind to SQLAlchemy's text() parser, Postgres got a literal ``:``
  and rejected it, and the savepoint swallowed the error into an empty result.
- utility_bill said +48687%. It summed the incoming supply AND the sub-meters beneath it, and
  set eight weeks of them against the same weeks last year, when only the two supply meters
  existed and only twelve hours of that window were on record at all.
- bms_trend said "no readings" for a boiler with 96 banded BMS readings on file, because it
  only ever read the chiller performance table.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

from sqlalchemy.dialects import postgresql

from src.engines.energy import investigate as inv
from src.engines.energy.investigate import _evidence

ASSET = {"asset_id": "a1", "asset_name": "Boiler 1 — central plant",
         "building_id": "b1", "building": "Bishopsgate Tower", "readings": []}
SINCE = datetime(2026, 8, 3, 12, tzinfo=timezone.utc)

#: A ``:name`` the compiler left in the SQL is a bind it did not recognise.
UNBOUND = re.compile(r"(?<![:\w]):[A-Za-z_]\w*")


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
    """Compiles every statement for Postgres, records it, and answers from ``route``."""

    def __init__(self, route=None):
        self.route = route or (lambda sql, params: [])
        self.sql: list[str] = []

    def begin_nested(self):
        return _Nested()

    async def commit(self):
        return None

    async def execute(self, clause, params=None):
        compiled = str(clause.compile(dialect=postgresql.dialect()))
        self.sql.append(compiled)
        out = self.route(compiled, params or {})
        if isinstance(out, Exception):
            raise out
        return _Result(out)


WALKERS = (inv._walk_efficiency, inv._walk_meter, inv._walk_bill, inv._walk_weather,
           inv._walk_work_orders, inv._walk_documents)


class TestEveryQueryReachesPostgresWhole:
    async def test_no_walker_leaves_a_bind_the_compiler_did_not_recognise(self):
        s = FakeSession()
        for w in WALKERS:
            await w(s, ASSET, SINCE)
        assert s.sql, "the walk ran no query"
        for sql in s.sql:
            left = UNBOUND.findall(sql)
            assert not left, f"unbound {left} would reach Postgres as a syntax error:\n{sql}"


class TestAFailedSourceSaysItFailed:
    async def test_a_query_that_errors_is_unreadable_not_absent(self):
        s = FakeSession(lambda sql, p: RuntimeError("relation does not exist"))
        for w in WALKERS:
            out = await w(s, ASSET, SINCE)
            assert out["status"] == "unreadable", (out["source"], out["status"])
            assert out["badge"] == "could not be read"

    async def test_an_unreadable_document_register_is_not_a_missing_record(self):
        # A failed read must never become the full-confidence "nothing is filed" finding.
        s = FakeSession(lambda sql, p: RuntimeError("boom") if "asset_documents" in sql else [])
        walk = {w["source"]: w for w in [await x(s, ASSET, SINCE) for x in WALKERS]}
        walk["work_order"] = {"source": "work_order", "status": "partial",
                              "closed_without_report": 1, "latest": {"title": "PPM"}}
        kinds = {e["kind"] for e in _evidence(walk, ASSET)}
        assert walk["document"]["status"] == "unreadable"
        assert not any("no service or treatment log" in e["statement"]
                       for e in _evidence(walk, ASSET))
        assert "missing record" in kinds  # the work order's own finding still stands


def _bill_rows(*groups):
    """Per-meter, per-day rows as the utility-bill query returns them."""
    rows = [r for g in groups for r in g]
    return lambda sql, p: rows if ("meter_readings" in sql and "min(r.reading_at)" not in sql) else []


def _meter_days(mid, sub, period, start, n, kwh):
    return [{"meter_id": mid, "meter_ref": mid, "fuel": "electricity", "sub": sub, "tariff": None,
             "period": period,
             "day": datetime.combine(start + timedelta(days=i), datetime.min.time(), timezone.utc),
             "kwh": kwh} for i in range(n)]


LY = inv.sources.a_year_before(SINCE).date()


class TestTheBillComparesLikeWithLike:
    async def test_the_supply_meters_are_counted_once_not_with_the_sub_meters_beneath(self):
        s = FakeSession(_bill_rows(
            _meter_days("E0", False, "now", SINCE.date(), 56, 110.0),
            _meter_days("E0", False, "last_year", LY, 56, 100.0),
            _meter_days("E-B", True, "now", SINCE.date(), 56, 260.0),
            _meter_days("E-B", True, "last_year", LY, 56, 90.0)))
        out = await inv._walk_bill(s, ASSET, SINCE)
        assert out["change_pct"] == 10.0
        assert out["now_kwh"] == 6160 and out["last_year_kwh"] == 5600
        assert out["badge"] == "+10%"

    async def test_a_building_metered_only_below_the_supply_uses_its_sub_meters(self):
        s = FakeSession(_bill_rows(
            _meter_days("E-B", True, "now", SINCE.date(), 56, 55.0),
            _meter_days("E-B", True, "last_year", LY, 56, 50.0)))
        out = await inv._walk_bill(s, ASSET, SINCE)
        assert out["change_pct"] == 10.0

    async def test_twelve_hours_of_last_year_is_not_the_same_period_last_year(self):
        # The Bishopsgate figures: 56 days now against the one day of history that falls in
        # last year's window. That is +48687% on paper and no comparison in fact.
        first = [{"meter_id": "E0", "first_at": datetime(2025, 9, 28, tzinfo=timezone.utc)}]
        rows = (_meter_days("E0", False, "now", SINCE.date(), 56, 26_878.0)
                + _meter_days("E0", False, "last_year", date(2025, 9, 28), 1, 8_383.0))

        def route(sql, p):
            if "min(r.reading_at)" in sql:
                return first
            return rows if "meter_readings" in sql else []

        out = await inv._walk_bill(FakeSession(route), ASSET, SINCE)
        assert out["status"] == "not_found"
        assert out["badge"] == "no comparable period"
        assert "change_pct" not in out
        assert "2025-09-28" in out["detail"]
        assert "1 of 56 days" in out["detail"]
        # And so the evidence cannot carry it as a full-confidence total.
        walk = {"utility_bill": out}
        assert not any(e["kind"] == "corroborating total" for e in _evidence(walk, ASSET))

    async def test_the_question_says_it_is_the_meter_standing_in_for_the_bill(self):
        out = await inv._walk_bill(FakeSession(), ASSET, SINCE)
        assert "meter" in out["question"]
        assert "same month" not in out["question"]


class TestPlantWithoutAChillerRecordReadsItsOwnBms:
    async def test_a_boiler_with_banded_readings_reports_them(self):
        at = datetime(2026, 9, 27, 23, tzinfo=timezone.utc)
        asset = {**ASSET, "bands": {"temperature": {"lo": 5.0, "hi": 95.0, "unit": "°C", "note": None},
                                    "pressure": {"lo": 3.0, "hi": 5.5, "unit": "bar", "note": None}}}
        readings = ([{"reading_type": "temperature", "value": 70.0 + i % 3, "unit": "°C",
                      "recorded_at": at - timedelta(hours=i)} for i in range(48)]
                    + [{"reading_type": "pressure", "value": 6.1 if i == 0 else 4.0, "unit": "bar",
                        "recorded_at": at - timedelta(hours=i)} for i in range(48)])

        def route(sql, p):
            if "asset_readings" in sql:
                return readings
            return [{"points": 0}] if "chiller_performance_readings" in sql else []

        out = await inv._walk_efficiency(FakeSession(route), asset, SINCE)
        assert out["source"] == "bms_trend"
        assert out["status"] == "found"
        assert out["points"] == 96
        assert out["badge"] == "96 points · 1 out of band"
        assert "kW per RT" not in out["question"]
        assert [r["reading_type"] for r in out["out_of_band"]] == ["pressure"]
        assert out["out_of_band"][0]["value"] == 6.1
        # Not a chiller record, so none of the chiller rules can fire off it.
        assert not {"over_design", "kw_per_rt_drift", "condenser_approach"} & set(out)

    async def test_the_window_bound_is_typed_so_a_naive_column_accepts_it(self):
        # hoistra_test holds asset_readings.recorded_at as timestamp WITHOUT time zone.
        # asyncpg types a bare $2 from the column and then refuses the aware `since` — "can't
        # subtract offset-naive and offset-aware datetimes" — so the line read "could not be
        # read" on the first live run. Typing the bound makes the column's type irrelevant.
        s = FakeSession(lambda sql, p: [{"points": 0}] if "chiller_performance_readings" in sql else [])
        await inv._walk_efficiency(s, ASSET, SINCE)
        sql = next(q for q in s.sql if "asset_readings" in q)
        assert re.search(r"recorded_at\s*>=\s*CAST\(\S+ AS timestamptz\)", sql), sql

    async def test_no_record_in_either_table_is_still_no_readings(self):
        route = lambda sql, p: [{"points": 0}] if "readings" in sql else []  # noqa: E731
        out = await inv._walk_efficiency(FakeSession(route), ASSET, SINCE)
        assert out["status"] == "not_found"
        assert out["badge"] == "no readings"


async def test_a_cut_readings_window_says_so_on_the_line():
    at = datetime(2026, 9, 27, 23, tzinfo=timezone.utc)
    rows = [{"reading_type": "temperature", "value": 70.0, "unit": "°C", "recorded_at": at - timedelta(minutes=i)}
            for i in range(5)]

    def route(sql, p):
        if "asset_readings" in sql:
            return rows
        return [{"points": 0}] if "chiller_performance_readings" in sql else []

    from src.engines.energy import asset_sources
    old = asset_sources.MAX_READINGS
    asset_sources.MAX_READINGS = 5
    try:
        out = await inv._walk_efficiency(FakeSession(route), {**ASSET, "bands": {}}, SINCE)
    finally:
        asset_sources.MAX_READINGS = old
    assert out["truncated"] is True
    assert "newest" in out["badge"] and "newest" in out["detail"]
