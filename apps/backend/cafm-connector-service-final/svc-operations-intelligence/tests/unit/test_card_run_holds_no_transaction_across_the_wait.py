"""A card run must not hold a database transaction open while it waits on the orchestrator.

Caught live on 17 Sep 2026 at 08:07 UTC with pg_stat_activity sampled every 5 seconds:

    pid 629877  idle in transaction  180s   SELECT … FROM plenum_cafm.users WHERE id = $1
    pid 630523  active  Lock/relation      blocked_by=1   DO $uuid_default$ … ALTER TABLE users …
    pid 629904  active  Lock/relation      blocked_by=1   SELECT u.id, u.email … (a token check)
    … twenty of them by 08:10:22, all released at 08:10:24 when the card's 180 s timeout fired.

The first is this module: run_card read the card and the owner, and the transaction those two
reads opened stayed open — idle, holding a share lock on users — for as long as the orchestrator
took. A migration's ALTER TABLE queued behind that lock, and every authenticated request in the
service queued behind the ALTER. The health endpoint answered in 60 ms throughout; the service
was fine and its database access was a queue.

Nothing is written until the run is over, so nothing needs a transaction open across the wait.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.engines.reports import cards as C


class _Result:
    def __init__(self, one):
        self._one = one
    def scalar_one_or_none(self):
        return self._one


class _FakeSession:
    """Records commits, and which call boundary each happened at."""

    def __init__(self, card):
        self.card = card
        self.events: list[str] = []
        self.added = []

    async def execute(self, *a, **k):
        self.events.append("execute")
        return _Result(self.card)

    async def commit(self):
        self.events.append("commit")

    def add(self, obj):
        self.added.append(obj)
        self.events.append("add")


def _card():
    return SimpleNamespace(
        id=uuid4(), owner_user_id=uuid4(), removed_at=None, status="running", enabled=True,
        prompt="which vendors are blocked", refresh={"every_minutes": 60}, timezone="UTC",
        last_tried_at=None, last_run_at=None, last_error=None, next_run_at=None,
        updated_at=datetime.now(timezone.utc),
    )


@pytest.fixture()
def wired(monkeypatch):
    session = _FakeSession(_card())

    async def fake_owner_token(sess, owner_id):
        sess.events.append("owner_token")
        return "tok"

    async def fake_ask(card, token, *, http=None):
        session.events.append("ask_orchestrator")
        return {"answer": "Six vendors are blocked.", "tool_calls": []}

    monkeypatch.setattr(C, "_owner_token", fake_owner_token)
    monkeypatch.setattr(C, "_ask_orchestrator", fake_ask)
    monkeypatch.setattr(C, "rich_from_tool_calls", lambda tc: None)
    return session


async def test_the_transaction_is_ended_before_the_orchestrator_is_awaited(wired):
    session = wired
    await C.run_card(session, session.card.id, trigger="schedule", claimed=True)
    ev = session.events
    assert "ask_orchestrator" in ev and "owner_token" in ev
    i_owner, i_ask = ev.index("owner_token"), ev.index("ask_orchestrator")
    assert i_owner < i_ask
    assert "commit" in ev[i_owner:i_ask], (
        f"no commit between reading the owner and awaiting the orchestrator: {ev}")


class TestMigrationsCannotQueueBehindAReader:
    """The other half of the same incident: the ALTER TABLE that waited three minutes. A
    migration statement now runs with a lock_timeout and retries, so a lock it cannot get
    promptly is an error to try again, not a wait that queues the service behind it."""

    def test_every_statement_runs_with_a_lock_timeout(self):
        import inspect
        from src import db
        src = inspect.getsource(db._run_ddl_statement)
        assert "SET LOCAL lock_timeout" in src and "SET lock_timeout" in src
        assert db._DDL_LOCK_RETRIES >= 2

    def test_both_runners_go_through_it(self):
        import inspect
        from src import db
        assert "_run_ddl_statement(stmt)" in inspect.getsource(db.exec_migration_statements)
        assert "_run_ddl_statement(stmt)" in inspect.getsource(db.apply_sql_migrations)

    def test_only_a_lock_timeout_is_retried(self):
        from src import db
        assert db._is_lock_timeout(Exception("canceling statement due to lock timeout"))
        assert db._is_lock_timeout(Exception("LockNotAvailableError: ..."))
        assert not db._is_lock_timeout(Exception('relation "x" does not exist'))

    async def test_a_lock_timeout_backs_off_and_retries_then_succeeds(self, monkeypatch):
        from src import db
        calls = {"n": 0}

        class _Conn:
            async def exec_driver_sql(self, sql):
                if sql.startswith("SET LOCAL"):
                    return None
                calls["n"] += 1
                if calls["n"] < 3:
                    raise Exception("canceling statement due to lock timeout")

        class _Begin:
            async def __aenter__(self): return _Conn()
            async def __aexit__(self, *a): return False

        class _Engine:
            def begin(self): return _Begin()

        # AsyncEngine.begin is read-only; swap the module's engine for the test instead.
        monkeypatch.setattr(db, "engine", _Engine())
        monkeypatch.setattr(db, "_DDL_LOCK_RETRY_SLEEP_S", 0.0)
        await db._run_ddl_statement("ALTER TABLE plenum_cafm.users ADD COLUMN IF NOT EXISTS x INT")
        assert calls["n"] == 3


async def test_the_run_is_still_recorded_after_the_wait(wired):
    session = wired
    run = await C.run_card(session, session.card.id, trigger="schedule", claimed=True)
    assert run is not None and run.ok is True
    assert session.events[-1] == "commit"
    assert session.card.status == "ready"
