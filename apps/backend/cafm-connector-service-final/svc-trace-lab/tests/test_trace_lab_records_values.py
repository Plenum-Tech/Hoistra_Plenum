"""Values and a sentence from a result (18 Nov 2026, Hussain and Bala): a records run (records_run.run_query) becomes the
values a person sees as the expected answer - a count, a figure a record states, groups, records, a record's fields,
linked records, what Hoistra still reads as pending - each pointing at the receipt (the query and its rows) it came
from, and one sentence in fixed words. Pure functions: a fake map and hand-built runs, no database."""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from trace_lab import answer_key as ak
from trace_lab import records_values as rv


def _concept(name, label, identifiers=(), attributes=(), key="id"):
    return SimpleNamespace(name=name, label=label, identifiers=list(identifiers), key=key,
                           attributes={a: a for a in attributes}, states={})


ONTO = SimpleNamespace(concepts={
    "Floor": _concept("Floor", "name", attributes=["id", "name"]),
    "Building": _concept("Building", "name", attributes=["id", "name", "floors"]),
    "WorkOrder": _concept("WorkOrder", "title", ["wo_code"],
                          ["id", "wo_code", "title", "status", "priority", "wo_type", "building_id", "created_at",
                           "updated_at", "sla_due_at", "notes", "cost"]),
    "Vendor": _concept("Vendor", "vendor_name", ["vendor_code"], ["id", "vendor_name", "vendor_code"]),
    "AssetCategory": _concept("AssetCategory", "category_name", attributes=["id", "category_name"]),
})


def _plan(concept, question_type="list", constraints=(), include=(), aggregate=None, states=()):
    return {"question_type": question_type,
            "focus": {"concept": concept, "match": None, "states": list(states), "filters": []},
            "constraints": list(constraints), "include": list(include), "aggregate": aggregate}


PLAN_COUNT_FLOORS = _plan("Floor", "count", [{"concept": "Building", "match": "ABC", "keys": ["b1"], "states": [], "filters": []}])
PLAN_LIST_WOS = _plan("WorkOrder", "list", states=["open"])
PLAN_LIST_WOS_VENDOR = _plan("WorkOrder", "list", states=["open"],
                             include=[{"name": "vendor", "concept": "Vendor", "via": [], "states": [], "filters": []}])
PLAN_SINGLE_WO = _plan("WorkOrder", "single", include=[{"name": "vendor", "concept": "Vendor", "via": [], "states": [], "filters": []}])


def _receipt(n, label, rows=None, row_count=None):
    rows = rows or []
    return {"id": "q" + str(n), "label": label, "sql": "SELECT 1", "params": {},
            "row_count": len(rows) if row_count is None else row_count, "rows": rows, "psql": "SELECT 1;"}


def _run(receipts, result, *, pending=None, found=True, unresolved=()):
    return {"found": found, "unresolved": list(unresolved), "plan": {}, "notes": [], "receipts": receipts,
            "read_at": "2026-11-19T09:00:00+00:00", "result": result, "pending": pending}


RUN_ABC = _run([_receipt(1, "resolve Building (exact)", [{"k": "b1", "l": "ABC"}]),
                _receipt(2, "stated floors on Building", [{"l": "ABC", "v": "25"}]),
                _receipt(3, "count", [{"n": 0}]),
                _receipt(4, "focus Floor")],
               {"kind": "records", "focus": "Floor", "total": 0, "records": [], "paths": []})


def run_with_records(n, *, related=None):
    records = [{"key": "k" + str(i), "attributes": {"wo_code": "WO-" + str(1000 + i), "title": "Fix " + str(i)},
                "context": {}, "related": related or {}} for i in range(n)]
    return _run([_receipt(1, "count", [{"n": n}]), _receipt(2, "focus WorkOrder")],
                {"kind": "records", "focus": "WorkOrder", "total": n, "records": records, "paths": []})


def _kinds(facts):
    return [(f["kind"], f["label"], f["value"], f["receipt"]) for f in facts]


# ── The brief's four tests ───────────────────────────────────────────────────────────────────────────────────────

