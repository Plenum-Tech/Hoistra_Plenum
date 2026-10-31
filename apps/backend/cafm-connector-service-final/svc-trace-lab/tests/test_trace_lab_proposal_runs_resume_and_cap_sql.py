"""The run's job on a throwaway Postgres (the model faked): dispatch stops at the cap, a pause stops it after the items
in flight, a resume re-reads the stored items and skips the cached ones, a model that fails costs an item error and
never a default, a restart's interrupted run can be resumed, and only a run that is not going or finished may be
resumed. Skips without TRACE_LAB_TEST_DB_URL."""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import text

import trace_lab_m4_fixtures as fx
from tl_scratch import lab_scratch, main_scratch, requires_scratch  # noqa: F401
from trace_lab import jobs, proposals
from trace_lab.review_errors import Conflict, Forbidden, Invalid

pytestmark = requires_scratch
NOW = datetime.now(timezone.utc)
ABSENT = {"labels": [{"mode": "M1", "decision": "absent", "basis": "answer", "quote": "", "location": "",
                      "evidence": "The count matches the query result."}]}


async def _reviewed_turns(lab, main, n, prefix="turn-n0"):
    turns = [f"{prefix}{i:014d}" for i in range(n)]
    async with main.begin() as conn:
        for t in turns:
            await fx.add_turn(conn, t)
            await fx.add_span(conn, t, output=[{"open": 12}])
    async with lab.begin() as conn:
        for t in turns:
            await fx.add_note(conn, t, verdict="right")
        mid = await fx.add_mode(conn, "counts_cancelled_jobs_as_open", status="confirmed")
    return turns, mid


def _patch(monkeypatch, reply, *, usd=0.01):
    calls: list[str] = []

    async def ask(system, user, schema, *, role, max_tokens):
        calls.append(user)
        r = reply(len(calls))
        if isinstance(r, Exception):
            raise r
        return r, {"usd": usd}

    monkeypatch.setenv("TRACE_LAB_JUDGE_MODEL", "claude-sonnet-5")
    monkeypatch.setattr(proposals, "ASK", ask)
    monkeypatch.setattr(proposals, "run_in_background", lambda run_id: None)
    return calls


async def _start(cap, kind="cells"):
    return (await proposals.start_run(fx.owner(), kind=kind, mode_id=None, since=NOW - timedelta(days=1),
                                      until=NOW + timedelta(hours=1), usd_cap=cap))["run"]["id"]


async def _job(lab, run_id):
    async with lab.connect() as conn:
        return dict((await conn.execute(text(
            "SELECT j.* FROM tracelab.jobs j JOIN tracelab.proposal_runs r ON r.job_id = j.id "
            "WHERE r.id = CAST(:r AS uuid)"), {"r": run_id})).mappings().one())


async def test_dispatch_stops_at_the_cap(lab_scratch, main_scratch, monkeypatch):
    await _reviewed_turns(lab_scratch, main_scratch, 5)
    _patch(monkeypatch, lambda n: ABSENT)
    run_id = await _start(0.05)
    await proposals.execute(run_id)
    out = await proposals.get_run(fx.owner(), run_id)
    assert out["job"]["status"] == "capped"
    assert 1 <= out["counts"]["pending"] < 5 and out["job"]["usd_spent"] <= 0.05
    assert "spending cap" in out["job"]["error"]
    assert float((await _job(lab_scratch, run_id))["usd_reserved"]) == 0           # nothing is left held back


async def test_a_capped_run_resumes_with_a_higher_cap_and_finishes(lab_scratch, main_scratch, monkeypatch):
    await _reviewed_turns(lab_scratch, main_scratch, 5)
    calls = _patch(monkeypatch, lambda n: ABSENT)
    run_id = await _start(0.05)
    await proposals.execute(run_id)
    first = await proposals.get_run(fx.owner(), run_id)
    assert first["job"]["status"] == "capped"
    resumed = await proposals.resume_run(fx.owner(), run_id, usd_cap=1.0)
    assert resumed["job"]["status"] == "queued" and resumed["job"]["usd_cap"] == 1.0 and resumed["job"]["done"] == 0
    await proposals.execute(run_id)
    second = await proposals.get_run(fx.owner(), run_id)
    assert second["job"]["status"] == "done" and second["counts"]["pending"] == 5
    assert second["job"]["done"] == 5 and len(calls) == 5                         # every answer asked once, in total


