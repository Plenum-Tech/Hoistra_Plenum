"""Blind review end to end on the scratch Postgres: GET /annotations' data hides other reviewers until the caller
saves, shows them after, counts hidden comments, serves the active answer's block texts exactly as the
Conversations view renders them, keeps history to the caller's own rows while blind, and lets the platform owner
read everything. The turns come from the READ-ONLY main engine (or, for a golden answer, the lab database); the
annotations, events and sample batches are read from the lab database and the two are merged in Python. Skips
without TRACE_LAB_TEST_DB_URL."""
from __future__ import annotations

import asyncio
import json
import uuid

import pytest
from sqlalchemy import text

from golden_seed import seed_result, seed_run, seed_set
from tl_head_race import lose_the_race
from tl_scratch import lab_scratch, main_scratch, requires_scratch  # noqa: F401
from trace_lab import annotations, conversations, review_errors
from trace_lab.annotations import Anchor
from trace_lab_m2_fixtures import add_span, add_turn, principal

pytestmark = requires_scratch

ORG = str(uuid.uuid4())
OTHER = str(uuid.uuid4())


@pytest.fixture
async def both(main_scratch, lab_scratch):  # noqa: F811
    return main_scratch, lab_scratch


async def _batch(lab, org: str, turn: str, *, required: int = 1, reason: str = "uniform pick") -> str:
    bid = str(uuid.uuid4())
    async with lab.begin() as conn:
        await conn.execute(text("""
            INSERT INTO tracelab.sample_batches (id, organization_id, plan_step, strategy, seed, window_since,
                window_until, requested, selected, pool_size, counts_for_prevalence, reviews_required, created_by)
            VALUES (CAST(:b AS uuid), CAST(:o AS uuid), 'initial_uniform', 'uniform', 7, now() - interval '1 day',
                    now(), 1, 1, 1, true, :req, gen_random_uuid())"""), {"b": bid, "o": org, "req": required})
        await conn.execute(text("""
            INSERT INTO tracelab.sample_items (id, batch_id, organization_id, turn_id, position, reason)
            VALUES (gen_random_uuid(), CAST(:b AS uuid), CAST(:o AS uuid), :t, 1, :r)"""),
            {"b": bid, "o": org, "t": turn, "r": reason})
    return bid


async def _reveal(lab, org: str, turn: str, who) -> None:
    """What the reveal route (a later task) writes: a `reveal` event by the caller on the turn."""
    async with lab.begin() as conn:
        await conn.execute(text("""
            INSERT INTO tracelab.annotation_events (organization_id, turn_id, action, actor_id)
            VALUES (CAST(:o AS uuid), :t, 'reveal', CAST(:a AS uuid))"""), {"o": org, "t": turn, "a": str(who.user_id)})


async def test_others_are_hidden_until_you_save_then_shown(both):
    main, lab = both
    t1 = await add_turn(main, 1, ORG, session="sess-blind", minutes_ago=30)
    t2 = await add_turn(main, 2, ORG, session="sess-blind", minutes_ago=20)
    a, b = principal(ORG), principal(ORG)
    await annotations.save_verdict(a, ORG, t1, verdict="wrong", note="Counted July.", anchor=Anchor(None, "answer", "758"), base=None)
    await annotations.add_comment(a, ORG, "s:sess-blind", note="Same failure as last week.", turn_id=t1)

    before = await annotations.for_conversation(b, ORG, "s:sess-blind", turn_id=t1)
    st = before["turns"][t1]
    assert (st["mine"], st["others"], st["others_count"], st["others_hidden"]) == (None, None, 1, True)
    assert before["comments"] == [] and before["comments_hidden"] == 1
    assert before["blocks"][t1][":answer"] == "There were 758 work orders in August."
    assert before["ok"] is True and before["conversation_key"] == "s:sess-blind" and set(before["turns"]) == {t1, t2}
    hist = await annotations.history(b, ORG, t1)
    assert hist["versions"] == [] and hist["events"] == []

    await annotations.save_verdict(b, ORG, t1, verdict="right", note=None, anchor=None, base=None)
    await annotations.save_verdict(b, ORG, t2, verdict="right", note=None, anchor=None, base=None)
    after = await annotations.for_conversation(b, ORG, "s:sess-blind")
    assert after["turns"][t1]["others"][0]["note"] == "Counted July."
    assert after["turns"][t1]["mine"]["verdict"] == "right" and after["blocks"] is None
    assert after["comments_hidden"] == 0 and after["comments"][0]["note"] == "Same failure as last week."
    assert len((await annotations.history(b, ORG, t1))["versions"]) == 2


