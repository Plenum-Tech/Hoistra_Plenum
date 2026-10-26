"""Jobs on a throwaway Postgres (the lab schema). Skips without TRACE_LAB_TEST_DB_URL."""
from __future__ import annotations

import json
from decimal import Decimal

import pytest
from sqlalchemy import text

from tl_scratch import lab_scratch, requires_scratch  # noqa: F401
from trace_lab import jobs

pytestmark = requires_scratch

ORG = "00000000-0000-0000-0000-0000000000a1"
OTHER = "00000000-0000-0000-0000-0000000000b2"
ACTOR = "00000000-0000-0000-0000-0000000000c3"


async def _job(scope=f"golden_run:{ORG}", org=ORG, cap=Decimal("1.00"), kind="golden_run"):
    return await jobs.create(kind=kind, scope_key=scope, organization_id=org, params={"set_id": "s1"},
                             usd_cap=cap, usd_estimate=Decimal("0.40"), total=3, actor_id=ACTOR,
                             actor_email="a@b.c")


async def test_one_open_job_per_scope(lab_scratch):
    j = await _job()
    assert j["status"] == "queued" and j["organization_id"] == ORG and j["done"] == 0 and j["created_by"] == ACTOR
    assert j["params"] == {"set_id": "s1"}
    with pytest.raises(jobs.JobConflict):
        await _job()
    assert (await jobs.finish(j["id"], "done"))["status"] == "done"
    assert (await _job())["status"] == "queued"          # a finished job frees its scope


async def test_the_database_refuses_credentials_and_uncapped_spend(lab_scratch):
    for params, cap in (({"token": "abc"}, 1), ({}, None)):
        with pytest.raises(Exception, match="jobs_params_no_secrets|jobs_model_spend_capped"):
            async with lab_scratch.begin() as conn:
                await conn.execute(text(
                    "INSERT INTO tracelab.jobs (kind, scope_key, params, usd_cap)"
                    " VALUES ('golden_run', 'golden_run:x', CAST(:p AS jsonb), :cap)"),
                    {"p": json.dumps(params), "cap": cap})
    offboard = await jobs.create(kind="company_delete", scope_key=f"company_delete:{ORG}", organization_id=None,
                                 params={"organization_id": ORG}, usd_cap=None, actor_id=ACTOR)
    assert offboard["usd_cap"] is None and offboard["organization_id"] is None


async def test_claim_is_atomic_and_spend_stops_at_the_cap(lab_scratch):
    j = await _job()
    assert (await jobs.claim(j["id"], worker="w1"))["status"] == "running"
    assert await jobs.claim(j["id"], worker="w2") is None
    await jobs.reserve(j["id"], Decimal("0.60"))
    with pytest.raises(jobs.CapReached):
        await jobs.reserve(j["id"], Decimal("0.50"))
    async with lab_scratch.begin() as conn:
        await jobs.checkpoint(conn, j["id"], usd=Decimal("0.55"), released=Decimal("0.60"), progress_patch={"case": "C1"})
    row = await jobs.get(j["id"], organization_id=ORG)
    assert row["done"] == 1 and row["usd_spent"] == Decimal("0.55") and row["usd_reserved"] == Decimal("0")
    assert row["progress"] == {"case": "C1"}
    assert await jobs.add_spend(j["id"], Decimal("0.40")) is True      # 0.95 < 1.00
    assert await jobs.add_spend(j["id"], Decimal("0.10")) is False     # 1.05: the cap is reached
    assert await jobs.get(j["id"], organization_id=OTHER) is None
    assert await jobs.get(j["id"], organization_id=None, platform=True) is not None
    await jobs.finish(j["id"], "capped")
    with pytest.raises(jobs.JobNotRunning):
        await jobs.reserve(j["id"], Decimal("0.01"))


async def test_release_gives_back_a_failed_call(lab_scratch):
    j = await _job()
    await jobs.claim(j["id"])
    await jobs.reserve(j["id"], Decimal("0.30"))
    await jobs.release(j["id"], Decimal("0.30"))
    assert (await jobs.get(j["id"], organization_id=ORG))["usd_reserved"] == Decimal("0")


async def test_a_stale_running_job_is_interrupted_and_blocks_its_scope_until_resumed(lab_scratch):
    j = await _job()
    await jobs.claim(j["id"], worker="w1")
    fresh = await _job(scope=f"golden_run:{OTHER}", org=OTHER)
    await jobs.claim(fresh["id"], worker="w2")
    async with lab_scratch.begin() as conn:
        await conn.execute(text("UPDATE tracelab.jobs SET heartbeat_at = now() - interval '10 minutes'"
                                " WHERE id = CAST(:id AS uuid)"), {"id": j["id"]})
    assert await jobs.mark_interrupted() == [j["id"]]                # the fresh one keeps running
    row = await jobs.get(j["id"], organization_id=ORG)
    assert row["status"] == "interrupted" and row["error"] == jobs.INTERRUPTED_TEXT
    assert (await jobs.get(fresh["id"], organization_id=OTHER))["status"] == "running"
    with pytest.raises(jobs.JobConflict):
        await _job()
    resumed = await jobs.resume(j["id"], usd_cap=Decimal("2.00"))
    assert resumed["status"] == "queued" and resumed["usd_cap"] == Decimal("2.00") and resumed["error"] is None
    await jobs.claim(j["id"])
    assert await jobs.request_stop(j["id"]) is True
    assert await jobs.heartbeat(j["id"], progress_patch={"step": 2}) is True
    await jobs.finish(j["id"], "paused")
    assert (await jobs.discard(j["id"]))["status"] == "failed"
    assert [r["id"] for r in await jobs.list_for(organization_id=ORG)] == [j["id"]]
    assert {r["id"] for r in await jobs.list_for(organization_id=None, platform=True)} == {j["id"], fresh["id"]}


async def test_heartbeat_says_stop_once_the_job_is_not_running(lab_scratch):
    j = await _job()
    assert await jobs.heartbeat(j["id"]) is True                     # queued, not running
    await jobs.claim(j["id"])
    assert await jobs.heartbeat(j["id"], progress_patch={"case": "C2"}) is False
    assert (await jobs.get(j["id"], organization_id=ORG))["progress"] == {"case": "C2"}


async def test_list_for_filters_by_kind_and_clamps_the_limit(lab_scratch):
    await _job()
    await _job(scope="judge_run:J1:dev", org=None, kind="judge_run")
    assert [r["kind"] for r in await jobs.list_for(organization_id=None)] == ["judge_run"]
    assert await jobs.list_for(organization_id=ORG, kind="judge_run") == []
    assert len(await jobs.list_for(organization_id=None, platform=True, limit=0)) == 1
