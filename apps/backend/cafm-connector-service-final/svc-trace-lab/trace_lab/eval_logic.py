"""The logic level of evaluation (11 Nov 2026, task plan B3): a trace's steps compared with the scenario's expected
outcome, automatically. "Correct plan, call order, scope kept, retries follow the rules, context passed correctly."

    completed         the turn ended normally
    plan_coverage     every expected call was made (the named tool, or any call in the named area), and was in the
                      orchestrator's own plan when the trace records one
    call_order        a call that depends on another started after that one ended
    parallel          calls that do not depend on each other overlapped in time (only for scenarios that test
                      dependency order)
    scope             no call asked for a building, vendor or date outside the scenario's scope
    retries           only temporary errors were retried, with the same parameters, at most RETRY_MAX times
    context_passing   a value from the earlier call's output reached the later call's input
    evidence_state    the state a tool reported (zero_results, not_recorded, unavailable, tool_failed) is the one
                      expected

Each check answers pass, fail, not_observable or not_applicable. That is deliberate. Today's orchestrator records no
structured plan, no retry attempts, no scope check and no evidence state; those arrive with Workstream A, as spans
named plan, retry_attempt, context_passing and scope_check and a `status` on tool outputs. A check that cannot see
what it needs says so. Only a check that FOUND something wrong fails the trace, so the first run of today's code is
measured honestly and the same checks get sharper as the orchestrator records more.

Pure: no database, no model. The nodes are observation rows as eval_store.node_of gives them (the tree view's
nodes); nothing here raises on a payload of an unexpected shape.
"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

EVALUATOR = "logic-1"
RETRY_MAX = 2
CHECKS = ("completed", "plan_coverage", "call_order", "parallel", "scope", "retries", "context_passing",
          "evidence_state")
EVIDENCE_STATES = ("zero_results", "not_recorded", "unavailable", "tool_failed")
#: Names the same area goes by in traces, as the name scenarios use.
DOMAIN_ALIASES = {"maintenance": "wo_engine", "wo": "wo_engine", "work_orders": "wo_engine", "work_order": "wo_engine",
                  "energy": "energy_intelligence", "contract": "contract_performance",
                  "contracts": "contract_performance"}
BUILDING_KEYS = frozenset({"building", "buildings", "building_id", "building_ids", "building_name", "site", "sites",
                           "site_id", "site_ids", "site_name"})
VENDOR_KEYS = frozenset({"vendor", "vendors", "vendor_id", "vendor_ids", "vendor_name"})
FROM_KEYS = frozenset({"from", "date_from", "from_date", "start_date", "since", "period_from"})
TO_KEYS = frozenset({"to", "date_to", "to_date", "end_date", "until", "period_to"})
_PERMANENT = re.compile(r"\b(400|401|403|404|409|422)\b|forbidden|unauthori[sz]ed|denied|validation|invalid|not found", re.I)
_TRANSIENT = re.compile(r"\b(429|500|502|503|504)\b|time[d ]?out|rate.?limit|temporar|unavailable|connection|reset", re.I)
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")
_COMMON = frozenset({"true", "false", "null", "none", "open", "closed", "active", "inactive", "pending", "success",
                     "failed", "error", "unknown", "status"})
VALUES_MAX = 400


@dataclass(frozen=True)
class Check:
    key: str
    result: str                 # pass | fail | not_observable | not_applicable
    note: str = ""


@dataclass(frozen=True)
class LogicResult:
    verdict: str                # pass | fail | not_evaluated
    value: float | None
    checks: tuple[Check, ...]

    def as_details(self) -> dict[str, Any]:
        """What the score row keeps: every check, and how many ended each way."""
        counts = {k: 0 for k in ("pass", "fail", "not_observable", "not_applicable")}
        for c in self.checks:
            counts[c.result] = counts.get(c.result, 0) + 1
        return {"evaluator": EVALUATOR, "checks": [{"key": c.key, "result": c.result, "note": c.note} for c in self.checks],
                "counts": counts}


# ── reading the trace ─────────────────────────────────────────────────────────────────

def _domain(name: Any) -> str | None:
    if not isinstance(name, str) or not name.strip():
        return None
    d = name.strip().lower().replace("-", "_").replace(" ", "_")
    return DOMAIN_ALIASES.get(d, d)


def _time(v: Any) -> datetime | None:
    if isinstance(v, datetime):
        return v
    if isinstance(v, str) and v:
        try:
            return datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _same(a: Any, b: Any) -> bool:
    try:
        return json.dumps(a, sort_keys=True, default=str) == json.dumps(b, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return a == b


def _nodes(nodes: Any) -> list[Mapping[str, Any]]:
    return [n for n in (nodes or []) if isinstance(n, Mapping)]


def _domain_of(node: Mapping[str, Any], by_id: Mapping[str, Mapping[str, Any]]) -> str | None:
    """The area a step belongs to: its own agent, else the nearest step it ran inside that has one."""
    seen = set()
    cur: Mapping[str, Any] | None = node
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        d = _domain(cur.get("agent"))
        if d and d != "orchestrator":
            return d
        cur = by_id.get(str(cur.get("parent_id"))) if cur.get("parent_id") is not None else None
    return None


def tool_calls(nodes: Sequence[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    """The trace's tool calls in the order they started, each with its area, times, whether it failed, the retry
    attempts recorded under it, and which earlier call it repeats (`retry_of`)."""
    rows = _nodes(nodes)
    by_id = {str(n.get("id")): n for n in rows}
    calls: list[dict[str, Any]] = []
    for n in rows:
        if n.get("type") != "tool":
            continue
        attempts = [a for a in rows if a.get("name") == "retry_attempt" and str(a.get("parent_id")) == str(n.get("id"))]
        calls.append({"id": str(n.get("id")), "name": str(n.get("name") or ""), "domain": _domain_of(n, by_id),
                      "input": n.get("input"), "output": n.get("output"), "start": _time(n.get("started_at")),
                      "end": _time(n.get("ended_at")), "failed": n.get("level") == "ERROR" or n.get("ok") is False,
                      "error": str(n.get("error") or ""), "attempts": attempts, "retry_of": None})
    calls.sort(key=lambda c: (c["start"] is None, c["start"] or datetime.min))
    for i, c in enumerate(calls):
        for earlier in reversed(calls[:i]):
            if earlier["failed"] and earlier["name"] == c["name"] and _same(earlier["input"], c["input"]):
                c["retry_of"] = earlier["id"]
                break
    return calls


def planned_calls(nodes: Sequence[Mapping[str, Any]] | None) -> list[dict[str, Any]] | None:
    """The orchestrator's own plan, when the trace records one: the `calls` of the step named plan. None otherwise."""
    for n in _nodes(nodes):
        if n.get("name") == "plan" and isinstance(n.get("output"), Mapping):
            calls = n["output"].get("calls")
            if isinstance(calls, list):
                return [dict(c) for c in calls if isinstance(c, Mapping)]
    return None


