"""Grader validation (Phase 3; the senior's decision 1): is one grader version trusted to judge judgment cases?

The grader is one prompt and one model serving every company, so its agreement with people is measured on human
labels POOLED across companies, and the same numbers are also REPORTED PER COMPANY. Pass is the positive class:
TPR = the share of human passes the grader passed, TNR = the share of human fails it failed, each with its Wilson
95% interval (Phase 1 stats). A version passes when the pooled labels hold at least
TRACE_LAB_GRADER_MIN_PER_CLASS human passes AND human fails (default 20 each) that the grader decided (a pass or
fail grade; unsure rows do not count toward the minimum, they are only reported as n_pass / n_fail / unsure) and both rates reach
TRACE_LAB_GRADER_MIN_TPR / _MIN_TNR (default 0.90). Until then golden_views reads judgment cases as unvalidated.

Human labels come from label sources that later phases register (register_label_source): Phase 3 has none, so every
version reads "needs labels" and judgment cases stay unvalidated, which is the honest state. A source answers
{turn_id: "pass" | "fail"} for the golden answers it was asked about; two sources that disagree on a turn leave it
out. At most two reps of one (company, case) count, so one case can never carry the measure. The grader's own unsure
verdicts never count as agreement; they are reported apart. Reads only, on db.lab_engine().
"""
from __future__ import annotations

import os
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

import structlog
from sqlalchemy import text

from . import db, stats

log = structlog.get_logger(__name__)

LabelSource = Callable[[list[str]], Awaitable[dict[str, str]]]
CACHE_TTL_S = 60.0
MAX_PER_CASE = 2
MAX_ROWS = 5000

_SOURCES: list[LabelSource] = []
_cache: dict[tuple[str, str], tuple[float, dict]] = {}

_ROWS_SQL = """
    SELECT r.organization_id::text AS org, r.case_key, r.turn_id, r.grade ->> 'verdict' AS verdict
      FROM tracelab.golden_results r
      JOIN tracelab.golden_cases c ON c.id = r.case_id
     WHERE r.grade ->> 'grader_version' = :v AND c.evaluation = :e AND r.turn_id IS NOT NULL
       AND r.grade ->> 'verdict' IN ('pass', 'fail', 'unsure') AND coalesce(r.grade ->> 'source', '') = 'grader'
     ORDER BY r.created_at DESC, r.id
     LIMIT :lim"""


def thresholds() -> dict[str, Any]:
    return {"min_per_class": int(os.getenv("TRACE_LAB_GRADER_MIN_PER_CLASS") or 20),
            "min_tpr": float(os.getenv("TRACE_LAB_GRADER_MIN_TPR") or 0.90),
            "min_tnr": float(os.getenv("TRACE_LAB_GRADER_MIN_TNR") or 0.90)}


def register_label_source(fn: LabelSource) -> None:
    """Add a source of human pass/fail labels on golden answers (Phase 4/5 register theirs). Idempotent."""
    if fn not in _SOURCES:
        _SOURCES.append(fn)
    clear_cache()


def clear_cache() -> None:
    _cache.clear()


async def human_labels(turn_ids: Sequence[str]) -> dict[str, str]:
    """{turn_id: 'pass' | 'fail'} from every registered source; a turn the sources disagree on is left out, and a
    source that fails is logged and skipped."""
    ids = [str(t) for t in turn_ids if t]
    if not ids or not _SOURCES:
        return {}
    seen: dict[str, set[str]] = {}
    for source in list(_SOURCES):
        try:
            got = await source(list(ids))
        except Exception as exc:  # noqa: BLE001 - a broken source means fewer labels, never a crash
            log.warning("trace_lab.grader_validation.source_failed", error=str(exc)[:200])
            continue
        for tid, label in (got or {}).items():
            if label in ("pass", "fail"):
                seen.setdefault(str(tid), set()).add(label)
    return {tid: labels.pop() for tid, labels in seen.items() if len(labels) == 1}


def _ratio(a: int, b: int) -> float | None:
    return round(a / b, 4) if b else None


