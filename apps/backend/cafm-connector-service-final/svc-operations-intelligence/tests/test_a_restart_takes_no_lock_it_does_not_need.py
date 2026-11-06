"""A restart of a migrated database must not queue behind anybody's reads.

6 Nov 2026: rolling a new revision left the identity service on 502 for a quarter of an hour.
Its start re-ran every migration file, and two statements took an exclusive lock on
plenum_cafm.users to change nothing -- one dropped a constraint dropped in September, one re-set
a default that was already set. Somebody else's fourteen-minute query held users, so the start
waited; and a waiting exclusive lock queues every token check behind it. Measured afterwards
against a copy of hoistra_test's own schema, every start took 305 such locks.

These tests reproduce the incident and generalise it: a second session holds a lock on every
table and view in plenum_cafm and plenum_scoped, the way a long query would, and a restart must
complete without waiting on any of them.

They need a real Postgres holding a migrated schema -- a clone of hoistra_test's structure works
(pg_dump --schema-only into a local database). Set OPS_TEST_DB_URL to it; they are skipped
otherwise. They WRITE to that database (the ledger, the views, a scratch table), so the URL
must be local: they refuse anything else, and never point it at an Azure host.
"""
from __future__ import annotations

import os
import time
from urllib.parse import urlparse

import pytest

URL = os.environ.get("OPS_TEST_DB_URL", "")

pytestmark = pytest.mark.skipif(not URL, reason="OPS_TEST_DB_URL is not set")


def _local_only(url: str) -> None:
    host = (urlparse(url.replace("+asyncpg", "")).hostname or "").lower()
    if host not in ("localhost", "127.0.0.1", "::1", "host.docker.internal"):
        pytest.fail(f"OPS_TEST_DB_URL must be a local database; refusing {host!r}")


@pytest.fixture
async def ops(monkeypatch):
    """src.db, pointed at the test database, with a short lock timeout so a statement that
    WOULD have waited fails in about a second instead of four retries of five."""
    _local_only(URL)
    from sqlalchemy.ext.asyncio import create_async_engine

    from src import db

    eng = create_async_engine(URL, pool_size=5, connect_args={
        "server_settings": {"application_name": "svc-operations-intelligence-test"}})
    monkeypatch.setattr(db, "engine", eng)
    monkeypatch.setattr(db, "_DDL_LOCK_TIMEOUT", "300ms")
    monkeypatch.setattr(db, "_DDL_LOCK_RETRY_SLEEP_S", 0.05)
    monkeypatch.setattr(db.settings, "migrations_ledger", True)
    monkeypatch.setattr(db.settings, "auto_migrate_on_startup", True)
    monkeypatch.setattr(db.settings, "migrations_allowed_to_fail", "")
    # The first start on a database with no ledger yet is deploy day: every file runs once.
    # Done here so every test starts from a database the ledger knows.
    await db.init_db()
    yield db
    await eng.dispose()


class Holder:
    """A second session holding a lock on relations, the way a long query does."""

    def __init__(self, mode: str, relations: list[str]):
        self.mode, self.relations, self.conn = mode, relations, None

    async def __aenter__(self):
        import asyncpg
        self.conn = await asyncpg.connect(URL.replace("postgresql+asyncpg://", "postgresql://"))
        await self.conn.execute("BEGIN")
        for i in range(0, len(self.relations), 100):
            await self.conn.execute(
                f"LOCK TABLE {', '.join(self.relations[i:i + 100])} IN {self.mode} MODE")
        return self

    async def __aexit__(self, *exc):
        await self.conn.execute("ROLLBACK")
        await self.conn.close()


async def _everything(db) -> list[str]:
    from sqlalchemy import text
    async with db.engine.connect() as conn:
        rows = await conn.execute(text(
            "SELECT format('%I.%I', n.nspname, c.relname) FROM pg_class c"
            "  JOIN pg_namespace n ON n.oid = c.relnamespace"
            " WHERE n.nspname IN ('plenum_cafm', 'plenum_scoped') AND c.relkind IN ('r','p','v')"
            "   AND c.relname <> 'ops_schema_migrations'"
            " ORDER BY 1"))
        return [r[0] for r in rows]