def test_floors_give_the_count_and_the_figure_the_building_states():
    facts = rv.values_of(ONTO, PLAN_COUNT_FLOORS, RUN_ABC)
    assert _kinds(facts) == [("count", "Floor records found", 0, "q3"), ("stated", "ABC states floors", 25, "q2")]
    assert [f["id"] for f in facts] == ["f1", "f2"]
    assert rv.sentence_of(ONTO, PLAN_COUNT_FLOORS, RUN_ABC, facts) == "ABC states floors: 25. Floor records found: 0."


def test_a_name_not_on_record_is_a_value_not_a_zero():
    run = {**RUN_ABC, "found": False, "unresolved": ["ABC"], "result": None}
    facts = rv.values_of(ONTO, PLAN_COUNT_FLOORS, run)
    assert [(f["kind"], f["value"]) for f in facts] == [("missing", "ABC")]
    assert facts[0]["receipt"] is None, "no reason is invented for a name that was not found"
    assert rv.sentence_of(ONTO, PLAN_COUNT_FLOORS, run, facts) == "Not on record: ABC."


def test_several_names_not_on_record_are_each_a_value():
    run = _run([], None, found=False, unresolved=["ABC", "XYZ", "ABC"])
    facts = rv.values_of(ONTO, PLAN_COUNT_FLOORS, run)
    assert [f["value"] for f in facts] == ["ABC", "XYZ"]
    assert rv.sentence_of(ONTO, PLAN_COUNT_FLOORS, run, facts) == "Not on record: ABC, XYZ."


def test_a_long_list_shows_twenty_and_the_exact_total():
    assert rv.VALUES_SHOWN == 20
    run = run_with_records(37)
    facts = rv.values_of(ONTO, PLAN_LIST_WOS, run)
    assert facts[0] == {"id": "f1", "kind": "count", "label": "Work order records found", "value": 37, "receipt": "q1"}
    records = [f for f in facts if f["kind"] == "record"]
    assert len(records) == 20
    assert records[0] == {"id": "f2", "kind": "record", "label": "", "value": "WO-1000", "receipt": "q2"}
    sentence = rv.sentence_of(ONTO, PLAN_LIST_WOS, run, facts)
    assert sentence.startswith("Work order records found: 37. Work orders: WO-1000, WO-1001, ")
    assert sentence.endswith(", WO-1019, and 17 more.")


def test_a_short_list_has_no_more():
    run = run_with_records(2)
    facts = rv.values_of(ONTO, PLAN_LIST_WOS, run)
    assert rv.sentence_of(ONTO, PLAN_LIST_WOS, run, facts) == "Work order records found: 2. Work orders: WO-1000, WO-1001."


def test_no_records_is_a_zero_with_its_receipt():
    run = run_with_records(0)
    facts = rv.values_of(ONTO, PLAN_LIST_WOS, run)
    assert _kinds(facts) == [("count", "Work order records found", 0, "q1")]
    assert rv.sentence_of(ONTO, PLAN_LIST_WOS, run, facts) == "Work order records found: 0."


# ── Groups, a single record's fields, linked records ─────────────────────────────────────────────────────────────

AGG_PLAN = _plan("WorkOrder", "count", aggregate={"count_by": [{"concept": "Building", "attribute": "name"}]})


def test_groups_give_one_value_per_row_with_the_aggregate_receipt():
    run = _run([_receipt(1, "resolve Building (exact)"), _receipt(2, "aggregate", [{"Building_name": "Bishopsgate Tower", "count": 12},
                                                                                  {"Building_name": "Harbour Point", "count": 4}])],
               {"kind": "aggregate", "rows": [{"Building_name": "Bishopsgate Tower", "count": 12},
                                              {"Building_name": "Harbour Point", "count": 4}], "paths": []})
    facts = rv.values_of(ONTO, AGG_PLAN, run)
    assert _kinds(facts) == [("group", "Bishopsgate Tower", 12, "q2"), ("group", "Harbour Point", 4, "q2")]
    assert [f["id"] for f in facts] == ["f1", "f2"]
    assert rv.sentence_of(ONTO, AGG_PLAN, run, facts) == "Counts: Bishopsgate Tower 12, Harbour Point 4."


