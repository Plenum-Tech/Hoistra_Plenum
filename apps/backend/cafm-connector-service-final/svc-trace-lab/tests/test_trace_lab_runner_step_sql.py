"""Stepping a golden run on the scratch Trace Lab schema (G0 addendum 2.5): each step runs one item in the
background with the page's bearer - every message in one session, captured, graded and stored with its
evidence - infrastructure failures are retried and never graded, refusals read degraded, caps stop the run
before an item, and nothing left means done. The chat is a recorded stand-in. Skips without
TRACE_LAB_TEST_DB_URL."""
from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import text

from golden_runner_env import BEARER, EXPECTED, ORG, Msg, body, drain, install_seeded, jwt, principal
from src.agents import session_workspace
from tl_scratch import lab_scratch, requires_scratch  # noqa: F401
from trace_lab import jobs, runner

pytestmark = requires_scratch


async def _started(engine, monkeypatch, **over):
    w = await install_seeded(engine, monkeypatch)
    run = (await runner.start_run(org=ORG, principal=principal(), authorization=BEARER, body=body(**over)))["run"]
    return w, run


async def _step(run, who=None, token=BEARER):
    out = await runner.step(run["id"], principal=who or principal(), authorization=token)
    await drain()
    return out


async def _rows(engine, sql: str, **params):
    async with engine.connect() as conn:
        return [dict(r) for r in (await conn.execute(text(sql), params)).mappings().all()]


async def test_a_step_answers_one_item_and_stores_every_message_with_its_evidence(lab_scratch, monkeypatch):
    w, run = await _started(lab_scratch, monkeypatch)
    card = {"kpis": [{"label": "Work orders", "value": 759}]}
    w.script += [Msg(answer="759 work orders.", cards=[card]), Msg(answer="733 of them were reactive.")]
    out = await _step(run)
    assert out["started"] is True and out["item"] == {"case_key": "21", "rep": 1, "attempt": 1}
    rows = await _rows(lab_scratch, "SELECT * FROM tracelab.golden_results ORDER BY msg_idx")
    sid = runner.session_id_for(run["id"], "21", 1)
    assert [r["msg_idx"] for r in rows] == [0, 1] and {r["session_id"] for r in rows} == {sid}
    assert rows[0]["turn_id"] != rows[1]["turn_id"] and all(r["turn_id"].startswith("turn-g") for r in rows)
    assert [r["answer"] for r in rows] == ["759 work orders.", "733 of them were reactive."]
    # the final row (the case's last message) carries the outcome and the grade; earlier rows stay pending
    assert [r["outcome"] for r in rows] == ["pending", "pass"] and rows[0]["grade"] is None
    assert rows[0]["graded_at"] is None and rows[1]["graded_at"] is not None and rows[0]["refused"] == []
    assert rows[1]["grade"]["grader_version"] == "grader-test-1" and rows[1]["grade"]["usd"] == 0.02
    assert float(rows[1]["usd"]) == pytest.approx(float(rows[0]["usd"]), abs=1e-6)   # answer cost only
    turns = await _rows(lab_scratch, "SELECT turn_id, reason, turn FROM tracelab.evidence_turns ORDER BY captured_at")
    assert {t["reason"] for t in turns} == {"golden"} and len(turns) == 2
    first = next(t for t in turns if t["turn_id"] == rows[0]["turn_id"])["turn"]
    assert first["golden"]["case_key"] == "21" and first["golden"]["msg_idx"] == 0 and first["cards"] == [card]
    assert first["organization_id"] == ORG and first["ended_by"] == "workflow_completed"
    spans = await _rows(lab_scratch, "SELECT count(*) AS n FROM tracelab.evidence_spans WHERE turn_id = :t",
                        t=rows[0]["turn_id"])
    assert spans[0]["n"] >= 5
    loaded = await runner.load_run(run["id"], ORG)
    job = await jobs.get(loaded["job_id"], organization_id=ORG)
    assert job["done"] == 1 and float(job["usd_reserved"]) == 0
    assert float(job["usd_spent"]) == pytest.approx(sum(float(r["usd"]) for r in rows) + 0.02, abs=1e-6)
    assert float(loaded["usd_spent"]) == pytest.approx(float(job["usd_spent"])) and loaded["summary"]["current"] is None
    s1, s2 = w.orchestrator.seen
    assert (s1["session"], s2["session"]) == (sid, sid) and s1["auth"] == BEARER and s1["replay"] is False
    assert s1["context"] == "The user is in the Hoistra orchestrator conversation." == s2["context"]
    assert all(EXPECTED not in (s["message"] + (s["context"] or "")) for s in w.orchestrator.seen)
    ((graded_case, last),) = w.graded
    assert graded_case["case_key"] == "21" and last.answer == "733 of them were reactive."
    assert last.history == [{"question": "How many work orders were raised in August 2026?",
                             "answer": "759 work orders."}]
    assert sid not in session_workspace._SESSION_STATE