async def test_a_complete_sample_item_is_visible_and_the_next_verdict_is_not_blind(both):
    main, lab = both
    t1 = await add_turn(main, 3, ORG)
    a, b = principal(ORG), principal(ORG)
    await _batch(lab, ORG, t1, required=1)
    await annotations.save_verdict(a, ORG, t1, verdict="right", note=None, anchor=None, base=None)
    seen = await annotations.for_turns(b, ORG, [t1])
    assert seen[t1]["others"] is not None and seen[t1]["reviews_required"] == 1
    assert seen[t1]["sample_item"]["reason"] == "uniform pick"
    assert seen[t1]["sample_item"]["plan_step"] == "initial_uniform" and seen[t1]["sample_item"]["counts_for_prevalence"] is True
    vb = await annotations.save_verdict(b, ORG, t1, verdict="right", note=None, anchor=None, base=None)
    assert vb["blind"] is False


async def test_an_incomplete_sample_item_stays_blind(both):
    main, lab = both
    t1 = await add_turn(main, 32, ORG)
    a, b = principal(ORG), principal(ORG)
    await _batch(lab, ORG, t1, required=2)
    await annotations.save_verdict(a, ORG, t1, verdict="right", note=None, anchor=None, base=None)
    seen = (await annotations.for_turns(b, ORG, [t1]))[t1]
    assert seen["others"] is None and seen["others_hidden"] is True and seen["reviews_required"] == 2
    assert (await annotations.save_verdict(b, ORG, t1, verdict="right", note=None, anchor=None, base=None))["blind"] is True


async def test_a_reveal_opens_the_answer_and_the_verdict_after_it_is_not_blind(both):
    main, lab = both
    t1 = await add_turn(main, 33, ORG)
    a, b = principal(ORG), principal(ORG)
    await annotations.save_verdict(a, ORG, t1, verdict="wrong", note="Counted July.", anchor=Anchor(None, "answer", "758"), base=None)
    assert (await annotations.for_turns(b, ORG, [t1]))[t1]["others"] is None
    await _reveal(lab, ORG, t1, b)
    revealed = (await annotations.for_turns(b, ORG, [t1]))[t1]
    assert [o["note"] for o in revealed["others"]] == ["Counted July."] and revealed["others_hidden"] is False
    assert (await annotations.save_verdict(b, ORG, t1, verdict="right", note=None, anchor=None, base=None))["blind"] is False
    c = principal(ORG)                                              # a reveal by b opens nothing for c
    assert (await annotations.for_turns(c, ORG, [t1]))[t1]["others"] is None


async def test_the_platform_owner_reads_every_company_and_an_admin_only_their_own(both):
    main, _ = both
    t1 = await add_turn(main, 4, ORG, session="sess-4")
    await annotations.save_verdict(principal(ORG), ORG, t1, verdict="right", note=None, anchor=None, base=None)
    owner = principal(None, role="superadmin")
    st = (await annotations.for_turns(owner, None, [t1]))[t1]
    assert st["others"] is not None and st["others_count"] == 1
    acting = (await annotations.for_turns(owner, ORG, [t1]))[t1]            # acting for the company: blind like anyone
    assert acting["others"] is None and acting["others_count"] == 1
    stranger = str(uuid.uuid4())
    assert await annotations.for_turns(principal(stranger), stranger, [t1]) == {}
    assert await annotations.for_turns(principal(ORG, role="user"), ORG, [t1]) == {}      # not their own turn
    assert await annotations.for_turns(principal(ORG), ORG, []) == {}
    with pytest.raises(review_errors.NotFound):
        await annotations.for_conversation(principal(stranger), stranger, "s:sess-4")
    with pytest.raises(review_errors.NotFound):
        await annotations.history(principal(stranger), stranger, t1)
    mine = await annotations.for_conversation(principal(ORG), ORG, "s:sess-4")
    assert list(mine["turns"]) == [t1]


