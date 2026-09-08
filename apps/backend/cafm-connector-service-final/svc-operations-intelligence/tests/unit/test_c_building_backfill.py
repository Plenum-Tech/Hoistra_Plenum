"""One building per site — backfill planning, and multi-building safety. Pure, no DB."""
from __future__ import annotations

from src.engines.energy.building_backfill import derive_building_id, plan_row


def _site(**over):
    base = {"site_id": "S-01", "site_name": "Bishopsgate Tower", "city": "London",
            "postcode": "EC2M 4NR", "region": "South East"}
    base.update(over)
    return base


def _plan(site, existing_by_site=None, existing_ids=None, country_code=None):
    return plan_row(site, existing_by_site=existing_by_site or {},
                    existing_ids=existing_ids or {}, country_code=country_code)


def test_building_id_is_derived_predictably():
    assert derive_building_id("S-01") == "B-01"
    assert derive_building_id("S_07") == "B-07"
    assert derive_building_id("B-001") == "B-001"
    assert derive_building_id("HQ") == "B-HQ"
    assert derive_building_id("") == ""


def test_derivation_is_stable_so_a_second_run_is_a_no_op():
    once = derive_building_id("S-01")
    assert derive_building_id(once) == once


def test_a_site_becomes_one_building_carrying_its_details():
    p = _plan(_site())
    assert p["action"] == "create" and p["building_id"] == "B-01"
    v = p["values"]
    assert v["site_id"] == "S-01" and v["name"] == "Bishopsgate Tower"
    assert v["state"] == "South East" and v["city"] == "London"


def test_country_is_never_guessed_from_a_region():
    p = _plan(_site())
    assert "country_code" not in p["values"]
    assert "country_code" in p["missing"]


def test_country_is_applied_only_when_the_caller_states_one():
    p = _plan(_site(), country_code="UK")
    assert p["values"]["country_code"] == "UK"
    assert p["values"]["_country_from_parameter"] is True
    assert "country_code" not in p["missing"]


def test_a_site_that_already_has_a_building_is_skipped_not_duplicated():
    p = _plan(_site(), existing_by_site={"S-01": "B-01"})
    assert p["action"] == "skip" and p["building_id"] == "B-01"


def test_a_site_with_several_buildings_is_left_alone():
    """Multi-building sites are the reason this skips rather than tops up: the second and
    third buildings are facts about the property, not something a backfill can derive."""
    p = _plan(_site(), existing_by_site={"S-01": "B-01a"})
    assert p["action"] == "skip"


def test_an_id_owned_by_a_different_site_is_a_conflict_not_a_merge():
    p = _plan(_site(), existing_ids={"B-01": "S-99"})
    assert p["action"] == "conflict" and "S-99" in p["reason"]


def test_an_id_already_owned_by_this_site_is_not_a_conflict():
    p = _plan(_site(), existing_ids={"B-01": "S-01"})
    assert p["action"] == "create"


def test_recorded_site_figures_carry_over_as_recorded_not_counted():
    p = _plan(_site(floors="34", gfa_sqm="38276", site_type="Commercial", hoist_score="88"))
    v = p["values"]
    assert v["floors_recorded"] == "34" and v["gfa_sqm_recorded"] == "38276"
    assert v["use_type"] == "Commercial" and v["hoist_score"] == "88"


def test_the_plan_names_the_fields_it_cannot_fill():
    p = _plan(_site())
    assert set(p["missing"]) == {"country_code", "use_type", "floors_recorded", "gfa_sqm_recorded"}
