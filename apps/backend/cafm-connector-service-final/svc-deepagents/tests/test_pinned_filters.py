"""A reader's query correction is compiled into the record engine's plan, not asked for in prose."""
from __future__ import annotations

from src.agents import ontology_qa as oq
from src.agents import rerun


class _Concept:
    def __init__(self, attrs):
        self.attributes = attrs

    def col(self, attr):
        if attr not in self.attributes:
            raise oq.OntologyError("no attribute " + str(attr))
        return self.attributes[attr]


class _Onto:
    concepts = {"WorkOrder": _Concept({"status": "status", "raised_at": "raised_at", "reported_at": "reported_at", "created_at": "created_at"}),
                "Vendor": _Concept({"vendor_name": "vendor_name"})}


def _plan():
    return {"focus": {"concept": "WorkOrder", "match": None, "states": [], "filters": [{"attribute": "reported_at", "op": "within", "value": "last_month"}]},
            "constraints": [], "include": [], "aggregate": None}


def test_excluded_statuses_become_a_not_in_filter_and_the_period_and_field_are_rewritten():
    plan = _plan()
    notes = oq.apply_pinned(_Onto(), plan, {"exclude_statuses": ["Cancelled", "Draft"], "period": "this_month", "date_field": "raised_at"})
    f = plan["focus"]["filters"]
    assert {"attribute": "status", "op": "not_in", "value": ["Cancelled", "Draft"], "pinned": True} in f
    within = next(x for x in f if x["op"] == "within")
    assert within["attribute"] == "raised_at" and within["value"] == "this_month" and within["pinned"]
    assert any("exclude status Cancelled, Draft" in n for n in notes)
    assert any("dated by raised_at instead of reported_at" in n for n in notes)
    assert any("period this_month instead of last_month" in n for n in notes)


def test_a_pin_the_focus_cannot_take_is_skipped_so_the_plan_stays_valid():
    plan = {"focus": {"concept": "Vendor", "match": None, "states": [], "filters": []}, "constraints": [], "include": [], "aggregate": None}
    assert oq.apply_pinned(_Onto(), plan, {"exclude_statuses": ["Lapsed"], "period": "last_month"}) == []
    assert plan["focus"]["filters"] == []
    # a period with no date filter yet picks the first date attribute the concept has
    plan = {"focus": {"concept": "WorkOrder", "match": None, "states": [], "filters": []}, "constraints": [], "include": [], "aggregate": None}
    notes = oq.apply_pinned(_Onto(), plan, {"period": "last_90_days"})
    assert plan["focus"]["filters"] == [{"attribute": "raised_at", "op": "within", "value": "last_90_days", "pinned": True}]
    assert notes == ["Pinned by the reader: raised_at within last_90_days."]
    assert oq.apply_pinned(_Onto(), _plan(), None) == []


def test_pins_come_only_from_data_corrections_and_merge():
    pins = rerun.pins_from([{"mode": "query", "exclude": ["Cancelled"], "period": None, "field": None},
                            {"mode": "tool", "exclude": ["Draft", "Cancelled"], "period": "this_month"},
                            {"mode": "route", "exclude": ["Held"], "route": "compliance"}])
    assert pins == {"exclude_statuses": ["Cancelled", "Draft"], "period": "this_month", "date_field": None}
    assert rerun.pins_from([{"mode": "query", "text": "x"}]) is None