async def test_the_next_steps_ask_the_next_case_then_say_done(lab_scratch, monkeypatch):
    w, run = await _started(lab_scratch, monkeypatch)
    w.script += [Msg(), Msg(), Msg()]
    assert (await _step(run))["item"]["case_key"] == "21"
    assert (await _step(run))["item"]["case_key"] == "22"
    out = await _step(run)
    assert out["started"] is False and out["done"] is True and out["run"]["status"] == "done"
    assert out["run"]["items_done"] == 2 and out["run"]["finished_at"]
    (row,) = await _rows(lab_scratch, "SELECT status, finished_at FROM tracelab.golden_runs")
    assert row["status"] == "done" and row["finished_at"] is not None


async def test_infrastructure_failures_are_retried_with_a_fresh_session_and_never_graded(lab_scratch, monkeypatch):
    w, run = await _started(lab_scratch, monkeypatch, case_keys=["22"])
    w.script += [Msg(upstream_status=503), Msg(upstream_status=503), Msg(upstream_status=503)]
    for attempt in (1, 2):
        out = await _step(run)
        assert out["item"] == {"case_key": "22", "rep": 1, "attempt": attempt}
        assert await _rows(lab_scratch, "SELECT id FROM tracelab.golden_results") == []
        loaded = await runner.load_run(run["id"], ORG)
        assert loaded["progress"]["attempts"] == {"22#1": attempt} and float(loaded["job_usd_spent"]) > 0
    assert w.orchestrator.seen[1]["session"] == runner.session_id_for(run["id"], "22", 1, 2)
    out = await _step(run)
    assert out["item"]["attempt"] == 3
    (row,) = await _rows(lab_scratch, "SELECT outcome, grade, error, graded_at FROM tracelab.golden_results")
    assert row["outcome"] == "error" and row["grade"]["attempts"] == 3 and "503" in row["error"]
    assert row["graded_at"] is None and w.graded == []
    assert (await _step(run))["done"] is True


async def test_an_item_that_never_answers_is_stored_as_an_error_after_three_attempts(lab_scratch, monkeypatch):
    w, run = await _started(lab_scratch, monkeypatch, case_keys=["22"])
    monkeypatch.setattr(runner, "ITEM_TIMEOUT_S", 0.05)
    w.script += [Msg(kind="hang"), Msg(kind="hang"), Msg(kind="hang")]
    for _ in range(3):
        await _step(run)
    (row,) = await _rows(lab_scratch, "SELECT turn_id, outcome, error, answer FROM tracelab.golden_results")
    assert row["outcome"] == "error" and "longer than" in row["error"] and row["turn_id"] is None
    assert row["answer"] is None and await _rows(lab_scratch, "SELECT turn_id FROM tracelab.evidence_turns") == []
    assert (await _step(run))["done"] is True and w.graded == []


async def test_a_chat_error_stops_the_conversation_and_the_unasked_follow_up_gets_the_final_row(lab_scratch,
                                                                                               monkeypatch):
    w, run = await _started(lab_scratch, monkeypatch, case_keys=["21"])
    w.grade_outcome = "fail"
    w.script.append(Msg(kind="error", error="GraphRecursionError: needed too many tool steps"))
    await _step(run)
    assert len(w.orchestrator.seen) == 1                       # the follow-up was never asked
    first, final = await _rows(lab_scratch, "SELECT * FROM tracelab.golden_results ORDER BY msg_idx")
    assert first["turn_id"].startswith("turn-g") and "too many tool steps" in first["error"]
    assert first["outcome"] == "pending" and first["grade"] is None
    assert (final["msg_idx"], final["turn_id"], final["answer"], final["error"]) == (1, None, None, runner.NOT_ASKED)
    assert final["question"] == "And how many of those were reactive?" and final["outcome"] == "fail"
    assert final["grade"]["outcome"] == "fail" and final["graded_at"] is not None and final["usd"] is None
    ((_, graded),) = w.graded
    assert graded.ended_by == "error" and graded.turn_id == first["turn_id"]


async def test_a_timeout_inside_the_item_is_not_its_budget_and_counts_no_attempt(lab_scratch, monkeypatch):
    w, run = await _started(lab_scratch, monkeypatch, case_keys=["22"])

    async def stalled(job_id, org):
        raise TimeoutError("the database did not answer")

    monkeypatch.setattr(runner, "_job_status", stalled)
    assert (await _step(run))["item"]["attempt"] == 1
    loaded = await runner.load_run(run["id"], ORG)
    assert not loaded["progress"].get("attempts") and float(loaded["usd_reserved"]) == 0
    assert loaded["summary"]["current"] is None and w.orchestrator.seen == []
    assert await _rows(lab_scratch, "SELECT id FROM tracelab.golden_results") == []


