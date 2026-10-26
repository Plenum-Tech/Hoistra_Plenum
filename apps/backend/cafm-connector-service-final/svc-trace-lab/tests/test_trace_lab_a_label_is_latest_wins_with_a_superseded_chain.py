"""One decision per (answer, mode); a change supersedes the head; a stale base is refused with the current head;
two first writes race to one row and one 409; a writer that waited on the head gets the new head; a clear is a
tombstone; merged and merely proposed modes take no labels; a labelled answer's evidence is kept (spec 6.1, G0 2.6).
Needs the scratch database: the chat's tables are read through the read-only main engine, the labels live in the lab
database."""
from __future__ import annotations

import asyncio
import time
import uuid

import pytest
from sqlalchemy import text

import trace_lab_m4_fixtures as fx
from tl_scratch import lab_scratch, main_scratch, requires_scratch  # noqa: F401
from trace_lab import labels
from trace_lab.review_errors import Conflict, Invalid, NotFound, Stale

pytestmark = requires_scratch
TURN = "turn-a000000000000001"


async def _setup(lab, main, *, status="confirmed"):
    async with main.begin() as conn:
        await fx.add_turn(conn, TURN)
    async with lab.begin() as conn:
        return await fx.add_mode(conn, "counts_cancelled_jobs_as_open", status=status)


async def test_a_change_is_a_new_row_and_the_latest_wins(lab_scratch, main_scratch):
    mid = await _setup(lab_scratch, main_scratch)
    first = await labels.set_label(fx.admin(), fx.ORG_A, TURN, mid, present=True, base=None)
    assert first["cell"]["state"] == "present" and first["cell"]["base"] == first["label"]["id"]
    second = await labels.set_label(fx.admin(), fx.ORG_A, TURN, mid, present=False, base=first["label"]["id"])
    assert second["cell"]["state"] == "absent"
    chain = (await labels.history(fx.owner(), None, TURN, mid))["chain"]
    assert [c["id"] for c in chain] == [second["label"]["id"], first["label"]["id"]]
    assert chain[0]["supersedes"] == first["label"]["id"] and chain[1]["superseded_by"] == second["label"]["id"]
    assert chain[0]["superseded_by"] is None and chain[0]["source"] == "human_structured_label"


async def test_the_label_carries_the_answers_company_session_and_the_deciders_name(lab_scratch, main_scratch):
    async with main_scratch.begin() as conn:
        await fx.add_turn(conn, TURN, session_id="sess-label-1")
    async with lab_scratch.begin() as conn:
        mid = await fx.add_mode(conn, "counts_cancelled_jobs_as_open", status="confirmed")
    who = fx.admin()
    out = (await labels.set_label(who, fx.ORG_A, TURN, mid, present=True, base=None, note="Counts July."))["label"]
    assert (out["organization_id"], out["session_id"], out["note"]) == (fx.ORG_A, "sess-label-1", "Counts July.")
    assert (out["decided_by"], out["decided_by_email"], out["approval"]) == (str(who.user_id), who.email, "single")


async def test_a_stale_base_is_refused_with_the_current_head(lab_scratch, main_scratch):
    mid = await _setup(lab_scratch, main_scratch)
    first = await labels.set_label(fx.admin(), fx.ORG_A, TURN, mid, present=True, base=None)
    with pytest.raises(Stale) as e:
        await labels.set_label(fx.admin(n=2), fx.ORG_A, TURN, mid, present=False, base=None)
    assert e.value.current["id"] == first["label"]["id"]
    with pytest.raises(Stale):
        await labels.set_label(fx.admin(n=2), fx.ORG_A, TURN, mid, present=False, base=str(uuid.uuid4()))
    assert len((await labels.history(fx.owner(), None, TURN, mid))["chain"]) == 1          # nothing was written


