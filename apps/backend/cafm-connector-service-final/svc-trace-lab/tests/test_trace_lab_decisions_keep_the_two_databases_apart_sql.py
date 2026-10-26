"""Queues, decisions, bulk approval, the Progress rows and the enrich sources read the chat's tables only through the
READ-ONLY main engine and keep their rows in the lab database, and no SQL statement ever spans the two (Golden Rule G0;
in production they are two Postgres databases). Both live in one scratch database here, so the rule is pinned on the
statements themselves: every SQL string sent through each engine is recorded while a workload runs every public path
this task added. Skips without TRACE_LAB_TEST_DB_URL."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event

import trace_lab_m2_fixtures as m2
import trace_lab_m4_fixtures as fx
from tl_scratch import lab_scratch, main_scratch, requires_scratch  # noqa: F401
from trace_lab import db, labels, proposals, sampling, taxonomy

pytestmark = requires_scratch
WRITE = re.compile(r"^\s*(INSERT|UPDATE|DELETE|TRUNCATE|CREATE|ALTER|DROP|GRANT|COPY)\b", re.I)
MARK = "-- the decisions start here"
NAME = "counts_cancelled_jobs_as_open"


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


async def _workload(main, lab, seen):
    owner, admin = fx.owner(), fx.admin()
    turns = [f"turn-w{i:015d}" for i in range(26)]
    async with main.begin() as conn:
        for t in turns:
            await fx.add_turn(conn, t, session_id=f"sess-{t}")
            await fx.add_span(conn, t, output=[{"open": 12}])
    async with lab.begin() as conn:
        for t in turns:
            await fx.add_note(conn, t, verdict="right")                       # judged by the company's first admin
        wrong = [await fx.add_note(conn, turns[i], reviewer=fx.admin(n=3), note="Wrong building.") for i in range(3)]
        mode = await fx.add_mode(conn, NAME, status="confirmed")
        other = await fx.add_mode(conn, "includes_cancelled_jobs", status="confirmed")
        cells = await fx.add_run(conn, "cells", canary_total=5, canary_agree=5)
        scan = await fx.add_run(conn, "depth_scan", mode_id=mode)
        group = await fx.add_run(conn, "grouping")
        cell = [await fx.add_proposal(conn, cells, turn_id=t, mode_id=mode, suggestion="absent", evidence=f"Checked {i}.")
                for i, t in enumerate(turns[:22])]
        depth = await fx.add_proposal(conn, scan, kind="depth_scan", turn_id=turns[22], mode_id=mode,
                                      quote="12 open work orders", field="answer")
        override = await fx.add_proposal(conn, cells, turn_id=turns[23], mode_id=other, suggestion="present")
        g_file = await fx.add_proposal(conn, group, kind="group", suggestion="file_under", mode_id=other,
                                       annotation_ids=[wrong[0]])
        g_new = await fx.add_proposal(conn, group, kind="group", suggestion="new_mode", annotation_ids=[wrong[1]],
                                      draft={"name": "invents_asset_codes", "definition": "Present when asset codes are invented.",
                                             "boundary": "", "level": "technical", "agent": "udr"})
        g_none = await fx.add_proposal(conn, group, kind="group", suggestion="no_mode_fits", annotation_ids=[wrong[2]])
        await fx.add_label(conn, turns[24], mode, present=True, quote="12 open work orders")
        await fx.add_proposal(conn, scan, kind="depth_scan", turn_id=turns[25], mode_id=mode, status="accepted")
    for statements in seen.values():
        statements.append(MARK)
    served = datetime.now(timezone.utc) - timedelta(hours=1)
    for who, org in ((owner, None), (admin, fx.ORG_A), (fx.admin(n=2), fx.ORG_A)):
        for kind in ("suggestion", "cell", "depth_scan"):
            await proposals.queue(who, org, kind=kind)
    await proposals.queue(owner, None, kind="group")
    await proposals.queue(admin, fx.ORG_A, kind="cell", turn_id=turns[0], status="closed")
    await proposals.decide(admin, fx.ORG_A, depth, decision="accept")
    await proposals.decide(admin, fx.ORG_A, override, decision="override", present=False)
    for pid in (g_file, g_new, g_none):
        await proposals.decide(owner, None, pid, decision="accept", override_labels=True)
    check = await proposals.bulk_check(admin, fx.ORG_A, action="accept", proposal_ids=cell, served_at=served)
    for it in check["spot_check"]["items"]:
        await labels.set_label(admin, fx.ORG_A, it["turn_id"], it["mode_id"], present=False, base=it["base"],
                               spot_check_seed=check["spot_check"]["seed"])
    await proposals.bulk_check(owner, None, action="accept", proposal_ids=cell[:5], served_at=served)
    await proposals.bulk_approve(admin, fx.ORG_A, action="accept", proposal_ids=cell, served_at=served, bases={})
    await taxonomy.progress_extension(owner, None)
    await taxonomy.progress_extension(admin, fx.ORG_A)
    await sampling.progress(admin, fx.ORG_A, since=None, until=None)
    req = {"plan_step": "depth_search", "strategy": "enrich", "k": 3, "seed": 7, "channel": "chat", "reviews_required": 1,
           "since": m2.NOW - timedelta(days=7), "until": m2.NOW + timedelta(hours=1)}
    await sampling.preview(admin, fx.ORG_A, dict(req, mode_id=mode))
    await sampling.preview(admin, fx.ORG_A, dict(req, source="depth_scan", proposal_run_id=scan))
    return turns


async def test_no_statement_names_both_databases_and_each_engine_sees_only_its_own(spy):
    main, lab, seen = spy
    await _workload(main, lab, seen)
    assert len(seen["main"]) > 30 and len(seen["lab"]) > 300                      # the workload really used both engines
    for name, other in (("main", "tracelab"), ("lab", "plenum_cafm")):
        stray = [s for s in seen[name] if other in s.lower()]
        assert stray == [], f"the {name} engine was sent SQL naming {other}: {stray[:1]}"


async def test_the_main_engine_is_only_ever_read_from_and_in_read_only_transactions(spy):
    main, lab, seen = spy
    await _workload(main, lab, seen)
    after = seen["main"][seen["main"].index(MARK) + 1:]
    assert [s for s in seen["main"] if WRITE.match(s)] == [] and after
    assert any(s.strip().upper().startswith("SET TRANSACTION READ ONLY") for s in after)
    assert "agent_trace_turns" in " ".join(after)


async def test_every_write_lands_in_trace_labs_own_tables(spy):
    main, lab, seen = spy
    await _workload(main, lab, seen)
    own = seen["lab"][seen["lab"].index(MARK) + 1:]                          # not the test's own seeding
    touched = {m.group(1).lower() for s in own if WRITE.match(s)
               for m in re.finditer(r"(?:INSERT INTO|UPDATE)\s+(?:tracelab\.)?(\w+)", s, re.I)}
    assert {"proposals", "labels", "evidence_turns", "modes", "annotation_events"} <= touched, touched
    assert touched <= {"proposals", "labels", "evidence_turns", "evidence_spans", "modes", "mode_events",
                       "annotation_events", "jobs"}, touched
