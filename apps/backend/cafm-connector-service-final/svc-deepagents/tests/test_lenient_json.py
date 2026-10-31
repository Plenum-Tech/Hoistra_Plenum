"""The compliance analyst's JSON, read as the model meant it (agents/lenient_json.py).

A slip a strict reader refuses (a trailing comma, a key with no value, a raw line break in a
string) used to cost the whole analysis. These pin that the slip is repaired without changing any
value, that text inside strings is never touched, and that a cut-off answer is never completed.
"""
from __future__ import annotations

import json

import pytest

from src.agents import lenient_json


def test_valid_json_is_read_as_it_is():
    assert lenient_json.loads('{"a": [1, 2], "b": "x"}') == ({"a": [1, 2], "b": "x"}, [])


def test_a_trailing_comma_is_dropped():
    value, slips = lenient_json.loads('{"actions": [{"title": "Book it"}, ], "kpis": [3, 4,],}')
    assert value == {"actions": [{"title": "Book it"}], "kpis": [3, 4]}
    assert set(slips) == {"trailing comma"}


def test_a_missing_value_is_null_not_a_guess():
    value, slips = lenient_json.loads('{"days": , "status": "lapsed", "owner": }')
    assert value == {"days": None, "status": "lapsed", "owner": None}
    assert "missing value" in slips


def test_text_inside_strings_is_never_changed():
    raw = '{"text": "A list [a, b, ] and a key: , stays", "n": [1,]}'
    value, _ = lenient_json.loads(raw)
    assert value["text"] == "A list [a, b, ] and a key: , stays"
    assert value["n"] == [1]
    escaped = lenient_json.loads('{"t": "a \\\\\\" quote, ]", "x": [2,]}')[0]
    assert escaped["t"] == 'a \\" quote, ]'


def test_a_raw_line_break_in_a_string_is_kept():
    value, slips = lenient_json.loads('{"narrative": "Line one\nLine two"}')
    assert value == {"narrative": "Line one\nLine two"} and slips


def test_a_cut_off_answer_is_never_completed():
    with pytest.raises(json.JSONDecodeError):
        lenient_json.loads('{"actions": [{"title": "Book the')
    with pytest.raises(json.JSONDecodeError):
        lenient_json.loads('{"kpis": [1, 2')
