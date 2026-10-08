"""The orchestrator's planner: plan -> act -> verify, with the plan on the trace.

Until 2 Oct 2026 the orchestrator was a dispatcher: keyword tables and a one-call router chose an
engine, and the engine's own ReAct loop picked tools one at a time from the last result. Nothing
ever said "step 1 count by status at B-301 for September; step 2 keep the open ones; step 3 join
their vendors' compliance" - so a multi-part question was answered by whichever engine the router
named, with the rest of the question lost, and the trace showed calls but no intent.

This module is the planning step the user asked for, in the mode they chose:

  * a question that NEEDS decomposition (`is_multi_part`) gets a real plan: one model call that
    turns the question, the working set and the memories into a structured plan - steps, each an
    engine or a tool with its own ask, with dependencies; validated against the catalogue (an
    unknown engine or tool rejects the plan, never runs it);
  * the plan is executed by `execute()` - independent steps in parallel, dependent ones after
    their inputs, each step a `step` span with the engine's or tool's own spans beneath it;
  * `synthesise()` writes the answer from the step results only; `verify()` checks every figure
    in the answer against those results and names the ones it cannot trace;
  * a single-engine question keeps today's path and records a one-step plan (`one_step_plan`), so
    every run on Hoist Traces says why it went where it went.

Hard rules stay outside the plan: company and building scope, approvals, personal documents,
"never invent data" - the plan can only choose among what those already allow.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any, Awaitable, Callable

import structlog

log = structlog.get_logger(__name__)

ENGINES: dict[str, str] = {
    "compliance": "certificates, accreditations, statutory duties, expiry, what work a lapsed certificate blocks",
    "contract_performance": "vendor SLA, response and completion times, KPIs, invoices against contract rates, service credits",
    "energy_intelligence": "meters, consumption, anomalies, EUI and benchmarks, energy cost and waste per asset or building",
    "wo_engine": "work orders and maintenance: counts by status, overdue, blocked, decisions owed, cost saving, blockers by vendor",
}
MAX_STEPS = 6
#: Characters of an earlier step's output handed to a dependent step.
CONTEXT_CHARS = 3500

_MULTI = re.compile(
    r"\b(and then|then|after that|also|as well as|compare|versus|vs\.?|for each|both|respectively|"
    r"and (?:which|what|how|who|list|show|tell|give|also)|which of (?:those|these|them)|out of (?:those|these))\b", re.I)
_TWO_QUESTIONS = re.compile(r"\?.*\S.*\?", re.S)


def is_multi_part(question: str) -> bool:
    """A question that asks for more than one thing, or builds one answer on another."""
    q = " ".join((question or "").split())
    if len(q) < 25:
        return False
    if _TWO_QUESTIONS.search(q):
        return True
    if _MULTI.search(q):
        return True
    # Two clauses joined by "and", each with its own verb-ish interrogative.
    parts = re.split(r",?\s+and\s+", q, flags=re.I)
    asks = sum(1 for p in parts if re.search(r"\b(how many|which|what|who|when|where|list|show|count|cost|save|breach|blocked|overdue)\b", p, re.I))
    return len(parts) >= 2 and asks >= 2


def one_step_plan(target: str, reason: str | None, *, source: str = "router") -> dict[str, Any]:
    return {"mode": "single", "goal": None, "source": source,
            "steps": [{"id": "s1", "kind": "engine" if target in ENGINES else "loop", "target": target,
                       "ask": None, "depends_on": [], "why": reason or ""}],
            "answer_shape": None}


PLAN_PROMPT = """You plan how a facilities-management assistant will answer one question. You do not answer it.

Break the question into the smallest number of steps (1-{max_steps}) that together answer every part of it.
Each step runs ONE engine or ONE tool from the catalogue below, with a self-contained `ask` written for
that engine (name the building, period, vendor or work orders explicitly - the engine cannot see the
conversation). A step may depend on earlier steps; its `ask` then says what to take from them
("the open work orders from step s1"). Independent steps run in parallel.

Rules:
- Use only engines and tools from the catalogue, by exact name.
- When ONE tool answers a step, the step is that tool (kind "tool") with its arguments in `args`:
  counts, lists or details of records -> answer_from_records {{"question": "..."}};
  cost saving / where money goes -> get_cost_savings {{"building_name": "...", "period": "last_month|this_month|this_year|last_90_days"}};
  work orders blocked by a vendor's compliance or contract -> work_order_blockers {{"building_name": "...", "period": "..."}};
  repurchase / reorder / restock / replace / end of life / write off -> replacement_candidates {{"building_name": "...", "period": "..."}};
  decisions a manager owes / what needs my decision / which are statutory -> list_maintenance_decisions {{"building_id": "..."}}
  (statutory = source Compliance or a certificate trigger; get_maintenance_overview for the counts); what delaying a
  job costs -> get_cost_savings (energy waste per asset, open jobs with money at stake) - never ApprovalItem records;
  an asset's CONDITION GRADE (1-5; 4-5 = end of life) or replacement value -> answer_from_records on assets.condition_score /
  replacement_value, or replacement_candidates; list_asset_conditions is the ENERGY band (threat/watch/in_control), never the grade.
  what a document says -> search_documents {{"question": "...", "vendor"|"contract_ref"|"asset"|"building_name": "..."}}.
  An engine step costs 5-10x a tool step and rewrites the data as prose the final answer rewrites again:
  use an engine ONLY when the step needs judgement across several reads (a compliance analysis, an
  energy investigation, a vendor performance review).
- Keep the scope the question (and the working set) gives: never widen to other buildings or periods.
- Never plan an action that changes data (create, approve, send, delete) - this is a read-only plan.
- `answer_shape`: one line on how the final answer should be laid out.

Output ONLY JSON:
{{"goal": "...", "steps": [{{"id": "s1", "kind": "engine|tool", "target": "...", "ask": "...", "args": {{}}, "depends_on": [], "why": "..."}}],
  "answer_shape": "..."}}

CATALOGUE
Engines:
{engines}
Tools:
{tools}

