"""Verdict writes on the scratch Postgres: an edit is a new version and the latest wins, a stale base is a 409
carrying the current head, two first saves race to one row and one 409, a delete is a tombstone plus a delete
event, judging again continues the lineage, and two reviewers code the same answer without overwriting each
other (spec "Open coding ..." 2.4). The turn is read through the READ-ONLY main engine, the verdict is written
through the lab engine, and the turn's evidence is copied into the lab database first. Skips without
TRACE_LAB_TEST_DB_URL."""
from __future__ import annotations

import asyncio
import json
import uuid

import pytest
from sqlalchemy import text

from golden_seed import seed_result, seed_run, seed_set
from tl_head_race import lose_the_race
from tl_scratch import lab_scratch, main_scratch, requires_scratch  # noqa: F401
from trace_lab import annotations, evidence, review_errors
from trace_lab.annotations import Anchor
from trace_lab_m2_fixtures import add_span, add_turn, principal

pytestmark = requires_scratch

ORG = str(uuid.uuid4())
OTHER = str(uuid.uuid4())


@pytest.fixture
async def both(main_scratch, lab_scratch):  # noqa: F811
    """(writable main engine for seeding, lab engine). The code under test reaches them through db.*_engine()."""
    return main_scratch, lab_scratch


async def _rows(lab, tid):
    async with lab.connect() as conn:
        return [dict(r) for r in (await conn.execute(text(
            "SELECT id::text AS id, version, superseded_by::text AS superseded_by, tombstone, verdict "
            "FROM tracelab.annotations WHERE turn_id = :t ORDER BY version"), {"t": tid})).mappings().all()]


async def test_an_edit_is_a_new_version_and_the_latest_wins(both):
    main, lab = both
    tid = await add_turn(main, 1, ORG)
    me = principal(ORG)
    v1 = await annotations.save_verdict(me, ORG, tid, verdict="right", note=None, anchor=None, base=None)
    assert (v1["version"], v1["note"], v1["anchor"], v1["blind"]) == (1, "no failure observed", None, True)
    assert (v1["conversation_key"], v1["reviewer_id"], v1["reviewer_email"]) == ("s:sess-1", str(me.user_id), me.email)
    v2 = await annotations.save_verdict(me, ORG, tid, verdict="wrong", note="Counted July, not August.",
                                        anchor=Anchor(None, "answer", "758"), base=v1["id"])
    assert (v2["version"], v2["supersedes"], v2["lineage_id"]) == (2, v1["id"], v1["id"])
    assert v2["anchor"]["span_name"] == "turn" and v2["anchor"]["span_kind"] == "answer" and v2["anchor"]["start"] == 11
    assert v2["anchor"]["quote"] == "758" and v2["anchor"]["occurrence"] == 1 and v2["anchor"]["span_id"] is None
    rows = await _rows(lab, tid)
    assert [(r["version"], r["superseded_by"]) for r in rows] == [(1, v2["id"]), (2, None)]
    async with lab.connect() as conn:
        org = (await conn.execute(text("SELECT DISTINCT organization_id::text FROM tracelab.annotations"))).scalar_one()
    assert org == ORG


async def test_a_stale_base_is_refused_with_the_current_head(both):
    main, _ = both
    tid = await add_turn(main, 2, ORG)
    me = principal(ORG)
    v1 = await annotations.save_verdict(me, ORG, tid, verdict="right", note=None, anchor=None, base=None)
    v2 = await annotations.save_verdict(me, ORG, tid, verdict="right", note=None, anchor=None, base=v1["id"])
    with pytest.raises(review_errors.Stale) as e:
        await annotations.save_verdict(me, ORG, tid, verdict="right", note=None, anchor=None, base=v1["id"])
    assert e.value.current["id"] == v2["id"] and e.value.status == 409 and e.value.reason == "stale"
    with pytest.raises(review_errors.Stale):
        await annotations.save_verdict(me, ORG, tid, verdict="right", note=None, anchor=None, base=None)
    with pytest.raises(review_errors.Stale):
        await annotations.save_verdict(me, ORG, tid, verdict="right", note=None, anchor=None, base="not-an-id")
    ok = await annotations.save_verdict(me, ORG, tid, verdict="right", note=None, anchor=None, base=v2["id"].upper())
    assert ok["version"] == 3                                  # an id in capitals is still the same id


