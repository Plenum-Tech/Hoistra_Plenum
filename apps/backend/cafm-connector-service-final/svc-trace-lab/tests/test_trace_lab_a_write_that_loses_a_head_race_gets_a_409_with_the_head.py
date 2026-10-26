"""A write that loses a race for a head is a 409 carrying the re-read head, never a 404. Pure: the lab connection
is a scripted fake, so this runs without a database (the real two-connection race is in the _sql twin tests,
which use tl_head_race.lose_the_race).

Under READ COMMITTED a writer that waited on the head's FOR UPDATE lock gets NO row back once the winner
superseded it. The head getters then re-read the live head without a lock. These tests script exactly that:
the locked read matches nothing, the plain read finds the winner's version. The fake refuses every statement it
does not expect, so a write on a refused path would fail the test."""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from trace_lab import annotations, db, evidence, review_errors

ORG = str(uuid.uuid4())
ME = uuid.uuid4()
OTHER = uuid.uuid4()
LINEAGE = str(uuid.uuid4())
V1, V2 = str(uuid.uuid4()), str(uuid.uuid4())
ME_P = SimpleNamespace(user_id=ME, email="me@example.com")


def row(id_, version, *, kind="verdict", reviewer=ME, tombstone=False):
    return {"id": id_, "lineage_id": LINEAGE, "version": version, "supersedes": None, "kind": kind,
            "turn_id": "turn-1" if kind == "verdict" else None, "conversation_key": "s:sess-1",
            "reviewer_id": reviewer, "reviewer_email": "x@example.com", "tombstone": tombstone,
            "verdict": None if tombstone else "right", "note": None, "blind": None, "created_at": None}


class Result:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.rowcount = 1

    def mappings(self):
        return self

    def first(self):
        return self.rows[0] if self.rows else None


class Conn:
    """Answers the two head reads and the SET LOCAL; any other statement is a test failure."""

    def __init__(self, locked, live):
        self.locked, self.live, self.reads = locked, live, []

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        if sql.startswith("SET LOCAL"):
            return Result()
        if sql.startswith("SELECT * FROM tracelab.annotations"):
            self.reads.append("locked" if "FOR UPDATE" in sql else "plain")
            return Result(self.locked if "FOR UPDATE" in sql else self.live)
        raise AssertionError(f"unexpected statement: {sql[:80]}")


class Begin:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *exc):
        return False


@pytest.fixture
def script(monkeypatch):
    """script(locked=[...], live=[...]) -> the Conn that db.lab_engine().begin() will hand out."""
    async def scoped(principal, org, turn_id):
        return {"turn_id": turn_id, "session_id": "sess-1"}

    async def no_snapshot(*a, **k):
        return 0

    monkeypatch.setattr(annotations, "_scoped_turn", scoped)
    monkeypatch.setattr(evidence, "snapshot", no_snapshot)

    def make(locked, live):
        conn = Conn(locked, live)
        monkeypatch.setattr(db, "lab_engine", lambda: SimpleNamespace(begin=lambda: Begin(conn)))
        return conn
    return make


# ── verdicts ───────────────────────────────────────────────────────────────────────────

async def test_a_verdict_edit_that_loses_the_race_is_a_409_with_the_re_read_head(script):
    conn = script(locked=[], live=[row(V2, 2)])
    with pytest.raises(review_errors.Stale) as e:
        await annotations.save_verdict(ME_P, ORG, "turn-1", verdict="right", note=None, anchor=None, base=V1)
    assert e.value.status == 409 and e.value.current["id"] == V2 and e.value.current["version"] == 2
    assert conn.reads == ["locked", "plain"]


async def test_a_first_save_that_finds_the_winners_first_save_is_a_409_with_it(script):
    script(locked=[], live=[row(V1, 1)])
    with pytest.raises(review_errors.Stale) as e:
        await annotations.save_verdict(ME_P, ORG, "turn-1", verdict="right", note=None, anchor=None, base=None)
    assert e.value.current["id"] == V1


async def test_a_delete_that_loses_the_race_is_a_409_not_a_404(script):
    conn = script(locked=[], live=[row(V2, 2)])
    with pytest.raises(review_errors.Stale) as e:
        await annotations.delete_verdict(ME_P, ORG, "turn-1", base=V1)
    assert e.value.status == 409 and e.value.current["id"] == V2 and conn.reads == ["locked", "plain"]


async def test_a_delete_after_a_winning_delete_is_a_409_with_the_tombstone(script):
    script(locked=[], live=[row(V2, 2, tombstone=True)])
    with pytest.raises(review_errors.Stale) as e:
        await annotations.delete_verdict(ME_P, ORG, "turn-1", base=V1)
    assert e.value.current["id"] == V2 and e.value.current["tombstone"] is True


