"""A {placeholder} left in Question or Follow-ups is a row error naming it: the cell is sent word for word as a chat
message, so an untouched template row must never run. 15 Oct 2026. Findings question-bank.md section 5."""
from __future__ import annotations

from datetime import date

from trace_lab import golden

TODAY = date(2026, 10, 15)
HEAD = "ID,Question,Follow-ups,Expected answer,Evaluation\r\n"


def _parse(*rows):
    return golden.parse_upload("g.csv", (HEAD + "".join(r + "\r\n" for r in rows)).encode("utf-8"), today=TODAY)


def test_a_placeholder_in_the_question_is_named():
    r = _parse("1,Is {building} compliant?,,No,objective")
    assert r.cases == []
    msgs = [e for e in r.errors if e["column"] == "Question"]
    assert len(msgs) == 1 and msgs[0]["row"] == 2 and "{building}" in msgs[0]["message"]


def test_every_distinct_placeholder_is_named_once():
    r = _parse("1,{building} or {building_2} or {building},,No,objective")
    (e,) = [e for e in r.errors if e["column"] == "Question"]
    assert "{building}" in e["message"] and "{building_2}" in e["message"]


def test_a_placeholder_in_a_follow_up_is_named():
    r = _parse('1,Is it fine?,"And {vendor}?",No,objective')
    (e,) = [e for e in r.errors if e["column"] == "Follow-ups"]
    assert "{vendor}" in e["message"] and r.cases == []


def test_a_filled_row_and_ordinary_braces_pass():
    r = _parse("1,Is Town Hall compliant?,,No,objective", "2,What is {Mixed Case} or {a b}?,,No,objective")
    assert r.errors == [] and len(r.cases) == 2


def test_the_error_does_not_hide_other_row_errors():
    r = _parse("1,Is {building} compliant?,,,objective")
    cols = {e["column"] for e in r.errors}
    assert cols == {"Question", "Expected answer"}
