"""Cells read as the admin sees them: date serials become ISO dates in both date systems, percent formats
become "79.4%", formulas are read from their saved values and never evaluated, CSV encodings and delimiters
are detected. 15 Oct 2026. Spec: Golden set §1.4."""
from __future__ import annotations

from trace_lab import golden
from golden_xlsx import boolean, error, formula, inline, make_xlsx, num


def _cells(data, name="set.xlsx"):
    kind, tables = golden.read_tables(name, data)
    _, rows, warnings = tables[0]
    return dict(rows), warnings


def test_a_date_serial_becomes_an_iso_date_in_the_1900_system():
    rows, _ = _cells(make_xlsx([["Verified on"], [num(46308, style=1)], [num(46308.75, style=1)]], xfs=(0, 14)))
    assert rows[2][0] == "2026-10-13" and rows[3][0] == "2026-10-13"


def test_a_date_serial_becomes_an_iso_date_in_the_1904_system():
    rows, _ = _cells(make_xlsx([["Verified on"], [num(44846, style=1)]], xfs=(0, 14), date1904=True))
    assert rows[2][0] == "2026-10-13"


def test_a_custom_date_format_is_a_date_but_quoted_letters_are_not():
    data = make_xlsx([["a", "b"], [num(46308, style=1), num(7, style=2)]], xfs=(0, 164, 165),
                     custom_formats={164: "dd/mm/yyyy", 165: '"Day "0'})
    rows, _ = _cells(data)
    assert rows[2] == {0: "2026-10-13", 1: "7"}


def test_percent_formats_read_as_percent_text():
    data = make_xlsx([["a", "b"], [num(0.794, style=1), num(0.5, style=2)]], xfs=(0, 10, 166),
                     custom_formats={166: "0.0%"})
    rows, _ = _cells(data)
    assert rows[2] == {0: "79.4%", 1: "50%"}


def test_numbers_lose_a_trailing_point_zero():
    rows, _ = _cells(make_xlsx([["a", "b"], [22.0, 79.45]]))
    assert rows[2] == {0: "22", 1: "79.45"}


def test_formulas_are_read_from_their_saved_value():
    rows, _ = _cells(make_xlsx([["a", "b"], [formula("B9*2", cached=44), formula('A1&"x"', cached="ax")]]))
    assert rows[2] == {0: "44", 1: "ax"}


def test_a_formula_without_a_saved_value_is_marked_for_a_row_error():
    rows, _ = _cells(make_xlsx([["a"], [formula("B9*2")]]))
    assert rows[2][0] is golden.NO_SAVED_VALUE


def test_booleans_errors_and_inline_strings():
    rows, warnings = _cells(make_xlsx([["a", "b", "c"], [boolean(True), error("#N/A"), inline("Bishopsgate")]]))
    assert rows[2] == {0: "TRUE", 1: "", 2: "Bishopsgate"}
    assert warnings[0]["row"] == 2 and warnings[0]["column"] == "B" and "#N/A" in warnings[0]["message"]


def test_rich_text_runs_are_joined_and_phonetic_runs_skipped():
    shared = ('<?xml version="1.0"?><sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
              '<si><r><t>Bishops</t></r><r><t>gate</t></r><rPh><t>x</t></rPh></si></sst>')
    data = make_xlsx([["a"]], shared_override=shared)
    rows, _ = _cells(data)
    assert rows[1] == {0: "Bishopsgate"}


def test_sheet_row_numbers_are_kept_across_gaps():
    data = make_xlsx([("Sheet1", [["a"], ["one"], ["two"]])], row_numbers={"Sheet1": [1, 2, 7]})
    _, tables = golden.read_tables("set.xlsx", data)
    assert [r for r, _ in tables[0][1]] == [1, 2, 7]


def test_csv_with_a_bom_semicolons_and_a_quoted_multiline_cell():
    data = '﻿Question;Follow-ups;Expected answer\r\n"How many?";"first\nsecond";22\r\n'.encode("utf-8")
    rows, _ = _cells(data, name="set.csv")
    assert rows[1] == {0: "Question", 1: "Follow-ups", 2: "Expected answer"}
    assert rows[2] == {0: "How many?", 1: "first\nsecond", 2: "22"}


def test_csv_that_is_not_utf8_is_read_as_cp1252():
    rows, _ = _cells("Question,Expected answer\r\nCafé?,Oui\r\n".encode("cp1252"), name="set.csv")
    assert rows[2] == {0: "Café?", 1: "Oui"}


def test_only_the_first_40_columns_are_read():
    rows, warnings = _cells(make_xlsx([[f"c{i}" for i in range(45)]]))
    assert len(rows[1]) == 40 and any("first 40 columns" in w["message"] for w in warnings)
