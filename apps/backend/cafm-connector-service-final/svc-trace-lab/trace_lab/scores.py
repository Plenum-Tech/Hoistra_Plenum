"""Scores on traces, and what follows from them (11 Nov 2026, task plan B3 and B4).

A score is a verdict on one trace at one level, as Langfuse keeps one:

    name            the level: logic | ux | domain
    source          ANNOTATION (a person) | EVAL (an automatic check or a model judge)
    string_value    pass | fail | not_evaluated
    value           0 to 1, or nothing
    comment         what the person or the evaluator says about it

Nothing is ever changed: a second opinion is a new row (tracelab.scores is append-only). The CURRENT score of a
trace at a level is the newest one a person gave, and only when no person has scored it, the newest automatic one.
An evaluator can therefore be re-run as often as it improves without ever overriding a reviewer.

A session's verdict at a level is fail when any of its traces fails, pass when every trace passes, and
not_evaluated otherwise. Comparing two run tags pairs the latest attempt of each scenario on both sides and says, per
level, whether it was fixed, regressed, stayed the same, changed in some other way, is new or is missing.

The domain level is the subject-matter expert's: only a domain expert, the platform team or the consultant score it.
G0: Trace Lab's own tables only.
"""
from __future__ import annotations

import json
import statistics
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import text

from . import conversations, db, expected_flags, labelling, runs
from .review_errors import Forbidden, Invalid, NotFound

LEVELS = ("logic", "ux", "domain")
VERDICTS = ("pass", "fail")
LIST_VERDICTS = ("pass", "fail", "not_evaluated")
DOMAIN_ROLES = ("domain_expert", "platform", "consultant")
COMMENT_MAX = 4000
CHANGES = ("fixed", "regressed", "same", "changed", "new", "missing", "not_counted")
COMPARE_MAX = 5000
#: The newest score a person gave first, then the newest automatic one: the first row per (trace, level) is current.
CURRENT_ORDER = "(k.source = 'ANNOTATION') DESC, k.created_at DESC, k.id DESC"
_COLS = ("k.id::text AS id, k.trace_id, k.session_id, k.name, k.source, k.string_value, k.value, k.comment,"
         " k.details::text AS details, k.evaluator, k.author_email, k.author_role, k.created_at")


