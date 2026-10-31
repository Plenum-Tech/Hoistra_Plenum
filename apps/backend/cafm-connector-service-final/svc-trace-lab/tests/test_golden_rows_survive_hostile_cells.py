"""A crafted cell comes back as a row error, or is read cheaply: never a bare exception, never a stall, never a
whole 20 MB cell echoed into a message. Each case below was reached with a real file by probing parse_upload
after the brief's code was in (see the Task 5 report). 15 Oct 2026. Spec: Golden set §1.5, §1.6."""
from __future__ import annotations

import json
import random
import time
from datetime import date

import pytest

from trace_lab import golden
from golden_xlsx import make_xlsx

TODAY = date(2026, 10, 15)
HEAD = ["ID", "Question", "Expected answer", "Evaluation", "Expected values", "Verified on", "Requirement IDs"]


def _parse(**cells):
    row = {"ID": "C1", "Question": "How many floors?", "Expected answer": "22", "Evaluation": "objective",
           "Expected values": "", "Verified on": "", "Requirement IDs": ""}
    row.update(cells)
    return golden.parse_upload("set.xlsx", make_xlsx([HEAD, [row[h] for h in HEAD]]), today=TODAY)


def _errors(r, column):
    return [e["message"] for e in r.errors if e["row"] == 2 and e["column"] == column]


# ── a number too long to store ────────────────────────────────────────────────────────

@pytest.mark.parametrize("digits", [201, 4400, 5000])
def test_a_number_too_long_to_store_is_a_row_error_not_an_exception(digits):
    # 4,300 digits or more made json.dumps raise a bare ValueError out of parse_upload.
    r = _parse(**{"Expected values": "9" * digits})
    assert r.cases == [] and any("longer than 200 characters" in m for m in _errors(r, "Expected values"))


def test_a_long_but_sane_number_still_reads_and_can_be_stored_as_json():
    r = _parse(**{"Expected values": "9" * 150})
    assert r.errors == [] and json.dumps(r.cases[0].expected_values, allow_nan=False)


def test_an_expected_value_may_be_exactly_two_hundred_characters():
    assert _parse(**{"Expected values": '"' + "a" * 198 + '"'}).errors == []
    assert _errors(_parse(**{"Expected values": '"' + "a" * 199 + '"'}), "Expected values")


# ── whitespace the value grammar cannot afford ────────────────────────────────────────

def test_each_expected_value_is_cut_to_single_spaces_before_the_grammar_reads_it(monkeypatch):
    seen = []
    real = golden.parse_expected_values
    monkeypatch.setattr(golden, "parse_expected_values", lambda cell: (seen.append(cell), real(cell))[1])
    r = _parse(**{"Expected values": '22  floors;\t  £1.5m\n"Bishop   Tower"'})
    assert seen == ['22 floors\n£1.5m\n"Bishop Tower"']
    assert [v["value"] for v in r.cases[0].expected_values] == [22, 1500000, "Bishop Tower"]


def test_a_run_of_spaces_in_every_expected_value_does_not_stall_the_reader():
    # The grammar's number pattern takes cubic time on "1", a long run of spaces, then "5": 40 of these cost
    # about two seconds before the fix and about a millisecond after it. A CSV carries it with no zip-bomb check.
    cell = ";".join("1" + " " * 195 + "5" for _ in range(golden.MAX_VALUES))
    data = ('Question,Expected answer,Expected values\r\nq,a,"' + cell + '"\r\n').encode()
    started = time.perf_counter()
    r = golden.parse_upload("set.csv", data, today=TODAY)
    assert r.errors == [] and len(r.cases[0].expected_values) == golden.MAX_VALUES
    assert time.perf_counter() - started < 1.0


# ── letters that match a month name only when case is ignored ─────────────────────────

@pytest.mark.parametrize("text", ["13 ſeptember 2026", "1 aprıl 2026"])
def test_a_look_alike_month_letter_in_verified_on_is_not_a_date(text):
    # re.I matched the long s and the dotless i, then the month table had no such key: a bare KeyError.
    r = _parse(**{"Verified on": text})
    assert r.cases == [] and any("not a date" in m for m in _errors(r, "Verified on"))