def _rates(records: list[dict]) -> dict[str, Any]:
    decided = [r for r in records if r["grader"] in ("pass", "fail")]
    tp = sum(1 for r in decided if r["grader"] == "pass" and r["human"] == "pass")
    fn = sum(1 for r in decided if r["grader"] == "fail" and r["human"] == "pass")
    tn = sum(1 for r in decided if r["grader"] == "fail" and r["human"] == "fail")
    fp = sum(1 for r in decided if r["grader"] == "pass" and r["human"] == "fail")
    unsure = sum(1 for r in records if r["grader"] == "unsure")
    return {"n": len(records), "n_pass": sum(1 for r in records if r["human"] == "pass"),
            "n_fail": sum(1 for r in records if r["human"] == "fail"), "tp": tp, "fn": fn, "tn": tn, "fp": fp,
            "tpr": _ratio(tp, tp + fn), "tnr": _ratio(tn, tn + fp),
            "wilson": {"tpr": list(stats.wilson_interval(tp, tp + fn)), "tnr": list(stats.wilson_interval(tn, tn + fp))},
            "unsure": unsure, "unsure_rate": _ratio(unsure, len(records))}


def metrics(records: Sequence[dict], *, min_per_class: int, min_tpr: float, min_tnr: float) -> dict[str, Any]:
    """Pure. records (newest first): {org, case_key, grader: pass|fail|unsure, human: pass|fail}. Returns the pooled
    rates, the same rates per company, whether the version passes and why not."""
    capped: list[dict] = []
    per_case: dict[tuple[str, str], int] = {}
    for r in records:
        key = (str(r["org"]), str(r["case_key"]))
        if per_case.get(key, 0) >= MAX_PER_CASE:
            continue
        per_case[key] = per_case.get(key, 0) + 1
        capped.append(r)
    pooled = _rates(capped)
    by_org: dict[str, list[dict]] = {}
    for r in capped:
        by_org.setdefault(str(r["org"]), []).append(r)
    by_company = [{"organization_id": org, **_rates(rows)} for org, rows in sorted(by_org.items())]
    decided_pass, decided_fail = pooled["tp"] + pooled["fn"], pooled["tn"] + pooled["fp"]
    if decided_pass < min_per_class or decided_fail < min_per_class:
        reason = (f"needs {min_per_class} human passes and {min_per_class} human fails across companies "
                  f"graded pass or fail by the grader (has {decided_pass} and {decided_fail}; unsure grades do not count)")
    elif pooled["tpr"] is None or pooled["tpr"] < min_tpr:
        reason = f"TPR {pooled['tpr']} is below {min_tpr}"
    elif pooled["tnr"] is None or pooled["tnr"] < min_tnr:
        reason = f"TNR {pooled['tnr']} is below {min_tnr}"
    else:
        reason = "validated"
    return {"pooled": pooled, "by_company": by_company, "passes": reason == "validated", "reason": reason}


async def validation(*, grader_version: str, evaluation: str = "judgment") -> dict[str, Any]:
    """The validation of one grader version on one evaluation kind (judgment by default), computed on read and
    cached for CACHE_TTL_S: {pooled: {tpr, tnr, wilson, n_pass, n_fail, ...}, by_company: [...], passes, reason,
    grader_version, evaluation, thresholds, labelled}."""
    key = (str(grader_version), str(evaluation))
    hit = _cache.get(key)
    if hit is not None and time.monotonic() - hit[0] <= CACHE_TTL_S:
        return dict(hit[1])
    async with db.lab_engine().connect() as conn:
        rows = (await conn.execute(text(_ROWS_SQL), {"v": key[0], "e": key[1], "lim": MAX_ROWS})).mappings().all()
    labels = await human_labels([r["turn_id"] for r in rows])
    records = [{"org": r["org"], "case_key": r["case_key"], "grader": r["verdict"], "human": labels[r["turn_id"]]}
               for r in rows if r["turn_id"] in labels]
    t = thresholds()
    out = metrics(records, **t)
    out.update(grader_version=key[0], evaluation=key[1], thresholds=t, graded=len(rows), labelled=len(records))
    _cache[key] = (time.monotonic(), out)
    return dict(out)


async def passes(grader_version: str, *, evaluation: str = "judgment") -> bool:
    """True only when validation passes; False on any error (unknown means not validated)."""
    try:
        return bool((await validation(grader_version=grader_version, evaluation=evaluation))["passes"])
    except Exception as exc:  # noqa: BLE001
        log.warning("trace_lab.grader_validation.failed", error=str(exc)[:200])
        return False