def test_a_group_with_no_name_says_which_kind_of_name_is_missing():
    plan = _plan("WorkOrder", "count", aggregate={"count_by": [{"concept": "Vendor", "attribute": "vendor_name"}]})
    rows = [{"Vendor_vendor_name": "Apex Mechanical", "count": 5}, {"Vendor_vendor_name": None, "count": 2}]
    run = _run([_receipt(1, "aggregate", rows)], {"kind": "aggregate", "rows": rows, "paths": []})
    facts = rv.values_of(ONTO, plan, run)
    assert [(f["label"], f["value"]) for f in facts] == [("Apex Mechanical", 5), ("No vendor on record", 2)]


def test_groups_over_twenty_say_how_many_more():
    rows = [{"Building_name": "B" + str(i), "count": 100 - i} for i in range(23)]
    run = _run([_receipt(1, "aggregate", rows[:50])], {"kind": "aggregate", "rows": rows, "paths": []})
    facts = rv.values_of(ONTO, AGG_PLAN, run)
    assert len(facts) == 20
    assert rv.sentence_of(ONTO, AGG_PLAN, run, facts).endswith("B19 81, and 3 more.")


def test_a_single_record_gives_its_non_empty_fields_but_not_its_keys_or_timestamps():
    attrs = {"id": "u-1", "wo_code": "WO-4527", "title": "Chiller fault", "status": "Open", "priority": "P1", "building_id": "b-9",
             "created_at": datetime(2026, 11, 1, tzinfo=timezone.utc), "updated_at": "x", "sla_due_at": datetime(2026, 11, 20, 9, 30),
             "notes": "", "cost": Decimal("120.50"), "wo_type": None}
    result = {"kind": "records", "focus": "WorkOrder", "total": 1,
              "records": [{"key": "u-1", "attributes": attrs, "context": {}, "related": {}}], "paths": []}
    run = _run([_receipt(1, "count", [{"n": 1}]), _receipt(2, "focus WorkOrder")], result)
    facts = rv.values_of(ONTO, PLAN_SINGLE_WO, run)
    assert _kinds(facts) == [
        ("count", "Work order records found", 1, "q1"),
        ("field", "Wo code", "WO-4527", "q2"), ("field", "Title", "Chiller fault", "q2"), ("field", "Status", "Open", "q2"),
        ("field", "Priority", "P1", "q2"), ("field", "Sla due at", "2026-11-20T09:30:00", "q2"), ("field", "Cost", 120.5, "q2")]
    assert rv.sentence_of(ONTO, PLAN_SINGLE_WO, run, facts) == (
        "Work order records found: 1. WO-4527: wo code WO-4527, title Chiller fault, status Open, priority P1, "
        "sla due at 2026-11-20T09:30:00, cost 120.5.")


def test_a_single_question_with_several_records_lists_them():
    run = run_with_records(2)
    facts = rv.values_of(ONTO, PLAN_SINGLE_WO, run)
    assert [f["kind"] for f in facts] == ["count", "record", "record"]


def test_a_single_records_fields_are_capped():
    attrs = {"f" + str(i): "v" + str(i) for i in range(30)}
    result = {"kind": "records", "focus": "Vendor", "total": 1, "records": [{"key": "k", "attributes": attrs, "context": {}, "related": {}}], "paths": []}
    run = _run([_receipt(1, "count", [{"n": 1}]), _receipt(2, "focus Vendor")], result)
    facts = rv.values_of(ONTO, _plan("Vendor", "single"), run)
    assert len([f for f in facts if f["kind"] == "field"]) == 20