def _record(monkeypatch, db) -> list[str]:
    """Every statement the runner hands to Postgres."""
    ran: list[str] = []
    real = db._run_ddl_statement

    async def spy(stmt):
        ran.append(stmt)
        return await real(stmt)

    monkeypatch.setattr(db, "_run_ddl_statement", spy)
    return ran


# -- the incident -----------------------------------------------------------------------------------

async def test_a_restart_does_not_wait_on_a_reader_of_every_table(ops, monkeypatch):
    """ROW EXCLUSIVE is what a writer holds, and it conflicts with every lock the old start took:
    the exclusive locks of ALTER and CREATE OR REPLACE VIEW, and the share lock of a plain
    CREATE INDEX. A restart that runs none of those passes straight through."""
    ran = _record(monkeypatch, ops)
    async with Holder("ROW EXCLUSIVE", await _everything(ops)):
        t0 = time.monotonic()
        await ops.init_db()            # raises MigrationError if any statement waited it out
        elapsed = time.monotonic() - t0
    assert elapsed < 15, f"a restart with nothing to apply took {elapsed:.1f}s"
    every_start = {f.name for f in ops._MIGRATIONS_DIR.glob("*.sql")
                   if ops._EVERY_START.search(f.read_text(encoding="utf-8"))}
    allowed = {" ".join(s.split()) for f in ops._MIGRATIONS_DIR.glob("*.sql")
               if f.name in every_start
               for s in ops._split_sql(f.read_text(encoding="utf-8"))}
    allowed.add(" ".join(ops._LEDGER_DDL.split()))
    strays = [" ".join(s.split())[:100] for s in ran if " ".join(s.split()) not in allowed]
    assert not strays, f"ran statements from files the ledger says are applied: {strays}"


async def test_the_users_lock_of_6_nov_no_longer_stops_the_auth_migrations(ops):
    """The exact shape of the incident: a long SELECT holding users. The two auth files must run
    in full under it -- even with the ledger OFF, so even on deploy day -- because their
    statements now ask the catalogue before they ALTER."""
    files = [ops._MIGRATIONS_DIR / "auth_platform_role.sql",
             ops._MIGRATIONS_DIR / "auth_identity_and_otp.sql"]
    async with Holder("ACCESS SHARE", ["plenum_cafm.users"]):
        failed = await ops.apply_sql_migrations(only=files, use_ledger=False)
    assert failed == {}, failed


# -- and that the holder is a real test, not a pass for nothing ------------------------------------

async def test_the_old_statement_does_wait_on_that_reader(ops):
    """Without this the test above could pass because the holder holds nothing. The statement as
    it stood until today, under the same lock, gives up with a lock timeout."""
    async with Holder("ACCESS SHARE", ["plenum_cafm.users"]):
        with pytest.raises(Exception) as caught:
            await ops._run_ddl_statement(
                "ALTER TABLE plenum_cafm.users DROP CONSTRAINT IF EXISTS ck_users_role")
    assert ops._is_lock_timeout(caught.value), caught.value


async def test_with_the_ledger_off_the_same_reader_stops_a_start(ops):
    """And the ledger is what makes the difference: run the files regardless, under the same
    every-table lock, and the ones that take a lock they do not need fail. vendor_buildings.sql
    builds its index without CONCURRENTLY, which takes a share lock even when it exists."""
    files = [ops._MIGRATIONS_DIR / "vendor_buildings.sql"]
    async with Holder("ROW EXCLUSIVE", await _everything(ops)):
        failed = await ops.apply_sql_migrations(only=files, use_ledger=False)
    assert files[0] in failed and ops._is_lock_timeout(Exception(failed[files[0]]))


# -- what still has to run every start, still does -------------------------------------------------

