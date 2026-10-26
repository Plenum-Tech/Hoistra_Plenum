"""Trace Lab eval cases (Phase 7; spec "Judges, code checks, monitoring, eval cases and release check" section 5).

A confirmed failure, a monitoring flag or a golden case becomes an eval case. Every case runs through a golden case in
the company's "Regression cases" golden set (its twin), on the golden runner (runner.py: stepped by the browser): a
5-run baseline classifies it (5 of 5 = regression, else capability), a release check runs every active case k times,
and repeat-n measures one answer's failure rate. Only automatic, validated evidence decides a rep.

Golden Rule G0 (addendum 2.3): the cases, their twins, the runs and their grading are Trace Lab's own tables, on
db.lab_engine() (schema tracelab). The chat's tables are READ through db.main_engine() in one place only (the head of
the answer a case is made from, _turn_for_case); no SQL names both databases. Nothing is written to the chat. An
answer a case is made from is copied into the evidence tables first (evidence.snapshot), so it outlives the chat's
span purge. Golden answers live in the lab's evidence tables already, so grading reads them with judges.load_judge_turns.
"""
from __future__ import annotations

import json
import os
import secrets
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from src.agents.skill_lab import WRITE_INTENT

from . import checks, conversations, db, evidence, golden, golden_store, jobs, judges, monitor, normalize, provenance, runner, stats
from .monitor import WatchError, _field, _json, judges_in_use, public

log = structlog.get_logger(__name__)

REGRESSION_SET_NAME = golden_store.REGRESSION_SET_NAME   # the company's one "Regression cases" set is golden_store's
REGRESSION_SET_MAX = 2000                                # golden_sets.case_count is capped at 2,000 by 0002
BASELINE_K = 5
REPEAT_DEFAULT_N = 10
REPEAT_MIN_N = 2
REPEAT_MAX_N = runner.HARD_MAX_K                         # 20; runner.max_k() may lower it per deployment
RELEASE_DEFAULT_K = 5
RELEASE_MAX_K = 10
MAX_FOLLOWUPS = 4
QUESTION_FETCH = 4_000                                   # the chat keeps at most this much of a question
SNAPSHOT_REASON = "proposed"                             # evidence_turns.reason of an answer first kept for a case
LIVE_STATUSES = ("proposed", "draft", "baselining", "active")
DECIDED_REPS = ("passed", "failed")
REPLAY_GUIDANCE = ("A high, consistent failure rate is a regression to fix and block in CI. "
                   "A rare failure should become an evaluation case.")


def eval_grading_usd_cap() -> float:
    return float(os.getenv("TRACE_LAB_EVAL_GRADING_USD_CAP", "2"))


def new_case_key() -> str:
    return "e-" + secrets.token_hex(4)


def twin_key(case_key: str, version: int) -> str:
    """The golden case key of one case version's twin (golden_cases is unique per set and key)."""
    return f"{case_key}.v{int(version)}"


def replayable(question: str | None, channel: str | None, session_id: str | None) -> bool:
    """Only chat answers whose question asks for no change can be replayed (spec 5.7)."""
    return (channel == "chat" and not (session_id or "").startswith("golden-")
            and not WRITE_INTENT.search(question or ""))


def _scope_of(value: Any) -> dict:
    """The asker's scope as a mapping, or {} when it is missing or is not a JSON object."""
    try:
        scope = _json(value)
    except ValueError:
        return {}
    return dict(scope) if isinstance(scope, Mapping) else {}


def case_from_turn(turn: Any, meta: Mapping[str, Any] | None) -> dict:
    """The case input from an answer (spec 5.1). A follow-up in an observed conversation starts at the first
    question, with the later questions up to this one as follow-ups (at most MAX_FOLLOWUPS). The page context and the
    asker's scope come from `meta` when the caller holds them; Trace Lab records neither for a live answer (G0: no
    write-time capture), so the turn's own page context is used, else none ('' = ask with no page context). The
    answer is never copied."""
    asked = [str(_field(p, "question") or "").strip() for p in (_field(turn, "prior") or [])]
    asked.append(str(_field(turn, "question") or "").strip())
    asked = [q for q in asked if q][-(MAX_FOLLOWUPS + 1):]
    m = dict(meta or {})
    scope = _scope_of(m.get("asker_scope"))
    page = m.get("context_text")
    if page is None:
        page = _field(turn, "page_context")
    return {"question": asked[0] if asked else "", "followups": asked[1:], "page_context": page or "",
            "scope": {"asker_role": m.get("asker_role"), "building_ids": scope.get("building_ids")}}


def merge_input(base: Mapping[str, Any], override: Mapping[str, Any] | None) -> dict:
    """The case input with a person's edits on it, checked against what its twin can hold."""
    out = {"question": str(base.get("question") or ""), "followups": list(base.get("followups") or []),
           "page_context": base.get("page_context"), "scope": dict(base.get("scope") or {})}
    o = dict(override or {})
    if "question" in o:
        out["question"] = str(o["question"] or "").strip()
    if "followups" in o:
        out["followups"] = [str(f).strip() for f in (o["followups"] or []) if str(f).strip()]
    if "page_context" in o:
        out["page_context"] = None if o["page_context"] is None else str(o["page_context"])
    if not 1 <= len(out["question"]) <= 2000:
        raise WatchError(422, "bad_input", "The question must be 1 to 2,000 characters.")
    if len(out["followups"]) > MAX_FOLLOWUPS or any(len(f) > 2000 for f in out["followups"]):
        raise WatchError(422, "bad_input", f"At most {MAX_FOLLOWUPS} follow-ups, each up to 2,000 characters.")
    if out["page_context"] is not None and len(out["page_context"]) > 4000:
        raise WatchError(422, "bad_input", "The page context can be up to 4,000 characters.")
    return out


def normalise_expected(expected: Mapping[str, Any] | None, *, modes_in_use: Mapping[str, Any]) -> dict:
    """Spec 5.2: assertions are prose and decide nothing; checks are keys of checks.CHECKS; judges name modes
    that have a judge in use ("never a new judge in CI")."""
    e = dict(expected or {})
    out_checks = []
    for c in e.get("checks") or []:
        key = str((c or {}).get("check") or "")
        if key not in checks.CHECKS:
            raise WatchError(422, "unknown_check", f"There is no code check called {key or '(blank)'}.")
        out_checks.append({**{k: v for k, v in dict(c).items() if k != "check"}, "check": key})
    out_judges: list[dict] = []
    for jd in e.get("judges") or []:
        mode_id = str((jd or {}).get("mode_id") or "")
        if mode_id not in modes_in_use:
            raise WatchError(409, "judge_not_frozen",
                             "That failure mode has no judge in use yet, so it cannot decide a case.")
        if all(j["mode_id"] != mode_id for j in out_judges):
            out_judges.append({"mode_id": mode_id})
    golden_expected = bool(e.get("golden_expected", True))
    if not out_checks and not out_judges and not golden_expected:
        raise WatchError(422, "nothing_decides",
                         "Name a code check, a judged failure mode, or grade against the expected answer.")
    return {"assertions": [str(a)[:500] for a in (e.get("assertions") or [])][:20], "checks": out_checks,
            "judges": out_judges, "golden_expected": golden_expected}


def parse_values(cell: str | None) -> list[dict]:
    """The golden Expected values grammar (golden.parse_expected_values)."""
    if not (cell or "").strip():
        return []
    try:
        return list(golden.parse_expected_values(cell))
    except ValueError as exc:
        raise WatchError(422, "bad_expected_values", f"Could not read this expected value: {exc}") from None


_OUTCOME_REASONS = {"degraded": "a step was refused or needed approval",
                    "error": "the service failed three times in a row"}


def rep_verdict(*, rep: int, turn_id: str | None, outcome: str | None, status: str | None,
                check_results: Sequence[Mapping[str, Any]], judge_results: Sequence[Mapping[str, Any]],
                golden_expected: bool, usd: float = 0.0, graded_at: str | None = None,
                steps: int | None = None) -> dict:
    """Spec 5.3, in order. `outcome` is golden_results.outcome of the rep's final row (None while nothing is
    stored; `status` is then None, or running / infra). Not run yet -> missing. A 'degraded' outcome (a refused or
    approval step) or an 'error' one (the service failed three times) -> degraded, and so is an answer with no turn.
    A failing check, a Fail from a judge in use, or (with golden_expected) an automatic 'fail' grade -> failed, which
    wins over everything; then degraded (a check or judge that could not decide, a stale expected answer), then
    ungraded (a judge call not made yet), then pending (the golden grade is not made, or needs a person), then
    passed."""
    base = {"rep": rep, "turn_id": turn_id, "failures": [], "degraded_reason": None,
            "checks": [dict(c) for c in check_results], "judges": [dict(j) for j in judge_results],
            "golden": outcome, "usd": round(float(usd or 0), 6), "graded_at": graded_at, "steps": steps}
    if outcome is None and status in (None, "running", "infra"):
        return {**base, "status": "missing"}
    if outcome in _OUTCOME_REASONS:
        return {**base, "status": "degraded", "degraded_reason": _OUTCOME_REASONS[outcome]}
    if turn_id is None:
        return {**base, "status": "degraded", "degraded_reason": "no answer was recorded"}
    failures: list[str] = []
    degraded: str | None = None
    ungraded = pending = False
    for c in check_results:
        if c["status"] == "fail":
            failures.append(f"check:{c['key']}")
        elif c["status"] in ("undecidable", "not_applicable"):
            degraded = degraded or f"the check {c['key']} could not decide"
    for j in judge_results:
        if j["status"] == "decided" and j.get("failure_present") == 1:
            failures.append(f"judge:{j['mode']}")
        elif j["status"] == "error":
            degraded = degraded or f"the judge for {j['mode']} returned no verdict"
        elif j["status"] == "ungraded":
            ungraded = True
    if golden_expected:
        if outcome == "fail":
            failures.append("golden:expected_answer")
        elif outcome == "stale_reference":
            degraded = degraded or "the expected answer is out of date"
        elif outcome != "pass":
            pending = True
    if failures:
        state = "failed"
    elif degraded:
        state = "degraded"
    elif ungraded:
        state = "ungraded"
    elif pending:
        state = "pending"
    else:
        state = "passed"
    return {**base, "status": state, "failures": failures, "degraded_reason": degraded if state == "degraded" else None}


def classify_baseline(statuses: Sequence[str]) -> dict | None:
    """Spec 5.4: classify only with 5 decided reps."""
    if len(statuses) != BASELINE_K or any(s not in DECIDED_REPS for s in statuses):
        return None
    passes = list(statuses).count("passed")
    if passes == BASELINE_K:
        return {"kind": "regression", "baseline_pass_rate": None}
    return {"kind": "capability", "baseline_pass_rate": round(passes / BASELINE_K, 2)}


