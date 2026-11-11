"""Scores on traces (11 Nov 2026, task plan B4): a verdict, a score and a comment on one trace at one of three levels
(logic, ux, domain), from a person or from an evaluator, and what follows from them: the current score, the filters
of the sessions list, and two run tags compared side by side.

The rules that matter: a person's score is the current one whatever an evaluator says later; nothing is overwritten
(a second opinion is a new row); the domain level is scored by a domain expert; and a session fails a level when any
of its turns does. Skips without TRACE_LAB_TEST_DB_URL."""
from __future__ import annotations

import json
from datetime import timedelta

import pytest
from sqlalchemy import text

from eval_fixtures import ORG, OTHER_ORG, T0, member, seed_score, seed_session
from tl_scratch import lab_scratch, requires_scratch  # noqa: F401
from trace_lab import eval_store, scores
from trace_lab.review_errors import Forbidden, Invalid, NotFound

pytestmark = requires_scratch

SID = "scn__baseline_v0__MT-001__1"


async def _scenario(lab, scenario_id: str, kind: str = "single_domain") -> None:
    async with lab.begin() as c:
        await c.execute(text(
            "INSERT INTO tracelab.scenarios (scenario_id, version, type, spec_sections, turns, content_sha256, status,"
            " source) VALUES (:id, 1, :k, ARRAY['output_format'], CAST(:t AS jsonb), repeat('a', 64), 'approved',"
            " 'import')"), {"id": scenario_id, "k": kind,
                            "t": json.dumps([{"message": "q", "expected": {"answer_format": "success"}}])})


async def _rows(lab) -> list[dict]:
    async with lab.connect() as c:
        return [dict(r) for r in (await c.execute(text(
            "SELECT name, source, string_value, value, comment, author_email, author_role FROM tracelab.scores"
            " ORDER BY created_at, id"))).mappings().all()]


async def test_a_person_scores_a_trace_with_a_verdict_a_score_and_a_comment(lab_scratch):
    await seed_session(lab_scratch)
    out = await scores.annotate(member(), "turn-1", "ux", verdict="fail", value=0.25, comment="  Too long to read.  ")
    assert out["verdict"] == "fail" and out["value"] == 0.25 and out["comment"] == "Too long to read."
    assert out["source"] == "ANNOTATION" and out["author_email"] == "reviewer@example.com"
    rows = await _rows(lab_scratch)
    assert len(rows) == 1 and rows[0]["author_role"] == "platform" and float(rows[0]["value"]) == 0.25


async def test_a_persons_score_is_the_current_one_whatever_an_evaluator_says_later(lab_scratch):
    await seed_session(lab_scratch)
    await seed_score(lab_scratch, "turn-1", name="ux", verdict="pass")
    await scores.annotate(member(), "turn-1", "ux", verdict="fail", value=0.5, comment="Missed the second part.")
    await seed_score(lab_scratch, "turn-1", name="ux", verdict="pass")                    # a later automatic pass
    async with lab_scratch.connect() as c:
        current = await scores.current_scores(c, ["turn-1", "turn-2"])
    assert current["turn-1"]["ux"]["verdict"] == "fail" and current["turn-1"]["ux"]["source"] == "ANNOTATION"
    assert current["turn-1"]["logic"] is None and current["turn-2"] == {"logic": None, "ux": None, "domain": None}


async def test_scoring_again_keeps_both_rows_and_shows_the_newer(lab_scratch):
    await seed_session(lab_scratch)
    await scores.annotate(member(), "turn-1", "logic", verdict="fail", value=0, comment="Wrong tool.")
    await scores.annotate(member(), "turn-1", "logic", verdict="pass", value=1, comment="I misread it.")
    assert [r["string_value"] for r in await _rows(lab_scratch)] == ["fail", "pass"]
    got = await eval_store.get_session(member(), SID)
    first = got["traces"][0]
    assert first["scores"]["logic"]["verdict"] == "pass" and first["scores"]["logic"]["comment"] == "I misread it."
    assert [h["verdict"] for h in first["score_history"]] == ["pass", "fail"], "newest first, nothing lost"


