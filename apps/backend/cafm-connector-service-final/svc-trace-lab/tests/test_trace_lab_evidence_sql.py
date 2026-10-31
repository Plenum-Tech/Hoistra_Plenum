"""Evidence snapshots on the scratch Postgres: the chat's turn and spans are read through the READ-ONLY main
engine and copied into Trace Lab's evidence tables through the lab engine, once, for the right company only; when
the chat's spans are purged, load answers from the snapshot. Skips without TRACE_LAB_TEST_DB_URL."""
from __future__ import annotations

import asyncio
import json
import uuid

import pytest
from sqlalchemy import text

from src.agents import trace
from tl_scratch import lab_scratch, main_scratch, requires_scratch  # noqa: F401
from trace_lab import db, evidence
from trace_lab_m2_fixtures import add_span, add_turn, truncate_chat, turn_id

pytestmark = requires_scratch

ORG = str(uuid.uuid4())
OTHER = str(uuid.uuid4())


@pytest.fixture
async def both(main_scratch, lab_scratch):  # noqa: F811
    """(writable main engine for seeding, lab engine). Evidence code reaches them only through db.*_engine()."""
    return main_scratch, lab_scratch


async def _count(engine, table: str) -> int:
    async with engine.connect() as conn:
        return (await conn.execute(text(f"SELECT count(*) FROM tracelab.{table}"))).scalar()


async def _seed(main, n: int = 1, org: str = ORG, spans: int = 2) -> str:
    tid = await add_turn(main, n, org)
    for i in range(1, spans + 1):
        await add_span(main, tid, seq=i, name=f"step-{i}", output={"step": i}, org=org)
    return tid


async def test_snapshot_copies_the_turn_and_all_its_spans(both):
    main, lab = both
    tid = await _seed(main, 1, spans=3)
    assert await evidence.snapshot([tid], organization_id=ORG, reason="sampled") == 1
    async with lab.connect() as conn:
        row = (await conn.execute(text("SELECT organization_id::text, reason, turn FROM tracelab.evidence_turns "
                                       "WHERE turn_id = :t"), {"t": tid})).one()
        spans = (await conn.execute(text("SELECT span_id, seq, span FROM tracelab.evidence_spans "
                                         "WHERE turn_id = :t ORDER BY seq"), {"t": tid})).all()
    assert row[0] == ORG and row[1] == "sampled"
    turn = row[2] if isinstance(row[2], dict) else json.loads(row[2])
    assert turn["turn_id"] == tid and turn["organization_id"] == ORG and turn["question"] == "How many work orders?"
    assert turn["answer"] == "There were 758 work orders in August." and turn["models"] == ["m"]
    assert turn["route"] == "single:wo_engine" and turn["input_tokens"] == 1000 and turn["ok"] is True
    assert [s[1] for s in spans] == [1, 2, 3]
    bodies = [s[2] if isinstance(s[2], dict) else json.loads(s[2]) for s in spans]
    assert [b["name"] for b in bodies] == ["step-1", "step-2", "step-3"] and bodies[2]["output"] == {"step": 3}
    assert all(s[0] == b["id"] and b["turn_id"] == tid and b["organization_id"] == ORG for s, b in zip(spans, bodies))


async def test_a_second_snapshot_copies_nothing_and_changes_nothing(both):
    main, lab = both
    tid = await _seed(main, 1)
    assert await evidence.snapshot([tid], organization_id=ORG, reason="sampled") == 1
    async with main.begin() as conn:                       # the live row changes afterwards (a feedback rating, say)
        await conn.execute(text("UPDATE plenum_cafm.agent_trace_turns SET answer = 'changed later'"))
    assert await evidence.snapshot([tid], organization_id=ORG, reason="annotated") == 0
    assert (await _count(lab, "evidence_turns"), await _count(lab, "evidence_spans")) == (1, 2)
    async with lab.connect() as conn:
        row = (await conn.execute(text("SELECT reason, turn->>'answer' FROM tracelab.evidence_turns"))).one()
    assert tuple(row) == ("sampled", "There were 758 work orders in August.")     # the first capture stands


async def test_two_callers_snapshotting_at_once_store_the_turn_once(both):
    main, lab = both
    tid = await _seed(main, 1)
    first, second = await asyncio.gather(*(evidence.snapshot([tid], organization_id=ORG, reason="sampled")
                                           for _ in range(2)))
    assert sorted((first, second)) == [0, 1]
    assert (await _count(lab, "evidence_turns"), await _count(lab, "evidence_spans")) == (1, 2)


async def test_a_turn_of_another_company_is_refused_and_a_missing_turn_skipped(both):
    main, lab = both
    mine, theirs = await _seed(main, 1), await _seed(main, 2, org=OTHER)
    assert await evidence.snapshot([theirs, "turn-does-not-exist", mine], organization_id=ORG, reason="sampled") == 1
    async with lab.connect() as conn:
        rows = (await conn.execute(text("SELECT turn_id FROM tracelab.evidence_turns"))).all()
        stray = (await conn.execute(text("SELECT count(*) FROM tracelab.evidence_spans WHERE turn_id = :t"),
                                    {"t": theirs})).scalar()
    assert [r[0] for r in rows] == [mine] and stray == 0
    assert await evidence.snapshot([theirs], organization_id=ORG, reason="sampled") == 0
    assert await evidence.snapshot([theirs], organization_id=OTHER, reason="sampled") == 1   # their own admin may


