"""The Labels grid (labels.matrix), Completeness and Per mode against a scratch database: which modes the grid shows,
paging and the missing filter, company scope, the blind rule applied to every figure, withdrawn batches, pending
suggestions, withdrawn notes, redaction. Needs the scratch database (chat rows on the main engine, Trace Lab's own in
the lab database)."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

import trace_lab_m4_fixtures as fx
from tl_scratch import lab_scratch, main_scratch, requires_scratch  # noqa: F401
from trace_lab import labels
from trace_lab.review_errors import Forbidden

pytestmark = requires_scratch
NOW = datetime.now(timezone.utc).replace(microsecond=0)


def _tid(group: str, i: int) -> str:
    return f"turn-{group}{i:015d}"


async def _answer(lab, main, tid, org=fx.ORG_A, *, minutes_ago=10, verdict="wrong", reviewer=None,
                  question="How many work orders are open at Bishopsgate?", batch=None):
    async with main.begin() as conn:
        await fx.add_turn(conn, tid, org, session_id=f"sess-{tid}", started_at=NOW - timedelta(minutes=minutes_ago),
                          question=question)
    async with lab.begin() as conn:
        note = await fx.add_note(conn, tid, org, verdict=verdict, reviewer=reviewer)
        if batch:
            await fx.add_batch_item(conn, tid, org, strategy=batch[0], plan_step=batch[1])
    return note


async def _modes(lab, **statuses):
    out = {}
    async with lab.begin() as conn:
        for key, status in statuses.items():
            out[key] = await fx.add_mode(conn, f"mode_{key}", status=status)
    return out


async def test_the_grid_shows_confirmed_and_frozen_modes_unless_asked_for_more(lab_scratch, main_scratch):
    await _answer(lab_scratch, main_scratch, _tid("a", 1))
    m = await _modes(lab_scratch, c="confirmed", f="frozen", k="candidate", p="proposed", r="retired", x="rejected")
    default = await labels.matrix(fx.admin(), fx.ORG_A)
    assert {x["id"] for x in default["modes"]} == {m["c"], m["f"]}
    wider = await labels.matrix(fx.admin(), fx.ORG_A, statuses=("candidate", "confirmed", "frozen", "retired", "proposed"))
    assert {x["id"] for x in wider["modes"]} == {m["c"], m["f"], m["k"]}       # retired, proposed, rejected are never live
    only = await labels.matrix(fx.admin(), fx.ORG_A, mode_ids=[m["c"].upper()])
    assert [x["id"] for x in only["modes"]] == [m["c"]] and set(only["rows"][0]["cells"]) == {m["c"]}
    none = await labels.matrix(fx.admin(), fx.ORG_A, mode_ids=["not-an-id"])
    assert none["modes"] == [] and none["totals"]["required"] == 0
    shown = default["modes"][0]
    assert set(shown) == {"id", "name", "status", "definition", "boundary", "agent", "agent_label", "level",
                          "judge_eligible"}                                       # nothing about who proposed or decided
    assert shown["agent_label"] == "Work orders" and shown["judge_eligible"] is True


async def test_paging_the_missing_filter_and_the_newest_first_order(lab_scratch, main_scratch):
    ids = [_tid("b", i) for i in range(5)]
    for i, t in enumerate(ids):                                                  # i = 0 is the oldest answer
        await _answer(lab_scratch, main_scratch, t, minutes_ago=100 - i)
    m = (await _modes(lab_scratch, c="confirmed"))["c"]
    async with lab_scratch.begin() as conn:
        for t in ids[:2]:                                                        # the two oldest are decided
            await fx.add_label(conn, t, m, present=True)
    everything = await labels.matrix(fx.admin(), fx.ORG_A)
    assert [r["turn_id"] for r in everything["rows"]] == list(reversed(ids)) and everything["total_rows"] == 5
    page = await labels.matrix(fx.admin(), fx.ORG_A, limit=2, offset=1)
    assert [r["turn_id"] for r in page["rows"]] == [ids[3], ids[2]] and page["total_rows"] == 5
    missing = await labels.matrix(fx.admin(), fx.ORG_A, state="missing")
    assert [r["turn_id"] for r in missing["rows"]] == [ids[4], ids[3], ids[2]] and missing["total_rows"] == 3
    assert missing["totals"] == {"reviewed_turns": 5, "required": 5, "done": 2, "missing": 3}      # totals ignore the filter
    assert [x["turn_id"] for x in missing["missing_preview"]] == [ids[4], ids[3], ids[2]]
    clamped = await labels.matrix(fx.admin(), fx.ORG_A, limit=0, offset=-4)
    assert len(clamped["rows"]) == 1
    assert len((await labels.matrix(fx.admin(), fx.ORG_A, limit=10_000))["rows"]) == 5


async def test_a_batch_narrows_the_rows_and_the_batches_are_listed(lab_scratch, main_scratch):
    await _answer(lab_scratch, main_scratch, _tid("c", 1), batch=("uniform", "initial_uniform"))
    await _answer(lab_scratch, main_scratch, _tid("c", 2), batch=("diversity", "cluster_representatives"))
    await _answer(lab_scratch, main_scratch, _tid("c", 3))
    await _modes(lab_scratch, c="confirmed")
    out = await labels.matrix(fx.admin(), fx.ORG_A)
    assert {b["plan_step"] for b in out["batches"]} == {"initial_uniform", "cluster_representatives"}
    one = out["batches"][0]["id"]
    narrowed = await labels.matrix(fx.admin(), fx.ORG_A, batch_id=one)
    assert len(narrowed["rows"]) == 1 and narrowed["rows"][0]["batch"]["id"] == one
    assert [r["batch"] for r in out["rows"] if r["turn_id"] == _tid("c", 3)] == [None]


async def test_each_company_sees_only_its_own_answers_and_the_platform_can_pick_one(lab_scratch, main_scratch):
    a, b = _tid("d", 1), _tid("d", 2)
    await _answer(lab_scratch, main_scratch, a, fx.ORG_A)
    await _answer(lab_scratch, main_scratch, b, fx.ORG_B)
    await _modes(lab_scratch, c="confirmed")
    assert [r["turn_id"] for r in (await labels.matrix(fx.admin(), fx.ORG_A))["rows"]] == [a]
    assert [r["turn_id"] for r in (await labels.matrix(fx.admin(fx.ORG_B), fx.ORG_B))["rows"]] == [b]
    stolen = await labels.matrix(fx.admin(), fx.ORG_A, filter_org=fx.ORG_B)            # a company admin cannot pick another
    assert [r["turn_id"] for r in stolen["rows"]] == [a]
    owner = await labels.matrix(fx.owner(), None)
    assert {r["turn_id"] for r in owner["rows"]} == {a, b}
    picked = await labels.matrix(fx.owner(), None, filter_org=fx.ORG_B)
    assert [r["turn_id"] for r in picked["rows"]] == [b]
    assert [r["turn_id"] for r in (await labels.matrix(fx.owner(), None, filter_org=fx.ORG_B.upper()))["rows"]] == [b]
    assert len((await labels.matrix(fx.owner(), None, filter_org=""))["rows"]) == 2           # none named: every company
    assert [r["turn_id"] for r in (await labels.matrix(fx.admin(), fx.ORG_A, filter_org="garbage"))["rows"]] == [a]
    with pytest.raises(Forbidden):
        await labels.matrix(fx.admin(), fx.ORG_B)
    assert (await labels.completeness(fx.admin(), fx.ORG_A))["reviewed_turns"] == 1
    assert (await labels.completeness(fx.owner(), None))["reviewed_turns"] == 2
    assert (await labels.completeness(fx.owner(), fx.ORG_B))["reviewed_turns"] == 1


async def test_a_withdrawn_batch_leaves_the_fair_rate_but_not_the_sample_fraction(lab_scratch, main_scratch):
    t = _tid("e", 1)
    await _answer(lab_scratch, main_scratch, t, batch=("uniform", "initial_uniform"))
    m = (await _modes(lab_scratch, c="confirmed"))["c"]
    async with lab_scratch.begin() as conn:
        await fx.add_label(conn, t, m, present=True)
    before = await labels.matrix(fx.admin(), fx.ORG_A)
    assert before["fair_sample"][m]["n"] == 1
    async with lab_scratch.begin() as conn:
        await conn.execute(text("UPDATE tracelab.sample_batches SET withdrawn_at = now()"))
    after = await labels.matrix(fx.admin(), fx.ORG_A)
    assert after["fair_sample"][m] == {"present": 0, "n": 0, "rate": None, "ci_low": None, "ci_high": None,
                                       "kind": "uniform_batches_only"}
    assert after["sample_fractions"][m]["decided"] == 1 and after["sample_fractions"][m]["fraction"] == 1.0


async def test_every_figure_counts_only_the_rows_the_caller_may_see(lab_scratch, main_scratch):
    mine, theirs = _tid("f", 1), _tid("f", 2)
    await _answer(lab_scratch, main_scratch, mine, reviewer=fx.admin(n=1), batch=("uniform", "initial_uniform"))
    await _answer(lab_scratch, main_scratch, theirs, reviewer=fx.admin(n=2), batch=("uniform", "initial_uniform"))
    m = (await _modes(lab_scratch, c="confirmed"))["c"]
    async with lab_scratch.begin() as conn:
        await conn.execute(text("UPDATE tracelab.sample_batches SET reviews_required = 2"))   # a second review is still due
        await fx.add_label(conn, theirs, m, present=True)
    out = await labels.matrix(fx.admin(n=1), fx.ORG_A)
    assert [r["turn_id"] for r in out["rows"]] == [mine] and out["hidden_rows"] == 1 and out["total_rows"] == 1
    assert out["totals"] == {"reviewed_turns": 1, "required": 1, "done": 0, "missing": 1}
    assert out["sample_fractions"][m]["decided"] == 0 and out["fair_sample"][m]["n"] == 0
    assert [x["turn_id"] for x in out["missing_preview"]] == [mine]
    owner = await labels.matrix(fx.owner(), None)
    assert owner["sample_fractions"][m] == {"present": 1, "decided": 1, "fraction": 1.0, "kind": "sample_fraction"}
    assert owner["hidden_rows"] == 0 and owner["totals"]["reviewed_turns"] == 2


async def test_a_pending_suggestion_shows_on_an_undecided_cell_only(lab_scratch, main_scratch):
    t1, t2 = _tid("q", 1), _tid("q", 2)
    await _answer(lab_scratch, main_scratch, t1)
    await _answer(lab_scratch, main_scratch, t2)
    m = (await _modes(lab_scratch, c="confirmed"))["c"]
    async with lab_scratch.begin() as conn:
        run = await fx.add_run(conn, "cells", org=fx.ORG_A)
        await fx.add_proposal(conn, run, kind="cell", turn_id=t1, mode_id=m, suggestion="present", evidence="It counts July.")
        await fx.add_proposal(conn, run, kind="cell", turn_id=t2, mode_id=m, suggestion="absent")
        await fx.add_label(conn, t2, m, present=False)
    rows = {r["turn_id"]: r for r in (await labels.matrix(fx.admin(), fx.ORG_A))["rows"]}
    shown = rows[t1]["cells"][m]
    assert shown["state"] == "unchecked" and shown["proposal"]["suggestion"] == "present"
    assert shown["proposal"]["evidence"] == "It counts July."
    assert rows[t2]["cells"][m]["state"] == "absent" and "proposal" not in rows[t2]["cells"][m]


async def test_a_label_whose_note_was_withdrawn_stays_and_is_flagged(lab_scratch, main_scratch):
    t = _tid("h", 1)
    note = await _answer(lab_scratch, main_scratch, t)
    m = (await _modes(lab_scratch, c="confirmed"))["c"]
    tomb = str(uuid.uuid4())
    async with lab_scratch.begin() as conn:
        await fx.add_label(conn, t, m, present=True, source="human_open_code", annotation_id=note)
        await conn.execute(text("UPDATE tracelab.annotations SET superseded_by = CAST(:n AS uuid) WHERE id = CAST(:o AS uuid)"),
                           {"n": tomb, "o": note})
        await conn.execute(text(
            "INSERT INTO tracelab.annotations (id, lineage_id, version, supersedes, kind, organization_id, "
            "conversation_key, turn_id, reviewer_id, reviewer_email, tombstone) SELECT CAST(:n AS uuid), lineage_id, 2, "
            "id, kind, organization_id, conversation_key, turn_id, reviewer_id, reviewer_email, true "
            "FROM tracelab.annotations WHERE id = CAST(:o AS uuid)"), {"n": tomb, "o": note})
    assert (await labels.matrix(fx.admin(), fx.ORG_A))["rows"] == []      # the withdrawn verdict leaves the answer unreviewed
    async with lab_scratch.begin() as conn:                  # the same answer judged again by someone else
        await fx.add_note(conn, t, fx.ORG_A, reviewer=fx.admin(n=3), verdict="right")
    row = (await labels.matrix(fx.owner(), None))["rows"][0]
    assert row["cells"][m]["state"] == "present" and row["cells"][m]["note_withdrawn"] is True


async def test_the_question_is_shown_redacted(lab_scratch, main_scratch):
    token = "Bearer abcdefghijklmnop123456"
    await _answer(lab_scratch, main_scratch, _tid("i", 1), question=f"Fetch it with {token} please")
    await _modes(lab_scratch, c="confirmed")
    out = await labels.matrix(fx.admin(), fx.ORG_A)
    assert "abcdefghijklmnop123456" not in out["rows"][0]["question"] and "Fetch it with" in out["rows"][0]["question"]
    assert "abcdefghijklmnop123456" not in (await labels.completeness(fx.admin(), fx.ORG_A))["missing_preview"][0]["question"]


async def test_per_mode_counts_first_failures_beside_every_instance(lab_scratch, main_scratch):
    t = [_tid("j", i) for i in range(1, 6)]
    notes = [await _answer(lab_scratch, main_scratch, x) for x in t]
    async with lab_scratch.begin() as conn:
        m = await fx.add_mode(conn, "counts_cancelled_jobs_as_open", status="confirmed")
        part = await fx.add_mode(conn, "includes_cancelled_jobs", status="retired", merged_into=m)
        run = await fx.add_run(conn, "cells", org=fx.ORG_A)
        await fx.add_label(conn, t[0], m, present=True, source="human_open_code", annotation_id=notes[0])
        await fx.add_label(conn, t[1], m, present=True)                                       # an instance, not a first failure
        await fx.add_label(conn, t[2], m, present=False)
        await fx.add_label(conn, t[3], part, present=True, source="human_open_code", annotation_id=notes[3])
        await fx.add_proposal(conn, run, kind="cell", turn_id=t[4], mode_id=m, suggestion="present")
        await fx.add_proposal(conn, run, kind="cell", turn_id=t[2], mode_id=m, suggestion="present", is_canary=True)
        other = await fx.add_run(conn, "cells", org=fx.ORG_B)
        await fx.add_proposal(conn, other, kind="cell", turn_id=_tid("j", 9), mode_id=m, org=fx.ORG_B)
    rows = {r["mode_id"]: r for r in await labels.per_mode(fx.admin(), fx.ORG_A)}
    r = rows[m]
    assert (r["first_failure"], r["any_instance"], r["decided"]) == (2, 3, 4)               # t0 and the merged part's t3
    assert r["sample_fraction"] == {"present": 3, "decided": 4, "fraction": 0.75, "kind": "sample_fraction"}
    assert r["pending_suggestions"] == 1 and r["status"] == "confirmed" and part not in rows
    everyone = {x["mode_id"]: x for x in await labels.per_mode(fx.owner(), None)}
    assert everyone[m]["pending_suggestions"] == 2
    with pytest.raises(Forbidden):
        await labels.per_mode(fx.admin(), fx.ORG_B)


async def test_completeness_takes_the_statuses_it_is_asked_for(lab_scratch, main_scratch):
    await _answer(lab_scratch, main_scratch, _tid("k", 1))
    await _modes(lab_scratch, c="confirmed", k="candidate")
    assert (await labels.completeness(fx.admin(), fx.ORG_A))["modes"] == 1
    assert (await labels.completeness(fx.admin(), fx.ORG_A, statuses=("candidate", "confirmed", "frozen")))["modes"] == 2
    empty = await labels.completeness(fx.admin(), fx.ORG_A, statuses=("retired",))
    assert (empty["modes"], empty["required"], empty["missing_preview"]) == (0, 0, [])
