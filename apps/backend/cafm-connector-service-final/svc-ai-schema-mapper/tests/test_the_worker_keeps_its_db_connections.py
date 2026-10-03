"""With HOIST_DB_POOL=1 a development process keeps a pool of database connections.

In development every session opened a new TLS connection to Azure (NullPool): ~0.25 s of handshake
and login for each small write, dozens of times a gate on the worker (2 Oct 2026). The pooled engine
is the one production uses (pre-ping, recycle, the same connect timeouts).
"""
from sqlalchemy.pool import NullPool

from src import db


def _engine(monkeypatch, pool_flag):
    settings = db.get_settings()
    monkeypatch.setattr(settings, "environment", "development")
    monkeypatch.setattr(settings, "db_url", "postgresql+asyncpg://u:p@db.example:5432/d")
    if pool_flag is None:
        monkeypatch.delenv("HOIST_DB_POOL", raising=False)
    else:
        monkeypatch.setenv("HOIST_DB_POOL", pool_flag)
    return db._build_async_engine()


def test_development_keeps_one_connection_per_session_by_default(monkeypatch):
    assert isinstance(_engine(monkeypatch, None).pool, NullPool)


def test_hoist_db_pool_gives_development_the_pooled_engine(monkeypatch):
    engine = _engine(monkeypatch, "1")
    assert not isinstance(engine.pool, NullPool)
    assert engine.pool._pre_ping and engine.pool._recycle == 1800


def test_the_worker_picks_a_queued_gate_answer_up_within_a_tenth_of_a_second():
    # arq's default poll is 0.5 s: ~0.25 s lost on average per gate answer, ten times a run.
    from src.worker import WorkerSettings

    assert WorkerSettings.poll_delay <= 0.1
