"""Failure-mode labels on a session's traces, on a throwaway Postgres (11 Nov 2026).

Runs are gone from Trace Lab: a person labels the turn they are reading on the session page. Each label says whether a
failure mode is there (positive) or not (negative) in one trace, from the same list of failure modes the Labels page
uses. Like every other table here it is append-only: a person's newest value is their value, clearing is a new row, and
everyone's values stay side by side. Skips without TRACE_LAB_TEST_DB_URL."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from eval_fixtures import ORG, OTHER_ORG, member, seed_session
from tl_scratch import lab_scratch, requires_scratch  # noqa: F401
from trace_lab import runs, trace_labels
from trace_lab.review_errors import Invalid, NotFound

pytestmark = requires_scratch

SME = member(org=ORG, all_companies=False, role="domain_expert", manages=False, user_id=str(uuid.uuid4()),
             email="sme@example.com")


async def _mode(name: str = "Wrong building") -> str:
    return (await runs.save_mode(member(), name=name, definition="The answer is about another building."))["lineage_id"]


async def _rows(lab) -> int:
    async with lab.connect() as c:
        return (await c.execute(text("SELECT count(*) FROM tracelab.trace_label_cells"))).scalar_one()


async def test_a_person_labels_a_trace_present_or_absent_per_failure_mode(lab_scratch):
    await seed_session(lab_scratch)
    lin = await _mode()
    out = await trace_labels.set_label(member(), "turn-1", mode=lin, value="positive")
    assert {k: out[k] for k in ("trace_id", "mode", "value")} == {"trace_id": "turn-1", "mode": lin, "value": "positive"}
    got = await trace_labels.labels_of(member(), "turn-1")
    assert [x["lineage_id"] for x in got["modes"]] == [lin] and got["me"] == member().user_id
    assert [{k: c[k] for k in ("value", "author_email", "mine")} for c in got["cells"][lin]] == [
        {"value": "positive", "author_email": "reviewer@example.com", "mine": True}]
    assert (await trace_labels.labels_of(member(), "turn-2"))["cells"] == {}, "a label belongs to one trace"


async def test_a_persons_newest_value_counts_and_everyone_elses_stays_beside_it(lab_scratch):
    await seed_session(lab_scratch)
    lin = await _mode()
    await trace_labels.set_label(member(), "turn-1", mode=lin, value="positive")
    await trace_labels.set_label(SME, "turn-1", mode=lin, value="positive")
    await trace_labels.set_label(member(), "turn-1", mode=lin, value="negative")
    cells = (await trace_labels.labels_of(member(), "turn-1"))["cells"][lin]
    assert [(c["author_email"], c["value"], c["mine"]) for c in cells] == [
        ("reviewer@example.com", "negative", True), ("sme@example.com", "positive", False)], "mine first"


async def test_clearing_takes_a_persons_value_away_and_keeps_every_row(lab_scratch):
    await seed_session(lab_scratch)
    lin = await _mode()
    await trace_labels.set_label(member(), "turn-1", mode=lin, value="positive")
    await trace_labels.set_label(member(), "turn-1", mode=lin, value=None)
    assert (await trace_labels.labels_of(member(), "turn-1"))["cells"] == {}
    assert await _rows(lab_scratch) == 2


async def test_a_label_needs_a_trace_the_person_may_see_a_live_mode_and_a_known_value(lab_scratch):
    await seed_session(lab_scratch)
    lin = await _mode()
    outsider = member(org=OTHER_ORG, all_companies=False, role="admin")
    with pytest.raises(NotFound):
        await trace_labels.set_label(outsider, "turn-1", mode=lin, value="positive")
    with pytest.raises(NotFound):
        await trace_labels.labels_of(outsider, "turn-1")
    with pytest.raises(NotFound):
        await trace_labels.set_label(member(), "turn-1", mode=str(uuid.uuid4()), value="positive")
    with pytest.raises(Invalid):
        await trace_labels.set_label(member(), "turn-1", mode=lin, value="maybe")
    await runs.retire_mode(member(), lin)
    with pytest.raises(NotFound):
        await trace_labels.set_label(member(), "turn-1", mode=lin, value="positive")
    assert await _rows(lab_scratch) == 0


async def test_a_company_member_labels_their_own_companys_traces(lab_scratch):
    await seed_session(lab_scratch)
    lin = await _mode()
    await trace_labels.set_label(SME, "turn-1", mode=lin, value="negative")
    assert (await trace_labels.labels_of(SME, "turn-1"))["cells"][lin][0]["value"] == "negative"


# ── the Labels page for sessions (13 Nov 2026): a run tag's sessions as one grid, each turn a row ─────────────────

def _scenario(scenario_id: str) -> dict:
    expected = {"plan": [{"call_id": "c1", "domain": "compliance", "tool": None, "params": {}, "depends_on": []}],
                "failure_handling": None, "answer_format": "success", "must_include": [], "must_not_include": [],
                "answer": "A list."}
    return {"scenario_id": scenario_id, "type": "single_domain", "spec_sections": ["output_format"],
            "role": "compliance_officer", "space": "comp", "scope": {}, "domain_rules": [],
            "turns": [{"message": "Which certificates are due?", "expected": expected},
                      {"message": "And at B-301?", "expected": expected}]}


async def _asked(lab, scenario_id: str, turn_ids: tuple[str, ...], *, tag: str = "baseline_v0", attempt: int = 1,
                 org: str = ORG) -> None:
    from trace_lab import scenario_file
    await scenario_file.save_scenarios(member(), [_scenario(scenario_id)], source="import")
    await seed_session(lab, f"scn__{tag}__{scenario_id}__{attempt}", tag=tag, turn_ids=turn_ids, attempt=attempt,
                       org=org, scenario=(scenario_id, 1))


async def test_a_run_tags_sessions_are_one_labels_grid_with_a_row_per_turn(lab_scratch):
    await _asked(lab_scratch, "MT-001", ("turn-1", "turn-2"))
    await _asked(lab_scratch, "SD-002", ("turn-3",))
    await _asked(lab_scratch, "SD-009", ("turn-9",), tag="postfix_v1")
    lin = await _mode()
    await trace_labels.set_label(SME, "turn-2", mode=lin, value="positive")
    out = await trace_labels.tag_matrix(member(), "baseline_v0")
    assert out["run"] == {"code": "baseline_v0", "title": "Sessions", "kind": "sessions"}
    assert [(q["id"], q["code"], q["session_id"]) for q in out["questions"]] == [
        ("turn-1", "MT-001 · turn 1", "scn__baseline_v0__MT-001__1"),
        ("turn-2", "MT-001 · turn 2", "scn__baseline_v0__MT-001__1"),
        ("turn-3", "SD-002 · turn 1", "scn__baseline_v0__SD-002__1")], "another run tag's sessions are not rows"
    assert out["questions"][0]["turn_ids"] == ["turn-1"] and out["questions"][0]["question"]
    assert [x["lineage_id"] for x in out["modes"]] == [lin]
    assert [(c["author_email"], c["value"], c["mine"]) for c in out["cells"]["turn-2"][lin]] == [
        ("sme@example.com", "positive", False)]
    assert out["last_updated"] and out["me"] == member().user_id


async def test_the_sessions_grid_shows_each_scenarios_latest_attempt_and_newest_values(lab_scratch):
    await _asked(lab_scratch, "MT-001", ("turn-1",))
    await _asked(lab_scratch, "MT-001", ("turn-5", "turn-6"), attempt=2)
    lin = await _mode()
    await trace_labels.set_label(member(), "turn-5", mode=lin, value="positive")
    await trace_labels.set_label(member(), "turn-5", mode=lin, value=None)
    out = await trace_labels.tag_matrix(member(), "baseline_v0")
    assert [q["id"] for q in out["questions"]] == ["turn-5", "turn-6"], "the latest attempt only, like the Sessions list"
    assert out["cells"] == {}, "a cleared value is no value"


async def test_the_sessions_grid_holds_only_the_callers_companys_sessions(lab_scratch):
    await _asked(lab_scratch, "MT-001", ("turn-1",))
    await seed_session(lab_scratch, "scn__baseline_v0__OT-001__1", turn_ids=("turn-o",), org=OTHER_ORG)
    assert [q["id"] for q in (await trace_labels.tag_matrix(SME, "baseline_v0"))["questions"]] == ["turn-1"]
    assert {q["id"] for q in (await trace_labels.tag_matrix(member(), "baseline_v0"))["questions"]} == {"turn-1", "turn-o"}
    empty = await trace_labels.tag_matrix(member(), "no_such_tag")
    assert (empty["questions"], empty["cells"], empty["last_updated"]) == ([], {}, None)
