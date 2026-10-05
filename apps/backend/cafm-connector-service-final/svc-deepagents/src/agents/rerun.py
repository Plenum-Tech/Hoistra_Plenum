"""Re-run a recorded turn with the reader's corrections applied - the steps, not a fresh question.

"Re-answer" hands a correction back to the orchestrator as a follow-up and lets it work the
question out again. This is the stricter thing the user asked for (2 Oct 2026): take the SAME run,
apply each correction at the step it was raised on, and execute that plan again - the step,
everything that depends on it, the synthesis and the figure check - as a new turn in the same
thread, traced like any other and linked to the original. The old turn stays as evidence; the pair
is what the training export wants.

How a correction lands, by the span it was raised on (agents/trace.py kinds):

    query / tool / model / agent / suggestion   appended to that step's ask as "Correction: ..."
                                                (a query filter or date field reaches the record
                                                engine through its planner, which honours attribute
                                                filters; an agent instruction is pinned to its ask)
    tool with `args`                            overrides the tool step's arguments
    route -> an engine                          the step's target becomes that engine
    route -> planner, or plan                   the question is planned again with the correction as
                                                a constraint (agents/planner.py)

A run that had no plan of its own (one engine by the router) is replayed as a one-step plan for
that engine, so the same machinery applies. A run the general loop answered ("orchestrator loop",
with its `task` sub-agents) is replayed as one loop step: the original question with the
corrections, asked of the same loop in the same conversation (5 Oct 2026).
"""
from __future__ import annotations

import json
import re
from typing import Any

import structlog

from . import planner

log = structlog.get_logger(__name__)

#: The general loop's name in the trace (agents/trace.py) and in a recorded plan.
LOOP = "orchestrator loop"


def _parse(v: Any) -> Any:
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


def _router_decision(spans: list[dict[str, Any]]) -> Any:
    """The router span's decision: the live router records its routing dict as the span output
    ({agent, also, reason} - seen on agent_trace_spans 5 Oct 2026); other recorders nest it
    under model_output. Either shape, or a bare agent name."""
    router = next((s for s in spans if s.get("kind") == "router"), None)
    if router is None:
        return None
    out = _parse(router.get("output"))
    if isinstance(out, dict) and "model_output" in out:
        return out.get("model_output")
    return out or None


def plan_of(turn: dict[str, Any]) -> dict[str, Any] | None:
    """The plan the recorded turn ran on: the planner's steps, or one step for the engine the
    router chose, or one loop step for a general-loop answer. None when the run shows no route at
    all (nothing to replay)."""
    spans = sorted(turn.get("spans") or [], key=lambda s: s.get("seq") or 0)
    plan_span = next((s for s in spans if s.get("kind") == "plan"), None)
    loop_plan = {"mode": "single", "source": "rerun", "goal": "", "answer_shape": "",
                 "steps": [{"id": "s1", "kind": "loop", "target": LOOP, "ask": turn.get("question") or "", "depends_on": [], "why": "the general loop answered it"}]}
    if plan_span:
        out = _parse(plan_span.get("output")) or {}
        plan = out.get("plan") if isinstance(out, dict) else None
        if isinstance(plan, dict) and plan.get("steps"):
            steps = []
            for st in plan["steps"]:
                target = str(st.get("target") or "")
                kind = st.get("kind") or ("engine" if target in planner.ENGINES else "tool")
                if kind == "loop" or target == LOOP:
                    continue
                steps.append({"id": str(st.get("id") or f"s{len(steps) + 1}"), "kind": kind, "target": target,
                              "ask": st.get("ask") or turn.get("question") or "", "depends_on": list(st.get("depends_on") or []),
                              "why": st.get("why") or "", "args": st.get("args") if isinstance(st.get("args"), dict) else None})
            if steps:
                return {"mode": plan.get("mode") or "multi", "source": "rerun", "goal": plan.get("goal") or "",
                        "steps": steps, "answer_shape": plan.get("answer_shape") or ""}
            # The recorded plan says the loop answered - after the router, which may have named an
            # engine the dispatch then sent to the loop (two registers, an action verb). The plan
            # is what ran, so the loop is what replays.
            if any((st.get("kind") == "loop" or str(st.get("target") or "") == LOOP) for st in plan["steps"]):
                return loop_plan
    mo = _router_decision(spans)
    engine = (mo.get("agent") if isinstance(mo, dict) else mo) if mo else None
    if not engine:
        group = next((s for s in spans if s.get("kind") == "agent" and s.get("name") in planner.ENGINES), None)
        engine = group.get("name") if group else None
    if engine in planner.ENGINES:
        return {"mode": "single", "source": "rerun", "goal": "", "answer_shape": "",
                "steps": [{"id": "s1", "kind": "engine", "target": engine, "ask": turn.get("question") or "", "depends_on": [], "why": "the engine the router chose"}]}
    if any(s.get("kind") == "agent" and s.get("name") == LOOP for s in spans):
        return loop_plan
    return None


