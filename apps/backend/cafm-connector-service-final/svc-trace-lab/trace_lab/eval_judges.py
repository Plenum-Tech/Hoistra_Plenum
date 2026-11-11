"""The user-experience and domain levels, and the pass that scores a run tag at all three (11 Nov 2026, task plan B3).

    logic     automatic, by comparing the trace with the expected outcome                  eval_logic.py
    ux        "concise, complete, right template, clear wording on failures and denials"   a model judge, here
    domain    "correct against domain rules"                                               a rubric + a model judge, here

A model judge only fills in criteria. The verdict is decided in code from those criteria and from what code can check
by itself: the answer's length against CONCISE_WORDS, and the phrases the expected outcome says the answer must and
must not contain. A judge that calls a long or incomplete answer fine cannot pass it.

The domain rubric is the scenario's own domain rules plus the knowledge document of each area the turn uses (the
compliance and contract skills have one). With no rubric the level is not evaluated rather than guessed. A domain
fail, or a pass the judge is unsure of, is marked needs_sme: it waits for a subject-matter expert, whose score then
becomes the current one (scores.py).

evaluate_run scores the latest attempt of every scenario under a run tag, a few traces per call so a page or a
script can drive it: one automatic score per trace per level, written once per evaluator version (a second pass
scores nothing new). It stops at its spending limit and says what is left. What cannot be scored (a session with no
scenario, a domain with no rules) is recorded as not_evaluated with the reason, once.

People score on top of this in Trace Lab; an automatic score never replaces a person's. Model calls go through
proposals.ask_json (its own ledger, an empty context), behind the ASK seam.
G0: imports svc-deepagents (the skills' documents, read-only), writes only Trace Lab's own scores.
"""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

import structlog
from sqlalchemy import text

from . import conversations, db, eval_logic, eval_store, labelling, proposals, runs, scores, spec_doc
from .review_errors import Conflict, Forbidden, Invalid

log = structlog.get_logger(__name__)

UX_EVALUATOR, DOMAIN_EVALUATOR = "ux-1", "domain-1"
EVALUATORS = {"logic": eval_logic.EVALUATOR, "ux": UX_EVALUATOR, "domain": DOMAIN_EVALUATOR}
UX_ROLE, DOMAIN_ROLE = "trace_lab_eval_ux", "trace_lab_eval_domain"
#: An answer longer than this is not concise, whatever the judge says.
CONCISE_WORDS = 250
EVIDENCE_CHARS = 6000
DOC_CHARS = 12_000
MAX_TOKENS = 800
PASS_MAX = 100
#: What the model judges may spend on one run tag, over every pass. 250 scenarios cost about $4 at both levels.
RUN_TAG_BUDGET_USD = 20.0
#: What a judge call is counted at when its price is not known (a model missing from the price list): never nothing.
UNPRICED_USD = 0.02
#: The levels that call a model.
JUDGED = ("ux", "domain")
#: The advisory-lock namespace that keeps one pass at a time on a run tag.
LOCK_NS = 7311011
ANSWER_FORMATS = ("success", "partial_success", "denial", "failure")
#: The knowledge document of an area, as (skill folder, document name), read live through svc-deepagents.
DOMAIN_DOCS = {"compliance": ("compliance", "domain_compliance_knowledge"),
               "contract_performance": ("contract-performance", "domain_contract_knowledge")}

UX_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "concise": {"type": "boolean"},
        "complete": {"type": "boolean"},
        "template": {"type": "string", "enum": [*ANSWER_FORMATS, "none"]},
        "failure_wording": {"type": "string", "enum": ["clear", "unclear", "n/a"]},
        "reason": {"type": "string"},
    },
    "required": ["concise", "complete", "template", "failure_wording", "reason"],
    "additionalProperties": False,
}
DOMAIN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "violations": {"type": "array", "items": {
            "type": "object", "properties": {"rule": {"type": "string"}, "why": {"type": "string"}},
            "required": ["rule", "why"], "additionalProperties": False}},
        "confidence": {"type": "string", "enum": ["high", "low"]},
        "reason": {"type": "string"},
    },
    "required": ["violations", "confidence", "reason"],
    "additionalProperties": False,
}
UX_SYSTEM = (
    "You judge how an answer from a facilities-management assistant reads to the person who asked. You do not judge "
    "whether its facts are right. Fill in each criterion:\n"
    "- concise: it says what was asked for without padding, repetition or a tour of how it was worked out.\n"
    "- complete: every part of the question is answered, or the answer says why a part is not.\n"
    "- template: which one the answer follows. success: a direct answer. partial_success: answers what worked and "
    "says clearly which part could not be answered. denial: says in plain business words that the person may not "
    "see this, with no internal message. failure: says what could not be done and gives a next step, with no raw "
    "error. none: it follows none of them.\n"
    "- failure_wording: clear when a failure or denial is explained in plain words with what to do next; unclear "
    "when it shows a raw error, an internal code or name, a stack trace, or is vague; n/a when the answer reports no "
    "failure or denial.\n"
    "- reason: one or two sentences a reviewer can check against the answer.")
