"""No sub-agent's text reaches the user as the answer (Workstream A1, 11 Nov 2026).

Until now only the planner's 6% of turns were synthesised from the tool results; an engine's
prose, the general loop's last message and a sub-agent's `task` reply went to the user as they
stood. Now each is a draft, and agents/final_answer.py writes the answer from what the tools
returned, traces every figure to a tool result, and rewrites at most twice.

These pin: what counts as evidence, that a draft is never evidence, the rewrite and the strict
rewrite, that a failing writer never ships an unchecked draft, the guard that names a path which
skips the step, and the engine path end to end with a fake engine.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest

from src.agents import final_answer as fa
from src.agents import trace

ROWS = {"rows": [{"building": "Kingsway House", "kwh": 41250, "cost_gbp": 6187.5}], "count": 1}


def _llm(*replies):
    """A model that answers in turn, recording each prompt."""
    seen: list[tuple[str, str]] = []
    queue = list(replies)

    async def llm(system, user, role):
        seen.append((role, user))
        if not queue:
            return replies[-1]
        r = queue.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    llm.seen = seen  # type: ignore[attr-defined]
    return llm


def _write(question, draft, results, llm, **kw):
    return asyncio.run(fa.write(question, draft, results=results, llm=llm, **kw))


# -- what is evidence ---------------------------------------------------------------------------

def test_cards_panels_and_plumbing_are_not_evidence():
    results, drafts = fa.gather([
        {"tool": "compliance_response", "output": {"kpis": []}},
        {"tool": "compliance_pipeline", "output": {"steps": []}},
        {"tool": "phase2_engine:energy_intelligence", "output": {"bound_tools": []}},
        {"tool": "write_todos", "output": "ok"},
        {"tool": "list_energy_buildings", "output": ROWS},
    ])
    assert [r["tool"] for r in results.values()] == ["list_energy_buildings"]
    assert drafts == []


def test_a_sub_agents_reply_is_a_draft_not_evidence():
    results, drafts = fa.gather([{"tool": "task", "output": "Kingsway used 99,999 kWh."},
                                 {"tool": "list_energy_buildings", "output": ROWS}])
    assert drafts == ["Kingsway used 99,999 kWh."]
    assert all(r["tool"] != "task" for r in results.values())


@pytest.mark.parametrize("output,error", [
    ({"ok": False, "error": "timeout"}, None),
    ({"error": "forbidden", "status_code": 403}, None),
    ({"rows": []}, "boom"),
])
def test_a_tool_that_answered_with_an_error_is_a_failed_result(output, error):
    """Before, a tool returning {ok: false} without raising reached the writer as a success."""
    results, _ = fa.gather([{"tool": "list_work_orders", "output": output, "error": error}])
    (r,) = results.values()
    assert r["ok"] is False and r["output"] is None and r["error"]


def test_the_same_call_seen_by_two_listeners_is_kept_once():
    call = {"tool": "list_energy_buildings", "output": ROWS}
    results, _ = fa.gather([call], [dict(call)])
    assert len(results) == 1


# -- writing and checking -----------------------------------------------------------------------

def _evidence():
    return fa.gather([{"tool": "list_energy_buildings", "output": ROWS}])[0]


def test_an_answer_whose_figures_trace_ships_first_time():
    llm = _llm("Kingsway House used **41,250 kWh** (£6,187.5).")
    answer, report = _write("How much did Kingsway use?", "draft", _evidence(), llm)
    assert answer.startswith("Kingsway House used")
    assert report["source"] == "synthesis" and report["rewrites"] == 0 and report["unmatched"] == []
    assert len(llm.seen) == 1


def test_the_draft_is_shown_to_the_writer_but_never_counts_as_evidence():
    """A figure only the draft holds is untraced: copying the draft does not make it true."""
    llm = _llm("Kingsway House used 99,999 kWh.", "Kingsway House used 41,250 kWh.")
    answer, report = _write("How much did Kingsway use?", "Kingsway House used 99,999 kWh.", _evidence(), llm)
    assert "DRAFT (not evidence)" in llm.seen[0][1] and "99,999" in llm.seen[0][1]
    assert answer == "Kingsway House used 41,250 kWh."
    assert report["rewrites"] == 1 and report["unmatched"] == []
    assert "99,999" in llm.seen[1][1]  # the rewrite was told which figure did not trace


def test_a_figure_that_survives_the_rewrite_gets_the_strict_rewrite():
    llm = _llm("It used 99,999 kWh.", "It used 99,999 kWh.", "Kingsway House used 41,250 kWh.")
    answer, report = _write("How much?", "", _evidence(), llm)
    assert report["rewrites"] == 2
    assert "STILL NOT FOUND" in llm.seen[2][1]
    assert answer == "Kingsway House used 41,250 kWh."


def test_a_rewrite_that_is_worse_is_not_kept():
    llm = _llm("It used 99,999 kWh.", "It used 99,999 kWh and 88,888 kWh.", "It used 99,999 kWh and 77,777 kWh.")
    answer, report = _write("How much?", "", _evidence(), llm)
    assert answer == "It used 99,999 kWh."
    assert report["unmatched"] == ["99,999"]


def test_the_previous_answer_is_evidence_for_a_follow_up():
    llm = _llm("Of the 12 sites listed before, Kingsway House used 41,250 kWh.")
    answer, report = _write("and which of those used most?", "", _evidence(), llm,
                            previous="12 sites are over their pack.")
    assert report["unmatched"] == [] and report["rewrites"] == 0


def test_a_failed_lookup_is_named_to_the_writer():
    results, _ = fa.gather([{"tool": "list_work_orders", "output": {"ok": False, "error": "timeout"}},
                            {"tool": "list_energy_buildings", "output": ROWS}])
    llm = _llm("Kingsway House used 41,250 kWh. The work-order lookup failed.")
    _write("energy and open work orders?", "", results, llm)
    prompt = llm.seen[0][1]
    failed = prompt.split("\nFAILED\n", 1)[1]
    assert '"list_work_orders" - TOOL_FAILED: The lookup took too long' in failed
    assert "Next step for the user: Ask again in a moment." in failed


# -- when the writer fails ----------------------------------------------------------------------

def test_a_failing_writer_ships_the_draft_only_when_it_passes_the_checks():
    answer, report = _write("How much?", "Kingsway House used 41,250 kWh.", _evidence(), _llm(RuntimeError("down")))
    assert answer == "Kingsway House used 41,250 kWh." and report["source"] == "draft_checked"


def test_a_failing_writer_never_ships_an_unchecked_draft():
    answer, report = _write("How much?", "Kingsway House used 99,999 kWh.", _evidence(), _llm(RuntimeError("down")))
    assert answer == fa.UNWRITTEN and report["source"] == "unwritten"
    assert "down" not in answer  # no exception text in front of the person


def test_the_first_writing_can_stream():
    painted: list[str] = []

    async def first(system, user, role):
        painted.append("streamed")
        return "Kingsway House used 41,250 kWh."

    answer, _ = _write("How much?", "", _evidence(), _llm("not used"), first=first)
    assert painted == ["streamed"] and answer == "Kingsway House used 41,250 kWh."


# -- the guard ----------------------------------------------------------------------------------

def test_an_answer_that_read_records_and_skipped_the_step_is_named():
    calls = [{"tool": "list_energy_buildings", "output": ROWS}]
    assert fa.audit("Kingsway used 41,250 kWh.", calls, None, where="test") is True
    assert fa.audit("Kingsway used 41,250 kWh.", calls, "synthesis", where="test") is False
    for exempt in fa.EXEMPT:
        assert fa.audit("x", calls, exempt, where="test") is False


def test_a_reply_that_read_nothing_is_not_flagged():
    assert fa.audit("Which area do you mean?", [], None, where="test") is False
    assert fa.audit("Laid out.", [{"tool": "compliance_pipeline", "output": {}}], None, where="test") is False


# -- the engine path, end to end ----------------------------------------------------------------

@pytest.fixture
def engine_env(monkeypatch):
    from src.agents import llm_cost, orchestrator as orch_mod
    from src.services import chat_memories
    from src.http_client import caller_organization_id
    from src.services.principal import Principal, caller_principal

    monkeypatch.setenv("ACTIVITY_LOG_ENABLED", "false")
    p = Principal(user_id=uuid.uuid4(), email="fm@example.com", organization_id=uuid.uuid4(), role="admin",
                  can_ingest=True, building_ids=None)
    caller_principal.set(p)
    caller_organization_id.set(str(p.organization_id))
    trace._turn.set(None)
    llm_cost._ledger.set(None)

    async def no_recall(*a, **k):
        return []

    monkeypatch.setattr(chat_memories, "recall", no_recall)
    state = {"prose": "Kingsway House used 99,999 kWh, the worst in the portfolio.", "calls": [
        {"tool": "list_energy_buildings", "input": {}, "output": ROWS}]}

    async def fake_engine(engine, prompt, on_event=None):
        if on_event is not None:
            await on_event({"type": "answer_delta", "text": state["prose"], "domain": engine})
        return state["prose"], list(state["calls"])

    monkeypatch.setattr(orch_mod, "run_phase2_engine_verbose", fake_engine)
    O = orch_mod.DeepAgentOrchestrator
    o = O.__new__(O)
    prompts: list[tuple[str, str]] = []

    async def llm(system, user, role, on_delta=None):
        prompts.append((role, user))
        text = "Kingsway House used 41,250 kWh."
        if on_delta is not None:
            await on_delta(text)
        return text

    async def set_scope(*a, **k):
        return None

    o._planner_llm = llm
    o._set_turn_scope = set_scope
    yield {"o": o, "prompts": prompts, "state": state}


def _engine(env, engine="energy_intelligence", on_zone=None):
    return asyncio.run(env["o"]._invoke_phase2_engine(
        engine=engine, user_message="Which building used most energy?", session_id="s-" + uuid.uuid4().hex[:6],
        extra_context=None, on_zone=on_zone, attach_route=False))


def test_an_engines_prose_is_a_draft_the_final_step_rewrites(engine_env):
    out = _engine(engine_env)
    assert out["answer"] == "Kingsway House used 41,250 kWh."
    assert "99,999" not in out["answer"]
    assert out["answer_source"] == "synthesis"
    assert [r for r, _ in engine_env["prompts"]] == ["final_answer"]


def test_only_the_final_answer_is_painted_never_the_engines_draft(engine_env):
    painted: list[dict] = []

    async def on_zone(key, value):
        if isinstance(value, dict) and value.get("type") == "answer_delta":
            painted.append(value)

    _engine(engine_env, on_zone=on_zone)
    assert [p["text"] for p in painted] == ["Kingsway House used 41,250 kWh."]


def test_an_engine_that_read_nothing_still_goes_through_the_final_step(engine_env):
    """Nothing read means nothing to quote: the writer is told no lookup ran - and, since 12 Nov 2026,
    that nothing failed either (it had answered "the records could not be read" on such a turn)."""
    engine_env["state"]["calls"] = []
    out = _engine(engine_env)
    assert out["answer_source"] == "synthesis"
    assert "(no lookup was run for this question - nothing failed; nothing was read)" in engine_env["prompts"][0][1]
