"""The Keys & shared attributes gate (B20.1) decides what the write enforces.

Its answers were applied only to state["column_intelligence"] — a report the screens draw —
while everything that builds the SQL artifacts and the write reads the relationships the
hierarchy step re-derives from scratch (29 Oct 2026, "the shared attribute doesn't work when we
customise it"):

* an FK the person demoted to Shared Attribute, or excluded, was still detected and enforced;
* a Shared Attribute promoted to FK existed only if the hierarchy step happened to find it;
* the gate ran without the run's rows (it was missing from BULK_IO, so full_tables came back
  empty once ingest had offloaded it), so a demoted FK never got its lookup table and a promoted
  one never got its FK candidate;
* the PK approved at the PK gate was re-detected when the classification was built, so the gate
  showed the machine's PK, not the person's.

Classification members are "sheet.source column"; the relationships use the sheet and the
column's name after mapping, so the answers are carried across the renames.
"""
from __future__ import annotations

import pytest

from src.graph.migration_graph import bulk_io_for
from src.graph.nodes.hierarchy_node import apply_classification_answers
from src.udr.column_intelligence import apply_classification_decisions


def _edge(st, sc, tt, tc="id", **kw):
    return {"source_table": st, "source_column": sc, "target_table": tt, "target_column": tc,
            "relationship_type": "CONTAINMENT", "customer_confirmed": False, **kw}


def _state(classification, rejected_rows=()):
    return {
        "column_intelligence": {"classification": classification,
                                "classification_rejected_rows": list(rejected_rows)},
        "tier1_mappings_by_table": {
            "Assets": [{"source_field": "Site Code", "target_field": "site_id"},
                       {"source_field": "Supplier", "target_field": "vendor_name"}],
            "Sites": [{"source_field": "Site Code", "target_field": "site_code"}],
            "Vendors": [{"source_field": "Name", "target_field": "vendor_name"}],
        },
    }


COLUMNS = {"Assets": ["site_id", "vendor_name", "asset_code"], "Sites": ["site_code"],
           "Vendors": ["vendor_name", "id"]}


def test_an_fk_demoted_to_shared_is_not_enforced():
    rows = [{"group_id": "g1", "verdict": "Shared Attribute", "human_override": "fk_to_shared",
             "members": ["Assets.Site Code", "Sites.Site Code"]}]
    rels = [_edge("Assets", "site_id", "Sites", "site_code"), _edge("Assets", "asset_code", "Vendors")]
    out, dropped, added = apply_classification_answers(rels, _state(rows), COLUMNS)
    assert [(r["source_table"], r["source_column"]) for r in out] == [("Assets", "asset_code")]
    assert (dropped, added) == (1, 0)


def test_an_excluded_group_is_not_enforced():
    rejected = [{"group_id": "g1", "verdict": "Foreign Key relationship",
                 "members": ["Assets.Site Code", "Sites.Site Code"]}]
    rels = [_edge("Assets", "site_id", "Sites", "site_code")]
    out, dropped, _ = apply_classification_answers(rels, _state([], rejected), COLUMNS)
    assert out == [] and dropped == 1


def test_a_shared_attribute_promoted_to_fk_is_enforced_against_its_pk():
    rows = [{"group_id": "g2", "verdict": "Foreign Key relationship",
             "human_override": "shared_to_fk", "has_pk": "Vendors.Name",
             "members": ["Assets.Supplier", "Vendors.Name"]}]
    out, dropped, added = apply_classification_answers([], _state(rows), COLUMNS)
    assert (dropped, added) == (0, 1)
    (rel,) = out
    assert (rel["source_table"], rel["source_column"], rel["target_table"], rel["target_column"]) \
        == ("Assets", "vendor_name", "Vendors", "vendor_name")
    assert rel["relationship_type"] == "REFERENCE" and rel["customer_confirmed"] is True


def test_a_promotion_is_not_added_twice():
    rows = [{"group_id": "g2", "verdict": "Foreign Key relationship",
             "human_override": "shared_to_fk", "has_pk": "Vendors.Name",
             "members": ["Assets.Supplier", "Vendors.Name"]}]
    rels = [_edge("Assets", "vendor_name", "Vendors", "vendor_name")]
    out, _, added = apply_classification_answers(rels, _state(rows), COLUMNS)
    assert len(out) == 1 and added == 0


def test_untouched_groups_change_nothing():
    rows = [{"group_id": "g3", "verdict": "Foreign Key relationship",
             "members": ["Assets.Site Code", "Sites.Site Code"]}]
    rels = [_edge("Assets", "site_id", "Sites", "site_code")]
    out, dropped, added = apply_classification_answers(rels, _state(rows), COLUMNS)
    assert out == rels and (dropped, added) == (0, 0)


def test_an_excluded_group_is_kept_for_the_steps_after_the_gate():
    ci = {"classification": [{"group_id": "g1", "verdict": "Foreign Key relationship",
                              "members": ["Assets.Site Code", "Sites.Site Code"]}]}
    apply_classification_decisions(ci, None, rejected=["g1"])
    assert ci["classification"] == []
    assert [r["group_id"] for r in ci["classification_rejected_rows"]] == ["g1"]


def test_the_classification_gate_gets_the_runs_rows():
    assert "full_tables" in bulk_io_for("grouping_review_node", {})[0]


async def test_the_classification_is_built_on_the_approved_pk(monkeypatch):
    import src.db as db
    from src.graph.nodes import pre_semantic_review_node as psr
    import src.udr.column_intelligence as ci_mod

    async def _tables():
        return ["assets"]

    async def _empty(*_a, **_k):
        return {}

    seen = {}

    def _capture(tables, **_kw):
        seen.update({n: t["pk"] for n, t in tables.items()})
        return {"classification": []}

    monkeypatch.setattr(psr, "_list_plenum_cafm_tables", _tables)
    monkeypatch.setattr(db, "get_plenum_cafm_columns_by_table", _empty)
    monkeypatch.setattr(db, "get_plenum_cafm_sample_values_by_table", _empty)
    monkeypatch.setattr(ci_mod, "build_column_intelligence", _capture)
    monkeypatch.setattr(psr, "interrupt", lambda _p: {"decisions": {}, "table_overrides": {}})
    # "Serial" is unique (what detection picks); the person approved "Tag".
    rows = [{"Tag": "A", "Serial": f"S{i}"} for i in range(3)] + [{"Tag": "B", "Serial": "S9"}]
    state = {
        "migration_id": None, "event_log": [], "table_routing": {"Assets": "assets"},
        "cafm_table_matches": {"Assets": "assets"}, "unresolved_by_table": {},
        "tier1_mappings_by_table": {"Assets": [
            {"source_field": "Tag", "target_field": "asset_code", "tier": "T1_alias"}]},
        "full_tables": {"Assets": rows}, "extra_fields_config": [],
        "pk_confirmed_by_table": {"Assets": ["Tag"]},
    }
    await psr.pre_semantic_review_node(state)
    assert seen == {"Assets": ["Tag"]}