DOMAIN_SYSTEM = (
    "You check an answer from a facilities-management assistant against the domain rules you are given and the "
    "records the tools returned. Report a violation only when the answer states something a rule or the records "
    "contradict, or leaves out something a rule requires for this question. Name the rule by its id. Do not judge "
    "style. Set confidence to low when the rules do not cover the question or the records are too thin to tell.")


def _read_doc(agent: str, name: str) -> str:
    from src.agents import skills

    return skills.prompt_doc(agent, name)


#: The seams tests replace: the model call, and the read of a skill's knowledge document.
ASK = proposals.ask_json
READ_DOC = _read_doc


# ── user experience ──────────────────────────────────────────────────────────────────

def _phrases(v: Any) -> list[str]:
    return [str(x) for x in (v or []) if isinstance(x, str) and x.strip()]


def ux_precheck(answer: str, expected: Mapping[str, Any] | None) -> dict[str, Any]:
    """What code checks without a model: the answer's length in words, the phrases it must contain and does not, and
    the phrases it must not contain and does. Matching ignores case."""
    e = expected or {}
    text_ = str(answer or "")
    low = text_.lower()
    return {"answer_words": len(text_.split()),
            "missing": [p for p in _phrases(e.get("must_include")) if p.lower() not in low],
            "forbidden_present": [p for p in _phrases(e.get("must_not_include")) if p.lower() in low]}


def ux_verdict(reply: Mapping[str, Any] | None, expected: Mapping[str, Any] | None,
               pre: Mapping[str, Any]) -> tuple[str, float, dict[str, Any]]:
    """(verdict, value, details) from the judge's criteria and the precheck. Pass needs all four: concise (and within
    the word limit), complete (and nothing missing or forbidden), the expected template, and no unclear failure
    wording. The value is the share of the four that hold."""
    r, e = reply or {}, expected or {}
    want = e.get("answer_format")
    found = r.get("template")
    criteria = {
        "concise": r.get("concise") is True and int(pre.get("answer_words") or 0) <= CONCISE_WORDS,
        "complete": r.get("complete") is True and not pre.get("missing") and not pre.get("forbidden_present"),
        "template": found in ANSWER_FORMATS and (want is None or found == want),
        "failure_wording": r.get("failure_wording") in ("clear", "n/a"),
    }
    held = sum(1 for ok in criteria.values() if ok)
    details = {"evaluator": UX_EVALUATOR, "criteria": criteria, "answer_words": int(pre.get("answer_words") or 0),
               "missing": list(pre.get("missing") or []), "forbidden_present": list(pre.get("forbidden_present") or []),
               "template": {"expected": want, "found": found}, "reason": str(r.get("reason") or "")[:1000]}
    return ("pass" if held == len(criteria) else "fail"), round(held / len(criteria), 3), details


def _ux_prompt(question: str, history: Sequence[Mapping[str, Any]], answer: str, expected: Mapping[str, Any]) -> str:
    parts = ["Question:", question or "(none)", ""]
    if history:
        parts.append("Earlier in this conversation:")
        for i, h in enumerate(history, start=1):
            parts += [f"Turn {i} question: {h.get('question') or ''}", f"Turn {i} answer: {h.get('answer') or ''}"]
        parts.append("")
    parts += ["Answer to judge:", answer or "(no answer)", "", "Expected outcome:",
              f"- answer format: {expected.get('answer_format')}"]
    fh = expected.get("failure_handling")
    if isinstance(fh, Mapping):
        failed = ", ".join(fh.get("failed") or [])
        parts.append(f"- failure handling: {fh.get('state')}" + (f"; the answer must say that {failed} failed" if failed else ""))
    if _phrases(expected.get("must_include")):
        parts.append("- must include: " + "; ".join(_phrases(expected.get("must_include"))))
    if _phrases(expected.get("must_not_include")):
        parts.append("- must not include: " + "; ".join(_phrases(expected.get("must_not_include"))))
    if expected.get("answer"):
        parts.append(f"- expected answer: {expected['answer']}")
    return "\n".join(parts)


