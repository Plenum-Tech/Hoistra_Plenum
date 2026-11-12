"""What the first scenario runs showed in the traces, and what each fix holds to (12 Nov 2026).

46 turns were asked over REST on 12 Nov 2026 (sessions scn__spec_v0__*, scn__spec_mt_v0__*,
scn__hussain_e2e_v0__*) and read back from agent_trace_turns / agent_trace_spans. Each test here is
one thing those traces showed:

  * a follow-up was asked "which area do you mean?" - "Which of those at Bishopsgate Tower are
    overdue?" after a list of open work orders (turn-f2eea58174d449c3, turn-3af1b52df7bb4cd3);
  * a superadmin who named no company had no one scope - the record readers read every company, the
    services read the superadmin's own (empty) one;
  * the loop's routing step was counted as evidence, and the answer then said a lookup had failed
    when none had run (turn-0742ad34d0b74817);
  * an answer spoke about "the only tool that surfaces ..." (turn-491d961e900b4400);
  * "due in the next 60 days" could not be planned: no rolling period (turn-4381e9d1938446d5);
  * two sub-agents run at once took each other's output and tool calls in the trace (same turn);
  * a contract-renewal question went to the certificate engine, which holds no contract.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest

from src.agents import agent_router, domain_chooser
from src.agents import evidence as ev
from src.agents import final_answer as fa
from src.agents import ontology_qa, thread_scope, trace
from src.http_client import caller_organization_id
from src.services.principal import Principal, caller_principal

# -- a follow-up is never asked which area it means ---------------------------------------------

OPEN_BY_BUILDING = {"question": "List all open work orders by building.",
                    "answer": "**23 work orders are open:** 13 at **Bishopsgate Tower** and 10 at **Ashgrove Court**."}


def _step(monkeypatch, message, *, prev=None, state=None):
    import src.agents.orchestrator as O

    cls = next(v for v in vars(O).values() if isinstance(v, type) and hasattr(v, "_conversation_step"))

    async def last_turn(sid):
        return prev

    monkeypatch.setattr(O.chat_threads, "last_turn", last_turn)
    return asyncio.run(cls.__new__(cls)._conversation_step("s-1", message, None, state if state is not None else {}))


def test_the_question_from_the_trace_reads_as_general_on_its_own():
    """Why it was asked back: "overdue" with no area named is a general question - when it stands alone."""
    assert domain_chooser.is_general("Which of those at Bishopsgate Tower are overdue?")


def test_a_follow_up_with_no_area_named_follows_the_answer_before_it(monkeypatch):
    out = _step(monkeypatch, "Which of those at Bishopsgate Tower are overdue?", prev=OPEN_BY_BUILDING)
    assert out["kind"] == "followup"
    assert "23 work orders are open" in out["context"]
    assert "List all open work orders by building" in out["router_context"]


def test_the_same_words_with_nothing_before_them_are_still_asked_which_area(monkeypatch):
    assert _step(monkeypatch, "Which of those at Bishopsgate Tower are overdue?")["kind"] == "choose"


def test_a_general_question_that_leans_on_nothing_is_still_asked_which_area(monkeypatch):
    assert _step(monkeypatch, "what is overdue today", prev=OPEN_BY_BUILDING)["kind"] == "choose"


def test_a_question_straight_after_the_area_cards_is_not_taken_as_a_follow_up_of_them(monkeypatch):
    state = {"pending_domain_question": "what is overdue"}
    cards = {"question": "what is overdue", "answer": domain_chooser.reply_text()}
    assert _step(monkeypatch, "which of those are overdue", prev=cards, state=state)["kind"] == "choose"


# -- one company for the whole turn -------------------------------------------------------------

def _principal(role, org):
    return Principal(user_id=uuid.uuid4(), email="x@example.com", organization_id=org, role=role,
                     can_ingest=True, building_ids=None)


def test_a_superadmin_who_names_no_company_asks_about_their_own():
    from src.api.routes.workflow import _resolve_acting_org

    home = uuid.uuid4()
    assert _resolve_acting_org(None, _principal("superadmin", home)) == str(home)
    assert _resolve_acting_org("  ", _principal("superadmin", home)) == str(home)


def test_a_superadmin_who_names_a_company_asks_about_that_one():
    from src.api.routes.workflow import _resolve_acting_org

    other = str(uuid.uuid4())
    assert _resolve_acting_org(other, _principal("superadmin", uuid.uuid4())) == other


def test_everyone_else_is_as_before():
    from fastapi import HTTPException

    from src.api.routes.workflow import _resolve_acting_org

    home = uuid.uuid4()
    assert _resolve_acting_org(None, _principal("admin", home)) is None
    assert _resolve_acting_org(str(home), _principal("admin", home)) is None
    with pytest.raises(HTTPException) as exc:
        _resolve_acting_org(str(uuid.uuid4()), _principal("admin", home))
    assert exc.value.status_code == 403


def test_the_record_reader_then_reads_that_company_and_no_other():
    """What the fix buys: with the company set, a superadmin's record read is no longer unrestricted."""
    home = str(uuid.uuid4())
    p = _principal("superadmin", uuid.UUID(home))
    tok = caller_organization_id.set(None)
    try:
        assert dict(ontology_qa.scope_settings(p))["app.udr_unrestricted"] == "1"     # what the traces ran under
        caller_organization_id.set(home)
        s = dict(ontology_qa.scope_settings(p))
        assert s["app.udr_unrestricted"] == "0" and s["app.udr_org"] == home
    finally:
        caller_organization_id.reset(tok)


