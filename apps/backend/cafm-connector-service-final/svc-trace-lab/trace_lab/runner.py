"""Golden runs (G0 addendum 2.5): every case of a company's golden set re-asked through the real chat in
this process, captured, graded and stored in Trace Lab's own database.

The browser steps the run (design D9): bearer tokens last 30 minutes and Trace Lab cannot mint one, so
each POST /golden-runs/{id}/step starts ONE item - a (case, rep) with its follow-ups, in one session - in
the background with the page's own token and returns at once; the page polls and steps again. One item
runs per process. An infrastructure failure (a timeout, an upstream 401/429/502/503/504, a service that
cannot be reached, an infrastructure-shaped error) is never graded: the item is tried again on a later step
with a fresh session, and the third failed attempt is stored with outcome 'error'. A request the proxy
refused makes the answer 'degraded', never 'wrong'.

Storage, schema tracelab (migration 0002): golden_runs (one row per run; its status mirrors the run's
jobs row), golden_results (one row per MESSAGE: msg_idx 0 is the question, 1.. the follow-ups; every row
of an item carries the item's outcome and grade, and the grading cost is on the item's last row; one row
per (case, rep) is the msg_idx = 0 row), evidence_turns and evidence_spans (each message's captured turn
and span tree, reason 'golden'). Golden answers are never written to the application database.

This module never stores a bearer token: jobs.params carries none, and the token a step brings lives only
in the background task that step started.
"""
from __future__ import annotations

# The whole module's imports. Phase 3 Tasks 8 and 9 append sections to the end of this file and use the
# ones this part does not.
import asyncio
import base64
import dataclasses
import hashlib
import json
import os
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import httpx
import structlog
from fastapi import HTTPException
from sqlalchemy import text

from . import capture, db, jobs, orchestrator_host

log = structlog.get_logger(__name__)

HARD_MAX_K = 20
PURPOSES = ("adhoc", "baseline", "release_check", "repeat")
MAX_ATTEMPTS = 3
MAX_QUESTION_CHARS = 2000
MAX_FOLLOWUPS = 5
DEFAULT_ANSWER_USD = 0.09
#: What one grading call is priced at for estimates: 8,000 tokens in, 600 out (about $0.022 on claude-sonnet-5).
GRADER_TOKENS: dict[str, int] = {"input_tokens": 8000, "output_tokens": 600}
HISTORY_MIN = 20
#: Session prefixes that are not live chat (excluded from the per-answer cost history).
SYNTHETIC_PREFIXES = ("golden-", "report-", "docs-", "ing-", "rerun", "skilllab-")
#: Shown on every golden run (G0 addendum 2.5).
FIDELITY_CAVEATS: tuple[str, ...] = (
    "Company memories are not recalled in a test run.",
    "Follow-ups answered by a specialist engine may lack the previous answer's context.",
    "Approved instruction updates are loaded read-only.",
    "The contract analyst's and the embeddings' spend is not measured.",
)
REFUSALS: dict[str, str] = {
    "bad_purpose": "Choose a purpose: ad hoc, baseline, release check or repeat.",
    "too_many_reps": "Ask each case fewer times.",
    "not_isolated": "Trace Lab is not set up to run tests safely right now. The platform team has the details.",
    "building_selected": "Clear your selected building first: a test run asks for the whole company.",
    "identity_unavailable": "Your sign-in could not be checked. Try again in a minute.",
    "sign_in_again": "Sign in again, then try once more.",
    "no_active_set": "Upload a golden set first.",
    "no_cases": "That golden set has no cases to ask.",
    "unknown_cases": "Some of those cases are not in the set.",
    "no_cases_left": "Every case was left out by the safety checks.",
    "grader_unpriced": "The grading model has no price, so the run's spend cannot be capped.",
    "cap_too_high": "That cap is above the most one run may spend.",
    "estimate_over_max": "This run would cost more than one run may spend. Ask fewer cases or fewer times.",
    "monthly_cap": "This company's test spend for the month would pass its limit.",
    "run_in_progress": "Another test run of this company is open. Resume or cancel it first.",
    "run_not_found": "That test run is not in this company's records.",
    "not_run_owner": "Only the person who started the run can step or resume it.",
    "run_not_running": "The run is not running.",
    "token_expiring": "Your sign-in expires soon. Refresh the page to carry on.",
    "case_missing": "A case of this run is no longer in its set.",
    "run_cap": "The run has reached its spending cap.",
    "run_not_paused": "Only a paused, interrupted or capped run can be resumed.",
    "run_not_open": "The run has already ended.",
    "run_still_running": "Pause the run, or let it finish, before grading it again.",
    "nothing_to_regrade": "Every answer of this run is already graded.",
    "regrade_in_progress": "This run is already being graded again.",
    "superadmins_only": "Only the platform team can grade a whole run again.",
}
_STATUS: dict[str, int] = {"bad_purpose": 422, "too_many_reps": 422, "unknown_cases": 422, "cap_too_high": 422,
                           "run_not_found": 404, "not_run_owner": 403, "superadmins_only": 403,
                           "identity_unavailable": 503, "sign_in_again": 401}
_FORGET = re.compile(r"^\s*(?:please\s+)?forget\b", re.I)
_CREDENTIAL = re.compile(
    r"\b(?:password|passwd|pwd|api[\s_-]?key|secret(?:[\s_-]?key)?|access[\s_-]?key|client[\s_-]?secret|"
    r"auth[\s_-]?token|access[\s_-]?token|token)\s*[:=]\s*\S+"
    r"|\bbearer\s+[A-Za-z0-9._~+/=-]{20,}"
    r"|\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.", re.I)


class RunRefused(HTTPException):
    """A refusal in the house envelope: {"detail": {"reason", "error", **figures}}."""

    def __init__(self, reason: str, http_status: int | None = None, **extra: Any) -> None:
        super().__init__(status_code=http_status or _STATUS.get(reason, 409),
                         detail={"reason": reason, "error": REFUSALS[reason], **extra})
        self.reason = reason


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name) or default)
    except ValueError:
        return default


def max_k() -> int:
    """TRACE_LAB_GOLDEN_MAX_K (default 20), never above HARD_MAX_K."""
    try:
        return max(1, min(HARD_MAX_K, int(os.getenv("TRACE_LAB_GOLDEN_MAX_K") or HARD_MAX_K)))
    except ValueError:
        return HARD_MAX_K


def run_max_usd() -> float:
    return _env_float("TRACE_LAB_GOLDEN_RUN_MAX_USD", 25.0)


def month_cap_usd() -> float:
    return _env_float("TRACE_LAB_GOLDEN_MONTHLY_USD", 50.0)


def min_token_minutes() -> float:
    return _env_float("TRACE_LAB_GOLDEN_MIN_TOKEN_MIN", 16.0)


