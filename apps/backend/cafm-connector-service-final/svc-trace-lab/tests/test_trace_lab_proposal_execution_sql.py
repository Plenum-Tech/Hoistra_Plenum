"""Execution outcomes of a proposal run, on a throwaway Postgres (the model faked, no network): a non-verbatim present
closes as quote_not_verbatim, a turn without spans closes as turn_unavailable with no model call, canaries are stored
closed and counted, a grouping run files nothing (notes become modes only when a person accepts), a merge reconciles
drafts from several batches, the proposed-for turns are kept as evidence snapshots, and the pool of a depth scan is the
company's live chat turns that nobody has decided. Skips without TRACE_LAB_TEST_DB_URL."""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

import trace_lab_m4_fixtures as fx
from src.agents import llm_cost, skill_lab
from tl_scratch import lab_scratch, main_scratch, requires_scratch  # noqa: F401
from trace_lab import evidence, proposals
from trace_lab.review_errors import Conflict, Invalid, NotFound

pytestmark = requires_scratch
NOW = datetime.now(timezone.utc)
NO_DRAFT = {k: "" for k in ("name", "definition", "boundary", "nearest_mode", "level", "agent")}
DRAFT = {"name": "counts_cancelled_jobs_as_open", "definition": "Present when the answer counts cancelled jobs as open.",
         "boundary": "Not when the job is on hold.", "nearest_mode": "", "level": "technical", "agent": "wo_engine"}


def _patch(monkeypatch, reply, *, usd=0.01):
    """Fake the proposer's single seam; reply(n, user, role) is the n-th reply (or an exception to raise)."""
    calls: list[dict] = []

    async def ask(system, user, schema, *, role, max_tokens):
        calls.append({"system": system, "user": user, "role": role, "max_tokens": max_tokens, "schema": schema})
        r = reply(len(calls), user, role)
        if isinstance(r, Exception):
            raise r
        return r, {"usd": usd}

    monkeypatch.setenv("TRACE_LAB_JUDGE_MODEL", "claude-sonnet-5")
    monkeypatch.setattr(proposals, "ASK", ask)
    monkeypatch.setattr(proposals, "run_in_background", lambda run_id: None)
    return calls


async def _answer(lab, main, tid, org=fx.ORG_A, *, spans=True, session_id=None, started=None, question=None,
                  answer=None, reviewed=True):
    kw = {k: v for k, v in (("question", question), ("answer", answer)) if v is not None}
    async with main.begin() as conn:
        await fx.add_turn(conn, tid, org, session_id=session_id, started_at=started, **kw)
        if spans:
            await fx.add_span(conn, tid, org, output=[{"open": 12}])
    if reviewed:
        async with lab.begin() as conn:
            await fx.add_note(conn, tid, org, verdict="right")


async def _start(kind="cells", cap=1.0, **kw):
    mode_id = kw.pop("mode_id", None)
    return (await proposals.start_run(fx.owner(), kind=kind, mode_id=mode_id, since=NOW - timedelta(days=1),
                                      until=NOW + timedelta(hours=1), usd_cap=cap, **kw))["run"]["id"]


async def _start_grouping(cap=1.0):
    return (await proposals.start_run(fx.owner(), kind="grouping", mode_id=None, since=None, until=None,
                                      usd_cap=cap))["run"]["id"]


async def _rows(lab, run_id):
    async with lab.connect() as conn:
        return [dict(r) for r in (await conn.execute(text(
            "SELECT turn_id, kind, suggestion, status, closed_reason, is_canary, draft, quote, field, span_id::text AS span_id, "
            "evidence, flags, usd, organization_id::text AS organization_id, mode_id::text AS mode_id, "
            "annotation_ids::text[] AS annotation_ids FROM tracelab.proposals WHERE run_id = CAST(:r AS uuid) "
            "ORDER BY turn_id, created_at, id"), {"r": run_id})).mappings().all()]


def _present(quote, location="answer"):
    return {"labels": [{"mode": "M1", "decision": "present", "basis": "answer", "quote": quote, "location": location,
                        "evidence": "The count is wrong."}]}


def _absent():
    return {"labels": [{"mode": "M1", "decision": "absent", "basis": "answer", "quote": "", "location": "",
                        "evidence": "Checked the count."}]}


async def _confirmed_mode(lab, name="counts_cancelled_jobs_as_open", **kw):
    async with lab.begin() as conn:
        return await fx.add_mode(conn, name, status=kw.pop("status", "confirmed"), **kw)


# ── cells ─────────────────────────────────────────────────────────────────────────────────

async def test_outcomes_of_a_cells_run(lab_scratch, main_scratch, monkeypatch):
    await _answer(lab_scratch, main_scratch, "turn-n100000000000001")
    await _answer(lab_scratch, main_scratch, "turn-n100000000000002", spans=False)
    await _confirmed_mode(lab_scratch)
    calls = _patch(monkeypatch, lambda n, user, role: _present("37 open"))
    run_id = await _start()
    await proposals.execute(run_id)
    rows = {r["turn_id"]: r for r in await _rows(lab_scratch, run_id)}
    assert (rows["turn-n100000000000001"]["status"], rows["turn-n100000000000001"]["closed_reason"]) == ("closed", "quote_not_verbatim")
    assert (rows["turn-n100000000000002"]["status"], rows["turn-n100000000000002"]["closed_reason"]) == ("closed", "turn_unavailable")
    assert len(calls) == 1 and calls[0]["role"] == "trace_lab_cells"
    out = await proposals.get_run(fx.owner(), run_id)
    assert out["job"]["status"] == "done" and out["counts"]["closed"] == 2 and out["counts"]["unsure"] == 1
    assert out["job"]["done"] == 2 and out["job"]["usd_spent"] == pytest.approx(0.01)


