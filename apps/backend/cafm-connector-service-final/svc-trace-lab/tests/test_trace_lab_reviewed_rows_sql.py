"""labels.reviewed_rows: the reviewed answers of a scope (at least one live verdict), newest first, with the batch each
was drawn into, joined in Python across the two databases. Golden answers and answers with no company are left out.
Needs the scratch database."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import trace_lab_m4_fixtures as fx
from tl_scratch import lab_scratch, main_scratch, requires_scratch  # noqa: F401
from trace_lab import labels

pytestmark = requires_scratch
NOW = datetime.now(timezone.utc).replace(microsecond=0)


async def _answer(lab, main, tid, org=fx.ORG_A, *, minutes_ago=10, session_id="sess-r", verdict="wrong", reviewer=None,
                  tombstone=False):
    async with main.begin() as conn:
        await fx.add_turn(conn, tid, org, session_id=session_id, started_at=NOW - timedelta(minutes=minutes_ago),
                          question=f"Question for {tid}")
    async with lab.begin() as conn:
        return await fx.add_note(conn, tid, org, verdict=verdict, reviewer=reviewer, tombstone=tombstone)


async def test_reviewed_answers_come_back_newest_first_with_their_conversation_key(lab_scratch, main_scratch):
    await _answer(lab_scratch, main_scratch, "turn-r100000000000001", minutes_ago=30)
    await _answer(lab_scratch, main_scratch, "turn-r100000000000002", minutes_ago=5)
    await _answer(lab_scratch, main_scratch, "turn-r100000000000003", minutes_ago=15, session_id=None)
    async with main_scratch.begin() as conn:                       # never reviewed: never listed
        await fx.add_turn(conn, "turn-r100000000000004", fx.ORG_A)
    async with lab_scratch.connect() as conn:
        rows = await labels.reviewed_rows(conn, fx.ORG_A)
    assert [r["turn_id"] for r in rows] == ["turn-r100000000000002", "turn-r100000000000003", "turn-r100000000000001"]
    assert [r["conversation_key"] for r in rows] == ["s:sess-r", "t:turn-r100000000000003", "s:sess-r"]
    first = rows[0]
    assert first["organization_id"] == fx.ORG_A and first["question"] == "Question for turn-r100000000000002"
    assert first["batch_id"] is None and first["plan_step"] is None and first["withdrawn_at"] is None
    assert set(first) >= {"turn_id", "session_id", "organization_id", "question", "started_at", "batch_id", "plan_step",
                          "strategy", "withdrawn_at", "conversation_key"}


async def test_the_scope_is_one_company_or_the_whole_platform(lab_scratch, main_scratch):
    await _answer(lab_scratch, main_scratch, "turn-r200000000000001", fx.ORG_A)
    await _answer(lab_scratch, main_scratch, "turn-r200000000000002", fx.ORG_B)
    async with lab_scratch.connect() as conn:
        a = await labels.reviewed_rows(conn, fx.ORG_A)
        b = await labels.reviewed_rows(conn, fx.ORG_B)
        everyone = await labels.reviewed_rows(conn, None)
    assert [r["turn_id"] for r in a] == ["turn-r200000000000001"] and [r["turn_id"] for r in b] == ["turn-r200000000000002"]
    assert {r["turn_id"] for r in everyone} == {"turn-r200000000000001", "turn-r200000000000002"}


async def test_a_right_verdict_counts_but_a_cleared_one_does_not(lab_scratch, main_scratch):
    await _answer(lab_scratch, main_scratch, "turn-r300000000000001", verdict="right")
    await _answer(lab_scratch, main_scratch, "turn-r300000000000002", tombstone=True)
    async with lab_scratch.connect() as conn:
        rows = await labels.reviewed_rows(conn, fx.ORG_A)
    assert [r["turn_id"] for r in rows] == ["turn-r300000000000001"]       # a tombstone is no verdict


async def test_the_window_and_the_batch_narrow_the_rows(lab_scratch, main_scratch):
    await _answer(lab_scratch, main_scratch, "turn-r400000000000001", minutes_ago=300)
    await _answer(lab_scratch, main_scratch, "turn-r400000000000002", minutes_ago=100)
    await _answer(lab_scratch, main_scratch, "turn-r400000000000003", minutes_ago=10)
    async with lab_scratch.begin() as conn:
        batch = await fx.add_batch_item(conn, "turn-r400000000000002", fx.ORG_A, strategy="diversity",
                                        plan_step="cluster_representatives")
    async with lab_scratch.connect() as conn:
        since = await labels.reviewed_rows(conn, fx.ORG_A, since=NOW - timedelta(minutes=200))
        until = await labels.reviewed_rows(conn, fx.ORG_A, until=NOW - timedelta(minutes=200))
        window = await labels.reviewed_rows(conn, fx.ORG_A, since=NOW - timedelta(minutes=200),
                                            until=NOW - timedelta(minutes=50))
        batched = await labels.reviewed_rows(conn, fx.ORG_A, batch_id=batch)
        nobatch = await labels.reviewed_rows(conn, fx.ORG_A, batch_id="not-a-uuid")
        stranger = await labels.reviewed_rows(conn, fx.ORG_A, batch_id="00000000-0000-4000-8000-000000000000")
        everything = await labels.reviewed_rows(conn, fx.ORG_A)
    assert [r["turn_id"] for r in since] == ["turn-r400000000000003", "turn-r400000000000002"]
    assert [r["turn_id"] for r in until] == ["turn-r400000000000001"]
    assert [r["turn_id"] for r in window] == ["turn-r400000000000002"]
    assert [r["turn_id"] for r in batched] == ["turn-r400000000000002"]
    assert (batched[0]["batch_id"], batched[0]["plan_step"], batched[0]["strategy"]) == (batch, "cluster_representatives", "diversity")
    assert nobatch == [] and stranger == []
    assert {r["turn_id"]: r["batch_id"] for r in everything}["turn-r400000000000002"] == batch


async def test_golden_answers_and_answers_with_no_company_are_left_out(lab_scratch, main_scratch):
    await _answer(lab_scratch, main_scratch, "turn-r500000000000001")
    async with lab_scratch.begin() as conn:                            # a golden answer carrying a verdict
        await fx.add_note(conn, "turn-g500000000000001", fx.ORG_A)
    async with lab_scratch.connect() as conn:
        rows = await labels.reviewed_rows(conn, fx.ORG_A)
    assert [r["turn_id"] for r in rows] == ["turn-r500000000000001"]


async def test_nothing_reviewed_is_an_empty_list(lab_scratch, main_scratch):
    async with lab_scratch.connect() as conn:
        assert await labels.reviewed_rows(conn, fx.ORG_A) == []
