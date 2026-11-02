"""A new table says what columns it is created with, and says it once.

Asked for on 2 Nov 2026: "add in backend so that every new table and new column names will
provide that information". The screen that confirms column matching listed a lookup table's
columns from memory in the frontend, which is a second copy of a schema the service already
knows -- free to drift from the one actually created, and with nothing to catch it when it does.

So the entry now carries `columns`, and `create_ddl` is RENDERED from that list rather than
written out beside it. The tests that matter here are the ones that fail if those two ever
become separate statements of the same thing again.

Shape, from write_node._write_shared_attribute_lookups: a surrogate id, the company that owns
the row, the attribute itself, when it was made, and a per-company unique. Not the attribute
alone as PRIMARY KEY -- that was emitted once and never existed in the database.
"""
from __future__ import annotations

import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from udr.column_intelligence import synthesize_shared_attribute_table  # noqa: E402

#: Three sheets sharing building_id, which is what makes it a shared attribute.
MEMBERS = [("bms_alarms", "building_id"), ("compliance_documents", "building_id"),
           ("contracts", "building_id")]


def _tables():
    shared = ["BLD-001", "BLD-002", "BLD-003"]
    out = {}
    for name, _col in MEMBERS:
        out[name] = {
            "rows": [{"building_id": shared[i % len(shared)]} for i in range(30)],
            "columns": ["building_id"],
        }
    return out


@pytest.fixture(scope="module")
def entries():
    entry = synthesize_shared_attribute_table(
        _tables(), canonical_name="building_id", group_id="G1",
        member_pairs=MEMBERS, members_display=[f"{t}.{c}" for (t, c) in MEMBERS])
    return [entry] if entry else []


def test_a_shared_attribute_produces_a_lookup_table(entries):
    """If this fails the fixture stopped producing one and every test below is vacuous."""
    assert entries, "no lookup table was built from three sheets sharing building_id"


def test_the_table_declares_its_columns(entries):
    for t in entries:
        names = [c["name"] for c in (t.get("columns") or [])]
        assert names, "%s declares no columns, so a screen has to invent them" % t["table_name"]
        assert names == ["id", "organization_id", t["pk_column"], "created_at"], names


def test_every_column_says_its_type_and_why_it_is_there(entries):
    for t in entries:
        for c in t["columns"]:
            assert c["type"], c
            assert c["note"], "%s.%s has no note; the panel would show a blank cell" % (
                t["table_name"], c["name"])
            assert "is_primary_key" in c and "nullable" in c, c


def test_exactly_one_column_is_the_primary_key(entries):
    for t in entries:
        pks = [c["name"] for c in t["columns"] if c["is_primary_key"]]
        assert pks == ["id"], pks


def test_the_attribute_itself_is_not_the_primary_key(entries):
    """The shape that was emitted once and never built. A per-company unique carries it."""
    for t in entries:
        attr = next(c for c in t["columns"] if c["name"] == t["pk_column"])
        assert attr["is_primary_key"] is False
        assert t.get("unique_on") == ["organization_id", t["pk_column"]]


def test_a_new_table_says_it_is_new(entries):
    for t in entries:
        assert t.get("is_new") is True


# -- the declaration and the script are one thing -------------------------------------------

def test_the_ddl_creates_exactly_the_columns_that_were_declared(entries):
    """The point of the change. A column added to one and not the other fails here."""
    for t in entries:
        ddl = t["create_ddl"]
        # Only the CREATE's own parens. rindex(")") lands inside the trailing COMMENT ON TABLE.
        body = ddl[ddl.index("(") + 1:ddl.index("\n);")]
        in_ddl = [ln.strip().split()[0] for ln in body.split("\n")
                  if ln.strip() and not ln.strip().upper().startswith("UNIQUE")]
        assert in_ddl == [c["name"] for c in t["columns"]], (
            "%s: DDL creates %r, the entry declares %r" % (
                t["table_name"], in_ddl, [c["name"] for c in t["columns"]]))


def test_each_declared_column_keeps_its_sql_in_the_ddl(entries):
    for t in entries:
        for c in t["columns"]:
            assert c["name"] + " " + c["sql"] in t["create_ddl"], (
                "%s.%s declares %r, which is not what the DDL writes" % (
                    t["table_name"], c["name"], c["sql"]))


def test_the_ddl_is_still_the_shape_the_writer_builds(entries):
    """Rendering it from a list must not have changed what it renders."""
    for t in entries:
        ddl = t["create_ddl"]
        assert ddl.startswith("CREATE TABLE IF NOT EXISTS plenum_cafm.")
        assert "id UUID PRIMARY KEY DEFAULT gen_random_uuid()" in ddl
        assert "organization_id UUID NOT NULL" in ddl
        assert t["pk_column"] + " TEXT NOT NULL" in ddl
        assert "created_at TIMESTAMPTZ NOT NULL DEFAULT now()" in ddl
        assert "UNIQUE (organization_id, %s)" % t["pk_column"] in ddl
        assert re.search(r"COMMENT ON TABLE plenum_cafm\.\w+ IS ", ddl)


def test_the_comment_still_names_the_sources(entries):
    """Asked for on 30 Oct and easy to lose while editing the block it sits in."""
    for t in entries:
        for src in t["source_columns"]:
            assert src in t["create_ddl"], src


# -- the gates that have to carry it ---------------------------------------------------------

def test_the_field_mapping_gate_passes_the_new_tables_through():
    """The pre-semantic gate carried these inside column_intelligence; the fields gate carried
    nothing, so step 5 could not show a table step 3 had already decided to create."""
    import inspect
    from src.graph.nodes import human_review_node as hrn
    src = inspect.getsource(hrn)
    assert '"shared_attribute_tables": list(' in src
    assert '(state.get("column_intelligence") or {}).get("shared_attribute_tables")' in src


def test_the_gate_passes_them_through_rather_than_rebuilding_them():
    """A gate that rebuilt the list would be a third copy of the schema."""
    import inspect
    from src.graph.nodes import human_review_node as hrn
    src = inspect.getsource(hrn)
    assert "build_column_intelligence(" not in src, (
        "the gate builds its own column intelligence; the list it publishes would be a third\n"
        "copy of the schema rather than the one the pre-semantic node already decided")