async def test_a_key_must_name_the_conversation_the_turn_is_in(both):
    main, _ = both
    with_session = await add_turn(main, 41, ORG, session="sess-41")
    blank = await add_turn(main, 42, ORG, session="")
    me = principal(ORG)
    assert list((await annotations.for_conversation(me, ORG, f"t:{blank}"))["turns"]) == [blank]
    for key in (f"t:{with_session}", f"r:{with_session}", "s:nope", "x:1", "s:", f"s:{blank}"):
        with pytest.raises(review_errors.NotFound):
            await annotations.for_conversation(me, ORG, key)
    with pytest.raises(review_errors.NotFound):
        await annotations.add_comment(me, ORG, f"t:{with_session}", note="wrong key", turn_id=None)


async def test_only_the_author_changes_a_comment_and_a_stale_base_is_refused(both):
    main, _ = both
    await add_turn(main, 5, ORG, session="sess-c")
    a, b = principal(ORG), principal(ORG)
    c1 = await annotations.add_comment(a, ORG, "s:sess-c", note="First thought.", turn_id=None)
    assert (c1["kind"], c1["version"], c1["turn_id"], c1["conversation_key"], c1["verdict"]) == ("comment", 1, None, "s:sess-c", None)
    with pytest.raises(review_errors.Forbidden) as e:
        await annotations.edit_comment(b, ORG, c1["lineage_id"], note="Not mine.", base=c1["id"])
    assert e.value.reason == "not_author" and e.value.status == 403
    with pytest.raises(review_errors.Forbidden):
        await annotations.delete_comment(b, ORG, c1["lineage_id"], base=c1["id"])
    c2 = await annotations.edit_comment(a, ORG, c1["lineage_id"], note="Second thought.", base=c1["id"])
    assert (c2["version"], c2["note"], c2["lineage_id"]) == (2, "Second thought.", c1["lineage_id"])
    with pytest.raises(review_errors.Stale) as e:
        await annotations.edit_comment(a, ORG, c1["lineage_id"], note="Third.", base=c1["id"])
    assert e.value.current["id"] == c2["id"]
    gone = await annotations.delete_comment(a, ORG, c1["lineage_id"], base=c2["id"])
    assert gone["tombstone"] is True and gone["note"] is None
    assert (await annotations.for_conversation(a, ORG, "s:sess-c"))["comments"] == []
    with pytest.raises(review_errors.NotFound) as e:
        await annotations.edit_comment(a, ORG, c1["lineage_id"], note="Back again.", base=gone["id"])
    assert e.value.reason == "annotation_not_found"


async def test_a_comment_edit_or_delete_that_loses_the_head_race_is_a_409_with_the_re_read_head(both):
    main, lab = both
    await add_turn(main, 52, ORG, session="sess-r")
    a = principal(ORG)
    c1 = await annotations.add_comment(a, ORG, "s:sess-r", note="First thought.", turn_id=None)
    lost = await lose_the_race(lab, c1["id"], lambda: annotations.edit_comment(
        a, ORG, c1["lineage_id"], note="Too late.", base=c1["id"]))
    assert isinstance(lost, review_errors.Stale), repr(lost)               # not NotFound(annotation_not_found)
    assert lost.status == 409 and lost.current is not None
    assert lost.current["version"] == 2 and lost.current["supersedes"] == c1["id"] and lost.current["tombstone"] is False
    c2_id = lost.current["id"]
    lost = await lose_the_race(lab, c2_id, lambda: annotations.delete_comment(a, ORG, c1["lineage_id"], base=c2_id))
    assert isinstance(lost, review_errors.Stale), repr(lost)
    assert lost.status == 409 and lost.current["version"] == 3
    c3_id = lost.current["id"]
    lost = await lose_the_race(lab, c3_id, lambda: annotations.edit_comment(
        a, ORG, c1["lineage_id"], note="After a delete.", base=c3_id), tombstone=True)
    assert isinstance(lost, review_errors.Stale), repr(lost)               # the winner deleted it: current is the tombstone
    assert lost.current["version"] == 4 and lost.current["tombstone"] is True


