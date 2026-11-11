"""Generating scenarios for every area of the specification (11 Nov 2026, task plan B2.1 and B2.2).

B2.1: "generate scenarios at scale for each spec area ... every scenario is tagged with the spec section it tests".
B2.2: "every scenario has an expected outcome stored with it": the expected plan, the call order (which call waits
for which), how a failure must be handled and the answer format.

The generator calls no model and reads no database, so the same seed gives the same batch with the same ids, and a
trace can always be matched back to the scenario it answered. The expected outcome comes from the area a scenario
tests, never from an answer."""
from __future__ import annotations

from collections import Counter

import pytest

from trace_lab import scenario_file, scenario_gen, scenarios


@pytest.fixture(scope="module")
def batch():
    rows, dropped = scenario_gen.generate(count=250, seed=7)
    return rows, dropped


def test_250_scenarios_with_the_mix_the_plan_names_and_unique_ids(batch):
    rows, _ = batch
    assert len(rows) == 250
    assert len({r["scenario_id"] for r in rows}) == 250
    assert Counter(r["type"] for r in rows) == scenario_gen.MIX_250
    assert scenario_gen.MIX_250 == {"single_domain": 100, "multi_domain": 60, "multi_turn": 50, "failure": 40}


def test_the_same_seed_gives_the_same_batch_and_another_seed_gives_other_ids(batch):
    rows, _ = batch
    again, _ = scenario_gen.generate(count=250, seed=7)
    assert again == rows
    other, _ = scenario_gen.generate(count=250, seed=8)
    assert not {r["scenario_id"] for r in other} & {r["scenario_id"] for r in rows}
    assert [r["turns"][0]["message"] for r in other] != [r["turns"][0]["message"] for r in rows]


def test_an_id_says_its_type_and_seed(batch):
    rows, _ = batch
    prefix = {"single_domain": "SD", "multi_domain": "MD", "multi_turn": "MT", "failure": "FL"}
    for r in rows:
        assert r["scenario_id"].startswith(prefix[r["type"]] + "-7-"), r["scenario_id"]


def test_every_spec_section_is_tagged_by_at_least_one_scenario(batch):
    rows, _ = batch
    tagged = Counter(s for r in rows for s in r["spec_sections"])
    assert set(tagged) == set(scenario_file.SPEC_SECTIONS), set(scenario_file.SPEC_SECTIONS) - set(tagged)
    assert all(r["spec_sections"] for r in rows)


def test_every_scenario_is_valid_and_carries_an_expected_plan_and_answer_format(batch):
    rows, _ = batch
    for r in rows:
        assert scenario_file.validate(r) == [], r["scenario_id"]
        for t in r["turns"]:
            assert t["expected"]["plan"], r["scenario_id"]
            assert t["expected"]["answer_format"] in scenario_file.ANSWER_FORMATS


def test_a_multi_domain_scenario_plans_a_call_per_area_it_needs(batch):
    rows, _ = batch
    multi = [r for r in rows if r["type"] == "multi_domain"]
    for r in multi:
        plan = r["turns"][0]["expected"]["plan"]
        assert len(plan) >= 2 and len({c["domain"] for c in plan}) == len(plan), r["scenario_id"]
    ordered = [r for r in multi if "dependency_order" in r["spec_sections"]]
    assert ordered and all(r["turns"][0]["expected"]["plan"][1]["depends_on"] == ["c1"] for r in ordered)
    free = [r for r in multi if "dependency_order" not in r["spec_sections"] and "context_passing" not in r["spec_sections"]]
    assert free and all(not c["depends_on"] for r in free for c in r["turns"][0]["expected"]["plan"])


def test_a_multi_turn_scenario_has_two_or_more_turns_each_with_its_own_expected_outcome(batch):
    rows, _ = batch
    turns = [r for r in rows if r["type"] == "multi_turn"]
    assert all(2 <= len(r["turns"]) <= 3 for r in turns)
    assert any(len(r["turns"]) == 3 for r in turns)
    assert all("multi_turn_continuity" in r["spec_sections"] for r in turns)
    scoped = [r for r in turns if "scope_preservation" in r["spec_sections"]]
    assert scoped and all(r["scope"].get("period", {}).get("from") for r in scoped), "a scope to keep across the turns"


def test_failure_scenarios_cover_every_failure_area_and_say_how_the_answer_must_handle_it(batch):
    rows, _ = batch
    failures = [r for r in rows if r["type"] == "failure"]
    states = Counter((t["expected"]["failure_handling"] or {}).get("state") for r in failures for t in r["turns"])
    assert {"partial_failure", "total_failure", "authorization_failure", "evidence_conflict",
            *scenario_file.EVIDENCE_STATES} <= set(states)
    for r in failures:
        e = r["turns"][0]["expected"]
        state = (e["failure_handling"] or {}).get("state")
        if state == "partial_failure":
            assert e["answer_format"] == "partial_success" and r["faults"] and e["failure_handling"]["failed"]
        if state == "total_failure":
            assert e["answer_format"] == "failure" and len(e["failure_handling"]["failed"]) == len(e["plan"])
        if state == "authorization_failure":
            assert e["answer_format"] == "denial" and r["faults"][0]["fault"] == "http_403"
        if state is None:
            assert r["spec_sections"] == ["retry_rules"] and r["faults"][0] == {**r["faults"][0], "fault": "timeout", "times": 1}
            assert e["answer_format"] == "success", "a temporary error retried once still ends in an answer"


def test_a_fault_names_the_service_and_path_of_the_area_it_breaks(batch):
    rows, _ = batch
    for r in rows:
        for f in r["faults"]:
            assert (f["service"], f["path_prefix"]) in set(scenario_gen.ENGINE_SERVICE.values()), r["scenario_id"]
            assert f["fault"] in scenario_file.FAULTS


def test_every_opening_question_routes_inside_the_space_it_is_generated_for():
    rows, dropped = scenario_gen.generate(count=60, seed=3, space="comp")
    mine = set(scenarios.engines_of("comp"))
    for r in rows:
        assert r["space"] == "comp"
        assert {c["domain"] for t in r["turns"] for c in t["expected"]["plan"]} <= mine, r["scenario_id"]
    assert all("reason" in d for d in dropped)


def test_a_smaller_batch_keeps_the_proportions_and_a_mix_can_be_given():
    rows, _ = scenario_gen.generate(count=25, seed=1)
    assert Counter(r["type"] for r in rows) == {"single_domain": 10, "multi_domain": 6, "multi_turn": 5, "failure": 4}
    only, _ = scenario_gen.generate(count=9, seed=1, mix={"failure": 9})
    assert len(only) == 9 and {r["type"] for r in only} == {"failure"}


def test_roles_are_the_specifications_four_and_a_bad_request_is_refused(batch):
    rows, _ = batch
    assert {r["role"] for r in rows} == {"general_user", "portfolio_manager", "property_manager", "compliance_officer"}
    with pytest.raises(ValueError):
        scenario_gen.generate(count=10, space="moon")
    with pytest.raises(ValueError):
        scenario_gen.generate(count=0)
    with pytest.raises(ValueError):
        scenario_gen.generate(count=10, mix={"other": 10})


def test_no_two_scenarios_of_one_type_ask_the_same_opening_question(batch):
    rows, _ = batch
    for kind in ("single_domain", "multi_domain"):
        base = [r["base"] for r in rows if r["type"] == kind] if "base" in rows[0] else None
        opening = [r["turns"][0]["message"] for r in rows if r["type"] == kind]
        assert len(set(opening)) == len(opening), kind
        assert base is None