async def test_a_verbatim_present_waits_for_a_person_with_its_evidence(lab_scratch, main_scratch, monkeypatch):
    await _answer(lab_scratch, main_scratch, "turn-n150000000000001")
    mid = await _confirmed_mode(lab_scratch)
    calls = _patch(monkeypatch, lambda n, user, role: _present("There are 12 open work orders"))
    run_id = await _start()
    await proposals.execute(run_id)
    (row,) = await _rows(lab_scratch, run_id)
    assert (row["kind"], row["suggestion"], row["status"], row["closed_reason"]) == ("cell", "present", "pending", None)
    assert (row["mode_id"], row["organization_id"], row["field"], row["span_id"]) == (mid, fx.ORG_A, "answer", None)
    assert row["quote"] == "There are 12 open work orders" and row["evidence"] == "The count is wrong."
    assert float(row["usd"]) == pytest.approx(0.01)
    out = await proposals.get_run(fx.owner(), run_id)
    assert out["counts"]["pending"] == 1 and out["job"]["progress"]["written"] == 1 and len(calls) == 1
    assert "Decide these modes: M1" in calls[0]["user"] and "counts_cancelled_jobs_as_open" in calls[0]["system"]


async def test_a_quote_from_a_tool_output_keeps_its_step(lab_scratch, main_scratch, monkeypatch):
    await _answer(lab_scratch, main_scratch, "turn-n160000000000001")
    await _confirmed_mode(lab_scratch)
    _patch(monkeypatch, lambda n, user, role: {"labels": [{"mode": "M1", "decision": "present", "basis": "tool_output",
                                                           "quote": '"open": 12', "location": "S1", "evidence": "e"}]})
    run_id = await _start()
    await proposals.execute(run_id)
    (row,) = await _rows(lab_scratch, run_id)
    assert (row["status"], row["field"], row["quote"]) == ("pending", "output", '"open": 12')
    async with main_scratch.connect() as conn:
        span = (await conn.execute(text("SELECT id::text FROM plenum_cafm.agent_trace_spans WHERE turn_id = :t"),
                                   {"t": "turn-n160000000000001"})).scalar()
    assert row["span_id"] == span


async def test_the_prompt_is_the_evidence_and_never_anyones_identity(lab_scratch, main_scratch, monkeypatch):
    await _answer(lab_scratch, main_scratch, "turn-n170000000000001")
    await _confirmed_mode(lab_scratch)
    calls = _patch(monkeypatch, lambda n, user, role: _present("x"))
    await proposals.execute(await _start())
    user = calls[0]["user"]
    assert "There are 12 open work orders at Bishopsgate." in user and '[S1] tool "query_table" (udr)' in user
    for private in ("asker@x.test", fx.ORG_A, "turn-n170000000000001", "admin1@1111.test", "owner@plenum.test"):
        assert private not in user and private not in calls[0]["system"]


async def test_the_answer_a_run_proposes_for_is_kept_as_evidence_after_the_chat_purges_it(lab_scratch, main_scratch,
                                                                                          monkeypatch):
    await _answer(lab_scratch, main_scratch, "turn-n180000000000001")
    await _confirmed_mode(lab_scratch)
    _patch(monkeypatch, lambda n, user, role: _present("12 open work orders"))
    await proposals.execute(await _start())
    async with lab_scratch.connect() as conn:
        kept = (await conn.execute(text(
            "SELECT reason, (SELECT count(*) FROM tracelab.evidence_spans s WHERE s.turn_id = t.turn_id) "
            "FROM tracelab.evidence_turns t WHERE t.turn_id = 'turn-n180000000000001'"))).first()
    assert kept == ("proposed", 1)
    async with main_scratch.begin() as conn:                      # the chat's 90-day purge
        await conn.execute(text("DELETE FROM plenum_cafm.agent_trace_spans WHERE turn_id = 'turn-n180000000000001'"))
        await conn.execute(text("DELETE FROM plenum_cafm.agent_trace_turns WHERE turn_id = 'turn-n180000000000001'"))
    got = await evidence.load(["turn-n180000000000001"], organization_id=fx.ORG_A)
    assert got["turn-n180000000000001"].source == "snapshot" and len(got["turn-n180000000000001"].spans) == 1


async def test_an_answer_that_cannot_be_kept_as_evidence_gets_no_proposal_and_costs_nothing(lab_scratch, main_scratch,
                                                                                            monkeypatch):
    await _answer(lab_scratch, main_scratch, "turn-n190000000000001")
    await _confirmed_mode(lab_scratch)
    calls = _patch(monkeypatch, lambda n, user, role: _present("12 open work orders"))
    run_id = await _start()

    async def broken(*a, **kw):
        raise RuntimeError("the lab database refused the copy")

    monkeypatch.setattr(evidence, "snapshot", broken)
    await proposals.execute(run_id)
    out = await proposals.get_run(fx.owner(), run_id)
    assert calls == [] and await _rows(lab_scratch, run_id) == []
    assert out["job"]["status"] == "paused" and out["counts"]["errors"] == 1 and out["job"]["usd_spent"] == 0


