"""A building's meter summary can be scoped to one calendar month (24 Nov 2026).

Without it, every per-meter total was all-time, so "total main electricity at Aire Mill Court
in October 2026" could only be answered with a cumulative figure over the whole range — the
chat said the October figure "is not available", though the readings were there.

`month` ('YYYY-MM') becomes the half-open range [first of the month, first of the next), applied
inside the LEFT JOIN so a meter with no reading that month still lists (with 0), and the window
is returned so a reader is never handed a cumulative total as a month's answer.
"""
from __future__ import annotations

from uuid import uuid4

import pytest

from src.engines.energy import meters as meters_mod


@pytest.mark.parametrize("month,expected", [
    ("2026-10", ("2026-10-01", "2026-11-01")),
    ("2026-12", ("2026-12-01", "2027-01-01")),   # year rolls over
    ("2026-01", ("2026-01-01", "2026-02-01")),
    ("2026-2", ("2026-02-01", "2026-03-01")),    # single-digit month
])
def test_a_month_becomes_a_half_open_range(month, expected):
    assert meters_mod._month_window(month) == expected


@pytest.mark.parametrize("bad", [None, "", "2026", "2026-13", "2026-00", "last month", "2026-10-05"])
def test_an_unparseable_month_is_no_window(bad):
    assert meters_mod._month_window(bad) is None


class _Res:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return list(self._rows)


class _CaptureSession:
    """Records the SQL and params of each execute; returns no rows."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def execute(self, stmt, params=None):
        self.calls.append((" ".join(str(stmt).split()), dict(params or {})))
        return _Res([])

    def begin_nested(self):
        sess = self

        class _Ctx:
            async def __aenter__(self):
                return sess

            async def __aexit__(self, *a):
                return False

        return _Ctx()


@pytest.mark.asyncio
async def test_a_month_scopes_the_reading_join_and_is_returned():
    s = _CaptureSession()
    bid = uuid4()
    out = await meters_mod.building_meter_summary(s, building_id=bid, month="2026-10")
    assert out["window"] == {"month": "2026-10", "from": "2026-10-01", "to": "2026-11-01"}
    meters_call = next((sql, p) for sql, p in s.calls if "FROM plenum_cafm.energy_meters em" in sql)
    sql, p = meters_call
    # The window is on the JOIN, not a WHERE, so an empty meter still lists.
    assert "LEFT JOIN plenum_cafm.meter_readings r ON r.meter_id = em.id AND r.reading_at >=" in sql
    assert "r.reading_at < CAST(:w_to AS date)" in sql
    assert p["w_from"] == "2026-10-01" and p["w_to"] == "2026-11-01"


@pytest.mark.asyncio
async def test_no_month_is_all_time_and_adds_no_window_clause():
    s = _CaptureSession()
    out = await meters_mod.building_meter_summary(s, building_id=uuid4())
    assert out["window"] is None
    sql = next(sql for sql, _ in s.calls if "FROM plenum_cafm.energy_meters em" in sql)
    # No window clause on the JOIN (reading_at still appears in the SELECT aggregates).
    assert "ON r.meter_id = em.id WHERE" in sql and "CAST(:w_to AS date)" not in sql
    assert all("w_from" not in p for _, p in s.calls)
