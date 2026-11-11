"""Evaluating a run tag at the three levels, on a throwaway Postgres (11 Nov 2026, task plan B3).

One pass writes an automatic score per trace per level: logic from the checks, user experience and domain from a
model judge (replaced here by a fake, so no model is called). It must be safe to repeat (a second pass scores nothing
new), stop at its spending limit and say what is left, never score what it cannot (no scenario, no domain rules),
and judge a follow-up turn with the turns before it. Skips without TRACE_LAB_TEST_DB_URL."""
from __future__ import annotations

import pytest
from sqlalchemy import text

from eval_fixtures import ANSWER, ORG, OTHER_ORG, T0, member, sample_turn, seed_session
from tl_scratch import lab_scratch, requires_scratch  # noqa: F401
from trace_lab import eval_judges, eval_logic, eval_store, scenario_file, spec_doc
from trace_lab.review_errors import Conflict, Forbidden, Invalid

pytestmark = requires_scratch


#: What the seeded traces were asked: a scenario's turn must ask the same.
QUESTION = "Which certificates expire in the next 30 days at B-301?"
FAULT = {"service": "ops-intelligence", "path_prefix": "/api/compliance", "fault": "timeout", "times": 1}


def _scenario(scenario_id: str = "EV-001", *, turns: int = 1, rules: bool = True, domain: str = "compliance",
              message: str = QUESTION, faults: list | None = None) -> dict:
    expected = {"plan": [{"call_id": "c1", "domain": domain, "tool": None, "params": {}, "depends_on": []}],
                "failure_handling": None, "answer_format": "success", "must_include": ["Gas Safety"],
                "must_not_include": [], "answer": None}
    return {"scenario_id": scenario_id, "type": "multi_turn" if turns > 1 else "single_domain",
            "spec_sections": ["output_format"], "role": "compliance_officer", "space": "comp",
            "scope": {"buildings": ["B-301"]},
            "domain_rules": [{"id": "R-1", "text": "A certificate past its expiry date is lapsed."}] if rules else [],
            "faults": faults or [],
            "turns": [{"message": message, "expected": expected} for i in range(turns)]}


class FakeJudge:
    """Stands in for the model: answers by role, counts its calls, and can be told to fail."""

    def __init__(self, usd: float = 0.01):
        self.calls: list[dict] = []
        self.usd = usd
        self.fail_role: str | None = None

    async def __call__(self, system, user, schema, *, role, max_tokens):
        self.calls.append({"role": role, "user": user, "system": system})
        if role == self.fail_role:
            raise RuntimeError("the model is not available")
        if role == eval_judges.UX_ROLE:
            return ({"concise": True, "complete": True, "template": "success", "failure_wording": "n/a",
                     "reason": "Short and complete."}, {"usd": self.usd, "model": "fake"})
        return ({"violations": [], "confidence": "high", "reason": "Consistent with the rules."},
                {"usd": self.usd, "model": "fake"})


@pytest.fixture
def judge(monkeypatch):
    fake = FakeJudge()
    monkeypatch.setattr(eval_judges, "ASK", fake)
    monkeypatch.setattr(eval_judges, "READ_DOC", lambda agent, name: "")
    return fake


async def _scores(lab) -> list[dict]:
    async with lab.connect() as c:
        return [dict(r) for r in (await c.execute(text(
            "SELECT trace_id, name, source, string_value, value, comment, evaluator, details::text AS details"
            " FROM tracelab.scores ORDER BY trace_id, name"))).mappings().all()]


async def _ready(lab, scenario: dict, session: str = "scn__baseline_v0__EV-001__1", turns=("turn-1",)) -> None:
    await scenario_file.save_scenarios(member(), [scenario], source="import")
    await seed_session(lab, session, turn_ids=tuple(turns), scenario=(scenario["scenario_id"], 1))