async def test_a_reader_who_is_not_the_author_never_gets_the_authors_comment_back_from_a_lost_race(both):
    main, lab = both
    await add_turn(main, 53, ORG, session="sess-s")
    a, b = principal(ORG), principal(ORG)
    c1 = await annotations.add_comment(a, ORG, "s:sess-s", note="Mine.", turn_id=None)
    lost = await lose_the_race(lab, c1["id"], lambda: annotations.edit_comment(
        b, ORG, c1["lineage_id"], note="Not mine.", base=c1["id"]))
    assert isinstance(lost, review_errors.Forbidden), repr(lost)
    assert lost.reason == "not_author" and lost.status == 403


async def test_two_comment_edits_of_one_head_are_one_new_version_and_one_409(both):
    main, lab = both
    await add_turn(main, 54, ORG, session="sess-t")
    a = principal(ORG)
    c1 = await annotations.add_comment(a, ORG, "s:sess-t", note="First thought.", turn_id=None)
    results = await asyncio.gather(
        annotations.edit_comment(a, ORG, c1["lineage_id"], note="Edit one.", base=c1["id"]),
        annotations.edit_comment(a, ORG, c1["lineage_id"], note="Edit two.", base=c1["id"]), return_exceptions=True)
    assert sorted(type(r).__name__ for r in results) == ["Stale", "dict"]
    stale = next(r for r in results if isinstance(r, review_errors.Stale))
    assert stale.status == 409 and stale.current is not None and stale.current["version"] == 2
    async with lab.connect() as conn:
        assert (await conn.execute(text(
            "SELECT count(*) FROM tracelab.annotations WHERE lineage_id = CAST(:l AS uuid)"),
            {"l": c1["lineage_id"]})).scalar() == 2


async def test_two_comment_deletes_of_one_head_are_one_tombstone_and_one_409(both):
    main, lab = both
    await add_turn(main, 55, ORG, session="sess-u")
    a = principal(ORG)
    c1 = await annotations.add_comment(a, ORG, "s:sess-u", note="First thought.", turn_id=None)
    results = await asyncio.gather(
        annotations.delete_comment(a, ORG, c1["lineage_id"], base=c1["id"]),
        annotations.delete_comment(a, ORG, c1["lineage_id"], base=c1["id"]), return_exceptions=True)
    assert sorted(type(r).__name__ for r in results) == ["Stale", "dict"]    # a double click is a 409, never a 404
    stale = next(r for r in results if isinstance(r, review_errors.Stale))
    assert stale.status == 409 and stale.current["version"] == 2 and stale.current["tombstone"] is True


async def test_a_comment_change_after_the_head_moved_on_is_a_409_even_when_the_head_is_a_tombstone(both):
    main, _ = both
    await add_turn(main, 56, ORG, session="sess-v")
    a = principal(ORG)
    c1 = await annotations.add_comment(a, ORG, "s:sess-v", note="First thought.", turn_id=None)
    gone = await annotations.delete_comment(a, ORG, c1["lineage_id"], base=c1["id"])
    for change in (lambda: annotations.delete_comment(a, ORG, c1["lineage_id"], base=c1["id"]),
                   lambda: annotations.edit_comment(a, ORG, c1["lineage_id"], note="Old tab.", base=c1["id"])):
        with pytest.raises(review_errors.Stale) as e:
            await change()
        assert e.value.current["id"] == gone["id"] and e.value.current["tombstone"] is True
    with pytest.raises(review_errors.NotFound):                                # nothing left to change: still a 404
        await annotations.delete_comment(a, ORG, c1["lineage_id"], base=gone["id"])


