"""Sites uuid migration — the deterministic mapping and value classification. Pure, no DB."""
from __future__ import annotations

import uuid

import pytest

from src.engines.energy.sites_uuid_migration import (
    SITE_NAMESPACE,
    classify_values,
    derive_site_uuid,
    is_uuid,
    _ident,
)


def test_the_mapping_is_deterministic_across_runs_and_environments():
    """The whole migration rests on this: the same key must give the same uuid in dev and
    in production, or a site acquires a second identity on the second run."""
    a = derive_site_uuid("S-01")
    b = derive_site_uuid("S-01")
    assert a == b
    assert a == str(uuid.uuid5(SITE_NAMESPACE, "S-01"))
    assert is_uuid(a)


def test_whitespace_does_not_create_a_second_identity():
    assert derive_site_uuid(" S-01 ") == derive_site_uuid("S-01")


def test_different_sites_get_different_uuids():
    ids = {derive_site_uuid(f"S-{n:02d}") for n in range(1, 11)}
    assert len(ids) == 10


def test_an_empty_key_maps_to_nothing_rather_than_a_uuid():
    assert derive_site_uuid("") is None
    assert derive_site_uuid(None) is None
    assert derive_site_uuid("   ") is None


def test_values_split_into_remappable_already_migrated_and_orphan():
    mapping = {"S-01": derive_site_uuid("S-01"), "S-02": derive_site_uuid("S-02")}
    out = classify_values(
        ["S-01", "S-02", derive_site_uuid("S-01"), None, "", "S-99"], mapping
    )
    assert out["total"] == 6
    assert out["remappable"] == 2
    assert out["already_uuid"] == 1      # a re-run, not an error
    assert out["null"] == 2
    assert out["orphan_count"] == 1
    assert out["orphans"] == ["S-99"]


def test_an_orphan_is_reported_not_invented():
    """A value pointing at a site that does not exist must survive as a reported orphan —
    the migration will not create a site to make the number look clean."""
    out = classify_values(["GHOST-1", "GHOST-2"], {"S-01": derive_site_uuid("S-01")})
    assert out["remappable"] == 0
    assert out["orphan_count"] == 2
    assert set(out["orphans"]) == {"GHOST-1", "GHOST-2"}


def test_orphan_samples_are_capped_but_the_count_is_not():
    out = classify_values([f"GHOST-{i}" for i in range(100)], {})
    assert out["orphan_count"] == 100
    assert len(out["orphans"]) == 20


def test_is_uuid_rejects_a_legacy_key():
    assert is_uuid(derive_site_uuid("S-01")) is True
    assert is_uuid("S-01") is False
    assert is_uuid(None) is False


def test_identifiers_are_allow_listed_before_reaching_sql():
    assert _ident("assets") == "assets"
    for bad in ("assets; DROP TABLE sites", "Assets", "1bad", "a" * 70, ""):
        with pytest.raises(ValueError):
            _ident(bad)