{context}
QUESTION
{question}"""


# ── the tool catalogue the planner reads ────────────────────────────────────────────────
# One line per tool: its first docstring line, then what it is NOT for. The table catalogue
# has had a `not_for` since September and it is the field that stops a near-miss; the tool
# catalogue had none, and "list_asset_conditions" was taken for the condition grade (4 Oct 2026).
# Tools that change data are not in the catalogue at all: the plan is read-only by rule, and a
# rule the model must remember is weaker than a name it cannot see.

WRITE_TOOL_RE = re.compile(
    r"^(ingest_|upsert_|record_|update_|set_|run_|seed_|share_|process_|pull_|log_|manage_|propose_|score_|"
    r"approve_|confirm_|decide_|act_on_|draft_|extract_|flag_|generate_|compute_|deduce_|verify_vendor_invoice$|"
    r"verify_accreditation_now$|create_|close_|transition_|trigger_|send_|request_|customize_|respond_|prepare_|submit_)")

TOOL_NOTES: dict[str, str] = {
    "list_asset_conditions": "the 1-5 condition grade or replacement value (assets.condition_score via answer_from_records / replacement_candidates); work orders.",
    "get_asset_condition_summary": "a list of assets or their grades; counts of the energy bands only.",
    "list_unscored_assets": "assets that HAVE a grade; only those never scored (health_score null).",
    "get_asset_condition_rules": "any asset data; the two banding thresholds only.",
    "answer_from_records": "what a document says (search_documents); energy waste in pounds (get_cost_savings); which vendor blocks a job (work_order_blockers); decisions a manager owes (list_maintenance_decisions - ApprovalItem rows are not decisions).",
    "get_cost_savings": "a plain count or list of work orders (answer_from_records); certificates; the condition grade.",
    "work_order_blockers": "why a job is late for any reason but its vendor; closed work orders; parts.",
    "replacement_candidates": "a single asset's history (answer_from_records); energy waste; which work orders are open.",
    "search_documents": "counts, dates, statuses or amounts held in records (answer_from_records); the whole corpus - always name the vendor, asset or building.",
    "consumption_by_asset": "condition grade, work orders or cost saving; kWh ranking only.",
    "list_energy_anomalies": "the honest total across detectors (get_anomaly_rollup); condition grade.",
    "summarise_anomalies": "individual anomaly rows (list_energy_anomalies).",
    "get_anomaly_rollup": "per-anomaly detail; one honest total only.",
    "list_work_orders": "counts by status or any aggregate (answer_from_records); why a job is blocked (work_order_blockers).",
    "get_dashboard_stats": "a filtered count or a list; fixed dashboard tiles only.",
    "list_maintenance_decisions": "work orders themselves; the decisions a manager owes.",
    "get_inspection_intelligence": "one report's text (search_documents); the condition grade number.",
    "count_compliance_certificates": "vendor accreditations' detail or work orders; deterministic certificate counts only.",
    "list_building_certificates": "vendor accreditations (list_vendor_accreditations); work orders blocked by them (work_order_blockers).",
    "list_vendor_accreditations": "building certificates (list_building_certificates); scorecards (list_vendor_scorecards).",
    "get_compliance_coverage": "a list of certificates; coverage percentages only.",
    "list_vendor_scorecards": "certificates or accreditations; invoices (list_invoices).",
    "list_invoices": "contract SLA clauses (search_documents); work orders.",
    "get_asset_details": "many assets at once (answer_from_records); the energy band.",
    "search_assets": "condition, cost or work orders; name/code lookup only.",
    "get_ppm_contracts": "individual work orders; planned maintenance against plan per contract.",
    "list_compliance_approvals": "certificates themselves; queue items only.",
}


def catalogue_line(name: str, description: str | None, params: Any = None) -> str | None:
    """The planner's line for a tool, or None when the tool changes data and must not be planned.

    ``params`` is the tool's argument schema (a LangChain tool's ``.args``); its names close the
    line. Until 7 Oct 2026 the line was the description's first 160 characters and no more, so
    the planner chose list_asset_conditions for "assets at risk", wrote "Threat band" in the
    step's ask, and left args empty - it had never been told the tool takes ``band``. All 61
    assets came back, and the re-plan that followed swapped the right tool for a wider search."""
    if WRITE_TOOL_RE.search(name or "") or name == "compact_context":
        # compact_context manages the agent's own context (agents/context_budget.py); it is not
        # a step towards an answer.
        return None
    first = (description or "").strip().splitlines()[0][:160] if description else name
    note = TOOL_NOTES.get(name)
    names = [k for k in (params or {}) if k not in ("question", "runtime", "config", "state")]
    return (first + (f" Not for: {note}" if note else "")
            + (f" Args: {', '.join(names)}." if names else ""))


def _catalogue_text(tools: dict[str, str]) -> tuple[str, str]:
    eng = "\n".join(f"- {k}: {v}" for k, v in ENGINES.items())
    tl = "\n".join(f"- {k}: {v}" for k, v in sorted(tools.items()))
    return eng, tl


def validate(plan: Any, tools: dict[str, str]) -> tuple[dict[str, Any] | None, str | None]:
    """The plan as the executor will run it, or why it was refused."""
    if not isinstance(plan, dict) or not isinstance(plan.get("steps"), list):
        return None, "plan is not an object with steps"
    steps = plan["steps"]
    if not 1 <= len(steps) <= MAX_STEPS:
        return None, f"plan has {len(steps)} steps (1-{MAX_STEPS} allowed)"
    ids: set[str] = set()
    out: list[dict[str, Any]] = []
    for n, s in enumerate(steps, 1):
        if not isinstance(s, dict):
            return None, f"step {n} is not an object"
        sid = str(s.get("id") or f"s{n}")
        if sid in ids:
            return None, f"duplicate step id {sid}"
        kind, target = str(s.get("kind") or "").strip(), str(s.get("target") or "").strip()
        if kind == "engine" and target not in ENGINES:
            return None, f"step {sid}: unknown engine {target!r}"
        if kind == "tool" and target not in tools:
            return None, f"step {sid}: unknown tool {target!r}"
        if kind not in ("engine", "tool"):
            return None, f"step {sid}: kind must be engine or tool"
        deps = [str(d) for d in (s.get("depends_on") or []) if str(d) in ids]
        ask = " ".join(str(s.get("ask") or "").split())
        if not ask:
            return None, f"step {sid}: no ask"
        out.append({"id": sid, "kind": kind, "target": target, "ask": ask[:2000], "depends_on": deps,
                    "why": " ".join(str(s.get("why") or "").split())[:300],
                    "args": s.get("args") if isinstance(s.get("args"), dict) else None})
        ids.add(sid)
    return {"mode": "multi", "source": "planner", "goal": " ".join(str(plan.get("goal") or "").split())[:400],
            "steps": out, "answer_shape": " ".join(str(plan.get("answer_shape") or "").split())[:300]}, None


def _json_in(text: str) -> Any:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except ValueError:
        return None


async def make_plan(question: str, *, context: str, tools: dict[str, str],
                    llm: Callable[[str, str, str], Awaitable[str]]) -> tuple[dict[str, Any] | None, str | None]:
    """One model call -> a validated plan, or (None, reason). `llm(system, user, role)` returns text."""
    eng, tl = _catalogue_text(tools)
    prompt = PLAN_PROMPT.format(max_steps=MAX_STEPS, engines=eng, tools=tl, context=(context or "").strip(), question=question)
    try:
        raw = await llm("You are the planning step of the Plenum CAFM orchestrator.", prompt, "plan")
    except Exception as exc:  # noqa: BLE001 - a failed plan falls back to routing, never to silence
        return None, "planner call failed: " + str(exc).splitlines()[0][:200]
    plan, why = validate(_json_in(raw), tools)
    if plan is None:
        log.warning("planner.rejected", reason=why, raw=(raw or "")[:300])
    return plan, why


def describe(plan: dict[str, Any]) -> str:
    """The plan as the chat shows it before anything runs."""
    lines = []
    if plan.get("goal"):
        lines.append(plan["goal"])
    for s in plan.get("steps") or []:
        dep = f" (after {', '.join(s['depends_on'])})" if s.get("depends_on") else ""
        ask = f" — {s['ask']}" if s.get("ask") else ""
        lines.append(f"{s['id']}: {s['target']}{dep}{ask}")
    return "\n".join(lines)


def _batches(steps: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Steps grouped so each batch depends only on earlier batches."""
    done: set[str] = set()
    pending = list(steps)
    out: list[list[dict[str, Any]]] = []
    while pending:
        ready = [s for s in pending if all(d in done for d in s["depends_on"])]
        if not ready:
            ready = pending[:1]  # a cycle: run it anyway rather than hang
        out.append(ready)
        done |= {s["id"] for s in ready}
        pending = [s for s in pending if s not in ready]
    return out