def case_run_summary(reps: Sequence[Mapping[str, Any]], *, k: int, purpose: str, kind: str,
                     baseline_pass_rate: float | None) -> dict:
    by_rep = {int(r["rep"]): r for r in reps}
    statuses = [by_rep.get(i, {"status": "missing"})["status"] for i in range(1, int(k) + 1)]
    passes, failed = statuses.count("passed"), statuses.count("failed")
    n = passes + failed
    ungraded = statuses.count("ungraded")
    degraded, pending, missing = statuses.count("degraded"), statuses.count("pending") + ungraded, statuses.count("missing")
    out: dict[str, Any] = {
        "k": int(k), "n": n, "passes": passes, "degraded": degraded, "pending": pending, "missing": missing,
        **{f"pass_at_{x}": (round(stats.pass_at_k(n, passes, x), 4) if n >= x else None) for x in (1, 3, 5)},
        "pass_hat_k": round(stats.pass_hat_k(n, passes, int(k)), 4) if n >= int(k) else None,
        "classification": None}
    if purpose == "baseline":
        cls = classify_baseline(statuses)
        out["classification"] = cls
        if cls is None:
            decision, reason = "incomplete", "Re-run the missing reps"
        elif cls["kind"] == "regression":
            decision, reason = "pass", f"baseline: a regression case, passed {passes} of {k}"
        else:
            decision, reason = "pass", f"baseline: a capability case, passed {passes} of {k}"
    elif kind in ("regression", "capability"):
        d = stats.case_decision(kind, passes, n, baseline_pass_rate, planned=int(k),
                                not_decided=degraded + pending + missing)
        decision, reason = d["decision"], d["reason"]
    else:
        decision, reason = "incomplete", "the case has no baseline yet, so this run decides nothing"
    if decision == "incomplete" and ungraded:
        reason = f"{ungraded} ungraded reps; grade them to decide. {reason}"
    out.update(decision=decision, reason=reason)
    return out


def release_decision(regression: Sequence[Mapping[str, Any]], *, k: int) -> tuple[str, str]:
    """Spec 5.6. Capability cases never affect it."""
    if not regression:
        return "incomplete", "No regression cases yet: nothing can be certified."
    blocked = [r for r in regression if r["decision"] == "block"]
    if blocked:
        return "block", f"{len(blocked)} of {len(regression)} regression cases failed at least one run"
    unsure = [r for r in regression if r["decision"] != "pass"]
    if unsure:
        return "incomplete", (f"{len(unsure)} of {len(regression)} regression cases have runs without a verdict; "
                              "incomplete evidence never passes")
    return "pass", f"all {len(regression)} regression cases passed {k} of {k} runs"


def rollout_records(reps: Sequence[Mapping[str, Any]]) -> tuple[list[dict], int]:
    records, excluded = [], 0
    for r in reps:
        if r.get("status") in DECIDED_REPS:
            records.append({"passed": r["status"] == "passed", "failures": list(r.get("failures") or []),
                            "steps": int(r.get("steps") or 0)})
        else:
            excluded += 1
    return records, excluded


def repeat_summary(reps: Sequence[Mapping[str, Any]]) -> dict:
    """Spec 5.7: failure rate with the replay bootstrap (2,000 resamples, seed 7); degraded reps left out."""
    records, excluded = rollout_records(reps)
    if not records:
        return {"n": 0, "excluded": excluded, "failure_rate": None, "ci_low": None, "ci_high": None,
                "mode_counts": {}, "steps": None, "wilson_high": None, "guidance": REPLAY_GUIDANCE}
    return {**stats.summarize_rollouts(records), "n": len(records), "excluded": excluded, "guidance": REPLAY_GUIDANCE}


_CASE_COPY = ("organization_id", "case_key", "mode_id", "source_type", "source_turn_id", "source_batch_id",
              "source_golden_case_id", "input", "initial_state", "expected")


def next_version(old: Mapping[str, Any], *, input_override: Mapping[str, Any] | None = None,
                 initial_state: Mapping[str, Any] | None = None, expected: Mapping[str, Any] | None = None) -> dict:
    """An edit is a new version with the same key, unclassified again (the repo never re-labels a case by hand)."""
    new = {k: (_json(old.get(k)) if k in ("input", "initial_state", "expected") else old.get(k)) for k in _CASE_COPY}
    for key in ("organization_id", "mode_id", "source_batch_id", "source_golden_case_id"):
        if new.get(key) is not None:
            new[key] = str(new[key])
    new.update(id=str(uuid.uuid4()), version=int(old["version"]) + 1, status="draft", kind="unclassified",
               baseline_pass_rate=None, baseline_run_id=None, golden_case_id=None, supersedes=str(old["id"]),
               retired_reason=None, rev=1)
    if input_override:
        new["input"] = merge_input(new["input"] or {}, input_override)
    if initial_state is not None:
        new["initial_state"] = dict(initial_state)
    if expected is not None:
        new["expected"] = dict(expected)
    return new


_PUBLIC_CASE = ("id", "organization_id", "case_key", "version", "status", "kind", "baseline_pass_rate",
                "baseline_run_id", "mode_id", "mode_name", "source_type", "source_turn_id", "source_batch_id",
                "source_golden_case_id", "golden_case_id", "input", "initial_state", "expected", "supersedes",
                "retired_reason", "rev", "created_by_email", "created_at", "updated_at")


def case_public(row: Mapping[str, Any]) -> dict:
    out = {k: row.get(k) for k in _PUBLIC_CASE}
    for key in ("input", "initial_state", "expected"):
        out[key] = _json(out[key])
    out["latest_run"] = ({"golden_run_id": row.get("run_golden_run_id"), "purpose": row.get("run_purpose"),
                          "decision": row.get("run_decision"), "reason": row.get("run_reason"),
                          "passes": row.get("run_passes"), "n": row.get("run_n"), "k": row.get("run_k"),
                          "pass_at_1": row.get("run_pass_at_1"), "pass_at_3": row.get("run_pass_at_3"),
                          "pass_at_5": row.get("run_pass_at_5"), "pass_hat_k": row.get("run_pass_hat_k"),
                          "computed_at": row.get("run_computed_at")}
                         if row.get("run_golden_run_id") else None)
    return public(out)


def _fmt(x: Any) -> str:
    return "—" if x is None else f"{float(x):.2f}"


def render_release_md(report: Mapping[str, Any], *, company: str) -> str:
    """Spec 5.6 Markdown, in order. No internal names: checks by label, judges by version name."""
    fp = report.get("fingerprint") or {}
    graders = report.get("graders") or {}
    labels = {c.evaluator_key: c.label for c in checks.CHECKS.values()}
    k = report.get("k")
    lines = [f"# Release check — {company} — {report.get('run_key')}", "",
             f"**Decision: {str(report.get('decision') or '').upper()}** — {report.get('reason')}", "",
             "## Run", f"- Runs per case: {k}", f"- Started: {report.get('started_at') or '—'}",
             f"- Finished: {report.get('finished_at') or 'not finished'}",
             f"- Cost: ${float(report.get('usd') or 0):.2f}", "",
             "## Configuration", f"- Build: {fp.get('image') or 'not recorded'}",
             f"- Chat model: {fp.get('openai_model') or 'not recorded'}",
             f"- Judge model: {fp.get('grader_model') or 'not recorded'}",
             "- Judge versions: " + (", ".join(j.get("judge_key") or "" for j in graders.get("judges") or []) or "none"),
             "- Code checks: " + (", ".join(labels.get(key, "a retired check") for key in graders.get("checks") or [])
                                  or "none"), "",
             "## Regression cases"]
    regression = report.get("regression") or []
    if regression:
        lines += ["| Case | Failure mode | Runs passed | Decision | Why |", "|---|---|---|---|---|"]
        lines += [f"| {r['case_key']} v{r['version']} | {r.get('mode') or '—'} | {r['passes']} of {k} | "
                  f"{r['decision']} | {r['reason']} |" for r in regression]
    else:
        lines.append("No regression cases yet.")
    lines += ["", "## Capability cases"]
    capability = report.get("capability") or []
    if capability:
        lines += ["| Case | Failure mode | pass@1 | pass@3 | pass@5 | pass^5 | Baseline |",
                  "|---|---|---|---|---|---|---|"]
        lines += [f"| {c['case_key']} v{c['version']} | {c.get('mode') or '—'} | {_fmt(c.get('pass_at_1'))} | "
                  f"{_fmt(c.get('pass_at_3'))} | {_fmt(c.get('pass_at_5'))} | {_fmt(c.get('pass_hat_5'))} | "
                  f"{_fmt(c.get('baseline_pass_rate'))} |" for c in capability]
    else:
        lines.append("None in this check.")
    lines += ["", "## Runs without a verdict"]
    gaps = report.get("runs_without_verdict") or []
    lines += [f"- {g['case_key']} run {g['rep']}: {g['status']}" + (f" ({g['reason']})" if g.get("reason") else "")
              for g in gaps] or ["None."]
    lines += ["", "## Changes since the last check"]
    changes = report.get("changes_since_last")
    if changes is None:
        lines.append("No earlier release check.")
    elif not changes:
        lines.append("No changes.")
    else:
        lines += [f"- {c['case_key']}: {c['before']} → {c['after']}" for c in changes]
    lines += ["", f"Open in the Trace Lab: ?tl=runs&run={report.get('golden_run_id')}", ""]
    return "\n".join(lines)


# ---- store: cases and their golden twins (the Regression cases set) -------------------------------------------

def _turn_sql() -> str:
    """The head of one chat answer: its company, session, question and inferred channel, from the chat's tables only
    (one database per statement). An answer with no company is never a case (R27)."""
    return f"""
SELECT tt.turn_id, CAST(tt.organization_id AS text) AS organization_id, tt.session_id,
       left(coalesce(tt.question, ''), {QUESTION_FETCH}) AS question, {provenance.INFERRED_CHANNEL_SQL} AS channel
  FROM {conversations.TURNS} tt
 WHERE tt.turn_id = :tid AND tt.organization_id IS NOT NULL
   AND (CAST(:org AS uuid) IS NULL OR tt.organization_id = CAST(:org AS uuid))"""


async def _turn_for_case(org: str | None, turn_id: str) -> dict | None:
    """The answer a case is made from, read-only through the main engine; org None reads it for its own company
    (a platform batch). The chat keeps the turn row after it purges the spans, so the head is always readable."""
    if not turn_id or len(turn_id) > evidence.MAX_ID_LENGTH:
        return None
    async with monitor._read_main() as conn:
        row = (await conn.execute(text(_turn_sql()), {"tid": turn_id, "org": org})).mappings().first()
    if row is None:
        return None
    out = dict(row)
    out["question"] = normalize.repair_question({"question": out["question"]}, None, None)[0]
    return out


