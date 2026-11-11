"""Scenarios versioned with the specification, on a throwaway Postgres (11 Nov 2026, task plan B2.3: "a change to
the spec flags which expected outcomes are now out of date").

The flag has to be exact in both directions: a scenario that cites a changed section is flagged, one that cites only
unchanged sections is not, and nothing already stored is rewritten to make the flag go away. It goes away when a
person brings the scenario up to date: importing it again or approving it stamps the specification as it reads now.
Skips without TRACE_LAB_TEST_DB_URL."""
from __future__ import annotations

import copy
from pathlib import Path

import pytest
from sqlalchemy import text

from eval_fixtures import ORG, member, seed_session
from tl_scratch import lab_scratch, requires_scratch  # noqa: F401
from trace_lab import eval_store, scenario_file, spec_doc
from trace_lab.review_errors import Forbidden, Invalid

pytestmark = requires_scratch

SAMPLE = Path(__file__).parent / "fixtures" / "eval_scenarios_sample.jsonl"
V1 = """# Specification

## Multi-turn continuity

Carry the verified entities and scope between related turns.

## Scope preservation

Filters, roles, buildings, vendors and periods stay the same across calls.

## Partial failure

Answer from the calls that worked and say which parts failed.

## Output format

Four templates: success, partial success, denial, failure.

## Answer completeness

Every sub-question is answered, or the answer says why not.

## Multi-read planning

One entry per call.

## Dependency order

Independent calls in parallel, dependent calls in sequence.

## Context passing

Record where each value came from.
"""
V0 = V1.replace("## Partial failure\n\nAnswer from the calls that worked and say which parts failed.\n\n", "")
V2 = V1.replace("say which parts failed.", "say clearly which parts failed and what to do next.")


def _rows() -> list[dict]:
    return scenario_file.parse_jsonl(SAMPLE.read_bytes())[0]


async def _stale(m=None) -> dict[str, list[str]]:
    return {r["scenario_id"]: r["stale"] for r in await scenario_file.list_scenarios(m or member())}


async def test_loading_a_specification_stores_its_sections_once(lab_scratch):
    out = await spec_doc.load(member(), text=V1)
    assert out["sections"] == 8 and out["new"] is True and len(out["spec_version"]) == 12
    again = await spec_doc.load(member(), text=V1)
    assert again["new"] is False and again["spec_version"] == out["spec_version"] and again["changed"] == []
    async with lab_scratch.connect() as c:
        assert (await c.execute(text("SELECT count(*) FROM tracelab.spec_sections"))).scalar_one() == 8


async def test_a_new_version_says_which_sections_changed(lab_scratch):
    await spec_doc.load(member(), text=V1)
    out = await spec_doc.load(member(), text=V2)
    assert out["new"] is True and out["changed"] == ["partial_failure"]
    version, hashes = await _current(lab_scratch)
    assert version == out["spec_version"] and len(hashes) == 8


async def _current(lab):
    async with lab.connect() as c:
        return await spec_doc.current_hashes(c)


async def test_a_stored_scenario_is_stamped_with_the_sections_it_cites(lab_scratch):
    v = (await spec_doc.load(member(), text=V1))["spec_version"]
    await scenario_file.save_scenarios(member(), _rows(), source="import")
    rows = {r["scenario_id"]: r for r in await scenario_file.list_scenarios(member())}
    assert rows["FL-001"]["spec_version"] == v
    assert set(rows["FL-001"]["spec_hashes"]) == {"partial_failure", "output_format"}
    assert all(r["stale"] == [] for r in rows.values())


async def test_a_spec_change_flags_only_the_scenarios_that_cite_the_changed_section(lab_scratch):
    await spec_doc.load(member(), text=V1)
    await scenario_file.save_scenarios(member(), _rows(), source="import")
    await spec_doc.load(member(), text=V2)
    assert await _stale() == {"FL-001": ["partial_failure"], "MD-001": [], "MT-001": [], "SD-001": []}
    only = await scenario_file.list_scenarios(member(), stale=True)
    assert [r["scenario_id"] for r in only] == ["FL-001"]