async def test_a_cell_already_decided_since_the_run_was_planned_is_skipped(lab_scratch, main_scratch, monkeypatch):
    await _answer(lab_scratch, main_scratch, "turn-n200000000000001")
    await _answer(lab_scratch, main_scratch, "turn-n200000000000002")
    mid = await _confirmed_mode(lab_scratch)
    calls = _patch(monkeypatch, lambda n, user, role: _present("12 open work orders"))
    run_id = await _start()
    async with lab_scratch.begin() as conn:                       # a person decides one cell before the run reaches it
        await fx.add_label(conn, "turn-n200000000000001", mid, present=False)
    await proposals.execute(run_id)
    rows = await _rows(lab_scratch, run_id)
    assert [r["turn_id"] for r in rows] == ["turn-n200000000000002"] and len(calls) == 1
    assert (await proposals.get_run(fx.owner(), run_id))["job"]["done"] == 2        # the skipped item still counts as done


async def test_a_cell_with_a_pending_suggestion_is_not_proposed_again(lab_scratch, main_scratch, monkeypatch):
    await _answer(lab_scratch, main_scratch, "turn-n210000000000001")
    mid = await _confirmed_mode(lab_scratch)
    async with lab_scratch.begin() as conn:
        earlier = await fx.add_run(conn, "cells")
        await fx.add_proposal(conn, earlier, turn_id="turn-n210000000000001", mode_id=mid, org=fx.ORG_A)
    _patch(monkeypatch, lambda n, user, role: _present("12 open work orders"))
    with pytest.raises(Invalid) as e:                              # nothing left to propose: no run is made
        await _start()
    assert e.value.reason == "nothing_to_propose"


async def test_candidates_are_asked_about_only_when_the_run_includes_them(lab_scratch, main_scratch, monkeypatch):
    await _answer(lab_scratch, main_scratch, "turn-n220000000000001")
    await _confirmed_mode(lab_scratch, status="candidate")
    calls = _patch(monkeypatch, lambda n, user, role: _present("12 open work orders"))
    with pytest.raises(Invalid) as e:                              # a candidate has no confirmed contract yet
        await _start()
    assert e.value.reason == "nothing_to_propose"
    run_id = await _start(include_candidates=True)
    await proposals.execute(run_id)
    assert len(calls) == 1 and (await _rows(lab_scratch, run_id))[0]["status"] == "pending"


async def test_a_company_run_and_a_platform_run_propose_each_answer_once(lab_scratch, main_scratch, monkeypatch):
    await _answer(lab_scratch, main_scratch, "turn-n230000000000001", fx.ORG_A)
    await _answer(lab_scratch, main_scratch, "turn-n230000000000002", fx.ORG_B)
    await _confirmed_mode(lab_scratch)
    _patch(monkeypatch, lambda n, user, role: _present("12 open work orders"))
    only_b = await _start(filter_org=fx.ORG_B)
    await proposals.execute(only_b)
    assert [(r["turn_id"], r["organization_id"]) for r in await _rows(lab_scratch, only_b)] == [
        ("turn-n230000000000002", fx.ORG_B)]
    assert (await proposals.get_run(fx.owner(), only_b))["run"]["organization_id"] == fx.ORG_B
    everyone = await _start()                                     # B's answer already has a pending suggestion
    await proposals.execute(everyone)
    assert [(r["turn_id"], r["organization_id"]) for r in await _rows(lab_scratch, everyone)] == [
        ("turn-n230000000000001", fx.ORG_A)]
    assert (await proposals.get_run(fx.owner(), everyone))["run"]["organization_id"] is None


async def test_canaries_are_stored_closed_and_counted(lab_scratch, main_scratch, monkeypatch):
    await _answer(lab_scratch, main_scratch, "turn-n300000000000001")
    mid = await _confirmed_mode(lab_scratch)
    async with lab_scratch.begin() as conn:
        await fx.add_label(conn, "turn-n300000000000001", mid, present=False)
    await _answer(lab_scratch, main_scratch, "turn-n300000000000002")
    calls = _patch(monkeypatch, lambda n, user, role: _absent())
    run_id = await _start()
    await proposals.execute(run_id)
    rows = {r["turn_id"]: r for r in await _rows(lab_scratch, run_id)}
    canary = rows["turn-n300000000000001"]
    assert (canary["is_canary"], canary["status"], canary["closed_reason"]) == (True, "closed", "canary")
    real = rows["turn-n300000000000002"]
    assert (real["is_canary"], real["status"], real["suggestion"]) == (False, "pending", "absent")
    out = await proposals.get_run(fx.owner(), run_id)
    assert out["canary"] == {"total": 1, "agree": 1, "rate": 1.0} and len(calls) == 2
    assert out["counts"]["pending"] == 1 and out["counts"]["closed"] == 0   # a canary is never counted among the proposals


async def test_a_canary_that_disagrees_lowers_the_rate(lab_scratch, main_scratch, monkeypatch):
    await _answer(lab_scratch, main_scratch, "turn-n310000000000001")
    mid = await _confirmed_mode(lab_scratch)
    async with lab_scratch.begin() as conn:
        await fx.add_label(conn, "turn-n310000000000001", mid, present=False)
    await _answer(lab_scratch, main_scratch, "turn-n310000000000002")
    _patch(monkeypatch, lambda n, user, role: _present("12 open work orders"))            # present where a person said absent
    run_id = await _start()
    await proposals.execute(run_id)
    assert (await proposals.get_run(fx.owner(), run_id))["canary"] == {"total": 1, "agree": 0, "rate": 0.0}