async def _keep_evidence(org: str, turn_id: str) -> None:
    """Copy the answer into the lab's evidence tables before a case relies on it. A failure stops the case: it must
    not rest on evidence the chat's span purge could take away."""
    try:
        await evidence.snapshot([turn_id], organization_id=org, reason=SNAPSHOT_REASON)
    except Exception as exc:  # noqa: BLE001 - whatever failed, the evidence was not kept
        log.warning("trace_lab.eval_cases.evidence_not_kept", turn_id=turn_id, error=str(exc)[:300] or type(exc).__name__)
        raise WatchError(503, "evidence_not_kept", "The answer could not be copied for this case. Try again in a minute.") from None


async def _failure_confirmed(org: str, turn_id: str, mode_id: str | None) -> bool:
    """Spec 5.1: a person recorded the failure - a live label 'present' decided one at a time (R10) for the mode, or a
    live 'wrong' verdict on the answer. Both tables are the lab's own."""
    async with db.lab_engine().connect() as conn:
        return bool((await conn.execute(text("""
SELECT EXISTS (
    SELECT 1 FROM tracelab.labels l
     WHERE l.turn_id = :tid AND l.organization_id = CAST(:org AS uuid) AND l.superseded_by IS NULL
       AND NOT l.tombstone AND l.present AND l.approval = 'single'
       AND (CAST(:mode AS uuid) IS NULL OR l.mode_id = CAST(:mode AS uuid)))
 OR EXISTS (
    SELECT 1 FROM tracelab.annotations a
     WHERE a.turn_id = :tid AND a.organization_id = CAST(:org AS uuid) AND a.kind = 'verdict'
       AND a.superseded_by IS NULL AND NOT a.tombstone AND a.verdict = 'wrong')"""),
            {"tid": turn_id, "org": org, "mode": mode_id})).scalar())


_CASE_SELECT = """
SELECT c.*, m.name AS mode_name, lr.golden_run_id AS run_golden_run_id, lr.purpose AS run_purpose,
       lr.decision AS run_decision, lr.reason AS run_reason, lr.passes AS run_passes, lr.n AS run_n, lr.k AS run_k,
       lr.pass_at_1 AS run_pass_at_1, lr.pass_at_3 AS run_pass_at_3, lr.pass_at_5 AS run_pass_at_5,
       lr.pass_hat_k AS run_pass_hat_k, lr.computed_at AS run_computed_at
  FROM tracelab.eval_cases c
  LEFT JOIN tracelab.modes m ON m.id = c.mode_id
  LEFT JOIN LATERAL (SELECT r.* FROM tracelab.eval_case_runs r JOIN tracelab.golden_runs g ON g.id = r.golden_run_id
                      WHERE r.eval_case_id = c.id ORDER BY g.started_at DESC, r.computed_at DESC LIMIT 1) lr ON true"""


def _is_uuid(value: Any) -> bool:
    try:
        uuid.UUID(str(value))
        return True
    except ValueError:
        return False


async def _case(org: str, case_id: str) -> dict | None:
    if not _is_uuid(case_id):
        return None
    async with db.lab_engine().connect() as conn:
        row = (await conn.execute(text(_CASE_SELECT + " WHERE c.id = CAST(:id AS uuid) AND c.organization_id = CAST(:org AS uuid)"),
                                  {"id": str(case_id), "org": org})).mappings().first()
    return dict(row) if row else None


async def _live_case_for_turn(org: str, turn_id: str) -> dict | None:
    async with db.lab_engine().connect() as conn:
        row = (await conn.execute(text(_CASE_SELECT + """
 WHERE c.organization_id = CAST(:org AS uuid) AND c.source_turn_id = :tid
   AND c.status IN ('proposed', 'draft', 'baselining', 'active')
 ORDER BY c.created_at DESC LIMIT 1"""), {"org": org, "tid": turn_id})).mappings().first()
    return dict(row) if row else None


async def _golden_case(org: str, golden_case_id: str) -> dict | None:
    if not _is_uuid(golden_case_id):
        return None
    async with db.lab_engine().connect() as conn:
        row = (await conn.execute(text("SELECT * FROM tracelab.golden_cases WHERE id = CAST(:id AS uuid) "
                                       "AND organization_id = CAST(:org AS uuid)"),
                                  {"id": str(golden_case_id), "org": org})).mappings().first()
    return dict(row) if row else None


async def _twin(golden_case_id: Any) -> dict | None:
    if not golden_case_id:
        return None
    async with db.lab_engine().connect() as conn:
        row = (await conn.execute(text("SELECT id, set_id, case_key, expected_answer, evaluation, expected_values, "
                                       "page_context FROM tracelab.golden_cases WHERE id = CAST(:id AS uuid)"),
                                  {"id": str(golden_case_id)})).mappings().first()
    return dict(row) if row else None


async def _mode_exists(mode_id: str) -> bool:
    return await monitor._mode(mode_id) is not None


def _actor_for_twin(actor_id: str | None, actor_email: str | None) -> tuple[str, str]:
    """golden_store.regression_set(create=True) records who made the company's Regression cases set, so a twin is
    written only under a real actor id and email: no placeholder, no company-id fallback."""
    if not (actor_id and actor_email):
        raise WatchError(422, "actor_required",
                         "Your account needs an email address before a case can be recorded under it.")
    return str(actor_id), str(actor_email)


async def _regression_set_id(org: str, actor_id: str | None, actor_email: str | None) -> str:
    """The company's Regression cases set, made on first use under the actor (golden_store owns it and does this in a
    transaction of its own: two concurrent creators get the same row)."""
    who, email = _actor_for_twin(actor_id, actor_email)
    row = await golden_store.regression_set(org, create=True, actor=SimpleNamespace(user_id=who, email=email))
    return str(row["id"])


async def _add_twin(conn: Any, *, set_id: str, org: str, case_key: str, question: str, followups: Sequence[str],
                    expected_answer: str, evaluation: str, expected_values: Sequence[Mapping[str, Any]],
                    page_context: str | None, source: str) -> str:
    """One golden case in the Regression cases set, inside the caller's lab transaction. golden_cases is append-only,
    so the next position is read under one lock per company: two writers never take the same one."""
    await conn.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": "tracelab-eval-twins:" + org})
    count, idx = (await conn.execute(text(
        "SELECT count(*), coalesce(max(idx), -1) + 1 FROM tracelab.golden_cases WHERE set_id = CAST(:s AS uuid)"),
        {"s": set_id})).one()
    if int(count) >= REGRESSION_SET_MAX:
        raise WatchError(409, "regression_set_full", "The Regression cases set holds 2,000 cases; retire some first.")
    flags = ("write_intent",) if WRITE_INTENT.search(" ".join([question, *followups])) else ()
    row = golden.CaseRow(row_no=int(idx) + 1, case_key=case_key, key_source="id_column", case_version="",
                         question=question, followups=tuple(followups), expected_answer=expected_answer,
                         evaluation=evaluation, expected_values=tuple(expected_values), source=source,
                         verified_on=datetime.now(timezone.utc).date(), page_context=page_context, ask_as="company",
                         requirement_ids=(), notes=None, flags=flags)
    golden_id = str(uuid.uuid4())
    await conn.execute(text("""
INSERT INTO tracelab.golden_cases (id, set_id, organization_id, case_key, key_source, case_version, idx, row_no,
    question, followups, expected_answer, evaluation, expected_values, source, verified_on, page_context, ask_as,
    requirement_ids, notes, flags)
VALUES (CAST(:id AS uuid), CAST(:set AS uuid), CAST(:org AS uuid), :key, 'id_column', :version, :idx, :row_no, :q,
    CAST(:followups AS jsonb), :expected, :evaluation, CAST(:values AS jsonb), :source, :verified, :ctx, 'company',
    CAST(:reqs AS text[]), NULL, CAST(:flags AS text[]))"""),
        {"id": golden_id, "set": set_id, "org": org, "key": case_key, "version": golden.case_version_for(row),
         "idx": int(idx), "row_no": int(idx) + 1, "q": question, "followups": json.dumps(list(followups)),
         "expected": expected_answer, "evaluation": evaluation, "values": json.dumps(public(list(expected_values))),
         "source": source[:500], "verified": row.verified_on, "ctx": page_context, "reqs": [], "flags": list(flags)})
    pairs = [(k, v) for k, v in (await conn.execute(text(
        "SELECT case_key, case_version FROM tracelab.golden_cases WHERE set_id = CAST(:s AS uuid)"),
        {"s": set_id})).all()]
    await conn.execute(text("UPDATE tracelab.golden_sets SET case_count = :n, set_hash = :h WHERE id = CAST(:s AS uuid)"),
                       {"n": len(pairs), "h": golden.set_hash(pairs), "s": set_id})
    return golden_id


_INSERT_CASE = """
INSERT INTO tracelab.eval_cases (id, organization_id, case_key, version, status, kind, baseline_pass_rate,
    mode_id, source_type, source_turn_id, source_batch_id, source_golden_case_id, golden_case_id, input,
    initial_state, expected, supersedes, created_by, created_by_email)
VALUES (CAST(:id AS uuid), CAST(:org AS uuid), :key, :version, :status, :kind, :rate, CAST(:mode AS uuid), :stype,
    :sturn, CAST(:sbatch AS uuid), CAST(:sgolden AS uuid), CAST(:golden AS uuid), CAST(:input AS jsonb),
    CAST(:state AS jsonb), CAST(:expected AS jsonb), CAST(:supersedes AS uuid), CAST(:by AS uuid), :email)"""


def _case_params(case: Mapping[str, Any], actor_id: str | None, actor_email: str | None) -> dict:
    rate = case.get("baseline_pass_rate")
    return {"id": case["id"], "org": case["organization_id"], "key": case["case_key"], "version": int(case["version"]),
            "status": case["status"], "kind": case["kind"], "rate": None if rate is None else Decimal(str(rate)),
            "mode": case.get("mode_id"), "stype": case["source_type"], "sturn": case.get("source_turn_id"),
            "sbatch": case.get("source_batch_id"), "sgolden": case.get("source_golden_case_id"),
            "golden": case.get("golden_case_id"), "input": json.dumps(public(case["input"])),
            "state": json.dumps(public(case.get("initial_state") or {})), "expected": json.dumps(public(case["expected"])),
            "supersedes": case.get("supersedes"), "by": actor_id, "email": actor_email}


def _is_case_conflict(exc: IntegrityError) -> bool:
    """A second current version of one case key, or a repeated key and version (the case's own unique rules)."""
    return "ux_eval_cases" in str(getattr(exc, "orig", exc))


