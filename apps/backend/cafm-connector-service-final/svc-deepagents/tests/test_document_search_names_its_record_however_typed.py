"""The documents of a vendor, a contract or an asset are found however it is typed (29 Oct 2026).

"HOW MANY FLOORS IN JASMEET BUILDING" found nothing because every place that compared a typed name
with a stored one did it its own way. search_documents passed what the agent typed straight to
/api/documents/search, which compares a vendor with ILIKE '%text%', a contract_ref with = and an
asset with code = or name ILIKE: "Acme contractor" and "Facilities Acme" found no document of
"Acme Facilities Ltd", "CT1042" none of contract "CT-1042", "B090765" none of asset "B-090765",
"Building 7" read Building 70's papers as well, and "the contractor" was searched for as a name.

The names are now resolved first by the shared ladder (agents/name_match.py) against the caller's
own registers — the assets, and the contracts with their vendors — and the record's name as stored
is what is searched. A name several records hold is reported as ambiguous, never one picked.

The fake routes answer as the real ones do: substring, equality, nothing more.
"""
from __future__ import annotations

import asyncio
import uuid

import httpx
import pytest

from src.agents import document_search_tool as dst
from src.agents import energy_intelligence_agent as energy
from src.agents import wo_engine_agent as wo


def _id(name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "hoistra-test/" + name))


B_JASMEET, B_RIVER = _id("b-jasmeet"), _id("b-river")

ASSETS = [
    {"asset_id": _id("a3"), "asset_name": "Cold Water Booster Set", "asset_code": "B-090765", "building_id": B_JASMEET},
    {"asset_id": _id("a4"), "asset_name": "Building 7 Boiler", "asset_code": "B7-BLR-1", "building_id": B_RIVER},
    {"asset_id": _id("a5"), "asset_name": "Building 70 Boiler", "asset_code": "B70-BLR-1", "building_id": B_RIVER},
    {"asset_id": _id("a6"), "asset_name": "JASMEET Chiller", "asset_code": "JAS-CH-1", "building_id": B_JASMEET},
    {"asset_id": _id("a9"), "asset_name": "Riverside Chiller", "asset_code": "RS-CH-1", "building_id": B_RIVER},
    {"asset_id": _id("a7"), "asset_name": "Air Handling Unit 1", "asset_code": "AHU-1", "building_id": B_JASMEET},
    {"asset_id": _id("a8"), "asset_name": "Air Handling Unit 1", "asset_code": "AHU-1", "building_id": B_RIVER},
]
CONTRACTS = [
    {"contract_ref": "CT-1042", "vendor_name": "Acme Facilities Ltd"},
    {"contract_ref": "CT-1042", "vendor_name": "Acme Facilities Ltd"},      # one row per SLA parameter
    {"contract_ref": "APX-77", "vendor_name": "Apex Lifts Ltd"},
    {"contract_ref": "APF-3", "vendor_name": "Apex Fire Ltd"},
]
DOCS = [
    {"title": "Acme Facilities Ltd - Hard FM contract", "vendor_name": "Acme Facilities Ltd", "contract_ref": "CT-1042"},
    {"title": "Booster set warranty", "asset_code": "B-090765", "asset_name": "Cold Water Booster Set"},
    {"title": "Building 7 boiler service report", "asset_code": "B7-BLR-1", "asset_name": "Building 7 Boiler"},
    {"title": "Building 70 boiler service report", "asset_code": "B70-BLR-1", "asset_name": "Building 70 Boiler"},
    {"title": "AHU-1 warranty", "asset_code": "AHU-1", "asset_name": "Air Handling Unit 1"},
    {"title": "Gas Safe certificate", "vendor_name": "Northern Gas Services"},   # certificates only, no contract
]


def _like(term, value) -> bool:
    return str(term).lower() in str(value or "").lower()


def _search(params) -> dict:
    """/api/documents/search's linked_documents: vendor ILIKE, contract_ref =, asset code = or name ILIKE."""
    v, c, a = params.get("vendor"), params.get("contract_ref"), params.get("asset")
    found = [d for d in DOCS
             if (v and (_like(v, d.get("vendor_name")) or _like(v, d["title"])))
             or (c and c.lower() == str(d.get("contract_ref") or "").lower())
             or (a and (a.lower() == str(d.get("asset_code") or "").lower() or _like(a, d.get("asset_name"))))]
    return {"ok": True, "about": {k: params[k] for k in ("vendor", "contract_ref", "asset") if k in params},
            "documents_found": len(found), "searched": [{"title": d["title"]} for d in found], "passages": []}


