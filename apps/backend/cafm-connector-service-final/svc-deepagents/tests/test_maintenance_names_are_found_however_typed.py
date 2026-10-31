"""An asset, a location or a work order is found however it is typed (29 Oct 2026).

"HOW MANY FLOORS IN JASMEET BUILDING" found nothing: the building is named "JASMEET", and every
place that compared a typed name with a stored one did it its own way. The maintenance tools sent
the text straight to svc-work-order-management, which compares it with ILIKE '%text%': so
"JASMEET BUILDING" missed the location "JASMEET", "tower hussain" missed "Hussain Tower", "RT5
Crossrail" missed "RT5, Crossrail", "B090765" missed "B-090765", "Jasmet" missed "JASMEET" — and
"Building 7" found "Building 70" as well. The status tool took only the id, so a code people quote
(WO-1042) was a 404, and a building's NAME passed where its id goes reached the route as an id.

The tools now rank what the route returns by the shared ladder (agents/name_match.py): exact,
compact, phrase at a word start, every word in any order, close spelling only when one record is
clearly closest. A name several records share returns all of them; "the building" names nothing.

The fake below answers like the real routes: a substring of the name, code or serial, case
ignored, nothing more.
"""
from __future__ import annotations

import asyncio
import uuid

import httpx
import pytest

from src.agents import energy_intelligence_agent as energy
from src.agents import thread_scope
from src.agents import wo_engine_agent as wo


def _id(name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "hoistra-test/" + name))


B_JASMEET, B_HUSSAIN, B_RIVER = _id("b-jasmeet"), _id("b-hussain"), _id("b-river")

ASSETS = [
    {"asset_id": _id("a1"), "asset_name": "Hussain Tower Booster Pump", "asset_code": "HT-BP-01",
     "serial_number": "SN-77812", "building_id": B_HUSSAIN},
    {"asset_id": _id("a2"), "asset_name": "RT5, Crossrail Lift", "asset_code": "RT5-L1",
     "serial_number": None, "building_id": B_RIVER},
    {"asset_id": _id("a3"), "asset_name": "Cold Water Booster Set", "asset_code": "B-090765",
     "serial_number": None, "building_id": B_JASMEET},
    {"asset_id": _id("a4"), "asset_name": "Building 7 Boiler", "asset_code": "B7-BLR-1",
     "serial_number": None, "building_id": B_RIVER},
    {"asset_id": _id("a5"), "asset_name": "Building 70 Boiler", "asset_code": "B70-BLR-1",
     "serial_number": None, "building_id": B_RIVER},
    {"asset_id": _id("a6"), "asset_name": "JASMEET Chiller", "asset_code": "JAS-CH-1",
     "serial_number": None, "building_id": B_JASMEET},
    {"asset_id": _id("a7"), "asset_name": "Air Handling Unit 1", "asset_code": "AHU-1",
     "serial_number": None, "building_id": B_JASMEET},
    {"asset_id": _id("a8"), "asset_name": "Air Handling Unit 1", "asset_code": "AHU-1",
     "serial_number": None, "building_id": B_HUSSAIN},
    {"asset_id": _id("a9"), "asset_name": "Jasmeet Heights Chiller", "asset_code": "JH-CH-1",
     "serial_number": None, "building_id": B_RIVER},
]
LOCATIONS = [
    {"location_id": _id("l1"), "name": "JASMEET", "building_id": B_JASMEET},
    {"location_id": _id("l2"), "name": "Jasmeet Heights", "building_id": B_RIVER},
    {"location_id": _id("l3"), "name": "Hussain Tower", "building_id": B_HUSSAIN},
    {"location_id": _id("l4"), "name": "The Building Annex", "building_id": B_RIVER},
]
WORK_ORDERS = [
    {"work_order_id": "7001", "wo_code": "WO-0701", "asset": "Building 7 Boiler", "status": "active"},
    {"work_order_id": "7002", "wo_code": "WO-0702", "asset": "Building 70 Boiler", "status": "active"},
    {"work_order_id": "7003", "wo_code": "WO-0703", "asset": "RT5, Crossrail Lift", "status": "active"},
    {"work_order_id": "7004", "wo_code": "WO-0704", "asset": "JASMEET Chiller", "status": "completed"},
    {"work_order_id": "8812", "wo_code": "WO-1042", "asset": "Air Handling Unit 1", "status": "active"},
]


def _not_found(path: str):
    req = httpx.Request("GET", "http://wo" + path)
    raise httpx.HTTPStatusError("404 Not Found", request=req,
                                response=httpx.Response(404, request=req, text='{"detail": "not found"}'))


def _like(term, *values) -> bool:
    """ILIKE '%term%' on any of the values — what the routes do."""
    t = str(term or "").lower()
    return any(t in str(v or "").lower() for v in values)


