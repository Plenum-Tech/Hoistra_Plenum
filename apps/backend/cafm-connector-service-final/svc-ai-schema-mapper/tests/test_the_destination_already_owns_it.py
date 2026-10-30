"""A column the destination schema already owns is a foreign key, not a new lookup table.

Reported 30 Oct: building_id and vendor_id were offered as Shared Attributes, each promising a
new lookup table. plenum_cafm.buildings and plenum_cafm.vendors already exist and own exactly
those values, so the run would have created plenum_cafm.building_id beside the real buildings
table and pointed every building_id column at the copy.

The classifier only ever looked at the workbook. The verdict is decided by whether a MEMBER of
the group is a primary key, and in that upload there was no `building` sheet at all — building_id
appeared only in bms_alarms, compliance_documents and contracts. No member is a key, so the rule
fell through to "Shared Attribute" and synthesis followed. Correct about the spreadsheet, wrong
about the database it is migrating into.

So before calling a group shared, ask the destination whether it already owns the attribute.
Entity name first — strip _id and look for that table, singular or plural — because the PK-name
rule alone misfires: plenum_cafm.chiller_design_specs has asset_id as its primary key, so asking
"which table has a PK called asset_id" answers chiller_design_specs, not assets. The PK rule is
the fallback, and only when exactly one table matches.

vendor_id is why the fallback is needed at all in the other direction: vendors' primary key is
`id`, not `vendor_id`, so the name match has to supply the key column from the table it found.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from udr.column_intelligence import destination_entity_for  # noqa: E402

#: A slice of the real plenum_cafm schema, including the trap.
DEST_PK = {
    "buildings": ["building_id"],
    "vendors": ["id"],
    "contracts": ["contract_id"],
    "assets": ["id"],
    "chiller_design_specs": ["asset_id"],     # PK named asset_id, but not the asset entity
    "work_orders": ["work_order_id"],
    "spaces": ["space_id"],
    "meters": ["meter_id"],
}
DEST_TABLES = set(DEST_PK)


def _own(name):
    return destination_entity_for(name, DEST_TABLES, DEST_PK)


class TestTheReportedColumns:
    def test_building_id_belongs_to_buildings(self):
        assert _own("building_id") == ("buildings", "building_id")

    def test_vendor_id_belongs_to_vendors_under_its_own_key_name(self):
        # vendors' key is `id`; the FK must target that, not a column called vendor_id.
        assert _own("vendor_id") == ("vendors", "id")

    def test_contract_id_belongs_to_contracts(self):
        assert _own("contract_id") == ("contracts", "contract_id")


class TestTheEntityNameWinsOverThePrimaryKeyName:
    def test_asset_id_resolves_to_assets_not_the_table_keyed_on_it(self):
        assert _own("asset_id") == ("assets", "id"), (
            "chiller_design_specs is keyed on asset_id but is not the asset entity")

    def test_the_pk_rule_still_catches_what_the_name_rule_misses(self):
        # No table called `alarm`/`alarms`, but exactly one is keyed on alarm_id.
        pk = {**DEST_PK, "bms_alarms": ["alarm_id"]}
        assert destination_entity_for("alarm_id", set(pk), pk) == ("bms_alarms", "alarm_id")

    def test_an_ambiguous_pk_name_resolves_to_nothing(self):
        pk = {"a_tbl": ["ref_id"], "b_tbl": ["ref_id"]}
        assert destination_entity_for("ref_id", set(pk), pk) is None, (
            "two tables keyed on the same name: guessing one would point the FK at a guess")


class TestItStaysOutOfTheWay:
    @pytest.mark.parametrize("name", ["country", "status", "currency", "utility", "data_provenance"])
    def test_a_real_vocabulary_is_untouched(self, name):
        assert _own(name) is None, f"{name} is a lookup table, not an entity reference"

    @pytest.mark.parametrize("name", ["id", "code", "key", "no", "number", "pk"])
    def test_a_generic_name_resolves_to_nothing(self, name):
        assert _own(name) is None

    @pytest.mark.parametrize("name", ["", None, "   "])
    def test_an_empty_name_resolves_to_nothing(self, name):
        assert _own(name) is None

    def test_nothing_resolves_without_a_destination_schema(self):
        assert destination_entity_for("building_id", set(), {}) is None
        assert destination_entity_for("building_id", None, None) is None

    def test_a_table_that_does_not_exist_is_not_invented(self):
        assert _own("gadget_id") is None

    def test_matching_ignores_case(self):
        assert destination_entity_for("BUILDING_ID", {"Buildings"}, {"buildings": ["building_id"]}) \
            == ("Buildings", "building_id")


class TestTheVerdictAndWhatFollowsFromIt:
    """End to end through build_column_intelligence, on the reported shape."""

    def _tables(self):
        # building_id in three tables and no `building` sheet — the reported upload.
        ids = ["BLD-0001", "BLD-0002", "BLD-0003", "BLD-0004"]

        def rows(prefix, bids):
            return [{f"{prefix}_id": f"{prefix.upper()}-{i:04d}", "building_id": b,
                     "note": f"row {i}"} for i, b in enumerate(bids, 1)]

        return {
            "bms_alarms": {"rows": rows("alarm", ids),
                           "columns": ["alarm_id", "building_id", "note"]},
            "contracts": {"rows": rows("contract", ids[:3]),
                          "columns": ["contract_id", "building_id", "note"]},
            "compliance_documents": {"rows": rows("document", ids[1:]),
                                     "columns": ["document_id", "building_id", "note"]},
        }

    def _ci(self, **kw):
        from udr.column_intelligence import build_column_intelligence
        return build_column_intelligence(self._tables(), **kw)

    def _row(self, ci):
        return next((r for r in ci["classification"]
                     if r["canonical_name"] == "building_id"), None)

    def test_without_the_destination_it_is_still_a_shared_attribute(self):
        """The old behaviour, unchanged when the schema cannot be read."""
        row = self._row(self._ci())
        assert row is not None and row["verdict"] == "Shared Attribute"
        assert row.get("owned_by_destination") is None

    def test_with_the_destination_it_becomes_a_foreign_key(self):
        row = self._row(self._ci(dest_pk_by_table=DEST_PK, dest_columns_by_table={}))
        assert row is not None
        assert row["verdict"] == "Foreign Key relationship"
        assert row["owned_by_destination"] == "buildings.building_id"
        assert row["has_pk"] == "buildings.building_id"

    def test_no_lookup_table_is_synthesised_for_it(self):
        ci = self._ci(dest_pk_by_table=DEST_PK, dest_columns_by_table={})
        names = [t["table_name"] for t in ci.get("shared_attribute_tables") or []]
        assert "building_id" not in names, (
            "a lookup table here is a second copy of plenum_cafm.buildings")

    def test_every_member_column_gets_a_foreign_key_to_the_real_table(self):
        ci = self._ci(dest_pk_by_table=DEST_PK, dest_columns_by_table={})
        fks = [f for f in ci["fk_candidates"] if f["dst_table"] == "buildings"]
        assert {f["src_table"] for f in fks} == {
            "bms_alarms", "contracts", "compliance_documents"}
        assert all(f["dst_column"] == "building_id" and f["confirmed"] for f in fks)

    def test_the_group_counts_follow_the_verdict(self):
        ci = self._ci(dest_pk_by_table=DEST_PK, dest_columns_by_table={})
        assert ci["summary"]["fk_groups"] >= 1
        assert all(r["verdict"] != "Shared Attribute"
                   for r in ci["classification"] if r["canonical_name"] == "building_id")
