"""Vendors AT a building, asked "based on its assets and work orders", plan first time (25 Nov 2026).

Production trace, 25 Nov, the udr agent: "Which vendors are recorded as working at Aire Mill Court
(B-304), based on its assets and work orders?" came back

    Could not build a valid plan: include[0]: via ['ASSET_VENDOR', 'ASSET_BUILDING'] from Vendor
    ends at Building, not Asset; include[1]: via ['WORK_ORDER_ASSIGNED_VENDOR',
    'WORK_ORDER_BUILDING'] from Vendor ends at Building, not WorkOrder

on every attempt. The model wrote "at the building" as the tail of each include's route: the
vendor's assets, ON TO the building. An include's route has to end at its own records, so the
plan was refused three times, the agent fell back to hand-written SQL, and the person was told
"Not answered".

The route is not wrong, it is two things at once: the records to fetch (the vendor's assets) and
a place the focus is restricted to (the building). So the plan is repaired before it is checked:
the include keeps the route to its own records, lists only those at the place, and the place is a
constraint - the one already in the plan, or one made from the name the include carried. Without
a name or a constraint for the place there is nothing to restrict to, and the plan is refused as
before rather than answered for every vendor.
"""
from __future__ import annotations

import asyncio
import json

from src.agents import ontology_qa as oq

BID = "c68acb8c-828b-473d-8abe-bdb2ba7fe5b6"
QUESTION = ("Which vendors are recorded as working at Aire Mill Court (B-304), based on its "
            "assets and work orders?")

ONTO = {
    "schema": "plenum_scoped",
    "concepts": {
        "Vendor": {"table": "vendors", "key": "id", "label": "vendor_name",
                   "identifiers": ["vendor_code"], "search": ["vendor_name"],
                   "attributes": {"id": "id", "vendor_name": "vendor_name", "trade": "trade"}},
        "Building": {"table": "buildings", "key": "building_id", "label": "name", "hub": True,
                     "identifiers": ["building_code"], "search": ["name"],
                     "attributes": {"building_id": "building_id", "name": "name"}},
        "WorkOrder": {"table": "work_orders", "key": "id", "label": "title", "identifiers": ["wo_code"],
                      "attributes": {"id": "id", "title": "title", "status": "status"}},
        "PPMVisit": {"table": "ppm_visits", "key": "id", "label": "ppm_ref",
                     "attributes": {"id": "id", "ppm_ref": "ppm_ref"}},
        "Asset": {"table": "assets", "key": "id", "label": "asset_name", "hub": True,
                  "attributes": {"id": "id", "asset_name": "asset_name"}},
    },
    "relations": {
        "PPM_VISIT_VENDOR": {"from": "PPMVisit", "to": "Vendor", "cardinality": "many_to_one",
                             "join": ["from.vendor_id = to.id"], "fill": 1.0},
        "PPM_VISIT_BUILDING": {"from": "PPMVisit", "to": "Building", "cardinality": "many_to_one",
                               "join": ["from.building_code = to.building_code"], "fill": 1.0},
        "WORK_ORDER_ASSIGNED_VENDOR": {"from": "WorkOrder", "to": "Vendor", "cardinality": "many_to_one",
                                       "join": ["from.assigned_vendor = to.id"], "fill": 0.0},
        "WORK_ORDER_VENDOR": {"from": "WorkOrder", "to": "Vendor", "cardinality": "many_to_one",
                              "join": ["from.vendor_id = to.id"], "fill": 0.669},
        "WORK_ORDER_BUILDING": {"from": "WorkOrder", "to": "Building", "cardinality": "many_to_one",
                                "join": ["from.building_id = to.building_id"], "fill": 0.669},
        "ASSET_VENDOR": {"from": "Asset", "to": "Vendor", "cardinality": "many_to_one",
                         "join": ["from.vendor_id = to.id"], "fill": 0.594},
        "ASSET_BUILDING": {"from": "Asset", "to": "Building", "cardinality": "many_to_one",
                           "join": ["from.building_id = to.building_id"], "fill": 0.594},
    },
    "derived": {},
}


def onto():
    return oq.Ontology(json.loads(json.dumps(ONTO)))


def include(name, concept, via, match=None):
    return {"name": name, "concept": concept, "match": match, "via": via, "states": [], "filters": [],
            "sort": None, "limit": 20}


