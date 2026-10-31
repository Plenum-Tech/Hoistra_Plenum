"""The five auto-close triggers of spec §7.7, plus the blind rule on suggestions (§2.3). A decision, a merge, a retire
and a deleted note each close the proposals they make stale; a decided cell is never proposed again; a suggestion on an
answer the caller has not judged yet stays hidden, and cannot be decided either. Needs the scratch database."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

import trace_lab_m4_fixtures as fx
from tl_scratch import lab_scratch, main_scratch, requires_scratch  # noqa: F401
from trace_lab import annotations, labels, proposals, taxonomy
from trace_lab.review_errors import NotFound

pytestmark = requires_scratch
TURN = "turn-p000000000000001"


async def _status(lab, pid):
    async with lab.connect() as conn:
        r = (await conn.execute(text("SELECT status, closed_reason FROM tracelab.proposals "
                                     "WHERE id = CAST(:p AS uuid)"), {"p": pid})).mappings().first()
    return r["status"], r["closed_reason"]


async def test_1_a_decision_on_a_cell_closes_the_other_suggestion_on_it(lab_scratch, main_scratch):
    async with main_scratch.begin() as conn:
        await fx.add_turn(conn, TURN)
    async with lab_scratch.begin() as conn:
        mid = await fx.add_mode(conn, "counts_cancelled_jobs_as_open", status="confirmed")
        cell = await fx.add_proposal(conn, await fx.add_run(conn, "cells"), turn_id=TURN, mode_id=mid, suggestion="absent")
        depth = await fx.add_proposal(conn, await fx.add_run(conn, "depth_scan", mode_id=mid), kind="depth_scan",
                                      turn_id=TURN, mode_id=mid)
    out = await proposals.decide(fx.owner(), None, cell, decision="accept")
    assert out["proposal"]["status"] == "accepted"
    assert await _status(lab_scratch, depth) == ("closed", "human_decision_exists")


async def test_2_a_merge_closes_the_pending_proposals_of_every_mode_involved(lab_scratch, main_scratch):
    async with lab_scratch.begin() as conn:
        target = await fx.add_mode(conn, "counts_cancelled_jobs_as_open")
        source = await fx.add_mode(conn, "includes_cancelled_jobs")
        run = await fx.add_run(conn, "cells")
        p1 = await fx.add_proposal(conn, run, turn_id=TURN, mode_id=target)
        p2 = await fx.add_proposal(conn, run, turn_id=TURN, mode_id=source)
    await taxonomy.merge(fx.owner(), [source], into=target, new=None, base_versions={source: 1, target: 1})
    assert await _status(lab_scratch, p1) == ("closed", "mode_changed")
    assert await _status(lab_scratch, p2) == ("closed", "mode_changed")


async def test_3_a_retire_closes_the_modes_pending_proposals(lab_scratch, main_scratch):
    async with lab_scratch.begin() as conn:
        mid = await fx.add_mode(conn, "counts_cancelled_jobs_as_open")
        pid = await fx.add_proposal(conn, await fx.add_run(conn, "cells"), turn_id=TURN, mode_id=mid)
    await taxonomy.retire(fx.owner(), mid, reason="not seen again", base_version=1)
    assert await _status(lab_scratch, pid) == ("closed", "mode_changed")


async def test_4_deleting_a_note_closes_grouping_proposals_that_cite_it(lab_scratch, main_scratch):
    async with main_scratch.begin() as conn:
        await fx.add_turn(conn, TURN)
    async with lab_scratch.begin() as conn:
        note = await fx.add_note(conn, TURN)
        pid = await fx.add_proposal(conn, await fx.add_run(conn, "grouping"), kind="group", suggestion="no_mode_fits",
                                    annotation_ids=[note])
    await annotations.delete_verdict(fx.admin(), fx.ORG_A, TURN, base=note)
    assert await _status(lab_scratch, pid) == ("closed", "superseded")


async def test_5_generation_skips_cells_a_person_already_decided(lab_scratch, main_scratch, monkeypatch):
    monkeypatch.setenv("TRACE_LAB_JUDGE_MODEL", "claude-sonnet-5")
    turns = ("turn-p100000000000001", "turn-p100000000000002")
    async with main_scratch.begin() as conn:
        for t in turns:
            await fx.add_turn(conn, t)
            await fx.add_span(conn, t, output=[{"open": 12}])
    async with lab_scratch.begin() as conn:
        for t in turns:
            await fx.add_note(conn, t, verdict="right")
        mid = await fx.add_mode(conn, "counts_cancelled_jobs_as_open", status="confirmed")
    await labels.set_label(fx.owner(), None, turns[0], mid, present=True, base=None)
    now = datetime.now(timezone.utc)
    est = await proposals.estimate(fx.owner(), kind="cells", mode_id=None, since=now - timedelta(days=1),
                                   until=now + timedelta(hours=1))
    assert est["items"] == 1


async def test_suggestions_stay_hidden_on_a_blind_turn(lab_scratch, main_scratch):
    async with main_scratch.begin() as conn:
        await fx.add_turn(conn, TURN)
    async with lab_scratch.begin() as conn:
        await fx.add_note(conn, TURN)
        mid = await fx.add_mode(conn, "counts_cancelled_jobs_as_open", status="confirmed")
        pid = await fx.add_proposal(conn, await fx.add_run(conn, "depth_scan", mode_id=mid), kind="depth_scan",
                                    turn_id=TURN, mode_id=mid, quote="12 open", field="answer")
    blind = await proposals.queue(fx.admin(n=2), fx.ORG_A, kind="suggestion", turn_id=TURN)
    assert blind["proposals"] == [] and blind["hidden"] == 1
    with pytest.raises(NotFound) as e:
        await proposals.decide(fx.admin(n=2), fx.ORG_A, pid, decision="accept")
    assert e.value.reason == "proposal_not_found"
    seen = await proposals.queue(fx.admin(), fx.ORG_A, kind="suggestion", turn_id=TURN)
    assert [p["id"] for p in seen["proposals"]] == [pid] and seen["proposals"][0]["quote"] == "12 open"
    assert seen["served_at"]