async def test_a_write_that_fails_costs_what_the_call_cost_and_leaves_the_item_for_a_resume(lab_scratch, main_scratch,
                                                                                            monkeypatch):
    await _answer(lab_scratch, main_scratch, "turn-n250000000000001")
    await _confirmed_mode(lab_scratch)
    calls = _patch(monkeypatch, lambda n, user, role: _present("12 open work orders"))
    run_id = await _start()

    async def boom(*a, **kw):
        raise RuntimeError("the disk is full")

    monkeypatch.setattr(proposals, "_insert_proposal", boom)
    await proposals.execute(run_id)
    out = await proposals.get_run(fx.owner(), run_id)
    assert len(calls) == 1 and await _rows(lab_scratch, run_id) == []                  # nothing written, the call was paid for
    assert out["job"]["status"] == "paused" and out["counts"]["errors"] == 1 and out["job"]["done"] == 0
    assert out["job"]["usd_spent"] == pytest.approx(0.01)
    async with lab_scratch.connect() as conn:
        assert float((await conn.execute(text("SELECT usd_reserved FROM tracelab.jobs"))).scalar()) == 0


async def test_a_run_planned_for_one_model_is_never_run_on_another(lab_scratch, main_scratch, monkeypatch):
    await _answer(lab_scratch, main_scratch, "turn-n260000000000001")
    await _confirmed_mode(lab_scratch)
    calls = _patch(monkeypatch, lambda n, user, role: _present("12 open work orders"))
    run_id = await _start()
    monkeypatch.setenv("TRACE_LAB_JUDGE_MODEL", "claude-opus-5")                  # changed after the cost was estimated
    await proposals.execute(run_id)
    out = await proposals.get_run(fx.owner(), run_id)
    assert calls == [] and out["job"]["status"] == "failed" and "model changed" in out["job"]["error"]


async def test_an_unpriced_model_is_refused_before_anything_is_created(lab_scratch, main_scratch, monkeypatch):
    await _answer(lab_scratch, main_scratch, "turn-n270000000000001")
    await _confirmed_mode(lab_scratch)
    calls = _patch(monkeypatch, lambda n, user, role: _present("12 open work orders"))
    monkeypatch.setenv("TRACE_LAB_JUDGE_MODEL", "a-model-nobody-priced")
    est = await proposals.estimate(fx.owner(), kind="cells", mode_id=None, since=NOW - timedelta(days=1),
                                   until=NOW + timedelta(hours=1))
    assert est["price_known"] is False and est["model"] == "a-model-nobody-priced" and est["items"] == 1
    with pytest.raises(Invalid) as e:
        await _start()
    assert e.value.reason == "price_unknown" and calls == []
    async with lab_scratch.connect() as conn:                       # no run and no job were made
        assert (await conn.execute(text("SELECT (SELECT count(*) FROM tracelab.jobs) + "
                                        "(SELECT count(*) FROM tracelab.proposal_runs)"))).scalar() == 0


async def test_a_known_answer_check_is_never_served_from_an_earlier_proposal(lab_scratch, main_scratch, monkeypatch):
    await _answer(lab_scratch, main_scratch, "turn-n320000000000001")
    mid = await _confirmed_mode(lab_scratch)
    _patch(monkeypatch, lambda n, user, role: _present("12 open work orders"))
    first = await _start()
    await proposals.execute(first)                                  # run one proposes for the answer
    async with lab_scratch.begin() as conn:                         # a person then decides it, which closes the suggestion
        await fx.add_label(conn, "turn-n320000000000001", mid, present=True)
        await conn.execute(text("UPDATE tracelab.proposals SET status = 'closed', closed_reason = 'human_decision_exists' "
                                "WHERE run_id = CAST(:r AS uuid)"), {"r": first})
    await _answer(lab_scratch, main_scratch, "turn-n320000000000002")
    calls = _patch(monkeypatch, lambda n, user, role: _present("12 open work orders"))
    second = await _start()
    await proposals.execute(second)
    out = await proposals.get_run(fx.owner(), second)
    assert len(calls) == 2 and out["canary"] == {"total": 1, "agree": 1, "rate": 1.0}   # the known answer was asked, not looked up
    assert {r["turn_id"]: r["is_canary"] for r in await _rows(lab_scratch, second)} == {
        "turn-n320000000000001": True, "turn-n320000000000002": False}


# ── depth scans ───────────────────────────────────────────────────────────────────────────

SAME_ANSWER = "There are 12 open work orders including cancelled ones."


async def _depth_setup(lab, main):
    """A mode with four human-confirmed instances and two human-confirmed absents that read just the same, and a pool
    of six other answers nobody has decided."""
    pos = [f"turn-d0{i:013d}" for i in range(4)]
    neg = [f"turn-d1{i:013d}" for i in range(2)]
    pool = [f"turn-d2{i:013d}" for i in range(6)]
    for t in pos + neg:
        await _answer(lab, main, t, reviewed=False, question="How many work orders are open?", answer=SAME_ANSWER)
    for t in pool:
        await _answer(lab, main, t, reviewed=False, session_id=f"sess-{t}", question="How many work orders are open?",
                      answer=SAME_ANSWER)
    mid = await _confirmed_mode(lab, boundary="Not when on hold.")
    async with lab.begin() as conn:
        for t in pos:
            await fx.add_label(conn, t, mid, present=True, quote="12 open work orders", note="Cancelled jobs are counted.")
        for t in neg:
            await fx.add_label(conn, t, mid, present=False)
    return mid, pos, neg, pool