def _out(r: Mapping[str, Any]) -> dict[str, Any]:
    details = r["details"]
    return {"id": r["id"], "trace_id": r["trace_id"], "name": r["name"], "source": r["source"],
            "verdict": r["string_value"], "value": float(r["value"]) if r["value"] is not None else None,
            "comment": r["comment"], "details": json.loads(details) if isinstance(details, str) else (details or {}),
            "evaluator": r["evaluator"], "author_email": r["author_email"], "author_role": r["author_role"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None}


async def history(conn: Any, trace_ids: Sequence[str]) -> dict[str, list[dict[str, Any]]]:
    """Every score of each trace, newest first."""
    out: dict[str, list[dict[str, Any]]] = {t: [] for t in trace_ids}
    if not trace_ids:
        return out
    rows = (await conn.execute(text(
        f"SELECT {_COLS} FROM tracelab.scores k WHERE k.trace_id = ANY(CAST(:ids AS text[]))"
        " ORDER BY k.created_at DESC, k.id DESC"), {"ids": list(trace_ids)})).mappings().all()
    for r in rows:
        out.setdefault(r["trace_id"], []).append(_out(r))
    return out


async def current_scores(conn: Any, trace_ids: Sequence[str]) -> dict[str, dict[str, dict[str, Any] | None]]:
    """{trace id: {level: its current score, or None}} for every trace asked for."""
    out: dict[str, dict[str, dict[str, Any] | None]] = {t: {level: None for level in LEVELS} for t in trace_ids}
    if not trace_ids:
        return out
    rows = (await conn.execute(text(
        f"SELECT DISTINCT ON (k.trace_id, k.name) {_COLS} FROM tracelab.scores k"
        f" WHERE k.trace_id = ANY(CAST(:ids AS text[])) ORDER BY k.trace_id, k.name, {CURRENT_ORDER}"),
        {"ids": list(trace_ids)})).mappings().all()
    # A turn whose expected outcome a reviewer flags as wrong is not counted (13 Nov 2026): while it is flagged, no
    # earlier score of it is current. Its history keeps every score.
    flagged = await expected_flags.flagged_traces(conn, list(trace_ids))
    for r in rows:
        if r["name"] in LEVELS and r["trace_id"] not in flagged:
            out.setdefault(r["trace_id"], {level: None for level in LEVELS})[r["name"]] = _out(r)
    return out


def session_verdict(per_trace: Sequence[Mapping[str, Any] | None]) -> str:
    """A session's verdict at one level from its traces' current scores, one entry per trace (None = not scored)."""
    verdicts = [(s or {}).get("verdict") for s in per_trace]
    if any(v == "fail" for v in verdicts):
        return "fail"
    if verdicts and all(v == "pass" for v in verdicts):
        return "pass"
    return "not_evaluated"


def session_scores(per_trace: Sequence[Mapping[str, Mapping[str, Any] | None]]) -> dict[str, dict[str, str] | None]:
    """A session's verdict and who gave it, per level, from its traces' current scores; None for a level nobody and
    nothing has scored."""
    out: dict[str, dict[str, str] | None] = {}
    for level in LEVELS:
        mine = [t.get(level) for t in per_trace]
        if not any(mine):
            out[level] = None
            continue
        by_person = any(s and s.get("source") == "ANNOTATION" for s in mine)
        out[level] = {"verdict": session_verdict(mine), "source": "ANNOTATION" if by_person else "EVAL"}
    return out


#: SQL for one level's session verdict (bind :level; alias s is the session).
LEVEL_VERDICT_SQL = (
    "SELECT CASE WHEN bool_or(c.string_value = 'fail') THEN 'fail'"
    " WHEN count(*) > 0 AND count(*) FILTER (WHERE c.string_value = 'pass') ="
    " (SELECT count(*) FROM tracelab.traces t WHERE t.session_id = s.id) THEN 'pass' ELSE 'not_evaluated' END"
    " FROM (SELECT DISTINCT ON (k.trace_id) k.string_value FROM tracelab.scores k"
    f" WHERE k.session_id = s.id AND k.name = :level AND NOT {expected_flags.flagged_id_sql('k.trace_id')}"
    f" ORDER BY k.trace_id, {CURRENT_ORDER}) c")
#: SQL that is true for a session with a trace whose current domain score is automatic and asks for an expert.
NEEDS_SME_SQL = (
    "EXISTS (SELECT 1 FROM (SELECT DISTINCT ON (k.trace_id) k.source, k.details FROM tracelab.scores k"
    f" WHERE k.session_id = s.id AND k.name = 'domain' AND NOT {expected_flags.flagged_id_sql('k.trace_id')}"
    f" ORDER BY k.trace_id, {CURRENT_ORDER}) q"
    " WHERE q.source = 'EVAL' AND (q.details->>'needs_sme') = 'true')")


# ── writing ──────────────────────────────────────────────────────────────────────────

async def record_eval(conn: Any, *, trace_id: str, session_id: str, level: str, verdict: str, value: float | None,
                      details: Mapping[str, Any], evaluator: str, comment: str | None = None) -> None:
    """An automatic score. `verdict` is pass, fail or not_evaluated (with the reason in details)."""
    await conn.execute(text(
        "INSERT INTO tracelab.scores (trace_id, session_id, name, source, string_value, value, comment, details,"
        " evaluator) VALUES (:t, :s, :n, 'EVAL', :v, :val, :c, CAST(:d AS jsonb), :e)"),
        {"t": trace_id, "s": session_id, "n": level, "v": verdict, "val": value,
         "c": (comment or "").replace("\x00", "")[:COMMENT_MAX] or None,
         "d": json.dumps(dict(details), default=str).replace("\\u0000", ""), "e": evaluator})


async def annotate(m: labelling.Member, trace_id: str, level: str, *, verdict: str, value: float | None,
                   comment: str | None) -> dict[str, Any]:
    """A person's score on one trace at one level: pass or fail, a score from 0 to 1 and a comment. It becomes the
    current score. The domain level is for a domain expert, the platform team and the consultant."""
    labelling.require_active(m)
    if level not in LEVELS:
        raise Invalid("bad_level", "A trace is scored at the logic, ux or domain level.")
    if verdict not in VERDICTS:
        raise Invalid("bad_verdict", "A score is pass or fail.")
    if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1):
        raise Invalid("bad_value", "A score is a number from 0 to 1.")
    if level == "domain" and m.role not in DOMAIN_ROLES:
        raise Forbidden("domain_experts_only", "Only a domain expert, the platform team or the consultant can score "
                                               "the domain level.")
    note = (comment or "").replace("\x00", "").strip()[:COMMENT_MAX] or None
    scope, p = runs._scope(m, "t")
    async with db.lab_engine().begin() as conn:
        trace = (await conn.execute(text(
            f"SELECT t.id, t.session_id FROM tracelab.traces t WHERE t.id = :id AND {scope}"),
            {**p, "id": trace_id})).mappings().first()
        if trace is None:
            raise NotFound("trace_not_found", "That trace is not in Trace Lab.")
        row = (await conn.execute(text(
            "INSERT INTO tracelab.scores (trace_id, session_id, name, source, string_value, value, comment, author_id,"
            " author_email, author_role) VALUES (:t, :s, :n, 'ANNOTATION', :v, :val, :c, CAST(:by AS uuid), :email,"
            " :role) RETURNING id::text AS id, trace_id, session_id, name, source, string_value, value, comment,"
            " details::text AS details, evaluator, author_email, author_role, created_at"),
            {"t": trace["id"], "s": trace["session_id"], "n": level, "v": verdict, "val": value, "c": note,
             "by": m.user_id, "email": m.email, "role": m.role})).mappings().one()
    return _out(row)


