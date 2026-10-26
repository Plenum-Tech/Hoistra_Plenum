"""Grouping proposals are the platform owner's: accepting file_under files every note as a human_open_code label,
new_mode creates a candidate from the notes with the owner's edits, no_mode_fits sets the notes aside; dismiss files
nothing; a company admin is refused (spec §7.2, §5.3). Needs the scratch database."""
from __future__ import annotations

import pytest
from sqlalchemy import text

import trace_lab_m4_fixtures as fx
from tl_scratch import lab_scratch, main_scratch, requires_scratch  # noqa: F401
from trace_lab import annotations, proposals
from trace_lab.review_errors import Forbidden

pytestmark = requires_scratch
DRAFT = {"name": "counts_cancelled_jobs_as_open", "definition": "Present when the answer counts cancelled jobs as open.",
         "boundary": "Not when the job is on hold.", "nearest_mode": "", "nearest_mode_id": None,
         "level": "technical", "agent": "wo_engine"}


async def _two_notes(lab, main):
    notes = []
    async with main.begin() as conn:
        for t in ("turn-q000000000000001", "turn-q000000000000002"):
            await fx.add_turn(conn, t)
    async with lab.begin() as conn:
        for t in ("turn-q000000000000001", "turn-q000000000000002"):
            notes.append(await fx.add_note(conn, t))
        run = await fx.add_run(conn, "grouping")
    return notes, run


async def test_accepting_file_under_files_every_note(lab_scratch, main_scratch):
    notes, run = await _two_notes(lab_scratch, main_scratch)
    async with lab_scratch.begin() as conn:
        mid = await fx.add_mode(conn, "counts_cancelled_jobs_as_open")
        pid = await fx.add_proposal(conn, run, kind="group", suggestion="file_under", mode_id=mid, annotation_ids=notes)
    out = await proposals.decide(fx.owner(), None, pid, decision="accept")
    assert out["proposal"]["status"] == "accepted" and len(out["labels"]) == 2
    assert {lab["source"] for lab in out["labels"]} == {"human_open_code"}
    assert (await annotations.unfiled_notes(fx.owner(), None))["notes"] == []


async def test_accepting_a_new_mode_creates_a_candidate_with_the_owners_edits(lab_scratch, main_scratch):
    notes, run = await _two_notes(lab_scratch, main_scratch)
    async with lab_scratch.begin() as conn:
        pid = await fx.add_proposal(conn, run, kind="group", suggestion="new_mode", annotation_ids=notes, draft=DRAFT)
    out = await proposals.decide(fx.owner(), None, pid, decision="accept",
                                 edits={"name": "counts_closed_jobs_as_open", "level": "data"})
    assert out["mode"]["name"] == "counts_closed_jobs_as_open" and out["mode"]["level"] == "data"
    assert out["mode"]["status"] == "candidate" and out["labels_written"] == 2
    assert out["proposal"]["flags"]["created_mode_id"] == out["mode"]["id"]


async def test_accepting_no_mode_fits_disposes_the_notes(lab_scratch, main_scratch):
    notes, run = await _two_notes(lab_scratch, main_scratch)
    async with lab_scratch.begin() as conn:
        pid = await fx.add_proposal(conn, run, kind="group", suggestion="no_mode_fits", annotation_ids=notes,
                                    evidence="Too vague to group.")
    out = await proposals.decide(fx.owner(), None, pid, decision="accept")
    assert [e["detail"]["outcome"] for e in out["events"]] == ["no_mode_fits", "no_mode_fits"]
    assert (await annotations.unfiled_notes(fx.owner(), None))["notes"] == []


async def test_dismiss_files_nothing(lab_scratch, main_scratch):
    notes, run = await _two_notes(lab_scratch, main_scratch)
    async with lab_scratch.begin() as conn:
        pid = await fx.add_proposal(conn, run, kind="group", suggestion="new_mode", annotation_ids=notes, draft=DRAFT)
    out = await proposals.decide(fx.owner(), None, pid, decision="dismiss")
    assert out["proposal"]["status"] == "dismissed"
    async with lab_scratch.connect() as conn:
        assert (await conn.execute(text("SELECT count(*) FROM tracelab.modes"))).scalar() == 0
    assert len((await annotations.unfiled_notes(fx.owner(), None))["notes"]) == 2


async def test_a_company_admin_cannot_decide_or_list_groupings(lab_scratch, main_scratch):
    notes, run = await _two_notes(lab_scratch, main_scratch)
    async with lab_scratch.begin() as conn:
        pid = await fx.add_proposal(conn, run, kind="group", suggestion="no_mode_fits", annotation_ids=notes)
    with pytest.raises(Forbidden):
        await proposals.decide(fx.admin(), fx.ORG_A, pid, decision="accept")
    with pytest.raises(Forbidden):
        await proposals.queue(fx.admin(), fx.ORG_A, kind="group")
    listed = await proposals.queue(fx.owner(), None, kind="group")
    assert [p["id"] for p in listed["proposals"]] == [pid] and len(listed["proposals"][0]["notes"]) == 2
