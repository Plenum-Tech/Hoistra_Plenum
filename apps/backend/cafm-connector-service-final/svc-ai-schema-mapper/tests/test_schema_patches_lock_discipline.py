"""Startup schema patches take no lock they do not need, and never wait long for one they do.

Measured 17 Sep 2026: an outside session idle in a transaction held a share lock; every replica
start ran an `ADD COLUMN IF NOT EXISTS` that needed an exclusive lock and waited, and each
waiting ALTER blocked every reader of its table behind it — 47 sessions within forty minutes.
"""
import pytest

from src import schema_patches as sp


class _Result:
    def __init__(self, value): self._v = value
    def scalar(self): return self._v


class _Conn:
    def __init__(self, engine): self.e = engine
    async def execute(self, clause, params=None):
        sql = str(clause)
        self.e.executed.append(sql)
        if "information_schema.columns" in sql:
            return _Result(1 if self.e.column_present else None)
        if sql.startswith("SET LOCAL"):
            return _Result(None)
        self.e.alters += 1
        exc = self.e.errors.pop(0) if self.e.errors else None
        if exc:
            raise exc
        return _Result(None)
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False


class _Engine:
    def __init__(self, *, column_present=False, errors=()):
        self.column_present = column_present
        self.errors = list(errors)
        self.executed: list[str] = []
        self.alters = 0
    def connect(self): return _Conn(self)
    def begin(self): return _Conn(self)


async def _no_sleep(_s): return None


DDL = sp.PATCHES[0][1]
LOCK = Exception("canceling statement due to lock timeout")


def test_every_patch_is_a_plain_add_column_statement_and_so_pre_checkable():
    """What makes a patch lock-free on a steady database is that information_schema can be asked
    first, which only works for a plain ADD COLUMN. Patches started out on migration_jobs only;
    since 6 Nov the started_by_user_id columns are on the other run tables too, named
    "<table>.<column>"."""
    for name, ddl in sp.PATCHES:
        parsed = sp.parse_add_column(ddl)
        assert parsed is not None, f"{name} is not a plain ADD COLUMN, so it cannot be pre-checked"
        schema, table, column = parsed
        assert schema == "plenum_cafm"
        assert name.split(".")[-1] == column, (name, column)
        assert "." not in name or name.split(".")[0] == table, (name, table)


async def test_an_existing_column_takes_no_lock():
    e = _Engine(column_present=True)
    assert await sp.apply_patch(e, "field_mapping_draft", DDL) == "already_applied"
    assert e.alters == 0


async def test_a_missing_column_is_added_in_its_own_transaction_under_a_lock_timeout():
    e = _Engine(column_present=False)
    assert await sp.apply_patch(e, "field_mapping_draft", DDL) == "applied"
    assert e.alters == 1
    assert any(s.startswith("SET LOCAL lock_timeout = '5s'") for s in e.executed)


async def test_a_lock_timeout_backs_off_and_retries_then_succeeds():
    e = _Engine(errors=[LOCK, LOCK])
    slept = []
    async def sleep(s): slept.append(s)
    assert await sp.apply_patch(e, "x", DDL, sleep=sleep) == "applied"
    assert e.alters == 3 and slept == [sp.LOCK_RETRY_SLEEP_S] * 2


async def test_a_lock_that_never_comes_is_skipped_not_waited_for():
    e = _Engine(errors=[LOCK] * sp.LOCK_RETRIES)
    assert await sp.apply_patch(e, "x", DDL, sleep=_no_sleep) == "lock_skipped"
    assert e.alters == sp.LOCK_RETRIES


async def test_any_other_error_is_skipped_without_retrying():
    e = _Engine(errors=[Exception('relation "nope" does not exist')])
    assert await sp.apply_patch(e, "x", DDL, sleep=_no_sleep) == "failed"
    assert e.alters == 1


async def test_the_entry_point_runs_every_patch_against_the_engine(monkeypatch):
    e = _Engine(column_present=True)
    monkeypatch.setattr(sp, "get_async_engine", lambda: e)
    # The cancel trigger has its own existence check and its own tests (test_migration_cancel);
    # here it only has to be run, against the same engine, after the column patches.
    # The activity stamp likewise (test_a_dead_run_says_it_was_interrupted, against real
    # Postgres) -- but it must be run, or the interrupted-run check has nothing to read.
    trigger_runs = []
    stamp_runs = []

    async def _trigger(engine, **_k):
        trigger_runs.append(engine)
        return "already_applied"

    async def _stamp(engine, **_k):
        stamp_runs.append(engine)
        return "already_applied"
    monkeypatch.setattr(sp, "ensure_cancel_is_final", _trigger)
    monkeypatch.setattr(sp, "ensure_activity_is_stamped", _stamp)
    await sp.ensure_migration_jobs_schema_patches()
    assert e.alters == 0
    assert sum("information_schema" in s for s in e.executed) == len(sp.PATCHES)
    assert trigger_runs == [e]
    assert stamp_runs == [e]
