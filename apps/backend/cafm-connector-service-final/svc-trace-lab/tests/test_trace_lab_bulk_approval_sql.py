"""Bulk approval against real rows: what the check returns and marks, what the approval writes (bulk approval, the
audit's basis, the evidence kept), who may do it, and what it refuses or skips without writing. Needs the scratch
database."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

import trace_lab_m4_fixtures as fx
from tl_scratch import lab_scratch, main_scratch, requires_scratch  # noqa: F401
from trace_lab import evidence, labels, proposals
from trace_lab.review_errors import Conflict, Forbidden, Invalid

pytestmark = requires_scratch
HOUR_AGO = lambda: datetime.now(timezone.utc) - timedelta(hours=1)          # noqa: E731


def _turn(i: int, prefix: str = "b") -> str:
    return f"turn-{prefix}{i:015d}"


async def _world(lab, main, *, n=22, kind="cell", suggestion="absent", org=fx.ORG_A, canary=(5, 5), judged=False,
                 mode_name="counts_cancelled_jobs_as_open", quote=None, field=None, notes=None):
    """n proposals, one per answer, with distinct evidence. `judged`: the company's first admin has a Right verdict on
    every answer, so a company caller may see the suggestions; `notes[i]`: a Wrong note by that admin on answer i."""
    turns = [_turn(i) for i in range(n)]
    async with main.begin() as conn:
        for t in turns:
            await fx.add_turn(conn, t, org)
    async with lab.begin() as conn:
        mid = await fx.add_mode(conn, mode_name, status="confirmed")
        run = await fx.add_run(conn, "cells" if kind == "cell" else "depth_scan", mode_id=mid,
                               canary_total=canary[0], canary_agree=canary[1])
        pids = []
        for i, t in enumerate(turns):
            if judged:
                await fx.add_note(conn, t, org, verdict="right")
            if notes and i in notes:
                await fx.add_note(conn, t, org, note=notes[i], reviewer=fx.admin(org, 3))
            pids.append(await fx.add_proposal(conn, run, kind=kind, turn_id=t, mode_id=mid, suggestion=suggestion,
                                              org=org, quote=quote, field=field,
                                              evidence=f"Checked the count for answer {i}: it excludes cancelled jobs."))
    return mid, turns, pids


async def _decide_spot(who, org, check):
    """Decide each spot-check item as the suggestion says (absent), hiding nothing from the person."""
    for it in check["spot_check"]["items"]:
        await labels.set_label(who, org, it["turn_id"], it["mode_id"], present=False, base=it["base"],
                               spot_check_seed=check["spot_check"]["seed"])


async def _count(lab, sql, **p):
    async with lab.connect() as conn:
        return (await conn.execute(text(sql), p)).scalar()


async def test_a_small_bulk_needs_no_spot_check_and_writes_one_bulk_label_per_suggestion(lab_scratch, main_scratch):
    mid, turns, pids = await _world(lab_scratch, main_scratch, n=6)
    served = HOUR_AGO()
    check = await proposals.bulk_check(fx.owner(), None, action="accept", proposal_ids=pids, served_at=served)
    assert check["allowed"] and check["spot_check"]["items"] == [] and check["stats"]["write"] == 6
    out = await proposals.bulk_approve(fx.owner(), None, action="accept", proposal_ids=pids, served_at=served, bases={})
    assert sorted(out["approved"]) == sorted(pids) and out["skipped"] == []
    assert {(x["present"], x["approval"], x["source"]) for x in out["labels"]} == {(False, "bulk", "accepted_label_proposal")}
    b = out["labels"][0]["basis"]["bulk"]
    assert b["n"] == 6 and b["distinct_share"] == 1.0 and b["spot_check"] is None and b["served_at"] == served.isoformat()
    assert b["canary_agreement"] and list(b["canary_agreement"].values()) == [1.0] and b["seconds_per_decision"] >= 5
    assert len(b["check"]) == 16
    rows = await _count(lab_scratch, "SELECT count(*) FROM tracelab.proposals WHERE status = 'accepted' AND label_id IS NOT NULL")
    assert rows == 6


async def test_a_bulk_labels_never_count_as_a_persons_own_judgement(lab_scratch, main_scratch):
    mid, turns, pids = await _world(lab_scratch, main_scratch, n=6)
    served = HOUR_AGO()
    await proposals.bulk_approve(fx.owner(), None, action="accept", proposal_ids=pids, served_at=served, bases={})
    cells = await labels.cells_for_turn(fx.owner(), None, turns[0])
    cell = next(c for c in cells["cells"] if c["mode_id"] == mid)
    assert (cell["state"], cell["source"], cell["approval"], cell["human_origin"]) == ("absent", "accepted_label_proposal",
                                                                                       "bulk", False)
    assert await labels.human_origin_cells(mid) == []                  # a judge never learns from a bulk approval


async def test_bulk_dismissal_of_depth_scan_suggestions_records_close_negatives(lab_scratch, main_scratch):
    mid, turns, pids = await _world(lab_scratch, main_scratch, n=6, kind="depth_scan", suggestion="present",
                                    quote="12 open work orders", field="answer")
    served = HOUR_AGO()
    out = await proposals.bulk_approve(fx.owner(), None, action="dismiss", proposal_ids=pids, served_at=served, bases={})
    assert {(x["source"], x["present"], x["approval"], x["quote"]) for x in out["labels"]} == {
        ("rejected_suggestion", False, "bulk", None)}
    assert await _count(lab_scratch, "SELECT count(*) FROM tracelab.proposals WHERE status = 'dismissed'") == 6


async def test_bulk_acceptance_of_depth_scan_suggestions_keeps_their_quote(lab_scratch, main_scratch):
    mid, turns, pids = await _world(lab_scratch, main_scratch, n=6, kind="depth_scan", suggestion="present",
                                    quote="12 open work orders", field="answer")
    out = await proposals.bulk_approve(fx.owner(), None, action="accept", proposal_ids=pids, served_at=HOUR_AGO(), bases={})
    assert {(x["source"], x["present"], x["quote"], x["span_kind"]) for x in out["labels"]} == {
        ("accepted_suggestion", True, "12 open work orders", "answer")}


async def test_a_refused_approval_writes_nothing_and_says_why(lab_scratch, main_scratch):
    mid, turns, pids = await _world(lab_scratch, main_scratch, n=10)
    async with lab_scratch.begin() as conn:
        await conn.execute(text("UPDATE tracelab.proposals SET evidence = 'The answer counts cancelled jobs.'"))
    with pytest.raises(Invalid) as e:
        await proposals.bulk_approve(fx.owner(), None, action="accept", proposal_ids=pids, served_at=HOUR_AGO(), bases={})
    assert e.value.reason == "bulk_refused" and e.value.status == 422
    assert [r["rule"] for r in e.value.extra["refused"]] == [2] and e.value.extra["flagged"] == []
    assert await _count(lab_scratch, "SELECT count(*) FROM tracelab.labels") == 0
    assert await _count(lab_scratch, "SELECT count(*) FROM tracelab.proposals WHERE status = 'pending'") == 10


async def test_too_fast_and_a_failed_known_answer_check_are_refused_with_what_to_do(lab_scratch, main_scratch):
    mid, turns, pids = await _world(lab_scratch, main_scratch, n=6)
    fast = await proposals.bulk_check(fx.owner(), None, action="accept", proposal_ids=pids,
                                      served_at=datetime.now(timezone.utc))
    assert not fast["allowed"] and fast["refused"][0]["rule"] == 7 and fast["refused"][0]["retry_after_s"] >= 29
    async with lab_scratch.begin() as conn:
        await conn.execute(text("UPDATE tracelab.proposal_runs SET canary_agree = 3"))
    bad = await proposals.bulk_check(fx.owner(), None, action="accept", proposal_ids=pids, served_at=HOUR_AGO())
    assert [r["rule"] for r in bad["refused"]] == [5] and not bad["allowed"]


async def test_a_note_that_describes_the_failure_flags_its_suggestion_and_it_is_skipped(lab_scratch, main_scratch):
    note = "The answer counts cancelled jobs as open in the total."
    mid, turns, pids = await _world(lab_scratch, main_scratch, n=6, notes={2: note})
    served = HOUR_AGO()
    check = await proposals.bulk_check(fx.owner(), None, action="accept", proposal_ids=pids, served_at=served)
    assert [f["id"] for f in check["flagged"]] == [pids[2]] and check["allowed"] and check["stats"]["write"] == 5
    out = await proposals.bulk_approve(fx.owner(), None, action="accept", proposal_ids=pids, served_at=served, bases={})
    assert sorted(out["approved"]) == sorted(set(pids) - {pids[2]}) and out["skipped"] == [{"id": pids[2], "reason": "flagged"}]
    assert await _count(lab_scratch, "SELECT count(*) FROM tracelab.proposals WHERE id = CAST(:p AS uuid) "
                                     "AND status = 'pending'", p=pids[2]) == 1


async def test_a_spot_check_that_disagrees_refuses_the_rest(lab_scratch, main_scratch):
    mid, turns, pids = await _world(lab_scratch, main_scratch)
    served = HOUR_AGO()
    check = await proposals.bulk_check(fx.owner(), None, action="accept", proposal_ids=pids, served_at=served)
    first, *rest = check["spot_check"]["items"]
    await labels.set_label(fx.owner(), None, first["turn_id"], first["mode_id"], present=True, base=first["base"],
                           spot_check_seed=check["spot_check"]["seed"])             # the person says present; the AI said absent
    for it in rest:
        await labels.set_label(fx.owner(), None, it["turn_id"], it["mode_id"], present=False, base=it["base"],
                               spot_check_seed=check["spot_check"]["seed"])
    with pytest.raises(Invalid) as e:
        await proposals.bulk_approve(fx.owner(), None, action="accept", proposal_ids=pids, served_at=served, bases={})
    assert [r["rule"] for r in e.value.extra["refused"]] == [6]
    assert await _count(lab_scratch, "SELECT count(*) FROM tracelab.labels WHERE approval = 'bulk'") == 0


async def test_an_unfinished_spot_check_is_a_409_and_a_wrong_seed_is_not_a_spot_check(lab_scratch, main_scratch):
    mid, turns, pids = await _world(lab_scratch, main_scratch)
    served = HOUR_AGO()
    check = await proposals.bulk_check(fx.owner(), None, action="accept", proposal_ids=pids, served_at=served)
    items = check["spot_check"]["items"]
    for it in items[:4]:
        await labels.set_label(fx.owner(), None, it["turn_id"], it["mode_id"], present=False, base=it["base"],
                               spot_check_seed=check["spot_check"]["seed"])
    with pytest.raises(Conflict) as e:
        await proposals.bulk_approve(fx.owner(), None, action="accept", proposal_ids=pids, served_at=served, bases={})
    assert e.value.reason == "spot_check_incomplete" and e.value.status == 409
    # a list the check never saw has another seed: the check's marks do not carry over
    other = await proposals.bulk_check(fx.owner(), None, action="accept", proposal_ids=pids[:-1], served_at=served)
    assert other["spot_check"]["seed"] != check["spot_check"]["seed"]


async def test_the_check_marks_only_the_spot_items_and_returns_no_suggestion_with_them(lab_scratch, main_scratch):
    mid, turns, pids = await _world(lab_scratch, main_scratch)
    check = await proposals.bulk_check(fx.owner(), None, action="accept", proposal_ids=pids, served_at=HOUR_AGO())
    spot = {it["proposal_id"] for it in check["spot_check"]["items"]}
    async with lab_scratch.connect() as conn:
        marked = {r[0] for r in (await conn.execute(text(
            "SELECT CAST(id AS text) FROM tracelab.proposals WHERE flags->>'spot_check_seed' = :s"),
            {"s": check["spot_check"]["seed"]})).all()}
    assert marked == spot and len(spot) == 5
    for it in check["spot_check"]["items"]:
        assert "suggestion" not in it and "evidence" not in it and it["question"] and it["mode_name"]


async def test_a_company_bulk_covers_only_what_that_company_has_judged(lab_scratch, main_scratch):
    mid, turns, pids = await _world(lab_scratch, main_scratch, n=6, judged=True)
    served = HOUR_AGO()
    ok = await proposals.bulk_check(fx.admin(), fx.ORG_A, action="accept", proposal_ids=pids, served_at=served)
    assert ok["allowed"] and ok["stats"]["write"] == 6
    blind = await proposals.bulk_check(fx.admin(n=2), fx.ORG_A, action="accept", proposal_ids=pids, served_at=served)
    assert not blind["allowed"] and blind["refused"][0]["rule"] == 1 and len(blind["refused"][0]["ids"]) == 6
    with pytest.raises(Invalid) as e:
        await proposals.bulk_approve(fx.admin(n=2), fx.ORG_A, action="accept", proposal_ids=pids, served_at=served, bases={})
    assert e.value.extra["refused"][0]["rule"] == 1
    assert await _count(lab_scratch, "SELECT count(*) FROM tracelab.labels") == 0
    out = await proposals.bulk_approve(fx.admin(), fx.ORG_A, action="accept", proposal_ids=pids, served_at=served, bases={})
    assert len(out["approved"]) == 6 and {x["decided_by_email"] for x in out["labels"]} == {fx.admin().email}


async def test_another_companys_suggestions_are_refused_and_never_marked_or_described(lab_scratch, main_scratch):
    mid, turns, pids = await _world(lab_scratch, main_scratch, judged=True)         # 22 of company A
    served = HOUR_AGO()
    check = await proposals.bulk_check(fx.admin(fx.ORG_B), fx.ORG_B, action="accept", proposal_ids=pids, served_at=served)
    assert not check["allowed"] and check["refused"][0]["rule"] == 1
    assert check["spot_check"]["items"] == []                       # nothing of company A's is described to company B
    assert await _count(lab_scratch, "SELECT count(*) FROM tracelab.proposals WHERE flags ? 'spot_check_seed'") == 0
    with pytest.raises(Forbidden):                                  # nor can B's admin act as company A
        await proposals.bulk_check(fx.admin(fx.ORG_B), fx.ORG_A, action="accept", proposal_ids=pids, served_at=served)


async def test_a_plain_user_a_bad_action_and_a_bad_time_are_refused_before_any_read(lab_scratch):
    admin = fx.admin()
    user = type(admin)(user_id=admin.user_id, email=admin.email, organization_id=admin.organization_id, role="user",
                       can_ingest=False, building_ids=None)
    with pytest.raises(Forbidden):
        await proposals.bulk_check(user, fx.ORG_A, action="accept", proposal_ids=[], served_at=HOUR_AGO())
    with pytest.raises(Invalid) as e:
        await proposals.bulk_check(fx.owner(), None, action="delete", proposal_ids=[], served_at=HOUR_AGO())
    assert e.value.reason == "decision_not_allowed"
    with pytest.raises(Invalid) as e:
        await proposals.bulk_approve(fx.owner(), None, action="accept", proposal_ids=[], served_at="yesterday", bases={})
    assert e.value.reason == "bad_served_at"


async def test_ids_that_are_not_suggestions_are_refused_by_rule_one(lab_scratch):
    check = await proposals.bulk_check(fx.owner(), None, action="accept", proposal_ids=["nope", "00000000-0000-4000-8000-000000000001"],
                                       served_at=HOUR_AGO())
    assert not check["allowed"] and check["refused"][0]["rule"] == 1 and check["refused"][0]["ids"] == [
        "nope", "00000000-0000-4000-8000-000000000001"]


async def test_a_suggestion_decided_after_the_audit_is_skipped_not_overwritten(lab_scratch, main_scratch, monkeypatch):
    mid, turns, pids = await _world(lab_scratch, main_scratch, n=6)
    served = HOUR_AGO()
    real, once = labels.scoped_turn, {"done": False}

    async def racing(principal, org, turn_id):
        if not once["done"]:
            once["done"] = True
            async with lab_scratch.begin() as conn:      # somebody else decides the first suggestion mid-approval
                await conn.execute(text("UPDATE tracelab.proposals SET status = 'dismissed', decided_at = now() "
                                        "WHERE turn_id = :t"), {"t": turn_id})
            once["turn"] = turn_id
        return await real(principal, org, turn_id)

    monkeypatch.setattr(labels, "scoped_turn", racing)
    out = await proposals.bulk_approve(fx.owner(), None, action="accept", proposal_ids=pids, served_at=served, bases={})
    assert len(out["approved"]) == 5 and [s["reason"] for s in out["skipped"]] == ["not_pending"]
    assert await _count(lab_scratch, "SELECT count(*) FROM tracelab.labels WHERE turn_id = :t", t=once["turn"]) == 0


async def test_the_answers_evidence_is_kept_and_a_failure_to_keep_it_stops_everything(lab_scratch, main_scratch, monkeypatch):
    mid, turns, pids = await _world(lab_scratch, main_scratch, n=6)
    served = HOUR_AGO()

    async def boom(*a, **k):
        raise RuntimeError("the lab database is down")

    real = evidence.snapshot
    monkeypatch.setattr(evidence, "snapshot", boom)
    with pytest.raises(RuntimeError):
        await proposals.bulk_approve(fx.owner(), None, action="accept", proposal_ids=pids, served_at=served, bases={})
    assert await _count(lab_scratch, "SELECT count(*) FROM tracelab.labels") == 0
    monkeypatch.setattr(evidence, "snapshot", real)
    await proposals.bulk_approve(fx.owner(), None, action="accept", proposal_ids=pids, served_at=served, bases={})
    assert await _count(lab_scratch, "SELECT count(*) FROM tracelab.evidence_turns") == 6


async def test_a_cell_changed_since_the_list_was_read_is_skipped_as_stale(lab_scratch, main_scratch):
    mid, turns, pids = await _world(lab_scratch, main_scratch, n=6)
    async with lab_scratch.begin() as conn:
        head = await fx.add_label(conn, turns[1], mid, present=None)               # a cleared cell: a head the list did not know
    out = await proposals.bulk_approve(fx.owner(), None, action="accept", proposal_ids=pids, served_at=HOUR_AGO(), bases={})
    assert [s for s in out["skipped"]] == [{"id": pids[1], "reason": "stale"}] and len(out["approved"]) == 5
    again = await proposals.bulk_approve(fx.owner(), None, action="accept", proposal_ids=[pids[1]], served_at=HOUR_AGO(),
                                         bases={pids[1]: head})
    assert again["approved"] == [pids[1]] and again["labels"][0]["supersedes"] == head


def test_no_more_than_the_bulk_limit_of_ids_is_ever_audited():
    org, ids, served = proposals._bulk_request(None, fx.owner(), "accept", [f"x{i}" for i in range(proposals.BULK_MAX + 80)],
                                               datetime(2026, 10, 15, tzinfo=timezone.utc))
    assert org is None and len(ids) == proposals.BULK_MAX and served.tzinfo is not None
    naive = proposals._bulk_request(None, fx.owner(), "dismiss", [], datetime(2026, 10, 15))[2]
    assert naive.tzinfo is timezone.utc


async def test_a_spot_check_item_clicked_with_its_suggestion_showing_is_not_a_spot_check_decision(lab_scratch, main_scratch):
    mid, turns, pids = await _world(lab_scratch, main_scratch)
    served = HOUR_AGO()
    check = await proposals.bulk_check(fx.owner(), None, action="accept", proposal_ids=pids, served_at=served)
    for it in check["spot_check"]["items"]:             # a plain click: the open suggestion counts as seen, so it is no blind check
        await labels.set_label(fx.owner(), None, it["turn_id"], it["mode_id"], present=False, base=it["base"])
    with pytest.raises(Conflict) as e:
        await proposals.bulk_approve(fx.owner(), None, action="accept", proposal_ids=pids, served_at=served, bases={})
    assert e.value.reason == "spot_check_incomplete"
    assert await _count(lab_scratch, "SELECT count(*) FROM tracelab.labels WHERE approval = 'bulk'") == 0