async def _insert_case(case: dict, twin: Mapping[str, Any] | None, *, actor_id: str | None, actor_email: str | None,
                       supersede: tuple[str, int] | None = None) -> dict:
    """Make the Regression cases set when it is missing (its own transaction, idempotent), then one transaction:
    supersede the old version (edit), create the twin, insert the case. Returns the stored case."""
    set_id = await _regression_set_id(case["organization_id"], actor_id, actor_email) if twin is not None else None
    try:
        async with db.lab_engine().begin() as conn:
            if supersede is not None:
                res = await conn.execute(text(
                    "UPDATE tracelab.eval_cases SET status = 'superseded', rev = rev + 1, updated_at = now() "
                    "WHERE id = CAST(:id AS uuid) AND rev = :base AND status IN ('draft', 'baselining', 'active')"),
                    {"id": supersede[0], "base": int(supersede[1])})
                if res.rowcount == 0:
                    raise WatchError(409, "stale_base", "This case changed since you opened it. Reload it and try again.")
            if twin is not None:
                case["golden_case_id"] = await _add_twin(conn, set_id=str(set_id), **twin)
            await conn.execute(text(_INSERT_CASE), _case_params(case, actor_id, actor_email))
    except IntegrityError as exc:
        if _is_case_conflict(exc):
            raise WatchError(409, "case_exists", "A case with this key already exists; reload and try again.") from None
        raise
    return await _case(str(case["organization_id"]), str(case["id"])) or case


async def _with_twin(twin: Mapping[str, Any], *, actor_id: str | None, actor_email: str | None, update_sql: str,
                     params: Mapping[str, Any]) -> str:
    """Creates the twin and runs one guarded UPDATE of the case in one transaction; 0 rows rolls the twin back."""
    set_id = await _regression_set_id(str(twin["org"]), actor_id, actor_email)
    async with db.lab_engine().begin() as conn:
        golden_id = await _add_twin(conn, set_id=set_id, **twin)
        res = await conn.execute(text(update_sql), {**params, "g": golden_id})
        if res.rowcount == 0:
            raise WatchError(409, "stale_base", "This case changed since you opened it. Reload it and try again.")
    return golden_id


async def _mark_draft(case_id: str, base: int) -> None:
    """A proposed case that already has its twin becomes a draft (the failure is confirmed)."""
    async with db.lab_engine().begin() as conn:
        res = await conn.execute(text(
            "UPDATE tracelab.eval_cases SET status = 'draft', rev = rev + 1, updated_at = now() "
            "WHERE id = CAST(:id AS uuid) AND rev = :base AND status = 'proposed'"),
            {"id": str(case_id), "base": int(base)})
        if res.rowcount == 0:
            raise WatchError(409, "stale_base", "This case changed since you opened it. Reload it and try again.")


def _new_case(org: str, key: str, *, status: str, source_type: str, inp: Mapping[str, Any],
              expected: Mapping[str, Any], mode_id: str | None = None, initial_state: Mapping[str, Any] | None = None,
              source_turn_id: str | None = None, source_batch_id: str | None = None,
              source_golden_case_id: str | None = None) -> dict:
    return {"id": str(uuid.uuid4()), "organization_id": org, "case_key": key, "version": 1, "status": status,
            "kind": "unclassified", "baseline_pass_rate": None, "baseline_run_id": None,
            "mode_id": str(mode_id) if mode_id else None, "source_type": source_type,
            "source_turn_id": source_turn_id, "source_batch_id": source_batch_id,
            "source_golden_case_id": source_golden_case_id, "golden_case_id": None, "input": dict(inp),
            "initial_state": dict(initial_state or {"as_of": datetime.now(timezone.utc).date().isoformat()}),
            "expected": dict(expected), "supersedes": None, "retired_reason": None, "rev": 1}


def _twin_from(case: Mapping[str, Any], *, expected_answer: str, evaluation: str,
               values: Sequence[Mapping[str, Any]], source: str) -> dict:
    inp = case["input"]
    return {"org": str(case["organization_id"]), "case_key": twin_key(case["case_key"], int(case["version"])),
            "question": inp["question"], "followups": list(inp.get("followups") or []),
            "expected_answer": expected_answer.strip()[:6000], "evaluation": evaluation,
            "expected_values": list(values), "page_context": inp.get("page_context"), "source": source}


def _require_answer(expected_answer: str | None) -> str:
    if not (expected_answer or "").strip():
        raise WatchError(422, "expected_answer_required",
                         "Write what a right answer says before the case can run.")
    return expected_answer.strip()


async def _answer_input(turn: Mapping[str, Any], turn_id: str, override: Mapping[str, Any] | None) -> dict:
    """The case input of one chat answer: the conversation as the judge store reads it (the live row, else its
    evidence snapshot), else the answer's own question; checked against what the twin can hold."""
    loaded = (await judges.load_judge_turns([turn_id])).get(turn_id)
    inp = case_from_turn(loaded if loaded is not None else {"question": turn["question"], "prior": []}, turn)
    return merge_input(inp, override)


# ---- cases API (spec 5.1, 5.2, 7.3) ---------------------------------------------------------------------------

async def create_case(org: str, *, source: Mapping[str, Any], mode_id: str | None = None,
                      input_override: Mapping[str, Any] | None = None, initial_state: Mapping[str, Any] | None = None,
                      expected: Mapping[str, Any] | None = None, expected_answer: str | None = None,
                      evaluation: str = "objective", expected_values: str | None = None,
                      actor_id: str | None, actor_email: str | None) -> dict:
    in_use = {j["mode_id"]: j for j in await judges_in_use(None)}
    exp = normalise_expected(expected, modes_in_use=in_use)
    if mode_id and not await _mode_exists(mode_id):
        raise WatchError(404, "mode_not_found", "That failure mode was not found.")
    kind = (source or {}).get("type")
    key = new_case_key()
    if kind == "live_turn":
        turn_id = str(source.get("turn_id") or "")
        turn = await _turn_for_case(org, turn_id)
        if turn is None:
            raise WatchError(404, "turn_not_in_scope", "That answer is not in this company.")
        if not replayable(turn["question"], turn["channel"], turn["session_id"]):
            raise WatchError(409, "turn_not_replayable", "Only chat answers that ask for no change can become cases.")
        if not await _failure_confirmed(org, turn_id, mode_id):
            raise WatchError(409, "failure_not_confirmed",
                             "A person must first mark this answer wrong, or label the failure present.")
        if await _live_case_for_turn(org, turn_id):
            raise WatchError(409, "case_exists", "This answer already has a case.")
        answer = _require_answer(expected_answer)
        values = parse_values(expected_values)
        await _keep_evidence(org, turn_id)
        inp = await _answer_input(turn, turn_id, input_override)
        case = _new_case(org, key, status="draft", source_type="live_turn", inp=inp, expected=exp, mode_id=mode_id,
                         initial_state=initial_state, source_turn_id=turn_id)
        twin = _twin_from(case, expected_answer=answer, evaluation=evaluation, values=values,
                          source=f"Eval case {key} v1, from a reviewed answer")
    elif kind == "golden_case":
        g = await _golden_case(org, str(source.get("golden_case_id") or ""))
        if g is None:
            raise WatchError(404, "not_found", "That golden case was not found.")
        inp = merge_input({"question": g["question"], "followups": list(_json(g["followups"]) or []),
                           "page_context": g["page_context"], "scope": {"asker_role": None, "building_ids": None}},
                          input_override)
        case = _new_case(org, key, status="draft", source_type="golden_case", inp=inp, expected=exp, mode_id=mode_id,
                         initial_state=initial_state, source_golden_case_id=str(g["id"]))
        twin = _twin_from(case, expected_answer=g["expected_answer"], evaluation=g["evaluation"],
                          values=list(_json(g["expected_values"]) or []), source=f"Eval case {key} v1, from a golden case")
    else:
        raise WatchError(422, "bad_source", "A case comes from a reviewed answer or a golden case.")
    saved = await _insert_case(case, twin, actor_id=actor_id, actor_email=actor_email)
    log.info("trace_lab.eval_cases.created", case_key=key, source=kind)
    return case_public(saved)


async def confirm_case(org: str, case_id: str, *, base: int, expected_answer: str | None = None,
                       evaluation: str = "objective", expected_values: str | None = None, actor_id: str | None,
                       actor_email: str | None) -> dict:
    """A proposed case becomes a draft once a person has confirmed the failure. Its twin is made here, from the
    expected answer the person writes - unless a repeat-n request already made one with a written answer."""
    case = await _case(org, case_id)
    if case is None:
        raise WatchError(404, "not_found", "That case was not found.")
    if case["status"] != "proposed":
        raise WatchError(409, "not_proposed", "Only a proposed case is confirmed.")
    if int(case["rev"]) != int(base):
        raise WatchError(409, "stale_base", "This case changed since you opened it. Reload it and try again.")
    if not await _failure_confirmed(org, case["source_turn_id"], str(case["mode_id"]) if case.get("mode_id") else None):
        raise WatchError(409, "failure_not_confirmed",
                         "A person must first mark this answer wrong, or label the failure present.")
    if case.get("golden_case_id"):
        await _mark_draft(str(case["id"]), int(base))
        return case_public(await _case(org, case_id) or case)
    answer = _require_answer(expected_answer)
    case = dict(case, input=_json(case["input"]))
    twin = _twin_from(case, expected_answer=answer, evaluation=evaluation, values=parse_values(expected_values),
                      source=f"Eval case {case['case_key']} v{case['version']}, from a monitoring flag")
    await _with_twin(twin, actor_id=actor_id, actor_email=actor_email,
                     update_sql="UPDATE tracelab.eval_cases SET status = 'draft', golden_case_id = CAST(:g AS uuid), "
                                "rev = rev + 1, updated_at = now() WHERE id = CAST(:id AS uuid) AND rev = :base "
                                "AND status = 'proposed'",
                     params={"id": str(case["id"]), "base": int(base)})
    return case_public(await _case(org, case_id) or case)


