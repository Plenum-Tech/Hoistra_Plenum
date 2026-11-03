"""Asking a few generated questions for real: what it refuses, and what it reports.

Generation and the instance context were done; this is the asking — item 7's "run batches of
synthetic questions" and "save each question, response, tool sequence, trace, model, instance
context and timestamp".

Two refusals matter more than the happy path and are tested first:

* it will not ask a question from a process that is not isolated. The check runs BEFORE anything
  reaches a model, because the point of it is that no question is asked from a process that could
  write something.
* it will not ask a whole batch. Every question is a real chat turn with a real model and a real
  cost, so one request answers a handful; a full batch belongs to the golden runner's job and
  step loop.

And one thing it is honest about: nothing is stored. tracelab.jobs.kind has a CHECK constraint
and there is no scenario table, so keeping results needs a migration and a Trace Lab migration is
applied by a person. Every reply says `persisted: false` rather than letting a caller assume.

No model is called here: capture.run_message is replaced, so these are about the plumbing.
"""
from __future__ import annotations

from typing import Any

import pytest

from trace_lab import scenario_run


class _Captured:
    """What capture.run_message gives back, with only the fields the result reads."""

    def __init__(self, answer: str = "an answer", tools: list[str] | None = None,
                 spans: list[dict] | None = None, error: str | None = None) -> None:
        self.answer = answer
        self.tool_names = tools if tools is not None else ["list_buildings", "count_work_orders"]
        self.spans = spans if spans is not None else [
            {"id": "s0", "parent_id": None, "seq": 0, "kind": "orchestrator", "name": "routing by code",
             "agent": "orchestrator", "model": None, "latency_ms": 12, "cost_usd": 0,
             "input": {"working_set": None}, "output": "wo_engine", "ok": True, "error": None},
            {"id": "s1", "parent_id": "s0", "seq": 1, "kind": "model", "name": "tool decision",
             "agent": "sub-agent", "model": "gpt-x", "latency_ms": 1280, "cost_usd": 0.071,
             "input": {"messages": 2}, "output": "list_buildings", "ok": True, "error": None},
            {"id": "s2", "parent_id": "s0", "seq": 2, "kind": "output", "name": "answer",
             "agent": None, "model": None, "latency_ms": 3, "cost_usd": 0,
             "input": None, "output": "nine work orders", "ok": True, "error": None}]
        self.turn = {"turn_id": "t-1"}
        self.cost_usd = 0.01
        self.ms = 1234
        self.error = error


@pytest.fixture
def no_model(monkeypatch):
    """Isolated process, overlays loaded, and every question answered without a model."""
    seen: list[dict[str, Any]] = []

    async def fake_capture(orchestrator, **kw):
        seen.append(kw)
        return _Captured()

    async def fake_overlays():
        return 0

    monkeypatch.setattr(scenario_run.orchestrator_host, "assert_isolated", lambda: [])
    monkeypatch.setattr(scenario_run.orchestrator_host, "ensure_overlays_readonly", fake_overlays)
    monkeypatch.setattr(scenario_run.orchestrator_host, "get_orchestrator", lambda: object())
    monkeypatch.setattr(scenario_run.orchestrator_host, "release_session", lambda sid: None)
    monkeypatch.setattr(scenario_run.capture, "run_message", fake_capture)
    return seen


# -- what it refuses -------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_it_will_not_ask_from_a_process_that_is_not_isolated(monkeypatch):
    monkeypatch.setattr(scenario_run.orchestrator_host, "assert_isolated",
                        lambda: ["the trace store is ready", "a writer points at a live url"])

    async def never(*a, **k):                     # pragma: no cover - must not be reached
        raise AssertionError("a question was asked from an unsafe process")

    monkeypatch.setattr(scenario_run.capture, "run_message", never)
    with pytest.raises(scenario_run.NotSafe) as exc:
        await scenario_run.run("ops", count=1)
    assert "trace store" in str(exc.value)
    assert exc.value.problems


@pytest.mark.asyncio
async def test_the_isolation_check_comes_before_the_overlays_are_touched(monkeypatch):
    """Anything done before the check is work done in a process that may not be safe."""
    touched = []

    async def overlays():
        touched.append("overlays")
        return 0

    monkeypatch.setattr(scenario_run.orchestrator_host, "assert_isolated", lambda: ["unsafe"])
    monkeypatch.setattr(scenario_run.orchestrator_host, "ensure_overlays_readonly", overlays)
    with pytest.raises(scenario_run.NotSafe):
        await scenario_run.run("ops", count=1)
    assert touched == []


@pytest.mark.asyncio
async def test_a_whole_batch_is_refused(no_model):
    with pytest.raises(ValueError):
        await scenario_run.run("ops", count=scenario_run.MAX_QUESTIONS + 1)


