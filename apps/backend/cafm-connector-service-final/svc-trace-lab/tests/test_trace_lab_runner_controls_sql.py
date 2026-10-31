"""Pause, resume, cancel and grade a golden run again, and what boot does (G0 addendum 2.5), on the scratch
Trace Lab schema. The chat and the grader are stand-ins. Skips without TRACE_LAB_TEST_DB_URL."""
from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text

from golden_runner_env import BEARER, ORG, Msg, RecordedOrchestrator, body, drain, install_seeded, principal
from tl_scratch import lab_scratch, requires_scratch  # noqa: F401
from trace_lab import jobs, runner

pytestmark = requires_scratch


async def _started(engine, monkeypatch, **over):
    w = await install_seeded(engine, monkeypatch)
    run = (await runner.start_run(org=ORG, principal=principal(), authorization=BEARER, body=body(**over)))["run"]
    return w, run


async def _step(run):
    out = await runner.step(run["id"], principal=principal(), authorization=BEARER)
    await drain()
    return out


async def _job(run):
    return await jobs.get((await runner.load_run(run["id"], ORG))["job_id"], organization_id=ORG)


async def test_any_reviewer_pauses_and_only_the_starter_resumes_with_a_higher_cap(lab_scratch, monkeypatch):
    w, run = await _started(lab_scratch, monkeypatch)
    someone = principal(user=str(uuid.uuid4()))
    paused = (await runner.pause(run["id"], principal=someone, reason="page_closed"))["run"]   # as the route calls it
    assert (paused["status"], paused["paused_reason"]) == ("paused", "page_closed")
    with pytest.raises(runner.RunRefused) as err:
        await runner.pause(run["id"], org=ORG, principal=someone)
    assert err.value.reason == "run_not_running"
    with pytest.raises(runner.RunRefused) as err:
        await runner.step(run["id"], principal=principal(), authorization=BEARER)
    assert (err.value.reason, err.value.detail["status"]) == ("run_not_running", "paused")
    with pytest.raises(runner.RunRefused) as err:
        await runner.resume(run["id"], org=ORG, principal=someone)
    assert err.value.reason == "not_run_owner"
    with pytest.raises(runner.RunRefused) as err:
        await runner.resume(run["id"], org=ORG, principal=principal(), usd_cap=26)
    assert err.value.reason == "cap_too_high"
    back = (await runner.resume(run["id"], principal=principal(), usd_cap=3))["run"]
    assert (back["status"], back["usd_cap"], back["paused_reason"]) == ("running", 3.0, None)
    with pytest.raises(runner.RunRefused) as err:
        await runner.resume(run["id"], org=ORG, principal=principal())
    assert err.value.reason == "run_not_paused"


async def test_a_capped_run_cannot_resume_over_another_open_run(lab_scratch, monkeypatch):
    w, run = await _started(lab_scratch, monkeypatch)
    await jobs.finish((await runner.load_run(run["id"], ORG))["job_id"], "capped", "cap")
    await runner.start_run(org=ORG, principal=principal(), authorization=BEARER, body=body())
    with pytest.raises(runner.RunRefused) as err:
        await runner.resume(run["id"], org=ORG, principal=principal(), usd_cap=2)
    assert err.value.reason == "run_in_progress"


async def test_cancel_is_final(lab_scratch, monkeypatch):
    w, run = await _started(lab_scratch, monkeypatch)
    out = (await runner.cancel(run["id"], principal=principal(user=str(uuid.uuid4()))))["run"]
    assert out["status"] == "cancelled" and out["finished_at"] and "Cancelled by" in out["error"]
    with pytest.raises(runner.RunRefused) as err:
        await runner.cancel(run["id"], org=ORG, principal=principal())
    assert err.value.reason == "run_not_open"
    with pytest.raises(runner.RunRefused) as err:
        await runner.cancel(str(uuid.uuid4()), org=ORG, principal=principal())
    assert err.value.status_code == 404


class _CancelsDuringTheQuestion(RecordedOrchestrator):
    def __init__(self, script, job_id):
        super().__init__(script)
        self.job_id = job_id

    async def stream(self, user_message, session_id=None, extra_context=None):
        if not self.seen:
            await jobs.finish(self.job_id, "cancelled", "Cancelled.")
        async for ev in super().stream(user_message, session_id, extra_context):
            yield ev


async def test_a_cancel_stops_the_item_before_its_next_message_and_stores_nothing(lab_scratch, monkeypatch):
    w, run = await _started(lab_scratch, monkeypatch)                          # case 21 has a follow-up
    job_id = (await runner.load_run(run["id"], ORG))["job_id"]
    w.orchestrator = _CancelsDuringTheQuestion([Msg(), Msg()], job_id)
    await _step(run)
    assert len(w.orchestrator.seen) == 1 and w.graded == []
    async with lab_scratch.connect() as conn:
        assert (await conn.execute(text("SELECT count(*) FROM tracelab.golden_results"))).scalar_one() == 0
    job = await _job(run)
    assert job["status"] == "cancelled" and float(job["usd_reserved"]) == 0


async def test_a_pause_lets_the_item_in_flight_finish(lab_scratch, monkeypatch):
    w, run = await _started(lab_scratch, monkeypatch, case_keys=["22"], k=2)
    gate = asyncio.Event()

    class Slow(RecordedOrchestrator):
        async def stream(self, user_message, session_id=None, extra_context=None):
            await gate.wait()
            async for ev in super().stream(user_message, session_id, extra_context):
                yield ev

    w.orchestrator = Slow([Msg()])
    await runner.step(run["id"], principal=principal(), authorization=BEARER)
    await runner.pause(run["id"], org=ORG, principal=principal())
    gate.set()
    await drain()
    job = await _job(run)
    assert job["status"] == "paused" and job["done"] == 1
    async with lab_scratch.connect() as conn:
        assert (await conn.execute(text("SELECT outcome FROM tracelab.golden_results"))).scalar_one() == "pass"


