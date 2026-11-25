"""A date filter on a column that holds no date is refused while planning, so the model retries.

Production traces, 30 Oct and 2 Nov, count plans over buildings:

    "f"."data_window_demo" >= (date_trunc('month', CURRENT_DATE) - INTERVAL '1 month')
        -> operator does not exist: character varying >= timestamp with time zone
    "f"."hoist_score" BETWEEN CAST(:p0 AS date) AND CAST(:p1 AS date)
        -> operator does not exist: integer >= date
    "f"."created_at" BETWEEN CAST(:p0 AS date) AND CAST(:p1 AS date), {"p0": "2026-10-01"}
        -> invalid input for query argument $1: '2026-10-01' ('str' object has no attribute 'toordinal')

(buildings.data_window_demo is character varying, hoist_score integer, created_at timestamp with
time zone - production plenum_cafm columns.) The third and the text-column period were fixed on 30
Oct (test_a_date_filter_reaches_the_database.py), but only when the SQL was compiled - after
planning, so the step failed instead of being replanned - and only for a text column: an integer
column, or a range (gte, between) with a date on a text column, still reached Postgres.

So (25 Nov 2026) validate_plan refuses a date (a period, today+Nd, YYYY-MM-DD) against any column
whose known type is not a date, and a value that is not a date against a date column, naming the
column and why; plan_question hands that back to the model, which picks a date column. The
compiler refuses the same, for filters added after planning.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json

import pytest

from src.agents import ontology_qa as oq

ONTO = {
    "schema": "plenum_scoped",
    "concepts": {
        "Building": {"table": "buildings", "key": "building_id", "label": "name", "hub": True,
                     "attributes": {"building_id": "building_id", "name": "name",
                                    "data_window_demo": "data_window_demo",
                                    "hoist_score": "hoist_score", "created_at": "created_at",
                                    "is_active": "is_active"}},
    },
    "relations": {},
    "derived": {},
}
TYPES = {"building_id": "uuid", "name": "character varying", "data_window_demo": "character varying",
         "hoist_score": "integer", "created_at": "timestamp with time zone", "is_active": "boolean"}


def onto(types=TYPES):
    o = oq.Ontology(json.loads(json.dumps(ONTO)))
    o.col_types = {("buildings", c): t for c, t in types.items()}
    return o


def plan(*filters):
    return oq.normalise_plan({
        "question_type": "count",
        "focus": {"concept": "Building", "match": None, "states": [],
                  "filters": [{"attribute": a, "op": op, "value": v} for a, op, v in filters]},
        "constraints": [], "include": [], "aggregate": None})


def errors(*filters, types=TYPES):
    return oq.validate_plan(onto(types), plan(*filters))


# -- the three traced filters are refused while planning, naming the column -------------

@pytest.mark.parametrize("flt,column", [
    (("data_window_demo", "within", "last_month"), "data_window_demo"),
    (("data_window_demo", "gte", "start_of_last_month"), "data_window_demo"),
    (("hoist_score", "between", ["2026-10-01", "2026-10-31"]), "hoist_score"),
    (("hoist_score", "within", "last_month"), "hoist_score"),
    (("hoist_score", "gte", "today-30d"), "hoist_score"),
    (("building_id", "eq", "2026-10-01"), "building_id"),
    (("is_active", "eq", "last_month"), "is_active"),
])
def test_a_date_against_a_column_that_holds_no_date_is_refused(flt, column):
    errs = errors(flt)
    assert len(errs) == 1 and column in errs[0] and "not a date column" in errs[0], errs


@pytest.mark.parametrize("value", ["October 2026", "last October", 5])
def test_a_value_that_is_no_date_against_a_date_column_is_refused(value):
    errs = errors(("created_at", "gte", value))
    assert len(errs) == 1 and "created_at" in errs[0], errs


def test_within_with_no_period_is_refused_while_planning_not_when_run():
    errs = errors(("created_at", "within", "the next quarter of the year"))
    assert len(errs) == 1 and "within needs a period" in errs[0] and "next_<N>_days" in errs[0]


@pytest.mark.parametrize("flt", [
    ("created_at", "between", ["2026-10-01", "2026-10-31"]),
    ("created_at", "within", "last_month"),
    ("created_at", "within", "next_60_days"),
    ("created_at", "gte", "today-7d"),
    ("created_at", "lt", "2026-10-01T09:30:00"),
    ("created_at", "eq", "last_month"),
    ("hoist_score", "gte", "70"),
    ("hoist_score", "between", ["60", "80"]),
    ("name", "eq", "Aire Mill Court"),
    ("name", "contains", "Mill"),
    ("data_window_demo", "eq", "2026-10-01"),       # compared as text, which Postgres can do
    ("data_window_demo", "is_null", True),
])
def test_filters_that_can_run_are_left_alone(flt):
    assert errors(flt) == []


def test_a_column_of_unknown_type_is_left_alone():
    assert errors(("hoist_score", "within", "last_month"), types={}) == []


# -- the model is told, and tries again --------------------------------------------------

def test_the_model_is_asked_again_and_its_corrected_plan_is_used():
    first = plan(("hoist_score", "between", ["2026-10-01", "2026-10-31"]))
    second = plan(("created_at", "between", ["2026-10-01", "2026-10-31"]))
    replies, asked = [first, second], []

    async def llm(messages):
        asked.append(messages[-1]["content"])
        return json.dumps(replies[len(asked) - 1])

    got, attempts = asyncio.run(oq.plan_question(onto(), "How many buildings were hoisted in October 2026?",
                                                 llm=llm))
    assert len(asked) == 2 and "hoist_score" in asked[1] and "not a date column" in asked[1]
    assert got["focus"]["filters"][0]["attribute"] == "created_at"
    assert attempts[0]["errors"] and attempts[1]["errors"] == []


# -- and the compiler refuses the same, for a filter added after planning ----------------

def compile_one(attr, op, value):
    o = onto()
    p = oq.Params()
    return oq.compile_condition(o, o.concepts["Building"], "f", {"attribute": attr, op: value}, p), p


@pytest.mark.parametrize("attr,op,value", [
    ("hoist_score", "between", ["2026-10-01", "2026-10-31"]),
    ("hoist_score", "within", "last_month"),
    ("data_window_demo", "gte", "start_of_last_month"),
    ("created_at", "gte", "October 2026"),
])
def test_the_compiler_refuses_what_postgres_or_the_driver_would(attr, op, value):
    with pytest.raises(oq.OntologyError) as exc:
        compile_one(attr, op, value)
    assert attr in str(exc.value) or repr(value) in str(exc.value)


def test_dates_on_a_date_column_are_bound_as_dates():
    sql, p = compile_one("created_at", "between", ["2026-10-01", "2026-10-31"])
    assert "BETWEEN" in sql
    assert sorted(p.d.values()) == [dt.date(2026, 10, 1), dt.date(2026, 10, 31)]
    assert all(isinstance(v, dt.date) for v in p.d.values())
