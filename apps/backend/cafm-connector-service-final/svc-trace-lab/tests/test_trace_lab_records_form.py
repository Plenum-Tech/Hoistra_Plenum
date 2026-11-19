"""Editing the whole query (19 Nov 2026, Hussain and Bala): what a person may pick from (Hoistra's map of the records:
the kinds of record, their columns with what kind of value each holds, their states with descriptions, what links to
what, the conditions and periods), whether a plan they wrote fits that map - with the problem said in plain words and
naming the part of the query it is in - and what they changed, in words.

A real Hoistra map (ontology_qa.Ontology) built from a small dict: no database is needed."""
from __future__ import annotations

import json

import pytest

from src.agents import ontology_qa as oq
from trace_lab import records_form as rf


def _concept(table, label, attributes, states=None, **more):
    return {"table": table, "key": "id", "label": label, "attributes": {a: a for a in attributes},
            "states": states or {}, **more}


def _onto(*, types=True):
    onto = oq.Ontology({
        "schema": "plenum_scoped",
        "concepts": {
            "Building": _concept("buildings", "name", ["id", "name", "floors", "created_at"], hub=True),
            "Floor": _concept("floors", "name", ["id", "name", "building_id", "area_m2"]),
            "ComplianceCertificate": _concept(
                "certificates", "cert_type",
                ["id", "cert_type", "building_id", "expiry_date", "next_due_date", "status", "cost"],
                {"lapsed": {"attribute": "expiry_date", "lt": "today", "description": "lapsed / expired"},
                 "expiring_soon": {"attribute": "status", "eq": "Expiring soon", "description": "status = Expiring soon"}}),
            "Vendor": _concept("vendors", "vendor_name", ["id", "vendor_name"]),
        },
        "relations": {
            "floor_in_building": {"from": "Floor", "to": "Building", "join": ["from.building_id = to.id"],
                                  "cardinality": "many_to_one"},
            "cert_in_building": {"from": "ComplianceCertificate", "to": "Building", "join": ["from.building_id = to.id"],
                                 "cardinality": "many_to_one"},
        }})
    if types:
        onto.col_types = {("buildings", "floors"): "integer", ("buildings", "name"): "character varying",
                          ("buildings", "created_at"): "timestamp with time zone",
                          ("floors", "area_m2"): "numeric",
                          ("certificates", "expiry_date"): "date", ("certificates", "next_due_date"): "timestamp without time zone",
                          ("certificates", "status"): "character varying", ("certificates", "cost"): "double precision"}
    return onto


def _node(concept, match=None, **kw):
    return dict({"concept": concept, "match": match, "states": [], "filters": [], "via": []}, **kw)


def _plan(focus, *, constraints=(), include=(), aggregate=None, question_type="list"):
    return {"question_type": question_type, "focus": focus, "constraints": list(constraints), "include": list(include),
            "aggregate": aggregate}


R62 = _plan(_node("Floor"), constraints=[_node("Building", "ABC")], question_type="count")
CERT_30 = _plan(_node("ComplianceCertificate", states=["expiring_soon"],
                      filters=[{"attribute": "expiry_date", "op": "within", "value": "next_30_days"}]),
                constraints=[_node("Building", "Bishopsgate")])


# ── the map ───────────────────────────────────────────────────────────────────────────────────────────────────────

def _concept_of(m, name):
    return next(c for c in m["concepts"] if c["name"] == name)


def test_the_map_lists_every_concept_with_its_words():
    m = rf.query_map(_onto())
    assert [c["name"] for c in m["concepts"]] == ["Building", "Floor", "ComplianceCertificate", "Vendor"]
    assert [c["words"] for c in m["concepts"]] == ["buildings", "floors", "compliance certificates", "vendors"]
    json.dumps(m)