async def test_two_first_writes_race_to_one_row_and_one_conflict(lab_scratch, main_scratch):
    mid = await _setup(lab_scratch, main_scratch)
    results = await asyncio.gather(
        labels.set_label(fx.admin(), fx.ORG_A, TURN, mid, present=True, base=None),
        labels.set_label(fx.admin(n=2), fx.ORG_A, TURN, mid, present=False, base=None),
        return_exceptions=True)
    assert sum(isinstance(r, dict) for r in results) == 1
    losers = [r for r in results if isinstance(r, Stale)]
    assert len(losers) == 1 and losers[0].current is not None and losers[0].current["id"]
    assert len((await labels.history(fx.owner(), None, TURN, mid))["chain"]) == 1


async def _waiting_for_a_lock(lab) -> bool:
    async with lab.connect() as conn:
        return bool((await conn.execute(text(
            "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
            "AND wait_event_type = 'Lock' AND pid <> pg_backend_pid()"))).scalar_one())


async def test_a_writer_that_waited_on_the_head_gets_the_winners_head_in_the_409(lab_scratch, main_scratch):
    mid = await _setup(lab_scratch, main_scratch)
    first = (await labels.set_label(fx.admin(), fx.ORG_A, TURN, mid, present=True, base=None))["label"]["id"]
    winner = str(uuid.uuid4())
    async with lab_scratch.connect() as holder:
        trans = await holder.begin()
        await holder.execute(text("SELECT id FROM tracelab.labels WHERE id = CAST(:i AS uuid) FOR UPDATE"), {"i": first})
        task = asyncio.create_task(labels.set_label(fx.admin(n=2), fx.ORG_A, TURN, mid, present=False, base=first))
        deadline = time.monotonic() + 8
        while not await _waiting_for_a_lock(lab_scratch):
            assert time.monotonic() < deadline and not task.done(), "the writer never queued behind the head lock"
            await asyncio.sleep(0.05)
        await holder.execute(text("UPDATE tracelab.labels SET superseded_by = CAST(:w AS uuid) WHERE id = CAST(:i AS uuid)"),
                             {"w": winner, "i": first})
        await holder.execute(text(
            "INSERT INTO tracelab.labels (id, organization_id, turn_id, mode_id, present, source, decided_by, supersedes) "
            "VALUES (CAST(:w AS uuid), CAST(:o AS uuid), :t, CAST(:m AS uuid), false, 'human_structured_label', "
            "CAST(:by AS uuid), CAST(:i AS uuid))"),
            {"w": winner, "o": fx.ORG_A, "t": TURN, "m": mid, "by": fx.OWNER_ID, "i": first})
        await trans.commit()
        with pytest.raises(Stale) as e:
            await task
    assert e.value.current["id"] == winner


async def test_a_clear_is_a_tombstone_and_reads_unchecked(lab_scratch, main_scratch):
    mid = await _setup(lab_scratch, main_scratch)
    first = await labels.set_label(fx.admin(), fx.ORG_A, TURN, mid, present=True, base=None)
    cleared = await labels.set_label(fx.admin(), fx.ORG_A, TURN, mid, present=None, base=first["label"]["id"])
    assert cleared["label"]["tombstone"] is True and cleared["cell"]["state"] == "unchecked"
    again = await labels.set_label(fx.admin(), fx.ORG_A, TURN, mid, present=False, base=cleared["label"]["id"])
    assert again["cell"]["state"] == "absent" and len((await labels.history(fx.owner(), None, TURN, mid))["chain"]) == 3


async def test_a_merged_mode_takes_no_labels_and_names_its_live_mode(lab_scratch, main_scratch):
    async with main_scratch.begin() as conn:
        await fx.add_turn(conn, TURN)
    async with lab_scratch.begin() as conn:
        target = await fx.add_mode(conn, "counts_cancelled_jobs_as_open", status="confirmed")
        source = await fx.add_mode(conn, "includes_cancelled_jobs", status="retired", merged_into=target)
    with pytest.raises(Conflict) as e:
        await labels.set_label(fx.admin(), fx.ORG_A, TURN, source, present=True, base=None)
    assert e.value.reason == "mode_not_live" and e.value.extra["live_mode_id"] == target