async def edit_case(org: str, case_id: str, *, base: int, input_override: Mapping[str, Any] | None = None,
                    initial_state: Mapping[str, Any] | None = None, expected: Mapping[str, Any] | None = None,
                    expected_answer: str | None = None, evaluation: str | None = None,
                    expected_values: str | None = None, actor_id: str | None, actor_email: str | None) -> dict:
    old = await _case(org, case_id)
    if old is None:
        raise WatchError(404, "not_found", "That case was not found.")
    if old["status"] in ("retired", "superseded"):
        raise WatchError(409, "case_retired", "This case is retired or replaced by a newer version.")
    if old["status"] == "proposed":
        raise WatchError(409, "not_confirmed", "Confirm the case first; that is where its expected answer is written.")
    if int(old["rev"]) != int(base):
        raise WatchError(409, "stale_base", "This case changed since you opened it. Reload it and try again.")
    exp = None
    if expected is not None:
        exp = normalise_expected(expected, modes_in_use={j["mode_id"]: j for j in await judges_in_use(None)})
    new = next_version(old, input_override=input_override, initial_state=initial_state, expected=exp)
    prev = await _twin(old.get("golden_case_id")) or {}
    twin = _twin_from(new, expected_answer=(expected_answer or prev.get("expected_answer") or ""),
                      evaluation=evaluation or prev.get("evaluation") or "objective",
                      values=(parse_values(expected_values) if expected_values is not None
                              else list(_json(prev.get("expected_values")) or [])),
                      source=f"Eval case {new['case_key']} v{new['version']}")
    _require_answer(twin["expected_answer"])
    saved = await _insert_case(new, twin, actor_id=actor_id, actor_email=actor_email, supersede=(str(old["id"]), int(base)))
    return case_public(saved)


async def retire_case(org: str, case_id: str, *, base: int, reason: str) -> dict:
    if not _is_uuid(case_id):
        raise WatchError(404, "not_found", "That case was not found.")
    async with db.lab_engine().begin() as conn:
        row = (await conn.execute(text(
            "UPDATE tracelab.eval_cases SET status = 'retired', retired_reason = :reason, rev = rev + 1, "
            "updated_at = now() WHERE id = CAST(:id AS uuid) AND organization_id = CAST(:org AS uuid) AND rev = :base "
            "AND status IN ('proposed', 'draft', 'baselining', 'active') RETURNING id"),
            {"reason": reason.strip()[:500], "id": str(case_id), "org": org, "base": int(base)})).first()
    if row is None:
        if await _case(org, case_id) is None:
            raise WatchError(404, "not_found", "That case was not found.")
        raise WatchError(409, "stale_base", "This case changed since you opened it, or is already retired.")
    return case_public(await _case(org, case_id) or {})


async def list_cases(org: str, *, status: str | None = None, kind: str | None = None,
                     mode_id: str | None = None) -> list[dict]:
    async with db.lab_engine().connect() as conn:
        rows = (await conn.execute(text(_CASE_SELECT + """
 WHERE c.organization_id = CAST(:org AS uuid)
   AND ((CAST(:status AS text) IS NULL AND c.status <> 'superseded') OR c.status = CAST(:status AS text))
   AND (CAST(:kind AS text) IS NULL OR c.kind = CAST(:kind AS text))
   AND (CAST(:mode AS text) IS NULL OR c.mode_id::text = CAST(:mode AS text))
 ORDER BY c.updated_at DESC LIMIT 500"""), {"org": org, "status": status, "kind": kind, "mode": mode_id})).mappings().all()
    return [case_public(r) for r in rows]


async def get_case(org: str, case_id: str) -> dict:
    case = await _case(org, case_id)
    if case is None:
        raise WatchError(404, "not_found", "That case was not found.")
    async with db.lab_engine().connect() as conn:
        runs = (await conn.execute(text(
            "SELECT r.* FROM tracelab.eval_case_runs r JOIN tracelab.golden_runs g ON g.id = r.golden_run_id "
            "WHERE r.eval_case_id = CAST(:id AS uuid) ORDER BY g.started_at DESC, r.computed_at DESC"),
            {"id": str(case_id)})).mappings().all()
    twin = await _twin(case.get("golden_case_id"))
    return {"case": case_public(case), "runs": public([dict(r, reps=_json(r["reps"]), graders=_json(r["graders"]),
                                                            summary=_json(r["summary"])) for r in runs]),
            "twin": public({"case_key": twin["case_key"], "expected_answer": twin["expected_answer"],
                            "evaluation": twin["evaluation"]}) if twin else None}


async def propose_from_monitor(*, batch: Mapping[str, Any], mode_id: str, turn_ids: Sequence[str]) -> list[str]:
    """Up to PROPOSE_CASES_MAX flagged answers become proposed cases (spec 4.2 step 7). No twin yet: confirming is
    where a person writes the expected answer. An answer that already has a live case, cannot replay, is too long for
    a twin, or whose evidence cannot be kept is skipped; the rest are still proposed."""
    ids: list[str] = []
    for turn_id in list(turn_ids)[:monitor.PROPOSE_CASES_MAX]:
        turn = await _turn_for_case(None, turn_id)
        if turn is None or not replayable(turn["question"], turn["channel"], turn["session_id"]):
            continue
        org = str(turn["organization_id"])
        if await _live_case_for_turn(org, turn_id):
            continue
        try:
            await _keep_evidence(org, turn_id)
            inp = await _answer_input(turn, turn_id, None)
        except WatchError as exc:
            log.info("trace_lab.eval_cases.not_proposed", turn_id=turn_id, reason=exc.reason)
            continue
        case = _new_case(org, new_case_key(), status="proposed", source_type="monitor", inp=inp, mode_id=mode_id,
                         source_turn_id=turn_id, source_batch_id=str(batch["id"]),
                         expected={"assertions": [], "checks": [], "judges": [{"mode_id": str(mode_id)}],
                                   "golden_expected": True})
        await _insert_case(case, None, actor_id=str(batch["started_by"]) if batch.get("started_by") else None,
                           actor_email=batch.get("started_by_email"))
        ids.append(case["id"])
    return ids


# ---- runs through the golden runner (spec 5.3-5.7) ------------------------------------------------------------

@dataclass(frozen=True)
class RunContext:
    """What runner.start_run needs from the request: the company, the caller (runs are browser-stepped, so the
    caller's own bearer is passed through for this one call and never stored) and nothing else: the runner finds the
    orchestrator itself (orchestrator_host). A superadmin acting for a company is the caller; org is the company."""
    org: str
    principal: Any
    authorization: str | None


def _run_body(*, set_id: str, case_keys: Sequence[str], k: int, purpose: str, usd_cap: float | None, note: str) -> dict:
    """The request body of one golden run over the named cases of a set, as the runner reads it (a plain mapping is a
    request body to runner.start_run and runner.estimate)."""
    return {"set_id": str(set_id), "case_keys": list(case_keys), "k": int(k), "purpose": purpose, "usd_cap": usd_cap,
            "context_default": "", "note": note[:500]}


async def _start_golden_run(*, ctx: RunContext, set_id: str, case_keys: Sequence[str], k: int, purpose: str,
                            usd_cap: float | None, note: str) -> dict:
    """One golden run over the named cases of the set (runner.start_run). Returns the run summary (with its id)."""
    body = _run_body(set_id=set_id, case_keys=case_keys, k=k, purpose=purpose, usd_cap=usd_cap, note=note)
    out = await runner.start_run(org=ctx.org, principal=ctx.principal, authorization=ctx.authorization, body=body)
    return dict(out["run"])


START_GOLDEN_RUN: Callable[..., Awaitable[dict]] = _start_golden_run   # tests replace this: never a real run in tests


def _check_reps(k: int) -> None:
    """The golden runner owns the rep cap (TRACE_LAB_GOLDEN_MAX_K, never above runner.HARD_MAX_K): refuse before a
    run starts. The per-purpose limits (baseline 5, release up to 10, repeat up to 20) are checked by each start_*."""
    cap = runner.max_k()
    if int(k) > cap:
        raise WatchError(422, "too_many_reps", f"This deployment allows at most {cap} runs per case.")


async def _twin_ref(case: Mapping[str, Any]) -> tuple[str, str]:
    twin = await _twin(case.get("golden_case_id"))
    if twin is None:
        raise WatchError(409, "expected_answer_required",
                         "Confirm the case and write its expected answer before it can run.")
    return str(twin["set_id"]), str(twin["case_key"])


async def _run_open(golden_run_id: Any) -> bool:
    if not golden_run_id:
        return False
    async with db.lab_engine().connect() as conn:
        status = (await conn.execute(text("SELECT j.status FROM tracelab.golden_runs r JOIN tracelab.jobs j "
                                          "ON j.id = r.job_id WHERE r.id = CAST(:id AS uuid)"),
                                     {"id": str(golden_run_id)})).scalar()
    return status in jobs.OPEN_STATUSES


async def _add_case_runs(rows: Sequence[Mapping[str, Any]]) -> None:
    if not rows:
        return
    async with db.lab_engine().begin() as conn:
        await conn.execute(text("INSERT INTO tracelab.eval_case_runs (id, eval_case_id, golden_run_id, purpose, k) "
                                "VALUES (CAST(:id AS uuid), CAST(:case_id AS uuid), CAST(:run_id AS uuid), :purpose, :k)"),
                           [{"id": str(uuid.uuid4()), **dict(r)} for r in rows])


async def _begin_baseline(case_id: str, golden_run_id: str) -> None:
    async with db.lab_engine().begin() as conn:
        await conn.execute(text("INSERT INTO tracelab.eval_case_runs (id, eval_case_id, golden_run_id, purpose, k) "
                                "VALUES (CAST(:id AS uuid), CAST(:case_id AS uuid), CAST(:run_id AS uuid), 'baseline', :k)"),
                           {"id": str(uuid.uuid4()), "case_id": case_id, "run_id": golden_run_id, "k": BASELINE_K})
        await conn.execute(text("UPDATE tracelab.eval_cases SET status = 'baselining', "
                                "baseline_run_id = CAST(:run_id AS uuid), rev = rev + 1, updated_at = now() "
                                "WHERE id = CAST(:case_id AS uuid) AND status IN ('draft', 'baselining')"),
                           {"case_id": case_id, "run_id": golden_run_id})


async def _active_cases(org: str) -> list[dict]:
    async with db.lab_engine().connect() as conn:
        rows = (await conn.execute(text(_CASE_SELECT + " WHERE c.organization_id = CAST(:org AS uuid) "
                                        "AND c.status = 'active' ORDER BY c.case_key"), {"org": org})).mappings().all()
    return [dict(r) for r in rows]