def _depth_reply(verdict="present", quote="12 open work orders including cancelled ones"):
    return {"verdict": verdict, "quote": quote if verdict == "present" else "",
            "location": "answer" if verdict == "present" else "", "evidence": "Counts cancelled jobs as open."}


async def test_a_depth_scan_proposes_from_the_pool_and_never_from_decided_cells(lab_scratch, main_scratch, monkeypatch):
    mid, pos, neg, pool = await _depth_setup(lab_scratch, main_scratch)
    calls = _patch(monkeypatch, lambda n, user, role: _depth_reply())
    run_id = await _start("depth_scan", mode_id=mid)
    await proposals.execute(run_id)
    rows = await _rows(lab_scratch, run_id)
    real = [r for r in rows if not r["is_canary"]]
    assert sorted(r["turn_id"] for r in real) == sorted(pool)                 # the six undecided answers, nothing else
    assert {(r["kind"], r["suggestion"], r["status"], r["mode_id"]) for r in real} == {
        ("depth_scan", "present", "pending", mid)}
    assert {r["quote"] for r in real} == {"12 open work orders including cancelled ones"}
    assert all(r["flags"] == {} and r["organization_id"] == fx.ORG_A and r["field"] == "answer" for r in real)
    out = await proposals.get_run(fx.owner(), run_id)
    assert out["job"]["status"] == "done" and out["counts"]["pending"] == 6
    assert all(c["role"] == "trace_lab_depth_scan" and c["max_tokens"] == 1200 for c in calls) and len(calls) == 12
    assert "THE MISTAKE: counts_cancelled_jobs_as_open" in calls[0]["system"]
    assert "Cancelled jobs are counted." in calls[0]["system"] and "Not when on hold." in calls[0]["system"]
    est = await proposals.estimate(fx.owner(), kind="depth_scan", mode_id=mid, since=NOW - timedelta(days=1),
                                   until=NOW + timedelta(hours=1))
    assert est["items"] == 0 and est["canaries"] == 6                          # every answer now has a pending suggestion


@pytest.mark.parametrize("verdict,agree", [("present", 4), ("unsure", 2)])
async def test_the_canaries_of_a_depth_scan_are_known_answers(lab_scratch, main_scratch, monkeypatch, verdict, agree):
    # Present is right for the 4 confirmed instances and wrong for the 2 known absents (which should read unsure);
    # unsure is right for the 2 absents and wrong for the 4 instances (spec 7.8 rule 5).
    mid, pos, neg, pool = await _depth_setup(lab_scratch, main_scratch)
    _patch(monkeypatch, lambda n, user, role: _depth_reply(verdict))
    run_id = await _start("depth_scan", mode_id=mid)
    await proposals.execute(run_id)
    out = await proposals.get_run(fx.owner(), run_id)
    assert out["canary"]["total"] == 6 and out["canary"]["agree"] == agree
    canaries = [r for r in await _rows(lab_scratch, run_id) if r["is_canary"]]
    assert len(canaries) == 6 and all((r["status"], r["closed_reason"]) == ("closed", "canary") for r in canaries)
    assert {r["turn_id"] for r in canaries} == set(pos) | set(neg)


async def test_a_depth_scan_needs_three_human_confirmed_answers(lab_scratch, main_scratch, monkeypatch):
    for i in range(2):
        await _answer(lab_scratch, main_scratch, f"turn-d3{i:013d}", reviewed=False)
    mid = await _confirmed_mode(lab_scratch)
    async with lab_scratch.begin() as conn:
        for i in range(2):
            await fx.add_label(conn, f"turn-d3{i:013d}", mid, present=True)
    _patch(monkeypatch, lambda n, user, role: _depth_reply())
    with pytest.raises(Invalid) as e:
        await _start("depth_scan", mode_id=mid)
    assert e.value.reason == "not_enough_positives" and e.value.extra == {"have": 2, "need": 3}


async def test_bulk_approved_confirmations_do_not_count_as_human(lab_scratch, main_scratch, monkeypatch):
    for i in range(3):
        await _answer(lab_scratch, main_scratch, f"turn-d6{i:013d}", reviewed=False)
    mid = await _confirmed_mode(lab_scratch)
    async with lab_scratch.begin() as conn:
        run = await fx.add_run(conn, "cells")
        for i in range(3):
            pid = await fx.add_proposal(conn, run, turn_id=f"turn-d6{i:013d}", mode_id=mid, status="accepted")
            await fx.add_label(conn, f"turn-d6{i:013d}", mid, present=True, source="accepted_suggestion",
                               approval="bulk", proposal_id=pid)
    _patch(monkeypatch, lambda n, user, role: _depth_reply())
    with pytest.raises(Invalid) as e:
        await _start("depth_scan", mode_id=mid)
    assert e.value.reason == "not_enough_positives" and e.value.extra == {"have": 0, "need": 3}


async def test_a_mode_id_in_capitals_names_the_same_mode(lab_scratch, main_scratch, monkeypatch):
    mid, pos, neg, pool = await _depth_setup(lab_scratch, main_scratch)
    est = await proposals.estimate(fx.owner(), kind="depth_scan", mode_id=mid.upper(), since=NOW - timedelta(days=1),
                                   until=NOW + timedelta(hours=1))
    assert est["items"] == 6 and est["canaries"] == 6
    _patch(monkeypatch, lambda n, user, role: _depth_reply())
    run_id = await _start("depth_scan", mode_id=mid.upper())
    assert (await proposals.get_run(fx.owner(), run_id))["run"]["mode_id"] == mid