# ── the checks ───────────────────────────────────────────────────────────────────────

def _plan(expected: Any) -> list[Mapping[str, Any]]:
    plan = expected.get("plan") if isinstance(expected, Mapping) else None
    return [c for c in (plan or []) if isinstance(c, Mapping) and c.get("call_id")]


def _label(e: Mapping[str, Any]) -> str:
    return f"{e.get('call_id')} ({e.get('tool') or e.get('domain') or 'a call'})"


def _matches(e: Mapping[str, Any], calls: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    if e.get("tool"):
        return [c for c in calls if c["name"] == e["tool"]]
    want = _domain(e.get("domain"))
    return [c for c in calls if want and c["domain"] == want]


def _first(e: Mapping[str, Any], calls: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    found = _matches(e, calls)
    return found[0] if found else None


def _check_plan(plan: Sequence[Mapping[str, Any]], rows: Sequence[Mapping[str, Any]],
                calls: Sequence[Mapping[str, Any]]) -> Check:
    if not plan:
        return Check("plan_coverage", "not_applicable", "No calls are expected.")
    if len(rows) <= 1:
        return Check("plan_coverage", "not_observable", "The trace has no recorded steps.")
    by_id = {str(n.get("id")): n for n in rows}
    active = {d for d in (_domain_of(n, by_id) for n in rows if n.get("parent_id") is not None) if d}
    missing = [_label(e) for e in plan
               if not _matches(e, calls) and (e.get("tool") or _domain(e.get("domain")) not in active)]
    if missing:
        return Check("plan_coverage", "fail", "No call was made for " + ", ".join(missing) + ".")
    planned = planned_calls(rows)
    if planned is not None:
        tools = {str(p.get("tool") or "") for p in planned}
        domains = {_domain(p.get("domain")) for p in planned}
        unplanned = [_label(e) for e in plan
                     if (e.get("tool") and e["tool"] not in tools)
                     or (not e.get("tool") and any(p.get("domain") for p in planned)
                         and _domain(e.get("domain")) not in domains)]
        if unplanned:
            return Check("plan_coverage", "fail", "Not in the planned calls: " + ", ".join(unplanned) + ".")
    return Check("plan_coverage", "pass", f"All {len(plan)} expected calls were made.")


def _edges(plan: Sequence[Mapping[str, Any]]) -> list[tuple[Mapping[str, Any], Mapping[str, Any]]]:
    by_id = {e["call_id"]: e for e in plan}
    return [(by_id[d], e) for e in plan for d in (e.get("depends_on") or []) if d in by_id]


def _check_order(plan: Sequence[Mapping[str, Any]], calls: Sequence[Mapping[str, Any]]) -> Check:
    edges = _edges(plan)
    if not edges:
        return Check("call_order", "not_applicable", "No call depends on another.")
    seen = 0
    for a, b in edges:
        ca, cb = _first(a, calls), _first(b, calls)
        if ca is None or cb is None or ca["end"] is None or cb["start"] is None:
            continue
        seen += 1
        if cb["start"] < ca["end"]:
            return Check("call_order", "fail", f"{_label(b)} started before {_label(a)} ended.")
    if not seen:
        return Check("call_order", "not_observable", "The calls or their times are not recorded.")
    return Check("call_order", "pass", "Every dependent call started after the call it depends on ended.")


def _reaches(plan: Sequence[Mapping[str, Any]], start: str, goal: str) -> bool:
    deps = {e["call_id"]: list(e.get("depends_on") or []) for e in plan}
    todo, seen = [start], set()
    while todo:
        cur = todo.pop()
        if cur == goal:
            return True
        if cur in seen:
            continue
        seen.add(cur)
        todo.extend(deps.get(cur, []))
    return False


def _check_parallel(plan: Sequence[Mapping[str, Any]], calls: Sequence[Mapping[str, Any]],
                    sections: Sequence[str]) -> Check:
    if "dependency_order" not in sections:
        return Check("parallel", "not_applicable", "The scenario does not test dependency order.")
    pairs = [(a, b) for i, a in enumerate(plan) for b in plan[i + 1:]
             if not _reaches(plan, a["call_id"], b["call_id"]) and not _reaches(plan, b["call_id"], a["call_id"])]
    if not pairs:
        return Check("parallel", "not_applicable", "No two expected calls are independent.")
    seen = 0
    for a, b in pairs:
        ca, cb = _first(a, calls), _first(b, calls)
        if ca is None or cb is None or None in (ca["start"], ca["end"], cb["start"], cb["end"]):
            continue
        seen += 1
        if not (ca["start"] < cb["end"] and cb["start"] < ca["end"]):
            return Check("parallel", "fail", f"{_label(a)} and {_label(b)} do not depend on each other but ran one "
                                             "after the other.")
    if not seen:
        return Check("parallel", "not_observable", "The calls or their times are not recorded.")
    return Check("parallel", "pass", "Independent calls overlapped in time.")


def _params(value: Any, depth: int = 0) -> list[tuple[str, Any]]:
    """Every (key, value) pair in a call's parameters, at any depth."""
    out: list[tuple[str, Any]] = []
    if depth > 6:
        return out
    if isinstance(value, Mapping):
        for k, v in value.items():
            out.append((str(k).lower(), v))
            out.extend(_params(v, depth + 1))
    elif isinstance(value, list):
        for v in value[:200]:
            out.extend(_params(v, depth + 1))
    return out


def _listed(value: Any) -> list[str]:
    items = value if isinstance(value, list) else [value]
    return [str(v).strip() for v in items if isinstance(v, (str, int)) and not isinstance(v, bool) and str(v).strip()]


def _check_scope(scope: Any, rows: Sequence[Mapping[str, Any]], calls: Sequence[Mapping[str, Any]]) -> Check:
    told = [n for n in rows if n.get("name") == "scope_check" and isinstance(n.get("output"), Mapping)]
    for n in told:
        if n["output"].get("ok") is False:
            said = n["output"].get("violations")
            said = [] if said is None else list(said.values()) if isinstance(said, Mapping) else (
                said if isinstance(said, list) else [said])
            why = "; ".join(str(v) for v in said[:5]) or "no reason given"
            return Check("scope", "fail", "The orchestrator's own scope check failed: " + why[:400])
    s = scope if isinstance(scope, Mapping) else {}
    buildings = {b.lower() for b in _listed(s.get("buildings") or [])}
    vendors = {v.lower() for v in _listed(s.get("vendors") or [])}
    period = s.get("period") if isinstance(s.get("period"), Mapping) else {}
    start = period.get("from") if isinstance(period.get("from"), str) and _DATE.match(period.get("from")) else None
    end = period.get("to") if isinstance(period.get("to"), str) and _DATE.match(period.get("to")) else None
    if not (buildings or vendors or start or end):
        return Check("scope", "pass", "The orchestrator's own scope check passed.") if told else Check(
            "scope", "not_applicable", "The scenario sets no scope.")
    compared = 0
    for c in calls:
        for key, value in _params(c["input"]):
            for allowed, keys, what in ((buildings, BUILDING_KEYS, "building"), (vendors, VENDOR_KEYS, "vendor")):
                if allowed and key in keys:
                    for v in _listed(value):
                        compared += 1
                        if v.lower() not in allowed:
                            return Check("scope", "fail", f"{c['name']} asked for {what} {v}, which is outside the scope.")
            if (start or end) and key in (FROM_KEYS | TO_KEYS) and isinstance(value, str) and _DATE.match(value):
                compared += 1
                day = value[:10]
                if (start and day < start[:10]) or (end and day > end[:10]):
                    return Check("scope", "fail", f"{c['name']} asked for {key} {day}, which is outside the period "
                                                  f"{start or '...'} to {end or '...'}.")
    if not compared and not told:
        return Check("scope", "not_observable", "No call carries a building, vendor or date to compare.")
    return Check("scope", "pass", "No call asked for anything outside the scope.")


def _check_retries(rows: Sequence[Mapping[str, Any]], calls: Sequence[Mapping[str, Any]]) -> Check:
    judged = 0
    by_id = {c["id"]: c for c in calls}
    repeats: dict[str, int] = {}
    for c in calls:
        if c["retry_of"]:
            first = c["retry_of"]
            while by_id.get(first, {}).get("retry_of"):
                first = by_id[first]["retry_of"]
            repeats[first] = repeats.get(first, 0) + 1
    for c in calls:
        attempts = c["attempts"]
        again = repeats.get(c["id"], 0)
        if not attempts and not again:
            continue
        judged += 1
        reason = " ".join([c["error"], *[str(a.get("error") or "") for a in attempts],
                           *[str((a.get("metadata") or {}).get("reason") or "") for a in attempts
                             if isinstance(a.get("metadata"), Mapping)]])
        if _PERMANENT.search(reason) and not _TRANSIENT.search(c["error"] or ""):
            return Check("retries", "fail", f"{c['name']} was retried after an error that is not temporary "
                                            f"({(c['error'] or reason).strip()[:120]}).")
        if any(not _same(a.get("input"), c["input"]) for a in attempts):
            return Check("retries", "fail", f"{c['name']} was retried with different parameters.")
        if len(attempts) + again > RETRY_MAX:
            return Check("retries", "fail", f"{c['name']} was retried {len(attempts) + again} times; the limit is "
                                            f"{RETRY_MAX}.")
    if not judged:
        return Check("retries", "not_applicable", "Nothing was retried.")
    return Check("retries", "pass", "Every retry followed a temporary error with the same parameters.")


def _values(value: Any, out: set[str], depth: int = 0) -> None:
    """Values worth looking for downstream: ids and names, not flags or common words."""
    if len(out) >= VALUES_MAX or depth > 6:
        return
    if isinstance(value, Mapping):
        for v in value.values():
            _values(v, out, depth + 1)
    elif isinstance(value, list):
        for v in value[:200]:
            _values(v, out, depth + 1)
    elif isinstance(value, str):
        v = value.strip()
        if 3 <= len(v) <= 200 and v.lower() not in _COMMON and (len(v) >= 6 or any(ch.isdigit() for ch in v)):
            out.add(v)
    elif isinstance(value, int) and not isinstance(value, bool) and value >= 1000:
        out.add(str(value))


def _check_context(plan: Sequence[Mapping[str, Any]], rows: Sequence[Mapping[str, Any]],
                   calls: Sequence[Mapping[str, Any]]) -> Check:
    edges = _edges(plan)
    if not edges:
        return Check("context_passing", "not_applicable", "No call depends on another.")
    if any(n.get("name") == "context_passing" for n in rows):
        return Check("context_passing", "pass", "The trace records what was passed from one call to the next.")
    seen = 0
    for a, b in edges:
        ca, cb = _first(a, calls), _first(b, calls)
        if ca is None or cb is None or ca["output"] is None or cb["input"] is None:
            continue
        found: set[str] = set()
        _values(ca["output"], found)
        if not found:
            continue
        seen += 1
        target = json.dumps(cb["input"], default=str)
        if not any(v in target for v in found):
            return Check("context_passing", "fail", f"Nothing from the output of {_label(a)} appears in the input of "
                                                    f"{_label(b)}.")
    if not seen:
        return Check("context_passing", "not_observable", "The calls' inputs and outputs are not recorded.")
    return Check("context_passing", "pass", "A value from the earlier call reached the later call.")


def _check_evidence(expected: Any, calls: Sequence[Mapping[str, Any]]) -> Check:
    fh = expected.get("failure_handling") if isinstance(expected, Mapping) else None
    want = fh.get("state") if isinstance(fh, Mapping) else None
    if want not in EVIDENCE_STATES:
        return Check("evidence_state", "not_applicable", "No evidence state is expected.")
    said = [str(c["output"].get("status")) for c in calls
            if isinstance(c["output"], Mapping) and c["output"].get("status") in (*EVIDENCE_STATES, "ok")]
    if not said:
        return Check("evidence_state", "not_observable", "No tool reported an evidence state.")
    if want in said:
        return Check("evidence_state", "pass", f"A tool reported {want}.")
    return Check("evidence_state", "fail", f"Expected {want}; the tools reported {', '.join(sorted(set(said)))}.")


def evaluate(expected: Any, scope: Any, nodes: Any, *, trace_status: str,
             sections: Sequence[str] | None = None) -> LogicResult:
    """Compare one trace with one turn's expected outcome. Never raises on what it is given.

    `sections` are the specification sections the scenario tests; the parallel check runs only for dependency_order.
    The verdict is fail when any check failed; pass when none failed and at least one check besides `completed`
    passed (that the turn ended normally says nothing about its plan, order, scope, retries or context); else
    not_evaluated, with no value. The value is passes over passes plus fails."""
    rows = _nodes(nodes)
    calls = tool_calls(rows)
    plan = _plan(expected)
    done = Check("completed", "pass", "The turn ended normally.") if trace_status == "ok" else Check(
        "completed", "fail", f"The turn ended as {trace_status or 'unknown'}.")
    checks = (done, _check_plan(plan, rows, calls), _check_order(plan, calls),
              _check_parallel(plan, calls, list(sections or [])), _check_scope(scope, rows, calls),
              _check_retries(rows, calls), _check_context(plan, rows, calls), _check_evidence(expected, calls))
    passed = sum(1 for c in checks if c.result == "pass")
    failed = sum(1 for c in checks if c.result == "fail")
    seen = any(c.result == "pass" for c in checks if c.key != "completed")
    verdict = "fail" if failed else ("pass" if seen else "not_evaluated")
    value = round(passed / (passed + failed), 3) if verdict != "not_evaluated" and passed + failed else None
    return LogicResult(verdict=verdict, value=value, checks=checks)
