"""The lifespan: startup() installs the read-only main engine, verifies the lab schema into
db.STATE, sweeps stale jobs, never raises and never runs svc-deepagents' startup; require_ready and
refresh_state re-check a stale state. No database: migrate and jobs are replaced with fakes."""
from __future__ import annotations

import ast
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src import database
from trace_lab import app as app_module
from trace_lab import db, jobs, migrate
from trace_lab.settings import Settings

TL_ROOT = Path(__file__).resolve().parents[1]
MAIN = "postgresql+asyncpg://u:p@127.0.0.1:1/plenum"
LAB = "postgresql+asyncpg://u:p@127.0.0.1:1/hoist_tracelab"
ON = Settings(main_db_url=MAIN, lab_db_url=LAB, enabled=True)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setattr(database, "_engine", None)
    monkeypatch.setattr(database, "_session_factory", None)
    monkeypatch.setattr(database, "AsyncSessionLocal", None)
    monkeypatch.delenv("TRACE_LAB_APPLY_DATABASES", raising=False)
    db.reset_for_tests()
    yield
    db.reset_for_tests()


@pytest.fixture
def swept(monkeypatch) -> list[int]:
    calls: list[int] = []

    async def sweep(stale_after_s=120):
        calls.append(1)
        return []

    monkeypatch.setattr(jobs, "mark_interrupted", sweep)
    return calls


def _verify(status: str):
    async def fake(engine, *, directory=None):
        return migrate.SchemaState(status, 1, 1 if status == "ready" else None,
                                   pending=[] if status == "ready" else [1])
    return fake


def _never(what: str):
    async def never(*a, **k):
        raise AssertionError(what)
    return never


async def test_startup_installs_the_read_only_engine_verifies_and_sweeps(monkeypatch, swept):
    monkeypatch.setattr(migrate, "verify", _verify("ready"))
    monkeypatch.setattr(migrate, "apply_pending", _never("applied in verify mode"))
    st = await app_module.startup(ON)
    assert st.status == "ready" and db.ready() and swept == [1]
    assert database._engine is db.main_engine()
    assert db.lab_engine() is not db.main_engine()


async def test_startup_leaves_trace_lab_down_but_never_raises(monkeypatch, swept):
    async def boom(engine, *, directory=None):
        raise RuntimeError("no route to host")

    monkeypatch.setattr(migrate, "verify", boom)
    st = await app_module.startup(ON)
    assert st.status == "unreadable" and "no route to host" in st.problems[0]
    assert not db.ready() and swept == []


async def test_a_slow_verify_times_out(monkeypatch, swept):
    async def slow(engine, *, directory=None):
        await asyncio.sleep(5)

    monkeypatch.setattr(app_module, "VERIFY_TIMEOUT_S", 0.05)
    monkeypatch.setattr(migrate, "verify", slow)
    st = await app_module.startup(ON)
    assert st.status == "unreadable" and "timed out" in st.problems[0]


@pytest.mark.parametrize("settings, needle", [
    (Settings(main_db_url=MAIN, lab_db_url=LAB, enabled=False), "turned off"),
    (Settings(lab_db_url=LAB, enabled=True), "main database: DB_URL is not set"),
    (Settings(main_db_url=MAIN, enabled=True), "TRACE_LAB_DB_URL is not set"),
])
async def test_startup_disables_itself_for_reasons_a_recheck_cannot_fix(monkeypatch, swept, settings, needle):
    monkeypatch.setattr(migrate, "verify", _never("a disabled service checked the schema"))
    st = await app_module.startup(settings)
    assert st.status == "unreadable" and needle in st.problems[0] and not db.ready()
    assert (await db.refresh_state(max_age_s=0)).status == "unreadable"     # pinned: no re-check


async def test_bad_settings_disable_the_service(monkeypatch, swept):
    monkeypatch.setenv("TRACE_LAB_SCHEMA", "migrate-please")
    st = await app_module.startup(None)
    assert st.status == "unreadable" and "TRACE_LAB_SCHEMA" in st.problems[0]
    assert database._engine is None


async def test_a_writable_engine_already_in_src_database_disables(monkeypatch, swept):
    monkeypatch.setattr(database, "_engine", object())
    monkeypatch.setattr(migrate, "verify", _never("ran on a writable engine"))
    st = await app_module.startup(ON)
    assert st.status == "unreadable" and "before Trace Lab installed its read-only engine" in st.problems[0]