async def test_a_refused_request_makes_the_answer_degraded_never_wrong(lab_scratch, monkeypatch):
    w, run = await _started(lab_scratch, monkeypatch, case_keys=["22"])
    w.grade_outcome = "fail"
    w.script.append(Msg(refuse=True))
    await _step(run)
    (row,) = await _rows(lab_scratch, "SELECT outcome, refused FROM tracelab.golden_results")
    assert row["outcome"] == "degraded" and row["refused"][0]["method"] == "PATCH"


async def test_a_grader_failure_leaves_the_item_pending(lab_scratch, monkeypatch):
    w, run = await _started(lab_scratch, monkeypatch, case_keys=["22"])
    w.grader_fails = True
    w.script.append(Msg())
    await _step(run)
    (row,) = await _rows(lab_scratch, "SELECT outcome, grade, graded_at FROM tracelab.golden_results")
    assert row["outcome"] == "pending" and "grader failed" in row["grade"]["error"] and row["graded_at"] is None


async def test_one_item_per_process(lab_scratch, monkeypatch):
    w, run = await _started(lab_scratch, monkeypatch)
    gate = asyncio.Event()

    async def slow_item(*a, **kw):
        await gate.wait()

    monkeypatch.setattr(runner, "_run_item", slow_item)
    first = await runner.step(run["id"], principal=principal(), authorization=BEARER)
    assert first["started"] is True and first["run"]["in_flight"] is True
    assert (await runner.load_run(run["id"], ORG))["summary"]["current"] == {"case_key": "21", "rep": 1}
    again = await runner.step(run["id"], principal=principal(), authorization=BEARER)
    assert (again["started"], again["reason"]) == (False, "in_flight")
    task = runner._ACTIVE.pop(run["id"])
    runner._ACTIVE["another-run"] = task
    busy = await runner.step(run["id"], principal=principal(), authorization=BEARER)
    assert (busy["started"], busy["reason"]) == (False, "busy")
    gate.set()
    await task
    runner._ACTIVE.clear()


@pytest.mark.parametrize("who,token,selected,reason,status", [
    ("other", BEARER, None, "not_run_owner", 403),
    ("me", "jwt5", None, "token_expiring", 409),
    ("me", BEARER, "b-301", "building_selected", 409),
])
async def test_only_the_starter_steps_with_time_left_and_no_building(lab_scratch, monkeypatch, who, token, selected,
                                                                      reason, status):
    w, run = await _started(lab_scratch, monkeypatch)
    w.selected = selected
    stepper = principal(user=str(uuid.uuid4())) if who == "other" else principal()
    with pytest.raises(runner.RunRefused) as err:
        await _step(run, stepper, jwt(5) if token == "jwt5" else token)
    assert (err.value.reason, err.value.status_code) == (reason, status) and w.orchestrator.seen == []
    if reason == "token_expiring":
        assert 4 < err.value.detail["minutes_left"] < 5.1


async def test_only_a_running_run_steps(lab_scratch, monkeypatch):
    w, run = await _started(lab_scratch, monkeypatch)
    await jobs.finish((await runner.load_run(run["id"], ORG))["job_id"], "paused")
    with pytest.raises(runner.RunRefused) as err:
        await _step(run)
    assert (err.value.reason, err.value.detail["status"]) == ("run_not_running", "paused")
    with pytest.raises(runner.RunRefused) as err:
        await runner.step(str(uuid.uuid4()), principal=principal(), authorization=BEARER)
    assert err.value.status_code == 404


async def test_the_run_cap_stops_the_run_before_an_item(lab_scratch, monkeypatch):
    w, run = await _started(lab_scratch, monkeypatch, usd_cap=0.05)            # one item of case 21 is estimated at 0.202
    with pytest.raises(runner.RunRefused) as err:
        await _step(run)
    assert err.value.reason == "run_cap" and err.value.detail["usd_cap"] == pytest.approx(0.05)
    loaded = await runner.load_run(run["id"], ORG)
    assert loaded["job_status"] == "capped" and loaded["status"] == "capped" and w.orchestrator.seen == []
    assert loaded["summary"]["paused_reason"] == "run_cap"


async def test_the_month_cap_stops_the_run_before_an_item(lab_scratch, monkeypatch):
    w, run = await _started(lab_scratch, monkeypatch)
    monkeypatch.setenv("TRACE_LAB_GOLDEN_MONTHLY_USD", "0.05")
    with pytest.raises(runner.RunRefused) as err:
        await _step(run)
    assert err.value.reason == "monthly_cap" and err.value.detail["month_cap_usd"] == 0.05
    loaded = await runner.load_run(run["id"], ORG)
    assert loaded["job_status"] == "capped" and loaded["summary"]["paused_reason"] == "monthly_cap"