async def test_a_delete_with_no_live_head_at_all_is_a_404(script):
    conn = script(locked=[], live=[])
    with pytest.raises(review_errors.NotFound) as e:
        await annotations.delete_verdict(ME_P, ORG, "turn-1", base=V1)
    assert e.value.status == 404 and e.value.reason == "annotation_not_found" and conn.reads == ["locked", "plain"]


async def test_a_delete_on_a_tombstone_head_is_a_409_for_an_old_base_and_a_404_for_the_tombstones_own_id(script):
    script(locked=[row(V2, 2, tombstone=True)], live=[])
    with pytest.raises(review_errors.Stale) as e:
        await annotations.delete_verdict(ME_P, ORG, "turn-1", base=V1)             # a double click carries the old base
    assert e.value.current["id"] == V2
    with pytest.raises(review_errors.NotFound) as e:
        await annotations.delete_verdict(ME_P, ORG, "turn-1", base=V2.upper())     # nothing left to delete
    assert e.value.reason == "annotation_not_found"


async def test_a_head_found_by_the_locked_read_is_not_read_again(script):
    conn = script(locked=[row(V2, 2)], live=[row(V2, 2)])
    with pytest.raises(review_errors.Stale):
        await annotations.delete_verdict(ME_P, ORG, "turn-1", base=V1)
    assert conn.reads == ["locked"]                                                 # the normal path costs one read


# ── comments ───────────────────────────────────────────────────────────────────────────

async def test_a_comment_edit_or_delete_that_loses_the_race_is_a_409_with_the_re_read_head(script):
    for change in (lambda: annotations.edit_comment(ME_P, ORG, LINEAGE, note="Late.", base=V1),
                   lambda: annotations.delete_comment(ME_P, ORG, LINEAGE, base=V1)):
        conn = script(locked=[], live=[row(V2, 2, kind="comment")])
        with pytest.raises(review_errors.Stale) as e:
            await change()
        assert e.value.status == 409 and e.value.current["id"] == V2 and conn.reads == ["locked", "plain"]


async def test_a_comment_change_after_a_winning_delete_is_a_409_with_the_tombstone(script):
    script(locked=[], live=[row(V2, 2, kind="comment", tombstone=True)])
    with pytest.raises(review_errors.Stale) as e:
        await annotations.edit_comment(ME_P, ORG, LINEAGE, note="Late.", base=V1)
    assert e.value.current["tombstone"] is True


async def test_a_comment_with_no_live_head_at_all_is_a_404(script):
    script(locked=[], live=[])
    with pytest.raises(review_errors.NotFound) as e:
        await annotations.edit_comment(ME_P, ORG, LINEAGE, note="Nothing there.", base=V1)
    assert e.value.reason == "annotation_not_found"


async def test_another_persons_comment_is_never_handed_back_as_the_current_head(script):
    script(locked=[], live=[row(V2, 2, kind="comment", reviewer=OTHER)])            # found by the re-read
    with pytest.raises(review_errors.Forbidden) as e:
        await annotations.edit_comment(ME_P, ORG, LINEAGE, note="Not mine.", base=V1)
    assert e.value.reason == "not_author" and e.value.status == 403
    script(locked=[row(V2, 2, kind="comment", reviewer=OTHER, tombstone=True)], live=[])
    with pytest.raises(review_errors.Forbidden):                                    # even a tombstone of someone else's
        await annotations.delete_comment(ME_P, ORG, LINEAGE, base=V2)


async def test_a_comment_tombstone_head_is_a_409_for_an_old_base_and_a_404_for_its_own_id(script):
    script(locked=[row(V2, 2, kind="comment", tombstone=True)], live=[])
    with pytest.raises(review_errors.Stale) as e:
        await annotations.delete_comment(ME_P, ORG, LINEAGE, base=V1)
    assert e.value.current["id"] == V2
    with pytest.raises(review_errors.NotFound):
        await annotations.edit_comment(ME_P, ORG, LINEAGE, note="Back again.", base=V2)


async def test_a_comment_head_found_by_the_locked_read_is_not_read_again(script):
    conn = script(locked=[row(V2, 2, kind="comment")], live=[row(V2, 2, kind="comment")])
    with pytest.raises(review_errors.Stale):
        await annotations.edit_comment(ME_P, ORG, LINEAGE, note="Old tab.", base=V1)
    assert conn.reads == ["locked"]
