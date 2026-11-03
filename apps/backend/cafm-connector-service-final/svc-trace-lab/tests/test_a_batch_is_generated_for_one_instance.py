"""A synthetic batch belongs to one role space, and the run says which one it was asked in.

"Provide a non-public backend endpoint for running batches of 50-100 synthetic questions. Keep
this separate from the user-facing production instance. Automatically save each question,
response, tool sequence, trace, model, instance context, and timestamp in Trace Lab."
(30 Oct 2026 feedback, item 7)

Most of that was already here: /golden-runs runs a batch behind require_reviewer, Trace Lab is
its own process with its own database and assert_isolated(), and capture.py records the answer,
the span tree, the models, the tools and the timing. INSTANCE CONTEXT was the missing one --
grepping the whole package for active_space found nothing.

It is not a label. agents/spaces.py decides which engines may answer at all, so the same question
asked in Ops Brain and in the Compliance Engine is two different measurements, and a batch asked
with no space is measuring whichever configuration the process happened to be in. Worse, a batch
generated without knowing its space contains questions the space refuses, and a refusal scored as
an answer measures the gate instead of the engines.

So: the run carries the space, capture asks in it, and generation starts from it.
"""
from __future__ import annotations

import pytest

from trace_lab import scenarios as sc


# -- generation is bounded by the space ------------------------------------------------------

@pytest.mark.parametrize("space", sc.SPACES)
def test_a_batch_only_uses_engines_its_space_has(space):
    from src.agents.spaces import allows

    for s in sc.generate(space, count=40, seed=1):
        assert allows(space, s.engine), (
            "%s generated a %s question, which %s cannot answer -- it would be refused, and a "
            "refusal scored as an answer measures the gate rather than the engines"
            % (space, s.engine, space))


@pytest.mark.parametrize("space", sc.SPACES)
def test_the_engine_recorded_is_the_one_the_real_router_picks(space):
    """The generator never guesses where a question goes; it asks and records the answer."""
    for s in sc.generate(space, count=25, seed=2):
        assert sc.routes_to(s.question) == s.engine


def test_ops_brain_never_generates_a_compliance_question():
    """The reported failure, from the generation side."""
    engines = {s.engine for s in sc.generate("ops", count=50, seed=1)}
    assert "compliance" not in engines and "energy_intelligence" not in engines


def test_the_compliance_engine_never_generates_a_work_order_question():
    engines = {s.engine for s in sc.generate("comp", count=50, seed=1)}
    assert "wo_engine" not in engines


def test_the_full_platform_can_reach_every_engine():
    engines = {s.engine for s in sc.generate("full", count=100, seed=1)}
    assert engines == set(sc.BY_ENGINE), engines


def test_maintenance_questions_come_from_the_spaces_that_have_them():
    """"Maintenance" is not a space: it is the work-order engine, which Ops Brain and the Assets
    Operating System both have and the Compliance Engine does not."""
    for space in ("ops", "assets"):
        assert "wo_engine" in sc.engines_of(space), space
    assert "wo_engine" not in sc.engines_of("comp")


# -- the size the feedback asked for ----------------------------------------------------------

@pytest.mark.parametrize("space", sc.SPACES)
@pytest.mark.parametrize("count", [50, 100])
def test_a_batch_of_fifty_to_a_hundred_is_produced_in_full(space, count):
    rows = sc.generate(space, count=count, seed=7)
    assert len(rows) == count, (
        "%s produced %d of %d: the generator gave up rather than filling the batch"
        % (space, len(rows), count))


@pytest.mark.parametrize("space", sc.SPACES)
def test_every_scenario_is_identified_and_distinct(space):
    rows = sc.generate(space, count=60, seed=4)
    ids = [s.scenario_id for s in rows]
    assert len(set(ids)) == len(ids), "duplicate scenario ids"
    assert all(s.question.strip() for s in rows)


# -- reproducible, or a failure cannot be re-run ------------------------------------------------

@pytest.mark.parametrize("space", sc.SPACES)
def test_the_same_seed_gives_the_same_batch(space):
    a = sc.batch(space, count=30, seed=11)
    b = sc.batch(space, count=30, seed=11)
    assert a["fingerprint"] == b["fingerprint"]
    assert [s["question"] for s in a["scenarios"]] == [s["question"] for s in b["scenarios"]]