@pytest.mark.asyncio
async def test_the_cap_is_small_enough_to_be_a_look(no_model):
    assert scenario_run.MAX_QUESTIONS <= 5


# -- what it asks -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_it_asks_the_number_of_questions_wanted(no_model):
    out = await scenario_run.run("ops", count=2, seed=1)
    assert out["asked"] == 2 and len(out["results"]) == 2
    assert len(no_model) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("space", ["ops", "comp", "assets", "full"])
async def test_every_question_is_asked_in_the_space_the_batch_names(no_model, space):
    await scenario_run.run(space, count=2, seed=1)
    assert [kw["space"] for kw in no_model] == [space, space], (
        "a question asked with the wrong space measures a different configuration")


@pytest.mark.asyncio
async def test_each_question_gets_its_own_session(no_model):
    await scenario_run.run("ops", count=3, seed=1)
    sids = [kw["session_id"] for kw in no_model]
    assert len(set(sids)) == 3, "two questions sharing a session would carry each other's context"
    assert all(s.startswith("scenario-ops-") for s in sids), sids


@pytest.mark.asyncio
async def test_the_session_is_released_even_when_a_question_fails(monkeypatch):
    released: list[str] = []

    async def boom(orchestrator, **kw):
        raise RuntimeError("the model refused")

    monkeypatch.setattr(scenario_run.orchestrator_host, "assert_isolated", lambda: [])
    monkeypatch.setattr(scenario_run.orchestrator_host, "ensure_overlays_readonly",
                        lambda: _done())
    monkeypatch.setattr(scenario_run.orchestrator_host, "get_orchestrator", lambda: object())
    monkeypatch.setattr(scenario_run.orchestrator_host, "release_session", released.append)
    monkeypatch.setattr(scenario_run.capture, "run_message", boom)
    out = await scenario_run.run("ops", count=2, seed=1)
    assert len(released) == 2, "a failed question leaked its session"
    assert all(r["ok"] is False and r["error"] for r in out["results"])


async def _done():
    return 0


