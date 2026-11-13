"""A reviewer flags a turn's expected outcome as wrong, on a throwaway Postgres (12 Nov 2026, migration 0016).

Expected outcomes are checked while reviewing, not before asking: a reviewer reading a turn can say the expected outcome
itself is wrong (the scenario needs correcting), rather than the answer. A flagged turn is never counted against Hoistra:
it is not scored, compare leaves its scenario out, and the scenario shows that it needs correcting until a corrected
version is stored. Flags are append-only like everything here: a person's newest row is their flag, clearing is a new
row, and everyone's flags stay side by side. Skips without TRACE_LAB_TEST_DB_URL."""
from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import text

from eval_fixtures import ORG, OTHER_ORG, member, seed_score, seed_session
from tl_scratch import lab_scratch, requires_scratch  # noqa: F401
from trace_lab import eval_judges, eval_store, expected_flags, scenario_file, scores
from trace_lab.review_errors import Invalid, NotFound

pytestmark = requires_scratch

SME = member(org=ORG, all_companies=False, role="domain_expert", manages=False, user_id=str(uuid.uuid4()),
             email="sme@example.com")
QUESTION = "Which certificates expire in the next 30 days at B-301?"


def _scenario(scenario_id: str = "EV-001", *, answer: str = "A short list of certificates.") -> dict:
    expected = {"plan": [{"call_id": "c1", "domain": "compliance", "tool": None, "params": {}, "depends_on": []}],
                "failure_handling": None, "answer_format": "success", "must_include": [], "must_not_include": [],
                "answer": answer}
    return {"scenario_id": scenario_id, "type": "single_domain", "spec_sections": ["output_format"], "role": "compliance_officer",
            "space": "comp", "scope": {}, "domain_rules": [], "turns": [{"message": QUESTION, "expected": expected}]}


async def _asked(lab, scenario_id: str = "EV-001", tag: str = "baseline_v0", turn: str = "turn-1") -> str:
    await scenario_file.save_scenarios(member(), [_scenario(scenario_id)], source="import")
    sid = f"scn__{tag}__{scenario_id}__1"
    await seed_session(lab, sid, tag=tag, turn_ids=(turn,), scenario=(scenario_id, 1))
    return sid


async def test_a_reviewer_flags_a_turns_expected_outcome_and_can_clear_it(lab_scratch):
    await _asked(lab_scratch)
    out = await expected_flags.set_flag(member(), "turn-1", flagged=True, comment="Drafts should be listed too.")
    assert {k: out[k] for k in ("trace_id", "flagged")} == {"trace_id": "turn-1", "flagged": True}
    got = await expected_flags.flags_of(member(), "turn-1")
    assert got["flagged"] is True and got["mine"] is True
    assert [(f["author_email"], f["comment"], f["mine"]) for f in got["flags"]] == [
        ("reviewer@example.com", "Drafts should be listed too.", True)]
    await expected_flags.set_flag(member(), "turn-1", flagged=False)
    got = await expected_flags.flags_of(member(), "turn-1")
    assert (got["flagged"], got["mine"], got["flags"]) == (False, False, [])
    async with lab_scratch.connect() as c:
        assert (await c.execute(text("SELECT count(*) FROM tracelab.expected_flags"))).scalar_one() == 2, "append-only"


async def test_each_persons_newest_flag_counts_and_one_flag_is_enough(lab_scratch):
    await _asked(lab_scratch)
    await expected_flags.set_flag(member(), "turn-1", flagged=True, comment="Wrong band.")
    await expected_flags.set_flag(SME, "turn-1", flagged=True, comment="Agreed.")
    await expected_flags.set_flag(member(), "turn-1", flagged=False)
    got = await expected_flags.flags_of(member(), "turn-1")
    assert got["flagged"] is True and got["mine"] is False, "the expert still flags it"
    assert [f["author_email"] for f in got["flags"]] == ["sme@example.com"]