async def _seed_clean(lab, session: str = "scn__baseline_v0__EV-001__1", scenario=("EV-001", 1)) -> None:
    """A session whose one trace has no failed tool call."""
    turn, spans = sample_turn("turn-1", session)
    for span in spans:
        span.update(ok=True, error=None)
    meta = eval_store.SessionMeta(id=session, organization_id=ORG, run_tag="baseline_v0", scenario_id=scenario[0],
                                  scenario_version=scenario[1], attempt=1, origin="test", source="runner", user_id=None,
                                  email=None, role=None, space=None, started_at=T0)
    async with lab.begin() as c:
        await c.execute(text("INSERT INTO tracelab.run_tags (tag, kind, origin) VALUES ('baseline_v0', 'baseline',"
                             " 'test') ON CONFLICT DO NOTHING"))
        await eval_store.write_session(c, meta, [eval_store.rows_for(turn, spans)])


async def test_one_pass_scores_every_trace_at_every_level(lab_scratch, judge):
    await _ready(lab_scratch, _scenario())
    out = await eval_judges.evaluate_run(member(), "baseline_v0")
    assert out["scored"] == {"logic": 1, "ux": 1, "domain": 1} and out["remaining"] == 0 and out["errors"] == 0
    assert out["usd"] == pytest.approx(0.02) and out["traces"] == 1
    rows = {r["name"]: r for r in await _scores(lab_scratch)}
    assert {k: (v["source"], v["string_value"], v["evaluator"]) for k, v in rows.items()} == {
        "logic": ("EVAL", "pass", "logic-1"), "ux": ("EVAL", "pass", "ux-1"), "domain": ("EVAL", "pass", "domain-1")}
    assert float(rows["logic"]["value"]) == 1.0 and rows["ux"]["comment"] == "Short and complete."
    assert '"plan_coverage"' in rows["logic"]["details"] and '"needs_sme": false' in rows["domain"]["details"]


async def test_the_judges_are_given_the_question_the_answer_and_the_expected_outcome(lab_scratch, judge):
    await _ready(lab_scratch, _scenario())
    await eval_judges.evaluate_run(member(), "baseline_v0")
    ux = next(c for c in judge.calls if c["role"] == eval_judges.UX_ROLE)["user"]
    assert ANSWER in ux and "Which certificates expire" in ux and "success" in ux and "Gas Safety" in ux
    domain = next(c for c in judge.calls if c["role"] == eval_judges.DOMAIN_ROLE)["user"]
    assert "A certificate past its expiry date is lapsed." in domain and "list_certificates" in domain


async def test_evaluating_twice_scores_nothing_new(lab_scratch, judge):
    await _ready(lab_scratch, _scenario())
    await eval_judges.evaluate_run(member(), "baseline_v0")
    calls = len(judge.calls)
    again = await eval_judges.evaluate_run(member(), "baseline_v0")
    assert again["scored"] == {"logic": 0, "ux": 0, "domain": 0} and again["remaining"] == 0
    assert len(judge.calls) == calls and len(await _scores(lab_scratch)) == 3


async def test_only_the_levels_asked_for_are_scored(lab_scratch, judge):
    await _ready(lab_scratch, _scenario())
    out = await eval_judges.evaluate_run(member(), "baseline_v0", levels=("logic",))
    assert out["scored"] == {"logic": 1} and judge.calls == [] and out["usd"] == 0
    assert [r["name"] for r in await _scores(lab_scratch)] == ["logic"]


async def test_a_follow_up_turn_is_judged_with_the_turns_before_it(lab_scratch, judge):
    await _ready(lab_scratch, _scenario(turns=2), turns=("turn-1", "turn-2"))
    out = await eval_judges.evaluate_run(member(), "baseline_v0", levels=("ux",))
    assert out["scored"] == {"ux": 2}
    second = judge.calls[1]["user"]
    assert "Earlier in this conversation" in second and second.count(ANSWER) == 2, "turn 1's answer, then turn 2's"
    assert "Earlier in this conversation" not in judge.calls[0]["user"]