async def test_two_first_saves_race_to_one_row_and_one_409(both):
    main, lab = both
    tid = await add_turn(main, 3, ORG)
    me = principal(ORG)
    results = await asyncio.gather(
        annotations.save_verdict(me, ORG, tid, verdict="right", note=None, anchor=None, base=None),
        annotations.save_verdict(me, ORG, tid, verdict="right", note=None, anchor=None, base=None),
        return_exceptions=True)
    assert sorted(type(r).__name__ for r in results) == ["Stale", "dict"]
    assert len(await _rows(lab, tid)) == 1
    stale = next(r for r in results if isinstance(r, review_errors.Stale))
    assert stale.current is not None and stale.current["version"] == 1      # the re-read head, not None


async def test_two_edits_of_one_head_race_to_one_new_version(both):
    main, lab = both
    tid = await add_turn(main, 31, ORG)
    me = principal(ORG)
    v1 = await annotations.save_verdict(me, ORG, tid, verdict="right", note=None, anchor=None, base=None)
    results = await asyncio.gather(
        annotations.save_verdict(me, ORG, tid, verdict="right", note=None, anchor=None, base=v1["id"]),
        annotations.save_verdict(me, ORG, tid, verdict="right", note=None, anchor=None, base=v1["id"]),
        return_exceptions=True)
    assert sorted(type(r).__name__ for r in results) == ["Stale", "dict"]
    assert [r["version"] for r in await _rows(lab, tid)] == [1, 2]
    stale = next(r for r in results if isinstance(r, review_errors.Stale))
    assert stale.status == 409 and stale.current is not None and stale.current["version"] == 2   # the re-read head


async def test_an_edit_that_loses_the_head_race_is_a_409_with_the_re_read_head(both):
    # The loser waits on the head's FOR UPDATE lock; the winner supersedes the row and commits; under READ COMMITTED the
    # loser's locked read then matches nothing. It must re-read the live head, not report "no head" (current=None).
    main, lab = both
    tid = await add_turn(main, 32, ORG)
    me = principal(ORG)
    v1 = await annotations.save_verdict(me, ORG, tid, verdict="right", note=None, anchor=None, base=None)
    lost = await lose_the_race(lab, v1["id"], lambda: annotations.save_verdict(
        me, ORG, tid, verdict="wrong", note="Too late.", anchor=Anchor(None, "answer", "758"), base=v1["id"]))
    assert isinstance(lost, review_errors.Stale), repr(lost)
    assert lost.status == 409 and lost.reason == "stale"
    assert lost.current is not None and lost.current["version"] == 2 and lost.current["supersedes"] == v1["id"]
    assert [r["version"] for r in await _rows(lab, tid)] == [1, 2]            # the loser wrote nothing


async def test_a_delete_that_loses_the_head_race_is_a_409_not_a_404(both):
    main, lab = both
    tid = await add_turn(main, 42, ORG)
    me = principal(ORG)
    v1 = await annotations.save_verdict(me, ORG, tid, verdict="right", note=None, anchor=None, base=None)
    lost = await lose_the_race(lab, v1["id"], lambda: annotations.delete_verdict(me, ORG, tid, base=v1["id"]))
    assert isinstance(lost, review_errors.Stale), repr(lost)               # not NotFound(annotation_not_found)
    assert lost.status == 409 and lost.current["version"] == 2 and lost.current["tombstone"] is False
    assert [r["version"] for r in await _rows(lab, tid)] == [1, 2]
    v2_id = (await _rows(lab, tid))[-1]["id"]
    lost = await lose_the_race(lab, v2_id, lambda: annotations.delete_verdict(me, ORG, tid, base=v1["id"]), tombstone=True)
    assert isinstance(lost, review_errors.Stale), repr(lost)               # the winner deleted it: current is the tombstone
    assert lost.current["version"] == 3 and lost.current["tombstone"] is True