def test_the_maps_words_are_the_ones_the_editor_reads():
    """The editor (apps/frontend/src/lab/logic/queryForm.js) reads a concept's `words` as its plural noun ("a count of
    floors") and an op's `words` inside a sentence ("the expiry date is within the next 60 days"), and `number_words`
    for a column that holds a number ("the cost is less than 5"). These are the exact words its tests feed it
    (queryForm.test.mjs SERVICE_MAP, a copy of this map): change both together."""
    m = rf.query_map(_onto())
    assert [c["words"] for c in m["concepts"]] == ["buildings", "floors", "compliance certificates", "vendors"]
    words = {o["id"]: o["words"] for o in m["ops"]}
    assert {k: words[k] for k in rf.OP_WORDS} == {
        "eq": "is", "ne": "is not", "in": "is one of", "not_in": "is not one of", "lt": "is before", "lte": "is on or before",
        "gt": "is after", "gte": "is on or after", "between": "is between", "within": "is within", "contains": "contains",
        "is_null": "is empty"}
    assert {o["id"]: o["number_words"] for o in m["ops"] if "number_words" in o} == {
        "lt": "is less than", "lte": "is at most", "gt": "is more than", "gte": "is at least"}


def test_attributes_have_the_kind_the_maps_column_types_say():
    m = rf.query_map(_onto())
    kinds = lambda name: {a["name"]: a["kind"] for a in _concept_of(m, name)["attributes"]}
    assert kinds("Building") == {"id": "text", "name": "text", "floors": "number", "created_at": "date"}
    assert kinds("ComplianceCertificate") == {"id": "text", "cert_type": "text", "building_id": "text", "expiry_date": "date",
                                              "next_due_date": "date", "status": "text", "cost": "number"}
    assert kinds("Floor")["area_m2"] == "number"


def test_without_column_types_the_name_decides_and_nothing_is_a_number():
    m = rf.query_map(_onto(types=False))
    kinds = {a["name"]: a["kind"] for a in _concept_of(m, "ComplianceCertificate")["attributes"]}
    assert kinds["expiry_date"] == "date" and kinds["next_due_date"] == "date"
    assert kinds["cost"] == "text" and kinds["status"] == "text"
    assert {a["name"]: a["kind"] for a in _concept_of(m, "Building")["attributes"]}["floors"] == "text"


def test_a_column_the_map_types_as_text_is_text_whatever_its_name():
    onto = _onto()
    onto.col_types[("certificates", "next_due_date")] = "character varying"
    kinds = {a["name"]: a["kind"] for a in _concept_of(rf.query_map(onto), "ComplianceCertificate")["attributes"]}
    assert kinds["next_due_date"] == "text"


def test_states_come_with_their_descriptions():
    m = rf.query_map(_onto())
    assert _concept_of(m, "ComplianceCertificate")["states"] == [
        {"id": "lapsed", "description": "lapsed / expired"}, {"id": "expiring_soon", "description": "status = Expiring soon"}]
    assert _concept_of(m, "Floor")["states"] == []


def test_links_are_the_kinds_of_record_a_path_reaches_and_never_itself():
    m = rf.query_map(_onto())
    assert _concept_of(m, "Floor")["links"] == ["Building", "ComplianceCertificate"], "through the building (a hub) they share"
    assert _concept_of(m, "Building")["links"] == ["Floor", "ComplianceCertificate"]
    assert _concept_of(m, "Vendor")["links"] == [], "nothing links to it"


def test_the_conditions_and_periods_are_hoistras_own():
    m = rf.query_map(_onto())
    assert {o["id"] for o in m["ops"]} == set(oq.OPS)
    assert all(o["words"] for o in m["ops"]) and next(o for o in m["ops"] if o["id"] == "is_null")["words"] == "is empty"
    assert next(o for o in m["ops"] if o["id"] == "within")["words"] == "is within"
    assert m["periods"] == list(oq.PERIODS)


