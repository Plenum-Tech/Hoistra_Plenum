"""Evaluation spend for one month (Phase 3; the senior's decision 5): what Trace Lab spent on model calls, shown on
Platform cost as its own line (approved attachment A3, EvalSpendLine) and never added into that page's totals.

The month's TOTAL and each company's total are the jobs' own spend (tracelab.jobs.usd_spent, the figure the caps use,
by the job's created_at): it is complete, so it includes retried attempts and replaced gradings. The BREAKDOWN by kind
is read from Trace Lab's own golden_results, which keeps only stored items and each rep's latest grade:
* golden_answers  the chat's own model spend while answering golden cases (golden_results.usd, by created_at);
* golden_grading  the grader's calls (golden_results.grade.usd, by graded_at, so a re-grade counts in its month).
other_usd is what the total holds that no stored result accounts for (retries, replaced gradings, judge runs and
monitoring batches; never negative).
Later phases add their kinds here (judges, monitoring). The months are UTC calendar months.

Phase 5 adds the AI proposal caps below (per run and per month); proposal jobs are not in JOB_KINDS, so this report's
totals do not change until a later task decides how the Platform cost line shows them.

GET /api/trace-lab/spend?month=YYYY-MM is for the platform team only (any superadmin; the page is platform-wide, so
a company the request names is ignored). Reads only, on db.lab_engine().
"""
from __future__ import annotations

import math
import os
import re
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text

from src.services.principal import Principal

from . import auth, db

KINDS = ("golden_answers", "golden_grading")
#: The job kinds whose own spend is the month's total: golden runs and gradings, judge runs and monitoring batches.
#: Judge and monitoring spend has no breakdown kind of its own yet, so it reads in other_usd.
JOB_KINDS = ("golden_run", "golden_grade", "judge_run", "monitor_batch")
UNMEASURED = (
    "The contract analyst's and the embeddings' spend is not measured.",
    "Attempts retried after a service failure, and gradings replaced by Grade again, are in the totals but in no "
    "kind below, so the kinds can add up to less than the total.",
)
_MONTH = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")

_JOBS_SQL = """
    SELECT organization_id::text AS org, coalesce(sum(usd_spent), 0) AS usd
      FROM tracelab.jobs
     WHERE kind = ANY(:kinds) AND created_at >= :start AND created_at < :end
     GROUP BY organization_id"""

_KIND_SQL = """
    SELECT organization_id::text AS org, 'golden_answers' AS kind, coalesce(sum(usd), 0) AS usd
      FROM tracelab.golden_results
     WHERE created_at >= :start AND created_at < :end AND usd IS NOT NULL
     GROUP BY organization_id
    UNION ALL
    SELECT organization_id::text AS org, 'golden_grading' AS kind,
           coalesce(sum(CASE WHEN jsonb_typeof(grade -> 'usd') = 'number' THEN CAST(grade ->> 'usd' AS numeric)
                             ELSE 0 END), 0) AS usd
      FROM tracelab.golden_results
     WHERE graded_at >= :start AND graded_at < :end AND grade IS NOT NULL
     GROUP BY organization_id"""


def month_bounds(month: str | None = None, *, now: datetime | None = None) -> tuple[str, datetime, datetime]:
    """("YYYY-MM", start, end) of a UTC calendar month; the current one when month is None. ValueError otherwise."""
    if month is None or not str(month).strip():
        t = now or datetime.now(timezone.utc)
        y, m = t.year, t.month
    else:
        found = _MONTH.match(str(month).strip())
        if not found:
            raise ValueError("month must be YYYY-MM")
        y, m = int(found.group(1)), int(found.group(2))
    start = datetime(y, m, 1, tzinfo=timezone.utc)
    end = datetime(y + (m == 12), 1 if m == 12 else m + 1, 1, tzinfo=timezone.utc)
    return f"{y:04d}-{m:02d}", start, end