async def test_two_deletes_of_one_head_are_one_tombstone_and_one_409(both):
    main, lab = both
    tid = await add_turn(main, 43, ORG)
    me = principal(ORG)
    v1 = await annotations.save_verdict(me, ORG, tid, verdict="right", note=None, anchor=None, base=None)
    results = await asyncio.gather(
        annotations.delete_verdict(me, ORG, tid, base=v1["id"]),
        annotations.delete_verdict(me, ORG, tid, base=v1["id"]), return_exceptions=True)
    assert sorted(type(r).__name__ for r in results) == ["Stale", "dict"]    # a double click is a 409, never a 404
    assert [(r["version"], r["tombstone"]) for r in await _rows(lab, tid)] == [(1, False), (2, True)]
    stale = next(r for r in results if isinstance(r, review_errors.Stale))
    assert stale.status == 409 and stale.current["version"] == 2 and stale.current["tombstone"] is True


async def test_a_delete_after_the_head_moved_on_is_a_409_even_when_the_head_is_a_tombstone(both):
    # Sequential double click: the second request carries the old base and finds the tombstone as head.
    main, _ = both
    tid = await add_turn(main, 44, ORG)
    me = principal(ORG)
    v1 = await annotations.save_verdict(me, ORG, tid, verdict="right", note=None, anchor=None, base=None)
    tomb = await annotations.delete_verdict(me, ORG, tid, base=v1["id"])
    with pytest.raises(review_errors.Stale) as e:
        await annotations.delete_verdict(me, ORG, tid, base=v1["id"])
    assert e.value.current["id"] == tomb["id"] and e.value.current["tombstone"] is True
    with pytest.raises(review_errors.NotFound) as e:                           # nothing left to delete: still a 404
        await annotations.delete_verdict(me, ORG, tid, base=tomb["id"])
    assert e.value.reason == "annotation_not_found"


async def test_a_delete_is_a_tombstone_and_judging_again_continues_the_lineage(both):
    main, lab = both
    tid = await add_turn(main, 4, ORG)
    me = principal(ORG)
    v1 = await annotations.save_verdict(me, ORG, tid, verdict="right", note=None, anchor=None, base=None)
    with pytest.raises(review_errors.Stale):
        await annotations.delete_verdict(me, ORG, tid, base=str(uuid.uuid4()))
    tomb = await annotations.delete_verdict(me, ORG, tid, base=v1["id"])
    assert tomb["tombstone"] is True and tomb["version"] == 2 and tomb["verdict"] is None and tomb["anchor"] is None
    with pytest.raises(review_errors.NotFound) as e:
        await annotations.delete_verdict(me, ORG, tid, base=tomb["id"])
    assert e.value.reason == "annotation_not_found"
    v3 = await annotations.save_verdict(me, ORG, tid, verdict="right", note=None, anchor=None, base=tomb["id"])
    assert (v3["version"], v3["lineage_id"]) == (3, v1["id"])
    async with lab.connect() as conn:
        actions = [r[0] for r in (await conn.execute(text(
            "SELECT action FROM tracelab.annotation_events WHERE turn_id = :t ORDER BY seq"), {"t": tid})).all()]
    assert actions == ["create", "delete", "edit"]


async def test_a_delete_with_nothing_to_delete_is_annotation_not_found(both):
    main, _ = both
    tid = await add_turn(main, 41, ORG)
    with pytest.raises(review_errors.NotFound) as e:
        await annotations.delete_verdict(principal(ORG), ORG, tid, base=str(uuid.uuid4()))
    assert e.value.reason == "annotation_not_found"


async def test_a_step_quote_copies_the_step_and_a_missing_one_is_refused(both):
    main, _ = both
    tid = await add_turn(main, 5, ORG)
    span = await add_span(main, tid, output={"count": 758, "period": "2026-07"}, org=ORG)
    other = await add_span(main, await add_turn(main, 6, ORG), org=ORG)
    me = principal(ORG)
    v = await annotations.save_verdict(me, ORG, tid, verdict="wrong", note="Read July.",
                                       anchor=Anchor(span, "output", '"period": "2026-07"'), base=None)
    assert (v["anchor"]["span_name"], v["anchor"]["span_kind"], v["anchor"]["span_agent"]) == ("answer_from_records", "tool", "wo_engine")
    assert v["anchor"]["span_id"] == span and v["anchor"]["field"] == "output"
    with pytest.raises(review_errors.Invalid) as e:
        await annotations.save_verdict(principal(ORG), ORG, tid, verdict="wrong", note="x",
                                       anchor=Anchor(other, "output", "758"), base=None)
    assert e.value.reason == "span_not_in_turn"
    with pytest.raises(review_errors.Invalid) as e:
        await annotations.save_verdict(principal(ORG), ORG, tid, verdict="wrong", note="x",
                                       anchor=Anchor(None, "answer", "758", 2), base=None)
    assert e.value.reason == "quote_not_found"
    with pytest.raises(review_errors.Invalid) as e:
        await annotations.save_verdict(principal(ORG), ORG, tid, verdict="wrong", note="x",
                                       anchor=Anchor(span, "output", '"period": "2026-08"'), base=None)
    assert e.value.reason == "quote_not_found"


