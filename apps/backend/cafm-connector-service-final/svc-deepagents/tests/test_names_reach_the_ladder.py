"""Every name in a question reaches the resolution ladder, and only the question's own names do (29 Oct 2026).

* A name the planner put in a filter (`name eq "JASMEET BUILDING"`) skipped the ladder: it was compared
  with = and found nothing. It is moved into the node's match.
* A code column whose values carry no hyphen ("ACME01", "BLD001") was never treated as a code, so
  "vendor ACME01" could not resolve. Code columns are recognised by name too, when the ontology loads.
* More than 50 matches were cut to an arbitrary 50 without a word. The answer is now told.
* On a follow-up, the thread's last building was appended to the question ("(at Bishopsgate Tower)")
  unless it held a B-123 code or "at <Capital>" — so "how many floors in JASMEET", asked after a
  Bishopsgate question, became JASMEET and Bishopsgate, and found nothing.
"""
import asyncio

import pytest

from src.agents import ontology_qa as oq


class _C:
    def __init__(self, name, table, key, label, identifiers=(), search=(), attributes=None):
        self.name, self.table, self.key, self.label = name, table, key, label
        self.identifiers, self.search = list(identifiers), list(search or [label])
        self.attributes, self.synonyms, self.states = dict(attributes or {}), [], {}

    def col(self, attr):
        if attr in self.attributes:
            return self.attributes[attr]
        if attr in (self.key, self.label):
            return attr
        raise oq.OntologyError(attr)


class _Onto:
    schema = "plenum_scoped"

    def __init__(self):
        self.concepts = {
            "Building": _C("Building", "buildings", "building_id", "name", ["building_code"],
                           ["name", "building_code"], {"name": "name", "floors": "floors"}),
            "Vendor": _C("Vendor", "vendors", "id", "vendor_name", [], ["vendor_name"],
                         {"vendor_name": "vendor_name", "vendor_code": "vendor_code", "city": "city"}),
            "Asset": _C("Asset", "assets", "id", "asset_name", ["asset_code"], ["asset_name", "asset_code"],
                        {"vendor_code": "vendor_code", "serial_number": "serial_number", "asset_id": "asset_id"}),
        }
        self.col_types = {("vendors", "vendor_code"): "character varying", ("vendors", "city"): "text",
                          ("assets", "vendor_code"): "text", ("assets", "serial_number"): "text",
                          ("assets", "asset_id"): "uuid", ("buildings", "building_code"): "text"}


def _node(concept, match=None, filters=()):
    return {"concept": concept, "match": match, "states": [], "filters": list(filters), "via": []}


def test_a_name_in_a_filter_becomes_the_nodes_match():
    plan = {"focus": _node("Building", filters=[{"attribute": "name", "op": "eq", "value": "JASMEET BUILDING"},
                                                 {"attribute": "floors", "op": "gt", "value": 3}]),
            "constraints": [], "include": []}
    notes = oq.names_from_filters(_Onto(), plan)
    assert plan["focus"]["match"] == "JASMEET BUILDING"
    assert plan["focus"]["filters"] == [{"attribute": "floors", "op": "gt", "value": 3}]
    assert notes


def test_a_node_that_already_has_a_match_keeps_its_filters():
    plan = {"focus": _node("Building", "JASMEET", [{"attribute": "name", "op": "eq", "value": "X"}]),
            "constraints": [], "include": []}
    assert oq.names_from_filters(_Onto(), plan) == [] and plan["focus"]["filters"]


def test_code_columns_are_codes_by_their_name():
    onto = _Onto()
    oq.add_named_code_columns(onto)
    assert "vendor_code" in onto.concepts["Vendor"].identifiers         # its own code
    assert "vendor_code" in onto.concepts["Vendor"].search
    assert "serial_number" in onto.concepts["Asset"].identifiers        # a code of no other record
    assert "vendor_code" not in onto.concepts["Asset"].identifiers      # another record's code: no
    assert "city" not in onto.concepts["Vendor"].identifiers
    assert "asset_id" not in onto.concepts["Asset"].identifiers         # not text


def test_a_code_named_for_another_record_is_not_this_rows_code():
    onto = _Onto()
    onto.concepts["AssetCriticality"] = _C("AssetCriticality", "asset_criticality", "id", "level",
                                           attributes={"asset_code": "asset_code"})
    onto.concepts["Meter"] = _C("Meter", "energy_meters", "id", "meter_ref", attributes={"meter_ref": "meter_ref"})
    onto.concepts["WorkOrder"] = _C("WorkOrder", "work_orders", "id", "title", attributes={"wo_code": "wo_code"})
    onto.col_types.update({("asset_criticality", "asset_code"): "text", ("energy_meters", "meter_ref"): "text",
                           ("work_orders", "wo_code"): "text"})
    oq.add_named_code_columns(onto)
    assert "asset_code" not in onto.concepts["AssetCriticality"].identifiers   # the asset's code
    assert "meter_ref" in onto.concepts["Meter"].identifiers                    # the meter's own
    assert "wo_code" in onto.concepts["WorkOrder"].identifiers                  # initials of work_orders


def test_a_capped_match_says_so():
    class R:
        async def try_fetch(self, sql, p, label=""):
            return [{"k": str(i), "l": "Tower " + str(i)} for i in range(oq.MAX_MATCH_KEYS)]

    name, keys, labels, note = asyncio.run(oq.resolve_match(R(), _Onto(), "Building", "tower"))
    assert len(keys) == oq.MAX_MATCH_KEYS and "first " + str(oq.MAX_MATCH_KEYS) in (note or "")


@pytest.mark.parametrize("q,needs", [
    ("how many floors in JASMEET", False),
    ("how many floors in jasmeet", False),
    ("open work orders at hussain tower", False),
    ("what about B-090765", False),
    ("what about B090765?", False),
    ("and the floors?", True),
    ("how many floors does it have", True),
    ("which of them are overdue", True),
    ("how many work orders in September", True),
    ("what is open in the building", True),
])
def test_the_threads_building_is_added_only_to_a_question_that_names_no_place(q, needs):
    assert oq.question_needs_scope_hint(q) is needs
