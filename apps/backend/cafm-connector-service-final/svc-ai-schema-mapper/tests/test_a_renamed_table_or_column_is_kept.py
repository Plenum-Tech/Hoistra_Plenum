"""A table or column the person renames at a gate keeps that name in the artifacts and the write.

Four ways the name was lost (29 Oct 2026, "the change in column names or table names doesn't work"):

1. A new table renamed at the routing gate (pre-semantic pass 1) came back under its default
   name: the column-matching gate (pass 2) is a fresh mount that rebuilds every sheet's target
   from the suggestions — a renamed new table is not a real table yet, so it falls back to the
   original guess — and sends that as table_overrides, which pass 2 let win over pass 1.
2. A sheet whose confirmed target differed from Node 1's table match was treated as re-routed
   even when the person had not moved it, so its pass-2 column decisions and renames were thrown
   away and the whole sheet was re-matched from scratch. Node 2's mappings were built against
   Node 2's routing; only a move away from that routing makes them stale.
3. The B14.1 column-mapping answers were applied last in preprocess, so a column renamed after
   them — at the column-matching gate or the field-mapping gate — was renamed back.
4. A column renamed at the column-matching gate to a name the destination does not have kept
   its Node-2 tier, and the writer only creates a new column on a core table for an explicit
   choice, so the column and its data were dropped at write.
"""
from __future__ import annotations

import pytest

import src.db as db
from src.graph.nodes import pre_semantic_review_node as psr
from src.graph.nodes import preprocess_node as pp
from src.graph.nodes.write_node import _collect_approved_new_columns


@pytest.fixture
def no_db(monkeypatch):
    async def _tables():
        return ["vendor_contracts", "vendors"]

    async def _empty(*_a, **_k):
        return {}

    monkeypatch.setattr(psr, "_list_plenum_cafm_tables", _tables)
    monkeypatch.setattr(db, "get_plenum_cafm_columns_by_table", _empty)
    monkeypatch.setattr(db, "get_plenum_cafm_column_types_by_table", _empty)
    monkeypatch.setattr(db, "get_plenum_cafm_sample_values_by_table", _empty)

    async def _no_views():
        return set()

    monkeypatch.setattr(db, "get_plenum_cafm_view_names", _no_views)


def _pass2_state(pending, routing, matches):
    """The state the column-matching gate (pass 2) resumes with — pass 1 already answered."""
    return {
        "migration_id": None,
        "event_log": [],
        "pre_semantic_ci_built": True,
        "column_intelligence": {},
        "pending_table_overrides": pending,
        "table_routing": routing,
        "cafm_table_matches": matches,
        "tier1_mappings_by_table": {
            "Contracts": [
                {"source_field": "Ref", "target_field": "contract_name", "tier": "T1_alias",
                 "confidence": 0.95},
                {"source_field": "Start", "target_field": "contract_start", "tier": "T1_alias",
                 "confidence": 0.95},
            ],
        },
        "unresolved_by_table": {},
        "full_tables": {"Contracts": [{"Ref": "C-1", "Start": "2026-01-01"}]},
        "extra_fields_config": [],
    }


async def _resume_pass2(monkeypatch, state, body):
    monkeypatch.setattr(psr, "interrupt", lambda _payload: body)
    return await psr.pre_semantic_review_node(state)


async def test_a_new_table_renamed_at_routing_keeps_its_name(no_db, monkeypatch):
    state = _pass2_state(
        pending={"Contracts": {"target_table": "contract_register", "is_new_table": True}},
        routing={"Contracts": "contract_register"},
        matches={"Contracts": "vendor_contracts"},
    )
    # What the remounted column-matching gate sends: the suggested default, not the rename.
    out = await _resume_pass2(monkeypatch, state, {
        "decisions": {"Contracts": [{"source_field": "Ref", "decision": "approve",
                                     "target_field": "contract_name"}]},
        "table_overrides": {"Contracts": {"target_table": "vendor_contracts",
                                          "is_new_table": False}},
    })
    assert out["table_routing"]["Contracts"] == "contract_register"
    assert "contract_register" in out["new_tables"]
    assert any(e.get("target_table") == "contract_register" and e.get("is_new_table")
               for e in out["extra_fields_config"])


async def test_the_routing_gate_records_which_sheets_the_person_moved(no_db, monkeypatch):
    state = _pass2_state(pending={}, routing={"Contracts": "vendor_contracts"},
                         matches={"Contracts": None})
    state["pre_semantic_ci_built"] = False
    out = await _resume_pass2(monkeypatch, state, {"decisions": {}, "table_overrides": {
        "Contracts": {"target_table": "vendor_contracts", "is_new_table": False},
        "Vendors": {"target_table": "suppliers", "is_new_table": True},
    }})
    assert out["pending_table_overrides"]["Contracts"]["rerouted"] is False
    assert out["pending_table_overrides"]["Vendors"]["rerouted"] is True
    assert out["table_routing"] == {"Contracts": "vendor_contracts", "Vendors": "suppliers"}


async def test_a_sheet_left_where_node_2_put_it_keeps_its_column_renames(no_db, monkeypatch):
    # Node 1 matched nothing; Node 2 routed by name; the person confirmed that routing as is.
    state = _pass2_state(
        pending={"Contracts": {"target_table": "vendor_contracts", "is_new_table": False,
                               "rerouted": False}},
        routing={"Contracts": "vendor_contracts"},
        matches={"Contracts": None},
    )
    out = await _resume_pass2(monkeypatch, state, {
        "decisions": {"Contracts": [
            {"source_field": "Ref", "decision": "approve", "target_field": "contract_ref"},
            {"source_field": "Start", "decision": "approve", "target_field": "contract_start"},
        ]},
    })
    kept = {m["source_field"]: m for m in out["tier1_mappings_by_table"]["Contracts"]}
    assert kept["Ref"]["target_field"] == "contract_ref"
    assert kept["Ref"].get("user_renamed") is True
    assert not kept["Start"].get("user_renamed")  # confirmed as shown, not renamed
    assert "Ref" not in (out["unresolved_by_table"].get("Contracts") or [])


