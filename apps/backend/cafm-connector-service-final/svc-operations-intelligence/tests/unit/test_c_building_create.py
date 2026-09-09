"""Creating a building from the UI — the four schema mismatches, and the refusals.

Validation is pure, so all of this runs without a database.
"""
from __future__ import annotations

from src.engines.energy.building_create import (
    MAX_FLOORS,
    USE_TYPE_STORES_AS,
    validate_payload,
)
from src.engines.energy.building_rollup import SQFT_PER_SQM


def _ok(**over):
    body = {
        "site_name": "Marina Mall", "country_code": "AE", "state": "Dubai",
        "use_type": "Mall", "use_mix": [{"use": "retail", "pct": 100}],
        "floors": 6, "metering_granularity": "building-level", "source": "hoistra-ui",
    }
    body.update(over)
    return body


# ── the four fixes ───────────────────────────────────────────────────────────


def test_mall_is_accepted_and_stored_as_retail():
    """A facilities manager calls it a mall. The enum has no Mall, so the word is kept and
    mapped — rejecting the user's vocabulary and failing at the database are both worse."""
    clean, errors = validate_payload(_ok())
    assert errors == {}
    assert clean["use_type"] == "Mall", "what they said"
    assert clean["primary_use"] == "Retail", "what it stores as"


def test_every_offered_use_maps_to_a_real_enum_member():
    enum = {"Commercial", "Residential", "Retail", "Mixed", "Industrial", "Logistics",
            "Hospital", "Hotel", "Education", "Laboratory", "Leisure", "Other"}
    assert set(USE_TYPE_STORES_AS.values()) <= enum


def test_an_unknown_use_is_rejected_before_the_database_sees_it():
    _, errors = validate_payload(_ok(use_type="Castle"))
    assert "use_type" in errors
    assert "Mall is accepted" in errors["use_type"]


def test_square_metres_are_converted_to_the_square_foot_column():
    """The column is gross_area_sqft. Writing m² into it understates a building 10.76x,
    and area is the denominator of every EUI computed from readings."""
    clean, errors = validate_payload(_ok(gfa_sqm=42000))
    assert errors == {}
    assert clean["gfa_sqm"] == 42000.0
    assert clean["gross_area_sqft"] == round(42000 * SQFT_PER_SQM, 2)
    assert clean["gross_area_sqft"] > 450_000, "sanity: sqft is the larger number"


def test_an_area_that_could_only_be_square_feet_is_refused():
    """452,000 in the m² box is a unit mistake, not a very large building."""
    _, errors = validate_payload(_ok(gfa_sqm=6_000_000))
    assert "gfa_sqm" in errors and "square metres" in errors["gfa_sqm"]


def test_no_benchmark_field_is_ever_accepted_for_storage():
    """They are derived per request from the location's regulation pack. A stored copy is a
    second answer that goes stale the day a standard changes."""
    clean, _ = validate_payload(_ok(
        benchmark_standard="Made up", benchmark_standing="enacted",
        benchmark_standing_note="whatever"))
    assert not any(k.startswith("benchmark") for k in clean)


def test_a_building_code_is_never_read_as_a_site_id():
    clean, errors = validate_payload(_ok(building_code="B-07"))
    assert errors == {}
    assert clean["building_code"] == "B-07"
    assert "site_id" not in clean, "a code is not a site reference"


# ── required fields ──────────────────────────────────────────────────────────


def test_every_missing_required_field_is_named_individually():
    """One banner for a page of fields makes the form guess. Each error names its input."""
    _, errors = validate_payload({})
    assert set(errors) == {
        "name", "country_code", "region", "use_type", "use_mix",
        "floors", "metering_granularity", "source",
    }


def test_both_vocabularies_are_accepted_for_the_same_thing():
    """The form says site_name and state; the canonical table says name and region."""
    a, _ = validate_payload(_ok())
    b, _ = validate_payload(_ok(site_name=None, name="Marina Mall",
                                state=None, region="Dubai"))
    assert a["name"] == b["name"] == "Marina Mall"
    assert a["region"] == b["region"] == "Dubai"


def test_gb_and_uae_are_understood_as_uk_and_ae():
    assert validate_payload(_ok(country_code="GB"))[0]["country_code"] == "UK"
    assert validate_payload(_ok(country_code="uae"))[0]["country_code"] == "AE"


def test_an_unsupported_market_is_refused_rather_than_guessed():
    _, errors = validate_payload(_ok(country_code="FR"))
    assert "country_code" in errors


# ── use_mix ──────────────────────────────────────────────────────────────────


