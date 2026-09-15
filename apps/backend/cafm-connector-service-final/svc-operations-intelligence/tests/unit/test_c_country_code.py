"""A country code that was whatever it was handed.

    return _COUNTRY_ALIASES.get(s, s if len(s) <= 3 else None) or s

The `else None` was unreachable: `.get()` returns None for an unknown long name, and
`None or s` hands back the string it was given. So country_code_for("Sharjah") was
"SHARJAH" and country_code_for("Some Town") was "SOME TOWN" — every value became a country
code by the act of being passed in, and no caller could tell a resolved code from a place
name nobody had mapped.

It had not bitten because the one caller feeds it `country_code or country`, and all four
country values in production are aliased. It would have bitten the moment anyone derived
country from region, which was proposed this week: `region` holds Sharjah on 96 production
sites and Ajman on 60.
"""
from __future__ import annotations

import pytest

from src.engines.energy.buildings import _COUNTRY_ALIASES, country_code_for

#: Every distinct sites.country in production, read off the live database.
PRODUCTION_COUNTRIES = {
    "UAE": "AE",
    "United Kingdom": "UK",
    "United States": "US",
    "Singapore": "SG",
}

#: Every distinct sites.region in production after the backfill, with the country each
#: can only mean — or None where the name alone cannot say.
PRODUCTION_REGIONS = {
    "Dubai": "AE", "Abu Dhabi": "AE", "Sharjah": "AE", "Ajman": "AE",
    "Greater London": "UK", "Greater Manchester": "UK", "Scotland": "UK",
    "Wales": "UK", "Northern Irl.": "UK", "Yorkshire": "UK", "West Midlands": "UK",
    "New York": "US",
    "North West": None, "South West": None, "South East": None, "Central Region": None,
}


@pytest.mark.parametrize("name,code", sorted(PRODUCTION_COUNTRIES.items()))
def test_every_country_in_production_still_resolves(name: str, code: str):
    # Tightening the fallback must not cost a value that resolves today.
    assert country_code_for(name) == code


@pytest.mark.parametrize("name,code", sorted(PRODUCTION_REGIONS.items(), key=lambda kv: kv[0]))
def test_every_region_in_production_resolves_or_says_it_cannot(name: str, code: str | None):
    assert country_code_for(name) == code


def test_an_unmapped_place_name_is_not_a_country_code():
    # The regression, in one line. This returned "SHARJAH" before.
    assert country_code_for("Sharjah") == "AE"
    assert country_code_for("Some Town") is None
    assert country_code_for("Riverside Court") is None


def test_an_ambiguous_region_returns_nothing_rather_than_a_guess():
    """Singapore has a Central Region. So do Ghana, Uganda and Malawi.

    Two UK sites hold "South West" in this portfolio, and that is a fact about those two
    rows, not about the phrase. Mapping it to UK would make a portfolio's accident into a
    rule and would be wrong the first time a site is added anywhere else.
    """
    for ambiguous in ("Central Region", "North West", "South West", "South East"):
        assert country_code_for(ambiguous) is None


def test_a_bare_code_is_accepted_as_itself():
    # So a country the table has never heard of still works when it arrives already coded.
    assert country_code_for("FR") == "FR"
    assert country_code_for("DE") == "DE"
    assert country_code_for("jpn") == "JPN"


@pytest.mark.parametrize("junk", ["?!", "123", "1A", "-", "  ", "", None, 0, []])
def test_junk_is_not_a_country_code(junk):
    # Short is not the same as valid: the old rule accepted any value of three characters
    # or fewer, so "?!" and "123" were country codes.
    assert country_code_for(junk) is None


def test_case_and_padding_do_not_change_the_answer():
    assert country_code_for("  united arab emirates  ") == "AE"
    assert country_code_for("uAe") == "AE"


def test_all_seven_emirates_are_mapped():
    # region carries the emirate for 604 of the 618 buildings, so a gap here is a gap for
    # most of the portfolio.
    emirates = ["Abu Dhabi", "Dubai", "Sharjah", "Ajman", "Umm Al Quwain",
                "Ras Al Khaimah", "Fujairah"]
    assert [country_code_for(e) for e in emirates] == ["AE"] * 7


def test_every_alias_resolves_to_a_plausible_code():
    # A typo in the table would otherwise surface as a country code nobody notices.
    for name, code in _COUNTRY_ALIASES.items():
        assert name == name.upper(), f"{name!r} will never match: lookups are upper-cased"
        assert 2 <= len(code) <= 3 and code.isalpha() and code == code.upper()


def test_a_code_maps_to_itself():
    # Every code the table produces is also a key, so feeding an output back in is stable —
    # which is what happens when a derived value is re-imported.
    for code in set(_COUNTRY_ALIASES.values()):
        assert country_code_for(code) == code
