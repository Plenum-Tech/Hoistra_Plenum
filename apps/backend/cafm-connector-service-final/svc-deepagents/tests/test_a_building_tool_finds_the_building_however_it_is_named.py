"""The building tools find a building however its name is typed, and never answer for another (29 Oct 2026).

"HOW MANY FLOORS IN JASMEET BUILDING" found nothing because every place that compares a typed name
with a stored one did it its own way. energy_intelligence_agent._resolve_building — the door every
building tool goes through (list_building_meter_readings, list_building_documents, get_cost_savings,
search_documents, replacement_candidates, work_order_blockers) — sent the name to the building
resolver once, as typed, and took whatever came back:

* "Kew building" missed "Kew" (too short for the resolver's containment rule), "B090765" missed
  B-090765 (sent as a name, never as a code), "tower hussain" missed "Hussain Tower", and "Jasmet"
  missed JASMEET;
* "Building 7" came back as "Building 70" (containment) and was answered as if it were Building 7;
* "the building" came back as "Building 70" too — a phrase of kind words named a building.

The client now sends the name's core beside the text as typed, a code as a code, and ranks what the
resolver offers by the shared ladder (agents/name_match.py, the one ontology_qa runs in SQL). A name
shared by several buildings returns all of them; a building whose name is not the one asked about is
never used. The fake below answers as svc-operations-intelligence's building_resolver.choose_building
does today: an exact code, an exact normalised name, then containment for names of five letters or more.
"""
from __future__ import annotations

import re

import pytest

from src.agents import energy_intelligence_agent as energy

JASMEET = {"building_id": "b-jas", "name": "JASMEET", "code": "B-090765"}
KEW = {"building_id": "b-kew", "name": "Kew", "code": "B-000012"}
HUSSAIN = {"building_id": "b-hus", "name": "Hussain Tower", "code": "B-000044"}
B70 = {"building_id": "b-70", "name": "Building 70", "code": "B-000070"}
B7E = {"building_id": "b-7e", "name": "Building 7 East", "code": "B-000071"}
RT5 = {"building_id": "b-rt5", "name": "RT5, Crossrail", "code": "B-000555"}
HTN = {"building_id": "b-htn", "name": "Hussain Tower North", "code": "B-000045"}
HTS = {"building_id": "b-hts", "name": "Hussain Tower South", "code": "B-000046"}
JASMEET_TWIN = {"building_id": "b-jas-2", "name": "Jasmeet", "code": "B-090766"}
ANNEX = {"building_id": "b-annex", "name": "Jasmeet Building Annex", "code": "B-090767"}

REGISTER = [JASMEET, KEW, HUSSAIN, B70, RT5]


def _norm(v):
    """compliance.site_links.normalize_name: case, punctuation and spacing; a leading "the"."""
    s = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", str(v or "").lower()).strip())
    return s[4:] if s.startswith("the ") else s


def _ref(v):
    return re.sub(r"[^A-Z0-9]+", "", str(v or "").upper())


def _choose(register, params):
    def cand(b):
        return {"building_id": b["building_id"], "name": b["name"], "site_id": None}

    ref = _ref(params.get("code"))
    if len(ref) >= 2:
        hits = [b for b in register if _ref(b["code"]) == ref]
        if len(hits) == 1:
            return {"ok": True, "outcome": "resolved", "reason": "building_code",
                    "building_id": hits[0]["building_id"], "building": hits[0]["name"]}
        if len(hits) > 1:
            return {"ok": True, "outcome": "review", "reason": "ambiguous_building_code",
                    "building_id": None, "candidates": []}
    n = _norm(params.get("name"))
    if n:
        hits = [b for b in register if _norm(b["name"]) == n]
        if len(hits) == 1:
            return {"ok": True, "outcome": "resolved", "reason": "name",
                    "building_id": hits[0]["building_id"], "building": hits[0]["name"]}
        if len(hits) > 1:
            return {"ok": True, "outcome": "review", "reason": "ambiguous_name",
                    "building_id": None, "candidates": [cand(b) for b in hits]}
        if len(n) >= 5:
            hits = [b for b in register if len(_norm(b["name"])) >= 5
                    and abs(len(_norm(b["name"])) - len(n)) <= 25
                    and (n in _norm(b["name"]) or _norm(b["name"]) in n)]
            if len(hits) == 1:
                return {"ok": True, "outcome": "resolved", "reason": "name_contains",
                        "building_id": hits[0]["building_id"], "building": hits[0]["name"]}
            if len(hits) > 1:
                return {"ok": True, "outcome": "review", "reason": "ambiguous_name_contains",
                        "building_id": None, "candidates": [cand(b) for b in hits]}
    return {"ok": True, "outcome": "unmatched", "reason": "no_match", "building_id": None,
            "candidates": []}


