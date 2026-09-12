"""The asset tier: a work order lands with the plant it services.

Not a matching tier. The other tiers turn a string into a building by comparing it against
names and codes. This one reads a link the graph already holds — the asset was placed when
it was ingested, and a work order against it inherits that. The reason recorded is
``asset``, never ``name``, so a work order placed this way is never read back as having
named its own building.
"""
from __future__ import annotations

import asyncio


from src.engines.energy import building_resolver as R

BID_A = "a0000000-0000-0000-0000-00000000000a"
BID_B = "a0000000-0000-0000-0000-00000000000b"

INDEX = [
    R.building_keys({"building_id": BID_A, "name": "Bishopsgate Tower",
                     "building_code": "BT-01", "site_id": "S-01"}),
    R.building_keys({"building_id": BID_B, "name": "Riverside Lab Block",
                     "site_id": "S-02"}),
]


def _patch(monkeypatch, asset_map):
    async def fake_index(session, **_):
        return INDEX

    async def fake_assets(session, codes):
        return asset_map

    monkeypatch.setattr(R, "load_building_index", fake_index)
    monkeypatch.setattr(R, "load_asset_building_map", fake_assets)


def _batch(monkeypatch, items, asset_map):
    _patch(monkeypatch, asset_map)
    return asyncio.run(R.resolve_batch(object(), items))


def test_a_work_order_inherits_its_assets_building(monkeypatch):
    out = _batch(monkeypatch, [{"asset_code": "AHU-004"}], {"AHU004": {"asset_id": "aid-1", "building_id": BID_A}})
    r = out["results"][0]
    assert r["building_id"] == BID_A
    assert r["asset_id"] == "aid-1", "the asset's own key travels with its building"
    assert r["reason"] == "asset" and r["outcome"] == "resolved"
    assert r["building"] == "Bishopsgate Tower", "the label still comes from the index"


def test_the_asset_beats_a_building_name_on_the_same_row(monkeypatch):
    """The asset is where the plant physically is, recorded. A building name on a work
    order line is a string somebody typed."""
    out = _batch(
        monkeypatch,
        [{"asset_code": "AHU-004", "name": "Riverside Lab Block"}],
        {"AHU004": {"asset_id": "aid-1", "building_id": BID_A}},
    )
    assert out["results"][0]["building_id"] == BID_A
    assert out["results"][0]["reason"] == "asset"


def test_an_unplaced_asset_falls_through_to_the_matching_tiers(monkeypatch):
    """The asset tier declining is not the end — whatever else the row says still runs."""
    out = _batch(
        monkeypatch,
        [{"asset_code": "AHU-999", "name": "Bishopsgate Tower"}],
        {},
    )
    assert out["results"][0]["building_id"] == BID_A
    assert out["results"][0]["reason"] == "name"


def test_a_row_with_only_an_unplaced_asset_resolves_to_nothing(monkeypatch):
    out = _batch(monkeypatch, [{"asset_code": "AHU-999"}], {})
    assert out["results"][0]["building_id"] is None
    assert out["results"][0]["outcome"] == "unmatched"


def test_asset_codes_match_however_they_are_punctuated(monkeypatch):
    out = _batch(monkeypatch, [{"asset_code": " ahu-004 "}], {"AHU004": {"asset_id": "aid-1", "building_id": BID_A}})
    assert out["results"][0]["building_id"] == BID_A


def test_the_whole_batch_costs_one_asset_read(monkeypatch):
    """5,000 work orders is two queries, not 5,000."""
    calls = {"n": 0}

    async def counting(session, codes):
        calls["n"] += 1
        return {"AHU004": {"asset_id": "aid-1", "building_id": BID_A}}

    async def fake_index(session, **_):
        return INDEX

    monkeypatch.setattr(R, "load_building_index", fake_index)
    monkeypatch.setattr(R, "load_asset_building_map", counting)
    items = [{"asset_code": "AHU-004"} for _ in range(500)]
    out = asyncio.run(R.resolve_batch(object(), items))
    assert calls["n"] == 1
    assert out["by_reason"]["asset"] == 500
    assert out["assets_placed"] == 1 and out["assets_known"] == 1