def traced_plan(constraint=True, match=None):
    """The plan shape from the trace: the building as the tail of each include's route."""
    return {
        "question_type": "list",
        "focus": {"concept": "Vendor", "match": None, "states": [], "filters": []},
        "constraints": ([{"concept": "Building", "match": "Aire Mill Court (B-304)", "via": [],
                          "states": [], "filters": []}] if constraint else []),
        "include": [include("assets_at_building", "Asset", ["ASSET_VENDOR", "ASSET_BUILDING"], match),
                    include("work_orders_at_building", "WorkOrder",
                            ["WORK_ORDER_ASSIGNED_VENDOR", "WORK_ORDER_BUILDING"], match)],
        "aggregate": None,
    }


def plan_with(raw):
    calls = []

    async def llm(messages):
        calls.append(messages)
        return json.dumps(raw)

    plan, attempts = asyncio.run(oq.plan_question(onto(), QUESTION, llm=llm))
    return plan, attempts, calls


# -- the plan from the trace is accepted first time -------------------------------------

def test_the_traced_plan_is_valid_on_the_first_attempt():
    plan, attempts, calls = plan_with(traced_plan())
    assert len(calls) == 1, "the model was asked again: %r" % [a["errors"] for a in attempts]
    assert attempts[0]["errors"] == []


def test_the_building_stays_the_one_constraint_and_is_matched_through_any_link():
    plan, _, _ = plan_with(traced_plan())
    assert [c["concept"] for c in plan["constraints"]] == ["Building"]
    assert plan["constraints"][0]["match"] == "Aire Mill Court (B-304)"
    assert plan["constraints"][0]["via"] == [], "no route given, so every link to the building counts"


def test_each_include_keeps_the_route_to_its_own_records():
    plan, _, _ = plan_with(traced_plan())
    got = {i["concept"]: i["via"] for i in plan["include"]}
    assert got == {"Asset": ["ASSET_VENDOR"], "WorkOrder": ["WORK_ORDER_ASSIGNED_VENDOR"]}
    assert oq.validate_plan(onto(), plan) == []


def test_with_no_constraint_the_name_the_include_carried_becomes_one():
    plan, attempts, calls = plan_with(traced_plan(constraint=False, match="B-304"))
    assert len(calls) == 1 and attempts[0]["errors"] == []
    assert [(c["concept"], c["match"], c["via"]) for c in plan["constraints"]] == [("Building", "B-304", [])], (
        "one constraint for the place, however many includes named it")
    assert all(i["match"] is None for i in plan["include"])


def test_without_a_name_or_a_constraint_for_the_place_it_is_refused_as_before():
    """Nothing says WHICH building: repairing it would answer for every vendor."""
    p = oq.normalise_plan(traced_plan(constraint=False))
    assert oq.includes_that_end_at_a_place(onto(), p) == []
    errors = oq.validate_plan(onto(), p)
    assert any("ends at Building, not Asset" in e for e in errors)


def test_an_include_that_ends_at_its_own_records_is_left_as_planned():
    p = oq.normalise_plan(traced_plan())
    p["include"] = [include("assets", "Asset", ["ASSET_VENDOR"])]
    before = json.dumps(p, sort_keys=True)
    assert oq.includes_that_end_at_a_place(onto(), p) == []
    assert json.dumps(p, sort_keys=True) == before


# -- and it reads what was asked --------------------------------------------------------

class Reader:
    """Answers the record engine's reads without a database and keeps the SQL."""

    def __init__(self):
        self.sql = []

    async def fetch(self, sql, p, label=""):
        self.sql.append((label, sql, dict(p.d)))
        if label == "count":
            return [{"n": 1}]
        if label.startswith("focus"):
            return [{"__key": "v-1", "id": "v-1", "vendor_name": "Pennard Fire Services",
                     "trade": "Fire", "__sort": "Pennard Fire Services"}]
        return []


def test_the_vendors_are_restricted_to_the_building_and_their_records_there():
    o = onto()
    plan, _, _ = plan_with(traced_plan())
    plan["constraints"][0]["keys"] = [BID]
    oq.swap_empty_links(o, plan)
    r = Reader()
    asyncio.run(oq.execute_plan(r, o, plan))
    focus_sql = next(s for label, s, _ in r.sql if label == "count")
    assert focus_sql.count("EXISTS (SELECT 1 FROM") == 3, "the vendor is at the building through any link"
    includes = [(label, s, d) for label, s, d in r.sql if label.startswith("include")]
    assert len(includes) == 2
    for label, s, d in includes:
        assert '"plenum_scoped"."buildings"' in s, label + " lists records anywhere, not at the building"
        assert [BID] in d.values(), label + " is not bound to the building's key"
