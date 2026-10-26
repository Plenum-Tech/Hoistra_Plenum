"""Open coding reads the chat's tables through the READ-ONLY main engine and keeps its own rows in the lab
database, and no SQL statement ever spans the two (Golden Rule G0; in production they are two Postgres databases).
Here both live in one scratch database, so the rule is pinned on the statements themselves: every SQL string sent
through each engine is recorded while a workload runs every public path of trace_lab.annotations. Skips without
TRACE_LAB_TEST_DB_URL."""
from __future__ import annotations

import json
import re
import uuid

import pytest
from sqlalchemy import event, text

from golden_seed import seed_result, seed_run, seed_set
from tl_scratch import lab_scratch, main_scratch, requires_scratch  # noqa: F401
from trace_lab import annotations, db
from trace_lab.annotations import Anchor
from trace_lab_m2_fixtures import add_span, add_turn, principal

pytestmark = requires_scratch

ORG = str(uuid.uuid4())
WRITE = re.compile(r"^\s*(INSERT|UPDATE|DELETE|TRUNCATE|CREATE|ALTER|DROP|GRANT|COPY)\b", re.I)


@pytest.fixture
async def spy(main_scratch, lab_scratch):  # noqa: F811
    """(writable main engine for seeding, lab engine, {"main": [...], "lab": [...]} of every statement run by the code)."""
    seen: dict[str, list[str]] = {"main": [], "lab": []}

    def recorder(name: str):
        def record(conn, cursor, statement, parameters, context, executemany):
            seen[name].append(statement)
        return record

    pairs = ((db.main_engine().sync_engine, recorder("main")), (lab_scratch.sync_engine, recorder("lab")))
    for engine, fn in pairs:
        event.listen(engine, "before_cursor_execute", fn)
    yield main_scratch, lab_scratch, seen
    for engine, fn in pairs:
        event.remove(engine, "before_cursor_execute", fn)


async def _workload(main, lab):
    me, you = principal(ORG), principal(ORG)
    t1 = await add_turn(main, 1, ORG, session="sess-x", minutes_ago=30)
    t2 = await add_turn(main, 2, ORG, session="sess-x", minutes_ago=20)
    span = await add_span(main, t1, output={"count": 758, "period": "2026-07"}, org=ORG)
    v1 = await annotations.save_verdict(me, ORG, t1, verdict="wrong", note="Read July.",
                                        anchor=Anchor(span, "output", '"period": "2026-07"'), base=None)
    v2 = await annotations.save_verdict(me, ORG, t1, verdict="right", note=None, anchor=None, base=v1["id"])
    await annotations.save_verdict(you, ORG, t2, verdict="right", note=None, anchor=None, base=None)
    await annotations.delete_verdict(me, ORG, t1, base=v2["id"])
    c1 = await annotations.add_comment(me, ORG, "s:sess-x", note="On the chat.", turn_id=t2)
    c2 = await annotations.edit_comment(me, ORG, c1["lineage_id"], note="On the chat, edited.", base=c1["id"])
    await annotations.delete_comment(me, ORG, c1["lineage_id"], base=c2["id"])
    await annotations.for_conversation(me, ORG, "s:sess-x", turn_id=t1)
    await annotations.for_turns(me, ORG, [t1, t2, "turn-missing"])
    await annotations.history(me, ORG, t1)
    await annotations.for_turns(principal(None, role="superadmin"), None, [t1])
    ids = await annotations.reviewed_turn_ids(ORG, reviewer_id=str(me.user_id), mode="mine")
    async with lab.connect() as conn:
        await annotations.reviewed_turns(conn, ORG)
        await annotations.visible_others(conn, me, ORG, [t1, t2])
    sql, params = annotations.reviewed_predicate("tt", "reviewed", ids)
    async with main.connect() as conn:                       # the predicate is for the main side
        await conn.execute(text(f"SELECT tt.turn_id FROM plenum_cafm.agent_trace_turns tt WHERE {sql}"), params)
    # a golden answer, whose turn and conversation live in the lab database
    gid = "turn-g00000000000009"
    async with lab.begin() as conn:
        s = await seed_set(conn, ORG, cases=(("21", ()),))
        run = await seed_run(conn, ORG, s["set_id"])
        await seed_result(conn, ORG, run, s["cases"]["21"], "21", turn_id=gid)
        turn = {"turn_id": gid, "session_id": f"golden-{run[:8]}-21-r1", "organization_id": ORG,
                "user_id": str(uuid.uuid4()), "question": "q", "answer": "759 work orders.", "ok": True}
        await conn.execute(text("INSERT INTO tracelab.evidence_turns (turn_id, organization_id, turn, reason) "
                                "VALUES (:t, CAST(:o AS uuid), CAST(:j AS jsonb), 'golden')"),
                           {"t": gid, "o": ORG, "j": json.dumps(turn)})
    gv = await annotations.save_verdict(me, ORG, gid, verdict="wrong", note="Off.", anchor=Anchor(None, "answer", "759"), base=None)
    await annotations.add_comment(me, ORG, gv["conversation_key"], note="Golden.", turn_id=gid)
    await annotations.for_conversation(me, ORG, gv["conversation_key"], turn_id=gid)
    await annotations.for_turns(me, ORG, [gid])
    await annotations.history(me, ORG, gid)


async def test_no_statement_names_both_databases_and_each_engine_sees_only_its_own(spy):
    main, lab, seen = spy
    await _workload(main, lab)
    assert len(seen["main"]) > 10 and len(seen["lab"]) > 30                 # the workload really used both engines
    for name, other in (("main", "tracelab"), ("lab", "plenum_cafm")):
        stray = [s for s in seen[name] if other in s.lower()]
        assert stray == [], f"the {name} engine was sent SQL naming {other}: {stray[:1]}"
    both = [s for engine_sql in seen.values() for s in engine_sql if "tracelab" in s.lower() and "plenum_cafm" in s.lower()]
    assert both == []


async def test_the_main_engine_is_only_ever_read_from(spy):
    main, lab, seen = spy
    await _workload(main, lab)
    writes = [s for s in seen["main"] if WRITE.match(s)]
    assert writes == [], writes[:1]
    assert any(s.strip().upper().startswith("SET TRANSACTION READ ONLY") for s in seen["main"])


async def test_every_write_lands_in_the_lab_database_only(spy):
    main, lab, seen = spy
    await _workload(main, lab)
    writes = [s for s in seen["lab"] if WRITE.match(s)]
    touched = {m.group(1).lower() for s in writes for m in re.finditer(r"(?:INSERT INTO|UPDATE)\s+(?:tracelab\.)?(\w+)", s, re.I)}
    # `proposals`: Phase 5's rule that editing or deleting a note closes the grouping proposals citing its old version
    # (proposals.close_for_annotation, called from save_verdict / delete_verdict) is one more write in the lab database.
    assert touched <= {"annotations", "annotation_events", "evidence_turns", "evidence_spans", "sample_batches",
                       "sample_items", "golden_sets", "golden_cases", "golden_runs", "golden_results",
                       "proposals"}, touched
    assert {"annotations", "annotation_events", "evidence_turns", "evidence_spans"} <= touched
    async with main.connect() as conn:                       # and nothing of Trace Lab's is in the chat's database schema
        names = {r[0] for r in (await conn.execute(text(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'plenum_cafm'"))).all()}
    assert not names & {"annotations", "annotation_events", "evidence_turns", "evidence_spans"}
