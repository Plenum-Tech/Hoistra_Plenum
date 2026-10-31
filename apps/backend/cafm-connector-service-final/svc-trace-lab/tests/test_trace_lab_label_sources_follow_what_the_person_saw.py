"""A pending cell proposal is assumed seen: clicking its value is accepted_label_proposal, the other value is a
human override that dismisses it; a pending depth-scan suggestion on the cell just closes (spec 6.2). Needs the scratch
database."""
from __future__ import annotations

import pytest
from sqlalchemy import text

import trace_lab_m4_fixtures as fx
from tl_scratch import lab_scratch, main_scratch, requires_scratch  # noqa: F401
from trace_lab import labels
from trace_lab.review_errors import Invalid

pytestmark = requires_scratch
TURN = "turn-b000000000000001"


async def _with_pending(lab, main, *, kind="cell", suggestion="absent", flags=None):
    async with main.begin() as conn:
        await fx.add_turn(conn, TURN)
    async with lab.begin() as conn:
        mid = await fx.add_mode(conn, "counts_cancelled_jobs_as_open", status="confirmed")
        run = await fx.add_run(conn, "cells" if kind == "cell" else "depth_scan", mode_id=mid)
        pid = await fx.add_proposal(conn, run, kind=kind, turn_id=TURN, mode_id=mid, suggestion=suggestion, flags=flags)
    return mid, pid


async def _proposal(lab, pid):
    async with lab.connect() as conn:
        return (await conn.execute(text("SELECT status, closed_reason, CAST(label_id AS text) AS label_id, "
                                        "decided_by_email FROM tracelab.proposals WHERE id = CAST(:p AS uuid)"),
                                   {"p": pid})).mappings().first()


async def test_clicking_the_suggested_value_accepts_the_proposal(lab_scratch, main_scratch):
    mid, pid = await _with_pending(lab_scratch, main_scratch, suggestion="absent")
    out = await labels.set_label(fx.admin(), fx.ORG_A, TURN, mid, present=False, base=None)
    assert out["label"]["source"] == "accepted_label_proposal" and out["label"]["proposal_id"] == pid
    p = await _proposal(lab_scratch, pid)
    assert p["status"] == "accepted" and p["label_id"] == out["label"]["id"] and p["decided_by_email"] == fx.admin().email


async def test_clicking_the_other_value_is_a_human_override_that_dismisses(lab_scratch, main_scratch):
    mid, pid = await _with_pending(lab_scratch, main_scratch, suggestion="absent")
    out = await labels.set_label(fx.admin(), fx.ORG_A, TURN, mid, present=True, base=None)
    assert out["label"]["source"] == "human_structured_label"
    p = await _proposal(lab_scratch, pid)
    assert p["status"] == "dismissed" and p["label_id"] == out["label"]["id"]


async def test_clearing_a_cell_does_not_decide_its_proposal_but_it_does_close_it(lab_scratch, main_scratch):
    mid, pid = await _with_pending(lab_scratch, main_scratch, suggestion="present")
    out = await labels.set_label(fx.admin(), fx.ORG_A, TURN, mid, present=None, base=None)
    assert out["label"]["tombstone"] is True and out["label"]["source"] == "human_structured_label"
    p = await _proposal(lab_scratch, pid)
    assert (p["status"], p["closed_reason"]) == ("closed", "human_decision_exists")


async def test_a_pending_depth_suggestion_on_the_cell_just_closes(lab_scratch, main_scratch):
    mid, pid = await _with_pending(lab_scratch, main_scratch, kind="depth_scan", suggestion="present")
    out = await labels.set_label(fx.admin(), fx.ORG_A, TURN, mid, present=True, base=None)
    assert out["label"]["source"] == "human_structured_label"
    p = await _proposal(lab_scratch, pid)
    assert (p["status"], p["closed_reason"]) == ("closed", "human_decision_exists")