class _Resp:
    def __init__(self, body):
        self._body = body

    def json(self):
        return self._body


class _Service:
    """The resolver over ``register``; a building's meter summary by its id."""

    def __init__(self, register):
        self.register = register
        self.calls: list[tuple[str, dict]] = []

    async def __call__(self, method, base, path, **kwargs):
        params = dict(kwargs.get("params") or {})
        self.calls.append((path, params))
        if path.endswith("/buildings/resolve"):
            return _Resp(_choose(self.register, params))
        if path.endswith("/meter-summary"):
            bid = path.split("/buildings/")[1].split("/")[0]
            return _Resp({"ok": True, "building_id": bid, "meters": [], "readings_total": 0})
        raise AssertionError(f"unexpected path {path}")

    def resolver_calls(self):
        return [p for path, p in self.calls if path.endswith("/buildings/resolve")]

    def summaries(self):
        return [path for path, _ in self.calls if path.endswith("/meter-summary")]


@pytest.fixture
def service(monkeypatch):
    def make(register=REGISTER):
        svc = _Service(register)
        monkeypatch.setattr(energy, "_request", svc)
        return svc
    return make


@pytest.mark.parametrize("typed,bid", [
    ("JASMEET BUILDING", "b-jas"),
    ("the JASMEET building", "b-jas"),
    ("Jasmeet's", "b-jas"),
    ("Kew building", "b-kew"),
    ("B090765", "b-jas"),
    ("building B-090765", "b-jas"),
    ("tower hussain", "b-hus"),
    ("Jasmet", "b-jas"),
    ("RT5 Crossrail", "b-rt5"),
], ids=["kind word", "kind word and article", "possessive", "short name with kind word",
        "code without hyphen", "code with kind word", "word order", "typo", "stray comma"])
async def test_a_building_is_found_however_it_is_typed(service, typed, bid):
    service()
    assert await energy._resolve_building(typed, None) == (bid, None)


async def test_building_7_is_not_answered_as_building_70(service):
    svc = service()
    bid, problem = await energy._resolve_building("Building 7", None)
    assert bid is None
    assert problem["error"] == "building_not_resolved"
    assert "Do NOT substitute another building" in problem["guidance"]
    out = await energy.list_building_meter_readings.ainvoke({"building_name": "Building 7"})
    assert out["error"] == "building_not_resolved"
    assert svc.summaries() == [], "Building 70's meters were read for Building 7"


async def test_a_false_containment_hit_does_not_make_a_real_match_ambiguous(service):
    # The resolver sees "Building 7" inside both "Building 70" and "Building 7 East"; only the
    # second is named "Building 7 ...".
    service([B70, B7E])
    assert await energy._resolve_building("Building 7", None) == ("b-7e", None)


async def test_the_building_names_no_building(service):
    svc = service()
    bid, problem = await energy._resolve_building("the building", None)
    assert bid is None and problem["error"] == "building_not_resolved"
    assert svc.resolver_calls() == [], "a phrase of only kind words must not be looked up"


async def test_two_buildings_matching_the_name_both_come_back_and_neither_is_picked(service):
    service([HTN, HTS, KEW])
    bid, problem = await energy._resolve_building("tower hussain", None)
    assert bid is None and problem["error"] == "building_not_resolved"
    assert {c["building_id"] for c in problem["candidates"]} == {"b-htn", "b-hts"}
    assert "Hussain Tower North" in problem["guidance"] and "Hussain Tower South" in problem["guidance"]


async def test_two_buildings_with_the_same_name_are_both_named(service):
    service([JASMEET, JASMEET_TWIN, KEW])
    bid, problem = await energy._resolve_building("JASMEET BUILDING", None)
    assert bid is None
    assert {c["building_id"] for c in problem["candidates"]} == {"b-jas", "b-jas-2"}


async def test_the_exact_name_beats_a_longer_one_that_contains_the_phrase(service):
    # "JASMEET BUILDING" is inside "Jasmeet Building Annex" and contains "JASMEET": the resolver calls
    # the phrase ambiguous; the building named exactly JASMEET is the one asked about.
    service([JASMEET, ANNEX])
    assert await energy._resolve_building("JASMEET BUILDING", None) == ("b-jas", None)


async def test_a_name_on_no_building_is_not_answered_with_another(service):
    svc = service()
    bid, problem = await energy._resolve_building("Moreland House", None)
    assert bid is None and problem["error"] == "building_not_resolved"
    assert svc.summaries() == []


async def test_the_meter_tool_reads_the_building_the_name_means(service):
    svc = service()
    out = await energy.list_building_meter_readings.ainvoke({"building_name": "JASMEET BUILDING"})
    assert out["building_id"] == "b-jas"
    assert svc.summaries() == ["/api/energy/buildings/b-jas/meter-summary"]
