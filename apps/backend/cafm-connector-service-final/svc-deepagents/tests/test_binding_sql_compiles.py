"""The binder's statements are valid SQL. No database — the dialect compiles them.

This exists because they were not, and nothing said so. `SET building_id = :b::uuid` looks
right and is not: SQLAlchemy's text() mis-parses a bind parameter followed immediately by a
cast, and Postgres answers "syntax error at or near :". The binder catches its own errors on
purpose — a failed link must not lose an upload — so the statement failed on every ingest,
was logged at warning level, and everything around it reported success. Documents went on
landing with building_id NULL while the code meant to set it ran every single time.

A compile check is the cheapest thing that would have caught it, and it needs no connection.
"""
from __future__ import annotations

import re

import pytest
from sqlalchemy import text
from sqlalchemy.dialects import postgresql

from src.services import building_binding

PG = postgresql.dialect(paramstyle="pyformat")

SOURCE = open(building_binding.__file__, encoding="utf-8").read()

#: Every SQL literal in the module, in either quoting style. The first version of this looked
#: only at triple-quoted blocks and silently checked three of the four statements — a guard
#: with a quiet gap in it is the same failure it was written to catch, so it now counts what
#: it finds and fails if that number drops.
_TRIPLE = re.findall(r'"""(.*?)"""', SOURCE, re.S)
_SINGLE = re.findall(r'text\(\s*"([^"\n]{20,})"\s*\)', SOURCE)
_IS_SQL = re.compile(r"\s*(SELECT|UPDATE|INSERT|DELETE)", re.I)
STATEMENTS = [b for b in (_TRIPLE + _SINGLE) if _IS_SQL.match(b)]


def test_every_statement_in_the_module_is_covered():
    # Six today: the registration insert for a file no engine claimed, the session lookup,
    # the building check, the insert that creates a row the owning engine has not written
    # yet, and the two updates. If a statement is added this number goes up; if the
    # extraction breaks it goes down, and either way somebody looks. It has already earned
    # its keep twice, catching each insert the moment it was added.
    assert len(STATEMENTS) == 6, f"found {len(STATEMENTS)}: {[s[:40] for s in STATEMENTS]}"


@pytest.mark.parametrize("sql", STATEMENTS)
def test_each_statement_compiles(sql: str):
    text(sql).compile(dialect=PG)


@pytest.mark.parametrize("sql", STATEMENTS)
def test_no_bind_parameter_is_followed_by_a_cast(sql: str):
    # The exact shape that broke: :name::type. SQLAlchemy produces something from it, and
    # what it produces is not valid Postgres. CAST(:name AS type) is the form that works.
    assert not re.search(r":\w+::", sql), (
        "use CAST(:param AS type) rather than :param::type — the second emits SQL that "
        "Postgres rejects with 'syntax error at or near :'"
    )


def test_a_cast_on_a_column_is_still_fine():
    # Only bind parameters are the problem. document_id::text is ordinary SQL and the rule
    # above must not catch it.
    assert not re.search(r":\w+::", "WHERE document_id::text = ANY(:ids)")
    text("SELECT 1 WHERE CAST(:x AS uuid) = ANY(:ids)").compile(dialect=PG)
