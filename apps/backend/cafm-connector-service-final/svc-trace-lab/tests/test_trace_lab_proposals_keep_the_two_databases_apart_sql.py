"""The proposer reads the chat's tables only through the READ-ONLY main engine and keeps its own rows in the lab
database, and no SQL statement ever spans the two (Golden Rule G0; in production they are two Postgres databases). Both
live in one scratch database here, so the rule is pinned on the statements themselves: every SQL string sent through
each engine is recorded while a workload runs every public path of the proposer (estimate, start, execute, pause,
resume, list, read, for all three kinds). Skips without TRACE_LAB_TEST_DB_URL."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event

import trace_lab_m4_fixtures as fx
from tl_scratch import lab_scratch, main_scratch, requires_scratch  # noqa: F401
from trace_lab import db, proposals

pytestmark = requires_scratch
NOW = datetime.now(timezone.utc)
WRITE = re.compile(r"^\s*(INSERT|UPDATE|DELETE|TRUNCATE|CREATE|ALTER|DROP|GRANT|COPY)\b", re.I)
NO_DRAFT = {k: "" for k in ("name", "definition", "boundary", "nearest_mode", "level", "agent")}
MARK = "-- the proposer starts here"


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


def _reply(n, user, role):
    if role == "trace_lab_grouping":
        handles = [f"N{k}" for k in re.findall(r"^\[N(\d+)\]", user, re.M)]
        return {"actions": [{"action": "no_mode_fits", "note_ids": handles, "mode_id": "", "draft": NO_DRAFT, "reason": "Vague."}]}
    if role == "trace_lab_depth_scan":
        return {"verdict": "present", "quote": "12 open work orders", "location": "answer", "evidence": "Counted."}
    asked = re.search(r"Decide these modes: (.*)", user).group(1).split(", ")
    return {"labels": [{"mode": m, "decision": "present", "basis": "answer", "quote": "12 open work orders",
                        "location": "answer", "evidence": "Counted."} for m in asked]}


async def _workload(main, lab, monkeypatch, seen):
    async def ask(system, user, schema, *, role, max_tokens):
        return _reply(0, user, role), {"usd": 0.001}

    monkeypatch.setenv("TRACE_LAB_JUDGE_MODEL", "claude-sonnet-5")
    monkeypatch.setattr(proposals, "ASK", ask)
    monkeypatch.setattr(proposals, "run_in_background", lambda run_id: None)
    turns = [f"turn-s{i:015d}" for i in range(9)]
    async with main.begin() as conn:
        for t in turns:
            await fx.add_turn(conn, t, session_id=f"sess-{t}", started_at=NOW - timedelta(minutes=5))
            await fx.add_span(conn, t, output=[{"open": 12}])
    async with lab.begin() as conn:
        for t in turns[:4]:
            await fx.add_note(conn, t, verdict="right")                # reviewed: cells run
        for t in turns[4:6]:
            await fx.add_note(conn, t, verdict="wrong")                # notes to group
        cells_mode = await fx.add_mode(conn, "counts_cancelled_jobs_as_open", status="confirmed")
        depth_mode = await fx.add_mode(conn, "invents_asset_codes", status="confirmed", agent="udr")
        for t in turns[6:9]:
            await fx.add_label(conn, t, depth_mode, present=True, quote="12 open work orders")
    for statements in seen.values():
        statements.append(MARK)                                              # what follows is the proposer's own work
    owner = fx.owner()
    window = dict(since=NOW - timedelta(days=1), until=NOW + timedelta(hours=1))
    await proposals.estimate(owner, kind="cells", mode_id=None, **window)
    await proposals.estimate(owner, kind="depth_scan", mode_id=depth_mode, **window)
    await proposals.estimate(owner, kind="grouping", mode_id=None, since=None, until=None)
    runs = []
    for kind, mode in (("depth_scan", depth_mode), ("grouping", None), ("cells", None)):
        w = window if kind != "grouping" else dict(since=None, until=None)
        out = await proposals.start_run(owner, kind=kind, mode_id=mode, usd_cap=1.0, **w)
        await proposals.execute(out["run"]["id"])
        runs.append(out["run"]["id"])
    for run_id in runs:
        await proposals.get_run(owner, run_id)
        await proposals.pause_run(owner, run_id)
    await proposals.list_runs(owner)
    async with lab.connect() as conn:
        await proposals.depth_batch_turns(conn, runs[0], None)
    await proposals.load_evidence(turns, organization_id=fx.ORG_A)
    assert cells_mode


async def test_no_statement_names_both_databases_and_each_engine_sees_only_its_own(spy, monkeypatch):
    main, lab, seen = spy
    await _workload(main, lab, monkeypatch, seen)
    assert len(seen["main"]) > 20 and len(seen["lab"]) > 100                      # the workload really used both engines
    for name, other in (("main", "tracelab"), ("lab", "plenum_cafm")):
        stray = [s for s in seen[name] if other in s.lower()]
        assert stray == [], f"the {name} engine was sent SQL naming {other}: {stray[:1]}"


async def test_the_main_engine_is_only_ever_read_from_and_in_read_only_transactions(spy, monkeypatch):
    main, lab, seen = spy
    await _workload(main, lab, monkeypatch, seen)
    assert [s for s in seen["main"] if WRITE.match(s)] == []
    assert any(s.strip().upper().startswith("SET TRANSACTION READ ONLY") for s in seen["main"])
    reads = " ".join(seen["main"])
    assert "agent_trace_turns" in reads and "agent_trace_spans" in reads and "chat_threads" in reads


async def test_every_write_lands_in_trace_labs_own_tables(spy, monkeypatch):
    main, lab, seen = spy
    await _workload(main, lab, monkeypatch, seen)
    own = seen["lab"][seen["lab"].index(MARK) + 1:]                          # not the test's own seeding of notes and modes
    writes = [s for s in own if WRITE.match(s)]
    touched = {m.group(1).lower() for s in writes
               for m in re.finditer(r"(?:INSERT INTO|UPDATE)\s+(?:tracelab\.)?(\w+)", s, re.I)}
    assert {"proposals", "proposal_runs", "jobs", "evidence_turns", "evidence_spans"} <= touched, touched
    assert touched <= {"proposals", "proposal_runs", "jobs", "evidence_turns", "evidence_spans"}, touched