# ── comparing two run tags ───────────────────────────────────────────────────────────

def _change(base: str | None, head: str | None) -> str:
    if base == head:
        return "same"
    if base == "fail" and head == "pass":
        return "fixed"
    if base == "pass" and head == "fail":
        return "regressed"
    return "changed"


async def _side(conn: Any, m: labelling.Member, tag: str) -> dict[str, dict[str, Any]]:
    """The latest attempt of every scenario asked under a run tag, by scenario id, with what compare needs."""
    scope, p = runs._scope(m, "s")
    sessions = (await conn.execute(text(
        "SELECT * FROM (SELECT s.id, s.scenario_id, sc.type, row_number() OVER (PARTITION BY s.scenario_id"
        " ORDER BY s.attempt DESC, s.created_at DESC, s.id DESC) AS rn FROM tracelab.sessions s"
        " LEFT JOIN tracelab.scenarios sc ON sc.scenario_id = s.scenario_id AND sc.version = s.scenario_version"
        f" WHERE s.run_tag = :tag AND s.scenario_id IS NOT NULL AND {scope}) x WHERE x.rn = 1 LIMIT :lim"),
        {**p, "tag": tag, "lim": COMPARE_MAX})).mappings().all()
    ids = [s["id"] for s in sessions]
    traces = (await conn.execute(text(
        "SELECT t.id, t.session_id, t.status, (t.metadata->>'answer_words')::int AS words,"
        " (t.header->>'latency_ms')::numeric AS latency FROM tracelab.traces t"
        " WHERE t.session_id = ANY(CAST(:ids AS text[])) ORDER BY t.session_id, t.turn_index"),
        {"ids": ids})).mappings().all() if ids else []
    current = await current_scores(conn, [t["id"] for t in traces])
    flagged = set(await expected_flags.flagged_traces(conn, [t["id"] for t in traces]))
    by_session: dict[str, list[Mapping[str, Any]]] = {}
    for t in traces:
        by_session.setdefault(t["session_id"], []).append(t)
    out: dict[str, dict[str, Any]] = {}
    for s in sessions:
        mine = by_session.get(s["id"], [])
        per_level = session_scores([current[t["id"]] for t in mine])
        out[s["scenario_id"]] = {
            "session_id": s["id"], "type": s["type"], "turns": len(mine),
            "status": "failed" if any(t["status"] != "ok" for t in mine) else "ok",
            "answer_words": sum(int(t["words"] or 0) for t in mine),
            "latency_ms": int(sum(float(t["latency"] or 0) for t in mine)),
            "verdicts": {level: (per_level[level] or {}).get("verdict", "not_evaluated") for level in LEVELS},
            "expected_flagged": any(t["id"] in flagged for t in mine)}
    return out


async def compare(m: labelling.Member, base: str, head: str) -> dict[str, Any]:
    """Two run tags side by side, scenario by scenario: {"base", "head", "summary", "rows"}.

    Each row has the scenario, both sides (session, verdict per level, answer length in words, time, status; None
    when the scenario was not asked on that side) and the change per level. The summary counts the changes per level
    and gives the median answer length on each side."""
    labelling.require_active(m)
    if not base or not head or base == head:
        raise Invalid("bad_compare", "Choose two different run tags to compare.")
    async with conversations._lab_read() as conn:
        left, right = await _side(conn, m, base), await _side(conn, m, head)
    rows = []
    summary: dict[str, Any] = {level: {c: 0 for c in CHANGES} for level in LEVELS}
    for scenario_id in sorted(set(left) | set(right)):
        b, h = left.get(scenario_id), right.get(scenario_id)
        # A scenario whose expected outcome a reviewer flagged as wrong, on either side, is not counted either way.
        disputed = bool((b and b["expected_flagged"]) or (h and h["expected_flagged"]))
        change = {}
        for level in LEVELS:
            change[level] = "new" if b is None else "missing" if h is None else "not_counted" if disputed else _change(
                b["verdicts"][level], h["verdicts"][level])
            summary[level][change[level]] += 1
        rows.append({"scenario_id": scenario_id, "type": (h or b)["type"], "base": b, "head": h, "change": change,
                     "expected_flagged": disputed})
    words = {side: [s["answer_words"] for s in data.values() if s["answer_words"]]
             for side, data in (("base", left), ("head", right))}
    summary["sessions"] = {"base": len(left), "head": len(right)}
    summary["answer_words"] = {side: int(statistics.median(v)) if v else 0 for side, v in words.items()}
    return {"base": base, "head": head, "summary": summary, "rows": rows}