async def start_baseline(ctx: RunContext, case_id: str, *, usd_cap: float | None = None) -> dict:
    """Spec 5.4: a golden run with purpose 'baseline' and k = 5 on the case's twin."""
    case = await _case(ctx.org, case_id)
    if case is None:
        raise WatchError(404, "not_found", "That case was not found.")
    if case["status"] == "proposed":
        raise WatchError(409, "not_confirmed", "Confirm the failure and write the expected answer first.")
    if case["status"] in ("retired", "superseded"):
        raise WatchError(409, "case_retired", "This case is retired or replaced by a newer version.")
    if case["status"] == "active" or case["kind"] != "unclassified":
        raise WatchError(409, "already_classified", "This case already has a baseline. Edit it to start a new version.")
    if case["status"] == "baselining" and await _run_open(case.get("baseline_run_id")):
        raise WatchError(409, "baseline_running", "The baseline for this case is still running; resume it from Runs.")
    _check_reps(BASELINE_K)
    set_id, key = await _twin_ref(case)
    run = await START_GOLDEN_RUN(ctx=ctx, set_id=set_id, case_keys=[key], k=BASELINE_K, purpose="baseline",
                                 usd_cap=usd_cap, note=f"Baseline for case {case['case_key']} v{case['version']}")
    await _begin_baseline(str(case["id"]), str(run["id"]))
    return {"golden_run": public(run)}


async def start_repeat(ctx: RunContext, case_id: str, *, n: int, usd_cap: float | None = None) -> dict:
    """Spec 5.7: one golden run with purpose 'repeat' and k = n on the case's twin."""
    if not REPEAT_MIN_N <= int(n) <= REPEAT_MAX_N:
        raise WatchError(422, "bad_n", f"Repeat an answer between {REPEAT_MIN_N} and {REPEAT_MAX_N} times.")
    _check_reps(n)
    case = await _case(ctx.org, case_id)
    if case is None:
        raise WatchError(404, "not_found", "That case was not found.")
    if case["status"] not in LIVE_STATUSES:
        raise WatchError(409, "case_retired", "This case is retired or replaced by a newer version.")
    set_id, key = await _twin_ref(case)
    run = await START_GOLDEN_RUN(ctx=ctx, set_id=set_id, case_keys=[key], k=int(n), purpose="repeat", usd_cap=usd_cap,
                                 note=f"Repeat {int(n)} times: case {case['case_key']} v{case['version']}")
    await _add_case_runs([{"case_id": str(case["id"]), "run_id": str(run["id"]), "purpose": "repeat", "k": int(n)}])
    return {"golden_run": public(run), "case": case_public(case)}


async def repeat_turn(ctx: RunContext, turn_id: str, *, n: int, mode_ids: Sequence[str],
                      expected_answer: str | None = None, evaluation: str = "objective", usd_cap: float | None = None,
                      actor_id: str | None, actor_email: str | None) -> dict:
    """Repeat-n of one answer: reuse its live case, or make one (draft when the failure is confirmed, else proposed)
    with a twin from the written expected answer, then start the repeat run."""
    turn = await _turn_for_case(ctx.org, turn_id)
    if turn is None:
        raise WatchError(404, "turn_not_in_scope", "That answer is not in this company.")
    if not replayable(turn["question"], turn["channel"], turn["session_id"]):
        raise WatchError(409, "turn_not_replayable", "Only chat answers that ask for no change can be repeated.")
    in_use = {j["mode_id"]: j for j in await judges_in_use(None)}
    modes = list(dict.fromkeys(str(m) for m in mode_ids or []))
    if not modes or any(m not in in_use for m in modes):
        raise WatchError(409, "judge_not_frozen", "Every failure mode named must have a judge in use.")
    case = await _live_case_for_turn(ctx.org, turn_id)
    if case is None:
        answer = _require_answer(expected_answer)
        confirmed = await _failure_confirmed(ctx.org, turn_id, None)
        await _keep_evidence(ctx.org, turn_id)
        inp = await _answer_input(turn, turn_id, None)
        new = _new_case(ctx.org, new_case_key(), status="draft" if confirmed else "proposed", source_type="live_turn",
                        inp=inp, mode_id=modes[0], source_turn_id=turn_id,
                        expected={"assertions": [], "checks": [], "judges": [{"mode_id": m} for m in modes],
                                  "golden_expected": True})
        await _insert_case(new, _twin_from(new, expected_answer=answer, evaluation=evaluation, values=[],
                                           source=f"Eval case {new['case_key']} v1, repeated from an answer"),
                           actor_id=actor_id, actor_email=actor_email)
        case_id = new["id"]
    else:
        case_id = str(case["id"])
        if not case.get("golden_case_id"):
            answer = _require_answer(expected_answer)
            current = dict(case, input=_json(case["input"]))
            await _with_twin(_twin_from(current, expected_answer=answer, evaluation=evaluation, values=[],
                                        source=f"Eval case {case['case_key']} v{case['version']}, repeated from an answer"),
                             actor_id=actor_id, actor_email=actor_email,
                             update_sql="UPDATE tracelab.eval_cases SET golden_case_id = CAST(:g AS uuid), "
                                        "rev = rev + 1, updated_at = now() WHERE id = CAST(:id AS uuid) "
                                        "AND golden_case_id IS NULL",
                             params={"id": case_id})
    return await start_repeat(ctx, case_id, n=n, usd_cap=usd_cap)


async def start_release_check(ctx: RunContext, *, k: int = RELEASE_DEFAULT_K, include_capability: bool = True,
                              usd_cap: float | None = None) -> dict:
    """Spec 5.6: one golden run, purpose 'release_check', over the company's active cases (all twins live in its
    Regression cases set). It measures the deployment serving this page, read-only."""
    if not 1 <= int(k) <= RELEASE_MAX_K:
        raise WatchError(422, "bad_k", f"A release check runs each case 1 to {RELEASE_MAX_K} times.")
    _check_reps(k)
    cases = await _active_cases(ctx.org)
    regression = [c for c in cases if c["kind"] == "regression"]
    chosen = regression + ([c for c in cases if c["kind"] == "capability"] if include_capability else [])
    if not chosen:
        raise WatchError(409, "no_active_cases", "No case has a baseline yet, so there is nothing to run.")
    refs = [await _twin_ref(c) for c in chosen]
    set_ids = {s for s, _ in refs}
    if len(set_ids) != 1:
        raise WatchError(409, "cases_in_several_sets", "These cases do not share one Regression cases set.")
    run = await START_GOLDEN_RUN(ctx=ctx, set_id=set_ids.pop(), case_keys=[key for _, key in refs], k=int(k),
                                 purpose="release_check", usd_cap=usd_cap,
                                 note=f"Release check: {len(regression)} regression and "
                                      f"{len(chosen) - len(regression)} capability cases")
    await _add_case_runs([{"case_id": str(c["id"]), "run_id": str(run["id"]), "purpose": "release_check", "k": int(k)}
                          for c in chosen])
    return {"golden_run": public(run), "cases": {"regression": len(regression), "capability": len(chosen) - len(regression)}}


# ---- grading reps (spec 5.3) ----------------------------------------------------------------------------------

def _fingerprint(params: Any) -> dict:
    """What the run recorded about the deployment it measured (runner.like_for_like, kept in golden_runs.params):
    the build, the chat model, the judge model, the set and the routing flags."""
    p = _json(params) or {}
    return {"image": p.get("git_sha"), "openai_model": p.get("chat_model"), "grader_model": p.get("grader_model"),
            "set_hash": p.get("set_hash"), "flags": dict(p.get("flags") or {})}


async def _golden_run(org: str, golden_run_id: str) -> dict | None:
    """One golden run of the company with its job's status and spend; `started_at` is when it began."""
    if not _is_uuid(golden_run_id):
        return None
    async with db.lab_engine().connect() as conn:
        row = (await conn.execute(text("""
SELECT r.id, r.label, r.purpose, r.k, r.set_id, r.job_id, r.params, r.started_at, r.finished_at, r.organization_id,
       coalesce(j.status, r.status) AS job_status, coalesce(j.usd_spent, r.usd_spent) AS usd_spent
  FROM tracelab.golden_runs r LEFT JOIN tracelab.jobs j ON j.id = r.job_id
 WHERE r.id = CAST(:id AS uuid) AND r.organization_id = CAST(:org AS uuid)"""),
            {"id": str(golden_run_id), "org": org})).mappings().first()
    return dict(row, fingerprint=_fingerprint(row["params"])) if row else None


async def _case_runs(golden_run_id: str) -> list[dict]:
    async with db.lab_engine().connect() as conn:
        rows = (await conn.execute(text("""
SELECT cr.*, c.organization_id, c.case_key, c.version, c.status AS case_status, c.kind, c.baseline_pass_rate,
       c.baseline_run_id, c.expected, c.mode_id, m.name AS mode_name, gc.case_key AS twin_key
  FROM tracelab.eval_case_runs cr
  JOIN tracelab.eval_cases c ON c.id = cr.eval_case_id
  LEFT JOIN tracelab.modes m ON m.id = c.mode_id
  JOIN tracelab.golden_cases gc ON gc.id = c.golden_case_id
 WHERE cr.golden_run_id = CAST(:run AS uuid)
 ORDER BY c.case_key"""), {"run": str(golden_run_id)})).mappings().all()
    return [dict(r) for r in rows]


async def _finals(golden_run_id: str) -> dict[tuple[str, int], dict]:
    """The final row of each (case, rep): its last message (the highest msg_idx), which carries the item's outcome and
    graded_at, and the turn of the answer that was graded (None when the conversation stopped before it)."""
    async with db.lab_engine().connect() as conn:
        rows = (await conn.execute(text("""
SELECT DISTINCT ON (case_key, rep) case_key, rep, msg_idx, turn_id, outcome, graded_at
  FROM tracelab.golden_results
 WHERE run_id = CAST(:run AS uuid)
 ORDER BY case_key, rep, msg_idx DESC"""), {"run": str(golden_run_id)})).mappings().all()
    return {(r["case_key"], int(r["rep"])): dict(r) for r in rows}


async def _result_row(result_id: str) -> dict | None:
    async with db.lab_engine().connect() as conn:
        row = (await conn.execute(text("SELECT run_id, organization_id, case_key, rep FROM tracelab.golden_results "
                                       "WHERE id = CAST(:id AS uuid)"), {"id": result_id})).mappings().first()
    return dict(row) if row else None


async def _save_case_run(case_run_id: str, reps: Sequence[Mapping[str, Any]], summary: Mapping[str, Any],
                         graders: Mapping[str, Any], repeat: Mapping[str, Any] | None) -> None:
    def num(v: Any) -> Decimal | None:
        return None if v is None else Decimal(str(v))

    async with db.lab_engine().begin() as conn:
        await conn.execute(text("""
UPDATE tracelab.eval_case_runs
   SET reps = CAST(:reps AS jsonb), n = :n, passes = :passes, degraded = :degraded, pending = :pending,
       graders = CAST(:graders AS jsonb), pass_at_1 = :p1, pass_at_3 = :p3, pass_at_5 = :p5, pass_hat_k = :phk,
       summary = CAST(:summary AS jsonb), decision = :decision, reason = :reason, computed_at = now()
 WHERE id = CAST(:id AS uuid)"""),
            {"id": case_run_id, "reps": json.dumps(public(list(reps))), "n": summary["n"], "passes": summary["passes"],
             "degraded": summary["degraded"], "pending": summary["pending"] + summary["missing"],
             "graders": json.dumps(public(dict(graders))), "p1": num(summary["pass_at_1"]),
             "p3": num(summary["pass_at_3"]), "p5": num(summary["pass_at_5"]), "phk": num(summary["pass_hat_k"]),
             "summary": json.dumps(public(dict(repeat))) if repeat is not None else None,
             "decision": summary["decision"], "reason": summary["reason"][:1000]})