class FakeRoutes:
    """svc-work-order-management and the building resolver, answering as the real routes do."""

    def __init__(self, resolve=None):
        self.calls: list[tuple[str, dict]] = []
        self.resolve = resolve or {}

    async def __call__(self, method, base, path, **kwargs):
        params = dict(kwargs.get("params") or {})
        self.calls.append((path, params))

        class R:
            def __init__(self, body):
                self._body = body

            def json(self):
                return self._body

        if path == "/api/assets":
            rows = [a for a in ASSETS if not params.get("q")
                    or _like(params["q"], a["asset_name"], a["asset_code"], a["serial_number"])]
            return R(sorted(rows, key=lambda a: a["asset_name"])[: int(params.get("limit", 50))])
        if path.startswith("/api/assets/"):
            key = path.rsplit("/", 1)[1]
            for a in ASSETS:
                if a["asset_id"] == key:
                    return R(dict(a, warranty_expiry="2027-03-31"))
            _not_found(path)
        if path == "/api/locations":
            rows = [x for x in LOCATIONS if not params.get("q") or _like(params["q"], x["name"])]
            return R(rows[: int(params.get("limit", 100))])
        if path == "/api/work-orders/":
            rows = [w for w in WORK_ORDERS if not params.get("asset") or _like(params["asset"], w["asset"])]
            return R(rows[: int(params.get("limit", 20))])
        if path.endswith("/status-track"):
            key = path.split("/")[3]
            for w in WORK_ORDERS:
                if w["work_order_id"] == key:          # keyed on the id only, as the real route is
                    return R({"found": True, "work_order_id": key, "wo_code": w["wo_code"], "status": w["status"]})
            _not_found(path)
        if path.startswith("/api/work-orders/"):
            key = path.split("/")[3]
            for w in WORK_ORDERS:
                if key in (w["work_order_id"], w["wo_code"]):   # the id or the code, as _get_wo_or_404
                    return R(dict(w))
            _not_found(path)
        if path == "/api/energy/buildings/resolve":
            return R(self.resolve.get(params.get("name"), {"ok": True, "outcome": "not_found", "candidates": []}))
        if path == "/api/maintenance/decisions":
            return R({"count": 0, "rows": [], "asked": params})
        raise AssertionError("unexpected path " + path)


@pytest.fixture
def routes(monkeypatch):
    fake = FakeRoutes(resolve={
        "JASMEET": {"ok": True, "outcome": "resolved", "building_id": B_JASMEET, "building": "JASMEET"},
        "Riverside": {"ok": True, "outcome": "review", "reason": "multiple_name_matches", "building_id": None,
                      "candidates": [{"name": "Riverside Court"}, {"name": "Riverside House"}]},
    })
    monkeypatch.setattr(wo, "_request", fake)
    monkeypatch.setattr(energy, "_request", fake)
    return fake


def _run(tool, **args):
    return asyncio.run(tool.ainvoke(args))


def _codes(rows):
    return sorted(r.get("asset_code") for r in rows)


# ── search_assets ────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("typed,codes", [
    ("pump booster hussain tower", ["HT-BP-01"]),     # every word, any order
    ("RT5 Crossrail Lift", ["RT5-L1"]),               # the comma the register has, not typed
    ("B090765", ["B-090765"]),                        # a code typed without its hyphen
    ("Building 7", ["B7-BLR-1"]),                     # never Building 70
    ("Jasmet Chiller", ["JAS-CH-1"]),                 # one letter missing, clearly closest
    ("AHU-1", ["AHU-1", "AHU-1"]),                    # a code two buildings share: both, never one
    ("JASMEET Chiller asset", ["JAS-CH-1"]),          # the kind word typed beside the name
])
def test_an_asset_is_found_however_it_is_typed(routes, typed, codes):
    rows = _run(wo.search_assets, query=typed)
    assert _codes(rows) == codes


def test_a_close_spelling_says_it_is_one(routes):
    rows = _run(wo.search_assets, query="Jasmet Chiller")
    assert [r["asset_code"] for r in rows] == ["JAS-CH-1"]
    assert "closest" in rows[0]["match_note"] and "Jasmet Chiller" in rows[0]["match_note"]


def test_kind_words_alone_name_no_asset_and_read_nothing(routes):
    rows = _run(wo.search_assets, query="the asset")
    assert routes.calls == []
    assert rows and rows[0]["error"] == "names_nothing"


def test_an_empty_search_still_lists_the_register(routes):
    rows = _run(wo.search_assets, query="", limit=5)
    assert len(rows) == 5 and routes.calls == [("/api/assets", {"q": "", "limit": 5})]


# ── get_asset_details ───────────────────────────────────────────────────────────────────────