async def test_a_depth_scan_refuses_a_mode_that_is_not_live(lab_scratch, main_scratch, monkeypatch):
    async with lab_scratch.begin() as conn:
        retired = await fx.add_mode(conn, "old_mode_name", status="retired")
        proposed = await fx.add_mode(conn, "proposed_mode_name", status="proposed")
    _patch(monkeypatch, lambda n, user, role: _depth_reply())
    for mode, cls, reason in ((retired, Conflict, "mode_not_live"), (proposed, Conflict, "mode_not_live"),
                              ("00000000-0000-4000-8000-000000000000", NotFound, "mode_not_found"),
                              ("nonsense", NotFound, "mode_not_found"), (None, NotFound, "mode_not_found")):
        with pytest.raises(cls) as e:
            await _start("depth_scan", mode_id=mode)
        assert e.value.reason == reason


async def test_a_depth_scan_fails_when_its_mode_leaves_before_it_runs(lab_scratch, main_scratch, monkeypatch):
    mid, pos, neg, pool = await _depth_setup(lab_scratch, main_scratch)
    calls = _patch(monkeypatch, lambda n, user, role: _depth_reply())
    run_id = await _start("depth_scan", mode_id=mid)
    async with lab_scratch.begin() as conn:
        await conn.execute(text("UPDATE tracelab.modes SET status = 'retired', retired_at = now() WHERE id = CAST(:m AS uuid)"),
                           {"m": mid})
    await proposals.execute(run_id)
    out = await proposals.get_run(fx.owner(), run_id)
    assert calls == [] and out["job"]["status"] == "failed" and "merged or retired" in out["job"]["error"]


async def test_the_pool_is_live_chat_turns_of_the_window_in_the_scope(lab_scratch, main_scratch, monkeypatch):
    mid, pos, neg, pool = await _depth_setup(lab_scratch, main_scratch)
    other_company, old = "turn-d4000000000000001", "turn-d4000000000000002"
    migrate, golden = "turn-d4000000000000003", "turn-g4000000000000004"
    await _answer(lab_scratch, main_scratch, other_company, fx.ORG_B, reviewed=False, session_id="sess-b", answer=SAME_ANSWER)
    await _answer(lab_scratch, main_scratch, old, reviewed=False, session_id="sess-old", started=NOW - timedelta(days=3),
                  answer=SAME_ANSWER)
    await _answer(lab_scratch, main_scratch, migrate, reviewed=False, session_id="sess-ui",
                  question="Migrate this building's data please", answer=SAME_ANSWER)
    await _answer(lab_scratch, main_scratch, golden, reviewed=False, session_id="golden-run-case-r1", answer=SAME_ANSWER)
    _patch(monkeypatch, lambda n, user, role: _depth_reply())
    run_id = await _start("depth_scan", mode_id=mid, filter_org=fx.ORG_A)
    await proposals.execute(run_id)
    turns = {r["turn_id"] for r in await _rows(lab_scratch, run_id) if not r["is_canary"]}
    assert turns == set(pool)                                       # not another company, a past window, a migration or a golden run
    wide = await proposals.estimate(fx.owner(), kind="depth_scan", mode_id=mid, since=NOW - timedelta(days=1),
                                    until=NOW + timedelta(hours=1))
    assert wide["items"] == 1                                        # the platform scope still reaches the other company's answer


async def test_a_platform_wide_scan_names_each_turns_own_company(lab_scratch, main_scratch, monkeypatch):
    mid, pos, neg, pool = await _depth_setup(lab_scratch, main_scratch)
    await _answer(lab_scratch, main_scratch, "turn-d5000000000000001", fx.ORG_B, reviewed=False, session_id="sess-b",
                  answer=SAME_ANSWER)
    _patch(monkeypatch, lambda n, user, role: _depth_reply())
    run_id = await _start("depth_scan", mode_id=mid)
    await proposals.execute(run_id)
    by_turn = {r["turn_id"]: r["organization_id"] for r in await _rows(lab_scratch, run_id) if not r["is_canary"]}
    assert by_turn["turn-d5000000000000001"] == fx.ORG_B and all(by_turn[t] == fx.ORG_A for t in pool)


async def test_depth_batch_turns_lists_the_decided_suggestions_in_decision_order(lab_scratch, main_scratch, monkeypatch):
    mid, pos, neg, pool = await _depth_setup(lab_scratch, main_scratch)
    _patch(monkeypatch, lambda n, user, role: _depth_reply())
    run_id = await _start("depth_scan", mode_id=mid)
    await proposals.execute(run_id)
    async with lab_scratch.begin() as conn:
        for i, (t, status) in enumerate([(pool[2], "accepted"), (pool[0], "dismissed"), (pool[1], "accepted")]):
            await conn.execute(text(
                "UPDATE tracelab.proposals SET status = :s, decided_at = now() + make_interval(secs => :i) "
                "WHERE run_id = CAST(:r AS uuid) AND turn_id = :t AND NOT is_canary"), {"s": status, "i": i, "r": run_id, "t": t})
    async with lab_scratch.connect() as conn:
        everyone = await proposals.depth_batch_turns(conn, run_id, None)
        mine = await proposals.depth_batch_turns(conn, run_id, fx.ORG_A)
        other = await proposals.depth_batch_turns(conn, run_id, fx.ORG_B)
    assert everyone == [(pool[2], "accepted"), (pool[0], "dismissed"), (pool[1], "accepted")] == mine
    assert other == []
    async with lab_scratch.connect() as conn:                       # an id that is no uuid is no run: nothing, not an error
        assert await proposals.depth_batch_turns(conn, "not-a-run", None) == []
        assert await proposals.depth_batch_turns(conn, run_id, "not-a-company") == []


