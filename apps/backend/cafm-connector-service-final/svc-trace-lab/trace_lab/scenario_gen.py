"""Scenarios for every area of the specification, generated (11 Nov 2026, task plan B2.1 and B2.2).

One batch covers the four kinds of scenario the plan names, and between them every section of the specification:

    single_domain   one question, one area                  final synthesis, answer completeness, output format, ...
    multi_domain    one question that needs several areas   multi-read planning, dependency order, context passing
    multi_turn      a question and its follow-ups           multi-turn continuity, scope preservation
    failure         a question asked while something fails  partial / total / authorization failure, retry rules,
                                                            missing evidence (its four states), evidence conflicts

Every scenario is tagged with the sections it tests and carries the expected outcome of each turn: the plan (a call
per area; `depends_on` says which call waits for which, so the order is implied), how a failure must be handled and
the answer format. That expected outcome follows from the AREA the scenario tests and is a draft for a person to
confirm; values that depend on a company's records (which work orders, which dates) are added by a person or a coding
agent through export, edit and import, before the scenario is asked. Nothing here comes from an answer.

Pure and seeded, like scenarios.py, whose question pools it reuses: no model, no database, no network. The same
(count, seed, space, mix) gives the same scenarios with the same ids (SD-7-001, MD-7-001, MT-7-001, FL-7-001), so a
trace can always be matched back to the scenario it answered. Every opening question is put to the real router
(scenarios.routes_to) and dropped, with the reason, when it would leave the space the batch is for.

G0: imports svc-deepagents through scenarios.py, changes nothing in it.
"""
from __future__ import annotations

import random
from collections.abc import Mapping
from typing import Any

from . import scenario_file, scenarios

MIX_250 = {"single_domain": 100, "multi_domain": 60, "multi_turn": 50, "failure": 40}
PREFIX = {"single_domain": "SD", "multi_domain": "MD", "multi_turn": "MT", "failure": "FL"}
#: The roles of the specification (General User, Portfolio Manager, Property Manager, Compliance Officer). A
#: compliance officer's capabilities are compliance only, so that role is dealt only to scenarios that stay there.
ROLES = ("general_user", "portfolio_manager", "property_manager", "compliance_officer")
COMPLIANCE_ONLY = "compliance_officer"
#: Where each area's calls go: the service the read proxy knows it by, and the path its calls start with. A failure
#: scenario's fault names these, so whoever asks it knows what to break.
ENGINE_SERVICE = {"compliance": ("oi", "/api/compliance"), "contract_performance": ("oi", "/api/contract-performance"),
                  "energy_intelligence": ("oi", "/api/energy"), "wo_engine": ("wom", "/api/work-orders")}
SINGLE_SECTIONS = (("final_synthesis", "output_format"), ("answer_completeness", "output_format"),
                   ("final_synthesis", "scope_preservation"), ("few_shot_examples", "output_format"))
#: Multi-domain: with dependency_order or context_passing each call waits for the one before it; otherwise the calls
#: are independent, which is the case the specification runs in parallel.
MULTI_SECTIONS = (("multi_read_planning", "dependency_order"), ("multi_read_planning", "answer_completeness"),
                  ("multi_read_planning", "context_passing"), ("multi_read_planning", "final_synthesis"))
CHAINED = frozenset({"dependency_order", "context_passing"})
TURN_SECTIONS = (("multi_turn_continuity", "scope_preservation"), ("multi_turn_continuity", "context_passing"))
#: A period to hold across the turns of a conversation: what is typed, and the dates it means.
PERIODS = (("in October 2026", "2026-10-01", "2026-10-31"), ("in September 2026", "2026-09-01", "2026-09-30"),
           ("in the third quarter of 2026", "2026-07-01", "2026-09-30"), ("in August 2026", "2026-08-01", "2026-08-31"))