def test_details_by_code_resolve_the_code_to_its_asset(routes):
    out = _run(wo.get_asset_details, asset_id="B090765")
    assert out["asset_code"] == "B-090765" and out["warranty_expiry"] == "2027-03-31"
    assert routes.calls[-1][0] == "/api/assets/" + _id("a3")


def test_details_for_a_shared_code_say_it_is_ambiguous_and_pick_none(routes):
    out = _run(wo.get_asset_details, asset_id="AHU-1")
    assert out["error"] == "asset_ambiguous"
    assert sorted(c["asset_id"] for c in out["candidates"]) == sorted([_id("a7"), _id("a8")])
    assert not any(p.startswith("/api/assets/") for p, _ in routes.calls)


def test_details_by_uuid_read_the_asset_directly(routes):
    out = _run(wo.get_asset_details, asset_id=_id("a6"))
    assert out["asset_name"] == "JASMEET Chiller"
    assert [p for p, _ in routes.calls] == ["/api/assets/" + _id("a6")]


def test_details_for_a_name_on_no_record_say_not_found(routes):
    out = _run(wo.get_asset_details, asset_id="Kensington Palace Boiler")
    assert out["error"] == "asset_not_found" and out["asked_for"] == "Kensington Palace Boiler"


# ── search_locations ─────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("typed,names", [
    ("JASMEET BUILDING", ["JASMEET"]),                # exactly JASMEET, not Jasmeet Heights
    ("tower hussain", ["Hussain Tower"]),
    ("hussain tower's", ["Hussain Tower"]),           # a possessive
    ("Jasmeet Heights", ["Jasmeet Heights"]),
])
def test_a_location_is_found_however_it_is_typed(routes, typed, names):
    rows = _run(wo.search_locations, query=typed)
    assert sorted(r["name"] for r in rows) == names


def test_the_building_names_no_location(routes):
    rows = _run(wo.search_locations, query="the building")
    assert routes.calls == []
    assert rows and rows[0]["error"] == "names_nothing"


def test_no_query_still_lists_every_location(routes):
    rows = _run(wo.search_locations)
    assert len(rows) == len(LOCATIONS) and routes.calls == [("/api/locations", {})]


# ── list_work_orders(asset=...) ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("typed,ids", [
    ("Building 7", ["7001"]),                         # the route's substring also returned 7002
    ("RT5 Crossrail", ["7003"]),
    ("Jasmet Chiller", ["7004"]),
])
def test_work_orders_on_an_asset_are_found_however_it_is_typed(routes, typed, ids):
    rows = _run(wo.list_work_orders, asset=typed)
    assert sorted(r["work_order_id"] for r in rows) == ids


def test_work_orders_keep_their_other_filters(routes):
    _run(wo.list_work_orders, asset="RT5 Crossrail", status="active")
    assert routes.calls and all(p.get("status") == "active" for _, p in routes.calls)


# ── work order references ────────────────────────────────────────────────────────────────────
# extract_work_order_id_from_text still reads only the generated 14-digit key: the orchestrator's
# status shortcut (tests/test_the_work_order_status_shortcut_never_guesses.py) relies on that, so a
# register code - not unique on the register - goes to the agents. The tool the agents call takes
# the code.

def test_the_status_of_a_work_order_by_its_register_code(routes):
    out = _run(wo.get_work_order_status_track, work_order_id="WO-1042")
    assert out.get("found") is True and out["work_order_id"] == "8812" and out["wo_code"] == "WO-1042"


def test_a_code_on_no_work_order_is_still_not_found(routes):
    out = _run(wo.get_work_order_status_track, work_order_id="WO-9999")
    assert out.get("status_code") == 404


# ── list_maintenance_decisions(building_id=...) ─────────────────────────────────────────────

def test_a_building_name_passed_as_its_id_is_resolved(routes):
    out = _run(wo.list_maintenance_decisions, building_id="JASMEET")
    assert out["asked"]["building_id"] == B_JASMEET


def test_a_thread_building_that_does_not_resolve_is_never_read_as_the_whole_portfolio(routes):
    token = thread_scope.active_scope.set({"building_name": "Riverside"})
    try:
        out = _run(wo.list_maintenance_decisions)
    finally:
        thread_scope.active_scope.reset(token)
    assert out["error"] == "building_not_resolved" and len(out["candidates"]) == 2
    assert not any(p == "/api/maintenance/decisions" for p, _ in routes.calls)


def test_a_building_id_is_passed_through(routes):
    out = _run(wo.list_maintenance_decisions, building_id=B_RIVER)
    assert out["asked"]["building_id"] == B_RIVER
    assert [p for p, _ in routes.calls] == ["/api/maintenance/decisions"]