async def _classify(case_id: str, classification: Mapping[str, Any], golden_run_id: str) -> None:
    rate = classification.get("baseline_pass_rate")
    async with db.lab_engine().begin() as conn:
        await conn.execute(text("""
UPDATE tracelab.eval_cases
   SET status = 'active', kind = :kind, baseline_pass_rate = :rate, rev = rev + 1, updated_at = now()
 WHERE id = CAST(:id AS uuid) AND status = 'baselining' AND baseline_run_id = CAST(:run AS uuid)"""),
            {"id": case_id, "kind": classification["kind"], "rate": None if rate is None else Decimal(str(rate)),
             "run": golden_run_id})
    log.info("trace_lab.eval_cases.classified", case_id=case_id, kind=classification["kind"])


async def _judge_turn(j: Mapping[str, Any], turn: Any, *, org: str, allow_model_calls: bool,
                      job_id: str | None) -> tuple[dict, Decimal]:
    """A judge in use on one rep's answer: a code judge runs its pinned check (free; a check that changed since the
    judge was registered is an error), the prediction cache is read first (free), and an LLM call is made only when
    allowed - charged to job_id under its cap. A call the job cannot pay for leaves the rep ungraded."""
    head = {"mode": j["mode_name"], "mode_id": j["mode_id"], "judge_id": j.get("judge_id"), "judge_key": j["judge_key"],
            "prompt_hash": j["prompt_hash"]}                          # the head stays on the rep: it names what decided it
    if j["kind"] == "code":
        pred = judges.code_prediction(j, turn)
        decided = pred.get("status") == "decided"
        return {**head, "status": "decided" if decided else "error",
                "failure_present": pred.get("failure_present") if decided else None}, Decimal(0)
    tid = str(_field(turn, "turn_id"))
    cached = (await monitor._predictions(j["prompt_hash"], [tid])).get(tid)
    if cached is not None:
        return {**head, "status": "decided", "failure_present": int(cached["failure_present"])}, Decimal(0)
    if not allow_model_calls or not job_id:
        return {**head, "status": "ungraded", "failure_present": None}, Decimal(0)
    inp = judges.judge_input(turn)
    chars = int(_field(inp, "chars", 0) or 0)
    est = monitor.call_usd(j["model"], j["prompt_text"] or "", chars) or Decimal(0)
    try:
        await jobs.reserve(job_id, est)
    except (jobs.CapReached, jobs.JobNotRunning):
        return {**head, "status": "ungraded", "failure_present": None}, Decimal(0)
    try:
        reply = await judges.ASK(j["model"], j["prompt_text"], _field(inp, "text"))
    except Exception as exc:  # noqa: BLE001 - a failed call is an error, never a pass
        reply = SimpleNamespace(status="error", failure_present=None, critique="", usd=None, error=type(exc).__name__)
    prediction = monitor.prediction_args(judge=j, turn_id=tid, organization_id=org, reply=reply, inp=inp)
    pred = prediction["pred"]                                         # written through the judge store's write_prediction
    await monitor._save(job_id, verdicts=[], usd=pred["usd"], released=est, prediction=prediction)
    return {**head, "status": pred["status"], "failure_present": pred["failure_present"]}, pred["usd"] or Decimal(0)


async def _grade(cr: Mapping[str, Any], rep: int, row: Mapping[str, Any] | None, expected: Mapping[str, Any],
                 in_use: Mapping[str, Mapping[str, Any]], *, allow_model_calls: bool, job_id: str | None) -> dict:
    """One rep from its final golden row: its checks, its judges, and (when the case uses it) the golden grade."""
    golden_expected = bool(expected.get("golden_expected"))
    if row is None:                                                    # nothing stored for this rep yet
        return rep_verdict(rep=rep, turn_id=None, outcome=None, status=None, check_results=[], judge_results=[],
                           golden_expected=golden_expected)
    if row["outcome"] in ("degraded", "error"):                       # never graded; no answer needs reading
        return rep_verdict(rep=rep, turn_id=row["turn_id"], outcome=row["outcome"], status="completed",
                           check_results=[], judge_results=[], golden_expected=golden_expected)
    turn = (await judges.load_judge_turns([row["turn_id"]])).get(row["turn_id"]) if row["turn_id"] else None
    if turn is None:
        return rep_verdict(rep=rep, turn_id=None, outcome=row["outcome"], status="completed", check_results=[],
                           judge_results=[], golden_expected=golden_expected)
    check_results = []
    for c in expected.get("checks") or []:
        key = c["check"]
        result = checks.run_check(checks.CHECKS[key], turn, {k: v for k, v in c.items() if k != "check"})
        check_results.append({"key": key, "status": result.status, "findings": list(result.findings or ())[:3]})
    judge_results, usd = [], Decimal(0)
    for jd in expected.get("judges") or []:
        j = in_use.get(str(jd["mode_id"]))
        if j is None:
            judge_results.append({"mode": cr.get("mode_name") or str(jd["mode_id"]), "mode_id": str(jd["mode_id"]),
                                  "status": "error", "failure_present": None})
            continue
        result, spent = await _judge_turn(j, turn, org=str(cr["organization_id"]), allow_model_calls=allow_model_calls,
                                          job_id=job_id)
        judge_results.append(result)
        usd += spent
    return rep_verdict(rep=rep, turn_id=row["turn_id"], outcome=row["outcome"], status="completed",
                       check_results=check_results, judge_results=judge_results, golden_expected=golden_expected,
                       usd=float(usd), graded_at=datetime.now(timezone.utc).isoformat(),
                       steps=int(_field(turn, "tool_calls", 0) or 0))


def _graders(expected: Mapping[str, Any], reps: Sequence[Mapping[str, Any]]) -> dict:
    """What produced a case run's verdicts. The checks are the ones the case names. The judges are read off the reps
    themselves: each rep keeps the head of every judge version that decided it, and only a version that made a
    verdict is recorded. Never taken from the judges in use when the run is read: a judge replaced or retired since
    must not rewrite the versions a baseline or a release check was decided by (a rep that is decided is final)."""
    decided: dict[tuple[str, str], dict] = {}
    for rep in reps:
        for j in rep.get("judges") or []:
            if j.get("status") == "decided" and j.get("prompt_hash"):
                decided.setdefault((str(j["mode_id"]), str(j["prompt_hash"])),
                                   {"mode_id": j["mode_id"], "judge_id": j.get("judge_id"), "judge_key": j.get("judge_key"),
                                    "prompt_hash": j["prompt_hash"]})
    return {"checks": sorted({checks.CHECKS[c["check"]].evaluator_key for c in expected.get("checks") or []
                              if c.get("check") in checks.CHECKS}),
            "judges": [decided[key] for key in sorted(decided)]}


def _run_public(run: Mapping[str, Any]) -> dict:
    return public({"id": run["id"], "label": run.get("label"), "purpose": run.get("purpose"), "k": run.get("k"),
                   "status": run.get("job_status"), "started_at": run.get("started_at"),
                   "finished_at": run.get("finished_at"), "usd_spent": run.get("usd_spent")})


async def refresh_run(org: str, golden_run_id: str, *, allow_model_calls: bool, job_id: str | None = None,
                      only: tuple[str, int] | None = None) -> dict:
    """Grades every rep of every eval case on this golden run that has no final verdict yet, and stores each case
    run's summary and decision. passed/failed reps are final; the rest are recomputed. Without allow_model_calls
    nothing is spent: uncached judge calls leave the rep 'ungraded'. With it and no job_id the judge calls are
    charged to the golden run's own job. The graders stored with each case run come from the reps (see _graders), so
    reading a run again after a judge changed never rewrites the judge versions that decided it."""
    run = await _golden_run(org, golden_run_id)
    if run is None:
        raise WatchError(404, "not_found", "That run was not found.")
    case_runs = await _case_runs(golden_run_id)
    if not case_runs:
        return {"run": _run_public(run), "case_runs": []}
    finals = await _finals(golden_run_id)
    wanted = sorted({str(jd["mode_id"]) for cr in case_runs for jd in (_json(cr["expected"]) or {}).get("judges") or []})
    in_use = {j["mode_id"]: j for j in await judges_in_use(wanted)} if wanted else {}   # only the judges the cases name
    spend_job = job_id or (str(run["job_id"]) if allow_model_calls and run.get("job_id") else None)
    out = []
    for cr in case_runs:
        expected = _json(cr["expected"]) or {}
        previous = {int(r["rep"]): r for r in (_json(cr["reps"]) or [])}
        reps = []
        for rep in range(1, int(cr["k"]) + 1):
            old = previous.get(rep)
            if old and old.get("status") in DECIDED_REPS:
                reps.append(old)
            elif only is not None and (cr["twin_key"], rep) != only:
                reps.append(old or {"rep": rep, "status": "missing"})
            else:
                reps.append(await _grade(cr, rep, finals.get((cr["twin_key"], rep)), expected, in_use,
                                         allow_model_calls=allow_model_calls, job_id=spend_job))
        rate = float(cr["baseline_pass_rate"]) if cr.get("baseline_pass_rate") is not None else None
        summary = case_run_summary(reps, k=int(cr["k"]), purpose=cr["purpose"], kind=cr["kind"], baseline_pass_rate=rate)
        graders = _graders(expected, reps)
        repeat = repeat_summary(reps) if cr["purpose"] == "repeat" else None
        await _save_case_run(str(cr["id"]), reps, summary, graders, repeat)
        cls = summary.get("classification")
        if (cr["purpose"] == "baseline" and cls and cr.get("case_status") == "baselining"
                and str(cr.get("baseline_run_id")) == str(golden_run_id)):
            await _classify(str(cr["eval_case_id"]), cls, str(golden_run_id))
        out.append(public({"eval_case_id": cr["eval_case_id"], "case_key": cr["case_key"], "version": cr["version"],
                           "kind": cr["kind"], "mode": cr.get("mode_name"), "purpose": cr["purpose"],
                           "baseline_pass_rate": rate, "reps": reps, **summary, "graders": graders, "summary": repeat}))
    return {"run": _run_public(run), "case_runs": out}