async def judge_ux(*, question: str, history: Sequence[Mapping[str, Any]], answer: str,
                   expected: Mapping[str, Any]) -> tuple[dict[str, Any], float | None]:
    """Ask the judge for the four criteria. Returns (the reply, what the call cost in dollars)."""
    reply, entry = await ASK(UX_SYSTEM, _ux_prompt(question, history, answer, expected), UX_SCHEMA, role=UX_ROLE,
                             max_tokens=MAX_TOKENS)
    usd = (entry or {}).get("usd")
    return dict(reply), (float(usd) if usd else None)


# ── domain ───────────────────────────────────────────────────────────────────────────

def domain_rubric(scenario: Mapping[str, Any] | None, expected: Mapping[str, Any] | None) -> list[dict[str, str]]:
    """The rules a turn is judged against: the scenario's own, then the knowledge document of each area the turn's
    plan uses. A document that cannot be read is left out."""
    rules = [{"id": str(r["id"]), "text": str(r["text"])} for r in ((scenario or {}).get("domain_rules") or [])
             if isinstance(r, Mapping) and r.get("id") and r.get("text")]
    seen: list[str] = []
    for call in ((expected or {}).get("plan") or []):
        domain = eval_logic._domain(call.get("domain")) if isinstance(call, Mapping) else None
        if not domain or domain in seen or domain not in DOMAIN_DOCS:
            continue
        seen.append(domain)
        try:
            body = READ_DOC(*DOMAIN_DOCS[domain])
        except Exception as exc:                  # noqa: BLE001 - a missing document is not this trace's failure
            log.warning("trace_lab.eval.rubric_unreadable", domain=domain, error=type(exc).__name__)
            continue
        if isinstance(body, str) and body.strip():
            rules.append({"id": f"{domain}:knowledge", "text": body.strip()[:DOC_CHARS]})
    return rules


def evidence_text(nodes: Sequence[Mapping[str, Any]] | None) -> str:
    """What the tools returned, as text for the judge: one line per tool call, cut to EVIDENCE_CHARS overall."""
    lines = []
    for n in (nodes or []):
        if isinstance(n, Mapping) and n.get("type") == "tool" and n.get("output") is not None:
            lines.append(f"{n.get('name')}: {json.dumps(n.get('output'), default=str)[:2000]}")
    out = "\n".join(lines)
    return out if len(out) <= EVIDENCE_CHARS else out[:EVIDENCE_CHARS] + " … [cut]"


def domain_verdict(reply: Mapping[str, Any] | None) -> tuple[str, float, dict[str, Any]]:
    """(verdict, value, details). Pass when no rule is broken. needs_sme on a fail, or when the judge is not sure."""
    r = reply or {}
    violations = [{"rule": str(v.get("rule") or ""), "why": str(v.get("why") or "")[:600]}
                  for v in (r.get("violations") or []) if isinstance(v, Mapping)]
    confident = r.get("confidence") == "high"
    verdict = "fail" if violations else "pass"
    return verdict, (0.0 if violations else 1.0), {
        "evaluator": DOMAIN_EVALUATOR, "violations": violations, "confidence": r.get("confidence") or "low",
        "needs_sme": bool(violations) or not confident, "reason": str(r.get("reason") or "")[:1000]}


def _domain_prompt(question: str, answer: str, evidence: str, rules: Sequence[Mapping[str, str]]) -> str:
    listed = "\n\n".join(f"[{r['id']}]\n{r['text']}" for r in rules)
    return "\n".join(["Question:", question or "(none)", "", "Answer to check:", answer or "(no answer)", "",
                      "What the tools returned:", evidence or "(nothing recorded)", "", "Domain rules:", listed])


