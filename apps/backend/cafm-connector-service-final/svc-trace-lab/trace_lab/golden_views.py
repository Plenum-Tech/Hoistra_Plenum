"""What the Runs view and the Conversations view read about golden runs (Phase 3): run summaries, one run's cases,
one result's messages, two runs compared, and the expected outcome behind a golden answer.

Readings. A result's stored outcome (golden_results.outcome) is read through grader validation: a judgment case the
grader could not yet be trusted on is stored as unsure with reading needs_person, and READS as "unvalidated" until
grader_validation passes for the grader version that graded it; then it reads as the verdict it was given
(grade.validated_outcome). Every "outcome" these views return is that reading. Only pass and fail are counted in a
rate; unsure, unvalidated, degraded (a refused request, or a follow-up without its context), stale_reference, error
and pending never are. The headline pass rate counts fresh cases only (grade.stale false); pass_rate_incl_stale adds
the stale ones, with a stale reference counted as not passing.

The blind rule. The expected answer, the grader's verdict and its critique of a golden answer stay closed for a
reviewer until that reviewer reveals them (reveal: one append-only row per reviewer and answer). Run detail and
compare never carry an expected answer or a critique; they show readings only.

Reads on db.lab_engine() (schema tracelab) only; reveal is the one write. Every function is company-scoped: a run or
answer of another company is NotFound (or "not a golden answer"), never a hint that it exists.
"""
from __future__ import annotations

import json
import statistics
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text

from . import db, grader_validation, normalize, stats
from .golden_store import NotFound

COUNTED = ("pass", "fail")
READINGS = ("pending", "pass", "fail", "unsure", "unvalidated", "degraded", "stale_reference", "error")
STORED = frozenset({"pending", "pass", "fail", "unsure", "degraded", "stale_reference", "error"})
FLIP_KINDS = ("fixed", "regressed", "still_failing", "now_measured", "not_measured")
QUESTION_MAX = 300
MAX_RUNS = 100
#: Shown on every golden run (G0 addendum 2.5): what a golden answer can differ in from a live one.
#: The same four sentences, word for word, as runner.FIDELITY_CAVEATS (Task 7) and logic/golden.js (Task 18).
FIDELITY_CAVEATS = (
    "Company memories are not recalled in a test run.",
    "Follow-ups answered by a specialist engine may lack the previous answer's context.",
    "Approved instruction updates are loaded read-only.",
    "The contract analyst's and the embeddings' spend is not measured.",
)
REFUSED_LABEL = "A change the test was not allowed to make"

_RUN_SQL = """
    SELECT r.id::text AS id, r.organization_id::text AS organization_id, r.set_id::text AS set_id, r.label, r.purpose,
           r.k, r.status, r.job_id::text AS job_id, r.started_by, r.started_by_email, r.started_at, r.finished_at,
           r.params, r.estimate_usd, r.usd_cap, r.usd_spent, r.summary, s.name AS set_name, s.kind AS set_kind,
           s.version AS set_version, s.status AS set_status, s.set_hash
      FROM tracelab.golden_runs r JOIN tracelab.golden_sets s ON s.id = r.set_id"""
_ROWS_SQL = """
    SELECT x.id::text AS id, x.run_id::text AS run_id, x.case_id::text AS case_id, x.case_key, x.case_version, x.rep,
           x.msg_idx, x.session_id, x.turn_id, x.question, x.answer, x.outcome, x.grade, x.refused, x.usd, x.ms,
           x.error, x.graded_at, x.created_at
      FROM tracelab.golden_results x
     WHERE x.run_id = ANY(CAST(:ids AS uuid[])) AND x.organization_id = CAST(:org AS uuid)"""
_CASES_SQL = """
    SELECT c.set_id::text AS set_id, c.id::text AS id, c.case_key, c.case_version, c.idx, c.question, c.followups,
           c.evaluation, c.stale
      FROM tracelab.golden_cases c WHERE c.set_id = ANY(CAST(:sets AS uuid[])) ORDER BY c.idx"""


# ── small helpers ──────────────────────────────────────────────────────────────────────────

def _json(v: Any, default: Any) -> Any:
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return default
    return default if v is None else v


def _iso(v: Any) -> Any:
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, uuid.UUID):
        return str(v)
    return v


def _num(v: Any) -> float | None:
    return None if v is None else round(float(v), 6)


def _uuid(v: Any) -> str | None:
    try:
        return str(uuid.UUID(str(v)))
    except (TypeError, ValueError):
        return None