@pytest.mark.parametrize("status", ["proposed", "rejected"])
async def test_a_proposed_or_rejected_mode_takes_no_labels(lab_scratch, main_scratch, status):
    mid = await _setup(lab_scratch, main_scratch, status=status)
    with pytest.raises(Conflict) as e:
        await labels.set_label(fx.admin(), fx.ORG_A, TURN, mid, present=True, base=None)
    assert e.value.reason == "mode_not_live" and e.value.extra["live_mode_id"] is None


async def test_an_unknown_mode_is_not_found(lab_scratch, main_scratch):
    await _setup(lab_scratch, main_scratch)
    for bad in (str(uuid.uuid4()), "not-a-uuid"):
        with pytest.raises(NotFound) as e:
            await labels.set_label(fx.admin(), fx.ORG_A, TURN, bad, present=True, base=None)
        assert e.value.reason == "mode_not_found"


async def test_another_companys_answer_is_not_found(lab_scratch, main_scratch):
    mid = await _setup(lab_scratch, main_scratch)
    with pytest.raises(NotFound) as e:
        await labels.set_label(fx.admin(fx.ORG_B), fx.ORG_B, TURN, mid, present=True, base=None)
    assert e.value.reason == "turn_not_found"
    with pytest.raises(NotFound):
        await labels.history(fx.admin(fx.ORG_B), fx.ORG_B, TURN, mid)
    with pytest.raises(NotFound):
        await labels.set_label(fx.admin(), fx.ORG_A, "turn-doesnotexist00", mid, present=True, base=None)


async def test_an_anchor_must_quote_the_answer_verbatim(lab_scratch, main_scratch):
    mid = await _setup(lab_scratch, main_scratch)
    with pytest.raises(Invalid) as e:
        await labels.set_label(fx.admin(), fx.ORG_A, TURN, mid, present=True, base=None,
                               anchor={"span_id": None, "field": "answer", "quote": "37 open", "occurrence": 1})
    assert e.value.reason == "quote_not_found"
    assert (await labels.history(fx.owner(), None, TURN, mid))["chain"] == []
    ok = await labels.set_label(fx.admin(), fx.ORG_A, TURN, mid, present=True, base=None,
                                anchor={"span_id": None, "field": "answer", "quote": "12 open", "occurrence": 1})
    assert ok["label"]["quote"] == "12 open" and ok["label"]["span_kind"] == "answer"
    assert ok["label"]["span_name"] == "turn" and ok["label"]["span_id"] is None


async def test_an_anchor_on_a_step_keeps_the_steps_name_kind_and_agent(lab_scratch, main_scratch):
    mid = await _setup(lab_scratch, main_scratch)
    async with main_scratch.begin() as conn:
        span = await fx.add_span(conn, TURN, output={"rows": 758})
    out = await labels.set_label(fx.admin(), fx.ORG_A, TURN, mid, present=True, base=None,
                                 anchor={"span_id": span, "field": "output", "quote": "758", "occurrence": 1})
    lab = out["label"]
    assert (lab["span_id"], lab["span_name"], lab["span_kind"], lab["span_agent"]) == (span, "query_table", "tool", "udr")
    with pytest.raises(Invalid) as e:
        await labels.set_label(fx.admin(), fx.ORG_A, TURN, mid, present=False, base=lab["id"],
                               anchor={"span_id": str(uuid.uuid4()), "field": "output", "quote": "758"})
    assert e.value.reason == "span_not_in_turn"


@pytest.mark.parametrize("anchor", [
    {"field": "answer", "quote": "   "}, {"field": "answer", "quote": ""}, {"field": "bogus", "quote": "12 open"},
    {"field": "answer", "quote": "12 open", "occurrence": 0}, {"field": "answer", "quote": "12 open", "occurrence": True},
    {"field": "output", "quote": "758"}, "12 open"])