# -- what counts as evidence, and what an answer may say about failure ---------------------------

def test_the_loops_routing_step_is_not_evidence():
    results, drafts = fa.gather([
        {"tool": "select_skill", "output": {"primary_agent": "wo_engine", "matched_triggers": ["work order"]}},
        {"tool": "compact_context", "output": "kept the last 4 messages"},
        {"tool": "task", "output": "No matching certificates were found, so there are no work orders to report."},
    ])
    assert results == {} and len(drafts) == 1
    assert not fa.has_evidence([{"tool": "select_skill", "output": {}}, {"tool": "compact_context", "output": ""}])


def test_the_writer_is_told_nothing_was_read_not_that_something_failed():
    text = fa._results_text({"previous_answer": {"tool": "", "output": "No certificates are lapsed.", "ok": True,
                                                 "tool_calls": [], "error": None}})
    assert "no lookup was run" in text and "nothing failed" in text
    assert "No certificates are lapsed." in text
    assert "ONLY for a result listed under FAILED" in fa.FINAL_PROMPT


INVENTED = ("**Whether any related work orders are raised could not be checked** - the work-order records could "
            "not be read.\n**Next step:** ask again in a moment.")


def test_an_answer_that_says_a_lookup_failed_when_none_did_is_rewritten():
    assert any("no lookup failed" in m for m in ev.state_mismatches(INVENTED, {}))
    ok = {"t1": {"tool": "list_work_orders", "ok": True, "status": ev.OK, "output": {"rows": []}, "tool_calls": []}}
    assert any("no lookup failed" in m for m in ev.state_mismatches(INVENTED, ok))


def test_saying_so_is_right_when_a_lookup_did_fail():
    failed = {"t1": {"tool": "list_work_orders", "ok": False, "status": ev.FAILED, "output": None, "tool_calls": []}}
    assert not any("no lookup failed" in m for m in ev.state_mismatches(INVENTED, failed))


def test_saying_so_is_right_when_a_call_inside_an_engine_step_failed():
    step = {"s1": {"tool": "compliance", "ok": True, "output": "prose", "tool_calls": [
        {"tool": "work_order_blockers", "output": {"ok": False, "error": "the service did not respond"}}]}}
    assert not any("no lookup failed" in m for m in ev.state_mismatches(INVENTED, step))


def test_the_invented_failure_is_caught_by_the_final_step_and_rewritten():
    calls = []

    async def llm(system, user, role):
        calls.append((role, user))
        return INVENTED if len(calls) == 1 else ("**No.** The previous answer found no lapsed or expiring "
                                                 "certificates, so there is none to raise a work order for.")

    answer, report = asyncio.run(fa.write("Is a work order raised for any of them?", "draft", results={}, llm=llm,
                                          previous="No certificates are lapsed or expiring within 30 days."))
    assert answer.startswith("**No.**") and report["rewrites"] == 1
    assert "no lookup failed" in calls[1][1]


@pytest.mark.parametrize("answer", [
    "Used `work_order_blockers`; it only shows contracts ending within 60 days.",
    "No contracts come up for renewal, but the only tool that surfaces contract-renewal risk does so through open work orders.",
    "There is no exposed contract-list tool for a 90-day renewal register.",
])
def test_an_answer_that_talks_about_the_tools_is_rewritten(answer):
    assert ev.speaks_of_tools(answer)
    assert any("tools" in m for m in ev.state_mismatches(answer, {}))


@pytest.mark.parametrize("answer", [
    "23 work orders are open across two buildings.",
    "The power tool store at Bishopsgate is locked; a tool hire invoice is due.",
    "No tool was returned to stores after the lift repair.",
])
def test_plain_answers_and_real_tools_are_left_alone(answer):
    assert not ev.speaks_of_tools(answer)


# -- a rolling period ----------------------------------------------------------------------------