def test_a_mixed_file_reports_each_tier_separately(monkeypatch):
    out = _batch(
        monkeypatch,
        [
            {"asset_code": "AHU-004"},                 # asset
            {"code": "BT-01"},                         # building_code
            {"name": "Nowhere House"},                 # no_match
        ],
        {"AHU004": {"asset_id": "aid-1", "building_id": BID_A}},
    )
    assert out["by_reason"] == {"asset": 1, "building_code": 1, "no_match": 1}
    assert out["by_outcome"] == {"resolved": 2, "unmatched": 1}


# ── the map itself ───────────────────────────────────────────────────────────


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


class _Session:
    def __init__(self, rows):
        self._rows = rows

    def begin_nested(self):
        class _Ctx:
            async def __aenter__(s):
                return s

            async def __aexit__(s, *a):
                return False
        return _Ctx()

    async def execute(self, *_a, **_k):
        return _Rows(self._rows)


def _map(monkeypatch, rows, codes, columns=("asset_id", "asset_code", "building_id")):
    async def fake_shape(session, **_):
        return {"assets": {"exists": True, "key": "asset_id", "columns": set(columns)}}

    import src.engines.energy.building_rollup as BR
    monkeypatch.setattr(BR, "graph_shape", fake_shape)
    return asyncio.run(R.load_asset_building_map(_Session(rows), codes))


def test_only_the_codes_asked_about_come_back(monkeypatch):
    rows = [{"code": "AHU-004", "aid": "a1", "bid": BID_A},
            {"code": "CH-1", "aid": "a2", "bid": BID_B}]
    assert _map(monkeypatch, rows, ["AHU-004"]) == {
        "AHU004": {"asset_id": "a1", "building_id": BID_A}}


def test_one_asset_code_in_two_buildings_resolves_to_neither(monkeypatch):
    """Which is right is not knowable from here, and picking one files the work order
    against a building that may not hold the plant at all."""
    rows = [{"code": "AHU-004", "aid": "a1", "bid": BID_A},
            {"code": "AHU-004", "aid": "a2", "bid": BID_B}]
    assert _map(monkeypatch, rows, ["AHU-004"]) == {}


def test_the_same_asset_listed_twice_in_one_building_is_not_a_clash(monkeypatch):
    rows = [{"code": "AHU-004", "aid": "a1", "bid": BID_A},
            {"code": "AHU-004", "aid": "a1", "bid": BID_A}]
    assert _map(monkeypatch, rows, ["AHU-004"]) == {
        "AHU004": {"asset_id": "a1", "building_id": BID_A}}


def test_asking_about_nothing_reads_nothing(monkeypatch):
    row = [{"code": "AHU-004", "aid": "a1", "bid": BID_A}]
    assert _map(monkeypatch, row, []) == {}
    assert _map(monkeypatch, row, [None, "  "]) == {}


def test_assets_that_predate_the_graph_still_yield_the_asset_key(monkeypatch):
    """assets only gains building_id via the migration. Before it no work order can be
    PLACED this way — but it can still record which asset it is against, which is what
    lets the building be filled in once the migration runs."""
    rows = [{"code": "AHU-004", "aid": "a1", "bid": None}]
    out = _map(monkeypatch, rows, ["AHU-004"], columns=("asset_id", "asset_code"))
    assert out == {"AHU004": {"asset_id": "a1", "building_id": None}}


def test_an_unplaced_asset_names_the_ordering_problem_not_just_no_match(monkeypatch):
    """'Nothing matched' is true but useless. 'That asset has no building either' names
    the actual problem and its order of operations: assets before work orders."""
    out = _batch(monkeypatch, [{"asset_code": "PUMP-9"}], {})
    r = out["results"][0]
    assert r["reason"] == "asset_not_placed"
    assert r["outcome"] == "unmatched"


def test_a_row_naming_no_asset_still_reports_a_plain_no_match(monkeypatch):
    out = _batch(monkeypatch, [{"name": "Nowhere House"}], {})
    assert out["results"][0]["reason"] == "no_match"