#: Follow-ups that only make sense with the first answer in hand: the test of continuity is that the agent does not
#: ask again what "those" are.
FOLLOW_UPS: dict[str, tuple[str, ...]] = {
    "wo_engine": ("for each of those, who is the vendor", "which of them are past their sla",
                  "how long has each of those been open", "only the ones that are still open, please"),
    "compliance": ("which of those are statutory", "who is responsible for renewing each one",
                   "for each of those, has a work order been raised", "which of them lapse first"),
    "contract_performance": ("which of those vendors is doing worst", "what did each of those cost",
                             "which of those contracts ends first", "only the ones that missed their sla"),
    "energy_intelligence": ("which of those buildings is furthest from its target", "what changed since the month before",
                            "which of those is costing the most", "only the ones still unexplained"),
}
#: Missing evidence, state by state. zero_results: a filter nothing can match. not_recorded: something the records
#: do not hold at all, which is a different answer from "none".
ZERO_RESULTS: dict[str, str] = {
    "wo_engine": "which work orders are due after 1 January 2099",
    "compliance": "which certificates expire after 1 January 2099",
    "contract_performance": "which vendor invoices are dated after 1 January 2099",
    "energy_intelligence": "which energy anomalies were recorded after 1 January 2099",
}
NOT_RECORDED: dict[str, str] = {
    "wo_engine": "what was the weather on site when each overdue work order was raised",
    "compliance": "which insurer underwrites each vendor certificate that expires this quarter",
    "contract_performance": "what profit margin does each vendor make on the invoices it sends us",
    "energy_intelligence": "what make and model is the electricity meter in each building with an energy anomaly",
}
CONFLICTS: dict[str, str] = {
    "wo_engine": "the work order list and the planned maintenance schedule disagree on when the next lift service is "
                 "due - which is right",
    "compliance": "the certificate register and the latest inspection report give different expiry dates for the "
                  "fire alarm certificate - which is right",
    "contract_performance": "the contract and the latest invoice give different sla response hours for the same "
                            "vendor - which is right",
    "energy_intelligence": "the meter readings and the energy bill give different consumption for last month - "
                           "which is right",
}
FAILURE_KINDS = ("partial_failure", "total_failure", "authorization_failure", "retry", "zero_results",
                 "not_recorded", "unavailable", "tool_failed", "evidence_conflict")
MAX_COUNT = 2000


def _scaled(count: int, mix: Mapping[str, int] | None) -> dict[str, int]:
    """How many of each type make `count`, in the proportions of `mix` (MIX_250 when none), largest remainders first."""
    weights = dict(MIX_250 if mix is None else mix)
    if not weights or any(k not in scenario_file.TYPES for k in weights) or any(
            not isinstance(v, int) or isinstance(v, bool) or v < 0 for v in weights.values()) or not sum(weights.values()):
        raise ValueError(f"a mix gives a whole number for each of {', '.join(scenario_file.TYPES)}")
    total = sum(weights.values())
    raw = {k: count * v / total for k, v in weights.items()}
    out = {k: int(raw[k]) for k in weights}
    order = sorted(weights, key=lambda k: (-(raw[k] - out[k]), scenario_file.TYPES.index(k)))
    for k in order[:count - sum(out.values())]:
        out[k] += 1
    return out


def _dealer(values: tuple[Any, ...], rng: random.Random):
    """Deal `values` from a shuffled deck, reshuffling when it runs out: every value comes up about equally often,
    instead of independent draws that repeat one and starve another."""
    deck: list[Any] = []

    def take() -> Any:
        if not deck:
            deck.extend(values)
            rng.shuffle(deck)
        return deck.pop()

    return take


def _call(n: int, domain: str, depends_on: list[str] | None = None) -> dict[str, Any]:
    return {"call_id": f"c{n}", "domain": domain, "tool": None, "params": {}, "depends_on": list(depends_on or [])}


def _expected(plan: list[dict[str, Any]], *, answer_format: str = "success", state: str | None = None,
              failed: list[str] | None = None, answer: str | None = None) -> dict[str, Any]:
    return {"plan": plan, "failure_handling": {"state": state, "failed": list(failed or [])} if state else None,
            "answer_format": answer_format, "must_include": [], "must_not_include": [], "answer": answer}


def _fault(engine: str, fault: str, times: int | None = None) -> dict[str, Any]:
    service, prefix = ENGINE_SERVICE[engine]
    return {"service": service, "path_prefix": prefix, "fault": fault, "times": times}