def rate(passes: int, n: int, key: str = "graded") -> dict[str, Any]:
    lo, hi = stats.wilson_interval(passes, n)
    return {"passes": passes, key: n, "rate": round(passes / n, 4) if n else None, "wilson": [lo, hi] if n else None}


def refused_labels(refused: Any) -> list[str]:
    """Plain labels only: a refusal's service, method and path never reach the page."""
    out = []
    for e in _json(refused, []) or []:
        label = e.get("label") if isinstance(e, dict) else None
        out.append(str(label)[:120] if label else REFUSED_LABEL)
    return out


def conversation_key(session_id: str | None, turn_id: str | None) -> str | None:
    return normalize.group_key(session_id, turn_id) if turn_id or session_id else None


# ── pure rules ─────────────────────────────────────────────────────────────────────────────

def reading(row: dict, *, validated: frozenset[str] | set[str] = frozenset()) -> str:
    """How one final result reads. A judgment case still waiting for grader validation reads unvalidated until its
    grader version is in `validated`; then it reads as the verdict the grader gave it."""
    outcome = row.get("outcome") or "pending"
    g = _json(row.get("grade"), {}) or {}
    if outcome == "unsure" and g.get("reading") == "needs_person":
        if g.get("grader_version") in validated and g.get("validated_outcome") in STORED:
            return str(g["validated_outcome"])
        return "unvalidated"
    return outcome if outcome in STORED else "pending"


def run_case_keys(run: dict, cases: dict[str, dict]) -> list[str]:
    """The cases a run asks: params.case_keys when the runner chose a subset, else every case of its set."""
    keys = [str(k) for k in (_json(run.get("params"), {}) or {}).get("case_keys") or [] if str(k) in cases]
    return keys or list(cases)


def rep_rows(rows: list[dict], cases: dict[str, dict]) -> dict[tuple[str, int], dict]:
    """(case_key, rep) -> its message rows in order and its final row: the row of the case's last message
    (msg_idx = number of follow-ups), None while the rep is still being asked."""
    by: dict[tuple[str, int], list[dict]] = {}
    for r in rows:
        by.setdefault((str(r["case_key"]), int(r["rep"])), []).append(r)
    out: dict[tuple[str, int], dict] = {}
    for key, items in by.items():
        ordered = sorted(items, key=lambda r: int(r["msg_idx"]))
        last = len((cases.get(key[0]) or {}).get("followups") or [])
        out[key] = {"rows": ordered, "final": next((r for r in ordered if int(r["msg_idx"]) == last), None)}
    return out