async def judge_domain(*, question: str, answer: str, evidence: str,
                       rules: Sequence[Mapping[str, str]]) -> tuple[dict[str, Any], float | None]:
    reply, entry = await ASK(DOMAIN_SYSTEM, _domain_prompt(question, answer, evidence, rules), DOMAIN_SCHEMA,
                             role=DOMAIN_ROLE, max_tokens=MAX_TOKENS)
    usd = (entry or {}).get("usd")
    return dict(reply), (float(usd) if usd else None)


# ── scoring a run tag ────────────────────────────────────────────────────────────────

def _json(v: Any) -> Any:
    return json.loads(v) if isinstance(v, str) else v


async def _load(m: labelling.Member, run_tag: str) -> dict[str, Any]:
    """Everything a pass needs, read once: the latest attempt of each scenario under the tag, their traces in turn
    order, the scenario version each session was asked with, which automatic scores exist, and the current
    specification's hashes."""
    scope, p = runs._scope(m, "s")
    async with conversations._lab_read() as conn:
        sessions = [dict(r) for r in (await conn.execute(text(
            "SELECT * FROM (SELECT s.id, s.organization_id::text AS organization_id, s.scenario_id, s.scenario_version,"
            " s.started_at, row_number() OVER"
            " (PARTITION BY s.scenario_id ORDER BY s.attempt DESC, s.created_at DESC, s.id DESC) AS rn"
            f" FROM tracelab.sessions s WHERE s.run_tag = :tag AND {scope}) x"
            " WHERE x.rn = 1 OR x.scenario_id IS NULL ORDER BY x.started_at, x.id"),
            {**p, "tag": run_tag})).mappings().all()]
        ids = [s["id"] for s in sessions]
        traces = [dict(r) for r in (await conn.execute(text(
            "SELECT t.id, t.session_id, t.turn_index, t.input, t.output, t.status FROM tracelab.traces t"
            " WHERE t.session_id = ANY(CAST(:ids AS text[])) ORDER BY t.session_id, t.turn_index"),
            {"ids": ids})).mappings().all()] if ids else []
        done = {(r[0], r[1], r[2]) for r in (await conn.execute(text(
            "SELECT k.trace_id, k.name, k.evaluator FROM tracelab.scores k WHERE k.source = 'EVAL'"
            " AND k.trace_id = ANY(CAST(:ids AS text[]))"), {"ids": [t["id"] for t in traces]})).all()} if traces else set()
        scenarios: dict[tuple[str, int], dict[str, Any]] = {}
        for key in {(s["scenario_id"], s["scenario_version"]) for s in sessions if s["scenario_id"]}:
            row = (await conn.execute(text(
                "SELECT scenario_id, version, organization_id::text AS organization_id, spec_sections,"
                " turns::text AS turns, scope::text AS scope, faults::text AS faults,"
                " domain_rules::text AS domain_rules, spec_hashes::text AS spec_hashes, status"
                " FROM tracelab.scenarios WHERE scenario_id = :id AND version = :v"),
                {"id": key[0], "v": key[1]})).mappings().first()
            if row is not None:
                scenarios[key] = {**dict(row), **{k: _json(row[k]) for k in ("turns", "scope", "faults", "domain_rules",
                                                                             "spec_hashes")},
                                  "spec_sections": list(row["spec_sections"] or [])}
        _version, hashes = await spec_doc.current_hashes(conn)
        spent = float((await conn.execute(text(
            "SELECT coalesce(sum((k.details->>'usd')::numeric), 0) FROM tracelab.scores k"
            " JOIN tracelab.sessions s ON s.id = k.session_id WHERE k.source = 'EVAL' AND s.run_tag = :tag"),
            {"tag": run_tag})).scalar() or 0)
    return {"sessions": {s["id"]: s for s in sessions}, "order": ids, "traces": traces, "done": done,
            "scenarios": scenarios, "hashes": hashes, "spent": spent}


def _asked(text_: Any) -> str:
    """A question as compared with its scenario turn: case, spacing and the closing mark do not count."""
    return " ".join(str(text_ or "").split()).casefold().rstrip(" ?.!")


