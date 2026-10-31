"""A date filter has to survive the driver, and a period needs a date column.

From Aasim's 30 Oct trace, the question "Show me all the new buildings that have been hoisted in
October 2026, and show me all the details of the data ingested for the most recent building".
The planner did its job -- two steps, a synthesise step and two verify steps -- and both steps
came back empty, so the answer read "The requested October 2026 hoist list could not be
produced." Two different SQL faults, neither of them the planner's:

    invalid input for query argument $1: '2026-10-01' ('str' object has no attribute 'toordinal')
    operator does not exist: character varying >= timestamp with time zone

The first: an ISO date is bound as a Python str under CAST(:p0 AS date). asyncpg encodes a
parameter by its declared type, so it asks the str for toordinal() and the whole statement dies
before Postgres sees it. The cast in the SQL does not save it -- the driver never gets that far.

The second: a period filter ("within last_month") compiles to `col >= <timestamp> AND col <
<timestamp>` with no check that the column is a date at all. data_window_demo is character
varying, and Postgres has no >= between varchar and timestamptz. compile_condition already works
out `numeric_or_date` for exactly this reason and then consults it only for eq/ne.
"""
from __future__ import annotations

import datetime as dt

import pytest

from src.agents.ontology_qa import OntologyError, Params, value_expr


def bound(value, col_type=""):
    """The SQL fragment and the one parameter it bound."""
    p = Params()
    sql = value_expr(value, p, col_type)
    assert len(p.d) == 1, "expected exactly one bound parameter, got %r" % (p.d,)
    return sql, next(iter(p.d.values()))


# -- 1. what is bound under a date cast must be a date ----------------------------------

@pytest.mark.parametrize("iso", ["2026-10-01", "2026-10-31", "2025-01-09"])
def test_an_iso_date_is_bound_as_a_date_not_a_string(iso):
    sql, val = bound(iso)
    assert "AS date" in sql
    assert isinstance(val, dt.date), (
        "bound %r as %s under a date cast; asyncpg encodes by the declared type and asks a str "
        "for toordinal(), which is the error that emptied the October hoist list"
        % (val, type(val).__name__))
    assert val == dt.date.fromisoformat(iso)


def test_a_date_column_value_is_bound_as_a_datetime():
    sql, val = bound("2026-10-01", "timestamp with time zone")
    assert isinstance(val, (dt.date, dt.datetime)), (
        "bound %r as %s under a timestamptz cast" % (val, type(val).__name__))


def test_a_non_date_string_is_still_bound_as_text():
    sql, val = bound("Bishopsgate Tower")
    assert val == "Bishopsgate Tower" and isinstance(val, str)


def test_a_number_is_still_bound_as_a_number():
    sql, val = bound("12", "integer")
    assert "AS numeric" in sql and val == "12.0"


def test_a_boolean_is_still_bound_as_a_boolean():
    sql, val = bound("yes", "boolean")
    assert "AS boolean" in sql and val is True


# -- 2. a period filter needs a column it can compare -----------------------------------

class _Concept:
    table = "buildings"

    def col(self, attribute):
        return attribute


class _Onto:
    def __init__(self, types):
        self.col_types = {("buildings", c): t for c, t in types.items()}


def compile_one(cond, types):
    from src.agents.ontology_qa import compile_condition
    return compile_condition(_Onto(types), _Concept(), "f", cond, Params())


@pytest.mark.parametrize("column,ctype", [
    ("data_window_demo", "character varying"),
    ("notes", "text"),
    ("building_code", "character"),
])
def test_a_period_on_a_text_column_says_so_instead_of_asking_postgres(column, ctype):
    """It cannot be answered, and the model can only act on a reason it understands."""
    with pytest.raises(OntologyError) as exc:
        compile_one({"attribute": column, "within": "last_month"}, {column: ctype})
    assert column in str(exc.value)


def test_a_between_period_on_a_text_column_is_refused_the_same_way():
    with pytest.raises(OntologyError):
        compile_one({"attribute": "data_window_demo", "between": ["last_month", "last_month"]},
                    {"data_window_demo": "character varying"})


@pytest.mark.parametrize("ctype", ["date", "timestamp without time zone",
                                   "timestamp with time zone"])
def test_a_period_on_a_real_date_column_still_compiles(ctype):
    sql = compile_one({"attribute": "created_at", "within": "last_month"}, {"created_at": ctype})
    assert ">=" in sql and "<" in sql and "created_at" in sql


def test_a_between_of_two_dates_on_a_date_column_still_compiles():
    sql = compile_one({"attribute": "created_at", "between": ["2026-10-01", "2026-10-31"]},
                      {"created_at": "date"})
    assert "BETWEEN" in sql


def test_a_column_of_unknown_type_is_left_alone():
    """An empty type is "we do not know", not "text" -- refusing it would break every concept
    whose column types were not loaded."""
    sql = compile_one({"attribute": "created_at", "within": "last_month"}, {})
    assert ">=" in sql