async def test_storing_the_same_scenario_again_does_not_clear_the_out_of_date_flag(lab_scratch):
    await spec_doc.load(member(), text=V1)
    await scenario_file.save_scenarios(member(), _rows(), source="import")
    await spec_doc.load(member(), text=V2)
    out = await scenario_file.save_scenarios(member(), _rows(), source="import")
    assert out == {"added": 0, "new_versions": 0, "unchanged": 4}, "nobody looked at it, so it stays as it was"
    assert (await _stale())["FL-001"] == ["partial_failure"]


async def test_a_changed_expected_outcome_brings_a_scenario_up_to_date_and_the_older_version_is_kept(lab_scratch):
    await spec_doc.load(member(), text=V1)
    await scenario_file.save_scenarios(member(), _rows(), source="import")
    await spec_doc.load(member(), text=V2)
    rows = copy.deepcopy(_rows())
    next(r for r in rows if r["scenario_id"] == "FL-001")["turns"][0]["expected"]["must_include"] = ["what to do next"]
    out = await scenario_file.save_scenarios(member(), rows, source="import")
    assert out == {"added": 0, "new_versions": 1, "unchanged": 3}, "only the rewritten one gets a new version"
    assert (await _stale())["FL-001"] == []
    async with lab_scratch.connect() as c:
        versions = (await c.execute(text("SELECT version, spec_hashes->>'partial_failure' FROM tracelab.scenarios"
                                         " WHERE scenario_id = 'FL-001' ORDER BY version"))).all()
    assert len(versions) == 2 and versions[0][1] != versions[1][1], "version 1 keeps the hash it was written with"


async def test_a_section_that_arrives_after_a_scenario_was_written_flags_it(lab_scratch):
    await spec_doc.load(member(), text=V0)
    await scenario_file.save_scenarios(member(), _rows(), source="import")
    await seed_session(lab_scratch, "scn__baseline_v0__FL-001__1", turn_ids=("turn-f",), scenario=("FL-001", 1))
    assert (await _stale())["FL-001"] == [], "the specification has no such section yet: nothing to compare"
    await spec_doc.load(member(), text=V1)
    assert await _stale() == {"FL-001": ["partial_failure"], "MD-001": [], "MT-001": [], "SD-001": []}
    listed = await eval_store.list_sessions(member(), stale=True)
    assert [r["id"] for r in listed["sessions"]] == ["scn__baseline_v0__FL-001__1"]
    got = await eval_store.get_session(member(), "scn__baseline_v0__FL-001__1")
    assert got["scenario"]["stale"] == ["partial_failure"]


async def test_the_overview_names_the_cited_sections_the_specification_does_not_have(lab_scratch):
    await spec_doc.load(member(), text=V0)
    await scenario_file.save_scenarios(member(), _rows(), source="import")
    got = await spec_doc.overview(member())
    assert (got["stale_scenarios"], got["uncovered_scenarios"], got["uncovered_sections"]) == (0, 1, ["partial_failure"])
    rows = {r["scenario_id"]: r["uncovered"] for r in await scenario_file.list_scenarios(member())}
    assert rows == {"FL-001": ["partial_failure"], "MD-001": [], "MT-001": [], "SD-001": []}


async def test_a_scenario_written_before_any_specification_is_flagged_once_one_is_read(lab_scratch):
    await scenario_file.save_scenarios(member(), _rows(), source="import")
    await spec_doc.load(member(), text=V1)
    stale = await _stale()
    assert all(stale.values()) and stale["MT-001"] == ["multi_turn_continuity", "scope_preservation"]


async def test_approving_an_out_of_date_scenario_stamps_the_specification_as_it_reads_now(lab_scratch):
    await spec_doc.load(member(), text=V1)
    await scenario_file.save_scenarios(member(), _rows(), source="import")
    await spec_doc.load(member(), text=V2)
    await scenario_file.approve(member(), ["FL-001"])
    assert (await _stale())["FL-001"] == []


