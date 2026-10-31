"""Filing, setting aside and listing reviewers' Wrong notes, beyond the happy paths: who may file what, a note edited or
deleted under the caller, modes that are not live, the evidence kept, the blind rule, dispositions that expire when a
note is edited, paging and text of the unfiled list, and grouping proposals closed by an edit or a delete. Needs the
scratch database."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

import trace_lab_m4_fixtures as fx
from tl_scratch import lab_scratch, main_scratch, requires_scratch  # noqa: F401
from trace_lab import annotations, evidence, labels, normalize
from trace_lab.review_errors import Conflict, Invalid, NotFound, Stale

pytestmark = requires_scratch
ANCHOR = annotations.Anchor(span_id=None, field="answer", quote="12 open", occurrence=1)


def _tid(i: int, group: str = "n") -> str:
    return f"turn-{group}{i:015d}"


async def _setup(lab, main, i=1, *, org=fx.ORG_A, reviewer=None, status="confirmed", name="counts_cancelled_jobs_as_open",
                 span=False, **turn):
    t = _tid(i)
    async with main.begin() as conn:
        await fx.add_turn(conn, t, org, session_id=f"sess-{t}", **turn)
        sid = await fx.add_span(conn, t, org, kind="tool", name="query_table", agent="udr", output={"rows": 12}) if span else None
    async with lab.begin() as conn:
        note = await fx.add_note(conn, t, org, reviewer=reviewer, span_id=sid, span_agent="udr" if span else None)
        mid = await fx.add_mode(conn, name, status=status)
    return t, note, mid


async def _rows(lab, sql, **params):
    async with lab.connect() as conn:
        return [dict(r) for r in (await conn.execute(text(sql), params)).mappings().all()]


# ── filing ────────────────────────────────────────────────────────────────────────────────

async def test_the_label_copies_the_note_its_quote_and_the_step_and_the_answers_evidence_is_kept(lab_scratch, main_scratch):
    t, note, mid = await _setup(lab_scratch, main_scratch, span=True)
    who = fx.admin()
    out = await annotations.file_note(who, fx.ORG_A, note, mid, base=None)
    label = out["label"]
    assert (label["source"], label["present"], label["approval"], label["annotation_id"]) == (
        "human_open_code", True, "single", note)
    assert (label["note"], label["quote"]) == ("The count includes cancelled jobs.", "12 open work orders")
    assert (label["span_name"], label["span_kind"], label["span_agent"]) == ("query_table", "tool", "udr")
    assert label["span_id"] and label["organization_id"] == fx.ORG_A and label["session_id"] == f"sess-{t}"
    assert (label["decided_by"], label["decided_by_email"]) == (str(who.user_id), who.email)
    assert out["cell"]["base"] == label["id"] and out["cell"]["source"] == "human_open_code"
    assert [r["turn_id"] for r in await _rows(lab_scratch, "SELECT turn_id FROM tracelab.evidence_turns")] == [t]
    assert len(await _rows(lab_scratch, "SELECT 1 FROM tracelab.evidence_spans")) == 1


async def test_a_note_on_an_answer_the_chat_has_purged_is_still_filed_from_its_snapshot(lab_scratch, main_scratch):
    t, note, mid = await _setup(lab_scratch, main_scratch)
    await evidence.snapshot([t], organization_id=fx.ORG_A, reason="annotated")
    async with main_scratch.begin() as conn:
        await conn.execute(text("DELETE FROM plenum_cafm.agent_trace_turns WHERE turn_id = :t"), {"t": t})
    out = await annotations.file_note(fx.admin(), fx.ORG_A, note, mid, base=None)
    assert out["cell"]["state"] == "present" and out["label"]["session_id"] == f"sess-{t}"


async def test_the_platform_owner_files_for_any_company_and_a_company_admin_only_for_their_own(lab_scratch, main_scratch):
    ta, na, mid = await _setup(lab_scratch, main_scratch, 1)
    tb, nb, _ = await _setup(lab_scratch, main_scratch, 2, org=fx.ORG_B, name="invents_asset_codes")
    with pytest.raises(NotFound) as e:
        await annotations.file_note(fx.admin(), fx.ORG_A, nb, mid, base=None)         # B's note, A's admin
    assert e.value.reason == "annotation_not_found"
    with pytest.raises(NotFound):
        await annotations.file_note(fx.admin(fx.ORG_B), fx.ORG_B, na, mid, base=None)
    with pytest.raises(NotFound):
        await annotations.file_note(fx.owner(), fx.ORG_A, nb, mid, base=None)         # acting for A, B's note
    out = await annotations.file_note(fx.owner(), None, nb, mid, base=None)           # the platform view reaches every company
    assert out["label"]["organization_id"] == fx.ORG_B and out["label"]["turn_id"] == tb
    with pytest.raises(NotFound):                                                     # acting for A, the blind rule applies
        await annotations.file_note(fx.owner(), fx.ORG_A, na, mid, base=None)
    await annotations.save_verdict(fx.owner(), fx.ORG_A, ta, verdict="right", note=None, anchor=None, base=None)
    assert (await annotations.file_note(fx.owner(), fx.ORG_A, na, mid, base=None))["label"]["turn_id"] == ta


@pytest.mark.parametrize("which,exc,reason", [
    ("unknown_note", NotFound, "annotation_not_found"), ("unknown_mode", NotFound, "mode_not_found"),
    ("right_verdict", Invalid, "not_a_wrong_note"), ("retired_mode", Conflict, "mode_not_live"),
    ("proposed_mode", Conflict, "mode_not_live")])
async def test_what_cannot_be_filed_is_refused_and_writes_nothing(lab_scratch, main_scratch, which, exc, reason):
    t, note, mid = await _setup(lab_scratch, main_scratch)
    async with lab_scratch.begin() as conn:
        retired = await fx.add_mode(conn, "includes_cancelled_jobs", status="retired", merged_into=mid)
        proposed = await fx.add_mode(conn, "proposed_by_a_company", status="proposed")
    note_id, mode_id = note, mid
    if which == "unknown_note":
        note_id = str(uuid.uuid4())
    elif which == "unknown_mode":
        mode_id = str(uuid.uuid4())
    elif which == "right_verdict":
        async with main_scratch.begin() as conn:
            await fx.add_turn(conn, _tid(2), fx.ORG_A)
        async with lab_scratch.begin() as conn:
            note_id = await fx.add_note(conn, _tid(2), fx.ORG_A, verdict="right")
    elif which == "retired_mode":
        mode_id = retired
    else:
        mode_id = proposed
    with pytest.raises(exc) as e:
        await annotations.file_note(fx.admin(), fx.ORG_A, note_id, mode_id, base=None)
    assert e.value.reason == reason
    if which == "retired_mode":
        assert e.value.extra["live_mode_id"] == mid                                      # the mode it was merged into
    assert await _rows(lab_scratch, "SELECT 1 FROM tracelab.labels") == []


async def test_a_note_edited_or_deleted_since_it_was_listed_is_a_409_with_the_live_version(lab_scratch, main_scratch):
    t, note, mid = await _setup(lab_scratch, main_scratch)
    edited = await annotations.save_verdict(fx.admin(), fx.ORG_A, t, verdict="wrong", note="Counts on-hold jobs too.",
                                            anchor=ANCHOR, base=note)
    with pytest.raises(Stale) as e:
        await annotations.file_note(fx.admin(), fx.ORG_A, note, mid, base=None)
    assert e.value.current == {"annotation_id": edited["id"]}
    assert (await annotations.file_note(fx.admin(), fx.ORG_A, edited["id"], mid, base=None))["cell"]["state"] == "present"
    t2, note2, _ = await _setup(lab_scratch, main_scratch, 2, name="invents_asset_codes")
    tomb = await annotations.delete_verdict(fx.admin(), fx.ORG_A, t2, base=note2)
    with pytest.raises(Stale) as e:
        await annotations.file_note(fx.admin(), fx.ORG_A, note2, mid, base=None)
    assert e.value.current == {"annotation_id": tomb["id"]}
    with pytest.raises(Invalid) as e2:                                                # the tombstone itself is not a note
        await annotations.file_note(fx.admin(), fx.ORG_A, tomb["id"], mid, base=None)
    assert e2.value.reason == "not_a_wrong_note"


async def test_a_stale_base_is_refused_with_the_current_label(lab_scratch, main_scratch):
    t, note, mid = await _setup(lab_scratch, main_scratch)
    head = await labels.set_label(fx.admin(n=2), fx.ORG_A, t, mid, present=True, base=None)
    with pytest.raises(Stale) as e:
        await annotations.file_note(fx.admin(), fx.ORG_A, note, mid, base=None)
    assert e.value.current["id"] == head["label"]["id"]
    out = await annotations.file_note(fx.admin(), fx.ORG_A, note, mid, base=head["label"]["id"])
    assert out["label"]["supersedes"] == head["label"]["id"] and out["cell"]["source"] == "human_open_code"
    assert len(await _rows(lab_scratch, "SELECT 1 FROM tracelab.labels WHERE superseded_by IS NULL")) == 1


async def test_only_a_persons_absent_stops_a_note_from_being_filed_not_an_ai_bulk_one(lab_scratch, main_scratch):
    t, note, mid = await _setup(lab_scratch, main_scratch)
    async with lab_scratch.begin() as conn:
        run = await fx.add_run(conn, "cells", org=fx.ORG_A)
        pid = await fx.add_proposal(conn, run, kind="cell", turn_id=t, mode_id=mid, suggestion="absent", status="accepted")
        bulk = await fx.add_label(conn, t, mid, present=False, source="accepted_label_proposal", approval="bulk",
                                  proposal_id=pid)
    out = await annotations.file_note(fx.admin(), fx.ORG_A, note, mid, base=bulk)
    assert out["cell"]["state"] == "present"


async def test_another_reviewers_note_cannot_be_filed_until_the_caller_has_judged_the_answer(lab_scratch, main_scratch):
    t, note, mid = await _setup(lab_scratch, main_scratch, reviewer=fx.admin(n=2))
    with pytest.raises(NotFound) as e:                                                # never confirm it exists
        await annotations.file_note(fx.admin(n=1), fx.ORG_A, note, mid, base=None)
    assert e.value.reason == "annotation_not_found" and await _rows(lab_scratch, "SELECT 1 FROM tracelab.labels") == []
    await annotations.save_verdict(fx.admin(n=1), fx.ORG_A, t, verdict="right", note=None, anchor=None, base=None)
    out = await annotations.file_note(fx.admin(n=1), fx.ORG_A, note, mid, base=None)
    assert out["label"]["annotation_id"] == note
    t2, note2, _ = await _setup(lab_scratch, main_scratch, 2, reviewer=fx.admin(n=2), name="invents_asset_codes")
    assert (await annotations.file_note(fx.owner(), None, note2, mid, base=None))["label"]["turn_id"] == t2


# ── setting aside ─────────────────────────────────────────────────────────────────────────

async def test_setting_a_note_aside_is_logged_and_refuses_what_is_not_a_live_wrong_note(lab_scratch, main_scratch):
    t, note, mid = await _setup(lab_scratch, main_scratch)
    long_note = "  " + "x" * 1500 + "  "
    out = await annotations.dispose_note(fx.owner(), note, outcome="no_mode_fits", note=long_note)
    assert out["event"]["action"] == "dispose" and out["event"]["detail"] == {"outcome": "no_mode_fits", "note": "x" * 1000}
    ev = (await _rows(lab_scratch, "SELECT * FROM tracelab.annotation_events WHERE action = 'dispose'"))[0]
    assert (str(ev["annotation_id"]), ev["turn_id"], str(ev["organization_id"]), ev["actor_email"]) == (
        note, t, fx.ORG_A, "owner@plenum.test")
    with pytest.raises(Invalid) as e:
        await annotations.dispose_note(fx.owner(), note, outcome="bogus", note="x")
    assert e.value.reason == "unknown_outcome"
    with pytest.raises(NotFound):
        await annotations.dispose_note(fx.owner(), "not-an-id", outcome="no_mode_fits", note="x")
    with pytest.raises(NotFound):
        await annotations.dispose_note(fx.owner(), str(uuid.uuid4()), outcome="no_mode_fits", note="x")
    async with main_scratch.begin() as conn:
        await fx.add_turn(conn, _tid(2), fx.ORG_A)
    async with lab_scratch.begin() as conn:
        right = await fx.add_note(conn, _tid(2), fx.ORG_A, verdict="right")
    with pytest.raises(Invalid) as e:
        await annotations.dispose_note(fx.owner(), right, outcome="no_mode_fits", note="x")
    assert e.value.reason == "not_a_wrong_note"
    assert len(await _rows(lab_scratch, "SELECT 1 FROM tracelab.annotation_events WHERE action = 'dispose'")) == 1


async def test_a_disposition_ends_when_the_note_is_edited_and_a_new_one_can_be_made(lab_scratch, main_scratch):
    t, note, _mid = await _setup(lab_scratch, main_scratch)
    await annotations.dispose_note(fx.owner(), note, outcome="no_mode_fits", note="Too vague.")
    assert [n["annotation_id"] for n in (await annotations.unfiled_notes(fx.admin(), fx.ORG_A))["notes"]] == []
    edited = await annotations.save_verdict(fx.admin(), fx.ORG_A, t, verdict="wrong", note="It counts on-hold jobs.",
                                            anchor=ANCHOR, base=note)
    with pytest.raises(Stale) as e:                                                   # the old version cannot be set aside again
        await annotations.dispose_note(fx.owner(), note, outcome="no_mode_fits", note="Again.")
    assert e.value.current == {"annotation_id": edited["id"]}
    listed = [n["annotation_id"] for n in (await annotations.unfiled_notes(fx.admin(), fx.ORG_A))["notes"]]
    assert listed == [edited["id"]]                                                   # the edit brought it back
    await annotations.dispose_note(fx.owner(), edited["id"], outcome="no_mode_fits", note="Still vague.")
    assert (await annotations.unfiled_notes(fx.admin(), fx.ORG_A))["notes"] == []
    await annotations.undispose_note(fx.owner(), edited["id"])
    await annotations.undispose_note(fx.owner(), edited["id"])                        # nothing to undo: harmless
    assert len((await annotations.unfiled_notes(fx.admin(), fx.ORG_A))["notes"]) == 1
    with pytest.raises(NotFound):
        await annotations.undispose_note(fx.owner(), str(uuid.uuid4()))


async def test_not_a_failure_is_allowed_when_no_mode_is_present_and_stops_when_one_is(lab_scratch, main_scratch):
    t, note, mid = await _setup(lab_scratch, main_scratch)
    await annotations.dispose_note(fx.owner(), note, outcome="not_a_failure", note="The answer was right.")
    await annotations.undispose_note(fx.owner(), note)
    first = await labels.set_label(fx.admin(n=2), fx.ORG_A, t, mid, present=True, base=None)
    with pytest.raises(Invalid) as e:
        await annotations.dispose_note(fx.owner(), note, outcome="not_a_failure", note="x")
    assert e.value.extra["modes"] == [mid]
    await labels.set_label(fx.admin(n=2), fx.ORG_A, t, mid, present=None, base=first["label"]["id"])    # cleared again
    await annotations.dispose_note(fx.owner(), note, outcome="not_a_failure", note="Cleared.")


# ── the unfiled list ──────────────────────────────────────────────────────────────────────

async def test_the_unfiled_list_is_oldest_first_paged_and_says_where_each_note_points(lab_scratch, main_scratch):
    notes = []
    for i in range(1, 5):
        _t, n, _m = await _setup(lab_scratch, main_scratch, i, name=f"mode_number_{i}", span=(i == 2))
        notes.append(n)
    page = await annotations.unfiled_notes(fx.admin(), fx.ORG_A)
    assert [n["annotation_id"] for n in page["notes"]] == notes and page["total"] == 4 and page["hidden"] == 0
    assert [n["step_label"] for n in page["notes"]] == ["Answer", "Records lookup", "Answer", "Answer"]
    first = page["notes"][0]
    assert set(first) == {"annotation_id", "lineage_id", "turn_id", "conversation_key", "reviewer_email", "note", "quote",
                          "span_name", "span_agent", "step_label", "created_at"}
    assert first["note"] == "The count includes cancelled jobs." and first["reviewer_email"] == fx.admin().email
    two = await annotations.unfiled_notes(fx.admin(), fx.ORG_A, limit=2, offset=1)
    assert [n["annotation_id"] for n in two["notes"]] == notes[1:3] and two["total"] == 4
    assert len((await annotations.unfiled_notes(fx.admin(), fx.ORG_A, limit=0, offset=-3))["notes"]) == 1


async def test_a_company_lists_its_own_unfiled_notes_and_the_platform_lists_every_companys(lab_scratch, main_scratch):
    _ta, na, _ = await _setup(lab_scratch, main_scratch, 1)
    _tb, nb, _ = await _setup(lab_scratch, main_scratch, 2, org=fx.ORG_B, name="invents_asset_codes")
    assert [n["annotation_id"] for n in (await annotations.unfiled_notes(fx.admin(), fx.ORG_A))["notes"]] == [na]
    assert [n["annotation_id"] for n in (await annotations.unfiled_notes(fx.admin(fx.ORG_B), fx.ORG_B))["notes"]] == [nb]
    assert {n["annotation_id"] for n in (await annotations.unfiled_notes(fx.owner(), None))["notes"]} == {na, nb}
    assert [n["annotation_id"] for n in (await annotations.unfiled_notes(fx.owner(), fx.ORG_B))["notes"]] == []   # not judged


async def test_unfiled_rows_carry_the_asked_for_text_repaired_redacted_and_cut(lab_scratch, main_scratch):
    token = "Bearer abcdefghijklmnop123456"
    wrapped = "Page context: Assets > Boilers" + normalize.QUESTION_MARKER + "How many open work orders?"
    t1, n1, _ = await _setup(lab_scratch, main_scratch, 1, question=wrapped, answer=f"Use {token} then " + "a" * 900)
    t2, n2, _ = await _setup(lab_scratch, main_scratch, 2, name="invents_asset_codes", question="q" * 1000)
    async with lab_scratch.connect() as conn:
        rows = {r["annotation_id"]: r for r in await annotations.unfiled_rows(conn, fx.ORG_A)}
        bare = await annotations.unfiled_rows(conn, fx.ORG_A, with_text=False)
        one = await annotations.unfiled_rows(conn, fx.ORG_A, turn_ids=[t2])
    assert rows[n1]["question"] == "How many open work orders?"
    assert "abcdefghijklmnop123456" not in rows[n1]["answer"] and rows[n1]["answer"].startswith("Use Bearer [redacted] then aaa")
    assert len(rows[n1]["answer"]) == 600 and len(rows[n2]["question"]) == 300
    assert all("question" not in r and "answer" not in r for r in bare) and len(bare) == 2
    assert [r["annotation_id"] for r in one] == [n2]


async def test_unfiled_text_falls_back_to_the_snapshot_and_never_crosses_companies(lab_scratch, main_scratch):
    t, note, _ = await _setup(lab_scratch, main_scratch, 1, question="Which boiler failed?", answer="Boiler 7 failed.")
    await evidence.snapshot([t], organization_id=fx.ORG_A, reason="annotated")
    async with main_scratch.begin() as conn:
        await conn.execute(text("DELETE FROM plenum_cafm.agent_trace_turns WHERE turn_id = :t"), {"t": t})
    async with main_scratch.begin() as conn:                                          # B's note on an answer that is A's
        await fx.add_turn(conn, _tid(2), fx.ORG_A, question="A's private question", answer="A's private answer")
    async with lab_scratch.begin() as conn:
        stray = await fx.add_note(conn, _tid(2), fx.ORG_B)
    async with lab_scratch.connect() as conn:
        rows = {r["annotation_id"]: r for r in await annotations.unfiled_rows(conn, None)}
    assert (rows[note]["question"], rows[note]["answer"]) == ("Which boiler failed?", "Boiler 7 failed.")
    assert (rows[stray]["question"], rows[stray]["answer"]) == ("", "")


# ── grouping proposals that cite a note ───────────────────────────────────────────────────

async def test_an_edit_or_a_delete_closes_only_the_grouping_proposals_that_cite_that_version(lab_scratch, main_scratch):
    t1, n1, _ = await _setup(lab_scratch, main_scratch, 1)
    t2, n2, _ = await _setup(lab_scratch, main_scratch, 2, name="invents_asset_codes")
    async with lab_scratch.begin() as conn:
        run = await fx.add_run(conn, "grouping")
        cites_1 = await fx.add_proposal(conn, run, kind="group", suggestion="no_mode_fits", annotation_ids=[n1])
        cites_2 = await fx.add_proposal(conn, run, kind="group", suggestion="no_mode_fits", annotation_ids=[n2])
        both = await fx.add_proposal(conn, run, kind="group", suggestion="no_mode_fits", annotation_ids=[n1, n2])
    await annotations.save_verdict(fx.admin(), fx.ORG_A, t1, verdict="wrong", note="Again wrong.", anchor=ANCHOR, base=n1)

    async def status(pid):
        return (await _rows(lab_scratch, "SELECT status, closed_reason FROM tracelab.proposals WHERE id = CAST(:p AS uuid)",
                            p=pid))[0]

    assert (await status(cites_1))["status"] == "closed" and (await status(both))["status"] == "closed"
    assert (await status(cites_1))["closed_reason"] == "superseded" and (await status(cites_2))["status"] == "pending"
    await annotations.delete_verdict(fx.admin(), fx.ORG_A, t2, base=n2)
    assert (await status(cites_2)) == {"status": "closed", "closed_reason": "superseded"}


async def test_a_first_verdict_closes_nothing(lab_scratch, main_scratch):
    t = _tid(1)
    async with main_scratch.begin() as conn:
        await fx.add_turn(conn, t, fx.ORG_A)
    async with lab_scratch.begin() as conn:
        other_turn_note = await fx.add_note(conn, _tid(9), fx.ORG_A, reviewer=fx.admin(n=2))
        run = await fx.add_run(conn, "grouping")
        pid = await fx.add_proposal(conn, run, kind="group", suggestion="no_mode_fits", annotation_ids=[other_turn_note])
    await annotations.save_verdict(fx.admin(), fx.ORG_A, t, verdict="wrong", note="It counts July.", anchor=ANCHOR, base=None)
    row = (await _rows(lab_scratch, "SELECT status FROM tracelab.proposals WHERE id = CAST(:p AS uuid)", p=pid))[0]
    assert row["status"] == "pending"
