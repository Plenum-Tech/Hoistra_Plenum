"""Every row rule of spec §1.6: an error names the row and column; a warning or flag never blocks a save.
One bad row makes the whole upload a 422 (Task 26 pins that no row is saved). 15 Oct 2026."""
from __future__ import annotations

from datetime import date

import pytest

from trace_lab import golden
from golden_xlsx import formula, make_xlsx

TODAY = date(2026, 10, 15)
HEAD = ["ID", "Question", "Follow-ups", "Expected answer", "Evaluation", "Expected values", "Source",
        "Verified on", "Page/context", "Ask as", "Requirement IDs", "Notes"]


def _row(**kw):
    base = {"ID": "C1", "Question": "How many floors?", "Follow-ups": "", "Expected answer": "22",
            "Evaluation": "objective", "Expected values": "", "Source": "", "Verified on": "",
            "Page/context": "", "Ask as": "", "Requirement IDs": "", "Notes": ""}
    base.update(kw)
    return [base[h] for h in HEAD]


def _parse(*rows):
    return golden.parse_upload("set.xlsx", make_xlsx([HEAD, *rows]), today=TODAY)


@pytest.mark.parametrize("override,column,needle", [
    ({"Question": ""}, "Question", "missing"),
    ({"Question": "q" * 2001}, "Question", "2,000"),
    ({"Expected answer": ""}, "Expected answer", "missing"),
    ({"Expected answer": "a" * 6001}, "Expected answer", "6,000"),
    ({"Evaluation": "maybe"}, "Evaluation", "objective or judgment"),
    ({"Expected values": "number:abc"}, "Expected values", "number:abc"),
    ({"Verified on": "soon"}, "Verified on", "not a date"),
    ({"Verified on": "2026-11-01"}, "Verified on", "future"),
    ({"ID": "has space"}, "ID", "letters, digits"),
    ({"Ask as": "building"}, "Ask as", "company-wide questions only"),
    ({"Follow-ups": "1\n2\n3\n4\n5\n6"}, "Follow-ups", "At most 5"),
    ({"Requirement IDs": "R1, bad id!"}, "Requirement IDs", "bad id!"),
    ({"Source": "s" * 501}, "Source", "500"),
    ({"Page/context": "p" * 4001}, "Page/context", "4,000"),
    ({"Notes": "n" * 2001}, "Notes", "2,000"),
])
def test_each_row_rule_is_an_error_naming_row_and_column(override, column, needle):
    r = _parse(_row(**override))
    assert r.cases == []
    assert any(e["row"] == 2 and e["column"] == column and needle in e["message"] for e in r.errors), r.errors


def test_a_formula_with_no_saved_value_is_a_row_error():
    row = _row()
    row[HEAD.index("Expected answer")] = formula("B9*2")
    r = _parse(row)
    assert any(e["column"] == "Expected answer" and "no saved value" in e["message"] for e in r.errors)


def test_a_duplicated_id_errors_on_the_second_row():
    r = _parse(_row(), _row(Question="Another question?"))
    assert [c.row_no for c in r.cases] == [2] and r.errors[0]["row"] == 3 and "row 2" in r.errors[0]["message"]


def test_the_same_question_without_ids_errors_on_the_second_row():
    r = _parse(_row(ID=""), _row(ID="", Question="how many FLOORS"))
    assert r.errors and r.errors[0]["row"] == 3 and "repeat row 2" in r.errors[0]["message"]


def test_one_bad_row_among_good_ones_still_fails_the_file():
    r = _parse(_row(), _row(ID="C2", Question=""))
    assert r.errors and len(r.cases) == 1          # the route refuses the whole file when errors is non-empty


def test_write_intent_is_a_warning_and_a_flag_not_an_error():
    r = _parse(_row(Question="Raise a work order for the boiler at B-301?"))
    assert r.errors == [] and "write_intent" in r.cases[0].flags
    assert any("change something" in w["message"] for w in r.warnings)


def test_blank_evaluation_defaults_to_objective_with_a_flag():
    r = _parse(_row(Evaluation=""))
    assert r.cases[0].evaluation == "objective" and "evaluation_defaulted" in r.cases[0].flags


def test_judgment_aliases_and_ignored_values():
    r = _parse(_row(Evaluation="Judgement", **{"Expected values": "22"}))
    assert r.cases[0].evaluation == "judgment" and r.cases[0].expected_values == ()
    assert any("ignored on judgment" in w["message"] for w in r.warnings)


def test_verified_on_blank_flags_unverified_and_a_date_is_kept():
    assert "unverified" in _parse(_row()).cases[0].flags
    c = _parse(_row(**{"Verified on": "13 October 2026"})).cases[0]
    assert c.verified_on == date(2026, 10, 13) and "unverified" not in c.flags


def test_page_context_none_means_send_nothing():
    assert _parse(_row(**{"Page/context": "none"})).cases[0].page_context == ""
    assert _parse(_row()).cases[0].page_context is None


def test_more_than_the_row_cap_refuses_the_file(monkeypatch):
    monkeypatch.setattr(golden, "MAX_ROWS", 3)
    with pytest.raises(golden.UploadRefused) as err:
        _parse(*[_row(ID=f"C{i}", Question=f"Question {i}?") for i in range(4)])
    assert err.value.reason == "too_many_rows"
