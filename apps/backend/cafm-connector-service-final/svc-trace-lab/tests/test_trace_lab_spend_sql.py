"""month_spend on a throwaway Postgres (tracelab_scratch). The month's TOTAL and each company's total come from the
jobs' own usd_spent (complete: retried attempts and replaced gradings included); the per-kind breakdown comes from
golden_results (answers counted in the month they were asked, grading in the month it was graded). Other months left
out. Skips without TRACE_LAB_TEST_DB_URL."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from golden_seed import seed_result, seed_run, seed_set
from tl_scratch import lab_scratch, requires_scratch  # noqa: F401
from trace_lab import spend

pytestmark = requires_scratch

A, B = str(uuid.uuid4()), str(uuid.uuid4())
OCT = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)
SEP = datetime(2026, 9, 30, 23, 59, tzinfo=timezone.utc)
NOV = datetime(2026, 11, 1, 0, 0, tzinfo=timezone.utc)


async def seed_job(conn, org, usd, created_at, *, kind="golden_run", status="done"):
    await conn.execute(text(
        "INSERT INTO tracelab.jobs (kind, status, organization_id, scope_key, usd_cap, usd_spent, created_at) VALUES "
        "(:kind, :st, CAST(:org AS uuid), :scope, 100, :usd, :at)"),
        {"kind": kind, "st": status, "org": org, "scope": f"{kind}:{org}:{uuid.uuid4().hex[:8]}", "usd": usd,
         "at": created_at})


async def test_month_spend_by_company_and_kind(lab_scratch):
    async with lab_scratch.begin() as conn:
        sa = await seed_set(conn, A, cases=(("1", ()), ("2", ())))
        sb = await seed_set(conn, B)
        ra, rb = await seed_run(conn, A, sa["set_id"], k=2), await seed_run(conn, B, sb["set_id"])
        c1, c2 = sa["cases"]["1"], sa["cases"]["2"]
        await seed_result(conn, A, ra, c1, "1", usd=0.10, grade={"usd": 0.02}, created_at=OCT, graded_at=OCT)
        await seed_result(conn, A, ra, c2, "2", usd=0.30, grade={"usd": 0.03}, created_at=SEP, graded_at=OCT)   # regraded
        await seed_result(conn, A, ra, c1, "1", rep=2, usd=None, grade=None, created_at=OCT)                  # in flight
        await seed_result(conn, A, ra, c2, "2", rep=2, usd=0.50, grade={"usd": "not a number"}, created_at=NOV,
                          graded_at=NOV)
        await seed_result(conn, B, rb, sb["cases"]["1"], "1", usd=0.05, grade={"usd": 0.01}, created_at=OCT,
                          graded_at=OCT)
        # The jobs' own spend: A's October run also paid for a retried attempt and a replaced grading (0.05 that no
        # stored result carries); B's run matches its results; September and November are other months.
        await seed_job(conn, A, 0.20, OCT)
        await seed_job(conn, B, 0.06, OCT, status="capped")
        await seed_job(conn, A, 0.40, SEP)
        await seed_job(conn, A, 0.55, NOV)
    out = await spend.month_spend("2026-10")
    assert out["month"] == "2026-10" and out["by_kind"] == {"golden_answers": 0.15, "golden_grading": 0.06}
    assert out["total_usd"] == pytest.approx(0.26)
    assert out["other_usd"] == pytest.approx(0.05)
    assert out["by_company"] == [
        {"organization_id": A, "usd": 0.2, "by_kind": {"golden_answers": 0.1, "golden_grading": 0.05}},
        {"organization_id": B, "usd": 0.06, "by_kind": {"golden_answers": 0.05, "golden_grading": 0.01}}]
    sep = await spend.month_spend("2026-09")
    assert (sep["total_usd"], sep["by_kind"]["golden_answers"]) == (0.4, 0.3)
    nov = await spend.month_spend("2026-11")
    assert nov["total_usd"] == 0.55 and nov["by_kind"] == {"golden_answers": 0.5, "golden_grading": 0.0}
    empty = await spend.month_spend("2025-01")
    assert (empty["total_usd"], empty["by_company"], empty["unmeasured"]) == (0.0, [], list(spend.UNMEASURED))
    assert empty["other_usd"] == 0.0


async def test_a_retried_attempt_counts_in_the_total_but_not_in_golden_results(lab_scratch):
    """The run's job paid 0.25 in all; the one stored answer carries 0.10. The total is the job's 0.25."""
    async with lab_scratch.begin() as conn:
        s = await seed_set(conn, A)
        run = await seed_run(conn, A, s["set_id"])
        await seed_result(conn, A, run, s["cases"]["1"], "1", usd=0.10, grade=None, created_at=OCT)
        await seed_job(conn, A, 0.25, OCT)
    out = await spend.month_spend("2026-10")
    assert out["total_usd"] == 0.25
    assert out["by_kind"] == {"golden_answers": 0.1, "golden_grading": 0.0}
    assert out["by_company"] == [{"organization_id": A, "usd": 0.25,
                                  "by_kind": {"golden_answers": 0.1, "golden_grading": 0.0}}]
    assert out["other_usd"] == pytest.approx(0.15)


async def test_other_kinds_of_job_and_other_companies_jobs_stay_out(lab_scratch):
    async with lab_scratch.begin() as conn:
        await seed_job(conn, A, 0.07, OCT, kind="golden_grade")        # a re-grade job: counted, it paid the grader
        await seed_job(conn, A, 0.00, OCT, kind="company_delete")      # not a model-spending job
    out = await spend.month_spend("2026-10")
    assert out["total_usd"] == 0.07
    assert [c["organization_id"] for c in out["by_company"]] == [A]
