"""Creating a building from the UI — the four schema mismatches, and the refusals.

Validation is pure, so all of this runs without a database.
"""
from __future__ import annotations

import uuid

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


def test_an_integer_locations_id_is_linked_through_the_pseudo_uuid_shape():
    """An integer ``locations.id`` no longer refuses the link. On a deployment where
    cafm-connector-service created the table first, its id is that service's legacy integer
    primary key; rather than migrate a table this platform does not own, the sequence
    assigns the id and it is wrapped in a deterministic uuid-shaped stand-in that
    ``buildings.location_id`` can actually hold."""
    from src.engines.energy.building_create import plan_location_insert

    for t in ("integer", "bigint", "smallint", "INTEGER"):
        plan = plan_location_insert(t)
        assert plan["can_insert"] is True, t
        assert plan["id_kind"] == "integer", t
        assert plan["reason"] is None, t


def test_an_unrecognised_or_missing_locations_id_type_refuses_defensively():
    """A type the engine does not know how to write still refuses outright — a missing or
    unexpected type is exactly the situation this check exists to catch, not a case to
    guess through."""
    from src.engines.energy.building_create import plan_location_insert

    for t in (None, "", "text", "character varying", "jsonb"):
        plan = plan_location_insert(t)
        assert plan["can_insert"] is False, t
        assert plan["id_kind"] is None, t
        assert "locations.id" in plan["reason"], t


def test_the_pseudo_uuid_is_the_exact_shape_the_rollup_join_matches():
    """building_rollup.py joins locations to buildings with
    ``'00000000-0000-0000-0000-' || lpad(l.id::text, 12, '0')``. If the padding here and the
    padding there ever drift apart, every building silently loses its location — so the two
    are pinned together by this test rather than by a comment alone."""
    from src.engines.energy.building_create import _int_location_id_to_uuid

    assert _int_location_id_to_uuid(1) == "00000000-0000-0000-0000-000000000001"
    assert _int_location_id_to_uuid(42) == "00000000-0000-0000-0000-000000000042"
    assert _int_location_id_to_uuid(999999999999) == "00000000-0000-0000-0000-999999999999"
    # Same zero-padding width as the SQL lpad(..., 12, '0'), and a valid uuid either way.
    for n in (0, 7, 1234, 999999999999):
        out = _int_location_id_to_uuid(n)
        assert len(out) == 36, out
        assert uuid.UUID(out)


# ── the composite building code: org-country-number-region-use ─────────────────


def test_region_short_takes_the_first_three_letters_alpha_only():
    from src.engines.energy.building_create import _region_short

    assert _region_short("South East") == "SOU"
    assert _region_short("new jersey") == "NEW"
    assert _region_short("Abu Dhabi") == "ABU"
    assert _region_short("") == "REG"
    assert _region_short(None) == "REG"


def test_use_short_never_collides_hospital_with_hotel():
    from src.engines.energy.building_create import USE_TYPE_STORES_AS, _use_short

    # The whole reason this is an explicit map and not a blind 3-letter slice.
    assert _use_short("Hospital") == "HSP"
    assert _use_short("Hotel") == "HTL"
    assert _use_short("Hospital") != _use_short("Hotel")
    # Every canonical enum value the UI can actually store has its own 3-letter code, and
    # no two collide — a slice-based fallback would only ever run for a value this map
    # does not know, which should not happen for anything USE_TYPE_STORES_AS produces.
    codes = {_use_short(v) for v in set(USE_TYPE_STORES_AS.values())}
    assert len(codes) == len(set(USE_TYPE_STORES_AS.values()))
    assert _use_short(None) == "OTH"
    assert _use_short("Something new") == "SOM"


class _FakeResult:
    """MagicMock stands in for the sync .first()/.all() SQLAlchemy hands back from an
    awaited session.execute(...) — the await is on execute() itself, not on reading its
    result, so these need to be plain synchronous values, not coroutines."""

    def __init__(self, first=None, all_rows=None):
        self._first = first
        self._all = all_rows or []

    def first(self):
        return self._first

    def all(self):
        return self._all