def _as_text(v: Any, limit: int = CONTEXT_CHARS) -> str:
    s = v if isinstance(v, str) else json.dumps(v, default=str)
    return s if len(s) <= limit else s[:limit] + f" …[{len(s) - limit} more chars]"


_DIGEST_KEY = re.compile(r"(code|name|id$|status|grade|band|score|value|cost|total|count|date|expiry|ref|priority|vendor|building)", re.I)
DIGEST_ROWS = 60


def _digest(v: Any, limit: int = CONTEXT_CHARS) -> str:
    """An earlier step's result as the next step needs it: the rows, one line each, with their
    identifiers and figures - not the raw JSON. Raw JSON of 61 banded assets, pasted into the next
    step's question, was echoed back by the record engine and pushed the records it found out of
    the answer-writer's window (turn-1ab9f7050c4a40d3, 4 Oct 2026)."""
    if isinstance(v, str):
        return _as_text(v, limit)
    rows = None
    if isinstance(v, list):
        rows = v
    elif isinstance(v, dict):
        lists = [x for x in v.values() if isinstance(x, list) and x and all(isinstance(r, dict) for r in x)]
        rows = max(lists, key=len) if lists else None
    if not rows or not all(isinstance(r, dict) for r in rows):
        return _as_text(v, limit)
    lines = []
    for r in rows[:DIGEST_ROWS]:
        # Every scalar field, ids last and long prose clipped: a key filter here once hid `state`,
        # `trigger` and `detail` from the check, which then judged 29 real decisions as "rows
        # without decision details" and replanned into a worse source (4 Oct 2026).
        kv = []
        for k in r:
            val = r[k]
            if isinstance(val, (dict, list)) or val in (None, ""):
                continue
            sv = str(val)
            if k.endswith("_id") or k == "id":
                continue
            kv.append(f"{k}={sv[:90] + '…' if len(sv) > 90 else sv}")
        lines.append("- " + "; ".join(kv[:14]))
    head = ""
    if isinstance(v, dict):
        scalars = {k: v[k] for k in v if not isinstance(v[k], (dict, list)) and k not in ("ok", "question", "answer_rules")}
        head = json.dumps(scalars, default=str) + "\n"
    more = f"\n…{len(rows) - DIGEST_ROWS} more rows" if len(rows) > DIGEST_ROWS else ""
    return _as_text(head + f"{len(rows)} rows:\n" + "\n".join(lines) + more, limit)


# ── step gates ───────────────────────────────────────────────────────────────────────────
# What can be checked on a step's result without a model, before the next step builds on it.
# Measured 4 Oct 2026 (turn-1ab9f7050c4a40d3): "assets with condition grade 4" ran a tool that
# returned 61 of 61 assets (its filter is the energy band, not the grade) and the plan carried on;
# the next step's rows held replacement_value for every asset and the answer said "unavailable"
# because the writer read a truncated result. Each of those is a fact about the result, visible
# to code. A flagged step costs one replanning call; a clean run costs nothing.

#: Words in an ask that say the step was meant to narrow, not list everything.
_QUALIFIER = re.compile(r"\b(grade|graded|band|status|overdue|expired|expiring|lapsed|blocked|held|open|closed|"
                        r"below|above|over|under|only|with|where|at least|more than|less than|greater|fewer|"
                        r"between|since|before|after|last|this|next)\b", re.I)
#: Phrases an ask uses for a field, and the row keys that would carry it.
FIELD_KEYS: dict[str, tuple[str, ...]] = {
    "replacement": ("replacement_value", "replacement_cost", "replace_cost_gbp"),
    "replace": ("replacement_value", "replacement_cost", "replace_cost_gbp"),
    "grade": ("condition_score", "condition_grade", "grade"),
    "condition score": ("condition_score",),
    "expir": ("expiry", "expiry_date", "expires", "warranty_expiry", "contract_end"),
    "replacement value": ("replacement_value",),
    "reorder": ("reorder_level", "stock_quantity"),
    "stock": ("stock_quantity", "stock", "reorder_level"),
    "priority": ("priority",),
    "statutory": ("statutory", "statutory_certificate", "is_statutory"),
    "decision": ("state", "trigger", "awaiting", "waiting_on", "decision"),
    "vendor": ("vendor", "vendor_name", "vendors"),
    "sla": ("sla_due_at", "sla_hours", "sla"),
    "invoice": ("invoice_ref", "invoice_lines", "delta_gbp"),
}


def _rows_of(output: Any) -> list[dict[str, Any]]:
    """The rows a result carries: a list of dicts at the top or the largest one inside a dict."""
    if isinstance(output, list) and output and all(isinstance(r, dict) for r in output):
        return output
    if isinstance(output, dict):
        lists = [x for x in output.values() if isinstance(x, list) and x and all(isinstance(r, dict) for r in x)]
        if lists:
            return max(lists, key=len)
        rec = output.get("records")
        if isinstance(rec, str):
            try:
                v = json.loads(rec)
                if isinstance(v, list) and v and all(isinstance(r, dict) for r in v):
                    return v
            except ValueError:
                pass
    return []


def fields_asked(text: str) -> list[str]:
    """The field phrases a text mentions; a phrase inside a longer matched phrase is dropped
    ("replacement value" covers "replacement")."""
    t = (text or "").lower()
    hits = [k for k in FIELD_KEYS if k in t]
    return [k for k in hits if not any(o != k and k in o for o in hits)]


def _has_key(rows: list[dict[str, Any]], keys: tuple[str, ...]) -> bool:
    return any(any(k in r and r[k] not in (None, "") for k in keys) for r in rows)


def inspect_step(step: dict[str, Any], result: dict[str, Any]) -> list[dict[str, str]]:
    """Deterministic findings about one step's result. Each is {"kind", "detail"}."""
    out: list[dict[str, str]] = []
    if not result.get("ok"):
        return out
    o = result.get("output")
    ask = str(step.get("ask") or "")
    text = o if isinstance(o, str) else json.dumps(o, default=str)
    if (isinstance(o, dict) and (o.get("truncated") is True or "_truncated" in o)) or "more chars]" in text or "...(truncated)" in text:
        out.append({"kind": "truncated", "detail": "the result was cut; what follows reads it as if it were complete"})
    rows = _rows_of(o)
    if isinstance(o, dict) and rows and _QUALIFIER.search(ask):
        count, total = o.get("count"), o.get("total")
        if isinstance(count, int) and isinstance(total, int) and total > 0 and count == total and count > 1:
            out.append({"kind": "filter_did_nothing",
                        "detail": f"the ask narrows ('{_QUALIFIER.search(ask).group(0)}...') but the tool returned every row: "
                                  f"{count} of {total}. Keep this tool and pass the narrowing as one of its Args in `args` "
                                  "(the ask text is not a filter); do not swap it for a wider search"})
    if rows:
        for phrase in fields_asked(ask):
            if not _has_key(rows, FIELD_KEYS[phrase]):
                out.append({"kind": "field_missing", "detail": f"the ask names '{phrase}' but no row carries " + "/".join(FIELD_KEYS[phrase][:3])})
    return out


