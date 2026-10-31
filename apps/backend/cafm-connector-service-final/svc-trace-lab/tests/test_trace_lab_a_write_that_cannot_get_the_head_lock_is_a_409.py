"""A writer that cannot get the head's lock in time is a 409 carrying the re-read head, not a timeout error.
Pure: the lab connection is a scripted fake whose locked read raises Postgres's "lock not available" (55P03,
what `lock_timeout` raises). Other database errors still propagate, every path leaves its transaction through
the context manager (a rollback, so no connection stays open), and another person's comment is never handed
back. The real two-connection version is in the _sql twin of the edit tests."""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import DBAPIError

from trace_lab import annotations, db, evidence, review_errors

ORG = str(uuid.uuid4())
ME = uuid.uuid4()
OTHER = uuid.uuid4()
LINEAGE = str(uuid.uuid4())
V1 = str(uuid.uuid4())
ME_P = SimpleNamespace(user_id=ME, email="me@example.com")


class LockNotAvailable(Exception):
    sqlstate = "55P03"


class QueryCanceled(Exception):
    sqlstate = "57014"


def head(kind="verdict", reviewer=ME):
    return {"id": V1, "lineage_id": LINEAGE, "version": 1, "supersedes": None, "kind": kind,
            "turn_id": "turn-1" if kind == "verdict" else None, "conversation_key": "s:sess-1",
            "reviewer_id": reviewer, "reviewer_email": "x@example.com", "tombstone": False,
            "verdict": "right" if kind == "verdict" else None, "note": None, "blind": None, "created_at": None}


class Result:
    def __init__(self, rows=()):
        self.rows = list(rows)

    def mappings(self):
        return self

    def first(self):
        return self.rows[0] if self.rows else None


class Conn:
    def __init__(self, live, error):
        self.live, self.error, self.sql = live, error, []

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        self.sql.append(sql)
        if sql.startswith("SET "):
            return Result()
        if "FOR UPDATE" in sql:
            raise DBAPIError(sql, {}, self.error)
        if sql.startswith("SELECT * FROM tracelab.annotations"):
            return Result(self.live)
        raise AssertionError(f"unexpected statement: {sql[:80]}")


class Begin:
    def __init__(self, conn, exits):
        self.conn, self.exits = conn, exits

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, exc_type, *rest):
        self.exits.append(exc_type)
        return False


@pytest.fixture
def script(monkeypatch):
    exits: list = []

    async def scoped(principal, org, turn_id):
        return {"turn_id": turn_id, "session_id": "sess-1"}

    async def no_snapshot(*a, **k):
        return 0

    monkeypatch.setattr(annotations, "_scoped_turn", scoped)
    monkeypatch.setattr(evidence, "snapshot", no_snapshot)

    def make(live, error=LockNotAvailable()):
        conn = Conn(live, error)
        monkeypatch.setattr(db, "lab_engine", lambda: SimpleNamespace(begin=lambda: Begin(conn, exits)))
        return conn
    make.exits = exits
    return make


async def test_a_verdict_save_that_waits_too_long_for_the_head_is_a_409_with_the_head(script):
    conn = script([head()])
    with pytest.raises(review_errors.Stale) as e:
        await annotations.save_verdict(ME_P, ORG, "turn-1", verdict="right", note=None, anchor=None, base=V1)
    assert e.value.status == 409 and e.value.current["id"] == V1
    assert any(s.startswith("SET LOCAL lock_timeout") for s in conn.sql)
    assert script.exits == [DBAPIError, None]                                      # the write's transaction was rolled back, then the re-read closed


async def test_a_verdict_delete_that_waits_too_long_for_the_head_is_a_409_with_the_head(script):
    script([head()])
    with pytest.raises(review_errors.Stale) as e:
        await annotations.delete_verdict(ME_P, ORG, "turn-1", base=V1)
    assert e.value.status == 409 and e.value.current["id"] == V1


async def test_a_comment_change_that_waits_too_long_for_the_head_is_a_409_with_the_head(script):
    for change in (lambda: annotations.edit_comment(ME_P, ORG, LINEAGE, note="Late.", base=V1),
                   lambda: annotations.delete_comment(ME_P, ORG, LINEAGE, base=V1)):
        script([head(kind="comment")])
        with pytest.raises(review_errors.Stale) as e:
            await change()
        assert e.value.status == 409 and e.value.current["id"] == V1


async def test_another_persons_comment_is_not_handed_back_when_the_lock_wait_runs_out(script):
    script([head(kind="comment", reviewer=OTHER)])
    with pytest.raises(review_errors.Forbidden) as e:
        await annotations.edit_comment(ME_P, ORG, LINEAGE, note="Not mine.", base=V1)
    assert e.value.reason == "not_author" and e.value.status == 403


async def test_a_lock_wait_that_runs_out_with_no_head_left_is_a_409_without_one(script):
    script([])
    with pytest.raises(review_errors.Stale) as e:
        await annotations.delete_verdict(ME_P, ORG, "turn-1", base=V1)
    assert e.value.current is None


async def test_any_other_database_error_still_propagates(script):
    script([head()], error=QueryCanceled())
    with pytest.raises(DBAPIError):
        await annotations.save_verdict(ME_P, ORG, "turn-1", verdict="right", note=None, anchor=None, base=V1)
    assert script.exits == [DBAPIError]                                            # and the transaction was left through its context manager


def test_lock_busy_reads_the_sqlstate_through_the_driver_wrappers():
    inner = LockNotAvailable()
    wrapped = DBAPIError("stmt", {}, SimpleNamespace(__cause__=inner))
    wrapped.orig.__cause__ = inner
    assert annotations._lock_busy(DBAPIError("stmt", {}, inner)) is True
    assert annotations._lock_busy(wrapped) is True
    assert annotations._lock_busy(DBAPIError("stmt", {}, QueryCanceled())) is False
    assert annotations._lock_busy(ValueError("nope")) is False