async def test_a_resume_rereads_the_stored_items_and_skips_cached_ones(lab_scratch, main_scratch, monkeypatch):
    await _reviewed_turns(lab_scratch, main_scratch, 3)
    calls = _patch(monkeypatch, lambda n: proposals.ModelStopped("invalid_json", {"usd": 0.01}) if n == 2 else ABSENT)
    run_id = await _start(1.0)
    await proposals.execute(run_id)
    first = await proposals.get_run(fx.owner(), run_id)
    assert first["job"]["status"] == "paused" and first["counts"]["pending"] == 2 and first["counts"]["errors"] == 1
    assert first["job"]["usd_spent"] == pytest.approx(0.03) and first["job"]["done"] == 2
    async with main_scratch.begin() as conn:                                      # a new answer after the run was planned
        await fx.add_turn(conn, "turn-n9000000000000001")
    async with lab_scratch.begin() as conn:
        await fx.add_note(conn, "turn-n9000000000000001", verdict="right")
    calls.clear()
    await proposals.resume_run(fx.owner(), run_id)
    await proposals.execute(run_id)
    second = await proposals.get_run(fx.owner(), run_id)
    assert second["job"]["status"] == "done" and second["counts"]["pending"] == 3 and len(calls) == 1
    assert second["counts"]["errors"] == 0 and second["job"]["done"] == 3 and second["job"]["error"] is None


async def test_an_items_checkpoint_gives_back_its_reservation_and_pays_for_what_it_used(lab_scratch, main_scratch,
                                                                                         monkeypatch):
    await _reviewed_turns(lab_scratch, main_scratch, 1)
    _patch(monkeypatch, lambda n: ABSENT)
    run_id = await _start(1.0)
    job = await _job(lab_scratch, run_id)
    assert await jobs.claim(str(job["id"])) is not None
    run, state = {"id": run_id, "job_id": str(job["id"])}, {"errors": 0, "written": 0}
    await jobs.reserve(run["job_id"], Decimal("0.03"))
    await proposals._fail_item(run, state, usd=Decimal("0.012"), worst=Decimal("0.03"), error="model stopped: invalid_json")
    failed = await _job(lab_scratch, run_id)
    assert float(failed["usd_reserved"]) == 0 and float(failed["usd_spent"]) == pytest.approx(0.012)
    assert failed["done"] == 0 and state["errors"] == 1                         # an error is not progress: a resume retries it
    assert failed["progress"]["errors"] == 1 and failed["progress"]["last_error"] == "model stopped: invalid_json"
    await jobs.reserve(run["job_id"], Decimal("0.02"))
    await proposals._skip_item(run, state, "cached")
    skipped = await _job(lab_scratch, run_id)
    assert float(skipped["usd_reserved"]) == pytest.approx(0.02) and float(skipped["usd_spent"]) == pytest.approx(0.012)
    assert skipped["done"] == 1 and skipped["progress"]["last_skip"] == "cached"       # a skip is progress and costs nothing


UNSURE = {"labels": [{"mode": "M1", "decision": "unsure", "basis": "none", "quote": "", "location": "",
                      "evidence": "Cannot tell from what is given."}]}


async def test_a_suggestion_that_is_no_longer_pending_is_not_bought_again_on_resume(lab_scratch, main_scratch, monkeypatch):
    # An 'unsure' reply is stored closed, so the cell is not pending any more: only the prompt's cache key stops a
    # resume from paying for the same question twice.
    await _reviewed_turns(lab_scratch, main_scratch, 3)
    calls = _patch(monkeypatch, lambda n: proposals.ModelStopped("invalid_json", {"usd": 0.01}) if n == 2 else UNSURE)
    run_id = await _start(1.0)
    await proposals.execute(run_id)
    first = await proposals.get_run(fx.owner(), run_id)
    assert first["job"]["status"] == "paused" and first["counts"]["closed"] == 2 and first["counts"]["pending"] == 0
    calls.clear()
    await proposals.resume_run(fx.owner(), run_id)
    await proposals.execute(run_id)
    second = await proposals.get_run(fx.owner(), run_id)
    assert len(calls) == 1 and second["job"]["status"] == "done" and second["counts"]["closed"] == 3