def test_a_mix_that_does_not_sum_to_a_hundred_is_refused_with_its_total():
    _, errors = validate_payload(_ok(use_mix=[{"use": "office", "pct": 70},
                                              {"use": "retail", "pct": 20}]))
    assert "sum to 100" in errors["use_mix"] and "90" in errors["use_mix"]


def test_a_mix_summing_to_a_hundred_across_several_uses_is_kept_whole():
    clean, errors = validate_payload(_ok(use_mix=[{"use": "retail", "pct": 80},
                                                  {"use": "office", "pct": 20}]))
    assert errors == {}
    assert clean["use_mix"] == [{"use": "retail", "pct": 80.0},
                                {"use": "office", "pct": 20.0}]


def test_rounding_in_a_mix_is_tolerated_but_a_real_gap_is_not():
    assert validate_payload(_ok(use_mix=[{"use": "a", "pct": 33.33},
                                         {"use": "b", "pct": 33.33},
                                         {"use": "c", "pct": 33.34}]))[1] == {}
    assert "use_mix" in validate_payload(_ok(use_mix=[{"use": "a", "pct": 50}]))[1]


def test_a_mix_entry_with_no_use_or_a_negative_share_is_refused():
    assert "use_mix" in validate_payload(_ok(use_mix=[{"pct": 100}]))[1]
    assert "use_mix" in validate_payload(_ok(use_mix=[{"use": "office", "pct": -5},
                                                      {"use": "retail", "pct": 105}]))[1]


# ── bounds ───────────────────────────────────────────────────────────────────


def test_floors_outside_the_plausible_range_are_refused():
    assert "floors" in validate_payload(_ok(floors=0))[1]
    assert "floors" in validate_payload(_ok(floors=MAX_FLOORS + 1))[1]
    assert validate_payload(_ok(floors=1))[1] == {}
    assert validate_payload(_ok(floors=MAX_FLOORS))[1] == {}


def test_floors_must_be_a_number():
    assert "floors" in validate_payload(_ok(floors="twenty"))[1]


def test_an_invented_metering_granularity_is_refused():
    assert "metering_granularity" in validate_payload(_ok(metering_granularity="guessed"))[1]
    for good in ("none", "building-level", "sub-metered"):
        assert validate_payload(_ok(metering_granularity=good))[1] == {}


def test_a_malformed_organization_id_is_caught_before_the_insert():
    assert "organization_id" in validate_payload(_ok(organization_id="not-a-uuid"))[1]


# ── the location link ────────────────────────────────────────────────────────


def test_the_market_codes_all_map_to_a_seeded_pack():
    """A building is scored against the pack its location points at. Every market the form
    offers must reach one, or a building created there is scored against nothing."""
    from src.engines.energy.building_create import COUNTRY_CODES, PACK_STANDARD_FOR

    assert set(PACK_STANDARD_FOR) == COUNTRY_CODES
    seeded = {"CIBSE TM46", "Energy Star · ASHRAE 100",
              "BCA Benchmarking Report", "Rolling portfolio benchmark"}
    assert set(PACK_STANDARD_FOR.values()) <= seeded


# ── locations.id schema drift ────────────────────────────────────────────────
#
# plenum_cafm.locations predates this feature on a deployment built by
# cafm-connector-service first: `id` is that service's legacy integer primary key.
# udr_building_graph.sql's `CREATE TABLE IF NOT EXISTS locations (id UUID PRIMARY KEY …)`
# no-ops against a table that already exists, so `id` never becomes uuid, and a generated
# uuid4() cannot be inserted into it — Postgres refuses the cast outright
# (DatatypeMismatchError: column "id" is of type integer but expression is of type uuid).
# plan_location_insert() decides whether to attempt that insert, from the column's real,
# introspected type — the same technique this function already uses for organization_id.


def test_a_uuid_locations_id_permits_the_insert():
    from src.engines.energy.building_create import plan_location_insert

    plan = plan_location_insert("uuid")
    assert plan["can_insert"] is True
    assert plan["reason"] is None


def test_an_integer_locations_id_refuses_the_insert_with_a_clear_reason():
    from src.engines.energy.building_create import plan_location_insert

    plan = plan_location_insert("integer")
    assert plan["can_insert"] is False
    assert "integer" in plan["reason"]
    assert "locations.id" in plan["reason"]


def test_an_unrecognised_or_missing_locations_id_type_refuses_defensively():
    """Anything other than a confirmed uuid column refuses the insert — a missing or
    unexpected type is exactly the situation this check exists to catch, not a case to
    guess through."""
    from src.engines.energy.building_create import plan_location_insert

    assert plan_location_insert(None)["can_insert"] is False
    assert plan_location_insert("bigint")["can_insert"] is False
    assert plan_location_insert("")["can_insert"] is False
