"""A year of readings goes in batches, and a dead connection stops the run.

Two faults met on a 35,040-row ingest on 22 Sep 2026.

Row at a time is three round trips each — SAVEPOINT, INSERT, RELEASE — so two meters with a
year of half hours is over a hundred thousand round trips to a database across a WAN. Azure
closed the connection part way through. That is the first line in the log: "connection was
closed in the middle of operation" on a RELEASE SAVEPOINT.

And once it closed, the loop carried on. A dead connection is not a bad row, but it was
handled as one, so every remaining row logged "Can't reconnect until invalid transaction is
rolled back" and was counted as skipped. Seventeen thousand warnings for one failure, and a
run that blamed the file for the database going away.

Imported as a package here rather than by path, as its siblings are: write_node carries the
service's own relative imports, which a path load cannot satisfy. cafm_shared is installed in
the container and not on a developer's machine, so its logger is stubbed rather than made a
condition of running these tests.
"""
from __future__ import annotations

import logging
import sys
import types

import pytest

if "cafm_shared" not in sys.modules:
    _shared = types.ModuleType("cafm_shared")
    _logging = types.ModuleType("cafm_shared.logging")
    _logging.get_logger = lambda name=None: logging.getLogger(name or "test")
    _shared.logging = _logging
    sys.modules["cafm_shared"] = _shared
    sys.modules["cafm_shared.logging"] = _logging

from src.graph.nodes import write_node as wn  # noqa: E402


class _Result:
    def __init__(self, rowcount):
        self.rowcount = rowcount


class _Savepoint:
    def __init__(self, session):
        self._s = session

    async def __aenter__(self):
        self._s.savepoints += 1
        return self

    async def __aexit__(self, *exc):
        return False


class _Session:
    """Counts round trips, and fails whichever statements it is told to."""

    def __init__(self, fail_on=None, raise_after=None, error=None):
        self.statements = []          # one entry per execute() call
        self.savepoints = 0
        self.fail_on = fail_on or (lambda params: False)
        self.raise_after = raise_after
        self.error = error or Exception("bad row")
        self.calls = 0

    def begin_nested(self):
        return _Savepoint(self)

    async def execute(self, _stmt, params=None):
        self.calls += 1
        self.statements.append(params)
        if self.raise_after is not None and self.calls > self.raise_after:
            raise self.error
        if isinstance(params, list):
            if any(self.fail_on(p) for p in params):
                raise Exception("batch contains a bad row")
            return _Result(len(params))
        if self.fail_on(params):
            raise self.error
        return _Result(1)


def rows(n, table="meter_readings"):
    out = []
    for i in range(n):
        filtered = {"meter_id": "m-1", "reading_at": f"t{i}", "consumption_kwh": i}
        sql = f"INSERT INTO plenum_cafm.{table} (meter_id, reading_at, consumption_kwh) VALUES (:meter_id, :reading_at, :consumption_kwh) ON CONFLICT DO NOTHING"
        out.append((filtered, sql, dict(filtered)))
    return out


async def insert(session, pending, nullable=frozenset()):
    return await wn._insert_rows(
        session, schema_name="plenum_cafm", table_name="meter_readings",
        pending=pending, unique_sets=set(), nullable_cols=set(nullable),
    )