async def month_spend(month: str | None = None) -> dict[str, Any]:
    """{month, total_usd, other_usd, by_company: [{organization_id, usd, by_kind}], by_kind: {kind: usd}, unmeasured}."""
    label, start, end = month_bounds(month)
    window = {"start": start, "end": end}
    async with db.lab_engine().connect() as conn:
        jobs = (await conn.execute(text(_JOBS_SQL), {**window, "kinds": list(JOB_KINDS)})).mappings().all()
        rows = (await conn.execute(text(_KIND_SQL), window)).mappings().all()
    by_kind = {k: 0.0 for k in KINDS}
    spent: dict[str | None, float] = {}
    kinds_of: dict[str | None, dict[str, float]] = {}
    for j in jobs:
        spent[j["org"]] = spent.get(j["org"], 0.0) + float(j["usd"] or 0)
    for r in rows:
        usd = float(r["usd"] or 0)
        by_kind[r["kind"]] = by_kind.get(r["kind"], 0.0) + usd
        kinds_of.setdefault(r["org"], {k: 0.0 for k in KINDS})[r["kind"]] += usd
    total = sum(spent.values())
    by_company = sorted(
        ({"organization_id": org, "usd": round(spent.get(org, 0.0), 6),
          "by_kind": {k: round(v, 6) for k, v in kinds_of.get(org, {k: 0.0 for k in KINDS}).items()}}
         for org in set(spent) | set(kinds_of)),
        key=lambda c: (-c["usd"], str(c["organization_id"])))
    return {"month": label, "total_usd": round(total, 6),
            "other_usd": round(max(0.0, total - sum(by_kind.values())), 6), "by_company": by_company,
            "by_kind": {k: round(v, 6) for k, v in by_kind.items()}, "unmeasured": list(UNMEASURED)}


# ── GET /api/trace-lab/spend ─────────────────────────────────────────────────────────────────

router = APIRouter(tags=["Trace Lab spend"])


async def _platform(principal: Principal = auth.caller) -> Principal:
    """Any superadmin; checked before readiness so nobody else learns anything about Trace Lab's state."""
    if principal.role != auth.SUPERADMIN:
        raise db.http_error(403, "platform_only", auth.PLATFORM_ONLY)
    return principal


async def _ready_platform(who: Principal = Depends(_platform), _ready: Any = Depends(db.require_ready)) -> Principal:
    return who


@router.get("/spend")
async def get_spend(month: str | None = Query(None, max_length=7),
                    _who: Principal = Depends(_ready_platform)) -> dict[str, Any]:
    try:
        return {"ok": True, **(await month_spend(month))}
    except ValueError:
        raise db.http_error(400, "bad_month", "Name the month as YYYY-MM, for example 2026-10.") from None


# ── AI proposals: grouping, depth scans and cell proposals (Phase 5) ─────────────────────────────────────────

#: Job kinds whose usd_spent counts toward the monthly proposal cap. Golden spend (JOB_KINDS) is a separate budget.
PROPOSAL_JOB_KINDS = ("proposal_grouping", "proposal_depth_scan", "proposal_cells")
PROPOSAL_RUN_MAX_DEFAULT = 25.0
PROPOSALS_MONTHLY_DEFAULT = 100.0


def _env_usd(name: str, default: float) -> float:
    """A positive, finite dollar amount from the environment; the default when unset, unreadable, zero or negative."""
    try:
        value = float((os.getenv(name) or "").strip() or default)
    except ValueError:
        return default
    return value if math.isfinite(value) and value > 0 else default


def proposal_run_max() -> float:
    """TRACE_LAB_PROPOSAL_RUN_MAX_USD (default 25.00): the largest cap one proposal run may ask for."""
    return _env_usd("TRACE_LAB_PROPOSAL_RUN_MAX_USD", PROPOSAL_RUN_MAX_DEFAULT)


def proposals_month_cap() -> float:
    """TRACE_LAB_PROPOSALS_MONTHLY_USD (default 100.00): platform-wide proposal spend per UTC month."""
    return _env_usd("TRACE_LAB_PROPOSALS_MONTHLY_USD", PROPOSALS_MONTHLY_DEFAULT)


_PROPOSALS_MONTH_SQL = """
    SELECT coalesce(sum(usd_spent), 0) FROM tracelab.jobs
     WHERE kind = ANY(CAST(:kinds AS text[]))
       AND created_at >= date_trunc('month', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'"""


async def proposals_month_spent(conn=None) -> float:
    """Sum of tracelab.jobs.usd_spent over the three proposal job kinds created this UTC month. Reads through conn
    when the caller has one open (a reservation check inside its own transaction), else on db.lab_engine()."""
    params = {"kinds": list(PROPOSAL_JOB_KINDS)}
    if conn is not None:
        return float((await conn.execute(text(_PROPOSALS_MONTH_SQL), params)).scalar() or 0)
    async with db.lab_engine().connect() as c:
        return float((await c.execute(text(_PROPOSALS_MONTH_SQL), params)).scalar() or 0)
