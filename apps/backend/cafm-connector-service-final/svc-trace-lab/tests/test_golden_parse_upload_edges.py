"""Behaviour of parse_upload that the brief's tests leave unpinned: the default for today, the header search
across sheets, the empty-sheet error, quoted separators surviving the Expected values tidy-up, numbered
follow-ups out of order, and the key rules. Characterization tests: they describe what the code does and the
spec asks for. 15 Oct 2026. Spec: Golden set §1.1, §1.2, §1.6, §1.7."""
from __future__ import annotations

from datetime import date

import pytest

from trace_lab import golden
from golden_xlsx import formula, make_xlsx

TODAY = date(2026, 10, 15)


def _csv(*lines, **kw):
    return golden.parse_upload("set.csv", ("\r\n".join(lines) + "\r\n").encode(), **kw)


def _xlsx(rows, **kw):
    return golden.parse_upload("set.xlsx", make_xlsx(rows), today=TODAY, **kw)


def test_today_defaults_to_the_current_utc_date():
    r = _csv("Question,Expected answer,Verified on", "q,a,9999-12-31")
    assert any("future" in e["message"] for e in r.errors)
    r = _csv("Question,Expected answer,Verified on", "q,a,2020-06-15")      # past for good; a frozen 2000 would call it future
    assert r.errors == [] and r.cases[0].verified_on == date(2020, 6, 15)


def test_the_header_is_looked_for_on_every_visible_sheet():
    data = make_xlsx([("Cover", [["Hello"], ["nothing here"]]), ("Cases", [["Question", "Expected answer"], ["q", "a"]])])
    r = golden.parse_upload("set.xlsx", data, today=TODAY)
    assert (r.sheet_name, r.header_row, len(r.cases)) == ("Cases", 1, 1)


def test_no_header_on_any_sheet_names_the_first_sheet():
    data = make_xlsx([("A", [["x"]]), ("B", [["y"]])])
    r = golden.parse_upload("set.xlsx", data, today=TODAY)
    assert (r.sheet_name, r.header_row, r.cases) == ("A", 0, []) and "No header row" in r.errors[0]["message"]


def test_a_header_with_no_cases_under_it_is_one_error():
    r = _xlsx([["Question", "Expected answer"]])
    assert r.header_row == 1 and r.cases == [] and [e["message"] for e in r.errors] == [
        "The sheet has a header row but no cases under it."]


def test_a_sheet_that_is_not_there_is_refused():
    with pytest.raises(golden.UploadRefused) as err:
        _xlsx([["Question", "Expected answer"], ["q", "a"]], sheet="Nope")
    assert err.value.reason == "bad_file"


def test_a_header_cell_holding_an_uncalculated_formula_is_skipped_not_reported_as_unknown():
    r = _xlsx([[formula("A9"), "Question", "Expected answer"], ["x", "q", "a"]])
    assert r.errors == [] and len(r.cases) == 1 and not any("not used" in w["message"] for w in r.warnings)


def test_a_repeated_column_is_ignored_with_a_warning():
    r = _xlsx([["Question", "Question", "Expected answer"], ["first", "second", "a"]])
    assert r.cases[0].question == "first" and any("repeated" in w["message"] for w in r.warnings)


def test_quoted_separators_in_expected_values_survive_the_tidy_up():
    cell = '"Block A; Block B"\n22 floors'
    r = _csv("Question,Expected answer,Expected values", 'q,a,"' + cell.replace('"', '""') + '"')
    assert r.errors == []
    assert [(v["kind"], v["value"]) for v in r.cases[0].expected_values] == [("name", "Block A; Block B"), ("number", 22)]


def test_numbered_follow_ups_are_kept_in_number_order_not_column_order():
    r = _xlsx([["Question", "Follow-up 2", "Follow-up 1", "Expected answer"], ["q", "second", "first", "a"]])
    assert r.cases[0].followups == ("first", "second")


def test_a_write_request_in_a_follow_up_is_flagged_too():
    r = _xlsx([["Question", "Follow-ups", "Expected answer"], ["How many floors?", "Now delete the building", "22"]])
    assert r.errors == [] and "write_intent" in r.cases[0].flags


def test_ask_as_company_is_accepted_in_any_case():
    r = _xlsx([["Question", "Expected answer", "Ask as"], ["q", "a", "COMPANY"]])
    assert r.errors == [] and r.cases[0].ask_as == "company"


def test_the_same_question_under_different_page_contexts_is_two_cases():
    r = _xlsx([["Question", "Expected answer", "Page/context"], ["How many?", "22", "The user is on Home."],
               ["How many?", "22", "none"], ["How many?", "22", ""]])
    assert r.errors == [] and len({c.case_key for c in r.cases}) == 3


def test_a_row_without_an_id_is_keyed_by_the_hash():
    r = _csv("Question,Expected answer", "How many floors?,22")
    c = r.cases[0]
    assert (c.case_key, c.key_source) == golden.case_key_for("How many floors?", None) and c.key_source == "hash"


def test_blank_rows_do_not_count_towards_the_row_cap(monkeypatch):
    monkeypatch.setattr(golden, "MAX_ROWS", 2)
    r = _xlsx([["Question", "Expected answer"], ["q1", "a"], ["q2", "a"], [None], [None], ["", ""]])
    assert r.errors == [] and len(r.cases) == 2