@pytest.mark.asyncio
async def test_one_failed_question_does_not_end_the_run(monkeypatch, no_model):
    calls = {"n": 0}

    async def every_other(orchestrator, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("timed out")
        return _Captured()

    monkeypatch.setattr(scenario_run.capture, "run_message", every_other)
    out = await scenario_run.run("ops", count=3, seed=1)
    assert out["asked"] == 3
    assert [r["ok"] for r in out["results"]] == [False, True, True]


# -- what it reports --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_result_carries_everything_the_feedback_named(no_model):
    out = await scenario_run.run("ops", count=1, seed=1)
    r = out["results"][0]
    for field in ("question", "answer", "tools", "models", "space", "asked_at", "turn_id", "ms"):
        assert field in r, field
    assert r["question"] and r["answer"]
    assert r["tools"] == ["list_buildings", "count_work_orders"]
    assert r["models"] == ["gpt-x"]
    assert r["space"] == "ops"
    assert r["asked_at"].endswith("+00:00")


@pytest.mark.asyncio
async def test_the_tools_are_a_sequence_not_a_set(no_model):
    """The order they were called in is the thing worth reading."""
    out = await scenario_run.run("ops", count=1, seed=1)
    assert isinstance(out["results"][0]["tools"], list)
    assert out["results"][0]["tool_count"] == 2


@pytest.mark.asyncio
async def test_it_says_that_nothing_was_stored(no_model):
    out = await scenario_run.run("ops", count=1, seed=1)
    assert out["persisted"] is False, (
        "a caller that assumes these were saved will look for them and find nothing")


@pytest.mark.asyncio
async def test_the_cost_of_the_run_is_added_up(no_model):
    out = await scenario_run.run("ops", count=3, seed=1)
    assert out["cost_usd"] == pytest.approx(0.03)


# -- the one thing that can be judged without an expected answer ---------------------------------

@pytest.mark.asyncio
async def test_an_in_module_question_is_not_graded(no_model):
    out = await scenario_run.run("ops", count=2, seed=1)
    assert all(r["gate_ok"] is None for r in out["results"] if r["in_module"]), (
        "there is no expected answer, so there is nothing to grade against")


@pytest.mark.asyncio
async def test_a_question_outside_the_instance_is_expected_to_be_refused(monkeypatch, no_model):
    async def refuses(orchestrator, **kw):
        return _Captured(answer="Compliance is not part of Ops Brain. Try the Compliance Engine.")

    monkeypatch.setattr(scenario_run.capture, "run_message", refuses)
    out = await scenario_run.run("ops", count=4, seed=5, challenge_ratio=0.5)
    outside = [r for r in out["results"] if not r["in_module"]]
    assert outside, "no out-of-module questions were produced"
    assert all(r["gate_ok"] is True for r in outside)


@pytest.mark.asyncio
async def test_an_answered_out_of_module_question_is_reported_as_a_gate_failure(monkeypatch, no_model):
    """The failure this check exists for: the space answered something it does not have."""
    async def answers_anyway(orchestrator, **kw):
        return _Captured(answer="Here are the 8 certificates for that building.")

    monkeypatch.setattr(scenario_run.capture, "run_message", answers_anyway)
    out = await scenario_run.run("ops", count=4, seed=5, challenge_ratio=0.5)
    outside = [r for r in out["results"] if not r["in_module"]]
    assert outside and all(r["gate_ok"] is False for r in outside)


# -- the blocks the review page draws -------------------------------------------------------
#
# These read source, not a database: they say which span fields the USER / ORCHESTRATOR /
# MODEL / OUTPUT blocks are drawn from, so a capture that trimmed one is caught here rather
# than as a blank step on screen. They live beside the run rather than beside the store
# because importing capture pulls svc-deepagents in, which disturbs the scratch fixture.

def test_a_captured_span_carries_what_the_blocks_are_drawn_from():
    """The USER / ORCHESTRATOR / MODEL / OUTPUT blocks and their payloads come from the span
    fields, so a capture that dropped one would give a question with blank steps. capture.py
    builds these rows to the agent_trace_spans shape on purpose; this says which fields the
    review page actually needs out of that shape, so a trim is caught here rather than on screen.
    """
    import inspect

    from trace_lab import capture

    src = inspect.getsource(capture.span_rows)
    for field in ("kind", "name", "agent", "model", "parent_id", "seq",
                  "input", "output", "latency_ms", "cost_usd", "ok", "error"):
        assert f'"{field}"' in src, (
            "a captured span has no %s, so the step block that shows it is blank" % field)


def test_the_answer_itself_comes_from_the_orchestrator_chat():
    """Not a replica of it: capture drives orchestrator.stream(), the path the chat UI uses."""
    import inspect

    from trace_lab import capture

    src = inspect.getsource(capture)
    assert "orchestrator.stream(" in src
    assert "replay_guard.REPLAY is never set" in src or "replay_guard" in src


# -- the steps the page draws ---------------------------------------------------------------

@pytest.mark.asyncio
async def test_each_answer_carries_its_steps(no_model):
    """So a batch can be read before it is saved: without them the page has an answer and no
    account of how it was reached."""
    out = await scenario_run.run("ops", count=1, seed=1)
    steps = out["results"][0]["steps"]
    assert [s["kind"] for s in steps] == ["orchestrator", "model", "output"]
    assert steps[1]["model"] == "gpt-x" and steps[1]["ms"] == 1280


@pytest.mark.asyncio
async def test_a_step_says_what_it_was_given_and_what_it_returned(no_model):
    out = await scenario_run.run("ops", count=1, seed=1)
    model_step = next(s for s in out["results"][0]["steps"] if s["kind"] == "model")
    assert "messages" in model_step["input"], "the model input is what 'prompt assembled' shows"
    assert model_step["output"] == "list_buildings"


@pytest.mark.asyncio
async def test_the_tree_keeps_its_shape(no_model):
    """parent_id is what makes a sub-step sit under the step that called it."""
    out = await scenario_run.run("ops", count=1, seed=1)
    steps = out["results"][0]["steps"]
    assert steps[0]["parent_id"] is None
    assert steps[1]["parent_id"] == steps[0]["id"]


@pytest.mark.asyncio
async def test_a_huge_payload_is_cut_rather_than_sent_whole(monkeypatch, no_model):
    """A model input can be tens of thousands of characters and this travels in an HTTP reply."""
    async def huge(orchestrator, **kw):
        return _Captured(spans=[{"id": "s0", "parent_id": None, "seq": 0, "kind": "model",
                                 "name": "big", "agent": None, "model": "gpt-x", "latency_ms": 1,
                                 "cost_usd": 0, "input": "x" * 50000, "output": None,
                                 "ok": True, "error": None}])
    monkeypatch.setattr(scenario_run.capture, "run_message", huge)
    out = await scenario_run.run("ops", count=1, seed=1)
    given = out["results"][0]["steps"][0]["input"]
    assert len(given) <= scenario_run.PAYLOAD_CHARS and given.endswith("…")


@pytest.mark.asyncio
async def test_a_run_with_more_steps_than_fit_is_capped(monkeypatch, no_model):
    async def many(orchestrator, **kw):
        return _Captured(spans=[{"id": f"s{i}", "parent_id": None, "seq": i, "kind": "model",
                                 "name": f"step {i}", "agent": None, "model": None,
                                 "latency_ms": 1, "cost_usd": 0, "input": None, "output": None,
                                 "ok": True, "error": None} for i in range(200)])
    monkeypatch.setattr(scenario_run.capture, "run_message", many)
    out = await scenario_run.run("ops", count=1, seed=1)
    assert len(out["results"][0]["steps"]) == scenario_run.MAX_STEPS
