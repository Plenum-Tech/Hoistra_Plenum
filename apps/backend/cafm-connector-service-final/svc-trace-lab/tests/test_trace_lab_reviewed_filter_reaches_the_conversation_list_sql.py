"""The conversation list's `reviewed` filter on a scratch Postgres: reviewed, unreviewed and mine select conversations
by live verdicts (a tombstone is not a review), for live traffic and for a golden run. The verdicts are read from the
lab database and handed to the chat's query as a list of turn ids, so no statement names both databases (Golden Rule
G0). Skips without TRACE_LAB_TEST_DB_URL."""
from __future__ import annotations

import json
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import event, text

from golden_seed import seed_result, seed_run, seed_set
from tl_scratch import lab_scratch, main_scratch, requires_scratch  # noqa: F401
from trace_lab import annotations, conversations, db
from trace_lab.annotations import Anchor
from trace_lab_m2_fixtures import NOW, add_turn, principal

pytestmark = requires_scratch

ORG = str(uuid.uuid4())
OTHER = str(uuid.uuid4())


async def _keys(me, reviewed, *, run="live", org=ORG):
    f = conversations.TurnFilter(since=NOW - timedelta(days=7), until=NOW + timedelta(hours=1), reviewed=reviewed, run=run)
    out = await conversations.list_conversations(me, org, f, cursor=None, limit=50)
    return sorted(c["key"] for c in out["conversations"])


async def test_the_reviewed_filter_selects_by_live_verdicts(main_scratch, lab_scratch):  # noqa: F811
    t1 = await add_turn(main_scratch, 1, ORG, session="sess-r1")
    t2 = await add_turn(main_scratch, 2, ORG, session="sess-r2")
    await add_turn(main_scratch, 3, ORG, session="sess-r3")
    me, other = principal(ORG), principal(ORG)
    await annotations.save_verdict(me, ORG, t1, verdict="right", note=None, anchor=None, base=None)
    v = await annotations.save_verdict(other, ORG, t2, verdict="right", note=None, anchor=None, base=None)
    assert await _keys(me, "any") == ["s:sess-r1", "s:sess-r2", "s:sess-r3"]
    assert await _keys(me, "mine") == ["s:sess-r1"]
    assert await _keys(me, "reviewed") == ["s:sess-r1", "s:sess-r2"]
    assert await _keys(me, "unreviewed") == ["s:sess-r3"]
    assert await _keys(other, "mine") == ["s:sess-r2"]
    await annotations.delete_verdict(other, ORG, t2, base=v["id"])          # a tombstone is not a review
    assert await _keys(me, "unreviewed") == ["s:sess-r2", "s:sess-r3"]
    assert await _keys(me, "reviewed") == ["s:sess-r1"]


async def test_a_conversation_is_reviewed_when_any_of_its_answers_is(main_scratch, lab_scratch):  # noqa: F811
    a = await add_turn(main_scratch, 1, ORG, session="sess-two", minutes_ago=30)
    await add_turn(main_scratch, 2, ORG, session="sess-two", minutes_ago=20)
    await add_turn(main_scratch, 3, ORG, session="sess-untouched")
    await annotations.save_verdict(principal(ORG), ORG, a, verdict="right", note=None, anchor=None, base=None)
    assert await _keys(principal(ORG), "reviewed") == ["s:sess-two"]
    assert await _keys(principal(ORG), "unreviewed") == ["s:sess-two", "s:sess-untouched"]   # it still holds an answer nobody judged


async def test_another_companys_verdicts_never_make_this_companys_answers_reviewed(main_scratch, lab_scratch):  # noqa: F811
    t = await add_turn(main_scratch, 1, ORG, session="sess-mine")
    theirs = await add_turn(main_scratch, 2, OTHER, session="sess-theirs")
    await annotations.save_verdict(principal(OTHER), OTHER, theirs, verdict="right", note=None, anchor=None, base=None)
    assert t
    assert await _keys(principal(ORG), "reviewed") == []
    assert await _keys(principal(ORG), "unreviewed") == ["s:sess-mine"]
    assert await _keys(principal(OTHER), "reviewed", org=OTHER) == ["s:sess-theirs"]