def test_linked_records_point_at_their_own_include_query():
    related = {"vendor": [{"vendor_name": "Apex Mechanical", "linked by": "other"}],
               "trade (AssetCategory)": [{"category_name": "HVAC", "linked by": "wo_asset"}], "empty": []}
    run = run_with_records(1, related=related)
    run["receipts"] += [_receipt(3, "include vendor [wo_vendor]"), _receipt(4, "include vendor [other]"),
                        _receipt(5, "include trade [wo_asset]")]
    facts = rv.values_of(ONTO, PLAN_LIST_WOS_VENDOR, run)
    linked = [f for f in facts if f["kind"] == "linked"]
    assert [(f["label"], f["value"], f["receipt"]) for f in linked] == [
        ("WO-1000 → vendor", "Apex Mechanical", "q4"), ("WO-1000 → trade", "HVAC", "q5")]
    assert rv.sentence_of(ONTO, PLAN_LIST_WOS_VENDOR, run, facts).endswith(
        "WO-1000 → vendor: Apex Mechanical. WO-1000 → trade: HVAC.")


def test_linked_records_fall_back_to_a_name_and_the_include_receipt_by_prefix():
    run = run_with_records(1, related={"vendor": [{"name": "Apex Mechanical"}]})
    run["receipts"].append(_receipt(3, "include vendor [some link]"))
    facts = rv.values_of(ONTO, PLAN_LIST_WOS, run)
    assert [(f["kind"], f["label"], f["value"], f["receipt"]) for f in facts if f["kind"] == "linked"] == [
        ("linked", "WO-1000 → vendor", "Apex Mechanical", "q3")]


def test_many_linked_records_show_three_and_the_rest_as_more():
    items = [{"name": "N" + str(i)} for i in range(5)]
    run = run_with_records(1, related={"open_work_orders (WorkOrder)": items})
    facts = rv.values_of(ONTO, PLAN_LIST_WOS, run)
    assert [(f["label"], f["value"]) for f in facts if f["kind"] == "linked"] == [
        ("WO-1000 → open work orders", "N0, N1, N2, and 2 more")], "Hoistra's include name, read as words"


def test_linked_records_are_listed_only_for_the_records_shown():
    run = run_with_records(25, related={"vendor": [{"name": "Apex"}]})
    facts = rv.values_of(ONTO, PLAN_LIST_WOS, run)
    assert len([f for f in facts if f["kind"] == "linked"]) == 20


# ── Stated figures ───────────────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,value", [("25", 25), ("25.5", 25.5), (" 89 ", 89), ("about 20", "about 20"), ("nan", "nan"),
                                        ("1e400", "1e400"), ("1_000", "1_000")])
def test_a_stated_figure_that_is_a_number_becomes_a_number(text, value):
    run = _run([_receipt(1, "stated floors on Building", [{"l": "ABC", "v": text}]), _receipt(2, "count", [{"n": 0}]),
                _receipt(3, "focus Floor")], {"kind": "records", "focus": "Floor", "total": 0, "records": [], "paths": []})
    stated = [f for f in rv.values_of(ONTO, PLAN_COUNT_FLOORS, run) if f["kind"] == "stated"]
    assert [f["value"] for f in stated] == [value]
    assert type(stated[0]["value"]) is type(value)


def test_stated_figures_skip_empty_values_and_name_each_record():
    rows = [{"l": "ABC", "v": "25"}, {"l": "XYZ", "v": ""}, {"l": None, "v": "7"}]
    run = _run([_receipt(1, "stated work_orders on Building", rows), _receipt(2, "count", [{"n": 0}]), _receipt(3, "focus WorkOrder")],
               {"kind": "records", "focus": "WorkOrder", "total": 0, "records": [], "paths": []})
    stated = [(f["label"], f["value"]) for f in rv.values_of(ONTO, PLAN_LIST_WOS, run) if f["kind"] == "stated"]
    assert stated == [("ABC states work orders", 25), ("Building states work orders", 7)]


# ── What Hoistra still reads as pending ──────────────────────────────────────────────────────────────────────────