@pytest.mark.parametrize("value,name,start,end", [
    ("next_60_days", "next_60_days", "CURRENT_DATE", "(CURRENT_DATE + INTERVAL '61 day')"),
    ("Next 90 days", "next_90_days", "CURRENT_DATE", "(CURRENT_DATE + INTERVAL '91 day')"),
    ("last-14-days", "last_14_days", "(CURRENT_DATE - INTERVAL '14 day')", "(CURRENT_DATE + INTERVAL '1 day')"),
    ("next_2_weeks", "next_2_weeks", "CURRENT_DATE", "(CURRENT_DATE + INTERVAL '15 day')"),
])
def test_a_rolling_window_of_any_length_is_a_period(value, name, start, end):
    assert ontology_qa.period_of(value) == (name, None)
    assert ontology_qa.period_bounds(name) == (start, end)


def test_the_named_periods_read_as_they_did():
    assert ontology_qa.period_bounds("last_30_days") == ontology_qa.PERIODS["last_30_days"]
    assert ontology_qa.period_of("next_month") == ("next_month", None)
    assert ontology_qa.period_of("start_of_last_month") == ("last_month", "start")


@pytest.mark.parametrize("value", ["soon", "next_0_days", "next_99999_days", "next_60_days; DROP TABLE x", "next__days",
                                   "next_6e1_days"])
def test_only_a_whole_number_of_days_becomes_sql(value):
    assert ontology_qa.period_of(value) is None


def test_the_planner_is_told_about_rolling_windows():
    import inspect

    assert "next_<N>_days" in inspect.getsource(ontology_qa)
    assert "next_60_days" in ontology_qa.ROLLING_HELP


# -- two sub-agents at once, each with its own span ----------------------------------------------

@pytest.fixture
def traced(monkeypatch):
    monkeypatch.setattr(trace, "_ready", True)

    async def fake_write(t, answer, tool_calls, summary):
        return None

    monkeypatch.setattr(trace, "_write", fake_write)
    p = _principal("admin", uuid.uuid4())
    tp, to = caller_principal.set(p), caller_organization_id.set(str(p.organization_id))
    trace._turn.set(None)
    trace._current_step.set(None)
    trace._current_call.set(None)
    thread_scope.active_scope.set(None)
    trace.on_activity({"agent": "orchestrator", "stage": "turn", "direction": "input", "summary": "q",
                       "payload": {"message": "q"}})
    yield trace.current()
    caller_principal.reset(tp)
    caller_organization_id.reset(to)
    trace._turn.set(None)
    trace._current_call.set(None)


def _loop_tool(direction, name, run_id, **payload):
    trace.on_activity({"agent": "tool:meta", "stage": "tool", "direction": direction, "summary": name,
                       "payload": {"tool": name, "run_id": run_id, **payload}})


def _descends(t, span, ancestor_id):
    while span is not None and span.parent_id:
        if span.parent_id == ancestor_id:
            return True
        span = t.by_id.get(span.parent_id)
    return False


def test_two_tasks_at_once_each_keep_their_own_answer(traced):
    """They were paired by name, newest first: the sub-agent that finished first closed the other's span."""
    _loop_tool("input", "task", "r-comp", input={"agent": "compliance", "prompt": "certificates due in 60 days"})
    _loop_tool("input", "task", "r-wo", input={"agent": "wo_engine", "prompt": "work orders booked"})
    _loop_tool("output", "task", "r-comp", output="no certificates are due")        # the first to open ends first
    _loop_tool("output", "task", "r-wo", output="29 decisions are to raise")
    by_agent = {s.input["agent"]: s for s in traced.spans if s.kind == "tool" and s.name == "task"}
    assert by_agent["compliance"].output == "no certificates are due"
    assert by_agent["wo_engine"].output == "29 decisions are to raise"


def test_a_row_without_a_run_id_pairs_by_name_as_before(traced):
    trace.on_activity({"agent": "tool:meta", "stage": "tool", "direction": "input", "summary": "select_skill",
                       "payload": {"tool": "select_skill", "input": {"question": "q"}}})
    trace.on_activity({"agent": "tool:meta", "stage": "tool", "direction": "output", "summary": "select_skill",
                       "payload": {"tool": "select_skill", "output": {"primary_agent": "wo_engine"}}})
    s = next(x for x in traced.spans if x.name == "select_skill")
    assert not s.open and s.output == {"primary_agent": "wo_engine"}