def gate_findings(plan: dict[str, Any], results: dict[str, dict[str, Any]]) -> dict[str, list[dict[str, str]]]:
    """{step_id: findings} for every step that has any."""
    out: dict[str, list[dict[str, str]]] = {}
    for s in plan.get("steps") or []:
        r = results.get(s["id"])
        if r is not None:
            f = inspect_step(s, r)
            if f:
                out[s["id"]] = f
    return out


def replan_context(question: str, plan: dict[str, Any], findings: dict[str, list[dict[str, str]]],
                   results: dict[str, dict[str, Any]]) -> str:
    """What the planner is told the second time: which step failed its gate, how, and the digest of
    what it returned - so the new plan avoids that source for that purpose."""
    lines = ["GATE FINDINGS on the first plan (hard constraints for this plan):"]
    for sid, fs in findings.items():
        st = next((s for s in plan["steps"] if s["id"] == sid), {})
        lines.append(f"- step {sid} ran {st.get('target')} for: {str(st.get('ask') or '')[:200]}")
        for f in fs:
            lines.append(f"    {f['kind']}: {f['detail']}")
        lines.append("    it returned: " + _digest(results.get(sid, {}).get("output"), 600).replace("\n", " ")[:600])
    lines.append("Do not use that tool for that purpose again. A field a row lacks lives in the records: "
                 "answer_from_records reads any column of a table (assets.condition_score, assets.replacement_value, ...). "
                 "A result that was cut wants fewer columns or an aggregate.")
    return "\n".join(lines) + "\n"


CHECK_PROMPT = """A step of a plan has run. Decide whether its RESULT answers its ASK - nothing more.

ASK
{ask}

RESULT (digest)
{result}

Answer ONLY JSON: {{"answers_ask": true|false, "why": "one sentence: what the result holds or lacks"}}
Say false ONLY when the result is about different things than the ask names (every asset when the ask said
grade 4; approvals when the ask said work orders), is empty for a reason the ask did not expect, or was cut.
A result of the right kind that lacks one optional field the ask mentioned is TRUE - a later step can add the
field; replacing the source would lose the rows."""


def check_prompt(step: dict[str, Any], output: Any) -> str:
    return CHECK_PROMPT.format(ask=str(step.get("ask") or "")[:800], result=_digest(output, 2500))


def parse_check(raw: str) -> tuple[bool, str]:
    """(answers_ask, why). Anything unparseable counts as a pass: the check may only stop a plan
    when it can say why."""
    try:
        v = _json_in(raw)
        if isinstance(v, dict) and "answers_ask" in v:
            return bool(v["answers_ask"]), str(v.get("why") or "")[:300]
    except Exception:  # noqa: BLE001
        pass
    return True, ""


def _has_dependents(plan: dict[str, Any], sid: str) -> bool:
    return any(sid in (s.get("depends_on") or []) for s in plan.get("steps") or [])


async def execute(plan: dict[str, Any], *, run_engine: Callable[..., Awaitable[tuple[str, list]]],
                  run_tool: Callable[[str, dict], Awaitable[Any]], scope_hint: str = "",
                  given: str = "",
                  on_event: Callable[[dict], Awaitable[None]] | None = None,
                  question: str = "",
                  replan: Callable[[str, dict[str, Any], dict[str, list[dict[str, str]]], dict[str, dict[str, Any]]], Awaitable[dict[str, Any] | None]] | None = None,
                  check: Callable[[dict[str, Any], dict[str, Any]], Awaitable[tuple[bool, str]]] | None = None,
                  ) -> dict[str, dict[str, Any]]:
    """Run the plan. Returns {step_id: {"output", "tool_calls", "ok", "error", "ms"}}.

    After each batch the steps are gated (inspect_step). When a finding lands on a step that later
    steps depend on, or on the last batch, `replan` (when given) is asked once for a new plan built
    with the findings as constraints; the new plan then runs in place of what was left. The result
    carries the plan actually run under the key "__plan__" when it changed."""
    from . import trace

    results: dict[str, dict[str, Any]] = {}

    async def one(step: dict[str, Any]) -> None:
        t0 = time.perf_counter()
        ask = step["ask"]
        if step["depends_on"]:
            ctx = "\n\n".join(f"Result of step {d}:\n{_digest(results.get(d, {}).get('output'))}" for d in step["depends_on"])
            ask = f"{ask}\n\nUse these results from earlier steps as given facts:\n{ctx}"
        if scope_hint:
            ask = f"{ask}\n\nScope (hard filter): {scope_hint}"
        if given:
            # A follow-up ("for each of these assets"): the answer it follows is the list the
            # step works on. Without it every step searched afresh and said the list was gone.
            ask = (f"{ask}\n\nThe previous answer in this conversation - the items 'these' / 'each one' "
                   f"refer to; use it as given facts:\n{given}")
        run_id = trace.on_step_open(step["id"], f"{step['id']}: {step['target']}", {"kind": step["kind"], "target": step["target"],
                                                                                   "ask": ask, "depends_on": step["depends_on"], "why": step["why"]})
        if on_event:
            await on_event({"type": "agent_switch", "from_domain": "orchestrator", "to_domain": step["target"]})
        try:
            if step["kind"] == "engine":
                answer, tool_calls = await run_engine(step["target"], ask, on_event)
                results[step["id"]] = {"output": answer, "tool_calls": tool_calls, "ok": True, "error": None}
            else:
                args = dict(step.get("args") or {}) or {"question": ask}
                # Earlier steps' results and the hard filter travel in the question when the tool takes one.
                if "question" in args and (step["depends_on"] or scope_hint or given):
                    args["question"] = ask
                if on_event:
                    await on_event({"type": "tool_started", "tool": step["target"], "domain": "orchestrator", "input": args})
                out = await run_tool(step["target"], args)
                if on_event:
                    await on_event({"type": "tool_completed", "tool": step["target"], "domain": "orchestrator", "output": out})
                results[step["id"]] = {"output": out, "tool_calls": [{"tool": step["target"], "input": args, "output": out}],
                                       "ok": True, "error": None}
        except Exception as exc:  # noqa: BLE001 - one failed step is a fact for the synthesis, not the end of the turn
            err = str(exc).splitlines()[0][:300]
            results[step["id"]] = {"output": None, "tool_calls": [], "ok": False, "error": err}
        results[step["id"]]["ms"] = int((time.perf_counter() - t0) * 1000)
        r = results[step["id"]]
        trace.on_step_close(run_id, {"output": r["output"], "tool_calls": len(r["tool_calls"]), "ms": r["ms"]}, ok=r["ok"], error=r["error"])

    replanned = False
    batches = _batches(plan["steps"])
    i = 0
    while i < len(batches):
        batch = batches[i]
        await asyncio.gather(*(one(s) for s in batch))
        i += 1
        if replanned or replan is None:
            continue
        found = {s["id"]: inspect_step(s, results[s["id"]]) for s in batch}
        found = {k: v for k, v in found.items() if v}
        # Observe before the next step builds on this one: a short model check, only for steps
        # that have dependents and passed the deterministic gates (a flagged step replans anyway).
        if check is not None:
            for s in batch:
                if s["id"] in found or not _has_dependents(plan, s["id"]) or not results[s["id"]].get("ok"):
                    continue
                rid = trace.on_step_open("check", f"check {s['id']}", {"ask": str(s.get("ask") or "")[:300]})
                try:
                    ok, why = await check(s, results[s["id"]])
                except Exception as exc:  # noqa: BLE001 - a failed check never stops the plan
                    ok, why = True, "check failed: " + str(exc)[:120]
                trace.on_step_close(rid, {"answers_ask": ok, "why": why}, ok=ok, error=None if ok else why[:300])
                if not ok:
                    found[s["id"]] = [{"kind": "check_failed", "detail": why or "the result does not answer the ask"}]
        if not found:
            continue
        rid = trace.on_step_open("gate", "step gates", {"findings": found})
        new_plan = None
        try:
            new_plan = await replan(question, plan, found, results)
        except Exception as exc:  # noqa: BLE001 - a failed replan keeps the first plan's results
            trace.on_step_close(rid, {"replanned": False, "error": str(exc)[:200]}, ok=False, error=str(exc)[:200])
            continue
        if not new_plan:
            trace.on_step_close(rid, {"replanned": False}, ok=False, error="; ".join(f"{k}: {f['kind']}" for k, fs in found.items() for f in fs)[:300])
            continue
        trace.on_step_close(rid, {"replanned": True, "steps": [s["target"] for s in new_plan["steps"]]}, ok=True, error=None)
        trace.on_plan(new_plan, source="replan")
        if on_event:
            await on_event({"type": "reasoning", "label": "Replan", "domain": "orchestrator",
                            "text": "A step failed its gate (" + "; ".join(f"{k}: {f['kind']}" for k, fs in found.items() for f in fs) + "). New plan:\n" + describe(new_plan)})
        # The new plan replaces what was left: its steps get fresh ids so the old results stay as evidence.
        replanned = True
        plan = new_plan
        for s in plan["steps"]:
            s["id"] = "r" + s["id"]
            s["depends_on"] = ["r" + d for d in s["depends_on"]]
        results["__plan__"] = {"output": plan, "tool_calls": [], "ok": True, "error": None, "ms": 0}
        batches = _batches(plan["steps"])
        i = 0
    return results