async def test_the_limit_stops_a_pass_and_says_what_is_left(lab_scratch, judge):
    await _ready(lab_scratch, _scenario(turns=2), turns=("turn-1", "turn-2"))
    out = await eval_judges.evaluate_run(member(), "baseline_v0", levels=("logic",), limit=1)
    assert out["scored"] == {"logic": 1} and out["remaining"] == 1
    rest = await eval_judges.evaluate_run(member(), "baseline_v0", levels=("logic",), limit=1)
    assert rest["scored"] == {"logic": 1} and rest["remaining"] == 0


async def test_the_spending_limit_stops_the_judges_and_says_what_is_left(lab_scratch, judge):
    judge.usd = 0.6
    await _ready(lab_scratch, _scenario(turns=2), turns=("turn-1", "turn-2"))
    out = await eval_judges.evaluate_run(member(), "baseline_v0", levels=("ux", "domain"), usd_cap=1.0)
    assert out["capped"] is True and out["usd"] == pytest.approx(1.2) and len(judge.calls) == 2
    assert out["remaining"] == 2, "the second trace's two levels are still to do"


async def test_a_session_with_no_scenario_is_not_scored_and_no_model_is_called(lab_scratch, judge):
    await seed_session(lab_scratch, "scn__baseline_v0__NOT-IMPORTED__1", turn_ids=("turn-x",))
    out = await eval_judges.evaluate_run(member(), "baseline_v0")
    assert out["scored"] == {"logic": 0, "ux": 0, "domain": 0}
    assert out["not_evaluated"] == {"logic": 1, "ux": 1, "domain": 1} and judge.calls == []
    rows = await _scores(lab_scratch)
    assert {r["string_value"] for r in rows} == {"not_evaluated"} and all('"no_scenario"' in r["details"] for r in rows)
    again = await eval_judges.evaluate_run(member(), "baseline_v0")
    assert again["not_evaluated"] == {"logic": 0, "ux": 0, "domain": 0}, "said once, not on every pass"


async def test_another_companys_scenario_is_never_used_as_the_expected_outcome(lab_scratch, judge):
    await scenario_file.save_scenarios(member(org=OTHER_ORG, all_companies=False, role="admin"), [_scenario()],
                                       source="import")
    await seed_session(lab_scratch, "scn__baseline_v0__EV-001__1", turn_ids=("turn-1",), scenario=("EV-001", 1), org=ORG)
    out = await eval_judges.evaluate_run(member(), "baseline_v0")
    assert out["scored"] == {"logic": 0, "ux": 0, "domain": 0} and judge.calls == []
    assert {r["string_value"] for r in await _scores(lab_scratch)} == {"not_evaluated"}
    assert all('"no_scenario"' in r["details"] for r in await _scores(lab_scratch))


async def test_the_domain_level_without_rules_is_not_evaluated_and_calls_no_model(lab_scratch, judge):
    await _ready(lab_scratch, _scenario(rules=False))
    out = await eval_judges.evaluate_run(member(), "baseline_v0", levels=("domain",))
    assert out["scored"] == {"domain": 0} and out["not_evaluated"] == {"domain": 1} and judge.calls == []
    assert '"no_domain_rules"' in (await _scores(lab_scratch))[0]["details"]


async def test_a_judge_that_fails_scores_nothing_and_is_counted_so_the_pass_can_be_run_again(lab_scratch, judge):
    judge.fail_role = eval_judges.UX_ROLE
    await _ready(lab_scratch, _scenario())
    out = await eval_judges.evaluate_run(member(), "baseline_v0")
    assert out["errors"] == 1 and out["scored"] == {"logic": 1, "ux": 0, "domain": 1} and out["remaining"] == 1
    judge.fail_role = None
    again = await eval_judges.evaluate_run(member(), "baseline_v0")
    assert again["scored"] == {"logic": 0, "ux": 1, "domain": 0} and again["remaining"] == 0