def test_what_each_sub_agent_runs_hangs_under_its_own_task(traced):
    """Both sub-agents' tool calls were under the task that opened last."""
    _loop_tool("input", "task", "r-comp", input={"agent": "compliance", "prompt": "certificates due in 60 days"})
    _loop_tool("input", "task", "r-wo", input={"agent": "wo_engine", "prompt": "work orders booked"})

    async def sub_agent(agent, prompt, tool):
        token = trace.in_call("task", {"agent": agent, "prompt": prompt})
        try:
            await asyncio.sleep(0.01)
            trace.on_tool_open("run-" + agent, tool, {"limit": 200}, group=agent)
            await asyncio.sleep(0.02)                    # both are inside their tool at once
            trace.on_tool_close("run-" + agent, {"ok": True, "count": 0})
        finally:
            trace.out_call(token)

    async def both():
        await asyncio.gather(sub_agent("compliance", "certificates due in 60 days", "list_building_certificates"),
                             sub_agent("wo_engine", "work orders booked", "answer_from_records"))

    asyncio.run(both())
    tasks = {s.input["agent"]: s for s in traced.spans if s.kind == "tool" and s.name == "task"}
    certs = next(s for s in traced.spans if s.name == "list_building_certificates")
    records = next(s for s in traced.spans if s.name == "answer_from_records")
    assert _descends(traced, certs, tasks["compliance"].id) and not _descends(traced, certs, tasks["wo_engine"].id)
    assert _descends(traced, records, tasks["wo_engine"].id) and not _descends(traced, records, tasks["compliance"].id)


def test_two_tasks_for_the_same_agent_do_not_share_one_span(traced):
    for rid, prompt in (("r1", "open at Bishopsgate"), ("r2", "open at Ashgrove")):
        _loop_tool("input", "task", rid, input={"agent": "wo_engine", "prompt": prompt})

    async def sub_agent(prompt):
        token = trace.in_call("task", {"agent": "wo_engine", "prompt": prompt})
        try:
            await asyncio.sleep(0.01)
            trace.on_tool_open("run-" + prompt, "list_work_orders", {"q": prompt}, group="wo_engine")
            await asyncio.sleep(0.02)
            trace.on_tool_close("run-" + prompt, {"rows": []})
        finally:
            trace.out_call(token)

    async def both():
        await asyncio.gather(sub_agent("open at Bishopsgate"), sub_agent("open at Ashgrove"))

    asyncio.run(both())
    groups = [s for s in traced.spans if s.kind == "agent" and s.name == "wo_engine"]
    assert len(groups) == 2 and len({g.parent_id for g in groups}) == 2


def test_the_loop_sends_the_run_id_with_both_rows():
    import inspect

    import src.agents.orchestrator as O

    src = inspect.getsource(O)
    assert src.count('"run_id": str(event.get("run_id") or "")') == 2


def test_the_task_tool_names_its_own_call():
    import inspect

    from src.agents import meta_tools

    body = inspect.getsource(meta_tools.task.coroutine)
    assert "in_call(\"task\"" in body and "out_call(" in body


# -- a contract that comes up for renewal is a contract question ---------------------------------

def test_the_router_is_told_whose_renewal_it_is():
    assert "CONTRACT that ends, expires or comes up for renewal" in agent_router._PROMPT


def test_the_contract_agent_is_described_as_holding_the_contract_dates():
    from src.agents.skills import routable_skills

    skill = next(s for s in routable_skills() if s.agent == "contract_performance")
    assert "renewal" in skill.description and "ends" in skill.description


def test_the_contract_agent_can_read_the_contract_register():
    import inspect

    from src.agents import meta_tools

    assert "tools=[*CONTRACT_PERFORMANCE_TOOLS, answer_from_records]" in inspect.getsource(meta_tools)


def test_an_analyst_answer_that_talks_about_tools_goes_to_the_final_step():
    import inspect

    import src.agents.orchestrator as O

    src = inspect.getsource(O)
    assert "analyst_spoke_of_tools" in src and "answer_evidence.speaks_of_tools(answer)" in src


def test_a_rolling_window_compiles_to_a_date_range_on_the_column():
    """The filter the planner could not write on 12 Nov: contracts ending within the next 90 days."""
    from src.agents.ontology_qa import Params, compile_condition

    class _Concept:
        table = "vendor_contracts"

        def col(self, attribute):
            return attribute

    class _Onto:
        col_types = {("vendor_contracts", "contract_end"): "date"}

    sql = compile_condition(_Onto(), _Concept(), "f", {"attribute": "contract_end", "within": "next_90_days"}, Params())
    assert "CURRENT_DATE" in sql and "INTERVAL '91 day'" in sql and ">=" in sql and "<" in sql
    with pytest.raises(ontology_qa.OntologyError) as exc:
        compile_condition(_Onto(), _Concept(), "f", {"attribute": "contract_end", "within": "soonish"}, Params())
    assert "next_<N>_days" in str(exc.value)
