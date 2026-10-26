"""One person, one proposal (proposals.decide): the label and the decision are one transaction; a stale decision, a
changed cell, a hidden suggestion, another company's, a known-answer check and a plain user are all refused without
writing anything; grouping decisions stay with the platform owner. Needs the scratch database."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

import trace_lab_m4_fixtures as fx
from tl_scratch import lab_scratch, main_scratch, requires_scratch  # noqa: F401
from trace_lab import annotations, labels, proposals, taxonomy
from trace_lab.annotations import Anchor
from trace_lab.review_errors import Conflict, Forbidden, Invalid, NotFound, Stale

pytestmark = requires_scratch
TURN = "turn-d000000000000001"
OTHER_TURN = "turn-d000000000000002"
NAME = "counts_cancelled_jobs_as_open"


async def _world(lab, main, *, kind="cell", suggestion="present", quote="12 open work orders", field="answer",
                 org=fx.ORG_A):
    async with main.begin() as conn:
        await fx.add_turn(conn, TURN, org)
    async with lab.begin() as conn:
        await fx.add_note(conn, TURN, org)
        mid = await fx.add_mode(conn, NAME, status="confirmed")
        run = await fx.add_run(conn, "cells" if kind == "cell" else "depth_scan", mode_id=mid)
        pid = await fx.add_proposal(conn, run, kind=kind, turn_id=TURN, mode_id=mid, suggestion=suggestion, quote=quote,
                                    field=field, org=org)
    return mid, pid


async def _row(lab, pid):
    async with lab.connect() as conn:
        return (await conn.execute(text("SELECT status, closed_reason, CAST(label_id AS text) AS label_id, "
                                        "decided_by_email FROM tracelab.proposals WHERE id = CAST(:p AS uuid)"),
                                   {"p": pid})).mappings().first()


async def _labels(lab):
    async with lab.connect() as conn:
        return (await conn.execute(text("SELECT count(*) FROM tracelab.labels"))).scalar()


async def test_accepting_a_present_cell_proposal_keeps_its_quote_and_its_step(lab_scratch, main_scratch):
    mid, pid = await _world(lab_scratch, main_scratch)
    out = await proposals.decide(fx.admin(), fx.ORG_A, pid, decision="accept")
    lab = out["label"]
    assert (lab["source"], lab["present"], lab["approval"], lab["proposal_id"]) == ("accepted_label_proposal", True,
                                                                                    "single", pid)
    assert lab["quote"] == "12 open work orders" and lab["span_kind"] == "answer" and lab["decided_by_email"] == fx.admin().email
    p = out["proposal"]
    assert (p["status"], p["label_id"], p["decided_by_email"]) == ("accepted", lab["id"], fx.admin().email) and p["decided_at"]


async def test_accepting_an_absent_cell_proposal_writes_absent_with_no_quote(lab_scratch, main_scratch):
    mid, pid = await _world(lab_scratch, main_scratch, suggestion="absent", quote=None, field=None)
    lab = (await proposals.decide(fx.admin(), fx.ORG_A, pid, decision="accept"))["label"]
    assert (lab["source"], lab["present"], lab["quote"]) == ("accepted_label_proposal", False, None)


async def test_overriding_with_the_suggested_value_is_an_acceptance(lab_scratch, main_scratch):
    mid, pid = await _world(lab_scratch, main_scratch, suggestion="present")
    same = await proposals.decide(fx.admin(), fx.ORG_A, pid, decision="override", present=True)
    assert same["label"]["source"] == "accepted_label_proposal" and same["proposal"]["status"] == "accepted"
    assert same["label"]["quote"] == "12 open work orders"


async def test_overriding_with_the_other_value_is_the_persons_own_label_and_dismisses(lab_scratch, main_scratch):
    mid, pid = await _world(lab_scratch, main_scratch, suggestion="present")
    other = await proposals.decide(fx.admin(), fx.ORG_A, pid, decision="override", present=False)
    assert (other["label"]["source"], other["label"]["present"], other["label"]["quote"]) == ("human_structured_label", False, None)
    assert other["proposal"]["status"] == "dismissed" and other["label"]["proposal_id"] == pid


@pytest.mark.parametrize("kind,kw", [("cell", {"decision": "dismiss"}), ("cell", {"decision": "override"}),
                                     ("depth_scan", {"decision": "override", "present": True}),
                                     ("cell", {"decision": "approve"}), ("depth_scan", {"decision": None})])
async def test_a_decision_that_does_not_fit_the_kind_is_refused_and_writes_nothing(lab_scratch, main_scratch, kind, kw):
    mid, pid = await _world(lab_scratch, main_scratch, kind=kind)
    with pytest.raises(Invalid) as e:
        await proposals.decide(fx.admin(), fx.ORG_A, pid, **kw)
    assert e.value.reason == "decision_not_allowed" and e.value.status == 422
    assert (await _row(lab_scratch, pid))["status"] == "pending" and await _labels(lab_scratch) == 0


async def test_a_second_decision_is_stale_and_carries_what_the_first_did(lab_scratch, main_scratch):
    mid, pid = await _world(lab_scratch, main_scratch)
    await proposals.decide(fx.admin(), fx.ORG_A, pid, decision="accept")
    with pytest.raises(Stale) as e:
        await proposals.decide(fx.admin(), fx.ORG_A, pid, decision="dismiss")
    assert e.value.status == 409 and e.value.current["id"] == pid and e.value.current["status"] == "accepted"
    assert await _labels(lab_scratch) == 1


async def test_a_changed_cell_is_a_409_with_the_head_and_the_proposal_stays_pending(lab_scratch, main_scratch):
    mid, pid = await _world(lab_scratch, main_scratch, kind="depth_scan")
    async with lab_scratch.begin() as conn:
        head = await fx.add_label(conn, TURN, mid, present=False)                   # a person got there first
    with pytest.raises(Stale) as e:
        await proposals.decide(fx.admin(), fx.ORG_A, pid, decision="accept", base=None)
    assert e.value.current["id"] == head
    assert (await _row(lab_scratch, pid))["status"] == "pending" and await _labels(lab_scratch) == 1
    ok = await proposals.decide(fx.admin(), fx.ORG_A, pid, decision="accept", base=head)       # with the head it goes through
    assert ok["label"]["supersedes"] == head


async def test_a_mode_retired_since_the_suggestion_refuses_and_nothing_is_half_written(lab_scratch, main_scratch):
    mid, pid = await _world(lab_scratch, main_scratch)
    async with lab_scratch.begin() as conn:                     # a retire that did not close the suggestion (a race)
        await conn.execute(text("UPDATE tracelab.modes SET status = 'retired', retired_at = now() WHERE id = CAST(:m AS uuid)"),
                           {"m": mid})
    with pytest.raises(Conflict) as e:
        await proposals.decide(fx.admin(), fx.ORG_A, pid, decision="accept")
    assert e.value.reason == "mode_not_live"
    assert (await _row(lab_scratch, pid))["status"] == "pending" and await _labels(lab_scratch) == 0


async def test_a_suggestion_a_person_already_settled_by_labelling_is_stale(lab_scratch, main_scratch):
    mid, pid = await _world(lab_scratch, main_scratch, kind="depth_scan")
    await labels.set_label(fx.owner(), None, TURN, mid, present=True, base=None)       # closes the depth-scan suggestion
    assert (await _row(lab_scratch, pid))["closed_reason"] == "human_decision_exists"
    with pytest.raises(Stale) as e:
        await proposals.decide(fx.owner(), None, pid, decision="accept")
    assert e.value.current["status"] == "closed" and e.value.current["closed_reason"] == "human_decision_exists"


async def test_another_companys_suggestion_is_not_found_whoever_asks(lab_scratch, main_scratch):
    mid, pid = await _world(lab_scratch, main_scratch)
    with pytest.raises(Forbidden):
        await proposals.decide(fx.admin(fx.ORG_B), fx.ORG_A, pid, decision="accept")           # naming A is refused to B's admin
    with pytest.raises(NotFound) as e:
        await proposals.decide(fx.admin(fx.ORG_B), fx.ORG_B, pid, decision="accept")
    assert e.value.reason == "proposal_not_found"
    with pytest.raises(NotFound):
        await proposals.decide(fx.owner(), fx.ORG_B, pid, decision="accept")                   # a superadmin acting for B
    assert (await _row(lab_scratch, pid))["status"] == "pending"


async def test_an_admin_with_no_company_and_a_plain_user_cannot_decide(lab_scratch, main_scratch):
    mid, pid = await _world(lab_scratch, main_scratch)
    admin = fx.admin()
    nobody = type(admin)(user_id=admin.user_id, email=admin.email, organization_id=None, role="admin", can_ingest=True,
                         building_ids=None)
    user = type(admin)(user_id=admin.user_id, email=admin.email, organization_id=admin.organization_id, role="user",
                       can_ingest=False, building_ids=None)
    for who, org in ((nobody, None), (user, fx.ORG_A)):
        with pytest.raises(Forbidden) as e:
            await proposals.decide(who, org, pid, decision="accept")
        assert e.value.reason == "reviewers_only"
    assert (await _row(lab_scratch, pid))["status"] == "pending"


@pytest.mark.parametrize("pid", ["not-a-uuid", "", str(uuid.uuid4())])
async def test_an_unknown_suggestion_is_not_found(lab_scratch, pid):
    with pytest.raises(NotFound) as e:
        await proposals.decide(fx.owner(), None, pid, decision="accept")
    assert e.value.reason == "proposal_not_found" and e.value.status == 404


async def test_a_known_answer_check_cannot_be_decided(lab_scratch, main_scratch):
    mid, _pid = await _world(lab_scratch, main_scratch)
    async with main_scratch.begin() as conn:
        await fx.add_turn(conn, OTHER_TURN)
    async with lab_scratch.begin() as conn:
        canary = await fx.add_proposal(conn, await fx.add_run(conn, "cells"), turn_id=OTHER_TURN, mode_id=mid,
                                       status="closed", closed_reason="canary", is_canary=True)
    with pytest.raises(NotFound):
        await proposals.decide(fx.owner(), None, canary, decision="accept")


async def test_a_closed_suggestion_on_a_hidden_answer_is_not_found_not_stale(lab_scratch, main_scratch):
    mid, pid = await _world(lab_scratch, main_scratch)
    await proposals.decide(fx.admin(), fx.ORG_A, pid, decision="accept")
    with pytest.raises(NotFound):                               # a stale answer would hand a blind reviewer the quote
        await proposals.decide(fx.admin(n=2), fx.ORG_A, pid, decision="accept")


async def test_deciding_keeps_the_answers_evidence_in_the_lab_database(lab_scratch, main_scratch):
    mid, pid = await _world(lab_scratch, main_scratch)
    async with main_scratch.begin() as conn:
        await fx.add_span(conn, TURN)
    await proposals.decide(fx.admin(), fx.ORG_A, pid, decision="accept")
    async with lab_scratch.connect() as conn:
        kept = (await conn.execute(text("SELECT reason, (SELECT count(*) FROM tracelab.evidence_spans s "
                                        "WHERE s.turn_id = e.turn_id) AS spans FROM tracelab.evidence_turns e "
                                        "WHERE turn_id = :t"), {"t": TURN})).mappings().first()
    assert kept is not None and kept["spans"] == 1


async def test_a_suggestion_pointing_at_a_step_keeps_the_steps_name_on_the_label(lab_scratch, main_scratch):
    async with main_scratch.begin() as conn:
        await fx.add_turn(conn, TURN)
        span = await fx.add_span(conn, TURN, name="query_table", output=[{"open": 12}])
    async with lab_scratch.begin() as conn:
        await fx.add_note(conn, TURN)
        mid = await fx.add_mode(conn, NAME, status="confirmed")
        pid = await fx.add_proposal(conn, await fx.add_run(conn, "cells"), turn_id=TURN, mode_id=mid,
                                    quote='"open": 12', field="output")
        await conn.execute(text("UPDATE tracelab.proposals SET span_id = CAST(:s AS uuid) WHERE id = CAST(:p AS uuid)"),
                           {"s": span, "p": pid})
    lab = (await proposals.decide(fx.admin(), fx.ORG_A, pid, decision="accept"))["label"]
    assert (lab["span_id"], lab["span_name"], lab["span_kind"], lab["span_agent"]) == (span, "query_table", "tool", "udr")


# ── grouping proposals ───────────────────────────────────────────────────────────────────────────────

DRAFT = {"name": "counts_cancelled_jobs_as_open", "definition": "Present when the answer counts cancelled jobs as open.",
         "boundary": "Not when the job is on hold.", "nearest_mode": "", "nearest_mode_id": None, "level": "technical",
         "agent": "wo_engine"}


async def _group_world(lab, main, *, suggestion="file_under", with_mode=True, draft=None):
    async with main.begin() as conn:
        await fx.add_turn(conn, TURN)
        await fx.add_turn(conn, OTHER_TURN)
    async with lab.begin() as conn:
        notes = [await fx.add_note(conn, TURN), await fx.add_note(conn, OTHER_TURN)]
        mid = await fx.add_mode(conn, "includes_cancelled_jobs", status="confirmed") if with_mode else None
        pid = await fx.add_proposal(conn, await fx.add_run(conn, "grouping"), kind="group", suggestion=suggestion,
                                    mode_id=mid, annotation_ids=notes, draft=draft, evidence="They share one fix.")
    return mid, notes, pid


async def test_file_under_labels_each_answer_with_its_notes_words_and_keeps_the_evidence(lab_scratch, main_scratch):
    mid, notes, pid = await _group_world(lab_scratch, main_scratch)
    out = await proposals.decide(fx.owner(), None, pid, decision="accept")
    assert {(x["turn_id"], x["annotation_id"], x["present"]) for x in out["labels"]} == {(TURN, notes[0], True),
                                                                                         (OTHER_TURN, notes[1], True)}
    assert all(x["note"] and x["quote"] == "12 open work orders" and x["mode_id"] == mid for x in out["labels"])
    async with lab_scratch.connect() as conn:
        n = (await conn.execute(text("SELECT count(*) FROM tracelab.evidence_turns"))).scalar()
    assert n == 2


async def test_file_under_stops_at_a_persons_absent_unless_the_owner_overrides(lab_scratch, main_scratch):
    mid, notes, pid = await _group_world(lab_scratch, main_scratch)
    async with lab_scratch.begin() as conn:
        absent = await fx.add_label(conn, OTHER_TURN, mid, present=False)
    with pytest.raises(Conflict) as e:
        await proposals.decide(fx.owner(), None, pid, decision="accept")
    assert e.value.reason == "conflicts_with_label" and e.value.extra["turn_id"] == OTHER_TURN
    assert (await _row(lab_scratch, pid))["status"] == "pending" and await _labels(lab_scratch) == 1     # nothing half filed
    out = await proposals.decide(fx.owner(), None, pid, decision="accept", override_labels=True)
    assert len(out["labels"]) == 2
    other = next(x for x in out["labels"] if x["turn_id"] == OTHER_TURN)
    assert other["supersedes"] == absent and other["present"] is True


async def test_a_note_edited_since_the_draft_makes_the_decision_stale_and_files_nothing(lab_scratch, main_scratch):
    mid, notes, pid = await _group_world(lab_scratch, main_scratch)
    await annotations.save_verdict(fx.admin(), fx.ORG_A, TURN, verdict="wrong", note="Reworded.",
                                   anchor=Anchor(None, "answer", "12 open work orders"), base=notes[0])
    async with lab_scratch.begin() as conn:                     # the edit closed the draft; put it back to see the race
        await conn.execute(text("UPDATE tracelab.proposals SET status = 'pending', closed_reason = NULL "
                                "WHERE id = CAST(:p AS uuid)"), {"p": pid})
    with pytest.raises(Stale) as e:
        await proposals.decide(fx.owner(), None, pid, decision="accept")
    assert e.value.current["id"] == pid and await _labels(lab_scratch) == 0


async def test_a_new_mode_whose_name_is_taken_is_a_409_and_creates_nothing(lab_scratch, main_scratch):
    mid, notes, pid = await _group_world(lab_scratch, main_scratch, suggestion="new_mode", with_mode=False, draft=DRAFT)
    async with lab_scratch.begin() as conn:
        await fx.add_mode(conn, DRAFT["name"], status="confirmed")
    with pytest.raises(Conflict) as e:
        await proposals.decide(fx.owner(), None, pid, decision="accept")
    assert e.value.reason == "name_taken"
    assert (await _row(lab_scratch, pid))["status"] == "pending" and await _labels(lab_scratch) == 0
    out = await proposals.decide(fx.owner(), None, pid, decision="accept", edits={"name": "counts_closed_jobs_as_open"})
    assert out["mode"]["name"] == "counts_closed_jobs_as_open" and out["mode"]["created_from"] == notes
    assert out["mode"]["boundary"] == DRAFT["boundary"]             # an edit changes only what it names


async def test_a_grouping_cannot_be_overridden_or_decided_twice(lab_scratch, main_scratch):
    mid, notes, pid = await _group_world(lab_scratch, main_scratch, suggestion="no_mode_fits", with_mode=False)
    with pytest.raises(Invalid) as e:
        await proposals.decide(fx.owner(), None, pid, decision="override", present=True)
    assert e.value.reason == "decision_not_allowed"
    await proposals.decide(fx.owner(), None, pid, decision="dismiss")
    with pytest.raises(Stale) as e2:
        await proposals.decide(fx.owner(), None, pid, decision="accept")
    assert e2.value.current["status"] == "dismissed"


async def test_a_decision_that_loses_a_race_to_another_decision_is_stale_and_writes_nothing(lab_scratch, main_scratch, monkeypatch):
    mid, pid = await _world(lab_scratch, main_scratch)
    real = labels.span_meta

    async def racing(span_id, field, turn):
        async with lab_scratch.begin() as conn:        # another reviewer dismisses it between the first look and the write
            await conn.execute(text("UPDATE tracelab.proposals SET status = 'dismissed', decided_at = now() "
                                    "WHERE id = CAST(:p AS uuid)"), {"p": pid})
        return await real(span_id, field, turn)

    monkeypatch.setattr(labels, "span_meta", racing)
    with pytest.raises(Stale) as e:
        await proposals.decide(fx.admin(), fx.ORG_A, pid, decision="accept")
    assert e.value.current["status"] == "dismissed" and await _labels(lab_scratch) == 0


async def test_a_grouping_decided_by_someone_else_in_the_meantime_is_stale_and_files_nothing(lab_scratch, main_scratch, monkeypatch):
    mid, notes, pid = await _group_world(lab_scratch, main_scratch)

    async def lose(rows):                              # runs inside the decision's transaction, after its lock
        raise AssertionError("not reached when the row was already decided")

    async with lab_scratch.begin() as conn:
        await conn.execute(text("UPDATE tracelab.proposals SET status = 'dismissed', decided_at = now() "
                                "WHERE id = CAST(:p AS uuid)"), {"p": pid})
    monkeypatch.setattr(taxonomy, "_snapshot_notes", lose)
    with pytest.raises(Stale) as e:
        await proposals.decide(fx.owner(), None, pid, decision="accept")
    assert e.value.current["status"] == "dismissed" and await _labels(lab_scratch) == 0
