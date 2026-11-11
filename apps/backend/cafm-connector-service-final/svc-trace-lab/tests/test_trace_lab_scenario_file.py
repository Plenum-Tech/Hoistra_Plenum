"""Scenario files (11 Nov 2026, task plan B2.1 and B2.2): one scenario per line, each tagged with the sections of the
specification it tests and carrying, per turn, the expected outcome it was written with: the plan (which calls, and
which depend on which, so the order is implied), how a failure must be handled and which of the four answer formats
the answer takes.

A file is taken whole or not at all, the way a golden set is: a batch with one bad scenario in it is a batch nobody
checked. Every refusal names the line and the field, so the person or the coding agent that wrote the file can fix it
without guessing."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from trace_lab import scenario_file

SAMPLE = Path(__file__).parent / "fixtures" / "eval_scenarios_sample.jsonl"


def _rows() -> list[dict]:
    return [json.loads(line) for line in SAMPLE.read_text(encoding="utf-8").splitlines() if line.strip()]


def _one(scenario_id: str = "MD-001") -> dict:
    return copy.deepcopy(next(r for r in _rows() if r["scenario_id"] == scenario_id))


def _file(rows: list[dict]) -> bytes:
    return "".join(json.dumps(r) + "\n" for r in rows).encode("utf-8")


def test_the_sample_file_is_valid_and_has_one_scenario_of_each_type():
    rows, errors = scenario_file.parse_jsonl(SAMPLE.read_bytes())
    assert errors == []
    assert [r["scenario_id"] for r in rows] == ["SD-001", "MD-001", "MT-001", "FL-001"]
    assert {r["type"] for r in rows} == set(scenario_file.TYPES)


def test_the_values_the_task_plan_fixes():
    assert scenario_file.TYPES == ("single_domain", "multi_domain", "multi_turn", "failure")
    assert scenario_file.ANSWER_FORMATS == ("success", "partial_success", "denial", "failure")
    assert scenario_file.EVIDENCE_STATES == ("zero_results", "not_recorded", "unavailable", "tool_failed")
    assert len(scenario_file.SPEC_SECTIONS) == 15 and "retry_rules" in scenario_file.SPEC_SECTIONS


def test_a_parsed_scenario_has_every_field_filled_in():
    row = scenario_file.parse_jsonl(SAMPLE.read_bytes())[0][0]
    assert row["scope"] == {"buildings": ["B-301"]} and row["faults"] == [] and row["grounded_on"] == []
    assert row["domain_rules"] == [] and row["role"] == "property_manager" and row["space"] == "ops"
    expected = row["turns"][0]["expected"]
    assert expected["plan"][0] == {"call_id": "c1", "domain": "wo_engine", "tool": None, "params": {}, "depends_on": []}
    assert expected["answer_format"] == "success" and expected["failure_handling"] is None
    assert expected["must_include"] == [] and expected["must_not_include"] == [] and expected["answer"] is None


def test_a_file_that_is_one_json_array_is_read_the_same_way():
    rows, errors = scenario_file.parse_jsonl(json.dumps(_rows()).encode("utf-8"))
    assert errors == [] and len(rows) == 4


@pytest.mark.parametrize("patch, needle", [
    ({"type": "other"}, "type"),
    ({"spec_sections": ["made_up"]}, "made_up"),
    ({"spec_sections": []}, "spec_sections"),
    ({"turns": []}, "turns"),
    ({"scenario_id": "a__b"}, "scenario_id"),
    ({"scenario_id": "has space"}, "scenario_id"),
    ({"space": "moon"}, "space"),
    ({"scope": ["B-301"]}, "scope"),
    ({"scope": {"buildings": "B-301"}}, "buildings"),
    ({"faults": [{"service": "ops-intelligence", "path_prefix": "/api", "fault": "explode"}]}, "fault"),
    ({"domain_rules": [{"id": "R-1"}]}, "domain_rules"),
])
def test_a_bad_scenario_is_refused_and_the_field_is_named(patch, needle):
    row = {**_one(), **patch}
    problems = scenario_file.validate(row)
    assert problems and any(needle in p for p in problems), problems


def test_a_scenario_has_at_most_six_turns():
    row = _one("MT-001")
    row["turns"] = row["turns"] * 4
    assert any("turns" in p for p in scenario_file.validate(row))


def test_every_turn_needs_a_message_and_an_expected_outcome():
    row = _one()
    row["turns"][0]["message"] = "   "
    assert any("message" in p for p in scenario_file.validate(row))
    row = _one()
    del row["turns"][0]["expected"]
    assert any("expected" in p for p in scenario_file.validate(row))


def test_the_answer_format_must_be_one_of_the_four():
    row = _one()
    row["turns"][0]["expected"]["answer_format"] = "essay"
    assert any("answer_format" in p for p in scenario_file.validate(row))
    for fmt in scenario_file.ANSWER_FORMATS:
        row["turns"][0]["expected"]["answer_format"] = fmt
        assert scenario_file.validate(row) == []


def test_depends_on_must_name_an_earlier_call_of_the_same_turn():
    row = _one()
    row["turns"][0]["expected"]["plan"][0]["depends_on"] = ["c2"]          # c1 depending on a later call
    assert any("depends_on" in p for p in scenario_file.validate(row))
    row = _one()
    row["turns"][0]["expected"]["plan"][1]["depends_on"] = ["nope"]
    assert any("depends_on" in p for p in scenario_file.validate(row))


def test_call_ids_are_unique_and_a_call_names_a_domain_or_a_tool():
    row = _one()
    row["turns"][0]["expected"]["plan"][1]["call_id"] = "c1"
    assert any("call_id" in p for p in scenario_file.validate(row))
    row = _one()
    row["turns"][0]["expected"]["plan"][0].update(domain=None, tool=None)
    assert any("domain" in p for p in scenario_file.validate(row))


def test_failure_handling_names_a_known_state_and_calls_of_the_plan():
    row = _one("FL-001")
    row["turns"][0]["expected"]["failure_handling"] = {"state": "meltdown", "failed": ["c2"]}
    assert any("failure_handling" in p for p in scenario_file.validate(row))
    row = _one("FL-001")
    row["turns"][0]["expected"]["failure_handling"] = {"state": "partial_failure", "failed": ["c9"]}
    assert any("failed" in p for p in scenario_file.validate(row))
    for state in ("partial_failure", "total_failure", "authorization_failure", "evidence_conflict",
                  *scenario_file.EVIDENCE_STATES):
        row["turns"][0]["expected"]["failure_handling"] = {"state": state, "failed": []}
        assert scenario_file.validate(row) == [], state


def test_a_multi_turn_scenario_needs_two_turns_and_a_failure_scenario_says_how_to_handle_it():
    row = _one("MT-001")
    row["turns"] = row["turns"][:1]
    assert any("multi_turn" in p for p in scenario_file.validate(row))
    row = _one("FL-001")
    row["turns"][0]["expected"]["failure_handling"] = None
    assert scenario_file.validate(row) == [], "a fault to recover from (a retry that then succeeds) needs no handling"
    row["faults"] = []
    assert any("type failure" in p for p in scenario_file.validate(row)), "neither a fault nor a handling: not a failure"


def test_a_multi_domain_scenario_plans_two_or_more_calls():
    row = _one()
    row["turns"][0]["expected"]["plan"] = row["turns"][0]["expected"]["plan"][:1]
    assert any("multi_domain" in p for p in scenario_file.validate(row))


def test_one_bad_line_rejects_the_whole_file_and_names_the_line():
    rows = _rows()
    rows[2]["type"] = "other"
    good, errors = scenario_file.parse_jsonl(_file(rows))
    assert good == [] and [e["line"] for e in errors] == [3] and "type" in errors[0]["error"]


def test_two_lines_with_the_same_id_reject_the_whole_file():
    rows = _rows()
    good, errors = scenario_file.parse_jsonl(_file(rows + [rows[0]]))
    assert good == [] and errors[0]["line"] == 5 and "SD-001" in errors[0]["error"]


def test_a_line_that_is_not_json_or_not_an_object_is_named():
    good, errors = scenario_file.parse_jsonl(b'{"scenario_id": "A-1"\n[1, 2]\n')
    assert good == [] and [e["line"] for e in errors] == [1, 2]


def test_an_empty_file_and_an_oversized_file_are_refused():
    assert scenario_file.parse_jsonl(b"  \n\n")[1][0]["error"].startswith("The file has no scenarios")
    big = b" " * (scenario_file.MAX_BYTES + 1)
    assert "larger" in scenario_file.parse_jsonl(big)[1][0]["error"]


def test_the_content_hash_ignores_key_order_and_changes_with_the_expected_outcome():
    a = scenario_file.parse_jsonl(SAMPLE.read_bytes())[0][1]
    shuffled = json.loads(json.dumps(dict(reversed(list(_one().items())))))
    b = scenario_file.parse_jsonl(_file([shuffled]))[0][0]
    assert scenario_file.content_hash(a) == scenario_file.content_hash(b)
    changed = _one()
    changed["turns"][0]["expected"]["answer_format"] = "partial_success"
    c = scenario_file.parse_jsonl(_file([changed]))[0][0]
    assert scenario_file.content_hash(c) != scenario_file.content_hash(a)


def test_export_gives_back_a_file_that_parses_to_the_same_scenarios():
    rows, _ = scenario_file.parse_jsonl(SAMPLE.read_bytes())
    again, errors = scenario_file.parse_jsonl(scenario_file.export_jsonl(rows))
    assert errors == [] and again == rows


def test_a_scenario_may_cite_the_sections_it_is_allowed_besides_the_planned_ones():
    rows, errors = scenario_file.parse_jsonl(SAMPLE.read_bytes())
    assert errors == []
    s = dict(rows[0], spec_sections=["s_1_overdue_reported_as_none"])
    assert any("not a section" in p for p in scenario_file.validate(s)), "unknown by default"
    assert scenario_file.validate(s, sections={"s_1_overdue_reported_as_none"}) == []
    line = (json.dumps(s) + "\n").encode()
    assert scenario_file.parse_jsonl(line, sections={"s_1_overdue_reported_as_none"})[1] == []
    assert scenario_file.parse_jsonl(line)[1] != []
