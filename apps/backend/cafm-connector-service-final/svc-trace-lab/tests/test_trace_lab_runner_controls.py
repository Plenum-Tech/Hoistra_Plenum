"""Golden-run controls without a database: grade handlers are registered by name (never "golden"), boot
settles golden runs once the schema is ready, and settling does nothing before then (G0 addendum 2.5)."""
from __future__ import annotations

import pytest

from trace_lab import db, jobs, runner


def test_a_grade_handler_needs_a_name_other_than_golden_and_must_be_callable(monkeypatch):
    monkeypatch.setattr(runner, "GRADE_HANDLERS", {})

    async def fn(job):
        return "done"

    runner.register_grade_handler(" eval_cases ", fn)
    assert runner.GRADE_HANDLERS == {"eval_cases": fn}
    for bad in ("golden", "", "  "):
        with pytest.raises(ValueError):
            runner.register_grade_handler(bad, fn)
    with pytest.raises(ValueError):
        runner.register_grade_handler("x", None)


async def test_settling_does_nothing_before_the_schema_is_ready(monkeypatch):
    monkeypatch.setattr(db, "STATE", None)
    assert await runner.settle_after_restart() == {"runs": 0, "released": 0, "grading": 0}


async def test_boot_settles_golden_runs_once_the_schema_is_ready(monkeypatch):
    from src import database
    from trace_lab import app as app_module
    from trace_lab import migrate
    from trace_lab.settings import Settings

    calls: list[str] = []

    async def verify(engine, *, directory=None):
        return migrate.SchemaState("ready", 2, 2, pending=[])

    async def sweep(stale_after_s=120):
        calls.append("sweep")
        return []

    async def settle():
        calls.append("settle")
        return {"runs": 0, "released": 0, "grading": 0}

    monkeypatch.setattr(database, "_engine", None)
    monkeypatch.setattr(database, "_session_factory", None)
    monkeypatch.setattr(database, "AsyncSessionLocal", None)
    monkeypatch.setattr(migrate, "verify", verify)
    monkeypatch.setattr(jobs, "mark_interrupted", sweep)
    monkeypatch.setattr(runner, "settle_after_restart", settle)
    db.reset_for_tests()
    try:
        dead = "postgresql+asyncpg://u:p@127.0.0.1:1/x"
        st = await app_module.startup(Settings(main_db_url=dead, lab_db_url=dead, enabled=True))
        assert st.status == "ready" and calls == ["sweep", "settle"]
    finally:
        db.reset_for_tests()