async def test_the_filter_combines_with_the_other_filters(main_scratch, lab_scratch):  # noqa: F811
    t1 = await add_turn(main_scratch, 1, ORG, session="sess-a", question="How many boilers?")
    await add_turn(main_scratch, 2, ORG, session="sess-b", question="How many chillers?")
    await annotations.save_verdict(principal(ORG), ORG, t1, verdict="right", note=None, anchor=None, base=None)
    f = conversations.TurnFilter(since=NOW - timedelta(days=7), until=NOW + timedelta(hours=1), reviewed="unreviewed", q="How many")
    out = await conversations.list_conversations(principal(ORG), ORG, f, cursor=None, limit=10)
    assert [c["key"] for c in out["conversations"]] == ["s:sess-b"] and out["total"] == 1
    f2 = conversations.TurnFilter(since=NOW - timedelta(days=7), until=NOW + timedelta(hours=1), reviewed="reviewed", q="chillers")
    assert (await conversations.list_conversations(principal(ORG), ORG, f2, cursor=None, limit=10))["conversations"] == []


async def test_the_filter_works_on_a_golden_run_too(main_scratch, lab_scratch):  # noqa: F811
    me = principal(ORG)
    async with lab_scratch.begin() as conn:
        s = await seed_set(conn, ORG, cases=(("21", ()), ("22", ())))
        run = await seed_run(conn, ORG, s["set_id"])
        ids = {}
        for key in ("21", "22"):
            _, tid = await seed_result(conn, ORG, run, s["cases"][key], key, turn_id=f"turn-g00000000000{key}")
            ids[key] = tid
            turn = {"turn_id": tid, "session_id": f"golden-{run[:8]}-{key}-r1", "organization_id": ORG,
                    "user_id": str(uuid.uuid4()), "question": "q", "answer": "759 work orders.", "ok": True}
            await conn.execute(text("INSERT INTO tracelab.evidence_turns (turn_id, organization_id, turn, reason) "
                                    "VALUES (:t, CAST(:o AS uuid), CAST(:j AS jsonb), 'golden')"),
                               {"t": tid, "o": ORG, "j": json.dumps(turn)})
    await annotations.save_verdict(me, ORG, ids["21"], verdict="wrong", note="Off.", anchor=Anchor(None, "answer", "759"), base=None)
    assert [k.endswith("-21-r1") for k in await _keys(me, "reviewed", run=run)] == [True]
    assert [k.endswith("-22-r1") for k in await _keys(me, "unreviewed", run=run)] == [True]
    assert len(await _keys(me, "any", run=run)) == 2


async def test_the_reviewed_ids_go_to_the_chat_as_a_parameter_and_no_statement_names_both_databases(main_scratch, lab_scratch):  # noqa: F811
    t1 = await add_turn(main_scratch, 1, ORG, session="sess-r1")
    await add_turn(main_scratch, 2, ORG, session="sess-r2")
    me = principal(ORG)
    await annotations.save_verdict(me, ORG, t1, verdict="right", note=None, anchor=None, base=None)
    seen: dict[str, list[tuple[str, object]]] = {"main": [], "lab": []}

    def recorder(name):
        def record(conn, cursor, statement, parameters, context, executemany):
            seen[name].append((statement, parameters))
        return record

    pairs = ((db.main_engine().sync_engine, recorder("main")), (lab_scratch.sync_engine, recorder("lab")))
    for engine, fn in pairs:
        event.listen(engine, "before_cursor_execute", fn)
    try:
        await _keys(me, "reviewed")
    finally:
        for engine, fn in pairs:
            event.remove(engine, "before_cursor_execute", fn)
    main_sql = [s for s, _ in seen["main"]]
    assert any("ANY(" in s and "turn_id" in s for s in main_sql)
    assert [s for s in main_sql if "tracelab" in s.lower()] == []
    assert [s for s, _ in seen["lab"] if "plenum_cafm" in s.lower()] == []
    assert any(t1 in str(params) for s, params in seen["main"])         # the reviewed id travelled as a bound parameter