async def test_the_map_is_built_once_for_one_map_of_the_records(monkeypatch):
    onto = _onto()
    built = []
    real = rf.query_map
    monkeypatch.setattr(rf, "query_map", lambda o: built.append(o) or real(o))
    first = await rf.map_for(onto)
    assert await rf.map_for(onto) is first and built == [onto]
    other = _onto()
    assert await rf.map_for(other) is not first and len(built) == 2


# ── checking a plan ───────────────────────────────────────────────────────────────────────────────────────────────

def test_a_plan_that_fits_the_map_has_no_problems():
    assert rf.check_plan(_onto(), R62) == [] and rf.check_plan(_onto(), CERT_30) == []


def test_the_plan_is_checked_as_a_copy_and_left_as_it_was():
    plan = json.loads(json.dumps(R62))
    plan["focus"].pop("via")                       # a hand-written plan need not carry every key
    before = json.dumps(plan, sort_keys=True)
    assert rf.check_plan(_onto(), plan) == [] and json.dumps(plan, sort_keys=True) == before


@pytest.mark.parametrize("plan,part,words", [
    (_plan(_node("Flor")), "What", "'Flor' is not a kind of record"),
    (_plan(_node("Floor"), constraints=[_node("Bulding", "ABC")]), "Limited to", "'Bulding' is not a kind of record"),
    (_plan(_node("Floor", states=["open"])), "What", "'open' is not a state of floor records"),
    (_plan(_node("ComplianceCertificate", states=["made_up"])), "What", "'made_up' is not a state of compliance certificate records"),
    (_plan(_node("Floor"), constraints=[_node("Building", states=["open"])]), "Limited to", "'open' is not a state of building records"),
    (_plan(_node("Floor", filters=[{"attribute": "colour", "op": "eq", "value": "red"}])), "What", "floor records have no column 'colour'"),
    (_plan(_node("Floor", filters=[{"attribute": "name", "op": "like", "value": "x"}])), "What", "'like' is not a condition"),
    (_plan(_node("Floor"), include=[_node("Building", name="its_building", filters=[{"attribute": "colour", "op": "eq", "value": 1}])]),
     "Also show", "building records have no column 'colour'"),
    (_plan(_node("Floor"), include=[_node("Building", name="its_building", sort={"attribute": "colour", "dir": "asc"})]),
     "Also show", "building records have no column 'colour'"),
    (_plan(_node("Floor"), constraints=[_node("Vendor")]), "Limited to", "floor records are not linked to vendor records"),
    (_plan(_node("Floor"), aggregate={"count_by": [{"concept": "Vendor", "attribute": "vendor_name"}]}), "Grouped by",
     "vendor records"),
    (_plan(_node("Floor"), constraints=[_node("Building")], aggregate={"count_by": [{"concept": "Building", "attribute": "colour"}]}),
     "Grouped by", "building records have no column 'colour'"),
])
def test_a_part_that_does_not_fit_is_named_in_words(plan, part, words):
    problems = rf.check_plan(_onto(), plan)
    assert problems and any(part in p and words in p for p in problems), problems
    assert not any("Valid:" in p or "[" in p or "{" in p for p in problems), "no engine text: " + str(problems)


def test_a_state_that_is_wrong_lists_the_ones_that_exist():
    (problem,) = rf.check_plan(_onto(), _plan(_node("ComplianceCertificate", states=["made_up"])))
    assert "lapsed" in problem and "expiring_soon" in problem
    (none,) = rf.check_plan(_onto(), _plan(_node("Floor", states=["open"])))
    assert "no states" in none


@pytest.mark.parametrize("plan", [None, [], "list", {}, {"focus": None}, {"focus": "Floor"}, {"focus": {}}])
def test_a_plan_with_no_kind_of_record_asks_for_one(plan):
    problems = rf.check_plan(_onto(), plan)
    assert problems and "What" in problems[0]