async def test_the_suggestion_is_hidden_in_a_spot_check_and_the_decision_is_the_persons_own(lab_scratch, main_scratch):
    mid, pid = await _with_pending(lab_scratch, main_scratch, suggestion="present", flags={"spot_check_seed": "seed-7"})
    out = await labels.set_label(fx.admin(), fx.ORG_A, TURN, mid, present=True, base=None, spot_check_seed="seed-7")
    assert out["label"]["source"] == "human_structured_label"
    assert out["label"]["basis"] == {"spot_check": {"seed": "seed-7", "proposal_id": pid, "kind": "cell"}}
    assert (await _proposal(lab_scratch, pid))["status"] == "closed"


async def test_a_spot_check_needs_a_pending_item_with_that_seed_and_a_value(lab_scratch, main_scratch):
    mid, _pid = await _with_pending(lab_scratch, main_scratch, flags={"spot_check_seed": "seed-7"})
    with pytest.raises(Invalid) as e:
        await labels.set_label(fx.admin(), fx.ORG_A, TURN, mid, present=True, base=None, spot_check_seed="other")
    assert e.value.reason == "not_in_spot_check"
    with pytest.raises(Invalid) as e:
        await labels.set_label(fx.admin(), fx.ORG_A, TURN, mid, present=None, base=None, spot_check_seed="seed-7")
    assert e.value.reason == "not_in_spot_check"
    assert (await labels.history(fx.owner(), None, TURN, mid))["chain"] == []


async def test_a_label_on_a_cell_with_no_proposal_is_a_plain_human_label(lab_scratch, main_scratch):
    async with main_scratch.begin() as conn:
        await fx.add_turn(conn, TURN)
    async with lab_scratch.begin() as conn:
        mid = await fx.add_mode(conn, "counts_cancelled_jobs_as_open", status="confirmed")
    out = await labels.set_label(fx.admin(), fx.ORG_A, TURN, mid, present=True, base=None)
    assert out["label"]["source"] == "human_structured_label" and out["label"]["proposal_id"] is None


# ── a person decides one proposal (proposals.decide, spec §7.7) ──────────────────────────

from trace_lab import proposals  # noqa: E402


async def test_accepting_a_depth_suggestion_writes_a_present_accepted_suggestion(lab_scratch, main_scratch):
    mid, pid = await _with_pending(lab_scratch, main_scratch, kind="depth_scan", suggestion="present")
    out = await proposals.decide(fx.owner(), None, pid, decision="accept")
    assert (out["label"]["source"], out["label"]["present"]) == ("accepted_suggestion", True)
    assert out["proposal"]["status"] == "accepted"
    assert out["label"]["proposal_id"] == pid and out["proposal"]["label_id"] == out["label"]["id"]


async def test_dismissing_a_depth_suggestion_records_a_close_negative(lab_scratch, main_scratch):
    mid, pid = await _with_pending(lab_scratch, main_scratch, kind="depth_scan", suggestion="present")
    out = await proposals.decide(fx.owner(), None, pid, decision="dismiss")
    assert (out["label"]["source"], out["label"]["present"]) == ("rejected_suggestion", False)
    assert out["proposal"]["status"] == "dismissed"


async def test_overriding_a_cell_proposal_is_a_human_label(lab_scratch, main_scratch):
    mid, pid = await _with_pending(lab_scratch, main_scratch, suggestion="absent")
    out = await proposals.decide(fx.owner(), None, pid, decision="override", present=True)
    assert out["label"]["source"] == "human_structured_label" and out["proposal"]["status"] == "dismissed"


async def test_a_spot_check_decision_is_human_and_closes_the_hidden_suggestion(lab_scratch, main_scratch):
    mid, pid = await _with_pending(lab_scratch, main_scratch, suggestion="absent")
    async with lab_scratch.begin() as conn:
        await conn.execute(text("UPDATE tracelab.proposals SET flags = '{\"spot_check_seed\": \"abcd\"}'::jsonb "
                                "WHERE id = CAST(:p AS uuid)"), {"p": pid})
    out = await labels.set_label(fx.admin(), fx.ORG_A, TURN, mid, present=False, base=None, spot_check_seed="abcd")
    assert out["label"]["source"] == "human_structured_label"
    assert out["label"]["basis"]["spot_check"]["seed"] == "abcd"
    p = await _proposal(lab_scratch, pid)
    assert (p["status"], p["closed_reason"]) == ("closed", "human_decision_exists")
