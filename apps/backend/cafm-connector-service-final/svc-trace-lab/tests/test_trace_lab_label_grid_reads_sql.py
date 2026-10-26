"""The label reads the proposals and the judges stand on, against a scratch database: decided_cells (with the answer's
company and session even after the chat purged it), human_origin_cells (R10, R16, live modes only), the fair-sample
statistics, undecided cells left after a bulk write, the answers that resolve present, and the modes an answer is
present for. Needs the scratch database."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

import trace_lab_m4_fixtures as fx
from tl_scratch import lab_scratch, main_scratch, requires_scratch  # noqa: F401
from trace_lab import evidence, labels, taxonomy

pytestmark = requires_scratch
LATER = datetime.now(timezone.utc) + timedelta(hours=1)          # after every mode's rescoped_at (the database's now())


def _at(minutes: int) -> datetime:
    return LATER + timedelta(minutes=minutes)


def _tid(group: str, i: int) -> str:
    return f"turn-{group}{i:015d}"


async def _turns(main, ids, org=fx.ORG_A):
    async with main.begin() as conn:
        for t in ids:
            await fx.add_turn(conn, t, org, session_id=f"sess-{t}")


async def test_decided_cells_carry_their_provenance_in_decided_order(lab_scratch, main_scratch):
    t1, t2, t3, t4 = (_tid("r", i) for i in range(1, 5))
    await _turns(main_scratch, [t1, t2, t3, t4])
    await evidence.snapshot([t3], organization_id=fx.ORG_A, reason="labelled")
    async with main_scratch.begin() as conn:                                      # the chat purged answer 3
        await conn.execute(text("DELETE FROM plenum_cafm.agent_trace_turns WHERE turn_id = :t"), {"t": t3})
    async with lab_scratch.begin() as conn:
        note = await fx.add_note(conn, t2)
        m = await fx.add_mode(conn, "counts_cancelled_jobs_as_open", status="confirmed")
        part = await fx.add_mode(conn, "includes_cancelled_jobs", status="retired", merged_into=m)
        await fx.add_label(conn, t3, m, present=False, created_at=_at(1))
        await fx.add_label(conn, t1, m, present=True, quote="12 open", note="Counts July.", created_at=_at(2))
        await fx.add_label(conn, t2, part, present=True, source="human_open_code", annotation_id=note, created_at=_at(3))
    first = await labels.set_label(fx.owner(), None, t4, m, present=True, base=None)       # answer 4 is decided, then cleared
    await labels.set_label(fx.owner(), None, t4, m, present=None, base=first["label"]["id"])
    async with lab_scratch.connect() as conn:
        graph = await taxonomy.load_graph(conn)
        cells = await labels.decided_cells(conn, graph, graph.modes[m])
    assert [c["turn_id"] for c in cells] == [t3, t1, t2]                          # oldest decision first; the clear is no cell
    absent, present, inherited = cells
    assert (absent["present"], absent["label"], absent["inherited"]) == (False, 0, False)
    assert (absent["organization_id"], absent["session_id"]) == (fx.ORG_A, f"sess-{t3}")      # from the evidence snapshot
    assert (present["quote"], present["note"], present["human_origin"], present["human_written"]) == (
        "12 open", "Counts July.", True, True)
    assert present["organization_id"] == fx.ORG_A and present["session_id"] == f"sess-{t1}"
    assert (inherited["inherited"], inherited["via_mode_id"], inherited["source"]) == (True, part, "human_open_code")
    assert inherited["decided_by_email"] == "reviewer@x.test" and inherited["decided_at"]


async def test_only_a_live_judge_eligible_mode_gives_human_origin_cells(lab_scratch, main_scratch):
    t = _tid("r", 10)
    await _turns(main_scratch, [t])
    async with lab_scratch.begin() as conn:
        live = await fx.add_mode(conn, "counts_cancelled_jobs_as_open", status="candidate")
        retired = await fx.add_mode(conn, "includes_cancelled_jobs", status="retired")
        proposed = await fx.add_mode(conn, "proposed_by_a_company", status="proposed")
        await fx.add_label(conn, t, live, present=True, created_at=_at(1))
        await fx.add_label(conn, t, retired, present=True, created_at=_at(1))
    assert [c["turn_id"] for c in await labels.human_origin_cells(live)] == [t]           # a candidate is live
    for gone in (retired, proposed, str(uuid.uuid4()), "not-an-id", ""):
        assert await labels.human_origin_cells(gone) == []


async def test_the_fair_sample_statistics_use_uniform_human_decisions_only(lab_scratch, main_scratch):
    u = [_tid("s", i) for i in range(1, 5)]
    other, withdrawn = _tid("s", 8), _tid("s", 9)
    await _turns(main_scratch, u + [other, withdrawn])
    async with lab_scratch.begin() as conn:
        for t in u:
            await fx.add_note(conn, t)
            await fx.add_batch_item(conn, t, strategy="uniform", plan_step="initial_uniform")
        await fx.add_note(conn, other)
        await fx.add_batch_item(conn, other, strategy="diversity", plan_step="cluster_representatives")
        await fx.add_note(conn, withdrawn)
        w_batch = await fx.add_batch_item(conn, withdrawn, strategy="uniform", plan_step="extra")
        m = await fx.add_mode(conn, "counts_cancelled_jobs_as_open", status="confirmed")
        gone = await fx.add_mode(conn, "includes_cancelled_jobs", status="retired")
        run = await fx.add_run(conn, "cells", org=fx.ORG_A)
        p3 = await fx.add_proposal(conn, run, kind="cell", turn_id=u[2], mode_id=m, status="accepted")
        await fx.add_label(conn, u[0], m, present=True, created_at=_at(1))
        await fx.add_label(conn, u[1], m, present=False, created_at=_at(1))
        await fx.add_label(conn, u[2], m, present=True, source="accepted_label_proposal", approval="bulk",
                           proposal_id=p3, created_at=_at(1))
        await fx.add_label(conn, other, m, present=True, created_at=_at(1))
        await fx.add_label(conn, withdrawn, m, present=True, created_at=_at(1))
        await fx.add_label(conn, u[0], gone, present=True, created_at=_at(1))
        await conn.execute(text("UPDATE tracelab.sample_batches SET withdrawn_at = now() WHERE id = CAST(:b AS uuid)"),
                           {"b": w_batch})
    async with lab_scratch.connect() as conn:
        out = await labels.uniform_human_stats(conn, fx.ORG_A, [m, m, gone, str(uuid.uuid4())])
        none = await labels.uniform_human_stats(conn, fx.ORG_B, [m])
    _rate, low, high = labels._wilson(1, 2)
    assert out == {m: {"n": 2, "present": 1, "low": low, "high": high}}          # no retired mode, no stranger, no duplicate
    assert none == {m: {"n": 0, "present": 0, "low": None, "high": None}}


async def test_undecided_cells_left_after_a_bulk_write_are_counted(lab_scratch, main_scratch):
    a, b = _tid("t", 1), _tid("t", 2)
    async with lab_scratch.begin() as conn:
        m1 = await fx.add_mode(conn, "counts_cancelled_jobs_as_open", status="confirmed")
        m2 = await fx.add_mode(conn, "invents_asset_codes", status="frozen")
        await fx.add_mode(conn, "only_a_candidate")
        await fx.add_label(conn, a, m1, present=True, created_at=_at(1))
        await fx.add_label(conn, b, m2, present=True, org=fx.ORG_B, created_at=_at(1))
    async with lab_scratch.connect() as conn:
        assert await labels.undecided_after(conn, None, [], set()) == 0
        assert await labels.undecided_after(conn, None, [a, b], set()) == 2             # (a, m2) and (b, m1); a candidate needs none
        assert await labels.undecided_after(conn, None, [a, b], {(a, m2), (b, m1)}) == 0
        assert await labels.undecided_after(conn, None, [a, b], {(a, m2)}) == 1
        assert await labels.undecided_after(conn, fx.ORG_A, [a, b], set()) == 3          # B's label is not this company's decision


async def test_the_answers_that_resolve_present_are_listed_for_live_modes_only(lab_scratch, main_scratch):
    t = [_tid("u", i) for i in range(1, 6)]
    async with lab_scratch.begin() as conn:
        m = await fx.add_mode(conn, "counts_cancelled_jobs_as_open", status="confirmed")
        part = await fx.add_mode(conn, "includes_cancelled_jobs", status="retired", merged_into=m)
        await fx.add_label(conn, t[3], m, present=True, created_at=_at(1))
        await fx.add_label(conn, t[1], m, present=True, created_at=_at(1))
        await fx.add_label(conn, t[2], m, present=False, created_at=_at(1))
        await fx.add_label(conn, t[0], part, present=True, created_at=_at(1))             # present through the merged part
        await fx.add_label(conn, t[4], m, present=True, org=fx.ORG_B, created_at=_at(1))
    async with lab_scratch.connect() as conn:
        assert await labels.present_turn_ids(conn, m, None) == sorted([t[0], t[1], t[3], t[4]])
        assert await labels.present_turn_ids(conn, m, fx.ORG_A) == sorted([t[0], t[1], t[3]])
        assert await labels.present_turn_ids(conn, m, fx.ORG_B) == [t[4]]
        assert await labels.present_turn_ids(conn, part, None) == []                       # retired: no cells of its own
        assert await labels.present_turn_ids(conn, str(uuid.uuid4()), None) == []


async def test_an_answer_is_present_for_live_modes_only(lab_scratch, main_scratch):
    t = _tid("v", 1)
    async with lab_scratch.begin() as conn:
        m1 = await fx.add_mode(conn, "counts_cancelled_jobs_as_open", status="confirmed")
        m2 = await fx.add_mode(conn, "invents_asset_codes")
        m3 = await fx.add_mode(conn, "includes_cancelled_jobs", status="retired")
        await fx.add_label(conn, t, m1, present=True, created_at=_at(1))
        await fx.add_label(conn, t, m2, present=False, created_at=_at(1))
        await fx.add_label(conn, t, m3, present=True, created_at=_at(1))
    async with lab_scratch.connect() as conn:
        assert await labels.turn_present_modes(conn, t) == [m1]
        assert await labels.turn_present_modes(conn, _tid("v", 2)) == []