@pytest.mark.parametrize("plan", [
    {"focus": _node("Floor"), "constraints": "Building"},
    {"focus": _node("Floor"), "constraints": [None]},
    {"focus": _node("Floor"), "include": [3]},
    {"focus": _node("Floor"), "aggregate": [1]},
    {"focus": _node("Floor"), "aggregate": {"count_by": [7]}},
    {"focus": _node("Floor", filters=[7])},
    {"focus": _node("Floor", filters=[{"attribute": "name", "op": "eq", "value": {"a": 1}}], states="open")},
    {"focus": _node("Floor"), "include": [_node("Building", sort="name")]},
])
def test_a_plan_that_is_not_written_in_the_plan_shape_is_refused_not_crashed(plan):
    problems = rf.check_plan(_onto(), plan)
    assert problems and all(isinstance(p, str) and p for p in problems)


def test_a_period_condition_must_name_a_period():
    bad = _plan(_node("ComplianceCertificate", filters=[{"attribute": "expiry_date", "op": "within", "value": "soonish"}]))
    (problem,) = rf.check_plan(_onto(), bad)
    assert "What" in problem and "'soonish' is not a period" in problem and "next_60_days" in problem
    for ok in ("last_month", "next_60_days", "this_quarter", "Last month"):
        assert rf.check_plan(_onto(), _plan(_node("ComplianceCertificate", filters=[{"attribute": "expiry_date", "op": "within", "value": ok}]))) == []


def test_a_between_condition_needs_two_values():
    for value in ("2026-01-01", ["2026-01-01"], ["a", "b", "c"], None):
        problems = rf.check_plan(_onto(), _plan(_node("ComplianceCertificate", filters=[{"attribute": "expiry_date", "op": "between", "value": value}])))
        assert problems and "two" in problems[0], value
    ok = _plan(_node("ComplianceCertificate", filters=[{"attribute": "expiry_date", "op": "between", "value": ["2026-01-01", "2026-03-01"]}]))
    assert rf.check_plan(_onto(), ok) == []


def test_every_problem_is_told_once():
    plan = _plan(_node("Floor", states=["open", "shut"]), constraints=[_node("Bulding", "ABC")])
    problems = rf.check_plan(_onto(), plan)
    assert len(problems) == len(set(problems)) == 3, problems


def test_a_plan_of_unreasonable_size_is_refused_before_it_is_run():
    plan = _plan(_node("Floor"), constraints=[_node("Building") for _ in range(rf.MAX_PARTS + 1)])
    assert any("Limited to" in p and str(rf.MAX_PARTS) in p for p in rf.check_plan(_onto(), plan))
    many = _plan(_node("Floor", filters=[{"attribute": "name", "op": "eq", "value": str(i)} for i in range(rf.MAX_PARTS + 1)]))
    assert any("conditions" in p and str(rf.MAX_PARTS) in p for p in rf.check_plan(_onto(), many))


def test_a_plan_that_weighs_too_much_is_refused_before_it_is_run():
    huge = _plan(_node("Floor", filters=[{"attribute": "name", "op": "in", "value": ["x" * 1000] * 80}]))
    assert rf.check_plan(_onto(), huge) == ["The query is too large to run."]


# ── what changed ──────────────────────────────────────────────────────────────────────────────────────────────────

def _changed(old, new):
    return rf.plan_changes(_onto(), old, new)


def test_nothing_changed_says_nothing():
    assert _changed(R62, json.loads(json.dumps(R62))) == []


def test_a_different_kind_of_record_is_said_in_words():
    new = json.loads(json.dumps(R62))
    new["focus"]["concept"] = "Building"
    assert _changed(R62, new) == ["What: Floor records → Building records"]


def test_a_condition_added_is_said_with_its_column_condition_and_value():
    new = json.loads(json.dumps(CERT_30))
    new["focus"]["filters"].append({"attribute": "expiry_date", "op": "within", "value": "next_60_days"})
    assert _changed(CERT_30, new) == ["Condition added: expiry_date within next_60_days"]


