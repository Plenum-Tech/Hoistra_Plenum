"""A question spanning two domains gets a step on each domain's engine.

The reported case (30 Oct):

    "Please check work order WO00000359 at MixedUse 038. What is its current status, did it meet
     its response SLA, and how has HVAC Vendor 0111 been performing at this building overall?"

    expected : work-order status and SLA, then that vendor's record at that building
    actual   : "I need a permitted vendor, reporting period, or configuration request to continue."

Everything needed is already built. is_multi_part is True for that question, so a plan is made;
ENGINES holds both wo_engine ("work orders and maintenance: counts by status, overdue, blocked")
and contract_performance ("vendor SLA, response and completion times, KPIs"); validate accepts a
step per engine; _stream_planned_turn has run_engine and runs independent steps together.

What stops it is the instruction. The plan prompt says an engine step "costs 5-10x a tool step"
and to use one ONLY where a step needs judgement across several reads — so the planner reaches
for answer_from_records instead, and one tool cannot apply another domain's logic. The October
hoist plan did exactly that: two steps, both answer_from_records.

These pin the parts that must hold whatever the model answers: a cross-domain plan validates,
its independent steps run together, a dependent step waits, and the prompt tells the planner to
split by domain and to carry the vendor and period into the ask — the absence of which is what
the failing answer asked for.
"""
from __future__ import annotations

from src.agents import planner
from src.agents.planner import ENGINES, MAX_STEPS, _batches, validate

TOOLS = {"answer_from_records": "counts, lists or details of records",
         "get_cost_savings": "where money goes"}


def plan_of(*steps):
    return {"goal": "g", "answer_shape": "a", "steps": list(steps)}


def step(sid, kind, target, ask="do the thing", depends=()):
    return {"id": sid, "kind": kind, "target": target, "ask": ask, "depends_on": list(depends)}


# -- the two engines the reported question needs -------------------------------------------

def test_both_engines_the_question_needs_are_in_the_catalogue():
    assert "wo_engine" in ENGINES and "contract_performance" in ENGINES
    assert "sla" in ENGINES["contract_performance"].lower()
    assert "work order" in ENGINES["wo_engine"].lower()


def test_a_plan_with_a_step_on_each_engine_is_accepted():
    ok, why = validate(plan_of(
        step("s1", "engine", "wo_engine", "Status and response SLA of work order WO00000359 at MixedUse 038"),
        step("s2", "engine", "contract_performance", "HVAC Vendor 0111's record at MixedUse 038"),
    ), TOOLS)
    assert ok is not None, why
    assert [s["target"] for s in ok["steps"]] == ["wo_engine", "contract_performance"]
    assert ok["mode"] == "multi"


def test_three_steps_across_two_engines_and_a_tool_are_accepted():
    ok, why = validate(plan_of(
        step("s1", "engine", "wo_engine"),
        step("s2", "engine", "contract_performance"),
        step("s3", "tool", "answer_from_records"),
    ), TOOLS)
    assert ok is not None, why
    assert len(ok["steps"]) == 3


def test_an_unknown_engine_is_still_refused():
    ok, why = validate(plan_of(step("s1", "engine", "vendor_performance")), TOOLS)
    assert ok is None and "vendor_performance" in why


# -- independent steps run together, a dependent one waits ---------------------------------

def test_two_independent_engine_steps_run_in_one_batch():
    batches = _batches([step("s1", "engine", "wo_engine"),
                        step("s2", "engine", "contract_performance")])
    assert len(batches) == 1 and len(batches[0]) == 2, (
        "two steps that need nothing from each other should run together, not one after the other")


def test_a_step_that_needs_an_earlier_one_runs_after_it():
    batches = _batches([step("s1", "engine", "wo_engine"),
                        step("s2", "engine", "contract_performance", depends=["s1"])])
    assert [[s["id"] for s in b] for b in batches] == [["s1"], ["s2"]]


def test_the_vendor_step_can_wait_for_the_work_order_step():
    """"how has that vendor performed" may need the vendor the first step found."""
    batches = _batches([step("s1", "engine", "wo_engine"),
                        step("s2", "tool", "answer_from_records", depends=["s1"]),
                        step("s3", "engine", "contract_performance", depends=["s1"])])
    assert [s["id"] for s in batches[0]] == ["s1"]
    assert {s["id"] for s in batches[1]} == {"s2", "s3"}


# -- what the planner is told --------------------------------------------------------------

def test_the_prompt_tells_the_planner_to_split_a_cross_domain_question_by_domain():
    p = planner.PLAN_PROMPT.lower()
    assert "domain" in p, "the prompt never mentions domains"
    assert ("one engine cannot answer another" in p or "owning engine" in p
            or "a step for each domain" in p), (
        "the prompt discourages engine steps on cost and says nothing about a question that "
        "spans domains, so the planner reaches for a tool that cannot apply the other domain's "
        "logic -- which is how two answer_from_records steps came to answer a hoist question")


def test_the_prompt_still_discourages_an_engine_where_a_tool_will_do():
    """The cost rule is right for a single-domain question and must survive."""
    p = planner.PLAN_PROMPT.lower()
    assert "costs 5-10x" in p or "5-10x" in p


def test_the_prompt_asks_for_the_vendor_and_period_in_the_ask():
    """The failing answer asked for "a permitted vendor, reporting period" -- an engine given a
    step that does not name them can only ask back."""
    p = planner.PLAN_PROMPT.lower()
    assert "vendor" in p and "period" in p


def test_the_step_budget_allows_a_question_with_three_parts():
    assert MAX_STEPS >= 3