async def test_a_turn_with_no_company_is_never_copied(both):
    main, lab = both
    tid = await add_turn(main, 5, None)
    assert await evidence.snapshot([tid], organization_id=ORG, reason="sampled") == 0
    assert await _count(lab, "evidence_turns") == 0


async def test_load_reads_live_rows_while_the_chat_still_has_them(both):
    main, _ = both
    tid = await _seed(main, 1)
    await evidence.snapshot([tid], organization_id=ORG, reason="sampled")
    got = await evidence.load([tid, "turn-nope"], organization_id=ORG)
    assert list(got) == [tid] and got[tid].source == "live"
    assert got[tid].turn["question"] == "How many work orders?" and [s["name"] for s in got[tid].spans] == ["step-1", "step-2"]


async def test_after_the_chat_purges_its_spans_load_returns_the_snapshot(both):
    main, _ = both
    tid = await _seed(main, 1)
    live = (await evidence.load([tid], organization_id=ORG))[tid]
    assert live.source == "live" and len(live.spans) == 2
    await evidence.snapshot([tid], organization_id=ORG, reason="annotated")
    async with main.begin() as conn:                       # svc-deepagents' retention purge: spans go, the turn row stays
        await conn.execute(text("DELETE FROM plenum_cafm.agent_trace_spans"))
    after = (await evidence.load([tid], organization_id=ORG))[tid]
    assert after.source == "snapshot" and after.turn == live.turn and after.spans == live.spans
    async with main.begin() as conn:                       # and when the whole turn row is gone as well
        await conn.execute(text("DELETE FROM plenum_cafm.agent_trace_turns"))
    gone = (await evidence.load([tid], organization_id=ORG))[tid]
    assert gone.source == "snapshot" and gone.spans == live.spans


async def test_a_turn_never_snapshotted_and_purged_is_absent(both):
    main, _ = both
    tid = await _seed(main, 1)
    async with main.begin() as conn:
        await conn.execute(text("DELETE FROM plenum_cafm.agent_trace_spans"))
        await conn.execute(text("DELETE FROM plenum_cafm.agent_trace_turns"))
    assert await evidence.load([tid], organization_id=ORG) == {}


async def test_load_never_returns_another_companys_rows_live_or_snapshotted(both):
    main, _ = both
    theirs = await _seed(main, 2, org=OTHER)
    await evidence.snapshot([theirs], organization_id=OTHER, reason="sampled")
    assert await evidence.load([theirs], organization_id=ORG) == {}                    # live row exists, snapshot exists
    async with main.begin() as conn:
        await conn.execute(text("DELETE FROM plenum_cafm.agent_trace_spans"))
        await conn.execute(text("DELETE FROM plenum_cafm.agent_trace_turns"))
    assert await evidence.load([theirs], organization_id=ORG) == {}                    # snapshot only
    assert list(await evidence.load([theirs], organization_id=OTHER)) == [theirs]


async def test_span_fields_are_copied_as_they_are_in_the_chat_and_never_cut_again(both):
    main, _ = both
    tid = await add_turn(main, 1, ORG, answer="a" * trace.BOUND)
    big_in, big_out = "i" * trace.BOUND, {"text": "o" * trace.BOUND, "unicode": "é" * 5000}
    await add_span(main, tid, seq=1, inp={"prompt": big_in}, output=big_out, org=ORG)
    await evidence.snapshot([tid], organization_id=ORG, reason="sampled")
    async with main.begin() as conn:
        await conn.execute(text("DELETE FROM plenum_cafm.agent_trace_spans"))
    got = (await evidence.load([tid], organization_id=ORG))[tid]
    assert got.source == "snapshot" and len(got.turn["answer"]) == trace.BOUND
    assert got.spans[0]["input"] == {"prompt": big_in} and got.spans[0]["output"] == big_out
    assert len(got.spans[0]["output"]["text"]) == trace.BOUND >= 12_000


async def test_many_turns_are_captured_in_chunks(both):
    main, lab = both
    ids = [await add_turn(main, n, ORG) for n in range(1, evidence.CHUNK + 6)]
    assert await evidence.snapshot(ids, organization_id=ORG, reason="sampled") == len(ids)
    assert await evidence.snapshot(ids, organization_id=ORG, reason="sampled") == 0
    assert await _count(lab, "evidence_turns") == len(ids)


async def test_the_application_database_is_only_ever_read(both):
    main, _ = both
    tid = await _seed(main, 1)
    async with db.main_engine().connect() as conn:
        assert (await conn.execute(text("SHOW default_transaction_read_only"))).scalar() == "on"
    await evidence.snapshot([tid], organization_id=ORG, reason="sampled")
    await evidence.load([tid], organization_id=ORG)
    async with main.connect() as conn:
        assert (await conn.execute(text("SELECT count(*) FROM plenum_cafm.agent_trace_turns"))).scalar() == 1
        assert (await conn.execute(text("SELECT count(*) FROM plenum_cafm.agent_trace_spans"))).scalar() == 2
        assert (await conn.execute(text("SELECT answer FROM plenum_cafm.agent_trace_turns"))).scalar() == \
            "There were 758 work orders in August."


async def test_the_fixture_helpers_agree_with_the_real_tables(both):
    main, _ = both
    assert turn_id(255) == "turn-00000000000000ff"
    await _seed(main, 1)
    await truncate_chat(main)
    async with main.connect() as conn:
        assert (await conn.execute(text("SELECT count(*) FROM plenum_cafm.agent_trace_turns"))).scalar() == 0