def test_a_condition_removed_and_a_condition_changed():
    new = json.loads(json.dumps(CERT_30))
    new["focus"]["filters"] = []
    assert _changed(CERT_30, new) == ["Condition removed: expiry_date within next_30_days"]
    new["focus"]["filters"] = [{"attribute": "expiry_date", "op": "within", "value": "next_60_days"}]
    assert _changed(CERT_30, new) == ["Condition changed: expiry_date within next_30_days → within next_60_days"]


def test_conditions_are_read_in_words_by_the_kind_of_column():
    new = _plan(_node("ComplianceCertificate", filters=[
        {"attribute": "expiry_date", "op": "lt", "value": "2026-12-01"}, {"attribute": "cost", "op": "gt", "value": 500},
        {"attribute": "status", "op": "in", "value": ["a", "b"]}, {"attribute": "expiry_date", "op": "between", "value": ["2026-01-01", "2026-03-01"]},
        {"attribute": "cert_type", "op": "is_null", "value": True}, {"attribute": "status", "op": "ne", "value": "Valid"}]))
    old = _plan(_node("ComplianceCertificate"))
    assert _changed(old, new) == [
        "Condition added: expiry_date is before 2026-12-01", "Condition added: cost is more than 500",
        "Condition added: status is one of a, b", "Condition added: expiry_date is between 2026-01-01 and 2026-03-01",
        "Condition added: cert_type is empty", "Condition added: status is not Valid"]


def test_a_limit_removed_is_said_with_the_record_it_named():
    new = json.loads(json.dumps(R62))
    new["constraints"] = []
    assert _changed(R62, new) == ["Limited to: Building ABC removed"]


def test_a_limit_added_or_changed():
    base = _plan(_node("Floor"), question_type="count")
    assert _changed(base, R62) == ["Limited to: Building ABC added"]
    new = json.loads(json.dumps(R62))
    new["constraints"][0]["match"] = "XYZ"
    assert _changed(R62, new) == ["Limited to Building: Name or code: ABC → XYZ"]
    new["constraints"][0]["states"] = ["open"]
    assert "Limited to Building: State added: open" in _changed(R62, new)


def test_grouping_is_said_by_the_record_and_column():
    new = json.loads(json.dumps(R62))
    new["aggregate"] = {"count_by": [{"concept": "Building", "attribute": "name"}]}
    assert _changed(R62, new) == ["Grouped by: Building name"]
    assert _changed(new, R62) == ["Grouped by: Building name removed"]
    other = json.loads(json.dumps(new))
    other["aggregate"]["count_by"] = [{"concept": "Building", "attribute": "floors"}]
    assert _changed(new, other) == ["Grouped by: Building name → Building floors"]


def test_states_the_kind_of_answer_and_the_name_are_said():
    new = json.loads(json.dumps(CERT_30))
    new["focus"]["states"] = ["lapsed"]
    new["focus"]["match"] = "LOLER-1"
    new["question_type"] = "count"
    assert _changed(CERT_30, new) == ["Kind of answer: list → count", "Name or code: any → LOLER-1",
                                      "State added: lapsed", "State removed: expiring_soon"]


def test_linked_records_shown_with_each_result_are_said():
    base = _plan(_node("Floor"))
    new = _plan(_node("Floor"), include=[_node("Building", name="its_building", limit=10)])
    assert _changed(base, new) == ["Also show: its_building added"]
    assert _changed(new, base) == ["Also show: its_building removed"]
    more = _plan(_node("Floor"), include=[_node("Building", name="its_building", limit=25, sort={"attribute": "name", "dir": "desc"})])
    assert _changed(new, more) == ["Also show its_building: Limit: 10 → 25", "Also show its_building: Sorted by: none → name descending"]


def test_a_plan_the_words_cannot_read_gives_no_lines_not_a_crash():
    assert rf.plan_changes(_onto(), R62, "nonsense") == [] and rf.plan_changes(_onto(), None, R62) == []