async def test_a_flag_needs_a_trace_the_person_may_see_and_a_short_comment(lab_scratch):
    await _asked(lab_scratch)
    outsider = member(org=OTHER_ORG, all_companies=False, role="admin")
    with pytest.raises(NotFound):
        await expected_flags.set_flag(outsider, "turn-1", flagged=True)
    with pytest.raises(NotFound):
        await expected_flags.flags_of(outsider, "turn-1")
    with pytest.raises(Invalid):
        await expected_flags.set_flag(member(), "turn-1", flagged=True, comment="x" * 2001)
    await expected_flags.set_flag(SME, "turn-1", flagged=True)
    assert (await expected_flags.flags_of(SME, "turn-1"))["flagged"] is True, "a company's reviewer flags its own"


async def test_the_session_and_its_list_row_say_which_turns_are_flagged(lab_scratch):
    sid = await _asked(lab_scratch)
    await expected_flags.set_flag(SME, "turn-1", flagged=True, comment="Wrong.")
    got = await eval_store.get_session(member(), sid)
    assert got["traces"][0]["expected_flag"] == {"flagged": True, "count": 1, "mine": False}
    listed = (await eval_store.list_sessions(member()))["sessions"]
    assert [(r["id"], r["expected_flagged"]) for r in listed] == [(sid, 1)]


async def test_a_scenario_shows_it_needs_correcting_until_a_corrected_version_is_stored(lab_scratch):
    await _asked(lab_scratch)
    await expected_flags.set_flag(SME, "turn-1", flagged=True)
    rows = {r["scenario_id"]: r for r in await scenario_file.list_scenarios(member())}
    assert rows["EV-001"]["expected_flagged"] == 1
    await scenario_file.save_scenarios(member(), [_scenario(answer="A short list, drafts included.")], source="import")
    rows = {r["scenario_id"]: r for r in await scenario_file.list_scenarios(member())}
    assert (rows["EV-001"]["version"], rows["EV-001"]["expected_flagged"]) == (2, 0), "the corrected version starts clean"


async def test_a_flagged_turn_is_not_scored(lab_scratch, monkeypatch):
    calls = []

    async def judge(system, user, schema, *, role, max_tokens):
        calls.append(role)
        return {}, {"usd": 0.01}

    monkeypatch.setattr(eval_judges, "ASK", judge)
    await _asked(lab_scratch)
    await expected_flags.set_flag(member(), "turn-1", flagged=True)
    out = await eval_judges.evaluate_run(member(), "baseline_v0", levels=("logic", "ux"))
    assert out["not_evaluated"] == {"logic": 1, "ux": 1} and calls == []
    async with lab_scratch.connect() as c:
        reasons = (await c.execute(text("SELECT details->>'reason' FROM tracelab.scores"))).scalars().all()
    assert set(reasons) == {"expected_flagged"}


async def test_compare_leaves_out_a_scenario_whose_expected_outcome_is_flagged(lab_scratch):
    for sid, tag, turn, verdict in (("SD-001", "baseline_v0", "b1", "fail"), ("SD-001", "postfix_v1", "h1", "pass"),
                                    ("SD-002", "baseline_v0", "b2", "fail"), ("SD-002", "postfix_v1", "h2", "pass")):
        if tag == "baseline_v0":
            await scenario_file.save_scenarios(member(), [_scenario(sid)], source="import")
        session = f"scn__{tag}__{sid}__1"
        await seed_session(lab_scratch, session, tag=tag, turn_ids=(turn,), scenario=(sid, 1))
        await seed_score(lab_scratch, turn, session, name="logic", verdict=verdict, value=1 if verdict == "pass" else 0)
    await expected_flags.set_flag(SME, "h2", flagged=True, comment="The expected list is wrong.")
    out = await scores.compare(member(), "baseline_v0", "postfix_v1")
    change = {r["scenario_id"]: r["change"]["logic"] for r in out["rows"]}
    assert change == {"SD-001": "fixed", "SD-002": "not_counted"}
    assert out["summary"]["logic"]["fixed"] == 1 and out["summary"]["logic"]["not_counted"] == 1
    assert next(r for r in out["rows"] if r["scenario_id"] == "SD-002")["expected_flagged"] is True