async def test_two_reviewers_code_the_same_answer_without_overwriting(both):
    main, lab = both
    tid = await add_turn(main, 7, ORG)
    a, b = principal(ORG), principal(ORG)
    va = await annotations.save_verdict(a, ORG, tid, verdict="wrong", note="Counted July.",
                                        anchor=Anchor(None, "answer", "758"), base=None)
    vb = await annotations.save_verdict(b, ORG, tid, verdict="right", note=None, anchor=None, base=None)
    assert va["blind"] is True and vb["blind"] is True
    heads = [r for r in await _rows(lab, tid) if r["superseded_by"] is None]
    assert sorted(r["verdict"] for r in heads) == ["right", "wrong"]
    va2 = await annotations.save_verdict(a, ORG, tid, verdict="wrong", note="Counted July, and the wrong site.",
                                         anchor=Anchor(None, "answer", "758"), base=va["id"])
    assert va2["blind"] is False                       # b already had a verdict when this version was saved


async def test_another_companys_turn_and_a_turn_without_a_company_are_not_found(both):
    main, lab = both
    other = await add_turn(main, 8, OTHER)
    orphan = await add_turn(main, 9, None)
    for tid in (other, orphan, "turn-does-not-exist"):
        with pytest.raises(review_errors.NotFound) as e:
            await annotations.save_verdict(principal(ORG), ORG, tid, verdict="right", note=None, anchor=None, base=None)
        assert e.value.reason == "turn_not_found"
        with pytest.raises(review_errors.NotFound):
            await annotations.delete_verdict(principal(ORG), ORG, tid, base=str(uuid.uuid4()))
    assert await _rows(lab, other) == [] and await _rows(lab, orphan) == []
    async with lab.connect() as conn:
        assert (await conn.execute(text("SELECT count(*) FROM tracelab.evidence_turns"))).scalar() == 0   # nothing copied either


async def test_a_plain_user_cannot_judge_a_turn_that_is_not_theirs(both):
    main, _ = both
    tid = await add_turn(main, 10, ORG)
    user = principal(ORG, role="user")
    with pytest.raises(review_errors.NotFound) as e:
        await annotations.save_verdict(user, ORG, tid, verdict="right", note=None, anchor=None, base=None)
    assert e.value.reason == "turn_not_found"


async def test_the_conversation_key_follows_the_session(both):
    main, _ = both
    blank = await add_turn(main, 11, ORG, session="")
    rerun = await add_turn(main, 12, ORG, session="rerun")
    me = principal(ORG)
    v = await annotations.save_verdict(me, ORG, blank, verdict="right", note=None, anchor=None, base=None)
    assert v["conversation_key"] == f"t:{blank}"
    v = await annotations.save_verdict(me, ORG, rerun, verdict="right", note=None, anchor=None, base=None)
    assert v["conversation_key"] == f"r:{rerun}"


# ── evidence: the turn is kept in the lab database before a note relies on it ─────────────────────

async def test_saving_a_verdict_copies_the_turn_and_its_spans_into_the_lab_database(both):
    main, lab = both
    tid = await add_turn(main, 20, ORG)
    await add_span(main, tid, org=ORG)
    await annotations.save_verdict(principal(ORG), ORG, tid, verdict="right", note=None, anchor=None, base=None)
    async with lab.connect() as conn:
        row = (await conn.execute(text("SELECT organization_id::text, reason FROM tracelab.evidence_turns WHERE turn_id = :t"),
                                  {"t": tid})).one()
        spans = (await conn.execute(text("SELECT count(*) FROM tracelab.evidence_spans WHERE turn_id = :t"), {"t": tid})).scalar()
    assert tuple(row) == (ORG, "annotated") and spans == 1


