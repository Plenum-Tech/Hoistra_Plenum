"""Boot and the judge runs (Phase 6): after the job sweep, a judge run whose job the sweep took away reads
interrupted, so the next start resumes it. Recovery runs once the schema is ready, after the golden-run settle, and a
failure in it never stops startup. No database: every step is replaced."""
from __future__ import annotations

from pathlib import Path

from trace_lab import app as app_module
from trace_lab import db, jobs, judges, migrate, runner
from trace_lab.settings import Settings

DEAD = "postgresql+asyncpg://u:p@127.0.0.1:1/x"


def test_startup_recovers_judge_runs_after_the_job_sweep():
    source = Path(app_module.__file__).read_text(encoding="utf-8")
    assert "recover_after_restart(" in source
    assert source.index("mark_interrupted(") < source.index("recover_after_restart(")
    assert source.index("settle_after_restart(") < source.index("recover_after_restart(")


async def _boot(monkeypatch, recover) -> tuple[migrate.SchemaState, list[str]]:
    from src import database

    calls: list[str] = []

    async def verify(engine, *, directory=None):
        return migrate.SchemaState("ready", 5, 5, pending=[])

    async def sweep(stale_after_s=120):
        calls.append("sweep")
        return []

    async def settle():
        calls.append("settle")
        return {"runs": 0, "released": 0, "grading": 0}

    async def recovering(**kwargs):
        calls.append("recover")
        return await recover()

    monkeypatch.setattr(database, "_engine", None)
    monkeypatch.setattr(database, "_session_factory", None)
    monkeypatch.setattr(database, "AsyncSessionLocal", None)
    monkeypatch.setattr(migrate, "verify", verify)
    monkeypatch.setattr(jobs, "mark_interrupted", sweep)
    monkeypatch.setattr(runner, "settle_after_restart", settle)
    monkeypatch.setattr(judges, "recover_after_restart", recovering)
    db.reset_for_tests()
    try:
        st = await app_module.startup(Settings(main_db_url=DEAD, lab_db_url=DEAD, enabled=True))
    finally:
        db.reset_for_tests()
    return st, calls


async def test_boot_recovers_judge_runs_once_the_schema_is_ready(monkeypatch):
    async def fine():
        return 2

    st, calls = await _boot(monkeypatch, fine)
    assert st.status == "ready" and calls == ["sweep", "settle", "recover"]


async def test_a_failing_recovery_never_stops_startup(monkeypatch):
    async def broken():
        raise RuntimeError("the lab database dropped the connection")

    st, calls = await _boot(monkeypatch, broken)
    assert st.status == "ready" and calls == ["sweep", "settle", "recover"]


async def test_a_failing_sweep_does_not_skip_the_recovery(monkeypatch):
    from src import database

    calls: list[str] = []

    async def verify(engine, *, directory=None):
        return migrate.SchemaState("ready", 5, 5, pending=[])

    async def sweep(stale_after_s=120):
        raise RuntimeError("sweep failed")

    async def recovering(**kwargs):
        calls.append("recover")
        return 0

    monkeypatch.setattr(database, "_engine", None)
    monkeypatch.setattr(database, "_session_factory", None)
    monkeypatch.setattr(database, "AsyncSessionLocal", None)
    monkeypatch.setattr(migrate, "verify", verify)
    monkeypatch.setattr(jobs, "mark_interrupted", sweep)
    monkeypatch.setattr(judges, "recover_after_restart", recovering)
    db.reset_for_tests()
    try:
        st = await app_module.startup(Settings(main_db_url=DEAD, lab_db_url=DEAD, enabled=True))
    finally:
        db.reset_for_tests()
    assert st.status == "ready" and calls == ["recover"]


async def test_nothing_is_recovered_while_the_schema_is_not_ready(monkeypatch):
    from src import database

    calls: list[str] = []

    async def verify(engine, *, directory=None):
        return migrate.SchemaState("behind", 5, 4, pending=[5])

    async def recovering(**kwargs):
        calls.append("recover")
        return 0

    monkeypatch.setattr(database, "_engine", None)
    monkeypatch.setattr(database, "_session_factory", None)
    monkeypatch.setattr(database, "AsyncSessionLocal", None)
    monkeypatch.setattr(migrate, "verify", verify)
    monkeypatch.setattr(judges, "recover_after_restart", recovering)
    db.reset_for_tests()
    try:
        st = await app_module.startup(Settings(main_db_url=DEAD, lab_db_url=DEAD, enabled=True))
    finally:
        db.reset_for_tests()
    assert st.status != "ready" and calls == []