class TestBatching:
    @pytest.mark.asyncio
    async def test_five_hundred_rows_are_one_statement(self):
        s = _Session()
        out = await insert(s, rows(500))
        assert out["inserted"] == 500
        assert s.calls == 1, f"{s.calls} round trips for one batch"

    @pytest.mark.asyncio
    async def test_a_year_of_half_hours_is_not_a_hundred_thousand_round_trips(self):
        """17,520 rows went as 52,560 round trips. That is what the connection could not
        survive."""
        s = _Session()
        await insert(s, rows(17_520))
        assert s.calls == 1
        assert s.savepoints == 1

    @pytest.mark.asyncio
    async def test_rows_with_different_columns_are_grouped_by_statement(self):
        mixed = rows(3)
        extra = ({"meter_id": "m-1", "reading_at": "t9"},
                 "INSERT INTO plenum_cafm.meter_readings (meter_id, reading_at) VALUES (:meter_id, :reading_at)",
                 {"meter_id": "m-1", "reading_at": "t9"})
        s = _Session()
        out = await insert(s, mixed + [extra])
        assert s.calls == 2, "one statement per column set"
        assert out["inserted"] == 4

    @pytest.mark.asyncio
    async def test_the_chunk_size_is_set_where_it_can_be_found(self):
        assert isinstance(wn._WRITE_CHUNK, int) and wn._WRITE_CHUNK >= 100


class TestOneBadRow:
    @pytest.mark.asyncio
    async def test_a_bad_row_costs_its_own_row_and_not_the_batch(self):
        bad = lambda p: p.get("reading_at") == "t7"
        s = _Session(fail_on=bad)
        out = await insert(s, rows(20))
        assert out["skipped"] == 1
        assert out["inserted"] == 19, "the other nineteen still went in"

    @pytest.mark.asyncio
    async def test_the_failure_is_reported_once_with_the_table_named(self):
        s = _Session(fail_on=lambda p: p.get("reading_at") == "t2")
        out = await insert(s, rows(5))
        assert len(out["errors"]) == 1
        assert out["errors"][0].startswith("meter_readings:")

    @pytest.mark.asyncio
    async def test_at_most_twenty_errors_are_kept(self):
        """A run that fails every row must not return a hundred thousand strings."""
        s = _Session(fail_on=lambda p: True)
        out = await insert(s, rows(60))
        assert out["skipped"] == 60
        assert len(out["errors"]) == 20


class TestADeadConnection:
    @pytest.mark.parametrize("message", [
        "connection was closed in the middle of operation",
        "Can't reconnect until invalid transaction is rolled back",
        "server closed the connection unexpectedly",
        "terminating connection due to administrator command",
    ])
    def test_it_is_recognised_however_it_is_worded(self, message):
        assert wn._is_connection_lost(Exception(message)) is True

    def test_an_ordinary_data_error_is_not_mistaken_for_one(self):
        for message in ("null value in column violates not-null constraint",
                        "invalid input syntax for type uuid",
                        "duplicate key value violates unique constraint"):
            assert wn._is_connection_lost(Exception(message)) is False, message

    @pytest.mark.asyncio
    async def test_it_stops_the_run_instead_of_repeating_itself(self):
        """The whole bug. Seventeen thousand warnings, each describing the same one event."""
        s = _Session(raise_after=0,
                     error=Exception("connection was closed in the middle of operation"))
        with pytest.raises(wn.ConnectionLost):
            await insert(s, rows(17_520))
        assert s.calls <= 2, f"{s.calls} attempts after the connection had gone"

    @pytest.mark.asyncio
    async def test_a_connection_lost_during_the_row_by_row_retry_also_stops(self):
        """The batch fails on a bad row, the retry starts, and the connection dies mid-retry."""
        calls = {"n": 0}

        class _S(_Session):
            async def execute(self, _stmt, params=None):
                calls["n"] += 1
                if calls["n"] == 1:
                    raise Exception("duplicate key value violates unique constraint")
                raise Exception("connection was closed in the middle of operation")

        with pytest.raises(wn.ConnectionLost):
            await insert(_S(), rows(100))

    @pytest.mark.asyncio
    async def test_nothing_is_reported_as_skipped_when_the_database_went_away(self):
        """Skipped rows mean the data was wrong. Saying that here sends the reader to look at
        their file, which is the one place the answer is not."""
        s = _Session(raise_after=0, error=Exception("connection was closed"))
        try:
            await insert(s, rows(10))
        except wn.ConnectionLost:
            pass
        else:
            raise AssertionError("should have raised")