async def test_the_domain_level_is_scored_by_a_domain_expert_the_platform_team_or_the_consultant(lab_scratch):
    await seed_session(lab_scratch)
    tester = member(org=ORG, all_companies=False, role="tester", manages=False)
    with pytest.raises(Forbidden) as e:
        await scores.annotate(tester, "turn-1", "domain", verdict="pass", value=1, comment=None)
    assert e.value.reason == "domain_experts_only"
    await scores.annotate(tester, "turn-1", "ux", verdict="pass", value=1, comment=None)       # other levels: yes
    expert = member(org=ORG, all_companies=False, role="domain_expert", manages=False)
    out = await scores.annotate(expert, "turn-1", "domain", verdict="fail", value=0.2, comment="Wrong regulation.")
    assert out["author_role"] == "domain_expert"
    await scores.annotate(member(role="consultant"), "turn-1", "domain", verdict="pass", value=1, comment=None)


@pytest.mark.parametrize("kw, reason", [
    ({"level": "style", "verdict": "pass", "value": 1}, "bad_level"),
    ({"level": "ux", "verdict": "maybe", "value": 1}, "bad_verdict"),
    ({"level": "ux", "verdict": "pass", "value": 1.5}, "bad_value"),
    ({"level": "ux", "verdict": "pass", "value": -0.1}, "bad_value"),
])
async def test_a_score_outside_the_rules_is_refused(lab_scratch, kw, reason):
    await seed_session(lab_scratch)
    with pytest.raises(Invalid) as e:
        await scores.annotate(member(), "turn-1", kw["level"], verdict=kw["verdict"], value=kw["value"], comment=None)
    assert e.value.reason == reason


async def test_another_company_cannot_score_or_see_the_trace(lab_scratch):
    await seed_session(lab_scratch)
    outsider = member(org=OTHER_ORG, all_companies=False, role="admin")
    with pytest.raises(NotFound):
        await scores.annotate(outsider, "turn-1", "ux", verdict="pass", value=1, comment=None)
    with pytest.raises(NotFound):
        await scores.annotate(member(), "no-such-trace", "ux", verdict="pass", value=1, comment=None)


def test_a_session_fails_a_level_when_any_turn_does_and_passes_when_every_turn_does():
    p, f, n = {"verdict": "pass"}, {"verdict": "fail"}, {"verdict": "not_evaluated"}
    assert scores.session_verdict([p, p]) == "pass"
    assert scores.session_verdict([p, f]) == "fail"
    assert scores.session_verdict([p, None]) == "not_evaluated"
    assert scores.session_verdict([p, n]) == "not_evaluated"
    assert scores.session_verdict([]) == "not_evaluated"


async def test_the_list_shows_each_sessions_verdict_per_level_and_who_gave_it(lab_scratch):
    await seed_session(lab_scratch)                                                   # 2 traces
    await seed_score(lab_scratch, "turn-1", name="logic", verdict="pass")
    await seed_score(lab_scratch, "turn-2", name="logic", verdict="fail", value=0.5)
    await scores.annotate(member(), "turn-1", "ux", verdict="pass", value=1, comment=None)
    row = (await eval_store.list_sessions(member()))["sessions"][0]
    assert row["scores"]["logic"] == {"verdict": "fail", "source": "EVAL"}
    assert row["scores"]["ux"] == {"verdict": "not_evaluated", "source": "ANNOTATION"}, "one turn is not scored yet"
    assert row["scores"]["domain"] is None


async def test_the_list_filters_by_a_levels_verdict(lab_scratch):
    await seed_session(lab_scratch)
    await seed_session(lab_scratch, "scn__baseline_v0__SD-001__1", turn_ids=("turn-3",), at=T0 + timedelta(hours=1))
    await seed_score(lab_scratch, "turn-1", name="logic", verdict="pass")
    await seed_score(lab_scratch, "turn-2", name="logic", verdict="fail", value=0)
    await seed_score(lab_scratch, "turn-3", "scn__baseline_v0__SD-001__1", name="logic", verdict="pass")
    ids = lambda out: [r["id"] for r in out["sessions"]]                                  # noqa: E731
    assert ids(await eval_store.list_sessions(member(), level="logic", verdict="fail")) == [SID]
    assert ids(await eval_store.list_sessions(member(), level="logic", verdict="pass")) == ["scn__baseline_v0__SD-001__1"]
    assert ids(await eval_store.list_sessions(member(), level="ux", verdict="not_evaluated")) == [
        "scn__baseline_v0__SD-001__1", SID]
    with pytest.raises(Invalid):
        await eval_store.list_sessions(member(), level="style", verdict="pass")