def new_run_label(run_id: str | uuid.UUID, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return f"run-{now.strftime('%Y%m%dT%H%M%SZ')}-{uuid.UUID(str(run_id)).hex[:8]}"


def session_id_for(run_id: str | uuid.UUID, case_key: str, rep: int, attempt: int = 1) -> str:
    """golden-<12 hex of the run>-<case_key>-r<rep>, plus a<attempt> for a retried attempt."""
    sid = f"golden-{uuid.UUID(str(run_id)).hex[:12]}-{case_key}-r{int(rep)}"
    return sid + (f"a{int(attempt)}" if int(attempt) > 1 else "")


def token_minutes_left(authorization: str | None, now: float | None = None) -> float | None:
    """Minutes until the bearer's exp, read without verifying it (the identity service verifies it);
    None when it is not a JWT carrying exp."""
    try:
        token = (authorization or "").split(" ", 1)[1].strip()
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        exp = json.loads(base64.urlsafe_b64decode(payload.encode()).decode()).get("exp")
        if exp is None:
            return None
        return (float(exp) - (time.time() if now is None else now)) / 60.0
    except Exception:  # noqa: BLE001 - not a JWT: nothing to refuse for
        return None


def context_for(case: dict[str, Any], context_default: str | None) -> str | None:
    """The page context a case is asked with: its own Page/context, else the run's default; the word
    "none" (stored as '') sends nothing."""
    pc = case.get("page_context")
    if pc is None:
        return context_default or None
    return pc or None


def _field(body: Any, name: str, default: Any = None) -> Any:
    if body is None:
        return default
    value = body.get(name, default) if isinstance(body, dict) else getattr(body, name, default)
    return default if value is None else value


def screen_case(case: dict[str, Any]) -> tuple[str | None, list[str]]:
    """(why the case is left out, or None; its flags). Left out: an invalid row, a question or follow-up
    that starts with "forget", or anything that looks like a credential. Kept but flagged: text that
    reads like a request to change something (skill_lab.WRITE_INTENT): the proxy refuses the change and
    the answer reads degraded."""
    from src.agents.fiix_credential_parse import message_looks_like_fiix_credentials
    from src.agents.skill_lab import WRITE_INTENT

    question = str(case.get("question") or "").strip()
    followups = [str(f or "") for f in (case.get("followups") or [])]
    texts = [question, *followups]
    if not question or len(question) > MAX_QUESTION_CHARS:
        return "the question is empty or too long", []
    if len(followups) > MAX_FOLLOWUPS or any(len(f) > MAX_QUESTION_CHARS or not f.strip() for f in followups):
        return "a follow-up is empty or too long, or there are more than five", []
    if str(case.get("ask_as") or "company").casefold() != "company":
        return "test runs ask company-wide questions only", []
    if any(_FORGET.match(t) for t in texts):
        return "it asks the chat to forget something", []
    if any(_CREDENTIAL.search(t) or message_looks_like_fiix_credentials(t) for t in texts):
        return "it looks like it carries a credential", []
    return None, (["write_intent"] if any(WRITE_INTENT.search(t) for t in texts) else [])


def screen_cases(cases: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """(kept, skipped [{case_key, reason}], flagged [{case_key, flags}]) in the set's order."""
    kept, skipped, flagged = [], [], []
    for c in cases:
        why, flags = screen_case(c)
        if why:
            skipped.append({"case_key": c.get("case_key"), "reason": why})
            continue
        kept.append(c)
        if flags:
            flagged.append({"case_key": c.get("case_key"), "flags": flags})
    return kept, skipped, flagged


def grader_model() -> str:
    """TRACE_LAB_JUDGE_MODEL, else svc-deepagents' compliance_summary_model, else claude-sonnet-5."""
    from src.config import settings

    return ((os.getenv("TRACE_LAB_JUDGE_MODEL") or "").strip()
            or (getattr(settings, "compliance_summary_model", "") or "").strip() or "claude-sonnet-5")


def grader_price(model: str | None = None) -> float | None:
    from src.agents import llm_cost

    return llm_cost.price_usd(model or grader_model(), GRADER_TOKENS)


async def answer_basis(org: str) -> dict[str, Any]:
    """What one answer costs: the median of the company's last 200 live chat turns when there are at least
    20 (read-only, from the application database), else TRACE_LAB_GOLDEN_DEFAULT_ANSWER_USD ($0.09)."""
    excluded = " ".join(f"AND coalesce(session_id, '') NOT LIKE '{p}%'" for p in SYNTHETIC_PREFIXES)
    sql = (f"SELECT count(*) AS n, percentile_cont(0.5) WITHIN GROUP (ORDER BY cost_usd) AS usd FROM ("
           f"SELECT cost_usd FROM plenum_cafm.agent_trace_turns WHERE organization_id = CAST(:org AS uuid) "
           f"AND cost_usd IS NOT NULL {excluded} ORDER BY started_at DESC LIMIT 200) t")
    row = None
    try:
        async with db.main_engine().begin() as conn:
            await conn.execute(text("SET LOCAL statement_timeout = '10s'"))
            row = (await conn.execute(text(sql), {"org": org})).mappings().first()
    except Exception as exc:  # noqa: BLE001 - no history is not an error: the default applies
        log.info("trace_lab.golden.no_cost_history", error=str(exc)[:200])
    if row and int(row["n"] or 0) >= HISTORY_MIN and row["usd"] is not None:
        return {"per_answer_usd": round(float(row["usd"]), 4), "basis": "history"}
    return {"per_answer_usd": _env_float("TRACE_LAB_GOLDEN_DEFAULT_ANSWER_USD", DEFAULT_ANSWER_USD), "basis": "default"}


async def month_spent(org: str) -> float:
    """The company's golden spend this UTC month: every golden run and re-grade job's usd_spent."""
    async with db.lab_engine().connect() as conn:
        v = (await conn.execute(text(
            "SELECT coalesce(sum(usd_spent), 0) FROM tracelab.jobs WHERE organization_id = CAST(:org AS uuid)"
            " AND kind IN ('golden_run', 'golden_grade')"
            " AND created_at >= date_trunc('month', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'"),
            {"org": org})).scalar_one()
    return float(v or 0)


def messages_of(case: dict[str, Any]) -> int:
    return 1 + len(case.get("followups") or [])


def estimate_for(cases: list[dict[str, Any]], k: int, *, per_answer: float, per_grade: float) -> dict[str, Any]:
    messages = sum(messages_of(c) for c in cases) * k
    items = len(cases) * k
    answers, grading = round(messages * per_answer, 4), round(items * per_grade, 4)
    total = round(answers + grading, 4)
    return {"cases": len(cases), "k": k, "messages": messages, "items": items, "per_answer_usd": per_answer,
            "per_grade_usd": round(per_grade, 4), "est_answers_usd": answers, "est_grading_usd": grading,
            "est_total_usd": total, "usd_cap_default": round(min(max(total * 1.5, 0.01), run_max_usd()), 2),
            "run_max_usd": run_max_usd()}


async def fetch_me(authorization: str | None) -> dict[str, Any]:
    """The caller's /api/auth/me payload, fetched like svc-deepagents' own principal lookup: through
    settings.operations_intelligence_base_url, which in this container is Trace Lab's proxy."""
    from src.config import settings
    from src.http_client import request as http_request

    try:
        resp = await http_request("GET", settings.operations_intelligence_base_url.rstrip("/"), "/api/auth/me",
                                  service="operations_intelligence", timeout=15.0,
                                  headers={"Authorization": authorization or ""}, max_attempts=1)
        body = resp.json()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 401:
            raise RunRefused("sign_in_again") from None
        raise RunRefused("identity_unavailable") from None
    except Exception:  # noqa: BLE001
        raise RunRefused("identity_unavailable") from None
    return body if isinstance(body, dict) else {}


async def selected_building(authorization: str | None) -> str | None:
    """The building the caller has selected, if any (the /me payload's user.selected_building_id): a
    selection narrows every service's answers to one building, so a run refuses to start or step."""
    user = (await fetch_me(authorization)).get("user") or {}
    sel = user.get("selected_building_id")
    return str(sel) if sel else None


async def _active_set(org: str) -> dict[str, Any] | None:
    from . import golden_store

    return await golden_store.active_set(org)


async def _cases(org: str, set_id: str) -> list[dict[str, Any]]:
    from . import golden_store

    return [dict(c) for c in await golden_store.cases_for_run(org, set_id)]


async def _choose(org: str, body: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """The set (named, else the company's active one) and the cases to ask (all, or the keys named)."""
    set_id = _field(body, "set_id")
    if set_id:
        chosen: dict[str, Any] | None = {"id": str(set_id)}
    else:
        chosen = await _active_set(org)
        if chosen is None:
            raise RunRefused("no_active_set")
    cases = await _cases(org, str(chosen["id"]))
    if not cases:
        raise RunRefused("no_cases")
    wanted = [str(k) for k in (_field(body, "case_keys") or [])]
    if wanted:
        known = {c["case_key"] for c in cases}
        unknown = sorted(set(wanted) - known)
        if unknown:
            raise RunRefused("unknown_cases", case_keys=unknown[:20])
        cases = [c for c in cases if c["case_key"] in set(wanted)]
    return dict(chosen), cases


async def _plan(org: str, body: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """(the estimate, the screened cases to ask): every refusal start_run gives about the request itself."""
    purpose = str(_field(body, "purpose", "adhoc"))
    if purpose not in PURPOSES:
        raise RunRefused("bad_purpose")
    k = int(_field(body, "k", 1))
    if not 1 <= k <= max_k():
        raise RunRefused("too_many_reps", max_k=max_k())
    chosen, cases = await _choose(org, body)
    kept, skipped, flagged = screen_cases(cases)
    if not kept:
        raise RunRefused("no_cases_left", skipped=skipped[:50])
    model = grader_model()
    per_grade = grader_price(model)
    if per_grade is None:
        raise RunRefused("grader_unpriced", grader_model=model)
    basis = await answer_basis(org)
    est = estimate_for(kept, k, per_answer=basis["per_answer_usd"], per_grade=per_grade)
    spent, cap = await month_spent(org), month_cap_usd()
    return {**est, "purpose": purpose, "set_id": str(chosen["id"]),
            "set": {key: chosen.get(key) for key in ("id", "name", "version", "kind")},
            "per_answer_basis": basis["basis"], "grader_model": model, "month_spent_usd": round(spent, 4),
            "month_cap_usd": cap, "month_left_usd": round(max(0.0, cap - spent), 4), "skipped": skipped,
            "flagged": flagged, "case_keys": [c["case_key"] for c in kept], "caveats": list(FIDELITY_CAVEATS)}, kept


async def estimate(*, org: str, principal: Any, body: Any, authorization: str | None = None) -> dict[str, Any]:
    """POST /golden-runs/estimate: what a run would ask and cost, with every refusal about the request
    start_run would give. Writes nothing and calls no model."""
    est, _ = await _plan(org, body)
    return est


def _sha12(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()[:12]


def like_for_like(*, context_default: str, set_hash: str, model: str) -> dict[str, Any]:
    """What can change answers between runs, kept on the run so Compare says why two runs differ."""
    from src.config import settings
    from src.services import skill_overlays

    return {"chat_model": settings.openai_model, "grader_model": model,
            "flags": {name: os.getenv(name) for name in ("ORCHESTRATOR_LLM_ROUTING", "COMPLIANCE_SKILL_PATH",
                                                         "COMPLIANCE_SELECTIVE_SKILLS", "CONTEXT_SELF_MANAGE")},
            "git_sha": os.getenv("GIT_SHA") or os.getenv("IMAGE_TAG") or None,
            "overlays_sha": _sha12({f"{s}/{d}": c for (s, d), c in sorted(skill_overlays._active.items())}),
            "context_default_sha": _sha12(context_default), "set_hash": set_hash}


async def start_run(*, org: str, principal: Any, authorization: str | None, body: Any) -> dict[str, Any]:
    """POST /golden-runs. Every refusal comes before anything is written: the purpose and reps, an
    unsafe process (orchestrator_host.assert_isolated), a selected building, the set and its cases, the
    screening, an unpriced grader, the run and month caps, and another open run of the company. Then the
    job (scope golden_run:<org>, claimed: running) and the run row with its like-for-like params. Nothing
    is asked until the page steps the run."""
    from . import golden

    problems = orchestrator_host.assert_isolated()
    if problems:
        raise RunRefused("not_isolated", problems=problems)
    est, kept = await _plan(org, body)
    if await selected_building(authorization):
        raise RunRefused("building_selected")
    cap = round(float(_field(body, "usd_cap", 0) or est["usd_cap_default"]), 2)
    if cap > run_max_usd():
        raise RunRefused("cap_too_high", usd_cap=cap, run_max_usd=run_max_usd())
    if est["est_total_usd"] > run_max_usd():
        raise RunRefused("estimate_over_max", est_total_usd=est["est_total_usd"], run_max_usd=run_max_usd())
    if est["month_spent_usd"] + est["est_total_usd"] > est["month_cap_usd"]:
        raise RunRefused("monthly_cap", month_spent_usd=est["month_spent_usd"], month_cap_usd=est["month_cap_usd"],
                         est_total_usd=est["est_total_usd"])
    await orchestrator_host.ensure_overlays_readonly()
    run_id = str(uuid.uuid4())
    context_default = str(_field(body, "context_default", "") or "")[:4000]
    set_hash = golden.set_hash([(c["case_key"], str(c.get("case_version") or "")) for c in kept])
    params = {"case_keys": est["case_keys"], "skipped": est["skipped"], "flagged": est["flagged"],
              "context_default": context_default, "acting_org": org, "per_answer_usd": est["per_answer_usd"],
              "per_grade_usd": est["per_grade_usd"], "note": str(_field(body, "note", "") or "")[:500] or None,
              "started_by_role": getattr(principal, "role", None),
              **like_for_like(context_default=context_default, set_hash=set_hash, model=est["grader_model"])}
    try:
        job = await jobs.create(kind="golden_run", scope_key=f"golden_run:{org}", organization_id=org,
                                params={"run_id": run_id, "set_id": est["set_id"], "k": est["k"],
                                        "purpose": est["purpose"]},
                                usd_cap=Decimal(str(cap)), usd_estimate=Decimal(str(est["est_total_usd"])),
                                total=len(kept) * est["k"], actor_id=str(principal.user_id),
                                actor_email=getattr(principal, "email", None) or None)
    except jobs.JobConflict:
        raise RunRefused("run_in_progress") from None
    try:
        async with db.lab_engine().begin() as conn:
            await conn.execute(text(
                "INSERT INTO tracelab.golden_runs (id, organization_id, set_id, label, purpose, k, status, job_id,"
                " started_by, started_by_email, started_at, params, estimate_usd, usd_cap, usd_spent)"
                " VALUES (CAST(:id AS uuid), CAST(:org AS uuid), CAST(:set AS uuid), :label, :purpose, :k, 'queued',"
                " CAST(:job AS uuid), :by, :email, now(), CAST(:params AS jsonb), :est, :cap, 0)"),
                {"id": run_id, "org": org, "set": est["set_id"], "label": new_run_label(run_id),
                 "purpose": est["purpose"], "k": est["k"], "job": job["id"], "by": str(principal.user_id),
                 "email": getattr(principal, "email", None) or None, "params": json.dumps(params, default=str),
                 "est": est["est_total_usd"], "cap": cap})
    except Exception:
        await jobs.finish(job["id"], "failed", "The run could not be recorded.")
        raise
    await jobs.claim(job["id"])
    await _sync_status(run_id)
    log.info("trace_lab.golden.run_started", run_id=run_id, organization_id=org, cases=len(kept), k=est["k"],
             purpose=est["purpose"], usd_cap=cap)
    return {"ok": True, "run": summary(await load_run(run_id, org)), "estimate": est}


async def load_run(run_id: str, org: str | None = None) -> dict[str, Any] | None:
    """The run joined with its job (job_status, done, total, usd_reserved, progress, job_error); scoped to
    org when given. None for an unknown or malformed id."""
    try:
        rid = str(uuid.UUID(str(run_id)))
    except ValueError:
        return None
    where = "" if org is None else " AND r.organization_id = CAST(:org AS uuid)"
    async with db.lab_engine().connect() as conn:
        row = (await conn.execute(text(
            "SELECT r.*, j.status AS job_status, j.done, j.total, j.usd_reserved, j.usd_spent AS job_usd_spent,"
            " j.usd_cap AS job_usd_cap, j.progress, j.error AS job_error FROM tracelab.golden_runs r"
            f" JOIN tracelab.jobs j ON j.id = r.job_id WHERE r.id = CAST(:id AS uuid){where}"),
            {"id": rid, "org": org})).mappings().first()
    if row is None:
        return None
    out = dict(row)
    for k in ("id", "organization_id", "set_id", "job_id"):
        if out.get(k) is not None:
            out[k] = str(out[k])
    for k in ("params", "progress", "summary"):
        if isinstance(out.get(k), str):
            out[k] = json.loads(out[k])
    out["params"] = out.get("params") or {}
    out["progress"] = out.get("progress") or {}
    return out


async def _sync_status(run_id: str) -> None:
    """golden_runs follows the run's job: status, spend, cap, finished_at once it has ended, and the job's
    error in golden_runs.summary (where the run views read paused_reason, current and error)."""
    async with db.lab_engine().begin() as conn:
        await conn.execute(text(
            "UPDATE tracelab.golden_runs r SET status = j.status, usd_spent = j.usd_spent, usd_cap = j.usd_cap,"
            " finished_at = CASE WHEN j.status IN ('done', 'failed', 'cancelled') THEN coalesce(r.finished_at, now())"
            " ELSE NULL END, summary = r.summary || jsonb_build_object('error', j.error)"
            " FROM tracelab.jobs j WHERE j.id = r.job_id AND r.id = CAST(:id AS uuid)"),
            {"id": run_id})


async def _set_summary(run_id: str, **patch: Any) -> None:
    """Merge keys into golden_runs.summary: paused_reason (paused, page_closed, run_cap, monthly_cap,
    interrupted or None) and current ({case_key, rep} while an item runs, else None)."""
    async with db.lab_engine().begin() as conn:
        await conn.execute(text("UPDATE tracelab.golden_runs SET summary = summary || CAST(:p AS jsonb)"
                                " WHERE id = CAST(:id AS uuid)"), {"p": json.dumps(patch, default=str), "id": run_id})


def _num(v: Any) -> float | None:
    return None if v is None else float(v)


def _iso(v: Any) -> str | None:
    return v.isoformat() if isinstance(v, datetime) else (None if v is None else str(v))


def summary(run: dict[str, Any] | None) -> dict[str, Any] | None:
    """The run as the API returns it (never a token, never an expected answer)."""
    if run is None:
        return None
    p = run.get("params") or {}
    live = run.get("summary") or {}
    return {"id": run["id"], "label": run.get("label"), "status": run.get("job_status") or run.get("status"),
            "purpose": run.get("purpose"), "k": run.get("k"), "set_id": run.get("set_id"),
            "organization_id": run.get("organization_id"), "cases": len(p.get("case_keys") or []),
            "items_total": run.get("total"), "items_done": run.get("done"),
            "estimate_usd": _num(run.get("estimate_usd")), "usd_cap": _num(run.get("job_usd_cap") or run.get("usd_cap")),
            "usd_spent": _num(run.get("job_usd_spent") if run.get("job_usd_spent") is not None else run.get("usd_spent")),
            "started_at": _iso(run.get("started_at")), "finished_at": _iso(run.get("finished_at")),
            "started_by_email": run.get("started_by_email"), "in_flight": str(run["id"]) in _ACTIVE,
            "skipped": p.get("skipped") or [], "flagged": p.get("flagged") or [], "note": p.get("note"),
            "paused_reason": live.get("paused_reason"), "current": live.get("current"),
            "error": run.get("job_error"), "caveats": list(FIDELITY_CAVEATS)}


#: Items running in this process, by run id (Phase 3 Task 8 fills it). At most one at a time.
_ACTIVE: dict[str, asyncio.Task] = {}


# ── stepping a run: one item per step, in the background, with the page's bearer (Phase 3 Task 8) ──────

ITEM_TIMEOUT_S = _env_float("TRACE_LAB_GOLDEN_ITEM_TIMEOUT_S", 900.0)
INFRA_STATUS = frozenset({401, 429, 502, 503, 504})
#: Applied to error texts only, never to answer text. A 500 or a 403 is an answer: users hit the same.
INFRA_MARKERS: tuple[str, ...] = ("timeout", "timed out", "rate limit", "429", "502", "503", "504", "connection",
                                  "circuit open", "overloaded", "temporarily unavailable",
                                  "identity service unavailable", "missing_token", "invalid_token")
_TOOL_INFRA_TEXT = ("circuit open", "timed out", "missing_token")
OUTCOMES = ("pending", "pass", "fail", "unsure", "degraded", "stale_reference", "error")
#: Phase 1 grading words that golden_results.outcome spells differently.
OUTCOME_MAP: dict[str, str] = {"grading_pending": "pending", "infra_failed": "error", "needs_person": "unsure",
                               "context_unavailable": "degraded"}
_STEPPING: set[str] = set()


def infra_reason(ct: capture.CapturedTurn) -> str | None:
    """Why this message failed for an infrastructure reason (never graded), or None."""
    if ct.ended_by == "timeout":
        return ct.error or "the message timed out"
    for u in ct.upstream_failures:
        status = u.get("status")
        if status is None or int(status) in INFRA_STATUS:
            return f"a service answered {status or 'nothing'} ({u.get('service')} {u.get('method')} {u.get('path')})"
    for e in ct.tool_errors:
        err = str(e.get("error") or "").lower()
        if e.get("status_code") in INFRA_STATUS or any(t in err for t in _TOOL_INFRA_TEXT):
            return f"a tool failed ({e.get('tool')}: {e.get('status_code') or e.get('error')})"
    if ct.error and ct.ended_by in ("error", "exception") and any(m in ct.error.lower() for m in INFRA_MARKERS):
        return ct.error
    return None


def classify(ct: capture.CapturedTurn) -> str | None:
    """'infra', 'gate' (the chat asked for an approval) or 'chat_error'; None for an answer."""
    if infra_reason(ct):
        return "infra"
    if ct.ended_by == "gate_interrupt":
        return "gate"
    if ct.ended_by != "workflow_completed" or ct.error:
        return "chat_error"
    return None


def final_outcome(grade: Any, captured: list[capture.CapturedTurn]) -> str:
    """The item's stored outcome: the grader's, spelled as golden_results allows; 'degraded' when the proxy
    refused a request or the chat stopped at an approval gate (never 'wrong'), unless the chat failed."""
    raw = getattr(grade, "outcome", None) if not isinstance(grade, dict) else grade.get("outcome")
    out = OUTCOME_MAP.get(str(raw or "pending"), str(raw or "pending"))
    chat_error = any(classify(ct) == "chat_error" for ct in captured)
    if not chat_error and any(ct.refused or ct.ended_by == "gate_interrupt" for ct in captured):
        return "degraded"
    return out if out in OUTCOMES else "unsure"


def grade_dict(grade: Any) -> dict[str, Any]:
    """What golden_results.grade holds: the grader's own to_json() when it has one (Task 10's Grade), else the
    record's fields."""
    if grade is None:
        return {}
    if callable(getattr(grade, "to_json", None)):
        d = dict(grade.to_json())
    elif dataclasses.is_dataclass(grade) and not isinstance(grade, type):
        d = dataclasses.asdict(grade)
    elif hasattr(grade, "_asdict"):
        d = dict(grade._asdict())
    elif isinstance(grade, dict):
        d = dict(grade)
    else:
        d = {k: getattr(grade, k, None) for k in ("outcome", "critique", "mismatches", "value_check",
                                                   "grader_version", "usd")}
    return json.loads(json.dumps(d, default=str))


def next_item(case_keys: list[str], k: int, done: set[tuple[str, int]], attempts: dict[str, int]) -> tuple[str, int, int] | None:
    """The next (case_key, rep, attempt) to ask, rep-major; None when every item is stored. attempts holds
    the infrastructure attempts already made, keyed "<case_key>#<rep>"."""
    for rep in range(1, int(k) + 1):
        for key in case_keys:
            if (key, rep) not in done:
                return key, rep, int(attempts.get(f"{key}#{rep}", 0)) + 1
    return None


async def _done_items(run_id: str) -> set[tuple[str, int]]:
    async with db.lab_engine().connect() as conn:
        rows = (await conn.execute(text(
            "SELECT DISTINCT case_key, rep FROM tracelab.golden_results WHERE run_id = CAST(:r AS uuid)"),
            {"r": run_id})).all()
    return {(str(r[0]), int(r[1])) for r in rows}


async def _job_status(job_id: str, org: str) -> str | None:
    job = await jobs.get(job_id, organization_id=org)
    return job["status"] if job else None


async def _grade(case: dict[str, Any], captured: capture.CapturedTurn, *,
                 earlier: list[tuple[str, capture.CapturedTurn]] | None = None) -> Any:
    """Phase 3 Task 10's grader on the last message asked, with the messages before it."""
    from . import grader

    return await grader.grade(case, captured, earlier=list(earlier or []), question=captured.question)


def _dumps(value: Any) -> str:
    """JSON for a jsonb column: Postgres refuses the NUL character inside text."""
    return json.dumps(value, default=str).replace("\\u0000", "")


async def _snapshot(conn: Any, org: str, ct: capture.CapturedTurn, meta: dict[str, Any]) -> None:
    """The message's turn and span tree into Trace Lab's evidence tables (append-only), reason 'golden'.
    The turn row carries the golden identity and the answer's cards, which re-grading needs."""
    turn = {**ct.turn, "golden": meta, "cards": ct.cards, "refused": ct.refused, "ended_by": ct.ended_by}
    await conn.execute(text(
        "INSERT INTO tracelab.evidence_turns (turn_id, organization_id, turn, reason)"
        " VALUES (:tid, CAST(:org AS uuid), CAST(:turn AS jsonb), 'golden') ON CONFLICT (turn_id) DO NOTHING"),
        {"tid": ct.turn_id, "org": org, "turn": _dumps(turn)})
    for seq, span in enumerate(ct.spans):
        await conn.execute(text(
            "INSERT INTO tracelab.evidence_spans (turn_id, span_id, seq, span)"
            " VALUES (:tid, :sid, :seq, CAST(:span AS jsonb)) ON CONFLICT DO NOTHING"),
            {"tid": ct.turn_id, "sid": str(span.get("id")), "seq": seq, "span": _dumps(span)})


NOT_ASKED = "Not asked: the conversation stopped before this message."
#: Spend columns of golden_runs follow the job inside the same transaction as the job's checkpoint.
_RUN_FOLLOWS_JOB = ("UPDATE tracelab.golden_runs r SET usd_spent = j.usd_spent,"
                    " summary = r.summary || CAST('{\"current\": null}' AS jsonb)"
                    " FROM tracelab.jobs j WHERE j.id = r.job_id AND r.id = CAST(:id AS uuid)")


async def _store_item(run: dict[str, Any], case: dict[str, Any], rep: int, attempt: int, sid: str,
                      captured: list[capture.CapturedTurn], *, outcome: str, grade: dict[str, Any],
                      grade_usd: float, reserved: Decimal, error: str | None, page: str | None,
                      principal: Any) -> None:
    """One transaction: the evidence of every message asked, one golden_results row for EVERY message of the
    case (a message never asked gets a row with no answer and no turn), the job's checkpoint (done + 1, the
    answers' and the grader's spend, the reservation released) and the run's spend. The FINAL row - the
    case's last message, msg_idx = number of follow-ups - carries the outcome, the grade (with the grader's
    usd) and graded_at; earlier rows stay pending with no grade. usd on a row is that message's answer cost."""
    org = run["organization_id"]
    texts = [str(case["question"]), *[str(f) for f in (case.get("followups") or [])]]
    spent = round(sum(ct.usd for ct in captured) + grade_usd, 6)
    graded = outcome not in ("pending", "error")
    async with db.lab_engine().begin() as conn:
        for i, question in enumerate(texts):
            ct = captured[i] if i < len(captured) else None
            final = i == len(texts) - 1
            if ct is not None and ct.turn:
                meta = {"run_id": run["id"], "case_key": case["case_key"], "case_version": case.get("case_version"),
                        "rep": rep, "attempt": attempt, "msg_idx": i, "page_context": page,
                        "asker_role": getattr(principal, "role", None)}
                await _snapshot(conn, org, ct, meta)
            if ct is not None:
                err = ct.error or (error if final else None)
            else:
                err = error if error and (final or i == len(captured)) else NOT_ASKED
            await conn.execute(text(
                "INSERT INTO tracelab.golden_results (id, run_id, organization_id, case_id, case_key, case_version,"
                " rep, msg_idx, session_id, turn_id, question, answer, outcome, grade, refused, usd, ms, error,"
                " graded_at) VALUES (CAST(:id AS uuid), CAST(:run AS uuid), CAST(:org AS uuid), CAST(:case AS uuid),"
                " :key, :ver, :rep, :msg, :sid, :tid, :q, :a, :outcome, CAST(:grade AS jsonb), CAST(:refused AS jsonb),"
                " :usd, :ms, :err, CASE WHEN :graded THEN now() END)"),
                {"id": str(uuid.uuid4()), "run": run["id"], "org": org, "case": case.get("id"),
                 "key": case["case_key"], "ver": case.get("case_version"), "rep": rep, "msg": i, "sid": sid,
                 "tid": ct.turn_id if ct is not None else None, "q": ct.question if ct is not None else question,
                 "a": ct.answer if ct is not None else None, "outcome": outcome if final else "pending",
                 "grade": _dumps(grade) if final else None, "refused": _dumps(ct.refused if ct is not None else []),
                 "usd": round(ct.usd, 6) if ct is not None else None, "ms": ct.ms if ct is not None else None,
                 "err": (err or None) and str(err)[:2000], "graded": final and graded})
        await jobs.checkpoint(conn, run["job_id"], usd=Decimal(str(spent)), released=reserved, done_delta=1,
                              progress_patch={"current": None})
        await conn.execute(text(_RUN_FOLLOWS_JOB), {"id": run["id"]})


async def _note_attempt(run: dict[str, Any], key: str, rep: int, attempt: int, reason: str, spent: float,
                        reserved: Decimal) -> None:
    """An infrastructure attempt: nothing is stored or graded; its spend counts, and the next step asks
    the item again with a fresh session."""
    job = await jobs.get(run["job_id"], organization_id=run["organization_id"])
    tries = dict(((job or {}).get("progress") or {}).get("attempts") or {})
    tries[f"{key}#{rep}"] = attempt
    async with db.lab_engine().begin() as conn:
        await jobs.checkpoint(conn, run["job_id"], usd=Decimal(str(round(spent, 6))), released=reserved,
                              done_delta=0, progress_patch={"attempts": tries, "current": None, "last_infra": {
                                  "case_key": key, "rep": rep, "attempt": attempt, "reason": reason[:300]}})
        await conn.execute(text(_RUN_FOLLOWS_JOB), {"id": run["id"]})
    log.warning("trace_lab.golden.infra_attempt", run_id=run["id"], case_key=key, rep=rep, attempt=attempt,
                reason=reason[:200])


async def _run_item(run: dict[str, Any], case: dict[str, Any], rep: int, attempt: int, *, principal: Any,
                    authorization: str | None, reserved: Decimal) -> None:
    """One attempt at one (case, rep): every message in order in one session (follow-ups keep its
    context), then graded and stored. Never raises."""
    org, key = run["organization_id"], case["case_key"]
    sid = session_id_for(run["id"], key, rep, attempt)
    page = context_for(case, (run.get("params") or {}).get("context_default"))
    messages = [str(case["question"]), *[str(f) for f in (case.get("followups") or [])]]
    captured: list[capture.CapturedTurn] = []
    cancelled, timed_out = False, None
    try:
        orchestrator = orchestrator_host.get_orchestrator()
        budget = asyncio.timeout(ITEM_TIMEOUT_S)
        try:
            async with budget:
                for i, question in enumerate(messages):
                    if await _job_status(run["job_id"], org) in ("cancelled", "failed"):
                        cancelled = True
                        break
                    ct = await capture.run_message(orchestrator, question=question, session_id=sid, page_context=page,
                                                   principal=principal, organization_id=org,
                                                   authorization=authorization)
                    ct.history = [{"question": c.question, "answer": c.answer} for c in captured]
                    captured.append(ct)
                    await jobs.heartbeat(run["job_id"], progress_patch={"current": {
                        "case_key": key, "rep": rep, "attempt": attempt, "msg_idx": i}})
                    if classify(ct) is not None:
                        break                       # scripted follow-ups assume an answer
        except TimeoutError:
            if not budget.expired():
                raise                               # a timeout inside (the database, say): not the item's budget
            timed_out = f"the case took longer than {int(ITEM_TIMEOUT_S)} s"
        if cancelled:
            await jobs.release(run["job_id"], reserved)
            await _set_summary(run["id"], current=None)
            return                                  # an unfinished item is never stored or graded
        infra = timed_out or next((r for r in (infra_reason(ct) for ct in captured) if r), None)
        if not captured and not infra:
            infra = "nothing was asked"
        if infra and attempt < MAX_ATTEMPTS:
            await _note_attempt(run, key, rep, attempt, infra, sum(ct.usd for ct in captured), reserved)
            return
        if infra:
            grade, grade_usd, outcome = {"error": infra, "attempts": attempt}, 0.0, "error"
        else:
            try:
                g = await _grade(case, captured[-1], earlier=[(c.question, c) for c in captured[:-1]])
                grade, outcome = grade_dict(g), final_outcome(g, captured)
                grade_usd = float((g.get("usd") if isinstance(g, dict) else getattr(g, "usd", 0)) or 0)
            except Exception as exc:  # noqa: BLE001 - graded later ("Grade again")
                grade, grade_usd = {"error": f"the grader failed: {type(exc).__name__}: {str(exc)[:200]}"}, 0.0
                outcome = "pending"
        await _store_item(run, case, rep, attempt, sid, captured, outcome=outcome, grade=grade, grade_usd=grade_usd,
                          reserved=reserved, error=infra, page=page, principal=principal)
        log.info("trace_lab.golden.item_done", run_id=run["id"], case_key=key, rep=rep, attempt=attempt,
                 outcome=outcome, messages=len(captured))
    except Exception as exc:  # noqa: BLE001 - the item runs again on a later step
        log.warning("trace_lab.golden.item_failed", run_id=run["id"], case_key=key, rep=rep, error=str(exc)[:300])
        try:
            await jobs.release(run["job_id"], reserved)
            await _set_summary(run["id"], current=None)
        except Exception:  # noqa: BLE001
            pass
    finally:
        orchestrator_host.release_session(sid)


def _forget_task(run_id: str, task: asyncio.Task) -> None:
    if _ACTIVE.get(run_id) is task:
        _ACTIVE.pop(run_id, None)


async def step(run_id: str, *, principal: Any, authorization: str | None, org: str | None = None) -> dict[str, Any]:
    """POST /golden-runs/{run_id}/step with the page's own bearer: start the run's next item in the
    background and answer at once (the route returns 202). Only the starter steps, the run must be running,
    the token needs min_token_minutes() left, no building may be selected and the process must be isolated.
    Payloads: {"started": True, "item"}, {"started": False, "reason": "in_flight" | "busy"} or
    {"started": False, "done": True}; each carries "run". Refusals raise RunRefused."""
    run = await load_run(run_id, org)
    if run is None:
        raise RunRefused("run_not_found")
    if str(principal.user_id) != str(run.get("started_by")):
        raise RunRefused("not_run_owner")
    if run["job_status"] != "running":
        raise RunRefused("run_not_running", status=run["job_status"])
    if run["id"] in _ACTIVE:
        return {"ok": True, "started": False, "reason": "in_flight", "run": summary(run)}
    if _ACTIVE or _STEPPING:
        return {"ok": True, "started": False, "reason": "busy", "run": summary(run)}
    _STEPPING.add(run["id"])
    try:
        left = token_minutes_left(authorization)
        if left is not None and left < min_token_minutes():
            raise RunRefused("token_expiring", minutes_left=round(left, 1))
        problems = orchestrator_host.assert_isolated()
        if problems:
            raise RunRefused("not_isolated", problems=problems)
        if not orchestrator_host.overlays_loaded():
            # After a restart the run was started by another process: load the approved overlays read-only
            # (skill_overlays._ready pre-set, one SELECT) before any chat runs, so refresh() never tries DDL.
            await orchestrator_host.ensure_overlays_readonly()
        if await selected_building(authorization):
            raise RunRefused("building_selected")
        p = run.get("params") or {}
        nxt = next_item(list(p.get("case_keys") or []), int(run["k"]), await _done_items(run["id"]),
                        dict((run.get("progress") or {}).get("attempts") or {}))
        if nxt is None:
            await jobs.finish(run["job_id"], "done")
            await _sync_status(run["id"])
            return {"ok": True, "started": False, "done": True, "run": summary(await load_run(run["id"]))}
        key, rep, attempt = nxt
        case = next((c for c in await _cases(run["organization_id"], str(run["set_id"])) if c["case_key"] == key), None)
        if case is None:
            raise RunRefused("case_missing", case_key=key)
        est = Decimal(str(round(messages_of(case) * float(p.get("per_answer_usd") or DEFAULT_ANSWER_USD)
                                + float(p.get("per_grade_usd") or 0), 6)))
        spent, cap = await month_spent(run["organization_id"]), month_cap_usd()
        if spent + float(est) > cap:
            await jobs.finish(run["job_id"], "capped", REFUSALS["monthly_cap"])
            await _sync_status(run["id"])
            await _set_summary(run["id"], paused_reason="monthly_cap", current=None)
            raise RunRefused("monthly_cap", month_spent_usd=round(spent, 4), month_cap_usd=cap,
                             next_item_usd=float(est))
        try:
            await jobs.reserve(run["job_id"], est)
        except jobs.CapReached:
            await jobs.finish(run["job_id"], "capped", REFUSALS["run_cap"])
            await _sync_status(run["id"])
            await _set_summary(run["id"], paused_reason="run_cap", current=None)
            raise RunRefused("run_cap", usd_spent=_num(run.get("job_usd_spent")), usd_cap=_num(run.get("job_usd_cap")),
                             next_item_usd=float(est)) from None
        except jobs.JobNotRunning:
            raise RunRefused("run_not_running", status=await _job_status(run["job_id"], run["organization_id"])) from None
        await _set_summary(run["id"], current={"case_key": key, "rep": rep}, paused_reason=None)
        task = asyncio.create_task(_run_item(run, case, rep, attempt, principal=principal,
                                             authorization=authorization, reserved=est))
        _ACTIVE[run["id"]] = task
        task.add_done_callback(lambda t, rid=run["id"]: _forget_task(rid, t))
        return {"ok": True, "started": True, "item": {"case_key": key, "rep": rep, "attempt": attempt},
                "run": summary(run)}
    finally:
        _STEPPING.discard(run["id"])


# ── pause, resume, cancel, grade again, and after a restart (Phase 3 Task 9) ──────────────────────────────

#: Graders other than the golden run's own for golden_grade jobs, chosen by params["handler"]. A later
#: phase registers its own (Phase 7: "eval_cases"). Each value is `async fn(job: GradeJob) -> str`, returning
#: the job's final status (a jobs.FINISH_STATUSES value).
GRADE_HANDLERS: dict[str, Any] = {}
_GRADING: set[asyncio.Task] = set()


@dataclass
class GradeJob:
    """What a golden_grade handler is given: the job row and its heartbeat (True means stop)."""
    job_id: str
    job: dict[str, Any]

    @property
    def params(self) -> dict[str, Any]:
        p = self.job.get("params") or {}
        return json.loads(p) if isinstance(p, str) else dict(p)

    async def heartbeat(self, progress_patch: dict[str, Any] | None = None) -> bool:
        return await jobs.heartbeat(self.job_id, progress_patch=progress_patch)


def register_grade_handler(name: str, fn: Any) -> None:
    """A golden_grade job whose params["handler"] is `name` runs `await fn(GradeJob)`. "golden" (and no
    handler at all) is the golden run's own re-grade and cannot be replaced."""
    key = str(name or "").strip()
    if not key or key == "golden":
        raise ValueError("a golden_grade handler needs a name other than 'golden'")
    if not callable(fn):
        raise ValueError(f"the golden_grade handler {key!r} is not callable")
    GRADE_HANDLERS[key] = fn


async def _owned_run(run_id: str, org: str | None) -> dict[str, Any]:
    """The run, scoped to org when given (the routes check the company first with golden_views.require_run)."""
    run = await load_run(run_id, org)
    if run is None:
        raise RunRefused("run_not_found")
    return run


PAUSE_REASONS = ("paused", "page_closed")


async def pause(run_id: str, *, principal: Any, reason: str = "paused", org: str | None = None) -> dict[str, Any]:
    """Any reviewer of the company (reason paused), or the closing page (reason page_closed): no new item
    starts; the item in flight finishes and is stored."""
    run = await _owned_run(run_id, org)
    if run["job_status"] != "running":
        raise RunRefused("run_not_running", status=run["job_status"])
    await jobs.finish(run["job_id"], "paused")
    await _sync_status(run["id"])
    await _set_summary(run["id"], paused_reason=reason if reason in PAUSE_REASONS else "paused")
    log.info("trace_lab.golden.paused", run_id=run["id"], by=str(principal.user_id), reason=reason)
    return {"ok": True, "run": summary(await load_run(run["id"]))}


async def resume(run_id: str, *, principal: Any, usd_cap: float | None = None, org: str | None = None) -> dict[str, Any]:
    """The starter resumes a paused, interrupted or capped run, optionally with a higher cap (within
    run_max_usd() and what is left of the company's month). 409 run_in_progress when another run of the
    company opened meanwhile (one open golden_run job per company)."""
    run = await _owned_run(run_id, org)
    org = run["organization_id"]
    if run["job_status"] not in ("paused", "interrupted", "capped"):
        raise RunRefused("run_not_paused", status=run["job_status"])
    if str(principal.user_id) != str(run.get("started_by")):
        raise RunRefused("not_run_owner")
    cap = None
    if usd_cap is not None:
        cap = round(float(usd_cap), 2)
        if cap > run_max_usd():
            raise RunRefused("cap_too_high", usd_cap=cap, run_max_usd=run_max_usd())
        spent, month = float(run.get("job_usd_spent") or 0), await month_spent(org)
        if cap - spent > month_cap_usd() - month:
            raise RunRefused("monthly_cap", month_spent_usd=round(month, 4), month_cap_usd=month_cap_usd(), usd_cap=cap)
    try:
        back = await jobs.resume(run["job_id"], usd_cap=Decimal(str(cap)) if cap is not None else None)
    except jobs.JobConflict:
        raise RunRefused("run_in_progress") from None
    if back is None or await jobs.claim(run["job_id"]) is None:
        raise RunRefused("run_not_paused", status=await _job_status(run["job_id"], org))
    await _sync_status(run["id"])
    await _set_summary(run["id"], paused_reason=None)
    log.info("trace_lab.golden.resumed", run_id=run["id"], usd_cap=cap)
    return {"ok": True, "run": summary(await load_run(run["id"], org))}


async def cancel(run_id: str, *, principal: Any, org: str | None = None) -> dict[str, Any]:
    """Any reviewer of the company: the run ends as cancelled; the item in flight stops before its next
    message and is never stored or graded."""
    run = await _owned_run(run_id, org)
    if run["job_status"] not in jobs.OPEN_STATUSES:
        raise RunRefused("run_not_open", status=run["job_status"])
    who = getattr(principal, "email", None) or "a reviewer"
    await jobs.finish(run["job_id"], "cancelled", f"Cancelled by {who}.")
    await _sync_status(run["id"])
    await _set_summary(run["id"], paused_reason=None)
    log.info("trace_lab.golden.cancelled", run_id=run["id"], by=str(principal.user_id))
    return {"ok": True, "run": summary(await load_run(run["id"]))}


async def _regrade_items(run_id: str, only: str) -> list[tuple[str, int]]:
    """Items to grade again, read from each item's FINAL row (its last message): 'pending' = the grader did
    not finish; 'all' = every stored item except an infrastructure 'error'."""
    async with db.lab_engine().connect() as conn:
        rows = (await conn.execute(text(
            "SELECT DISTINCT ON (case_key, rep) case_key, rep, outcome FROM tracelab.golden_results"
            " WHERE run_id = CAST(:r AS uuid) ORDER BY case_key, rep, msg_idx DESC"), {"r": run_id})).all()
    keep = [(str(r[0]), int(r[1])) for r in rows if (r[2] == "pending" if only == "pending" else r[2] != "error")]
    return sorted(keep, key=lambda x: (x[1], x[0]))


async def _captured_for(run_id: str, case_key: str, rep: int) -> list[capture.CapturedTurn]:
    """The messages one item asked (rows with a turn), rebuilt as CapturedTurns from golden_results and the
    evidence."""
    async with db.lab_engine().connect() as conn:
        rows = (await conn.execute(text(
            "SELECT r.*, e.turn AS evidence FROM tracelab.golden_results r"
            " LEFT JOIN tracelab.evidence_turns e ON e.turn_id = r.turn_id"
            " WHERE r.run_id = CAST(:r AS uuid) AND r.case_key = :k AND r.rep = :rep AND r.turn_id IS NOT NULL"
            " ORDER BY r.msg_idx"),
            {"r": run_id, "k": case_key, "rep": rep})).mappings().all()
        out: list[capture.CapturedTurn] = []
        for r in rows:
            turn = r["evidence"] if isinstance(r["evidence"], dict) else json.loads(r["evidence"] or "{}")
            spans = [s if isinstance(s, dict) else json.loads(s) for (s,) in (await conn.execute(text(
                "SELECT span FROM tracelab.evidence_spans WHERE turn_id = :t ORDER BY seq"),
                {"t": r["turn_id"]})).all()] if r["turn_id"] else []
            refused = r["refused"] if isinstance(r["refused"], list) else json.loads(r["refused"] or "[]")
            out.append(capture.CapturedTurn(
                answer=r["answer"] or "", error=r["error"], turn_id=r["turn_id"] or "", spans=spans, ledger=[],
                usd=0.0, ms=int(r["ms"] or 0), refused=refused, cards=list(turn.get("cards") or []),
                question=r["question"] or "", session_id=r["session_id"] or "", turn=turn,
                ended_by=str(turn.get("ended_by") or ("error" if r["error"] else "workflow_completed")),
                history=[{"question": c.question, "answer": c.answer} for c in out]))
    return out


async def _regrade_run(job: GradeJob) -> str:
    """The golden run's own re-grade: one checkpoint per item, inside the job's cap."""
    p = job.params
    org = str(job.job.get("organization_id"))
    run = await load_run(str(p.get("run_id")), org)
    if run is None:
        return "failed"
    per = Decimal(str(round(grader_price() or 0.0, 6)))
    cases = {c["case_key"]: c for c in await _cases(org, str(run["set_id"]))}
    for key, rep in await _regrade_items(run["id"], str(p.get("only") or "pending")):
        if await job.heartbeat({"current": {"case_key": key, "rep": rep}}):
            return "paused"
        case = cases.get(key)
        if case is None:
            continue
        try:
            await jobs.reserve(job.job_id, per)
        except jobs.CapReached:
            return "capped"
        except jobs.JobNotRunning:
            return "paused"
        captured = await _captured_for(run["id"], key, rep)
        if not captured:
            await jobs.release(job.job_id, per)
            continue
        try:
            g = await _grade(case, captured[-1], earlier=[(c.question, c) for c in captured[:-1]])
        except Exception as exc:  # noqa: BLE001 - one item failing never stops the job
            log.warning("trace_lab.golden.regrade_failed", run_id=run["id"], case_key=key, error=str(exc)[:200])
            await jobs.release(job.job_id, per)
            continue
        outcome, grade = final_outcome(g, captured), grade_dict(g)
        usd = float((g.get("usd") if isinstance(g, dict) else getattr(g, "usd", 0)) or 0)
        async with db.lab_engine().begin() as conn:
            await conn.execute(text(
                "UPDATE tracelab.golden_results SET outcome = :o, grade = CAST(:g AS jsonb),"
                " graded_at = CASE WHEN :graded THEN now() END WHERE id = (SELECT id FROM tracelab.golden_results"
                " WHERE run_id = CAST(:r AS uuid) AND case_key = :k AND rep = :rep ORDER BY msg_idx DESC LIMIT 1)"),
                {"o": outcome, "g": _dumps(grade), "graded": outcome not in ("pending", "error"), "r": run["id"],
                 "k": key, "rep": rep})
            await jobs.checkpoint(conn, job.job_id, usd=Decimal(str(round(usd, 6))), released=per, done_delta=1,
                                  progress_patch={"last": {"case_key": key, "rep": rep, "outcome": outcome}})
    return "done"


async def _grade_worker(job_id: str) -> str:
    """Run one golden_grade job to its end: a registered handler when params["handler"] names one, the
    golden run's own re-grade for "golden" (or none), and 'failed' for a handler nobody registered."""
    job = await jobs.get(job_id, organization_id=None, platform=True)
    if job is None:
        return "failed"
    ctx = GradeJob(job_id=job_id, job=job)
    name = str(ctx.params.get("handler") or "golden")
    error = None
    try:
        if name in GRADE_HANDLERS:
            status = str(await GRADE_HANDLERS[name](ctx))
        elif name == "golden":
            status = await _regrade_run(ctx)
        else:
            status, error = "failed", f"No grader is registered for {name[:60]!r}."
    except Exception as exc:  # noqa: BLE001
        status, error = "failed", f"{type(exc).__name__}: {str(exc)[:200]}"
        log.warning("trace_lab.golden.grade_job_failed", job_id=job_id, error=error)
    if status not in jobs.FINISH_STATUSES:
        status = "failed"
    await jobs.finish(job_id, status, error)
    return status


async def start_grade_job(job_id: str) -> bool:
    """Claim a queued, paused or interrupted golden_grade job and run it in the background (no bearer is
    needed: grading calls only the model). False when someone else holds it or it has ended."""
    if await jobs.claim(job_id) is None:
        return False
    task = asyncio.create_task(_grade_worker(job_id))
    _GRADING.add(task)
    task.add_done_callback(_GRADING.discard)
    return True


async def _open_grade_job(run_id: str) -> dict[str, Any] | None:
    async with db.lab_engine().connect() as conn:
        row = (await conn.execute(text(
            "SELECT id::text AS id, status FROM tracelab.jobs WHERE scope_key = :s"
            " AND status IN ('queued', 'running', 'paused', 'interrupted')"), {"s": f"golden_grade:{run_id}"})).mappings().first()
    return dict(row) if row else None


async def regrade(run_id: str, *, principal: Any, only: str = "pending", org: str | None = None) -> dict[str, Any]:
    """Grade a run's answers again as a golden_grade job in this process, inside its own cap and the
    company's month: 'pending' (any reviewer) re-grades items whose grader did not finish; 'all' (the
    platform team) re-grades every answered item. A job a restart interrupted is picked up again."""
    run = await _owned_run(run_id, org)
    org = run["organization_id"]
    which = "all" if only == "all" else "pending"
    if which == "all" and getattr(principal, "role", None) != "superadmin":
        raise RunRefused("superadmins_only")
    if run["job_status"] in ("queued", "running"):
        raise RunRefused("run_still_running")
    await jobs.mark_interrupted()                  # a grade job whose process died gives its scope back
    open_job = await _open_grade_job(run["id"])
    if open_job is not None:
        if open_job["status"] == "interrupted":
            await jobs.resume(open_job["id"])
        if open_job["status"] in ("queued", "interrupted", "paused") and await start_grade_job(open_job["id"]):
            return {"ok": True, "job_id": open_job["id"], "resumed": True}
        raise RunRefused("regrade_in_progress")
    items = await _regrade_items(run["id"], which)
    if not items:
        raise RunRefused("nothing_to_regrade")
    model = grader_model()
    per = grader_price(model)
    if per is None:
        raise RunRefused("grader_unpriced", grader_model=model)
    est = round(len(items) * per, 4)
    spent, cap_month = await month_spent(org), month_cap_usd()
    if spent + est > cap_month:
        raise RunRefused("monthly_cap", month_spent_usd=round(spent, 4), month_cap_usd=cap_month, est_total_usd=est)
    cap = round(min(max(est * 1.5, 0.01), cap_month - spent), 4)
    try:
        job = await jobs.create(kind="golden_grade", scope_key=f"golden_grade:{run['id']}", organization_id=org,
                                params={"handler": "golden", "run_id": run["id"], "only": which},
                                usd_cap=Decimal(str(cap)), usd_estimate=Decimal(str(est)), total=len(items),
                                actor_id=str(principal.user_id), actor_email=getattr(principal, "email", None) or None)
    except jobs.JobConflict:
        raise RunRefused("regrade_in_progress") from None
    await start_grade_job(job["id"])
    log.info("trace_lab.golden.regrade_started", run_id=run["id"], job_id=job["id"], items=len(items), only=which)
    return {"ok": True, "job_id": job["id"], "items": len(items), "estimate_usd": est}


async def settle_after_restart() -> dict[str, int]:
    """At boot, after jobs.mark_interrupted (app.startup): every run's status follows its job again,
    reservations left by items that died with the old process are given back, and grading jobs a restart
    interrupted run again. Never raises; does nothing while Trace Lab is not ready."""
    out = {"runs": 0, "released": 0, "grading": 0}
    if not db.ready():
        return out
    try:
        async with db.lab_engine().begin() as conn:
            moved = (await conn.execute(text(
                "UPDATE tracelab.golden_runs r SET status = j.status, usd_spent = j.usd_spent,"
                " finished_at = CASE WHEN j.status IN ('done', 'failed', 'cancelled')"
                " THEN coalesce(r.finished_at, j.finished_at, now()) ELSE NULL END,"
                " summary = r.summary || jsonb_build_object('error', j.error, 'current', NULL)"
                " || CASE WHEN j.status = 'interrupted' THEN jsonb_build_object('paused_reason', 'interrupted')"
                " ELSE CAST('{}' AS jsonb) END"
                " FROM tracelab.jobs j WHERE j.id = r.job_id AND r.status IS DISTINCT FROM j.status RETURNING r.id"))).all()
            held = (await conn.execute(text(
                "SELECT id::text, usd_reserved FROM tracelab.jobs WHERE kind IN ('golden_run', 'golden_grade')"
                " AND status IN ('queued', 'running', 'paused', 'interrupted') AND usd_reserved > 0"))).all()
            grading = (await conn.execute(text(
                "SELECT id::text, status FROM tracelab.jobs WHERE kind = 'golden_grade'"
                " AND status IN ('queued', 'interrupted') ORDER BY created_at"))).all()
        out["runs"] = len(moved)
        for jid, reserved in held:
            await jobs.release(jid, Decimal(str(reserved)))
            out["released"] += 1
        for jid, status in grading:
            if status == "interrupted" and await jobs.resume(jid) is None:
                continue
            if await start_grade_job(jid):
                out["grading"] += 1
    except Exception as exc:  # noqa: BLE001 - boot never fails for this
        log.warning("trace_lab.golden.settle_failed", error=str(exc)[:300])
    if any(out.values()):
        log.info("trace_lab.golden.settled", **out)
    return out
