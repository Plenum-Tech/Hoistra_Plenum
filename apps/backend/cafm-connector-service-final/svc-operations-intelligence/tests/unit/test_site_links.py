"""Site resolution for building certificates — matching rules and coverage bucketing.

The behaviour under test is why per-site coverage reported one portfolio bucket: a site key
that is not UUID-shaped (this deployment's ``plenum_cafm.sites`` keys on
``site_id VARCHAR(50)``) has to be linkable too, and a building that matches no site row
still has to be counted as its own building.
"""
from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from src.engines.compliance.site_links import (
    as_uuid,
    choose_site_match,
    coverage_bucket,
    normalize_name,
    normalize_ref,
)


def _site(key: str, *names: str, codes: tuple[str, ...] = ()) -> dict:
    """A sites row shaped the way the matcher consumes it."""
    u = as_uuid(key)
    return {
        "key": str(u) if u else key,
        "site_uuid": u,
        "site_ref": None if u else key,
        "label": names[0] if names else key,
        "name_keys": {normalize_name(n) for n in names if normalize_name(n)},
        "code_keys": {normalize_ref(c) for c in (*codes, key) if normalize_ref(c)},
    }


class TestNormalisation:
    def test_case_punctuation_and_spacing_fold(self):
        assert normalize_name("  The  Mill-House ,") == "mill house"
        assert normalize_name("Tower A") == normalize_name("tower a")

    def test_distinct_buildings_do_not_fold_together(self):
        # Dropping "House"/"Building" as noise would merge two real addresses.
        assert normalize_name("Mill House") != normalize_name("Mill Building")

    def test_reference_folds_to_alphanumerics(self):
        assert normalize_ref("site-001 ") == "SITE001"
        assert normalize_ref(None) == ""


class TestUuidShape:
    def test_uuid_key_parses(self):
        u = uuid4()
        assert as_uuid(str(u)) == u

    def test_varchar_key_is_not_a_uuid(self):
        assert as_uuid("SITE-001") is None
        assert as_uuid("") is None
        assert as_uuid(None) is None


class TestChooseSiteMatch:
    def test_exact_name_wins(self):
        rows = [_site("SITE-1", "Mill House"), _site("SITE-2", "Riverside Depot")]
        match, reason = choose_site_match(rows, name="mill house")
        assert reason == "name"
        assert match["key"] == "SITE-1"

    def test_reference_match_beats_name_search(self):
        rows = [_site("SITE-1", "Mill House", codes=("MH-01",))]
        match, reason = choose_site_match(rows, name="Somewhere else", reference="mh-01")
        assert reason == "reference"
        assert match["key"] == "SITE-1"

    def test_varchar_key_resolves_as_site_ref(self):
        # The case that broke everything: a non-UUID site key must still link.
        rows = [_site("SITE-1", "Mill House")]
        match, _ = choose_site_match(rows, name="Mill House")
        assert match["site_uuid"] is None
        assert match["site_ref"] == "SITE-1"

    def test_uuid_key_resolves_as_site_uuid(self):
        key = str(uuid4())
        rows = [_site(key, "Mill House")]
        match, _ = choose_site_match(rows, name="Mill House")
        assert match["site_uuid"] == UUID(key)
        assert match["site_ref"] is None

    def test_unique_containment_is_accepted(self):
        rows = [_site("SITE-1", "Mill House Annexe"), _site("SITE-2", "Riverside Depot")]
        match, reason = choose_site_match(rows, name="Mill House")
        assert reason == "name_contains"
        assert match["key"] == "SITE-1"

    def test_two_candidates_are_ambiguous_not_a_guess(self):
        rows = [_site("SITE-1", "Mill House"), _site("SITE-2", "Mill House")]
        match, reason = choose_site_match(rows, name="Mill House")
        assert match is None
        assert reason == "ambiguous_name"

    def test_ambiguous_containment_is_refused(self):
        rows = [_site("SITE-1", "Mill House North"), _site("SITE-2", "Mill House South")]
        match, reason = choose_site_match(rows, name="Mill House")
        assert match is None
        assert reason == "ambiguous_name_contains"

    def test_short_name_never_matches_by_containment(self):
        rows = [_site("SITE-1", "Mill House")]
        match, reason = choose_site_match(rows, name="Mill")
        assert match is None
        assert reason == "no_match"

    def test_wildly_longer_name_is_not_the_same_building(self):
        rows = [_site("SITE-1", "Tower A Annexe Car Park And Surrounding Grounds")]
        match, _ = choose_site_match(rows, name="Tower A Annexe")
        assert match is None

    def test_no_sites_and_no_name_are_distinguishable_reasons(self):
        assert choose_site_match([], name="Mill House") == (None, "no_sites")
        assert choose_site_match([_site("SITE-1", "Mill House")], name="")[1] == (
            "no_building_name"
        )