async def test_a_table_added_since_the_last_start_gets_its_scoped_view(ops):
    """udr_scoped_views.sql is how a new table becomes readable through plenum_scoped. It runs
    every start and skips views that are current -- a new table has none, so it is built."""
    from sqlalchemy import text
    async with ops.engine.begin() as conn:
        await conn.execute(text("DROP VIEW IF EXISTS plenum_scoped.zz_ledger_probe"))
        await conn.execute(text("DROP TABLE IF EXISTS plenum_cafm.zz_ledger_probe"))
        await conn.execute(text(
            "CREATE TABLE plenum_cafm.zz_ledger_probe (id uuid, organization_id uuid)"))
    try:
        await ops.init_db()
        async with ops.engine.connect() as conn:
            comment = (await conn.execute(text(
                "SELECT obj_description(to_regclass('plenum_scoped.zz_ledger_probe'), 'pg_class')"
            ))).scalar()
        assert comment and comment.startswith("udr_scoped "), "no scoped view for the new table"
    finally:
        async with ops.engine.begin() as conn:
            await conn.execute(text("DROP VIEW IF EXISTS plenum_scoped.zz_ledger_probe"))
            await conn.execute(text("DROP TABLE IF EXISTS plenum_cafm.zz_ledger_probe"))


async def test_a_table_that_gained_a_column_gets_its_view_rebuilt(ops):
    """`SELECT t.*` freezes the column list when the view is made, so a view that skipped a
    rebuild after its table grew would silently hide the new column."""
    from sqlalchemy import text
    async with ops.engine.begin() as conn:
        await conn.execute(text("DROP VIEW IF EXISTS plenum_scoped.zz_ledger_probe"))
        await conn.execute(text("DROP TABLE IF EXISTS plenum_cafm.zz_ledger_probe"))
        await conn.execute(text("CREATE TABLE plenum_cafm.zz_ledger_probe (id uuid)"))
    try:
        await ops.init_db()
        async with ops.engine.begin() as conn:
            await conn.execute(text("ALTER TABLE plenum_cafm.zz_ledger_probe ADD COLUMN note text"))
        await ops.init_db()
        async with ops.engine.connect() as conn:
            cols = (await conn.execute(text(
                "SELECT string_agg(column_name, ',' ORDER BY ordinal_position)"
                "  FROM information_schema.columns"
                " WHERE table_schema = 'plenum_scoped' AND table_name = 'zz_ledger_probe'"))).scalar()
        assert cols == "id,note", cols
    finally:
        async with ops.engine.begin() as conn:
            await conn.execute(text("DROP VIEW IF EXISTS plenum_scoped.zz_ledger_probe"))
            await conn.execute(text("DROP TABLE IF EXISTS plenum_cafm.zz_ledger_probe"))


# -- the status, as the console reads it ------------------------------------------------------------

async def test_status_has_every_file_and_only_the_every_start_ones_run_next(ops):
    status = await ops.migration_status()
    files = sorted(f.name for f in ops._MIGRATIONS_DIR.glob("*.sql"))
    listed = sorted(i["file"] for i in status["files"] if i["status"] != "removed")
    assert listed == files
    assert sorted(status["will_run_next_start"]) == ["phase3_stamp_single_org.sql",
                                                     "udr_scoped_views.sql"]
    assert status["counts"].get("pending", 0) == 0
    assert status["counts"].get("failed", 0) == 0


async def test_a_failing_file_is_recorded_with_its_error_and_runs_again(ops, monkeypatch, tmp_path):
    from sqlalchemy import text
    bad = tmp_path / "zz_ledger_probe.sql"
    bad.write_text("SELECT 1 / 0;\n", encoding="utf-8")
    monkeypatch.setattr(ops, "_MIGRATIONS_DIR", tmp_path)
    try:
        failed = await ops.apply_sql_migrations()
        assert bad in failed
        row = {i["file"]: i for i in (await ops.migration_status())["files"]}["zz_ledger_probe.sql"]
        assert row["status"] == "failed" and "division by zero" in (row["last_error"] or "")

        bad.write_text("SELECT 1;\n", encoding="utf-8")       # fixed
        assert await ops.apply_sql_migrations() == {}
        row = {i["file"]: i for i in (await ops.migration_status())["files"]}["zz_ledger_probe.sql"]
        assert row["status"] == "applied" and row["last_error"] is None

        ran = _record(monkeypatch, ops)
        await ops.apply_sql_migrations()
        assert not [s for s in ran if "SELECT 1" in s], "an applied file ran again"
    finally:
        async with ops.engine.begin() as conn:
            await conn.execute(text("DELETE FROM plenum_cafm.ops_schema_migrations"
                                    " WHERE filename = 'zz_ledger_probe.sql'"))