async def test_a_refused_quote_copies_nothing_and_writes_nothing(both):
    main, lab = both
    tid = await add_turn(main, 21, ORG)
    with pytest.raises(review_errors.Invalid):
        await annotations.save_verdict(principal(ORG), ORG, tid, verdict="wrong", note="x",
                                       anchor=Anchor(None, "answer", "no such text"), base=None)
    async with lab.connect() as conn:
        assert (await conn.execute(text("SELECT count(*) FROM tracelab.evidence_turns"))).scalar() == 0
    assert await _rows(lab, tid) == []


async def test_when_the_evidence_cannot_be_kept_the_verdict_is_not_saved(both, monkeypatch):
    main, lab = both
    tid = await add_turn(main, 22, ORG)

    async def broken(*a, **k):
        raise RuntimeError("the lab database is down")

    monkeypatch.setattr(evidence, "snapshot", broken)
    with pytest.raises(RuntimeError):
        await annotations.save_verdict(principal(ORG), ORG, tid, verdict="right", note=None, anchor=None, base=None)
    assert await _rows(lab, tid) == []


async def test_a_note_still_checks_its_quote_after_the_chat_purged_the_spans(both):
    main, lab = both
    tid = await add_turn(main, 23, ORG)
    span = await add_span(main, tid, output={"count": 758, "period": "2026-07"}, org=ORG)
    me = principal(ORG)
    anchor = Anchor(span, "output", '"period": "2026-07"')
    v1 = await annotations.save_verdict(me, ORG, tid, verdict="wrong", note="Read July.", anchor=anchor, base=None)
    async with main.begin() as conn:                    # svc-deepagents' retention purge: the spans go, the turn row stays
        await conn.execute(text("DELETE FROM plenum_cafm.agent_trace_spans"))
    v2 = await annotations.save_verdict(me, ORG, tid, verdict="wrong", note="Read July, not August.", anchor=anchor,
                                        base=v1["id"])
    assert v2["version"] == 2 and v2["anchor"]["span_name"] == "answer_from_records" and v2["anchor"]["start"] == 20
    async with main.begin() as conn:                    # and when the whole turn row is gone as well
        await conn.execute(text("DELETE FROM plenum_cafm.agent_trace_turns"))
    v3 = await annotations.save_verdict(me, ORG, tid, verdict="right", note=None, anchor=None, base=v2["id"])
    assert v3["version"] == 3
    tomb = await annotations.delete_verdict(me, ORG, tid, base=v3["id"])
    assert tomb["tombstone"] is True


async def test_a_turn_only_the_snapshot_still_holds_is_judged_by_admins_and_not_by_plain_users(both):
    main, _ = both
    tid = await add_turn(main, 24, ORG)
    await evidence.snapshot([tid], organization_id=ORG, reason="sampled")
    async with main.begin() as conn:
        await conn.execute(text("DELETE FROM plenum_cafm.agent_trace_spans"))
        await conn.execute(text("DELETE FROM plenum_cafm.agent_trace_turns"))
    with pytest.raises(review_errors.NotFound):
        await annotations.save_verdict(principal(ORG, role="user"), ORG, tid, verdict="right", note=None, anchor=None, base=None)
    with pytest.raises(review_errors.NotFound):
        await annotations.save_verdict(principal(OTHER), OTHER, tid, verdict="right", note=None, anchor=None, base=None)
    ok = await annotations.save_verdict(principal(ORG), ORG, tid, verdict="right", note=None, anchor=None, base=None)
    assert ok["version"] == 1


# ── golden answers can be judged: they live in the lab database ───────────────────────────────────

GOLDEN_TID = "turn-g00000000000001"


