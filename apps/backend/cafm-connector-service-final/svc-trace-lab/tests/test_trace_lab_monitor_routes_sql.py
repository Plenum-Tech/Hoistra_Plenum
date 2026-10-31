"""The Watch routes on a scratch Postgres (tracelab_scratch), through the real monitor and eval-case stores: the route
layer and the stores agree on every name and argument, a company admin reads only their own company's batches and cases
(and gets the house 404 for another company's), the platform view reads all, an eval case goes from a golden case to
retired over HTTP, and a release check with nothing to run is refused before anything is started. No model, no golden
run (the runner is a tripwire). Skips without TRACE_LAB_TEST_DB_URL."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

import httpx
import pytest
from fastapi import FastAPI

import trace_lab_m4_fixtures as fx
from golden_seed import seed_run, seed_set
from src.services.principal import Principal, current_principal
from tl_scratch import lab_scratch, requires_scratch  # noqa: F401
from trace_lab import db, eval_cases, jobs, monitor, routes_monitor

pytestmark = requires_scratch

UTC = timezone.utc
ORG_A = fx.ORG_A
ORG_B = fx.ORG_B
B = "/api/trace-lab"
EXPECTED = {"checks": [{"check": "must_contain", "text": "786"}], "golden_expected": False}


def _p(role: str, org: str | None) -> Principal:
    return Principal(user_id=uuid.UUID(fx.OWNER_ID), email=f"{role}@example.com",
                     organization_id=uuid.UUID(org) if org else None, role=role, can_ingest=True, building_ids=None)


def _client(principal: Principal) -> httpx.AsyncClient:
    """The router on the real stores, in this test's own event loop. The schema check is the real one (the scratch
    fixture left it ready); only the caller is replaced."""
    app = FastAPI()
    app.include_router(routes_monitor.router, prefix=B)
    app.dependency_overrides[current_principal] = lambda: principal
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://trace-lab")


@pytest.fixture(autouse=True)
def no_golden_run(monkeypatch):
    """The golden runner is never started here: a route that reaches it fails the test."""
    async def tripwire(**kw):
        raise AssertionError("a golden run was started")

    monkeypatch.setattr(eval_cases, "START_GOLDEN_RUN", tripwire)
    routes_monitor.SPENDING.reset()
    yield
    routes_monitor.SPENDING.reset()


async def _batch(org: str | None) -> str:
    """A queued monitoring batch and its job, as start_batch records them."""
    job = await jobs.create(kind="monitor_batch", scope_key=f"monitor_batch:{org or '*'}", organization_id=org, params={},
                            usd_cap=Decimal("5"), usd_estimate=Decimal("1"), total=10, actor_id=fx.OWNER_ID,
                            actor_email="owner@example.com")
    row = {"id": str(uuid.uuid4()), "organization_id": org, "period_label": "2026-W41",
           "window_start": datetime(2026, 10, 5, tzinfo=UTC), "window_end": datetime(2026, 10, 12, tzinfo=UTC),
           "sample_rate": 0.25, "seed": 7, "risk_groups": ["failed"], "judges": [], "checks": monitor.live_checks(),
           "status": "queued", "job_id": str(job["id"]), "usd_cap": Decimal("5"), "started_by": fx.OWNER_ID,
           "started_by_email": "owner@example.com"}
    await monitor._insert_batch(row)
    return row["id"]


async def test_a_company_reads_its_own_batches_and_the_platform_reads_them_all(lab_scratch):
    a, b, platform = await _batch(ORG_A), await _batch(ORG_B), await _batch(None)

    async with _client(_p("admin", ORG_A)) as me:
        r = await me.get(f"{B}/monitor/batches")
        assert r.status_code == 200 and r.json()["ok"] is True
        assert [x["id"] for x in r.json()["batches"]] == [a] and r.json()["due"]["is_due"] is True
        assert (await me.get(f"{B}/monitor/batches/{a}")).json()["batch"]["id"] == a
        for other in (b, platform, str(uuid.uuid4()), "not-an-id"):               # another scope's, or none, or garbage
            r = await me.get(f"{B}/monitor/batches/{other}")
            assert r.status_code == 404 and r.json()["detail"]["reason"] == "not_found", other
        r = await me.get(f"{B}/monitor/batches", params={"organization_id": ORG_B})
        assert r.status_code == 403 and r.json()["detail"]["reason"] == "bad_company"
        assert (await me.get(f"{B}/monitor/code-checks")).json()["checks"]
        assert len((await me.get(f"{B}/monitor/automation", params={"weeks": 4})).json()["weeks"]) == 4
        r = await me.get(f"{B}/monitor/chart", params={"mode_id": str(uuid.uuid4())})
        assert r.status_code == 404 and r.json()["detail"]["reason"] == "not_found"

    async with _client(_p("superadmin", None)) as boss:
        listed = (await boss.get(f"{B}/monitor/batches")).json()["batches"]
        assert {x["id"] for x in listed} == {a, b, platform}
        assert (await boss.get(f"{B}/monitor/batches/{b}")).json()["batch"]["id"] == b
        acting = (await boss.get(f"{B}/monitor/batches", params={"organization_id": ORG_B})).json()["batches"]
        assert [x["id"] for x in acting] == [b]
        assert (await boss.get(f"{B}/monitor/batches", params={"limit": 1})).json()["batches"].__len__() == 1


async def test_nobody_but_the_platform_team_starts_monitoring_and_nothing_starts_without_them(lab_scratch):
    async with _client(_p("admin", ORG_A)) as me:
        for method, path in (("post", "/monitor/batches"), ("get", "/monitor/plan")):
            r = await getattr(me, method)(B + path)
            assert r.status_code == 403 and r.json()["detail"]["reason"] == "platform_only"
    async with _client(_p("superadmin", None)) as boss:
        r = await boss.post(f"{B}/monitor/batches/{uuid.uuid4()}/resume", json={})
        assert r.status_code == 404 and r.json()["detail"]["reason"] == "not_found"          # the store's own refusal
        r = await boss.post(f"{B}/monitor/batches/{uuid.uuid4()}/review-batch", json={"mode_id": str(uuid.uuid4())})
        assert r.status_code == 404 and r.json()["detail"]["reason"] == "not_found"
        r = await boss.post(f"{B}/monitor/batches", json={"sample_rate": 0.25, "since": "2026-10-12T00:00:00Z",
                                                           "until": "2026-10-05T00:00:00Z"})
        assert r.status_code in (422, 409) and r.json()["detail"]["reason"] and r.json()["detail"]["error"]   # a bad window
        assert await jobs.list_for(organization_id=None, kind="monitor_batch") == []         # nothing was created


async def test_an_eval_case_goes_from_a_golden_case_to_retired_over_http(lab_scratch):
    async with lab_scratch.begin() as conn:
        seeded = await seed_set(conn, ORG_A, cases=(("1", ()),))
    golden_case = seeded["cases"]["1"]

    async with _client(_p("admin", ORG_A)) as me, _client(_p("admin", ORG_B)) as other:
        r = await me.post(f"{B}/eval-cases", json={"source": {"type": "golden_case", "golden_case_id": golden_case},
                                                    "expected": EXPECTED})
        assert r.status_code == 201, r.text
        case = r.json()["case"]
        assert case["organization_id"] == ORG_A and case["case_key"].startswith("e-") and case["version"] == 1
        assert case["status"] == "draft" and case["kind"] == "unclassified"

        assert [c["id"] for c in (await me.get(f"{B}/eval-cases")).json()["cases"]] == [case["id"]]
        assert (await me.get(f"{B}/eval-cases", params={"status": "active"})).json()["cases"] == []
        assert (await other.get(f"{B}/eval-cases")).json()["cases"] == []                       # another company: none
        r = await other.get(f"{B}/eval-cases/{case['id']}")
        assert r.status_code == 404 and r.json()["detail"]["reason"] == "not_found"
        r = await me.get(f"{B}/eval-cases/{case['id']}")
        assert r.status_code == 200 and r.json()["case"]["id"] == case["id"] and r.json()["runs"] == []
        assert r.json()["twin"]["expected_answer"]

        r = await me.patch(f"{B}/eval-cases/{case['id']}", json={"base": case["rev"] + 5,
                                                                  "input": {"question": "A new question?"}})
        assert r.status_code == 409 and r.json()["detail"]["reason"] == "stale_base"
        r = await me.patch(f"{B}/eval-cases/{case['id']}", json={"base": case["rev"],
                                                                  "input": {"question": "A new question?"}})
        assert r.status_code == 200 and r.json()["case"]["version"] == 2 and r.json()["case"]["input"]["question"] == "A new question?"
        new = r.json()["case"]
        r = await me.patch(f"{B}/eval-cases/{case['id']}", json={"base": case["rev"]})
        assert r.status_code == 409 and r.json()["detail"]["reason"] == "case_retired"          # version 1 was replaced

        r = await me.post(f"{B}/eval-cases/{new['id']}/retire", json={"base": new["rev"] + 1, "reason": "No longer a failure."})
        assert r.status_code == 409 and r.json()["detail"]["reason"] == "stale_base"
        r = await me.post(f"{B}/eval-cases/{new['id']}/retire", json={"base": new["rev"], "reason": "No longer a failure."})
        assert r.status_code == 200 and r.json()["case"]["status"] == "retired"
        assert r.json()["case"]["retired_reason"] == "No longer a failure."
        assert (await me.get(f"{B}/eval-cases", params={"status": "retired"})).json()["cases"][0]["id"] == new["id"]


async def test_a_release_check_with_nothing_to_run_is_refused_before_anything_starts(lab_scratch):
    async with _client(_p("admin", ORG_A)) as me:
        r = await me.post(f"{B}/release-checks", json={}, headers={"Authorization": "Bearer t"})
        assert r.status_code == 409 and r.json()["detail"]["reason"] == "no_active_cases"        # the tripwire never fired
        for path in ("/eval-cases/{}/baseline", "/eval-cases/{}/repeat"):
            r = await me.post(B + path.format(uuid.uuid4()), json={})
            assert r.status_code == 404 and r.json()["detail"]["reason"] == "not_found"
        r = await me.post(f"{B}/eval-runs/{uuid.uuid4()}/grade", json={})
        assert r.status_code == 404 and r.json()["detail"]["reason"] == "not_found"
        assert (await me.get(f"{B}/release-checks")).json() == {"ok": True, "checks": []}
        for path in (f"/release-checks/{uuid.uuid4()}", f"/eval-runs/{uuid.uuid4()}", "/release-checks/not-an-id"):
            r = await me.get(B + path)
            assert r.status_code == 404 and r.json()["detail"]["reason"] == "not_found", path
        r = await me.post(f"{B}/release-checks", json={"k": eval_cases.RELEASE_MAX_K + 1})
        assert r.status_code == 422
    assert await jobs.list_for(organization_id=ORG_A) == []                                     # no job, run or grade started


async def test_the_golden_run_a_baseline_starts_is_the_callers_own(lab_scratch, monkeypatch):
    """The route hands the real store the company, the caller and the bearer; the store passes them on to the runner."""
    async with lab_scratch.begin() as conn:
        seeded = await seed_set(conn, ORG_A, cases=(("1", ()),))
    async with _client(_p("admin", ORG_A)) as me:
        r = await me.post(f"{B}/eval-cases", json={"source": {"type": "golden_case", "golden_case_id": seeded["cases"]["1"]},
                                                    "expected": EXPECTED})
        case = r.json()["case"]
        seen = {}

        async def start(*, ctx, set_id, case_keys, k, purpose, usd_cap, note):
            seen.update(ctx=ctx, k=k, purpose=purpose, usd_cap=usd_cap, keys=case_keys)
            async with lab_scratch.begin() as conn:                   # the case's run row points at a real golden run
                run_id = await seed_run(conn, ORG_A, set_id, k=k, status="running")
            return {"id": run_id, "label": "run-x"}

        monkeypatch.setattr(eval_cases, "START_GOLDEN_RUN", start)
        r = await me.post(f"{B}/eval-cases/{case['id']}/baseline", json={"usd_cap": 2.5},
                          headers={"Authorization": "Bearer caller-token"})
    assert r.status_code == 202, r.text
    assert r.json()["golden_run"]["label"] == "run-x"
    ctx = seen["ctx"]
    assert (ctx.org, ctx.authorization, ctx.principal.email) == (ORG_A, "Bearer caller-token", "admin@example.com")
    assert (seen["k"], seen["purpose"], seen["usd_cap"]) == (eval_cases.BASELINE_K, "baseline", 2.5)
    assert len(seen["keys"]) == 1 and seen["keys"][0].startswith(case["case_key"])
    assert db.STATE is not None and db.STATE.status == "ready"