SYNTH_PROMPT = """You write the final answer to the user's question from the step results below, and from nothing else.

Rules:
- Every figure, name, date and code you state must appear in a step result. If a part of the question has no
  result (a step failed or returned nothing), say so for that part; never fill it from general knowledge.
- Follow the answer shape. Lead with the answer, then the detail. Markdown tables where they help.
- Keep the scope the steps used; do not generalise beyond the building, period or records they covered.
- Where a step returned `answer_rules`, follow them for that part.
- Weigh what the steps found as the LENS below says: risk to life, then statutory exposure, then service
  to occupants, with the money each finding costs or saves carried on every one, and the total at stake.
  A multi-part question is a judgement question: close with "What to do".

LENS
{lens}

QUESTION
{question}

PLAN
{plan}

ANSWER SHAPE
{shape}

STEP RESULTS
{results}"""


def _lens_text() -> str:
    from .skills import fm_lens
    return fm_lens() or "(not loaded)"


async def synthesise(question: str, plan: dict[str, Any], results: dict[str, dict[str, Any]],
                     llm: Callable[[str, str, str], Awaitable[str]]) -> str:
    body = "\n\n".join(
        f"[{sid}] {('OK' if r['ok'] else 'FAILED: ' + str(r['error']))}\n{_as_text(r['output'], 12000) if r['ok'] else ''}"
        for sid, r in results.items())
    prompt = SYNTH_PROMPT.format(question=question, plan=describe(plan), shape=plan.get("answer_shape") or "concise, tables where useful",
                                 results=body, lens=_lens_text())
    return await llm("You are the answer-writing step of the Plenum CAFM orchestrator.", prompt, "synthesise")


_FIGURE = re.compile(r"(?<![\w.])(?:£|\$|AED\s?|€)?\d[\d,]*(?:\.\d+)?%?(?![\w.])")
_UNAVAILABLE = re.compile(r"\b(unavailable|not (?:recorded|available|returned|held|present)|no (?:\w+ ){0,3}(?:records?|values?|figures?|costs?) (?:were|was|are|is) (?:returned|recorded|available)|cannot be (?:calculated|determined))\b", re.I)


def verify_claims(answer: str, question: str, results: dict[str, dict[str, Any]]) -> list[str]:
    """Claims in the answer that the step results contradict. Deterministic, two checks:

    - the answer calls a field unavailable while a step's rows carry it with values;
    - the answer quotes, as the answer to a narrowing question, a count that equals a step's
      unfiltered total (the filter did nothing).
    Each finding is a sentence the writer can act on."""
    out: list[str] = []
    a = answer or ""
    steps = {k: v for k, v in results.items() if k != "__plan__" and isinstance(v, dict) and v.get("ok")}
    # "unavailable" is judged per sentence: the fields that sentence names are the ones it calls unavailable.
    for sentence in re.split(r"(?<=[.!?])\s+|\n+|\|", a):
        m_un = _UNAVAILABLE.search(sentence)
        if not m_un:
            continue
        # The field a sentence calls unavailable is named just before the phrase ("replacement cost
        # unavailable"), not anywhere in a table row that also says "Priority: P2".
        window = sentence[max(0, m_un.start() - 80):m_un.end() + 20]
        for phrase in fields_asked(window):
            for sid, r in steps.items():
                rows = _rows_of(r.get("output"))
                if rows and _has_key(rows, FIELD_KEYS[phrase]):
                    n = sum(1 for row in rows if any(row.get(k) not in (None, "") for k in FIELD_KEYS[phrase]))
                    out.append(f"The answer calls '{phrase}' unavailable, but step {sid} returned {n} rows with "
                               + "/".join(k for k in FIELD_KEYS[phrase] if any(k in row for row in rows)) + " populated.")
                    break
    if _QUALIFIER.search(question or ""):
        for sid, r in steps.items():
            o = r.get("output")
            if isinstance(o, dict) and isinstance(o.get("count"), int) and o.get("count") == o.get("total") and o["count"] > 1:
                if re.search(r"(?<!\d)" + str(o["count"]) + r"(?!\d)", a):
                    out.append(f"The answer quotes {o['count']} as the narrowed count, but step {sid} returned every row "
                               f"({o['count']} of {o['total']}): its filter did nothing.")
    return list(dict.fromkeys(out))


REWRITE_NOTE = """

CONTRADICTIONS FOUND IN A FIRST DRAFT (fix every one; state the figures the results actually hold):
"""


