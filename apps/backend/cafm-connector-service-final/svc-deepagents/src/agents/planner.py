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


async def execute(plan: dict[str, Any], *, run_engine: Callable[..., Awaitable[tuple[str, list]]],
                  run_tool: Callable[[str, dict], Awaitable[Any]], scope_hint: str = "",
                  on_event: Callable[[dict], Awaitable[None]] | None = None) -> dict[str, dict[str, Any]]:
    """Run the plan. Returns {step_id: {"output", "tool_calls", "ok", "error", "ms"}}."""
    from . import trace

    results: dict[str, dict[str, Any]] = {}

    async def one(step: dict[str, Any]) -> None:
        t0 = time.perf_counter()
        ask = step["ask"]
        if step["depends_on"]:
            ctx = "\n\n".join(f"Result of step {d}:\n{_as_text(results.get(d, {}).get('output'))}" for d in step["depends_on"])
            ask = f"{ask}\n\nUse these results from earlier steps as given facts:\n{ctx}"
        if scope_hint:
            ask = f"{ask}\n\nScope (hard filter): {scope_hint}"
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
                if "question" in args and (step["depends_on"] or scope_hint):
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

    for batch in _batches(plan["steps"]):
        await asyncio.gather(*(one(s) for s in batch))
    return results


SYNTH_PROMPT = """You write the final answer to the user's question from the step results below, and from nothing else.

Rules:
- Every figure, name, date and code you state must appear in a step result. If a part of the question has no
  result (a step failed or returned nothing), say so for that part; never fill it from general knowledge.
- Follow the answer shape. Lead with the answer, then the detail. Markdown tables where they help.
- Keep the scope the steps used; do not generalise beyond the building, period or records they covered.
- Where a step returned `answer_rules`, follow them for that part.

QUESTION
{question}

PLAN
{plan}

ANSWER SHAPE
{shape}

STEP RESULTS
{results}"""


async def synthesise(question: str, plan: dict[str, Any], results: dict[str, dict[str, Any]],
                     llm: Callable[[str, str, str], Awaitable[str]]) -> str:
    body = "\n\n".join(
        f"[{sid}] {('OK' if r['ok'] else 'FAILED: ' + str(r['error']))}\n{_as_text(r['output'], 12000) if r['ok'] else ''}"
        for sid, r in results.items())
    prompt = SYNTH_PROMPT.format(question=question, plan=describe(plan), shape=plan.get("answer_shape") or "concise, tables where useful",
                                 results=body)
    return await llm("You are the answer-writing step of the Plenum CAFM orchestrator.", prompt, "synthesise")


_FIGURE = re.compile(r"(?<![\w.])(?:£|\$|AED\s?|€)?\d[\d,]*(?:\.\d+)?%?(?![\w.])")


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