async def test_a_malformed_anchor_is_refused_before_anything_is_written(lab_scratch, main_scratch, anchor):
    mid = await _setup(lab_scratch, main_scratch)
    with pytest.raises(Invalid):
        await labels.set_label(fx.admin(), fx.ORG_A, TURN, mid, present=True, base=None, anchor=anchor)
    assert (await labels.history(fx.owner(), None, TURN, mid))["chain"] == []


async def test_labelling_keeps_the_answers_evidence_in_the_lab_database(lab_scratch, main_scratch):
    mid = await _setup(lab_scratch, main_scratch)
    async with main_scratch.begin() as conn:
        await fx.add_span(conn, TURN, output={"rows": 12})
    await labels.set_label(fx.admin(), fx.ORG_A, TURN, mid, present=True, base=None)
    async with lab_scratch.connect() as conn:
        turn = (await conn.execute(text("SELECT reason, CAST(organization_id AS text) AS org FROM tracelab.evidence_turns "
                                        "WHERE turn_id = :t"), {"t": TURN})).mappings().first()
        spans = (await conn.execute(text("SELECT count(*) FROM tracelab.evidence_spans WHERE turn_id = :t"),
                                    {"t": TURN})).scalar()
    assert turn == {"reason": "labelled", "org": fx.ORG_A} and spans == 1


async def test_an_answers_evidence_is_captured_once_however_often_it_is_labelled(lab_scratch, main_scratch):
    mid = await _setup(lab_scratch, main_scratch)
    first = await labels.set_label(fx.admin(), fx.ORG_A, TURN, mid, present=True, base=None)
    assert first["label"]["id"]
    async with lab_scratch.connect() as conn:
        n = (await conn.execute(text("SELECT count(*) FROM tracelab.evidence_turns"))).scalar()
    assert n == 1                                            # captured once, never twice
    await labels.set_label(fx.admin(), fx.ORG_A, TURN, mid, present=False, base=first["label"]["id"])
    async with lab_scratch.connect() as conn:
        assert (await conn.execute(text("SELECT count(*) FROM tracelab.evidence_turns"))).scalar() == 1


async def test_the_platform_owner_labels_in_the_platform_view_and_a_plain_admin_without_a_company_cannot(
        lab_scratch, main_scratch):
    mid = await _setup(lab_scratch, main_scratch)
    out = await labels.set_label(fx.owner(), None, TURN, mid, present=True, base=None)
    assert out["label"]["organization_id"] == fx.ORG_A                       # the answer's company, not the caller's
    with pytest.raises(NotFound):
        await labels.set_label(fx.admin(), None, TURN, mid, present=False, base=out["label"]["id"])


async def test_cells_for_a_turn_are_blind_until_the_company_admin_has_judged_it(lab_scratch, main_scratch):
    from trace_lab import annotations
    mid = await _setup(lab_scratch, main_scratch)
    await labels.set_label(fx.owner(), None, TURN, mid, present=True, base=None)
    blind = await labels.cells_for_turn(fx.admin(), fx.ORG_A, TURN)
    assert blind == {"cells": [], "hidden": True}
    await annotations.save_verdict(fx.admin(), fx.ORG_A, TURN, verdict="right", note=None, anchor=None, base=None)
    seen = await labels.cells_for_turn(fx.admin(), fx.ORG_A, TURN)
    assert seen["hidden"] is False and [(c["mode_id"], c["state"]) for c in seen["cells"]] == [(mid, "present")]
    assert seen["cells"][0]["base"] and seen["cells"][0]["status"] == "confirmed"
    owner = await labels.cells_for_turn(fx.owner(), None, TURN)
    assert owner["hidden"] is False and owner["cells"][0]["state"] == "present"