async def evaluate_run(m: labelling.Member, run_tag: str, *, levels: Sequence[str] = scores.LEVELS, limit: int = 25,
                       usd_cap: float | None = None, skip: Sequence[str] = ()) -> dict[str, Any]:
    """Score up to `limit` traces of a run tag at the levels asked for. Curators only; the levels that call a model
    (ux, domain) only for the platform team and the consultant. One pass at a time on a run tag.

    Returns {"traces": scored in this pass, "scored": {level: n}, "not_evaluated": {level: n}, "errors": judge calls
    that failed, "failed_traces": their trace ids, "usd": spent in this pass, "spent_usd": spent on the run tag so far,
    "budget_usd", "capped", "remaining": (trace, level) pairs still without an automatic score, "left_for_later": those
    of them on traces passed over through `skip`}. A caller runs passes until nothing is left but what it passes over,
    adding each pass's failed_traces to `skip` so a judge that is down does not hold up the traces after it.

    `usd_cap` is the run tag's budget over every pass (RUN_TAG_BUDGET_USD when not given). A judge call with no price
    is counted at UNPRICED_USD."""
    runs.require_curator(m)
    wanted = tuple(dict.fromkeys(levels))
    if not wanted or any(level not in scores.LEVELS for level in wanted):
        raise Invalid("bad_level", "Traces are evaluated at the logic, ux and domain levels.")
    if not m.all_companies and any(level in JUDGED for level in wanted):
        raise Forbidden("platform_only", "Only the platform team and the consultant can run the model judges, which "
                                         "cost money. Company admins can run the logic checks.")
    limit = max(1, min(int(limit or 25), PASS_MAX))
    budget = RUN_TAG_BUDGET_USD if usd_cap is None else float(usd_cap)
    lock = {"ns": LOCK_NS, "tag": run_tag}
    async with db.lab_engine().connect() as held:
        got = (await held.execute(text("SELECT pg_try_advisory_lock(:ns, hashtext(:tag))"), lock)).scalar()
        await held.commit()
        if not got:
            raise Conflict("evaluation_running", "This run tag is being evaluated already. Try again when that pass "
                                                 "has finished.")
        try:
            return await _one_pass(m, run_tag, wanted, limit, budget, {str(x) for x in skip or ()})
        finally:
            await held.execute(text("SELECT pg_advisory_unlock(:ns, hashtext(:tag))"), lock)
            await held.commit()