def _fake_session(org_row, code_rows):
    """Same fake_execute-counts-calls pattern test_b2_scoring.py already uses for an async
    session: the org-code lookup is always the first execute() a composite request makes,
    the building_code regex scan is always the second."""
    from unittest.mock import AsyncMock

    calls = {"n": 0}

    async def fake_execute(*_args, **_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return _FakeResult(first=org_row)
        return _FakeResult(all_rows=code_rows)

    session = AsyncMock()
    session.execute = fake_execute
    return session


async def test_the_first_building_in_a_bucket_is_numbered_01():
    from src.engines.energy.building_create import _next_building_code

    session = _fake_session(org_row=("TC-FMGMT",), code_rows=[])
    code = await _next_building_code(
        session, organization_id="11111111-1111-1111-1111-111111111111",
        country_code="UK", region="London", primary_use="Commercial",
    )
    assert code == "TC-FMGMT-UK-01-LON-COM"


async def test_the_number_counts_only_buildings_sharing_the_other_four_segments():
    from src.engines.energy.building_create import _next_building_code

    # Two existing buildings in this exact bucket (03 and 07, not consecutive — the
    # highest is what matters, same rule the legacy B-NN scheme already follows).
    session = _fake_session(
        org_row=("TC-FMGMT",),
        code_rows=[("TC-FMGMT-UK-03-LON-COM",), ("TC-FMGMT-UK-07-LON-COM",)],
    )
    code = await _next_building_code(
        session, organization_id="11111111-1111-1111-1111-111111111111",
        country_code="UK", region="London", primary_use="Commercial",
    )
    assert code == "TC-FMGMT-UK-08-LON-COM"


async def test_a_different_bucket_never_shows_up_in_the_scan():
    """The regex is anchored to this exact org/country/region/use — a Retail building in
    the same org and country does not bump the Commercial count, and could not: the SQL
    WHERE clause itself only matches rows whose code fits this bucket's own pattern."""
    from src.engines.energy.building_create import _next_building_code

    session = _fake_session(
        org_row=("TC-FMGMT",),
        # A Retail building would never be returned by the WHERE building_code ~ :pat this
        # function sends — modelled here by simply not including it in code_rows, since the
        # fake session's job is to stand in for what postgres itself would have filtered.
        code_rows=[],
    )
    code = await _next_building_code(
        session, organization_id="11111111-1111-1111-1111-111111111111",
        country_code="UK", region="London", primary_use="Commercial",
    )
    assert code == "TC-FMGMT-UK-01-LON-COM"


async def test_no_organization_id_falls_back_to_the_flat_scheme():
    """No organization_id means _org_code is never called at all — the legacy path's own
    building_code scan is the FIRST and only execute(), unlike every case above where an
    org lookup comes first. A one-call fake, not _fake_session's two-call one."""
    from unittest.mock import AsyncMock

    from src.engines.energy.building_create import _next_building_code

    session = AsyncMock()
    session.execute = AsyncMock(return_value=_FakeResult(all_rows=[("B-04",)]))
    code = await _next_building_code(
        session, organization_id=None, country_code="UK", region="London",
        primary_use="Commercial",
    )
    assert code == "B-05"


async def test_a_missing_region_or_use_also_falls_back_even_with_an_organization_id():
    from src.engines.energy.building_create import _next_building_code

    session = _fake_session(org_row=("TC-FMGMT",), code_rows=[])
    code = await _next_building_code(
        session, organization_id="11111111-1111-1111-1111-111111111111",
        country_code="UK", region=None, primary_use="Commercial",
    )
    assert code == "B-01"


async def test_an_organization_id_that_resolves_to_no_org_code_also_falls_back():
    """organizations.code is NOT NULL and unique on the live schema, but a stale or
    cross-environment organization_id that matches no row must not crash the create — it
    falls back the same as a missing organization_id would."""
    from src.engines.energy.building_create import _next_building_code

    session = _fake_session(org_row=None, code_rows=[("B-01",)])
    code = await _next_building_code(
        session, organization_id="11111111-1111-1111-1111-111111111111",
        country_code="UK", region="London", primary_use="Commercial",
    )
    assert code == "B-02"


# ── GET /buildings/next-code — the preview the form calls live ─────────────────


def _preview_client():
    """Same fixture pattern test_body_organization_scoped.py already uses for a route-level
    test with no real database: get_session is overridden with a fake, current_principal
    with a fixed admin on ORG."""
    from datetime import datetime, timezone
    from unittest.mock import AsyncMock
    from uuid import UUID

    from fastapi.testclient import TestClient

    from src.api.routes import auth as auth_routes
    from src.app import app
    from src.db import get_session
    from src.engines.auth.tokens import Principal

    org = UUID("11111111-1111-1111-1111-111111111111")

    async def _session():
        s = AsyncMock()
        s.execute = AsyncMock(return_value=_FakeResult(first=("TC-FMGMT",), all_rows=[]))
        yield s

    async def _principal():
        return Principal(user_id=UUID(int=2), email="a@example.com", organization_id=org,
                          session_id=None, issued_at=datetime.now(timezone.utc),
                          password_changed_at=0, role="admin", can_ingest=True,
                          building_ids=None)

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[auth_routes.current_principal] = _principal
    return TestClient(app), org


def test_the_preview_route_returns_the_same_shape_the_create_route_would_allocate():
    client, org = _preview_client()
    try:
        r = client.get("/api/energy/buildings/next-code", params={
            "organization_id": str(org), "country_code": "UK", "region": "London",
            "use_type": "Commercial",
        })
    finally:
        from src.app import app
        app.dependency_overrides.clear()
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "building_code": "TC-FMGMT-UK-01-LON-COM"}


def test_the_preview_route_normalises_gb_and_maps_mall_to_retail():
    """The same GB→UK and Mall→Retail rules validate_payload already applies — a preview
    that used the raw values instead would show a code the real create would never
    allocate, since Mall previews as MAL while the actual insert stores Retail (RET)."""
    client, org = _preview_client()
    try:
        r = client.get("/api/energy/buildings/next-code", params={
            "organization_id": str(org), "country_code": "GB", "state": "London",
            "use_type": "Mall",
        })
    finally:
        from src.app import app
        app.dependency_overrides.clear()
    assert r.status_code == 200, r.text
    assert r.json()["building_code"] == "TC-FMGMT-UK-01-LON-RET"


def test_the_preview_route_falls_back_when_a_field_is_not_chosen_yet():
    """The Hoist-a-building form's fields fill in one at a time — a preview asked for
    before use_type is chosen previews the flat scheme rather than refusing to answer."""
    client, org = _preview_client()
    try:
        r = client.get("/api/energy/buildings/next-code", params={
            "organization_id": str(org), "country_code": "UK", "region": "London",
        })
    finally:
        from src.app import app
        app.dependency_overrides.clear()
    assert r.status_code == 200, r.text
    assert r.json()["building_code"] == "B-01"
