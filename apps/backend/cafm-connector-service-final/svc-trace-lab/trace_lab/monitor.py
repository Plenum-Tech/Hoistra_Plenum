"""Trace Lab monitoring (Phase 7; spec "Judges, code checks, monitoring, eval cases and release check" sections 4 and 6).

Code checks run on every chat answer in a window. Frozen judges run on a uniform random sample, random answers
first, so a capped batch still holds a uniform subset. Corrected prevalence comes from each judge's frozen TEST
pairs (J6) through stats.corrected_prevalence. Crossing a mode's threshold opens a review batch per company and
proposes eval cases. V1 runs by hand ("Run now", R18): nothing here schedules itself.

Golden Rule G0 (addendum 2.3): the chat's answers are READ through db.main_engine() (the read-only engine); every
Trace Lab table (batches, verdicts, jobs, judges) is on db.lab_engine(). No SQL names both databases: the window's
answers come from the chat, their ids go to the lab as `= ANY(CAST(:ids AS text[]))`. A judged answer is copied into
the evidence tables first (evidence.snapshot), so it outlives the chat's span purge.

The reads at the end (batches with the Due marker, one batch, the chart, the code-check trend, the prevalence the
failure report shows, and the automatic-vs-people trend) are on the lab database only. The one chat read among them is
the question of a flagged answer, taken through judges.turn_facts (the chat's row, else the evidence snapshot).
"""
from __future__ import annotations

import asyncio
import contextvars
import json
import math
import os
import random
import re
import uuid
from collections.abc import Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, AsyncIterator

import structlog
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from src.agents import llm_cost
from src.services.principal import Principal

from . import (checks, conversations, db, evidence, golden_views, grader_validation, jobs, judges, normalize, provenance,
               review_errors, sampling, stats)

log = structlog.get_logger(__name__)

DEFAULT_RATE = 0.25
DEFAULT_SEED = 7
CHUNK = 50                       # answers read and code-checked per checkpoint
WINDOW_PAGE = 500                # answers read from the chat per query when a window is paged
FLAGGED_REVIEW_MAX = 50
PROPOSE_CASES_MAX = 5
RE_ASK_MINUTES = 10
RE_ASK_JACCARD = 0.5
RETRY_PHRASES = ("try again", "that's wrong", "not what i asked")
GATE_REWRITES = ("corrected", "degraded", "blocked")
CHEAP_GROUPS = ("thumbs_down", "failed")
EST_EVIDENCE_CHARS = 12_000
EST_OUTPUT_TOKENS = 800
SNAPSHOT_REASON = "monitored"     # evidence_turns.reason of an answer first kept because a batch planned or flagged it
BATCH_LIST_MAX = 100             # batches one list call returns, at most
FLAGGED_PAGE_MAX = 200           # flagged answers one page of a batch returns, at most
QUESTION_SHOWN = 300             # characters of a question the batch page shows (redacted first, then cut)
CRITIQUE_SHOWN = 600             # characters of a critique the batch page shows (redacted first, then cut)


def monitor_usd_cap() -> float:
    return float(os.getenv("TRACE_LAB_MONITOR_USD_CAP", "5"))


def retention_days() -> int:
    """The span purge's own variable and default, through the one rule sampling uses, so a judge never reads
    purged spans and a review batch opened from a monitoring window never meets a different limit."""
    return sampling.retention_days()