async def grade_rep(result_id: str) -> dict | None:
    """Grades the one eval rep a golden result belongs to (judge calls allowed, charged to the run's job). Nothing
    in the golden runner calls this: reads grade for free through eval_run and "Grade now" through grade_job. It is
    kept for an explicit after-grade hook, should one be added. It never breaks a run: every error is logged and
    swallowed."""
    if not _is_uuid(result_id):
        return None
    try:
        row = await _result_row(str(result_id))
        if row is None:
            return None
        return await refresh_run(str(row["organization_id"]), str(row["run_id"]), allow_model_calls=True,
                                 only=(row["case_key"], int(row["rep"])))
    except Exception as exc:  # noqa: BLE001
        log.warning("trace_lab.eval_cases.grade_rep_failed", result_id=str(result_id), error=str(exc)[:300])
        return None


async def eval_run(org: str, golden_run_id: str) -> dict:
    """The read behind GET /eval-runs/{id}: checks and cached predictions only - a GET never spends money."""
    return await refresh_run(org, golden_run_id, allow_model_calls=False)


async def start_grading(org: str, golden_run_id: str, *, usd_cap: float | None, actor_id: str,
                        actor_email: str | None) -> dict:
    """'Grade now' (spec 5.3): a golden_grade job that makes the judge calls reads left ungraded. The golden runner
    has one worker per job kind (runner.start_grade_job claims the job and runs the handler its params name; after a
    restart runner.settle_after_restart starts every queued or interrupted golden_grade job the same way), so the job
    names its handler (params["handler"] == "eval_cases", registered below)."""
    if await _golden_run(org, golden_run_id) is None:
        raise WatchError(404, "not_found", "That run was not found.")
    cap = Decimal(str(usd_cap if usd_cap is not None else eval_grading_usd_cap()))
    try:
        job = await jobs.create(kind="golden_grade", scope_key=f"golden_grade:eval:{golden_run_id}", organization_id=org,
                                params={"handler": "eval_cases", "golden_run_id": str(golden_run_id),
                                        "organization_id": org}, usd_cap=cap, usd_estimate=None, total=None,
                                actor_id=actor_id, actor_email=actor_email)
    except jobs.JobConflict:
        raise WatchError(409, "grading_running", "These runs are already being graded.") from None
    job_id = str(job["id"])
    taken = await runner.start_grade_job(job_id)
    return {"job": {"id": job_id, "status": "running" if taken else "queued", "usd_cap": float(cap)}}


async def grade_job(job: Any) -> str:
    """The `eval_cases` handler of the golden runner's grading worker (job is its GradeJob). The worker has already
    claimed the job and finishes it with the status returned here, so there is no claim and no finish in this
    function: 'capped' while reps stay ungraded (the cost cap stopped the judge calls), else 'done'. A failure raises:
    the worker records the job as failed."""
    params = job.params
    out = await refresh_run(str(params["organization_id"]), str(params["golden_run_id"]), allow_model_calls=True,
                            job_id=str(job.job_id))
    left = sum(1 for cr in out["case_runs"] for r in cr["reps"] if r.get("status") == "ungraded")
    return "capped" if left else "done"


runner.register_grade_handler("eval_cases", grade_job)     # at import: the runner's restart settle starts jobs that name it


# ---- the release check: list and report (spec 5.6) ------------------------------------------------------------

async def _release_runs(org: str, limit: int) -> list[dict]:
    async with db.lab_engine().connect() as conn:
        rows = (await conn.execute(text("""
SELECT r.id, r.label, r.k, r.started_at, r.finished_at, coalesce(j.status, r.status) AS job_status
  FROM tracelab.golden_runs r LEFT JOIN tracelab.jobs j ON j.id = r.job_id
 WHERE r.organization_id = CAST(:org AS uuid) AND r.purpose = 'release_check'
 ORDER BY r.started_at DESC LIMIT :limit"""), {"org": org, "limit": int(limit)})).mappings().all()
    return [dict(r) for r in rows]


async def _stored_case_runs(run_ids: Sequence[str]) -> list[dict]:
    if not run_ids:
        return []
    async with db.lab_engine().connect() as conn:
        rows = (await conn.execute(text("""
SELECT cr.golden_run_id, cr.decision, c.kind, c.case_key
  FROM tracelab.eval_case_runs cr JOIN tracelab.eval_cases c ON c.id = cr.eval_case_id
 WHERE cr.golden_run_id::text = ANY(CAST(:ids AS text[]))"""), {"ids": [str(r) for r in run_ids]})).mappings().all()
    return [dict(r) for r in rows]


async def _baseline_graders(case_ids: Sequence[str]) -> dict[str, dict]:
    if not case_ids:
        return {}
    async with db.lab_engine().connect() as conn:
        rows = (await conn.execute(text("""
SELECT cr.eval_case_id, cr.graders
  FROM tracelab.eval_case_runs cr
  JOIN tracelab.eval_cases c ON c.id = cr.eval_case_id AND cr.golden_run_id = c.baseline_run_id
 WHERE c.id::text = ANY(CAST(:ids AS text[]))"""), {"ids": [str(c) for c in case_ids]})).mappings().all()
    return {str(r["eval_case_id"]): _json(r["graders"]) or {} for r in rows}


async def _previous_release(org: str, golden_run_id: str, started_at: datetime | None) -> dict[str, str] | None:
    if started_at is None:
        return None
    async with db.lab_engine().connect() as conn:
        prev = (await conn.execute(text("""
SELECT id FROM tracelab.golden_runs
 WHERE organization_id = CAST(:org AS uuid) AND purpose = 'release_check' AND started_at < :at
   AND id <> CAST(:id AS uuid)
 ORDER BY started_at DESC LIMIT 1"""), {"org": org, "at": started_at, "id": str(golden_run_id)})).scalar()
        if prev is None:
            return None
        rows = (await conn.execute(text("""
SELECT c.case_key, cr.decision FROM tracelab.eval_case_runs cr
  JOIN tracelab.eval_cases c ON c.id = cr.eval_case_id
 WHERE cr.golden_run_id = CAST(:prev AS uuid)"""), {"prev": str(prev)})).mappings().all()
    return {r["case_key"]: r["decision"] for r in rows}


async def list_release_checks(org: str, *, limit: int = 20) -> list[dict]:
    """Stored decisions: refreshed whenever a run is read (free) or graded."""
    runs = await _release_runs(org, max(1, min(int(limit), 100)))
    stored = await _stored_case_runs([str(r["id"]) for r in runs])
    out = []
    for r in runs:
        regression = [x for x in stored if str(x["golden_run_id"]) == str(r["id"]) and x["kind"] == "regression"]
        decision, reason = release_decision(regression, k=int(r["k"]))
        out.append({"golden_run_id": str(r["id"]), "run_key": r["label"], "decision": decision, "reason": reason,
                    "k": r["k"], "status": r["job_status"], "started_at": r["started_at"], "finished_at": r["finished_at"]})
    return public(out)


async def release_report(org: str, golden_run_id: str) -> dict:
    """Spec 5.6 JSON (plus runs_without_verdict). Read-only: grades with checks and cached predictions only."""
    full = await _golden_run(org, golden_run_id)
    if full is None:
        raise WatchError(404, "not_found", "That run was not found.")
    if full.get("purpose") != "release_check":                         # checked first: such a run is not even re-graded
        raise WatchError(404, "not_found", "That run is not a release check.")
    data = await refresh_run(org, golden_run_id, allow_model_calls=False)
    run = data["run"]
    k = int(run["k"])
    baselines = await _baseline_graders([str(c["eval_case_id"]) for c in data["case_runs"] if c["kind"] == "regression"])
    regression, capability, gaps = [], [], []
    for cr in data["case_runs"]:
        for rep in cr["reps"]:
            if rep.get("status") not in DECIDED_REPS:
                gaps.append({"case_key": cr["case_key"], "rep": rep["rep"], "status": rep.get("status"),
                             "reason": rep.get("degraded_reason")})
        if cr["kind"] == "regression":
            then = {(j["mode_id"], j["prompt_hash"]) for j in (baselines.get(str(cr["eval_case_id"])) or {}).get("judges", [])}
            now = {(j["mode_id"], j["prompt_hash"]) for j in (cr.get("graders") or {}).get("judges", [])}
            regression.append({"case_key": cr["case_key"], "version": cr["version"], "mode": cr.get("mode"),
                               "passes": cr["passes"], "n": cr["n"],
                               "not_decided": int(cr["degraded"]) + int(cr["pending"]) + int(cr["missing"]),
                               "decision": cr["decision"], "reason": cr["reason"],
                               "turn_ids": [r["turn_id"] for r in cr["reps"] if r.get("turn_id")],
                               "judge_changed_since_baseline": bool(now - then)})   # a version that decided a rep and did not decide the baseline
        elif cr["kind"] == "capability":
            n, passes = int(cr["n"]), int(cr["passes"])
            capability.append({"case_key": cr["case_key"], "version": cr["version"], "mode": cr.get("mode"),
                               "passes": passes, "n": n, "pass_at_1": cr.get("pass_at_1"), "pass_at_3": cr.get("pass_at_3"),
                               "pass_at_5": cr.get("pass_at_5"),
                               "pass_hat_5": round(stats.pass_hat_k(n, passes, 5), 4) if n >= 5 else None,
                               "baseline_pass_rate": cr.get("baseline_pass_rate"), "decision": cr["decision"],
                               "reason": cr["reason"]})
    decision, reason = release_decision(regression, k=k)
    checks_used = sorted({c for cr in data["case_runs"] for c in (cr.get("graders") or {}).get("checks", [])})
    judges_used: dict[str, dict] = {}
    for cr in data["case_runs"]:
        for j in (cr.get("graders") or {}).get("judges", []):
            judges_used[j["prompt_hash"]] = {"mode": cr.get("mode"), "judge_key": j["judge_key"], "prompt_hash": j["prompt_hash"]}
    previous = await _previous_release(org, golden_run_id, full.get("started_at"))
    changes = None if previous is None else [
        {"case_key": r["case_key"], "before": previous[r["case_key"]], "after": r["decision"]}
        for r in regression + capability if r["case_key"] in previous and previous[r["case_key"]] != r["decision"]]
    return public({"golden_run_id": str(run["id"]), "run_key": run.get("label"), "organization_id": org,
                   "decision": decision, "reason": reason, "k": k, "started_at": run.get("started_at"),
                   "finished_at": run.get("finished_at"), "usd": run.get("usd_spent"),
                   "fingerprint": full.get("fingerprint") or {},
                   "graders": {"checks": checks_used, "judges": list(judges_used.values())},
                   "regression": regression, "capability": capability, "runs_without_verdict": gaps,
                   "changes_since_last": changes})