@pytest.mark.parametrize("space", sc.SPACES)
def test_a_different_seed_gives_a_different_batch(space):
    assert sc.batch(space, count=30, seed=11)["fingerprint"] != \
        sc.batch(space, count=30, seed=12)["fingerprint"]


# -- the questions a space must refuse ----------------------------------------------------------

@pytest.mark.parametrize("space", ["ops", "comp", "assets"])
def test_a_challenge_scenario_is_genuinely_outside_its_space(space):
    from src.agents.spaces import allows

    rows = sc.generate(space, count=40, seed=5, challenge_ratio=0.25)
    outside = [s for s in rows if not s.in_module]
    assert outside, "no challenge scenarios were produced for " + space
    for s in outside:
        assert not allows(space, s.engine), (
            "%s is marked out-of-module but %s allows it; the gate check would pass for the "
            "wrong reason" % (s.scenario_id, space))


def test_the_full_platform_has_nothing_to_refuse():
    """It has every engine, so a challenge scenario there would be a contradiction."""
    rows = sc.generate("full", count=40, seed=5, challenge_ratio=0.25)
    assert all(s.in_module for s in rows)


def test_in_module_and_out_of_module_are_both_counted():
    b = sc.batch("ops", count=40, seed=5, challenge_ratio=0.25)
    assert b["out_of_module"] > 0
    assert b["generated"] == len(b["scenarios"])


# -- an unknown space is refused rather than silently unbounded ----------------------------------

@pytest.mark.parametrize("bad", ["", "   ", "something_new", "OPS", None])
def test_generation_refuses_a_space_it_does_not_know(bad):
    """engines_for() returns None (= every engine) for an unknown space, which is right for a
    live caller and wrong here: it would quietly generate an unbounded batch labelled `bad`."""
    with pytest.raises(ValueError):
        sc.generate(bad, count=5)


# -- the run carries the instance context --------------------------------------------------------

def test_the_run_start_takes_a_space():
    from trace_lab.routes_golden import GoldenRunEstimate

    assert "space" in GoldenRunEstimate.model_fields


@pytest.mark.parametrize("space", list(sc.SPACES))
def test_the_run_start_accepts_each_real_space(space):
    from trace_lab.routes_golden import GoldenRunStart

    assert GoldenRunStart(space=space).space == space


def test_a_run_may_still_name_no_space():
    """A batch that is deliberately unscoped stays possible; it just says so."""
    from trace_lab.routes_golden import GoldenRunStart

    assert GoldenRunStart().space is None


def test_a_space_that_is_not_real_is_refused_at_the_edge():
    from pydantic import ValidationError

    from trace_lab.routes_golden import GoldenRunStart

    with pytest.raises(ValidationError):
        GoldenRunStart(space="maintenance")


def test_the_space_is_recorded_among_the_things_that_change_an_answer():
    """like_for_like is "what can change answers between runs". Which engines may answer is
    exactly that, so Compare can say two runs differ because they were asked in different
    instances rather than leaving it to be guessed."""
    from trace_lab import runner

    out = runner.like_for_like(context_default="", set_hash="x", model="m", space="ops")
    assert out["space"] == "ops"
    assert runner.like_for_like(context_default="", set_hash="x", model="m")["space"] is None


def test_capture_asks_in_the_space_the_run_names():
    import inspect

    from trace_lab import capture

    src = inspect.getsource(capture)
    assert "spaces.active_space.set(" in src, (
        "capture never sets the role space, so every batch is asked with no space and every "
        "engine is allowed whatever the run says")
    # Set in the task with the other caller vars, so it dies with the task rather than leaking
    # into the next question of the batch.
    body = inspect.getsource(capture._run)
    assert body.index("spaces.active_space.set(") < body.index("activity_log.set_current_session")


def test_the_runner_passes_the_runs_space_to_every_question():
    import inspect

    from trace_lab import runner

    src = inspect.getsource(runner)
    assert 'space=(run.get("params") or {}).get("space")' in src, (
        "the runner does not hand the run's space to capture, so the column is recorded and "
        "then ignored -- worse than not having it")