async def test_apply_mode_applies_on_an_allowed_scratch_database(monkeypatch, swept):
    seen = {}

    async def dbname(engine):
        return "tracelab_scratch"

    async def apply(engine, *, applied_by, through=None, dry_run=False, directory=None):
        seen["by"] = applied_by
        return migrate.SchemaState("ready", 1, 1)

    monkeypatch.setattr(migrate, "current_database", dbname)
    monkeypatch.setattr(migrate, "apply_pending", apply)
    s = Settings(main_db_url=MAIN, lab_db_url="postgresql+asyncpg://u:p@127.0.0.1:1/tracelab_scratch",
                 schema_mode="apply", enabled=True)
    st = await app_module.startup(s)
    assert st.status == "ready" and seen["by"].startswith("startup:")


@pytest.mark.parametrize("database_name, url", [
    ("hoist_tracelab", "postgresql+asyncpg://u:p@127.0.0.1:1/hoist_tracelab"),
    ("tracelab_scratch", "postgresql+asyncpg://u:p@tl.postgres.database.azure.com:5432/tracelab_scratch"),
])
async def test_apply_mode_only_verifies_off_the_allow_list_and_on_azure(monkeypatch, swept, database_name, url):
    async def dbname(engine):
        return database_name

    monkeypatch.setattr(migrate, "current_database", dbname)
    monkeypatch.setattr(migrate, "verify", _verify("behind"))
    monkeypatch.setattr(migrate, "apply_pending", _never("applied where it must only verify"))
    st = await app_module.startup(Settings(main_db_url=MAIN, lab_db_url=url, schema_mode="apply", enabled=True))
    assert st.status == "behind" and not db.ready()


async def test_refresh_state_rechecks_a_stale_state_once_and_sweeps_when_it_turns_ready(monkeypatch, swept):
    calls: list[int] = []

    async def now_ready(engine, *, directory=None):
        calls.append(1)
        await asyncio.sleep(0.01)
        return migrate.SchemaState("ready", 1, 1)

    monkeypatch.setattr(db, "lab_engine", lambda: object())
    monkeypatch.setattr(migrate, "verify", now_ready)
    db.set_state(migrate.SchemaState("behind", 1, None, pending=[1], checked_at="2026-10-15T09:00:00Z"))
    states = await asyncio.gather(*(db.refresh_state() for _ in range(5)))
    assert calls == [1] and all(s.status == "ready" for s in states) and swept == [1]
    await db.refresh_state(max_age_s=0)
    assert calls == [1]                                   # a ready state is never re-checked


async def test_refresh_state_keeps_a_recent_state(monkeypatch, swept):
    monkeypatch.setattr(migrate, "verify", _never("re-checked a fresh state"))
    db.set_state(migrate.SchemaState("behind", 1, None, pending=[1]))
    assert (await db.refresh_state()).status == "behind"


async def test_a_failed_recheck_is_a_state_not_a_crash(monkeypatch, swept):
    def no_url():
        raise RuntimeError("TRACE_LAB_DB_URL is not set")

    monkeypatch.setattr(db, "lab_engine", no_url)
    st = await db.refresh_state()
    assert st.status == "unreadable" and "TRACE_LAB_DB_URL" in st.problems[0] and swept == []


def test_the_lifespan_runs_startup_and_shutdown(monkeypatch):
    seen: list[object] = []

    async def fake_startup(settings=None):
        seen.append(settings)
        return migrate.SchemaState("ready", 1, 1)

    async def fake_shutdown():
        seen.append("shutdown")

    monkeypatch.setattr(app_module, "startup", fake_startup)
    monkeypatch.setattr(app_module, "shutdown", fake_shutdown)
    s = Settings()
    with TestClient(app_module.create_app(s)) as client:
        assert client.get("/health").json() == {"ok": True, "service": "trace-lab"}
    assert seen == [s, "shutdown"]


def test_the_service_never_runs_svc_deepagents_startup():
    tree = ast.parse((TL_ROOT / "trace_lab" / "app.py").read_text(encoding="utf-8"))
    names: set[str] = set()
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.ImportFrom):
            modules.add(node.module or "")
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
    assert not names & {"ensure_tables", "ensure_table", "purge", "create_checkpointer", "init_db",
                        "init_session_factory"}
    assert not any(m == "src" or m.startswith("src.") for m in modules)   # app.py imports only its own modules


def test_importing_the_app_runs_no_startup_and_builds_no_engine():
    code = (
        "import json, sys\n"
        "import trace_lab.app\n"
        "from trace_lab import db\n"
        "import src.database as d\n"
        "print(json.dumps({'state': db.STATE is None, 'engine': d._engine is None,\n"
        "                  'main': 'src.api.main' in sys.modules}))\n"
    )
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(TL_ROOT), str(TL_ROOT.parent / "svc-deepagents")]),
           "PYTHONDONTWRITEBYTECODE": "1"}
    done = subprocess.run([sys.executable, "-c", code], cwd=TL_ROOT, capture_output=True, text=True,
                          check=True, timeout=120, env=env)
    assert json.loads(done.stdout.strip().splitlines()[-1]) == {"state": True, "engine": True, "main": False}