class WatchError(Exception):
    """A refusal the Watch routes turn into the house envelope {detail: {reason, error, ...extra}}."""

    def __init__(self, status: int, reason: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.status = status
        self.reason = reason
        self.message = message
        self.extra = extra


def _field(obj: Any, key: str, default: Any = None) -> Any:
    """A NormalizedTurn field, whether the record is a mapping or a dataclass."""
    if isinstance(obj, Mapping):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def public(value: Any) -> Any:
    """A JSON-safe copy: uuid -> str, datetime/date -> ISO text, Decimal -> float."""
    if isinstance(value, Mapping):
        return {str(k): public(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [public(v) for v in value]
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def _utc(ts: datetime) -> datetime:
    return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts.astimezone(timezone.utc)


# ---- windows (section 4.1) ---------------------------------------------------------------------------

def last_complete_week(now: datetime) -> tuple[datetime, datetime]:
    """Monday 00:00 UTC of last week, and the Monday after it (exclusive)."""
    n = _utc(now)
    monday = datetime.combine(n.date() - timedelta(days=n.weekday()), time(0), tzinfo=timezone.utc)
    return monday - timedelta(days=7), monday


def period_label(start: datetime, end: datetime) -> str:
    s, e = _utc(start), _utc(end)
    if e - s == timedelta(days=7) and s.weekday() == 0 and s.time() == time(0):
        return stats.iso_week_label(s)
    return f"{s.date().isoformat()}..{(e - timedelta(microseconds=1)).date().isoformat()}"


def check_window(start: datetime, end: datetime, *, now: datetime, days: int | None = None) -> None:
    s, e, n = _utc(start), _utc(end), _utc(now)
    if e <= s:
        raise WatchError(422, "bad_window", "The window must end after it starts.")
    if e > n:
        raise WatchError(422, "window_in_future", "The window cannot end in the future.")
    d = days if days is not None else retention_days()
    if s < n - timedelta(days=d - 1):
        raise WatchError(409, "window_beyond_retention",
                         f"Answers older than {d - 1} days have lost their steps; start the window later.")


def weeks_back(now: datetime, n: int) -> list[tuple[str, datetime, datetime]]:
    """The last n ISO weeks in UTC, oldest first, ending with the current (partial) week."""
    current = last_complete_week(now)[1]
    out = []
    for i in range(max(1, int(n)) - 1, -1, -1):
        s = current - timedelta(days=7 * i)
        out.append((stats.iso_week_label(s), s, s + timedelta(days=7)))
    return out


# ---- risk groups (section 4.3) -----------------------------------------------------------------------

_TOKEN = re.compile(r"[a-z0-9']+")


def _tokens(q: str | None) -> set[str]:
    return set(_TOKEN.findall((q or "").lower()))


def jaccard(a: str | None, b: str | None) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def mark_re_asks(turns: Sequence[Mapping[str, Any]]) -> set[str]:
    """turn_ids the same person asked again, in the same observed conversation, within RE_ASK_MINUTES:
    the later question shares at least RE_ASK_JACCARD of its words, or starts with a retry phrase."""
    by_key: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for t in turns:
        if t.get("session_kind") != "observed" or not t.get("session_id") or not t.get("user_id"):
            continue
        by_key.setdefault((str(t["session_id"]), str(t["user_id"])), []).append(t)
    window = timedelta(minutes=RE_ASK_MINUTES)
    out: set[str] = set()
    for group in by_key.values():
        ordered = sorted(group, key=lambda t: (_utc(t["started_at"]), t["turn_id"]))
        for i, a in enumerate(ordered):
            for b in ordered[i + 1:]:
                if _utc(b["started_at"]) - _utc(a["started_at"]) > window:
                    break
                later = (b.get("question") or "").strip().lower()
                if later.startswith(RETRY_PHRASES) or jaccard(a.get("question"), b.get("question")) >= RE_ASK_JACCARD:
                    out.add(a["turn_id"])
                    break
    return out


RISK_GROUPS: dict[str, Callable[[Mapping[str, Any]], bool]] = {
    "thumbs_down": lambda t: t.get("feedback_rating") == "down",
    "failed": lambda t: t.get("ok") is False,
    "gate_rewrote": lambda t: any(o in GATE_REWRITES for o in (t.get("gate_outcomes") or ())),
    "code_flagged": lambda t: bool(t.get("code_failed")),
    "re_ask": lambda t: bool(t.get("re_ask")),
}
RISK_LABELS = {"thumbs_down": "rated down by the asker", "failed": "the answer failed",
               "gate_rewrote": "changed after checking", "code_flagged": "flagged by a code check",
               "re_ask": "asked again soon after"}


def select_turns(turns: Sequence[Mapping[str, Any]], rate: float,
                 groups: Mapping[str, Callable[[Mapping[str, Any]], bool]], seed: int = DEFAULT_SEED) -> dict:
    """A uniform sample of round(rate x N) answers in window order, every member of each risk group in input
    order, and to_judge = random first, then risk-only (the Cartwheel monitoring/sample.py contract)."""
    if not (0 < rate <= 1):
        raise ValueError("the sample rate must be above 0 and at most 1")
    for t in turns:
        if not t.get("turn_id"):
            raise ValueError("every answer in the window needs a turn_id")
    picked = random.Random(seed).sample(list(turns), round(rate * len(turns)))
    random_ids = [t["turn_id"] for t in picked]
    risk = {name: [t["turn_id"] for t in turns if pred(t)] for name, pred in groups.items()}
    seen: set[str] = set()
    to_judge: list[str] = []
    for tid in random_ids + [x for ids in risk.values() for x in ids]:
        if tid not in seen:
            seen.add(tid)
            to_judge.append(tid)
    return {"n_window": len(turns), "random": random_ids, "risk_groups": risk, "to_judge": to_judge}


# ---- estimate, threshold, review picks, chart, due (section 4.2 steps 5-7, 4.4, 4.6) -----------------------

def estimate_mode(*, verdicts: Sequence[Mapping[str, Any]], test_labels: Sequence[int], test_preds: Sequence[int],
                  threshold: float | None, n_planned: int,
                  prevalence: Callable[..., dict] | None = None) -> dict:
    """Corrected prevalence for one mode. Errors, undecidable and not-applicable verdicts are left out of
    both numerator and denominator and counted separately."""
    decided = [int(v["failure_present"]) for v in verdicts if v.get("status") == "decided"]
    out: dict[str, Any] = {
        "n_sample": len(decided),
        "n_error": sum(1 for v in verdicts if v.get("status") == "error"),
        "n_undecidable": sum(1 for v in verdicts if v.get("status") == "undecidable"),
        "n_not_applicable": sum(1 for v in verdicts if v.get("status") == "not_applicable"),
        "threshold": threshold, "raw": None, "corrected": None, "ci_low": None, "ci_high": None,
        "confidence": 0.95, "test_tpr": None, "test_tnr": None, "n_test": len(test_labels),
        "validity_warning": "", "crossed": False, "clearly_above": False, "reason": None,
    }
    if n_planned == 0:
        out["reason"] = "uniform sample is empty"
        return out
    if not decided:
        out["reason"] = "no answer in the sample has a verdict yet"
        return out
    if not test_labels:
        out["reason"] = "the judge has no held-out test result"
        return out
    fn = prevalence or stats.corrected_prevalence
    try:
        est = fn(decided, list(test_labels), list(test_preds))
    except ValueError as exc:
        out["reason"] = f"no estimate: {exc}"
        return out
    for key in ("raw", "corrected", "ci_low", "ci_high", "confidence", "test_tpr", "test_tnr", "n_test",
                "validity_warning"):
        if key in est:
            out[key] = est[key]
    c, lo = out["corrected"], out["ci_low"]
    out["crossed"] = c is not None and threshold is not None and c > threshold
    out["clearly_above"] = lo is not None and threshold is not None and lo > threshold
    return out


def estimate_ids(kind: str, plan: Mapping[str, Any], window_ids: Sequence[str]) -> list[str]:
    """LLM judge: the uniform sample only (risk groups never enter the estimate). Validated code judge:
    every answer in the window (a census)."""
    return list(plan.get("random") or []) if kind == "llm" else list(window_ids)


def flagged_for_review(plan: Mapping[str, Any], flagged: set[str], *, limit: int = FLAGGED_REVIEW_MAX) -> list[dict]:
    """Uniform-sample flags first, in sampled order, then risk-only flags in group order."""
    out: list[dict] = []
    seen: set[str] = set()
    random_ids = list(plan.get("random") or [])
    for tid in random_ids:
        if tid in flagged and tid not in seen:
            seen.add(tid)
            out.append({"turn_id": tid, "group": None})
    in_random = set(random_ids)
    for name, ids in (plan.get("risk_groups") or {}).items():
        for tid in ids:
            if tid in flagged and tid not in in_random and tid not in seen:
                seen.add(tid)
                out.append({"turn_id": tid, "group": name})
    return out[:limit]


def review_reason(judge_key: str, period: str, group: str | None) -> str:
    where = "uniform sample" if group is None else f"risk group {RISK_LABELS.get(group, group)}"
    return f"flagged by {judge_key} in {period}: {where}"[:300]


def chart_points(entries: Sequence[Mapping[str, Any]], *, periods: int = 12) -> dict:
    """entries: one per period (its latest done or capped batch), oldest first. y_max = 1.25 x the largest of
    the thresholds, interval tops and corrected values (Cartwheel monitoring/chart.py); 0.1 when all are zero."""
    keys = ("period_label", "window_start", "window_end", "batch_id", "raw", "corrected", "ci_low", "ci_high",
            "n_sample", "threshold", "crossed", "validity_warning")
    pts = [{k: e.get(k) for k in keys} for e in list(entries)[-max(1, int(periods)):]] if entries else []
    if not pts:
        return {"points": [], "threshold": None, "y_max": None}
    values = [v for p in pts for v in (p["ci_high"], p["corrected"], p["threshold"]) if v is not None]
    top = max(values) if values else 0.0
    return {"points": pts, "threshold": pts[-1]["threshold"], "y_max": round(1.25 * top, 4) if top > 0 else 0.1}


def due_marker(batches: Sequence[Mapping[str, Any]], *, now: datetime) -> dict:
    start, end = last_complete_week(now)
    covered = any(b.get("status") in ("done", "capped") and b.get("window_start") and b.get("window_end")
                  and _utc(b["window_start"]) <= start and _utc(b["window_end"]) >= end for b in batches)
    return {"period_label": stats.iso_week_label(start), "is_due": not covered}


def summarise_checks(rows: Sequence[Mapping[str, Any]], *, labels: Mapping[str, str], validated: set[str],
                     n_window: int) -> list[dict]:
    """rows: [{evaluator_key, status, failure_present, n}] grouped counts over the window's answers."""
    out = []
    for ek, label in labels.items():
        mine = [r for r in rows if r["evaluator_key"] == ek]

        def count(pred: Callable[[Mapping[str, Any]], bool]) -> int:
            return sum(int(r["n"]) for r in mine if pred(r))

        decided = count(lambda r: r["status"] == "decided")
        flagged = count(lambda r: r["status"] == "decided" and r["failure_present"] == 1)
        out.append({"evaluator_key": ek, "label": label, "n": n_window, "decided": decided, "flagged": flagged,
                    "undecidable": count(lambda r: r["status"] == "undecidable"),
                    "not_applicable": count(lambda r: r["status"] == "not_applicable"),
                    "error": count(lambda r: r["status"] == "error"),
                    "rate": round(flagged / decided, 4) if decided else None, "validated": ek in validated})
    return out


def call_usd(model: str, prompt_text: str, chars: int) -> Decimal | None:
    """One judge call (spec 3.11): input = ceil((len(prompt) + evidence chars) / 3.5) tokens, output 800.
    None when the model has no price: a cap cannot be enforced, so nothing is guessed."""
    usd = llm_cost.price_usd(model, {"input_tokens": math.ceil((len(prompt_text or "") + int(chars)) / 3.5),
                                     "output_tokens": EST_OUTPUT_TOKENS})
    return None if usd is None else Decimal(str(usd))


# ---- store: each call opens its own short connection (tests replace these) ----------------------------

TURNS = conversations.TURNS


@asynccontextmanager
async def _read_main() -> AsyncIterator[Any]:
    """A read-only, 30-second-bounded transaction on the read-only main engine (the chat's tables). A window of
    every company is read in pages, and each page sorts the window, so this is longer than the review reads."""
    async with db.main_engine().begin() as conn:
        await conn.execute(text("SET TRANSACTION READ ONLY"))
        await conn.execute(text("SET LOCAL statement_timeout = '30s'"))
        yield conn


def _window_sql(view_as: str = "TRUE") -> str:
    """Chat answers of one company, or of every company, in [start, end), in pages (section 4.1). Read from the
    chat's tables only (one database per statement). R27: an answer with no company is left out. The channel is
    inferred at read time (provenance.INFERRED_CHANNEL_SQL), so an upload, a correction, a report, a support turn
    and every golden run are left out. R20: `view_as` is sampling's clause that leaves out a superadmin's answers
    given while acting for another company."""
    return f"""
SELECT tt.turn_id, tt.session_id, CAST(tt.organization_id AS text) AS organization_id, CAST(tt.user_id AS text) AS user_id,
       tt.started_at, tt.ok, tt.feedback_rating
  FROM {TURNS} tt
 WHERE tt.started_at >= :start AND tt.started_at < :end
   AND tt.organization_id IS NOT NULL
   AND (CAST(:org AS uuid) IS NULL OR tt.organization_id = CAST(:org AS uuid))
   AND {provenance.INFERRED_CHANNEL_SQL} = 'chat'
   AND {view_as}
   AND (CAST(:after_ts AS timestamptz) IS NULL
        OR (tt.started_at, tt.turn_id) > (CAST(:after_ts AS timestamptz), CAST(:after_id AS text)))
 ORDER BY tt.started_at, tt.turn_id
 LIMIT :limit"""


async def _window_page(start: datetime, end: datetime, org: str | None, after: tuple[datetime, str] | None,
                       limit: int = WINDOW_PAGE) -> list[dict]:
    async with _read_main() as conn:
        accounts = await sampling._superadmins(conn)           # a hint: no accounts table reads as no rows
        view_as, view_params = sampling._view_as_clause(accounts)
        res = await conn.execute(text(_window_sql(view_as)), {
            **view_params, "start": start, "end": end, "org": org, "limit": limit,
            "after_ts": after[0] if after else None, "after_id": after[1] if after else None})
        return [dict(r) for r in res.mappings().all()]


async def window_turns(start: datetime, end: datetime, org: str | None) -> list[dict]:
    out: list[dict] = []
    after: tuple[datetime, str] | None = None
    while True:
        page = await _window_page(start, end, org, after, limit=WINDOW_PAGE)
        out.extend(page)
        if len(page) < WINDOW_PAGE:
            return out
        after = (page[-1]["started_at"], page[-1]["turn_id"])


def _evaluator_key(kind: str, judge_id: Any, check_key: str | None, check_version: Any) -> str:
    return f"judge:{judge_id}" if kind == "llm" else f"code:{check_key}@v{check_version}"


async def judges_in_use(mode_ids: Sequence[str] | None = None) -> list[dict]:
    """Per mode, the judge in use (spec 3.1), from the judge store: judges.in_use_judges() (the highest version that
    is frozen, has a finished test run and the decision 'use') plus judges.test_pairs() (its frozen J6 test pairs,
    label 1 = failure present). A retired mode is skipped. Monitoring has no judge query of its own, so the judge
    store's in-use rule and monitoring's can never drift apart."""
    wanted = {str(m) for m in mode_ids} if mode_ids else None
    rows = await judges.in_use_judges()
    out = []
    for mode_id, r in sorted(rows.items(), key=lambda kv: str(kv[1].get("mode_name") or "")):
        if (wanted is not None and str(mode_id) not in wanted) or r.get("mode_status") == "retired":
            continue
        labels, preds = await judges.test_pairs(str(r["id"]))
        thr = r.get("monitor_threshold")
        out.append({"judge_id": str(r["id"]), "mode_id": str(mode_id), "mode_name": r.get("mode_name"),
                    "judge_key": r.get("judge_key"), "kind": r.get("kind"), "model": r.get("model"),
                    "prompt_text": r.get("prompt_text"), "prompt_hash": r.get("prompt_hash"),
                    "check_key": r.get("check_key"), "check_version": r.get("check_version"),
                    "threshold": float(thr) if thr is not None else None,
                    "evaluator_key": _evaluator_key(r.get("kind"), r["id"], r.get("check_key"), r.get("check_version")),
                    "test_labels": list(labels), "test_preds": list(preds)})
    return out


def _snapshot(j: Mapping[str, Any]) -> dict:
    return {k: j.get(k) for k in ("judge_id", "judge_key", "mode_id", "mode_name", "kind", "prompt_hash", "threshold",
                                  "evaluator_key", "check_key", "check_version")}


def live_checks() -> list[dict]:
    return [{"check_key": c.key, "version": c.version, "evaluator_key": c.evaluator_key, "label": c.label}
            for c in checks.CHECKS.values() if c.scope == "live"]


async def _judge_row(judge_id: str) -> dict:
    """The judge's whole row from the judge store (id, model, prompt_text, prompt_hash, ...); {} when it is gone."""
    return await judges.fetch_judge(judge_id) or {}


async def _verdicts(evaluator_key: str, turn_ids: Sequence[str]) -> dict[str, dict]:
    if not turn_ids:
        return {}
    async with db.lab_engine().connect() as conn:
        rows = (await conn.execute(text(
            "SELECT turn_id, status, failure_present, detail, organization_id FROM tracelab.monitor_verdicts "
            "WHERE evaluator_key = :ek AND turn_id = ANY(CAST(:ids AS text[]))"),
            {"ek": evaluator_key, "ids": list(turn_ids)})).mappings().all()
    return {r["turn_id"]: dict(r, detail=_json(r["detail"])) for r in rows}


async def _predictions(prompt_hash: str, turn_ids: Sequence[str]) -> dict[str, dict]:
    """Decided predictions only, from the judge store's cache (judges.cached_predictions): what makes a re-run
    cost nothing (spec 3.6). An error row is not a verdict, so it is left out and retried."""
    if not turn_ids:
        return {}
    got = await judges.cached_predictions(prompt_hash, list(turn_ids))
    return {t: p for t, p in got.items() if p.get("status") == "decided"}


_UPSERT_VERDICT = """
INSERT INTO tracelab.monitor_verdicts (id, evaluator_key, turn_id, organization_id, turn_started_at, judge_id,
    mode_id, status, failure_present, detail, first_batch_id)
VALUES (CAST(:id AS uuid), :ek, :tid, CAST(:org AS uuid), :ts, CAST(:judge_id AS uuid), CAST(:mode_id AS uuid), :status,
    :fp, CAST(:detail AS jsonb), CAST(:batch_id AS uuid))
ON CONFLICT (evaluator_key, turn_id) DO UPDATE
   SET status = EXCLUDED.status, failure_present = EXCLUDED.failure_present, detail = EXCLUDED.detail,
       attempts = tracelab.monitor_verdicts.attempts + 1, updated_at = now()
 WHERE tracelab.monitor_verdicts.status <> 'decided'"""


def verdict_row(*, evaluator_key: str, turn: Mapping[str, Any], status: str, failure_present: int | None,
                detail: Mapping[str, Any], batch_id: str, judge_id: str | None = None,
                mode_id: str | None = None) -> dict:
    return {"id": str(uuid.uuid4()), "ek": evaluator_key, "tid": turn["turn_id"], "org": str(turn["organization_id"]),
            "ts": turn["started_at"], "judge_id": judge_id, "mode_id": mode_id, "status": status,
            "fp": failure_present, "detail": json.dumps(public(detail)), "batch_id": batch_id}


def _check_status(status: str) -> str:
    return "decided" if status in ("pass", "fail") else status


def _check_detail(result: Any) -> dict:
    return {"findings": [str(f)[:200] for f in (result.findings or ())][:8]}


def prediction_args(*, judge: Mapping[str, Any], turn_id: str, organization_id: str, reply: Any, inp: Any) -> dict:
    """The keyword arguments of judges.write_prediction for one judge call. The judge store owns that upsert (a
    decided row is never touched; usd and attempts accumulate), so monitoring only shapes the model's reply into
    its `pred` mapping. `judge` needs the judge's id (or judge_id), prompt_hash and model; `inp` is
    judges.judge_input's result."""
    decided = reply.status == "decided" and reply.failure_present in (0, 1)
    return {"judge": {"id": str(judge.get("id") or judge.get("judge_id")), "prompt_hash": judge["prompt_hash"],
                      "model": judge.get("model")},
            "turn_id": turn_id, "organization_id": organization_id,
            "pred": {"status": "decided" if decided else "error",
                     "failure_present": int(reply.failure_present) if decided else None,
                     "critique": (reply.critique or "")[:4000] or None,
                     "error": None if decided else (reply.error or "no verdict")[:500],
                     "usd": None if reply.usd is None else Decimal(str(reply.usd)),
                     "attempts": int(getattr(reply, "attempts", 1) or 1),
                     "input_sha": _field(inp, "sha"), "evidence_chars": int(_field(inp, "chars", 0) or 0),
                     "evidence_truncated": bool(_field(inp, "truncated", False))}}


async def _save(job_id: str, *, verdicts: Sequence[Mapping[str, Any]], prediction: Mapping[str, Any] | None = None,
                usd: Decimal | None = None, released: Decimal = Decimal(0), done_delta: int = 0,
                progress: Mapping[str, Any] | None = None) -> None:
    """Result rows and the job checkpoint in ONE lab transaction (jobs contract, spec 2.9). `prediction` is
    prediction_args(...): the cached judge reply goes through the judge store's judges.write_prediction in the same
    transaction. Eval-case grading calls this too, with no verdicts."""
    async with db.lab_engine().begin() as conn:
        if prediction is not None:
            await judges.write_prediction(conn, **prediction)
        if verdicts:
            await conn.execute(text(_UPSERT_VERDICT), [dict(v) for v in verdicts])
        await jobs.checkpoint(conn, job_id, usd=usd, released=released, done_delta=done_delta,
                              progress_patch=dict(progress) if progress else None)


async def _verdict_counts(evaluator_keys: Sequence[str], turn_ids: Sequence[str]) -> list[dict]:
    if not evaluator_keys or not turn_ids:
        return []
    async with db.lab_engine().connect() as conn:
        rows = (await conn.execute(text(
            "SELECT evaluator_key, status, failure_present, count(*) AS n FROM tracelab.monitor_verdicts "
            "WHERE evaluator_key = ANY(CAST(:eks AS text[])) AND turn_id = ANY(CAST(:ids AS text[])) GROUP BY 1, 2, 3"),
            {"eks": list(evaluator_keys), "ids": list(turn_ids)})).mappings().all()
    return [dict(r) for r in rows]


async def _validated_check_keys() -> set[str]:
    async with db.lab_engine().connect() as conn:
        rows = (await conn.execute(text("""
SELECT DISTINCT 'code:' || j.check_key || '@v' || j.check_version AS ek
  FROM tracelab.judges j
  JOIN tracelab.judge_runs r ON r.judge_id = j.id AND r.split = 'test' AND r.status = 'done'
 WHERE j.kind = 'code' AND j.status = 'frozen' AND j.decision = 'use'"""))).scalars().all()
    return set(rows)


_BATCH_SET = {"status": ":status", "plan": "CAST(:plan AS jsonb)", "results": "CAST(:results AS jsonb)",
              "usd": ":usd", "error": ":error"}


async def _insert_batch(row: Mapping[str, Any]) -> None:
    async with db.lab_engine().begin() as conn:
        await conn.execute(text("""
INSERT INTO tracelab.monitor_batches (id, organization_id, period_label, window_start, window_end, channel,
    sample_rate, seed, risk_groups, judges, checks, status, job_id, usd_cap, started_by, started_by_email)
VALUES (CAST(:id AS uuid), CAST(:org AS uuid), :period, :start, :end, 'chat', :rate, :seed, CAST(:groups AS text[]),
    CAST(:judges AS jsonb), CAST(:checks AS jsonb), 'queued', CAST(:job AS uuid), :cap, CAST(:by AS uuid), :email)"""),
            {"id": row["id"], "org": row["organization_id"], "period": row["period_label"],
             "start": row["window_start"], "end": row["window_end"], "rate": Decimal(str(row["sample_rate"])),
             "seed": int(row["seed"]), "groups": list(row["risk_groups"]), "judges": json.dumps(public(row["judges"])),
             "checks": json.dumps(public(row["checks"])), "job": row["job_id"], "cap": Decimal(str(row["usd_cap"])),
             "by": row["started_by"], "email": row["started_by_email"]})


def _decode(row: Mapping[str, Any]) -> dict:
    """A batch row with its jsonb columns decoded (a driver may hand them back as text)."""
    out = dict(row)
    for key in ("judges", "checks", "plan", "results"):
        if key in out:
            out[key] = _json(out[key])
    return out


async def _load_batch(batch_id: str) -> dict | None:
    try:
        uuid.UUID(str(batch_id))
    except ValueError:
        return None
    async with db.lab_engine().connect() as conn:
        row = (await conn.execute(text("SELECT * FROM tracelab.monitor_batches WHERE id = CAST(:id AS uuid)"),
                                  {"id": str(batch_id)})).mappings().first()
    return _decode(row) if row is not None else None


async def _update_batch(batch_id: str, *, finished: bool = False, **fields: Any) -> None:
    sets: list[str] = []
    params: dict[str, Any] = {"id": str(batch_id)}
    for key, value in fields.items():
        if key not in _BATCH_SET:
            raise ValueError(f"not a batch field: {key}")
        sets.append(f"{key} = {_BATCH_SET[key]}")
        params[key] = json.dumps(public(value)) if key in ("plan", "results") else value
    if finished:
        sets.append("finished_at = now()")
    if not sets:
        return
    async with db.lab_engine().begin() as conn:
        await conn.execute(text(f"UPDATE tracelab.monitor_batches SET {', '.join(sets)} WHERE id = CAST(:id AS uuid)"),
                           params)


def batch_public(row: Mapping[str, Any], *, with_plan: bool = False) -> dict:
    results = _json(row.get("results")) or None
    judges_snap = _json(row.get("judges")) or []
    out = {"id": row.get("id"), "organization_id": row.get("organization_id"),
           "scope": "platform" if row.get("organization_id") is None else "company",
           "period_label": row.get("period_label"), "window_start": row.get("window_start"),
           "window_end": row.get("window_end"), "sample_rate": row.get("sample_rate"), "seed": row.get("seed"),
           "risk_groups": list(row.get("risk_groups") or []),
           "judges": [{k: j.get(k) for k in ("judge_id", "judge_key", "mode_id", "mode_name", "kind", "threshold")}
                      for j in judges_snap],
           "status": row.get("status"), "usd": row.get("usd"), "usd_cap": row.get("usd_cap"),
           "started_by_email": row.get("started_by_email"), "started_at": row.get("started_at"),
           "finished_at": row.get("finished_at"), "error": row.get("error"), "results": results,
           "crossed": any(m.get("crossed") for m in ((results or {}).get("modes") or []))}
    if with_plan:
        out["plan"] = _json(row.get("plan"))
    return public(out)


# ---- plan preview and start (sections 4.1, 4.6, 7.3) -----------------------------------------------------

def _resolve_window(since: datetime | None, until: datetime | None, now: datetime) -> tuple[datetime, datetime]:
    if since is None and until is None:
        return last_complete_week(now)
    if since is None or until is None:
        raise WatchError(422, "bad_window", "Give both the start and the end of the window, or neither.")
    return _utc(since), _utc(until)


def _resolve_groups(names: Sequence[str] | None) -> list[str]:
    if names is None:
        return list(RISK_GROUPS)
    unknown = [n for n in names if n not in RISK_GROUPS]
    if unknown:
        raise WatchError(422, "unknown_risk_group", f"Unknown risk group: {', '.join(unknown)}.")
    return list(dict.fromkeys(names))


async def plan_preview(*, organization_id: str | None, since: datetime | None = None, until: datetime | None = None,
                       sample_rate: float = DEFAULT_RATE, seed: int = DEFAULT_SEED,
                       risk_groups: Sequence[str] | None = None, mode_ids: Sequence[str] | None = None,
                       now: datetime | None = None) -> dict:
    """What "Run now" would do, before it spends anything (D11): window, sample size and an estimate."""
    now = now or datetime.now(timezone.utc)
    start, end = _resolve_window(since, until, now)
    check_window(start, end, now=now)
    if not (0 < float(sample_rate) <= 1):
        raise WatchError(422, "bad_rate", "The sample rate must be above 0 and at most 1.")
    groups = _resolve_groups(risk_groups)
    turns = await window_turns(start, end, organization_id)
    plan = select_turns(turns, float(sample_rate), {g: RISK_GROUPS[g] for g in groups if g in CHEAP_GROUPS}, int(seed))
    in_use = await judges_in_use(mode_ids)
    warnings: list[str] = []
    unpriced: list[str] = []
    usd = Decimal(0)
    for j in in_use:
        if j["kind"] != "llm":
            continue
        have = await _verdicts(j["evaluator_key"], plan["to_judge"])
        done = {t for t, v in have.items() if v["status"] == "decided"}
        done |= set(await _predictions(j["prompt_hash"], [t for t in plan["to_judge"] if t not in done]))
        per = call_usd(j["model"], j["prompt_text"] or "", EST_EVIDENCE_CHARS)
        if per is None:
            unpriced.append(str(j["mode_name"]))
            warnings.append(f"The judge for {j['mode_name']} has no price, so its cost cannot be estimated.")
            continue
        usd += per * sum(1 for t in plan["to_judge"] if t not in done)
    if not in_use:
        warnings.append("No failure mode has a judge in use yet: only the code checks will run.")
    if any(g not in CHEAP_GROUPS for g in groups):
        warnings.append("Answers in the risk groups that need each answer's steps (changed after checking, flagged by "
                        "a code check, asked again soon after) are added when the batch runs; the cost estimate "
                        "leaves them out.")
    if plan["n_window"] and not plan["random"]:
        warnings.append("The uniform sample is empty at this rate, so no failure rate can be estimated.")
    n_random, n_to_judge = len(plan["random"]), len(plan["to_judge"])
    return {"period_label": period_label(start, end), "window": {"start": start.isoformat(), "end": end.isoformat()},
            "n_window": plan["n_window"], "n_random": n_random, "n_risk_only": n_to_judge - n_random,
            "n_to_judge": n_to_judge, "judges": [_snapshot(j) for j in in_use], "checks": live_checks(),
            "usd_estimate": float(round(usd, 4)), "sample_rate": float(sample_rate), "seed": int(seed),
            "risk_groups": groups, "warnings": warnings, "unpriced": unpriced}


def _is_active_batch_conflict(exc: IntegrityError) -> bool:
    return "ux_monitor_batches_active" in str(getattr(exc, "orig", exc))


#: The background workers of this process: a task is kept referenced until it ends.
_RUNNING: set[asyncio.Task] = set()


def run_in_background(batch_id: str) -> asyncio.Task:
    """Work a batch in this process: a task in an EMPTY context (nothing of the request that started it follows
    it, so no chat ledger or trace turn can receive its entries). work_batch never raises, and claims the batch's
    job itself."""
    task = asyncio.get_running_loop().create_task(work_batch(batch_id), context=contextvars.Context())
    _RUNNING.add(task)
    task.add_done_callback(_RUNNING.discard)
    return task


async def start_batch(*, organization_id: str | None, since: datetime | None, until: datetime | None,
                      sample_rate: float, seed: int, risk_groups: Sequence[str] | None,
                      mode_ids: Sequence[str] | None, usd_cap: float | None, actor_id: str,
                      actor_email: str | None, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    preview = await plan_preview(organization_id=organization_id, since=since, until=until, sample_rate=sample_rate,
                                 seed=seed, risk_groups=risk_groups, mode_ids=mode_ids, now=now)
    if preview.get("unpriced"):
        raise WatchError(409, "model_unpriced", "A judge model has no price, so the cost of this batch cannot be "
                         "capped. Choose other failure modes or price the model first.", modes=preview["unpriced"])
    cap = Decimal(str(usd_cap if usd_cap is not None else monitor_usd_cap()))
    estimate = Decimal(str(preview["usd_estimate"]))
    if estimate > cap:
        raise WatchError(409, "over_cap", f"This batch is estimated at ${float(estimate):.2f}, above its "
                         f"${float(cap):.2f} cap. Lower the sample rate or raise the cap.",
                         usd_estimate=float(estimate), usd_cap=float(cap))
    batch_id = str(uuid.uuid4())
    busy = "A monitoring batch for this scope is running or waiting to be resumed."
    try:
        job = await jobs.create(kind="monitor_batch", scope_key=f"monitor_batch:{organization_id or '*'}",
                                organization_id=organization_id, params={"batch_id": batch_id}, usd_cap=cap,
                                usd_estimate=estimate, total=preview["n_window"], actor_id=actor_id,
                                actor_email=actor_email)
    except jobs.JobConflict:
        raise WatchError(409, "batch_running", busy) from None
    job_id = str(job["id"])
    row = {"id": batch_id, "organization_id": organization_id, "period_label": preview["period_label"],
           "window_start": datetime.fromisoformat(preview["window"]["start"]),
           "window_end": datetime.fromisoformat(preview["window"]["end"]), "sample_rate": preview["sample_rate"],
           "seed": preview["seed"], "risk_groups": preview["risk_groups"], "judges": preview["judges"],
           "checks": preview["checks"], "status": "queued", "job_id": job_id, "usd_cap": cap,
           "started_by": actor_id, "started_by_email": actor_email}
    try:
        await _insert_batch(row)
    except Exception as exc:
        # The job already holds this scope: give it back, whatever stopped the batch from being recorded, or the
        # next "Run now" would meet batch_running until someone discards a job nobody can see.
        await jobs.finish(job_id, "failed", error="the monitoring batch could not be recorded")
        if isinstance(exc, IntegrityError) and _is_active_batch_conflict(exc):
            raise WatchError(409, "batch_running", busy) from None
        raise
    run_in_background(batch_id)
    log.info("trace_lab.monitor.batch_started", batch_id=batch_id, scope=organization_id or "platform",
             n_window=preview["n_window"], usd_estimate=float(estimate))
    return {"batch": batch_public({**row, "usd": 0, "started_at": now}), "job": {"id": job_id, "status": "queued"}}


# ---- threshold action (section 4.2 step 7) --------------------------------------------------------------

async def _flagged_ids(snap: Mapping[str, Any], plan: Mapping[str, Any]) -> set[str]:
    rows = await _verdicts(snap["evaluator_key"], list(plan.get("to_judge") or []))
    return {t for t, v in rows.items() if v["status"] == "decided" and v["failure_present"] == 1}


async def _review_batches_for(batch_id: str, mode_id: str) -> list[str]:
    async with db.lab_engine().connect() as conn:
        rows = (await conn.execute(text(
            "SELECT id FROM tracelab.sample_batches WHERE strategy = 'monitor_flagged' "
            "AND params ->> 'monitor_batch_id' = :b AND params ->> 'mode_id' = :m ORDER BY created_at, id"),
            {"b": str(batch_id), "m": str(mode_id)})).scalars().all()
    return [str(r) for r in rows]


async def _turn_rows(turn_ids: Sequence[str]) -> dict[str, dict]:
    """Company and session of each answer the chat still has, read from the chat's tables (main engine)."""
    if not turn_ids:
        return {}
    async with _read_main() as conn:
        rows = (await conn.execute(text(
            f"SELECT turn_id, CAST(organization_id AS text) AS organization_id, session_id FROM {TURNS} "
            "WHERE turn_id = ANY(CAST(:ids AS text[]))"), {"ids": list(turn_ids)})).mappings().all()
    return {r["turn_id"]: dict(r) for r in rows}


async def open_review_batches(batch_id: str, mode_id: str, *, actor_id: str, actor_email: str | None,
                              batch: Mapping[str, Any] | None = None, plan: Mapping[str, Any] | None = None,
                              org: str | None = None) -> list[str]:
    """The answers this judge flagged in the batch (uniform-sample flags first, then risk-only, at most
    FLAGGED_REVIEW_MAX), as one review batch per company, opened at most once per batch and mode. Each company's
    batch is recorded by the review module's sampling.create_batch (strategy 'monitor_flagged'): it keeps only that
    company's answers that are not in one of its batches already, copies them into the evidence tables, never counts
    toward prevalence, and asks for one review each. Monitoring writes no sample_batches or sample_items row.

    `org` is the company the caller acts for (Phase 8): the batch must be that company's own, else it is not found
    (the same 404 as an unknown batch, so nothing says which companies have batches), checked before anything else is
    read. None is the platform view, which opens any batch. A company's own batch only ever files that company's
    answers: an answer of another company in its plan is left out, never filed under the wrong company."""
    batch = batch or await _load_batch(batch_id)
    if batch is None or (org is not None and str(batch.get("organization_id") or "") != str(org)):
        raise WatchError(404, "not_found", "That monitoring batch was not found.")
    own = str(batch["organization_id"]) if batch.get("organization_id") else None
    snap = next((s for s in batch.get("judges") or [] if str(s["mode_id"]) == str(mode_id)), None)
    if snap is None:
        raise WatchError(404, "not_found", "That failure mode was not part of this batch.")
    existing = await _review_batches_for(str(batch["id"]), str(mode_id))
    if existing:
        return existing
    plan = plan or batch.get("plan") or {}
    picks = flagged_for_review(plan, await _flagged_ids(snap, plan))
    if not picks:
        raise WatchError(409, "nothing_flagged", "No answer was flagged for this failure mode in this batch.")
    rows = await _turn_rows([p["turn_id"] for p in picks])
    by_org: dict[str, list[dict]] = {}
    for p in picks:
        row = rows.get(p["turn_id"])
        if not row or not row.get("organization_id") or (own is not None and str(row["organization_id"]) != own):
            continue
        by_org.setdefault(str(row["organization_id"]), []).append({
            "turn_id": p["turn_id"], "session_id": row.get("session_id"),
            "reason": review_reason(snap.get("judge_key") or "", batch.get("period_label") or "", p["group"])})
    actor = Principal(user_id=uuid.UUID(str(actor_id)), email=actor_email or "", organization_id=None,
                      role="superadmin", can_ingest=False, building_ids=None)
    created: list[str] = []
    for org, items in by_org.items():
        req = {"strategy": "monitor_flagged", "plan_step": "extra", "items": items, "seed": int(batch["seed"]),
               "since": batch["window_start"], "until": batch["window_end"],
               "params": {"monitor_batch_id": str(batch["id"]), "mode_id": str(snap["mode_id"]),
                          "judge_id": str(snap["judge_id"])}}
        try:
            out = await sampling.create_batch(actor, org, req)
        except (review_errors.Invalid, review_errors.Conflict) as exc:
            if exc.reason in ("pool_empty", "pool_changed"):
                continue                      # this company's flagged answers are all in one of its batches already
            raise
        created.append(str(out["batch"]["id"]))
    if not created:
        raise WatchError(409, "nothing_flagged", "Every flagged answer is already in a review batch.")
    log.info("trace_lab.monitor.review_batches", batch_id=str(batch["id"]), mode_id=str(mode_id), batches=len(created))
    return created


async def _propose_cases(batch: Mapping[str, Any], mode_id: str, turn_ids: Sequence[str]) -> list[str]:
    from . import eval_cases   # imported when needed: eval_cases imports this module

    return await eval_cases.propose_from_monitor(batch=batch, mode_id=mode_id, turn_ids=turn_ids)


async def _act_on_crossings(batch: Mapping[str, Any], plan: Mapping[str, Any], results: dict) -> None:
    """At most once per batch per mode: a review batch per company, and up to PROPOSE_CASES_MAX uniform-sample
    flags as proposed eval cases (a person confirms each one). No email in V1. The estimates are already computed
    and are kept whatever happens here: a refused review batch or a failed proposal is logged and left empty, never
    raised, so a batch that has spent its judge calls does not end as failed."""
    actor = str(batch["started_by"]) if batch.get("started_by") else None
    for m in results.get("modes") or []:
        if not m.get("crossed"):
            continue
        snap = next((s for s in batch.get("judges") or [] if str(s["mode_id"]) == str(m["mode_id"])), None)
        if snap is None:
            continue
        if actor:
            try:
                m["review_batch_ids"] = await open_review_batches(str(batch["id"]), str(m["mode_id"]), actor_id=actor,
                                                                  actor_email=batch.get("started_by_email"),
                                                                  batch=batch, plan=plan)
            except (WatchError, review_errors.ReviewError) as exc:
                log.info("trace_lab.monitor.no_review_batch", batch_id=str(batch["id"]), mode_id=str(m["mode_id"]),
                         reason=exc.reason)
        uniform_flags = [p["turn_id"] for p in flagged_for_review(plan, await _flagged_ids(snap, plan))
                         if p["group"] is None][:PROPOSE_CASES_MAX]
        if uniform_flags:
            try:
                m["proposed_case_ids"] = await _propose_cases(batch, str(m["mode_id"]), uniform_flags)
            except Exception as exc:  # noqa: BLE001 - the estimates stand; the cases can be proposed by hand
                log.warning("trace_lab.monitor.cases_not_proposed", batch_id=str(batch["id"]),
                            mode_id=str(m["mode_id"]), error=str(exc)[:300] or type(exc).__name__)


# ---- the batch worker (section 4.2; job kind monitor_batch) ---------------------------------------------

async def work_batch(batch_id: str) -> None:
    """The monitor_batch job body. Never raises into the event loop: a failure is recorded on the batch and
    the job. A resumed batch re-runs the free code checks and reuses its stored plan and decided verdicts."""
    batch = await _load_batch(batch_id)
    if batch is None or not batch.get("job_id"):
        return
    job_id = str(batch["job_id"])
    if await jobs.claim(job_id, worker=jobs.worker_id()) is None:
        return
    await _update_batch(batch_id, status="running")
    try:
        status = await _run(batch, job_id)
    except Exception as exc:  # noqa: BLE001 - the batch and its job record the failure; nothing is lost
        log.warning("trace_lab.monitor.batch_failed", batch_id=batch_id, error=str(exc)[:300] or type(exc).__name__)
        note = str(exc)[:2000] or type(exc).__name__
        await _update_batch(batch_id, status="failed", error=note, finished=True)
        await jobs.finish(job_id, "failed", error=note)
        return
    await _update_batch(batch_id, status=status, finished=True)
    await jobs.finish(job_id, status)
    log.info("trace_lab.monitor.batch_finished", batch_id=batch_id, status=status)


async def _run(batch: Mapping[str, Any], job_id: str) -> str:
    org = str(batch["organization_id"]) if batch.get("organization_id") else None
    turns = await window_turns(batch["window_start"], batch["window_end"], org)
    facts = await _check_all(batch, job_id, turns)
    plan = batch.get("plan")
    if not plan:
        groups = {g: RISK_GROUPS[g] for g in batch.get("risk_groups") or [] if g in RISK_GROUPS}
        plan = select_turns(facts, float(batch["sample_rate"]), groups, int(batch["seed"]))
        await _update_batch(str(batch["id"]), plan=plan)
    by_id = {f["turn_id"]: f for f in facts}
    await _keep_evidence(batch, plan, by_id)
    capped = await _judge_sample(batch, job_id, plan, by_id)
    results = await _estimate_all(batch, plan, facts)
    await _act_on_crossings(batch, plan, results)
    job = await jobs.get(job_id, organization_id=org)
    await _update_batch(str(batch["id"]), results=results, usd=Decimal(str((job or {}).get("usd_spent") or 0)))
    return "capped" if capped else "done"


async def _keep_evidence(batch: Mapping[str, Any], plan: Mapping[str, Any],
                         by_id: Mapping[str, Mapping[str, Any]]) -> None:
    """Copy every monitored answer into the evidence tables, one company at a time (G0 addendum 2.6), before the
    first paid call: its verdict stays readable after the chat's 90-day span purge. Monitored means: every answer
    the plan names (to_judge: the uniform sample and the risk groups) when the batch has any judge, LLM or code, and
    every answer a live code check flagged (a decided fail), judge or no judge. The rest of the window (answers
    that passed every check and no judge reads) keeps counts only: copying every chat answer of a platform-wide
    week into the lab database would be very large, and nobody reviews a passing answer. Idempotent: an answer
    already kept is not read again. A failure stops the batch: no call is paid for an answer whose evidence was not
    kept."""
    wanted: list[str] = list(plan.get("to_judge") or []) if batch.get("judges") else []
    wanted += [tid for tid, fact in by_id.items() if fact.get("code_failed")]
    by_org: dict[str, list[str]] = {}
    seen: set[str] = set()
    for tid in wanted:
        fact = by_id.get(tid)
        if tid in seen or not fact or not fact.get("organization_id"):
            continue
        seen.add(tid)
        by_org.setdefault(str(fact["organization_id"]), []).append(tid)
    for org, ids in by_org.items():
        await evidence.snapshot(ids, organization_id=org, reason=SNAPSHOT_REASON)


async def _check_all(batch: Mapping[str, Any], job_id: str, turns: Sequence[Mapping[str, Any]]) -> list[dict]:
    """Every live code check on every answer in the window: free, one checkpoint per chunk of CHUNK."""
    live = [c for c in checks.CHECKS.values() if c.scope == "live"]
    batch_id = str(batch["id"])
    facts: list[dict] = []
    for i in range(0, len(turns), CHUNK):
        chunk = list(turns[i:i + CHUNK])
        loaded = await judges.load_judge_turns([t["turn_id"] for t in chunk])     # {turn_id: NormalizedTurn}
        rows: list[dict] = []
        for t in chunk:
            fact = dict(t)
            nt = loaded.get(t["turn_id"])
            fact["session_kind"] = _field(nt, "session_kind", "inferred") if nt is not None else "inferred"
            if nt is None:
                fact.update(question=None, gate_outcomes=(), code_failed=False)
                rows += [verdict_row(evaluator_key=c.evaluator_key, turn=t, status="undecidable", failure_present=None,
                                     detail={"findings": ["the answer could not be read"]}, batch_id=batch_id)
                         for c in live]
            else:
                results = checks.run_live_checks(nt)
                fact.update(question=_field(nt, "question"),
                            gate_outcomes=tuple(_field(g, "outcome") for g in (_field(nt, "gates") or ())),
                            code_failed=any(r.status == "fail" for r in results.values()))
                rows += [verdict_row(evaluator_key=checks.CHECKS[key].evaluator_key, turn=t,
                                     status=_check_status(r.status), failure_present=r.failure_present,
                                     detail=_check_detail(r), batch_id=batch_id) for key, r in results.items()]
            facts.append(fact)
        await _save(job_id, verdicts=rows, done_delta=len(chunk),
                    progress={"phase": "checks", "checked": i + len(chunk), "n_window": len(turns)})
    re_asked = mark_re_asks(facts)
    for f in facts:
        f["re_ask"] = f["turn_id"] in re_asked
    return facts


async def _judge_sample(batch: Mapping[str, Any], job_id: str, plan: Mapping[str, Any],
                        by_id: Mapping[str, Mapping[str, Any]]) -> bool:
    """Each LLM judge in use, over to_judge in order: reuse a decided verdict, else copy a decided prediction,
    else call the judge. True when the cap stopped it."""
    for snap in batch.get("judges") or []:
        if snap.get("kind") != "llm":
            continue
        judge = await _judge_row(str(snap["judge_id"]))
        if not judge:
            log.warning("trace_lab.monitor.judge_missing", batch_id=str(batch["id"]), judge_id=str(snap["judge_id"]))
            continue
        order = [t for t in plan.get("to_judge") or [] if t in by_id]
        have = await _verdicts(snap["evaluator_key"], order)
        decided = {t for t, v in have.items() if v["status"] == "decided"}
        cached = await _predictions(judge["prompt_hash"], [t for t in order if t not in decided])
        copies = [verdict_row(evaluator_key=snap["evaluator_key"], turn=by_id[t], status="decided",
                              failure_present=int(p["failure_present"]),
                              detail={"critique": (p.get("critique") or "")[:1800]}, batch_id=str(batch["id"]),
                              judge_id=str(snap["judge_id"]), mode_id=str(snap["mode_id"])) for t, p in cached.items()]
        if copies:
            await _save(job_id, verdicts=copies, progress={"phase": "judging", "judge": snap.get("judge_key")})
        todo = [t for t in order if t not in decided and t not in cached]
        if await _call_judge(batch, job_id, snap, judge, todo, by_id):
            return True
    return False


async def _call_judge(batch: Mapping[str, Any], job_id: str, snap: Mapping[str, Any], judge: Mapping[str, Any],
                      todo: Sequence[str], by_id: Mapping[str, Mapping[str, Any]]) -> bool:
    """Calls the frozen judge on todo in order, at most JUDGE_CONCURRENCY in flight. Dispatch stops at the cap, so
    what was judged is a prefix of the order: the uniform sample first. A call or a save that raises stops further
    dispatch too (no paid call whose result could not be kept); the calls already in flight finish and the first
    failure is re-raised. True when the cap stopped it."""
    sem = asyncio.Semaphore(max(1, int(judges.JUDGE_CONCURRENCY)))
    state = {"capped": False, "failed": False}

    async def call(tid: str) -> None:
        fact = by_id[tid]
        common = {"evaluator_key": snap["evaluator_key"], "turn": fact, "batch_id": str(batch["id"]),
                  "judge_id": str(snap["judge_id"]), "mode_id": str(snap["mode_id"])}
        turn = (await judges.load_judge_turns([tid])).get(tid)                  # {turn_id: NormalizedTurn}
        if turn is None:
            await _save(job_id, verdicts=[verdict_row(status="error", failure_present=None,
                                                      detail={"error": "the answer could not be read"}, **common)])
            return
        inp = judges.judge_input(turn)
        chars = int(_field(inp, "chars", 0) or 0)
        est = call_usd(judge["model"], judge["prompt_text"] or "", chars) or Decimal(0)
        try:
            await jobs.reserve(job_id, est)
        except jobs.CapReached:
            state["capped"] = True
            return
        try:
            reply = await judges.ASK(judge["model"], judge["prompt_text"], _field(inp, "text"))
        except Exception as exc:  # noqa: BLE001 - a failed call is an error verdict, never a pass
            reply = SimpleNamespace(status="error", failure_present=None, critique="", usd=None,
                                    error=type(exc).__name__)
        prediction = prediction_args(judge=judge, turn_id=tid, organization_id=str(fact["organization_id"]),
                                     reply=reply, inp=inp)                      # written through judges.write_prediction
        pred = prediction["pred"]
        detail = ({"critique": (reply.critique or "")[:1800]} if pred["status"] == "decided"
                  else {"error": pred["error"]})
        await _save(job_id, verdicts=[verdict_row(status=pred["status"], failure_present=pred["failure_present"],
                                                  detail=detail, **common)],
                    prediction=prediction, usd=pred["usd"], released=est,
                    progress={"phase": "judging", "judge": snap.get("judge_key")})

    async def one(tid: str) -> None:
        async with sem:
            if state["capped"] or state["failed"]:
                return
            try:
                await call(tid)
            except BaseException:
                state["failed"] = True
                raise

    results = await asyncio.gather(*(one(t) for t in todo), return_exceptions=True)
    failures = [r for r in results if isinstance(r, BaseException)]
    if failures:
        raise failures[0]
    return state["capped"]


async def _estimate_all(batch: Mapping[str, Any], plan: Mapping[str, Any], facts: Sequence[Mapping[str, Any]]) -> dict:
    window_ids = [f["turn_id"] for f in facts]
    to_judge = list(plan.get("to_judge") or [])
    snaps = list(batch.get("judges") or [])
    in_use = {j["judge_id"]: j for j in await judges_in_use([s["mode_id"] for s in snaps])} if snaps else {}
    modes = []
    for snap in snaps:
        j = in_use.get(str(snap["judge_id"]))
        labels, preds = (j["test_labels"], j["test_preds"]) if j else await judges.test_pairs(str(snap["judge_id"]))
        ids = estimate_ids(snap["kind"], plan, window_ids)
        scope = to_judge if snap["kind"] == "llm" else window_ids          # ids is inside scope: one read serves both
        rows = await _verdicts(snap["evaluator_key"], scope)
        sample = [rows[t] for t in ids if t in rows]
        est = await asyncio.to_thread(estimate_mode, verdicts=sample, test_labels=labels, test_preds=preds,
                                      threshold=snap.get("threshold"), n_planned=len(ids))
        flagged = {t for t, v in rows.items() if v["status"] == "decided" and v["failure_present"] == 1}
        risk = {g: {"n": len(members), "flagged": sum(1 for t in members if t in flagged)}
                for g, members in (plan.get("risk_groups") or {}).items()}
        modes.append({"mode_id": str(snap["mode_id"]), "mode_name": snap.get("mode_name"),
                      "judge_id": str(snap["judge_id"]), "judge_key": snap.get("judge_key"), "kind": snap["kind"],
                      "evaluator_key": snap["evaluator_key"], **est, "flagged": len(flagged), "risk_groups": risk,
                      "review_batch_ids": [], "proposed_case_ids": []})
    labels_by_key = {c["evaluator_key"]: c["label"] for c in batch.get("checks") or []}
    counts = await _verdict_counts(list(labels_by_key), window_ids)
    n_random = len(plan.get("random") or [])
    return {"n_window": len(window_ids), "n_random": n_random, "n_risk_only": len(to_judge) - n_random,
            "modes": modes,
            "code_checks": summarise_checks(counts, labels=labels_by_key, validated=await _validated_check_keys(),
                                            n_window=len(window_ids))}


async def _settle(rows: Sequence[Mapping[str, Any]]) -> list[dict]:
    """Settle on read (instead of a startup hook): a queued or running batch whose job was interrupted,
    failed or cancelled, or never claimed for STALE_AFTER_S, reads as interrupted or failed."""
    out: list[dict] = []
    for r in rows:
        r = dict(r)
        if r.get("status") in ("queued", "running") and r.get("job_id"):
            job = await jobs.get(str(r["job_id"]),
                                 organization_id=str(r["organization_id"]) if r.get("organization_id") else None)
            js = (job or {}).get("status")
            created = (job or {}).get("created_at")
            stale_queue = (js == "queued" and created is not None
                           and _utc(created) < datetime.now(timezone.utc) - timedelta(seconds=int(jobs.STALE_AFTER_S)))
            new = "interrupted" if js == "interrupted" or stale_queue else ("failed" if js in ("failed", "cancelled") else None)
            if new:
                await _update_batch(str(r["id"]), status=new)
                r["status"] = new
        out.append(r)
    return out


async def resume_batch(batch_id: str, *, org: str | None) -> dict:
    row = await _load_batch(batch_id)
    if row is None or str(row.get("organization_id") or "") != str(org or ""):
        raise WatchError(404, "not_found", "That monitoring batch was not found.")
    row = (await _settle([row]))[0]
    if row["status"] != "interrupted":
        raise WatchError(409, "not_interrupted", "Only an interrupted batch can be resumed.")
    try:
        await jobs.resume(str(row["job_id"]))      # None when the job is already queued (a stale queue): nothing to put back
    except jobs.JobConflict:
        raise WatchError(409, "batch_running", "A monitoring batch for this scope is running or waiting to be resumed.") from None
    await _update_batch(str(row["id"]), status="queued")
    run_in_background(str(row["id"]))
    return {"batch": batch_public(dict(row, status="queued"))}


# ---- reads: batches, one batch, the chart, the code-check trend, prevalence (spec 4.4, 7.3) ----------------------
# Every read below is on the lab database (db.lab_engine()). The chat is touched only to read the question of a
# flagged answer (judges.turn_facts: the chat's row while it holds the turn, else the evidence snapshot), in a read
# of its own: no statement names both databases.

@asynccontextmanager
async def _read_lab() -> AsyncIterator[Any]:
    """A read-only, 30-second-bounded transaction on the lab database, for the reads that scan weeks of rows."""
    async with db.lab_engine().begin() as conn:
        await conn.execute(text("SET TRANSACTION READ ONLY"))
        await conn.execute(text("SET LOCAL statement_timeout = '30s'"))
        yield conn


#: A batch's columns without `plan`: the plan names every answer of the sample (thousands of ids for a platform week), and
#: a list or a settle never reads it. `_load_batch` reads the whole row.
_LIST_COLS = ("id, organization_id, period_label, window_start, window_end, channel, sample_rate, seed, risk_groups, "
              "judges, checks, results, status, job_id, usd, usd_cap, started_by, started_by_email, started_at, "
              "finished_at, error")
_DUE_COLS = "id, organization_id, period_label, window_start, window_end, status, job_id, started_at"


async def _batch_rows(org: str | None, limit: int, before: datetime | None) -> list[dict]:
    async with db.lab_engine().connect() as conn:
        rows = (await conn.execute(text(f"""
SELECT {_LIST_COLS} FROM tracelab.monitor_batches
 WHERE (CAST(:org AS uuid) IS NULL OR organization_id = CAST(:org AS uuid))
   AND (CAST(:before AS timestamptz) IS NULL OR started_at < CAST(:before AS timestamptz))
 ORDER BY started_at DESC, id LIMIT :limit"""), {"org": org, "before": before,
                                                 "limit": max(1, min(int(limit), BATCH_LIST_MAX))})).mappings().all()
    return [_decode(r) for r in rows]


async def _scope_batches(org: str | None) -> list[dict]:
    """The latest batches of exactly this scope (the platform when org is None), for the Due marker."""
    async with db.lab_engine().connect() as conn:
        rows = (await conn.execute(text(f"""
SELECT {_DUE_COLS} FROM tracelab.monitor_batches
 WHERE organization_id IS NOT DISTINCT FROM CAST(:org AS uuid)
 ORDER BY started_at DESC, id LIMIT 20"""), {"org": org})).mappings().all()
    return [_decode(r) for r in rows]


async def list_batches(*, org: str | None, limit: int = 20, before: datetime | None = None,
                       now: datetime | None = None) -> dict:
    """R19: a company reads its own batches; the platform owner (org None) reads every batch. The Due marker is
    for exactly the scope viewed."""
    rows = await _settle(await _batch_rows(org, limit, before))
    scope_rows = await _settle(await _scope_batches(org))
    return {"batches": [batch_public(r) for r in rows],
            "due": due_marker(scope_rows, now=now or datetime.now(timezone.utc))}


async def _flagged_rows(evaluator_keys: Sequence[str], turn_ids: Sequence[str], *, org: str | None, offset: int,
                        limit: int) -> list[dict]:
    """The answers a batch's evaluators flagged, oldest answer first. The verdicts are read in the lab database; the
    question of each comes from judges.turn_facts (the chat's row, else the evidence snapshot), so a flag stays
    readable after the chat's span purge. A company reader sees only its own company's verdicts."""
    if not evaluator_keys or not turn_ids:
        return []
    async with db.lab_engine().connect() as conn:
        rows = (await conn.execute(text("""
SELECT v.turn_id, v.evaluator_key, v.detail, CAST(v.organization_id AS text) AS organization_id,
       v.turn_started_at AS started_at
  FROM tracelab.monitor_verdicts v
 WHERE v.evaluator_key = ANY(CAST(:eks AS text[])) AND v.turn_id = ANY(CAST(:ids AS text[]))
   AND v.status = 'decided' AND v.failure_present = 1
   AND (CAST(:org AS uuid) IS NULL OR v.organization_id = CAST(:org AS uuid))
 ORDER BY v.turn_started_at, v.turn_id, v.evaluator_key
 OFFSET :offset LIMIT :limit"""), {"eks": list(evaluator_keys), "ids": list(turn_ids), "org": org,
                                   "offset": max(0, int(offset)),
                                   "limit": max(1, min(int(limit), FLAGGED_PAGE_MAX))})).mappings().all()
    facts = await judges.turn_facts([r["turn_id"] for r in rows]) if rows else {}
    out = []
    for r in rows:
        detail = _json(r["detail"]) or {}
        findings = detail.get("findings") if isinstance(detail, Mapping) else None
        critique = (detail.get("critique") if isinstance(detail, Mapping) else None) or (
            "; ".join(str(f) for f in findings) if isinstance(findings, list) else None)
        out.append({"turn_id": r["turn_id"], "evaluator_key": r["evaluator_key"], "critique": critique,
                    "organization_id": r["organization_id"], "started_at": r["started_at"],
                    "question": (facts.get(r["turn_id"]) or {}).get("question")})
    return out


def _shown(text_: Any, limit: int) -> str:
    """Text a person reads on the batch page: secret values redacted FIRST, then cut, so a secret that straddles the
    cut cannot survive in part."""
    return normalize.redact_text(str(text_ or ""))[:limit]


async def batch_detail(batch_id: str, *, org: str | None, mode_id: str | None = None, offset: int = 0,
                       limit: int = 50) -> dict:
    row = await _load_batch(batch_id)
    if row is None or (org is not None and str(row.get("organization_id") or "") != str(org)):
        raise WatchError(404, "not_found", "That monitoring batch was not found.")
    row = (await _settle([row]))[0]
    plan = _json(row.get("plan")) or {}
    snaps = [s for s in (_json(row.get("judges")) or []) if mode_id is None or str(s["mode_id"]) == str(mode_id)]
    in_random = set(plan.get("random") or [])
    group_of: dict[str, str] = {}
    for name, ids in (plan.get("risk_groups") or {}).items():
        for tid in ids:
            group_of.setdefault(tid, name)
    names = {s["evaluator_key"]: s.get("mode_name") for s in snaps}
    found = await _flagged_rows([s["evaluator_key"] for s in snaps], list(plan.get("to_judge") or []), org=org,
                                offset=offset, limit=limit)
    flagged = [{"turn_id": f["turn_id"], "question": _shown(f.get("question"), QUESTION_SHOWN),
                "mode_name": names.get(f["evaluator_key"]), "evaluator_key": f["evaluator_key"],
                "critique": _shown(f.get("critique"), CRITIQUE_SHOWN),
                "group": "uniform sample" if f["turn_id"] in in_random
                else RISK_LABELS.get(group_of.get(f["turn_id"], ""), "risk group")} for f in found]
    return {"batch": batch_public(row), "flagged": public(flagged)}


async def _mode(mode_id: str) -> dict | None:
    try:
        uuid.UUID(str(mode_id))
    except ValueError:
        return None
    async with db.lab_engine().connect() as conn:
        row = (await conn.execute(text("SELECT id, name FROM tracelab.modes WHERE id = CAST(:m AS uuid)"),
                                  {"m": str(mode_id)})).mappings().first()
    return {"id": str(row["id"]), "name": row["name"]} if row else None


async def _period_batches(org: str | None) -> list[dict]:
    """The latest done or capped batch per period for exactly this scope, oldest window first."""
    async with db.lab_engine().connect() as conn:
        rows = (await conn.execute(text("""
SELECT * FROM (
    SELECT DISTINCT ON (period_label) id, period_label, window_start, window_end, results, started_at
      FROM tracelab.monitor_batches
     WHERE status IN ('done', 'capped') AND organization_id IS NOT DISTINCT FROM CAST(:org AS uuid)
     ORDER BY period_label, started_at DESC) latest
 ORDER BY window_start"""), {"org": org})).mappings().all()
    return [_decode(r) for r in rows]


async def chart(*, org: str | None, mode_id: str, periods: int = 12) -> dict:
    mode = await _mode(mode_id)
    if mode is None:
        raise WatchError(404, "not_found", "That failure mode was not found.")
    entries = []
    for r in await _period_batches(org):
        found = next((m for m in ((r.get("results") or {}).get("modes") or []) if str(m.get("mode_id")) == str(mode_id)), None)
        if found:
            entries.append({**found, "period_label": r["period_label"], "window_start": r["window_start"],
                            "window_end": r["window_end"], "batch_id": r["id"]})
    return public({"mode": mode, **chart_points(entries, periods=periods)})


async def _check_week_rows(org: str | None, since: datetime) -> list[dict]:
    async with db.lab_engine().connect() as conn:
        rows = (await conn.execute(text("""
SELECT v.evaluator_key, date_trunc('week', v.turn_started_at AT TIME ZONE 'UTC') AS week_start,
       count(*) AS n, count(*) FILTER (WHERE v.status = 'decided') AS decided,
       count(*) FILTER (WHERE v.status = 'decided' AND v.failure_present = 1) AS flagged
  FROM tracelab.monitor_verdicts v
 WHERE v.evaluator_key LIKE 'code:%' AND v.turn_started_at >= :since
   AND (CAST(:org AS uuid) IS NULL OR v.organization_id = CAST(:org AS uuid))
 GROUP BY 1, 2"""), {"org": org, "since": since})).mappings().all()
    return [dict(r) for r in rows]


async def code_check_trend(*, org: str | None, periods: int = 12, now: datetime | None = None) -> dict:
    """Weekly raw rates per live check, by the ISO week of the answer (spec 4.4). Unvalidated checks are
    shown as raw rates only (J3); `validated` says which is which."""
    spans = weeks_back(now or datetime.now(timezone.utc), min(max(int(periods), 1), 52))
    rows = await _check_week_rows(org, spans[0][1])
    validated = await _validated_check_keys()
    by_key = {(r["evaluator_key"], stats.iso_week_label(_utc(r["week_start"]))): r for r in rows}
    out = []
    for c in checks.CHECKS.values():
        if c.scope != "live":
            continue
        weeks = []
        for label, _, _ in spans:
            r = by_key.get((c.evaluator_key, label))
            decided, flagged = (int(r["decided"]), int(r["flagged"])) if r else (0, 0)
            weeks.append({"period_label": label, "n": int(r["n"]) if r else 0, "flagged": flagged,
                          "rate": round(flagged / decided, 4) if decided else None})
        out.append({"evaluator_key": c.evaluator_key, "label": c.label, "validated": c.evaluator_key in validated,
                    "weeks": weeks})
    return {"checks": out}


async def latest_prevalence(mode_id: str) -> tuple[dict | None, str]:
    """What the failure report shows as a mode's prevalence (the seam judges.latest_prevalence calls this): the
    corrected prevalence from the latest done or capped platform-wide batch that estimated the mode. The report is
    the platform owner's, so company batches are not mixed in. (None, reason) says why there is no figure."""
    for batch in reversed(await _period_batches(None)):
        found = next((m for m in ((batch.get("results") or {}).get("modes") or [])
                      if str(m.get("mode_id")) == str(mode_id)), None)
        if found is None:
            continue
        if found.get("corrected") is None:
            return None, found.get("reason") or "The latest batch could not estimate this mode."
        return public({"period": batch["period_label"], "batch_id": batch["id"], "n_sample": found.get("n_sample"),
                       "raw": found.get("raw"), "corrected": found.get("corrected"), "ci_low": found.get("ci_low"),
                       "ci_high": found.get("ci_high"), "confidence": found.get("confidence"),
                       "validity_warning": found.get("validity_warning") or "", "threshold": found.get("threshold")}), ""
    return None, "Monitoring has not produced an estimate for this mode yet."


# ---- "less manual checking over time" (spec 6) -----------------------------------------------------------

AUTOMATION_DEFINITIONS = {
    "auto_share": "Answers checked with no person needed that week: a validated code check, a judge in use, or a "
                  "golden answer graded automatically, and no person.",
    "people_share": "Answers a person checked that week (review notes, labels, golden answers decided by a person), "
                    "with or without an automatic check as well.",
    "auto_by_source": "A validated code check covers every chat answer, so read the sources and the automated "
                      "failure modes beside the headline: full coverage is not full correctness checking.",
    "golden_accuracy": "Golden answers graded that week that matched the expected answer (degraded runs left out).",
    "live_accuracy": "Fair-sample share right (sample fraction): verdicts that week on answers from uniform review "
                     "batches only.",
    "modes_automated": "Confirmed failure modes with a judge in use at the end of the week.",
}


def bucket_automation(*, weeks: Sequence[tuple[str, datetime, datetime]], auto: Sequence[Mapping[str, Any]],
                      human: Sequence[Mapping[str, Any]], golden_acc: Sequence[Mapping[str, Any]],
                      live_acc: Sequence[Mapping[str, Any]], modes: Sequence[Mapping[str, Any]],
                      automated: Sequence[Mapping[str, Any]]) -> list[dict]:
    """Per ISO week, by when the check was made: A = answers with a validated automatic verdict, H = answers a
    person decided. auto_share = |A minus H| / |A or H|; people_share = 1 - auto_share (None when nothing)."""
    def inside(r: Mapping[str, Any], ws: datetime, we: datetime) -> bool:
        return r.get("at") is not None and ws <= _utc(r["at"]) < we

    out = []
    for label, ws, we in weeks:
        a_rows = [r for r in auto if r.get("validated") and inside(r, ws, we)]
        h_rows = [r for r in human if inside(r, ws, we)]
        a_ids, h_ids = {r["turn_id"] for r in a_rows}, {r["turn_id"] for r in h_rows}
        checked = a_ids | h_ids
        golden_ids = {r["turn_id"] for r in a_rows + h_rows if r.get("is_golden")} & checked
        n = len(checked)
        auto_share = round(len(a_ids - h_ids) / n, 4) if n else None
        ga = [r for r in golden_acc if inside(r, ws, we)]
        la = [r for r in live_acc if inside(r, ws, we)]
        g_ok, l_ok = sum(1 for r in ga if r["correct"]), sum(1 for r in la if r["right"])
        g_lo, g_hi = stats.wilson_interval(g_ok, len(ga))
        l_lo, l_hi = stats.wilson_interval(l_ok, len(la))
        confirmed = {str(m["mode_id"]) for m in modes if m.get("confirmed_at") and _utc(m["confirmed_at"]) < we
                     and not (m.get("retired_at") and _utc(m["retired_at"]) < we)}
        automated_ids = {str(m["mode_id"]) for m in automated if m.get("since") and _utc(m["since"]) < we} & confirmed
        out.append({
            "week": label, "week_start": ws.isoformat(), "answers_live": n - len(golden_ids),
            "answers_golden": len(golden_ids), "checked_any": n, "auto_only": len(a_ids - h_ids),
            "people_only": len(h_ids - a_ids), "both": len(a_ids & h_ids), "auto_share": auto_share,
            "people_share": round(1 - auto_share, 4) if auto_share is not None else None,
            "auto_by_source": {s: len({r["turn_id"] for r in a_rows if r["source"] == s})
                               for s in ("golden_auto", "code_validated", "judge_frozen")},
            "people_by_source": {s: len({r["turn_id"] for r in h_rows if r["source"] == s})
                                 for s in ("open_coding", "labels", "golden_person")},
            "modes_confirmed": len(confirmed), "modes_automated": len(automated_ids),
            "golden_accuracy": {"decided": len(ga), "correct": g_ok, "rate": round(g_ok / len(ga), 4) if ga else None,
                                "low": g_lo, "high": g_hi},
            "live_accuracy": {"reviewed": len(la), "right": l_ok, "rate": round(l_ok / len(la), 4) if la else None,
                              "low": l_lo, "high": l_hi}})
    return out


#: A value check that found every expected value, or found a different one, decided the answer with no model.
_DECISIVE_VALUE_CHECKS = ("all_found", "mismatch")


def golden_records(rows: Sequence[Mapping[str, Any]], *, validated: set[str] | frozenset[str]) -> tuple[list[dict], list[dict]]:
    """The final graded result of each golden case rep (the row that carries the outcome and the grade) as
    (automatic checks, accuracy rows). A result reads as the Runs view reads it (golden_views.reading: a judgment
    verdict counts only once its grader version is in `validated`), and only a pass or a fail counts at all.
    The check is a VALIDATED automatic one when no model made it (a chat error), a value check decided it, or it is
    a judgment verdict of a validated grader; an objective answer the model alone graded is accuracy only."""
    auto: list[dict] = []
    acc: list[dict] = []
    for r in rows:
        grade = {k: r.get(k) for k in ("reading", "validated_outcome", "grader_version")}
        reading = golden_views.reading({"outcome": r.get("outcome"), "grade": grade}, validated=validated)
        if reading not in golden_views.COUNTED:
            continue
        acc.append({"at": r["at"], "correct": reading == "pass"})
        auto.append({"turn_id": r["turn_id"], "at": r["at"], "source": "golden_auto", "is_golden": True,
                     "validated": (r.get("source") == "runner" or r.get("det_status") in _DECISIVE_VALUE_CHECKS
                                   or r.get("evaluation") == "judgment")})
    return auto, acc


_ORG = "(CAST(:org AS uuid) IS NULL OR {alias}.organization_id = CAST(:org AS uuid))"
_IS_GOLDEN = "EXISTS (SELECT 1 FROM tracelab.golden_results g WHERE g.turn_id = {alias}.turn_id)"

# The monitoring verdicts of the window, by when each was made. A code check is VALIDATED once a frozen judge of kind
# code is in use for it and its held-out test has finished (the rule _validated_check_keys states); a judge verdict
# is from a judge in use, because monitoring runs no other.
_AUTO_VERDICTS_SQL = f"""
SELECT v.turn_id, v.updated_at AS at,
       CASE WHEN v.evaluator_key LIKE 'judge:%' THEN 'judge_frozen' ELSE 'code_validated' END AS source,
       (v.evaluator_key LIKE 'judge:%' OR EXISTS (
            SELECT 1 FROM tracelab.judges j
              JOIN tracelab.judge_runs jr ON jr.judge_id = j.id AND jr.split = 'test' AND jr.status = 'done'
             WHERE j.kind = 'code' AND j.status = 'frozen' AND j.decision = 'use' AND jr.finished_at <= v.updated_at
               AND v.evaluator_key = 'code:' || j.check_key || '@v' || j.check_version)) AS validated,
       false AS is_golden
  FROM tracelab.monitor_verdicts v
 WHERE v.status = 'decided' AND v.updated_at >= :start AND v.updated_at < :end AND {_ORG.format(alias='v')}"""

# Only the FINAL row of a case rep carries graded_at (runner._store_item: earlier messages stay pending with no grade),
# so graded_at marks exactly the results that count. The stored outcome and grade are read by golden_records.
_GOLDEN_FINAL_SQL = f"""
SELECT COALESCE(r.turn_id, 'golden-result:' || CAST(r.id AS text)) AS turn_id, r.graded_at AS at, r.outcome,
       r.grade ->> 'source' AS source, r.grade -> 'value_check' ->> 'status' AS det_status,
       r.grade ->> 'evaluation' AS evaluation, r.grade ->> 'reading' AS reading,
       r.grade ->> 'validated_outcome' AS validated_outcome, r.grade ->> 'grader_version' AS grader_version
  FROM tracelab.golden_results r
 WHERE r.graded_at >= :start AND r.graded_at < :end AND r.outcome IN ('pass', 'fail', 'unsure')
   AND {_ORG.format(alias='r')}"""

# Eval-case reps graded by a check or a judge (eval_cases.rep_verdict): passed or failed reps with a time. The twin runs
# of those cases are golden runs too; the same turn counts once in a week however many sources name it.
_EVAL_REPS_SQL = f"""
SELECT s.turn_id, s.at, 'golden_auto' AS source, true AS validated, true AS is_golden
  FROM (SELECT rep ->> 'turn_id' AS turn_id, c.organization_id,
               CASE WHEN rep ->> 'graded_at' ~ '^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}'
                    THEN CAST(rep ->> 'graded_at' AS timestamptz) END AS at
          FROM tracelab.eval_case_runs cr
          JOIN tracelab.eval_cases c ON c.id = cr.eval_case_id
         CROSS JOIN LATERAL jsonb_array_elements(
               CASE WHEN jsonb_typeof(cr.reps) = 'array' THEN cr.reps ELSE CAST('[]' AS jsonb) END) AS rep
         WHERE rep ->> 'status' IN ('passed', 'failed') AND rep ->> 'turn_id' IS NOT NULL) s
 WHERE s.at >= :start AND s.at < :end AND {_ORG.format(alias='s')}"""

_HUMAN_SQL = [f"""
SELECT a.turn_id, a.created_at AS at,
       CASE WHEN {_IS_GOLDEN.format(alias='a')} THEN 'golden_person' ELSE 'open_coding' END AS source,
       {_IS_GOLDEN.format(alias='a')} AS is_golden
  FROM tracelab.annotations a
 WHERE a.kind = 'verdict' AND NOT a.tombstone AND a.created_at >= :start AND a.created_at < :end
   AND {_ORG.format(alias='a')}""",
              f"""
SELECT l.turn_id, l.created_at AS at, 'labels' AS source, {_IS_GOLDEN.format(alias='l')} AS is_golden
  FROM tracelab.labels l
 WHERE l.approval = 'single' AND NOT l.tombstone AND l.created_at >= :start AND l.created_at < :end
   AND {_ORG.format(alias='l')}"""]

_LIVE_ACC_SQL = f"""
SELECT DISTINCT ON (a.turn_id, a.reviewer_id, date_trunc('week', a.created_at AT TIME ZONE 'UTC'))
       a.created_at AS at, (a.verdict = 'right') AS is_right
  FROM tracelab.annotations a
  JOIN tracelab.sample_items si ON si.turn_id = a.turn_id AND si.organization_id = a.organization_id
  JOIN tracelab.sample_batches b ON b.id = si.batch_id AND b.strategy = 'uniform' AND b.withdrawn_at IS NULL
 WHERE a.kind = 'verdict' AND NOT a.tombstone AND a.created_at >= :start AND a.created_at < :end
   AND {_ORG.format(alias='a')}
 ORDER BY a.turn_id, a.reviewer_id, date_trunc('week', a.created_at AT TIME ZONE 'UTC'), a.created_at DESC"""

_MODES_SQL = "SELECT id AS mode_id, confirmed_at, retired_at FROM tracelab.modes WHERE confirmed_at IS NOT NULL"

_AUTOMATED_SQL = """
SELECT j.mode_id, min(r.finished_at) AS since
  FROM tracelab.judges j
  JOIN tracelab.judge_runs r ON r.judge_id = j.id AND r.split = 'test' AND r.status = 'done'
 WHERE j.status = 'frozen' AND j.decision = 'use' AND r.finished_at IS NOT NULL
 GROUP BY j.mode_id"""


async def _automation_rows(org: str | None, start: datetime, end: datetime) -> dict:
    """Everything the buckets need for [start, end), in one read-only transaction on the lab database (every table
    here is Trace Lab's own: no chat table is read). The grader versions that now pass validation come from
    grader_validation, as the Runs view reads them."""
    p = {"org": org, "start": start, "end": end}
    async with _read_lab() as conn:
        async def rows(sql: str, params: Mapping[str, Any] | None = None) -> list[dict]:
            return [dict(r) for r in (await conn.execute(text(sql), dict(params or {}))).mappings().all()]

        auto = await rows(_AUTO_VERDICTS_SQL, p) + await rows(_EVAL_REPS_SQL, p)
        golden = await rows(_GOLDEN_FINAL_SQL, p)
        human = [r for sql in _HUMAN_SQL for r in await rows(sql, p)]
        live_acc = [{"at": r["at"], "right": r["is_right"]} for r in await rows(_LIVE_ACC_SQL, p)]
        modes = await rows(_MODES_SQL)
        automated = await rows(_AUTOMATED_SQL)
    waiting = {str(r["grader_version"]) for r in golden if r["outcome"] == "unsure" and r["reading"] == "needs_person"
               and r["grader_version"]}
    trusted = {v for v in sorted(waiting) if await grader_validation.passes(v)}
    golden_auto, golden_acc = golden_records(golden, validated=trusted)
    return {"auto": auto + golden_auto, "human": human, "golden_acc": golden_acc, "live_acc": live_acc,
            "modes": modes, "automated": automated}


async def automation_trend(*, org: str | None, weeks: int = 12, now: datetime | None = None) -> dict:
    spans = weeks_back(now or datetime.now(timezone.utc), min(max(int(weeks), 1), 52))
    rows = await _automation_rows(org, spans[0][1], spans[-1][2])
    return {"weeks": public(bucket_automation(weeks=spans, **rows)), "definitions": AUTOMATION_DEFINITIONS}