async def test_a_provider_error_is_an_item_error_counted_and_retried_on_resume(lab_scratch, main_scratch, monkeypatch):
    await _reviewed_turns(lab_scratch, main_scratch, 2)
    calls = _patch(monkeypatch, lambda n: ConnectionError("the provider is down") if n == 1 else ABSENT)
    run_id = await _start(1.0)
    await proposals.execute(run_id)
    first = await proposals.get_run(fx.owner(), run_id)
    assert first["job"]["status"] == "paused" and first["counts"]["errors"] == 1 and first["counts"]["pending"] == 1
    assert first["job"]["usd_spent"] == pytest.approx(0.01)                        # the failed call cost nothing
    assert "could not be read" in first["job"]["error"]
    await proposals.resume_run(fx.owner(), run_id)
    await proposals.execute(run_id)
    assert (await proposals.get_run(fx.owner(), run_id))["job"]["status"] == "done" and len(calls) == 3


async def test_a_grouping_resume_asks_again_only_for_what_did_not_finish(lab_scratch, main_scratch, monkeypatch):
    n = 41                                                                          # two batches, so a merge call follows
    async with main_scratch.begin() as conn:
        for i in range(n):
            await fx.add_turn(conn, f"turn-n7{i:014d}", session_id=f"sess-g{i}")
    async with lab_scratch.begin() as conn:
        for i in range(n):
            await fx.add_note(conn, f"turn-n7{i:014d}", verdict="wrong", note=f"note {i} counts cancelled jobs")
    roles: list[str] = []
    state = {"merge_calls": 0}
    draft = {"name": "counts_cancelled_jobs_as_open", "definition": "Present when the answer counts cancelled jobs as open.",
             "boundary": "Not when on hold.", "nearest_mode": "", "level": "technical", "agent": "wo_engine"}

    async def ask(system, user, schema, *, role, max_tokens):
        roles.append(role)
        if role == "trace_lab_grouping":
            handles = [f"N{k}" for k in re.findall(r"^\[N(\d+)\]", user, re.M)]
            return {"actions": [{"action": "new_mode", "note_ids": handles, "mode_id": "",
                                 "draft": dict(draft, name=f"draft_{len(roles)}_counts_jobs"), "reason": "Same fix."}]}, {"usd": 0.01}
        state["merge_calls"] += 1
        if state["merge_calls"] == 1:
            raise proposals.ModelStopped("invalid_json", {"usd": 0.01})
        return {"modes": [{"from_drafts": ["D1", "D2"], **draft, "reason": "One fix."}]}, {"usd": 0.01}

    monkeypatch.setenv("TRACE_LAB_JUDGE_MODEL", "claude-sonnet-5")
    monkeypatch.setattr(proposals, "ASK", ask)
    monkeypatch.setattr(proposals, "run_in_background", lambda run_id: None)
    run_id = (await proposals.start_run(fx.owner(), kind="grouping", mode_id=None, since=None, until=None,
                                        usd_cap=1.0))["run"]["id"]
    await proposals.execute(run_id)
    first = await proposals.get_run(fx.owner(), run_id)
    assert sorted(roles) == ["trace_lab_grouping", "trace_lab_grouping", "trace_lab_grouping_merge"]
    assert first["job"]["status"] == "paused" and first["counts"]["errors"] == 1 and first["counts"]["pending"] == 2
    roles.clear()
    await proposals.resume_run(fx.owner(), run_id)
    await proposals.execute(run_id)
    second = await proposals.get_run(fx.owner(), run_id)
    assert roles == ["trace_lab_grouping_merge"]                                     # the two batches were already paid for
    assert second["job"]["status"] == "done" and second["counts"]["pending"] == 1 and second["counts"]["errors"] == 0
    assert second["job"]["done"] == 3 and second["job"]["usd_spent"] == pytest.approx(0.04)


