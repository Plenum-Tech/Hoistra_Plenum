"""trace_lab.db without a database: the read-only main engine installed into src.database before
first use, the lab engine, the 503 dependency and the error envelope (G0 addendum 2.3).
Building an asyncpg engine opens no connection, so nothing here reaches a database."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient

from src import database
from trace_lab import db
from trace_lab.settings import Settings

MAIN = "postgresql+asyncpg://u:p@127.0.0.1:1/plenum?sslmode=require"
LAB = "postgresql+asyncpg://u:p@127.0.0.1:1/hoist_tracelab"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setattr(database, "_engine", None)
    monkeypatch.setattr(database, "_session_factory", None)
    monkeypatch.setattr(database, "AsyncSessionLocal", None)
    monkeypatch.delenv("TRACE_LAB_DB_URL", raising=False)
    db.reset_for_tests()
    yield
    db.reset_for_tests()


def _state(status="ready", applied=1, expected=1, problems=()):
    return SimpleNamespace(status=status, expected_version=expected, applied_version=applied, pending=[],
                           problems=list(problems), checked_at="2026-10-15T09:00:00Z")


def _spy(monkeypatch) -> list[dict]:
    calls: list[dict] = []
    real = db.create_async_engine

    def spy(url, **kw):
        calls.append({"url": url, **kw})
        return real(url, **kw)

    monkeypatch.setattr(db, "create_async_engine", spy)
    return calls


def test_the_main_engine_is_read_only_small_and_installed_before_first_use(monkeypatch):
    calls = _spy(monkeypatch)
    engine = db.install_main_engine(Settings(main_db_url=MAIN))
    assert database._engine is engine and database._get_engine() is engine and db.main_engine() is engine
    assert database.AsyncSessionLocal is not None and database._session_factory.kw["bind"] is engine
    (call,) = calls
    assert call["pool_size"] == 2 and call["max_overflow"] == 0 and call["pool_pre_ping"] is True
    assert call["connect_args"]["server_settings"] == {"default_transaction_read_only": "on",
                                                       "application_name": "trace-lab"}
    assert "ssl" in call["connect_args"]          # sslmode=require became asyncpg's ssl context
    assert "sslmode" not in call["url"]           # svc-deepagents' own helper stripped it
    assert engine.pool.size() == 2


def test_install_main_engine_is_idempotent(monkeypatch):
    calls = _spy(monkeypatch)
    first = db.install_main_engine(Settings(main_db_url=MAIN))
    assert db.install_main_engine(Settings(main_db_url=MAIN)) is first and len(calls) == 1


@pytest.mark.parametrize("attr", ["_engine", "_session_factory"])
def test_install_refuses_when_src_database_was_used_first(monkeypatch, attr):
    monkeypatch.setattr(database, attr, object())
    with pytest.raises(RuntimeError, match="before Trace Lab installed its read-only engine"):
        db.install_main_engine(Settings(main_db_url=MAIN))


def test_install_refuses_when_the_engine_was_swapped_afterwards(monkeypatch):
    db.install_main_engine(Settings(main_db_url=MAIN))
    monkeypatch.setattr(database, "_engine", object())
    with pytest.raises(RuntimeError, match="replaced"):
        db.install_main_engine(Settings(main_db_url=MAIN))


def test_install_needs_the_main_database_url():
    with pytest.raises(ValueError, match="DB_URL"):
        db.install_main_engine(Settings())
    assert database._engine is None


def test_main_engine_before_install_is_an_error():
    with pytest.raises(RuntimeError, match="not installed"):
        db.main_engine()


def test_the_lab_engine_is_its_own_pool_and_not_read_only(monkeypatch):
    calls = _spy(monkeypatch)
    db.install_main_engine(Settings(main_db_url=MAIN, lab_db_url=LAB))
    lab = db.lab_engine()
    assert lab is db.lab_engine() and lab is not db.main_engine() and lab is not database._engine
    call = calls[-1]
    assert call["pool_size"] == 3 and call["connect_args"]["server_settings"] == {"application_name": "trace-lab"}
    assert "ssl" not in call["connect_args"]      # a local host with no sslmode: plain connection
    assert lab.pool.size() == 3


def test_the_lab_engine_reads_the_environment_when_nothing_was_installed(monkeypatch):
    monkeypatch.setenv("TRACE_LAB_DB_URL", LAB)
    assert db.lab_engine().url.database == "hoist_tracelab"
    assert database._engine is None               # the CLI path never installs the main engine


def test_the_lab_engine_needs_its_url(monkeypatch):
    monkeypatch.delenv("TRACE_LAB_DB_URL", raising=False)
    with pytest.raises(RuntimeError, match="TRACE_LAB_DB_URL"):
        db.lab_engine()


def test_reset_for_tests_takes_the_engine_back_out():
    db.install_main_engine(Settings(main_db_url=MAIN))
    db.set_state(_state())
    db.reset_for_tests()
    assert database._engine is None and database._session_factory is None and database.AsyncSessionLocal is None
    assert db.STATE is None and not db.ready()


def test_ready_follows_the_state():
    assert db.ready() is False
    db.set_state(_state("behind", applied=0))
    assert db.ready() is False
    db.set_state(_state("ready"))
    assert db.ready() is True


def test_http_error_is_the_house_envelope():
    e = db.http_error(403, "reviewers_only", "Only admins.", hint="ask")
    assert isinstance(e, HTTPException) and e.status_code == 403
    assert e.detail == {"reason": "reviewers_only", "error": "Only admins.", "hint": "ask"}


def test_unavailable_body_names_no_table_or_schema():
    db.set_state(_state("drifted", applied=1, expected=2, problems=["jobs: extra column foo", "tracelab.x"]))
    body = db.unavailable_body()
    assert body["reason"] == "trace_lab_unavailable"
    assert body["error"] == "Trace Lab is not available right now. The chat is not affected."
    assert (body["status"], body["expected_version"], body["applied_version"]) == ("drifted", 2, 1)
    assert body["available"] is False and body["retryable"] is True and body["checked_at"].endswith("Z")
    assert "jobs" not in json.dumps(body) and "tracelab" not in json.dumps(body)


def test_unavailable_body_before_any_check():
    body = db.unavailable_body()
    assert body["status"] == "unreadable" and body["applied_version"] is None and body["checked_at"] is None


def _probe() -> TestClient:
    app = FastAPI()

    @app.get("/probe", dependencies=[Depends(db.require_ready)])
    async def probe():
        return {"ok": True}

    return TestClient(app)


def test_require_ready_answers_503_until_ready():
    r = _probe().get("/probe")
    assert r.status_code == 503 and r.headers["retry-after"] == "30"
    assert r.json()["detail"]["reason"] == "trace_lab_unavailable"
    db.set_state(_state("ready"))
    assert _probe().get("/probe").json() == {"ok": True}