# ── grouping ──────────────────────────────────────────────────────────────────────────────

def _all_notes(user):
    return [f"N{n}" for n in re.findall(r"^\[N(\d+)\]", user, re.M)]


async def test_a_grouping_run_files_nothing_until_a_person_accepts(lab_scratch, main_scratch, monkeypatch):
    notes = []
    for t in ("turn-n400000000000001", "turn-n400000000000002"):
        await _answer(lab_scratch, main_scratch, t, reviewed=False)
        async with lab_scratch.begin() as conn:
            notes.append(await fx.add_note(conn, t, verdict="wrong"))
    calls = _patch(monkeypatch, lambda n, user, role: {"actions": [{"action": "new_mode", "note_ids": _all_notes(user),
                                                                    "mode_id": "", "draft": DRAFT, "reason": "Same fix."}]})
    run_id = await _start_grouping()
    await proposals.execute(run_id)
    rows = await _rows(lab_scratch, run_id)
    assert [(r["kind"], r["suggestion"], r["status"]) for r in rows] == [("group", "new_mode", "pending")]
    assert sorted(rows[0]["annotation_ids"]) == sorted(notes) and rows[0]["turn_id"] is None and rows[0]["organization_id"] is None
    assert rows[0]["draft"]["name"] == "counts_cancelled_jobs_as_open" and rows[0]["evidence"] == "Same fix."
    async with lab_scratch.connect() as conn:
        assert (await conn.execute(text("SELECT count(*) FROM tracelab.modes"))).scalar() == 0
        assert (await conn.execute(text("SELECT count(*) FROM tracelab.labels"))).scalar() == 0
    assert calls[0]["role"] == "trace_lab_grouping" and "[N1] pinned at: the answer" in calls[0]["user"]
    for private in ("admin1@1111.test", "asker@x.test", "turn-n40000000000000"):
        assert private not in calls[0]["user"]
    assert (await proposals.get_run(fx.owner(), run_id))["job"]["status"] == "done"


async def test_a_grouping_run_reads_only_unfiled_notes(lab_scratch, main_scratch, monkeypatch):
    for t in ("turn-n410000000000001", "turn-n410000000000002"):
        await _answer(lab_scratch, main_scratch, t, reviewed=False)
    async with lab_scratch.begin() as conn:
        n1 = await fx.add_note(conn, "turn-n410000000000001", verdict="wrong")
        await fx.add_note(conn, "turn-n410000000000002", verdict="right")                   # a right verdict is no note to group
        mid = await fx.add_mode(conn, "invents_asset_codes", status="confirmed", created_from=[n1])
        await fx.add_label(conn, "turn-n410000000000001", mid, present=True, source="human_open_code", annotation_id=n1)
    calls = _patch(monkeypatch, lambda n, user, role: {"actions": []})
    with pytest.raises(Invalid) as e:                           # the only wrong note is already filed under a mode
        await _start_grouping()
    assert e.value.reason == "nothing_to_propose" and calls == []


async def test_a_grouping_batch_that_files_under_an_existing_mode_names_it(lab_scratch, main_scratch, monkeypatch):
    await _answer(lab_scratch, main_scratch, "turn-n420000000000001", reviewed=False)
    await _answer(lab_scratch, main_scratch, "turn-n420000000000002", reviewed=False)
    async with lab_scratch.begin() as conn:
        first = await fx.add_note(conn, "turn-n420000000000001", verdict="wrong", note="Counts cancelled jobs.")
        mid = await fx.add_mode(conn, "counts_cancelled_jobs_as_open", status="confirmed", created_from=[first])
        await fx.add_label(conn, "turn-n420000000000001", mid, present=True, source="human_open_code", annotation_id=first)
        second = await fx.add_note(conn, "turn-n420000000000002", verdict="wrong", note="Cancelled jobs counted again.")
    calls = _patch(monkeypatch, lambda n, user, role: {"actions": [{"action": "file_under", "note_ids": ["N1"],
                                                                    "mode_id": "M1", "draft": NO_DRAFT,
                                                                    "reason": "Same fix."}]})
    run_id = await _start_grouping()
    await proposals.execute(run_id)
    (row,) = await _rows(lab_scratch, run_id)
    assert (row["suggestion"], row["mode_id"], row["annotation_ids"], row["draft"]) == ("file_under", mid, [second], None)
    assert "[M1] counts_cancelled_jobs_as_open (confirmed, agent wo_engine)" in calls[0]["user"]
    assert "Notes it came from:\n  - Counts cancelled jobs." in calls[0]["user"]
    assert "Cancelled jobs counted again." in calls[0]["user"]