async def test_a_sheet_the_person_moved_is_still_re_matched(no_db, monkeypatch):
    state = _pass2_state(
        pending={"Contracts": {"target_table": "vendors", "is_new_table": False,
                               "rerouted": True}},
        routing={"Contracts": "vendors"},
        matches={"Contracts": "vendors"},  # Node 1 agreed with the move; Node 2 did not
    )
    out = await _resume_pass2(monkeypatch, state, {"decisions": {"Contracts": []}})
    assert out["tier1_mappings_by_table"]["Contracts"] == []
    assert set(out["unresolved_by_table"]["Contracts"]) == {"Ref", "Start"}


def _pp_state(**kw):
    state = {
        "migration_id": None,
        "event_log": [],
        "table_routing": {"Contracts": "vendor_contracts"},
        "full_tables": {"Contracts": [{"Ref": "C-1", "Kind": "FM"}]},
        "tier1_mappings_by_table": {"Contracts": [
            {"source_field": "Ref", "target_field": "contract_ref", "tier": "T1_alias",
             "user_renamed": True},
        ]},
        "column_dest_overrides": {"Contracts": {"Ref": "contract_name", "Kind": "__new__"}},
        "extra_fields_config": [],
    }
    state.update(kw)
    return state


async def test_a_later_rename_beats_the_column_mapping_answer(no_db):
    out = await pp.preprocess_node(_pp_state())
    row = out["cleaned_tables"]["Contracts"][0]
    assert row["contract_ref"] == "C-1" and "contract_name" not in row
    assert out["tier1_mappings_by_table"]["Contracts"][0]["target_field"] == "contract_ref"


async def test_a_new_column_named_later_beats_the_column_mapping_new_column(no_db):
    out = await pp.preprocess_node(_pp_state(extra_fields_config=[{
        "source_table": "Contracts", "source_field": "Kind", "storage_strategy": "custom",
        "target_table": "vendor_contracts", "custom_column_name": "contract_type",
        "is_new_table": False,
    }]))
    row = out["cleaned_tables"]["Contracts"][0]
    assert row["contract_type"] == "FM" and "kind" not in row


def test_a_column_renamed_at_a_gate_may_be_created_on_a_core_table():
    approved = _collect_approved_new_columns({
        "table_routing": {"Contracts": "vendor_contracts"},
        "tier1_mappings_by_table": {"Contracts": [
            {"source_field": "Ref", "target_field": "contract_ref", "tier": "T1_alias",
             "user_renamed": True},
            {"source_field": "Start", "target_field": "contract_start", "tier": "T1_alias"},
        ]},
    })
    assert approved == {"vendor_contracts": {"contract_ref"}}


# 5. The new-column clash check ran at write time, and the Python write step runs its DDL before
#    the write gate and again from the top on resume: on the second pass the column it had just
#    created looked like a clash, and the data moved to the canonical name (supplier_name ->
#    vendor_name, contract_reference -> contract_ref; scratch end-to-end run, 29 Oct 2026). The
#    check now runs once, in preprocess, against the columns the destination had before the run.

def _clash_state(name):
    return {
        "migration_id": None, "event_log": [],
        "table_routing": {"Assets": "assets"},
        "full_tables": {"Assets": [{"Tag": "A1", "Supplier": "Daikin"}]},
        "tier1_mappings_by_table": {"Assets": [{"source_field": "Tag", "target_field": "asset_code"}]},
        "extra_fields_config": [{"source_table": "Assets", "source_field": "Supplier",
                                 "storage_strategy": "custom", "target_table": "assets",
                                 "custom_column_name": name, "is_new_table": False}],
        "column_intelligence": {"column_canonical": {"Assets.Supplier": "vendor_name"}},
    }


async def test_the_write_step_keeps_a_new_column_it_created_itself(monkeypatch):
    from src.graph.nodes import write_node as wn

    async def _after_ddl():
        return {"assets": {"asset_code", "supplier_name"}}

    monkeypatch.setattr(db, "get_plenum_cafm_columns_by_table", _after_ddl)
    state = _clash_state("supplier_name")
    state["cleaned_tables"] = {"Assets": [{"asset_code": "A1", "supplier_name": "Daikin"}]}
    ddl, renames = await wn._ddl_statements_for(state)
    assert renames == []
    assert state["extra_fields_config"][0]["custom_column_name"] == "supplier_name"
    assert state["cleaned_tables"]["Assets"][0]["supplier_name"] == "Daikin"
    assert any("ADD COLUMN IF NOT EXISTS supplier_name" in d["sql"] for d in ddl)


async def test_preprocess_moves_a_new_column_off_a_real_existing_column(no_db, monkeypatch):
    async def _before_run():
        return {"assets": {"asset_code", "id"}}

    monkeypatch.setattr(db, "get_plenum_cafm_columns_by_table", _before_run)
    out = await pp.preprocess_node(_clash_state("id"))
    assert out["extra_fields_config"][0]["custom_column_name"] == "vendor_name"
    assert out["cleaned_tables"]["Assets"][0]["vendor_name"] == "Daikin"
