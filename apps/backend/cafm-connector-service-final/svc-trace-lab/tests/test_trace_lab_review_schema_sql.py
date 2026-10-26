"""0003_review.sql on a throwaway Postgres (tracelab_scratch): history is append-only, an annotation version is
superseded once and never edited, a turn is drawn into at most one batch per company, only uniform batches count
toward rates, and the offboarding hatch is the only way to delete. Skips without TRACE_LAB_TEST_DB_URL."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tl_scratch import lab_scratch, requires_scratch  # noqa: F401

pytestmark = requires_scratch

ORG = str(uuid.uuid4())

ANNOTATION = """
    INSERT INTO tracelab.annotations (id, lineage_id, version, kind, organization_id, conversation_key, turn_id,
        reviewer_id, verdict, note, blind)
    VALUES (CAST(:id AS uuid), CAST(:lineage AS uuid), :version, 'verdict', CAST(:org AS uuid), 's:x', :turn,
            CAST(:me AS uuid), :v, :n, true)"""


async def refused(engine, sql: str, params: dict | None = None) -> str:
    with pytest.raises(DBAPIError) as e:
        async with engine.begin() as conn:
            await conn.execute(text(sql), params or {})
    return str(e.value)


async def run(engine, sql: str, params: dict | None = None):
    async with engine.begin() as conn:
        return await conn.execute(text(sql), params or {})


async def _batch(engine, strategy: str = "uniform", cfp: bool = True) -> str:
    bid = str(uuid.uuid4())
    await run(engine, """
        INSERT INTO tracelab.sample_batches (id, organization_id, plan_step, strategy, seed, window_since,
            window_until, requested, selected, pool_size, counts_for_prevalence, created_by)
        VALUES (CAST(:id AS uuid), CAST(:org AS uuid), 'extra', :s, 7, now() - interval '1 day', now(), 1, 1, 1, :cfp,
                CAST(:me AS uuid))""", {"id": bid, "org": ORG, "s": strategy, "cfp": cfp, "me": str(uuid.uuid4())})
    return bid


async def _item(engine, bid: str, tid: str, position: int = 1, org: str = ORG) -> None:
    await run(engine, """
        INSERT INTO tracelab.sample_items (id, batch_id, organization_id, turn_id, position, reason)
        VALUES (gen_random_uuid(), CAST(:b AS uuid), CAST(:org AS uuid), :tid, :pos, 'uniform pick')""",
              {"b": bid, "org": org, "tid": tid, "pos": position})


async def _annotation(engine, verdict: str = "right", note: str = "no failure observed", turn: str = "turn-1",
                      me: str | None = None) -> str:
    aid = str(uuid.uuid4())
    await run(engine, ANNOTATION, {"id": aid, "lineage": aid, "version": 1, "org": ORG, "turn": turn,
                                   "me": me or str(uuid.uuid4()), "v": verdict, "n": note})
    return aid


async def test_sample_items_refuse_update_delete_and_truncate(lab_scratch):
    bid = await _batch(lab_scratch)
    await _item(lab_scratch, bid, "turn-1")
    assert "append-only" in await refused(lab_scratch, "UPDATE tracelab.sample_items SET reason = 'x'")
    assert "append-only" in await refused(lab_scratch, "DELETE FROM tracelab.sample_items")
    assert "append-only" in await refused(lab_scratch, "TRUNCATE tracelab.sample_items CASCADE")


async def test_annotation_events_refuse_update_delete_and_truncate(lab_scratch):
    await run(lab_scratch, """INSERT INTO tracelab.annotation_events (organization_id, turn_id, action, actor_id)
                              VALUES (CAST(:org AS uuid), 'turn-1', 'create', gen_random_uuid())""", {"org": ORG})
    assert "append-only" in await refused(lab_scratch, "UPDATE tracelab.annotation_events SET action = 'edit'")
    assert "append-only" in await refused(lab_scratch, "DELETE FROM tracelab.annotation_events")
    assert "append-only" in await refused(lab_scratch, "TRUNCATE tracelab.annotation_events")


async def test_an_annotation_only_gains_superseded_by_once_and_is_never_deleted(lab_scratch):
    aid = await _annotation(lab_scratch)
    assert "never edited" in await refused(lab_scratch, "UPDATE tracelab.annotations SET note = 'changed'")
    nxt = str(uuid.uuid4())
    await run(lab_scratch, "UPDATE tracelab.annotations SET superseded_by = CAST(:n AS uuid), superseded_at = now() "
                           "WHERE id = CAST(:a AS uuid)", {"n": nxt, "a": aid})
    assert "never edited" in await refused(lab_scratch,
                                           "UPDATE tracelab.annotations SET superseded_by = gen_random_uuid()")
    assert "never deleted" in await refused(lab_scratch, "DELETE FROM tracelab.annotations")
    assert "never deleted" in await refused(lab_scratch, "TRUNCATE tracelab.annotations CASCADE")


async def test_marking_a_row_superseded_cannot_smuggle_in_another_change(lab_scratch):
    aid = await _annotation(lab_scratch)
    sql = ("UPDATE tracelab.annotations SET superseded_by = gen_random_uuid(), superseded_at = now(), {} "
           "WHERE id = CAST(:a AS uuid)")
    for change in ("note = 'rewritten'", "verdict = 'wrong'", "reviewer_id = gen_random_uuid()", "tombstone = true"):
        assert "never edited" in await refused(lab_scratch, sql.format(change), {"a": aid}), change
    async with lab_scratch.connect() as conn:
        row = (await conn.execute(text("SELECT note, superseded_by FROM tracelab.annotations WHERE id = CAST(:a AS uuid)"),
                                  {"a": aid})).one()
    assert row == ("no failure observed", None)


async def test_an_edit_is_a_new_version_row_in_the_same_transaction(lab_scratch):
    me = str(uuid.uuid4())
    v1 = await _annotation(lab_scratch, me=me)
    v2 = str(uuid.uuid4())
    async with lab_scratch.begin() as conn:
        await conn.execute(text("UPDATE tracelab.annotations SET superseded_by = CAST(:n AS uuid), "
                                "superseded_at = clock_timestamp() WHERE id = CAST(:a AS uuid)"), {"n": v2, "a": v1})
        await conn.execute(text("""
            INSERT INTO tracelab.annotations (id, lineage_id, version, supersedes, kind, organization_id,
                conversation_key, turn_id, reviewer_id, verdict, note, blind)
            VALUES (CAST(:id AS uuid), CAST(:lineage AS uuid), 2, CAST(:old AS uuid), 'verdict', CAST(:org AS uuid),
                    's:x', 'turn-1', CAST(:me AS uuid), 'right', 'checked again, still right', true)"""),
                           {"id": v2, "lineage": v1, "old": v1, "org": ORG, "me": me})
    async with lab_scratch.connect() as conn:
        heads = (await conn.execute(text("SELECT id::text, version FROM tracelab.annotations "
                                         "WHERE lineage_id = CAST(:l AS uuid) AND superseded_by IS NULL"),
                                    {"l": v1})).all()
        history = (await conn.execute(text("SELECT version FROM tracelab.annotations "
                                           "WHERE lineage_id = CAST(:l AS uuid) ORDER BY version"), {"l": v1})).all()
    assert heads == [(v2, 2)] and history == [(1,), (2,)]


async def test_one_live_head_per_lineage_and_a_row_is_superseded_by_at_most_one(lab_scratch):
    aid = await _annotation(lab_scratch)
    dup_head = str(uuid.uuid4())
    assert "ux_tl_ann_lineage_head" in await refused(lab_scratch, ANNOTATION, {
        "id": dup_head, "lineage": aid, "version": 2, "org": ORG, "turn": "turn-2", "me": str(uuid.uuid4()),
        "v": "right", "n": "no failure observed"})
    sup = """
        INSERT INTO tracelab.annotations (id, lineage_id, version, supersedes, kind, organization_id, conversation_key,
            turn_id, reviewer_id, verdict, note)
        VALUES (gen_random_uuid(), gen_random_uuid(), 2, CAST(:a AS uuid), 'verdict', CAST(:org AS uuid), 's:x', :turn,
                gen_random_uuid(), 'right', 'no failure observed')"""
    await run(lab_scratch, sup, {"a": aid, "org": ORG, "turn": "turn-3"})
    assert "ux_tl_ann_supersedes" in await refused(lab_scratch, sup, {"a": aid, "org": ORG, "turn": "turn-4"})


async def test_the_offboarding_hatch_is_the_only_way_to_delete(lab_scratch):
    await _annotation(lab_scratch)
    assert "never deleted" in await refused(lab_scratch, "DELETE FROM tracelab.annotations")
    async with lab_scratch.begin() as conn:
        await conn.execute(text("SET LOCAL tracelab.allow_purge = 'on'"))
        res = await conn.execute(text("DELETE FROM tracelab.annotations WHERE organization_id = CAST(:o AS uuid)"),
                                 {"o": ORG})
    assert res.rowcount == 1
    async with lab_scratch.connect() as conn:       # the hatch lasted one transaction
        assert (await conn.execute(text("SELECT count(*) FROM tracelab.annotations"))).scalar() == 0
    await _annotation(lab_scratch)
    assert "never deleted" in await refused(lab_scratch, "DELETE FROM tracelab.annotations")


async def test_a_wrong_verdict_needs_note_quote_field_and_occurrence_and_a_right_one_has_none(lab_scratch):
    assert "ck_tl_ann_wrong" in await refused(lab_scratch, """
        INSERT INTO tracelab.annotations (id, lineage_id, version, kind, organization_id, conversation_key, turn_id,
            reviewer_id, verdict, note)
        VALUES (gen_random_uuid(), gen_random_uuid(), 1, 'verdict', CAST(:o AS uuid), 's:x', 't', gen_random_uuid(),
                'wrong', 'counted July')""", {"o": ORG})
    assert "ck_tl_ann_right" in await refused(lab_scratch, """
        INSERT INTO tracelab.annotations (id, lineage_id, version, kind, organization_id, conversation_key, turn_id,
            reviewer_id, verdict, note, field, quote, quote_occurrence)
        VALUES (gen_random_uuid(), gen_random_uuid(), 1, 'verdict', CAST(:o AS uuid), 's:x', 't', gen_random_uuid(),
                'right', 'no failure observed', 'answer', '758', 1)""", {"o": ORG})
    await run(lab_scratch, """
        INSERT INTO tracelab.annotations (id, lineage_id, version, kind, organization_id, conversation_key, turn_id,
            reviewer_id, verdict, note, field, quote, quote_occurrence)
        VALUES (gen_random_uuid(), gen_random_uuid(), 1, 'verdict', CAST(:o AS uuid), 's:x', 't', gen_random_uuid(),
                'wrong', 'counted July', 'answer', '758', 1)""", {"o": ORG})


async def test_a_comment_has_a_note_and_no_verdict_and_a_tombstone_needs_neither(lab_scratch):
    base = """
        INSERT INTO tracelab.annotations (id, lineage_id, version, kind, organization_id, conversation_key,
            reviewer_id, verdict, note, tombstone)
        VALUES (gen_random_uuid(), gen_random_uuid(), 1, 'comment', CAST(:o AS uuid), 's:x', gen_random_uuid(),
                {verdict}, {note}, {tomb})"""
    assert "ck_tl_ann_comment_note" in await refused(
        lab_scratch, base.format(verdict="NULL", note="'  '", tomb="false"), {"o": ORG})
    assert "ck_tl_ann_comment" in await refused(
        lab_scratch, base.format(verdict="'right'", note="'looks fine'", tomb="false"), {"o": ORG})
    await run(lab_scratch, base.format(verdict="NULL", note="'Check the period.'", tomb="false"), {"o": ORG})
    await run(lab_scratch, base.format(verdict="NULL", note="NULL", tomb="true"), {"o": ORG})


async def test_a_verdict_belongs_to_a_turn(lab_scratch):
    assert "ck_tl_ann_verdict_turn" in await refused(lab_scratch, """
        INSERT INTO tracelab.annotations (id, lineage_id, version, kind, organization_id, conversation_key,
            reviewer_id, verdict, note)
        VALUES (gen_random_uuid(), gen_random_uuid(), 1, 'verdict', CAST(:o AS uuid), 's:x', gen_random_uuid(),
                'right', 'no failure observed')""", {"o": ORG})


async def test_one_live_verdict_head_per_reviewer_and_turn(lab_scratch):
    me = str(uuid.uuid4())
    await _annotation(lab_scratch, turn="turn-9", me=me)
    assert "ux_tl_ann_verdict_head" in await refused(lab_scratch, ANNOTATION, {
        "id": str(uuid.uuid4()), "lineage": str(uuid.uuid4()), "version": 1, "org": ORG, "turn": "turn-9", "me": me,
        "v": "right", "n": "no failure observed"})
    await _annotation(lab_scratch, turn="turn-9")           # another reviewer may give their own verdict
    await _annotation(lab_scratch, turn="turn-10", me=me)   # and the same reviewer another turn


async def test_a_turn_is_never_in_two_batches_of_one_company(lab_scratch):
    b1, b2 = await _batch(lab_scratch), await _batch(lab_scratch)
    await _item(lab_scratch, b1, "turn-7")
    assert "ux_tl_items_turn" in await refused(lab_scratch, """
        INSERT INTO tracelab.sample_items (id, batch_id, organization_id, turn_id, position, reason)
        VALUES (gen_random_uuid(), CAST(:b AS uuid), CAST(:o AS uuid), 'turn-7', 1, 'uniform pick')""",
                                               {"b": b2, "o": ORG})
    await _item(lab_scratch, b2, "turn-7", org=str(uuid.uuid4()))        # another company may draw the same id
    assert "ux_tl_items_position" in await refused(lab_scratch, """
        INSERT INTO tracelab.sample_items (id, batch_id, organization_id, turn_id, position, reason)
        VALUES (gen_random_uuid(), CAST(:b AS uuid), CAST(:o AS uuid), 'turn-8', 1, 'uniform pick')""",
                                               {"b": b1, "o": ORG})


async def test_only_uniform_batches_count_toward_rates(lab_scratch):
    assert "ck_tl_batches_prevalence" in await refused(lab_scratch, """
        INSERT INTO tracelab.sample_batches (id, organization_id, plan_step, strategy, seed, window_since,
            window_until, requested, selected, pool_size, counts_for_prevalence, created_by)
        VALUES (gen_random_uuid(), CAST(:o AS uuid), 'extra', 'diversity', 7, now() - interval '1 day', now(), 1, 1, 1,
                true, gen_random_uuid())""", {"o": ORG})
    assert "ck_tl_batches_prevalence" in await refused(lab_scratch, """
        INSERT INTO tracelab.sample_batches (id, organization_id, plan_step, strategy, seed, window_since,
            window_until, requested, selected, pool_size, counts_for_prevalence, created_by)
        VALUES (gen_random_uuid(), CAST(:o AS uuid), 'extra', 'uniform', 7, now() - interval '1 day', now(), 1, 1, 1,
                false, gen_random_uuid())""", {"o": ORG})
    for strategy in ("diversity", "dimension", "outlier", "enrich", "monitor_flagged"):
        await _batch(lab_scratch, strategy, cfp=False)


async def test_a_batch_window_runs_forward_and_an_item_needs_its_batch(lab_scratch):
    assert "ck_tl_batches_window" in await refused(lab_scratch, """
        INSERT INTO tracelab.sample_batches (id, organization_id, plan_step, strategy, seed, window_since,
            window_until, requested, selected, pool_size, counts_for_prevalence, created_by)
        VALUES (gen_random_uuid(), CAST(:o AS uuid), 'extra', 'uniform', 7, now(), now() - interval '1 day', 1, 1, 1,
                true, gen_random_uuid())""", {"o": ORG})
    assert "sample_items_batch_id_fkey" in await refused(lab_scratch, """
        INSERT INTO tracelab.sample_items (id, batch_id, organization_id, turn_id, position, reason)
        VALUES (gen_random_uuid(), gen_random_uuid(), CAST(:o AS uuid), 'turn-1', 1, 'uniform pick')""", {"o": ORG})
