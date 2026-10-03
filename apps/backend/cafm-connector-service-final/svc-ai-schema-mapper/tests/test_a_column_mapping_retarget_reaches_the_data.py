"""B14.1: "map this column to X" is the person's decision and the data must land in X.

Until 1 Oct 2026 the overrides were applied in output_generator_node to tier1_mappings /
tier2_auto_accepted / human_approved_mappings — keys MigrationState does not declare, so LangGraph
had dropped them at the first checkpoint and every re-target was silently ignored."""
import pytest

from src.graph.nodes import preprocess_node as pp


@pytest.fixture
def no_db(monkeypatch):
    import src.db as db

    async def _types():
        return {"assets": {"frequency_value": "integer", "frequency_type": "text", "asset_code": "text"}}
    monkeypatch.setattr(db, "get_plenum_cafm_column_types_by_table", _types)


def _state(overrides):
    return {
        "migration_id": None,
        "event_log": [],
        "table_routing": {"Assets": "assets"},
        "full_tables": {"Assets": [{"Tag": "A1", "Freq": "Quarterly"}, {"Tag": "A2", "Freq": "Monthly"}]},
        "tier1_mappings_by_table": {"Assets": [
            {"source_field": "Tag", "target_field": "asset_code"},
            {"source_field": "Freq", "target_field": "frequency_type"},
        ]},
        "column_dest_overrides": overrides,
    }


async def test_a_retarget_moves_the_values_to_the_chosen_column(no_db):
    out = await pp.preprocess_node(_state({"Assets": {"Tag": "serial_number"}}))
    row = out["cleaned_tables"]["Assets"][0]
    assert row["serial_number"] == "A1" and "asset_code" not in row
    m = out["tier1_mappings_by_table"]["Assets"][0]
    assert m["target_field"] == "serial_number" and m["column_mapping_override"] is True


async def test_new_column_lands_under_its_snake_case_name(no_db):
    out = await pp.preprocess_node(_state({"Assets": {"Tag": "__new__"}}))
    assert out["cleaned_tables"]["Assets"][0]["tag"] == "A1"


async def test_an_explicit_choice_is_not_second_guessed_by_the_type_guard(no_db):
    out = await pp.preprocess_node(_state({"Assets": {"Freq": "frequency_value"}}))
    assert out["cleaned_tables"]["Assets"][0]["frequency_value"] == "Quarterly"


async def test_a_column_with_no_tier_mapping_is_still_renamed(no_db):
    st = _state({"Assets": {"Freq": "frequency_type"}})
    st["tier1_mappings_by_table"]["Assets"] = [{"source_field": "Tag", "target_field": "asset_code"}]
    out = await pp.preprocess_node(st)
    assert out["cleaned_tables"]["Assets"][0]["frequency_type"] == "Quarterly"


async def test_no_overrides_changes_nothing(no_db):
    out = await pp.preprocess_node(_state({}))
    assert set(out["cleaned_tables"]["Assets"][0]) == {"asset_code", "frequency_type"}