class TestCoverageBucket:
    def test_uuid_linked_certificate_buckets_on_its_site(self):
        key = str(uuid4())
        bucket, _, linked = coverage_bucket({"site_id": key})
        assert bucket == f"site:{key}"
        assert linked is True

    def test_varchar_linked_certificate_buckets_on_its_site(self):
        bucket, _, linked = coverage_bucket({"site_id": None, "site_ref": "SITE-1"})
        assert bucket == "site:SITE-1"
        assert linked is True

    def test_unlinked_building_gets_its_own_bucket(self):
        # The fix: an unregistered building is still a building, not "the portfolio".
        a = coverage_bucket({"building_name": "Mill House"})
        b = coverage_bucket({"building_name": "  the mill-house "})
        c = coverage_bucket({"building_name": "Riverside Depot"})
        assert a[0] == b[0], "the same building spelled two ways is one bucket"
        assert a[0] != c[0], "two buildings are never one bucket"
        assert a[2] is False
        assert a[1] == "Mill House"

    def test_building_reference_stands_in_for_a_missing_name(self):
        bucket, label, _ = coverage_bucket({"building_reference": "MH-01"})
        assert bucket == "building:mh 01"
        assert label == "MH-01"

    def test_only_a_nameless_certificate_falls_to_portfolio(self):
        bucket, label, linked = coverage_bucket({"certificate_type_code": "EPC"})
        assert bucket == "_portfolio"
        assert label == "Portfolio (no building on certificate)"
        assert linked is False


@pytest.mark.parametrize(
    "name,reference,expected",
    [
        ("Mill House", None, "SITE-1"),
        ("MILL  house", None, "SITE-1"),
        (None, "MH-01", "SITE-1"),
        ("Riverside Depot", None, "SITE-2"),
    ],
)
def test_resolution_is_stable_across_spellings(name, reference, expected):
    rows = [
        _site("SITE-1", "Mill House", codes=("MH-01",)),
        _site("SITE-2", "Riverside Depot", codes=("RD-02",)),
    ]
    match, _ = choose_site_match(rows, name=name, reference=reference)
    assert match is not None and match["key"] == expected


class _NoDbSession:
    """A session whose every query fails — so the test exercises grouping, not SQL.

    ``building_coverage`` treats a failed sites/pack lookup as "no rows", which is exactly
    the state this fix is about: nothing in the database ties these certificates to a site.
    """

    def begin_nested(self):
        class _Ctx:
            async def __aenter__(self):
                return None

            async def __aexit__(self, *exc):
                return False

        return _Ctx()

    async def execute(self, *args, **kwargs):
        raise RuntimeError("no database in unit tests")


def test_building_coverage_reports_a_row_per_building(monkeypatch):
    """The gap this closes: unlinked certificates used to collapse into one bucket.

    Six certificates across two buildings and one unattributed used to produce a single
    "Portfolio (no site linked)" row at one blended percentage. They now produce a row per
    building, each scored against the pack it is actually obliged to hold.
    """
    import asyncio
    from types import SimpleNamespace

    from src.engines.compliance import coverage as coverage_svc

    pack = [
        SimpleNamespace(certificate_type_code=c, trade_category=None)
        for c in ("EPC", "EICR", "FRA", "GAS_SAFETY")
    ]
    certs = [
        {"building_name": "Mill House", "certificate_type_code": "EPC", "status": "Current"},
        {"building_name": "Mill House", "certificate_type_code": "EICR", "status": "Current"},
        {"building_name": "The Mill-House", "certificate_type_code": "FRA", "status": "Lapsed"},
        {"building_name": "Riverside Depot", "certificate_type_code": "EPC", "status": "Current"},
        {"site_ref": "SITE-9", "building_name": "Linked Site", "certificate_type_code": "EPC",
         "status": "Current"},
        {"certificate_type_code": "EPC", "status": "Current"},
    ]

    async def _pack_types(session, **kwargs):
        return pack

    async def _list(session, **kwargs):
        return certs

    async def _labels(session, keys):
        return {"SITE-9": "Riverside Wharf"}

    monkeypatch.setattr(coverage_svc, "list_pack_types", _pack_types)
    monkeypatch.setattr(coverage_svc.cert_svc, "list_certificates", _list)
    monkeypatch.setattr(coverage_svc, "_site_labels", _labels)

    out = asyncio.run(coverage_svc.building_coverage(_NoDbSession()))
    by_name = {b["site_name"]: b for b in out["buildings"]}

    assert set(by_name) == {
        "Mill House",
        "Riverside Depot",
        "Riverside Wharf",
        "Portfolio (no building on certificate)",
    }
    # Three certificates, two spellings of one name, one building.
    assert by_name["Mill House"]["certificates_total"] == 3
    assert by_name["Mill House"]["on_record"] == 3
    assert by_name["Mill House"]["coverage_pct"] == 75.0
    assert by_name["Mill House"]["non_compliant"] == 1
    assert by_name["Mill House"]["gaps"] == ["GAS_SAFETY"]
    # A building named only on its documents is counted, and says so.
    assert by_name["Mill House"]["linked"] is False
    assert by_name["Mill House"]["site_id"] is None
    # A varchar-keyed site links, and is labelled from the sites table.
    assert by_name["Riverside Wharf"]["linked"] is True
    assert by_name["Riverside Wharf"]["site_ref"] == "SITE-9"
    assert by_name["Riverside Wharf"]["site_id"] is None
    # One certificate names no building; it alone is the portfolio row.
    assert by_name["Portfolio (no building on certificate)"]["certificates_total"] == 1

    assert out["buildings_linked_to_site"] == 1
    assert out["buildings_unlinked"] == 3
    # Every row says what its denominator counts, so a caller cannot render the percentage
    # as a compliance score by accident.
    assert out["required_basis"] == "country_pack"
    assert out["required_basis_note"]
    assert all(b["required_basis"] == "country_pack" for b in out["buildings"])
    # The average is now across buildings, not one portfolio-wide figure.
    assert out["average_coverage_pct"] == 37.5
