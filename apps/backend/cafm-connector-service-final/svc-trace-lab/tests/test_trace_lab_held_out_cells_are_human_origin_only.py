"""human_origin_cells (the only label source for judge dev and test splits): human-origin only (R10), bulk never,
inherited cells flagged, data-fault modes give nothing (R16). Needs the scratch database."""
from __future__ import annotations

import trace_lab_m4_fixtures as fx
from tl_scratch import lab_scratch, main_scratch, requires_scratch  # noqa: F401
from trace_lab import labels

pytestmark = requires_scratch
T = [f"turn-j0000000000000{i:02d}" for i in range(1, 7)]


async def test_held_out_cells_follow_r10_and_flag_inherited_ones(lab_scratch, main_scratch):
    async with main_scratch.begin() as conn:
        for t in T:
            await fx.add_turn(conn, t)
    async with lab_scratch.begin() as conn:
        m = await fx.add_mode(conn, "counts_cancelled_jobs_as_open", status="confirmed")
        part = await fx.add_mode(conn, "includes_cancelled_jobs", status="retired", merged_into=m)
        run = await fx.add_run(conn, "depth_scan", mode_id=m)
        p2 = await fx.add_proposal(conn, run, kind="depth_scan", turn_id=T[1], mode_id=m, status="accepted")
        p3 = await fx.add_proposal(conn, run, kind="cell", turn_id=T[2], mode_id=m, suggestion="absent", status="accepted")
        p5 = await fx.add_proposal(conn, run, kind="depth_scan", turn_id=T[4], mode_id=m, status="dismissed")
        await fx.add_label(conn, T[0], m, present=True)
        await fx.add_label(conn, T[1], m, present=True, source="accepted_suggestion", proposal_id=p2)
        await fx.add_label(conn, T[2], m, present=False, source="accepted_label_proposal", approval="bulk", proposal_id=p3)
        await fx.add_label(conn, T[3], part, present=True)
        await fx.add_label(conn, T[4], m, present=False, source="rejected_suggestion", proposal_id=p5)
        await fx.add_label(conn, T[5], m, present=True, org=fx.ORG_B)
    cells = {c["turn_id"]: c for c in await labels.human_origin_cells(m)}
    assert set(cells) == {T[0], T[1], T[3], T[4], T[5]}
    assert cells[T[3]]["inherited"] is True and cells[T[0]]["inherited"] is False
    assert cells[T[1]]["human_written"] is False and cells[T[0]]["human_written"] is True
    assert (cells[T[4]]["label"], cells[T[0]]["label"]) == (0, 1)
    assert set(c["turn_id"] for c in await labels.human_origin_cells(m, org=fx.ORG_B)) == {T[5]}


async def test_a_data_fault_mode_gives_no_judge_cells(lab_scratch, main_scratch):
    async with main_scratch.begin() as conn:
        await fx.add_turn(conn, T[0])
    async with lab_scratch.begin() as conn:
        m = await fx.add_mode(conn, "work_order_dates_missing_in_records", status="confirmed", level="data")
        await fx.add_label(conn, T[0], m, present=True)
    assert await labels.human_origin_cells(m) == []
