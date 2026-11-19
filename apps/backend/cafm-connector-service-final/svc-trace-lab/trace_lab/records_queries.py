"""Which records queries a live question's turn gets (18 Nov 2026, Hussain and Bala).

For a live question the expected answer's values are read by running a records query. The query comes, in this order,
from:

    1. Hoistra's own - the plan its records tool (answer_from_records) wrote, found in the turn's trace, one per call;
    2. Trace Lab's fixed query - an answer-key shape that matches the question (answer_key.match);
    3. a proposal - Hoistra's own planner (ontology_qa.plan_question, its prompt, its map, its retries) asked for one,
       only when the turn called a data tool other than the records tool, and only when the caller asks for it;
    4. none, with the reason: not about records, no map of the records yet, or the planner could not be reached.

The nodes are the tree view's, as eval_store.nodes_of returns them. A records call keeps its LAST plan (the planner
retries until its plan validates), and a call that found nothing or failed is dropped when a later call follows it:
that call was a retry. Nothing here reads the records or changes anything in the orchestrator; a proposal is a model
call and is always labelled as one (SOURCE_WORDS).
"""
from __future__ import annotations

import json
import math
from typing import Any

from src.agents import ontology_qa as oq

from . import answer_key as ak
from . import records_run

#: Hoistra's records tool (svc-deepagents ontology_qa.answer_from_records).
RECORDS_TOOL = "answer_from_records"
#: Tool nodes that are not a data tool: the records tool itself, the sub-agent hand-off and the final answer.
NOT_DATA_TOOLS = frozenset({RECORDS_TOOL, "task", "final_answer"})
#: ...and the checks (verify_vendor_invoice, verify_accreditation_now): they test a record, they do not read data.
NOT_DATA_PREFIX = "verify"

SOURCE_WORDS = {"hoistra": "Hoistra’s query", "answer_key": "Trace Lab’s fixed query",
                "proposed": "Proposed for this question - check it before accepting"}


def _nodes(nodes: Any) -> list[dict]:
    return [n for n in (nodes or []) if isinstance(n, dict)]


def _is_tool(n: dict) -> bool:
    return n.get("type") == "tool" or n.get("kind") == "tool"


def _object(v: Any) -> dict | None:
    """A recorded input or output as a dict: it is one, or a JSON string of one (the tool's message content); a value
    Hoist Traces cut ({"_truncated": ...} or text ending in "… [N more chars]") is not parsed."""
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except ValueError:
            return None
    return v if isinstance(v, dict) else None


def _question_of(call: dict) -> str | None:
    q = (_object(call.get("input")) or {}).get("question")
    return q.strip() or None if isinstance(q, str) else None


def _failed(call: dict) -> bool:
    """The call found nothing or failed: the tool says found false, ok false or an error, or its node is marked failed."""
    if call.get("ok") is False or call.get("error"):
        return True
    out = _object(call.get("output")) or {}
    return out.get("found") is False or out.get("ok") is False or bool(out.get("error"))


def _order(nodes: list[dict]):
    """The key that puts nodes in the order they happened: their seq (one number per recorded step, in the order the
    steps began) when every node has one, else their place in the list."""
    if nodes and all(isinstance(n.get("seq"), int) for n in nodes):
        return lambda n: n["seq"]
    place = {id(n): i for i, n in enumerate(nodes)}
    return lambda n: place[id(n)]


def _last_plan(nodes: list[dict]) -> dict | None:
    """The plan the planner wrote last among these nodes. A node whose output the tree view filled in from a step
    below it (filled.output_from; an agent group shows its last model call's output) is a copy, not a plan."""
    for n in reversed(nodes):
        if (n.get("filled") or {}).get("output_from"):
            continue
        found = ak.extract_plans([n])
        if found:
            return found[0]
    return None