async def test_a_pause_stops_after_the_items_in_flight_and_a_resume_continues(lab_scratch, main_scratch, monkeypatch):
    await _reviewed_turns(lab_scratch, main_scratch, 8)
    gate = asyncio.Event()
    started = asyncio.Event()
    calls: list[str] = []

    async def ask(system, user, schema, *, role, max_tokens):
        calls.append(user)
        started.set()
        await gate.wait()
        return ABSENT, {"usd": 0.001}

    monkeypatch.setenv("TRACE_LAB_JUDGE_MODEL", "claude-sonnet-5")
    monkeypatch.setattr(proposals, "ASK", ask)
    monkeypatch.setattr(proposals, "run_in_background", lambda run_id: None)
    run_id = await _start(1.0)
    task = asyncio.create_task(proposals.execute(run_id))
    await asyncio.wait_for(started.wait(), 30)
    paused = await proposals.pause_run(fx.owner(), run_id)
    assert paused["job"]["status"] == "running"                                     # it stops after the items in flight
    gate.set()
    await asyncio.wait_for(task, 60)
    out = await proposals.get_run(fx.owner(), run_id)
    assert out["job"]["status"] == "paused" and 1 <= out["counts"]["pending"] < 8
    done_first = out["counts"]["pending"]
    gate.clear()
    started.clear()
    await proposals.resume_run(fx.owner(), run_id)
    gate.set()
    await proposals.execute(run_id)
    final = await proposals.get_run(fx.owner(), run_id)
    assert final["job"]["status"] == "done" and final["counts"]["pending"] == 8 and len(calls) == 8 and done_first < 8


async def test_only_a_run_that_is_not_going_or_finished_may_be_resumed(lab_scratch, main_scratch, monkeypatch):
    await _reviewed_turns(lab_scratch, main_scratch, 2)
    _patch(monkeypatch, lambda n: ABSENT)
    run_id = await _start(1.0)
    with pytest.raises(Conflict) as e:                                              # queued: its task is about to claim it
        await proposals.resume_run(fx.owner(), run_id)
    assert e.value.reason == "run_in_progress"
    await proposals.execute(run_id)
    with pytest.raises(Invalid) as e:                                               # done: nothing to resume
        await proposals.resume_run(fx.owner(), run_id)
    assert e.value.reason == "run_finished" and e.value.extra == {"given": "done"}
    out = await proposals.pause_run(fx.owner(), run_id)                             # pausing a finished run changes nothing
    assert out["job"]["status"] == "done"


async def test_a_second_run_of_a_kind_waits_for_the_first(lab_scratch, main_scratch, monkeypatch):
    await _reviewed_turns(lab_scratch, main_scratch, 2)
    _patch(monkeypatch, lambda n: ABSENT)
    await _start(1.0)
    with pytest.raises(Conflict) as e:
        await _start(1.0)
    assert e.value.reason == "run_in_progress"


async def test_a_resume_checks_the_cap_against_the_run_limit_and_the_month(lab_scratch, main_scratch, monkeypatch):
    await _reviewed_turns(lab_scratch, main_scratch, 4)
    _patch(monkeypatch, lambda n: ABSENT)
    run_id = await _start(0.05)
    await proposals.execute(run_id)
    assert (await proposals.get_run(fx.owner(), run_id))["job"]["status"] == "capped"
    with pytest.raises(Invalid) as e:
        await proposals.resume_run(fx.owner(), run_id, usd_cap=26.0)
    assert e.value.reason == "cap_above_limit"
    with pytest.raises(Invalid) as e:
        await proposals.resume_run(fx.owner(), run_id, usd_cap=0)
    assert e.value.reason == "bad_cap"
    monkeypatch.setenv("TRACE_LAB_PROPOSALS_MONTHLY_USD", "0.5")
    async with lab_scratch.begin() as conn:                                         # another run already spent most of the month
        await conn.execute(text(
            "INSERT INTO tracelab.jobs (kind, status, scope_key, usd_cap, usd_spent) "
            "VALUES ('proposal_grouping', 'done', 'test:month', 1, 0.4)"))
    with pytest.raises(Conflict) as e:
        await proposals.resume_run(fx.owner(), run_id, usd_cap=0.5)
    assert e.value.reason == "monthly_cap"
    assert (await _job(lab_scratch, run_id))["status"] == "capped"                  # refused: nothing changed


