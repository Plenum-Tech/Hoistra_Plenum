"""What a proposer is shown about one answer, read across the two databases (the chat's rows on the read-only main engine,
the company's evidence snapshots in the lab database): the answer and its steps, the two turns before it in an OBSERVED
conversation of the same company, and nothing of another company's. Skips without TRACE_LAB_TEST_DB_URL."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import text

import trace_lab_m4_fixtures as fx
from tl_scratch import lab_scratch, main_scratch, requires_scratch  # noqa: F401
from trace_lab import evidence, normalize, proposals

pytestmark = requires_scratch
NOW = datetime.now(timezone.utc).replace(microsecond=0)


async def _thread(conn, session_id, org=fx.ORG_A):
    await conn.execute(text(
        "INSERT INTO plenum_cafm.chat_threads (id, organization_id, user_id, title) "
        "VALUES (:id, CAST(:org AS uuid), CAST(:uid AS uuid), 'A chat')"),
        {"id": session_id, "org": org, "uid": fx.OWNER_ID})


async def _conversation(main, session="sess-obs", n=4, org=fx.ORG_A, prefix="turn-e0"):
    async with main.begin() as conn:
        for i in range(n):
            await fx.add_turn(conn, f"{prefix}{i:014d}", org, session_id=session, started_at=NOW - timedelta(minutes=10 - i),
                              question=f"Question {i}?", answer=f"Answer {i}.")
            await fx.add_span(conn, f"{prefix}{i:014d}", org, output=[{"n": i}])
    return [f"{prefix}{i:014d}" for i in range(n)]


async def test_an_observed_conversation_gives_the_two_turns_before_the_answer(lab_scratch, main_scratch):
    turns = await _conversation(main_scratch)
    async with main_scratch.begin() as conn:
        await _thread(conn, "sess-obs")
    got = await proposals.load_evidence([turns[3], turns[0]], organization_id=fx.ORG_A)
    assert [e["turn_id"] for e in got[turns[3]]["earlier"]] == [turns[1], turns[2]]    # two, oldest first, not later ones
    assert got[turns[0]]["earlier"] == []                                                       # the first turn has none
    assert len(got[turns[3]]["spans"]) == 1
    text_, locations = normalize.evidence_text(got[turns[3]]["turn"], got[turns[3]]["spans"], got[turns[3]]["earlier"])
    assert text_.startswith("EARLIER IN THIS CONVERSATION\nQ: Question 1?\nA: Answer 1.\nQ: Question 2?\nA: Answer 2.")
    assert "Question 0?" not in text_ and locations["answer"]["text"] == "Answer 3."


async def test_a_conversation_nobody_opened_in_the_chat_gives_no_earlier_turns(lab_scratch, main_scratch):
    turns = await _conversation(main_scratch, session="sess-unseen")                          # no chat thread row
    got = await proposals.load_evidence([turns[3]], organization_id=fx.ORG_A)
    assert got[turns[3]]["earlier"] == []


async def test_an_app_made_session_gives_no_earlier_turns_even_with_a_thread(lab_scratch, main_scratch):
    turns = await _conversation(main_scratch, session="report-monthly")
    async with main_scratch.begin() as conn:
        await _thread(conn, "report-monthly")
    assert (await proposals.load_evidence([turns[3]], organization_id=fx.ORG_A))[turns[3]]["earlier"] == []


async def test_another_companys_turns_are_never_read_or_shown(lab_scratch, main_scratch):
    mine = await _conversation(main_scratch, session="sess-shared", n=2, prefix="turn-e1")
    theirs = await _conversation(main_scratch, session="sess-shared", n=3, org=fx.ORG_B, prefix="turn-e2")
    async with main_scratch.begin() as conn:
        await _thread(conn, "sess-shared", fx.ORG_A)
    got = await proposals.load_evidence([mine[1], theirs[2]], organization_id=fx.ORG_A)
    assert set(got) == {mine[1]}                                                                 # their answer is not returned
    assert [e["turn_id"] for e in got[mine[1]]["earlier"]] == [mine[0]]                  # nor their earlier turns


async def test_a_purged_answer_comes_back_from_its_snapshot_and_a_lost_one_is_absent(lab_scratch, main_scratch):
    turns = await _conversation(main_scratch, n=2)
    assert await evidence.snapshot([turns[1]], organization_id=fx.ORG_A, reason="proposed") == 1
    async with main_scratch.begin() as conn:
        await conn.execute(text("DELETE FROM plenum_cafm.agent_trace_spans WHERE turn_id = ANY(CAST(:t AS text[]))"), {"t": turns})
        await conn.execute(text("DELETE FROM plenum_cafm.agent_trace_turns WHERE turn_id = ANY(CAST(:t AS text[]))"), {"t": turns})
    got = await proposals.load_evidence(turns + ["turn-nothing-0000000"], organization_id=fx.ORG_A)
    assert set(got) == {turns[1]}                                                                # kept: the snapshot; the rest are gone
    assert got[turns[1]]["turn"]["answer"] == "Answer 1." and len(got[turns[1]]["spans"]) == 1


async def test_nothing_to_load_reads_nothing():
    assert await proposals.load_evidence([], organization_id=fx.ORG_A) == {}
    assert await proposals.load_evidence(["", None], organization_id=fx.ORG_A) == {}
