"""A synthesised lookup table says so in its name: shared_<entity, plural>.

Asked for 30 Oct. The lookup tables were named after the attribute — `alarm_id`, `status`,
`building_id` — which reads as a column, not a table, and puts a synthesised table into
plenum_cafm under a name indistinguishable from a real entity. `plenum_cafm.alarm_id` is in the
database from this morning's run; nothing about that name says a migration invented it.

So: `shared_` and the plural of the thing. An attribute ending in _id names the entity it
identifies — alarm_id holds alarms — so the suffix comes off and the entity is pluralised:
shared_alarms, shared_buildings, shared_assets. A vocabulary is pluralised as it stands:
shared_statuses, shared_currencies.

The PRIMARY KEY keeps the attribute's own name, because that is what the foreign keys reference
and what the source columns are called: shared_alarms(alarm_id), shared_statuses(status).
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from udr.column_intelligence import synthesize_shared_attribute_table  # noqa: E402


def _synth(canonical, pairs, **kw):
    tables = {}
    for (t, c, vals) in pairs:
        tables.setdefault(t, {"rows": [], "columns": []})
        tables[t]["columns"].append(c)
        tables[t]["rows"] = [{c: v} for v in vals]
    kw.setdefault("vocabulary_only", False)
    return synthesize_shared_attribute_table(
        tables, canonical_name=canonical, group_id="G1",
        member_pairs=[(t, c) for (t, c, _) in pairs], **kw)


class TestTheName:
    @pytest.mark.parametrize("canonical,expected", [
        ("alarm_id", "shared_alarms"),
        ("building_id", "shared_buildings"),
        ("asset_id", "shared_assets"),
        ("schedule_id", "shared_schedules"),
        ("work_order_id", "shared_work_orders"),
        ("meter_id", "shared_meters"),
    ])
    def test_an_identifier_names_the_entity_it_identifies(self, canonical, expected):
        e = _synth(canonical, [("a", "x", ["V-1", "V-2", "V-3"]),
                               ("b", "y", ["V-1", "V-2"])])
        assert e["table_name"] == expected

    @pytest.mark.parametrize("canonical,expected", [
        ("status", "shared_statuses"),
        ("currency", "shared_currencies"),
        ("country", "shared_countries"),
        ("utility", "shared_utilities"),
        ("unit", "shared_units"),
        ("category", "shared_categories"),
    ])
    def test_a_vocabulary_is_pluralised_as_it_stands(self, canonical, expected):
        e = _synth(canonical, [("a", "x", ["one", "two", "three"]),
                               ("b", "y", ["one", "two"])])
        assert e["table_name"] == expected

    def test_the_primary_key_keeps_the_attributes_own_name(self):
        e = _synth("alarm_id", [("a", "x", ["V-1", "V-2"]), ("b", "y", ["V-1"])])
        assert e["table_name"] == "shared_alarms"
        assert e["pk_column"] == "alarm_id", (
            "the foreign keys reference this column and the source columns carry its name")

    def test_the_ddl_uses_both(self):
        e = _synth("status", [("docs", "status", ["Valid", "Expired"]),
                              ("certs", "status", ["Valid"])])
        assert "CREATE TABLE IF NOT EXISTS plenum_cafm.shared_statuses (" in e["create_ddl"]
        assert "status TEXT PRIMARY KEY" in e["create_ddl"]
        assert all("REFERENCES plenum_cafm.shared_statuses (status)" in d for d in e["fk_ddl"])

    def test_the_fk_rewrites_point_at_the_new_name(self):
        e = _synth("status", [("docs", "status", ["Valid", "Expired"]),
                              ("certs", "status", ["Valid"])])
        assert {r["references"] for r in e["fk_rewrites"]} == {"shared_statuses.status"}

    def test_the_comment_still_names_every_source(self):
        e = _synth("status", [("docs", "status", ["Valid", "Expired"]),
                              ("certs", "status", ["Valid"])])
        assert "COMMENT ON TABLE plenum_cafm.shared_statuses IS" in e["create_ddl"]
        assert "docs.status" in e["create_ddl"] and "certs.status" in e["create_ddl"]


class TestItCannotCollide:
    def test_a_source_table_of_that_name_pushes_it_aside(self):
        """Unlikely now, but the no-clobber rule still has to hold."""
        tables = {
            "shared_statuses": {"rows": [{"x": "Valid"}], "columns": ["x"]},
            "docs": {"rows": [{"status": "Valid"}, {"status": "Expired"}], "columns": ["status"]},
        }
        e = synthesize_shared_attribute_table(
            tables, canonical_name="status", group_id="G1",
            member_pairs=[("docs", "status")], vocabulary_only=False)
        assert e["table_name"] == "shared_statuses_ref"
        assert e["pk_column"] == "status"

    def test_the_name_never_collides_with_the_entity_it_came_from(self):
        """shared_assets beside a real `assets` is the whole point of the prefix."""
        e = _synth("asset_id", [("assets", "asset_id", ["A-1", "A-2"]),
                                ("work_orders", "asset_id", ["A-1"])])
        assert e["table_name"] == "shared_assets" and e["table_name"] != "assets"