async def test_a_comment_is_checked_before_it_is_stored(both):
    main, lab = both
    t1 = await add_turn(main, 51, ORG, session="sess-d")
    other = await add_turn(main, 52, ORG, session="sess-e")
    await add_turn(main, 53, OTHER, session="sess-f")
    me = principal(ORG)
    for note in (None, "", "   "):
        with pytest.raises(review_errors.Invalid) as e:
            await annotations.add_comment(me, ORG, "s:sess-d", note=note, turn_id=None)
        assert e.value.reason == "note_required"
    with pytest.raises(review_errors.NotFound):                       # a turn of another conversation
        await annotations.add_comment(me, ORG, "s:sess-d", note="x", turn_id=other)
    with pytest.raises(review_errors.NotFound):                       # another company's conversation
        await annotations.add_comment(me, ORG, "s:sess-f", note="x", turn_id=None)
    with pytest.raises(review_errors.NotFound):
        await annotations.add_comment(principal(OTHER), OTHER, "s:sess-d", note="x", turn_id=None)
    with pytest.raises(review_errors.NotFound):
        await annotations.add_comment(me, ORG, "s:not-there", note="x", turn_id=None)
    async with lab.connect() as conn:
        assert (await conn.execute(text("SELECT count(*) FROM tracelab.annotations"))).scalar() == 0
    await annotations.add_comment(me, ORG, "s:sess-d", note="On a turn.", turn_id=t1)
    async with lab.connect() as conn:                                  # a comment on a turn keeps that turn's evidence
        assert (await conn.execute(text("SELECT turn_id FROM tracelab.evidence_turns"))).scalar() == t1


async def test_a_conversation_level_comment_copies_no_evidence(both):
    main, lab = both
    await add_turn(main, 54, ORG, session="sess-g")
    await annotations.add_comment(principal(ORG), ORG, "s:sess-g", note="About the chat as a whole.", turn_id=None)
    async with lab.connect() as conn:
        assert (await conn.execute(text("SELECT count(*) FROM tracelab.evidence_turns"))).scalar() == 0


async def test_comments_stay_hidden_while_any_answer_is_still_blind_and_the_owner_sees_all(both):
    main, _ = both
    t1 = await add_turn(main, 61, ORG, session="sess-h", minutes_ago=30)
    t2 = await add_turn(main, 62, ORG, session="sess-h", minutes_ago=20)
    a, b = principal(ORG), principal(ORG)
    mine = await annotations.add_comment(b, ORG, "s:sess-h", note="Mine.", turn_id=None)
    await annotations.add_comment(a, ORG, "s:sess-h", note="Theirs one.", turn_id=None)
    await annotations.add_comment(a, ORG, "s:sess-h", note="Theirs two.", turn_id=t2)
    await annotations.save_verdict(b, ORG, t1, verdict="right", note=None, anchor=None, base=None)   # t2 still blind for b
    out = await annotations.for_conversation(b, ORG, "s:sess-h")
    assert [c["id"] for c in out["comments"]] == [mine["id"]] and out["comments_hidden"] == 2
    await annotations.save_verdict(b, ORG, t2, verdict="right", note=None, anchor=None, base=None)
    out = await annotations.for_conversation(b, ORG, "s:sess-h")
    assert [c["note"] for c in out["comments"]] == ["Mine.", "Theirs one.", "Theirs two."] and out["comments_hidden"] == 0
    owner = await annotations.for_conversation(principal(None, role="superadmin"), None, "s:sess-h")
    assert len(owner["comments"]) == 3 and owner["comments_hidden"] == 0