async def test_grading_again_picks_up_pending_answers(lab_scratch, monkeypatch):
    w, run = await _started(lab_scratch, monkeypatch, case_keys=["22"])
    w.grader_fails = True
    w.script.append(Msg())
    await _step(run)
    with pytest.raises(runner.RunRefused) as err:
        await runner.regrade(run["id"], org=ORG, principal=principal())
    assert err.value.reason == "run_still_running"
    assert (await _step(run))["done"] is True
    with pytest.raises(runner.RunRefused) as err:
        await runner.regrade(run["id"], org=ORG, principal=principal(), only="all")
    assert (err.value.reason, err.value.status_code) == ("superadmins_only", 403)
    w.grader_fails, w.grade_outcome = False, "fail"
    out = await runner.regrade(run["id"], org=ORG, principal=principal())
    assert out["items"] == 1 and out["estimate_usd"] == pytest.approx(0.022)
    await drain()
    grade_job = await jobs.get(out["job_id"], organization_id=ORG)
    assert (grade_job["kind"], grade_job["status"], grade_job["done"]) == ("golden_grade", "done", 1)
    assert float(grade_job["usd_spent"]) == pytest.approx(0.02)
    async with lab_scratch.connect() as conn:
        row = (await conn.execute(text("SELECT outcome, grade, graded_at FROM tracelab.golden_results"))).mappings().one()
    assert row["outcome"] == "fail" and row["grade"]["outcome"] == "fail" and row["graded_at"] is not None
    (_, regraded) = w.graded[-1]
    assert regraded.answer.startswith("There were 759") and regraded.ended_by == "workflow_completed"
    with pytest.raises(runner.RunRefused) as err:
        await runner.regrade(run["id"], org=ORG, principal=principal())
    assert err.value.reason == "nothing_to_regrade"
    again = await runner.regrade(run["id"], org=ORG, principal=principal("superadmin", None), only="all")
    assert again["items"] == 1


async def test_a_second_regrade_waits_for_the_first(lab_scratch, monkeypatch):
    w, run = await _started(lab_scratch, monkeypatch, case_keys=["22"])
    w.grader_fails = True
    w.script.append(Msg())
    await _step(run)
    await _step(run)

    async def held(job_id):
        await jobs.claim(job_id)
        return True                                            # claimed, but no worker: it stays running

    monkeypatch.setattr(runner, "start_grade_job", held)
    await runner.regrade(run["id"], org=ORG, principal=principal())
    with pytest.raises(runner.RunRefused) as err:
        await runner.regrade(run["id"], org=ORG, principal=principal())
    assert err.value.reason == "regrade_in_progress"


async def test_a_grade_job_naming_a_handler_runs_that_handler(lab_scratch, monkeypatch):
    monkeypatch.setattr(runner, "GRADE_HANDLERS", {})
    seen = []

    async def eval_cases(job):
        seen.append((job.job_id, job.params["run_id"]))
        assert await job.heartbeat({"graded": 0}) is False
        return "done"

    runner.register_grade_handler("eval_cases", eval_cases)
    with pytest.raises(ValueError):
        runner.register_grade_handler("golden", eval_cases)
    with pytest.raises(ValueError):
        runner.register_grade_handler("x", "not callable")
    made = await jobs.create(kind="golden_grade", scope_key="golden_grade:eval:r1", organization_id=ORG,
                             params={"handler": "eval_cases", "run_id": "r1"}, usd_cap=Decimal("1"), actor_id=None)
    assert await runner.start_grade_job(made["id"]) is True
    await drain()
    assert seen == [(made["id"], "r1")] and (await jobs.get(made["id"], organization_id=ORG))["status"] == "done"
    lost = await jobs.create(kind="golden_grade", scope_key="golden_grade:eval:r2", organization_id=ORG,
                             params={"handler": "gone"}, usd_cap=Decimal("1"), actor_id=None)
    await runner.start_grade_job(lost["id"])
    await drain()
    job = await jobs.get(lost["id"], organization_id=ORG)
    assert job["status"] == "failed" and "gone" in job["error"]


async def test_after_a_restart_runs_follow_their_jobs_reservations_return_and_grading_restarts(lab_scratch, monkeypatch):
    w, run = await _started(lab_scratch, monkeypatch, case_keys=["22"])
    job_id = (await runner.load_run(run["id"], ORG))["job_id"]
    await jobs.reserve(job_id, Decimal("0.10"))                      # an item that died with the old process
    grading = await jobs.create(kind="golden_grade", scope_key="golden_grade:old", organization_id=ORG,
                                params={"handler": "gone"}, usd_cap=Decimal("1"), actor_id=None)
    await jobs.claim(grading["id"])
    async with lab_scratch.begin() as conn:
        await conn.execute(text("UPDATE tracelab.jobs SET heartbeat_at = now() - interval '10 minutes'"))
    assert sorted(await jobs.mark_interrupted()) == sorted([job_id, grading["id"]])
    out = await runner.settle_after_restart()
    await drain()
    assert out == {"runs": 1, "released": 1, "grading": 1}
    loaded = await runner.load_run(run["id"], ORG)
    assert loaded["status"] == "interrupted" and float(loaded["usd_reserved"]) == 0
    assert loaded["summary"]["paused_reason"] == "interrupted"
    assert (await jobs.get(grading["id"], organization_id=ORG))["status"] == "failed"   # restarted, then ran
    back = (await runner.resume(run["id"], org=ORG, principal=principal()))["run"]
    assert back["status"] == "running"
