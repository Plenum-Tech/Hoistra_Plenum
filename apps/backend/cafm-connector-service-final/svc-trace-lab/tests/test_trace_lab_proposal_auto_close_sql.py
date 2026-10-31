"""The proposal helpers the label and mode writes lean on (spec 7): a human decision, a rescope, a retire or a merge
closes the pending proposals it makes stale; the grid's dashed cells and the per-mode pending counts read the pending
ones. Lab database only; needs the scratch database."""
from __future__ import annotations

import uuid

from sqlalchemy import text

import trace_lab_m4_fixtures as fx
from tl_scratch import lab_scratch, requires_scratch  # noqa: F401
from trace_lab import proposals

pytestmark = requires_scratch
T1, T2 = "turn-c100000000000001", "turn-c100000000000002"


def test_the_kinds_line_up_with_the_job_kinds_and_the_stored_proposal_kinds():
    from trace_lab import jobs

    assert proposals.KINDS == ("grouping", "depth_scan", "cells")
    assert proposals.PROPOSAL_KIND == {"grouping": "group", "depth_scan": "depth_scan", "cells": "cell"}
    assert proposals.JOB_KIND == {"grouping": "proposal_grouping", "depth_scan": "proposal_depth_scan",
                                  "cells": "proposal_cells"}
    assert set(proposals.JOB_KIND.values()) <= set(jobs.KINDS)
    assert proposals.TURN_KINDS == ("depth_scan", "cell")


async def _status(lab, pid):
    async with lab.connect() as conn:
        return (await conn.execute(text("SELECT status, closed_reason FROM tracelab.proposals WHERE id = CAST(:p AS uuid)"),
                                   {"p": pid})).mappings().first()


async def _world(lab):
    async with lab.begin() as conn:
        mid = await fx.add_mode(conn, "counts_cancelled_jobs_as_open", status="confirmed")
        other = await fx.add_mode(conn, "includes_cancelled_jobs", status="confirmed")
        cells = await fx.add_run(conn, "cells", mode_id=mid)
        depth = await fx.add_run(conn, "depth_scan", mode_id=mid)
        group = await fx.add_run(conn, "grouping")
        ids = {
            "cell": await fx.add_proposal(conn, cells, turn_id=T1, mode_id=mid),
            "depth": await fx.add_proposal(conn, depth, kind="depth_scan", turn_id=T1, mode_id=mid),
            "elsewhere": await fx.add_proposal(conn, cells, turn_id=T2, mode_id=mid),
            "other_mode": await fx.add_proposal(conn, cells, turn_id=T1, mode_id=other),
            "group": await fx.add_proposal(conn, group, kind="group", suggestion="file_under", mode_id=mid,
                                           annotation_ids=[str(uuid.uuid4())]),
        }
    return mid, other, ids


async def test_closing_a_cell_closes_its_cell_and_depth_proposals_only(lab_scratch):
    mid, _other, ids = await _world(lab_scratch)
    async with lab_scratch.begin() as conn:
        n = await proposals.close_for_cell(conn, T1, mid, "human_decision_exists")
    assert n == 2
    for key in ("cell", "depth"):
        assert dict(await _status(lab_scratch, ids[key])) == {"status": "closed", "closed_reason": "human_decision_exists"}
    for key in ("elsewhere", "other_mode", "group"):
        assert (await _status(lab_scratch, ids[key]))["status"] == "pending"


async def test_closing_a_cell_can_keep_the_one_being_decided(lab_scratch):
    mid, _other, ids = await _world(lab_scratch)
    async with lab_scratch.begin() as conn:
        n = await proposals.close_for_cell(conn, T1, mid, "human_decision_exists", keep=ids["cell"])
    assert n == 1 and (await _status(lab_scratch, ids["cell"]))["status"] == "pending"
    assert (await _status(lab_scratch, ids["depth"]))["status"] == "closed"


async def test_closing_a_mode_closes_every_pending_proposal_on_it_even_a_grouping(lab_scratch):
    mid, _other, ids = await _world(lab_scratch)
    async with lab_scratch.begin() as conn:
        n = await proposals.close_for_mode(conn, mid, "mode_changed")
    assert n == 4
    for key in ("cell", "depth", "elsewhere", "group"):
        assert dict(await _status(lab_scratch, ids[key])) == {"status": "closed", "closed_reason": "mode_changed"}
    assert (await _status(lab_scratch, ids["other_mode"]))["status"] == "pending"


async def test_a_decided_proposal_is_never_reopened_or_re_closed(lab_scratch):
    mid, _other, ids = await _world(lab_scratch)
    async with lab_scratch.begin() as conn:
        await conn.execute(text("UPDATE tracelab.proposals SET status = 'dismissed' WHERE id = CAST(:p AS uuid)"),
                           {"p": ids["cell"]})
        n = await proposals.close_for_mode(conn, mid, "mode_changed")
    assert n == 3 and (await _status(lab_scratch, ids["cell"]))["status"] == "dismissed"


async def test_an_edited_or_deleted_note_closes_the_groupings_that_cite_it(lab_scratch):
    note = str(uuid.uuid4())
    async with lab_scratch.begin() as conn:
        group = await fx.add_run(conn, "grouping")
        cites = await fx.add_proposal(conn, group, kind="group", suggestion="no_mode_fits", annotation_ids=[note, str(uuid.uuid4())])
        other = await fx.add_proposal(conn, group, kind="group", suggestion="no_mode_fits", annotation_ids=[str(uuid.uuid4())])
    async with lab_scratch.begin() as conn:
        n = await proposals.close_for_annotation(conn, note)
    assert n == 1
    assert dict(await _status(lab_scratch, cites)) == {"status": "closed", "closed_reason": "superseded"}
    assert (await _status(lab_scratch, other))["status"] == "pending"


async def test_pending_cells_are_the_grids_dashed_cells(lab_scratch):
    mid, other, ids = await _world(lab_scratch)
    async with lab_scratch.connect() as conn:
        pending = await proposals.pending_cells(conn, [T1, T2], [mid, other])
        none_a = await proposals.pending_cells(conn, [], [mid])
        none_b = await proposals.pending_cells(conn, [T1], [])
    assert set(pending) == {(T1, mid), (T2, mid), (T1, other)}            # depth-scan and grouping rows are not cells
    assert pending[(T1, mid)] == {"id": ids["cell"], "suggestion": "present",
                                  "evidence": "The answer counts cancelled jobs as open.", "quote": ""}
    assert none_a == {} and none_b == {}


async def test_pending_counts_per_mode_for_a_company_or_the_platform(lab_scratch):
    mid, other, _ids = await _world(lab_scratch)
    async with lab_scratch.begin() as conn:
        run = await fx.add_run(conn, "cells", mode_id=mid, org=fx.ORG_B)
        await fx.add_proposal(conn, run, turn_id="turn-c200000000000001", mode_id=mid, org=fx.ORG_B)
        canary = await fx.add_proposal(conn, run, turn_id="turn-c200000000000002", mode_id=mid, org=fx.ORG_B, is_canary=True)
    assert canary
    async with lab_scratch.connect() as conn:
        everyone = await proposals.pending_counts(conn, None)
        company_a = await proposals.pending_counts(conn, fx.ORG_A)
        company_b = await proposals.pending_counts(conn, fx.ORG_B)
    assert everyone == {mid: 4, other: 1}                       # depth + cell ones, never a grouping, never a canary
    assert company_a == {mid: 3, other: 1} and company_b == {mid: 1}