async def test_the_list_finds_sessions_waiting_for_a_domain_expert(lab_scratch):
    await seed_session(lab_scratch)
    await seed_session(lab_scratch, "scn__baseline_v0__SD-001__1", turn_ids=("turn-3",), at=T0 + timedelta(hours=1))
    await seed_score(lab_scratch, "turn-1", name="domain", verdict="fail", value=0, details={"needs_sme": True})
    await seed_score(lab_scratch, "turn-3", "scn__baseline_v0__SD-001__1", name="domain", verdict="pass",
                     details={"needs_sme": False})
    waiting = await eval_store.list_sessions(member(), needs_sme=True)
    assert [r["id"] for r in waiting["sessions"]] == [SID]
    await scores.annotate(member(role="consultant"), "turn-1", "domain", verdict="fail", value=0, comment="Confirmed.")
    assert (await eval_store.list_sessions(member(), needs_sme=True))["sessions"] == [], "an expert has scored it"


async def test_two_run_tags_are_compared_scenario_by_scenario(lab_scratch):
    for sid in ("SD-001", "SD-002", "SD-003", "SD-004"):
        await _scenario(lab_scratch, sid)
    b, h = "baseline_v0", "postfix_v1"

    async def run(tag, scenario, turn, logic, words):
        await seed_session(lab_scratch, f"scn__{tag}__{scenario}__1", tag=tag, turn_ids=(turn,), scenario=(scenario, 1))
        await seed_score(lab_scratch, turn, f"scn__{tag}__{scenario}__1", name="logic", verdict=logic,
                         value=1 if logic == "pass" else 0)

    await run(b, "SD-001", "b1", "fail", 0); await run(h, "SD-001", "h1", "pass", 0)        # fixed
    await run(b, "SD-002", "b2", "pass", 0); await run(h, "SD-002", "h2", "fail", 0)        # regressed
    await run(b, "SD-003", "b3", "pass", 0); await run(h, "SD-003", "h3", "pass", 0)        # same
    await run(b, "SD-004", "b4", "pass", 0)                                                 # missing from head
    out = await scores.compare(member(), b, h)
    change = {r["scenario_id"]: r["change"]["logic"] for r in out["rows"]}
    assert change == {"SD-001": "fixed", "SD-002": "regressed", "SD-003": "same", "SD-004": "missing"}
    assert out["summary"]["logic"] == {"fixed": 1, "regressed": 1, "same": 1, "changed": 0, "new": 0, "missing": 1}
    assert out["summary"]["sessions"] == {"base": 4, "head": 3}
    row = next(r for r in out["rows"] if r["scenario_id"] == "SD-001")
    assert row["base"]["session_id"] == "scn__baseline_v0__SD-001__1" and row["head"]["verdicts"]["logic"] == "pass"
    assert row["base"]["answer_words"] == row["head"]["answer_words"] > 0 and row["type"] == "single_domain"
    assert out["summary"]["answer_words"]["base"] == out["summary"]["answer_words"]["head"] > 0
    assert out["base"] == b and out["head"] == h


async def test_compare_takes_the_latest_attempt_and_names_a_scenario_only_in_the_newer_run_as_new(lab_scratch):
    await _scenario(lab_scratch, "SD-001")
    await _scenario(lab_scratch, "SD-009")
    await seed_session(lab_scratch, "scn__baseline_v0__SD-001__1", turn_ids=("a1",), scenario=("SD-001", 1))
    await seed_session(lab_scratch, "scn__baseline_v0__SD-001__2", turn_ids=("a2",), scenario=("SD-001", 1), attempt=2)
    await seed_session(lab_scratch, "scn__postfix_v1__SD-009__1", tag="postfix_v1", turn_ids=("n1",),
                       scenario=("SD-009", 1))
    out = await scores.compare(member(), "baseline_v0", "postfix_v1")
    rows = {r["scenario_id"]: r for r in out["rows"]}
    assert rows["SD-001"]["base"]["session_id"] == "scn__baseline_v0__SD-001__2"
    assert rows["SD-009"]["change"]["logic"] == "new" and rows["SD-009"]["base"] is None
    with pytest.raises(Invalid):
        await scores.compare(member(), "baseline_v0", "baseline_v0")
