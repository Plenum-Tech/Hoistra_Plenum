"""Resolving codes to keys: one union query for a question's codes, a UUID matched on the key,
and nothing looked up twice in one read. Measured 5 Oct 2026: 116 resolve queries for one answer."""
from __future__ import annotations

import asyncio

from src.agents import ontology_qa as oq


class _C:
    def __init__(self, name, table, key, identifiers, search=None, label=None):
        self.name, self.table, self.key, self.identifiers = name, table, key, identifiers
        self.search, self.label = search or [label or key], label or key


class _Onto:
    schema = "plenum_scoped"

    def __init__(self):
        self.concepts = {"WorkOrder": _C("WorkOrder", "work_orders", "id", ["wo_code"], label="title"),
                         "Asset": _C("Asset", "assets", "id", ["asset_code"], label="asset_name"),
                         "Building": _C("Building", "buildings", "building_id", ["building_code"], label="name")}

    def resolve_path(self, a, b, via):
        return []


class _Reader:
    def __init__(self, answer):
        self.calls, self.answer = [], answer

    async def try_fetch(self, sql, p, label=""):
        self.calls.append((label, sql, dict(p.d)))
        return self.answer(sql, p.d)


def test_a_questions_codes_are_resolved_in_one_query_and_a_uuid_by_its_key():
    bid = "93b8800f-754c-5805-9bd6-f65c8d263fe8"

    def answer(sql, d):
        rows = []
        if "assets" in sql:
            rows.append({"c": "Asset", "o": 1, "v": "b-301-chiller-101", "k": "a1", "l": "CHILLER-101"})
        if "buildings" in sql and '"building_id"' in sql:
            rows.append({"c": "Building", "o": 2, "v": bid, "k": bid, "l": "Bishopsgate Tower"})
        return rows

    r = _Reader(answer)
    plan = {"focus": {"concept": "WorkOrder", "match": None, "states": [], "filters": []}, "constraints": [], "include": []}
    q = f"Cost to replace B-301-CHILLER-101 at building_id {bid}, and WO-B-301-9999 if any"
    out = asyncio.run(oq.add_missing_codes(r, _Onto(), plan, q))
    assert len(r.calls) == 1, [c[0] for c in r.calls]
    label, sql, params = r.calls[0]
    assert label == "resolve 3 across 3 concepts (exact)" and "UNION ALL" in sql and "= ANY(" in sql
    assert '"building_id"::text' in sql                      # a UUID in the question checks the keys too
    assert sorted(params["p0"]) == sorted([bid.lower(), "b-301-chiller-101", "wo-b-301-9999"])
    # codes are taken in sorted order, as the one-at-a-time loop did
    assert [(c["concept"], c["match"]) for c in out["constraints"]] == [("Building", bid), ("Asset", "B-301-CHILLER-101")]
    # asked again in the same read: remembered, no second query
    asyncio.run(oq.add_missing_codes(r, _Onto(), {"focus": {"concept": "WorkOrder", "match": None, "states": [], "filters": []},
                                                 "constraints": [], "include": []}, q))
    assert len(r.calls) == 1


def test_a_miss_on_the_named_concept_sweeps_the_others_in_one_query_per_mode():
    def answer(sql, d):
        if "UNION ALL" in sql and "~*" in sql:
            return [{"c": "Asset", "o": 0, "v": "x", "k": "a9", "l": "Grade-4 chiller"}]
        return []

    r = _Reader(answer)
    concept, keys, labels, note = asyncio.run(oq.resolve_match(r, _Onto(), "WorkOrder", "grade-4"))
    assert (concept, keys) == ("Asset", ["a9"]) and "matched Asset instead" in note
    # WorkOrder exact + search, then one exact union and one search union - 4, not 2 + 2 x 11
    assert [c[0] for c in r.calls] == ["resolve WorkOrder (exact)", "resolve WorkOrder (search)",
                                       "resolve 1 across 2 concepts (exact)", "resolve 1 across 2 concepts (search)"]
    asyncio.run(oq.resolve_match(r, _Onto(), "WorkOrder", "grade-4"))
    assert len(r.calls) == 4                                  # remembered


def test_a_uuid_match_on_its_own_concept_reads_the_key():
    bid = "93b8800f-754c-5805-9bd6-f65c8d263fe8"
    r = _Reader(lambda sql, d: [{"k": bid, "l": "Bishopsgate Tower"}] if '"building_id"::text' in sql else [])
    concept, keys, labels, note = asyncio.run(oq.resolve_match(r, _Onto(), "Building", bid))
    assert (concept, keys, labels, note) == ("Building", [bid], ["Bishopsgate Tower"], None) and len(r.calls) == 1