def verify(answer: str, results: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Every figure in the answer must be traceable to a step result. Returns what was checked and
    what could not be traced (digits compared without separators or symbols)."""
    haystack = re.sub(r"[,£$€\s]", "", " ".join(_as_text(r.get("output"), 200000) for r in results.values()))
    checked, unmatched = [], []
    for m in _FIGURE.finditer(answer or ""):
        raw = m.group(0)
        digits = re.sub(r"[,£$€%\sAED]", "", raw)
        if len(digits.replace(".", "")) < 2 or re.fullmatch(r"(19|20)\d\d", digits):
            continue  # single digits and years are prose, not findings
        checked.append(raw)
        if digits not in haystack and digits.rstrip("0").rstrip(".") not in haystack:
            unmatched.append(raw)
    uniq = list(dict.fromkeys(unmatched))
    return {"figures_checked": len(checked), "unmatched": uniq, "ok": not uniq}


# ── the answer as cards ──────────────────────────────────────────────────────────────────
# A planned answer used to reach the chat as markdown only. The engines' answers arrive as a
# dashboard (KPIs, owner groups, priority actions, the pipeline) because they attach two tool
# outputs the chat renders - `compliance_response` and `compliance_pipeline`. These are built
# here for a planned turn, deterministically from the step results: a number on a card is a
# number a step returned, never one the writer typed. Asked for on 4 Oct 2026 ("show me as
# dashboard cards, listed dynamically").

_MONEY = "£{:,.0f}"


def _money(v: Any) -> str:
    try:
        return _MONEY.format(float(v))
    except (TypeError, ValueError):
        return "n/a"


def _lead(answer: str) -> str:
    """The answer's first paragraph as plain text, for the Overall card."""
    for para in re.split(r"\n\s*\n", answer or ""):
        t = re.sub(r"[*_`#>|]", "", para).strip()
        if t and not t.startswith("-") and len(t) > 20:
            return t[:600]
    return ""


def _kpi(count: Any, label: str, sublabel: str = "", severity: str = "info", unit: str = "other") -> dict[str, Any]:
    try:
        n = int(round(float(count)))
    except (TypeError, ValueError):
        n = 0
    return {"count": n, "label": label, "sublabel": sublabel, "severity": severity, "unit": unit, "cert_ids": []}


def _group(owner: str, headline: str, points: list[str], severity: str = "info", scope: str = "Mixed", qid: str = "") -> dict[str, Any]:
    return {"owner": owner, "scope": scope, "severity": severity, "headline": headline,
            "points": [str(p)[:220] for p in points[:8]], "cert_ids": [], "sub_question_id": qid}


def _action(title: str, severity: str = "warning", tags: list[str] | None = None, scope: str = "Mixed", qid: str = "") -> dict[str, Any]:
    return {"title": title[:200], "scope": scope, "severity": severity, "tags": [str(t)[:40] for t in (tags or [])][:4],
            "cert_ids": [], "sub_question_id": qid}


_STATE_SEV = {"Blocked": "critical", "Deviation": "warning", "Awaiting approval": "warning", "To raise": "info"}


def _cards_decisions(o: dict[str, Any], qid: str, kpis: list, groups: list, actions: list) -> None:
    rows = [r for r in (o.get("decisions") or []) if isinstance(r, dict)]
    total = o.get("total") if isinstance(o.get("total"), int) else len(rows)
    by_state = o.get("by_state") if isinstance(o.get("by_state"), dict) else {}
    statutory = [r for r in rows if r.get("statutory")]
    kpis.append(_kpi(total, "Decisions owed", "blocked, awaiting approval, deviating or to raise", "warning" if total else "ok"))
    if by_state.get("Blocked"):
        kpis.append(_kpi(by_state["Blocked"], "Blocked", "cannot proceed today", "critical"))
    kpis.append(_kpi(len(statutory), "Statutory", "a certificate lapsed or due forces it", "critical" if statutory else "ok"))
    if by_state.get("Awaiting approval"):
        kpis.append(_kpi(by_state["Awaiting approval"], "Awaiting approval", "yours to approve", "warning"))
    for state in ("Blocked", "Awaiting approval", "Deviation", "To raise"):
        rs = [r for r in rows if r.get("state") == state]
        if not rs:
            continue
        pts = []
        for r in rs:
            cert = r.get("statutory_certificate") or {}
            tag = (" · statutory: " + str(cert.get("certificate_type") or "certificate")
                   + (" lapsed" if cert.get("lapsed") else (" expires " + str(cert.get("expires"))[:10] if cert.get("expires") else ""))) if r.get("statutory") else ""
            who = r.get("vendor") or r.get("asset") or ""
            pts.append(f"{r.get('work_order') or 'to raise'} — {r.get('asset') or ''}: {str(r.get('detail') or r.get('trigger') or '')[:110]}"
                       + (f" ({who})" if r.get("vendor") else "") + tag)
        groups.append(_group(state, f"{len(rs)} decision{'s' if len(rs) != 1 else ''}", pts, _STATE_SEV.get(state, "info"), qid=qid))
    for r in sorted(statutory, key=lambda r: str((r.get("statutory_certificate") or {}).get("expires") or "~"))[:4]:
        cert = r.get("statutory_certificate") or {}
        actions.append(_action(f"{'Unblock' if r.get('state') == 'Blocked' else 'Approve'} {r.get('work_order') or 'and raise'} — "
                               f"{r.get('asset') or ''}: {cert.get('certificate_type') or 'certificate'} "
                               f"{'has lapsed' if cert.get('lapsed') else 'expires ' + str(cert.get('expires'))[:10]}",
                               "critical", ["statutory", str(r.get("state") or "")], qid=qid))


def _cards_savings(o: dict[str, Any], qid: str, kpis: list, groups: list, actions: list) -> None:
    jobs = [j for j in (o.get("open_jobs") or []) if isinstance(j, dict)]
    waste = sum(float(j.get("energy_waste_gbp_per_year") or 0) for j in jobs)
    if jobs:
        kpis.append(_kpi(waste, "£/yr at stake", f"energy waste behind {len(jobs)} open job{'s' if len(jobs) != 1 else ''}", "warning" if waste else "info"))
        groups.append(_group("Open jobs with money at stake", f"{_money(waste)} a year of identified waste",
                             [f"{j.get('wo_code')} — {str(j.get('what') or '')[:80]}: {_money(j.get('cost_to_act'))} to act"
                              + (f", up to {_money(j.get('energy_waste_gbp_per_year'))}/yr" if j.get("energy_waste_gbp_per_year") else "")
                              for j in sorted(jobs, key=lambda j: -(float(j.get("energy_waste_gbp_per_year") or 0)))], "warning", qid=qid))
        for j in sorted(jobs, key=lambda j: -(float(j.get("energy_waste_gbp_per_year") or 0)))[:2]:
            if j.get("energy_waste_gbp_per_year"):
                actions.append(_action(f"Approve {j.get('wo_code')} ({str(j.get('what') or '')[:60]}): {_money(j.get('cost_to_act'))} against "
                                       f"{_money(j.get('energy_waste_gbp_per_year'))}/yr", "warning", ["cost saving"], qid=qid))
    rf = [r for r in (o.get("repeat_failures") or []) if isinstance(r, dict)]
    rof = [r for r in rf if (r.get("cost_to_replacement_ratio") or 0) >= 0.5]
    if rf:
        kpis.append(_kpi(len(rof), "Replace or fix", f"of {len(rf)} repeat-failure assets", "critical" if rof else "info"))


def _cards_replacement(o: dict[str, Any], qid: str, kpis: list, groups: list, actions: list) -> None:
    sm = o.get("summary") if isinstance(o.get("summary"), dict) else {}
    kpis.append(_kpi(sm.get("parts_to_reorder", 0), "Parts to reorder", f"{sm.get('stock_outs', 0)} out of stock", "warning" if sm.get("parts_to_reorder") else "ok"))
    kpis.append(_kpi(sm.get("end_of_life", 0), "End of life", f"condition grade ≥ {o.get('eol_grade', 4)}", "critical" if sm.get("end_of_life") else "ok"))
    kpis.append(_kpi(sm.get("replace_or_fix", 0), "Replace or fix", "reactive cost vs replacement value", "warning" if sm.get("replace_or_fix") else "ok"))
    kpis.append(_kpi(sm.get("purchase_orders_open", 0), "Purchase orders open", "on file", "info"))
    parts = [p for p in (o.get("parts_to_reorder") or []) if isinstance(p, dict)]
    if parts:
        groups.append(_group("Parts below reorder level", f"{len(parts)} part{'s' if len(parts) != 1 else ''}, {_money(sum(float(p.get('cost_to_restock') or 0) for p in parts))} to restock",
                             [f"{p.get('part')} — stock {p.get('stock')} / reorder at {p.get('reorder_level')}, {_money(p.get('cost_to_restock'))} ({p.get('supplier')})" for p in parts], "warning", qid=qid))
    eol = [a for a in (o.get("end_of_life") or []) if isinstance(a, dict)]
    if eol:
        groups.append(_group("Assets at end of life", f"{len(eol)} at grade ≥ {o.get('eol_grade', 4)}, {_money(sum(float(a.get('replacement_value') or 0) for a in eol))} to replace",
                             [f"{a.get('asset')} — grade {a.get('condition_grade')}, {_money(a.get('replacement_value'))}"
                              + (f", {a.get('design_life_used_pct')}% of design life" if a.get("design_life_used_pct") is not None else "") for a in eol], "critical", qid=qid))
        for a in eol[:2]:
            actions.append(_action(f"Decide remediate-or-replace for {a.get('asset')} ({_money(a.get('replacement_value'))})", "critical", ["end of life"], qid=qid))


def _cards_blockers(o: dict[str, Any], qid: str, kpis: list, groups: list, actions: list) -> None:
    sm = o.get("summary") if isinstance(o.get("summary"), dict) else {}
    kpis.append(_kpi(sm.get("blocked", 0), "Blocked by vendor", "cannot proceed today", "critical" if sm.get("blocked") else "ok"))
    kpis.append(_kpi(sm.get("at_risk", 0), "At risk", "vendor risk to call out", "warning" if sm.get("at_risk") else "ok"))
    for key, sev in (("blocked", "critical"), ("at_risk", "warning")):
        items = [b for b in (o.get(key) or []) if isinstance(b, dict)]
        if items:
            groups.append(_group("Blocked" if key == "blocked" else "At risk", f"{len(items)} work order{'s' if len(items) != 1 else ''}",
                                 [f"{b.get('wo_code')} — {str(b.get('title') or '')[:60]} ({b.get('vendor') or 'no vendor'}): "
                                  + "; ".join(str(x.get("detail") or "") for x in (b.get("blockers") or [])[:2]) for b in items], sev, scope="Vendor", qid=qid))
            if key == "blocked":
                for b in items[:3]:
                    actions.append(_action(f"Reassign or renew for {b.get('wo_code')}: " + "; ".join(str(x.get("detail") or "") for x in (b.get("blockers") or [])[:1]),
                                           "critical", ["vendor"], scope="Vendor", qid=qid))


def _cards_records(o: dict[str, Any], ask: str, qid: str, kpis: list, groups: list) -> None:
    rows = _rows_of(o)
    if isinstance(o.get("records"), str):
        return
    if o.get("header") and rows and all(("count" in r or "n" in r) for r in rows[:3]):
        for r in rows[:6]:
            label = " · ".join(str(v) for k, v in r.items() if k not in ("count", "n") and v not in (None, ""))[:60]
            kpis.append(_kpi(r.get("count", r.get("n")), label or "records", str(ask)[:60], "info"))
        return
    total = o.get("total") if isinstance(o.get("total"), int) else len(rows)
    if rows:
        kpis.append(_kpi(total, "Records", str(ask)[:70], "info"))


def cards_from_results(question: str, plan: dict[str, Any], results: dict[str, dict[str, Any]], answer: str,
                       cost: dict[str, Any] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """(compliance_response, compliance_pipeline) for a planned turn - the chat's card contract.
    Cards come only from step results the builders recognise; a turn with none gets an empty
    response (the chat then shows the markdown) but always a pipeline."""
    kpis: list[dict[str, Any]] = []
    groups: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []
    steps_out: list[dict[str, Any]] = [{"stage": "plan", "label": f"{len(plan.get('steps') or [])} step{'s' if len(plan.get('steps') or []) != 1 else ''} planned",
                                        "detail": plan.get("goal") or describe(plan)[:200]}]
    # A card tool's figures carry no scope label, so a tool that returned DIFFERENT outputs in one
    # plan (one per building) is not carded from the engine steps - the cards would show one
    # building's figures as if they were the total. Identical outputs are carded once.
    seen_outputs: set[str] = set()
    variants: dict[str, set[str]] = {}
    for st in plan.get("steps") or []:
        r = results.get(st["id"]) or {}
        if not r.get("ok"):
            continue
        if st.get("kind") != "engine" and isinstance(r.get("output"), dict):
            variants.setdefault(str(st.get("target") or ""), set()).add(json.dumps(r["output"], sort_keys=True, default=str))
        for c in (r.get("tool_calls") or []):
            co = _as_dict(c.get("output")) if isinstance(c, dict) else None
            if co is not None:
                variants.setdefault(str(c.get("tool") or ""), set()).add(json.dumps(co, sort_keys=True, default=str))
    for st in plan.get("steps") or []:
        r = results.get(st["id"]) or {}
        o = r.get("output")
        qid = st["id"]
        if r.get("ok") and isinstance(o, dict):
            seen_outputs.add(json.dumps(o, sort_keys=True, default=str))
            t = st.get("target")
            if t == "list_maintenance_decisions":
                _cards_decisions(o, qid, kpis, groups, actions)
            elif t == "get_cost_savings":
                _cards_savings(o, qid, kpis, groups, actions)
            elif t == "replacement_candidates":
                _cards_replacement(o, qid, kpis, groups, actions)
            elif t == "work_order_blockers":
                _cards_blockers(o, qid, kpis, groups, actions)
            elif t == "answer_from_records":
                _cards_records(o, st.get("ask") or "", qid, kpis, groups)
            elif t == "get_asset_condition_summary":
                _cards_condition(o, qid, kpis, groups, actions)
        # An engine step answers in prose, but the data tools it called are the same ones the
        # builders know; card those (once each) unless the engine produced its own dashboard.
        nested = [c for c in (r.get("tool_calls") or []) if isinstance(c, dict)] if r.get("ok") else []
        if st.get("kind") == "engine" and not any(c.get("tool") == "compliance_response" for c in nested):
            for c in nested:
                name = str(c.get("tool") or "")
                co = _as_dict(c.get("output"))
                if name not in _CARD_BUILDERS or co is None or co.get("ok") is False or len(variants.get(name) or ()) > 1:
                    continue
                key = json.dumps(co, sort_keys=True, default=str)
                if key not in seen_outputs:
                    seen_outputs.add(key)
                    ask = (c.get("input") or {}).get("question") if isinstance(c.get("input"), dict) else ""
                    _CARD_BUILDERS[name](co, qid, kpis, groups, actions, str(ask or st.get("ask") or question or ""))
        n = len(_rows_of(o)) if r.get("ok") else 0
        steps_out.append({"stage": "data", "label": f"{st['id']}: {st.get('target')}",
                          "detail": (f"{n} rows · " if n else "") + f"{r.get('ms', 0)} ms" + ("" if r.get("ok") else f" · failed: {r.get('error')}")})
    if results.get("__plan__"):
        steps_out.append({"stage": "revise", "label": "replanned after a step gate", "detail": describe(results["__plan__"]["output"])[:200]})
    steps_out.append({"stage": "validate", "label": "claims and figures checked against the step results", "detail": ""})
    # Actions worst first, capped; KPIs capped so the strip stays a strip.
    order = {"critical": 0, "warning": 1, "info": 2, "ok": 3}
    actions.sort(key=lambda a: order.get(a["severity"], 9))
    response = {"narrative": _lead(answer) if (kpis or groups) else "", "sections": [], "groups": groups[:6], "kpis": kpis[:8],
                "actions": actions[:6], "insights": [], "certificates": [], "pending": [], "offers": [], "validation": None}
    pipeline = {"engine": "orchestrator", "steps": steps_out, "cost": cost}
    return response, pipeline


def _fmt_num(v: Any) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "n/a"
    return str(int(f)) if f == int(f) else f"{f:.1f}"


def _cards_condition(o: dict[str, Any], qid: str, kpis: list, groups: list, actions: list) -> None:
    """get_asset_condition_summary: the Assets page's Threat / Watch / In control tiles, the
    sections running over their own reference, and the buildings behind them (5 Oct 2026)."""
    sm = o.get("summary") if isinstance(o.get("summary"), dict) else {}
    kpis.append(_kpi(sm.get("threat", 0), "Threat", "over reference and a persistent anomaly", "critical" if sm.get("threat") else "ok", unit="assets"))
    kpis.append(_kpi(sm.get("watch", 0), "Watch", f"{sm.get('watch_shares_section', 0)} shared section · {sm.get('watch_persistent_anomaly', 0)} persistent anomaly",
                     "warning" if sm.get("watch") else "ok", unit="assets"))
    kpis.append(_kpi(sm.get("in_control", 0), "In control", f"{sm.get('in_control_anomaly_under_threshold', 0)} with an anomaly under threshold", "ok", unit="assets"))
    kpis.append(_kpi(sm.get("assets", 0), "Assets banded", f"{sm.get('section_not_measured', 0)} on one signal (no sub-meter)" if sm.get("section_not_measured") else "every section measured", "info", unit="assets"))
    over = [x for x in (o.get("sections") or []) if isinstance(x, dict) and x.get("over_reference")]
    if over:
        groups.append(_group("Sections over reference", f"{len(over)} section{'s' if len(over) != 1 else ''} running over their own reference EUI",
                             [f"{x.get('section')}, {x.get('building')} — {_fmt_num(x.get('eui_kwh_per_m2'))} vs {_fmt_num(x.get('reference_eui_kwh_m2'))} kWh/m², "
                              f"+{_fmt_num(x.get('deviation_pct'))}% · {x.get('threat', 0)} threat, {x.get('watch', 0)} watch of {x.get('assets', 0)}" for x in over],
                             "critical" if any(x.get("threat") for x in over) else "warning", scope="Building", qid=qid))
        for x in [x for x in over if x.get("threat")][:2]:
            actions.append(_action(f"Investigate {x.get('section')} at {x.get('building')}: {x.get('threat')} threat asset{'s' if x.get('threat') != 1 else ''}, "
                                   f"+{_fmt_num(x.get('deviation_pct'))}% over its reference EUI", "critical", ["threat", "energy"], scope="Building", qid=qid))
    blds = [b for b in (o.get("buildings") or []) if isinstance(b, dict)]
    if blds:
        groups.append(_group("By building", f"{len(blds)} building{'s' if len(blds) != 1 else ''}",
                             [f"{b.get('building') or 'Unplaced'} — {b.get('threat', 0)} threat · {b.get('watch', 0)} watch · {b.get('in_control', 0)} in control; "
                              f"{b.get('sections_over_reference', 0)} of {b.get('sections', 0)} sections over reference, {b.get('work_orders_open', 0)} open work orders"
                              for b in blds], "critical" if any(b.get("threat") for b in blds) else "info", scope="Building", qid=qid))


_CARD_BUILDERS = {
    "list_maintenance_decisions": lambda o, q, k, g, a, ask: _cards_decisions(o, q, k, g, a),
    "get_cost_savings": lambda o, q, k, g, a, ask: _cards_savings(o, q, k, g, a),
    "replacement_candidates": lambda o, q, k, g, a, ask: _cards_replacement(o, q, k, g, a),
    "work_order_blockers": lambda o, q, k, g, a, ask: _cards_blockers(o, q, k, g, a),
    "answer_from_records": lambda o, q, k, g, a, ask: _cards_records(o, ask, q, k, g),
    "get_asset_condition_summary": lambda o, q, k, g, a, ask: _cards_condition(o, q, k, g, a),
}
CARD_TOOLS = frozenset(_CARD_BUILDERS)


def _as_dict(v: Any) -> dict[str, Any] | None:
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except ValueError:
            return None
    return v if isinstance(v, dict) else None


def cards_from_tool_calls(question: str, tool_calls: list[Any], answer: str,
                          cost: dict[str, Any] | None = None) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Cards for ANY turn, from the tool calls it made - the same builders a planned turn uses.

    A scheduled question has to come back as a dashboard every day whichever route answered
    it (asked 4 Oct 2026): a direct wo_engine turn that read replacement_candidates, a general
    loop that called answer_from_records. None when no recognised tool ran, or when the turn
    already carries its own cards (the compliance engine's compliance_response)."""
    calls = [t for t in (tool_calls or []) if isinstance(t, dict)]
    names = [str(t.get("tool") or "") for t in calls]
    if "compliance_response" in names or "planner" in names:
        return None
    kpis: list[dict[str, Any]] = []
    groups: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []
    steps_out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for i, t in enumerate(calls):
        name = str(t.get("tool") or "")
        o = _as_dict(t.get("output"))
        if name in _CARD_BUILDERS and o is not None and o.get("ok") is not False and name not in seen:
            seen.add(name)
            ask = (t.get("input") or {}).get("question") if isinstance(t.get("input"), dict) else ""
            _CARD_BUILDERS[name](o, f"t{i}", kpis, groups, actions, str(ask or question or ""))
        if name and not name.startswith("compliance_pipeline"):
            n = len(_rows_of(o)) if o is not None else 0
            steps_out.append({"stage": "data", "label": name, "detail": f"{n} rows" if n else ""})
    if not (kpis or groups or actions):
        return None
    order = {"critical": 0, "warning": 1, "info": 2, "ok": 3}
    actions.sort(key=lambda a: order.get(a["severity"], 9))
    response = {"narrative": _lead(answer), "sections": [], "groups": groups[:6], "kpis": kpis[:8],
                "actions": actions[:6], "insights": [], "certificates": [], "pending": [], "offers": [], "validation": None}
    pipeline = {"engine": "orchestrator", "steps": steps_out[:12] + [{"stage": "validate", "label": "figures from the tool results", "detail": ""}], "cost": cost}
    return response, pipeline