async def test_history_shows_the_callers_rows_until_the_answer_opens(both):
    main, lab = both
    t1 = await add_turn(main, 71, ORG)
    a, b = principal(ORG), principal(ORG)
    va = await annotations.save_verdict(a, ORG, t1, verdict="right", note=None, anchor=None, base=None)
    await annotations.save_verdict(a, ORG, t1, verdict="wrong", note="Counted July.", anchor=Anchor(None, "answer", "758"), base=va["id"])
    mine_a = await annotations.history(a, ORG, t1)
    assert [v["version"] for v in mine_a["versions"]] == [1, 2] and [e["action"] for e in mine_a["events"]] == ["create", "edit"]
    assert (await annotations.history(b, ORG, t1))["versions"] == []
    await annotations.save_verdict(b, ORG, t1, verdict="right", note=None, anchor=None, base=None)
    both_rows = await annotations.history(b, ORG, t1)
    assert len(both_rows["versions"]) == 3 and len(both_rows["events"]) == 3
    assert both_rows["ok"] is True and both_rows["turn_id"] == t1
    other_org = await annotations.history(principal(None, role="superadmin"), None, t1)
    assert len(other_org["versions"]) == 3


async def test_reviewed_turns_are_the_live_verdict_heads_of_one_company(both):
    main, lab = both
    t1, t2, t3 = [await add_turn(main, n, ORG) for n in (81, 82, 83)]
    await add_turn(main, 84, ORG)
    foreign = await add_turn(main, 85, OTHER)
    a, b = principal(ORG), principal(ORG)
    await annotations.save_verdict(a, ORG, t1, verdict="right", note=None, anchor=None, base=None)
    await annotations.save_verdict(b, ORG, t2, verdict="right", note=None, anchor=None, base=None)
    v3 = await annotations.save_verdict(a, ORG, t3, verdict="right", note=None, anchor=None, base=None)
    await annotations.delete_verdict(a, ORG, t3, base=v3["id"])                       # a tombstone is not a review
    await annotations.add_comment(a, ORG, "s:sess-84", note="A comment is not a review.", turn_id=None)
    await annotations.save_verdict(principal(OTHER), OTHER, foreign, verdict="right", note=None, anchor=None, base=None)
    async with lab.connect() as conn:
        assert await annotations.reviewed_turns(conn, ORG) == {t1, t2}
        assert await annotations.reviewed_turns(conn, OTHER) == {foreign}
        assert await annotations.reviewed_turns(conn, None) == {t1, t2, foreign}
    assert await annotations.reviewed_turn_ids(ORG) == {t1, t2}
    assert await annotations.reviewed_turn_ids(ORG, reviewer_id=str(a.user_id), mode="mine") == {t1}
    assert await annotations.reviewed_turn_ids(ORG, reviewer_id=str(b.user_id), mode="mine") == {t2}
    assert await annotations.reviewed_turn_ids(ORG, mode="unreviewed") == {t1, t2}      # the ids to leave out
    with pytest.raises(ValueError):
        await annotations.reviewed_turn_ids(ORG, mode="mine")
    with pytest.raises(ValueError):
        await annotations.reviewed_turn_ids(ORG, mode="sideways")


async def test_the_reviewed_predicate_selects_chat_turns_through_the_main_engine(both):
    """The ids come from the lab database, the predicate runs on the main one: no join between them."""
    main, lab = both
    t1, t2, t3 = [await add_turn(main, n, ORG) for n in (91, 92, 93)]
    me = principal(ORG)
    await annotations.save_verdict(me, ORG, t1, verdict="right", note=None, anchor=None, base=None)
    for mode, want in (("reviewed", {t1}), ("unreviewed", {t2, t3}), ("mine", {t1})):
        ids = await annotations.reviewed_turn_ids(ORG, reviewer_id=str(me.user_id), mode=mode)
        sql, params = annotations.reviewed_predicate("tt", mode, ids)
        async with main.connect() as conn:
            got = {r[0] for r in (await conn.execute(text(
                f"SELECT tt.turn_id FROM plenum_cafm.agent_trace_turns tt WHERE tt.organization_id = CAST(:o AS uuid) AND {sql}"),
                {"o": ORG, **params})).all()}
        assert got == want, mode