def _pending_run(*, drop_overdue=False):
    """Main query (q1 resolve, q2 count, q3 focus), then pending_context's own: its open list with includes (q4-q8),
    by category (q9), by vendor (q10), by type (q11), by priority (q12), overdue (q13, q14), blocked (q15, q16)."""
    pending = {"open_total": 13,
               "open_by_trade": {"HVAC": 4, "Fire": 2},
               "open_by_category": [{"name": "HVAC · Boilers", "count": 3}, {"name": "HVAC · AHU", "count": 1}, {"name": "Fire", "count": 2}],
               "open_by_vendor": [{"name": "Apex Mechanical", "count": 5}, {"name": "Unassigned", "count": 1}],
               "open_by_wo_type": [{"name": "Reactive", "count": 9}, {"name": "Planned", "count": 4}],
               "open_by_priority": [{"name": "P1", "count": 3}],
               "overdue_open": 3, "blocked_open": 1, "risk_to_life_open": 2,
               "open_items": [{"wo_code": "WO-1", "risk_to_life": "gas"}], "open_items_note": "showing 1 of 13 open"}
    receipts = [
        _receipt(1, "resolve Building (exact)", [{"k": "b1", "l": "Bishopsgate"}]),
        _receipt(2, "count", [{"n": 13}]), _receipt(3, "focus WorkOrder", [{"__key": "k"}]),
        _receipt(4, "resolve Building (exact)", [{"k": "b1", "l": "Bishopsgate"}]),
        _receipt(5, "count", [{"n": 13}]), _receipt(6, "focus WorkOrder", [{"__key": "k"}]),
        _receipt(7, "include vendor [wo_vendor]"), _receipt(8, "include trade [wo_asset]"),
        _receipt(9, "aggregate", [{"AssetCategory_category_name": "HVAC · Boilers", "count": 3},
                                  {"AssetCategory_category_name": "HVAC · AHU", "count": 1},
                                  {"AssetCategory_category_name": "Fire", "count": 2}]),
        _receipt(10, "aggregate", [{"Vendor_vendor_name": "Apex Mechanical", "count": 5}, {"Vendor_vendor_name": None, "count": 1}]),
        _receipt(11, "aggregate", [{"WorkOrder_wo_type": "Reactive", "count": 9}, {"WorkOrder_wo_type": "Planned", "count": 4}]),
        _receipt(12, "aggregate", [{"WorkOrder_priority": "P1", "count": 3}]),
        _receipt(13, "count", [{"n": 3}]), _receipt(14, "focus WorkOrder", [{"__key": "k"}]),
        _receipt(15, "count", [{"n": 1}]), _receipt(16, "focus WorkOrder", [{"__key": "k"}])]
    if drop_overdue:
        pending["overdue_open"] = None
        receipts = [r for r in receipts if r["id"] not in ("q13", "q14")]
    result = {"kind": "records", "focus": "WorkOrder", "total": 13, "records": [], "paths": []}
    return _run(receipts, result, pending=pending)


def test_every_pending_count_is_a_value_with_its_own_receipt():
    run = _pending_run()
    facts = rv.values_of(ONTO, PLAN_LIST_WOS, run)
    assert _kinds(facts) == [
        ("count", "Work order records found", 13, "q2"),
        ("pending", "Open work orders", 13, "q5"),
        ("pending", "Open work orders by trade: HVAC", 4, "q9"),
        ("pending", "Open work orders by trade: Fire", 2, "q9"),
        ("pending", "Open work orders by vendor: Apex Mechanical", 5, "q10"),
        ("pending", "Open work orders by vendor: Unassigned", 1, "q10"),
        ("pending", "Open work orders by type: Reactive", 9, "q11"),
        ("pending", "Open work orders by type: Planned", 4, "q11"),
        ("pending", "Open work orders by priority: P1", 3, "q12"),
        ("pending", "Overdue open work orders", 3, "q13"),
        ("pending", "Blocked open work orders", 1, "q15")]
    assert not any("risk" in str(f["label"]).lower() for f in facts), "the risk-to-life flag is a keyword match, not a value"
    assert [f["id"] for f in facts] == ["f" + str(i) for i in range(1, 12)]


def test_the_pending_sentence_groups_each_kind_of_count():
    run = _pending_run()
    facts = rv.values_of(ONTO, PLAN_LIST_WOS, run)
    assert rv.sentence_of(ONTO, PLAN_LIST_WOS, run, facts) == (
        "Work order records found: 13. Open work orders: 13. Open work orders by trade: HVAC 4, Fire 2. "
        "Open work orders by vendor: Apex Mechanical 5, Unassigned 1. Open work orders by type: Reactive 9, Planned 4. "
        "Open work orders by priority: P1 3. Overdue open work orders: 3. Blocked open work orders: 1.")


