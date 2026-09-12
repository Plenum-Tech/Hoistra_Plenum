"""Building resolver — the tiers, and the refusal to guess. Pure, no DB."""
from __future__ import annotations

from src.engines.energy.building_resolver import (
    building_keys,
    choose_building,
    resolution_outcome,
)


def _b(bid, name, code=None, site_id=None, site_name=None):
    return building_keys({
        "building_id": bid, "name": name, "building_code": code,
        "site_id": site_id, "site_name": site_name,
    })


BISHOPSGATE = _b("u-1", "Bishopsgate Tower", "BT-01", "S-01", "Bishopsgate Estate")
KINGSWAY = _b("u-2", "Kingsway House", "KH-01", "S-03", "Kingsway Estate")
NORTH = _b("u-3", "Riverside North Wing", "RN-01", "S-02", "Riverside Campus")
LAB = _b("u-4", "Riverside Lab Block", "RL-01", "S-02", "Riverside Campus")
INDEX = [BISHOPSGATE, KINGSWAY, NORTH, LAB]


def test_an_exact_code_is_definitive():
    row, reason = choose_building(INDEX, code="BT-01", name="something else entirely")
    assert row["building_id"] == "u-1" and reason == "building_code"


def test_an_exact_name_resolves():
    row, reason = choose_building(INDEX, name="Bishopsgate Tower")
    assert row["building_id"] == "u-1" and reason == "name"


def test_name_matching_ignores_case_and_punctuation():
    row, reason = choose_building(INDEX, name="  bishopsgate  tower ")
    assert row["building_id"] == "u-1" and reason == "name"


def test_containment_resolves_a_longer_document_title():
    row, reason = choose_building(INDEX, name="Kingsway House, 12 Kingsway")
    assert row["building_id"] == "u-2" and reason == "name_contains"


def test_a_short_fragment_never_matches_by_containment():
    """'Mill' inside 'Mill House' says nothing — below the length guard it is not a match."""
    row, reason = choose_building([_b("u-9", "Mill House")], name="Mill")
    assert row is None and reason == "no_match"


def test_a_wildly_longer_string_is_not_the_same_building():
    row, reason = choose_building(
        INDEX, name="Bishopsgate Tower Annexe Car Park And Surrounding Grounds Phase Two"
    )
    assert row is None and reason == "no_match"


def test_two_buildings_with_the_same_name_resolve_to_neither():
    """The central rule: an ambiguous answer is not an answer. Filing against the wrong
    building misstates two buildings' obligations at once."""
    dupes = [_b("u-a", "Tech Park"), _b("u-b", "Tech Park")]
    row, reason = choose_building(dupes, name="Tech Park")
    assert row is None and reason == "ambiguous_name"
    assert resolution_outcome(reason) == "review"


def test_a_site_with_one_building_resolves_through_the_site():
    row, reason = choose_building(INDEX, site_id="S-01")
    assert row["building_id"] == "u-1" and reason == "site_sole_building"


def test_a_campus_does_not_resolve_through_its_site():
    """S-02 holds two buildings, so naming the campus names the estate, not the structure."""
    row, reason = choose_building(INDEX, site_id="S-02")
    assert row is None and reason == "site_has_several_buildings"
    assert resolution_outcome(reason) == "review"


def test_a_site_name_resolves_when_that_site_has_one_building():
    row, reason = choose_building(INDEX, site_name="Bishopsgate Estate")
    assert row["building_id"] == "u-1" and reason == "site_sole_building"


def test_the_building_name_wins_over_its_site():
    """A document naming both should file against the building it names, not the estate."""
    row, reason = choose_building(INDEX, name="Riverside Lab Block", site_id="S-02")
    assert row["building_id"] == "u-4" and reason == "name"


def test_nothing_recognisable_is_unmatched_not_a_guess():
    row, reason = choose_building(INDEX, name="Somewhere Else Entirely")
    assert row is None and reason == "no_match"
    assert resolution_outcome(reason) == "unmatched"


def test_an_empty_portfolio_says_so():
    row, reason = choose_building([], name="Bishopsgate Tower")
    assert row is None and reason == "no_buildings"
    assert resolution_outcome(reason) == "unmatched"


def test_a_document_naming_nothing_resolves_to_nothing():
    row, reason = choose_building(INDEX)
    assert row is None and reason == "no_match"


def test_outcomes_separate_review_from_unmatched():
    assert resolution_outcome("name") == "resolved"
    assert resolution_outcome("building_code") == "resolved"
    assert resolution_outcome("site_sole_building") == "resolved"
    assert resolution_outcome("ambiguous_name") == "review"
    assert resolution_outcome("site_has_several_buildings") == "review"
    assert resolution_outcome("no_match") == "unmatched"