async def test_a_session_keeps_showing_that_the_version_it_ran_with_is_out_of_date(lab_scratch):
    await spec_doc.load(member(), text=V1)
    await scenario_file.save_scenarios(member(), _rows(), source="import")
    await seed_session(lab_scratch, "scn__baseline_v0__FL-001__1", turn_ids=("turn-f",), scenario=("FL-001", 1))
    await seed_session(lab_scratch, "scn__baseline_v0__SD-001__1", turn_ids=("turn-s",), scenario=("SD-001", 1))
    await spec_doc.load(member(), text=V2)
    await scenario_file.save_scenarios(member(), _rows(), source="import")            # FL-001 version 2, up to date
    got = await eval_store.get_session(member(), "scn__baseline_v0__FL-001__1")
    assert got["scenario"]["version"] == 1 and got["scenario"]["stale"] == ["partial_failure"]
    fresh = await eval_store.get_session(member(), "scn__baseline_v0__SD-001__1")
    assert fresh["scenario"]["stale"] == []
    listed = {r["id"]: r["stale"] for r in (await eval_store.list_sessions(member()))["sessions"]}
    assert listed == {"scn__baseline_v0__FL-001__1": True, "scn__baseline_v0__SD-001__1": False}
    only = await eval_store.list_sessions(member(), stale=True)
    assert [r["id"] for r in only["sessions"]] == ["scn__baseline_v0__FL-001__1"]


async def test_with_no_specification_loaded_nothing_is_stamped_or_flagged(lab_scratch):
    await scenario_file.save_scenarios(member(), _rows(), source="import")
    rows = await scenario_file.list_scenarios(member())
    assert all(r["spec_hashes"] == {} and r["stale"] == [] and r["spec_version"] is None for r in rows)


async def test_an_overview_says_which_version_is_current_and_how_many_scenarios_are_out_of_date(lab_scratch):
    assert (await spec_doc.overview(member()))["spec_version"] is None
    await spec_doc.load(member(), text=V1)
    await scenario_file.save_scenarios(member(), _rows(), source="import")
    await spec_doc.load(member(), text=V2)
    got = await spec_doc.overview(member())
    assert len(got["sections"]) == 8 and got["stale_scenarios"] == 1
    assert {"section_id": "partial_failure", "title": "Partial failure"}.items() <= got["sections"][2].items()


async def test_a_specification_with_no_sections_or_a_missing_file_is_refused(lab_scratch, monkeypatch, tmp_path):
    with pytest.raises(Invalid) as e:
        await spec_doc.load(member(), text="Just prose, no headings.")
    assert e.value.reason == "spec_empty"
    monkeypatch.setenv("TRACE_LAB_SPEC_PATH", str(tmp_path / "missing.md"))
    with pytest.raises(Invalid) as e:
        await spec_doc.load(member())
    assert e.value.reason == "spec_missing"


async def test_loading_reads_the_file_the_environment_names(lab_scratch, monkeypatch, tmp_path):
    f = tmp_path / "spec.md"
    f.write_text(V1, encoding="utf-8")
    monkeypatch.setenv("TRACE_LAB_SPEC_PATH", str(f))
    assert (await spec_doc.load(member()))["sections"] == 8


async def test_only_curators_load_a_specification(lab_scratch):
    with pytest.raises(Forbidden):
        await spec_doc.load(member(org=ORG, all_companies=False, role="tester", manages=False), text=V1)


REAL_SPEC = Path(__file__).resolve().parents[2] / "svc-deepagents" / "skills" / "spec" / "SPEC.md"


async def test_scenarios_may_cite_the_rules_of_the_specification_that_is_loaded(lab_scratch):
    await spec_doc.load(member(), text=REAL_SPEC.read_text(encoding="utf-8"))
    assert {"s_1_overdue_reported_as_none", "final_synthesis"} <= await scenario_file.allowed_sections()
    row = dict(copy.deepcopy(_rows()[0]), spec_sections=["s_1_overdue_reported_as_none", "s_4_list_answer_as_paragraph"])
    assert (await scenario_file.save_scenarios(member(), [row], source="import"))["added"] == 1
    stored = (await scenario_file.list_scenarios(member()))[0]
    assert set(stored["spec_hashes"]) == {"s_1_overdue_reported_as_none", "s_4_list_answer_as_paragraph"}
    assert stored["stale"] == [] and stored["uncovered"] == []
    bad = dict(copy.deepcopy(_rows()[1]), spec_sections=["s_99_not_a_rule"])
    with pytest.raises(Invalid):
        await scenario_file.save_scenarios(member(), [bad], source="import")
