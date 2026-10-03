"""Which building an MPAN's readings belong to.

A half-hourly CSV for MPAN-B-006-2 was ingested against Riverside Court. All 48 readings
landed, and none of them could be reached from the building: the ingest looked for the MPAN
in energy_meters, did not find it, and created a meter with site_id NULL — while
plenum_cafm.meters had recorded that exact MPAN against Riverside Court all along. There
are two meter registers and the ingest only consulted the one with no building on it.

attribute_energy() already treats a building's own id in site_id as the strongest case, so
the fix is a lookup rather than a new field: ask the register, and put the building it names
on the meter.

No database here. These tests cover what the function decides and, importantly, which
identifiers it is willing to name in SQL — every one of them comes from the literal tuple
in the module, filtered by what information_schema reports, and none from a CSV.
"""
from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

import pytest

from src.engines.energy import meters

BUILDING = "f12e9629-95ed-5722-80cc-a1397f627850"


class _Result:
    def __init__(self, scalar: Any = None, rows: list[Any] | None = None):
        self._scalar, self._rows = scalar, rows or []

    def scalar(self):
        return self._scalar

    def scalars(self):
        return self

    def all(self):
        return self._rows


class FakeSession:
    """Answers the two queries building_for_meter makes, and records them."""

    def __init__(self, columns: list[str], owner: str | None = BUILDING):
        self.columns, self.owner = columns, owner
        self.sql: list[str] = []
        self.params: list[dict] = []

    def begin_nested(self):
        session = self

        class _Ctx:
            async def __aenter__(self):
                return session

            async def __aexit__(self, *exc):
                return False

        return _Ctx()

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        self.sql.append(sql)
        self.params.append(params or {})
        if "information_schema" in sql:
            return _Result(rows=list(self.columns))
        return _Result(scalar=self.owner)


CANONICAL = ["meter_id", "building_id", "mpan_mprn", "meter_type"]


def call(session, **kw):
    return asyncio.run(meters.building_for_meter(session, **kw))


def test_the_register_names_the_building():
    s = FakeSession(CANONICAL)
    assert call(s, mpan="MPAN-B-006-2") == UUID(BUILDING)


def test_an_unregistered_meter_has_no_known_building():
    # Not an error: a meter the portfolio has never recorded genuinely has no building, and
    # the caller creates it and keeps the readings either way.
    s = FakeSession(CANONICAL, owner=None)
    assert call(s, mpan="MPAN-NEW-1") is None


def test_no_identifier_asks_nothing():
    s = FakeSession(CANONICAL)
    assert call(s) is None
    assert call(s, mpan="", mprn="   ") is None
    assert s.sql == []


def test_the_mpan_is_a_bound_parameter_and_never_inlined():
    # The identifier comes off a customer CSV. It reaches SQL as a parameter or not at all.
    s = FakeSession(CANONICAL)
    call(s, mpan="'; DROP TABLE plenum_cafm.meters; --")
    lookup = s.sql[-1]
    assert "DROP TABLE" not in lookup
    assert "'; DROP" not in lookup
    assert "'; DROP TABLE plenum_cafm.meters; --" in s.params[-1].values()


def test_only_columns_information_schema_confirms_are_named():
    # The older shape keys on mpan/mprn; the canonical one on a single mpan_mprn. Naming a
    # column the live table does not have is an error that takes the whole ingest with it.
    s = FakeSession(["meter_id", "building_id", "mpan", "mprn"])
    call(s, mpan="X", mprn="Y")
    lookup = s.sql[-1]
    assert "mpan_mprn" not in lookup
    assert "mpan = " in lookup and "mprn = " in lookup


def test_both_numbers_are_tried_against_every_identifier_column():
    s = FakeSession(CANONICAL)
    call(s, mpan="A", mprn="B")
    assert sorted(v for k, v in s.params[-1].items()) == ["A", "B"]


def test_a_register_without_a_building_column_is_no_answer():
    # Some deployments have not got this table, or have it without the link. Guessing a
    # building from a table that cannot name one would be worse than saying nothing.
    s = FakeSession(["meter_id", "mpan_mprn"])
    assert call(s) is None
    s = FakeSession(["meter_id", "mpan_mprn"])
    assert call(s, mpan="MPAN-B-006-2") is None


def test_a_register_with_no_identifier_column_is_no_answer():
    s = FakeSession(["meter_id", "building_id"])
    assert call(s, mpan="MPAN-B-006-2") is None


def test_a_broken_register_loses_the_link_never_the_readings():
    class Exploding(FakeSession):
        async def execute(self, stmt, params=None):
            raise RuntimeError("relation does not exist")

    assert call(Exploding(CANONICAL), mpan="MPAN-B-006-2") is None


def test_a_non_uuid_in_the_register_is_not_a_building():
    s = FakeSession(CANONICAL, owner="B-006")
    assert call(s, mpan="MPAN-B-006-2") is None


@pytest.mark.parametrize("col", meters._REGISTER_IDENT_COLUMNS + (meters._REGISTER_LINK,))
def test_every_identifier_the_module_may_name_is_a_plain_column_name(col: str):
    # These are interpolated into SQL, so they are a fixed literal list rather than anything
    # derived from a request — this states that and fails if a name arrives that is not one.
    assert col.replace("_", "").isalnum() and col.islower()