def test_a_pending_query_that_failed_does_not_shift_the_receipts_of_the_others():
    run = _pending_run(drop_overdue=True)
    facts = {f["label"]: f for f in rv.values_of(ONTO, PLAN_LIST_WOS, run)}
    assert "Overdue open work orders" not in facts
    assert facts["Blocked open work orders"]["receipt"] == "q15"
    assert facts["Open work orders by priority: P1"]["receipt"] == "q12"


def test_a_pending_value_with_no_matching_receipt_has_none_not_a_guess():
    run = _pending_run()
    run["receipts"] = [r for r in run["receipts"] if r["id"] not in ("q9", "q10")]
    facts = {f["label"]: f for f in rv.values_of(ONTO, PLAN_LIST_WOS, run)}
    assert facts["Open work orders by trade: HVAC"]["receipt"] is None
    assert facts["Open work orders by vendor: Apex Mechanical"]["receipt"] is None
    assert facts["Open work orders by type: Reactive"]["receipt"] == "q11"


def test_pending_counts_are_not_read_from_the_main_queries_receipts():
    run = _pending_run()
    facts = rv.values_of(ONTO, PLAN_LIST_WOS, run)
    assert facts[0]["receipt"] == "q2", "the answer's own count, not the pending one beside it"
    run["pending"] = {"open_total": 13}
    facts = rv.values_of(ONTO, PLAN_LIST_WOS, run)
    assert [(f["label"], f["receipt"]) for f in facts] == [("Work order records found", "q2"), ("Open work orders", "q5")]


def test_pending_that_found_nothing_open_is_one_zero():
    run = _pending_run()
    run["pending"] = {"open_total": 0}
    run["receipts"] = run["receipts"][:3] + [_receipt(4, "count", [{"n": 0}]), _receipt(5, "focus WorkOrder")]
    facts = rv.values_of(ONTO, PLAN_LIST_WOS, run)
    assert _kinds(facts)[-1] == ("pending", "Open work orders", 0, "q4")


def test_an_aggregate_with_pending_reads_its_groups_from_the_first_aggregate():
    rows = [{"Building_name": "Bishopsgate Tower", "count": 12}]
    run = _run([_receipt(1, "aggregate", rows), _receipt(2, "count", [{"n": 12}]), _receipt(3, "focus WorkOrder"),
                _receipt(4, "aggregate", [{"WorkOrder_priority": "P1", "count": 3}])],
               {"kind": "aggregate", "rows": rows, "paths": []}, pending={"open_total": 12, "open_by_priority": [{"name": "P1", "count": 3}]})
    facts = rv.values_of(ONTO, AGG_PLAN, run)
    assert _kinds(facts) == [("group", "Bishopsgate Tower", 12, "q1"), ("pending", "Open work orders", 12, "q2"),
                             ("pending", "Open work orders by priority: P1", 3, "q4")]


def test_pending_groups_over_twenty_are_cut_to_twenty():
    run = _pending_run()
    run["pending"] = {"open_total": 40, "open_by_vendor": [{"name": "V" + str(i), "count": 40 - i} for i in range(30)]}
    facts = rv.values_of(ONTO, PLAN_LIST_WOS, run)
    assert len([f for f in facts if f["label"].startswith("Open work orders by vendor")]) == 20


# ── What a run says when there is nothing to show ────────────────────────────────────────────────────────────────

def test_a_run_with_no_result_has_no_values_and_a_plain_sentence():
    run = _run([], None)
    assert rv.values_of(ONTO, PLAN_LIST_WOS, run) == []
    assert rv.sentence_of(ONTO, PLAN_LIST_WOS, run, []) == "Nothing was found in the records."


