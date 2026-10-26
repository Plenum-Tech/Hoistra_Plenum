"""Labels and failure modes read the chat's tables through the READ-ONLY main engine and keep their own rows in the lab
database, and no SQL statement ever spans the two (Golden Rule G0; in production they are two Postgres databases). Here
both live in one scratch database, so the rule is pinned on the statements themselves: every SQL string sent through
each engine is recorded while a workload runs every public path of the label, mode, merge/split and proposal code.
Skips without TRACE_LAB_TEST_DB_URL."""
from __future__ import annotations

import re

import pytest
from sqlalchemy import event

import trace_lab_m4_fixtures as fx
from tl_scratch import lab_scratch, main_scratch, requires_scratch  # noqa: F401
from trace_lab import annotations, db, labels, taxonomy

pytestmark = requires_scratch
WRITE = re.compile(r"^\s*(INSERT|UPDATE|DELETE|TRUNCATE|CREATE|ALTER|DROP|GRANT|COPY)\b", re.I)
DRAFT = {"name": "counts_cancelled_jobs_as_open", "definition": "Present when the answer counts cancelled jobs as open.",
         "boundary": "", "level": "technical", "agent": "wo_engine"}


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
    turns = [f"turn-s{i:015d}" for i in range(5)]
    async with main.begin() as conn:
        for t in turns:
            await fx.add_turn(conn, t, session_id="sess-spy")
        await fx.add_span(conn, turns[0], output={"rows": 12})
    async with lab.begin() as conn:
        notes = [await fx.add_note(conn, t) for t in turns[:3]]
        other = await fx.add_mode(conn, "includes_cancelled_jobs")
        sib = await fx.add_mode(conn, "counts_closed_jobs_as_open")
    for t in turns[3:]:                                    # the first three already carry this admin's wrong notes
        await annotations.save_verdict(fx.admin(), fx.ORG_A, t, verdict="right", note=None, anchor=None, base=None)
    parent = (await taxonomy.create(fx.owner(), dict(DRAFT, name="wrong_job_status_counts"), created_from=notes[:2]))["mode"]
    first = await labels.set_label(fx.owner(), None, turns[3], parent["id"], present=False, base=None,
                                   anchor={"span_id": None, "field": "answer", "quote": "12 open", "occurrence": 1})
    await labels.set_label(fx.owner(), None, turns[3], parent["id"], present=None, base=first["label"]["id"])
    await labels.history(fx.owner(), None, turns[3], parent["id"])
    await labels.cells_for_turn(fx.owner(), None, turns[0])
    await labels.cells_for_turn(fx.admin(), fx.ORG_A, turns[0])
    await taxonomy.list_modes(fx.admin(), fx.ORG_A)
    await taxonomy.get_mode(fx.admin(), fx.ORG_A, parent["id"])
    edited = (await taxonomy.edit(fx.owner(), parent["id"], {"boundary": "Not on hold."}, base_version=parent["version"]))["mode"]
    await taxonomy.rename(fx.owner(), other, "includes_cancelled_work", base_version=1)
    await taxonomy.merge(fx.owner(), [other], into=sib, new=None, base_versions={other: 2, sib: 1})
    await taxonomy.split(fx.owner(), parent["id"], [dict(DRAFT, name="child_one_mode", created_from=[notes[0]]),
                                                    dict(DRAFT, name="child_two_mode", created_from=[notes[1]])],
                         {turns[0]: [0]}, base_version=edited["version"])
    async with lab.connect() as conn:
        await labels.reviewed_rows(conn, fx.ORG_A)
    proposed = (await taxonomy.create(fx.admin(), dict(DRAFT, name="proposed_by_a_company"), created_from=notes[2:],
                                      org=fx.ORG_A))["mode"]
    await taxonomy.list_proposals(fx.admin(), fx.ORG_A)
    await taxonomy.list_proposals(fx.owner(), None, status="all")
    await taxonomy.accept_proposal(fx.owner(), proposed["id"])
    again = (await taxonomy.create(fx.admin(), dict(DRAFT, name="rejected_by_the_platform"), created_from=notes[:1],
                                   org=fx.ORG_A))["mode"]
    await taxonomy.reject_proposal(fx.owner(), again["id"], "Too vague.")


async def test_no_statement_names_both_databases_and_each_engine_sees_only_its_own(spy):
    main, lab, seen = spy
    await _workload(main, lab)
    assert len(seen["main"]) > 10 and len(seen["lab"]) > 60                 # the workload really used both engines
    for name, other in (("main", "tracelab"), ("lab", "plenum_cafm")):
        stray = [s for s in seen[name] if other in s.lower()]
        assert stray == [], f"the {name} engine was sent SQL naming {other}: {stray[:1]}"


async def test_the_main_engine_is_only_ever_read_from(spy):
    main, lab, seen = spy
    await _workload(main, lab)
    writes = [s for s in seen["main"] if WRITE.match(s)]
    assert writes == [], writes[:1]                          # seeding goes through the separate writable engine
    reads = [s for s in seen["main"] if s.strip().upper().startswith("SET TRANSACTION READ ONLY")]
    assert reads, "the code never opened a read-only transaction on the main engine"


async def test_every_write_the_code_makes_lands_in_trace_labs_own_tables(spy):
    main, lab, seen = spy
    await _workload(main, lab)
    writes = [s for s in seen["lab"] if WRITE.match(s)]
    touched = {m.group(1).lower() for s in writes for m in re.finditer(r"(?:INSERT INTO|UPDATE)\s+(?:tracelab\.)?(\w+)", s, re.I)}
    assert {"labels", "modes", "mode_events", "evidence_turns"} <= touched, touched
    assert touched <= {"labels", "modes", "mode_events", "evidence_turns", "evidence_spans", "annotations",
                       "annotation_events", "proposals", "jobs", "proposal_runs", "sample_batches", "sample_items"}, touched