# ── the blocks are the strings the Conversations view renders ─────────────────────────────────────

async def test_the_blocks_are_what_the_conversations_view_renders_for_a_live_turn(both):
    main, _ = both
    tid = await add_turn(main, 101, ORG, session="sess-p", question="Boilerplate wrapper\n\n---\n\n**Current user message:**\nHow many WOs?")
    await add_span(main, tid, output={"count": 758, "period": "2026-07"}, org=ORG)
    async with main.begin() as conn:                                # the typed words, in the earliest activity row
        await conn.execute(text("""
            INSERT INTO plenum_cafm.agent_activity_log (id, session_id, turn_id, agent, stage, direction, payload)
            VALUES (CAST(:id AS uuid), 'sess-p', :tid, 'orchestrator', 'turn', 'input', CAST(:p AS jsonb))"""),
            {"id": str(uuid.uuid4()), "tid": tid, "p": json.dumps({"message": "How many WOs did we raise?"})})
    me = principal(ORG)
    out = await annotations.for_conversation(me, ORG, "s:sess-p", turn_id=tid)
    view = (await conversations.get_conversation(me, ORG, "s:sess-p", turn_id=tid))["active"]
    shown = {k: v["text"] for k, v in annotations.block_texts(view).items()}
    assert out["blocks"][tid] == shown
    assert shown[":question"] == "How many WOs did we raise?" and ":answer" in shown and len(shown) == 3
    v = await annotations.save_verdict(me, ORG, tid, verdict="wrong", note="Asked about raising, not counting.",
                                       anchor=Anchor(None, "question", "did we raise"), base=None)
    assert v["anchor"]["start"] == shown[":question"].index("did we raise") == 13


async def test_the_blocks_are_what_the_conversations_view_renders_for_a_golden_turn(both):
    _, lab = both
    tid = "turn-g00000000000002"
    async with lab.begin() as conn:
        s = await seed_set(conn, ORG, cases=(("21", ()),))
        run = await seed_run(conn, ORG, s["set_id"])
        await seed_result(conn, ORG, run, s["cases"]["21"], "21", turn_id=tid)
        sid = f"golden-{run[:8]}-21-r1"
        turn = {"turn_id": tid, "session_id": sid, "organization_id": ORG, "user_id": str(uuid.uuid4()),
                "email": "asker@example.com", "question": "wrapped", "answer": "759 work orders.", "ok": True,
                "models": ["m"], "started_at": "2026-10-01T10:00:00+00:00"}
        await conn.execute(text("INSERT INTO tracelab.evidence_turns (turn_id, organization_id, turn, reason) "
                                "VALUES (:t, CAST(:o AS uuid), CAST(:j AS jsonb), 'golden')"),
                           {"t": tid, "o": ORG, "j": json.dumps(turn)})
    me = principal(ORG)
    key = "s:" + sid
    out = await annotations.for_conversation(me, ORG, key, turn_id=tid)
    view = (await conversations.get_conversation(me, ORG, key, turn_id=tid))["active"]
    assert out["blocks"][tid] == {k: v["text"] for k, v in annotations.block_texts(view).items()}
    assert out["blocks"][tid][":question"] == "Message 0 of 21?" and out["blocks"][tid][":answer"] == "759 work orders."
    assert list(out["turns"]) == [tid]
    c = await annotations.add_comment(me, ORG, key, note="A golden answer, commented.", turn_id=tid)
    assert c["conversation_key"] == key
    assert [x["note"] for x in (await annotations.for_conversation(me, ORG, key))["comments"]] == ["A golden answer, commented."]
    with pytest.raises(review_errors.NotFound):
        await annotations.for_conversation(principal(OTHER), OTHER, key)
