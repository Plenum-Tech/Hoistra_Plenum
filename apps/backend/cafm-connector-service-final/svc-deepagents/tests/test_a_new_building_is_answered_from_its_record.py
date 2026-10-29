"""A building added a moment ago is answered from its own record (29 Oct 2026).

JASMEET (B-090765) was added on the Buildings page with 89 floors. Asked about it:

* "HOW MANY FLOORS IN JASMEET" was planned as a count of Floor records at that building. A
  building added by hand has no Floor records — its count is the building's own `floors`, the
  figure the Buildings page shows as "surveyed" — so the answer was "Jasmeet has no floor
  records". The count stays; the figure the building record states is read and handed over
  beside it.
* "Show all recorded details for Jasmeet" was planned about a Technician called Jasmeet. The
  sweep across the other concepts found the Building — and then kept the Technician focus with
  the building as a constraint ("technicians at Jasmeet": none). When the question never names
  the planned kind of record, the name WAS the subject: the focus moves to what it matched.
  "Work orders at Bishopsgate", which names its records, keeps them.

Both plans reproduced against the live planner on the service's own cached ontology.
"""
from __future__ import annotations

import asyncio

from src.agents import ontology_qa as oq


class _C:
    def __init__(self, name, table, key, label, identifiers=(), attributes=None, synonyms=()):
        self.name, self.table, self.key, self.label = name, table, key, label
        self.identifiers, self.search = list(identifiers), [label, *identifiers]
        self.attributes = attributes or {}
        self.states, self.synonyms = {}, list(synonyms)

    def col(self, attr):
        if attr in self.attributes:
            return self.attributes[attr]
        raise oq.OntologyError(self.name + " has no attribute " + attr)


class _Onto:
    schema = "plenum_scoped"

    def __init__(self):
        self.concepts = {
            "Technician": _C("Technician", "technicians", "id", "full_name", synonyms=["engineer"]),
            "WorkOrder": _C("WorkOrder", "work_orders", "id", "title", ["wo_code"]),
            "Floor": _C("Floor", "floors", "floor_id", "name"),
            "Building": _C("Building", "buildings", "building_id", "name", ["building_code"],
                           attributes={"floors": "floors", "name": "name"}),
        }

    def resolve_path(self, a, b, via):
        return []


class _Reader:
    def __init__(self, answer):
        self.calls, self.answer = [], answer

    async def try_fetch(self, sql, p, label=""):
        self.calls.append((label, sql, dict(p.d)))
        return self.answer(sql, p.d)


def _jasmeet_is_a_building(sql, d):
    if "UNION ALL" in sql or ('"buildings"' in sql and "~*" in sql):
        return [{"c": "Building", "o": 3, "v": "Jasmeet", "k": "b-090765", "l": "JASMEET"}]
    return []


def _node(concept, match=None, **kw):
    return dict({"concept": concept, "match": match, "states": [], "filters": [], "via": []}, **kw)


def test_details_for_a_name_are_about_what_the_name_is():
    plan = {"question_type": "list", "focus": _node("Technician", "Jasmeet"), "constraints": [], "include": []}
    plan, notes, unresolved = asyncio.run(oq.resolve_plan(_Reader(_jasmeet_is_a_building), _Onto(), plan,
                                                          question="Show all recorded details for Jasmeet"))
    assert unresolved == []
    assert plan["focus"]["concept"] == "Building" and plan["focus"]["keys"] == ["b-090765"]
    assert plan["constraints"] == []
    assert any("is a Building" in n for n in notes)


def test_a_question_that_names_its_records_keeps_them():
    plan = {"question_type": "list", "focus": _node("WorkOrder", "Jasmeet"), "constraints": [], "include": []}
    plan, notes, _ = asyncio.run(oq.resolve_plan(_Reader(_jasmeet_is_a_building), _Onto(), plan,
                                                 question="Which work orders are open at Jasmeet?"))
    assert plan["focus"]["concept"] == "WorkOrder" and plan["focus"]["match"] is None
    assert [(c["concept"], c["keys"]) for c in plan["constraints"]] == [("Building", ["b-090765"])]


def test_a_count_of_floors_also_reads_the_floors_the_building_states():
    def answer(sql, d):
        return [{"l": "JASMEET", "v": "89"}] if '"floors"' in sql and '"buildings"' in sql else []

    r = _Reader(answer)
    plan = {"question_type": "count", "focus": _node("Floor"), "include": [], "aggregate": {"count_by": []},
            "constraints": [_node("Building", "JASMEET", via=["FLOOR_BUILDING"], keys=["b-090765"])]}
    notes = asyncio.run(oq.stated_count_notes(r, _Onto(), plan))
    assert len(notes) == 1 and "JASMEET" in notes[0] and "89" in notes[0]
    assert r.calls[0][2]["p0"] == ["b-090765"]


def test_no_stated_figure_no_note():
    plan = {"question_type": "count", "focus": _node("WorkOrder"), "include": [], "aggregate": None,
            "constraints": [_node("Building", "JASMEET", keys=["b-090765"])]}
    r = _Reader(lambda sql, d: [])
    assert asyncio.run(oq.stated_count_notes(r, _Onto(), plan)) == []
    assert r.calls == []          # Building has no "work_orders" figure: nothing is read
