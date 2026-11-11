"""The specification read as sections (11 Nov 2026, task plan B2.3): each heading is a section with an id and the hash
of its text, so "has the part of the specification this scenario was written from changed?" has an exact answer.

The ids are derived from the headings, because the specification is written by people (Workstream A): a heading
"Multi-read planning" is the section multi_read_planning. A whitespace-only edit is not a change; a changed word is."""
from __future__ import annotations

import pytest

from trace_lab import scenario_file, spec_doc

TITLES = ("Final synthesis", "Multi-read planning", "Dependency order", "Context passing", "Scope preservation",
          "Retry rules", "Partial failure", "Total failure", "Authorization failure", "Missing evidence",
          "Evidence conflicts", "Multi-turn continuity", "Answer completeness", "Output format", "Few-shot examples")

SPEC = """# Orchestrator specification

Intro that belongs to no section.

## A2. Planning and running tool calls

How a request becomes calls.

### Multi-read planning

One entry per call: call_id, tool, params, depends_on.

### Retry rules

Retry only temporary errors, at most 2 times, with the same parameters.

```
# not a heading: a comment inside a code block
```

## Output format

Four templates: success, partial success, denial, failure.
"""


@pytest.mark.parametrize("title, want", [
    ("Multi-read planning", "multi_read_planning"),
    ("Few-shot examples", "few_shot_examples"),
    ("A2.3 Context passing", "context_passing"),
    ("3.1 Role definitions and ordinary data scope", "role_definitions_and_ordinary_data_scope"),
    ("A2.3 Anything at all {#context_passing}", "context_passing"),
    ("  Retry rules  ", "retry_rules"),
    ("S-4 · list_answer_as_paragraph", "s_4_list_answer_as_paragraph"),
])
def test_a_heading_gives_its_section_id(title, want):
    assert spec_doc.section_id_of(title) == want


def test_the_fifteen_area_titles_give_the_fifteen_section_ids_scenarios_cite():
    assert tuple(spec_doc.section_id_of(t) for t in TITLES) == scenario_file.SPEC_SECTIONS


def test_sections_are_the_headings_of_level_two_to_four_and_a_code_block_hides_none():
    sections = spec_doc.parse(SPEC)
    assert [s.section_id for s in sections] == ["planning_and_running_tool_calls", "multi_read_planning",
                                               "retry_rules", "output_format"]
    assert [s.title for s in sections][1] == "Multi-read planning"
    retry = sections[2]
    assert "at most 2 times" in retry.body and "# not a heading" in retry.body
    assert "Four templates" not in retry.body, "a section ends at the next heading of its own level or higher"


def test_a_section_holds_the_sections_under_it():
    parent = spec_doc.parse(SPEC)[0]
    assert "Retry only temporary errors" in parent.body and "Four templates" not in parent.body


def test_two_headings_with_the_same_words_are_two_sections():
    ids = [s.section_id for s in spec_doc.parse("## Examples\n\na\n\n## Examples\n\nb\n")]
    assert ids == ["examples", "examples_2"]


def test_a_whitespace_only_edit_keeps_the_hash_and_a_changed_word_changes_it():
    base = {s.section_id: s.sha256 for s in spec_doc.parse(SPEC)}
    spaced = SPEC.replace("\n\n", "\n\n\n").replace("\n", "  \r\n")
    assert {s.section_id: s.sha256 for s in spec_doc.parse(spaced)} == base
    assert spec_doc.version_of(spaced) == spec_doc.version_of(SPEC)
    edited = {s.section_id: s.sha256 for s in spec_doc.parse(SPEC.replace("at most 2 times", "at most 3 times"))}
    assert edited["retry_rules"] != base["retry_rules"]
    assert edited["output_format"] == base["output_format"] and edited["multi_read_planning"] == base["multi_read_planning"]
    assert edited["planning_and_running_tool_calls"] != base["planning_and_running_tool_calls"], "its parent holds it"
    assert spec_doc.version_of(SPEC.replace("2 times", "3 times")) != spec_doc.version_of(SPEC)
    assert len(spec_doc.version_of(SPEC)) == 12


def test_what_is_out_of_date_is_each_cited_section_whose_text_changed_or_went():
    now = {"retry_rules": "b" * 64, "output_format": "c" * 64}
    assert spec_doc.stale_sections({"retry_rules": "a" * 64, "output_format": "c" * 64}, now) == ["retry_rules"]
    assert spec_doc.stale_sections({"total_failure": "a" * 64}, now) == ["total_failure"], "the section is gone"
    assert spec_doc.stale_sections({"output_format": "c" * 64}, now) == []
    assert spec_doc.stale_sections({}, now) == [], "a scenario stamped with nothing cannot be judged"
    assert spec_doc.stale_sections({"retry_rules": "a" * 64}, {}) == [], "no specification loaded: nothing to compare"
    assert spec_doc.stale_sections({}, now, cited=["retry_rules"]) == ["retry_rules"], "the section came after it was written"
    assert spec_doc.stale_sections({}, now, cited=["not_in_the_spec"]) == [], "a section neither has cannot be compared"
    assert spec_doc.stale_sections({"total_failure": "a" * 64}, now, cited=["output_format"]) == ["output_format", "total_failure"]


def test_a_cited_section_the_specification_does_not_have_is_named_as_not_covered():
    now = {"retry_rules": "b" * 64}
    assert spec_doc.uncovered_sections(["retry_rules", "not_in_the_spec"], now) == ["not_in_the_spec"]
    assert spec_doc.uncovered_sections(["retry_rules"], {}) == [], "no specification loaded: nothing to say"


def test_the_specification_is_read_from_where_the_environment_says(tmp_path):
    f = tmp_path / "spec.md"
    assert spec_doc.spec_path({"TRACE_LAB_SPEC_PATH": str(f)}) == f
    default = spec_doc.spec_path({})
    assert default.name == "SPEC.md" and default.parent.name == "spec" and "svc-deepagents" in default.parts
