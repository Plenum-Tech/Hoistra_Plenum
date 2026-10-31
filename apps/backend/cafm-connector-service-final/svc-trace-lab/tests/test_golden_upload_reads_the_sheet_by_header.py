"""The header row is found in rows 1-10 by its Question and Expected answer columns, under any of their
aliases; the "Golden set" sheet is preferred and hidden sheets are skipped unless named; row numbers are the
sheet's own. 15 Oct 2026. Spec: Golden set §1.1, §1.2."""
from __future__ import annotations

from datetime import date

from trace_lab import golden
from golden_xlsx import make_xlsx

TODAY = date(2026, 10, 15)


def _parse(data, name="set.xlsx", **kw):
    return golden.parse_upload(name, data, today=TODAY, **kw)


def test_the_bishopsgate_layout_reads_with_no_edits():
    data = make_xlsx([("Reference answers", [
        ["#", "Question", "Answer", "Source (workbook sheet)"],
        ["1", "How many floors does Bishopsgate Tower have?", "22 floors.", "Buildings"],
        ["2", "What is the postcode of Bishopsgate Tower?", "EC2M 9ZZ (London).", "Sites"],
    ])])
    r = _parse(data)
    assert r.errors == [] and r.header_row == 1 and r.sheet_name == "Reference answers"
    assert [c.case_key for c in r.cases] == ["1", "2"]
    c = r.cases[0]
    assert (c.key_source, c.evaluation, c.expected_answer, c.source) == ("id_column", "objective", "22 floors.", "Buildings")
    assert set(c.flags) == {"evaluation_defaulted", "unverified"}


def test_the_header_may_sit_below_a_title_within_ten_rows():
    data = make_xlsx([["Golden set for Plenum"], [], ["Ref", "Prompt", "Right answer"], ["A1", "Q one?", "yes"]])
    r = _parse(data)
    assert r.header_row == 3 and r.cases[0].case_key == "A1" and r.cases[0].row_no == 4


def test_no_header_in_the_first_ten_rows_is_one_error():
    data = make_xlsx([["x"]] * 10 + [["Question", "Expected answer"], ["q", "a"]])
    r = _parse(data)
    assert r.cases == [] and "No header row" in r.errors[0]["message"]


def test_a_sheet_named_golden_set_is_preferred_and_hidden_sheets_are_skipped():
    head = ["Question", "Expected answer"]
    data = make_xlsx([("Hidden", [head, ["hidden q", "a"]]), ("Other", [head, ["other q", "a"]]),
                      ("golden SET", [head, ["golden q", "a"]])], hidden=("Hidden",))
    assert _parse(data).cases[0].question == "golden q"
    data = make_xlsx([("Hidden", [head, ["hidden q", "a"]]), ("Other", [head, ["other q", "a"]])], hidden=("Hidden",))
    assert _parse(data).cases[0].question == "other q"
    assert _parse(data, sheet="hidden").cases[0].question == "hidden q"


def test_numbered_and_per_line_follow_ups():
    data = make_xlsx([["Question", "Follow-up 1", "Follow-up 2", "Expected answer"], ["q", "f1", "f2", "a"]])
    assert _parse(data).cases[0].followups == ("f1", "f2")
    data = make_xlsx([["Question", "Follow-ups", "Expected answer"], ["q", "f1\nf2\n", "a"]])
    assert _parse(data).cases[0].followups == ("f1", "f2")


def test_both_follow_up_forms_in_one_file_is_an_error():
    data = make_xlsx([["Question", "Follow-ups", "Follow-up 1", "Expected answer"], ["q", "f", "g", "a"]])
    assert any("not both" in e["message"] for e in _parse(data).errors)


def test_unknown_columns_are_ignored_with_a_warning():
    data = make_xlsx([["Question", "Owner", "Expected answer"], ["q", "ada", "a"]])
    r = _parse(data)
    assert r.errors == [] and any('"Owner"' in w["message"] for w in r.warnings)


def test_row_numbers_are_the_sheets_own_across_gaps():
    data = make_xlsx([("S", [["Question", "Expected answer"], ["q1", "a"], ["q2", "a"]])], row_numbers={"S": [1, 2, 9]})
    assert [c.row_no for c in _parse(data).cases] == [2, 9]


def test_a_csv_reads_the_same_way():
    data = b"ID,Question,Expected answer,Evaluation\r\nX-1,How many?,22,objective\r\n"
    r = _parse(data, name="set.csv")
    assert r.source_kind == "csv" and r.cases[0].case_key == "X-1" and r.cases[0].flags == ("unverified",)


def test_an_empty_row_is_skipped_silently_and_a_notes_only_row_with_a_warning():
    data = make_xlsx([["Question", "Expected answer", "Notes"], ["q", "a", ""], [None, None, None],
                      ["", "", "just a note"]])
    r = _parse(data)
    assert len(r.cases) == 1 and any("Only Notes" in w["message"] for w in r.warnings)
