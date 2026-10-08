"""The Evidence tab's rows: a scored work order beside what it was measured against."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

from src.engines.contract_performance.evidence import evidence_row, pick_work_order

SCORE = {
    "id": uuid4(),
    "vendor_id": uuid4(),
    "wo_code": "WO-B101-0007",
    "asset_id": uuid4(),
    "score_month": date(2026, 9, 1),
    "sla_response_met": False,
    "sla_completion_met": True,
    "first_fix": True,
    "recall": False,
    "accreditation_current": True,
    "component_scores": {"criticality": "L1", "criticality_weight": 3.0, "sla_response": 0.0},
    "overall_score": Decimal("62.50"),
    "capped_by_block": False,
    "cost_actual": Decimal("410.00"),
    "cost_estimated": None,
    "cost_variance_pct": None,
}
WO = {
    "priority": "P1",
    "reported_at": "2026-09-03T08:00:00+00:00",
    "attended_at": "2026-09-03T14:30:00+00:00",
    "completed_at": "2026-09-04T08:00:00+00:00",
    "building_id": "b-1",
}
PARAMS = {
    "_contract_confirmed": True,
    "_contract_parameters_id": "cp-1",
    "sla_response_p1_hours": 4,
    "sla_completion_p1_hours": 24,
}


def test_hours_and_targets_come_from_the_work_order_and_the_confirmed_contract():
    row = evidence_row(SCORE, work_order=WO, asset={"asset_name": "AHU-01"},
                       building={"name": "Harbour Point"}, params=PARAMS)
    assert row["wo_code"] == "WO-B101-0007"
    assert row["asset_name"] == "AHU-01"
    assert row["building_name"] == "Harbour Point"
    assert row["criticality"] == "L1"
    assert row["response_hours"] == 6.5 and row["response_target_hours"] == 4.0
    assert row["completion_hours"] == 24.0 and row["completion_target_hours"] == 24.0
    assert row["sla_response_met"] is False
    assert row["overall_score"] == 62.5 and row["cost_actual"] == 410.0
    assert row["score_month"] == "2026-09-01"


def test_no_confirmed_contract_means_no_target_not_a_default():
    row = evidence_row(SCORE, work_order=WO, asset=None, building=None,
                       params={"_contract_confirmed": False, "sla_response_p1_hours": 4})
    assert row["response_target_hours"] is None
    assert row["completion_target_hours"] is None
    assert row["contract_parameters_id"] is None


def test_a_missing_work_order_keeps_the_verdicts_and_leaves_hours_null():
    row = evidence_row(SCORE, work_order=None, asset=None, building=None, params=PARAMS)
    assert row["response_hours"] is None and row["completion_hours"] is None
    assert row["sla_response_met"] is False and row["sla_completion_met"] is True
    assert row["asset_name"] is None and row["building_name"] is None


def test_a_naive_and_an_aware_timestamp_on_one_work_order_still_give_hours():
    # to_jsonb keeps each column's own type: a `timestamp` column comes back with no offset
    # and a `timestamptz` one with it. Subtracting them raised TypeError and 500'd the read.
    mixed = dict(WO, reported_at="2026-09-03T08:00:00", completed_at="2026-09-04T08:00:00+00:00")
    row = evidence_row(SCORE, work_order=mixed, asset=None, building=None, params=PARAMS)
    assert row["completion_hours"] == 24.0
    assert row["response_hours"] == 6.5



def test_an_unparseable_timestamp_blanks_the_field_not_the_list():
    bad = dict(WO, reported_at="01/09/2026")
    row = evidence_row(SCORE, work_order=bad, asset=None, building=None, params=PARAMS)
    assert row["reported_at"] is None and row["response_hours"] is None
    assert row["wo_code"] == "WO-B101-0007"


def test_same_coded_work_orders_resolve_to_the_scored_vendors_or_to_none():
    v = SCORE["vendor_id"]
    ours = {"wo_code": "WO-1", "vendor_id": str(v), "priority": "P1"}
    theirs = {"wo_code": "WO-1", "vendor_id": "someone-else", "priority": "P3"}
    assert pick_work_order([theirs, ours], v) is ours
    assert pick_work_order([theirs], v) is theirs          # one candidate: it is the row
    assert pick_work_order([theirs, dict(theirs)], v) is None   # two strangers: refuse to guess
    assert pick_work_order([], v) is None
