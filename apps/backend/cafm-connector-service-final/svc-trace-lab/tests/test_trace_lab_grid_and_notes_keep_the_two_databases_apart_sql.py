"""The Labels grid, the note filing, the unfiled list and the discovery read use the chat's tables only through the
READ-ONLY main engine and keep their own rows in the lab database, and no SQL statement ever spans the two (Golden Rule
G0; in production they are two Postgres databases). Both live in one scratch database here, so the rule is pinned on the
statements themselves: every SQL string sent through each engine is recorded while a workload runs every public path of
this task's code. Skips without TRACE_LAB_TEST_DB_URL."""
from __future__ import annotations

import re

import pytest
from sqlalchemy import event

import trace_lab_m4_fixtures as fx
from tl_scratch import lab_scratch, main_scratch, requires_scratch  # noqa: F401
from trace_lab import annotations, db, labels, taxonomy

pytestmark = requires_scratch
WRITE = re.compile(r"^\s*(INSERT|UPDATE|DELETE|TRUNCATE|CREATE|ALTER|DROP|GRANT|COPY)\b", re.I)


@pytest.fixture
async def spy(main_scratch, lab_scratch):  # noqa: F811
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
    turns = [f"turn-w{i:015d}" for i in range(4)]
    async with main.begin() as conn:
        for t in turns:
            await fx.add_turn(conn, t, session_id="sess-spy")
    async with lab.begin() as conn:
        notes = [await fx.add_note(conn, t) for t in turns]
        m1 = await fx.add_mode(conn, "counts_cancelled_jobs_as_open", status="confirmed")
        m2 = await fx.add_mode(conn, "invents_asset_codes", status="frozen")
        await fx.add_batch_item(conn, turns[0], strategy="uniform", plan_step="initial_uniform")
        await fx.add_label(conn, turns[0], m2, present=True)
    me, owner = fx.admin(), fx.owner()
    await labels.matrix(me, fx.ORG_A)
    await labels.matrix(me, fx.ORG_A, state="missing", limit=1, offset=1)
    await labels.matrix(owner, None, filter_org=fx.ORG_A)
    await labels.completeness(me, fx.ORG_A)
    await labels.per_mode(owner, None)
    await annotations.unfiled_notes(me, fx.ORG_A)
    await annotations.unfiled_notes(owner, None)
    async with lab.connect() as conn:
        await annotations.unfiled_rows(conn, fx.ORG_A)                                  # with the answers' text from the chat
        await labels.uniform_human_stats(conn, fx.ORG_A, [m1, m2])
        await labels.undecided_after(conn, fx.ORG_A, turns, set())
        await labels.present_turn_ids(conn, m2, None)
        await labels.turn_present_modes(conn, turns[0])
        graph = await taxonomy.load_graph(conn)
        await labels.decided_cells(conn, graph, graph.modes[m2])
    await labels.human_origin_cells(m2)
    await annotations.file_note(me, fx.ORG_A, notes[1], m1, base=None)
    await annotations.dispose_note(owner, notes[2], outcome="no_mode_fits", note="Too vague.")
    await annotations.undispose_note(owner, notes[2])
    edited = await annotations.save_verdict(
        me, fx.ORG_A, turns[3], verdict="wrong", note="Again.", base=notes[3],
        anchor=annotations.Anchor(span_id=None, field="answer", quote="12 open", occurrence=1))
    await annotations.delete_verdict(me, fx.ORG_A, turns[3], base=edited["id"])
    await taxonomy.discovery(me, fx.ORG_A)


async def test_no_statement_names_both_databases_and_each_engine_sees_only_its_own(spy):
    main, lab, seen = spy
    await _workload(main, lab)
    assert len(seen["main"]) > 8 and len(seen["lab"]) > 60                       # the workload really used both engines
    for name, other in (("main", "tracelab"), ("lab", "plenum_cafm")):
        stray = [s for s in seen[name] if other in s.lower()]
        assert stray == [], f"the {name} engine was sent SQL naming {other}: {stray[:1]}"


async def test_the_main_engine_is_only_ever_read_from_and_in_read_only_transactions(spy):
    main, lab, seen = spy
    await _workload(main, lab)
    assert [s for s in seen["main"] if WRITE.match(s)] == []
    assert any(s.strip().upper().startswith("SET TRANSACTION READ ONLY") for s in seen["main"])
    reads = [s for s in seen["main"] if "agent_trace_turns" in s]
    assert reads, "the grid and the note text never read the chat's answers"


async def test_every_write_lands_in_trace_labs_own_tables(spy):
    main, lab, seen = spy
    await _workload(main, lab)
    writes = [s for s in seen["lab"] if WRITE.match(s)]
    touched = {m.group(1).lower() for s in writes
               for m in re.finditer(r"(?:INSERT INTO|UPDATE)\s+(?:tracelab\.)?(\w+)", s, re.I)}
    assert {"labels", "annotation_events", "evidence_turns"} <= touched, touched
    assert touched <= {"labels", "annotation_events", "evidence_turns", "evidence_spans", "annotations", "proposals",
                       "modes", "mode_events", "sample_batches", "sample_items", "jobs", "proposal_runs"}, touched
