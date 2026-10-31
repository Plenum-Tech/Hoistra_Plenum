"""File under writes a human_open_code label and refuses to contradict a human absent unless told; dispose and
undispose move a note in and out of the unfiled list; an edited note stays filed while a label cites its lineage;
editing a note closes grouping proposals citing the old version; other reviewers' unfiled notes stay hidden on a
turn you have not judged (spec §6.5, §7.7 rule 4, §2.3). Needs the scratch database."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

import trace_lab_m4_fixtures as fx
from tl_scratch import lab_scratch, main_scratch, requires_scratch  # noqa: F401
from trace_lab import annotations, labels
from trace_lab.review_errors import Conflict, Invalid

pytestmark = requires_scratch
TURN = "turn-k000000000000001"


async def _note_and_mode(lab, main):
    async with main.begin() as conn:
        await fx.add_turn(conn, TURN)
    async with lab.begin() as conn:
        note = await fx.add_note(conn, TURN)
        mid = await fx.add_mode(conn, "counts_cancelled_jobs_as_open", status="confirmed")
    return note, mid


async def _unfiled_ids(principal, org):
    return [n["annotation_id"] for n in (await annotations.unfiled_notes(principal, org))["notes"]]


async def test_file_under_writes_an_open_code_label_and_files_the_note(lab_scratch, main_scratch):
    note, mid = await _note_and_mode(lab_scratch, main_scratch)
    assert await _unfiled_ids(fx.admin(), fx.ORG_A) == [note]
    out = await annotations.file_note(fx.admin(), fx.ORG_A, note, mid, base=None)
    assert out["label"]["source"] == "human_open_code" and out["label"]["annotation_id"] == note
    assert out["cell"]["state"] == "present"
    assert await _unfiled_ids(fx.admin(), fx.ORG_A) == []


async def test_file_under_refuses_to_contradict_a_human_absent_unless_overridden(lab_scratch, main_scratch):
    note, mid = await _note_and_mode(lab_scratch, main_scratch)
    absent = await labels.set_label(fx.admin(n=2), fx.ORG_A, TURN, mid, present=False, base=None)
    with pytest.raises(Conflict) as e:
        await annotations.file_note(fx.admin(), fx.ORG_A, note, mid, base=absent["label"]["id"])
    assert e.value.reason == "conflicts_with_label"
    out = await annotations.file_note(fx.admin(), fx.ORG_A, note, mid, base=absent["label"]["id"], override_label=True)
    assert out["cell"]["state"] == "present"


async def test_dispose_and_undispose_move_the_note_out_and_back(lab_scratch, main_scratch):
    note, _mid = await _note_and_mode(lab_scratch, main_scratch)
    await annotations.dispose_note(fx.owner(), note, outcome="no_mode_fits", note="Too vague to group.")
    assert await _unfiled_ids(fx.admin(), fx.ORG_A) == []
    await annotations.undispose_note(fx.owner(), note)
    assert await _unfiled_ids(fx.admin(), fx.ORG_A) == [note]


async def test_not_a_failure_is_refused_while_the_answer_resolves_present(lab_scratch, main_scratch):
    note, mid = await _note_and_mode(lab_scratch, main_scratch)
    await labels.set_label(fx.admin(n=2), fx.ORG_A, TURN, mid, present=True, base=None)
    with pytest.raises(Invalid) as e:
        await annotations.dispose_note(fx.owner(), note, outcome="not_a_failure", note="The answer was right.")
    assert e.value.reason == "not_a_failure_while_present"


async def test_an_edited_note_stays_filed_while_a_label_cites_its_lineage(lab_scratch, main_scratch):
    note, mid = await _note_and_mode(lab_scratch, main_scratch)
    await annotations.file_note(fx.admin(), fx.ORG_A, note, mid, base=None)
    v2 = str(uuid.uuid4())
    async with lab_scratch.begin() as conn:
        await conn.execute(text("UPDATE tracelab.annotations SET superseded_by = CAST(:n AS uuid) "
                                "WHERE id = CAST(:o AS uuid)"), {"n": v2, "o": note})
        await conn.execute(text(
            "INSERT INTO tracelab.annotations (id, lineage_id, version, supersedes, kind, organization_id, "
            "conversation_key, turn_id, reviewer_id, reviewer_email, verdict, note, field, quote, quote_occurrence, "
            "quote_start, blind) SELECT CAST(:n AS uuid), lineage_id, 2, id, kind, organization_id, conversation_key, "
            "turn_id, reviewer_id, reviewer_email, 'wrong', 'Counts cancelled and on-hold jobs.', field, quote, "
            "quote_occurrence, quote_start, false FROM tracelab.annotations WHERE id = CAST(:o AS uuid)"),
            {"n": v2, "o": note})
    assert await _unfiled_ids(fx.admin(), fx.ORG_A) == []


async def test_editing_a_note_closes_grouping_proposals_that_cite_its_old_version(lab_scratch, main_scratch):
    note, _mid = await _note_and_mode(lab_scratch, main_scratch)
    async with lab_scratch.begin() as conn:
        run = await fx.add_run(conn, "grouping")
        pid = await fx.add_proposal(conn, run, kind="group", suggestion="no_mode_fits", annotation_ids=[note])
    await annotations.save_verdict(fx.admin(), fx.ORG_A, TURN, verdict="wrong", note="Counts cancelled and on-hold jobs.",
                                   anchor=annotations.Anchor(span_id=None, field="answer", quote="12 open", occurrence=1),
                                   base=note)
    async with lab_scratch.connect() as conn:
        row = (await conn.execute(text("SELECT status, closed_reason FROM tracelab.proposals "
                                       "WHERE id = CAST(:p AS uuid)"), {"p": pid})).mappings().first()
    assert (row["status"], row["closed_reason"]) == ("closed", "superseded")


async def test_other_reviewers_unfiled_notes_stay_hidden_until_you_judge(lab_scratch, main_scratch):
    note, _mid = await _note_and_mode(lab_scratch, main_scratch)
    seen = await annotations.unfiled_notes(fx.admin(n=2), fx.ORG_A)
    assert seen["notes"] == [] and seen["hidden"] == 1
    assert [n["annotation_id"] for n in (await annotations.unfiled_notes(fx.owner(), None))["notes"]] == [note]