async def test_a_score_against_an_out_of_date_or_unapproved_expected_outcome_says_so(lab_scratch, judge):
    v1 = "# Spec\n\n## Output format\n\nFour templates.\n"
    await spec_doc.load(member(), text=v1)
    await _ready(lab_scratch, _scenario())
    await spec_doc.load(member(), text=v1.replace("Four templates.", "Four templates, and a fifth."))
    await eval_judges.evaluate_run(member(), "baseline_v0", levels=("logic",))
    details = (await _scores(lab_scratch))[0]["details"]
    assert '"stale_expected": true' in details and '"expected_status": "draft"' in details


async def test_only_the_latest_attempt_of_a_scenario_is_scored(lab_scratch, judge):
    await _ready(lab_scratch, _scenario())
    await seed_session(lab_scratch, "scn__baseline_v0__EV-001__2", turn_ids=("turn-9",), scenario=("EV-001", 1),
                       attempt=2)
    out = await eval_judges.evaluate_run(member(), "baseline_v0", levels=("logic",))
    assert out["scored"] == {"logic": 1} and [r["trace_id"] for r in await _scores(lab_scratch)] == ["turn-9"]


async def test_a_judge_that_is_down_does_not_hold_up_the_traces_after_it(lab_scratch, judge):
    judge.fail_role = eval_judges.UX_ROLE
    await _ready(lab_scratch, _scenario(turns=2), turns=("turn-1", "turn-2"))
    first = await eval_judges.evaluate_run(member(), "baseline_v0", levels=("logic", "ux"), limit=1)
    assert first["failed_traces"] == ["turn-1"] and first["scored"]["logic"] == 1
    second = await eval_judges.evaluate_run(member(), "baseline_v0", levels=("logic", "ux"), limit=1, skip=["turn-1"])
    assert second["failed_traces"] == ["turn-2"] and second["scored"]["logic"] == 1, "the next trace, not turn-1 again"
    assert {(r["trace_id"], r["name"]) for r in await _scores(lab_scratch)} == {("turn-1", "logic"), ("turn-2", "logic")}
    assert second["remaining"] == 2 and second["left_for_later"] == 1


async def test_an_evaluator_that_breaks_on_a_trace_records_why_and_the_pass_goes_on(lab_scratch, judge, monkeypatch):
    def breaks(*args, **kwargs):
        raise ValueError("a payload it did not expect")

    monkeypatch.setattr(eval_logic, "evaluate", breaks)
    await _ready(lab_scratch, _scenario(turns=2), turns=("turn-1", "turn-2"))
    out = await eval_judges.evaluate_run(member(), "baseline_v0", levels=("logic",))
    assert out["not_evaluated"] == {"logic": 2} and out["remaining"] == 0
    assert all('"evaluator_error"' in r["details"] for r in await _scores(lab_scratch))


async def test_the_spending_limit_holds_across_passes_of_a_run_tag(lab_scratch, judge, monkeypatch):
    monkeypatch.setattr(eval_judges, "RUN_TAG_BUDGET_USD", 1.0)
    judge.usd = 0.6
    await _ready(lab_scratch, _scenario(turns=2), turns=("turn-1", "turn-2"))
    first = await eval_judges.evaluate_run(member(), "baseline_v0", levels=("ux", "domain"), limit=1)
    assert first["usd"] == pytest.approx(1.2) and len(judge.calls) == 2
    second = await eval_judges.evaluate_run(member(), "baseline_v0", levels=("ux", "domain"), limit=1)
    assert second["capped"] is True and len(judge.calls) == 2, "the run tag has spent its budget"
    assert second["spent_usd"] == pytest.approx(1.2) and second["remaining"] == 2


async def test_a_judge_call_with_no_price_is_counted_at_an_estimate(lab_scratch, judge):
    judge.usd = None
    await _ready(lab_scratch, _scenario())
    out = await eval_judges.evaluate_run(member(), "baseline_v0", levels=("ux", "domain"))
    assert out["usd"] == pytest.approx(2 * eval_judges.UNPRICED_USD) and out["usd"] > 0
    assert all('"usd_estimated": true' in r["details"] for r in await _scores(lab_scratch))