async def _one_pass(m: labelling.Member, run_tag: str, wanted: tuple[str, ...], limit: int, budget: float,
                    skip: set[str]) -> dict[str, Any]:
    data = await _load(m, run_tag)
    by_session: dict[str, list[dict[str, Any]]] = {}
    for t in data["traces"]:
        by_session.setdefault(t["session_id"], []).append(t)
    todo, later = [], 0
    for sid in data["order"]:
        for t in by_session.get(sid, []):
            missing = [level for level in wanted if (t["id"], level, EVALUATORS[level]) not in data["done"]]
            if missing and t["id"] in skip:
                later += len(missing)
            elif missing:
                todo.append((t, missing))
    out: dict[str, Any] = {"traces": 0, "scored": {level: 0 for level in wanted},
                           "not_evaluated": {level: 0 for level in wanted}, "errors": 0, "failed_traces": [],
                           "usd": 0.0, "spent_usd": round(data["spent"], 6), "budget_usd": budget, "capped": False}
    written = 0
    for trace, missing in todo[:limit]:
        session = data["sessions"][trace["session_id"]]
        scenario = data["scenarios"].get((session["scenario_id"], session["scenario_version"]))
        if scenario and scenario["organization_id"] not in (None, session["organization_id"]):
            scenario = None                 # another company's scenario is never this session's expected outcome
        turns = (scenario or {}).get("turns") or []
        turn = turns[trace["turn_index"] - 1] if 0 < trace["turn_index"] <= len(turns) else None
        expected = turn.get("expected") if isinstance(turn, Mapping) else None
        flags = {"scenario": {"id": scenario["scenario_id"], "version": scenario["version"]},
                 "expected_status": scenario["status"],
                 "stale_expected": bool(spec_doc.stale_sections(scenario["spec_hashes"], data["hashes"],
                                                                 scenario["spec_sections"]))} if scenario else {}
        async with conversations._lab_read() as conn:
            nodes = await eval_store.nodes_of(conn, trace["id"])
        # Why this trace cannot be compared with its turn's expected outcome, if it cannot: no scenario, no expected
        # outcome for this turn, another question asked than the turn's (a trace is matched to a turn by its place),
        # or a failure the scenario needs that was never made to happen (faults are injected by a runner; without one
        # the expected failure handling is bound to look wrong).
        why_not = None
        if scenario is None:
            why_not = "no_scenario"
        elif expected is None:
            why_not = "no_expected_for_turn"
        elif _asked(trace["input"]) != _asked(turn.get("message")):
            why_not = "question_differs"
        elif scenario.get("faults") and not any(c["failed"] for c in eval_logic.tool_calls(nodes)):
            why_not = "fault_not_injected"
        earlier = [{"question": x["input"], "answer": x["output"]} for x in by_session[trace["session_id"]]
                   if x["turn_index"] < trace["turn_index"]]
        rows: list[dict[str, Any]] = []
        for level in missing:
            if why_not:
                rows.append({"level": level, "verdict": "not_evaluated", "value": None, "comment": None,
                             "details": {"reason": why_not, **flags}})
                continue
            if level == "logic":
                try:
                    result = eval_logic.evaluate(expected, scenario.get("scope"), nodes, trace_status=trace["status"],
                                                 sections=scenario["spec_sections"])
                except Exception as exc:      # noqa: BLE001 - one trace must not end the pass, or block the run tag
                    log.warning("trace_lab.eval.logic_failed", trace=trace["id"], error=type(exc).__name__)
                    rows.append({"level": level, "verdict": "not_evaluated", "value": None, "comment": None,
                                 "details": {"reason": "evaluator_error", "error": type(exc).__name__, **flags}})
                    continue
                failed = "; ".join(c.note for c in result.checks if c.result == "fail") or None
                rows.append({"level": level, "verdict": result.verdict, "value": result.value, "comment": failed,
                             "details": {**result.as_details(), **flags}})
                continue
            rubric = domain_rubric(scenario, expected) if level == "domain" else []
            if level == "domain" and not rubric:
                rows.append({"level": level, "verdict": "not_evaluated", "value": None, "comment": None,
                             "details": {"reason": "no_domain_rules", **flags}})
                continue
            if out["spent_usd"] >= budget:
                out["capped"] = True
                break
            try:
                if level == "ux":
                    reply, usd = await judge_ux(question=trace["input"] or "", history=earlier,
                                                answer=trace["output"] or "", expected=expected)
                    verdict, value, details = ux_verdict(reply, expected, ux_precheck(trace["output"] or "", expected))
                else:
                    reply, usd = await judge_domain(question=trace["input"] or "", answer=trace["output"] or "",
                                                    evidence=evidence_text(nodes), rules=rubric)
                    verdict, value, details = domain_verdict(reply)
                    details["rules"] = [r["id"] for r in rubric]
            except Exception as exc:              # noqa: BLE001 - one judge call must not end the pass
                out["errors"] += 1
                if trace["id"] not in out["failed_traces"]:
                    out["failed_traces"].append(trace["id"])
                log.warning("trace_lab.eval.judge_failed", level=level, trace=trace["id"], error=type(exc).__name__)
                continue
            estimated = usd is None
            usd = UNPRICED_USD if estimated else usd
            out["usd"] = round(out["usd"] + usd, 6)
            out["spent_usd"] = round(out["spent_usd"] + usd, 6)
            rows.append({"level": level, "verdict": verdict, "value": value, "comment": details.get("reason") or None,
                         "details": {**details, **flags, "usd": usd, "usd_estimated": estimated}})
        if rows:
            async with db.lab_engine().begin() as conn:
                for r in rows:
                    await scores.record_eval(conn, trace_id=trace["id"], session_id=trace["session_id"],
                                             level=r["level"], verdict=r["verdict"], value=r["value"],
                                             details=r["details"], evaluator=EVALUATORS[r["level"]], comment=r["comment"])
            for r in rows:
                out["not_evaluated" if r["verdict"] == "not_evaluated" else "scored"][r["level"]] += 1
            written += len(rows)
            out["traces"] += 1
        if out["capped"]:
            break
    out["remaining"] = sum(len(missing) for _t, missing in todo) + later - written
    out["left_for_later"] = later
    return out
