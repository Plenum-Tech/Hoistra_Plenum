"""Blind review (spec 2.3) holds in the proposals queue. `list_proposals` shows the notes a proposal was made from, but
a company caller sees another reviewer's note (text, quote, reviewer email) only on an answer where the blind rule lets
them: they judged it themselves, the sample reached its review count, or they revealed it. The note is also kept when
the caller wrote it. This is the rule `get_mode` already applies to the same notes; the queue and the drawer must agree.
The platform owner reads every note. Needs the scratch database."""
from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import text

import trace_lab_m4_fixtures as fx
from tl_scratch import lab_scratch, main_scratch, requires_scratch  # noqa: F401
from trace_lab import taxonomy

pytestmark = requires_scratch
DRAFT = {"name": "counts_cancelled_jobs_as_open", "definition": "Present when the answer counts cancelled jobs as open.",
         "boundary": "", "level": "technical", "agent": "wo_engine"}
NOTE_TEXT = "The count includes cancelled jobs."
REVIEWER = fx.admin(fx.ORG_A, n=2)           # R: wrote the Wrong note
PROPOSER = fx.admin(fx.ORG_A, n=1)           # A: proposes a mode citing R's note
OTHER = fx.admin(fx.ORG_A, n=3)              # B: same company, has not judged the answer
TURN = "turn-b100000000000001"


async def _proposal_citing_the_reviewers_note(lab, main):
    async with main.begin() as conn:
        await fx.add_turn(conn, TURN, fx.ORG_A)
    async with lab.begin() as conn:
        note = await fx.add_note(conn, TURN, fx.ORG_A, reviewer=REVIEWER, note=NOTE_TEXT)
    mode = (await taxonomy.create(PROPOSER, DRAFT, created_from=[note], org=fx.ORG_A))["mode"]
    return mode, note


async def _row(who, org, mode_id, status="proposed"):
    return next(p for p in await taxonomy.list_proposals(who, org, status=status) if p["id"] == mode_id)


def _shows_nothing_of_the_note(row):
    blob = json.dumps(row, default=str)
    return row["notes"] == [] and NOTE_TEXT not in blob and REVIEWER.email not in blob


async def test_an_admin_who_has_not_judged_the_answer_does_not_see_the_cited_note(lab_scratch, main_scratch):
    mode, note = await _proposal_citing_the_reviewers_note(lab_scratch, main_scratch)
    for who, org in ((OTHER, fx.ORG_A), (OTHER, None), (fx.acting_superadmin(), fx.ORG_A)):
        row = await _row(who, org, mode["id"])
        assert _shows_nothing_of_the_note(row)
        assert row["created_from"] == [] and row["created_from_count"] == 1       # a count, never the note's id
    assert [n["annotation_id"] for n in (await _row(PROPOSER, fx.ORG_A, mode["id"]))["notes"]] == []


async def test_the_decided_proposal_hides_the_note_the_same_way(lab_scratch, main_scratch):
    mode, _note = await _proposal_citing_the_reviewers_note(lab_scratch, main_scratch)
    await taxonomy.accept_proposal(fx.owner(), mode["id"])
    for status in ("accepted", "all"):
        assert _shows_nothing_of_the_note(await _row(OTHER, fx.ORG_A, mode["id"], status))


async def test_the_reviewer_who_wrote_the_note_still_sees_it(lab_scratch, main_scratch):
    mode, note = await _proposal_citing_the_reviewers_note(lab_scratch, main_scratch)
    row = await _row(REVIEWER, fx.ORG_A, mode["id"])
    assert [n["annotation_id"] for n in row["notes"]] == [note]
    assert row["notes"][0]["note"] == NOTE_TEXT and row["notes"][0]["reviewer_email"] == REVIEWER.email
    assert row["created_from"] == [note]


async def test_the_reviewer_still_sees_their_own_note_after_withdrawing_the_verdict(lab_scratch, main_scratch):
    mode, note = await _proposal_citing_the_reviewers_note(lab_scratch, main_scratch)
    tomb = str(uuid.uuid4())
    async with lab_scratch.begin() as conn:               # R withdraws the verdict: the cited version stays on file
        await conn.execute(text("UPDATE tracelab.annotations SET superseded_by = CAST(:n AS uuid) "
                                "WHERE id = CAST(:o AS uuid)"), {"n": tomb, "o": note})
        await conn.execute(text(
            "INSERT INTO tracelab.annotations (id, lineage_id, version, supersedes, kind, organization_id, "
            "conversation_key, turn_id, reviewer_id, reviewer_email, tombstone) SELECT CAST(:n AS uuid), lineage_id, 2, "
            "id, kind, organization_id, conversation_key, turn_id, reviewer_id, reviewer_email, true "
            "FROM tracelab.annotations WHERE id = CAST(:o AS uuid)"), {"n": tomb, "o": note})
    mine = await _row(REVIEWER, fx.ORG_A, mode["id"])
    assert [n["annotation_id"] for n in mine["notes"]] == [note] and mine["created_from"] == [note]
    assert _shows_nothing_of_the_note(await _row(OTHER, fx.ORG_A, mode["id"]))


async def test_the_note_shows_once_the_caller_has_judged_the_answer_too(lab_scratch, main_scratch):
    mode, note = await _proposal_citing_the_reviewers_note(lab_scratch, main_scratch)
    assert _shows_nothing_of_the_note(await _row(OTHER, fx.ORG_A, mode["id"]))
    async with lab_scratch.begin() as conn:
        await fx.add_note(conn, TURN, fx.ORG_A, reviewer=OTHER, verdict="right")
    row = await _row(OTHER, fx.ORG_A, mode["id"])
    assert [n["annotation_id"] for n in row["notes"]] == [note] and row["created_from"] == [note]
    assert row["notes"][0]["reviewer_email"] == REVIEWER.email


async def test_the_platform_owner_reads_every_note(lab_scratch, main_scratch):
    mode, note = await _proposal_citing_the_reviewers_note(lab_scratch, main_scratch)
    row = await _row(fx.owner(), None, mode["id"])
    assert [n["annotation_id"] for n in row["notes"]] == [note] and row["notes"][0]["note"] == NOTE_TEXT
    assert row["created_from"] == [note]


async def test_the_queue_and_the_drawer_show_the_same_notes(lab_scratch, main_scratch):
    mode, note = await _proposal_citing_the_reviewers_note(lab_scratch, main_scratch)

    async def both(who, org):
        queue = [n["annotation_id"] for n in (await _row(who, org, mode["id"]))["notes"]]
        drawer = [n["annotation_id"] for n in (await taxonomy.get_mode(who, org, mode["id"]))["notes"]]
        return queue, drawer

    assert await both(OTHER, fx.ORG_A) == ([], [])
    assert await both(REVIEWER, fx.ORG_A) == ([note], [note])
    async with lab_scratch.begin() as conn:
        await fx.add_note(conn, TURN, fx.ORG_A, reviewer=OTHER, verdict="right")
    assert await both(OTHER, fx.ORG_A) == ([note], [note])
