"""A mode may be confirmed only when its contract is whole (boundary, evaluator, requirement, 3 positives, 3 close
negatives or a waiver), and a cited requirement that moved on flags the mode (spec §5.3, §8). Pure."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from trace_lab import labels, taxonomy

NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)
ACTIVE = {"WO-1": {"revision": 2, "id": "r2", "created_at": NOW}}


def _cell(state, approval="single"):
    return labels.Cell(state, "l", "human_structured_label", approval, None, True)


def _whole(**over):
    mode = {"boundary": "Cancelled jobs counted as open.", "evaluator_type": "judge", "requirement_ids": ["WO-1"],
            "positive_turn_ids": ["p1", "p2", "p3"], "close_negative_turn_ids": ["n1", "n2", "n3"],
            "close_negatives_waiver": None}
    mode.update(over)
    return mode


CELLS = {**{t: _cell("present") for t in ("p1", "p2", "p3")}, **{t: _cell("absent") for t in ("n1", "n2", "n3")}}


def test_a_whole_contract_has_nothing_missing():
    assert taxonomy.contract_missing(_whole(), CELLS, ACTIVE) == []


@pytest.mark.parametrize("over,item", [
    ({"boundary": "   "}, "boundary"),
    ({"boundary": None}, "boundary"),
    ({"evaluator_type": None}, "evaluator_type"),
    ({"evaluator_type": "vibes"}, "evaluator_type"),
    ({"requirement_ids": []}, "requirement"),
    ({"requirement_ids": ["WO-9"]}, "requirement"),
    ({"positive_turn_ids": ["p1", "p2"]}, "positives"),
    ({"positive_turn_ids": ["p1", "p1", "p2"]}, "positives"),
    ({"close_negative_turn_ids": ["n1", "n2"]}, "close_negatives"),
])
def test_each_missing_item_is_named(over, item):
    assert taxonomy.contract_missing(_whole(**over), CELLS, ACTIVE) == [item]


def test_every_item_can_be_missing_at_once_in_contract_order():
    out = taxonomy.contract_missing({"boundary": "", "requirement_ids": []}, {}, {})
    assert out == list(taxonomy.CONTRACT_ITEMS)


def test_a_waiver_stands_in_for_close_negatives_but_a_blank_one_does_not():
    few = {"close_negative_turn_ids": []}
    assert taxonomy.contract_missing(_whole(**few, close_negatives_waiver="No near misses exist yet."), CELLS,
                                     ACTIVE) == []
    assert taxonomy.contract_missing(_whole(**few, close_negatives_waiver="  "), CELLS, ACTIVE) == ["close_negatives"]


def test_only_single_approved_cells_in_the_right_state_count():
    cells = dict(CELLS)
    cells["p3"] = _cell("present", approval="bulk")
    cells["n3"] = _cell("present")                     # listed as a negative but labelled present
    assert taxonomy.contract_missing(_whole(), cells, ACTIVE) == ["positives", "close_negatives"]
    cells.pop("p2")                                     # an unlabelled turn is no positive
    assert taxonomy.contract_missing(_whole(), cells, ACTIVE) == ["positives", "close_negatives"]


def test_a_requirement_revised_since_confirmation_flags_the_mode():
    mode = {"requirement_ids": ["WO-1"]}
    flags = taxonomy.requirement_flags(mode, {"WO-1": 1}, ACTIVE)
    assert [(f["code"], f["kind"], f["confirmed_revision"], f["active_revision"]) for f in flags] == [
        ("WO-1", "revised", 1, 2)]
    assert taxonomy.requirement_flags(mode, {"WO-1": 2}, ACTIVE) == []
    assert taxonomy.requirement_flags(mode, None, ACTIVE) == []


def test_a_withdrawn_requirement_flags_the_mode_even_before_it_was_confirmed():
    flags = taxonomy.requirement_flags({"requirement_ids": ["WO-1", "EN-4"]}, None, ACTIVE)
    assert flags == [{"code": "EN-4", "kind": "not_active", "text": "EN-4 is no longer an active requirement."}]


def test_row_dict_turns_ids_into_text_and_nulls_into_empty_lists():
    import uuid

    a, b = uuid.uuid4(), uuid.uuid4()
    out = taxonomy.row_dict({"id": a, "merged_into": b, "split_from": None, "created_from": [b], "requirement_ids": None,
                             "positive_turn_ids": ["t"], "close_negative_turn_ids": None,
                             "proposer_organization_id": a, "decided_by": None})
    assert out["id"] == str(a) and out["merged_into"] == str(b) and out["split_from"] is None
    assert out["created_from"] == [str(b)] and out["requirement_ids"] == [] and out["close_negative_turn_ids"] == []
    assert out["proposer_organization_id"] == str(a) and out["decided_by"] is None


def test_payload_dates_become_text_and_levels_are_named():
    out = taxonomy.payload({"id": "m", "level": "data", "agent": "compliance", "created_at": NOW})
    assert out["created_at"] == NOW.isoformat() and out["judge_eligible"] is False
    assert out["agent_label"] == "Compliance" and out["level_label"].startswith("Data fault")


def test_json_value_reads_text_and_passes_decoded_values_through():
    assert taxonomy.json_value('[{"a": 1}]') == [{"a": 1}] and taxonomy.json_value([1]) == [1]
    assert taxonomy.is_uuid("8e7f1b1e-0000-4000-8000-000000000001") and not taxonomy.is_uuid("nope")
    assert not taxonomy.is_uuid(None)


def test_names_follow_the_pattern():
    ok = ["counts_cancelled_jobs", "abc"]
    bad = ["Ab", "ab", "1abc", "has space", "x" * 65, "has-dash"]
    assert all(taxonomy.NAME_RE.match(n) for n in ok) and not any(taxonomy.NAME_RE.match(n) for n in bad)