async def test_a_run_a_restart_interrupted_can_be_resumed(lab_scratch, main_scratch, monkeypatch):
    await _reviewed_turns(lab_scratch, main_scratch, 3)
    calls = _patch(monkeypatch, lambda n: ABSENT)
    run_id = await _start(1.0)
    job = await _job(lab_scratch, run_id)
    claimed = await jobs.claim(str(job["id"]), worker="old-process:1")             # a process that then died mid-run
    assert claimed is not None
    async with lab_scratch.begin() as conn:                                         # it died holding a reservation
        await conn.execute(text("UPDATE tracelab.jobs SET heartbeat_at = now() - interval '10 minutes', "
                                "usd_reserved = 0.9 WHERE id = :i"), {"i": job["id"]})
    assert await jobs.mark_interrupted() == [str(job["id"])]
    assert float((await _job(lab_scratch, run_id))["usd_reserved"]) == pytest.approx(0.9)
    resumed = await proposals.resume_run(fx.owner(), run_id)
    assert resumed["job"]["status"] == "queued" and resumed["job"]["error"] is None
    assert float((await _job(lab_scratch, run_id))["usd_reserved"]) == 0           # nothing is in flight any more
    await proposals.execute(run_id)
    out = await proposals.get_run(fx.owner(), run_id)
    assert out["job"]["status"] == "done" and out["counts"]["pending"] == 3 and len(calls) == 3


async def test_the_final_progress_is_exactly_what_the_pass_counted(lab_scratch, main_scratch, monkeypatch):
    # Items finish at the same time (four at once), so what each checkpoint wrote can be stale: here the checkpoints
    # carry no progress at all, and the pass itself must still leave the job's progress exact.
    await _reviewed_turns(lab_scratch, main_scratch, 9)
    _patch(monkeypatch, lambda n: ABSENT)
    real = jobs.checkpoint

    async def without_progress(conn, job_id, **kw):
        await real(conn, job_id, **dict(kw, progress_patch=None))

    monkeypatch.setattr(jobs, "checkpoint", without_progress)
    run_id = await _start(1.0)
    await proposals.execute(run_id)
    out = await proposals.get_run(fx.owner(), run_id)
    assert out["counts"]["pending"] == 9 and out["job"]["progress"]["written"] == 9
    assert out["job"]["progress"]["errors"] == 0 and out["job"]["done"] == 9


async def test_a_job_someone_else_holds_is_not_run_twice(lab_scratch, main_scratch, monkeypatch):
    await _reviewed_turns(lab_scratch, main_scratch, 2)
    calls = _patch(monkeypatch, lambda n: ABSENT)
    run_id = await _start(1.0)
    job = await _job(lab_scratch, run_id)
    assert await jobs.claim(str(job["id"]), worker="another-process:2") is not None
    await proposals.execute(run_id)                                                 # cannot claim: does nothing
    assert calls == [] and (await _job(lab_scratch, run_id))["status"] == "running"
    await proposals.execute("00000000-0000-4000-8000-000000000000")                 # an unknown run: does nothing
    await proposals.execute("not-a-uuid")


async def test_the_runs_are_listed_newest_first_with_their_jobs(lab_scratch, main_scratch, monkeypatch):
    await _reviewed_turns(lab_scratch, main_scratch, 2)
    _patch(monkeypatch, lambda n: ABSENT)
    first = await _start(1.0)
    await proposals.execute(first)
    listed = await proposals.list_runs(fx.owner())
    assert [r["id"] for r in listed] == [first]
    assert listed[0]["job"]["status"] == "done" and listed[0]["job"]["total"] == 2 and listed[0]["job"]["done"] == 2
    assert listed[0]["kind"] == "cells" and "items" not in listed[0]["params"] and listed[0]["items_planned"] == 2
    with pytest.raises(Forbidden):
        await proposals.list_runs(fx.admin())