async def _seed_golden(lab, *, org: str = ORG, tid: str = GOLDEN_TID):
    async with lab.begin() as conn:
        s = await seed_set(conn, org, cases=(("21", ()),))
        run = await seed_run(conn, org, s["set_id"])
        _, got = await seed_result(conn, org, run, s["cases"]["21"], "21", turn_id=tid)
        sid = f"golden-{run[:8]}-21-r1"
        turn = {"turn_id": tid, "session_id": sid, "organization_id": org, "user_id": str(uuid.uuid4()),
                "email": "asker@example.com", "question": "How many work orders?", "answer": "759 work orders.",
                "ok": True, "models": ["m"], "started_at": "2026-10-01T10:00:00+00:00"}
        await conn.execute(text("INSERT INTO tracelab.evidence_turns (turn_id, organization_id, turn, reason) "
                                "VALUES (:t, CAST(:o AS uuid), CAST(:j AS jsonb), 'golden')"),
                           {"t": tid, "o": org, "j": json.dumps(turn)})
    return sid


async def test_a_golden_answer_can_be_judged_by_its_own_company_only(both):
    _, lab = both
    sid = await _seed_golden(lab)
    me = principal(ORG)
    v = await annotations.save_verdict(me, ORG, GOLDEN_TID, verdict="wrong", note="Off by one.",
                                       anchor=Anchor(None, "answer", "759"), base=None)
    assert v["conversation_key"] == "s:" + sid and v["anchor"]["start"] == 0
    other = principal(OTHER)
    with pytest.raises(review_errors.NotFound) as e:
        await annotations.save_verdict(other, OTHER, GOLDEN_TID, verdict="right", note=None, anchor=None, base=None)
    assert e.value.reason == "turn_not_found"
    again = await annotations.save_verdict(me, ORG, GOLDEN_TID, verdict="right", note=None, anchor=None, base=v["id"])
    assert again["version"] == 2


async def test_the_golden_question_is_the_one_the_case_asked(both):
    _, lab = both
    await _seed_golden(lab)
    # The Conversations view shows a golden turn's question as the run recorded it (golden_results.question), not the
    # stored turn's wrapped text, so a quote is checked against that.
    with pytest.raises(review_errors.Invalid) as e:
        await annotations.save_verdict(principal(ORG), ORG, GOLDEN_TID, verdict="wrong", note="x",
                                       anchor=Anchor(None, "question", "How many work orders"), base=None)
    assert e.value.reason == "quote_not_found"
    v = await annotations.save_verdict(principal(ORG), ORG, GOLDEN_TID, verdict="wrong", note="Misread the ask.",
                                       anchor=Anchor(None, "question", "Message 0 of 21"), base=None)
    assert v["anchor"]["quote"] == "Message 0 of 21" and v["anchor"]["start"] == 0


async def test_a_turn_id_is_text_of_a_sane_length(both):
    me = principal(ORG)
    for bad in ("", "x" * 201):
        with pytest.raises(review_errors.NotFound):
            await annotations.save_verdict(me, ORG, bad, verdict="right", note=None, anchor=None, base=None)


async def test_a_writer_that_cannot_get_the_head_lock_in_time_is_a_409_and_leaves_no_open_connection(both, monkeypatch):
    # The head is held by another transaction for the whole test: the writer must not wait for it for 10 seconds
    # and surface a timeout, it must be refused (409) with the live head once its short lock wait runs out.
    main, lab = both
    monkeypatch.setattr(annotations, "LOCK_WAIT_MS", 300)
    tid = await add_turn(main, 43, ORG)
    me = principal(ORG)
    v1 = await annotations.save_verdict(me, ORG, tid, verdict="right", note=None, anchor=None, base=None)
    async with lab.begin() as holder:
        await holder.execute(text("SELECT 1 FROM tracelab.annotations WHERE id = CAST(:h AS uuid) FOR UPDATE"),
                             {"h": v1["id"]})
        for call in (
                lambda: annotations.save_verdict(me, ORG, tid, verdict="right", note=None, anchor=None, base=v1["id"]),
                lambda: annotations.delete_verdict(me, ORG, tid, base=v1["id"])):
            started = asyncio.get_running_loop().time()
            with pytest.raises(review_errors.Stale) as e:
                await call()
            assert asyncio.get_running_loop().time() - started < 5
            assert e.value.status == 409 and e.value.current["id"] == v1["id"]
        await holder.execute(text("SELECT pg_stat_clear_snapshot()"))
        left = (await holder.execute(text(
            "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() AND pid <> pg_backend_pid() "
            "AND state LIKE 'idle in transaction%'"))).scalar_one()
    assert left == 0                                                       # the refused writers rolled back and let go
    assert [r["version"] for r in await _rows(lab, tid)] == [1]
