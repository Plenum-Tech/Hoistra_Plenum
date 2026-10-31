"""Starting a golden run on the scratch Trace Lab schema: every refusal comes before anything is written; a
start records the job (one open run per company) and the run with its like-for-like params, claims it and
asks nothing until the page steps it. Skips without TRACE_LAB_TEST_DB_URL."""
from __future__ import annotations

import json
from decimal import Decimal

import pytest
from sqlalchemy import text

from golden_runner_env import BEARER, ORG, SET_ID, body, install_seeded, principal
from tl_scratch import lab_scratch, requires_scratch  # noqa: F401
from trace_lab import jobs, runner

pytestmark = requires_scratch


async def _count(engine, table: str) -> int:
    async with engine.connect() as conn:
        return (await conn.execute(text(f"SELECT count(*) FROM tracelab.{table}"))).scalar_one()


async def _start(**over):
    return await runner.start_run(org=ORG, principal=principal(), authorization=BEARER, body=body(**over))


async def test_a_start_records_the_job_and_the_run_and_asks_nothing(lab_scratch, monkeypatch):
    w = await install_seeded(lab_scratch, monkeypatch)
    out = await _start(k=2, note="after the date fix")
    run = out["run"]
    assert run["status"] == "running" and run["k"] == 2 and run["cases"] == 2 and run["items_total"] == 4
    assert run["items_done"] == 0 and run["usd_cap"] == pytest.approx(out["estimate"]["usd_cap_default"])
    assert run["note"] == "after the date fix" and run["caveats"] == list(runner.FIDELITY_CAVEATS)
    async with lab_scratch.connect() as conn:
        job = (await conn.execute(text("SELECT * FROM tracelab.jobs"))).mappings().one()
        row = (await conn.execute(text("SELECT * FROM tracelab.golden_runs"))).mappings().one()
    assert (job["kind"], job["status"], job["scope_key"], job["total"]) == ("golden_run", "running",
                                                                             f"golden_run:{ORG}", 4)
    assert set(job["params"]) == {"run_id", "set_id", "k", "purpose"} and job["params"]["run_id"] == run["id"]
    assert (str(row["set_id"]), row["status"], row["purpose"], row["k"]) == (SET_ID, "running", "adhoc", 2)
    assert row["label"].startswith("run-") and row["started_by"] == str(principal().user_id)
    p = row["params"]
    assert p["case_keys"] == ["21", "22"] and p["acting_org"] == ORG and p["grader_model"] == "claude-sonnet-5"
    assert {"chat_model", "flags", "git_sha", "overlays_sha", "context_default_sha", "set_hash"} <= set(p)
    assert p["context_default"].startswith("The user is in the Hoistra")
    assert "Bearer" not in json.dumps(p) and "Bearer" not in json.dumps(job["params"])
    assert await _count(lab_scratch, "golden_results") == 0 and w.orchestrator.seen == []


@pytest.mark.parametrize("change,reason", [
    ({"problems": ["Activity logging is on."]}, "not_isolated"),
    ({"selected": "b-301"}, "building_selected"),
    ({"body": {"purpose": "check"}}, "bad_purpose"),
    ({"body": {"k": 21}}, "too_many_reps"),
    ({"body": {"usd_cap": 26}}, "cap_too_high"),
    ({"env": {"TRACE_LAB_GOLDEN_RUN_MAX_USD": "0.10"}}, "estimate_over_max"),
    ({"env": {"TRACE_LAB_GOLDEN_MONTHLY_USD": "0.10"}}, "monthly_cap"),
    ({"env": {"TRACE_LAB_JUDGE_MODEL": "no-such-model"}}, "grader_unpriced"),
])
async def test_every_refusal_comes_before_anything_is_written(lab_scratch, monkeypatch, change, reason):
    w = await install_seeded(lab_scratch, monkeypatch)
    w.problems = change.get("problems", [])
    w.selected = change.get("selected")
    for name, value in change.get("env", {}).items():
        monkeypatch.setenv(name, value)
    with pytest.raises(runner.RunRefused) as err:
        await _start(**change.get("body", {}))
    assert err.value.reason == reason
    assert await _count(lab_scratch, "jobs") == 0 and await _count(lab_scratch, "golden_runs") == 0


async def test_one_open_run_per_company(lab_scratch, monkeypatch):
    await install_seeded(lab_scratch, monkeypatch)
    first = (await _start())["run"]
    with pytest.raises(runner.RunRefused) as err:
        await _start()
    assert (err.value.reason, err.value.status_code) == ("run_in_progress", 409)
    loaded = await runner.load_run(first["id"], ORG)
    await jobs.finish(loaded["job_id"], "paused")                  # a paused run still holds the company's slot
    with pytest.raises(runner.RunRefused):
        await _start()
    await jobs.finish(loaded["job_id"], "cancelled")
    assert (await _start())["run"]["status"] == "running"
    assert await _count(lab_scratch, "golden_runs") == 2


async def test_load_run_is_scoped_to_the_company(lab_scratch, monkeypatch):
    await install_seeded(lab_scratch, monkeypatch)
    run = (await _start())["run"]
    assert (await runner.load_run(run["id"], ORG))["job_status"] == "running"
    assert await runner.load_run(run["id"], "00000000-0000-0000-0000-0000000000b2") is None
    assert await runner.load_run("not-a-uuid", ORG) is None


async def test_the_month_spend_counts_golden_jobs_only(lab_scratch, monkeypatch):
    await install_seeded(lab_scratch, monkeypatch)
    run = (await _start())["run"]
    loaded = await runner.load_run(run["id"], ORG)
    assert await jobs.add_spend(loaded["job_id"], Decimal("0.25"))
    other = await jobs.create(kind="judge_run", scope_key="judge_run:x", organization_id=ORG, params={},
                              usd_cap=Decimal("1"), actor_id=None)
    await jobs.add_spend(other["id"], Decimal("0.50"))
    assert await runner.month_spent(ORG) == pytest.approx(0.25)
