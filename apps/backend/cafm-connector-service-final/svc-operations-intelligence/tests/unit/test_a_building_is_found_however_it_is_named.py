"""A building on the register is found however it is named — and a wrong one never is (29 Oct 2026).

The user added "JASMEET" (B-090765, 89 floors) and asked "HOW MANY FLOORS IN JASMEET BUILDING". It
was not found: every place compared the typed name with the stored one its own way, the whole
phrase in order. GET /api/energy/buildings/resolve (choose_building, read for answers) must now
resolve "JASMEET BUILDING", "jasmeet", "B090765", "tower hussain", "RT5 Crossrail" and "Jasmet" to
the right building, and say "ambiguous" with the candidates rather than pick one.

resolve-batch and the certificate ingest (graph_ingest) LINK what they resolve, so they stay on the
exact forms only — the same name, compacted or with the kind word out — never part of a name or a
close spelling. And the containment tier both use no longer files "Building 7" under "Building 70".
"""
from __future__ import annotations

import pytest

from src.engines.energy.building_resolver import (
    building_keys,
    choose_building,
    resolution_outcome,
)
from src.shared import name_lookup


def _b(bid, name, code=None, site_id=None, site_name=None):
    return building_keys({"building_id": bid, "name": name, "building_code": code,
                          "site_id": site_id, "site_name": site_name})


JASMEET = _b("u-jas", "JASMEET", "B-090765", "S-9")
HEIGHTS = _b("u-hts", "Jasmeet Heights", "JH-01", "S-10")
HUSSAIN = _b("u-hus", "Hussain Tower", "HT-01", "S-11")
RT5 = _b("u-rt5", "RT5, Crossrail", "RT5-01", "S-12")
B7 = _b("u-b7", "Building 7", "B7", "S-13")
B70 = _b("u-b70", "Building 70", "B70", "S-14")
ACME = _b("u-acme", "Acme Facilities House", "AF-01", "S-15")
INDEX = [JASMEET, HEIGHTS, HUSSAIN, RT5, B7, B70, ACME]


@pytest.mark.parametrize("typed, expected", [
    ("JASMEET BUILDING", "u-jas"),
    ("jasmeet", "u-jas"),
    ("Jasmeet's building", "u-jas"),
    ("tower hussain", "u-hus"),
    ("Hussain Tower building", "u-hus"),
    ("RT5 Crossrail", "u-rt5"),
    ("Jasmet", "u-jas"),
    ("Building 7", "u-b7"),
    ("Building 70", "u-b70"),
])
def test_a_read_resolves_the_building_however_it_is_named(typed, expected):
    row, reason = choose_building(INDEX, name=typed, loose=True)
    assert row is not None, (typed, reason)
    assert row["building_id"] == expected, (typed, reason)
    assert resolution_outcome(reason) == "resolved"


def test_a_code_typed_without_its_hyphen_resolves_as_a_code_or_as_a_name():
    row, reason = choose_building(INDEX, code="B090765")
    assert row["building_id"] == "u-jas" and reason == "building_code"
    row, reason = choose_building(INDEX, name="B090765", loose=True)
    assert row["building_id"] == "u-jas", reason


def test_a_close_spelling_says_it_was_one():
    row, reason = choose_building(INDEX, name="Jasmet", loose=True)
    assert row["building_id"] == "u-jas" and reason == "name_close_spelling"


def test_an_exact_name_beats_a_longer_one_that_starts_the_same():
    """"jasmeet" is exactly JASMEET; Jasmeet Heights only starts with it."""
    row, reason = choose_building(INDEX, name="jasmeet", loose=True)
    assert row["building_id"] == "u-jas"


def test_a_name_two_buildings_share_is_ambiguous_with_both_as_candidates():
    twins = [_b("u-a", "Jasmeet Heights North"), _b("u-b", "Jasmeet Heights South"), HUSSAIN]
    row, reason = choose_building(twins, name="Jasmeet Heights", loose=True)
    assert row is None and resolution_outcome(reason) == "review"
    from src.engines.energy.building_resolver import name_candidates
    assert {r["building_id"] for r in name_candidates(twins, "Jasmeet Heights", loose=True)} == {"u-a", "u-b"}


def test_the_kind_of_record_alone_names_nothing():
    for typed in ("the building", "building", "the site", "property"):
        row, reason = choose_building(INDEX, name=typed, loose=True)
        assert row is None and reason == "no_match", (typed, reason)


def test_building_7_is_never_building_70_and_never_block_7():
    only_70 = [B70, _b("u-blk7", "Block 7"), HUSSAIN]
    for loose in (False, True):
        row, reason = choose_building(only_70, name="Building 7", loose=loose)
        assert row is None and reason == "no_match", (loose, reason)


