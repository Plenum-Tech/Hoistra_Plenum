"""startup() end to end on a throwaway Postgres: apply mode on the allowed scratch database builds
the schema, the read-only main engine refuses writes, and a second boot in verify mode is ready.
Skips without TRACE_LAB_TEST_DB_URL."""
from __future__ import annotations

import pytest
from sqlalchemy import text

from src import database
from tl_scratch import SCRATCH_URL, new_engine, requires_scratch
from trace_lab import app as app_module
from trace_lab import db, migrate
from trace_lab.settings import Settings

pytestmark = requires_scratch


@pytest.fixture
async def clean_boot(monkeypatch):
    monkeypatch.setattr(database, "_engine", None)
    monkeypatch.setattr(database, "_session_factory", None)
    monkeypatch.setattr(database, "AsyncSessionLocal", None)
    monkeypatch.delenv("TRACE_LAB_APPLY_DATABASES", raising=False)
    db.reset_for_tests()
    engine = new_engine()
    async with engine.begin() as conn:
        await conn.exec_driver_sql(f"DROP SCHEMA IF EXISTS {migrate.SCHEMA} CASCADE")
    await engine.dispose()
    yield
    await app_module.shutdown()
    db.reset_for_tests()


def _settings(mode: str) -> Settings:
    return Settings(main_db_url=SCRATCH_URL, lab_db_url=SCRATCH_URL, schema_mode=mode, enabled=True)


async def test_boot_applies_on_scratch_then_verifies(clean_boot):
    first = await app_module.startup(_settings("apply"))
    assert first.status == "ready" and db.ready()
    async with db.lab_engine().connect() as conn:
        ledger = await migrate.read_ledger(conn)
    assert ledger[0]["applied_by"].startswith("startup:")
    with pytest.raises(Exception, match="read-only transaction"):
        async with db.main_engine().begin() as conn:
            await conn.exec_driver_sql("CREATE TABLE public.trace_lab_must_not_exist (id integer)")
    await app_module.shutdown()
    db.reset_for_tests()                       # takes the main engine back out of src.database
    assert database._engine is None
    second = await app_module.startup(_settings("verify"))
    assert second.status == "ready" and second.applied_version == len(migrate.files())


async def test_boot_in_verify_mode_never_creates_the_schema(clean_boot):
    st = await app_module.startup(_settings("verify"))
    assert st.status == "absent" and not db.ready()
    async with db.lab_engine().connect() as conn:
        assert (await conn.execute(text("SELECT to_regnamespace('tracelab')"))).scalar() is None