async def test_only_the_platform_team_and_the_consultant_call_the_judges(lab_scratch, judge):
    await _ready(lab_scratch, _scenario())
    admin = member(org=ORG, all_companies=False, role="admin")
    with pytest.raises(Forbidden) as e:
        await eval_judges.evaluate_run(admin, "baseline_v0", levels=("logic", "ux"))
    assert e.value.reason == "platform_only"
    out = await eval_judges.evaluate_run(admin, "baseline_v0", levels=("logic",))
    assert out["scored"] == {"logic": 1} and judge.calls == []


async def test_two_passes_over_one_run_tag_never_run_at_once(lab_scratch, judge):
    await _ready(lab_scratch, _scenario())
    async with lab_scratch.connect() as other:
        await other.execute(text("SELECT pg_advisory_lock(:ns, hashtext(:tag))"),
                            {"ns": eval_judges.LOCK_NS, "tag": "baseline_v0"})
        with pytest.raises(Conflict) as e:
            await eval_judges.evaluate_run(member(), "baseline_v0", levels=("logic",))
        assert e.value.reason == "evaluation_running"
        await other.execute(text("SELECT pg_advisory_unlock(:ns, hashtext(:tag))"),
                            {"ns": eval_judges.LOCK_NS, "tag": "baseline_v0"})
    assert (await eval_judges.evaluate_run(member(), "baseline_v0", levels=("logic",)))["scored"] == {"logic": 1}
    assert (await eval_judges.evaluate_run(member(), "baseline_v0", levels=("logic",)))["traces"] == 0, "and it let go"


async def test_a_scenario_whose_fault_was_not_injected_is_not_scored(lab_scratch, judge):
    await scenario_file.save_scenarios(member(), [_scenario(faults=[FAULT])], source="import")
    await _seed_clean(lab_scratch)
    out = await eval_judges.evaluate_run(member(), "baseline_v0")
    assert out["not_evaluated"] == {"logic": 1, "ux": 1, "domain": 1} and judge.calls == []
    assert all('"fault_not_injected"' in r["details"] for r in await _scores(lab_scratch))


async def test_a_scenario_whose_fault_shows_in_the_trace_is_scored(lab_scratch, judge):
    await _ready(lab_scratch, _scenario(faults=[FAULT]))           # the seeded trace has a tool call that failed
    out = await eval_judges.evaluate_run(member(), "baseline_v0")
    assert out["scored"] == {"logic": 1, "ux": 1, "domain": 1} and len(judge.calls) == 2


async def test_a_trace_asked_another_question_than_its_turn_is_not_scored(lab_scratch, judge):
    await _ready(lab_scratch, _scenario(message="Which work orders are overdue at B-301?"))
    out = await eval_judges.evaluate_run(member(), "baseline_v0")
    assert out["not_evaluated"] == {"logic": 1, "ux": 1, "domain": 1} and judge.calls == []
    assert all('"question_differs"' in r["details"] for r in await _scores(lab_scratch))
    await _ready(lab_scratch, _scenario("EV-002", message="  which certificates EXPIRE in the next 30 days at b-301 "),
                 session="scn__baseline_v0__EV-002__1", turns=("turn-7",))
    again = await eval_judges.evaluate_run(member(), "baseline_v0", levels=("logic",))
    assert again["scored"] == {"logic": 1}, "case, spaces and the question mark do not make it another question"


async def test_evaluating_is_for_curators_and_for_known_levels(lab_scratch, judge):
    with pytest.raises(Forbidden):
        await eval_judges.evaluate_run(member(org=ORG, all_companies=False, role="tester", manages=False), "baseline_v0")
    with pytest.raises(Invalid):
        await eval_judges.evaluate_run(member(), "baseline_v0", levels=("style",))
    assert (await eval_judges.evaluate_run(member(), "no_such_tag"))["traces"] == 0