def test_a_link_path_never_matches_by_part_of_a_name_or_a_close_spelling():
    """resolve-batch and graph_ingest write the building they resolve: exact forms only."""
    for typed in ("tower hussain", "Jasmet"):
        row, reason = choose_building(INDEX, name=typed)
        assert row is None and reason == "no_match", (typed, reason)


def test_a_link_path_takes_the_exact_forms():
    row, reason = choose_building(INDEX, name="RT5 Crossrail")
    assert row["building_id"] == "u-rt5" and resolution_outcome(reason) == "resolved"
    row, reason = choose_building(INDEX, name="B090765")
    assert row["building_id"] == "u-jas" and resolution_outcome(reason) == "resolved"
    row, reason = choose_building([JASMEET, HEIGHTS], name="JASMEET BUILDING")
    assert row["building_id"] == "u-jas" and resolution_outcome(reason) == "resolved"


def test_containment_no_longer_cuts_a_number_or_a_word():
    """Before: "building 7" in "building 70" was a containment hit, and a certificate for
    Building 7 was filed against Building 70 when Building 7 was not on the register."""
    row, reason = choose_building([B70, HUSSAIN], name="Building 7")
    assert row is None and reason == "no_match"
    row, reason = choose_building([_b("u-17", "17 Kingsway Court")], name="7 Kingsway Court")
    assert row is None and reason == "no_match"
    # A longer document title still finds the building named inside it, as before.
    row, reason = choose_building([B7, B70], name="Building 7, Plant Room")
    assert row["building_id"] == "u-b7" and reason == "name_contains"


def test_the_helper_rungs_match_the_ladder():
    k = name_lookup.BUILDING_KIND_WORDS
    assert name_lookup.rung("JASMEET BUILDING", "JASMEET", k) == 0
    assert name_lookup.rung("B090765", "B-090765", k) == 1
    assert name_lookup.rung("RT5 Crossrail", "RT5, Crossrail", k) == 1
    assert name_lookup.rung("tower hussain", "Hussain Tower", k) == 3
    assert name_lookup.rung("Jasmet", "JASMEET", k) == 4
    assert name_lookup.rung("Building 7", "Building 70", k) is None
    assert name_lookup.rung("Building 7", "Block 7", k) is None
    assert name_lookup.rung("the building", "Building 7", k) is None
    assert name_lookup.rung("Jasmet", "JASMEET", k, max_rung=1) is None


# ── GET /buildings/resolve and POST /buildings/resolve-batch, with the index read stubbed ──────

@pytest.fixture
def stub_index(monkeypatch):
    from src.engines.energy import building_resolver as br

    async def _index(session, **_):
        return INDEX

    async def _assets(session, codes):
        return {}

    monkeypatch.setattr(br, "load_building_index", _index)
    monkeypatch.setattr(br, "load_asset_building_map", _assets)
    return br


@pytest.mark.asyncio
async def test_the_resolve_route_finds_jasmeet_and_says_a_close_spelling_was_one(stub_index):
    out = await stub_index.resolve_one(None, name="JASMEET BUILDING")
    assert out["outcome"] == "resolved" and out["building_id"] == "u-jas"
    out = await stub_index.resolve_one(None, name="Jasmet")
    assert out["building_id"] == "u-jas" and out["reason"] == "name_close_spelling"
    assert "JASMEET" in out["note"]


@pytest.mark.asyncio
async def test_the_resolve_route_lists_every_building_an_ambiguous_name_could_mean(stub_index, monkeypatch):
    twins = [_b("u-n", "Hussain Tower North"), _b("u-s", "Hussain Tower South"), JASMEET]

    async def _twins(session, **_):
        return twins

    monkeypatch.setattr(stub_index, "load_building_index", _twins)
    out = await stub_index.resolve_one(None, name="tower hussain")
    assert out["outcome"] == "review" and out["building_id"] is None
    assert {c["building_id"] for c in out["candidates"]} == {"u-n", "u-s"}
    out = await stub_index.resolve_one(None, name="the building")
    assert out["outcome"] == "unmatched" and out["candidates"] == []


@pytest.mark.asyncio
async def test_resolve_batch_links_exact_forms_only(stub_index):
    out = await stub_index.resolve_batch(None, [{"name": "JASMEET BUILDING"}, {"name": "B090765"},
                                                {"name": "tower hussain"}, {"name": "Jasmet"}])
    got = [(r["outcome"], r["building_id"]) for r in out["results"]]
    assert got == [("resolved", "u-jas"), ("resolved", "u-jas"), ("unmatched", None), ("unmatched", None)]