def routing_of(turn: dict[str, Any]) -> dict[str, Any] | None:
    """What the router decided for the recorded turn - {agent, also, reason} from its span - so a
    loop re-run is told the same sub-agents and keeps the same catalogue rule. None: no decision."""
    mo = _router_decision(sorted(turn.get("spans") or [], key=lambda s: s.get("seq") or 0))
    return mo if isinstance(mo, dict) and mo.get("agent") else None


def loop_step(plan: dict[str, Any] | None) -> dict[str, Any] | None:
    """The step when the plan is the general loop and nothing else (plan_of). Such a re-run asks
    the loop again; there are no steps to replay one by one."""
    steps = (plan or {}).get("steps") or []
    if len(steps) != 1:
        return None
    return steps[0] if steps[0].get("kind") == "loop" else None


def single_engine_step(plan: dict[str, Any] | None) -> dict[str, Any] | None:
    """The step when the plan is one engine and nothing else - how every direct engine answer is
    recorded (plan_of). Such a re-run replays the engine's own path, so it comes back with the
    dashboard the answer first had; any other plan replays step by step (5 Oct 2026)."""
    steps = (plan or {}).get("steps") or []
    if len(steps) != 1:
        return None
    st = steps[0]
    return st if st.get("kind") == "engine" and st.get("target") in planner.ENGINES else None


def is_engine_error(answer: Any) -> bool:
    """An engine run that failed inside meta_tools.run_verbose comes back as a normal answer whose
    text is a bare JSON error object; the chat would show it as the answer."""
    text = str(answer or "").strip()
    if not text:
        return True
    if not text.startswith("{"):
        return False
    try:
        obj = json.loads(text)
    except ValueError:
        return False
    return isinstance(obj, dict) and "error" in obj and len(obj) <= 3


def step_for_span(turn: dict[str, Any], span_id: str | None, plan: dict[str, Any]) -> str:
    """Which plan step a correction raised on `span_id` belongs to: the nearest `step` ancestor
    (its name is "s1: target"), else the only step, else the first."""
    steps = plan.get("steps") or []
    if not steps:
        return ""
    if len(steps) == 1 or not span_id:
        return steps[0]["id"]
    by_id = {s.get("id"): s for s in turn.get("spans") or []}
    cur = by_id.get(span_id)
    while cur is not None:
        if cur.get("kind") == "step":
            m = re.match(r"\s*([A-Za-z0-9_]+)\s*:", str(cur.get("name") or ""))
            if m and any(s["id"] == m.group(1) for s in steps):
                return m.group(1)
        cur = by_id.get(cur.get("parent_id"))
    return steps[0]["id"]


def apply_corrections(turn: dict[str, Any], plan: dict[str, Any], corrections: list[dict[str, Any]]) -> tuple[dict[str, Any], list[str], bool]:
    """The plan with the corrections applied. Returns (plan, notes, needs_replan)."""
    plan = json.loads(json.dumps(plan))
    notes: list[str] = []
    replan = False
    for c in corrections or []:
        text = " ".join(str(c.get("text") or "").split())
        mode = str(c.get("mode") or "suggestion")
        sid = step_for_span(turn, c.get("span_id"), plan)
        step = next((s for s in plan["steps"] if s["id"] == sid), None)
        if mode == "route" and c.get("route") and c["route"] != "planner" and c["route"] in planner.ENGINES and step:
            step["kind"], step["target"], step["args"] = "engine", c["route"], None
            notes.append(f"{sid}: route to {c['route']}")
        elif mode == "plan" or (mode == "route" and c.get("route") == "planner"):
            replan = True
            notes.append("plan again: " + (text or "as corrected"))
        if text and step is not None:
            step["ask"] = (step.get("ask") or "") + "\n\nCorrection (apply exactly): " + text
            if mode != "route":
                notes.append(f"{sid}: {text[:160]}")
        if mode == "tool" and isinstance(c.get("args"), dict) and step is not None and step["kind"] == "tool":
            step["args"] = {**(step.get("args") or {}), **c["args"]}
            notes.append(f"{sid}: args {json.dumps(c['args'])[:120]}")
    return plan, notes, replan


def pins_from(corrections: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The structured corrections a reader made on a query or tool - excluded statuses, a period, a
    date field - as filters the record engine compiles into its plan (ontology_qa.apply_pinned)."""
    ex: list[str] = []
    period = field = None
    for c in corrections or []:
        if c.get("mode") not in ("query", "tool", "suggestion"):
            continue
        for x in c.get("exclude") or []:
            if str(x).strip() and str(x) not in ex:
                ex.append(str(x))
        period = c.get("period") or period
        field = c.get("field") or field
    if not (ex or period or field):
        return None
    return {"exclude_statuses": ex, "period": period, "date_field": field}


def rerun_question(turn: dict[str, Any], notes: list[str]) -> str:
    """The question the re-run is recorded under, so the thread reads as what happened."""
    q = (turn.get("question") or "").strip()
    return f"Re-run with corrections ({'; '.join(notes)[:400] or 'as marked'}): {q}"