@pytest.mark.parametrize("text", ["ſep 5, 2026", "5 aprıl 2026"])
def test_a_look_alike_month_letter_in_expected_values_is_a_row_error(text):
    r = _parse(**{"Expected values": text})
    assert r.cases == [] and any("plain letters" in m for m in _errors(r, "Expected values"))


# ── a cell is never echoed whole ──────────────────────────────────────────────────────

def test_many_bad_requirement_ids_make_a_bounded_number_of_errors():
    r = _parse(**{"Requirement IDs": ",".join(f"!r{i}" for i in range(500))})
    msgs = _errors(r, "Requirement IDs")
    assert any("At most 20" in m for m in msgs) and len(msgs) <= golden.MAX_REQUIREMENTS + 1


def test_a_long_cell_is_never_echoed_whole_in_a_message():
    r = _parse(**{"Requirement IDs": "!" + "a" * 3000})
    assert all(len(m) < 200 for m in _errors(r, "Requirement IDs")) and _errors(r, "Requirement IDs")
    data = make_xlsx([["Question", "Expected answer", "z" * 3000], ["q", "a", "x"]])
    w = golden.parse_upload("set.xlsx", data, today=TODAY).warnings
    assert w and all(len(x["message"]) < 200 and len(x["column"]) < 100 for x in w)


# ── one huge shared string, used by every row ─────────────────────────────────────────
# An .xlsx shared string can be referenced by every row, so one cell's length is paid once per row, up to
# MAX_ROWS (2,000) times. A 4 MB string cost 0.3 s a row to cut into items (10 minutes a file) while the
# result was errors=0; the same went for the Requirement IDs and Follow-ups cells. Each cell's raw length is
# now checked against the most its column can hold, before anything cuts the cell up.

HUGE_CHARS = 600_000     # seconds to minutes a file before the fix, well under one second after it
HUGE_ROWS = 200
TIME_LIMIT = 3.0