def _grading_usd(row: dict | None) -> float:
    g = _json((row or {}).get("grade"), {}) or {}
    try:
        return float(g.get("usd") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _models(params: dict) -> list[str]:
    return sorted({str(v) for k, v in (params or {}).items() if "model" in str(k).lower() and isinstance(v, str) and v})


def _is_stale(row: dict | None) -> bool:
    return bool((_json((row or {}).get("grade"), {}) or {}).get("stale"))


def summarize(run: dict, rows: list[dict], cases: dict[str, dict], *,
              validated: frozenset[str] | set[str] = frozenset(), viewer: str | None = None) -> dict[str, Any]:
    """One run's summary. Pure. Its first keys are the Phase 2 runs-list row (run_id, kind, name, recorded, status,
    started_at, ended_at, conversation_count, turn_count, models, cost_usd). viewer is the reader's user id (mine)."""
    keys = run_case_keys(run, cases)
    wanted = set(keys)
    k = int(run.get("k") or 1)
    counts = {r: 0 for r in READINGS}
    per_case: dict[str, list[str]] = {}
    fresh, with_stale = [0, 0], [0, 0]
    done = refused = 0
    judgment = waiting = False
    run_rows = [r for r in rows if str(r["case_key"]) in wanted]
    items = rep_rows(run_rows, cases)
    for (key, _rep), it in items.items():
        refused += sum(len(_json(r.get("refused"), []) or []) for r in it["rows"])
        f = it["final"]
        if f is None:
            continue
        rd = reading(f, validated=validated)
        counts[rd] += 1
        done += rd != "pending"
        judgment = judgment or (_json(f.get("grade"), {}) or {}).get("evaluation") == "judgment"
        waiting = waiting or rd == "unvalidated"
        if rd in COUNTED:
            with_stale[1] += 1
            with_stale[0] += rd == "pass"
            if not _is_stale(f):
                fresh[1] += 1
                fresh[0] += rd == "pass"
                per_case.setdefault(key, []).append(rd)
        elif rd == "stale_reference":
            with_stale[1] += 1
    pak = phk = None
    if k > 1:
        eligible = [(len(v), v.count("pass")) for v in per_case.values() if len(v) >= k]
        if eligible:
            pak = round(sum(stats.pass_at_k(n, c, k) for n, c in eligible) / len(eligible), 4)
            phk = round(sum(stats.pass_hat_k(n, c, k) for n, c in eligible) / len(eligible), 4)
    answers = round(sum(float(r.get("usd") or 0) for r in run_rows), 6)
    grading_usd = round(sum(_grading_usd(it["final"]) for it in items.values()), 6)
    ms = [int(r["ms"]) for r in run_rows if r.get("ms") is not None]
    params = _json(run.get("params"), {}) or {}
    live = _json(run.get("summary"), {}) or {}
    return {
        "run_id": run["id"], "kind": "golden", "name": run.get("label"), "recorded": True, "status": run.get("status"),
        "started_at": _iso(run.get("started_at")), "ended_at": _iso(run.get("finished_at")),
        "conversation_count": len({(str(r["case_key"]), int(r["rep"])) for r in run_rows}),
        "turn_count": sum(1 for r in run_rows if r.get("turn_id")), "models": _models(params),
        "cost_usd": round(answers + grading_usd, 6),
        "id": run["id"], "label": run.get("label"), "purpose": run.get("purpose"), "k": k,
        "set": {"id": run.get("set_id"), "name": run.get("set_name"), "version": run.get("set_version"),
                "kind": run.get("set_kind"), "status": run.get("set_status")},
        "started_by_email": run.get("started_by_email"), "mine": bool(viewer) and run.get("started_by") == viewer,
        "paused_reason": live.get("paused_reason"), "current": live.get("current"), "error": live.get("error"),
        "note": params.get("note"), "params": params, "cases": len(keys), "items_total": len(keys) * k,
        "items_done": done, "outcomes": counts, "pass_rate": rate(fresh[0], fresh[1]),
        "pass_rate_incl_stale": rate(with_stale[0], with_stale[1]),
        "case_pass_rate": rate(sum(1 for v in per_case.values() if all(x == "pass" for x in v)), len(per_case),
                               key="measured"),
        "pass_at_k": pak, "pass_hat_k": phk, "grader_validated": (not waiting) if judgment else None,
        "answers_usd": answers, "grading_usd": grading_usd, "usd_spent": _num(run.get("usd_spent")),
        "usd_cap": _num(run.get("usd_cap")), "estimate_usd": _num(run.get("estimate_usd")),
        "median_ms": int(statistics.median(ms)) if ms else None, "refused": refused, "caveats": list(FIDELITY_CAVEATS),
    }


def _case_status(readings: list[str]) -> str:
    counted = [r for r in readings if r in COUNTED]
    if not counted:
        return "not_measured"
    return "pass" if all(r == "pass" for r in counted) else "fail"


def detail_cases(run: dict, rows: list[dict], cases: dict[str, dict], *,
                 validated: frozenset[str] | set[str] = frozenset(), revealed: set[str] = frozenset()) -> list[dict]:
    """Every case of the run in sheet order with one entry per rep. Readings only: no expected answer, no critique.
    `revealed` holds the turn ids this reviewer has revealed."""
    items = rep_rows([r for r in rows if str(r["case_key"]) in cases], cases)
    out = []
    for key in run_case_keys(run, cases):
        case = cases.get(key) or {}
        reps, readings = [], []
        for rep in range(1, int(run.get("k") or 1) + 1):
            it = items.get((key, rep))
            if it is None:
                reps.append({"rep": rep, "state": "not_started"})
                continue
            msgs, f = it["rows"], it["final"]
            turn_ids = [r["turn_id"] for r in msgs if r.get("turn_id")]
            entry = {"rep": rep, "state": "in_flight" if f is None else "done",
                     "session_id": msgs[-1]["session_id"] if msgs else None,
                     "turn_id": (f or (msgs[-1] if msgs else {})).get("turn_id"),
                     "conversation_key": conversation_key(msgs[-1]["session_id"], msgs[-1].get("turn_id")) if msgs else None,
                     "turn_ids": turn_ids, "usd": round(sum(float(r.get("usd") or 0) for r in msgs), 6),
                     "ms": sum(int(r.get("ms") or 0) for r in msgs),
                     "refused": [lbl for r in msgs for lbl in refused_labels(r.get("refused"))],
                     "revealed": any(t in revealed for t in turn_ids)}
            entry["refused_count"] = len(entry["refused"])
            if f is not None:
                rd = reading(f, validated=validated)
                readings.append(rd)
                entry.update(result_id=f["id"], final_turn_id=f.get("turn_id"), outcome=rd,
                             grading_usd=round(_grading_usd(f), 6))
            reps.append(entry)
        counted = [r for r in readings if r in COUNTED]
        out.append({"case_key": key, "case_version": case.get("case_version"),
                    "question": str(case.get("question") or "")[:QUESTION_MAX], "evaluation": case.get("evaluation"),
                    "messages": 1 + len(case.get("followups") or []), "stale": bool(case.get("stale")),
                    "passes": counted.count("pass"), "graded": len(counted), "case_status": _case_status(readings),
                    "reps": reps})
    return out


# ── comparing two runs ─────────────────────────────────────────────────────────────────────

def flip(base: str, head: str) -> str:
    if head == "not_measured":
        return "not_measured"
    if base == "not_measured":
        return "now_measured"
    if base == "fail" and head == "pass":
        return "fixed"
    if base == "pass" and head == "fail":
        return "regressed"
    return "still_failing" if head == "fail" else "still_passing"


def case_statuses(bundle: dict) -> dict[str, dict]:
    run, cases = bundle["run"], bundle["cases"]
    validated = bundle.get("validated") or frozenset()
    items = rep_rows(bundle["rows"], cases)
    out: dict[str, dict] = {}
    for key in run_case_keys(run, cases):
        readings, results = [], []
        for rep in range(1, int(run.get("k") or 1) + 1):
            f = (items.get((key, rep)) or {}).get("final")
            if f is None:
                continue
            rd = reading(f, validated=validated)
            readings.append(rd)
            results.append({"rep": rep, "result_id": f["id"], "session_id": f.get("session_id"),
                            "turn_id": f.get("turn_id"), "final_turn_id": f.get("turn_id"),
                            "conversation_key": conversation_key(f.get("session_id"), f.get("turn_id")), "outcome": rd})
        case = cases.get(key) or {}
        out[key] = {"status": _case_status(readings), "version": case.get("case_version"),
                    "question": str(case.get("question") or "")[:QUESTION_MAX], "results": results}
    return out


def _flatten(d: Any, prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in (d or {}).items():
        name = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(_flatten(v, name + "."))
        else:
            out[name] = v
    return out


def params_diff(base: dict, head: dict) -> dict[str, list]:
    """Every like-for-like field (golden_runs.params, flattened with dots) that differs, as [base, head]."""
    b, h = _flatten(_json(base, {}) or {}), _flatten(_json(head, {}) or {})
    return {k: [b.get(k), h.get(k)] for k in sorted(set(b) | set(h)) if b.get(k) != h.get(k)}


def _grader_versions(bundle: dict) -> list[str]:
    finals = [it["final"] for it in rep_rows(bundle["rows"], bundle["cases"]).values() if it["final"] is not None]
    return sorted({str((_json(f.get("grade"), {}) or {}).get("grader_version")) for f in finals
                   if (_json(f.get("grade"), {}) or {}).get("grader_version")})


def _setting_warning(field: str) -> str:
    f = field.lower()
    if "flag" in f:
        return "The two runs had different chat settings."
    if "overlay" in f or "skill" in f:
        return "Skill tuning changed between the two runs."
    if any(w in f for w in ("git", "sha", "image", "build", "version")):
        return "The two runs ran on different builds of the assistant."
    if "context" in f:
        return "The two runs asked with different page context."
    if "case_keys" in f:
        return "The two runs asked different case lists."
    return "Something else in how the two runs were set up changed."


def compare_runs(base: dict, head: dict) -> dict[str, Any]:
    """Two runs compared, matched on case key with an equal case version (a changed case is listed, not compared).
    Pure. bundle = {"run", "rows", "cases", "validated"}."""
    b, h = case_statuses(base), case_statuses(head)
    flips: dict[str, list] = {kind: [] for kind in FLIP_KINDS}
    still_passing = 0
    changed, added, removed = [], [], []
    for key in sorted(set(b) | set(h)):
        if key not in h:
            removed.append(key)
            continue
        if key not in b:
            added.append(key)
            continue
        if b[key]["version"] != h[key]["version"]:
            changed.append(key)
            continue
        kind = flip(b[key]["status"], h[key]["status"])
        if kind == "still_passing":
            still_passing += 1
            continue
        flips[kind].append({"case_key": key, "question": h[key]["question"] or b[key]["question"],
                            "base": {"status": b[key]["status"], "results": b[key]["results"]},
                            "head": {"status": h[key]["status"], "results": h[key]["results"]}})
    sb = summarize(base["run"], base["rows"], base["cases"], validated=base.get("validated") or frozenset())
    sh = summarize(head["run"], head["rows"], head["cases"], validated=head.get("validated") or frozenset())
    diff = params_diff(base["run"].get("params"), head["run"].get("params"))
    gb, gh = _grader_versions(base), _grader_versions(head)
    warnings: list[str] = []
    comparable = True
    if gb and gh and (gb != gh or len(gb) > 1):
        comparable = False
        warnings.append("The two runs were graded by different grader versions.")
    if base["run"].get("set_kind") != head["run"].get("set_kind"):
        comparable = False
        warnings.append("The two runs used different golden sets.")
    if any("model" in f.lower() for f in diff):
        comparable = False
        warnings.append("The two runs used different chat models.")
    for f in diff:
        if "model" not in f.lower():
            w = _setting_warning(f)
            if w not in warnings:
                warnings.append(w)
    if base["run"].get("set_kind") == head["run"].get("set_kind") and base["run"].get("set_id") != head["run"].get("set_id"):
        warnings.append("The two runs used different versions of the golden set; cases are matched on their key and "
                        "version.")
    kb, kh = int(base["run"].get("k") or 1), int(head["run"].get("k") or 1)
    if kb != kh:
        warnings.append("The two runs used a different number of repetitions.")

    def delta(x: float | None, y: float | None) -> float | None:
        return round(y - x, 4) if x is not None and y is not None else None

    return {
        "ok": True, "base": sb, "head": sh, "comparable": comparable, "like_for_like": comparable and not warnings,
        "warnings": warnings, "flips": flips, "still_passing": still_passing, "changed": changed, "added": added,
        "removed": removed,
        "pass_rate": {"base": sb["pass_rate"], "head": sh["pass_rate"],
                      "delta": delta(sb["pass_rate"]["rate"], sh["pass_rate"]["rate"])},
        "case_pass_rate": {"base": sb["case_pass_rate"], "head": sh["case_pass_rate"],
                           "delta": delta(sb["case_pass_rate"]["rate"], sh["case_pass_rate"]["rate"])},
        "pass_hat_k": {"base": {"k": kb, "value": sb["pass_hat_k"]}, "head": {"k": kh, "value": sh["pass_hat_k"]},
                       "k_differs": kb != kh},
        "pass_at_k": {"base": {"k": kb, "value": sb["pass_at_k"]}, "head": {"k": kh, "value": sh["pass_at_k"]},
                      "k_differs": kb != kh},
        "cost": {"base_usd": sb["cost_usd"], "head_usd": sh["cost_usd"], "delta_usd": delta(sb["cost_usd"], sh["cost_usd"]),
                 "base": {"total_usd": sb["cost_usd"], "answers_usd": sb["answers_usd"], "grading_usd": sb["grading_usd"]},
                 "head": {"total_usd": sh["cost_usd"], "answers_usd": sh["answers_usd"], "grading_usd": sh["grading_usd"]}},
        "latency": {"base_median_ms": sb["median_ms"], "head_median_ms": sh["median_ms"]},
        "degraded": {"base": sb["outcomes"]["degraded"], "head": sh["outcomes"]["degraded"]},
        "params_diff": diff, "grader_versions": {"base": gb, "head": gh},
    }


# ── reads ──────────────────────────────────────────────────────────────────────────────────

def _run_dict(row: Any) -> dict[str, Any]:
    d = dict(row)
    d["params"] = _json(d.get("params"), {}) or {}
    return d


async def _validated_for(rows: list[dict]) -> frozenset[str]:
    """The grader versions (of judgment results waiting for validation) that now pass validation."""
    versions = {str((_json(r.get("grade"), {}) or {}).get("grader_version")) for r in rows
                if (_json(r.get("grade"), {}) or {}).get("reading") == "needs_person"}
    ok = set()
    for v in sorted(versions):
        if v and v != "None" and await grader_validation.passes(v):
            ok.add(v)
    return frozenset(ok)


async def _load(conn: Any, org: str, runs: list[dict]) -> tuple[dict[str, list[dict]], dict[str, dict[str, dict]]]:
    """Result rows by run id, and cases by set id ({case_key: case})."""
    rows: dict[str, list[dict]] = {r["id"]: [] for r in runs}
    cases: dict[str, dict[str, dict]] = {}
    if not runs:
        return rows, cases
    for r in (await conn.execute(text(_ROWS_SQL), {"ids": [r["id"] for r in runs], "org": org})).mappings().all():
        rows.setdefault(r["run_id"], []).append(dict(r))
    for c in (await conn.execute(text(_CASES_SQL), {"sets": sorted({r["set_id"] for r in runs})})).mappings().all():
        d = dict(c)
        d["followups"] = list(_json(d.get("followups"), []) or [])
        cases.setdefault(d["set_id"], {})[d["case_key"]] = d
    return rows, cases


async def load_run(org: str, run_id: str) -> dict[str, Any] | None:
    rid = _uuid(run_id)
    if rid is None:
        return None
    async with db.lab_engine().connect() as conn:
        row = (await conn.execute(text(_RUN_SQL + " WHERE r.id = CAST(:id AS uuid) AND r.organization_id = CAST(:org AS uuid)"),
                                  {"id": rid, "org": org})).mappings().first()
    return _run_dict(row) if row else None


async def require_run(org: str, run_id: str) -> dict[str, Any]:
    """The run, or NotFound when it is not this company's. Routes call it before any run control."""
    run = await load_run(org, run_id)
    if run is None:
        raise NotFound(run_id)
    return run


async def run_summaries(org: str, *, since: datetime | None = None, until: datetime | None = None, limit: int = 50,
                        reviewer: Any = None) -> list[dict[str, Any]]:
    """The company's golden runs, newest first, as summaries (the Phase 2 runs-list row plus the golden figures);
    mine is true on the runs the reviewer started."""
    where = ["r.organization_id = CAST(:org AS uuid)"]
    p: dict[str, Any] = {"org": org, "lim": max(1, min(int(limit), MAX_RUNS))}
    if since is not None:
        where.append("r.started_at >= :since")
        p["since"] = since
    if until is not None:
        where.append("r.started_at < :until")
        p["until"] = until
    async with db.lab_engine().connect() as conn:
        runs = [_run_dict(r) for r in (await conn.execute(text(
            _RUN_SQL + " WHERE " + " AND ".join(where) + " ORDER BY r.started_at DESC, r.id LIMIT :lim"), p)).mappings().all()]
        rows, cases = await _load(conn, org, runs)
    validated = await _validated_for([r for rs in rows.values() for r in rs])
    viewer = str(reviewer.user_id) if reviewer is not None else None
    return [summarize(run, rows[run["id"]], cases.get(run["set_id"], {}), validated=validated, viewer=viewer)
            for run in runs]


async def _revealed(conn: Any, org: str, reviewer: Any, turn_ids: list[str]) -> set[str]:
    if reviewer is None or not turn_ids:
        return set()
    got = (await conn.execute(text(
        "SELECT turn_id FROM tracelab.golden_reveals WHERE organization_id = CAST(:org AS uuid) "
        "AND reviewer_id = :me AND turn_id = ANY(CAST(:t AS text[]))"),
        {"org": org, "me": str(reviewer.user_id), "t": turn_ids})).scalars().all()
    return set(got)


async def run_detail(org: str, run_id: str, *, reviewer: Any = None) -> dict[str, Any]:
    """One run: its summary, its like-for-like record, the fidelity caveats and every case with its reps."""
    run = await require_run(org, run_id)
    async with db.lab_engine().connect() as conn:
        rows, cases = await _load(conn, org, [run])
        mine = rows[run["id"]]
        revealed = await _revealed(conn, org, reviewer, [r["turn_id"] for r in mine if r.get("turn_id")])
    set_cases = cases.get(run["set_id"], {})
    validated = await _validated_for(mine)
    viewer = str(reviewer.user_id) if reviewer is not None else None
    return {"ok": True, "run": summarize(run, mine, set_cases, validated=validated, viewer=viewer),
            "params": run["params"],
            "caveats": list(FIDELITY_CAVEATS),
            "cases": detail_cases(run, mine, set_cases, validated=validated, revealed=revealed)}


async def compare(org: str, base: str, head: str) -> dict[str, Any]:
    """Two runs of this company compared (compare_runs). NotFound when either is not this company's."""
    b, h = await require_run(org, base), await require_run(org, head)
    async with db.lab_engine().connect() as conn:
        rows, cases = await _load(conn, org, [b, h])
    validated = await _validated_for(rows[b["id"]] + rows[h["id"]])
    return compare_runs({"run": b, "rows": rows[b["id"]], "cases": cases.get(b["set_id"], {}), "validated": validated},
                        {"run": h, "rows": rows[h["id"]], "cases": cases.get(h["set_id"], {}), "validated": validated})


# ── one golden answer: its messages and its expected outcome (blind until revealed) ─────────

_TURN_SQL = """
    SELECT x.run_id::text AS run_id, x.case_id::text AS case_id, x.case_key, x.rep, x.msg_idx, r.label
      FROM tracelab.golden_results x JOIN tracelab.golden_runs r ON r.id = x.run_id
     WHERE x.turn_id = :t AND x.organization_id = CAST(:org AS uuid)
     ORDER BY x.created_at DESC LIMIT 1"""
_REP_SQL = """
    SELECT x.id::text AS id, x.case_key, x.rep, x.msg_idx, x.session_id, x.turn_id, x.question, x.answer, x.outcome,
           x.grade, x.refused, x.usd, x.ms, x.error, x.graded_at
      FROM tracelab.golden_results x
     WHERE x.run_id = CAST(:run AS uuid) AND x.case_key = :k AND x.rep = :rep AND x.organization_id = CAST(:org AS uuid)
     ORDER BY x.msg_idx"""
_CASE_SQL = """
    SELECT c.case_key, c.case_version, c.question, c.followups, c.evaluation, c.expected_answer, c.expected_values,
           c.source, c.verified_on, c.page_context, c.requirement_ids, c.notes, c.stale
      FROM tracelab.golden_cases c WHERE c.id = CAST(:c AS uuid) AND c.organization_id = CAST(:org AS uuid)"""


async def _rep(conn: Any, org: str, run_id: str, case_id: str, case_key: str, rep: int) -> tuple[list[dict], dict | None]:
    msgs = [dict(r) for r in (await conn.execute(text(_REP_SQL), {"run": run_id, "k": case_key, "rep": rep,
                                                                    "org": org})).mappings().all()]
    case = (await conn.execute(text(_CASE_SQL), {"c": case_id, "org": org})).mappings().first()
    return msgs, (dict(case) if case else None)


def _final(msgs: list[dict], case: dict | None) -> dict | None:
    last = len(_json((case or {}).get("followups"), []) or [])
    return next((m for m in msgs if int(m["msg_idx"]) == last), None)


async def _reveal_time(conn: Any, org: str, reviewer: Any, turn_ids: list[str]) -> str | None:
    if reviewer is None or not turn_ids:
        return None
    at = (await conn.execute(text(
        "SELECT min(revealed_at) FROM tracelab.golden_reveals WHERE organization_id = CAST(:org AS uuid) "
        "AND reviewer_id = :me AND turn_id = ANY(CAST(:t AS text[]))"),
        {"org": org, "me": str(reviewer.user_id), "t": turn_ids})).scalar_one()
    return _iso(at)


def _case_block(case: dict) -> dict[str, Any]:
    pc = case.get("page_context")
    return {"question": case.get("question"), "followups": list(_json(case.get("followups"), []) or []),
            "evaluation": case.get("evaluation"), "expected_answer": case.get("expected_answer"),
            "expected_values": list(_json(case.get("expected_values"), []) or []), "source": case.get("source"),
            "verified_on": _iso(case.get("verified_on")), "stale": bool(case.get("stale")),
            "page_context_used": "default" if pc is None else ("none" if pc == "" else "case"),
            "requirement_ids": list(case.get("requirement_ids") or []), "notes": case.get("notes")}


def _redacted(value: Any) -> Any:
    """A stored text through normalize.redact_text (the grader's critique can quote the answer); None stays None."""
    return normalize.redact_text(value) if isinstance(value, str) and value else value


def _result_block(final: dict | None, msgs: list[dict], validated: frozenset[str]) -> dict[str, Any] | None:
    if final is None:
        return None
    g = _json(final.get("grade"), {}) or {}
    rd = reading(final, validated=validated)
    return {"outcome": rd, "stored_outcome": final.get("outcome"),
            "grade": {"verdict": g.get("verdict"), "critique": _redacted(g.get("critique")),
                      "mismatches": [_redacted(x) for x in g.get("mismatches") or []], "value_check": g.get("value_check"),
                      "grader_version": g.get("grader_version"), "stale": bool(g.get("stale")),
                      "validated": (rd != "unvalidated") if g.get("evaluation") == "judgment" else None},
            "refused": [lbl for m in msgs for lbl in refused_labels(m.get("refused"))],
            "usd": round(sum(float(m.get("usd") or 0) for m in msgs), 6), "grading_usd": round(_grading_usd(final), 6),
            "ms": sum(int(m.get("ms") or 0) for m in msgs), "graded_at": _iso(final.get("graded_at"))}


async def expected_outcome(org: str, turn_id: str, *, reviewer: Any) -> dict[str, Any]:
    """The case behind a golden answer and how its rep was graded. Closed (revealed: false, no expected answer, no
    verdict) until this reviewer reveals it. {available: false} when the turn is not a golden answer of this
    company."""
    async with db.lab_engine().connect() as conn:
        head = (await conn.execute(text(_TURN_SQL), {"t": str(turn_id), "org": org})).mappings().first()
        if head is None:
            return {"ok": True, "available": False, "reason": "not_a_golden_turn"}
        msgs, case = await _rep(conn, org, head["run_id"], head["case_id"], head["case_key"], int(head["rep"]))
        at = await _reveal_time(conn, org, reviewer, [m["turn_id"] for m in msgs if m.get("turn_id")] or [str(turn_id)])
    final = _final(msgs, case)
    followups = list(_json((case or {}).get("followups"), []) or [])
    golden = {"run": {"id": head["run_id"], "label": head["label"]}, "case_key": head["case_key"],
              "case_version": (case or {}).get("case_version"), "rep": int(head["rep"]), "msg_idx": int(head["msg_idx"]),
              "is_final": int(head["msg_idx"]) == len(followups), "messages_total": 1 + len(followups),
              "evaluation": (case or {}).get("evaluation"), "complete": final is not None}
    if at is None:
        return {"ok": True, "available": True, "revealed": False, "golden": golden}
    validated = await _validated_for([final] if final else [])
    golden.update(case=_case_block(case) if case else None, result=_result_block(final, msgs, validated),
                  revealed_at=at)
    return {"ok": True, "available": True, "revealed": True, "golden": golden}


async def reveal(org: str, turn_id: str, *, reviewer: Any) -> dict[str, Any]:
    """Record that this reviewer chose to see the expected outcome of a golden answer (once; append-only), then
    return it open. {available: false} when the turn is not a golden answer of this company (nothing recorded)."""
    async with db.lab_engine().begin() as conn:
        head = (await conn.execute(text(_TURN_SQL), {"t": str(turn_id), "org": org})).mappings().first()
        if head is None:
            return {"ok": True, "available": False, "reason": "not_a_golden_turn"}
        await conn.execute(text(
            "INSERT INTO tracelab.golden_reveals (organization_id, turn_id, reviewer_id, reviewer_email) "
            "VALUES (CAST(:org AS uuid), :t, :me, :email) ON CONFLICT DO NOTHING"),
            {"org": org, "t": str(turn_id), "me": str(reviewer.user_id), "email": getattr(reviewer, "email", None)})
    return await expected_outcome(org, turn_id, reviewer=reviewer)


async def result_detail(org: str, run_id: str, result_id: str, *, reviewer: Any) -> dict[str, Any]:
    """Every message of one case rep (questions, answers, refusals as labels, errors, cost, time) and its reading.
    The expected answer and the grading are added only when this reviewer has revealed the rep."""
    run = await require_run(org, run_id)
    rid = _uuid(result_id)
    async with db.lab_engine().connect() as conn:
        head = None
        if rid is not None:
            head = (await conn.execute(text(
                "SELECT case_id::text AS case_id, case_key, rep FROM tracelab.golden_results "
                "WHERE id = CAST(:id AS uuid) AND run_id = CAST(:run AS uuid) AND organization_id = CAST(:org AS uuid)"),
                {"id": rid, "run": run["id"], "org": org})).mappings().first()
        if head is None:
            raise NotFound(result_id)
        msgs, case = await _rep(conn, org, run["id"], head["case_id"], head["case_key"], int(head["rep"]))
        at = await _reveal_time(conn, org, reviewer, [m["turn_id"] for m in msgs if m.get("turn_id")])
    final = _final(msgs, case)
    validated = await _validated_for([final] if final else [])
    out = {"ok": True, "run": {"id": run["id"], "label": run.get("label")}, "case_key": head["case_key"],
           "rep": int(head["rep"]), "question": (case or {}).get("question"), "evaluation": (case or {}).get("evaluation"),
           "outcome": reading(final, validated=validated) if final else None, "revealed": at is not None,
           # Redacted as the conversation views are: a credential the chat put in an answer never reaches the page.
           "messages": [{"msg_idx": int(m["msg_idx"]), "question": _redacted(m.get("question")),
                         "answer": _redacted(m.get("answer")),
                         "turn_id": m.get("turn_id"),
                         "conversation_key": conversation_key(m.get("session_id"), m.get("turn_id")),
                         "refused": refused_labels(m.get("refused")), "error": _redacted(m.get("error")),
                         "usd": _num(m.get("usd")), "ms": m.get("ms")} for m in msgs]}
    if at is not None:
        out.update(case=_case_block(case) if case else None, result=_result_block(final, msgs, validated),
                   revealed_at=at)
    return out