def generate(*, count: int = 250, seed: int = 0, space: str = "full",
             mix: Mapping[str, int] | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """A batch as (scenarios, dropped). Each scenario is valid, normalised and ready to store; `dropped` says which
    questions were left out and why (routed outside the space, or the pool ran out)."""
    if space not in scenarios.SPACES:
        raise ValueError(f"unknown space {space!r}; one of {scenarios.SPACES}")
    if not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= MAX_COUNT:
        raise ValueError(f"count is a whole number from 1 to {MAX_COUNT}")
    want = _scaled(count, mix)
    rng = random.Random(f"evalgen:{space}:{seed}")
    engines = scenarios.engines_of(space)
    dropped: list[dict[str, Any]] = []
    if not engines:
        return [], [{"question": None, "reason": f"the space {space} has no areas to ask"}]

    def routed(engine: str, question: str) -> bool:
        to = scenarios.routes_to(question)
        if to == engine:
            return True
        dropped.append({"question": question, "reason": f"routes to {to or 'no area'}, not {engine}"})
        return False

    # Single questions, dealt round-robin across the areas so one area's pool running out leaves no gap. The area a
    # question belongs to is the one the real router sends it to, as in scenarios.generate: a question the router
    # sends nowhere, or to an area this space does not have, is dropped rather than guessed at.
    decks = {e: list(scenarios.BY_ENGINE[e]) for e in engines}
    for deck in decks.values():
        rng.shuffle(deck)
    singles: list[tuple[str, str]] = []
    while any(decks.values()):
        for e in engines:
            if decks[e]:
                q = decks[e].pop()
                to = scenarios.routes_to(q)
                if to in engines:
                    singles.append((to, q))
                else:
                    dropped.append({"question": q, "reason": f"routes to {to or 'no area'}, which {space} does not have"})
    crosses = []
    for c in scenarios.CROSS_DOMAIN:
        if not set(c["needs"]) <= set(engines):
            continue
        to = scenarios.routes_to(c["question"])
        if to in c["needs"]:
            crosses.append(c)
        else:
            dropped.append({"question": c["question"], "reason": f"routes to {to or 'no area'}, not an area it needs"})
    rng.shuffle(crosses)

    style = _dealer(scenarios.STYLES, rng)
    role_deck = _dealer(ROLES, rng)
    period = _dealer(tuple(range(len(PERIODS))), rng)
    follow = {e: _dealer(FOLLOW_UPS[e], rng) for e in engines}

    def role_for(domains: set[str]) -> str:
        for _ in range(len(ROLES) * 2):
            r = role_deck()
            if r != COMPLIANCE_ONLY or domains <= {"compliance"}:
                return r
        return "general_user"

    out: list[dict[str, Any]] = []

    def add(kind: str, n: int, sections: tuple[str, ...], turns: list[dict[str, Any]], domains: set[str], *,
            scope: dict[str, Any] | None = None, faults: list[dict[str, Any]] | None = None) -> None:
        row = {"scenario_id": f"{PREFIX[kind]}-{seed}-{n:03d}", "type": kind, "spec_sections": list(sections),
               "role": role_for(domains), "space": space, "scope": scope or {}, "faults": faults or [],
               "grounded_on": [], "domain_rules": [], "turns": turns}
        problems = scenario_file.validate(row)
        if problems:                                    # a generator bug, never a caller's mistake
            raise RuntimeError(f"generated an invalid scenario {row['scenario_id']}: {problems}")
        out.append(scenario_file.normalise(row))

    def short(kind: str, made: int) -> None:
        if made < want.get(kind, 0):
            dropped.append({"question": None, "reason": f"the question pool for {space} ran out: {made} of "
                                                        f"{want[kind]} {kind} scenarios were made"})

    # -- single_domain --------------------------------------------------------------------------------
    take = singles[:want.get("single_domain", 0)]
    for i, (engine, question) in enumerate(take, start=1):
        add("single_domain", i, SINGLE_SECTIONS[(i - 1) % len(SINGLE_SECTIONS)],
            [{"message": scenarios._rough(question, style(), rng), "expected": _expected([_call(1, engine)])}],
            {engine})
    short("single_domain", len(take))
    rest = singles[len(take):] or singles                 # openings for the other types; reused only when spent

    # -- multi_domain ---------------------------------------------------------------------------------
    take_x = crosses[:want.get("multi_domain", 0)]
    for i, c in enumerate(take_x, start=1):
        sections = MULTI_SECTIONS[(i - 1) % len(MULTI_SECTIONS)]
        chained = bool(CHAINED & set(sections))
        plan = [_call(n, need, [f"c{n - 1}"] if chained and n > 1 else None)
                for n, need in enumerate(c["needs"], start=1)]
        add("multi_domain", i, sections,
            [{"message": scenarios._rough(c["question"], style(), rng),
              "expected": _expected(plan, answer=("A good answer covers: " + c["covers"]) if c.get("covers") else None)}],
            set(c["needs"]))
    short("multi_domain", len(take_x))

    # -- multi_turn -----------------------------------------------------------------------------------
    n_turns = want.get("multi_turn", 0)
    made = 0
    for i in range(1, n_turns + 1):
        if not rest:
            break
        engine, question = rest[(i - 1) % len(rest)]
        sections = TURN_SECTIONS[(i - 1) % len(TURN_SECTIONS)]
        scope: dict[str, Any] = {}
        opening = question
        if "scope_preservation" in sections:
            label, start, end = PERIODS[period()]
            opening = f"{question} {label}"
            scope = {"period": {"from": start, "to": end}}
        asks = [opening, follow[engine]()]
        if i % 3 == 0:
            third = follow[engine]()
            asks.append(third if third != asks[1] else follow[engine]())
        add("multi_turn", i, sections,
            [{"message": m, "expected": _expected([_call(1, engine)])} for m in asks], {engine}, scope=scope)
        made += 1
    short("multi_turn", made)

    # -- failure --------------------------------------------------------------------------------------
    n_fail = want.get("failure", 0)
    made = 0
    for i in range(1, n_fail + 1):
        kind = FAILURE_KINDS[(i - 1) % len(FAILURE_KINDS)]
        engine, question = rest[(len(rest) - i) % len(rest)] if rest else (engines[0], "")
        cross = crosses[(len(crosses) - i) % len(crosses)] if crosses else None
        if kind in ("partial_failure", "total_failure"):
            if cross is None:
                dropped.append({"question": None, "reason": f"{space} has no question that needs two areas, so no "
                                                            f"{kind} scenario was made"})
                continue
            needs = list(cross["needs"])
            plan = [_call(n, need) for n, need in enumerate(needs, start=1)]
            if kind == "partial_failure":
                add("failure", i, ("partial_failure", "output_format"),
                    [{"message": cross["question"], "expected": _expected(
                        plan, answer_format="partial_success", state="partial_failure", failed=[plan[-1]["call_id"]])}],
                    set(needs), faults=[_fault(needs[-1], "http_500")])
            else:
                add("failure", i, ("total_failure", "output_format"),
                    [{"message": cross["question"], "expected": _expected(
                        plan, answer_format="failure", state="total_failure", failed=[c["call_id"] for c in plan])}],
                    set(needs), faults=[_fault(need, "http_500") for need in dict.fromkeys(needs)])
        elif kind in ("zero_results", "not_recorded", "evidence_conflict"):
            table = {"zero_results": ZERO_RESULTS, "not_recorded": NOT_RECORDED, "evidence_conflict": CONFLICTS}[kind]
            area = engines[(i - 1) % len(engines)]
            if not routed(area, table[area]):
                continue
            sections = ("evidence_conflicts",) if kind == "evidence_conflict" else ("missing_evidence",)
            add("failure", i, sections,
                [{"message": table[area], "expected": _expected([_call(1, area)], state=kind)}], {area})
        else:
            if not question:
                continue
            if kind == "authorization_failure":
                add("failure", i, ("authorization_failure", "output_format"),
                    [{"message": question, "expected": _expected(
                        [_call(1, engine)], answer_format="denial", state="authorization_failure", failed=["c1"])}],
                    {engine}, faults=[_fault(engine, "http_403")])
            elif kind == "retry":
                # A temporary error once, then the call works: the answer is an ordinary one, reached by one retry.
                add("failure", i, ("retry_rules",),
                    [{"message": question, "expected": _expected([_call(1, engine)])}],
                    {engine}, faults=[_fault(engine, "timeout", 1)])
            else:                                       # unavailable | tool_failed
                add("failure", i, ("missing_evidence", "output_format"),
                    [{"message": question, "expected": _expected(
                        [_call(1, engine)], answer_format="failure", state=kind, failed=["c1"])}],
                    {engine}, faults=[_fault(engine, "http_500" if kind == "unavailable" else "timeout")])
        made += 1
    short("failure", made)
    return out, dropped
