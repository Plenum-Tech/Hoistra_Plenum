"""PATCH validation — what a partial edit touches, and what it refuses to.

Pure; no database.
"""
from __future__ import annotations

from src.engines.energy.building_update import (
    PATCHABLE,
    REQUIRED_FIELDS,
    _same_instant,
    validate_patch,
)
from src.engines.energy.building_rollup import SQFT_PER_SQM


# ── only what was sent ───────────────────────────────────────────────────────


def test_a_patch_carries_only_the_field_it_sent():
    """The whole point. A create validator run over a padded payload must not leak its
    padding into the update, or every patch would rewrite the whole row."""
    clean, errors = validate_patch({"floors": 26})
    assert errors == {}
    assert clean == {"floors": 26}


def test_the_two_vocabularies_both_reach_the_same_field():
    assert validate_patch({"site_name": "New Name"})[0] == {"name": "New Name"}
    assert validate_patch({"state": "Dubai"})[0] == {"region": "Dubai"}


def test_a_derived_value_travels_only_with_its_source():
    """primary_use is derived from use_type; gross_area_sqft from gfa_sqm. Neither may
    appear in a patch that did not send the field it comes from."""
    assert "primary_use" not in validate_patch({"floors": 3})[0]
    assert "gross_area_sqft" not in validate_patch({"floors": 3})[0]

    clean, _ = validate_patch({"use_type": "Mall"})
    assert clean["primary_use"] == "Retail"
    clean, _ = validate_patch({"gfa_sqm": 42000})
    assert clean["gross_area_sqft"] == round(42000 * SQFT_PER_SQM, 2)


# ── a blank is not an absence ────────────────────────────────────────────────


def test_a_required_field_cannot_be_emptied():
    """A form submitting a blank box must not leave a building with no country. Ignoring
    the blank would tell the user it saved something it did not."""
    for f in ("name", "country_code", "region", "use_type", "floors", "metering_granularity"):
        _, errors = validate_patch({f: ""})
        assert f in errors and "Cannot be cleared" in errors[f], f


def test_an_optional_field_can_be_emptied_deliberately():
    clean, errors = validate_patch({"postcode": ""})
    assert errors == {} and clean == {"postcode": None}


def test_omitting_a_field_is_not_the_same_as_clearing_it():
    assert "postcode" not in validate_patch({"floors": 4})[0]


# ── refusals ─────────────────────────────────────────────────────────────────


def test_a_field_that_is_not_editable_is_refused_by_name():
    """Silently dropping it looks exactly like saving it."""
    _, errors = validate_patch({"benchmark_standard": "Made up", "hoist_score": 99,
                                "building_id": "x"})
    assert errors == {"benchmark_standard": "Not editable here.",
                      "hoist_score": "Not editable here.",
                      "building_id": "Not editable here."}


def test_the_create_rules_still_apply_to_an_edit():
    """A value a create would refuse cannot slip in through a patch."""
    assert "floors" in validate_patch({"floors": 900})[1]
    assert "country_code" in validate_patch({"country_code": "FR"})[1]
    assert "use_type" in validate_patch({"use_type": "Castle"})[1]
    assert "gfa_sqm" in validate_patch({"gfa_sqm": 6_000_000})[1]


def test_a_use_mix_is_still_checked_against_a_hundred():
    _, errors = validate_patch({"use_mix": [{"use": "office", "pct": 70}]})
    assert "sum to 100" in errors["use_mix"]


def test_an_empty_patch_says_so_rather_than_reporting_success():
    assert validate_patch({}) == ({}, {"_": "Nothing to change."})


def test_benchmark_fields_are_not_quietly_accepted_anywhere():
    clean, _ = validate_patch({"floors": 3, "benchmark_kwh_per_m2": 100})
    assert not any(k.startswith("benchmark") for k in clean)


# ── the patchable surface ────────────────────────────────────────────────────


def test_every_required_field_is_patchable():
    """A field that must have a value but cannot be edited is a value nobody can correct."""
    assert REQUIRED_FIELDS <= PATCHABLE


# ── optimistic concurrency ───────────────────────────────────────────────────


def test_two_edits_inside_one_second_are_two_edits():
    """The case the check exists for. A second-resolution compare calls these identical,
    which is the one moment it must not."""
    a = "2026-09-08 11:22:19.022251+00"
    b = "2026-09-08 11:22:19.988000+00"
    assert _same_instant(a, b) is False


def test_the_same_moment_written_differently_is_not_a_conflict():
    """A client round-tripping the value through ISO-8601 must not be told its own edit
    conflicts with itself."""
    assert _same_instant("2026-09-08 11:22:19.022251+00",
                         "2026-09-08T11:22:19.022251+00:00") is True


def test_an_unparseable_timestamp_does_not_block_the_edit():
    """Our own parsing failing is not evidence that somebody else edited the row."""
    assert _same_instant("2026-09-08 11:22:19+00", "not a timestamp") is None


def test_expected_updated_at_is_not_written_as_a_column():
    clean, _ = validate_patch({"floors": 3, "expected_updated_at": "2026-09-08 11:00:00+00"})
    assert clean["expected_updated_at"] == "2026-09-08 11:00:00+00"
    assert "floors" in clean