def _huge(separator: str | None) -> str:
    """Random hex (so deflate cannot shrink it past the zip-bomb ratio), a quarter of it turned into separators."""
    text = random.Random(7).randbytes(HUGE_CHARS // 2).hex()
    return text if separator is None else text.translate({ord(c): separator for c in "0123"})


def _sheet_of(column: str, text: str, *, evaluation: str = "objective", rows: int = HUGE_ROWS) -> bytes:
    head = ["ID", "Question", "Expected answer", "Evaluation"]
    if column not in head:
        head.append(column)
    body = []
    for i in range(rows):
        cells = {"ID": f"C{i}", "Question": "How many floors?", "Expected answer": "22", "Evaluation": evaluation}
        cells[column] = text
        body.append([cells[h] for h in head])
    data = make_xlsx([head] + body)
    assert len(data) < golden.MAX_BYTES        # the reader accepts the file: only the row checks are on trial
    return data


def _errors_in(r, column):
    return [e for e in r.errors if e["column"] == column]


def _timed(data):
    started = time.perf_counter()
    r = golden.parse_upload("set.xlsx", data, today=TODAY)
    return r, time.perf_counter() - started


@pytest.mark.parametrize("evaluation", ["objective", "judgment"])
@pytest.mark.parametrize("separator", [None, "\n", ";"])
def test_one_huge_expected_values_string_shared_by_every_row_is_refused_without_cutting_it_up(separator, evaluation):
    r, took = _timed(_sheet_of("Expected values", _huge(separator), evaluation=evaluation))
    assert took < TIME_LIMIT
    assert r.cases == [] and len(_errors_in(r, "Expected values")) == HUGE_ROWS
    assert all(len(e["message"]) < 200 for e in r.errors)


def test_a_huge_expected_values_cell_is_never_handed_to_split_items(monkeypatch):
    calls = []
    real = golden.split_items
    monkeypatch.setattr(golden, "split_items", lambda cell: (calls.append(len(cell)), real(cell))[1])
    for evaluation in ("objective", "judgment"):
        r = golden.parse_upload("set.xlsx", _sheet_of("Expected values", _huge(";"), evaluation=evaluation, rows=3),
                                today=TODAY)
        assert len(_errors_in(r, "Expected values")) == 3
    assert calls == []


def test_a_judgment_row_does_not_cut_its_expected_values_up_at_all(monkeypatch):
    # They are ignored on a judgment case (a warning), so there is nothing to read: the brief's code never split them.
    monkeypatch.setattr(golden, "split_items", lambda cell: pytest.fail("split_items ran on a judgment row"))
    r = _parse(**{"Evaluation": "judgment", "Expected values": "22; 40; 1.5m"})
    assert r.errors == [] and len(r.cases) == 1 and r.cases[0].expected_values == ()
    assert any("ignored on judgment" in w["message"] for w in r.warnings)


@pytest.mark.parametrize("separator", [None, ",", ";"])
def test_one_huge_requirement_ids_string_shared_by_every_row_is_refused_without_tokenising_it(separator):
    r, took = _timed(_sheet_of("Requirement IDs", _huge(separator)))
    assert took < TIME_LIMIT
    assert r.cases == [] and len(_errors_in(r, "Requirement IDs")) == HUGE_ROWS
    assert all(len(e["message"]) < 200 for e in r.errors)


@pytest.mark.parametrize("separator", [None, "\n"])
def test_one_huge_follow_ups_string_shared_by_every_row_is_refused_without_cutting_it_into_lines(separator):
    r, took = _timed(_sheet_of("Follow-ups", _huge(separator)))
    assert took < TIME_LIMIT
    assert r.cases == [] and len(_errors_in(r, "Follow-ups")) == HUGE_ROWS
    assert all(len(e["message"]) < 200 for e in r.errors)


def test_a_huge_question_or_numbered_follow_up_is_not_searched_for_write_intent():
    # Over the 2,000-character limit the row already fails, so the write-intent scan has nothing to decide.
    for column in ("Question", "Follow-up 1"):
        head = ["ID", "Question", "Expected answer", "Follow-up 1"]
        text = _huge(" ")
        body = [[f"C{i}", text if column == "Question" else "How many floors?", "22",
                 text if column == "Follow-up 1" else None] for i in range(HUGE_ROWS)]
        r, took = _timed(make_xlsx([head] + body))
        assert took < TIME_LIMIT
        assert r.cases == [] and len(_errors_in(r, "Question" if column == "Question" else "Follow-ups")) == HUGE_ROWS


def test_one_huge_verified_on_string_shared_by_every_row_is_not_scanned_for_dates():
    # The date patterns scan the whole text: 0.5 s a row for 4 MB, so about 18 minutes for 2,000 rows.
    r, took = _timed(_sheet_of("Verified on", _huge(None)))
    assert took < TIME_LIMIT
    assert r.cases == [] and len(_errors_in(r, "Verified on")) == HUGE_ROWS
    assert all("not a date" in e["message"] and len(e["message"]) < 200 for e in _errors_in(r, "Verified on"))


def test_a_date_with_stray_spaces_and_a_time_still_reads():
    assert _parse(**{"Verified on": "13   October    2026"}).cases[0].verified_on == date(2026, 10, 13)
    assert _parse(**{"Verified on": "2026-10-13T10:15:30.123456+05:30"}).cases[0].verified_on == date(2026, 10, 13)


# the cap must never turn away a cell the column can legally hold, whatever separates its items

def test_the_most_a_column_can_legally_hold_is_still_read():
    values = "\r\n".join('"' + "v" * 198 + '"' for _ in range(golden.MAX_VALUES))        # 40 items of 200, CRLF between
    ids = ", ".join("R" * 40 for _ in range(golden.MAX_REQUIREMENTS))                       # 20 IDs of 40, comma and space
    r = _parse(**{"Expected values": values, "Requirement IDs": ids})
    assert r.errors == [] and len(r.cases[0].expected_values) == golden.MAX_VALUES
    assert len(r.cases[0].requirement_ids) == golden.MAX_REQUIREMENTS
    followups = "\r\n".join("f" * 2000 for _ in range(golden.MAX_FOLLOWUPS))              # 5 follow-ups of 2,000, CRLF between
    data = ('Question,Expected answer,Follow-ups\r\nq,a,"' + followups + '"\r\n').encode()
    r = golden.parse_upload("set.csv", data, today=TODAY)
    assert r.errors == [] and len(r.cases[0].followups) == golden.MAX_FOLLOWUPS


def test_a_cell_past_what_its_column_can_hold_names_the_limit():
    r = _parse(**{"Expected values": "1;" * 5000, "Requirement IDs": "R," * 5000})
    assert any("40" in m and "200 characters" in m for m in _errors(r, "Expected values"))
    assert any("20" in m for m in _errors(r, "Requirement IDs"))
    data = ('Question,Expected answer,Follow-ups\r\nq,a,"' + "f\n" * 6000 + '"\r\n').encode()
    r = golden.parse_upload("set.csv", data, today=TODAY)
    assert any("5" in e["message"] and "2,000" in e["message"] for e in r.errors if e["column"] == "Follow-ups")


# ── casefold of one huge cell, paid once per row ──────────────────────────────────────
# Evaluation, Page/context and Ask as were lower-cased with str.casefold before their length was looked at. casefold
# has a fast path for pure ASCII text; one other character in the cell makes it about 17 times slower, so one shared
# 4,000,000-character string with a single accented letter cost 40 ms a row in each of the three columns: 85 s a
# column at 2,000 rows (a gateway drops a response that is idle for 300 s). Case folding never makes text shorter, so
# a cell longer than the longest value the column accepts cannot fold to one: it is refused on its length alone.

NON_ASCII_CHARS = 600_000
NON_ASCII_ROWS = 2000     # MAX_ROWS: the most one file may carry, each row pointing at the same shared string


def _huge_non_ascii() -> str:
    """Hex digits with one accented letter in the alphabet, so str.casefold cannot use its ASCII fast path."""
    text = "".join(random.Random(11).choices("0123456789abcdeé", k=NON_ASCII_CHARS))
    assert not text.isascii()
    return text


@pytest.mark.parametrize("column, fragment", [
    ("Evaluation", "objective or judgment"),
    ("Page/context", "4,000"),
    ("Ask as", "company-wide"),
])
def test_one_huge_non_ascii_string_shared_by_every_row_is_not_case_folded(column, fragment):
    r, took = _timed(_sheet_of(column, _huge_non_ascii(), rows=NON_ASCII_ROWS))
    assert took < TIME_LIMIT
    assert r.cases == [] and len(_errors_in(r, column)) == NON_ASCII_ROWS
    assert all(fragment in e["message"] and len(e["message"]) < 200 for e in _errors_in(r, column))


def test_one_huge_non_ascii_string_in_all_three_columns_at_once_stays_inside_the_bound():
    head = ["ID", "Question", "Expected answer", "Evaluation", "Page/context", "Ask as"]
    text = _huge_non_ascii()
    data = make_xlsx([head] + [[f"C{i}", "How many floors?", "22", text, text, text] for i in range(NON_ASCII_ROWS)])
    r, took = _timed(data)
    assert took < TIME_LIMIT
    assert r.cases == []
    for column in ("Evaluation", "Page/context", "Ask as"):
        assert len(_errors_in(r, column)) == NON_ASCII_ROWS


def test_case_folding_never_makes_text_shorter():
    # The premise of the length checks above, pinned for every code point: if a Python or Unicode update ever broke
    # it, a long cell could fold to a short word and the checks would turn away a value they used to accept.
    assert all(len(chr(cp).casefold()) >= 1 for cp in range(0x110000))
    for tricky in ("ß", "ﬃ", "İ", "ǰ", "ΐ", "ﷺ", "ſ", "K"):
        assert len(tricky.casefold()) >= len(tricky)


# what each column accepted before the length check, and still accepts, at its longest and one past it

def _parse_column(column: str, text: str):
    return golden.parse_upload("set.xlsx", make_xlsx([["Question", "Expected answer", column], ["q", "a", text]]),
                               today=TODAY)


@pytest.mark.parametrize("text, kind", [
    ("OBJECTIVE", "objective"), ("Objective", "objective"), ("Exact", "objective"), ("FACT", "objective"),
    ("JUDGMENT", "judgment"), ("Judgement", "judgment"), ("Human", "judgment"),
])
def test_evaluation_words_still_read_in_any_case(text, kind):
    r = _parse(**{"Evaluation": text})
    assert r.errors == [] and r.cases[0].evaluation == kind


@pytest.mark.parametrize("text", ["Objectives", "Judgements", "objective!", "ß" * 5, "éxact"])
def test_an_evaluation_word_one_character_too_long_or_not_a_word_is_still_refused(text):
    assert any("objective or judgment" in m for m in _errors(_parse(**{"Evaluation": text}), "Evaluation"))


def test_a_blank_evaluation_is_still_a_warning_not_an_error():
    r = _parse(**{"Evaluation": ""})
    assert r.errors == [] and r.cases[0].evaluation == "objective" and "evaluation_defaulted" in r.cases[0].flags


@pytest.mark.parametrize("text", ["none", "None", "NONE", "nOnE"])
def test_page_context_none_still_means_no_context_in_any_case(text):
    r = _parse_column("Page/context", text)
    assert r.errors == [] and r.cases[0].page_context == ""


@pytest.mark.parametrize("text", ["nones", "no", "none of it", "ǹone"])
def test_page_context_that_is_not_the_word_none_is_kept_as_typed(text):
    r = _parse_column("Page/context", text)
    assert r.errors == [] and r.cases[0].page_context == text


@pytest.mark.parametrize("text", ["company", "Company", "COMPANY"])
def test_ask_as_company_still_reads_in_any_case(text):
    r = _parse_column("Ask as", text)
    assert r.errors == [] and r.cases[0].ask_as == "company"


@pytest.mark.parametrize("text", ["companys", "company-wide", "building", "ＣＯＭＰＡＮＹ", "compañy"])
def test_ask_as_anything_but_company_is_still_refused(text):
    r = _parse_column("Ask as", text)
    assert any("company-wide questions only" in e["message"] for e in r.errors if e["column"] == "Ask as")


# ── header cells ──────────────────────────────────────────────────────────────────────
# The header scan normalises (NFKC, casefold, strip, letters-only) every cell of up to 10 rows x 40 columns, so a
# shared string in those 400 cells is paid 400 times: 12 s at 1,000,000 non-ASCII characters, about 4 minutes at the
# 19,000,000 the reader accepts in a 170 KB file. No column is called anything near 200 characters.

REAL_HEAD = ["ID", "Question", "Expected answer", "Evaluation"]


def test_a_huge_non_ascii_string_in_every_header_scan_cell_does_not_stall_the_header_search():
    text = _huge_non_ascii()
    junk = [[text] * 40 for _ in range(9)]
    r, took = _timed(make_xlsx(junk + [REAL_HEAD, ["C1", "q", "a", "objective"]]))
    assert took < TIME_LIMIT
    assert r.header_row == 10 and len(r.cases) == 1


def test_a_sheet_of_huge_cells_and_no_header_is_refused_quickly():
    r, took = _timed(make_xlsx([[_huge_non_ascii()] * 40 for _ in range(10)]))
    assert took < TIME_LIMIT
    assert r.cases == [] and any("No header row" in e["message"] for e in r.errors)


def test_huge_header_cells_beside_the_real_headers_are_ignored_with_a_short_warning():
    text = _huge_non_ascii()
    r, took = _timed(make_xlsx([REAL_HEAD + [text] * 10, ["C1", "q", "a", "objective"]]))
    assert took < TIME_LIMIT
    assert r.errors == [] and len(r.cases) == 1
    ignored = [w for w in r.warnings if "not used" in w["message"]]
    assert len(ignored) == 10 and all(len(w["message"]) < 200 and len(w["column"]) < 100 for w in ignored)


def test_a_long_source_header_still_reads_up_to_the_cap_and_is_ignored_past_it():
    inside = "Source (" + "x" * 150 + ")"
    outside = "Source (" + "x" * 300 + ")"
    assert len(inside) <= golden._MAX_HEADER < len(outside)
    ok = golden.parse_upload("set.xlsx", make_xlsx([REAL_HEAD + [inside], ["C1", "q", "a", "objective", "a page"]]),
                             today=TODAY)
    assert ok.errors == [] and ok.cases[0].source == "a page"
    past = golden.parse_upload("set.xlsx", make_xlsx([REAL_HEAD + [outside], ["C1", "q", "a", "objective", "a page"]]),
                               today=TODAY)
    assert past.errors == [] and past.cases[0].source is None
    assert any("not used" in w["message"] for w in past.warnings)


def test_headers_still_match_whatever_their_case_spacing_or_punctuation():
    head = ["  QUESTION ", "Expected-Answer", "Eval.", "Page / Context"]
    r = golden.parse_upload("set.xlsx", make_xlsx([head, ["q", "a", "fact", "none"]]), today=TODAY)
    assert r.errors == [] and r.cases[0].page_context == "" and r.cases[0].evaluation == "objective"