async def test_drafts_from_several_batches_are_reconciled_by_one_more_call(lab_scratch, main_scratch, monkeypatch):
    n = 41                                                                      # one more than a batch holds: two batches
    async with main_scratch.begin() as conn:
        for i in range(n):
            await fx.add_turn(conn, f"turn-n5{i:014d}", session_id=f"sess-m{i}")
    async with lab_scratch.begin() as conn:
        for i in range(n):
            await fx.add_note(conn, f"turn-n5{i:014d}", verdict="wrong", note=f"note {i} counts cancelled jobs")
    seen: list[str] = []

    def reply(k, user, role):
        seen.append(role)
        if role == "trace_lab_grouping":
            return {"actions": [{"action": "new_mode", "note_ids": _all_notes(user), "mode_id": "",
                                 "draft": dict(DRAFT, name=f"draft_{k}_counts_jobs"), "reason": "Same fix."}]}
        return {"modes": [{"from_drafts": ["D1", "D2"], "name": "counts_cancelled_jobs_as_open",
                           "definition": "Present when the answer counts cancelled jobs as open.",
                           "boundary": "Not when on hold.", "nearest_mode": "", "level": "technical",
                           "agent": "wo_engine", "reason": "One fix clears both."}]}

    calls = _patch(monkeypatch, reply)
    run_id = await _start_grouping()
    await proposals.execute(run_id)
    assert sorted(seen) == ["trace_lab_grouping", "trace_lab_grouping", "trace_lab_grouping_merge"] and len(calls) == 3
    assert seen[-1] == "trace_lab_grouping_merge"                               # merged after every batch
    rows = await _rows(lab_scratch, run_id)
    drafts = [r for r in rows if r["flags"].get("batch")]
    assert len(drafts) == 2 and {(r["status"], r["closed_reason"]) for r in drafts} == {("closed", "superseded")}
    (merged,) = [r for r in rows if r["status"] == "pending"]
    assert merged["suggestion"] == "new_mode" and merged["draft"]["name"] == "counts_cancelled_jobs_as_open"
    assert len(merged["annotation_ids"]) == n and len(merged["flags"]["merged_from"]) == 2
    out = await proposals.get_run(fx.owner(), run_id)
    assert out["job"]["status"] == "done" and out["job"]["done"] == 3 and out["counts"]["pending"] == 1


async def test_one_batch_needs_no_merge(lab_scratch, main_scratch, monkeypatch):
    for t in ("turn-n430000000000001", "turn-n430000000000002"):
        await _answer(lab_scratch, main_scratch, t, reviewed=False)
        async with lab_scratch.begin() as conn:
            await fx.add_note(conn, t, verdict="wrong")
    calls = _patch(monkeypatch, lambda n, user, role: {"actions": [{"action": "no_mode_fits", "note_ids": _all_notes(user),
                                                                    "mode_id": "", "draft": NO_DRAFT,
                                                                    "reason": "Too vague."}]})
    run_id = await _start_grouping()
    await proposals.execute(run_id)
    assert len(calls) == 1
    (row,) = await _rows(lab_scratch, run_id)
    assert (row["suggestion"], row["status"], row["draft"]) == ("no_mode_fits", "pending", None)


async def test_a_reply_that_breaks_the_reading_is_an_item_error_never_a_default(lab_scratch, main_scratch, monkeypatch):
    await _answer(lab_scratch, main_scratch, "turn-n440000000000001", reviewed=False)
    async with lab_scratch.begin() as conn:
        await fx.add_note(conn, "turn-n440000000000001", verdict="wrong")
    _patch(monkeypatch, lambda n, user, role: {"actions": [{"action": "not_a_failure", "note_ids": ["N1"], "mode_id": "",
                                                            "draft": NO_DRAFT, "reason": ""}]})
    run_id = await _start_grouping()
    await proposals.execute(run_id)
    out = await proposals.get_run(fx.owner(), run_id)
    assert await _rows(lab_scratch, run_id) == []
    assert out["job"]["status"] == "paused" and out["counts"]["errors"] == 1 and out["job"]["done"] == 0
    assert out["job"]["usd_spent"] == pytest.approx(0.01) and "could not be read" in out["job"]["error"]


# ── the cost the job records is the call's own ───────────────────────────────────────────────────────────────

async def test_a_runs_spend_is_what_the_calls_cost_on_their_own_ledger(lab_scratch, main_scratch, monkeypatch):
    await _answer(lab_scratch, main_scratch, "turn-n450000000000001")
    await _confirmed_mode(lab_scratch)
    monkeypatch.setenv("TRACE_LAB_JUDGE_MODEL", "claude-sonnet-5")
    monkeypatch.setattr(proposals, "run_in_background", lambda run_id: None)
    seen = []

    async def ask_json(system, prompt, schema, *, role, env_model, max_tokens=1500):
        seen.append((role, env_model, max_tokens))
        llm_cost.record(role, "claude-sonnet-5", {"input_tokens": 5000, "output_tokens": 300}, 5)
        return _present("12 open work orders")

    monkeypatch.setattr(skill_lab, "_ask_json", ask_json)
    run_id = await _start()
    await proposals.execute(run_id)
    out = await proposals.get_run(fx.owner(), run_id)
    assert seen == [("trace_lab_cells", "TRACE_LAB_JUDGE_MODEL", 3000)]
    assert out["job"]["usd_spent"] == pytest.approx(5000 * 2 / 1e6 + 300 * 10 / 1e6)       # 0.013
    async with lab_scratch.connect() as conn:
        reserved = (await conn.execute(text("SELECT usd_reserved FROM tracelab.jobs"))).scalar()
    assert float(reserved) == 0 and json.dumps(out["run"])                               # nothing is left reserved