class FakeRoutes:
    def __init__(self, fail=()):
        self.calls: list[tuple[str, dict]] = []
        self.fail = set(fail)

    async def __call__(self, method, base, path, **kwargs):
        params = dict(kwargs.get("params") or {})
        self.calls.append((path, params))
        if path in self.fail:
            req = httpx.Request("GET", "http://svc" + path)
            raise httpx.ConnectError("connection refused", request=req)

        class R:
            def __init__(self, body):
                self._body = body

            def json(self):
                return self._body

        if path == "/api/assets":
            q = str(params.get("q") or "").lower()
            return R([x for x in ASSETS if q in x["asset_name"].lower() or q in x["asset_code"].lower()])
        if path == "/api/contract-performance/contracts":
            return R({"parameters": [dict(x) for x in CONTRACTS]})
        if path == "/api/documents/search":
            return R(_search(params))
        if path == "/api/energy/buildings/resolve":
            if params.get("name") == "JASMEET":
                return R({"ok": True, "outcome": "resolved", "building_id": B_JASMEET})
            return R({"ok": True, "outcome": "not_found", "candidates": []})
        raise AssertionError("unexpected path " + path)

    def searches(self):
        return [p for path, p in self.calls if path == "/api/documents/search"]


@pytest.fixture
def routes(monkeypatch):
    fake = FakeRoutes()
    for mod in (dst, wo, energy):
        monkeypatch.setattr(mod, "_request", fake)
    return fake


def _run(**args):
    return asyncio.run(dst.search_documents.ainvoke({"question": "what does it say about service credits?", **args}))


@pytest.mark.parametrize("typed", ["Acme contractor", "Facilities Acme", "Acme", "acme facilities ltd's"])
def test_a_vendor_is_found_however_it_is_typed(routes, typed):
    out = _run(vendor=typed)
    assert out["documents_found"] == 1 and out["searched"][0]["title"].startswith("Acme Facilities Ltd")
    assert routes.searches()[-1]["vendor"] == "Acme Facilities Ltd"


def test_a_name_two_vendors_share_is_ambiguous_never_one(routes):
    out = _run(vendor="Apex")
    assert out["ok"] is False and out["error"] == "vendor_ambiguous"
    assert sorted(out["candidates"]) == ["Apex Fire Ltd", "Apex Lifts Ltd"]
    assert routes.searches() == []


def test_a_vendor_with_no_contract_is_still_searched_as_typed_then_without_kind_words(routes):
    out = _run(vendor="Northern Gas contractor")
    assert out["documents_found"] == 1 and out["searched"][0]["title"] == "Gas Safe certificate"
    assert [p["vendor"] for p in routes.searches()] == ["Northern Gas contractor", "northern gas"]


def test_words_for_a_vendor_alone_name_no_vendor(routes):
    out = _run(vendor="the contractor")
    assert out["ok"] is False and out["error"] == "names_nothing"
    assert routes.searches() == []


@pytest.mark.parametrize("typed", ["CT1042", "contract CT-1042", "ct 1042"])
def test_a_contract_ref_is_found_however_it_is_typed(routes, typed):
    out = _run(contract_ref=typed)
    assert out["documents_found"] == 1
    assert routes.searches()[-1]["contract_ref"] == "CT-1042"


@pytest.mark.parametrize("typed,code", [
    ("B090765", "B-090765"),                # a code typed without its hyphen
    ("Building 7", "B7-BLR-1"),             # never Building 70's papers as well
    ("booster set cold water", "B-090765"),  # every word, any order
    ("AHU-1", "AHU-1"),                      # a code two buildings share: the code, so both
])
def test_an_asset_is_resolved_to_its_code_before_the_search(routes, typed, code):
    out = _run(asset=typed)
    assert routes.searches()[-1]["asset"] == code
    assert out["documents_found"] == 1


def test_a_name_two_assets_share_is_ambiguous_unless_the_building_settles_it(routes):
    out = _run(asset="Chiller")
    assert out["ok"] is False and out["error"] == "asset_ambiguous"
    assert sorted(c["asset_code"] for c in out["candidates"]) == ["JAS-CH-1", "RS-CH-1"]
    assert routes.searches() == []
    _run(asset="Chiller", building_name="JASMEET")
    assert routes.searches()[-1]["asset"] == "JAS-CH-1"


def test_when_the_asset_register_cannot_be_read_the_name_goes_as_typed(monkeypatch):
    fake = FakeRoutes(fail={"/api/assets"})
    for mod in (dst, wo, energy):
        monkeypatch.setattr(mod, "_request", fake)
    out = _run(asset="Cold Water Booster Set")
    assert out["documents_found"] == 1 and fake.searches()[-1]["asset"] == "Cold Water Booster Set"
