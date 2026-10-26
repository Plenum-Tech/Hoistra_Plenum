"""The suggestion queue (proposals.queue): a company reads its own suggestions on answers it has judged, the platform owner
reads every company's, nothing else leaks; each row carries its mode, the answer's own question (repaired, redacted) and
conversation, and the cell's current label head. Reads only. Needs the scratch database."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

import trace_lab_m4_fixtures as fx
from tl_scratch import lab_scratch, main_scratch, requires_scratch  # noqa: F401
from trace_lab import evidence, normalize, proposals
from trace_lab.review_errors import Forbidden, Invalid

pytestmark = requires_scratch
U1, U2, U3, U4 = (f"turn-u{n:015d}" for n in (1, 2, 3, 4))


async def _world(lab, main):
    """Company A: three answers its admin judged (a cell proposal each, one of them a depth-scan suggestion) and a
    known-answer check; company B: one answer, one cell proposal. Returns the ids."""
    async with main.begin() as conn:
        for t in (U1, U2, U3):
            await fx.add_turn(conn, t)
        await fx.add_turn(conn, U4, fx.ORG_B)
    ids: dict[str, str] = {}
    async with lab.begin() as conn:
        for t in (U1, U2, U3):
            await fx.add_note(conn, t)
        await fx.add_note(conn, U4, fx.ORG_B)
        ids["mode"] = mid = await fx.add_mode(conn, "counts_cancelled_jobs_as_open", status="confirmed")
        cells = await fx.add_run(conn, "cells")
        scan = await fx.add_run(conn, "depth_scan", mode_id=mid)
        ids["p1"] = await fx.add_proposal(conn, cells, turn_id=U1, mode_id=mid, suggestion="absent")
        ids["p2"] = await fx.add_proposal(conn, cells, turn_id=U2, mode_id=mid, suggestion="present",
                                          quote="12 open work orders", field="answer")
        ids["p3"] = await fx.add_proposal(conn, scan, kind="depth_scan", turn_id=U3, mode_id=mid)
        ids["p4"] = await fx.add_proposal(conn, cells, turn_id=U4, mode_id=mid, org=fx.ORG_B)
        ids["canary"] = await fx.add_proposal(conn, cells, turn_id=U3, mode_id=mid, status="closed",
                                              closed_reason="canary", is_canary=True)
    return ids


def _ids(out):
    return {p["id"] for p in out["proposals"]}


async def test_a_company_reads_its_own_and_the_platform_reads_every_company(lab_scratch, main_scratch):
    w = await _world(lab_scratch, main_scratch)
    mine = await proposals.queue(fx.admin(), fx.ORG_A, kind="suggestion")
    assert _ids(mine) == {w["p1"], w["p2"], w["p3"]} and mine["total"] == 3 and mine["hidden"] == 0
    other = await proposals.queue(fx.admin(fx.ORG_B), fx.ORG_B, kind="suggestion")
    assert _ids(other) == {w["p4"]}
    everyone = await proposals.queue(fx.owner(), None, kind="suggestion")
    assert _ids(everyone) == {w["p1"], w["p2"], w["p3"], w["p4"]} and everyone["total"] == 4


async def test_a_superadmin_acting_for_a_company_is_a_reviewer_of_it_not_the_owner(lab_scratch, main_scratch):
    w = await _world(lab_scratch, main_scratch)
    acting = await proposals.queue(fx.owner(), fx.ORG_B, kind="suggestion")
    assert acting["proposals"] == [] and acting["hidden"] == 1                       # the blind rule applies to them too
    with pytest.raises(Forbidden) as e:
        await proposals.queue(fx.owner(), fx.ORG_B, kind="group")
    assert e.value.reason == "platform_only"
    assert w


async def test_a_caller_cannot_read_another_company_whatever_they_name(lab_scratch, main_scratch):
    await _world(lab_scratch, main_scratch)
    with pytest.raises(Forbidden) as e:
        await proposals.queue(fx.admin(), fx.ORG_B, kind="suggestion")
    assert e.value.reason == "reviewers_only"
    user = fx.admin()
    user = type(user)(user_id=user.user_id, email=user.email, organization_id=user.organization_id, role="user",
                      can_ingest=False, building_ids=None)
    with pytest.raises(Forbidden):
        await proposals.queue(user, fx.ORG_A, kind="suggestion")


async def test_the_kinds_split_depth_scans_from_cell_proposals(lab_scratch, main_scratch):
    w = await _world(lab_scratch, main_scratch)
    assert _ids(await proposals.queue(fx.admin(), fx.ORG_A, kind="cell")) == {w["p1"], w["p2"]}
    assert _ids(await proposals.queue(fx.admin(), fx.ORG_A, kind="depth_scan")) == {w["p3"]}
    assert _ids(await proposals.queue(fx.owner(), None, kind="cell", mode_id=w["mode"].upper())) == {w["p1"], w["p2"], w["p4"]}
    assert (await proposals.queue(fx.owner(), None, kind="cell", mode_id=str(uuid.uuid4())))["proposals"] == []


async def test_a_known_answer_check_is_never_listed(lab_scratch, main_scratch):
    w = await _world(lab_scratch, main_scratch)
    for status in ("pending", "closed"):
        out = await proposals.queue(fx.owner(), None, kind="suggestion", status=status)
        assert w["canary"] not in _ids(out)
    async with lab_scratch.connect() as conn:
        run = (await conn.execute(text("SELECT CAST(run_id AS text) FROM tracelab.proposals WHERE id = CAST(:p AS uuid)"),
                                  {"p": w["canary"]})).scalar()
    assert w["canary"] not in _ids(await proposals.queue(fx.owner(), None, kind="cell", status="closed", run_id=run))


async def test_the_status_filter_and_a_decided_row_carry_who_decided(lab_scratch, main_scratch):
    w = await _world(lab_scratch, main_scratch)
    assert (await proposals.queue(fx.owner(), None, kind="cell", status="accepted"))["proposals"] == []
    await proposals.decide(fx.admin(), fx.ORG_A, w["p1"], decision="accept")
    done = await proposals.queue(fx.owner(), None, kind="cell", status="accepted")
    assert [(p["id"], p["decided_by_email"], p["status"]) for p in done["proposals"]] == [(w["p1"], fx.admin().email, "accepted")]
    assert done["proposals"][0]["label_id"] and done["proposals"][0]["decided_at"]
    assert w["p1"] not in _ids(await proposals.queue(fx.owner(), None, kind="cell"))


async def test_paging_is_stable_and_counts_the_whole_queue(lab_scratch, main_scratch):
    w = await _world(lab_scratch, main_scratch)
    first = await proposals.queue(fx.owner(), None, kind="suggestion", limit=3)
    rest = await proposals.queue(fx.owner(), None, kind="suggestion", limit=3, offset=3)
    assert first["total"] == rest["total"] == 4 and len(first["proposals"]) == 3 and len(rest["proposals"]) == 1
    assert _ids(first) | _ids(rest) == {w["p1"], w["p2"], w["p3"], w["p4"]} and not _ids(first) & _ids(rest)
    assert len((await proposals.queue(fx.owner(), None, kind="suggestion", limit=0))["proposals"]) == 1     # at least one
    assert len((await proposals.queue(fx.owner(), None, kind="suggestion", limit=10 ** 6))["proposals"]) == 4   # at most 200


@pytest.mark.parametrize("kw,reason", [({"kind": "everything"}, "unknown_queue_kind"), ({"kind": None}, "unknown_queue_kind"),
                                       ({"kind": "cell", "status": "bogus"}, "unknown_queue_status")])
async def test_an_unknown_kind_or_status_is_refused_before_any_read(kw, reason):
    with pytest.raises(Invalid) as e:
        await proposals.queue(fx.owner(), None, **kw)
    assert e.value.reason == reason and e.value.error and e.value.status == 422


async def test_a_row_carries_its_mode_the_answers_own_question_and_conversation(lab_scratch, main_scratch):
    w = await _world(lab_scratch, main_scratch)
    p = next(x for x in (await proposals.queue(fx.owner(), None, kind="cell"))["proposals"] if x["id"] == w["p2"])
    assert p["mode_name"] == "counts_cancelled_jobs_as_open" and p["mode_definition"].startswith("Present when")
    assert p["question"] == "How many work orders are open at Bishopsgate?" and p["conversation_key"] == "t:" + U2
    assert (p["kind"], p["suggestion"], p["quote"], p["field"], p["turn_id"]) == ("cell", "present", "12 open work orders",
                                                                                "answer", U2)
    assert p["organization_id"] == fx.ORG_A and p["run_id"] and p["notes"] == [] and p["base"] is None


async def test_the_question_is_the_askers_own_words_redacted_and_short(lab_scratch, main_scratch):
    wrapped = ("Page context: Assets\nUse the following records ... " * 8 + normalize.QUESTION_MARKER
               + "Why is Bearer abcdefghij0123456789xyz open? " + "x" * 400)
    async with main_scratch.begin() as conn:
        await fx.add_turn(conn, U1, question=wrapped, session_id="sess-1")
    async with lab_scratch.begin() as conn:
        await fx.add_note(conn, U1)
        mid = await fx.add_mode(conn, "counts_cancelled_jobs_as_open", status="confirmed")
        await fx.add_proposal(conn, await fx.add_run(conn, "cells"), turn_id=U1, mode_id=mid)
    p = (await proposals.queue(fx.owner(), None, kind="cell"))["proposals"][0]
    assert p["question"].startswith("Why is ") and "abcdefghij0123456789xyz" not in p["question"]
    assert "Page context" not in p["question"] and len(p["question"]) <= proposals.QUESTION_CHARS
    assert p["conversation_key"] == "s:sess-1"


async def test_a_purged_answer_is_read_from_its_snapshot_and_an_unknown_one_has_no_question(lab_scratch, main_scratch):
    w = await _world(lab_scratch, main_scratch)
    async with main_scratch.begin() as conn:
        await fx.add_span(conn, U1)
    await evidence.snapshot([U1], organization_id=fx.ORG_A, reason="proposed")
    async with main_scratch.begin() as conn:
        await conn.execute(text("DELETE FROM plenum_cafm.agent_trace_spans"))
        await conn.execute(text("DELETE FROM plenum_cafm.agent_trace_turns WHERE turn_id IN (:a, :b)"), {"a": U1, "b": U2})
    out = {p["id"]: p for p in (await proposals.queue(fx.owner(), None, kind="cell"))["proposals"]}
    assert out[w["p1"]]["question"] == "How many work orders are open at Bishopsgate?"
    assert out[w["p1"]]["conversation_key"] == "t:" + U1
    assert out[w["p2"]]["question"] is None and out[w["p2"]]["conversation_key"] is None


async def test_an_answer_of_another_company_gives_no_question(lab_scratch, main_scratch):
    async with main_scratch.begin() as conn:
        await fx.add_turn(conn, U1, fx.ORG_A, question="Company A's private question")
    async with lab_scratch.begin() as conn:
        mid = await fx.add_mode(conn, "counts_cancelled_jobs_as_open", status="confirmed")
        await fx.add_proposal(conn, await fx.add_run(conn, "cells"), turn_id=U1, mode_id=mid, org=fx.ORG_B)
    p = (await proposals.queue(fx.owner(), None, kind="cell"))["proposals"][0]
    assert p["question"] is None and p["conversation_key"] is None


async def test_the_cells_label_head_is_the_base_a_decision_sends_back(lab_scratch, main_scratch):
    w = await _world(lab_scratch, main_scratch)
    async with lab_scratch.begin() as conn:
        head = await fx.add_label(conn, U2, w["mode"], present=None)            # a cleared cell: still a head
    p = next(x for x in (await proposals.queue(fx.owner(), None, kind="cell"))["proposals"] if x["id"] == w["p2"])
    assert p["base"] == head


async def test_a_running_grouping_run_shows_its_drafts_only_once_it_settles(lab_scratch, main_scratch):
    async with main_scratch.begin() as conn:
        await fx.add_turn(conn, U1)
    async with lab_scratch.begin() as conn:
        note = await fx.add_note(conn, U1)
        running = await fx.add_run(conn, "grouping", job_status="running")
        pid = await fx.add_proposal(conn, running, kind="group", suggestion="no_mode_fits", annotation_ids=[note])
    assert (await proposals.queue(fx.owner(), None, kind="group"))["proposals"] == []
    async with lab_scratch.begin() as conn:
        await conn.execute(text("UPDATE tracelab.jobs SET status = 'done', finished_at = now() WHERE kind = 'proposal_grouping'"))
    listed = await proposals.queue(fx.owner(), None, kind="group")
    assert [p["id"] for p in listed["proposals"]] == [pid]
    note_out = listed["proposals"][0]["notes"][0]
    assert note_out["id"] == note and note_out["note"] and note_out["step_label"] == "Answer"


async def test_a_page_is_never_longer_than_two_hundred_whatever_is_asked(lab_scratch, main_scratch):
    async with lab_scratch.begin() as conn:
        mid = await fx.add_mode(conn, "counts_cancelled_jobs_as_open", status="confirmed")
        run = await fx.add_run(conn, "cells")
        for i in range(205):
            await fx.add_proposal(conn, run, turn_id=f"turn-v{i:015d}", mode_id=mid)
    out = await proposals.queue(fx.owner(), None, kind="cell", limit=10 ** 6)
    assert len(out["proposals"]) == proposals.QUEUE_LIMIT_MAX == 200 and out["total"] == 205
    rest = await proposals.queue(fx.owner(), None, kind="cell", limit=10 ** 6, offset=200)
    assert len(rest["proposals"]) == 5


async def test_what_a_suggestion_cost_is_for_the_platform_team_only(lab_scratch, main_scratch):
    w = await _world(lab_scratch, main_scratch)
    async with lab_scratch.begin() as conn:
        await conn.execute(text("UPDATE tracelab.proposals SET usd = 0.012300"))
    mine = (await proposals.queue(fx.admin(), fx.ORG_A, kind="cell"))["proposals"]
    assert mine and all(p["usd"] is None for p in mine)
    owner = (await proposals.queue(fx.owner(), None, kind="cell"))["proposals"]
    assert owner and all(p["usd"] == 0.0123 for p in owner)
    decided = await proposals.decide(fx.admin(), fx.ORG_A, w["p1"], decision="accept")
    assert decided["proposal"]["usd"] is None