def extract_queries(nodes: list | None) -> list[dict]:
    """The records queries Hoistra's records tool made in this turn, in call order: [{"plan", "question"}].

    One per answer_from_records call: its last plan, and the question the model passed (None when the input was cut).
    The plan is a model call of the planner; Hoistra's trace usually hangs it under a "sub-agent" group rather than
    under the tool (trace.py on_model_start), so a call with no plan below it takes the plans recorded after it and
    before the next call. A call with no plan gives no query. A call that found nothing or failed is dropped when a
    later call (one that has a plan) follows it - a retry."""
    nodes = _nodes(nodes)
    order = _order(nodes)
    calls = sorted((n for n in nodes if _is_tool(n) and n.get("name") == RECORDS_TOOL), key=order)
    kids: dict[Any, list[dict]] = {}
    for n in nodes:
        if n.get("parent_id") is not None:
            kids.setdefault(n["parent_id"], []).append(n)

    def below(call: dict) -> list[dict]:
        out, stack, seen = [], [call.get("id")], set()
        while stack:
            pid = stack.pop()
            if pid in seen:
                continue
            seen.add(pid)
            for c in kids.get(pid, []):
                out.append(c)
                stack.append(c.get("id"))
        return sorted(out, key=order)

    own = {id(call): below(call) for call in calls}
    taken = {id(c) for under in own.values() for c in under} | {id(c) for c in calls}
    nearby = [n for n in nodes if id(n) not in taken]
    made = []
    for i, call in enumerate(calls):
        plan = _last_plan(own[id(call)])
        if plan is None:
            lo = order(call)
            hi = order(calls[i + 1]) if i + 1 < len(calls) else math.inf
            plan = _last_plan([n for n in nearby if lo < order(n) < hi])
        if plan is not None:
            made.append((call, plan))
    return [{"plan": plan, "question": _question_of(call)}
            for i, (call, plan) in enumerate(made) if i == len(made) - 1 or not _failed(call)]


def data_tools(nodes: list | None) -> list[str]:
    """The data tools this turn called, each name once in call order: every tool node but the records tool, `task`,
    `final_answer` and the checks (names starting with "verify")."""
    out: list[str] = []
    for n in _nodes(nodes):
        name = n.get("name")
        if (_is_tool(n) and isinstance(name, str) and name not in NOT_DATA_TOOLS
                and not name.startswith(NOT_DATA_PREFIX) and name not in out):
            out.append(name)
    return out


async def queries_for(nodes: list | None, question: str, *, propose: bool) -> dict:
    """The queries a live question's turn gets: {"queries": [{"source", "plan", "question"}], "none": reason}.

    source is "hoistra", "answer_key" or "proposed" (SOURCE_WORDS says it in words). With no query, `none` says why:
    None when no proposal was asked for, "not_about_records" (the turn called no data tool, or the planner says there is
    no plan for it), "no_map" (Hoistra's map of the records is not built) or "planner_unavailable" (the planner or
    the map failed otherwise). The planner runs only for propose=True, only when the turn called a data tool, and
    never when Hoistra's own or the answer key's query is known."""
    got = extract_queries(nodes)
    if got:
        return {"queries": [{"source": "hoistra", "plan": q["plan"], "question": q["question"] or question} for q in got],
                "none": None}
    found = ak.match(question)
    if found:
        key, params = found
        return {"queries": [{"source": "answer_key", "plan": key.plan(params), "question": question}], "none": None}
    if not propose:
        return {"queries": [], "none": None}
    if not data_tools(nodes):
        return {"queries": [], "none": "not_about_records"}
    try:
        onto = await records_run.load_map()
    except records_run.NotReady:
        return {"queries": [], "none": "no_map"}
    except Exception:  # noqa: BLE001 - a map that cannot be loaded is not the question's fault
        return {"queries": [], "none": "planner_unavailable"}
    try:
        plan, _attempts = await oq.plan_question(onto, question)
    except oq.OntologyError:
        return {"queries": [], "none": "not_about_records"}
    except Exception:  # noqa: BLE001 - no key, no network, a model error: no proposal, and the page says so
        return {"queries": [], "none": "planner_unavailable"}
    return {"queries": [{"source": "proposed", "plan": plan, "question": question}], "none": None}
