"""A vendor is at a building through ANY of its links, not the one link the search met first.

Found 24 Nov 2026: "Aire Mill Court (B-304) vendors for this building" answered 11 vendors, all
Clear. The building has 15, and one of the missing four is Blocked. A constraint with no explicit
route took auto_path's one shortest path - the fullest-filled link, which on hoistra_test is the
planned visits (ppm_visits) - while eleven two-hop routes tie: work orders, assets, certificates,
contracts, maintenance plans, planned visits. A vendor with work orders and certificates at the
building but no planned visit there was not "at" it.

So when several routes of the same length (two hops or more) reach the constraint, a record
matches through any of them. A one-hop tie (two columns joining the same two tables) keeps
taking the fullest link, as before; an empty link is used only where it is the only one; a route
given explicitly is followed as written; and a breakdown by the constrained concept keeps its join.
"""
from __future__ import annotations

import json

from src.agents import ontology_qa as oq

BID = "c68acb8c-828b-473d-8abe-bdb2ba7fe5b6"

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
                      "attributes": {"id": "id", "title": "title", "status": "status"},
                      "states": {"open": {"attribute": "status", "not_in": ["Completed", "Closed"]}}},
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
    o = oq.Ontology(json.loads(json.dumps(ONTO)))
    o.col_types = {("work_orders", "status"): "character varying"}
    return o


def plan(focus="Vendor", constraint="Building", via=None, **over):
    raw = {"question_type": "list",
           "focus": {"concept": focus, "match": None, "states": [], "filters": []},
           "constraints": [{"concept": constraint, "match": "Aire Mill Court", **({"via": via} if via else {})}],
           "include": []}
    raw.update(over)
    out = oq.normalise_plan(raw)
    out["constraints"][0]["keys"] = [BID]
    return out


def names(paths):
    return [[r.name for r, _ in hops] for hops, _ in paths]


def test_every_equally_short_route_is_found_fullest_first_and_an_empty_one_left_out():
    o = onto()
    got = names(o.auto_paths("Vendor", "Building"))
    assert got == [["PPM_VISIT_VENDOR", "PPM_VISIT_BUILDING"],
                   ["WORK_ORDER_VENDOR", "WORK_ORDER_BUILDING"],
                   ["ASSET_VENDOR", "ASSET_BUILDING"]]
    # the first is the one auto_path has always taken
    assert got[0] == [r.name for r, _ in o.auto_path("Vendor", "Building")[0]]


def test_a_vendor_constrained_to_a_building_matches_through_any_route():
    o, p = onto(), oq.Params()
    qb, where, aliases, paths = oq.build_main(o, plan(), p)
    sql = qb.from_clause() + oq.where_sql(where)
    assert sql.count("EXISTS (SELECT 1 FROM") == 3 and " OR " in sql
    for table in ("ppm_visits", "work_orders", "assets"):
        assert f'"plenum_scoped"."{table}"' in sql
    assert "assigned_vendor" not in sql                       # the empty link stays out
    assert BID not in sql and [BID] in p.d.values()           # the key is bound, never written
    assert "Building" not in aliases                          # matched, not joined: no duplicate rows
    assert len(paths) == 1 and "any of" in paths[0] and "WORK_ORDER_VENDOR > WORK_ORDER_BUILDING" in paths[0]


def test_a_one_hop_tie_keeps_the_fullest_link_and_its_join():
    o, p = onto(), oq.Params()
    qb, where, _, paths = oq.build_main(o, plan(focus="WorkOrder", constraint="Vendor"), p)
    sql = qb.from_clause() + oq.where_sql(where)
    assert '"plenum_scoped"."vendors"' in sql and "EXISTS" not in sql
    assert paths == ["WorkOrder -> Vendor via WORK_ORDER_VENDOR"]


def test_a_route_given_explicitly_is_followed_as_written():
    o, p = onto(), oq.Params()
    qb, where, _, paths = oq.build_main(
        o, plan(via=["WORK_ORDER_VENDOR", "WORK_ORDER_BUILDING"]), p)
    assert paths == ["Vendor -> Building via WORK_ORDER_VENDOR > WORK_ORDER_BUILDING"]
    assert "EXISTS" not in qb.from_clause() + oq.where_sql(where)


def test_a_breakdown_by_the_constrained_concept_keeps_its_join():
    o, p = onto(), oq.Params()
    pl = plan(aggregate={"count_by": [{"concept": "Building", "attribute": "name"}]})
    qb, where, aliases, _ = oq.build_main(o, pl, p)
    assert "Building" in aliases


def test_the_constraint_state_and_filters_apply_on_every_route():
    o, p = onto(), oq.Params()
    pl = plan(focus="Vendor", constraint="Building")
    pl["constraints"][0]["filters"] = [{"attribute": "name", "op": "eq", "value": "Aire Mill Court"}]
    qb, where, _, _ = oq.build_main(o, pl, p)
    sql = oq.where_sql(where)
    assert sql.count('."name"') == 3