def test_a_concept_the_map_does_not_hold_still_reads():
    plan = _plan("Mystery", "list")
    result = {"kind": "records", "focus": "Mystery", "total": 1,
              "records": [{"key": "k", "attributes": {"name": "Thing"}, "context": {}, "related": {}}], "paths": []}
    run = _run([_receipt(1, "count", [{"n": 1}]), _receipt(2, "focus Mystery")], result)
    facts = rv.values_of(ONTO, plan, run)
    assert _kinds(facts) == [("count", "Mystery records found", 1, "q1"), ("record", "", "Thing", "q2")]
    assert rv.sentence_of(ONTO, plan, run, facts) == "Mystery records found: 1. Mysteries: Thing."


def test_the_run_is_not_changed_and_the_values_are_safe_to_store():
    import copy
    import json

    run = _pending_run()
    before = copy.deepcopy(run)
    json.dumps(rv.values_of(ONTO, PLAN_LIST_WOS, run))
    assert run == before
    attrs = {"wo_code": "WO-1", "sla_due_at": date(2026, 11, 20), "cost": Decimal("1.5")}
    result = {"kind": "records", "focus": "WorkOrder", "total": 1, "records": [{"key": "k", "attributes": attrs, "context": {}, "related": {}}], "paths": []}
    json.dumps(rv.values_of(ONTO, _plan("WorkOrder", "single"), _run([_receipt(1, "count", [{"n": 1}])], result)))


def test_grouped_keeps_its_building_wording_by_default():
    rows = [{"Building_name": None, "count": 1}]
    assert ak._grouped({}, {"rows": rows}, [_receipt(1, "aggregate")])[0]["label"] == "No building on record"
    assert ak._grouped({}, {"rows": rows}, [_receipt(1, "aggregate")], empty="No vendor on record")[0]["label"] == "No vendor on record"


# ── What changed since Hoistra read ──────────────────────────────────────────────────────────────────────────────

def _span(label, row_count):
    return {"type": "sql", "input": {"label": label, "sql": "SELECT 1"}, "output": {"row_count": row_count}}


def test_a_query_whose_row_count_moved_since_hoistra_read_is_reported():
    receipts = [_receipt(1, "count", [{"n": 13}]), _receipt(2, "focus WorkOrder", row_count=13), _receipt(3, "include vendor [x]", row_count=7)]
    recorded = [_span("count", 1), _span("focus WorkOrder", 12), _span("include vendor [x]", 7)]
    assert rv.changed_since(receipts, recorded) == [{"label": "focus WorkOrder", "then": 12, "now": 13}]


def test_the_same_label_is_matched_in_order():
    receipts = [_receipt(1, "count", row_count=1), _receipt(2, "aggregate", row_count=3), _receipt(3, "aggregate", row_count=4),
                _receipt(4, "aggregate", row_count=9)]
    recorded = [_span("aggregate", 3), _span("aggregate", 5), _span("count", 1)]
    assert rv.changed_since(receipts, recorded) == [{"label": "aggregate", "then": 5, "now": 4}]


def test_a_query_hoistra_never_ran_or_that_failed_there_is_not_a_change():
    receipts = [_receipt(1, "count", row_count=1), _receipt(2, "resolve X (exact)", row_count=2)]
    recorded = [_span("count", None), _span("something else", 9)]
    assert rv.changed_since(receipts, recorded) == []
    # a query that failed for Hoistra left no row count and no receipt here: the next one with its label still pairs up
    receipts = [_receipt(1, "aggregate", row_count=4)]
    assert rv.changed_since(receipts, [_span("aggregate", None), _span("aggregate", 3)]) == [{"label": "aggregate", "then": 3, "now": 4}]
    assert rv.changed_since([], recorded) == [] and rv.changed_since(receipts, []) == []


def test_recorded_queries_may_be_flat_and_odd_ones_are_ignored():
    receipts = [_receipt(1, "count", row_count=2)]
    assert rv.changed_since(receipts, [{"label": "count", "row_count": 1}]) == [{"label": "count", "then": 1, "now": 2}]
    assert rv.changed_since(receipts, [{"input": {"label": "count"}, "output": {"row_count": "1"}}]) == []
    assert rv.changed_since(receipts, [{"input": "x", "output": None}, "junk", None]) == []
